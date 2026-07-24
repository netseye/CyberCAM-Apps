'''身份证 OCR 的纯逻辑层。

不依赖 cv2 / walnutpi，便于在开发机上测试字段解析、身份证校验、
卡片稳定判定和触摸区域映射。
'''

import datetime
import math
import re


FIELD_LABELS = ("姓名", "性别", "民族", "出生", "住址", "公民身份号码")
ETHNICITIES = {
    "汉", "蒙古", "回", "藏", "维吾尔", "苗", "彝", "壮", "布依", "朝鲜",
    "满", "侗", "瑶", "白", "土家", "哈尼", "哈萨克", "傣", "黎", "傈僳",
    "佤", "畲", "高山", "拉祜", "水", "东乡", "纳西", "景颇", "柯尔克孜",
    "土", "达斡尔", "仫佬", "羌", "布朗", "撒拉", "毛南", "仡佬", "锡伯",
    "阿昌", "普米", "塔吉克", "怒", "乌孜别克", "俄罗斯", "鄂温克", "德昂",
    "保安", "裕固", "京", "塔塔尔", "独龙", "鄂伦春", "赫哲", "门巴", "珞巴",
    "基诺",
}
ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
ID_CHECK_CODES = "10X98765432"
BACK_SIDE_KEYWORDS = ("签发机关", "有效期限", "中华人民共和国", "居民身份证")

# 真机上的当前量化识别模型会稳定地把小字号“汉”读成形近的“汀”。只收录
# 已由合成真机冒烟复现的混淆，不对任意单字做模糊猜测，避免把少数民族误
# 改成汉族。专用放大通道优先识别原字，此表仅作为仍然失败时的保守兜底。
ETHNICITY_OCR_CORRECTIONS = {"汀": "汉"}

# 与 vision.OCR_REGIONS 的目标画布纵向分区对应。放在纯逻辑层中可让解析测试
# 不依赖 OpenCV；分区之间留白，OCR 框中心不会落在边界上。
OCR_ROI_BANDS = {
    "name": (4 / 404, 72 / 404),
    # 性别与民族在同一横向画布带中左右分栏，解析时共同读取。
    "demographics": (76 / 404, 126 / 404),
    "birth": (130 / 404, 190 / 404),
    "address": (194 / 404, 318 / 404),
    "id_number": (326 / 404, 402 / 404),
}


def _compact(text):
    '''统一全半角标点并去掉 OCR 常见空白。'''
    return re.sub(r"\s+", "", str(text).replace("：", ":").strip())


def _value_after_label(line, label):
    pos = line.find(label)
    if pos < 0:
        return ""
    value = line[pos + len(label):].lstrip(":")
    stops = []
    for other in FIELD_LABELS:
        if other == label:
            continue
        at = value.find(other)
        if at >= 0:
            stops.append(at)
    if stops:
        value = value[:min(stops)]
    return value.strip("：:·,，。 ")


def normalize_id_candidate(text):
    '''只在疑似身份证号中纠正常见的拉丁字母/数字混淆。'''
    compact = re.sub(r"[^0-9A-Za-z]", "", str(text)).upper()
    if len(compact) < 15:
        return compact
    # 身份证前 17 位只能是数字，末位允许 X。
    trans = str.maketrans({
        "O": "0", "Q": "0", "D": "0", "I": "1", "L": "1",
        "Z": "2", "S": "5", "B": "8",
    })
    return compact[:-1].translate(trans) + compact[-1:].translate(trans)


def id_checksum(number17):
    '''返回 17 位本体对应的 GB 11643 校验字符。'''
    if not re.fullmatch(r"\d{17}", str(number17)):
        return None
    total = sum(int(ch) * weight for ch, weight in zip(number17, ID_WEIGHTS))
    return ID_CHECK_CODES[total % 11]


def is_valid_id_number(number):
    '''校验 18 位居民身份证号码，包括日期和校验位。'''
    number = str(number).upper()
    if not re.fullmatch(r"\d{17}[0-9X]", number):
        return False
    try:
        datetime.datetime.strptime(number[6:14], "%Y%m%d")
    except ValueError:
        return False
    return id_checksum(number[:17]) == number[-1]


