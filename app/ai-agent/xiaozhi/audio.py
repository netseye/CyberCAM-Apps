"""Thin libopus and sounddevice adapters for the K230 Linux image."""

import ctypes
import ctypes.util
import math
import threading
from array import array


OPUS_APPLICATION_VOIP = 2048


class PCMPreprocessor:
    """Stateful DC-blocking filter for the CyberCAM analog microphone."""

    def __init__(self, pole=0.98):
        self.pole = float(pole)
        self._previous_input = 0.0
        self._previous_output = 0.0

    def process(self, pcm_bytes):
        samples = array("h")
        samples.frombytes(pcm_bytes)
        previous_input = self._previous_input
        previous_output = self._previous_output
        for index, value in enumerate(samples):
            filtered = float(value) - previous_input + self.pole * previous_output
            previous_input = float(value)
            previous_output = filtered
            samples[index] = max(-32768, min(32767, int(round(filtered))))
        self._previous_input = previous_input
        self._previous_output = previous_output
        return samples.tobytes()


def pcm_level(pcm_bytes):
    if not pcm_bytes:
        return 0.0
    samples = (ctypes.c_int16 * (len(pcm_bytes) // 2)).from_buffer_copy(pcm_bytes)
    if not samples:
        return 0.0
    square = sum(float(value) * value for value in samples) / len(samples)
    rms = math.sqrt(square) / 32768.0
    return min(1.0, max(0.0, rms * 5.0))


class OpusCodec:
    def __init__(self, input_rate=16000, output_rate=24000, channels=1):
        library = ctypes.util.find_library("opus") or "libopus.so.0"
        self.lib = ctypes.CDLL(library)
        self.input_rate = int(input_rate)
        self.output_rate = int(output_rate)
        self.channels = int(channels)
        self.encoder = None
        self.decoder = None
        self._configure()
        error = ctypes.c_int()
        self.encoder = self.lib.opus_encoder_create(
            self.input_rate, self.channels, OPUS_APPLICATION_VOIP, ctypes.byref(error)
        )
        if not self.encoder or error.value != 0:
            raise RuntimeError("Opus encoder create failed: %d" % error.value)
        self.decoder = self.lib.opus_decoder_create(
            self.output_rate, self.channels, ctypes.byref(error)
        )
        if not self.decoder or error.value != 0:
            self.close()
            raise RuntimeError("Opus decoder create failed: %d" % error.value)

    def _configure(self):
        self.lib.opus_encoder_create.argtypes = [
            ctypes.c_int32, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)
        ]
        self.lib.opus_encoder_create.restype = ctypes.c_void_p
        self.lib.opus_encoder_destroy.argtypes = [ctypes.c_void_p]
        self.lib.opus_encode.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int16),
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.c_int32,
        ]
        self.lib.opus_encode.restype = ctypes.c_int32
        self.lib.opus_decoder_create.argtypes = [
            ctypes.c_int32, ctypes.c_int, ctypes.POINTER(ctypes.c_int)
        ]
        self.lib.opus_decoder_create.restype = ctypes.c_void_p
        self.lib.opus_decoder_destroy.argtypes = [ctypes.c_void_p]
        self.lib.opus_decode.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.c_int32,
            ctypes.POINTER(ctypes.c_int16),
            ctypes.c_int,
            ctypes.c_int,
        ]
        self.lib.opus_decode.restype = ctypes.c_int

    def encode(self, pcm_bytes, frame_size):
        expected = int(frame_size) * self.channels * 2
        if len(pcm_bytes) != expected:
            raise ValueError("expected %d PCM bytes, got %d" % (expected, len(pcm_bytes)))
        pcm = (ctypes.c_int16 * (len(pcm_bytes) // 2)).from_buffer_copy(pcm_bytes)
        output = (ctypes.c_ubyte * 4000)()
        size = self.lib.opus_encode(self.encoder, pcm, int(frame_size), output, len(output))
        if size < 0:
            raise RuntimeError("Opus encode failed: %d" % size)
        return bytes(output[:size])

    def decode(self, packet):
        encoded = (ctypes.c_ubyte * len(packet)).from_buffer_copy(packet)
        max_samples = self.output_rate * 120 // 1000
        pcm = (ctypes.c_int16 * (max_samples * self.channels))()
        samples = self.lib.opus_decode(
            self.decoder, encoded, len(packet), pcm, max_samples, 0
        )
        if samples < 0:
            raise RuntimeError("Opus decode failed: %d" % samples)
        return bytes(memoryview(pcm).cast("B")[: samples * self.channels * 2])

    def close(self):
        if self.encoder:
            self.lib.opus_encoder_destroy(self.encoder)
            self.encoder = None
        if self.decoder:
            self.lib.opus_decoder_destroy(self.decoder)
            self.decoder = None


class AudioIO:
    def __init__(self, input_device=None, output_device=None):
        import sounddevice as sd

        self.sd = sd
        self.input_device = input_device
        self.output_device = output_device
        self.input_stream = None
        self.output_stream = None
        # Playback writes and stream replacement/closure must never enter the
        # PortAudio stream concurrently. RLock permits open_output() to call
        # close_output() while already holding the lock.
        self._output_lock = threading.RLock()

    def open_input(self, rate=16000, blocksize=960):
        self.close_input()
        self.input_stream = self.sd.RawInputStream(
            samplerate=rate,
            blocksize=blocksize,
            device=self.input_device,
            channels=1,
            dtype="int16",
            latency="low",
        )
        self.input_stream.start()
        return self.input_stream

    def open_output(self, rate=24000):
        with self._output_lock:
            self.close_output()
            self.output_stream = self.sd.RawOutputStream(
                samplerate=rate,
                blocksize=0,
                device=self.output_device,
                channels=1,
                dtype="int16",
                latency="low",
            )
            self.output_stream.start()

    def write(self, pcm):
        with self._output_lock:
            if self.output_stream is not None:
                self.output_stream.write(pcm)

    def close_input(self):
        stream, self.input_stream = self.input_stream, None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()

    def close_output(self):
        with self._output_lock:
            stream, self.output_stream = self.output_stream, None
            if stream is not None:
                try:
                    stream.stop()
                finally:
                    stream.close()

    def close(self):
        self.close_input()
        self.close_output()
