import pathlib
import sys
import unittest

try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = np = None


sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
if cv2 is not None:
    import vision  # noqa: E402


@unittest.skipIf(cv2 is None, "开发机未安装 OpenCV，视觉测试在 K230 真机运行")
class VisionTests(unittest.TestCase):
    def test_detects_and_warps_rotated_card(self):
        frame = np.zeros((360, 640, 3), np.uint8)
        rect = ((320, 180), (360, 225), 6)
        box = cv2.boxPoints(rect).astype(np.int32)
        cv2.fillConvexPoly(frame, box, (235, 235, 235))
        cv2.rectangle(frame, (255, 145), (285, 185), (40, 40, 40), -1)
        cv2.line(frame, (315, 145), (520, 145), (40, 40, 40), 5)
        found = vision.find_id_card(frame)
        self.assertIsNotNone(found)
        warped = vision.warp_card(frame, found)
        self.assertEqual(warped.shape[:2], (vision.WARP_H, vision.WARP_W))

    def test_blank_frame_has_no_card(self):
        frame = np.zeros((360, 640, 3), np.uint8)
        self.assertIsNone(vision.find_id_card(frame))


if __name__ == "__main__":
    unittest.main()
