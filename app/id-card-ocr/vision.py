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
    # 性别和民族共享一条画布带但左右分开，既保留单字“男/女”，也不压缩
    # 地址区域。core.parse_id_card_roi_rows 会在同一纵向带内分别提取二者。
    ("sex", (0.16, 0.24, 0.30, 0.37), (16, 76, 170, 126)),
    # 实拍标准证件的民族值位于约 0.40W；旧起点 0.43W 会直接裁掉单字。
    ("ethnicity", (0.39, 0.24, 0.52, 0.37), (190, 76, 624, 126)),
    ("birth", (0.16, 0.36, 0.67, 0.50), (16, 130, 624, 190)),
    # 地址栏在头像左侧结束。限制到 0.64W，避免把人像和衣服当成文字。
    ("address", (0.16, 0.49, 0.625, 0.76), (16, 194, 624, 318)),
    ("id_number", (0.27, 0.79, 0.99, 0.96), (16, 326, 624, 402)),
)

# 姓名和号码在主画布中通常足够稳定；民族是单字、地址又是多行小字，把这
# 两块再放到一张专用画布中做一次放大识别。它只增加一次 KPU 推理，不需要
# 同时加载第二套模型。目标区域之间留出 16 px 空白，避免 DBNet 跨区合框。
DETAIL_REGIONS = (
    ("ethnicity", (0.39, 0.24, 0.52, 0.37), (16, 12, 624, 132)),
    ("address", (0.16, 0.49, 0.625, 0.76), (16, 148, 624, 396)),
)

# 民族单字偶发漏检时的专用重试。真机模糊边界样本中，常规增强、原图和
# Otsu 都会被量化模型读成数字/拉丁字符；只裁民族值并反锐化后可稳定恢复。
ETHNICITY_RETRY_REGIONS = (
    ("unsharp", (0.385, 0.225, 0.49, 0.345), (16, 20, 624, 384)),
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


def _find_id_card_by_chroma(frame, min_coverage):
    '''在浅色背景上用 Lab 黄蓝色差寻找低对比度身份证。

    实拍中卡片外沿与桌面亮度接近，Canny 可能完全没有闭合轮廓；二代证的
    蓝红防伪底纹在 Lab b 通道中仍与偏暖桌面明显分离。Otsu 阈值只用于生成
    候选，最终仍要求身份证比例、覆盖率和高矩形填充率，抑制普通色块误检。
    '''
    height, width = frame.shape[:2]
    frame_area = float(width * height)
    lab_b = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)[:, :, 2]
    if float(lab_b.std()) < 2.0:
        return None
    _, mask = cv2.threshold(
        lab_b, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    close_size = max(5, int(round(min(width, height) / 34.0)) | 1)
    open_size = max(3, int(round(close_size / 3.0)) | 1)
    mask = cv2.morphologyEx(
        mask, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT,
                                  (close_size, close_size)),
        iterations=2)
    mask = cv2.morphologyEx(
        mask, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT,
                                  (open_size, open_size)),
        iterations=1)
    contours = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                cv2.CHAIN_APPROX_SIMPLE)[-2]

    best = None
    best_score = -1.0
    for contour in contours:
        area = abs(cv2.contourArea(contour))
        coverage = area / frame_area
        if coverage < min_coverage or coverage > 0.93:
            continue
        rect = cv2.minAreaRect(contour)
        rect_w, rect_h = rect[1]
        long_side, short_side = max(rect_w, rect_h), min(rect_w, rect_h)
        if short_side < 1.0:
            continue
        ratio = long_side / short_side
        if not 1.30 <= ratio <= 1.90:
            continue
        rectangularity = area / max(1.0, rect_w * rect_h)
        if rectangularity < 0.72:
            continue
        ratio_score = max(0.0, 1.0 - abs(ratio - ID_CARD_RATIO) / 0.35)
        score = coverage * 2.0 + rectangularity + ratio_score
        if score > best_score:
            best_score = score
            best = order_quad(cv2.boxPoints(rect))
    return best


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
    if best is not None:
        return best
    return _find_id_card_by_chroma(frame, min_coverage)


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