def mask_id_number(number):
    '''结果页默认仅显示身份证号前 6 位和后 4 位。'''
    number = str(number)
    if len(number) < 10:
        return "未识别" if not number else "*" * len(number)
    return number[:6] + "*" * (len(number) - 10) + number[-4:]


def should_retry_orientation(parsed):
    '''号码没有通过校验时尝试 180°，避免错误的 18 位候选阻止重试。'''
    return not bool(parsed.get("id_valid"))


def recognition_status(parsed, rows):
    '''返回 reliable / partial / back / unreliable 四级识别结论。'''
    raw_text = "".join(
        str(row.get("text", "")) for row in rows
        if str(row.get("text", "")).strip()
    )
    if not raw_text:
        raw_text = "".join(parsed.get("raw_lines", []))
    compact = _compact(raw_text)
    if any(keyword in compact for keyword in BACK_SIDE_KEYWORDS):
        return "back"

    field_count = int(parsed.get("field_count", 0))
    if parsed.get("id_valid") and field_count >= 5:
        return "reliable"
    if parsed.get("id_valid") or field_count >= 3:
        return "partial"
    return "unreliable"


def ocr_candidate_score(parsed, rows):
    '''候选方向排序：可靠正面 > 合法号码 > 反面 > 无校验的部分字段。'''
    confidences = [float(row.get("confidence", 0.0)) for row in rows]
    confidence = sum(confidences) / max(1, len(confidences))
    status = recognition_status(parsed, rows)
    if status == "reliable":
        status_rank = 4
    elif parsed.get("id_valid"):
        status_rank = 3
    elif status == "back":
        status_rank = 2
    elif status == "partial":
        status_rank = 1
    else:
        status_rank = 0
    return (status_rank,
            int(parsed.get("field_count", 0)), confidence)


def _find_id_number(lines):
    candidates = []
    for line in lines:
        compact = normalize_id_candidate(line)
        candidates.extend(re.findall(r"\d{17}[0-9X]", compact))
    # OCR 偶尔会把号码从两行拆开，再尝试在合并文本中搜索。
    merged = normalize_id_candidate("".join(lines))
    candidates.extend(re.findall(r"\d{17}[0-9X]", merged))
    unique = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    for candidate in unique:
        if is_valid_id_number(candidate):
            return candidate, True
    return (unique[0], False) if unique else ("", False)


def _extract_date(text):
    match = re.search(
        r"((?:18|19|20)\d{2})[年./-]?([01]?\d)[月./-]?([0-3]?\d)日?", text)
    if not match:
        return ""
    year, month, day = map(int, match.groups())
    try:
        date = datetime.date(year, month, day)
    except ValueError:
        return ""
    return date.strftime("%Y-%m-%d")


def _clean_name(value):
    match = re.search(r"[\u3400-\u9fff·]{2,20}", value)
    return match.group(0) if match else value[:20]


def normalize_ethnicity_candidate(value):
    '''把民族 OCR 文本收敛到法定民族名称；未知文本不作猜测。'''
    compact = _compact(value).replace("民族", "").strip("：:，,。 ")
    for ethnicity in sorted(ETHNICITIES, key=len, reverse=True):
        if ethnicity in compact:
            return ethnicity
    return ETHNICITY_OCR_CORRECTIONS.get(compact, "")


def _address_text(value):
    value = _compact(value).replace("住址", "").strip("：:，,。 ")
    # 文字检测框偶尔会把边缘底纹读成引号/反引号；只清理地址两端噪声，
    # 内部的连字符、括号等真实门牌信息保持原样。
    value = re.sub(r"^[^\u3400-\u9fff0-9A-Za-z]+", "", value)
    return re.sub(r"[^\u3400-\u9fff0-9A-Za-z]+$", "", value)


