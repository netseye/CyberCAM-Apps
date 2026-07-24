'''K230 本地身份证 OCR。

实时取景只做轻量四边形检测；用户确认后才进行透视矫正与一次 KPU OCR。
原始证件图不落盘，日志不输出 OCR 内容，身份证号在结果页默认脱敏。
'''

import cv2
import fcntl
import gc
import os
import select
import struct
import threading
import time
from collections import deque

import numpy as np
from walnutpi import Display, Sensor, direction, kpu

import core
import vision


W, H = 640, 360
SCREEN_W, SCREEN_H = 640, 480
PREVIEW_Y = 60
CONTROLS_Y = PREVIEW_Y + H
GUIDE_CORNERS = ((72, 23), (568, 23), (568, 336), (72, 336))
FONT = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
MODEL_FILES = ("ocr_det_int16.kmodel", "ocr_rec_int16.kmodel", "dict.txt")
MODEL_EXPECTED_BYTES = {
    "ocr_det_int16.kmodel": 5_030_752,
    "ocr_rec_int16.kmodel": 13_008_216,
    "dict.txt": 32_521,
}

ft = cv2.freetype.createFreeType2()
ft.loadFontData(FONT, 0)


def text(img, value, org, color=(235, 235, 235), height=22):
    ft.putText(img=img, text=str(value), org=org, fontHeight=height,
               color=color, thickness=-1, line_type=cv2.LINE_AA,
               bottomLeftOrigin=False)
    return img


def _model_path(filename):
    local = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    deployed = os.path.join("/data/app/id-card-ocr", filename)
    # 系统自带 OCR 应用使用同一套 KPU 模型；部署时可复用，避免在不稳定
    # Wi-Fi 上重复传 17 MB。使用已知资源的精确大小，避免较大的中断上传
    # 残片抢在完整共享模型之前被选中。
    shared = os.path.join("/data/app/ocr", filename)
    for candidate in (local, deployed, shared, filename):
        if (os.path.isfile(candidate) and
                os.path.getsize(candidate) == MODEL_EXPECTED_BYTES[filename]):
            return candidate
    raise FileNotFoundError(filename + " 模型文件缺失")


class OCRRuntime:
    def __init__(self):
        self.detector = None

    def load(self):
        if self.detector is None:
            det, rec, dictionary = (_model_path(name) for name in MODEL_FILES)
            self.detector = kpu.OCR(det, rec, dictionary, 640, (512, 32))

    def run(self, card):
        self.load()
        boxes = self.detector.run(card, 0.25) or []
        rows = []
        for box in boxes:
            value = str(getattr(box, "text", "")).strip()
            reliability = float(getattr(box, "reliability", 0.0))
            if value and reliability >= 0.25:
                rows.append({
                    "text": value,
                    "x": int(getattr(box, "x", 0)),
                    "y": int(getattr(box, "y", 0)),
                    "w": int(getattr(box, "w", 0)),
                    "h": int(getattr(box, "h", 0)),
                    "confidence": reliability,
                })
        return core.order_ocr_rows(rows)

    def unload(self):
        detector = self.detector
        if detector is None:
            return True

        # walnutpi.kpu.OCR 的后台线程以绑定方法 self._worker_loop 为 target，
        # 会形成 OCR -> Thread -> bound method -> OCR 的强引用环。只赋 None
        # 不会触发 __del__，det/rec 的 Interpreter 与 AI2D 因而持续占用 CMA。
        # 显式执行其析构意图：通知、唤醒、等待线程结束，再拆除子模型引用。
        shutdown = getattr(detector, "_shutdown_event", None)
        if shutdown is not None:
            shutdown.set()
        task_queue = getattr(detector, "_task_queue", None)
        if task_queue is not None:
            try:
                task_queue.put_nowait(None)
            except Exception:
                pass
        worker = getattr(detector, "_worker_thread", None)
        if worker is not None and worker.is_alive():
            worker.join(timeout=1.5)
        if worker is not None and worker.is_alive():
            print("[memory] OCR 工作线程未退出，保留模型避免并发释放")
            return False

        self.detector = None
        detector.results = []
        # 子类没有公开 close/deinit；清空原生 Interpreter/AI2D 引用，确保
        # 它们在工作线程已经停止后按确定顺序析构。
        for child_name in ("det", "rec"):
            child = getattr(detector, child_name, None)
            if child is not None:
                for resource_name in ("ai2d", "kpu"):
                    if hasattr(child, resource_name):
                        setattr(child, resource_name, None)
                if hasattr(child, "results"):
                    child.results = []
                setattr(detector, child_name, None)
        detector = None
        gc.collect()
        return True


