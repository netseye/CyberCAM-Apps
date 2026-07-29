import os
import socket
import sys
import tempfile
import unittest


APP_DIR = os.path.dirname(os.path.dirname(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from network import HttpClient, NetworkError  # noqa: E402


class RetryClient(HttpClient):
    def __init__(self, failures, retryable=True):
        super().__init__(retries=3, retry_delay=0)
        self.failures = failures
        self.retryable = retryable
        self.calls = 0

    def _get_bytes_once(self, url, max_bytes):
        self.calls += 1
        if self.calls <= self.failures:
            raise NetworkError("temporary", retryable=self.retryable)
        return b"ok"


class NetworkTests(unittest.TestCase):
    def test_transient_failures_are_retried(self):
        client = RetryClient(failures=2)
        self.assertEqual(client.get_bytes("https://example.com/file"), b"ok")
        self.assertEqual(client.calls, 3)

    def test_permanent_failures_are_not_retried(self):
        client = RetryClient(failures=1, retryable=False)
        with self.assertRaises(NetworkError):
            client.get_bytes("https://example.com/missing")
        self.assertEqual(client.calls, 1)

    def test_retry_count_is_bounded(self):
        client = RetryClient(failures=9)
        with self.assertRaises(NetworkError):
            client.get_bytes("https://example.com/offline")
        self.assertEqual(client.calls, 4)

    def test_download_resumes_a_partial_response(self):
        class Response:
            def __init__(self, status, headers, events):
                self.status = status
                self.headers = headers
                self.events = list(events)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size):
                event = self.events.pop(0)
                if isinstance(event, Exception):
                    raise event
                return event

        class Client(HttpClient):
            def __init__(self):
                super().__init__(retries=1, retry_delay=0)
                self.headers = []
                self.responses = [
                    Response(
                        200,
                        {"Content-Length": "6"},
                        [b"abc", socket.timeout("interrupted")],
                    ),
                    Response(
                        206,
                        {"Content-Length": "3", "Content-Range": "bytes 3-5/6"},
                        [b"def", b""],
                    ),
                ]

            def _open(self, url, headers=None):
                self.headers.append(headers)
                return self.responses.pop(0)

        with tempfile.TemporaryDirectory() as root:
            destination = os.path.join(root, "file")
            client = Client()
            self.assertEqual(
                client.download(
                    "https://example.com/file",
                    destination,
                    max_bytes=6,
                    expected_size=6,
                ),
                6,
            )
            with open(destination, "rb") as handle:
                self.assertEqual(handle.read(), b"abcdef")
            self.assertEqual(client.headers, [None, {"Range": "bytes=3-"}])

    def test_download_rotates_to_an_equivalent_endpoint(self):
        class Client(HttpClient):
            def __init__(self):
                super().__init__(retries=1, retry_delay=0)
                self.urls = []

            def _download_once(self, url, destination, **_kwargs):
                self.urls.append(url)
                if len(self.urls) == 1:
                    raise NetworkError("primary offline", retryable=True)
                with open(destination, "wb") as handle:
                    handle.write(b"ok")
                return 2

        with tempfile.TemporaryDirectory() as root:
            destination = os.path.join(root, "file")
            client = Client()
            self.assertEqual(
                client.download_from(
                    [
                        "https://primary.example.com/file",
                        "https://mirror.example.com/file",
                    ],
                    destination,
                    max_bytes=2,
                    expected_size=2,
                ),
                2,
            )
            self.assertEqual(
                client.urls,
                [
                    "https://primary.example.com/file",
                    "https://mirror.example.com/file",
                ],
            )


if __name__ == "__main__":
    unittest.main()