def address_candidate_score(value, confidence=0.0):
    '''评估地址候选；专用放大结果必须像中文地址才可覆盖主结果。'''
    value = _address_text(value)
    if not value:
        return (0, 0.0, 0, 0.0)
    useful = re.findall(r"[\u3400-\u9fff0-9A-Za-z-]", value)
    chinese_digits = re.findall(r"[\u3400-\u9fff0-9]", value)
    useful_ratio = len(chinese_digits) / max(1, len(useful))
    address_marks = sum(mark in value for mark in (
        "省", "市", "州", "区", "县", "旗", "乡", "镇", "街", "路", "村",
        "号", "室", "栋", "单元", "弄", "巷", "大道"))
    plausible = int(len(chinese_digits) >= 6 and useful_ratio >= 0.72)
    return (plausible, useful_ratio, min(len(chinese_digits), 60),
            min(1.0, float(confidence)) + min(address_marks, 4) * 0.04)


def order_ocr_rows(rows):
    '''把带 x/y/h 的 OCR 框按真实阅读行排序。

    同一基线上的“性别/男/民族/汉”即使 y 有几像素误差，也先按 x 排列，
    避免直接 ``(y, x)`` 排序打乱字段和值。
    '''
    groups = []
    for row in sorted(rows, key=lambda item: item.get("y", 0)):
        center = row.get("y", 0) + row.get("h", 0) / 2.0
        height = max(1.0, float(row.get("h", 1)))
        chosen = None
        for group in groups:
            threshold = max(8.0, 0.55 * max(height, group["height"]))
            if abs(center - group["center"]) <= threshold:
                chosen = group
                break
        if chosen is None:
            groups.append({"center": center, "height": height, "rows": [row]})
        else:
            chosen["rows"].append(row)
            count = len(chosen["rows"])
            chosen["center"] += (center - chosen["center"]) / count
            chosen["height"] = max(chosen["height"], height)
    groups.sort(key=lambda group: group["center"])
    return [row for group in groups
            for row in sorted(group["rows"], key=lambda item: item.get("x", 0))]


def parse_id_card(lines):
    '''把按阅读顺序排列的 OCR 行解析成身份证正面字段。

    即便标签未全部识别，也会用合法身份证号回填出生日期和性别。
    原始行原样留在 ``raw_lines``，供结果页人工核对。
    '''
    raw_lines = [str(line).strip() for line in lines if str(line).strip()]
    compact_lines = [_compact(line) for line in raw_lines]
    result = {
        "name": "", "sex": "", "ethnicity": "", "birth": "",
        "address": "", "id_number": "", "id_valid": False,
        "raw_lines": raw_lines, "warnings": [],
    }

    address_parts = []
    collecting_address = False
    pending_field = None
    for line in compact_lines:
        has_any_label = any(label in line for label in FIELD_LABELS)
        if pending_field and not has_any_label:
            if pending_field == "name":
                result["name"] = _clean_name(line)
            elif pending_field == "sex":
                match = re.search(r"[男女]", line)
                result["sex"] = match.group(0) if match else ""
            elif pending_field == "ethnicity":
                result["ethnicity"] = normalize_ethnicity_candidate(line)
            elif pending_field == "birth":
                result["birth"] = _extract_date(line)
            pending_field = None
        elif pending_field and has_any_label:
            # 未识别到值就遇到了下一个字段，不能把后续地址误当成旧字段值。
            pending_field = None

        if "姓名" in line and not result["name"]:
            result["name"] = _clean_name(_value_after_label(line, "姓名"))
            if not result["name"]:
                pending_field = "name"

        if "性别" in line and not result["sex"]:
            sex_value = _value_after_label(line, "性别")
            match = re.search(r"[男女]", sex_value)
            if match:
                result["sex"] = match.group(0)
            else:
                pending_field = "sex"

        if "民族" in line and not result["ethnicity"]:
            value = _value_after_label(line, "民族")
            ethnicity = normalize_ethnicity_candidate(value)
            if ethnicity:
                result["ethnicity"] = ethnicity
            else:
                pending_field = "ethnicity"

        if "出生" in line and not result["birth"]:
            result["birth"] = _extract_date(_value_after_label(line, "出生"))
            if not result["birth"]:
                pending_field = "birth"

        if "住址" in line:
            collecting_address = True
            value = _value_after_label(line, "住址")
            if value:
                address_parts.append(value)
            continue

        has_id_label = ("公民身份号码" in line or "身份号码" in line)
        if has_id_label or re.search(r"\d{17}[0-9Xx]", normalize_id_candidate(line)):
            collecting_address = False
        elif collecting_address:
            # 过滤误入地址段的已知标签行。
            if not any(label in line for label in FIELD_LABELS[:-2]):
                address_parts.append(line)

    result["address"] = "".join(address_parts).strip("：:，,。 ")
    number, valid = _find_id_number(compact_lines)
    result["id_number"] = number
    result["id_valid"] = valid

    if number and len(number) == 18:
        birth_from_id = _extract_date(number[6:14])
        if not result["birth"]:
            result["birth"] = birth_from_id
        elif birth_from_id and result["birth"] != birth_from_id:
            result["warnings"].append("出生日期与身份证号不一致")
        sex_from_id = "男" if int(number[16]) % 2 else "女"
        if not result["sex"]:
            result["sex"] = sex_from_id
        elif result["sex"] != sex_from_id:
            result["warnings"].append("性别与身份证号不一致")
        if not valid:
            result["warnings"].append("身份证号码校验未通过")

    required = ("name", "sex", "ethnicity", "birth", "address", "id_number")
    result["field_count"] = sum(bool(result[field]) for field in required)
    return result


