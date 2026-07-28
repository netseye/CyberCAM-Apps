import json
import os
import stat
import tempfile
import unittest
from unittest import mock

import activation


class ActivationTests(unittest.TestCase):
    def test_identity_is_stable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "device.json")
            with mock.patch.object(activation, "primary_mac", return_value="02:00:00:00:00:01"):
                first = activation.ensure_identity(path)
                second = activation.ensure_identity(path)
            self.assertEqual(first, second)
            self.assertEqual(first["device_id"], "02:00:00:00:00:01")
            self.assertTrue(first["client_id"])
            self.assertNotIn("serial_number", first)
            self.assertNotIn("hmac_key", first)
            with open(path, "r", encoding="utf-8") as handle:
                self.assertEqual(json.load(handle), first)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_corrupt_identity_is_not_silently_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "device.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{broken")
            with self.assertRaisesRegex(RuntimeError, "身份文件损坏"):
                activation.ensure_identity(path)
            with open(path, "r", encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "{broken")

    def test_ota_payload_does_not_contain_access_token(self):
        identity = {
            "device_id": "02:00:00:00:00:01",
            "client_id": "client",
            "hmac_key": "secret",
        }
        with mock.patch.object(activation, "local_ip", return_value="192.0.2.4"):
            headers, payload = activation.build_ota_request(identity)
        self.assertEqual(headers["Device-Id"], identity["device_id"])
        self.assertEqual(headers["Activation-Version"], "1")
        self.assertEqual(payload["board"]["ip"], "192.0.2.4")
        self.assertNotIn("access_token", json.dumps(payload))
        self.assertNotIn("secret", json.dumps(payload))

    def test_v1_activation_uses_empty_payload(self):
        identity = {
            "device_id": "dev",
            "client_id": "client",
        }
        request_payload = {}
        request_headers = {}

        def fake_post(url, headers, payload, **kwargs):
            request_payload.update(payload)
            request_headers.update(headers)
            return 200, {}

        client = activation.OTAClient("https://example.invalid/ota/")
        with mock.patch.object(activation, "post_json", side_effect=fake_post):
            self.assertTrue(client.activate(identity, {"challenge": "abc", "code": "123456"}))
        self.assertEqual(request_payload, {})
        self.assertEqual(request_headers["Activation-Version"], "1")

    def test_v2_activation_uses_flat_signed_payload(self):
        identity = {
            "device_id": "dev",
            "client_id": "client",
            "activation_version": 2,
            "hmac_key": "6b6579",
            "serial_number": "serial",
        }
        captured = {}

        def fake_post(url, headers, payload, **kwargs):
            captured["headers"] = headers
            captured["payload"] = payload
            return 200, {}

        client = activation.OTAClient("https://example.invalid/ota/")
        with mock.patch.object(activation, "post_json", side_effect=fake_post):
            self.assertTrue(client.activate(identity, {"challenge": "abc", "code": "123456"}))
        self.assertEqual(captured["headers"]["Serial-Number"], "serial")
        self.assertEqual(captured["payload"]["serial_number"], "serial")
        self.assertIn("hmac", captured["payload"])
        self.assertNotIn("Payload", captured["payload"])


if __name__ == "__main__":
    unittest.main()
