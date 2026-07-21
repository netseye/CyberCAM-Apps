"""Local sherpa-onnx keyword spotter process for CyberCAM K230."""

import os
import re
import signal
import subprocess
import sys
import threading


READY_MARKER = "Recording started!"
IGNORED_AFTER_READY = ("Use recording device:", "Current sample rate:")
KEYWORD_PATTERN = re.compile(r'"keyword"\s*:\s*"([^"]+)"')


def _config_float(config, name, default):
    value = config.get(name)
    if value is None or value == "":
        value = default
    return float(value)


def is_detection_line(line, ready=False):
    line = str(line or "").strip()
    if not ready or not line:
        return False
    if line == READY_MARKER or line.startswith(IGNORED_AFTER_READY):
        return False
    return bool(KEYWORD_PATTERN.search(line))


def detected_keyword(line):
    match = KEYWORD_PATTERN.search(str(line or ""))
    return match.group(1).strip() if match else ""


class WakeWordEngine:
    def __init__(self, app_dir, config, on_ready, on_detect, on_error):
        self.app_dir = os.path.abspath(app_dir)
        self.config = config
        self.on_ready = on_ready
        self.on_detect = on_detect
        self.on_error = on_error
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._process = None
        self._thread = None
        self._model_ready = False
        self._want_listening = False
        self._process_starting = False

    @property
    def persistent_available(self):
        binary = os.path.join(
            self.app_dir, "wake", "native", "wakeword-daemon"
        )
        return os.path.isfile(binary) and os.access(binary, os.X_OK)

    @property
    def warmed_up(self):
        with self._lock:
            return self._model_ready

    @property
    def available(self):
        guard = os.path.join(self.app_dir, "wake", "parent_guard.py")
        fallback = self._required_paths()[0]
        executable_available = self.persistent_available or (
            os.path.isfile(fallback) and os.access(fallback, os.X_OK)
        )
        return (
            os.path.isfile(guard)
            and executable_available
            and all(os.path.isfile(path) for path in self._asset_paths())
        )

    def _asset_paths(self):
        wake_dir = os.path.join(self.app_dir, "wake")
        runtime = os.path.join(wake_dir, "runtime-spacemit", "lib")
        model = os.path.join(wake_dir, "model")
        return (
            os.path.join(model, "tokens.txt"),
            os.path.join(model, "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
            os.path.join(model, "decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
            os.path.join(model, "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
            os.path.join(wake_dir, "keywords.txt"),
            os.path.join(runtime, "libsherpa-onnx-c-api.so"),
            os.path.join(runtime, "libonnxruntime.so.1"),
            os.path.join(runtime, "libonnxruntime_providers_shared.so"),
            os.path.join(runtime, "libspacemit_ep.so.2"),
        )

    def _required_paths(self):
        wake_dir = os.path.join(self.app_dir, "wake")
        fallback = os.path.join(
            wake_dir, "runtime-spacemit", "bin", "sherpa-onnx-keyword-spotter-alsa"
        )
        tokens, encoder, decoder, joiner, keywords = self._asset_paths()[:5]
        return fallback, tokens, encoder, decoder, joiner, keywords

    def command(self):
        binary, tokens, encoder, decoder, joiner, keywords = self._required_paths()
        device = str(self.config.get("wake_word_device") or "plughw:0,0")
        score = _config_float(self.config, "wake_word_score", 3.5)
        threshold = _config_float(self.config, "wake_word_threshold", 0.1)
        return [
            binary,
            "--print-args=false",
            "--tokens=" + tokens,
            "--encoder=" + encoder,
            "--decoder=" + decoder,
            "--joiner=" + joiner,
            "--model-type=zipformer2",
            "--provider=cpu",
            "--num-threads=1",
            "--keywords-score=" + str(score),
            "--keywords-threshold=" + str(threshold),
            "--keywords-file=" + keywords,
            device,
        ]

    def daemon_command(self):
        _, tokens, encoder, decoder, joiner, keywords = self._required_paths()
        binary = os.path.join(
            self.app_dir, "wake", "native", "wakeword-daemon"
        )
        device = str(self.config.get("wake_word_device") or "plughw:0,0")
        score = _config_float(self.config, "wake_word_score", 3.5)
        threshold = _config_float(self.config, "wake_word_threshold", 0.1)
        return [
            binary,
            tokens,
            encoder,
            decoder,
            joiner,
            keywords,
            device,
            str(score),
            str(threshold),
        ]

    def guarded_command(self, command):
        guard = os.path.join(self.app_dir, "wake", "parent_guard.py")
        return [sys.executable, guard, str(os.getpid())] + list(command)

    def start(self):
        if not self.available:
            return False
        if self.persistent_available:
            return self._start_persistent()
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return True
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, name="xiaozhi-wakeword", daemon=True
            )
            self._thread.start()
        return True

    def _start_persistent(self):
        with self._lock:
            self._want_listening = True
            process = self._process
            ready = self._model_ready
            if process is None or process.poll() is not None:
                if self._process_starting:
                    return True
                self._process_starting = True
                self._stop.clear()
                self._paused.set()
                self._thread = threading.Thread(
                    target=self._run_persistent,
                    name="xiaozhi-wakeword-daemon",
                    daemon=True,
                )
                self._thread.start()
                return True
        if ready:
            self._resume_process(process)
        return True

    def _resume_process(self, process):
        if process is None or process.poll() is not None:
            return
        self._paused.clear()
        try:
            os.kill(process.pid, signal.SIGUSR2)
        except ProcessLookupError:
            pass

    def _run_persistent(self):
        runtime_lib = os.path.join(self.app_dir, "wake", "runtime-spacemit", "lib")
        environment = os.environ.copy()
        environment["LD_LIBRARY_PATH"] = runtime_lib
        try:
            process = subprocess.Popen(
                self.guarded_command(self.daemon_command()),
                cwd=os.path.join(self.app_dir, "wake", "model"),
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            with self._lock:
                self._process = process
            for raw_line in process.stdout:
                line = raw_line.strip()
                if line:
                    print("[wake]", line)
                if line == "MODEL_READY":
                    with self._lock:
                        self._model_ready = True
                        wanted = self._want_listening
                    self._paused.set()
                    if wanted:
                        self._resume_process(process)
                elif line == "LISTENING":
                    with self._lock:
                        wanted = self._want_listening
                    if wanted:
                        self.on_ready()
                elif line == "PAUSED":
                    self._paused.set()
                elif line.startswith("DETECTED\t"):
                    keyword = line.split("\t", 1)[1].strip()
                    with self._lock:
                        wanted = self._want_listening
                        self._want_listening = False
                    self._paused.set()
                    if wanted:
                        self.on_detect(keyword)
                elif line.startswith("ERROR\t"):
                    message = line.split("\t", 1)[1].strip()
                    with self._lock:
                        wanted = self._want_listening
                        self._want_listening = False
                    self._paused.set()
                    if wanted:
                        self.on_error(message)
                if self._stop.is_set():
                    break
            if not self._stop.is_set():
                self.on_error("唤醒服务意外退出")
        except Exception as exc:
            if not self._stop.is_set():
                self.on_error(str(exc) or type(exc).__name__)
        finally:
            self._terminate_process()
            with self._lock:
                self._process = None
                self._model_ready = False
                self._process_starting = False
            self._paused.set()

    def _run(self):
        runtime_lib = os.path.join(self.app_dir, "wake", "runtime-spacemit", "lib")
        environment = os.environ.copy()
        environment["LD_LIBRARY_PATH"] = runtime_lib
        ready = False
        detected = False
        try:
            process = subprocess.Popen(
                self.guarded_command(self.command()),
                cwd=os.path.join(self.app_dir, "wake", "model"),
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                start_new_session=True,
            )
            with self._lock:
                self._process = process
            for raw_line in process.stdout:
                line = raw_line.strip()
                if line:
                    print("[wake]", line)
                if line == READY_MARKER:
                    ready = True
                    self.on_ready()
                    continue
                if is_detection_line(line, ready):
                    detected = True
                    keyword = detected_keyword(line)
                    print("[wake] detected:", keyword)
                    self.on_detect(keyword)
                    break
                if self._stop.is_set():
                    break
            if not self._stop.is_set() and not detected:
                status = process.poll()
                message = "唤醒引擎已停止" if status in (0, None) else "唤醒引擎退出: %s" % status
                self.on_error(message)
        except Exception as exc:
            if not self._stop.is_set():
                self.on_error(str(exc) or type(exc).__name__)
        finally:
            self._terminate_process()
            with self._lock:
                self._process = None

    def _terminate_process(self):
        with self._lock:
            process = self._process
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1.0)

    def stop(self):
        if self.persistent_available:
            with self._lock:
                self._want_listening = False
                process = self._process
            if process is not None and process.poll() is None:
                self._paused.clear()
                try:
                    os.kill(process.pid, signal.SIGUSR1)
                except ProcessLookupError:
                    self._paused.set()
                self._paused.wait(timeout=1.5)
            return
        self._stop.set()
        self._terminate_process()
        with self._lock:
            thread = self._thread
            self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3.0)

    def shutdown(self):
        if not self.persistent_available:
            self.stop()
            return
        self._stop.set()
        with self._lock:
            self._want_listening = False
            thread = self._thread
        self._terminate_process()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3.0)
