"""Small HTTPS client used by the CyberCAM app store."""

from __future__ import annotations

import os
import re
import socket
import time
import urllib.error
import urllib.request

from store_core import CatalogError, StoreError, load_json_bytes, validate_https_url


class NetworkError(StoreError):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class HttpClient:
    def __init__(
        self,
        timeout: int = 20,
        user_agent: str = "CyberCAM-AppStore/1",
        retries: int = 3,
        retry_delay: float = 1.0,
    ):
        self.timeout = timeout
        self.user_agent = user_agent
        self.retries = max(0, int(retries))
        self.retry_delay = max(0.0, float(retry_delay))

    def _retry(self, operation):
        for attempt in range(self.retries + 1):
            try:
                return operation()
            except NetworkError as exc:
                if not exc.retryable or attempt >= self.retries:
                    raise
                time.sleep(self.retry_delay * (2**attempt))
        raise NetworkError("网络重试失败")

    def _open(self, url: str, headers: dict[str, str] | None = None):
        validate_https_url(url)
        request_headers = {
            "Accept": "application/vnd.github+json, application/json;q=0.9, */*;q=0.2",
            "User-Agent": self.user_agent,
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if headers:
            request_headers.update(headers)
        request = urllib.request.Request(
            url,
            headers=request_headers,
        )
        try:
            response = urllib.request.urlopen(request, timeout=self.timeout)
            validate_https_url(response.geturl())
            return response
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 429):
                reset = exc.headers.get("X-RateLimit-Reset", "")
                suffix = "，请稍后重试"
                if reset:
                    suffix = "，限额重置时间戳 " + reset
                raise NetworkError("服务器请求受限（HTTP %d）%s" % (exc.code, suffix))
            raise NetworkError(
                "下载失败（HTTP %d）" % exc.code,
                retryable=exc.code in (408, 425, 500, 502, 503, 504),
            )
        except (urllib.error.URLError, socket.timeout, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise NetworkError(
                "网络连接失败：%s" % reason,
                retryable=True,
            )
        except CatalogError:
            raise NetworkError("服务器重定向到了不安全的地址")

    def get_bytes(self, url: str, max_bytes: int = 4 * 1024 * 1024) -> bytes:
        return self._retry(lambda: self._get_bytes_once(url, max_bytes))

    def _get_bytes_once(self, url: str, max_bytes: int) -> bytes:
        chunks: list[bytes] = []
        received = 0
        try:
            with self._open(url) as response:
                length = response.headers.get("Content-Length")
                if length:
                    try:
                        parsed_length = int(length)
                        if parsed_length < 0:
                            raise ValueError
                        if parsed_length > max_bytes:
                            raise NetworkError("服务器响应过大")
                    except ValueError:
                        raise NetworkError("服务器返回了无效文件大小")
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > max_bytes:
                        raise NetworkError("服务器响应超过大小限制")
                    chunks.append(chunk)
        except NetworkError:
            raise
        except (urllib.error.URLError, socket.timeout, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise NetworkError(
                "网络读取失败：%s" % reason,
                retryable=True,
            )
        return b"".join(chunks)

    def get_json(self, url: str, max_bytes: int = 4 * 1024 * 1024) -> dict:
        return load_json_bytes(self.get_bytes(url, max_bytes), url)

    def download(
        self,
        url: str,
        destination: str,
        *,
        max_bytes: int,
        expected_size: int | None = None,
        progress=None,
    ) -> int:
        return self.download_from(
            [url],
            destination,
            max_bytes=max_bytes,
            expected_size=expected_size,
            progress=progress,
        )

    def download_from(
        self,
        urls: list[str],
        destination: str,
        *,
        max_bytes: int,
        expected_size: int | None = None,
        progress=None,
    ) -> int:
        """Download with resume, rotating through equivalent HTTPS endpoints."""

        if not urls:
            raise NetworkError("没有可用下载地址")
        urls = [validate_https_url(url) for url in urls]
        part = destination + ".part"
        try:
            os.unlink(part)
        except FileNotFoundError:
            pass
        try:
            attempts = max(self.retries + 1, len(urls))
            for attempt in range(attempts):
                try:
                    return self._download_once(
                        urls[attempt % len(urls)],
                        destination,
                        max_bytes=max_bytes,
                        expected_size=expected_size,
                        progress=progress,
                    )
                except NetworkError as exc:
                    if not exc.retryable or attempt + 1 >= attempts:
                        raise
                    time.sleep(self.retry_delay * (2**attempt))
        except Exception:
            try:
                os.unlink(part)
            except FileNotFoundError:
                pass
            raise
        raise NetworkError("网络重试失败")

    def _download_once(
        self,
        url: str,
        destination: str,
        *,
        max_bytes: int,
        expected_size: int | None,
        progress,
    ) -> int:
        part = destination + ".part"
        received = os.path.getsize(part) if os.path.isfile(part) else 0
        if received > max_bytes or (
            expected_size is not None and received > expected_size
        ):
            os.unlink(part)
            received = 0
        if expected_size is not None and received == expected_size:
            os.replace(part, destination)
            return received
        request_headers = {"Range": "bytes=%d-" % received} if received else None
        try:
            with self._open(url, headers=request_headers) as response:
                status = getattr(response, "status", None) or response.getcode()
                length_header = response.headers.get("Content-Length")
                try:
                    content_length = int(length_header) if length_header else None
                    if content_length is not None and content_length < 0:
                        raise ValueError
                except ValueError:
                    raise NetworkError("服务器返回了无效文件大小")
                mode = "wb"
                if received and status == 206:
                    content_range = response.headers.get("Content-Range", "")
                    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+|\*)", content_range)
                    if not match or int(match.group(1)) != received:
                        raise NetworkError("服务器返回了无效断点范围")
                    total = None if match.group(3) == "*" else int(match.group(3))
                    if expected_size is not None and total != expected_size:
                        raise NetworkError("服务器文件大小与清单不一致")
                    mode = "ab"
                elif received:
                    received = 0
                if (
                    content_length is not None
                    and received + content_length > max_bytes
                ):
                    raise NetworkError("下载文件超过大小限制")
                if expected_size is not None and content_length is not None:
                    if received + content_length != expected_size:
                        raise NetworkError("服务器文件大小与清单不一致")
                with open(part, mode) as output:
                    while True:
                        chunk = response.read(128 * 1024)
                        if not chunk:
                            break
                        received += len(chunk)
                        if received > max_bytes:
                            raise NetworkError("下载文件超过大小限制")
                        output.write(chunk)
                        if progress is not None:
                            progress(received, expected_size or content_length or 0)
                    output.flush()
                    os.fsync(output.fileno())
            if expected_size is not None and received != expected_size:
                raise NetworkError("下载文件不完整", retryable=True)
            os.replace(part, destination)
            return received
        except Exception as exc:
            if isinstance(exc, NetworkError):
                raise
            if isinstance(
                exc,
                (urllib.error.URLError, socket.timeout, TimeoutError, OSError),
            ):
                reason = getattr(exc, "reason", exc)
                raise NetworkError(
                    "网络读取失败：%s" % reason,
                    retryable=True,
                )
            raise
