import math, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import numpy as np
from core import (light_paint_accumulate, touch_region, level_color, face_geometry)

# ---- light_paint_accumulate: bright paints, dim doesn't, input not mutated
canvas = np.zeros((4, 4, 3), np.uint8)
frame = np.zeros((4, 4, 3), np.uint8)
frame[1, 1] = (200, 220, 240)   # bright (gray > 160)
frame[2, 2] = (10, 10, 10)      # dim
out = light_paint_accumulate(canvas, frame, thr=160)
assert tuple(out[1, 1]) == (200, 220, 240), out[1, 1]
assert tuple(out[2, 2]) == (0, 0, 0)
assert tuple(canvas[1, 1]) == (0, 0, 0)            # input not mutated
out2 = light_paint_accumulate(out, frame, thr=160)  # stacks (max)
assert tuple(out2[1, 1]) == (200, 220, 240)

# ---- touch_region (640 wide)
assert touch_region(10, 240, 640, 480) == "LEFT"
assert touch_region(320, 240, 640, 480) == "CENTER"
assert touch_region(630, 240, 640, 480) == "RIGHT"

# ---- level_color
assert level_color(0.0) == (0, 255, 0)
assert level_color(1.9) == (0, 255, 0)
assert level_color(3.0) == (0, 180, 255)

# ---- face_geometry
g = face_geometry(box=(100, 100, 80, 80),
                  leye=(120, 140), reye=(160, 140),
                  nose=(140, 155), lmouth=(125, 170), rmouth=(155, 170))
assert g['eye_center'] == (140.0, 140.0), g['eye_center']
assert abs(g['eye_dist'] - 40.0) < 1e-6
assert abs(g['angle'] - 0.0) < 1e-6
assert g['mouth_center'] == (140.0, 170.0)
assert abs(g['mouth_w'] - 30.0) < 1e-6
# tilted eyes (right eye lower) -> positive angle = atan2(20,40)
g2 = face_geometry((100, 100, 80, 80), (120, 140), (160, 160),
                   (140, 165), (125, 175), (155, 175))
assert abs(g2['angle'] - math.atan2(20, 40)) < 1e-6

print("OK")
