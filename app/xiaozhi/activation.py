"""Device identity, OTA discovery and Xiaozhi v2 activation."""

import hashlib
import hmac
import json
import os
import socket
import ssl
import tempfile
import time
import urllib.error
import urllib.request
import uuid


APP_VERSION = "2.0.8"
DEFAULT_OTA_URL = "https://api.tenclass.net/xiaozhi/ota/"
MAX_JSON_RESPONSE = 1024 * 1024


def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {} if default is None else dict(default)


def save_json_atomic(path, value):
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".%s." % os.path.basename(path), dir=directory)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def primary_mac():
    for interface in ("wlan0", "eth0", "end0"):
        try:
            with open("/sys/class/net/%s/address" % interface, "r", encoding="ascii") as handle:
                value = handle.read().strip().lower()
            if value and value != "00:00:00:00:00:00":
                return value
        except OSError:
            pass
    node = uuid.getnode()
    return ":".join("%02x" % ((node >> shift) & 0xFF) for shift in range(40, -1, -8))


def local_ip():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def ensure_identity(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                identity = json.load(handle)
        except (OSError, ValueError) as exc:
            raise RuntimeError("设备身份文件损坏，请从备份恢复 device.json") from exc
        if not isinstance(identity, dict):
            raise RuntimeError("设备身份文件必须是 JSON 对象")
        try:
            os.chmod(path, 0o600)
        except OSError as exc:
            raise RuntimeError("无法保护设备身份文件权限") from exc
    else:
        identity = {}
    mac = primary_mac()
    changed = False
    if not identity.get("device_id"):
        identity["device_id"] = mac
        changed = True
    if not identity.get("client_id"):
        identity["client_id"] = str(uuid.uuid4())
        changed = True
    if changed:
        save_json_atomic(path, identity)
    return identity


def activation_version(identity):
    version = str(identity.get("activation_version") or "1")
    if version not in ("1", "2"):
        raise RuntimeError("不支持的激活协议版本: %s" % version)
    if version == "2" and not (
        identity.get("serial_number") and identity.get("hmac_key")
    ):
        raise RuntimeError("激活 v2 需要预置 serial_number 和 hmac_key")
    return version


def _hmac_key_bytes(value):
    value = str(value)
    if len(value) % 2 == 0:
        try:
            return bytes.fromhex(value)
        except ValueError:
            pass
    return value.encode("utf-8")


def build_ota_request(identity):
    version = activation_version(identity)
    headers = {
        "Device-Id": identity["device_id"],
        "Client-Id": identity["client_id"],
        "Content-Type": "application/json",
        "User-Agent": "cybercam-k230/xiaozhi-%s" % APP_VERSION,
        "Accept-Language": "zh-CN",
        "Activation-Version": version,
    }
    if version == "2":
        headers["Serial-Number"] = str(identity["serial_number"])
    application = {"version": APP_VERSION}
    if identity.get("elf_sha256"):
        application["elf_sha256"] = str(identity["elf_sha256"])
    payload = {
        "application": application,
        "board": {
            "type": "cybercam-k230",
            "name": "cybercam-xiaozhi",
            "ip": local_ip(),
            "mac": identity["device_id"],
        },
    }
    return headers, payload


def _ssl_context(verify_tls):
    if verify_tls:
        return ssl.create_default_context()
    return ssl._create_unverified_context()


def post_json(url, headers, payload, timeout=10, verify_tls=True):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request, timeout=timeout, context=_ssl_context(verify_tls)
        ) as response:
            body = response.read(MAX_JSON_RESPONSE + 1)
            if len(body) > MAX_JSON_RESPONSE:
                raise RuntimeError("OTA 服务响应过大")
            return response.status, json.loads(body.decode("utf-8")) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read(MAX_JSON_RESPONSE + 1)
        if len(body) > MAX_JSON_RESPONSE:
            body = body[:MAX_JSON_RESPONSE]
        try:
            data = json.loads(body.decode("utf-8")) if body else {}
        except ValueError:
            data = {"message": body.decode("utf-8", "replace")[:200]}
        return exc.code, data


class OTAClient:
    def __init__(self, ota_url=DEFAULT_OTA_URL, verify_tls=True):
        self.ota_url = ota_url.rstrip("/") + "/"
        self.verify_tls = verify_tls

    def fetch(self, identity):
        headers, payload = build_ota_request(identity)
        status, data = post_json(
            self.ota_url, headers, payload, verify_tls=self.verify_tls
        )
        if status != 200:
            raise RuntimeError("OTA 服务返回 HTTP %d" % status)
        websocket = data.get("websocket") or {}
        return {
            "websocket_url": websocket.get("url") or "",
            "access_token": websocket.get("token") or "",
            "activation": data.get("activation"),
            "raw": data,
        }

    def activate(self, identity, activation, stop_event=None, on_wait=None, max_wait=300):
        challenge = str((activation or {}).get("challenge") or "")
        code = str((activation or {}).get("code") or "")
        if not challenge or not code:
            raise RuntimeError("激活响应缺少 challenge/code")
        version = activation_version(identity)
        payload = {}
        if version == "2":
            signature = hmac.new(
                _hmac_key_bytes(identity["hmac_key"]),
                challenge.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            payload = {
                "algorithm": "hmac-sha256",
                "serial_number": identity["serial_number"],
                "challenge": challenge,
                "hmac": signature,
            }
        headers = {
            "Activation-Version": version,
            "Device-Id": identity["device_id"],
            "Client-Id": identity["client_id"],
            "Content-Type": "application/json",
        }
        if version == "2":
            headers["Serial-Number"] = str(identity["serial_number"])
        deadline = time.monotonic() + max_wait
        while time.monotonic() < deadline:
            if stop_event is not None and stop_event.is_set():
                return False
            if on_wait:
                on_wait(code)
            status, _ = post_json(
                self.ota_url.rstrip("/") + "/activate",
                headers,
                payload,
                verify_tls=self.verify_tls,
            )
            if status == 200:
                return True
            if status != 202:
                raise RuntimeError("激活服务返回 HTTP %d" % status)
            if stop_event is not None:
                stop_event.wait(3.0)
            else:
                time.sleep(3.0)
        return False
