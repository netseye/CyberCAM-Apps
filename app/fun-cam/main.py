'''
趣拍 (Fun Camera) —— 多模式创意相机。

模式:
  1) 光绘相机   —— 黑暗中用光源作画,逐帧叠加光迹
  2) AR 魔法贴纸 —— 识别到脸自动贴眼镜/帽子/胡子,跟随移动与倾斜
  3) 体感快门   —— 摇一摇拍照,叠加 AR 水平线辅助构图

交互:
  - 触摸左/右:上一/下一模式;触摸中间:模式内动作(清空/换贴纸/手动拍)
  - 板载按键 KEY:下一模式
  - M1 长按:保存画布;M3 摇一摇/中间拍照、长按重新校准陀螺仪
'''

import cv2
import fcntl
import math
import os
import struct
import threading
import time
from collections import deque

import numpy as np
from walnutpi import Display, Sensor, IDE, direction

import core

# ----------------------------- 配置 ---------------------------------
W, H = 640, 480
FONT = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
FONT_H = 24
MODES = ["光绘相机", "AR 魔法贴纸", "体感快门"]
SAVED_DIR = "/data/app/fun-cam/saved"


# ----------------------------- 文字 ---------------------------------
ft = cv2.freetype.createFreeType2()
ft.loadFontData(FONT, 0)


def text(img, s, org, color=(0, 255, 0), h=FONT_H):
    '''org 为文字左上角。'''
    ft.putText(img=img, text=str(s), org=org, fontHeight=h,
               color=color, thickness=-1, line_type=cv2.LINE_AA,
               bottomLeftOrigin=False)
    return img


# ----------------------------- 触摸输入 -----------------------------
EV_KEY, EV_ABS = 0x01, 0x03
ABS_X, ABS_Y = 0x00, 0x01
BTN_TOUCH = 0x14a


def _eviocgabs(fd, axis):
    '''读 ABS 轴的 min/max(EVIOCGABS)。'''
    op = (3 << 30) | (24 << 16) | (0x45 << 8) | (0x40 + axis)
    buf = bytearray(24)
    try:
        fcntl.ioctl(fd, op, buf, True)
        _, lo, hi, _, _, _ = struct.unpack('<6i', bytes(buf))
        if hi > lo:
            return lo, hi
    except OSError:
        pass
    return 0, 1


class TouchInput:
    '''后台线程读 /dev/input/event0。落笔时把 tap 入队;另支持长按检测。'''

    def __init__(self, device="/dev/input/event0", flipped=False, long_thr=1.2):
        self.device = device
        self.flipped = flipped
        self.long_thr = long_thr
        self.taps = deque()
        self.running = False
        self._x = self._y = 0
        self._minx = self._maxx = None
        self._down = False
        self._down_t = 0.0
        self._long_fired = False
        try:
            fd = os.open(device, os.O_RDONLY)
            self._minx, self._maxx = _eviocgabs(fd, ABS_X)
            os.close(fd)
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self.running = True
            self._thread.start()
            print(f"[touch] {device} 范围 x:[{self._minx},{self._maxx}]")
        except OSError as e:
            self._thread = None
            print(f"[touch] 不可用({e}),仅用按键")

    def _loop(self):
        try:
            fd = os.open(self.device, os.O_RDONLY)
        except OSError:
            return
        while self.running:
            try:
                data = os.read(fd, 24)
            except OSError:
                break
            if len(data) < 24:
                continue
            _, _, typ, code, val = struct.unpack('<QQHHi', data)
            if typ == EV_ABS and code == ABS_X:
                self._x = val
            elif typ == EV_ABS and code == ABS_Y:
                self._y = val
            elif typ == EV_KEY and code == BTN_TOUCH:
                if val == 1:
                    self._down = True
                    self._down_t = time.monotonic()
                    self._long_fired = False
                else:
                    # 抬笔入队:此时 ABS_X/Y 是本次接触的稳定坐标(原实现漏了这步,
                    # 导致 poll_region 永远取不到 tap,触摸切模式/点按全部失效)
                    if self._down:
                        self.taps.append((self._x, self._y))
                    self._down = False

    def poll_region(self):
        '''取一次落笔,返回 "LEFT"/"CENTER"/"RIGHT" 或 None。'''
        if not self.taps or self._maxx is None:
            return None
        x, _ = self.taps.popleft()
        nx = (x - self._minx) / (self._maxx - self._minx)
        nx = max(0.0, min(1.0, nx))
        if self.flipped:
            nx = 1.0 - nx
        if nx < 0.33:
            return "LEFT"
        if nx > 0.66:
            return "RIGHT"
        return "CENTER"

    def check_longpress(self, now):
        '''按住超过阈值时返回一次 True(每次按下只触发一次)。'''
        if self._down and not self._long_fired and (now - self._down_t) > self.long_thr:
            self._long_fired = True
            return True
        return False