def _result_score(parsed, rows):
    return core.ocr_candidate_score(parsed, rows)


def recognize_card(runtime, card):
    '''主画布确定方向，再用放大画布精读民族和地址。'''
    canvas = vision.build_id_ocr_canvas(card)
    rows = runtime.run(canvas)
    parsed = core.parse_id_card_roi_rows(rows, canvas.shape[0])
    best = (parsed, rows)
    best_card = card
    if core.should_retry_orientation(parsed):
        rotated = cv2.rotate(card, cv2.ROTATE_180)
        other_canvas = vision.build_id_ocr_canvas(rotated)
        other_rows = runtime.run(other_canvas)
        other = core.parse_id_card_roi_rows(other_rows, other_canvas.shape[0])
        if _result_score(other, other_rows) > _result_score(parsed, rows):
            best = (other, other_rows)
            best_card = rotated
        other_canvas = None
    parsed, rows = best
    detail_canvas = vision.build_id_detail_ocr_canvas(best_card)
    detail_rows = runtime.run(detail_canvas)
    detail = core.parse_id_detail_rows(detail_rows, detail_canvas.shape[0])
    parsed = core.merge_id_detail_fields(parsed, detail)
    all_rows = rows + detail_rows
    if not parsed.get("ethnicity"):
        ethnicity_canvas = vision.build_ethnicity_retry_canvas(best_card)
        ethnicity_rows = runtime.run(ethnicity_canvas)
        ethnicity_detail = core.parse_ethnicity_retry_rows(ethnicity_rows)
        parsed = core.merge_id_detail_fields(parsed, ethnicity_detail)
        all_rows += ethnicity_rows
        ethnicity_canvas = None
    # 原文页同时显示主识别和放大识别结果，便于区分模型错误与字段解析错误。
    best = (parsed, all_rows)
    detail_canvas = best_card = None
    canvas = None
    return best


# ----------------------------- 输入 ---------------------------------
EV_KEY, EV_ABS = 0x01, 0x03
ABS_X, ABS_Y = 0x00, 0x01
BTN_TOUCH = 0x14A


def _eviocgabs(fd, axis):
    op = (2 << 30) | (24 << 16) | (0x45 << 8) | (0x40 + axis)
    data = bytearray(24)
    try:
        fcntl.ioctl(fd, op, data, True)
        _, lo, hi, _, _, _ = struct.unpack("<6i", bytes(data))
        if hi > lo:
            return lo, hi
    except OSError:
        pass
    return 0, 1


