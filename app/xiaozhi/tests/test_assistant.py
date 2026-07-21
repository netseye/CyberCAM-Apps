import queue
import threading
import unittest
from unittest import mock

from assistant import AssistantRuntime, SpeechActivityGate, TTSStopGate


class SpeechActivityGateTests(unittest.TestCase):
    def test_silence_times_out(self):
        gate = SpeechActivityGate(6, 0.08, started_at=10)
        gate.observe(0.03)
        self.assertFalse(gate.timed_out(15.9))
        self.assertTrue(gate.timed_out(16.0))

    def test_speech_prevents_silence_timeout(self):
        gate = SpeechActivityGate(6, 0.08, started_at=10)
        gate.observe(0.1)
        gate.observe(0.1)
        gate.observe(0.1)
        self.assertFalse(gate.timed_out(30))

    def test_single_noise_spike_does_not_count_as_speech(self):
        gate = SpeechActivityGate(6, 0.08, started_at=10)
        gate.observe(0.2)
        gate.observe(0.01)
        self.assertTrue(gate.timed_out(16))

    def test_candidate_speech_at_deadline_gets_confirmation_window(self):
        gate = SpeechActivityGate(6, 0.08, started_at=0)
        gate.observe(0.2)
        self.assertFalse(gate.timed_out(6.0))
        gate.observe(0.2)
        self.assertFalse(gate.timed_out(6.1))
        gate.observe(0.2)
        self.assertTrue(gate.heard_speech)


class TTSStopGateTests(unittest.TestCase):
    def test_old_reply_events_after_abort_are_ignored(self):
        gate = TTSStopGate()
        gate.mark_abort()
        self.assertTrue(gate.should_ignore_tts("start"))
        self.assertTrue(gate.should_ignore_tts("sentence_start"))
        self.assertTrue(gate.should_ignore_tts("stop"))
        self.assertFalse(gate.should_ignore_tts("stop"))

    def test_new_stt_cancels_stale_tts_guard(self):
        gate = TTSStopGate()
        gate.mark_abort()
        gate.mark_new_request()
        self.assertTrue(gate.on_stt())
        self.assertFalse(gate.should_ignore_tts("start"))
        self.assertFalse(gate.should_ignore_tts("stop"))

    def test_stt_from_cancelled_request_is_ignored_without_new_recording(self):
        gate = TTSStopGate()
        gate.mark_abort()
        self.assertFalse(gate.on_stt())
        self.assertTrue(gate.should_ignore_audio())

    def test_reset_clears_guard(self):
        gate = TTSStopGate()
        gate.mark_abort()
        gate.reset()
        self.assertFalse(gate.should_ignore_tts("stop"))

    def test_binary_audio_is_suppressed_until_stale_tts_stops(self):
        gate = TTSStopGate()
        gate.mark_abort()
        self.assertTrue(gate.should_ignore_audio())
        self.assertTrue(gate.should_ignore_tts("stop"))
        self.assertFalse(gate.should_ignore_audio())