# ----------------------------- 按键 ---------------------------------
class Key:
    def __init__(self):
        self._k = self._beep = self._led = None
        try:
            import board
            from digitalio import DigitalInOut, Direction, Pull
            k = DigitalInOut(board.KEY); k.direction = Direction.INPUT; k.pull = Pull.UP
            self._k = k
            b = DigitalInOut(board.BEEP); b.direction = Direction.OUTPUT
            self._beep = b
            l = DigitalInOut(board.LED); l.direction = Direction.OUTPUT
            self._led = l
            print("[key] 板载按键/蜂鸣/LED 已就绪")
        except Exception as e:
            print(f"[key] 不可用({e})")

    @property
    def pressed(self):
        return self._k is not None and self._k.value == 0

    def beep(self, on):
        if self._beep is not None:
            self._beep.value = 1 if on else 0

    def led(self, on):
        if self._led is not None:
            self._led.value = 1 if on else 0

    def flash(self, led=False, dur=0.05):
        self.beep(True)
        if led:
            self.led(True)
        time.sleep(dur)
        self.beep(False)
        if led:
            self.led(False)


def save_shot(img, tag):
    os.makedirs(SAVED_DIR, exist_ok=True)
    path = os.path.join(SAVED_DIR, f"{tag}_{int(time.time())}.jpg")
    try:
        cv2.imwrite(path, img)
        print("saved", path)
        return True
    except Exception as e:
        print("save failed", e)
        return False


# ----------------------------- 人脸检测(M2)------------------------------
face_ok = False
detector = None
for _mp, _ap in [("./face_detection_320.kmodel", "./prior_data_320.bin"),
                 ("/data/app/fun-cam/face_detection_320.kmodel", "/data/app/fun-cam/prior_data_320.bin")]:
    if os.path.exists(_mp) and os.path.exists(_ap):
        try:
            from walnutpi import kpu
            detector = kpu.FACE_DETECT(_mp, _ap, 320)
            face_ok = True
            print("[face] 模型已加载:", _mp)
        except Exception as e:
            print("[face] 模型加载失败:", e)
        break

STICKERS = ["眼镜", "帽子", "眼镜+帽子", "胡子", "关"]
ar_sticker = 0


# ----------------------------- IMU(M3)------------------------------
imu_ok = False
imu = None
imu_roll = 0.0
imu_pitch = 0.0
imu_last_t = None
shake = core.ShakeDetector(spike_g=2.5, debounce_s=1.5)
flash_until = 0.0
_drawn_roll = None    # 水平线显示死区:上次实际画线的 roll(deg)
_disp_roll = None     # 显示端 EMA 平滑后的 roll(deg);None=未初始化(首帧用原值播种)
_snap_in = False      # 吸附水平迟滞状态:当前是否处在「已对齐」吸附区
_disp_pitch = None    # 显示端 EMA 平滑后的 pitch(deg)
_drawn_pitch = None   # pitch 显示死区:上次显示的 pitch(deg)
_recal_guard = 0.0    # 重新校准后短暂忽略 tap(长按抬笔会产生一次 tap)
try:
    from qmi8658 import open_imu
    imu = open_imu()
    imu_ok = True
    # 陀螺仪零偏校准放到 main() 里做(带「请保持静止」屏显提示),这里不校准
