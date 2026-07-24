'''K230 实拍图隐私保护探针：只输出匹配指标，不打印证件字段。'''

import difflib
import os
import sys

import cv2


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import core  # noqa: E402
import main as app_main  # noqa: E402
import vision  # noqa: E402


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: device_image_probe.py IMAGE")
    image = cv2.imread(sys.argv[1])
    if image is None:
        raise SystemExit("image unreadable")
    corners = vision.find_id_card(image)
    if corners is None:
        raise SystemExit("card not detected")
    card = vision.warp_card(image, corners)
    quality = vision.document_quality(card)

    runtime = app_main.OCRRuntime()
    try:
        result, rows = app_main.recognize_card(runtime, card)
    finally:
        if not runtime.unload():
            raise SystemExit("OCR runtime did not unload")

    expected_ethnicity = os.environ.get("OCR_EXPECTED_ETHNICITY", "")
    expected_address = os.environ.get("OCR_EXPECTED_ADDRESS", "")
    actual_address = result.get("address", "")
    print("card_detected=1")
    print("quality_score=%.3f" % quality["score"])
    print("quality_issues=%d" % len(quality["issues"]))
    print("ocr_boxes=%d" % len(rows))
    print("field_count=%d" % result.get("field_count", 0))
    print("id_valid=%d" % bool(result.get("id_valid")))
    print("status=%s" % core.recognition_status(result, rows))
    if expected_ethnicity:
        print("ethnicity_match=%d" %
              (result.get("ethnicity", "") == expected_ethnicity))
    if expected_address:
        ratio = difflib.SequenceMatcher(
            None, expected_address, actual_address).ratio()
        print("address_exact=%d" % (actual_address == expected_address))
        print("address_similarity=%.3f" % ratio)
        print("address_length_delta=%d" %
              (len(actual_address) - len(expected_address)))


if __name__ == "__main__":
    main()
