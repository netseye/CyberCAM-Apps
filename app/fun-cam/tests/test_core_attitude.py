import math, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core import (accel_to_a_mag, accel_to_roll, accel_to_pitch,
                  complementary, ShakeDetector, ALPHA)

def approx(a, b, eps=1e-6):
    return abs(a - b) < eps

# |a| for (0,0,-1) = 1.0
assert approx(accel_to_a_mag(0, 0, -1), 1.0), accel_to_a_mag(0, 0, -1)
# |a| for (3,4,0) = 5
assert approx(accel_to_a_mag(3, 4, 0), 5.0)

# level device: 本板 QMI8658A 水平时重力沿 +Y (ay≈1) -> roll=0, pitch=0
assert approx(accel_to_roll(0, 1, 0), 0.0)
assert approx(accel_to_pitch(0, 1, 0), 0.0)
# 绕 X 轴滚 90°(重力从 +Y 转到 +Z): roll = atan2(az=1, ay=0) = +90°
assert approx(accel_to_roll(0, 0, 1), math.pi / 2)

# complementary branch
expected = 0.98 * (0.1 + 0.5 * 0.1) + 0.02 * 0.1
assert approx(complementary(0.1, 0.5, 0.1, 0.1), expected)

# ShakeDetector: prime, below threshold, spike fires once, debounce blocks, later refire
d = ShakeDetector(spike_g=2.5, debounce_s=1.5)
assert d.update(1.0, 0.0) is False
assert d.update(1.0, 0.5) is False
assert d.update(2.6, 1.0) is True
assert d.update(2.6, 1.2) is False
assert d.update(2.6, 3.0) is True

print("OK")
