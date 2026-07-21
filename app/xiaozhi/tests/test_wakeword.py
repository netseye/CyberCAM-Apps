import os
import tempfile
import unittest

from wakeword import READY_MARKER, WakeWordEngine, detected_keyword, is_detection_line


class WakeWordTests(unittest.TestCase):
    def test_detection_lines_only_after_ready(self):
        self.assertFalse(is_detection_line("小智小智", ready=False))
        self.assertFalse(is_detection_line(READY_MARKER, ready=True))
        self.assertFalse(is_detection_line("Use recording device: plughw:0,0", ready=True))
        self.assertFalse(is_detection_line("ALSA device warning", ready=True))
        result = '0:{"start_time":0.0,"keyword": "小智小智", "timestamps": [1.6,'
        self.assertTrue(is_detection_line(result, ready=True))
        self.assertEqual(detected_keyword(result), "小智小智")

    def test_missing_assets_disable_engine(self):
        with tempfile.TemporaryDirectory() as app_dir:
            engine = WakeWordEngine(app_dir, {}, lambda: None, lambda _: None, lambda _: None)
            self.assertFalse(engine.available)
            self.assertFalse(engine.start())

    def test_parent_guard_is_required_even_if_models_exist(self):
        with tempfile.TemporaryDirectory() as app_dir:
            engine = WakeWordEngine(app_dir, {}, lambda: None, lambda _: None, lambda _: None)
            for path in engine._required_paths():
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb"):
                    pass
            self.assertFalse(engine.available)

    def test_command_uses_private_assets_and_threshold(self):
        with tempfile.TemporaryDirectory() as app_dir:
            engine = WakeWordEngine(
                app_dir,
                {"wake_word_threshold": 0.12, "wake_word_score": 2.5},
                lambda: None,
                lambda _: None,
                lambda _: None,
            )
            command = engine.command()
            self.assertIn("--model-type=zipformer2", command)
            self.assertIn("--keywords-threshold=0.12", command)
            self.assertIn("--keywords-score=2.5", command)
            self.assertTrue(command[0].startswith(os.path.join(app_dir, "wake")))
            daemon = engine.daemon_command()
            self.assertTrue(daemon[0].endswith("wake/native/wakeword-daemon"))
            self.assertEqual(daemon[-2:], ["2.5", "0.12"])
            guarded = engine.guarded_command(daemon)
            self.assertEqual(guarded[2], str(os.getpid()))
            self.assertTrue(guarded[1].endswith("wake/parent_guard.py"))
            self.assertEqual(guarded[3:], daemon)


if __name__ == "__main__":
    unittest.main()
