'''使用真实应用 UI 渲染不含个人信息的 README 演示素材。

该脚本需要在装有 walnutpi、OpenCV FreeType 和中文字体的 K230 上运行：

    python tests/render_demo_assets.py /tmp/id-card-ocr-demo
'''

import os
import pathlib
import sys

import cv2
import numpy as np


DEFAULT_ROOT = pathlib.Path(__file__).resolve().parents[1]
ROOT = pathlib.Path(os.environ.get("ID_CARD_APP_ROOT", DEFAULT_ROOT))
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
import main as app_main  # noqa: E402


DEMO_ID_NUMBER = "000000199001010018"
DEMO_LINES = (
    "姓名示例用户",
    "性别男民族汉",
    "出生1990年01月01日",
    "住址示例市示例区演示路88号",
    "公民身份号码" + DEMO_ID_NUMBER,
)
DEMO_CORNERS = ((80, 31), (560, 31), (560, 330), (80, 330))


def _demo_camera_frame():
    '''绘制明确标注的演示卡片，避免 README 素材包含真实证件。'''
    height, width = app_main.H, app_main.W
    frame = np.zeros((height, width, 3), np.uint8)
    for y in range(height):
        blend = y / max(1, height - 1)
        frame[y, :] = (
            round(42 + blend * 16),
            round(38 + blend * 12),
            round(34 + blend * 9),
        )

    for offset in range(-height, width, 52):
        cv2.line(frame, (offset, 0), (offset + height, height),
                 (48, 45, 42), 1, cv2.LINE_AA)

    card = np.asarray(DEMO_CORNERS, np.int32)
    cv2.fillConvexPoly(frame, card, (224, 228, 230), cv2.LINE_AA)
    cv2.polylines(frame, [card.reshape((-1, 1, 2))], True,
                  (185, 192, 198), 2, cv2.LINE_AA)
    cv2.rectangle(frame, (80, 31), (560, 63), (205, 221, 220), -1)
    cv2.rectangle(frame, (101, 90), (365, 93), (152, 165, 172), -1)
    cv2.rectangle(frame, (101, 122), (330, 125), (168, 178, 184), -1)
    cv2.rectangle(frame, (101, 154), (350, 157), (168, 178, 184), -1)
    cv2.rectangle(frame, (101, 203), (400, 206), (168, 178, 184), -1)
    cv2.rectangle(frame, (101, 230), (382, 233), (168, 178, 184), -1)
    cv2.rectangle(frame, (101, 274), (432, 278), (132, 148, 157), -1)

    cv2.rectangle(frame, (438, 92), (529, 245), (194, 202, 207), -1)
    cv2.circle(frame, (484, 139), 24, (155, 168, 176), -1, cv2.LINE_AA)
    cv2.ellipse(frame, (484, 211), (36, 47), 0, 180, 360,
                (155, 168, 176), -1, cv2.LINE_AA)

    app_main.text(frame, "DEMO", (101, 38), (38, 115, 105), 20)
    app_main.text(frame, "演示卡片 · 无真实身份信息",
                  (185, 38), (62, 78, 84), 16)
    app_main.text(frame, "仅用于界面演示", (220, 304),
                  (82, 96, 104), 18)
    return frame


def _write(path, image, header=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), image):
        raise RuntimeError("无法写入 " + str(path))
    if header is not None:
        persisted = cv2.imread(str(path))
        if persisted is None:
            raise RuntimeError("无法重新读取 " + str(path))
        persisted[:app_main.PREVIEW_Y] = header
        if not cv2.imwrite(str(path), persisted):
            raise RuntimeError("无法更新顶部栏 " + str(path))


def render(output_dir):
    output = pathlib.Path(output_dir)
    steps = output / "steps"
    frame = _demo_camera_frame()
    result = core.parse_id_card(DEMO_LINES)
    rows = []

    empty = app_main.compose_preview(frame, None, False)
    first_step = steps / "00.png"
    _write(first_step, empty)
    persisted = cv2.imread(str(first_step))
    if persisted is None:
        raise RuntimeError("无法重新读取首帧顶部栏")
    header = persisted[:app_main.PREVIEW_Y].copy()
    persisted = None
    empty = None
    _write(steps / "01.png", app_main.compose_preview(
        frame, DEMO_CORNERS, False,
        notice="检测到卡片 · 请保持稳定"), header)

    ready = app_main.compose_preview(frame, DEMO_CORNERS, True)
    _write(output / "preview.png", ready, header)
    _write(steps / "02.png", ready, header)
    ready = None

    _write(steps / "03.png", app_main.compose_processing(
        "正在增强三帧中最佳画面…"), header)

    recognized = app_main.compose_result(result, rows, status="reliable")
    _write(output / "result.png", recognized, header)
    _write(steps / "04.png", recognized, header)
    recognized = None

    _write(output / "back-side.png", app_main.compose_result(
        core.parse_id_card([]), [], status="back"), header)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: render_demo_assets.py OUTPUT_DIR")
    render(sys.argv[1])
