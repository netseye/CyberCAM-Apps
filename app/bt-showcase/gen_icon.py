'''生成 icon.png(256×256):蓝色渐变底 + 白色蓝牙符。

设备上跑:`python gen_icon.py` → 当前目录生成 icon.png。
系统启动器据此自动生成 icon_100.png,并在桌面显示 app 名称(取自 app.txt),
故图标本身只放蓝牙符,不重复文字。

蓝牙符是 Hagall+Bjarkan 的 bind-rune:一根竖茎 + 上下两个三角,
共享右侧顶点 R。整符水平略左移,让菱形偏右的视觉重心落在画面中心。
'''
import os

import cv2
import numpy as np

S = 256
CX = CY = S // 2   # 128


def _gradient_bg():
    '''深钴蓝(顶)→ 亮天蓝(底)的垂直渐变(BGR)。'''
    img = np.zeros((S, S, 3), np.float32)
    top = np.array([120, 40, 10], np.float32)    # 深钴蓝
    bot = np.array([255, 165, 45], np.float32)   # 亮天蓝
    for y in range(S):
        img[y, :] = top + (bot - top) * (y / (S - 1))
    return img.astype(np.uint8)


# 蓝牙符顶点(见模块 docstring)
SX = CX - 14        # 茎的 x,整符视觉居中
H = 72              # 半高
W = 44              # 右顶点横距
T = (SX, CY - H)
B = (SX, CY + H)
U = (SX, CY - H // 3)
L = (SX, CY + H // 3)
R = (SX + W, CY)
RUNE = [(T, B), (T, R), (R, U), (B, R), (R, L)]


def main():
    img = _gradient_bg()

    # 柔和投影:把符偏移画一遍暗色,半透明叠加
    sh = img.copy()
    dx = dy = 3
    for a, b in RUNE:
        cv2.line(sh, (a[0] + dx, a[1] + dy), (b[0] + dx, b[1] + dy),
                 (0, 0, 0), 12, cv2.LINE_AA)
    cv2.addWeighted(sh, 0.35, img, 0.65, 0, img)

    # 清晰白色符
    for a, b in RUNE:
        cv2.line(img, a, b, (255, 255, 255), 9, cv2.LINE_AA)

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png")
    cv2.imwrite(out, img)
    print("icon saved", img.shape, "->", out)


if __name__ == "__main__":
    main()
