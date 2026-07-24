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

    def test_detects_low_contrast_card_from_chroma(self):
        # 暖色桌面与卡片亮度接近，外沿不足以形成稳定 Canny 闭环；卡片内部
        # 的冷色防伪底纹仍应让 Lab 色差兜底返回正确比例的四边形。
        frame = np.full((360, 640, 3), (185, 205, 220), np.uint8)
        card = np.array([[130, 75], [535, 82], [528, 335], [125, 328]],
                        np.int32)
        cv2.fillConvexPoly(frame, card, (214, 218, 214))
        for x in range(155, 510, 22):
            cv2.line(frame, (x, 95), (x + 35, 305),
                     (205, 220, 225), 1)
        found = vision.find_id_card(frame)
        self.assertIsNotNone(found)
        _, width, height, ratio = vision._quad_metrics(found)
        self.assertGreater(width * height / (640 * 360), 0.35)
        self.assertAlmostEqual(ratio, vision.ID_CARD_RATIO, delta=0.15)

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

    def test_sex_and_ethnicity_share_band_without_overlapping(self):
        card = np.full((vision.WARP_H, vision.WARP_W, 3), 235, np.uint8)
        cv2.rectangle(card, (112, 103), (142, 123), (0, 0, 0), -1)
        cv2.rectangle(card, (286, 103), (324, 123), (0, 0, 0), -1)
        canvas = vision.build_id_ocr_canvas(card)
        self.assertLess(canvas[76:126, 16:170].min(), 80)
        self.assertLess(canvas[76:126, 190:624].min(), 80)
        self.assertTrue(np.all(canvas[76:126, 170:190] == 255))

    def test_detail_canvas_enlarges_ethnicity_and_address(self):
        card = np.full((vision.WARP_H, vision.WARP_W, 3), 235, np.uint8)
        cv2.rectangle(card, (286, 103), (324, 123), (0, 0, 0), -1)
        cv2.rectangle(card, (120, 215), (360, 238), (0, 0, 0), -1)
        canvas = vision.build_id_detail_ocr_canvas(card)
        self.assertEqual(canvas.shape, (vision.OCR_CANVAS_H,
                                        vision.OCR_CANVAS_W, 3))
        self.assertLess(canvas[12:132].min(), 80)
        self.assertLess(canvas[148:396].min(), 80)
        self.assertTrue(np.all(canvas[132:148] == 255))

        ethnicity_pixels = np.argwhere(canvas[:132, :, 0] < 80)
        address_pixels = np.argwhere(canvas[148:, :, 0] < 80)
        self.assertGreater(np.ptp(ethnicity_pixels[:, 0]), 35)
        self.assertGreater(np.ptp(address_pixels[:, 1]), 300)

    def test_ethnicity_retry_canvas_isolated_value_fills_canvas(self):
        card = np.full((vision.WARP_H, vision.WARP_W, 3), 235, np.uint8)
        cv2.rectangle(card, (250, 92), (310, 130), (0, 0, 0), -1)
        canvas = vision.build_ethnicity_retry_canvas(card)
        self.assertLess(canvas[20:384].min(), 80)
        self.assertTrue(np.all(canvas[:20] == 255))
        self.assertTrue(np.all(canvas[384:] == 255))

    def test_quality_prefers_sharp_frame_and_reports_glare(self):
        sharp = np.full((vision.WARP_H, vision.WARP_W, 3), 215, np.uint8)
        for y in range(45, 360, 38):
            cv2.line(sharp, (70, y), (560, y), (25, 25, 25), 4)
        blurred = cv2.GaussianBlur(sharp, (31, 31), 0)
        sharp_quality = vision.document_quality(sharp)
        blurred_quality = vision.document_quality(blurred)
        self.assertGreater(sharp_quality["score"], blurred_quality["score"])
        corners = ((0, 0), (vision.WARP_W - 1, 0),
                   (vision.WARP_W - 1, vision.WARP_H - 1),
                   (0, vision.WARP_H - 1))
        _, selected = vision.select_best_card(
            [(blurred, corners), (sharp, corners)], corners)
        self.assertGreaterEqual(selected["score"], sharp_quality["score"] - 0.02)

        glare = sharp.copy()
        glare[:, :220] = 255
        glare_quality = vision.document_quality(glare)
        self.assertIn("反光过强", glare_quality["issues"])


if __name__ == "__main__":
    unittest.main()
