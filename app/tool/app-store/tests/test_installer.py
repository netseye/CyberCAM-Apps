import base64
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile


APP_DIR = os.path.dirname(os.path.dirname(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from installer import StoreService  # noqa: E402
from store_core import (  # noqa: E402
    OperationCancelled,
    SecurityError,
    StoreError,
    git_blob_sha,
)


APP_TXT = b'name_cn="Demo"\nname_en="Demo"\nversion="1.0.0"\nindex=9\n'
RUN_SH = b"#!/bin/sh\npython main.py\n"
ICON = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
MAIN_V1 = b"print('v1')\n"


class FakeClient:
    def __init__(self):
        self.json_values = {}
        self.downloads = {}

    def get_json(self, url, max_bytes=None):
        value = self.json_values[url]
        return json.loads(json.dumps(value))

    def download(
        self,
        url,
        destination,
        *,
        max_bytes,
        expected_size=None,
        progress=None,
    ):
        data = self.downloads[url]
        if len(data) > max_bytes:
            raise AssertionError("test download exceeds max")
        if expected_size is not None and len(data) != expected_size:
            raise AssertionError("test download size mismatch")
        with open(destination, "wb") as handle:
            handle.write(data)
        if progress:
            progress(len(data), expected_size or len(data))
        return len(data)


def write_json(path, value):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle)


def github_catalog(persistent=None, version=None):
    return {
        "schema_version": 1,
        "sources": {
            "official": {
                "kind": "github_tree",
                "repository": "owner/repo",
                "ref": "main",
            }
        },
        "apps": [
            dict(
                {
                    "id": "demo",
                    "name_cn": "Demo",
                    "name_en": "Demo",
                    "source": "official",
                    "path": "app/tool/demo",
                    "persistent_files": persistent or [],
                },
                **({"version": version} if version else {}),
            )
        ],
    }


def github_tree(files):
    entries = [
        {
            "path": "app/tool/demo",
            "type": "tree",
            "mode": "040000",
            "sha": "a" * 40,
        }
    ]
    for relative, data in files.items():
        entries.append(
            {
                "path": "app/tool/demo/" + relative,
                "type": "blob",
                "mode": "100644",
                "sha": git_blob_sha(data),
                "size": len(data),
            }
        )
    return entries


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = self.temp.name
        self.app_root = os.path.join(self.root, "data", "app")
        self.data_root = os.path.join(self.root, "data", ".app-store")
        self.bundle = os.path.join(self.root, "bundle")
        os.makedirs(self.bundle)
        self.catalog_url = "https://example.com/catalog.json"
        write_json(
            os.path.join(self.bundle, "sources.json"),
            {
                "schema_version": 1,
                "catalogs": [
                    {
                        "name": "Test",
                        "url": self.catalog_url,
                        "trusted": True,
                    }
                ],
            },
        )
        write_json(os.path.join(self.bundle, "catalog.json"), github_catalog())
        self.client = FakeClient()
        self.service = StoreService(
            app_root=self.app_root,
            data_root=self.data_root,
            app_dir=self.bundle,
            client=self.client,
        )

    def tearDown(self):
        self.temp.cleanup()

    def configure_github(self, files=None, persistent=None, version=None):
        files = files or {
            "app.txt": APP_TXT,
            "run.sh": RUN_SH,
            "icon.png": ICON,
            "main.py": MAIN_V1,
        }
        catalog = github_catalog(persistent, version)
        self.client.json_values[self.catalog_url] = catalog
        commit_url = "https://api.github.com/repos/owner/repo/commits/main"
        tree_url = "https://api.github.com/repos/owner/repo/git/trees/%s?recursive=1" % (
            "d" * 40
        )
        self.client.json_values[commit_url] = {
            "sha": "c" * 40,
            "commit": {"tree": {"sha": "d" * 40}},
        }
        self.client.json_values[tree_url] = {
            "truncated": False,
            "tree": github_tree(files),
        }
        for relative, data in files.items():
            raw = (
                "https://raw.githubusercontent.com/owner/repo/%s/app/tool/demo/%s"
                % ("c" * 40, relative)
            )
            self.client.downloads[raw] = data

    def test_refresh_and_transactional_github_install(self):
        self.configure_github()
        apps, warnings = self.service.refresh()
        self.assertEqual(warnings, [])
        self.assertEqual(apps[0]["status"], "available")
        self.service.install(apps[0])
        target = os.path.join(self.app_root, "demo")
        self.assertTrue(os.path.isfile(os.path.join(target, "main.py")))
        self.assertTrue(os.path.isfile(os.path.join(target, ".app-store.json")))
        self.assertTrue(os.access(os.path.join(target, "run.sh"), os.X_OK))
        self.assertEqual(
            self.service.refresh_local_status(apps)[0]["status"], "installed"
        )
        with open(
            os.path.join(target, ".app-store.json"), encoding="utf-8"
        ) as handle:
            metadata = json.load(handle)
        self.assertEqual(metadata["schema_version"], 2)
        self.assertEqual(metadata["version"], "1.0.0")
        staging = os.path.join(self.data_root, "staging")
        self.assertEqual(os.listdir(staging), [])

    def test_bundled_official_catalog_is_installable_without_api(self):
        service = StoreService(
            app_root=self.app_root,
            data_root=self.data_root,
            app_dir=APP_DIR,
            client=FakeClient(),
        )
        apps = service.load_initial()
        self.assertEqual(len(apps), 14)
        self.assertTrue(all(app.get("_files") for app in apps))
        self.assertTrue(all(app.get("_tree_sha") for app in apps))
        self.assertTrue(all(app.get("_revision") for app in apps))
        self.assertTrue(
            all(
                item.get("sha256")
                for app in apps
                for item in app.get("_files", [])
            )
        )
        self.assertEqual(
            apps[0]["_raw_bases"],
            [
                "https://raw.githubusercontent.com",
                "https://gh-proxy.com/https://raw.githubusercontent.com",
            ],
        )

    def test_embedded_file_manifest_does_not_use_github_api(self):
        files = {
            "app.txt": APP_TXT,
            "run.sh": RUN_SH,
            "icon.png": ICON,
            "main.py": MAIN_V1,
        }
        catalog = github_catalog()
        catalog["apps"][0]["tree_sha"] = "a" * 40
        catalog["apps"][0]["files"] = [
            {
                "path": relative,
                "mode": "100644",
                "sha": git_blob_sha(data),
                "size": len(data),
            }
            for relative, data in files.items()
        ]
        self.client.json_values[self.catalog_url] = catalog
        apps, warnings = self.service.refresh()
        self.assertEqual(warnings, [])
        self.assertEqual(apps[0]["_revision"], "main")
        self.assertEqual(apps[0]["_download_size"], sum(map(len, files.values())))

    def test_update_preserves_declared_configuration(self):
        self.configure_github(persistent=["config.json"])
        apps, _ = self.service.refresh()
        self.service.install(apps[0])
        target = os.path.join(self.app_root, "demo")
        with open(os.path.join(target, "config.json"), "w", encoding="utf-8") as handle:
            handle.write("keep-me")
        self.service.install(apps[0])
        with open(os.path.join(target, "config.json"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "keep-me")

    def test_blob_failure_does_not_replace_existing_app(self):
        self.configure_github()
        apps, _ = self.service.refresh()
        target = os.path.join(self.app_root, "demo")
        os.makedirs(target)
        with open(os.path.join(target, "marker"), "w", encoding="utf-8") as handle:
            handle.write("old")
        bad_url = (
            "https://raw.githubusercontent.com/owner/repo/%s/app/tool/demo/main.py"
            % ("c" * 40)
        )
        self.client.downloads[bad_url] = b"x" * len(MAIN_V1)
        with self.assertRaises(SecurityError):
            self.service.install(apps[0])
        self.assertTrue(os.path.isfile(os.path.join(target, "marker")))

    def test_catalog_and_app_txt_versions_must_match(self):
        self.configure_github(version="1.2.0")
        apps, _ = self.service.refresh()
        with self.assertRaises(SecurityError):
            self.service.install(apps[0])
        self.assertFalse(os.path.exists(os.path.join(self.app_root, "demo")))

    def test_cancelled_download_does_not_replace_existing_app(self):
        self.configure_github()
        apps, _ = self.service.refresh()
        target = os.path.join(self.app_root, "demo")
        os.makedirs(target)
        with open(os.path.join(target, "marker"), "w", encoding="utf-8") as handle:
            handle.write("old")

        def cancel(*_args):
            raise OperationCancelled("cancel")

        with self.assertRaises(OperationCancelled):
            self.service.install(apps[0], cancel)
        self.assertTrue(os.path.isfile(os.path.join(target, "marker")))
        self.assertEqual(
            os.listdir(os.path.join(self.data_root, "staging")), []
        )

    def test_update_retains_previous_version_and_rollback_swaps_it(self):
        self.configure_github()
        apps, _ = self.service.refresh()
        self.service.install(apps[0])
        target = os.path.join(self.app_root, "demo")
        with open(os.path.join(target, "main.py"), "wb") as handle:
            handle.write(b"locally-modified\n")
        self.service.install(apps[0])
        with open(os.path.join(target, "main.py"), "rb") as handle:
            self.assertEqual(handle.read(), MAIN_V1)
        self.service.rollback("demo")
        with open(os.path.join(target, "main.py"), "rb") as handle:
            self.assertEqual(handle.read(), b"locally-modified\n")

    def test_version_status_distinguishes_update_newer_and_repair(self):
        self.configure_github(version="1.2.0")
        apps, _ = self.service.refresh()
        target = os.path.join(self.app_root, "demo")
        os.makedirs(target)
        with open(os.path.join(target, "app.txt"), "wb") as handle:
            handle.write(APP_TXT)
        metadata_path = os.path.join(target, ".app-store.json")

        def status(version, tree_sha):
            write_json(
                metadata_path,
                {
                    "schema_version": 2,
                    "app_id": "demo",
                    "version": version,
                    "tree_sha": tree_sha,
                },
            )
            return self.service.refresh_local_status(apps)[0]["status"]

        self.assertEqual(status("1.1.0", "b" * 40), "update")
        self.assertEqual(status("1.3.0", "b" * 40), "newer")
        self.assertEqual(status("1.2.0", "b" * 40), "repair")
        self.assertEqual(status("1.2.0", "a" * 40), "installed")

    def test_operation_lock_rejects_a_second_writer(self):
        with self.service._operation_lock():
            with self.assertRaises(StoreError):
                with self.service._operation_lock():
                    pass

    def test_archive_install_and_recoverable_uninstall(self):
        package = io.BytesIO()
        manifest = json.dumps(
            {"schema_version": 1, "id": "archive-demo", "version": "1.0.0"}
        ).encode()
        with zipfile.ZipFile(package, "w") as archive:
            for name, data in {
                "archive-demo/app.txt": APP_TXT,
                "archive-demo/run.sh": RUN_SH,
                "archive-demo/icon.png": ICON,
                "archive-demo/main.py": MAIN_V1,
                "archive-demo/manifest.json": manifest,
            }.items():
                archive.writestr(name, data)
        package_data = package.getvalue()
        package_url = "https://example.com/archive-demo.zip"
        document = {
            "schema_version": 1,
            "sources": {"packages": {"kind": "archive"}},
            "apps": [
                {
                    "id": "archive-demo",
                    "name_cn": "Archive",
                    "name_en": "Archive",
                    "source": "packages",
                    "package_url": package_url,
                    "size": len(package_data),
                    "sha256": hashlib.sha256(package_data).hexdigest(),
                }
            ],
        }
        app = self.service._parse_catalog(
            document, catalog_name="Archive Test", trusted=False
        )[0]
        app = self.service.refresh_local_status([app])[0]
        self.client.downloads[package_url] = package_data
        self.service.install(app)
        target = os.path.join(self.app_root, "archive-demo")
        self.assertTrue(os.path.isfile(os.path.join(target, "manifest.json")))
        trash = self.service.uninstall("archive-demo")
        self.assertFalse(os.path.exists(target))
        self.assertTrue(os.path.isdir(trash))

    def test_zip_traversal_is_rejected(self):
        package = os.path.join(self.root, "bad.zip")
        destination = os.path.join(self.root, "extract")
        os.makedirs(destination)
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr("../escape", b"bad")
        with self.assertRaises(SecurityError):
            self.service._extract_archive(package, destination)
        self.assertFalse(os.path.exists(os.path.join(self.root, "escape")))

    def test_interrupted_update_restores_backup_on_next_start(self):
        backup = os.path.join(self.data_root, "backups", "demo-100")
        os.makedirs(backup)
        with open(os.path.join(backup, "marker"), "w", encoding="utf-8") as handle:
            handle.write("old")
        restored = self.service.recover_incomplete_updates()
        self.assertEqual(restored, ["demo"])
        self.assertTrue(os.path.isfile(os.path.join(self.app_root, "demo", "marker")))


if __name__ == "__main__":
    unittest.main()
