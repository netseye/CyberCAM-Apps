import math
import threading
import time
import unittest
from array import array
from unittest import mock

from audio import AudioIO, PCMPreprocessor, pcm_level


class AudioTests(unittest.TestCase):
    def test_dc_blocker_removes_constant_offset(self):
        source = array("h", [9000] * 960).tobytes()
        output = array("h")
        output.frombytes(PCMPreprocessor().process(source))
        self.assertLess(max(abs(value) for value in output[-100:]), 1)

    def test_dc_blocker_preserves_voice_band(self):
        samples = array(
            "h",
            [int(8000 * math.sin(2 * math.pi * 440 * index / 16000)) for index in range(960)],
        )
        filtered = PCMPreprocessor().process(samples.tobytes())
        self.assertGreater(pcm_level(filtered), 0.4)

    def test_pcm_level_is_bounded(self):
        self.assertEqual(pcm_level(b""), 0.0)
        self.assertLessEqual(pcm_level(array("h", [32767] * 20).tobytes()), 1.0)

    def test_close_output_waits_for_active_write(self):
        entered = threading.Event()
        release = threading.Event()
        stream = mock.Mock()

        def write(_pcm):
            entered.set()
            release.wait(1.0)

        stream.write.side_effect = write
        audio = AudioIO.__new__(AudioIO)
        audio.output_stream = stream
        audio._output_lock = threading.RLock()

        writer = threading.Thread(target=audio.write, args=(b"pcm",))
        closer = threading.Thread(target=audio.close_output)
        writer.start()
        self.assertTrue(entered.wait(0.5))
        closer.start()
        time.sleep(0.02)
        stream.stop.assert_not_called()
        release.set()
        writer.join(0.5)
        closer.join(0.5)
        stream.stop.assert_called_once_with()
        stream.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
