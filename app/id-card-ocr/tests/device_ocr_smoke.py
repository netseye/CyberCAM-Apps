'''K230 真机 OCR 冒烟测试：只生成虚构证件版式，不读摄像头、不落盘。'''

import os

import cv2
import numpy as np
from walnutpi import kpu


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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

    detector = kpu.OCR(
        os.path.join(ROOT, "ocr_det_int16.kmodel"),
        os.path.join(ROOT, "ocr_rec_int16.kmodel"),
        os.path.join(ROOT, "dict.txt"), 640, (512, 32))
    boxes = detector.run(card, 0.25) or []
    print("synthetic_boxes=", len(boxes))
    for box in boxes:
        print("%.3f|x=%d y=%d w=%d h=%d|%s" % (
            float(box.reliability), int(box.x), int(box.y), int(box.w),
            int(box.h), str(box.text)))
    if not boxes:
        raise SystemExit("KPU OCR returned no text boxes")


if __name__ == "__main__":
    main()
