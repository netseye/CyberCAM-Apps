"""Small RFC 6455 client used to avoid a runtime pip dependency on CyberCAM."""

import base64
import hashlib
import os
import socket
import ssl
import struct
import threading
import time
from urllib.parse import urlsplit


class WebSocketError(RuntimeError):
    pass


class WebSocketClosed(WebSocketError):
    pass


class WebSocket:
    def __init__(self, url, headers=None, timeout=10.0, verify_tls=True, max_payload=4 * 1024 * 1024):
        self.url = url
        self.headers = dict(headers or {})
        self.timeout = float(timeout)
        self.verify_tls = bool(verify_tls)
        self.max_payload = int(max_payload)
        self.sock = None
        self._send_lock = threading.Lock()
        self._closed = False
        self._recv_buffer = bytearray()
        self._idle_timeout = None
        self._ping_interval = None
        self._last_ping = time.monotonic()
        self._last_incoming = time.monotonic()

    @property
    def is_open(self):
        return self.sock is not None and not self._closed

    def connect(self):
        if self.sock is not None:
            self.close()
        parsed = urlsplit(self.url)
        if parsed.scheme not in ("ws", "wss") or not parsed.hostname:
            raise WebSocketError("invalid WebSocket URL")
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        raw = socket.create_connection((parsed.hostname, port), self.timeout)
        try:
            if parsed.scheme == "wss":
                context = ssl.create_default_context()
                if not self.verify_tls:
                    context.check_hostname = False
                    context.verify_mode = ssl.CERT_NONE
                raw = context.wrap_socket(raw, server_hostname=parsed.hostname)
            raw.settimeout(self.timeout)

            key = base64.b64encode(os.urandom(16)).decode("ascii")
            default_port = 443 if parsed.scheme == "wss" else 80
            host = parsed.hostname if port == default_port else "%s:%d" % (parsed.hostname, port)
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
            request_headers = {
                "Host": host,
                "Upgrade": "websocket",
                "Connection": "Upgrade",
                "Sec-WebSocket-Key": key,
                "Sec-WebSocket-Version": "13",
            }
            request_headers.update(self.headers)
            request = ["GET %s HTTP/1.1" % path]
            request.extend("%s: %s" % item for item in request_headers.items())
            raw.sendall(("\r\n".join(request) + "\r\n\r\n").encode("utf-8"))

            response = bytearray()
            while b"\r\n\r\n" not in response:
                chunk = raw.recv(4096)
                if not chunk:
                    raise WebSocketError("server closed during handshake")
                response.extend(chunk)
                if len(response) > 65536:
                    raise WebSocketError("oversized handshake response")
            header_blob, trailing = bytes(response).split(b"\r\n\r\n", 1)
            lines = header_blob.decode("iso-8859-1").split("\r\n")
            status = lines[0].split(" ", 2)
            if len(status) < 2 or status[1] != "101":
                raise WebSocketError("handshake rejected: " + lines[0])
            response_headers = {}
            for line in lines[1:]:
                if ":" in line:
                    name, value = line.split(":", 1)
                    response_headers[name.strip().lower()] = value.strip()
            expected = base64.b64encode(
                hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
            ).decode("ascii")
            if response_headers.get("sec-websocket-accept") != expected:
                raise WebSocketError("invalid Sec-WebSocket-Accept")
            if response_headers.get("upgrade", "").lower() != "websocket":
                raise WebSocketError("missing WebSocket Upgrade response")
            connection_tokens = {
                item.strip().lower()
                for item in response_headers.get("connection", "").split(",")
            }
            if "upgrade" not in connection_tokens:
                raise WebSocketError("missing Connection: Upgrade response")
            if "sec-websocket-extensions" in response_headers:
                raise WebSocketError("server selected an unrequested WebSocket extension")
        except Exception:
            raw.close()
            raise
        self.sock = raw
        self._recv_buffer = bytearray(trailing)
        self._closed = False
        self._last_ping = time.monotonic()
        self._last_incoming = time.monotonic()
        return self

    def settimeout(self, timeout):
        if self.sock is not None:
            self.sock.settimeout(timeout)

    def set_idle_timeout(self, timeout, poll_interval=5.0, ping_interval=30.0):
        self._idle_timeout = max(1.0, float(timeout))
        self._ping_interval = max(1.0, min(float(ping_interval), self._idle_timeout / 2.0))
        if self.sock is not None:
            self.sock.settimeout(min(float(poll_interval), self._idle_timeout))

    def _send_frame(self, opcode, payload=b""):
        payload = bytes(payload)
        first = 0x80 | (opcode & 0x0F)
        length = len(payload)
        if length < 126:
            header = struct.pack("!BB", first, 0x80 | length)
        elif length <= 0xFFFF:
            header = struct.pack("!BBH", first, 0x80 | 126, length)
        else:
            header = struct.pack("!BBQ", first, 0x80 | 127, length)
        mask = os.urandom(4)
        masked = bytes(value ^ mask[index & 3] for index, value in enumerate(payload))
        with self._send_lock:
            sock = self.sock
            if sock is None or self._closed:
                raise WebSocketClosed("not connected")
            try:
                sock.sendall(header + mask + masked)
            except OSError as exc:
                # A failed send is terminal for an RFC 6455 connection. Mark
                # it closed so callers do not reuse a half-dead session and so
                # the assistant takes its normal disconnect recovery path.
                self._closed = True
                self._dispose_socket()
                raise WebSocketClosed("connection failed while sending") from exc

    def _read_exact(self, size):
        # Preserve bytes read ahead during the HTTP upgrade or a previous frame.
        # Production frame reads are blocking; the timeout is only used for the
        # opening handshake.
        while len(self._recv_buffer) < size:
            sock = self.sock
            if sock is None or self._closed:
                raise WebSocketClosed("not connected")
            try:
                data = sock.recv(max(4096, size - len(self._recv_buffer)))
            except socket.timeout:
                if self._idle_timeout is None:
                    raise
                now = time.monotonic()
                if now - self._last_incoming >= self._idle_timeout:
                    raise WebSocketClosed("receive timeout")
                if now - self._last_ping >= self._ping_interval:
                    self._send_frame(0x9)
                    self._last_ping = now
                continue
            if not data:
                raise WebSocketClosed("connection closed")
            self._recv_buffer.extend(data)
            self._last_incoming = time.monotonic()
        result = bytes(self._recv_buffer[:size])
        del self._recv_buffer[:size]
        return result

    def send_text(self, text):
        self._send_frame(0x1, str(text).encode("utf-8"))

    def send_binary(self, data):
        self._send_frame(0x2, data)

    def _recv_frame(self):
        first, second = struct.unpack("!BB", self._read_exact(2))
        if first & 0x70:
            raise WebSocketError("unsupported WebSocket RSV bits")
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        if masked:
            raise WebSocketError("server frames must not be masked")
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._read_exact(8))[0]
        if length > self.max_payload:
            raise WebSocketError("frame exceeds maximum payload")
        if opcode & 0x08:
            if not fin:
                raise WebSocketError("fragmented WebSocket control frame")
            if length > 125:
                raise WebSocketError("oversized WebSocket control frame")
        payload = self._read_exact(length)
        return fin, opcode, payload

    def _dispose_socket(self):
        sock, self.sock = self.sock, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

    def recv(self):
        fragments = []
        message_opcode = None
        message_size = 0
        while True:
            fin, opcode, payload = self._recv_frame()
            if opcode == 0x8:
                if len(payload) == 1:
                    raise WebSocketError("invalid WebSocket close payload")
                if not self._closed:
                    try:
                        self._send_frame(0x8, payload[:125])
                    except Exception:
                        pass
                self._closed = True
                self._dispose_socket()
                raise WebSocketClosed("server closed connection")
            if opcode == 0x9:
                self._send_frame(0xA, payload[:125])
                continue
            if opcode == 0xA:
                continue
            if opcode in (0x1, 0x2):
                if message_opcode is not None:
                    raise WebSocketError("new message before fragmented message completed")
                message_opcode = opcode
                fragments = [payload]
                message_size = len(payload)
            elif opcode == 0x0:
                if message_opcode is None:
                    raise WebSocketError("unexpected continuation frame")
                message_size += len(payload)
                if message_size > self.max_payload:
                    raise WebSocketError("message exceeds maximum payload")
                fragments.append(payload)
            else:
                raise WebSocketError("unsupported opcode: %d" % opcode)
            if fin:
                data = b"".join(fragments)
                return data.decode("utf-8") if message_opcode == 0x1 else data

    def close(self):
        if not self._closed:
            try:
                self._send_frame(0x8, struct.pack("!H", 1000))
            except Exception:
                pass
        self._closed = True
        self._dispose_socket()
