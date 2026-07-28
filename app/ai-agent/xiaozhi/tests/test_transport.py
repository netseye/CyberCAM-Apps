import base64
import hashlib
import struct
import unittest
from unittest import mock

import transport


class FakeSocket:
    def __init__(self, incoming=b""):
        self.incoming = bytearray(incoming)
        self.sent = bytearray()
        self.timeout = None

    def settimeout(self, timeout):
        self.timeout = timeout

    def recv(self, size):
        data = self.incoming[:size]
        del self.incoming[:size]
        return bytes(data)

    def sendall(self, data):
        self.sent.extend(data)

    def shutdown(self, how):
        pass

    def close(self):
        pass


class TimeoutSocket:
    def __init__(self):
        self.sent = bytearray()

    def settimeout(self, timeout):
        self.timeout = timeout

    def recv(self, size):
        raise transport.socket.timeout()

    def sendall(self, data):
        self.sent.extend(data)

    def shutdown(self, how):
        pass

    def close(self):
        pass


class FailedSendSocket(FakeSocket):
    def sendall(self, data):
        raise BrokenPipeError("network down")


def server_frame(opcode, payload, fin=True):
    first = (0x80 if fin else 0) | opcode
    if len(payload) < 126:
        return struct.pack("!BB", first, len(payload)) + payload
    return struct.pack("!BBH", first, 126, len(payload)) + payload


def masked_server_frame(opcode, payload):
    mask = b"mask"
    encoded = bytes(value ^ mask[index & 3] for index, value in enumerate(payload))
    return struct.pack("!BB", 0x80 | opcode, 0x80 | len(payload)) + mask + encoded