def parse_id_card_rows(rows, card_width=640, card_height=404):
    '''结合二代身份证固定版式解析 OCR 框。

    通用 OCR 对较小的“姓名/性别/民族”标签容易误识别，但往往能正确读出
    右侧字段值。本函数先走标签解析，再用标准正面版式坐标补齐缺失值；
    身份证号的校验与派生字段仍由 :func:`parse_id_card` 负责。
    '''
    ordered = order_ocr_rows(rows)
    result = parse_id_card([row.get("text", "") for row in ordered])

    def center(row):
        return (row.get("x", 0) + row.get("w", 0) / 2.0,
                row.get("y", 0) + row.get("h", 0) / 2.0)

    def candidates(y_min, y_max, x_min=0.0, x_max=1.0):
        found = []
        for row in ordered:
            x, y = center(row)
            nx, ny = x / max(1, card_width), y / max(1, card_height)
            if x_min <= nx <= x_max and y_min <= ny < y_max:
                value = _compact(row.get("text", ""))
                if value:
                    found.append((row, value))
        return found

    if not result["name"]:
        for _, value in candidates(0.08, 0.24, 0.14, 0.66):
            if any(label in value for label in FIELD_LABELS):
                continue
            name = _clean_name(value)
            if 2 <= len(name) <= 12:
                result["name"] = name
                break

    if not result["sex"]:
        for _, value in candidates(0.22, 0.40, 0.12, 0.38):
            match = re.search(r"[男女]", value)
            if match:
                result["sex"] = match.group(0)
                break

    if not result["ethnicity"]:
        for _, value in candidates(0.22, 0.40, 0.36, 0.66):
            ethnicity = normalize_ethnicity_candidate(value)
            if ethnicity:
                result["ethnicity"] = ethnicity
                break

    if not result["birth"]:
        for _, value in candidates(0.34, 0.49, 0.12, 0.70):
            birth = _extract_date(value)
            if birth:
                result["birth"] = birth
                break

    if not result["address"]:
        address_parts = []
        for _, value in candidates(0.48, 0.80, 0.13, 0.74):
            if (not re.search(r"\d{17}[0-9Xx]", normalize_id_candidate(value))
                    and "公民身份" not in value):
                address_parts.append(value.replace("住址", "").lstrip(":"))
        result["address"] = "".join(address_parts).strip("：:，,。 ")

    required = ("name", "sex", "ethnicity", "birth", "address", "id_number")
    result["field_count"] = sum(bool(result[field]) for field in required)
    return result


