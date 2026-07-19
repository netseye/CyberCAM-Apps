'''趣拍 (Fun Camera) 纯逻辑层。不依赖 cv2 / walnutpi，可在 Mac 上单元测试。'''
import math

ALPHA = 0.98  # 互补滤波的陀螺仪权重


def accel_to_a_mag(ax, ay, az):
    '''三轴加速度 (g) -> 合成加速度幅值 |a| (g)。'''
    return math.sqrt(ax * ax + ay * ay + az * az)


def accel_to_roll(ax, ay, az):
    '''由加速度推 roll (rad)，约等于 atan2(ay, hypot(ax,az))。'''
    return math.atan2(ay, math.hypot(ax, az))


def accel_to_pitch(ax, ay, az):
    '''由加速度推 pitch (rad)。'''
    return math.atan2(-ax, math.hypot(ay, az))


def complementary(prev, gyro_rate, accel_angle, dt, alpha=ALPHA):
    '''互补滤波一步：陀螺积分 + 加速度修正。'''
    return alpha * (prev + gyro_rate * dt) + (1 - alpha) * accel_angle


class ShakeDetector:
    '''|a| 越过阈值即触发一次拍照，带去抖。'''

    def __init__(self, spike_g=2.5, debounce_s=1.5):
        self.spike_g = spike_g
        self.debounce_s = debounce_s
        self._last_fire = -1e9

    def update(self, a_mag, t):
        fire = False
        if a_mag >= self.spike_g and (t - self._last_fire) >= self.debounce_s:
            fire = True
            self._last_fire = t
        return fire


import numpy as np  # noqa: E402  (numpy 用在下面的模式辅助函数)


def light_paint_accumulate(canvas, frame, thr=160):
    '''把 frame 中亮于 thr 的像素按通道取大叠加到 canvas，返回新画布。不改动输入。'''
    b, g, r = frame[:, :, 0], frame[:, :, 1], frame[:, :, 2]
    gray = 0.114 * b + 0.587 * g + 0.299 * r
    mask = gray > thr
    out = canvas.copy()
    out[mask] = np.maximum(out[mask], frame[mask])
    return out


def touch_region(x, y, W, H):
    '''按横坐标三等分判定触摸区域。'''
    if x < W / 3:
        return "LEFT"
    if x > 2 * W / 3:
        return "RIGHT"
    return "CENTER"


def level_color(roll_deg, thresh=2.0):
    '''水平时绿、倾斜时橙（BGR）。'''
    return (0, 255, 0) if abs(roll_deg) < thresh else (0, 180, 255)


def face_geometry(box, leye, reye, nose, lmouth, rmouth):
    '''由人脸框 + 5 关键点算贴纸锚点（纯数学）。返回 dict。'''
    x, y, w, h = box
    lex, ley = leye
    rex, rey = reye
    eye_dx, eye_dy = rex - lex, rey - ley
    return {
        'angle': math.atan2(eye_dy, eye_dx),
        'eye_center': ((lex + rex) / 2.0, (ley + rey) / 2.0),
        'eye_dist': math.hypot(eye_dx, eye_dy),
        'left_eye': (lex, ley),
        'right_eye': (rex, rey),
        'hat_center': ((lex + rex) / 2.0, y),
        'hat_w': float(w),
        'mouth_center': ((lmouth[0] + rmouth[0]) / 2.0, (lmouth[1] + rmouth[1]) / 2.0),
        'mouth_w': abs(rmouth[0] - lmouth[0]),
        'box': box,
    }
