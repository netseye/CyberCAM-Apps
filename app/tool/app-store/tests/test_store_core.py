import os
import sys
import unittest


APP_DIR = os.path.dirname(os.path.dirname(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from store_core import (  # noqa: E402
    CatalogError,
    SecurityError,
    git_blob_sha,
    map_touch_coordinates,
    normalize_relative_path,
    parse_app_txt,
    select_github_app_files,
    validate_app_id,
    validate_https_url,
)


class StoreCoreTests(unittest.TestCase):
    def test_parse_app_txt_accepts_comments_and_quotes(self):
        parsed = parse_app_txt(
            '# comment\nname_cn="应用 商店" # 中文\n'
            'name_en="App Store"\nversion="1.0.0"\nindex=2\n'
        )
        self.assertEqual(parsed["name_cn"], "应用 商店")
        self.assertEqual(parsed["name_en"], "App Store")
        self.assertEqual(parsed["index"], "2")

    def test_parse_app_txt_rejects_shell_commands(self):
        with self.assertRaises(CatalogError):
            parse_app_txt(
                'name_cn="Bad"\nname_en="Bad"\nindex=1\npython main.py\n'
            )

    def test_identifiers_and_https_urls_are_strict(self):
        self.assertEqual(validate_app_id("face-det"), "face-det")
        self.assertEqual(
            validate_https_url("https://example.com/app.tar.gz"),
            "https://example.com/app.tar.gz",
        )
        for value in ("../bad", "UPPER", "", "a/b"):
            with self.assertRaises(CatalogError):
                validate_app_id(value)
        for value in (
            "http://example.com/a",
            "file:///tmp/a",
            "https://user@example.com/a",
            "https://example.com/a#fragment",
        ):
            with self.assertRaises(CatalogError):
                validate_https_url(value)

    def test_relative_paths_reject_traversal_and_backslashes(self):
        self.assertEqual(normalize_relative_path("assets/icon.png"), "assets/icon.png")
        for value in ("../a", "a/../b", "/absolute", "a\\b", "./a", "a//b"):
            with self.assertRaises(SecurityError):
                normalize_relative_path(value)

    def test_select_github_files_rejects_links(self):
        data = b"hello"
        entries = [
            {
                "path": "app/tool/demo",
                "type": "tree",
                "mode": "040000",
                "sha": "a" * 40,
            },
            {
                "path": "app/tool/demo/main.py",
                "type": "blob",
                "mode": "100644",
                "sha": git_blob_sha(data),
                "size": len(data),
            },
        ]
        directory_sha, files = select_github_app_files(entries, "app/tool/demo")
        self.assertEqual(directory_sha, "a" * 40)
        self.assertEqual(files[0]["relative_path"], "main.py")

        entries.append(
            {
                "path": "app/tool/demo/link",
                "type": "blob",
                "mode": "120000",
                "sha": "b" * 40,
                "size": 4,
            }
        )
        with self.assertRaises(SecurityError):
            select_github_app_files(entries, "app/tool/demo")

    def test_portrait_touch_axes_are_rotated_to_landscape(self):
        self.assertEqual(
            map_touch_coordinates(480, 0, (0, 480), (0, 640)),
            (0, 0),
        )
        self.assertEqual(
            map_touch_coordinates(0, 640, (0, 480), (0, 640)),
            (639, 479),
        )
        self.assertEqual(
            map_touch_coordinates(480, 0, (0, 480), (0, 640), flipped=True),
            (639, 479),
        )


if __name__ == "__main__":
    unittest.main()
