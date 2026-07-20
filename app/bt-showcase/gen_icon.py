'''生成 icon.png(256×256):深蓝渐变底 + 蓝牙 B 字形 + "蓝牙" 标签。

设备上跑:`python gen_icon.py` → 当前目录生成 icon.png。
系统启动器会据此自动生成 icon_100.png。
'''
import os

import cv2
import numpy as np

FONT = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
S = 256


def main():
    ft = cv2.freetype.createFreeType2()
    ft.loadFontData(FONT, 0)

    img = np.zeros((S, S, 3), np.uint8)
    for y in range(S):
        img[y, :] = (int(20 + 35 * y / S), int(15 + 25 * y / S), int(40 + 45 * y / S))

    cx, cy = 128, 108
    cv2.circle(img, (cx, cy), 80, (30, 30, 55), -1)
    cv2.circle(img, (cx, cy), 80, (190, 220, 255), 3)

    col = (255, 230, 90)   # 蓝牙符(琥珀色)
    # 竖线 + 两组斜线,组成蓝牙 B 字形
    cv2.line(img, (cx, cy - 46), (cx, cy + 46), col, 6)
    cv2.line(img, (cx, cy - 46), (cx + 32, cy - 14), col, 6)
    cv2.line(img, (cx + 32, cy - 14), (cx, cy + 18), col, 6)
    cv2.line(img, (cx, cy + 46), (cx + 32, cy + 14), col, 6)
    cv2.line(img, (cx + 32, cy + 14), (cx, cy - 18), col, 6)

    ft.putText(img=img, text="蓝牙", org=(74, 234), fontHeight=44, color=(240, 255, 245),
               thickness=-1, line_type=cv2.LINE_AA, bottomLeftOrigin=False)

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png")
    cv2.imwrite(out, img)
    print("icon saved", img.shape, "->", out)


if __name__ == "__main__":
    main()