def parse_id_card_roi_rows(rows, canvas_height=404):
    '''解析固定 ROI 画布的 OCR 框，不依赖任何固定标签被识别。

    每个框按纵向中心归入姓名/民族/出生/地址/号码区域，再借用统一的身份
    证号码校验和派生逻辑。``raw_lines`` 仍保留识别器原始输出供人工核对。
    '''
    ordered = order_ocr_rows(rows)
    grouped = {field: [] for field in OCR_ROI_BANDS}
    for row in ordered:
        value = _compact(row.get("text", ""))
        if not value:
            continue
        center_y = row.get("y", 0) + row.get("h", 0) / 2.0
        normalized_y = center_y / max(1, canvas_height)
        for field, (y_min, y_max) in OCR_ROI_BANDS.items():
            if y_min <= normalized_y < y_max:
                grouped[field].append(value)
                break

    pseudo_lines = []
    name_text = "".join(grouped["name"])
    name = _clean_name(name_text)
    if name:
        pseudo_lines.append("姓名" + name)

    demographics_text = "".join(grouped["demographics"])
    sex_match = re.search(r"[男女]", demographics_text)
    if sex_match:
        pseudo_lines.append("性别" + sex_match.group(0))

    ethnicity_match = normalize_ethnicity_candidate(demographics_text)
    if ethnicity_match:
        pseudo_lines.append("民族" + ethnicity_match)

    birth_text = "".join(grouped["birth"])
    birth = _extract_date(birth_text)
    if birth:
        pseudo_lines.append("出生" + birth)

    address_parts = []
    for value in grouped["address"]:
        cleaned = value.replace("住址", "").strip("：:，,。 ")
        if cleaned and re.search(r"[\u3400-\u9fff0-9]", cleaned):
            address_parts.append(cleaned)
    if address_parts:
        pseudo_lines.append("住址" + "".join(address_parts))

    pseudo_lines.extend(grouped["id_number"])
    result = parse_id_card(pseudo_lines)
    result["raw_lines"] = [
        str(row.get("text", "")).strip() for row in ordered
        if str(row.get("text", "")).strip()
    ]
    return result


def parse_id_detail_rows(rows, canvas_height=404):
    '''解析民族/地址专用放大画布，保留字段级平均置信度。'''
    ordered = order_ocr_rows(rows)
    ethnicity = ""
    ethnicity_confidence = 0.0
    ethnicity_corrected = False
    address_rows = []
    for row in ordered:
        value = _compact(row.get("text", ""))
        if not value:
            continue
        center_y = row.get("y", 0) + row.get("h", 0) / 2.0
        normalized_y = center_y / max(1, canvas_height)
        confidence = float(row.get("confidence", 0.0))
        if 0.0 <= normalized_y < 140 / 404:
            candidate = normalize_ethnicity_candidate(value)
            if candidate and confidence >= ethnicity_confidence:
                ethnicity = candidate
                ethnicity_confidence = confidence
                raw = value.replace("民族", "").strip("：:，,。 ")
                ethnicity_corrected = raw in ETHNICITY_OCR_CORRECTIONS
        elif 140 / 404 <= normalized_y < 1.0:
            cleaned = _address_text(value)
            if cleaned and re.search(r"[\u3400-\u9fff0-9]", cleaned):
                address_rows.append((cleaned, confidence))

    address = "".join(value for value, _ in address_rows)
    address_chars = sum(len(value) for value, _ in address_rows)
    address_confidence = (sum(len(value) * confidence
                              for value, confidence in address_rows) /
                          max(1, address_chars))
    return {
        "ethnicity": ethnicity,
        "ethnicity_confidence": ethnicity_confidence,
        "ethnicity_corrected": ethnicity_corrected,
        "address": address,
        "address_confidence": address_confidence,
        "raw_lines": [str(row.get("text", "")).strip() for row in ordered
                      if str(row.get("text", "")).strip()],
    }


def parse_ethnicity_retry_rows(rows):
    '''从民族专用重试画布中选择候选，精确词典结果优先于纠错结果。'''
    best = None
    for row in order_ocr_rows(rows):
        raw = _compact(row.get("text", "")).replace("民族", "").strip(
            "：:，,。 ")
        ethnicity = normalize_ethnicity_candidate(raw)
        if not ethnicity:
            continue
        corrected = raw in ETHNICITY_OCR_CORRECTIONS
        confidence = float(row.get("confidence", 0.0))
        score = (int(not corrected), confidence)
        if best is None or score > best[0]:
            best = (score, ethnicity, confidence, corrected)
    if best is None:
        ethnicity = ""
        confidence = 0.0
        corrected = False
    else:
        _, ethnicity, confidence, corrected = best
    return {
        "ethnicity": ethnicity,
        "ethnicity_confidence": confidence,
        "ethnicity_corrected": corrected,
        "address": "",
        "address_confidence": 0.0,
        "raw_lines": [str(row.get("text", "")).strip() for row in rows
                      if str(row.get("text", "")).strip()],
    }