class TouchInput:
    def __init__(self, device="/dev/input/event0", flipped=False):
        self.device = device
        self.flipped = flipped
        # OCR 是同步操作，期间不会消费触摸事件。只保留最近几次释放动作，
        # 防止异常输入设备或反复点击让队列无界增长。
        self.points = deque(maxlen=8)
        self.running = False
        self._fd = None
        self._x = self._y = 0
        self._generation = 0
        self._down_generation = None
        self._points_lock = threading.Lock()
        try:
            fd = os.open(device, os.O_RDONLY)
            self._minx, self._maxx = _eviocgabs(fd, ABS_X)
            self._miny, self._maxy = _eviocgabs(fd, ABS_Y)
            os.close(fd)
            self.running = True
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
            print("[input] 触摸屏已就绪")
        except OSError as exc:
            self._thread = None
            self._minx, self._maxx = 0, 1
            self._miny, self._maxy = 0, 1
            print("[input] 触摸不可用:", exc)

    def _loop(self):
        try:
            fd = os.open(self.device, os.O_RDONLY | os.O_NONBLOCK)
            self._fd = fd
        except OSError:
            return
        try:
            while self.running:
                try:
                    readable, _, _ = select.select([fd], [], [], 0.1)
                    if not readable:
                        continue
                    data = os.read(fd, 24)
                except (BlockingIOError, InterruptedError):
                    continue
                except OSError:
                    break
                if len(data) < 24:
                    continue
                _, _, kind, code, value = struct.unpack("<QQHHi", data)
                if kind == EV_ABS and code == ABS_X:
                    self._x = value
                elif kind == EV_ABS and code == ABS_Y:
                    self._y = value
                elif kind == EV_KEY and code == BTN_TOUCH:
                    with self._points_lock:
                        if value == 1:
                            self._down_generation = self._generation
                        elif value == 0:
                            # 页面切换会递增 generation；跨越识别/切页边界的
                            # 手势不能在新页面被解释成显示号码等动作。
                            if self._down_generation == self._generation:
                                self.points.append((self._x, self._y))
                            self._down_generation = None
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
            self._fd = None

    def poll(self):
        with self._points_lock:
            if not self.points:
                return None
            x, y = self.points.popleft()
        nx = (x - self._minx) / max(1, self._maxx - self._minx)
        ny = (y - self._miny) / max(1, self._maxy - self._miny)
        nx, ny = max(0.0, min(1.0, nx)), max(0.0, min(1.0, ny))
        if self.flipped:
            nx, ny = 1.0 - nx, 1.0 - ny
        return nx, ny

    def discard_pending(self):
        '''丢弃已排队及当前尚未抬起的手势。'''
        with self._points_lock:
            self._generation += 1
            self.points.clear()

    def close(self):
        self.running = False
        if self._thread is not None and self._thread.is_alive():
            # _loop 最长在 select 中等待 100 ms；等待它自行关闭 fd，避免从
            # 另一线程关闭后 fd 编号被系统复用造成竞态。
            self._thread.join(timeout=0.25)
        if self._thread is not None and self._thread.is_alive():
            print("[input] 触摸线程未在超时内退出")


class Key:
    def __init__(self):
        self._key = self._beep = self._led = None
        self._off_at = 0.0
        try:
            import board
            from digitalio import DigitalInOut, Direction, Pull
            self._key = DigitalInOut(board.KEY)
            self._key.direction = Direction.INPUT
            self._key.pull = Pull.UP
            self._beep = DigitalInOut(board.BEEP)
            self._beep.direction = Direction.OUTPUT
            self._beep.value = 0
            self._led = DigitalInOut(board.LED)
            self._led.direction = Direction.OUTPUT
            # DigitalInOut 切换方向时的默认锁存值并不作为产品语义依赖；
            # 显式写低，保证不会继承桌面/上一个应用留下的补光状态。
            self._led.value = 0
            print("[input] 实体键已就绪")
        except Exception as exc:
            print("[input] 实体键不可用:", exc)

    @property
    def pressed(self):
        return self._key is not None and self._key.value == 0

    def pulse(self, now, duration=0.06):
        self._off_at = now + duration
        if self._beep is not None:
            self._beep.value = 1

    def set_light(self, enabled):
        '''控制相机补光灯；提示脉冲不再自动操作它。'''
        if self._led is None:
            return False
        self._led.value = 1 if enabled else 0
        return True

    def update(self, now):
        if self._off_at and now >= self._off_at:
            if self._beep is not None:
                self._beep.value = 0
            self._off_at = 0.0

    def close(self):
        if self._beep is not None:
            self._beep.value = 0
        self.set_light(False)
        for device in (self._key, self._beep, self._led):
            deinit = getattr(device, "deinit", None)
            if callable(deinit):
                try:
                    deinit()
                except Exception:
                    pass
        self._key = self._beep = self._led = None


# ----------------------------- UI -----------------------------------
def _base_screen():
    screen = np.zeros((SCREEN_H, SCREEN_W, 3), np.uint8)
    screen[:] = (12, 15, 20)
    cv2.rectangle(screen, (5, 5), (51, 55), (52, 58, 67), -1)
    text(screen, "×", (18, 8), (235, 235, 238), 30)
    text(screen, "身份证 OCR", (64, 6), (0, 245, 145), 23)
    text(screen, "K230 本地识别 · 原图不保存", (350, 10), (140, 150, 158), 16)
    return screen


