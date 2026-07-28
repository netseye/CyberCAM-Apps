import json
import unittest

from protocol import (
    ViewState,
    authorization_value,
    button_label,
    encode_json,
    hello_message,
    continues_after_tts,
    listen_message,
    map_touch_coordinates,
    mcp_message,
    negotiated_output_rate,
    primary_action_enabled,
    reduce_server_message,
    touch_action,
)


class ProtocolTests(unittest.TestCase):
    def test_hello_announces_60ms_opus(self):
        hello = hello_message()
        self.assertEqual(hello["transport"], "websocket")
        self.assertEqual(hello["audio_params"]["sample_rate"], 16000)
        self.assertEqual(hello["audio_params"]["frame_duration"], 60)
        self.assertTrue(hello["features"]["mcp"])
        self.assertEqual(json.loads(encode_json(hello)), hello)

    def test_mcp_message_uses_session_envelope(self):
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        self.assertEqual(
            mcp_message(payload, "session-1"),
            {"type": "mcp", "session_id": "session-1", "payload": payload},
        )

    def test_manual_listen_messages(self):
        self.assertEqual(
            listen_message("start", "session-1"),
            {
                "type": "listen",
                "session_id": "session-1",
                "state": "start",
                "mode": "manual",
            },
        )
        self.assertEqual(listen_message("start", "session-1", mode="auto")["mode"], "auto")
        self.assertNotIn("mode", listen_message("stop", "session-1"))

    def test_wake_word_detect_message(self):
        self.assertEqual(
            listen_message("detect", "session-1", text="小智小智"),
            {
                "type": "listen",
                "session_id": "session-1",
                "state": "detect",
                "text": "小智小智",
            },
        )

    def test_authorization_header(self):
        self.assertEqual(authorization_value("token"), "Bearer token")
        self.assertEqual(authorization_value("Bearer token"), "Bearer token")
        self.assertIsNone(authorization_value(""))

    def test_follow_up_mode(self):
        self.assertTrue(continues_after_tts("auto"))
        self.assertTrue(continues_after_tts("realtime"))
        self.assertFalse(continues_after_tts("manual"))

    def test_server_message_state_flow(self):
        state = ViewState(phase="listening")
        state = reduce_server_message(state, {"type": "stt", "text": "今天天气怎么样"})
        self.assertEqual(state.phase, "thinking")
        self.assertEqual(state.transcript, "今天天气怎么样")
        state = reduce_server_message(state, {"type": "tts", "state": "start"})
        self.assertEqual(state.phase, "speaking")
        state = reduce_server_message(
            state, {"type": "tts", "state": "sentence_start", "text": "今天晴朗"}
        )
        self.assertEqual(state.answer, "今天晴朗")
        state = reduce_server_message(state, {"type": "tts", "state": "stop"})
        self.assertEqual(state.phase, "idle")

    def test_server_output_rate_validation(self):
        self.assertEqual(
            negotiated_output_rate(
                {"type": "hello", "transport": "websocket", "audio_params": {"sample_rate": 24000}}
            ),
            24000,
        )
        with self.assertRaises(ValueError):
            negotiated_output_rate(
                {"type": "hello", "transport": "websocket", "audio_params": {"sample_rate": 44100}}
            )
        with self.assertRaisesRegex(ValueError, "audio format"):
            negotiated_output_rate(
                {
                    "type": "hello",
                    "transport": "websocket",
                    "audio_params": {"format": "pcm", "sample_rate": 24000},
                }
            )
        with self.assertRaisesRegex(ValueError, "mono"):
            negotiated_output_rate(
                {
                    "type": "hello",
                    "transport": "websocket",
                    "audio_params": {"channels": 2, "sample_rate": 24000},
                }
            )

    def test_controls(self):
        self.assertEqual(touch_action(20, 20), "exit")
        self.assertEqual(touch_action(320, 430), "toggle")
        self.assertIsNone(touch_action(100, 200))
        self.assertIsNone(touch_action(700, 200))
        self.assertEqual(button_label("listening"), "说完了")
        self.assertEqual(button_label("error"), "重试")

    def test_primary_action_is_disabled_while_starting_or_connecting(self):
        for phase in ("starting", "connecting", "activating"):
            self.assertFalse(primary_action_enabled(phase))
        for phase in ("arming", "idle", "listening", "thinking", "speaking", "error"):
            self.assertTrue(primary_action_enabled(phase))

    def test_portrait_touch_axes_are_rotated_to_landscape_ui(self):
        self.assertEqual(
            map_touch_coordinates(463, 58, (0, 479), (0, 639)),
            (58, 16),
        )
        self.assertEqual(
            map_touch_coordinates(4, 389, (0, 479), (0, 639)),
            (389, 475),
        )

    def test_flipped_touch_matches_rotated_display(self):
        self.assertEqual(
            map_touch_coordinates(463, 58, (0, 479), (0, 639), flipped=True),
            (581, 463),
        )


if __name__ == "__main__":
    unittest.main()
