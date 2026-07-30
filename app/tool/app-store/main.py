"""CyberCAM 640x480 touch UI for browsing and installing apps."""

from __future__ import annotations

import fcntl
import os
import select
import signal
import struct
import threading
import time
from collections import deque

import cv2
import numpy as np
from walnutpi import Display, direction

from installer import StoreService
from store_core import OperationCancelled, human_size, map_touch_coordinates


SCREEN_W, SCREEN_H = 640, 480
FONT = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
EV_SYN, EV_KEY, EV_ABS = 0x00, 0x01, 0x03
SYN_REPORT = 0x00
ABS_X, ABS_Y = 0x00, 0x01
BTN_TOUCH = 0x14A
PER_PAGE = 4

COLORS = {
    "bg": (31, 20, 9),
    "header": (45, 29, 12),
    "surface": (62, 43, 24),
    "surface_high": (82, 58, 31),
    "text": (247, 246, 241),
    "muted": (181, 174, 162),
    "faint": (126, 116, 102),
    "blue": (240, 201, 76),
    "green": (164, 224, 82),
    "amber": (86, 200, 246),
    "red": (133, 114, 255),
}

CATEGORY_NAMES = {
    "ai-agent": "AI 智能体",
    "ai-vision": "AI 视觉",
    "tool": "工具",
    "desktop-widget": "桌面组件",
    "smart-home": "智能家居",
    "education": "教育",
    "game": "游戏",
    "industrial": "工业",
    "other": "其他",
}

STATUS = {
    "available": ("可安装", COLORS["blue"]),
    "installed": ("已安装", COLORS["green"]),
    "update": ("可更新", COLORS["amber"]),
    "newer": ("本地较新", COLORS["amber"]),
    "repair": ("需修复", COLORS["red"]),
    "unmanaged": ("已存在", COLORS["amber"]),
    "error": ("源错误", COLORS["red"]),
}

ft = cv2.freetype.createFreeType2()
ft.loadFontData(FONT, 0)


def draw_text(image, value, origin, color=None, height=20):
    ft.putText(
        img=image,
        text=str(value),
        org=origin,
        fontHeight=height,
        color=color or COLORS["text"],
        thickness=-1,
        line_type=cv2.LINE_AA,
        bottomLeftOrigin=False,
    )


def text_width(value, height):
    try:
        return ft.getTextSize(str(value), height, -1)[0][0]
    except Exception:
        return len(str(value)) * height


