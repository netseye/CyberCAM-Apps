import pathlib
import sys
import unittest


sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import core  # noqa: E402


class IdNumberTests(unittest.TestCase):
    def test_standard_checksum_example(self):
        self.assertTrue(core.is_valid_id_number("11010519491231002X"))
        self.assertEqual(core.id_checksum("11010519491231002"), "X")

    def test_invalid_date_and_checksum(self):
        self.assertFalse(core.is_valid_id_number("11010519490231002X"))
        self.assertFalse(core.is_valid_id_number("110105194912310021"))

    def test_mask(self):
        self.assertEqual(core.mask_id_number("11010519491231002X"),
                         "110105********002X")


class ParserTests(unittest.TestCase):
    def test_parses_front_fields_and_multiline_address(self):
        result = core.parse_id_card([
            "姓名 张三",
            "性别 男  民族 汉",
            "出生 1949年12月31日",
            "住址 北京市朝阳区幸福路",
            "一号院二单元",
            "公民身份号码 11010519491231002X",
        ])
        self.assertEqual(result["name"], "张三")
        self.assertEqual(result["sex"], "男")
        self.assertEqual(result["ethnicity"], "汉")
        self.assertEqual(result["birth"], "1949-12-31")
        self.assertEqual(result["address"], "北京市朝阳区幸福路一号院二单元")
        self.assertEqual(result["id_number"], "11010519491231002X")
        self.assertTrue(result["id_valid"])
        self.assertEqual(result["field_count"], 6)

    def test_derives_birth_and_sex_from_id(self):
        result = core.parse_id_card([
            "姓名张三", "民族汉", "住址北京市", "11010519491231002X"
        ])
        self.assertEqual(result["birth"], "1949-12-31")
        self.assertEqual(result["sex"], "女")

    def test_corrects_common_ocr_confusion_in_long_candidate(self):
        result = core.parse_id_card(["公民身份号码 11O1O519491231OO2X"])
        self.assertEqual(result["id_number"], "11010519491231002X")
        self.assertTrue(result["id_valid"])

    def test_invalid_number_retries_orientation_and_valid_candidate_wins(self):
        invalid = core.parse_id_card([
            "姓名测试用户", "住址测试市", "110105194912310021"
        ])
        valid = core.parse_id_card(["11010519491231002X"])
        self.assertTrue(core.should_retry_orientation(invalid))
        self.assertFalse(core.should_retry_orientation(valid))
        noisy_rows = [{"confidence": 0.99}] * 8
        valid_rows = [{"confidence": 0.40}]
        self.assertGreater(core.ocr_candidate_score(valid, valid_rows),
                           core.ocr_candidate_score(invalid, noisy_rows))

    def test_accepts_labels_and_values_in_separate_boxes(self):
        result = core.parse_id_card([
            "姓名", "张三", "性别", "女", "民族", "汉", "出生",
            "1949年12月31日", "住址", "北京市", "11010519491231002X",
        ])
        self.assertEqual(result["name"], "张三")
        self.assertEqual(result["sex"], "女")
        self.assertEqual(result["ethnicity"], "汉")
        self.assertEqual(result["birth"], "1949-12-31")

    def test_orders_boxes_by_text_line_then_x(self):
        rows = [
            {"text": "男", "x": 120, "y": 102, "h": 22},
            {"text": "民族", "x": 210, "y": 99, "h": 22},
            {"text": "性别", "x": 55, "y": 100, "h": 22},
            {"text": "姓名张三", "x": 55, "y": 60, "h": 22},
        ]
        ordered = core.order_ocr_rows(rows)
        self.assertEqual([row["text"] for row in ordered],
                         ["姓名张三", "性别", "男", "民族"])

    def test_layout_recovers_values_when_small_labels_are_wrong(self):
        rows = [
            {"text": "AF身一h", "x": 38, "y": 349, "w": 131, "h": 11},
            {"text": "11010519491231002X", "x": 188, "y": 338,
             "w": 297, "h": 24},
            {"text": "测试省测试市测试路一号", "x": 116, "y": 218,
             "w": 268, "h": 22},
            {"text": "H庄", "x": 37, "y": 168, "w": 45, "h": 16},
            {"text": "1949年12月31日", "x": 116, "y": 164,
             "w": 211, "h": 25},
            {"text": "测试用户", "x": 118, "y": 49, "w": 109, "h": 34},
            {"text": "姓夕", "x": 37, "y": 58, "w": 44, "h": 17},
        ]
        result = core.parse_id_card_rows(rows)
        self.assertEqual(result["name"], "测试用户")
        self.assertEqual(result["address"], "测试省测试市测试路一号")
        self.assertEqual(result["birth"], "1949-12-31")
        self.assertEqual(result["sex"], "女")
        self.assertTrue(result["id_valid"])

    def test_parses_clean_roi_canvas_without_labels(self):
        rows = [
            {"text": "测试用户", "x": 18, "y": 22, "w": 130, "h": 30},
            {"text": "汉", "x": 18, "y": 88, "w": 26, "h": 25},
            {"text": "1949年12月31日", "x": 18, "y": 145,
             "w": 220, "h": 28},
            {"text": "测试省测试市", "x": 18, "y": 208,
             "w": 180, "h": 25},
            {"text": "测试路一号", "x": 18, "y": 250,
             "w": 150, "h": 25},
            {"text": "11010519491231002X", "x": 18, "y": 345,
             "w": 300, "h": 30},
        ]
        result = core.parse_id_card_roi_rows(rows)
        self.assertEqual(result["name"], "测试用户")
        self.assertEqual(result["ethnicity"], "汉")
        self.assertEqual(result["address"], "测试省测试市测试路一号")
        self.assertEqual(result["birth"], "1949-12-31")
        self.assertEqual(result["sex"], "女")
        self.assertTrue(result["id_valid"])
        self.assertEqual(result["field_count"], 6)

    def test_roi_parser_ignores_text_in_separator_gaps(self):
        rows = [
            {"text": "噪声", "x": 20, "y": 72, "w": 40, "h": 2},
            {"text": "11010519491231002X", "x": 18, "y": 345,
             "w": 300, "h": 30},
        ]
        result = core.parse_id_card_roi_rows(rows)
        self.assertEqual(result["name"], "")
        self.assertTrue(result["id_valid"])

    def test_roi_parser_reads_sex_without_a_valid_id_number(self):
        rows = [
            {"text": "测试用户", "x": 18, "y": 22, "w": 130, "h": 30},
            {"text": "男", "x": 18, "y": 88, "w": 26, "h": 25},
            {"text": "汉", "x": 210, "y": 88, "w": 26, "h": 25},
        ]
        result = core.parse_id_card_roi_rows(rows)
        self.assertEqual(result["sex"], "男")
        self.assertEqual(result["ethnicity"], "汉")
        self.assertFalse(result["id_valid"])

    def test_detail_parser_corrects_han_confusion_and_replaces_address(self):
        base = core.parse_id_card([
            "姓名测试用户", "住址测试省错路", "11010519491231002X"
        ])
        detail_rows = [
            {"text": "汀", "x": 20, "y": 42, "w": 34, "h": 48,
             "confidence": 0.69},
            {"text": "「测试省测试市测试区", "x": 20, "y": 190,
             "w": 310, "h": 34, "confidence": 0.81},
            {"text": "幸福路一号`", "x": 20, "y": 245,
             "w": 190, "h": 34, "confidence": 0.78},
        ]
        detail = core.parse_id_detail_rows(detail_rows)
        result = core.merge_id_detail_fields(base, detail)
        self.assertEqual(result["ethnicity"], "汉")
        self.assertEqual(result["address"],
                         "测试省测试市测试区幸福路一号")
        self.assertEqual(result["field_count"], 6)

    def test_detail_address_rejects_short_garbage(self):
        base = core.parse_id_card(["住址北京市朝阳区幸福路一号"])
        detail = {
            "ethnicity": "", "ethnicity_confidence": 0.0,
            "ethnicity_corrected": False,
            "address": "A1?", "address_confidence": 0.99,
            "raw_lines": ["A1?"],
        }
        result = core.merge_id_detail_fields(base, detail)
        self.assertEqual(result["address"], "北京市朝阳区幸福路一号")

    def test_corrected_ethnicity_does_not_override_exact_base_value(self):
        base = core.parse_id_card(["民族苗"])
        detail = {
            "ethnicity": "汉", "ethnicity_confidence": 0.92,
            "ethnicity_corrected": True, "address": "",
            "address_confidence": 0.0, "raw_lines": ["汀"],
        }
        result = core.merge_id_detail_fields(base, detail)
        self.assertEqual(result["ethnicity"], "苗")

    def test_ethnicity_retry_prefers_exact_dictionary_match(self):
        detail = core.parse_ethnicity_retry_rows([
            {"text": "汀", "x": 20, "y": 30, "w": 30, "h": 45,
             "confidence": 0.95},
            {"text": "民族汉", "x": 20, "y": 240, "w": 120, "h": 42,
             "confidence": 0.52},
        ])
        self.assertEqual(detail["ethnicity"], "汉")
        self.assertFalse(detail["ethnicity_corrected"])