class ConnectionLifecycleTests(unittest.TestCase):
    def runtime(self):
        runtime = AssistantRuntime.__new__(AssistantRuntime)
        runtime._disconnect = mock.Mock()
        runtime._stop_listening = mock.Mock()
        runtime._set_state = mock.Mock()
        runtime._arm_wakeword = mock.Mock()
        runtime._audio = mock.Mock()
        runtime._ws = mock.Mock()
        runtime._connection_reset_pending = threading.Event()
        runtime._response_lock = threading.Lock()
        runtime._response_deadline = 0.0
        runtime._tts_stop_gate = mock.Mock()
        runtime._stop = threading.Event()
        runtime._disconnect.side_effect = lambda: setattr(runtime, "_ws", None)
        return runtime

    def test_normal_wake_idle_keeps_websocket(self):
        runtime = self.runtime()
        runtime._enter_wake_idle(reset_channel=False)
        runtime._disconnect.assert_not_called()
        runtime._stop_listening.assert_called_once_with(send_stop=False)
        runtime._audio.close_output.assert_called_once_with()
        runtime._arm_wakeword.assert_called_once_with()

    def test_closed_channel_is_reset_before_wake_idle(self):
        runtime = self.runtime()
        runtime._enter_wake_idle(reset_channel=True)
        runtime._disconnect.assert_called_once_with()
        runtime._stop_listening.assert_not_called()
        runtime._arm_wakeword.assert_called_once_with()

    def test_duplicate_connection_resets_are_coalesced(self):
        runtime = self.runtime()
        runtime.action = mock.Mock(return_value=True)
        self.assertTrue(runtime._queue_connection_reset())
        self.assertTrue(runtime._queue_connection_reset())
        runtime.action.assert_called_once_with("reset_and_arm_wake")

    def test_response_timeout_is_retried_if_command_queue_is_full(self):
        runtime = self.runtime()
        runtime._response_deadline = 1.0
        runtime.action = mock.Mock(side_effect=[False, True])
        with mock.patch("assistant.time.monotonic", return_value=2.0):
            runtime._check_response_timeout()
            self.assertEqual(runtime._response_deadline, 1.0)
            runtime._check_response_timeout()
        self.assertEqual(runtime._response_deadline, 0.0)

    def test_stt_restarts_deadline_while_waiting_for_tts(self):
        runtime = self.runtime()
        runtime._set_response_deadline = mock.Mock()
        runtime._tts_stop_gate.on_stt.return_value = True
        runtime._on_stt()
        runtime._set_response_deadline.assert_called_once_with()
        runtime._tts_stop_gate.on_stt.assert_called_once_with()

    def test_tts_progress_keeps_deadline_until_stop(self):
        runtime = self.runtime()
        runtime._set_response_deadline = mock.Mock()
        runtime._clear_response_deadline = mock.Mock()
        runtime._on_tts_progress("start")
        runtime._on_tts_progress("sentence_start")
        self.assertEqual(runtime._set_response_deadline.call_count, 2)
        runtime._clear_response_deadline.assert_not_called()
        runtime._on_tts_progress("stop")
        runtime._clear_response_deadline.assert_called_once_with()

    def test_playback_timeout_aborts_and_returns_to_wake_idle(self):
        runtime = self.runtime()
        runtime.snapshot = mock.Mock(return_value=mock.Mock(phase="speaking"))
        runtime._send_json = mock.Mock()
        runtime._tts_stop_gate.mark_new_request = mock.Mock()
        runtime._enter_wake_idle = mock.Mock()
        runtime._session_id = "session-1"
        runtime._handle_response_timeout()
        runtime._tts_stop_gate.mark_abort.assert_called_once_with()
        self.assertEqual(runtime._send_json.call_args.args[0]["reason"], "playback_timeout")
        runtime._enter_wake_idle.assert_called_once_with(reset_channel=False)

    def test_retry_failure_disconnects_partial_connection(self):
        runtime = self.runtime()
        runtime._disconnect = mock.Mock()
        runtime._prepare_endpoint = mock.Mock()
        runtime._start_listening = mock.Mock(side_effect=RuntimeError("microphone busy"))
        with self.assertRaisesRegex(RuntimeError, "microphone busy"):
            runtime._retry_listening()
        self.assertEqual(runtime._disconnect.call_count, 2)

    def test_disconnect_replaces_mcp_session_after_live_connection(self):
        runtime = self.runtime()
        runtime._disconnect_lock = threading.Lock()
        runtime._record_stop = threading.Event()
        runtime._ws = mock.Mock()
        runtime._record_thread = None
        runtime._receive_thread = None
        runtime._audio = None
        runtime._codec = None
        runtime._session_id = "session-1"
        runtime._clear_response_deadline = mock.Mock()
        runtime._reset_mcp_session = mock.Mock()
        AssistantRuntime._disconnect(runtime)
        runtime._reset_mcp_session.assert_called_once_with()

    def test_startup_failure_keeps_retry_command_loop_alive(self):
        runtime = self.runtime()
        runtime._commands = queue.Queue()
        runtime._commands.put("retry")
        runtime._commands.put("exit")
        runtime._prepare_endpoint = mock.Mock(side_effect=RuntimeError("offline"))
        runtime._retry_listening = mock.Mock()
        runtime._show_error = mock.Mock()
        runtime._disconnect = mock.Mock()
        runtime._run()
        runtime._show_error.assert_called_once()
        runtime._retry_listening.assert_called_once_with()
        runtime._disconnect.assert_called_once_with()

    def test_stale_wake_command_is_ignored_while_active(self):
        runtime = self.runtime()
        runtime.snapshot = mock.Mock(return_value=mock.Mock(phase="speaking"))
        runtime._stop_wakeword = mock.Mock()
        runtime._start_listening = mock.Mock()
        self.assertFalse(runtime._handle_command("wake:小智小智"))
        runtime._stop_wakeword.assert_not_called()
        runtime._start_listening.assert_not_called()

    def test_recording_error_queues_connection_reset(self):
        runtime = self.runtime()
        runtime.config = {
            "max_listen_seconds": 30,
            "no_speech_timeout_seconds": 6,
            "speech_level_threshold": 0.08,
        }
        runtime._record_stop = threading.Event()
        runtime._audio.input_stream.read.side_effect = RuntimeError("microphone failed")
        runtime._queue_connection_reset = mock.Mock(return_value=True)
        runtime._show_error = mock.Mock()
        runtime._record_loop(960, mock.Mock())
        runtime._queue_connection_reset.assert_called_once_with()
        runtime._show_error.assert_not_called()

    def test_receiver_error_queues_connection_reset(self):
        runtime = self.runtime()
        runtime._ws.recv.return_value = "not-json"
        runtime._queue_connection_reset = mock.Mock(return_value=True)
        runtime._show_error = mock.Mock()
        runtime._receive_loop()
        runtime._queue_connection_reset.assert_called_once_with()
        runtime._show_error.assert_not_called()

    @mock.patch("assistant.threading.Thread")
    def test_wake_start_sends_detect_before_auto_listen(self, thread_class):
        runtime = self.runtime()
        runtime._clear_response_deadline = mock.Mock()
        runtime._connect = mock.Mock()
        runtime._audio.close_output = mock.Mock()
        runtime._audio.open_input = mock.Mock()
        runtime._record_stop = threading.Event()
        runtime._session_id = "session-1"
        runtime._send_json = mock.Mock()
        runtime.config = {"input_device": None, "output_device": None}
        runtime._start_listening(mode="auto", wake_text="小智小智")
        sent = [call.args[0] for call in runtime._send_json.call_args_list]
        self.assertEqual(sent[0]["state"], "detect")
        self.assertEqual(sent[0]["text"], "小智小智")
        self.assertEqual(sent[1]["state"], "start")
        self.assertEqual(sent[1]["mode"], "auto")
        runtime._tts_stop_gate.mark_new_request.assert_called_once_with()
        thread_class.return_value.start.assert_called_once_with()