class TransportTests(unittest.TestCase):
    @staticmethod
    def handshake_response(include_upgrade=True):
        key = base64.b64encode(b"x" * 16).decode("ascii")
        accept = base64.b64encode(
            hashlib.sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
            ).digest()
        ).decode("ascii")
        headers = [
            "HTTP/1.1 101 Switching Protocols",
            "Sec-WebSocket-Accept: " + accept,
        ]
        if include_upgrade:
            headers.extend(["Upgrade: websocket", "Connection: keep-alive, Upgrade"])
        return ("\r\n".join(headers) + "\r\n\r\n").encode("ascii")

    def test_handshake_requires_upgrade_headers(self):
        socket = FakeSocket(self.handshake_response(include_upgrade=False))
        ws = transport.WebSocket("ws://example.invalid")
        with (
            mock.patch.object(transport.os, "urandom", return_value=b"x" * 16),
            mock.patch.object(transport.socket, "create_connection", return_value=socket),
        ):
            with self.assertRaisesRegex(transport.WebSocketError, "Upgrade"):
                ws.connect()

    def test_valid_handshake_connects(self):
        socket = FakeSocket(self.handshake_response())
        ws = transport.WebSocket("ws://example.invalid")
        with (
            mock.patch.object(transport.os, "urandom", return_value=b"x" * 16),
            mock.patch.object(transport.socket, "create_connection", return_value=socket),
        ):
            self.assertIs(ws.connect(), ws)
        self.assertTrue(ws.is_open)

    def test_receive_text_and_binary(self):
        socket = FakeSocket(server_frame(1, "你好".encode()) + server_frame(2, b"opus"))
        ws = transport.WebSocket("ws://example.invalid")
        ws.sock = socket
        self.assertEqual(ws.recv(), "你好")
        self.assertEqual(ws.recv(), b"opus")

    def test_fragmented_message_with_ping(self):
        incoming = (
            server_frame(1, b"hel", fin=False)
            + server_frame(9, b"p")
            + server_frame(0, b"lo", fin=True)
        )
        socket = FakeSocket(incoming)
        ws = transport.WebSocket("ws://example.invalid")
        ws.sock = socket
        self.assertEqual(ws.recv(), "hello")
        self.assertTrue(socket.sent)

    def test_client_frames_are_masked(self):
        socket = FakeSocket()
        ws = transport.WebSocket("ws://example.invalid")
        ws.sock = socket
        ws.send_binary(b"abc")
        self.assertEqual(socket.sent[0] & 0x0F, 2)
        self.assertTrue(socket.sent[1] & 0x80)

    def test_send_failure_closes_connection(self):
        ws = transport.WebSocket("ws://example.invalid")
        ws.sock = FailedSendSocket()
        with self.assertRaises(transport.WebSocketClosed):
            ws.send_binary(b"abc")
        self.assertFalse(ws.is_open)
        self.assertIsNone(ws.sock)

    def test_open_state_tracks_socket_lifecycle(self):
        socket = FakeSocket()
        ws = transport.WebSocket("ws://example.invalid")
        self.assertFalse(ws.is_open)
        ws.sock = socket
        self.assertTrue(ws.is_open)
        ws.close()
        self.assertFalse(ws.is_open)

    def test_oversized_frame_is_rejected(self):
        socket = FakeSocket(struct.pack("!BBQ", 0x82, 127, 1000))
        ws = transport.WebSocket("ws://example.invalid", max_payload=100)
        ws.sock = socket
        with self.assertRaises(transport.WebSocketError):
            ws.recv()

    def test_fragmented_message_cannot_exceed_maximum_payload(self):
        incoming = (
            server_frame(2, b"1234", fin=False)
            + server_frame(0, b"5678", fin=True)
        )
        ws = transport.WebSocket("ws://example.invalid", max_payload=7)
        ws.sock = FakeSocket(incoming)
        with self.assertRaisesRegex(transport.WebSocketError, "message exceeds"):
            ws.recv()

    def test_idle_connection_times_out_without_losing_frame_state(self):
        ws = transport.WebSocket("ws://example.invalid")
        ws.sock = TimeoutSocket()
        with mock.patch.object(transport.time, "monotonic", side_effect=[0.0, 2.0]):
            ws._last_incoming = transport.time.monotonic()
            ws.set_idle_timeout(1.0)
            with self.assertRaises(transport.WebSocketClosed):
                ws.recv()

    def test_handshake_timeout_is_not_swallowed_before_idle_mode(self):
        ws = transport.WebSocket("ws://example.invalid")
        ws.sock = TimeoutSocket()
        with self.assertRaises(transport.socket.timeout):
            ws.recv()

    def test_idle_connection_sends_masked_keepalive_ping(self):
        socket = TimeoutSocket()
        ws = transport.WebSocket("ws://example.invalid")
        ws.sock = socket
        ws.set_idle_timeout(120, ping_interval=30)
        ws._last_incoming = 0
        ws._last_ping = 0
        with mock.patch.object(
            transport.time, "monotonic", side_effect=[31, 121]
        ):
            with self.assertRaises(transport.WebSocketClosed):
                ws.recv()
        self.assertTrue(socket.sent)
        self.assertEqual(socket.sent[0] & 0x0F, 0x9)
        self.assertTrue(socket.sent[1] & 0x80)

    def test_masked_server_frame_is_rejected(self):
        ws = transport.WebSocket("ws://example.invalid")
        ws.sock = FakeSocket(masked_server_frame(1, b"text"))
        with self.assertRaisesRegex(transport.WebSocketError, "must not be masked"):
            ws.recv()

    def test_fragmented_control_frame_is_rejected(self):
        ws = transport.WebSocket("ws://example.invalid")
        ws.sock = FakeSocket(server_frame(9, b"ping", fin=False))
        with self.assertRaisesRegex(transport.WebSocketError, "control frame"):
            ws.recv()

    def test_remote_close_releases_socket(self):
        socket = FakeSocket(server_frame(8, b""))
        ws = transport.WebSocket("ws://example.invalid")
        ws.sock = socket
        with self.assertRaises(transport.WebSocketClosed):
            ws.recv()
        self.assertIsNone(ws.sock)


if __name__ == "__main__":
    unittest.main()
