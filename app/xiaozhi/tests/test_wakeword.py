import hashlib
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
            for path in engine._required_paths() + engine._asset_paths():
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb"):
                    pass
                os.chmod(path, 0o755)
            self.assertFalse(engine.available)

    def test_persistent_bundle_does_not_require_fallback_binary(self):
        with tempfile.TemporaryDirectory() as app_dir:
            engine = WakeWordEngine(app_dir, {}, lambda: None, lambda _: None, lambda _: None)
            guard = os.path.join(app_dir, "wake", "parent_guard.py")
            daemon = os.path.join(app_dir, "wake", "native", "wakeword-daemon")
            for path in (guard, daemon) + engine._asset_paths():
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb"):
                    pass
            os.chmod(daemon, 0o755)
            self.assertTrue(engine.persistent_available)
            self.assertTrue(engine.available)

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

    def test_explicit_zero_score_and_threshold_are_preserved(self):
        with tempfile.TemporaryDirectory() as app_dir:
            engine = WakeWordEngine(
                app_dir,
                {"wake_word_threshold": 0, "wake_word_score": 0},
                lambda: None,
                lambda _: None,
                lambda _: None,
            )
            command = engine.command()
            self.assertIn("--keywords-threshold=0.0", command)
            self.assertIn("--keywords-score=0.0", command)
            self.assertEqual(engine.daemon_command()[-2:], ["0.0", "0.0"])

    def test_bundled_binaries_target_riscv64(self):
        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        binaries = (
            os.path.join(app_dir, "wake", "native", "wakeword-daemon"),
            os.path.join(
                app_dir,
                "wake",
                "runtime-spacemit",
                "bin",
                "sherpa-onnx-keyword-spotter-alsa",
            ),
        )
        for binary in binaries:
            with self.subTest(binary=binary), open(binary, "rb") as stream:
                header = stream.read(20)
                self.assertEqual(header[:4], b"\x7fELF")
                self.assertEqual(int.from_bytes(header[18:20], "little"), 243)

    def test_bundled_assets_match_manifest(self):
        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        manifest = os.path.join(app_dir, "wake", "manifest.sha256")
        with open(manifest, "r", encoding="utf-8") as stream:
            entries = [line.strip().split(None, 1) for line in stream if line.strip()]

        self.assertGreater(len(entries), 0)
        for expected, relative_path in entries:
            path = os.path.join(app_dir, relative_path)
            digest = hashlib.sha256()
            with self.subTest(path=relative_path), open(path, "rb") as asset:
                for block in iter(lambda: asset.read(1024 * 1024), b""):
                    digest.update(block)
                self.assertEqual(digest.hexdigest(), expected)


if __name__ == "__main__":
    unittest.main()