class EndpointRefreshTests(unittest.TestCase):
    def runtime(self):
        runtime = AssistantRuntime.__new__(AssistantRuntime)
        runtime._endpoint = ("wss://old", "old-token")
        runtime._endpoint_source = "ota"
        runtime._endpoint_refresh_required = True
        runtime._prepare_endpoint = mock.Mock(
            side_effect=lambda: (
                setattr(runtime, "_endpoint", ("wss://fresh", "fresh-token")),
                setattr(runtime, "_endpoint_source", "ota"),
                setattr(runtime, "_endpoint_refresh_required", False),
            )
        )
        return runtime

    def test_ota_endpoint_is_refreshed_for_each_new_socket(self):
        runtime = self.runtime()
        self.assertEqual(
            runtime._endpoint_for_connection(),
            ("wss://fresh", "fresh-token"),
        )
        runtime._prepare_endpoint.assert_called_once_with()
        self.assertTrue(runtime._endpoint_refresh_required)

    def test_manual_endpoint_is_reused_without_refresh(self):
        runtime = self.runtime()
        runtime._endpoint = ("wss://manual", "manual-token")
        runtime._endpoint_source = "manual"
        runtime._endpoint_refresh_required = False
        self.assertEqual(
            runtime._endpoint_for_connection(),
            ("wss://manual", "manual-token"),
        )
        runtime._prepare_endpoint.assert_not_called()

if __name__ == "__main__":
    unittest.main()
