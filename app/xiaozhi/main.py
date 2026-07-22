"""CyberCAM K230 Xiaozhi voice assistant UI."""

import fcntl
import math
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

try:
    import board as hardware_board
    from digitalio import DigitalInOut, Direction, Pull

    GPIO_IMPORT_ERROR = None
except Exception as exc:
    hardware_board = DigitalInOut = Direction = Pull = None
    GPIO_IMPORT_ERROR = exc

from assistant import AssistantRuntime
from activation import ensure_identity
from protocol import (
    button_label,
    map_touch_coordinates,
    primary_action_enabled,
    touch_action,
)


SCREEN_W, SCREEN_H = 640, 480
FONT = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
EV_SYN, EV_KEY, EV_ABS = 0x00, 0x01, 0x03
SYN_REPORT = 0x00
ABS_X, ABS_Y = 0x00, 0x01
BTN_TOUCH = 0x14A

ft = cv2.freetype.createFreeType2()
ft.loadFontData(FONT, 0)


def draw_text(image, value, origin, color=(235, 238, 245), height=22):
    ft.putText(
        img=image,
        text=str(value),
        org=origin,
        fontHeight=height,
        color=color,
        thickness=-1,
        line_type=cv2.LINE_AA,
        bottomLeftOrigin=False,
    )


def _bgr(value):
    """Convert a CSS-style RGB hex color to OpenCV's BGR tuple."""
    value = value.lstrip("#")
    return tuple(int(value[index : index + 2], 16) for index in (4, 2, 0))


def _text_width(value, height):
    try:
        (width, _), _ = ft.getTextSize(str(value), height, -1)
        return width
    except Exception:
        return len(str(value)) * height