class RecognitionStatusTests(unittest.TestCase):
    def test_distinguishes_reliable_partial_back_and_unreliable(self):
        reliable = core.parse_id_card([
            "姓名张三", "民族汉", "住址北京市幸福路",
            "11010519491231002X",
        ])
        self.assertEqual(core.recognition_status(reliable, []), "reliable")

        number_only = core.parse_id_card(["11010519491231002X"])
        self.assertEqual(core.recognition_status(number_only, []), "partial")

        back = core.parse_id_card([])
        back_rows = [
            {"text": "中华人民共和国", "confidence": 0.8},
            {"text": "签发机关 测试市公安局", "confidence": 0.8},
            {"text": "有效期限 2020.01.01-2040.01.01", "confidence": 0.8},
        ]
        self.assertEqual(core.recognition_status(back, back_rows), "back")

        weak = core.parse_id_card(["测试用户"])
        self.assertEqual(core.recognition_status(weak, []), "unreliable")

    def test_detected_back_beats_invalid_partial_hallucination(self):
        back = core.parse_id_card([])
        back_rows = [{"text": "有效期限", "confidence": 0.45}]
        invalid_partial = core.parse_id_card([
            "姓名测试用户", "性别男", "住址测试市"
        ])
        noisy_rows = [{"text": "噪声", "confidence": 0.99}] * 6
        self.assertGreater(core.ocr_candidate_score(back, back_rows),
                           core.ocr_candidate_score(invalid_partial, noisy_rows))


class StabilityTests(unittest.TestCase):
    def test_requires_stable_hold(self):
        gate = core.CardStability(max_motion_px=5, hold_s=0.3)
        quad = [(0, 0), (100, 0), (100, 60), (0, 60)]
        self.assertFalse(gate.update(quad, 0.0)[0])
        self.assertFalse(gate.update(quad, 0.2)[0])
        self.assertTrue(gate.update(quad, 0.31)[0])
        moved = [(20, 0), (120, 0), (120, 60), (20, 60)]
        self.assertFalse(gate.update(moved, 0.4)[0])


class TouchTests(unittest.TestCase):
    def test_preview_and_result_actions(self):
        self.assertEqual(core.touch_action(0.5, 0.5), "SCAN")
        self.assertEqual(core.touch_action(0.03, 0.03), "EXIT")
        self.assertEqual(core.touch_action(0.9, 0.95), "LIGHT")
        self.assertEqual(core.touch_action(0.5, 0.95, "result"), "RESCAN")
        self.assertEqual(core.touch_action(0.1, 0.95, "result"), "REVEAL")
        self.assertEqual(core.touch_action(0.9, 0.95, "result"), "RAW")


if __name__ == "__main__":
    unittest.main()
