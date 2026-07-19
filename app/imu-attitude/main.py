'''
实验名称:IMU 姿态仪(多模式)
实验平台:CyberCAM
说明:读取板载 QMI8658A(6轴)做实时姿态可视化,触摸/按键/摇一摇交互。

模式:
  1) 3D 立方体 —— 随设备姿态转动(加速度+陀螺仪互补滤波)
  2) 水平仪   —— 气泡倾角仪
  3) 波形     —— |a| 与陀螺仪三轴滚动波形

交互:
  - 触摸屏:点左 1/3 上一模式,右 1/3 下一模式,中间重新校准
  - 板载按键(board.KEY):切换下一模式
  - 摇一摇:|a| 突变 -> 重新校准陀螺仪零偏(蜂鸣提示)
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
from walnutpi import Display, IDE, direction

from qmi8658 import open_imu

# ----------------------------- 配置 ---------------------------------
W, H = 640, 480                 # 画布(show_size)
FONT = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
FONT_H = 24
SHAKE_G = 2.2                   # 摇一摇阈值
SHAKE_COOLDOWN = 1.5            # 摇一摇冷却(秒)
MODES = ["3D 立方体", "水平仪", "波形"]

# 互补滤波系数(陀螺仪权重)
ALPHA = 0.98
DEG = math.pi / 180.0
RAD = 180.0 / math.pi


# ----------------------------- 文字 ---------------------------------
ft = cv2.freetype.createFreeType2()
ft.loadFontData(FONT, 0)


def text(img, s, org, color=(0, 255, 0), h=FONT_H):
    '''在图上写中英文(org 为文字左上角坐标)。'''
    ft.putText(img=img, text=str(s), org=org, fontHeight=h,
               color=color, thickness=-1, line_type=cv2.LINE_AA,
               bottomLeftOrigin=False)
    return img


def text_size(s, h=FONT_H):
    return ft.getTextSize(str(s), h, -1)[0]


# ----------------------------- 3D 工具 ------------------------------
def _Rx(t):
    c, s = math.cos(t), math.sin(t)
    return ((1, 0, 0), (0, c, -s), (0, s, c))


def _Ry(t):
    c, s = math.cos(t), math.sin(t)
    return ((c, 0, s), (0, 1, 0), (-s, 0, c))


def _Rz(t):
    c, s = math.cos(t), math.sin(t)
    return ((c, -s, 0), (s, c, 0), (0, 0, 1))


def _matmul(A, B):
    return tuple(tuple(sum(A[i][k] * B[k][j] for k in range(3))
                       for j in range(3)) for i in range(3))


def _mv(M, v):
    return (M[0][0] * v[0] + M[0][1] * v[1] + M[0][2] * v[2],
            M[1][0] * v[0] + M[1][1] * v[1] + M[1][2] * v[2],
            M[2][0] * v[0] + M[2][1] * v[1] + M[2][2] * v[2])


CUBE_V = [(-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
          (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)]
CUBE_E = [(0, 1), (1, 2), (2, 3), (3, 0),
          (4, 5), (5, 6), (6, 7), (7, 4),
          (0, 4), (1, 5), (2, 6), (3, 7)]


# ----------------------------- 触摸输入 -----------------------------
# Linux input 协议常量
EV_SYN, EV_KEY, EV_ABS = 0x00, 0x01, 0x03
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
    '''后台线程读 /dev/input/event0,落笔时把 (raw_x, raw_y) 入队。'''

    def __init__(self, device="/dev/input/event0", flipped=False):
        self.device = device
        self.flipped = flipped
        self.taps = deque()
        self.running = False
        self._x = self._y = 0
        self._minx = self._maxx = None
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
            print(f"[touch] 不可用({e}),仅用按键+摇一摇")

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
            elif typ == EV_KEY and code == BTN_TOUCH and val == 0:
                self.taps.append((self._x, self._y))

    def poll_region(self):
        '''取一次 tap,返回区域: -1 上一模式 / 0 中间(校准) / 1 下一模式 / None。'''
        if not self.taps or self._maxx is None:
            return None
        x, _ = self.taps.popleft()
        nx = (x - self._minx) / (self._maxx - self._minx)
        nx = max(0.0, min(1.0, nx))
        if self.flipped:
            nx = 1.0 - nx
        if nx < 0.33:
            return -1
        if nx > 0.66:
            return 1
        return 0


# ----------------------------- 按键 ---------------------------------
class Key:
    def __init__(self):
        self._k = None
        try:
            import board
            from digitalio import DigitalInOut, Direction, Pull
            self._dio = DigitalInOut
            self._Dir = Direction
            self._Pull = Pull
            self._board = board
            k = DigitalInOut(board.KEY)
            k.direction = Direction.INPUT
            k.pull = Pull.UP
            self._k = k
            b = DigitalInOut(board.BEEP)
            b.direction = Direction.OUTPUT
            self._beep = b
            l = DigitalInOut(board.LED)
            l.direction = Direction.OUTPUT
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


# ----------------------------- 渲染:立方体 -------------------------
def render_cube(img, roll, pitch, yaw, chip):
    cx, cy, s = W // 2, H // 2 - 10, 100
    R = _matmul(_Rz(yaw), _matmul(_Ry(pitch), _Rx(roll)))
    proj = []
    for v in CUBE_V:
        X, Y, Z = _mv(R, v)
        f = 1.0 / (1.0 - Z * 0.15)         # 弱透视
        proj.append((int(cx + s * X * f), int(cy - s * Y * f), Z))
    # 边:深度越前越亮
    for a, b in CUBE_E:
        za, zb = proj[a][2], proj[b][2]
        t = (za + zb) / 2 * 0.5 + 0.5      # 0..1
        col = (int(60 + 195 * t), int(120 + 135 * t), int(200 + 55 * t))
        cv2.line(img, proj[a][:2], proj[b][:2], col, 3, cv2.LINE_AA)
    # 本体坐标轴(红X 绿Y 蓝Z)
    o = _mv(R, (0, 0, 0))
    for vec, col in (((0.6, 0, 0), (0, 0, 255)),
                     ((0, 0.6, 0), (0, 255, 0)),
                     ((0, 0, 0.6), (255, 80, 0))):
        p0 = _project(cx, cy, s, o)
        p1 = _project(cx, cy, s, _mv(R, vec))
        cv2.arrowedLine(img, p0, p1, col, 2, cv2.LINE_AA, tipLength=0.2)
    text(img, "姿态(随设备)", (cx - 70, cy + s + 30), (200, 200, 200), 22)
    if not chip:
        text(img, "(无陀螺仪 yaw 不更新)", (cx - 110, cy + s + 58), (120, 120, 120), 20)


def _project(cx, cy, s, v):
    f = 1.0 / (1.0 - v[2] * 0.15)
    return (int(cx + s * v[0] * f), int(cy - s * v[1] * f))


# ----------------------------- 渲染:水平仪 -------------------------
def render_level(img, roll_deg, pitch_deg, state):
    cx, cy = W // 2, H // 2 + 10
    R_out, R_in = 150, 36
    # 阻尼跟随
    state.bx += (pitch_deg - state.bx) * 0.25
    state.by += (-roll_deg - state.by) * 0.25
    bx = max(-1.0, min(1.0, state.bx / 30.0))   # ±30° 满量程
    by = max(-1.0, min(1.0, state.by / 30.0))
    px = int(cx + bx * (R_out - R_in))
    py = int(cy + by * (R_out - R_in))
    level = math.hypot(state.bx, state.by) < 1.5
    ring_col = (0, 200, 0) if level else (180, 180, 180)
    cv2.circle(img, (cx, cy), R_out, ring_col, 2, cv2.LINE_AA)
    cv2.circle(img, (cx, cy), R_out - 40, (90, 90, 90), 1, cv2.LINE_AA)
    cv2.line(img, (cx - R_out, cy), (cx + R_out, cy), (90, 90, 90), 1, cv2.LINE_AA)
    cv2.line(img, (cx, cy - R_out), (cx, cy + R_out), (90, 90, 90), 1, cv2.LINE_AA)
    cv2.line(img, (cx - 12, cy), (cx + 12, cy), ring_col, 1, cv2.LINE_AA)
    cv2.line(img, (cx, cy - 12), (cx, cy + 12), ring_col, 1, cv2.LINE_AA)
    bcol = (0, 255, 120) if level else (90, 180, 255)
    cv2.circle(img, (px, py), R_in, bcol, -1, cv2.LINE_AA)
    cv2.circle(img, (px, py), R_in, (255, 255, 255), 2, cv2.LINE_AA)
    err = math.hypot(state.bx, state.by)
    text(img, f"pitch: {pitch_deg:+6.1f}°", (cx + R_out + 20, cy - 40), (220, 220, 220), 22)
    text(img, f"roll : {roll_deg:+6.1f}°", (cx + R_out + 20, cy - 8), (220, 220, 220), 22)
    text(img, f"水平误差: {err:4.1f}°", (cx + R_out + 20, cy + 24),
         (0, 255, 120) if level else (180, 180, 180), 22)
    if level and not state.level_done:
        state.beep_pulse = 0.08
        state.level_done = True
    if err > 3.0:
        state.level_done = False


# ----------------------------- 渲染:波形 ---------------------------
def render_scope(img, a_mag, gx, gy, gz, hist):
    hist["a"].append(a_mag)
    hist["g"].append((gx, gy, gz))
    pw = W - 60
    ph = 120
    _plot(img, hist["a"], 30, 170, pw, ph, 0.0, 3.0, (0, 255, 120), "|a| (g)")
    gmax = 60.0
    _plot(img, [v[0] for v in hist["g"]], 30, 300, pw, ph, -gmax, gmax, (0, 0, 255), "gx")
    _plot(img, [v[1] for v in hist["g"]], 30, 445, pw, ph, -gmax, gmax, (0, 255, 0), "gy")
    _plot(img, [v[2] for v in hist["g"]], 30, 445, pw, ph, -gmax, gmax, (255, 80, 0), "gz", share=True)


def _plot(img, data, x, y, w, h, lo, hi, color, label, share=False):
    cv2.rectangle(img, (x, y - h), (x + w, y), (40, 40, 40), 1)
    cv2.line(img, (x, y - h // 2), (x + w, y - h // 2), (60, 60, 60), 1)
    n = len(data)
    if n < 2:
        if not share:
            text(img, label, (x + 4, y - h + 24), color, 20)
        return
    pts = []
    span = max(1e-6, hi - lo)
    for i, v in enumerate(data):
        px = x + int(i * (w - 1) / (n - 1))
        vv = max(lo, min(hi, v))
        py = y - int((vv - lo) / span * h)
        pts.append((px, py))
    cv2.polylines(img, [np.array(pts, np.int32)], False, color, 2, cv2.LINE_AA)
    if not share:
        text(img, label, (x + 4, y - h + 24), color, 20)


# ----------------------------- 主程序 ------------------------------
def main():
    # 屏幕初始化 + 翻转处理(照搬 yolo11-det)
    Display.init()
    flipped = (direction.get_lcd() == 2)
    if flipped:
        Display.set_rotation(2)

    # 启动画面
    img = np.zeros((H, W, 3), np.uint8)
    text(img, "IMU 姿态仪", (W // 2 - 90, H // 2 - 30), (0, 255, 120), 40)
    text(img, "打开 IMU 中...", (W // 2 - 90, H // 2 + 20), (180, 180, 180), 24)
    Display.show(img)

    imu = open_imu()
    chip_ok_gyro = imu.has_gyro
    chip_name = imu.chip_name

    touch = TouchInput(flipped=flipped)
    key = Key()

    # 状态
    class S:
        roll = 0.0      # 弧度
        pitch = 0.0
        yaw = 0.0
        bx = by = 0.0   # 水平仪气泡(度)
        level_done = False
        beep_pulse = 0.0
    state = S()
    hist = {"a": deque(maxlen=200), "g": deque(maxlen=200)}

    # 校准
    img = np.zeros((H, W, 3), np.uint8)
    text(img, "陀螺仪校准中,请保持静止…", (W // 2 - 160, H // 2), (0, 255, 255), 26)
    Display.show(img)
    imu.calibrate(n=120)

    mode = 0
    last = time.monotonic()
    last_shake = 0.0
    fc = 0
    fps = 0.0
    fps_t = last

    while True:
        now = time.monotonic()
        dt = min(0.1, now - last)
        last = now

        ax, ay, az, gx, gy, gz = imu.read()
        # 互补滤波(陀螺仪 dps -> rad/s)
        if chip_ok_gyro:
            grx, gry, grz = gx * DEG, gy * DEG, gz * DEG
            roll_a = math.atan2(ay, math.hypot(ax, az))
            pitch_a = math.atan2(-ax, math.hypot(ay, az))
            state.roll = ALPHA * (state.roll + grx * dt) + (1 - ALPHA) * roll_a
            state.pitch = ALPHA * (state.pitch + gry * dt) + (1 - ALPHA) * pitch_a
            state.yaw += grz * dt
        else:
            state.roll = math.atan2(ay, math.hypot(ax, az))
            state.pitch = math.atan2(-ax, math.hypot(ay, az))

        roll_d, pitch_d, yaw_d = state.roll * RAD, state.pitch * RAD, state.yaw * RAD
        a_mag = math.sqrt(ax * ax + ay * ay + az * az)

        # ---- 交互 ----
        region = touch.poll_region()
        if region == -1:
            mode = (mode - 1) % len(MODES); key.led(True); time.sleep(0.05); key.led(False)
        elif region == 1:
            mode = (mode + 1) % len(MODES); key.led(True); time.sleep(0.05); key.led(False)
        elif region == 0:
            imu.calibrate(n=80); state.yaw = 0.0; key.beep(True); time.sleep(0.05); key.beep(False)

        if key.pressed:
            mode = (mode + 1) % len(MODES)
            key.led(True); time.sleep(0.05); key.led(False)
            while key.pressed:
                time.sleep(0.01)

        # 摇一摇
        if a_mag > SHAKE_G and (now - last_shake) > SHAKE_COOLDOWN:
            last_shake = now
            imu.calibrate(n=80); state.yaw = 0.0
            key.beep(True); time.sleep(0.05); key.beep(False)

        # ---- 渲染 ----
        img = np.zeros((H, W, 3), np.uint8)
        if mode == 0:
            render_cube(img, state.roll, state.pitch, state.yaw, chip_ok_gyro)
        elif mode == 1:
            render_level(img, roll_d, pitch_d, state)
        else:
            render_scope(img, a_mag, gx, gy, gz, hist)

        # 蜂鸣脉冲
        if state.beep_pulse > 0:
            key.beep(True); state.beep_pulse -= dt
            if state.beep_pulse <= 0:
                key.beep(False); state.beep_pulse = 0

        # HUD
        text(img, f"[{mode + 1}/3] {MODES[mode]}", (10, 8), (0, 255, 120), 22)
        tag = chip_name if chip_ok_gyro else f"{chip_name}·仅加速度"
        text(img, tag, (10, H - 50), (160, 160, 160), 20)
        text(img, f"FPS:{fps:4.1f}", (W - 90, 8), (160, 160, 160), 20)
        if mode == 0:
            text(img, f"pitch{pitch_d:+6.1f} roll{roll_d:+6.1f} yaw{yaw_d:+6.1f}~",
                 (W // 2 - 150, H - 50), (200, 200, 200), 20)
        text(img, "触摸/按键切换 · 中间或摇一摇校准", (W // 2 - 170, H - 28), (110, 110, 110), 18)

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
