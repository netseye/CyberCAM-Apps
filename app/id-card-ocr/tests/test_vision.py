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

    def test_builds_clean_roi_canvas_without_fixed_labels(self):
        card = np.full((vision.WARP_H, vision.WARP_W, 3), 235, np.uint8)
        # 模拟姓名值和左侧固定标签；画布只应包含 x>=0.16W 的值区。
        cv2.rectangle(card, (20, 38), (75, 58), (0, 0, 0), -1)
        cv2.rectangle(card, (120, 38), (225, 58), (0, 0, 0), -1)
        canvas = vision.build_id_ocr_canvas(card)
        self.assertEqual(canvas.shape, (vision.OCR_CANVAS_H,
                                        vision.OCR_CANVAS_W, 3))
        self.assertGreater(canvas[4:72].mean(), 200)
        self.assertLess(canvas[4:72, :360].min(), 80)
        self.assertTrue(np.all(canvas[72:76] == 255))


if __name__ == "__main__":
    unittest.main()
