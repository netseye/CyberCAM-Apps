'''身份证取景的 OpenCV 几何处理。'''

import cv2
import numpy as np


ID_CARD_RATIO = 85.60 / 53.98
WARP_W = 640
WARP_H = round(WARP_W / ID_CARD_RATIO)


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