def _draw_guide(preview):
    x1, y1 = GUIDE_CORNERS[0]
    x2, y2 = GUIDE_CORNERS[2]
    length = 34
    color = (105, 115, 125)
    for x, y, sx, sy in ((x1, y1, 1, 1), (x2, y1, -1, 1),
                         (x2, y2, -1, -1), (x1, y2, 1, -1)):
        cv2.line(preview, (x, y), (x + sx * length, y), color, 2)
        cv2.line(preview, (x, y), (x, y + sy * length), color, 2)


def compose_preview(frame, corners, ready, light_on=False, notice=None):
    preview = frame.copy()
    _draw_guide(preview)
    if corners is not None:
        points = np.asarray(corners, np.int32).reshape((-1, 1, 2))
        color = (0, 255, 120) if ready else (0, 205, 255)
        cv2.polylines(preview, [points], True, color, 3, cv2.LINE_AA)
        for x, y in np.asarray(corners, np.int32):
            cv2.circle(preview, (int(x), int(y)), 5, color, -1)

    screen = _base_screen()
    screen[PREVIEW_Y:CONTROLS_Y] = preview
    if notice:
        cv2.rectangle(screen, (100, 74), (540, 114), (20, 24, 30), -1)
        text(screen, notice, (125, 84), (0, 210, 255), 19)
    else:
        hint = "已对准 · 点击识别" if ready else (
            "保持稳定，或直接点击识别" if corners is not None
            else "把身份证放入框内 · 可直接识别")
        color = (0, 255, 120) if ready else (215, 220, 225)
        text(screen, hint, (215 if ready else 185, 388), color, 20)

    cv2.line(screen, (0, CONTROLS_Y), (SCREEN_W, CONTROLS_Y), (55, 62, 70), 1)
    cv2.line(screen, (160, CONTROLS_Y), (160, SCREEN_H), (45, 52, 60), 1)
    cv2.line(screen, (480, CONTROLS_Y), (480, SCREEN_H), (45, 52, 60), 1)
    text(screen, "退出", (62, 440), (180, 185, 190), 19)
    text(screen, "识别", (298, 440), (245, 245, 245), 20)
    light_color = (0, 220, 255) if light_on else (155, 165, 172)
    text(screen, "补光：开" if light_on else "补光：关",
         (540, 440), light_color, 17)
    cv2.circle(screen, (320, 469), 7, (235, 235, 235), 2)
    return screen


def compose_processing(message="正在增强字段并识别…"):
    screen = _base_screen()
    cv2.rectangle(screen, (95, 145), (545, 330), (24, 29, 36), -1)
    cv2.rectangle(screen, (95, 145), (545, 330), (55, 68, 78), 1)
    text(screen, "K230 AI", (264, 182), (0, 255, 145), 24)
    text(screen, message, (178, 232), (230, 233, 235), 23)
    text(screen, "证件图只在内存中处理", (205, 278), (135, 145, 152), 18)
    return screen


def _value(result, field):
    value = result.get(field, "")
    return value if value else "未识别"


def _draw_wrapped(screen, value, x, y, chars=24, lines=2,
                  color=(230, 232, 235), height=19):
    value = str(value)
    for index in range(lines):
        part = value[index * chars:(index + 1) * chars]
        if not part:
            break
        text(screen, part, (x, y + index * (height + 7)), color, height)