except Exception as e:
    print("[imu] 初始化失败:", e)


def _do_calibrate(n=150):
    '''校准陀螺仪零偏,并按当前加速度重置滤波器(避免校准后跳变)。无 UI。'''
    global imu_roll, imu_pitch, imu_last_t, _drawn_roll, _disp_roll, _snap_in, _disp_pitch, _drawn_pitch
    imu.calibrate(n)
    ax, ay, az, *_ = imu.read()
    imu_roll = core.accel_to_roll(ax, ay, az)
    imu_pitch = core.accel_to_pitch(ax, ay, az)
    imu_last_t = None
    _drawn_roll = None
    _disp_roll = None      # 重置显示平滑,让 EMA 从新角度重新播种
    _snap_in = False
    _disp_pitch = None
    _drawn_pitch = None


def _startup_calibrate_prompt():
    '''开机:全屏提示并校准一次(用户看得见「正在校准」,且 2σ 鲁棒)。'''
    if not (imu_ok and getattr(imu, "has_gyro", False)):
        return
    img = np.zeros((H, W, 3), np.uint8)
    text(img, "陀螺仪校准中", (W // 2 - 110, H // 2 - 34), (0, 255, 120), 30)
    text(img, "请保持设备静止 ~1 秒", (W // 2 - 170, H // 2 + 8), (200, 200, 200), 22)
    Display.show(img)
    _do_calibrate(180)


def _recalibrate_gyro(img):
    '''M3 长按:在相机画面上提示并重新校准陀螺仪零偏。'''
    if not (imu_ok and getattr(imu, "has_gyro", False)):
        return
    cv2.rectangle(img, (0, H // 2 - 30), (W, H // 2 + 30), (0, 0, 0), -1)
    text(img, "重新校准中,请保持静止…", (W // 2 - 200, H // 2 - 12), (0, 200, 255), 22)
    Display.show(img)
    _do_calibrate(150)


# ----------------------------- 模式渲染 --------------------------------
lp_canvas = None  # 光绘画布(持久累积)


def render_light(img, key, actions):
    '''M1 光绘:逐帧叠加亮部到画布;中间触摸清空,长按保存。返回合成画面。'''
    global lp_canvas
    if lp_canvas is None or lp_canvas.shape != img.shape:
        lp_canvas = np.zeros_like(img)
    lp_canvas = core.light_paint_accumulate(lp_canvas, img, thr=160)
    while actions:
        kind = actions.pop(0)
        if kind[0] == "tap" and kind[1] == "CENTER":
            lp_canvas = np.zeros_like(img)
            key.flash(dur=0.03)
        elif kind[0] == "longpress":
            if save_shot(lp_canvas, "light"):
                key.flash(led=True, dur=0.08)
    out = lp_canvas.copy()
    pip = cv2.resize(img, (120, 90))           # 右下角实时取景 PiP
    out[H - 90:H, W - 120:W] = pip
    text(out, "取景", (W - 118, H - 108), (160, 160, 160), 16)
    text(out, "中间清空 · 长按保存", (10, 36), (200, 200, 200), 20)
    return out


def _draw_glasses(img, g):
    '''彩色镜框 + 琥珀镜片 + 高光,镜片中心用真实眼关键点(倾斜也跟)。'''
    ang = math.degrees(g['angle'])
    d = g['eye_dist']
    r = max(4, int(d * 0.55))
    rh = max(4, int(r * 0.8))
    for (ex, ey) in (g['left_eye'], g['right_eye']):
        c = (int(ex), int(ey))
        cv2.ellipse(img, c, (r, rh), ang, 0, 360, (150, 110, 40), -1)    # 琥珀镜片填充
        cv2.ellipse(img, c, (r, rh), ang, 0, 360, (40, 40, 180), 4)      # 蓝色粗框
        cv2.circle(img, (int(ex - r * 0.32), int(ey - rh * 0.32)),
                   max(2, r // 5), (245, 245, 245), -1)                  # 左上高光
    lex, ley = g['left_eye']
    rex, rey = g['right_eye']
    cv2.line(img, (int(lex + r * 0.7), int(ley)), (int(rex - r * 0.7), int(rey)),
             (40, 40, 180), 4)                                           # 鼻梁


def _draw_hat(img, g):
    '''红色三角帽 + 帽檐 + 帽顶小球,水平居中于人脸框上方。'''
    cx, top = g['hat_center']
    w = int(g['hat_w'] * 0.6)
    pts = np.array([[int(cx), int(top - w * 0.7)],
                    [int(cx - w / 2), int(top)],
                    [int(cx + w / 2), int(top)]], np.int32)
    cv2.fillPoly(img, [pts], (60, 60, 220))                              # 红色帽体
    cv2.line(img, (int(cx - w * 0.7), int(top)), (int(cx + w * 0.7), int(top)),
             (40, 40, 160), 4)                                           # 帽檐
    cv2.circle(img, (int(cx), int(top - w * 0.7)),
               max(3, w // 8), (30, 200, 255), -1)                       # 帽顶黄球


def _draw_mustache(img, g):
    '''深棕色八字胡,两片椭圆覆在嘴巴上方。'''
    cx, cy = g['mouth_center']
    w = int(g['mouth_w'] * 0.55)
    h = max(5, int(w * 0.28))
    cv2.ellipse(img, (int(cx - w / 2), int(cy - 3)), (int(w / 2), h), 0, 0, 360, (45, 35, 30), -1)
    cv2.ellipse(img, (int(cx + w / 2), int(cy - 3)), (int(w / 2), h), 0, 0, 360, (45, 35, 30), -1)


def render_ar(img, key, actions):
    '''M2 AR 贴纸:检测到脸后画矢量眼镜/帽子/胡子,跟随眼睛角度;中间触摸循环贴纸。'''
    global ar_sticker
    while actions:
        kind = actions.pop(0)
        if kind[0] == "tap" and kind[1] == "CENTER":
            ar_sticker = (ar_sticker + 1) % len(STICKERS)
            key.flash(dur=0.03)
    if not face_ok:
        text(img, "人脸模型缺失", (W // 2 - 90, H // 2), (0, 160, 255), 26)
        text(img, "贴纸:无 · 中间切换", (10, 36), (255, 200, 0), 20)
        return
    results = detector.run(img, reliability_threshold=0.6, nms_threshold=0.7) if detector else []
    kind = STICKERS[ar_sticker]
    if not results and kind != "关":
        text(img, "请把脸对准镜头", (W // 2 - 110, H // 2), (160, 160, 160), 24)
    for r in results:
        g = core.face_geometry((r.x, r.y, r.w, r.h),
                               (r.left_eye.x, r.left_eye.y), (r.right_eye.x, r.right_eye.y),
                               (r.nose.x, r.nose.y),
                               (r.left_mouth.x, r.left_mouth.y), (r.right_mouth.x, r.right_mouth.y))
        if kind in ("眼镜", "眼镜+帽子"):
            _draw_glasses(img, g)
        if kind in ("帽子", "眼镜+帽子"):
            _draw_hat(img, g)
        if kind == "胡子":
            _draw_mustache(img, g)
    text(img, f"贴纸:{kind} · 中间切换", (10, 36), (255, 200, 0), 20)


def render_motion(img, key, actions):
    '''M3 体感快门:摇一摇或中间触摸拍照(蜂鸣+LED+白闪+保存),叠加 AR 水平线辅助构图。'''
    global imu_roll, imu_pitch, flash_until, imu_last_t, _recal_guard, _drawn_roll, _disp_roll, _snap_in, _disp_pitch, _drawn_pitch
    shot = None
    now = time.time()
    if imu_ok:
        ax, ay, az, gx, gy, gz = imu.read()
        a_mag = core.accel_to_a_mag(ax, ay, az)
        roll_a = core.accel_to_roll(ax, ay, az)
        pitch_a = core.accel_to_pitch(ax, ay, az)
        # 真实帧间隔;首帧或卡顿(>0.2s)后兜底 0.03,避免积分跳变
        if imu_last_t is None or not (0 < now - imu_last_t <= 0.2):
            dt = 0.03
        else:
            dt = now - imu_last_t
        imu_last_t = now
        if getattr(imu, "has_gyro", False):
            imu_roll = core.complementary(imu_roll, gx, roll_a, dt)
            imu_pitch = core.complementary(imu_pitch, gy, pitch_a, dt)
        else:
            imu_roll, imu_pitch = roll_a, pitch_a
        if shake.update(a_mag, now):
            shot = img
    # 中间触摸 = 手动快门;长按 = 重新校准陀螺仪(校准后 0.6s 内忽略抬笔 tap)
    while actions:
        kind = actions.pop(0)
        if kind[0] == "longpress":
            _recalibrate_gyro(img)
            _recal_guard = now + 0.6
        elif kind[0] == "tap" and now < _recal_guard:
            pass
        elif kind[0] == "tap":
            shot = img
    if shot is not None:
        if save_shot(shot, "snap"):
            key.flash(led=True, dur=0.06)
        flash_until = now + 0.15
    # AR 水平构图线(过中心,随 roll 旋转)。三段「准专业级」稳定处理
    # (对标 Sony/Canon/iPhone 的电子水平仪:融合 ✅ + 显示低通 + 吸附死区 + 到位反馈):
    #  ① 显示端 EMA(~0.3s)压手抖/残噪;② 吸附水平(进区 ±1°、出区 ±2° 迟滞)
    #     锁成 0° 并蜂鸣确认;③ 量化到 0.5° 台阶、变化 <0.3° 不重绘。
    roll_deg = math.degrees(imu_roll)
    if _disp_roll is None:
        _disp_roll = roll_deg
    else:
        _disp_roll = 0.88 * _disp_roll + 0.12 * roll_deg          # ① EMA 低通
    a = abs(_disp_roll)
    if _snap_in:
        if a > 2.0:                                                # ② 出吸附区(迟滞防边界抖)
            _snap_in = False
    elif a < 1.0:                                                  #    进吸附区
        _snap_in = True
        key.flash(dur=0.04)                                        #    短鸣=已对齐(iPhone 触觉的等价)
    shown = 0.0 if _snap_in else _disp_roll
    # 滞回死区(Schmitt 触发):只有当平滑值偏离「上次提交值」≥1° 才重新量化提交。
    # 单纯 round 量化在均值卡在 1° 边界(如 −1.5°)时,±0.1° 抖动会让它在相邻量子间
    # 来回跳(实测 23~69 次/60s);滞回与边界位置无关,静置时恒为 0 次重绘。
    if _drawn_roll is None or abs(shown - _drawn_roll) >= 1.0:
        _drawn_roll = round(shown / 1.0) * 1.0
    col = (0, 255, 120) if _snap_in else core.level_color(_drawn_roll)
    cx, cy = W // 2, H // 2
    ll = 160
    droll = math.radians(_drawn_roll)
    dx = int(ll * math.cos(droll))
    dy = int(ll * math.sin(droll))
    thick = 3 if _snap_in else 2                                   # 锁定时加粗,强化「已对齐」观感
    cv2.line(img, (cx - dx, cy - dy), (cx + dx, cy + dy), col, thick)
    cv2.line(img, (cx, cy - 12), (cx, cy + 12), col, thick)
    # 俯仰同样走 EMA + 死区(量化到 1°,辅助信息无需 0.5° 精度),否则十分位每帧乱跳
    pitch_deg = math.degrees(imu_pitch)
    if _disp_pitch is None:
        _disp_pitch = pitch_deg
    else:
        _disp_pitch = 0.88 * _disp_pitch + 0.12 * pitch_deg
    # 同 roll:滞回死区,偏离提交值 ≥1° 才重新量化(防边界抖)
    if _drawn_pitch is None or abs(_disp_pitch - _drawn_pitch) >= 1.0:
        _drawn_pitch = round(_disp_pitch / 1.0) * 1.0
    tag = "(已对齐) " if _snap_in else ""
    text(img, f"水平 {tag}{_drawn_roll:+5.1f}°  俯仰 {_drawn_pitch:+5.1f}°", (10, 36), col, 20)
    has_gyro = imu_ok and getattr(imu, "has_gyro", False)
    if not imu_ok:
        text(img, "IMU 不可用 · 中间手动拍", (W // 2 - 140, H // 2), (0, 160, 255), 22)
    elif not has_gyro:
        text(img, "仅加速度(无陀螺仪)· 水平线会抖", (W // 2 - 160, H // 2), (0, 160, 255), 20)
    else:
        text(img, "摇一摇/中间拍照 · 长按重新校准", (W // 2 - 170, H - 50), (180, 180, 180), 20)
    # 拍照白闪(仅显示,不影响已保存的原图)
    if now < flash_until:
        cv2.rectangle(img, (0, 0), (W, H), (255, 255, 255), -1)


def draw_hud(img, mode, fps):
    text(img, f"[{mode + 1}/3] {MODES[mode]}", (10, 8), (0, 255, 120), 22)
    text(img, f"FPS:{fps:4.1f}", (W - 90, 8), (160, 160, 160), 20)
    text(img, "左/右切换 · 中间模式内动作", (W // 2 - 150, H - 28), (110, 110, 110), 18)


# ----------------------------- 主程序 ------------------------------
def main():
    Display.init()
    flipped = (direction.get_lcd() == 2)
    if flipped:
        Display.set_rotation(2)

    _startup_calibrate_prompt()   # 屏显「请保持静止」并校准陀螺仪零偏

    cap = Sensor.Sensor(W, H)
    if not cap.isOpened():
        print("Cannot open camera")
        return
    if flipped:
        cap.set_hmirror(1)

    touch = TouchInput(flipped=flipped)
    key = Key()

    mode = 0
    actions = []           # 当前模式的动作队列 ('tap','CENTER') / ('longpress',)
    prev_key = False
    fc = 0
    fps = 0.0
    last = time.monotonic()
    fps_t = last

    while True:
        now = time.monotonic()
        ret, img = cap.read()
        if not ret:
            continue

        # ---- 输入 ----
        region = touch.poll_region()
        if region in ("LEFT", "RIGHT"):
            actions.clear()                       # 切模式,丢弃未消费的模式内动作
            mode = (mode + (1 if region == "RIGHT" else -1)) % len(MODES)
            key.flash(led=True, dur=0.03)
        elif region == "CENTER":
            actions.append(("tap", "CENTER"))
        if touch.check_longpress(now):
            actions.append(("longpress",))

        if key.pressed and not prev_key:
            actions.clear()
            mode = (mode + 1) % len(MODES)
            key.flash(led=True, dur=0.03)
        prev_key = key.pressed

        # ---- 渲染 ----
        if mode == 0:
            img = render_light(img, key, actions)
        elif mode == 1:
            render_ar(img, key, actions)
        else:
            render_motion(img, key, actions)

        draw_hud(img, mode, fps)
        Display.show(img)
        try:
            IDE.show(img)
        except Exception:
            pass

        fc += 1
        if now - fps_t >= 1.0:
            fps = fc / (now - fps_t)
            fc = 0
            fps_t = now


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("退出")
