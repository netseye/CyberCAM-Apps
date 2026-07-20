'''生成 icon.png(256×256):蓝色渐变底 + 白色蓝牙符。

设备上跑:`python3 gen_icon.py` → 当前目录生成 icon.png。
系统启动器据此自动生成 icon_100.png,并在桌面显示 app 名称(取自 app.txt),
故图标本身只放蓝牙符,不重复文字。

蓝牙符几何直接取自官方 Wikimedia `Bluetooth.svg`(viewBox 0 0 640 976,
path `m157 330 305 307-147 178V179l147 170-305 299`,stroke-width=53):
一根竖茎 + 上下两条贯穿对角线在中心交叉,构成 Hagall+ Bjarkan 的 bind-rune。
6 个顶点连成 5 段折线,按长宽比缩放居中到 256 画布。
'''
import os

import cv2
import numpy as np

S = 256
CX = CY = S // 2   # 128

# 官方蓝牙符顶点(Wikimedia Bluetooth.svg,viewBox 640×976,见模块 docstring)。
_VB = [(157, 330), (462, 637), (315, 815), (315, 179), (462, 349), (157, 648)]
_VB_STROKE = 53     # 官方描边宽(viewBox 单位)


def _rune():
    '''官方顶点按长宽比缩放、居中到 256 画布;返回 [(a,b),...] 5 段与描边宽。'''
    xs = [p[0] for p in _VB]
    ys = [p[1] for p in _VB]
    gw, gh = max(xs) - min(xs), max(ys) - min(ys)          # 305 × 636(高瘦)
    gcx, gcy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    pad = 34
    s = min((S - 2 * pad) / gw, (S - 2 * pad) / gh)        # 高度 636 受限
    pts = [(int(round(CX + (x - gcx) * s)),
            int(round(CY + (y - gcy) * s))) for x, y in _VB]
    segs = list(zip(pts, pts[1:]))                         # (P0,P1)..(P4,P5)
    return segs, max(8, int(round(_VB_STROKE * s)))


RUNE, STROKE = _rune()


def _gradient_bg():
    '''深钴蓝(顶)→ 亮天蓝(底)的垂直渐变(BGR)。'''
    img = np.zeros((S, S, 3), np.float32)
    top = np.array([120, 40, 10], np.float32)    # 深钴蓝
    bot = np.array([255, 165, 45], np.float32)   # 亮天蓝
    for y in range(S):
        img[y, :] = top + (bot - top) * (y / (S - 1))
    return img.astype(np.uint8)


def main():
    img = _gradient_bg()

    # 柔和投影:把符偏移画一遍暗色,半透明叠加
    sh = img.copy()
    dx = dy = 3
    for a, b in RUNE:
        cv2.line(sh, (a[0] + dx, a[1] + dy), (b[0] + dx, b[1] + dy),
                 (0, 0, 0), STROKE + 3, cv2.LINE_AA)
    cv2.addWeighted(sh, 0.35, img, 0.65, 0, img)

    # 清晰白色符(官方几何)
    for a, b in RUNE:
        cv2.line(img, a, b, (255, 255, 255), STROKE, cv2.LINE_AA)

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png")
    cv2.imwrite(out, img)
    print("icon saved", img.shape, "->", out)


if __name__ == "__main__":
    main()