def compose_result(result, rows, reveal=False, raw=False, notice=None,
                   status="reliable"):
    screen = _base_screen()
    cv2.rectangle(screen, (20, 70), (620, 405), (22, 27, 34), -1)
    cv2.rectangle(screen, (20, 70), (620, 405), (55, 66, 76), 1)

    if raw:
        text(screen, "OCR 原文（按阅读顺序）", (42, 88), (0, 235, 145), 20)
        if not rows:
            text(screen, "没有识别到文字，请改善光线后重扫", (112, 215),
                 (0, 190, 255), 21)
        for index, row in enumerate(rows[:10]):
            y = 124 + index * 26
            text(screen, row["text"][:34], (42, y), (225, 228, 232), 18)
            text(screen, "%d%%" % round(row["confidence"] * 100),
                 (558, y), (125, 135, 142), 15)
    elif status in ("back", "unreliable"):
        if status == "back":
            title = "检测到身份证反面"
            detail = "请翻到带头像和身份证号码的一面"
        else:
            title = "未可靠识别身份证正面"
            detail = "请重新对准边框，并避免模糊和反光"
        text(screen, title, (185, 168), (0, 195, 255), 27)
        text(screen, detail, (150, 222), (210, 215, 220), 21)
        text(screen, "点击下方“重新扫描”返回取景", (176, 276),
             (135, 150, 158), 18)
    else:
        text(screen, "姓名", (42, 88), (125, 145, 155), 17)
        text(screen, _value(result, "name"), (122, 84), (240, 242, 245), 23)
        text(screen, "性别", (335, 88), (125, 145, 155), 17)
        text(screen, _value(result, "sex"), (400, 84), (240, 242, 245), 22)
        text(screen, "民族", (470, 88), (125, 145, 155), 17)
        text(screen, _value(result, "ethnicity"), (535, 84), (240, 242, 245), 22)

        text(screen, "出生", (42, 132), (125, 145, 155), 17)
        text(screen, _value(result, "birth"), (122, 128), (235, 238, 240), 21)
        text(screen, "住址", (42, 178), (125, 145, 155), 17)
        _draw_wrapped(screen, _value(result, "address"), 122, 174, 23, 3)

        text(screen, "身份证号", (42, 276), (125, 145, 155), 17)
        number = result.get("id_number", "")
        shown = number if reveal else core.mask_id_number(number)
        number_color = (0, 245, 145) if result.get("id_valid") else (0, 195, 255)
        text(screen, shown, (142, 270), number_color, 22)
        check = "校验通过" if result.get("id_valid") else "请人工核对"
        text(screen, check, (485, 276), number_color, 16)

        count = result.get("field_count", 0)
        text(screen, "已识别 %d/6 个字段" % count, (42, 326),
             (160, 170, 178), 17)
        warnings = result.get("warnings", [])
        if warnings:
            text(screen, warnings[0][:28], (42, 362), (0, 185, 255), 17)
        elif count == 6 and result.get("id_valid"):
            text(screen, "结果完整，仍建议与原件核对", (42, 362),
                 (130, 185, 160), 17)

    if notice:
        text(screen, notice, (370, 42), (0, 205, 255), 16)
    cv2.line(screen, (0, CONTROLS_Y), (SCREEN_W, CONTROLS_Y), (55, 62, 70), 1)
    cv2.line(screen, (160, CONTROLS_Y), (160, SCREEN_H), (45, 52, 60), 1)
    cv2.line(screen, (480, CONTROLS_Y), (480, SCREEN_H), (45, 52, 60), 1)
    if status in ("back", "unreliable"):
        text(screen, "无有效号码", (45, 440), (105, 112, 120), 18)
    else:
        text(screen, "隐藏号码" if reveal else "显示号码", (45, 440),
             (180, 185, 190), 18)
    text(screen, "重新扫描", (280, 440), (245, 245, 245), 19)
    text(screen, "字段" if raw else "OCR原文", (548, 440),
         (180, 185, 190), 18)
    return screen


def _show(screen):
    Display.show(screen)


def _open_camera(flipped):
    cap = Sensor.Sensor(W, H)
    if not cap.isOpened():
        raise RuntimeError("无法打开摄像头")
    if flipped:
        cap.set_hmirror(1)
    return cap


def _release_camera(cap):
    if cap is not None:
        try:
            cap.release()
        except Exception:
            pass
    return None