def centered_text(image, value, y, color, height):
    draw_text(
        image,
        value,
        ((SCREEN_W - text_width(value, height)) // 2, y),
        color,
        height,
    )


def rounded_rect(image, top_left, bottom_right, radius, color):
    x1, y1 = top_left
    x2, y2 = bottom_right
    radius = max(1, min(radius, (x2 - x1) // 2, (y2 - y1) // 2))
    cv2.rectangle(image, (x1 + radius, y1), (x2 - radius, y2), color, -1)
    cv2.rectangle(image, (x1, y1 + radius), (x2, y2 - radius), color, -1)
    for center in (
        (x1 + radius, y1 + radius),
        (x2 - radius, y1 + radius),
        (x1 + radius, y2 - radius),
        (x2 - radius, y2 - radius),
    ):
        cv2.circle(image, center, radius, color, -1, cv2.LINE_AA)


def _eviocgabs(fd, axis):
    operation = (2 << 30) | (24 << 16) | (0x45 << 8) | (0x40 + axis)
    data = bytearray(24)
    try:
        fcntl.ioctl(fd, operation, data, True)
        _, low, high, _, _, _ = struct.unpack("<6i", bytes(data))
        if high > low:
            return low, high
    except OSError:
        pass
    return 0, 1


class TouchInput:
    def __init__(self, device="/dev/input/event0", flipped=False):
        self.device = device
        self.flipped = flipped
        self.points = deque(maxlen=8)
        self.running = False
        self._x = self._y = 0
        self._down = False
        self._queued = False
        self._thread = None
        try:
            fd = os.open(device, os.O_RDONLY)
            self._minx, self._maxx = _eviocgabs(fd, ABS_X)
            self._miny, self._maxy = _eviocgabs(fd, ABS_Y)
            os.close(fd)
            self.running = True
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
        except OSError as exc:
            print("[touch] 不可用：", exc)
            self._minx, self._maxx = 0, 1
            self._miny, self._maxy = 0, 1

    def _loop(self):
        try:
            fd = os.open(self.device, os.O_RDONLY | os.O_NONBLOCK)
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
                    if value:
                        self._down = True
                        self._queued = False
                    else:
                        if not self._queued:
                            self._queue_point()
                        self._down = False
                elif (
                    kind == EV_SYN
                    and code == SYN_REPORT
                    and self._down
                    and not self._queued
                ):
                    self._queue_point()
        finally:
            os.close(fd)

    def _queue_point(self):
        x, y = map_touch_coordinates(
            self._x,
            self._y,
            (self._minx, self._maxx),
            (self._miny, self._maxy),
            self.flipped,
        )
        self.points.append((x, y))
        self._queued = True

    def poll(self):
        try:
            return self.points.popleft()
        except IndexError:
            return None

    def close(self):
        self.running = False
        if self._thread is not None:
            self._thread.join(timeout=0.5)


class KeyInput:
    def __init__(self):
        self.key = self.beep = None
        self._off_at = 0.0
        try:
            import board
            from digitalio import DigitalInOut, Direction, Pull

            self.key = DigitalInOut(board.KEY)
            self.key.direction = Direction.INPUT
            self.key.pull = Pull.UP
            self.beep = DigitalInOut(board.BEEP)
            self.beep.direction = Direction.OUTPUT
            self.beep.value = 0
        except Exception as exc:
            print("[key] 不可用：", exc)

    @property
    def pressed(self):
        return self.key is not None and self.key.value == 0

    def pulse(self, now):
        if self.beep is not None:
            self.beep.value = 1
            self._off_at = now + 0.04

    def update(self, now):
        if self.beep is not None and self._off_at and now >= self._off_at:
            self.beep.value = 0
            self._off_at = 0.0

    def close(self):
        if self.beep is not None:
            self.beep.value = 0
        for device in (self.key, self.beep):
            deinit = getattr(device, "deinit", None)
            if callable(deinit):
                try:
                    deinit()
                except Exception:
                    pass


class StoreController:
    def __init__(self, service):
        self.service = service
        self.lock = threading.Lock()
        self.apps = service.load_initial()
        self.busy = False
        self.busy_kind = ""
        self.message = "正在连接官方应用源…"
        self.error = ""
        self.progress_done = 0
        self.progress_total = 0
        self.cancel_event = threading.Event()
        self.refresh_async()

    def snapshot(self):
        with self.lock:
            return {
                "apps": [dict(app) for app in self.apps],
                "busy": self.busy,
                "busy_kind": self.busy_kind,
                "message": self.message,
                "error": self.error,
                "progress_done": self.progress_done,
                "progress_total": self.progress_total,
            }

    def _start(self, kind, worker):
        with self.lock:
            if self.busy:
                return False
            self.busy = True
            self.busy_kind = kind
            self.error = ""
            self.progress_done = 0
            self.progress_total = 0
            self.cancel_event.clear()

        def run():
            try:
                worker()
            except OperationCancelled:
                with self.lock:
                    self.error = ""
                    self.message = "操作已取消，原应用未变更"
            except Exception as exc:
                print("[store]", kind, "失败：", exc)
                with self.lock:
                    self.error = str(exc)
                    self.message = "操作失败"
            finally:
                with self.lock:
                    self.busy = False
                    self.busy_kind = ""
                    self.cancel_event.clear()

        threading.Thread(target=run, name="app-store-" + kind, daemon=True).start()
        return True

    def refresh_async(self):
        def work():
            apps, warnings = self.service.refresh()
            with self.lock:
                self.apps = apps
                self.message = (
                    warnings[0] if warnings else "在线目录已更新 · 共 %d 个应用" % len(apps)
                )

        return self._start("refresh", work)

    def install_async(self, app):
        app_id = app["id"]

        def progress(done, total, message):
            if self.cancel_event.is_set():
                raise OperationCancelled("操作已取消")
            with self.lock:
                self.progress_done = done
                self.progress_total = total
                self.message = message

        def work():
            self.service.install(app, progress)
            with self.lock:
                self.apps = self.service.refresh_local_status(self.apps)
                self.message = "%s 已安装，桌面将自动显示" % app["name_cn"]

        return self._start("install:" + app_id, work)

    def request_cancel(self):
        with self.lock:
            if not self.busy or not self.busy_kind.startswith("install:"):
                return False
            self.cancel_event.set()
            self.message = "正在取消下载…"
            return True

    def uninstall_async(self, app):
        app_id = app["id"]

        def work():
            self.service.uninstall(app_id)
            with self.lock:
                self.apps = self.service.refresh_local_status(self.apps)
                self.message = "%s 已移到回收目录" % app["name_cn"]

        return self._start("uninstall:" + app_id, work)


def action_label(app, confirming=False):
    if confirming:
        return "再次点击确认"
    return {
        "available": "安装",
        "update": "更新",
        "newer": "降级",
        "repair": "修复",
        "installed": "卸载",
        "unmanaged": "重新安装",
        "error": "暂不可用",
    }.get(app.get("status"), "安装")


def compose(state, selected, page, confirm_id, confirm_until, now):
    image = np.zeros((SCREEN_H, SCREEN_W, 3), np.uint8)
    image[:] = COLORS["bg"]
    cv2.rectangle(image, (0, 0), (639, 67), COLORS["header"], -1)
    rounded_rect(image, (12, 12), (56, 56), 12, COLORS["surface_high"])
    draw_text(image, "×", (25, 13), COLORS["text"], 28)
    draw_text(image, "应用商店", (74, 13), COLORS["text"], 27)
    draw_text(image, "安全下载与安装", (198, 22), COLORS["faint"], 15)
    rounded_rect(image, (556, 13), (628, 55), 12, COLORS["surface_high"])
    draw_text(image, "刷新", (570, 24), COLORS["blue"], 17)

    apps = state["apps"]
    start = page * PER_PAGE
    visible = apps[start : start + PER_PAGE]
    for row, app in enumerate(visible):
        index = start + row
        y1 = 78 + row * 72
        y2 = y1 + 64
        selected_row = index == selected
        color = COLORS["surface_high"] if selected_row else COLORS["surface"]
        rounded_rect(image, (14, y1), (626, y2), 12, color)
        if selected_row:
            cv2.rectangle(image, (14, y1 + 12), (19, y2 - 12), COLORS["blue"], -1)
        draw_text(image, app["name_cn"], (32, y1 + 8), COLORS["text"], 22)
        category = CATEGORY_NAMES.get(app.get("category"), app.get("category", ""))
        details = []
        remote_version = app.get("version")
        installed_version = app.get("_installed_version")
        if (
            remote_version
            and remote_version != "rolling"
            and installed_version
            and installed_version != remote_version
        ):
            details.append("v%s → v%s" % (installed_version, remote_version))
        elif remote_version and remote_version != "rolling":
            details.append("v" + remote_version)
        elif installed_version:
            details.append("v" + installed_version)
        else:
            details.append("滚动版")
        details.append(app.get("summary_cn") or category)
        size = app.get("_download_size") or app.get("size") or 0
        if size:
            details.append(human_size(size))
        if not app.get("_trusted", False):
            details.append("第三方源")
        summary = " · ".join(details)
        draw_text(image, summary[:28], (32, y1 + 37), COLORS["muted"], 15)
        label, status_color = STATUS.get(
            app.get("status"), ("未知", COLORS["faint"])
        )
        width = max(70, text_width(label, 15) + 24)
        rounded_rect(
            image,
            (608 - width, y1 + 15),
            (608, y1 + 49),
            16,
            COLORS["bg"],
        )
        draw_text(image, label, (620 - width, y1 + 24), status_color, 15)

    total_pages = max(1, (len(apps) + PER_PAGE - 1) // PER_PAGE)
    selected_app = apps[selected] if apps and 0 <= selected < len(apps) else None
    rounded_rect(image, (14, 378), (142, 438), 14, COLORS["surface"])
    draw_text(image, "上一页", (48, 396), COLORS["text"], 18)
    rounded_rect(image, (498, 378), (626, 438), 14, COLORS["surface"])
    draw_text(image, "下一页", (528, 396), COLORS["text"], 18)
    action_color = COLORS["surface_high"]
    confirming = (
        selected_app is not None
        and selected_app["id"] == confirm_id
        and now < confirm_until
    )
    cancellable = state["busy"] and state["busy_kind"].startswith("install:")
    if cancellable:
        action_color = COLORS["red"]
    elif selected_app is not None and not state["busy"]:
        if confirming or selected_app.get("status") == "error":
            action_color = COLORS["red"]
        else:
            action_color = COLORS["blue"]
    rounded_rect(image, (156, 378), (484, 438), 14, action_color)
    if cancellable:
        label = "取消下载"
    else:
        label = (
            action_label(selected_app, confirming)
            if selected_app is not None
            else "没有可用应用"
        )
    centered = (156 + 484 - text_width(label, 20)) // 2
    draw_text(image, label, (centered, 396), COLORS["bg"], 20)

    if state["busy"] and state["progress_total"]:
        ratio = min(1.0, state["progress_done"] / max(1, state["progress_total"]))
        cv2.rectangle(image, (16, 446), (624, 452), COLORS["surface"], -1)
        cv2.rectangle(
            image, (16, 446), (16 + int(608 * ratio), 452), COLORS["green"], -1
        )
    message = state["error"] or state["message"]
    message_color = COLORS["red"] if state["error"] else COLORS["faint"]
    draw_text(image, message[:54], (16, 458), message_color, 13)
    draw_text(
        image,
        "%d/%d" % (page + 1, total_pages),
        (592, 458),
        COLORS["faint"],
        13,
    )
    return image


def main():
    shutdown = threading.Event()

    def request_shutdown(_signum, _frame):
        shutdown.set()

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)
    Display.init()
    flipped = direction.get_lcd() == 2
    if flipped:
        Display.set_rotation(2)
    touch = TouchInput(flipped=flipped)
    key = KeyInput()
    controller = StoreController(StoreService())
    selected = 0
    page = 0
    previous_key = False
    key_down_at = None
    long_fired = False
    confirm_id = ""
    confirm_until = 0.0

    def trigger_action(app, state, now):
        nonlocal confirm_id, confirm_until
        if state["busy"]:
            controller.request_cancel()
            return
        if app.get("status") == "error":
            return
        needs_confirmation = app.get("status") in (
            "installed",
            "unmanaged",
            "newer",
        ) or not app.get("_trusted", False)
        if needs_confirmation and not (
            confirm_id == app["id"] and now < confirm_until
        ):
            confirm_id = app["id"]
            confirm_until = now + 3.0
            with controller.lock:
                if app["status"] == "installed":
                    controller.message = "再次点击确认卸载"
                elif app["status"] == "unmanaged":
                    controller.message = "再次点击确认覆盖现有目录"
                elif app["status"] == "newer":
                    controller.message = "再次点击确认降级到 v%s" % app["version"]
                else:
                    controller.message = "第三方应用源 · 再次点击确认安装"
            return
        confirm_id = ""
        confirm_until = 0.0
        if app.get("status") == "installed":
            controller.uninstall_async(app)
        else:
            controller.install_async(app)
        key.pulse(now)

    try:
        while not shutdown.is_set():
            now = time.monotonic()
            state = controller.snapshot()
            apps = state["apps"]
            if apps:
                selected = max(0, min(selected, len(apps) - 1))
                page = selected // PER_PAGE
            else:
                selected = page = 0

            point = touch.poll()
            if point is not None:
                x, y = point
                if y < 68 and x < 68:
                    if state["busy"] and state["busy_kind"].startswith("install:"):
                        controller.request_cancel()
                    else:
                        break
                if y < 68 and x > 540:
                    controller.refresh_async()
                elif 78 <= y < 366:
                    row = (y - 78) // 72
                    index = page * PER_PAGE + row
                    if index < len(apps):
                        selected = index
                        confirm_id = ""
                elif 378 <= y <= 445:
                    if x < 150:
                        page = max(0, page - 1)
                        selected = min(len(apps) - 1, page * PER_PAGE) if apps else 0
                    elif x > 490:
                        total_pages = max(1, (len(apps) + PER_PAGE - 1) // PER_PAGE)
                        page = min(total_pages - 1, page + 1)
                        selected = min(len(apps) - 1, page * PER_PAGE) if apps else 0
                    elif apps:
                        trigger_action(apps[selected], state, now)

            pressed = key.pressed
            if pressed and not previous_key:
                key_down_at = now
                long_fired = False
            elif (
                pressed
                and key_down_at is not None
                and now - key_down_at >= 2.0
                and not long_fired
            ):
                if state["busy"] and state["busy_kind"].startswith("install:"):
                    controller.request_cancel()
                else:
                    break
                long_fired = True
            elif not pressed and previous_key and key_down_at is not None:
                if not long_fired and apps:
                    trigger_action(apps[selected], state, now)
                key_down_at = None
            previous_key = pressed

            if confirm_id and now >= confirm_until:
                confirm_id = ""
            key.update(now)
            Display.show(
                compose(state, selected, page, confirm_id, confirm_until, now)
            )
            time.sleep(0.06)
    finally:
        touch.close()
        key.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("退出")