def document_quality(card):
    '''评估矫正证件的清晰度、曝光、对比度和高光裁切。

    指标只在用户点击识别时计算，并缩小到半分辨率，避免拖慢实时预览。
    返回的 score 用于多帧择优；issues 用于首次低质量时提示重拍。
    '''
    if card is None or card.size == 0:
        raise ValueError("身份证图像为空")
    gray = (cv2.cvtColor(card, cv2.COLOR_BGR2GRAY)
            if len(card.shape) == 3 else card)
    if gray.shape[1] > 320:
        sample_h = max(1, round(gray.shape[0] * 320 / gray.shape[1]))
        gray = cv2.resize(gray, (320, sample_h), interpolation=cv2.INTER_AREA)

    gray_f = gray.astype(np.float32)
    laplacian = cv2.Laplacian(gray, cv2.CV_32F)
    sharpness = float(laplacian.var())
    brightness = float(gray_f.mean())
    contrast = float(gray_f.std())
    highlight_ratio = float(np.mean(gray >= 252))
    shadow_ratio = float(np.mean(gray <= 18))

    sharp_score = min(1.0, sharpness / 180.0)
    contrast_score = min(1.0, contrast / 55.0)
    exposure_score = max(0.0, 1.0 - abs(brightness - 145.0) / 120.0)
    clipping_score = max(
        0.0, 1.0 - (highlight_ratio + shadow_ratio) / 0.20)
    score = (0.55 * sharp_score + 0.20 * contrast_score +
             0.15 * exposure_score + 0.10 * clipping_score)

    issues = []
    if sharpness < 35.0:
        issues.append("画面模糊")
    if brightness < 55.0 or shadow_ratio > 0.30:
        issues.append("光线不足")
    elif brightness > 225.0:
        issues.append("画面过曝")
    if highlight_ratio > 0.16:
        issues.append("反光过强")
    if contrast < 22.0:
        issues.append("文字对比度低")
    return {
        "score": float(score),
        "sharpness": sharpness,
        "brightness": brightness,
        "contrast": contrast,
        "highlight_ratio": highlight_ratio,
        "shadow_ratio": shadow_ratio,
        "issues": issues,
    }


def select_best_card(candidates, fallback_corners):
    '''从最近几帧中透视矫正并保留质量最高的一张。

    candidates 是 ``(frame, corners)`` 序列；检测不到四角的帧使用屏幕标准
    取景框。函数逐张评分，任意时刻只额外保留当前最佳矫正图。
    '''
    best_card = best_quality = None
    for frame, corners in candidates:
        if frame is None or frame.size == 0:
            continue
        card = warp_card(frame, corners if corners is not None else fallback_corners)
        quality = document_quality(card)
        if best_quality is None or quality["score"] > best_quality["score"]:
            best_card, best_quality = card, quality
    if best_card is None:
        raise ValueError("没有可用于识别的取景帧")
    return best_card, best_quality


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

    只保留真正需要读取的值区：姓名、性别、民族、出生、地址和身份证号。
    性别与出生日期仍会由合法身份证号回填/交叉校验。
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


def build_id_detail_ocr_canvas(card):
    '''构建民族/地址专用放大画布，供主识别后的第二次精读使用。'''
    if card is None or card.size == 0:
        raise ValueError("身份证图像为空")
    if card.shape[1] != WARP_W or card.shape[0] != WARP_H:
        card = cv2.resize(card, (WARP_W, WARP_H), interpolation=cv2.INTER_CUBIC)
    canvas = np.full((OCR_CANVAS_H, OCR_CANVAS_W, 3), 255, np.uint8)
    for _, source, target in DETAIL_REGIONS:
        sx1, sy1, sx2, sy2 = source
        x1, y1 = int(round(sx1 * WARP_W)), int(round(sy1 * WARP_H))
        x2, y2 = int(round(sx2 * WARP_W)), int(round(sy2 * WARP_H))
        crop = enhance_document_crop(card[y1:y2, x1:x2])
        if crop is not None and crop.size:
            _paste_fit(canvas, crop, target)
    return canvas


def build_ethnicity_retry_canvas(card):
    '''构建民族漏检时才使用的值区反锐化专用画布。'''
    if card is None or card.size == 0:
        raise ValueError("身份证图像为空")
    if card.shape[1] != WARP_W or card.shape[0] != WARP_H:
        card = cv2.resize(card, (WARP_W, WARP_H), interpolation=cv2.INTER_CUBIC)
    canvas = np.full((OCR_CANVAS_H, OCR_CANVAS_W, 3), 255, np.uint8)
    for mode, source, target in ETHNICITY_RETRY_REGIONS:
        sx1, sy1, sx2, sy2 = source
        x1, y1 = int(round(sx1 * WARP_W)), int(round(sy1 * WARP_H))
        x2, y2 = int(round(sx2 * WARP_W)), int(round(sy2 * WARP_H))
        crop = card[y1:y2, x1:x2]
        if mode == "enhanced":
            crop = enhance_document_crop(crop)
        elif mode == "unsharp" and crop is not None and crop.size:
            gray = (cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
                    if len(crop.shape) == 3 else crop.copy())
            soft = cv2.GaussianBlur(gray, (0, 0), 1.0)
            crop = cv2.addWeighted(gray, 1.9, soft, -0.9, 6)
            crop = cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
        elif crop is not None and crop.size and len(crop.shape) == 2:
            crop = cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
        if crop is not None and crop.size:
            _paste_fit(canvas, crop, target)
    return canvas
