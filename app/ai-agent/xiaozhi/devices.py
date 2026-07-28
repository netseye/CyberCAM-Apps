"""Safe CyberCAM hardware adapters exposed through Xiaozhi MCP."""

import json
import http.client
import os
import platform
import re
import socket
import ssl
import subprocess
import threading
import urllib.parse
import uuid
from dataclasses import asdict, is_dataclass


BACKLIGHT_PATH = "/sys/class/backlight/backlight"
STATUS_LED_PATH = "/sys/class/leds/green:status"


def _read_text(path, default=""):
    try:
        with open(path, "r", encoding="ascii") as handle:
            return handle.read().strip()
    except OSError:
        return default


def _write_text(path, value):
    with open(path, "w", encoding="ascii") as handle:
        handle.write(str(value))


def _percent_to_raw(percent, maximum):
    return max(0, min(maximum, round(percent * maximum / 100.0)))


def _parse_mixer_percent(output):
    matches = re.findall(r"\[(\d+)%\]", output or "")
    if not matches:
        raise RuntimeError("无法读取扬声器音量")
    return int(matches[-1])


def _default_route_interface(route_path="/proc/net/route"):
    """Return the lowest-metric active IPv4 default-route interface."""
    routes = []
    try:
        with open(route_path, "r", encoding="ascii") as handle:
            for line in handle:
                fields = line.split()
                if len(fields) < 8 or fields[1] != "00000000":
                    continue
                try:
                    flags = int(fields[3], 16)
                    metric = int(fields[6])
                except ValueError:
                    continue
                if flags & 0x1:
                    routes.append((metric, fields[0]))
    except OSError:
        return ""
    return min(routes)[1] if routes else ""


def _active_network_interface():
    interface = _default_route_interface()
    if (
        interface
        and os.path.exists("/sys/class/net/%s" % interface)
        and _read_text("/sys/class/net/%s/operstate" % interface) == "up"
    ):
        return interface
    candidates = ("wlan0", "eth0", "end0")
    for candidate in candidates:
        if _read_text("/sys/class/net/%s/operstate" % candidate) == "up":
            return candidate
    for candidate in candidates:
        if os.path.exists("/sys/class/net/%s" % candidate):
            return candidate
    return ""


def _multipart_body(question, jpeg, boundary):
    marker = boundary.encode("ascii")
    chunks = [
        b"--" + marker + b"\r\n",
        b'Content-Disposition: form-data; name="question"\r\n\r\n',
        str(question).encode("utf-8"),
        b"\r\n--" + marker + b"\r\n",
        b'Content-Disposition: form-data; name="file"; filename="camera.jpg"\r\n',
        b"Content-Type: image/jpeg\r\n\r\n",
        jpeg,
        b"\r\n--" + marker + b"--\r\n",
    ]
    return b"".join(chunks)