def draw_centered(image, value, y, color, height):
    draw_text(image, value, ((SCREEN_W - _text_width(value, height)) // 2, y), color, height)


def rounded_rect(image, top_left, bottom_right, radius, color):
    """Draw a filled rounded rectangle without requiring extra UI libraries."""
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


def mix_color(first, second, amount):
    return tuple(int(a * (1.0 - amount) + b * amount) for a, b in zip(first, second))


def wrap_text(value, width=25, lines=2):
    compact = " ".join(str(value or "").split())
    result = []
    while compact and len(result) < lines:
        take = min(width, len(compact))
        if take < len(compact):
            # Prefer a word boundary for Latin text without harming CJK text.
            space = compact.rfind(" ", 0, take + 1)
            if space >= max(4, take // 2):
                take = space
        result.append(compact[:take].strip())
        compact = compact[take:].strip()
    if compact and result:
        result[-1] = result[-1][:-1] + "…" if result[-1] else "…"
    return result


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
        self._fd = None
        self._x = self._y = 0
        self._down = False
        self._tap_queued = False
        try:
            fd = os.open(device, os.O_RDONLY)
            self._minx, self._maxx = _eviocgabs(fd, ABS_X)
            self._miny, self._maxy = _eviocgabs(fd, ABS_Y)
            os.close(fd)
            self.running = True
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
        except OSError as exc:
            print("[input] 触摸不可用:", exc)
            self._thread = None
            self._minx, self._maxx = 0, 1
            self._miny, self._maxy = 0, 1

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
                    if value:
                        self._down = True
                        self._tap_queued = False
                    else:
                        # Some touch drivers do not emit a SYN frame after the
                        # final coordinates. Keep release as a fallback.
                        if not self._tap_queued:
                            self._queue_tap()
                        self._down = False
                elif kind == EV_SYN and code == SYN_REPORT and self._down and not self._tap_queued:
                    # Respond on the first complete contact frame instead of
                    # waiting for lift-off. This feels immediate and also works
                    # with controllers that occasionally omit BTN_TOUCH up.
                    self._queue_tap()
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
            self._fd = None

    def _queue_tap(self):
        try:
            x, y = map_touch_coordinates(
                self._x,
                self._y,
                (self._minx, self._maxx),
                (self._miny, self._maxy),
                self.flipped,
            )
            self.points.append((x, y))
            self._tap_queued = True
            print("[touch] raw=(%d,%d) screen=(%d,%d)" % (self._x, self._y, x, y))
        except Exception as exc:
            print("[touch] 坐标转换失败:", exc)

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
        self._beep_off_at = 0.0
        try:
            if GPIO_IMPORT_ERROR is not None:
                raise GPIO_IMPORT_ERROR
            self.key = DigitalInOut(hardware_board.KEY)
            self.key.direction = Direction.INPUT
            self.key.pull = Pull.UP
            self.beep = DigitalInOut(hardware_board.BEEP)
            self.beep.direction = Direction.OUTPUT
            self.beep.value = 0
        except Exception as exc:
            print("[input] 实体键不可用:", exc)

    @property
    def pressed(self):
        return self.key is not None and self.key.value == 0

    def pulse(self, now, duration=0.04):
        if self.beep is not None:
            self.beep.value = 1
            self._beep_off_at = now + duration

    def update(self, now):
        if self._beep_off_at and now >= self._beep_off_at:
            self.beep.value = 0
            self._beep_off_at = 0.0

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
        self.key = self.beep = None


COLORS = {
    "bg": _bgr("#07111F"),
    "header": _bgr("#0B1729"),
    "surface": _bgr("#101F35"),
    "surface_high": _bgr("#172A45"),
    "text": _bgr("#F4F7FB"),
    "muted": _bgr("#91A1B8"),
    "faint": _bgr("#53657D"),
    "blue": _bgr("#4CC9F0"),
    "cyan": _bgr("#67E8F9"),
    "green": _bgr("#52E0A4"),
    "amber": _bgr("#F6C85F"),
    "purple": _bgr("#B69CFF"),
    "red": _bgr("#FF7285"),
}

PHASE_COLORS = {
    "starting": COLORS["amber"],
    "arming": COLORS["amber"],
    "connecting": COLORS["amber"],
    "activating": COLORS["purple"],
    "idle": COLORS["blue"],
    "listening": COLORS["green"],
    "thinking": COLORS["amber"],
    "speaking": COLORS["purple"],
    "error": COLORS["red"],
}

STATUS_LABELS = {
    "starting": "启动中",
    "arming": "准备唤醒",
    "connecting": "连接中",
    "activating": "待绑定",
    "idle": "已就绪",
    "listening": "聆听中",
    "thinking": "思考中",
    "speaking": "回答中",
    "error": "需重试",
}


def _draw_orb(screen, state, now):
    color = PHASE_COLORS.get(state.phase, COLORS["blue"])
    pulse = 0.5 + 0.5 * math.sin(now * 3.0)
    radius = 62 + int(4 * pulse)
    if state.phase == "listening":
        radius += int(12 * state.level)
    center = (320, 164)
    cv2.circle(screen, center, radius + 24, mix_color(COLORS["bg"], color, 0.12), -1, cv2.LINE_AA)
    cv2.circle(screen, center, radius + 10, mix_color(COLORS["bg"], color, 0.27), -1, cv2.LINE_AA)
    cv2.circle(screen, center, radius, COLORS["surface"], -1, cv2.LINE_AA)
    cv2.ellipse(
        screen,
        center,
        (radius + 10, radius + 10),
        -90 + int(now * 25) % 360,
        0,
        112,
        color,
        4,
        cv2.LINE_AA,
    )
    if state.phase == "listening":
        for index in range(7):
            factor = 0.25 + 0.75 * abs(math.sin(now * 7 + index * 0.72))
            height = 8 + int(31 * max(state.level, 0.10) * factor)
            x = 284 + index * 12
            cv2.line(screen, (x, 164 - height // 2), (x, 164 + height // 2), color, 5, cv2.LINE_AA)
    elif state.phase == "thinking":
        for index in range(3):
            lift = int(5 * abs(math.sin(now * 5 + index * 0.8)))
            cv2.circle(screen, (300 + index * 20, 164 - lift), 6, color, -1, cv2.LINE_AA)
    else:
        cv2.circle(screen, (297, 153), 7, color, -1, cv2.LINE_AA)
        cv2.circle(screen, (343, 153), 7, color, -1, cv2.LINE_AA)
        cv2.ellipse(screen, (320, 174), (20, 9), 0, 0, 180, color, 3, cv2.LINE_AA)


def _draw_microphone(screen, center, color):
    x, y = center
    cv2.ellipse(screen, (x, y - 3), (6, 11), 0, 0, 360, color, 2, cv2.LINE_AA)
    cv2.ellipse(screen, (x, y), (12, 14), 0, 15, 165, color, 2, cv2.LINE_AA)
    cv2.line(screen, (x, y + 14), (x, y + 19), color, 2, cv2.LINE_AA)
    cv2.line(screen, (x - 7, y + 19), (x + 7, y + 19), color, 2, cv2.LINE_AA)


def compose(state, now, pressed=False):
    screen = np.zeros((SCREEN_H, SCREEN_W, 3), np.uint8)
    screen[:] = COLORS["bg"]
    cv2.rectangle(screen, (0, 0), (639, 68), COLORS["header"], -1)
    rounded_rect(screen, (12, 12), (56, 56), 12, COLORS["surface_high"])
    draw_text(screen, "×", (25, 13), COLORS["text"], 28)
    draw_text(screen, "小智", (74, 13), COLORS["text"], 27)
    draw_text(screen, "K230 智能语音助手", (136, 22), COLORS["faint"], 15)

    status_color = PHASE_COLORS.get(state.phase, COLORS["blue"])
    status_text = STATUS_LABELS.get(state.phase, "运行中")
    badge_width = _text_width(status_text, 14) + 38
    badge_left = 622 - badge_width
    rounded_rect(screen, (badge_left, 18), (622, 50), 16, COLORS["surface"])
    cv2.circle(screen, (badge_left + 16, 34), 4, status_color, -1, cv2.LINE_AA)
    draw_text(screen, status_text, (badge_left + 27, 23), COLORS["muted"], 14)

    _draw_orb(screen, state, now)
    color = PHASE_COLORS.get(state.phase, COLORS["blue"])
    draw_centered(screen, state.title, 249, COLORS["text"], 25)
    draw_centered(screen, state.detail, 285, COLORS["muted"], 17)

    if state.phase == "activating":
        rounded_rect(screen, (160, 322), (480, 372), 14, COLORS["surface"])
        spaced = "  ".join(state.activation_code or "------")
        draw_centered(screen, spaced, 332, COLORS["purple"], 27)
    elif state.answer or state.transcript:
        message = state.answer if state.phase == "speaking" and state.answer else state.transcript
        rounded_rect(screen, (52, 316), (588, 374), 14, COLORS["surface"])
        for index, line in enumerate(wrap_text(message, 29, 2)):
            draw_centered(screen, line, 326 + index * 24, COLORS["text"], 17)

    disabled = not primary_action_enabled(state.phase)
    button_color = COLORS["surface_high"] if disabled else color
    if pressed and not disabled:
        button_color = mix_color(button_color, COLORS["text"], 0.18)
    rounded_rect(screen, (132, 389), (508, 449), 18, button_color)
    label = button_label(state.phase)
    label_color = COLORS["muted"] if disabled else COLORS["bg"]
    group_width = _text_width(label, 21) + (42 if not disabled else 0)
    label_left = (SCREEN_W - group_width) // 2
    if not disabled:
        _draw_microphone(screen, (label_left + 14, 414), label_color)
        label_left += 42
    draw_text(screen, label, (label_left, 405), label_color, 21)
    draw_centered(screen, "短按实体键操作 · 长按 2 秒退出", 458, COLORS["faint"], 12)
    return screen


def main():
    app_dir = os.path.dirname(os.path.abspath(__file__))
    shutdown_requested = threading.Event()

    def request_shutdown(_signum, _frame):
        shutdown_requested.set()

    # Deployment/service managers normally stop the app with SIGTERM. Convert
    # it into a normal loop exit so the assistant can terminate its local KWS
    # subprocess and release the microphone before Python exits.
    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)
    # Prime identity/file codecs before initializing the vendor display module.
    # Its native extension is unstable when Python performs certain first-time
    # imports after Display.init() in a detached launcher process.
    ensure_identity(os.path.join(app_dir, "device.json"))
    Display.init()
    flipped = direction.get_lcd() == 2
    if flipped:
        Display.set_rotation(2)
    # board/digitalio load native GPIO extensions. Finish those imports before
    # starting the touch reader thread; importing them concurrently can crash
    # Python 3.13 on the vendor image during detached/menu launches.
    key = KeyInput()
    touch = TouchInput(flipped=flipped)
    runtime = AssistantRuntime(app_dir)
    previous_key = False
    key_down_at = None
    long_fired = False
    last_state = None
    last_pressed = False
    next_animation = 0.0
    pressed_until = 0.0
    last_action_at = 0.0
    try:
        while not shutdown_requested.is_set():
            now = time.monotonic()
            action = None
            point = touch.poll()
            if point is not None:
                action = touch_action(*point)
                if action == "toggle":
                    pressed_until = now + 0.14

            pressed = key.pressed
            if pressed and not previous_key:
                key_down_at = now
                long_fired = False
            elif pressed and key_down_at is not None and now - key_down_at >= 2.0 and not long_fired:
                action = "exit"
                long_fired = True
            elif not pressed and previous_key and key_down_at is not None and not long_fired:
                action = "toggle"
                key_down_at = None
            previous_key = pressed

            if action == "exit":
                break
            if action == "toggle":
                phase = runtime.snapshot().phase
                if not primary_action_enabled(phase) or now - last_action_at < 0.35:
                    action = None
                else:
                    last_action_at = now
            if action == "toggle":
                command = "retry" if phase == "error" else "toggle"
                accepted = runtime.action(command)
                print("[ui] action=%s phase=%s accepted=%s" % (command, phase, accepted))
                key.pulse(now)

            state = runtime.snapshot()
            pressed = now < pressed_until
            animated = state.phase in (
                "starting", "arming", "connecting", "listening", "thinking", "speaking"
            )
            if state != last_state or pressed != last_pressed or (animated and now >= next_animation):
                Display.show(compose(state, now, pressed))
                last_state = state
                last_pressed = pressed
                next_animation = now + 0.08
            key.update(now)
            time.sleep(0.04)
    finally:
        runtime.close()
        touch.close()
        key.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("退出")
