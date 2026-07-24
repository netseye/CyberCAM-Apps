'''K230 真机 OCR 冒烟测试：只生成虚构证件版式，不读摄像头、不落盘。'''

import os
import sys

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import core  # noqa: E402
import main as app_main  # noqa: E402
import vision  # noqa: E402


FONT = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"


def put(ft, image, value, x, y, height=27):
    ft.putText(img=image, text=value, org=(x, y), fontHeight=height,
               color=(25, 25, 25), thickness=-1, line_type=cv2.LINE_AA,
               bottomLeftOrigin=False)


def main():
    card = np.full((404, 640, 3), (235, 242, 238), np.uint8)
    ft = cv2.freetype.createFreeType2()
    ft.loadFontData(FONT, 0)
    put(ft, card, "姓名", 34, 50)
    put(ft, card, "测试用户", 120, 50)
    put(ft, card, "性别", 34, 105)
    put(ft, card, "女", 120, 105)
    put(ft, card, "民族", 205, 105)
    put(ft, card, "汉", 292, 105)
    put(ft, card, "出生", 34, 160)
    put(ft, card, "1949年12月31日", 120, 160)
    put(ft, card, "住址", 34, 215)
    put(ft, card, "测试省测试市测试路一号", 120, 215, 24)
    put(ft, card, "公民身份号码", 34, 340, 23)
    put(ft, card, "11010519491231002X", 195, 334, 27)

    runtime = app_main.OCRRuntime()
    cycles = max(1, int(os.environ.get("OCR_SMOKE_CYCLES", "1")))
    try:
        for cycle in range(1, cycles + 1):
            try:
                result, rows = app_main.recognize_card(runtime, card)
                print("cycle=%d synthetic_boxes=%d" % (cycle, len(rows)))
                for row in rows:
                    print("%.3f|x=%d y=%d w=%d h=%d|%s" % (
                        row["confidence"], row["x"], row["y"], row["w"],
                        row["h"], row["text"]))
                if not rows:
                    raise SystemExit("KPU OCR returned no text boxes")
                print("field_count=", result["field_count"])
                print("id_valid=", result["id_valid"])
                if result["name"] != "测试用户":
                    raise SystemExit("name mismatch")
                if result["address"] != "测试省测试市测试路一号":
                    raise SystemExit("address mismatch")
                if result["ethnicity"] != "汉":
                    raise SystemExit("ethnicity mismatch")
                if not result["id_valid"]:
                    raise SystemExit("ID number checksum failed")
            finally:
                runtime.unload()
    finally:
        runtime.unload()


if __name__ == "__main__":
    main()
