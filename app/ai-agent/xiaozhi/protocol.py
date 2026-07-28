"""Pure helpers for the Xiaozhi WebSocket protocol.

This module deliberately has no CyberCAM or audio dependencies so the protocol
state machine can be covered by ordinary unit tests on a development machine.
"""

import json
from dataclasses import dataclass, replace


PROTOCOL_VERSION = 1
INPUT_SAMPLE_RATE = 16000
DEFAULT_OUTPUT_SAMPLE_RATE = 24000
FRAME_DURATION_MS = 60


def hello_message():
    return {
        "type": "hello",
        "version": PROTOCOL_VERSION,
        "features": {"mcp": True},
        "transport": "websocket",
        "audio_params": {
            "format": "opus",
            "sample_rate": INPUT_SAMPLE_RATE,
            "channels": 1,
            "frame_duration": FRAME_DURATION_MS,
        },
    }


def session_message(kind, session_id=None, **fields):
    message = {"type": kind}
    if session_id:
        message["session_id"] = session_id
    message.update(fields)
    return message


def listen_message(state, session_id=None, mode="manual", text=None):
    fields = {"state": state}
    if state == "start":
        fields["mode"] = mode
    elif state == "detect" and text:
        fields["text"] = str(text)
    return session_message("listen", session_id, **fields)


def abort_message(session_id=None, reason="user_interruption"):
    return session_message("abort", session_id, reason=reason)


def mcp_message(payload, session_id=None):
    return session_message("mcp", session_id, payload=payload)


def authorization_value(token):
    token = str(token or "").strip()
    if not token:
        return None
    return token if " " in token else "Bearer " + token


def continues_after_tts(mode):
    return mode in ("auto", "realtime")


def encode_json(message):
    return json.dumps(message, ensure_ascii=False, separators=(",", ":"))


def negotiated_output_rate(server_hello):
    if server_hello.get("type") != "hello":
        raise ValueError("not a server hello")
    if server_hello.get("transport") != "websocket":
        raise ValueError("unsupported transport")
    params = server_hello.get("audio_params") or {}
    if not isinstance(params, dict):
        raise ValueError("invalid audio_params")
    if str(params.get("format") or "opus").lower() != "opus":
        raise ValueError("unsupported audio format")
    try:
        channels = int(params.get("channels") or 1)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid audio channels") from exc
    if channels != 1:
        raise ValueError("only mono Opus audio is supported")
    rate = int(params.get("sample_rate") or DEFAULT_OUTPUT_SAMPLE_RATE)
    if rate not in (8000, 12000, 16000, 24000, 48000):
        raise ValueError("unsupported Opus sample rate: %s" % rate)
    return rate


@dataclass(frozen=True)
class ViewState:
    phase: str = "starting"
    title: str = "正在启动"
    detail: str = "正在准备音频与网络"
    transcript: str = ""
    answer: str = ""
    emotion: str = "neutral"
    activation_code: str = ""
    error: str = ""
    level: float = 0.0


def reduce_server_message(state, message):
    """Apply a server JSON message to the immutable UI state."""
    kind = message.get("type")
    if kind == "stt":
        user_text = str(message.get("text") or "").strip()
        return replace(
            state,
            phase="thinking",
            title="正在思考",
            detail="小智正在组织回答",
            transcript=user_text,
            error="",
        )
    if kind == "llm":
        return replace(
            state,
            emotion=str(message.get("emotion") or "neutral"),
        )
    if kind == "tts":
        tts_state = message.get("state")
        if tts_state == "start":
            return replace(state, phase="speaking", title="小智正在说", detail="轻触可打断")
        if tts_state == "sentence_start":
            answer = str(message.get("text") or "").strip()
            return replace(
                state,
                phase="speaking",
                title="小智正在说",
                detail="轻触可打断",
                answer=answer or state.answer,
            )
        if tts_state == "stop":
            return replace(state, phase="idle", title="可以继续问我", detail="按一下开始说话", level=0.0)
    if kind == "alert":
        title = str(message.get("status") or "提示")
        detail = str(message.get("message") or "收到服务端提示")
        return replace(state, phase="error", title=title, detail=detail, error=detail)
    return state


def button_label(phase):
    if phase == "listening":
        return "说完了"
    if phase in ("thinking", "speaking"):
        return "打断并提问"
    if phase == "activating":
        return "等待激活"
    if phase == "connecting":
        return "正在连接"
    if phase == "error":
        return "重试"
    return "开始说话"


def primary_action_enabled(phase):
    return phase not in ("starting", "connecting", "activating")


def map_touch_coordinates(raw_x, raw_y, x_range, y_range, flipped=False):
    """Map the portrait touch controller axes onto the landscape UI."""
    min_x, max_x = x_range
    min_y, max_y = y_range
    # The K230 panel reports physical portrait coordinates (480 x 640), while
    # Display.show() presents the application canvas as landscape (640 x 480).
    # Display's normal orientation is a clockwise rotation, hence the swapped
    # axes and inverted physical X axis below.
    x = int((raw_y - min_y) * 639 / max(1, max_y - min_y))
    y = int((max_x - raw_x) * 479 / max(1, max_x - min_x))
    x = max(0, min(639, x))
    y = max(0, min(479, y))
    if flipped:
        x, y = 639 - x, 479 - y
    return x, y


def touch_action(x, y):
    if 0 <= x <= 88 and 0 <= y <= 80:
        return "exit"
    # Keep a forgiving margin around the visible 132,389 -> 508,449 button,
    # without turning unrelated screen areas into an accidental recording tap.
    if 108 <= x <= 532 and 365 <= y < 480:
        return "toggle"
    return None