class CyberCAMDevices:
    def __init__(self, identity, state_provider=None, verify_tls=True):
        self.identity = identity
        self.state_provider = state_provider
        self.verify_tls = verify_tls
        self._camera_lock = threading.Lock()
        self._cancel_operations = threading.Event()
        self._response_lock = threading.Lock()
        self._active_connection = None
        self._active_response = None
        self._vision_url = ""
        self._vision_token = ""

    def configure_vision(self, capability):
        capability = capability if isinstance(capability, dict) else {}
        self._vision_url = str(capability.get("url") or "").strip()
        self._vision_token = str(capability.get("token") or "").strip()

    def cancel_operations(self):
        self._cancel_operations.set()
        with self._response_lock:
            connection = self._active_connection
            response = self._active_response
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass

    def _check_cancelled(self):
        if self._cancel_operations.is_set():
            raise RuntimeError("设备操作已取消")

    def camera_available(self):
        return any(os.path.exists("/dev/video%d" % index) for index in range(5))

    def get_volume(self):
        result = subprocess.run(
            ["amixer", "sget", "PCM"],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
        return _parse_mixer_percent(result.stdout)

    def set_volume(self, volume):
        volume = int(volume)
        if not 0 <= volume <= 100:
            raise ValueError("volume 必须在 0 到 100 之间")
        subprocess.run(
            ["amixer", "sset", "PCM", "%d%%" % volume],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
        return {"volume": self.get_volume()}

    def get_brightness(self):
        maximum = int(_read_text(os.path.join(BACKLIGHT_PATH, "max_brightness"), "0"))
        current = int(_read_text(os.path.join(BACKLIGHT_PATH, "brightness"), "0"))
        return round(current * 100 / maximum) if maximum else 0

    def set_brightness(self, brightness):
        brightness = int(brightness)
        if not 0 <= brightness <= 100:
            raise ValueError("brightness 必须在 0 到 100 之间")
        maximum = int(_read_text(os.path.join(BACKLIGHT_PATH, "max_brightness"), "0"))
        if maximum <= 0:
            raise RuntimeError("设备不支持屏幕亮度控制")
        _write_text(
            os.path.join(BACKLIGHT_PATH, "brightness"),
            _percent_to_raw(brightness, maximum),
        )
        return {"brightness": self.get_brightness()}

    def get_status_led(self):
        return _read_text(os.path.join(STATUS_LED_PATH, "brightness"), "0") != "0"

    def set_status_led(self, enabled):
        if not os.path.exists(os.path.join(STATUS_LED_PATH, "brightness")):
            raise RuntimeError("设备不支持状态灯控制")
        _write_text(os.path.join(STATUS_LED_PATH, "brightness"), 1 if enabled else 0)
        return {"enabled": self.get_status_led()}

    def _network_info(self):
        interface = _active_network_interface()
        state = (
            _read_text("/sys/class/net/%s/operstate" % interface, "unknown")
            if interface
            else "unknown"
        )
        address = "127.0.0.1"
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            address = sock.getsockname()[0]
        except OSError:
            pass
        finally:
            sock.close()
        return {"interface": interface or "unknown", "state": state, "ip": address}

    def get_device_status(self):
        state = self.state_provider() if self.state_provider else None
        if is_dataclass(state):
            state = asdict(state)
        elif state is not None and not isinstance(state, dict):
            state = {"phase": str(getattr(state, "phase", "unknown"))}
        temperature = _read_text("/sys/class/thermal/thermal_zone0/temp")
        try:
            temperature_c = round(int(temperature) / 1000.0, 1)
        except (TypeError, ValueError):
            temperature_c = None
        try:
            volume = self.get_volume()
        except Exception:
            volume = None
        return {
            "audio": {"speaker_volume": volume},
            "screen": {"brightness": self.get_brightness(), "width": 640, "height": 480},
            "camera": {"available": self.camera_available()},
            "status_led": {"enabled": self.get_status_led()},
            "network": self._network_info(),
            "temperature_c": temperature_c,
            "assistant": state or {},
        }

    def get_system_info(self):
        return {
            "board": "CyberCAM K230",
            "platform": platform.platform(),
            "python": platform.python_version(),
            "device_id": self.identity.get("device_id", ""),
            "client_id": self.identity.get("client_id", ""),
            "camera_devices": [
                "/dev/video%d" % index
                for index in range(5)
                if os.path.exists("/dev/video%d" % index)
            ],
        }

    def get_screen_info(self):
        return {
            "width": 640,
            "height": 480,
            "brightness": self.get_brightness(),
            "backlight": os.path.exists(BACKLIGHT_PATH),
        }

    def _capture_jpeg(self):
        from walnutpi import Sensor, direction
        import cv2

        camera = Sensor.Sensor(640, 480)
        try:
            if not camera.isOpened():
                raise RuntimeError("无法打开摄像头")
            if direction.get_lcd() == 2:
                camera.set_hmirror(1)
            frame = None
            for _ in range(3):
                self._check_cancelled()
                ok, candidate = camera.read()
                if ok:
                    frame = candidate
            if frame is None:
                raise RuntimeError("摄像头未返回画面")
            encoded, buffer = cv2.imencode(
                ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85]
            )
            if not encoded:
                raise RuntimeError("摄像头画面编码失败")
            return buffer.tobytes()
        finally:
            camera.release()

    def take_photo(self, question):
        self._check_cancelled()
        question = str(question or "请描述这张照片").strip()
        parsed = urllib.parse.urlparse(self._vision_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise RuntimeError("服务端未下发有效的视觉分析地址")
        with self._camera_lock:
            jpeg = self._capture_jpeg()
        self._check_cancelled()
        boundary = "xiaozhi-%s" % uuid.uuid4().hex
        headers = {
            "Content-Type": "multipart/form-data; boundary=%s" % boundary,
            "Device-Id": str(self.identity.get("device_id") or ""),
            "Client-Id": str(self.identity.get("client_id") or ""),
        }
        if self._vision_token:
            headers["Authorization"] = (
                self._vision_token
                if " " in self._vision_token
                else "Bearer " + self._vision_token
            )
        body = _multipart_body(question, jpeg, boundary)
        context = ssl.create_default_context() if self.verify_tls else ssl._create_unverified_context()
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if parsed.scheme == "https":
            connection = http.client.HTTPSConnection(
                parsed.hostname, port, timeout=5, context=context
            )
        else:
            connection = http.client.HTTPConnection(parsed.hostname, port, timeout=5)
        path = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        response = None
        try:
            with self._response_lock:
                self._active_connection = connection
            self._check_cancelled()
            connection.request("POST", path, body=body, headers=headers)
            self._check_cancelled()
            if connection.sock is not None:
                connection.sock.settimeout(30)
            response = connection.getresponse()
            with self._response_lock:
                self._active_response = response
            self._check_cancelled()
            if not 200 <= response.status < 300:
                detail = response.read(200).decode("utf-8", "replace")
                raise RuntimeError(
                    "视觉服务返回 HTTP %d: %s" % (response.status, detail)
                )
            chunks = []
            size = 0
            while True:
                self._check_cancelled()
                chunk = response.read(min(65536, 1024 * 1024 + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > 1024 * 1024:
                    raise RuntimeError("视觉服务响应过大")
            return b"".join(chunks).decode("utf-8", "replace")
        except (OSError, http.client.HTTPException) as exc:
            if self._cancel_operations.is_set():
                raise RuntimeError("设备操作已取消") from exc
            raise
        finally:
            with self._response_lock:
                if self._active_response is response:
                    self._active_response = None
                if self._active_connection is connection:
                    self._active_connection = None
            if response is not None:
                response.close()
            connection.close()