def merge_id_detail_fields(parsed, detail):
    '''用可信的专用放大结果补充或替换民族、地址字段。'''
    result = dict(parsed)
    result["warnings"] = list(parsed.get("warnings", []))
    result["raw_lines"] = list(parsed.get("raw_lines", []))

    ethnicity = detail.get("ethnicity", "")
    current_ethnicity = result.get("ethnicity", "")
    ethnicity_is_correction = bool(detail.get("ethnicity_corrected", False))
    if ethnicity and (not current_ethnicity or
                      ethnicity == current_ethnicity or
                      (not ethnicity_is_correction and
                       float(detail.get("ethnicity_confidence", 0.0)) >= 0.45)):
        result["ethnicity"] = ethnicity

    address = _address_text(detail.get("address", ""))
    detail_score = address_candidate_score(
        address, detail.get("address_confidence", 0.0))
    current_score = address_candidate_score(result.get("address", ""), 0.0)
    current_length = len(re.findall(
        r"[\u3400-\u9fff0-9]", result.get("address", "")))
    detail_length = len(re.findall(r"[\u3400-\u9fff0-9]", address))
    confident_complete_detail = (
        float(detail.get("address_confidence", 0.0)) >= 0.45 and
        detail_length >= max(6, current_length - 3))
    if (address and detail_score[0] and
            (not current_score[0] or confident_complete_detail or
             detail_score > current_score)):
        result["address"] = address

    result["raw_lines"].extend(detail.get("raw_lines", []))
    required = ("name", "sex", "ethnicity", "birth", "address", "id_number")
    result["field_count"] = sum(bool(result.get(field)) for field in required)
    return result


class CardStability:
    '''对四角位置做低通，并要求连续稳定一段时间后才准许识别。'''

    def __init__(self, max_motion_px=9.0, hold_s=0.35, alpha=0.35):
        self.max_motion_px = max_motion_px
        self.hold_s = hold_s
        self.alpha = alpha
        self.corners = None
        self._stable_since = None

    def reset(self):
        self.corners = None
        self._stable_since = None

    def update(self, corners, now):
        if corners is None or len(corners) != 4:
            self.reset()
            return False, None
        points = [(float(x), float(y)) for x, y in corners]
        if self.corners is None:
            self.corners = points
            self._stable_since = now
            return False, self.corners

        rms = math.sqrt(sum(
            (new[0] - old[0]) ** 2 + (new[1] - old[1]) ** 2
            for new, old in zip(points, self.corners)) / 4.0)
        if rms > self.max_motion_px:
            self._stable_since = now
        self.corners = [
            (old[0] * (1 - self.alpha) + new[0] * self.alpha,
             old[1] * (1 - self.alpha) + new[1] * self.alpha)
            for new, old in zip(points, self.corners)
        ]
        ready = self._stable_since is not None and now - self._stable_since >= self.hold_s
        return ready, self.corners


def touch_action(nx, ny, state="preview", controls_top=0.875):
    '''把归一化触摸点映射为预览页或结果页动作。'''
    nx = max(0.0, min(1.0, nx))
    ny = max(0.0, min(1.0, ny))
    if nx <= 0.09 and ny <= 0.13:
        return "EXIT"
    if state == "preview":
        if ny >= controls_top:
            if nx < 0.25:
                return "EXIT"
            if nx > 0.75:
                return "LIGHT"
            return "SCAN"
        return "SCAN"
    if ny >= controls_top:
        if nx < 0.25:
            return "REVEAL"
        if nx > 0.75:
            return "RAW"
        return "RESCAN"
    return None
