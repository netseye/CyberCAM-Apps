'''
蓝牙展示 (BT Showcase) —— 多模式蓝牙能力展示(无摄像头)。

模式:
  1) 适配器   —— 本机蓝牙信息 + 可发现开关
  2) 扫描     —— 扫描周边设备,点选一个作为测距目标
  3) 测距     —— 对选中设备反复 l2ping,显示延迟/可达柱状图

交互:
  - 触摸左/右:上一/下一模式;触摸中间:模式内动作(开关/扫描/清空)
  - 板载按键 KEY:下一模式
'''

import cv2
import fcntl
import os
import struct
import threading
import time
from collections import deque

import numpy as np
from walnutpi import Display, direction

import core

# ----------------------------- 配置 ---------------------------------
W, H = 640, 480
FONT = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
FONT_H = 24
MODES = ["适配器", "扫描", "测距"]


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
    '''读 ABS 轴的 min/max(EVIOCGABS)。dir=READ=2(EVIOCGABS 是 _IOR;用 3=_IOWR 会被内核拒为 EINVAL)。'''
    op = (2 << 30) | (24 << 16) | (0x45 << 8) | (0x40 + axis)
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
    '''后台线程读 /dev/input/event0。落笔按区域入队;另支持长按检测。'''

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
                    self.taps.append((self._x, self._y))
                else:
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


# ----------------------------- 按键 ---------------------------------
class Key:
    def __init__(self):
        self._beep = self._led = None
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
            self._k = None
            print(f"[key] 不可用({e})")

    @property
    def pressed(self):
        return self._k is not None and self._k.value == 0

    def flash(self, led=False, dur=0.05):
        if self._beep is not None:
            self._beep.value = 1
        if led and self._led is not None:
            self._led.value = 1
        time.sleep(dur)
        if self._beep is not None:
            self._beep.value = 0
        if led and self._led is not None:
            self._led.value = 0


# ----------------------------- 模式渲染(占位)-------------------------
def render_adapter(img, key, actions):
    pass


def render_scan(img, key, actions):
    pass


def render_range(img, key, actions):
    pass


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

    touch = TouchInput(flipped=flipped)
    key = Key()

    mode = 0
    actions = []
    prev_key = False
    fc = 0
    fps = 0.0
    fps_t = time.monotonic()

    while True:
        now = time.monotonic()

        region = touch.poll_region()
        if region in ("LEFT", "RIGHT"):
            actions.clear()
            mode = (mode + (1 if region == "RIGHT" else -1)) % len(MODES)
            key.flash(led=True, dur=0.03)
        elif region == "CENTER":
            actions.append(("tap", "CENTER"))

        if key.pressed and not prev_key:
            actions.clear()
            mode = (mode + 1) % len(MODES)
            key.flash(led=True, dur=0.03)
        prev_key = key.pressed

        img = np.zeros((H, W, 3), np.uint8)
        if mode == 0:
            render_adapter(img, key, actions)
        elif mode == 1:
            render_scan(img, key, actions)
        else:
            render_range(img, key, actions)

        draw_hud(img, mode, fps)
        Display.show(img)
        try:
            IDE_show(img)
        except Exception:
            pass

        fc += 1
        if now - fps_t >= 1.0:
            fps = fc / (now - fps_t)
            fc = 0
            fps_t = now


def IDE_show(img):
    try:
        from walnutpi import IDE
        IDE.show(img)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("退出")