# ----------------------------- 主循环 -------------------------------
def main():
    Display.init()
    flipped = direction.get_lcd() == 2
    if flipped:
        Display.set_rotation(2)

    cap = touch = key = None
    frame = last_frame = last_corners = result = rows = result_screen = None
    frame_candidates = deque(maxlen=3)
    runtime = OCRRuntime()
    try:
        cap = _open_camera(flipped)
        touch = TouchInput(flipped=flipped)
        key = Key()

        gate = core.CardStability()
        detect_tick = 0
        detected = None
        state = "preview"
        result, rows = core.parse_id_card([]), []
        result_status = "unreliable"
        reveal = raw = False
        result_screen = None
        result_notice = None
        light_on = False
        last_frame = last_corners = None
        ready = False
        notice = None
        notice_until = 0.0
        prev_key = False
        key_down_at = None
        quality_override_until = 0.0

        while True:
            now = time.monotonic()
            action = None
            point = touch.poll()
            if point is not None:
                action = core.touch_action(point[0], point[1], state)

            pressed = key.pressed
            if pressed and not prev_key:
                key_down_at = now
            elif pressed and key_down_at is not None and now - key_down_at >= 2.0:
                action = "EXIT"
                key_down_at = None
            elif not pressed and prev_key and key_down_at is not None:
                action = "SCAN" if state == "preview" else "RESCAN"
                key_down_at = None
            prev_key = pressed

            if action == "EXIT":
                print("[app] 用户退出，返回桌面")
                break

            if state == "preview" and action == "LIGHT":
                quality_override_until = 0.0
                requested = not light_on
                if key.set_light(requested):
                    light_on = requested
                    notice = "补光已开启" if light_on else "补光已关闭"
                else:
                    light_on = False
                    notice = "补光灯不可用"
                notice_until = now + 1.2
                key.pulse(now, 0.025)

            if state == "result":
                result_dirty = False
                if action == "REVEAL":
                    reveal = not reveal
                    result_dirty = True
                    key.pulse(now, 0.03)
                elif action == "RAW":
                    raw = not raw
                    result_dirty = True
                    key.pulse(now, 0.03)
                elif action == "RESCAN":
                    _show(compose_processing("正在释放 AI 并恢复取景…"))
                    touch.discard_pending()
                    if not runtime.unload():
                        notice = "AI 资源释放失败，请退出后重试"
                        notice_until = time.monotonic() + 2.5
                        result_screen = None
                        key.pulse(now)
                        continue
                    cap = _open_camera(flipped)
                    touch.discard_pending()
                    frame_candidates.clear()
                    state = "preview"
                    result, rows = core.parse_id_card([]), []
                    result_status = "unreliable"
                    result_screen = result_notice = None
                    reveal = raw = False
                    light_on = False
                    key.set_light(False)
                    gate.reset()
                    key.pulse(now)
                    continue
                visible_notice = notice if now < notice_until else None
                # 结果页内容通常完全静止。仅在显示模式或提示状态变化时重建
                # 画布，避免每 10 ms 分配约 0.88 MiB 的 NumPy/OpenCV 对象。
                if (result_screen is None or result_dirty or
                        visible_notice != result_notice):
                    result_screen = compose_result(
                        result, rows, reveal, raw, visible_notice,
                        result_status)
                    result_notice = visible_notice
                    _show(result_screen)
                key.update(now)
                time.sleep(0.01)
                continue

            ret, frame = cap.read()
            if not ret:
                key.update(now)
                continue
            last_frame = frame.copy()
            # 四边形检测隔帧执行，降低预览 CPU；手动识别始终有标准取景框兜底。
            if detect_tick % 2 == 0:
                detected = vision.find_id_card(frame)
            detect_tick += 1
            ready, smoothed = gate.update(detected, now)
            last_corners = smoothed
            snapshot_corners = (None if last_corners is None else
                                tuple((float(x), float(y))
                                      for x, y in last_corners))
            frame_candidates.append((last_frame, snapshot_corners))

            if action == "SCAN":
                key.pulse(now)
                touch.discard_pending()
                card = None
                try:
                    card, quality = vision.select_best_card(
                        list(frame_candidates), GUIDE_CORNERS)
                except Exception as exc:
                    print("[quality] 取景评分失败:",
                          type(exc).__name__, str(exc))
                    notice = "无法读取取景帧，请重新对准"
                    notice_until = time.monotonic() + 2.0
                    continue

                quality_issues = quality["issues"]
                if quality_issues and now >= quality_override_until:
                    issue_text = "、".join(quality_issues[:2])
                    notice = issue_text + " · 再点识别可继续"
                    notice_until = now + 2.5
                    quality_override_until = notice_until
                    print("[quality] score=%.3f sharp=%.1f bright=%.1f "
                          "contrast=%.1f highlight=%.3f issues=%s" % (
                              quality["score"], quality["sharpness"],
                              quality["brightness"], quality["contrast"],
                              quality["highlight_ratio"], issue_text))
                    card = None
                    continue

                quality_override_until = 0.0
                processing = ("正在增强三帧中最佳画面…"
                              if len(frame_candidates) > 1 else
                              "正在矫正并增强字段…")
                _show(compose_processing(processing))
                try:
                    # K230 的相机和 KPU 都依赖 CMA 连续内存。两者并存会在 OCR
                    # 首次推理时申请大块 CMA 失败，并让 nncase 原生库 SIGSEGV。
                    # 证件已复制到普通内存后先释放相机，再运行 KPU，避免争抢。
                    cap = _release_camera(cap)
                    frame_candidates.clear()
                    frame = last_frame = last_corners = detected = None
                    gate.reset()
                    gc.collect()
                    print("[memory] 摄像头已释放，开始 KPU OCR")
                    result, rows = recognize_card(runtime, card)
                    touch.discard_pending()
                    result_status = core.recognition_status(result, rows)
                    state = "result"
                    reveal = raw = False
                    if result_status == "reliable":
                        notice = ("识别完成 · 质量偏低请仔细核对"
                                  if quality_issues else
                                  "识别完成 · 原图未保存")
                    elif result_status == "partial":
                        notice = "部分字段已识别 · 请仔细核对"
                    elif result_status == "back":
                        notice = "检测到反面 · 请翻面重扫"
                    else:
                        notice = "未可靠识别 · 请重新扫描"
                    notice_until = time.monotonic() + 2.0
                    result_screen = compose_result(
                        result, rows, reveal, raw, notice, result_status)
                    result_notice = notice
                    _show(result_screen)
                    continue
                except Exception as exc:
                    print("[ocr] 识别失败:", type(exc).__name__, str(exc))
                    notice = "识别失败，请重新对准后再试"
                    notice_until = time.monotonic() + 2.0
                    # 此 try 也覆盖结果画布生成和 Display.show；它们可能在
                    # state 已切到 result 后失败。恢复相机前必须完整回到预览态，
                    # 否则下一次“重新扫描”会在现有 Sensor 未释放时再次打开相机。
                    state = "preview"
                    result, rows = core.parse_id_card([]), []
                    result_status = "unreliable"
                    result_screen = result_notice = None
                    reveal = raw = False
                    quality_override_until = 0.0
                    runtime_released = runtime.unload()
                finally:
                    # 完整证件只存在于取景帧/矫正图，识别后立即释放且不落盘。
                    light_on = False
                    key.set_light(False)
                    frame_candidates.clear()
                    card = frame = last_frame = last_corners = None
                    gc.collect()

                # 卸载失败意味着 KPU 仍持有 CMA；此时绝不能重新打开相机。
                if not runtime_released:
                    print("[memory] AI 资源未释放，退出以交还 CMA")
                    break

                # 只有可捕获且已安全释放 AI 的异常会到这里；恢复相机后重画。
                if cap is None:
                    _show(compose_processing("正在恢复取景…"))
                    cap = _open_camera(flipped)
                    touch.discard_pending()
                continue

            _show(compose_preview(frame, last_corners, ready, light_on,
                                  notice if now < notice_until else None))
            key.update(now)
    finally:
        runtime.unload()
        if touch is not None:
            touch.close()
        if key is not None:
            key.close()
        cap = _release_camera(cap)
        # 不保留上次帧或证件透视图。
        frame_candidates.clear()
        frame = last_frame = last_corners = result = rows = result_screen = None
        gc.collect()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("退出")
