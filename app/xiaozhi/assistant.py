"""Threaded Xiaozhi assistant runtime used by the hardware UI."""

import json
import os
import queue
import socket
import threading
import time
from dataclasses import replace

from activation import APP_VERSION, DEFAULT_OTA_URL, OTAClient, ensure_identity, load_json
from audio import AudioIO, OpusCodec, PCMPreprocessor, pcm_level
from devices import CyberCAMDevices
from mcp import MCPServer
from protocol import (
    FRAME_DURATION_MS,
    INPUT_SAMPLE_RATE,
    ViewState,
    abort_message,
    authorization_value,
    continues_after_tts,
    encode_json,
    hello_message,
    listen_message,
    mcp_message,
    negotiated_output_rate,
    reduce_server_message,
)
from transport import WebSocket, WebSocketClosed
from wakeword import WakeWordEngine


DEFAULT_CONFIG = {
    "ota_url": DEFAULT_OTA_URL,
    "websocket_url": "",
    "access_token": "",
    "verify_tls": True,
    "input_device": None,
    "output_device": None,
    "max_listen_seconds": 30,
    "no_speech_timeout_seconds": 6,
    "speech_level_threshold": 0.08,
    "response_timeout_seconds": 15,
    "wake_word_enabled": True,
    "wake_word": "小智小智",
    "wake_word_device": "plughw:0,0",
    "wake_word_score": 2.0,
    "wake_word_threshold": 0.18,
}


class SpeechActivityGate:
    """Detect whether meaningful input arrived before the no-speech deadline."""

    def __init__(self, timeout_seconds, threshold, started_at=None, required_frames=3):
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.threshold = max(0.0, min(1.0, float(threshold)))
        self.started_at = time.monotonic() if started_at is None else float(started_at)
        self.required_frames = max(1, int(required_frames))
        self.heard_speech = False
        self.peak_level = 0.0
        self._speech_frames = 0

    def observe(self, level):
        level = float(level)
        self.peak_level = max(self.peak_level, level)
        if level >= self.threshold:
            self._speech_frames += 1
            if self._speech_frames >= self.required_frames:
                self.heard_speech = True
        else:
            self._speech_frames = 0

    def timed_out(self, now=None):
        now = time.monotonic() if now is None else float(now)
        return (
            not self.heard_speech
            and self._speech_frames == 0
            and now - self.started_at >= self.timeout_seconds
        )


class TTSStopGate:
    """Suppress late TTS events from an aborted request until new STT."""

    def __init__(self):
        self._lock = threading.Lock()
        self._suppress_stale_tts = False
        self._new_request_started = False

    def mark_abort(self):
        with self._lock:
            self._suppress_stale_tts = True
            self._new_request_started = False

    def mark_new_request(self):
        with self._lock:
            if self._suppress_stale_tts:
                self._new_request_started = True

    def on_stt(self):
        # STT is the first response for the newly recorded question. TTS events
        # after it belong to the new generation and must be handled normally.
        with self._lock:
            if self._suppress_stale_tts and not self._new_request_started:
                return False
            self._suppress_stale_tts = False
            self._new_request_started = False
            return True

    def should_ignore_tts(self, state):
        with self._lock:
            if not self._suppress_stale_tts:
                return False
            # A stale stop completes the aborted generation. Clear the guard so
            # a later independent server notification cannot be swallowed.
            if state == "stop":
                self._suppress_stale_tts = False
                self._new_request_started = False
            return True

    def should_ignore_audio(self):
        with self._lock:
            return self._suppress_stale_tts

    def reset(self):
        with self._lock:
            self._suppress_stale_tts = False
            self._new_request_started = False


