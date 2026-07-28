import unittest
from unittest import mock

from devices import (
    CyberCAMDevices,
    _active_network_interface,
    _default_route_interface,
    _multipart_body,
    _parse_mixer_percent,
    _percent_to_raw,
)


class FakeSocket:
    def __init__(self):
        self.timeout = None

    def settimeout(self, value):
        self.timeout = value


class FakeResponse:
    status = 200

    def __init__(self, body=b'{"success":true}'):
        self.body = body
        self.closed = False

    def read(self, size):
        chunk, self.body = self.body[:size], self.body[size:]
        return chunk

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, response=None):
        self.response = response or FakeResponse()
        self.sock = FakeSocket()
        self.request_args = None
        self.closed = False

    def request(self, *args, **kwargs):
        self.request_args = (args, kwargs)

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


class DeviceHelperTests(unittest.TestCase):
    def test_mixer_percent_is_parsed(self):
        self.assertEqual(_parse_mixer_percent("Mono: Playback 33 [73%] [on]"), 73)

    def test_backlight_percent_is_scaled_and_clamped(self):
        self.assertEqual(_percent_to_raw(50, 10), 5)
        self.assertEqual(_percent_to_raw(120, 10), 10)
        self.assertEqual(_percent_to_raw(-1, 10), 0)

    def test_default_route_prefers_lowest_metric_active_interface(self):
        routes = (
            "Iface Destination Gateway Flags RefCnt Use Metric Mask\n"
            "wlan0 00000000 01010101 0003 0 0 600 00000000\n"
            "eth0 00000000 01010101 0003 0 0 100 00000000\n"
            "down0 00000000 01010101 0000 0 0 1 00000000\n"
        )
        with mock.patch("builtins.open", mock.mock_open(read_data=routes)):
            self.assertEqual(_default_route_interface(), "eth0")

    def test_active_interface_falls_back_to_an_up_link(self):
        def state(path, default=""):
            return "up" if "/eth0/" in path else "down"

        with (
            mock.patch("devices._default_route_interface", return_value=""),
            mock.patch("devices._read_text", side_effect=state),
        ):
            self.assertEqual(_active_network_interface(), "eth0")

    def test_down_default_route_falls_back_to_an_up_link(self):
        def state(path, default=""):
            return "up" if "/eth0/" in path else "down"

        with (
            mock.patch("devices._default_route_interface", return_value="wlan0"),
            mock.patch("devices.os.path.exists", return_value=True),
            mock.patch("devices._read_text", side_effect=state),
        ):
            self.assertEqual(_active_network_interface(), "eth0")

    def test_camera_multipart_matches_vision_api_fields(self):
        body = _multipart_body("画面里有什么", b"jpeg-data", "boundary")
        self.assertIn(b'name="question"', body)
        self.assertIn("画面里有什么".encode("utf-8"), body)
        self.assertIn(b'name="file"; filename="camera.jpg"', body)
        self.assertIn(b"Content-Type: image/jpeg", body)
        self.assertIn(b"jpeg-data", body)

    def test_camera_http_connection_is_tracked_and_closed(self):
        devices = CyberCAMDevices({"device_id": "dev", "client_id": "client"})
        devices.configure_vision(
            {"url": "http://vision.example/explain?q=1", "token": "Bearer token"}
        )
        connection = FakeConnection()
        with (
            mock.patch.object(devices, "_capture_jpeg", return_value=b"jpeg"),
            mock.patch("devices.http.client.HTTPConnection", return_value=connection),
        ):
            result = devices.take_photo("看到了什么")
        self.assertEqual(result, '{"success":true}')
        args, kwargs = connection.request_args
        self.assertEqual(args[:2], ("POST", "/explain?q=1"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer token")
        self.assertTrue(connection.closed)
        self.assertTrue(connection.response.closed)

    def test_cancel_closes_active_response_and_connection(self):
        devices = CyberCAMDevices({})
        connection = FakeConnection()
        response = FakeResponse()
        devices._active_connection = connection
        devices._active_response = response
        devices.cancel_operations()
        self.assertTrue(connection.closed)
        self.assertTrue(response.closed)


if __name__ == "__main__":
    unittest.main()
