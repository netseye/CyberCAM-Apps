"""Pure helpers for the CyberCAM app store.

This module deliberately depends only on the Python standard library so the
security-sensitive parsing and path logic can be tested away from the device.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from pathlib import PurePosixPath
from urllib.parse import urlsplit


APP_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class StoreError(Exception):
    """Base error shown to the app-store user."""


class CatalogError(StoreError):
    """A catalog or app metadata document is invalid."""


class SecurityError(StoreError):
    """Downloaded data failed a security validation."""


def validate_app_id(value: str) -> str:
    value = str(value or "")
    if not APP_ID_RE.fullmatch(value):
        raise CatalogError("无效的应用 ID：%s" % value)
    return value


def validate_sha256(value: str) -> str:
    value = str(value or "").lower()
    if not SHA256_RE.fullmatch(value):
        raise CatalogError("缺少有效的 SHA-256")
    return value


def validate_https_url(value: str) -> str:
    value = str(value or "")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise CatalogError("仅支持不含凭据和片段的 HTTPS 地址")
    return value


def normalize_relative_path(value: str) -> str:
    """Validate an archive/Git path and return a POSIX relative path."""

    value = str(value or "")
    if not value or "\x00" in value or "\\" in value or value.startswith("/"):
        raise SecurityError("不安全的文件路径：%s" % value)
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise SecurityError("不安全的文件路径：%s" % value)
    if ":" in parts[0]:
        raise SecurityError("不安全的文件路径：%s" % value)
    normalized = str(PurePosixPath(*parts))
    if normalized != value:
        raise SecurityError("非规范文件路径：%s" % value)
    return normalized


def parse_app_txt(text: str) -> dict[str, str]:
    """Parse the small assignment-only subset used by CyberCAM app.txt.

    The file is never sourced as shell code. Unknown lines and shell syntax are
    rejected instead of executed.
    """

    result: dict[str, str] = {}
    for line_number, raw_line in enumerate(str(text).splitlines(), 1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            tokens = shlex.split(stripped, comments=True, posix=True)
        except ValueError as exc:
            raise CatalogError("app.txt 第 %d 行格式错误：%s" % (line_number, exc))
        if len(tokens) != 1 or "=" not in tokens[0]:
            raise CatalogError("app.txt 第 %d 行不是字段赋值" % line_number)
        key, value = tokens[0].split("=", 1)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise CatalogError("app.txt 第 %d 行字段名无效" % line_number)
        result[key] = value
    for required in ("name_cn", "name_en", "index"):
        if not result.get(required):
            raise CatalogError("app.txt 缺少字段：%s" % required)
    try:
        int(result["index"])
    except ValueError:
        raise CatalogError("app.txt 的 index 必须是整数")
    return result


def load_json_bytes(data: bytes, label: str = "JSON") -> dict:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CatalogError("%s 解析失败：%s" % (label, exc))
    if not isinstance(value, dict):
        raise CatalogError("%s 顶层必须是对象" % label)
    return value


def git_blob_sha(data: bytes) -> str:
    header = ("blob %d\0" % len(data)).encode("ascii")
    return hashlib.sha1(header + data).hexdigest()  # Git object identifier.


def select_github_app_files(entries: list[dict], prefix: str) -> tuple[str, list[dict]]:
    """Select regular files below one app directory from a recursive Git tree."""

    prefix = normalize_relative_path(prefix).rstrip("/")
    directory_sha = ""
    files: list[dict] = []
    marker = prefix + "/"
    for entry in entries:
        path = str(entry.get("path", ""))
        kind = entry.get("type")
        mode = str(entry.get("mode", ""))
        if path == prefix and kind == "tree":
            directory_sha = str(entry.get("sha", ""))
            continue
        if not path.startswith(marker):
            continue
        relative = normalize_relative_path(path[len(marker) :])
        if kind == "tree":
            continue
        if kind != "blob" or mode not in ("100644", "100755"):
            raise SecurityError("应用包含不支持的 Git 对象：%s" % path)
        sha = str(entry.get("sha", "")).lower()
        if not GIT_SHA_RE.fullmatch(sha):
            raise CatalogError("Git 文件缺少有效 SHA：%s" % path)
        size = entry.get("size", 0)
        if not isinstance(size, int) or size < 0:
            raise CatalogError("Git 文件大小无效：%s" % path)
        files.append(
            {
                "repository_path": path,
                "relative_path": relative,
                "sha": sha,
                "size": size,
                "mode": mode,
            }
        )
    if not directory_sha or not GIT_SHA_RE.fullmatch(directory_sha.lower()):
        raise CatalogError("Git 仓库中不存在应用目录：%s" % prefix)
    if not files:
        raise CatalogError("应用目录为空：%s" % prefix)
    files.sort(key=lambda item: item["relative_path"])
    return directory_sha.lower(), files


def human_size(value: int) -> str:
    value = max(0, int(value or 0))
    units = ("B", "KB", "MB", "GB")
    number = float(value)
    for unit in units:
        if number < 1024.0 or unit == units[-1]:
            return ("%.1f %s" % (number, unit)) if unit != "B" else ("%d B" % value)
        number /= 1024.0
    return "%d B" % value


def map_touch_coordinates(
    raw_x: int,
    raw_y: int,
    x_range: tuple[int, int],
    y_range: tuple[int, int],
    flipped: bool = False,
) -> tuple[int, int]:
    """Map the K230 portrait touch axes onto the 640x480 app canvas."""

    min_x, max_x = x_range
    min_y, max_y = y_range
    x = int((raw_y - min_y) * 639 / max(1, max_y - min_y))
    y = int((max_x - raw_x) * 479 / max(1, max_x - min_x))
    x = max(0, min(639, x))
    y = max(0, min(479, y))
    if flipped:
        x, y = 639 - x, 479 - y
    return x, y
