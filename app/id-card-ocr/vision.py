'''身份证取景的 OpenCV 几何处理。'''

import cv2
import numpy as np


ID_CARD_RATIO = 85.60 / 53.98
WARP_W = 640
WARP_H = round(WARP_W / ID_CARD_RATIO)

# 二代身份证正面的固定字段区域。固定标签、头像和国徽都不进入 OCR，避免
# 小标签与防伪底纹占用 DBNet/识别器算力。source 使用标准矫正卡片的归一化
# 坐标，target 是 640x404 干净画布中的像素区域。
OCR_CANVAS_W = WARP_W
OCR_CANVAS_H = WARP_H
OCR_REGIONS = (
    ("name", (0.16, 0.08, 0.58, 0.22), (16, 4, 624, 72)),
    ("ethnicity", (0.43, 0.24, 0.64, 0.37), (16, 76, 624, 126)),
    ("birth", (0.16, 0.36, 0.67, 0.50), (16, 130, 624, 190)),
    ("address", (0.16, 0.49, 0.76, 0.79), (16, 194, 624, 318)),
    ("id_number", (0.27, 0.79, 0.99, 0.96), (16, 326, 624, 402)),
)


def order_quad(points):
    '''把四点整理为左上、右上、右下、左下，并把长边置于水平方向。'''
    pts = np.asarray(points, dtype=np.float32).reshape(4, 2)
    total = pts.sum(axis=1)
    diff = np.diff(pts, axis=1).reshape(-1)
    ordered = np.array([
        pts[np.argmin(total)], pts[np.argmin(diff)],
        pts[np.argmax(total)], pts[np.argmax(diff)],
    ], dtype=np.float32)
    width = max(np.linalg.norm(ordered[1] - ordered[0]),
                np.linalg.norm(ordered[2] - ordered[3]))
    height = max(np.linalg.norm(ordered[3] - ordered[0]),
                 np.linalg.norm(ordered[2] - ordered[1]))
    if width < height:
        # 竖着拿卡时旋转点序，使输出仍为横向身份证。
        ordered = np.array([ordered[1], ordered[2], ordered[3], ordered[0]],
                           dtype=np.float32)
    return ordered


def _quad_metrics(quad):
    q = order_quad(quad)
    widths = (np.linalg.norm(q[1] - q[0]), np.linalg.norm(q[2] - q[3]))
    heights = (np.linalg.norm(q[3] - q[0]), np.linalg.norm(q[2] - q[1]))
    width, height = max(widths), max(heights)
    ratio = width / max(1.0, height)
    return q, width, height, ratio


def find_id_card(frame, min_coverage=0.12):
    '''从取景帧中找最像 ID-1 标准身份证比例的四边形。'''
    height, width = frame.shape[:2]
    frame_area = float(width * height)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(gray, 45, 135)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=2)
    contours = cv2.findContours(edges, cv2.RETR_EXTERNAL,
                                cv2.CHAIN_APPROX_SIMPLE)[-2]

    best = None
    best_score = -1.0
    for contour in contours:
        area = abs(cv2.contourArea(contour))
        coverage = area / frame_area
        if coverage < min_coverage or coverage > 0.93:
            continue
        perimeter = cv2.arcLength(contour, True)
        polygon = cv2.approxPolyDP(contour, 0.025 * perimeter, True)
        if len(polygon) != 4 or not cv2.isContourConvex(polygon):
            continue
        quad, card_w, card_h, ratio = _quad_metrics(polygon[:, 0, :])
        if not 1.30 <= ratio <= 1.90:
            continue
        box_area = card_w * card_h
        rectangularity = area / max(1.0, box_area)
        if rectangularity < 0.66:
            continue
        ratio_score = max(0.0, 1.0 - abs(ratio - ID_CARD_RATIO) / 0.45)
        score = coverage * 2.0 + rectangularity + ratio_score
        if score > best_score:
            best_score = score
            best = quad
    return best


def warp_card(frame, corners, width=WARP_W, height=WARP_H):
    '''透视矫正到标准身份证横向画布。'''
    source = order_quad(corners)
    target = np.array([
        [0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]
    ], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(source, target)
    return cv2.warpPerspective(frame, matrix, (width, height),
                               flags=cv2.INTER_CUBIC,
                               borderMode=cv2.BORDER_REPLICATE)


def enhance_document_crop(crop):
    '''压平光照并增强深色文字，返回三通道灰度图。

    这里不做强制二值化：低分辨率中文笔画经过 Otsu 后容易断裂，而 OCR 模型
    本身可以处理灰度抗锯齿。大尺度背景估计主要用于削弱阴影与防伪底纹。
    '''
    if crop is None or crop.size == 0:
        return crop
    if len(crop.shape) == 3:
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    else:
        gray = crop.copy()
    background = cv2.GaussianBlur(gray, (0, 0), 11)
    normalized = cv2.divide(gray, background, scale=242)
    try:
        normalized = cv2.createCLAHE(clipLimit=1.8, tileGridSize=(8, 4)).apply(
            normalized)
    except (AttributeError, cv2.error):
        normalized = cv2.equalizeHist(normalized)
    soft = cv2.GaussianBlur(normalized, (0, 0), 0.8)
    sharpened = cv2.addWeighted(normalized, 1.45, soft, -0.45, 8)
    return cv2.cvtColor(sharpened, cv2.COLOR_GRAY2BGR)


def _paste_fit(canvas, crop, target):
    '''等比例缩放 ROI 并左对齐放入目标区域，避免拉伸中文字形。'''
    x1, y1, x2, y2 = target
    available_w, available_h = x2 - x1, y2 - y1
    crop_h, crop_w = crop.shape[:2]
    scale = min(available_w / max(1, crop_w),
                available_h / max(1, crop_h))
    out_w = max(1, int(round(crop_w * scale)))
    out_h = max(1, int(round(crop_h * scale)))
    interpolation = cv2.INTER_CUBIC if scale >= 1.0 else cv2.INTER_AREA
    resized = cv2.resize(crop, (out_w, out_h), interpolation=interpolation)
    top = y1 + max(0, (available_h - out_h) // 2)
    canvas[top:top + out_h, x1:x1 + out_w] = resized


def build_id_ocr_canvas(card):
    '''把正面身份证字段拼成单次 KPU OCR 使用的干净画布。

    只保留真正需要读取的值区：姓名、民族、出生、地址和身份证号。性别与
    出生日期最终还会由合法身份证号回填/交叉校验。
    '''
    if card is None or card.size == 0:
        raise ValueError("身份证图像为空")
    if card.shape[1] != WARP_W or card.shape[0] != WARP_H:
        card = cv2.resize(card, (WARP_W, WARP_H), interpolation=cv2.INTER_CUBIC)
    canvas = np.full((OCR_CANVAS_H, OCR_CANVAS_W, 3), 255, np.uint8)
    for _, source, target in OCR_REGIONS:
        sx1, sy1, sx2, sy2 = source
        x1, y1 = int(round(sx1 * WARP_W)), int(round(sy1 * WARP_H))
        x2, y2 = int(round(sx2 * WARP_W)), int(round(sy2 * WARP_H))
        crop = enhance_document_crop(card[y1:y2, x1:x2])
        if crop is not None and crop.size:
            _paste_fit(canvas, crop, target)
    return canvas
