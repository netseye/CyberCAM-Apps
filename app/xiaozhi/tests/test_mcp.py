import json
import unittest
from unittest import mock

import mcp
from mcp import MCP_PROTOCOL_VERSION, MCPServer


class FakeDevices:
    def __init__(self):
        self.vision = None
        self.volume = None
        self.question = None
        self.cancelled = False

    def configure_vision(self, value):
        self.vision = value

    def get_device_status(self):
        return {"camera": {"available": True}}

    def set_volume(self, value):
        self.volume = value
        return {"volume": value}

    def set_brightness(self, value):
        return {"brightness": value}

    def take_photo(self, question):
        self.question = question
        return "照片里有一只猫"

    def set_status_led(self, enabled):
        return {"enabled": enabled}

    def get_system_info(self):
        return {"board": "CyberCAM K230"}

    def get_screen_info(self):
        return {"width": 640, "height": 480}

    def cancel_operations(self):
        self.cancelled = True


def request(method, params=None, request_id=1):
    value = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        value["params"] = params
    return value


class MCPServerTests(unittest.TestCase):
    def setUp(self):
        self.devices = FakeDevices()
        self.server = MCPServer(self.devices)

    def tearDown(self):
        self.server.close()

    def test_initialize_negotiates_protocol_and_vision(self):
        response = self.server.handle(
            request(
                "initialize",
                {"capabilities": {"vision": {"url": "https://vision", "token": "secret"}}},
            )
        )
        self.assertEqual(response["result"]["protocolVersion"], MCP_PROTOCOL_VERSION)
        self.assertEqual(response["result"]["capabilities"], {"tools": {}})
        self.assertEqual(self.devices.vision["url"], "https://vision")

    def test_notification_has_no_response(self):
        self.assertIsNone(
            self.server.handle(
                {"jsonrpc": "2.0", "method": "notifications/initialized"}
            )
        )

    def test_custom_method_without_id_is_also_a_notification(self):
        self.assertIsNone(
            self.server.handle(
                {"jsonrpc": "2.0", "method": "custom/device_event", "params": {}}
            )
        )

    def test_worker_is_daemon_and_close_cancels_device_operations(self):
        self.assertTrue(self.server._worker.daemon)
        self.server.close()
        self.assertTrue(self.devices.cancelled)

    def test_default_tool_list_hides_user_only_tools(self):
        response = self.server.handle(request("tools/list", {}))
        names = [tool["name"] for tool in response["result"]["tools"]]
        self.assertIn("self.camera.take_photo", names)
        self.assertIn("self.audio_speaker.set_volume", names)
        self.assertNotIn("self.get_system_info", names)
        self.assertTrue(all("handler" not in tool for tool in response["result"]["tools"]))

    def test_user_tool_list_can_be_requested(self):
        response = self.server.handle(request("tools/list", {"withUserTools": True}))
        names = [tool["name"] for tool in response["result"]["tools"]]
        self.assertIn("self.get_system_info", names)
        self.assertIn("self.screen.get_info", names)

    def test_tool_list_uses_official_name_cursor(self):
        with mock.patch.object(mcp, "MAX_LIST_PAYLOAD", 700):
            first = self.server.handle(request("tools/list", {}))["result"]
            self.assertIn("nextCursor", first)
            second = self.server.handle(
                request("tools/list", {"cursor": first["nextCursor"]})
            )["result"]
        self.assertEqual(second["tools"][0]["name"], first["nextCursor"])

    def test_tool_list_rejects_invalid_cursor_and_user_flag(self):
        response = self.server.handle(request("tools/list", {"cursor": 2}))
        self.assertEqual(response["error"]["code"], -32602)
        response = self.server.handle(
            request("tools/list", {"withUserTools": "false"})
        )
        self.assertEqual(response["error"]["code"], -32602)

    def test_tool_call_returns_mcp_text_content(self):
        response = self.server.handle(
            request(
                "tools/call",
                {"name": "self.audio_speaker.set_volume", "arguments": {"volume": 42}},
            )
        )
        self.assertFalse(response["result"]["isError"])
        self.assertEqual(json.loads(response["result"]["content"][0]["text"]), {"volume": 42})
        self.assertEqual(self.devices.volume, 42)

    def test_camera_question_is_forwarded(self):
        response = self.server.handle(
            request(
                "tools/call",
                {"name": "self.camera.take_photo", "arguments": {"question": "看到了什么"}},
            )
        )
        self.assertEqual(self.devices.question, "看到了什么")
        self.assertEqual(response["result"]["content"][0]["text"], "照片里有一只猫")

    def test_invalid_argument_is_json_rpc_error(self):
        response = self.server.handle(
            request(
                "tools/call",
                {"name": "self.audio_speaker.set_volume", "arguments": {"volume": 101}},
            )
        )
        self.assertEqual(response["error"]["code"], -32602)

    def test_non_object_params_and_arguments_are_rejected(self):
        response = self.server.handle(request("tools/list", []))
        self.assertEqual(response["error"]["code"], -32602)
        response = self.server.handle(
            request(
                "tools/call",
                {"name": "self.get_device_status", "arguments": []},
            )
        )
        self.assertEqual(response["error"]["code"], -32602)


if __name__ == "__main__":
    unittest.main()
