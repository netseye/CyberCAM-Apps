"""Xiaozhi MCP-over-WebSocket JSON-RPC server."""

import json
import queue
import threading


MCP_PROTOCOL_VERSION = "2024-11-05"
MAX_LIST_PAYLOAD = 8000


def _schema(properties=None, required=None):
    value = {"type": "object", "properties": properties or {}}
    if required:
        value["required"] = required
    return value


class MCPServer:
    def __init__(self, devices, server_name="cybercam-xiaozhi", version="2.1.0"):
        self.devices = devices
        self.server_name = server_name
        self.version = version
        self._jobs = queue.Queue(maxsize=16)
        self._closed = threading.Event()
        self._worker = threading.Thread(
            target=self._run,
            name="xiaozhi-mcp",
            daemon=True,
        )
        self._worker.start()
        self._tools = [
            self._tool("self.get_device_status", "获取设备当前状态", _schema(), lambda _: devices.get_device_status()),
            self._tool(
                "self.audio_speaker.set_volume",
                "设置扬声器音量，范围 0 到 100",
                _schema({"volume": {"type": "integer", "minimum": 0, "maximum": 100}}, ["volume"]),
                lambda args: devices.set_volume(args["volume"]),
            ),
            self._tool(
                "self.screen.set_brightness",
                "设置屏幕亮度，范围 0 到 100",
                _schema({"brightness": {"type": "integer", "minimum": 0, "maximum": 100}}, ["brightness"]),
                lambda args: devices.set_brightness(args["brightness"]),
            ),
            self._tool(
                "self.camera.take_photo",
                "拍摄当前画面并回答关于画面的问题",
                _schema({"question": {"type": "string", "description": "需要根据照片回答的问题"}}, ["question"]),
                lambda args: devices.take_photo(args["question"]),
            ),
            self._tool(
                "self.status_led.set_enabled",
                "打开或关闭设备绿色状态灯",
                _schema({"enabled": {"type": "boolean"}}, ["enabled"]),
                lambda args: devices.set_status_led(args["enabled"]),
            ),
            self._tool("self.get_system_info", "获取设备系统与硬件信息", _schema(), lambda _: devices.get_system_info(), True),
            self._tool("self.screen.get_info", "获取屏幕尺寸和背光信息", _schema(), lambda _: devices.get_screen_info(), True),
        ]

    @staticmethod
    def _tool(name, description, input_schema, handler, user_only=False):
        return {
            "name": name,
            "description": description,
            "inputSchema": input_schema,
            "handler": handler,
            "user_only": user_only,
        }

    @staticmethod
    def _response(request_id, result):
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id, code, message):
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }

    @staticmethod
    def _public_tool(tool):
        return {key: tool[key] for key in ("name", "description", "inputSchema")}

    def _list_tools(self, params):
        if not isinstance(params, dict):
            raise ValueError("params 必须是对象")
        include_user = params.get("withUserTools", False)
        if not isinstance(include_user, bool):
            raise ValueError("withUserTools 必须是布尔值")
        available = [tool for tool in self._tools if include_user or not tool["user_only"]]
        cursor = params.get("cursor") or ""
        if not isinstance(cursor, str):
            raise ValueError("cursor 必须是字符串")
        start = 0
        if cursor:
            start = next(
                (index for index, tool in enumerate(available) if tool["name"] == cursor),
                -1,
            )
            if start < 0:
                raise ValueError("未知 cursor: %s" % cursor)
        selected = []
        index = start
        while index < len(available):
            candidate = selected + [self._public_tool(available[index])]
            probe = {"tools": candidate}
            if index + 1 < len(available):
                probe["nextCursor"] = available[index + 1]["name"]
            if len(json.dumps(probe, ensure_ascii=False).encode("utf-8")) > MAX_LIST_PAYLOAD:
                if not selected:
                    raise ValueError("工具描述超过 MCP 列表大小限制: %s" % available[index]["name"])
                break
            selected = candidate
            index += 1
        result = {"tools": selected}
        if index < len(available):
            result["nextCursor"] = available[index]["name"]
        return result

    @staticmethod
    def _validate_arguments(tool, arguments):
        if not isinstance(arguments, dict):
            raise ValueError("arguments 必须是对象")
        schema = tool["inputSchema"]
        for name in schema.get("required", []):
            if name not in arguments:
                raise ValueError("缺少参数: %s" % name)
        for name, value in arguments.items():
            rule = schema.get("properties", {}).get(name)
            if rule is None:
                continue
            expected = rule.get("type")
            if expected == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
                raise ValueError("%s 必须是整数" % name)
            if expected == "boolean" and not isinstance(value, bool):
                raise ValueError("%s 必须是布尔值" % name)
            if expected == "string" and not isinstance(value, str):
                raise ValueError("%s 必须是字符串" % name)
            if "minimum" in rule and value < rule["minimum"]:
                raise ValueError("%s 小于允许的最小值" % name)
            if "maximum" in rule and value > rule["maximum"]:
                raise ValueError("%s 超出允许的最大值" % name)

    def handle(self, payload):
        if not isinstance(payload, dict):
            return self._error(None, -32600, "Invalid Request")
        request_id = payload.get("id")
        if payload.get("jsonrpc") != "2.0" or not isinstance(payload.get("method"), str):
            return self._error(request_id, -32600, "Invalid Request")
        method = payload["method"]
        # A JSON-RPC notification is defined by the absence of an id. Its
        # method name is unrestricted and it must never receive a response.
        if "id" not in payload:
            return None
        params = payload.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return self._error(request_id, -32602, "params 必须是对象")
        try:
            if method == "initialize":
                capabilities = params.get("capabilities") if isinstance(params, dict) else {}
                capabilities = capabilities if isinstance(capabilities, dict) else {}
                vision = capabilities.get("vision")
                vision = vision if isinstance(vision, dict) else {}
                self.devices.configure_vision(vision)
                print("[mcp] initialized (vision=%s)" % bool(vision and vision.get("url")))
                return self._response(
                    request_id,
                    {
                        "protocolVersion": MCP_PROTOCOL_VERSION,
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": self.server_name, "version": self.version},
                    },
                )
            if method == "tools/list":
                result = self._list_tools(params)
                print("[mcp] listed %d tools" % len(result["tools"]))
                return self._response(request_id, result)
            if method == "tools/call":
                if not isinstance(params, dict):
                    raise ValueError("params 必须是对象")
                name = params.get("name")
                tool = next((item for item in self._tools if item["name"] == name), None)
                if tool is None:
                    return self._error(request_id, -32601, "Unknown tool: %s" % name)
                print("[mcp] calling %s" % name)
                arguments = params.get("arguments", {})
                if arguments is None:
                    arguments = {}
                self._validate_arguments(tool, arguments)
                try:
                    value = tool["handler"](arguments)
                    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                    return self._response(
                        request_id,
                        {"content": [{"type": "text", "text": text}], "isError": False},
                    )
                except Exception as exc:
                    return self._response(
                        request_id,
                        {
                            "content": [{"type": "text", "text": str(exc) or type(exc).__name__}],
                            "isError": True,
                        },
                    )
            return self._error(request_id, -32601, "Method not found")
        except (KeyError, TypeError, ValueError) as exc:
            return self._error(request_id, -32602, str(exc) or "Invalid params")

    def _run(self):
        while not self._closed.is_set():
            try:
                job = self._jobs.get(timeout=0.2)
            except queue.Empty:
                continue
            if job is None:
                return
            payload, send_response = job
            try:
                response = self.handle(payload)
                if response is not None and not self._closed.is_set():
                    send_response(response)
            except Exception as exc:
                print("[mcp]", type(exc).__name__, exc)

    def submit(self, payload, send_response):
        if self._closed.is_set():
            return False
        try:
            self._jobs.put_nowait((payload, send_response))
            return True
        except queue.Full:
            if isinstance(payload, dict) and "id" in payload:
                send_response(
                    self._error(payload.get("id"), -32000, "MCP request queue is full")
                )
            return False

    def close(self):
        if self._closed.is_set():
            return
        self._closed.set()
        cancel = getattr(self.devices, "cancel_operations", None)
        if callable(cancel):
            cancel()
        try:
            while True:
                self._jobs.get_nowait()
        except queue.Empty:
            pass
        try:
            self._jobs.put_nowait(None)
        except queue.Full:
            pass
        if self._worker is not threading.current_thread():
            self._worker.join(timeout=1.0)