class AssistantRuntime:
    def __init__(self, app_dir):
        self.app_dir = app_dir
        self.config = load_json(os.path.join(app_dir, "config.json"), DEFAULT_CONFIG)
        for key, value in DEFAULT_CONFIG.items():
            self.config.setdefault(key, value)
        self.identity = ensure_identity(os.path.join(app_dir, "device.json"))
        self._state = ViewState()
        self._state_lock = threading.Lock()
        self._disconnect_lock = threading.Lock()
        self._response_lock = threading.Lock()
        # UI actions are debounced before reaching this queue, while lifecycle
        # events (TTS stop, wake detection, disconnect recovery) must never be
        # dropped merely because another command is being handled.
        self._commands = queue.Queue()
        self._stop = threading.Event()
        self._record_stop = threading.Event()
        self._connection_reset_pending = threading.Event()
        self._thread = threading.Thread(target=self._run, name="xiaozhi-runtime", daemon=True)
        self._record_thread = None
        self._receive_thread = None
        self._ws = None
        self._codec = None
        self._audio = None
        self._session_id = None
        self._listening_mode = "manual"
        self._endpoint = None
        self._endpoint_source = None
        self._endpoint_refresh_required = False
        self._tts_stop_gate = TTSStopGate()
        self._response_deadline = 0.0
        self._devices, self._mcp = self._build_mcp_session()
        self._wakeword = WakeWordEngine(
            app_dir,
            self.config,
            on_ready=lambda: self.action("wake_ready"),
            on_detect=lambda text: self.action("wake:" + text),
            on_error=lambda message: self.action("wake_error:" + message),
        )
        self._thread.start()

    def snapshot(self):
        with self._state_lock:
            return self._state

    def _set_state(self, **changes):
        with self._state_lock:
            self._state = replace(self._state, **changes)

    def _apply_server(self, message):
        with self._state_lock:
            self._state = reduce_server_message(self._state, message)

    def action(self, name="toggle"):
        try:
            self._commands.put_nowait(name)
            return True
        except queue.Full:
            return False

    def _run(self):
        try:
            try:
                self._prepare_endpoint()
                if not self._stop.is_set():
                    self._set_state(
                        phase="idle",
                        title="你好，我是小智",
                        detail="按一下开始说话",
                        error="",
                    )
                    self._arm_wakeword()
            except Exception as exc:
                if not self._stop.is_set():
                    self._show_error(exc)
            while not self._stop.is_set():
                try:
                    command = self._commands.get(timeout=0.2)
                except queue.Empty:
                    self._check_response_timeout()
                    continue
                try:
                    should_exit = self._handle_command(command)
                except Exception as exc:
                    self._show_error(exc)
                    self._disconnect()
                    should_exit = False
                if should_exit:
                    return
        finally:
            self._disconnect()

    def _handle_command(self, command):
        if command == "exit":
            return True
        if command == "retry":
            self._retry_listening()
            return False
        if command == "auto_stop":
            if self.snapshot().phase == "listening":
                self._stop_listening()
            return False
        if command == "no_speech":
            if self.snapshot().phase == "listening":
                self._tts_stop_gate.mark_abort()
                try:
                    if self._ws is not None:
                        self._send_json(
                            abort_message(self._session_id, reason="no_speech")
                        )
                except Exception as exc:
                    print("[xiaozhi] 无语音取消通知发送失败:", exc)
                self._stop_listening(send_stop=False)
                self._enter_wake_idle(reset_channel=False)
            return False
        if command == "response_timeout":
            self._handle_response_timeout()
            return False
        if command == "arm_wake":
            self._enter_wake_idle(reset_channel=False)
            return False
        if command == "reset_and_arm_wake":
            self._enter_wake_idle(reset_channel=True)
            return False
        if command == "continue_listening":
            if self.snapshot().phase == "idle":
                self._start_listening(mode=self._listening_mode)
            return False
        if command == "wake_ready":
            if self.snapshot().phase == "arming":
                phrase = str(self.config.get("wake_word") or "小智小智")
                self._set_state(
                    phase="idle",
                    title="叫我“%s”" % phrase,
                    detail="唤醒后直接说出问题 · 也可以点击按钮",
                    error="",
                )
            return False
        if command.startswith("wake_error:"):
            if self.snapshot().phase == "arming":
                self._set_state(
                    phase="idle",
                    title="按钮对话仍可使用",
                    detail=command.split(":", 1)[1][:72],
                )
            return False
        if command.startswith("wake:"):
            if self.snapshot().phase not in ("idle", "arming"):
                return False
            wake_text = command.split(":", 1)[1].strip()
            self._stop_wakeword()
            self._set_state(
                phase="connecting",
                title="唤醒成功",
                detail="请直接说出你的问题",
                error="",
            )
            self._start_listening(mode="auto", wake_text=wake_text)
            return False
        if command == "toggle":
            self._toggle()
        return False

    def _prepare_endpoint(self):
        manual_url = str(self.config.get("websocket_url") or "").strip()
        manual_token = str(self.config.get("access_token") or "").strip()
        if manual_url:
            self._endpoint = (manual_url, manual_token)
            self._endpoint_source = "manual"
            self._endpoint_refresh_required = False
            return

        self._set_state(phase="connecting", title="正在连接小智", detail="正在获取设备配置", error="")
        ota = OTAClient(
            self.config.get("ota_url") or DEFAULT_OTA_URL,
            bool(self.config.get("verify_tls", True)),
        )
        response = ota.fetch(self.identity)
        activation = response.get("activation")
        if activation:
            code = str(activation.get("code") or "")
            self._set_state(
                phase="activating",
                title="需要绑定设备",
                detail="登录 xiaozhi.me 添加设备",
                activation_code=code,
                error="",
            )

            def on_wait(current_code):
                self._set_state(activation_code=current_code)

            if not ota.activate(self.identity, activation, self._stop, on_wait):
                if self._stop.is_set():
                    return
                raise RuntimeError("等待激活超时，请重新进入 App")
            response = ota.fetch(self.identity)
        url = str(response.get("websocket_url") or "").strip()
        token = str(response.get("access_token") or "").strip()
        if not url:
            raise RuntimeError("OTA 未返回 WebSocket 地址")
        self._endpoint = (url, token)
        self._endpoint_source = "ota"
        self._endpoint_refresh_required = False

    def _build_mcp_session(self):
        devices = CyberCAMDevices(
            self.identity,
            state_provider=self.snapshot,
            verify_tls=bool(self.config.get("verify_tls", True)),
        )
        return devices, MCPServer(devices, version=APP_VERSION)

    def _reset_mcp_session(self):
        previous = self._mcp
        previous.close()
        self._devices, self._mcp = self._build_mcp_session()

    def _retry_listening(self):
        self._disconnect()
        try:
            self._prepare_endpoint()
            self._start_listening()
        except Exception:
            self._disconnect()
            raise

    def _endpoint_for_connection(self):
        if self._endpoint is None or self._endpoint_refresh_required:
            self._prepare_endpoint()
        if self._endpoint_source == "ota":
            # OTA credentials may be short-lived. Reuse them only for the
            # current socket; the next actual connection fetches a fresh pair.
            self._endpoint_refresh_required = True
        return self._endpoint

    def _toggle(self):
        phase = self.snapshot().phase
        if phase in ("idle", "arming"):
            self._stop_wakeword()
        if phase == "listening":
            self._stop_listening()
            return
        if phase in ("thinking", "speaking"):
            self._tts_stop_gate.mark_abort()
            if self._ws is not None:
                self._send_json(abort_message(self._session_id))
            self._stop_listening(send_stop=False)
        if phase == "error":
            self._prepare_endpoint()
        self._start_listening(mode="manual")

    def _arm_wakeword(self):
        if not bool(self.config.get("wake_word_enabled", True)):
            return False
        if not self._wakeword.available:
            print("[wake] 资源未安装，保留按钮对话")
            return False
        warmed_up = self._wakeword.warmed_up
        self._set_state(
            phase="arming",
            title="正在恢复语音唤醒" if warmed_up else "正在准备语音唤醒",
            detail="正在打开麦克风" if warmed_up else "首次加载大约需要 5 秒",
            error="",
        )
        if not self._wakeword.start():
            self._set_state(phase="idle", title="你好，我是小智", detail="按一下开始说话")
            return False
        return True

    def _stop_wakeword(self):
        self._wakeword.stop()

    def _enter_wake_idle(self, reset_channel=False):
        self._clear_response_deadline()
        if reset_channel:
            self._disconnect()
        else:
            # Match the official lifecycle: release audio hardware while idle,
            # but retain the WebSocket/session so the next wake can start with
            # listen/start instead of another TLS + hello round trip.
            self._stop_listening(send_stop=False)
            if self._audio is not None:
                self._audio.close_output()
        self._set_state(
            phase="idle",
            title="你好，我是小智",
            detail="按一下开始说话",
            error="",
            level=0.0,
        )
        if self._ws is not None:
            print("[protocol] websocket session kept warm for next wake")
        self._arm_wakeword()
        if reset_channel:
            self._connection_reset_pending.clear()

    def _queue_connection_reset(self):
        if self._connection_reset_pending.is_set():
            return True
        self._connection_reset_pending.set()
        if self.action("reset_and_arm_wake"):
            return True
        self._connection_reset_pending.clear()
        return False

    def _connect(self):
        if self._ws is not None and self._ws.is_open:
            print("[protocol] reusing websocket session")
            return
        if self._ws is not None:
            self._disconnect()
        url, token = self._endpoint_for_connection()
        self._set_state(phase="connecting", title="正在连接", detail="正在建立安全语音通道", error="")
        headers = {
            "Protocol-Version": "1",
            "Device-Id": self.identity["device_id"],
            "Client-Id": self.identity["client_id"],
        }
        authorization = authorization_value(token)
        if authorization:
            headers["Authorization"] = authorization
        ws = WebSocket(
            url,
            headers=headers,
            timeout=10.0,
            verify_tls=bool(self.config.get("verify_tls", True)),
        ).connect()
        try:
            ws.send_text(encode_json(hello_message()))
            hello_deadline = time.monotonic() + 10.0
            while time.monotonic() < hello_deadline:
                message = ws.recv()
                if isinstance(message, str):
                    data = json.loads(message)
                    if data.get("type") == "hello":
                        output_rate = negotiated_output_rate(data)
                        self._session_id = data.get("session_id")
                        break
            else:
                raise TimeoutError("等待服务端 hello 超时")
        except Exception:
            ws.close()
            raise
        # Poll inside the transport without losing partially received frames and
        # fail a channel that has been completely silent for two minutes.
        ws.set_idle_timeout(120.0)
        self._ws = ws
        print("[protocol] websocket session ready")
        self._codec = OpusCodec(INPUT_SAMPLE_RATE, output_rate)
        self._audio = AudioIO(
            self.config.get("input_device"), self.config.get("output_device")
        )
        self._receive_thread = threading.Thread(
            target=self._receive_loop, name="xiaozhi-receiver", daemon=True
        )
        self._receive_thread.start()

    def _send_json(self, message):
        if self._ws is None:
            raise RuntimeError("语音通道未连接")
        self._ws.send_text(encode_json(message))

    def _send_mcp_response(self, ws, payload):
        if self._ws is not ws or ws is None or not ws.is_open:
            return
        try:
            ws.send_text(encode_json(mcp_message(payload, self._session_id)))
        except Exception as exc:
            if self._ws is ws and not self._stop.is_set():
                print("[mcp] 响应发送失败:", exc)

    def _set_response_deadline(self):
        timeout = max(1.0, float(self.config.get("response_timeout_seconds") or 15))
        with self._response_lock:
            self._response_deadline = time.monotonic() + timeout

    def _clear_response_deadline(self):
        with self._response_lock:
            self._response_deadline = 0.0

    def _check_response_timeout(self):
        with self._response_lock:
            expired = bool(
                self._response_deadline and time.monotonic() >= self._response_deadline
            )
        if expired and self.action("response_timeout"):
            with self._response_lock:
                self._response_deadline = 0.0

    def _on_stt(self):
        # STT is progress, not completion. Start a fresh deadline for the LLM
        # and TTS stages so a partial backend response cannot leave the UI in
        # the thinking state forever.
        if not self._tts_stop_gate.on_stt():
            return False
        self._set_response_deadline()
        return True

    def _on_tts_progress(self, state):
        if state == "stop":
            self._clear_response_deadline()
        else:
            self._set_response_deadline()

    def _handle_response_timeout(self):
        phase = self.snapshot().phase
        if phase not in ("thinking", "speaking"):
            return
        reason = "playback_timeout" if phase == "speaking" else "response_timeout"
        self._tts_stop_gate.mark_abort()
        try:
            if self._ws is not None:
                self._send_json(abort_message(self._session_id, reason=reason))
        except Exception as exc:
            print("[xiaozhi] 响应超时取消通知发送失败:", exc)
        self._enter_wake_idle(reset_channel=False)

    def _start_listening(self, mode="manual", wake_text=None):
        self._clear_response_deadline()
        self._connect()
        self._stop_listening(send_stop=False)
        if self._audio is None:
            raise RuntimeError("音频设备未初始化")
        frame_size = INPUT_SAMPLE_RATE * FRAME_DURATION_MS // 1000
        self._audio.close_output()
        self._audio.open_input(INPUT_SAMPLE_RATE, frame_size)
        self._record_stop.clear()
        self._listening_mode = mode
        if wake_text:
            self._send_json(
                listen_message("detect", self._session_id, text=wake_text)
            )
        self._tts_stop_gate.mark_new_request()
        self._send_json(listen_message("start", self._session_id, mode=mode))
        self._set_state(
            phase="listening",
            title="我在听",
            detail="说完后再按一下" if mode == "manual" else "请直接说出你的问题",
            transcript="",
            answer="",
            error="",
            level=0.0,
        )
        self._record_thread = threading.Thread(
            target=self._record_loop,
            args=(frame_size, PCMPreprocessor()),
            name="xiaozhi-recorder",
            daemon=True,
        )
        self._record_thread.start()

    def _record_loop(self, frame_size, preprocessor):
        started_at = time.monotonic()
        max_seconds = max(5, int(self.config.get("max_listen_seconds") or 30))
        activity = SpeechActivityGate(
            self.config.get("no_speech_timeout_seconds") or 6,
            self.config.get("speech_level_threshold") or 0.08,
            started_at,
        )
        try:
            while not self._record_stop.is_set() and self._ws is not None:
                data, overflowed = self._audio.input_stream.read(frame_size)
                pcm = preprocessor.process(bytes(data))
                level = pcm_level(pcm)
                activity.observe(level)
                self._set_state(level=level)
                if overflowed:
                    print("[audio] input overflow")
                self._ws.send_binary(self._codec.encode(pcm, frame_size))
                if activity.timed_out():
                    print(
                        "[audio] no speech detected in %.1fs (peak %.3f)"
                        % (activity.timeout_seconds, activity.peak_level)
                    )
                    if self.action("no_speech"):
                        break
                if time.monotonic() - started_at >= max_seconds:
                    if self.action("auto_stop"):
                        break
        except WebSocketClosed:
            if not self._record_stop.is_set() and not self._stop.is_set():
                print("[xiaozhi] 发送语音时连接已结束")
                if not self._queue_connection_reset():
                    self._show_error(RuntimeError("语音连接已断开，请重试"))
        except Exception as exc:
            if not self._record_stop.is_set() and not self._stop.is_set():
                print("[xiaozhi] 录音线程异常，正在重置:", exc)
                if not self._queue_connection_reset():
                    self._show_error(exc)

    def _stop_listening(self, send_stop=True):
        was_listening = self.snapshot().phase == "listening"
        self._record_stop.set()
        record, self._record_thread = self._record_thread, None
        if record is not None and record is not threading.current_thread():
            record.join(timeout=1.0)
        # The K230 PortAudio backend must not be closed while another thread is
        # inside RawInputStream.read(). One 60 ms frame is enough to wake it.
        if self._audio is not None:
            try:
                self._audio.close_input()
            except Exception:
                pass
        if send_stop and was_listening and self._ws is not None:
            self._send_json(listen_message("stop", self._session_id))
            self._set_response_deadline()
            self._set_state(phase="thinking", title="正在识别", detail="请稍候", level=0.0)

    def _receive_loop(self):
        # Keep this receiver tied to the socket that created it. Normal idle
        # wake-word detection may run alongside this warm connection.
        ws = self._ws
        try:
            while not self._stop.is_set() and self._ws is ws and ws is not None:
                try:
                    message = ws.recv()
                except socket.timeout:
                    continue
                if isinstance(message, bytes):
                    phase = self.snapshot().phase
                    if phase not in ("thinking", "speaking"):
                        continue
                    if self._tts_stop_gate.should_ignore_audio():
                        continue
                    self._set_response_deadline()
                    if self._audio.output_stream is None:
                        self._audio.open_output(self._codec.output_rate)
                    self._audio.write(self._codec.decode(message))
                    continue
                data = json.loads(message)
                if data.get("type") == "mcp":
                    self._mcp.submit(
                        data.get("payload"),
                        lambda payload, current=ws: self._send_mcp_response(current, payload),
                    )
                    continue
                if data.get("type") == "stt":
                    if not self._on_stt():
                        print("[xiaozhi] ignored stale stt event after abort")
                        continue
                if data.get("type") == "tts" and self._tts_stop_gate.should_ignore_tts(
                    data.get("state")
                ):
                    # Keep the new microphone session active. Binary payloads
                    # for this stale reply are dropped while phase=listening.
                    print("[xiaozhi] ignored stale tts event after abort")
                    continue
                if data.get("type") == "tts":
                    self._on_tts_progress(data.get("state"))
                if data.get("type") == "tts" and data.get("state") == "start":
                    self._stop_listening(send_stop=False)
                    if self._audio.output_stream is None:
                        self._audio.open_output(self._codec.output_rate)
                self._apply_server(data)
                if data.get("type") == "tts" and data.get("state") == "stop":
                    self._audio.close_output()
                    self.action(
                        "continue_listening"
                        if continues_after_tts(self._listening_mode)
                        else "arm_wake"
                    )
        except WebSocketClosed:
            if not self._stop.is_set() and self._ws is ws:
                print("[xiaozhi] 语音连接已结束，恢复待机唤醒")
                if not self._queue_connection_reset():
                    self._show_error(RuntimeError("语音连接已断开，请重试"))
        except Exception as exc:
            if not self._stop.is_set() and self._ws is ws:
                print("[xiaozhi] 接收线程异常，正在重置:", exc)
                if not self._queue_connection_reset():
                    self._show_error(exc)

    def _show_error(self, error):
        message = str(error).strip() or type(error).__name__
        print("[xiaozhi]", type(error).__name__, message)
        self._set_state(
            phase="error",
            title="暂时无法使用",
            detail=message[:72],
            error=message,
            level=0.0,
        )

    def _disconnect(self):
        # close() and the runtime thread can reach here together. Serialize the
        # teardown so native PortAudio/Opus handles are never freed twice.
        with self._disconnect_lock:
            self._record_stop.set()
            # Wake the blocking receiver before touching native audio handles.
            ws, self._ws = self._ws, None
            had_session = ws is not None or self._session_id is not None
            if ws is not None:
                ws.close()
            record, self._record_thread = self._record_thread, None
            receiver, self._receive_thread = self._receive_thread, None
            for thread in (record, receiver):
                if thread is not None and thread is not threading.current_thread():
                    thread.join(timeout=1.0)
            audio, self._audio = self._audio, None
            if audio is not None:
                try:
                    audio.close()
                except Exception:
                    pass
            codec, self._codec = self._codec, None
            if codec is not None:
                codec.close()
            self._session_id = None
            self._clear_response_deadline()
            self._tts_stop_gate.reset()
            if had_session and not self._stop.is_set():
                self._reset_mcp_session()

    def close(self):
        self._stop.set()
        self._wakeword.shutdown()
        self.action("exit")
        self._disconnect()
        self._mcp.close()
        if self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
