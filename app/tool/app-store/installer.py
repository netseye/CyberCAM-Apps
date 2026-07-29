"""Catalog resolution and transactional installers for CyberCAM apps."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tarfile
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from urllib.parse import quote

from network import HttpClient, NetworkError
from store_core import (
    CatalogError,
    SecurityError,
    StoreError,
    git_blob_sha,
    load_json_bytes,
    normalize_relative_path,
    parse_app_txt,
    select_github_app_files,
    validate_app_id,
    validate_https_url,
    validate_sha256,
)


MAX_CATALOG_BYTES = 2 * 1024 * 1024
MAX_TREE_BYTES = 12 * 1024 * 1024
MAX_PACKAGE_BYTES = 256 * 1024 * 1024
MAX_APP_BYTES = 512 * 1024 * 1024
MAX_APP_FILES = 10_000
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _read_json_file(path: str, max_bytes: int = MAX_CATALOG_BYTES) -> dict:
    try:
        size = os.path.getsize(path)
        if size > max_bytes:
            raise CatalogError("JSON 文件过大：%s" % path)
        with open(path, "rb") as handle:
            return load_json_bytes(handle.read(), path)
    except FileNotFoundError:
        raise CatalogError("找不到文件：%s" % path)


def _write_json_atomic(path: str, value: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(256 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _github_raw_bases(source: dict) -> list[str]:
    primary = str(
        source.get("raw_base") or "https://raw.githubusercontent.com"
    ).rstrip("/")
    mirrors = source.get("raw_mirrors", [])
    if not isinstance(mirrors, list) or len(mirrors) > 4:
        raise CatalogError("GitHub raw_mirrors 必须是最多 4 项的数组")
    result: list[str] = []
    for value in [primary, *mirrors]:
        validated = validate_https_url(str(value)).rstrip("/")
        if validated not in result:
            result.append(validated)
    return result


class StoreService:
    def __init__(
        self,
        *,
        app_root: str | None = None,
        data_root: str | None = None,
        app_dir: str | None = None,
        client: HttpClient | None = None,
    ):
        self.app_root = os.path.abspath(
            app_root or os.environ.get("CYBERCAM_APP_ROOT", "/data/app")
        )
        self.data_root = os.path.abspath(
            data_root or os.environ.get("CYBERCAM_STORE_DATA", "/data/.app-store")
        )
        self.app_dir = os.path.abspath(app_dir or os.path.dirname(__file__))
        self.client = client or HttpClient()

    @property
    def bundled_catalog_path(self) -> str:
        return os.path.join(self.app_dir, "catalog.json")

    @property
    def bundled_sources_path(self) -> str:
        return os.path.join(self.app_dir, "sources.json")

    @property
    def user_sources_path(self) -> str:
        return os.path.join(self.data_root, "sources.json")

    def load_initial(self) -> list[dict]:
        self.recover_incomplete_updates()
        document = _read_json_file(self.bundled_catalog_path)
        apps = self._parse_catalog(
            document,
            catalog_name="01Studio 官方（内置）",
            trusted=True,
        )
        self._resolve_github_sources(apps, [], allow_api=False)
        return self._decorate_installed(apps)

    def refresh_local_status(self, apps: list[dict]) -> list[dict]:
        return self._decorate_installed(apps)

    def refresh(self) -> tuple[list[dict], list[str]]:
        configs = self._load_source_configs()
        apps: list[dict] = []
        warnings: list[str] = []
        seen_ids: set[str] = set()
        for config in configs:
            if not config.get("enabled", True):
                continue
            name = str(config.get("name") or "应用源")
            trusted = bool(config.get("trusted", False))
            document = None
            url = config.get("url")
            try:
                document = self.client.get_json(
                    validate_https_url(url), max_bytes=MAX_CATALOG_BYTES
                )
            except StoreError as exc:
                fallback = config.get("fallback")
                if fallback == "catalog.json":
                    try:
                        document = _read_json_file(self.bundled_catalog_path)
                        warnings.append("%s：网络目录不可用，已使用内置目录" % name)
                    except StoreError:
                        pass
                if document is None:
                    warnings.append("%s：%s" % (name, exc))
                    continue
            try:
                parsed = self._parse_catalog(
                    document, catalog_name=name, trusted=trusted
                )
            except StoreError as exc:
                warnings.append("%s：%s" % (name, exc))
                continue
            for app in parsed:
                if app["id"] in seen_ids:
                    warnings.append("%s：忽略重复应用 %s" % (name, app["id"]))
                    continue
                seen_ids.add(app["id"])
                apps.append(app)
        if not apps:
            raise CatalogError("没有可用应用源")
        self._resolve_github_sources(apps, warnings)
        return self._decorate_installed(apps), warnings

    def _load_source_configs(self) -> list[dict]:
        path = (
            self.user_sources_path
            if os.path.isfile(self.user_sources_path)
            else self.bundled_sources_path
        )
        document = _read_json_file(path)
        if document.get("schema_version") != 1:
            raise CatalogError("sources.json schema_version 必须为 1")
        catalogs = document.get("catalogs")
        if not isinstance(catalogs, list) or not catalogs:
            raise CatalogError("sources.json 没有 catalogs")
        if len(catalogs) > 20:
            raise CatalogError("sources.json 最多支持 20 个目录")
        for config in catalogs:
            if not isinstance(config, dict):
                raise CatalogError("catalogs 项必须是对象")
        return catalogs

    def _parse_catalog(
        self, document: dict, *, catalog_name: str, trusted: bool
    ) -> list[dict]:
        if document.get("schema_version") != 1:
            raise CatalogError("catalog.json schema_version 必须为 1")
        source_map = document.get("sources")
        raw_apps = document.get("apps")
        if not isinstance(source_map, dict) or not isinstance(raw_apps, list):
            raise CatalogError("catalog.json 缺少 sources 或 apps")
        if len(raw_apps) > 500:
            raise CatalogError("单个目录最多支持 500 个应用")
        apps: list[dict] = []
        for raw in raw_apps:
            if not isinstance(raw, dict):
                raise CatalogError("apps 项必须是对象")
            app = dict(raw)
            app_id = validate_app_id(app.get("id"))
            source_name = str(app.get("source") or "")
            source = source_map.get(source_name)
            if not isinstance(source, dict):
                raise CatalogError("%s 缺少应用源定义" % app_id)
            source = dict(source)
            kind = source.get("kind")
            if kind not in ("github_tree", "archive"):
                raise CatalogError("%s 的应用源类型不受支持" % app_id)
            app["id"] = app_id
            app["name_cn"] = str(app.get("name_cn") or app_id)[:48]
            app["name_en"] = str(app.get("name_en") or app_id)[:64]
            app["category"] = str(app.get("category") or "other")[:32]
            app["summary_cn"] = str(app.get("summary_cn") or "")[:120]
            app["version"] = str(app.get("version") or "rolling")
            app["_source"] = source
            app["_source_name"] = source_name
            app["_source_kind"] = kind
            app["_catalog_name"] = catalog_name
            app["_trusted"] = trusted
            persistent = app.get("persistent_files", [])
            if not isinstance(persistent, list):
                raise CatalogError("%s 的 persistent_files 必须是数组" % app_id)
            app["persistent_files"] = [
                normalize_relative_path(path) for path in persistent
            ]
            if kind == "github_tree":
                repository = str(source.get("repository") or "")
                if not REPOSITORY_RE.fullmatch(repository):
                    raise CatalogError("%s 的 GitHub 仓库名无效" % app_id)
                app["path"] = normalize_relative_path(app.get("path"))
            else:
                app["package_url"] = validate_https_url(app.get("package_url"))
                app["sha256"] = validate_sha256(app.get("sha256"))
                size = app.get("size", 0)
                if not isinstance(size, int) or size < 0 or size > MAX_PACKAGE_BYTES:
                    raise CatalogError("%s 的压缩包大小无效" % app_id)
                app["size"] = size
            apps.append(app)
        return apps

    def _resolve_github_sources(
        self, apps: list[dict], warnings: list[str], *, allow_api: bool = True
    ) -> None:
        groups: dict[tuple, list[dict]] = {}
        for app in apps:
            if app["_source_kind"] != "github_tree":
                continue
            source = app["_source"]
            try:
                raw_bases = _github_raw_bases(source)
            except StoreError as exc:
                app["_source_error"] = str(exc)
                warnings.append("%s：%s" % (app["_catalog_name"], exc))
                continue
            static_files = app.get("files")
            static_tree_sha = str(app.get("tree_sha") or "").lower()
            if isinstance(static_files, list) and static_tree_sha:
                try:
                    entries = [
                        {
                            "path": app["path"],
                            "type": "tree",
                            "mode": "040000",
                            "sha": static_tree_sha,
                        }
                    ]
                    for item in static_files:
                        if not isinstance(item, dict):
                            raise CatalogError("%s 的文件清单项无效" % app["id"])
                        relative = normalize_relative_path(item.get("path"))
                        entries.append(
                            {
                                "path": app["path"] + "/" + relative,
                                "type": "blob",
                                "mode": str(item.get("mode") or ""),
                                "sha": str(item.get("sha") or ""),
                                "size": item.get("size"),
                            }
                        )
                    directory_sha, files = select_github_app_files(
                        entries, app["path"]
                    )
                    total = sum(item["size"] for item in files)
                    if total > MAX_APP_BYTES or len(files) > MAX_APP_FILES:
                        raise SecurityError("%s 超过应用大小限制" % app["id"])
                    app["_revision"] = str(source.get("ref") or "main")
                    app["_tree_sha"] = directory_sha
                    app["_files"] = files
                    app["_download_size"] = total
                    app["_raw_base"] = raw_bases[0]
                    app["_raw_bases"] = raw_bases
                    continue
                except StoreError as exc:
                    app["_source_error"] = str(exc)
                    warnings.append("%s：%s" % (app["_catalog_name"], exc))
                    continue
            if not allow_api:
                app["_source_error"] = "在线目录尚未刷新"
                continue
            key = (
                source["repository"],
                str(source.get("ref") or "main"),
                str(source.get("api_base") or "https://api.github.com"),
                tuple(raw_bases),
            )
            groups.setdefault(key, []).append(app)
        for key, group in groups.items():
            repository, ref, api_base, raw_bases = key
            try:
                validate_https_url(api_base)
                commit_url = "%s/repos/%s/commits/%s" % (
                    api_base.rstrip("/"),
                    repository,
                    quote(ref, safe=""),
                )
                commit = self.client.get_json(commit_url, max_bytes=MAX_CATALOG_BYTES)
                revision = str(commit.get("sha") or "").lower()
                tree_sha = str(
                    ((commit.get("commit") or {}).get("tree") or {}).get("sha") or ""
                ).lower()
                if not re.fullmatch(r"[0-9a-f]{40}", revision) or not re.fullmatch(
                    r"[0-9a-f]{40}", tree_sha
                ):
                    raise CatalogError("GitHub 提交响应缺少 SHA")
                tree_url = "%s/repos/%s/git/trees/%s?recursive=1" % (
                    api_base.rstrip("/"),
                    repository,
                    tree_sha,
                )
                tree = self.client.get_json(tree_url, max_bytes=MAX_TREE_BYTES)
                if tree.get("truncated"):
                    raise CatalogError("GitHub 文件树过大，响应被截断")
                entries = tree.get("tree")
                if not isinstance(entries, list):
                    raise CatalogError("GitHub 文件树响应无效")
                for app in group:
                    directory_sha, files = select_github_app_files(
                        entries, app["path"]
                    )
                    total = sum(item["size"] for item in files)
                    if total > MAX_APP_BYTES or len(files) > MAX_APP_FILES:
                        raise SecurityError("%s 超过应用大小限制" % app["id"])
                    app["_revision"] = revision
                    app["_tree_sha"] = directory_sha
                    app["_files"] = files
                    app["_download_size"] = total
                    app["_raw_base"] = raw_bases[0]
                    app["_raw_bases"] = list(raw_bases)
            except StoreError as exc:
                warnings.append("%s：%s" % (group[0]["_catalog_name"], exc))
                for app in group:
                    app["_source_error"] = str(exc)

    def _decorate_installed(self, apps: list[dict]) -> list[dict]:
        for app in apps:
            target = self._target_path(app["id"])
            metadata = None
            if os.path.isdir(target) and not os.path.islink(target):
                metadata_path = os.path.join(target, ".app-store.json")
                try:
                    metadata = _read_json_file(metadata_path, 128 * 1024)
                except StoreError:
                    metadata = None
            app["_installed_metadata"] = metadata
            if not os.path.isdir(target) or os.path.islink(target):
                app["status"] = "available"
            elif metadata is None or metadata.get("app_id") != app["id"]:
                app["status"] = "unmanaged"
            elif app["_source_kind"] == "github_tree":
                remote_sha = app.get("_tree_sha")
                if remote_sha and metadata.get("tree_sha") != remote_sha:
                    app["status"] = "update"
                else:
                    app["status"] = "installed"
            elif metadata.get("sha256") != app.get("sha256"):
                app["status"] = "update"
            else:
                app["status"] = "installed"
            if app.get("_source_error") and app["status"] == "available":
                app["status"] = "error"
        return apps

    def install(self, app: dict, progress=None) -> dict:
        target = self._target_path(app["id"])
        if os.path.realpath(target) == os.path.realpath(self.app_dir):
            raise StoreError("应用商店不能在运行时更新自身")
        if app["_source_kind"] == "github_tree":
            return self._install_github(app, progress)
        return self._install_archive(app, progress)

    def _install_github(self, app: dict, progress=None) -> dict:
        files = app.get("_files")
        revision = app.get("_revision")
        if not isinstance(files, list) or not revision:
            raise StoreError(app.get("_source_error") or "请先刷新在线目录")
        total = sum(item["size"] for item in files)
        self._prepare_storage(total)
        operation = tempfile.mkdtemp(
            prefix=app["id"] + "-", dir=os.path.join(self.data_root, "staging")
        )
        stage = os.path.join(operation, "app")
        os.makedirs(stage)
        completed = 0
        try:
            repository = app["_source"]["repository"]
            for item in files:
                relative = item["relative_path"]
                target = os.path.join(stage, *relative.split("/"))
                os.makedirs(os.path.dirname(target), exist_ok=True)
                raw_urls = [
                    "%s/%s/%s/%s"
                    % (
                        base,
                        repository,
                        quote(revision, safe=""),
                        quote(item["repository_path"], safe="/"),
                    )
                    for base in app.get("_raw_bases", [app["_raw_base"]])
                ]

                def file_progress(done, _file_total, base=completed, name=relative):
                    if progress is not None:
                        progress(base + done, total, "下载 " + name)

                download_from = getattr(self.client, "download_from", None)
                if callable(download_from):
                    download_from(
                        raw_urls,
                        target,
                        max_bytes=max(item["size"], 1),
                        expected_size=item["size"],
                        progress=file_progress,
                    )
                else:
                    self.client.download(
                        raw_urls[0],
                        target,
                        max_bytes=max(item["size"], 1),
                        expected_size=item["size"],
                        progress=file_progress,
                    )
                with open(target, "rb") as handle:
                    data = handle.read()
                if git_blob_sha(data) != item["sha"]:
                    raise SecurityError("文件 Git SHA 校验失败：%s" % relative)
                os.chmod(target, 0o755 if item["mode"] == "100755" else 0o644)
                completed += item["size"]
            metadata = {
                "schema_version": 1,
                "app_id": app["id"],
                "installed_at": _utc_now(),
                "catalog": app["_catalog_name"],
                "source_kind": "github_tree",
                "repository": app["_source"]["repository"],
                "repository_path": app["path"],
                "revision": revision,
                "tree_sha": app["_tree_sha"],
            }
            self._finish_install(stage, app, metadata)
            if progress is not None:
                progress(total, total, "安装完成")
            return metadata
        finally:
            shutil.rmtree(operation, ignore_errors=True)

    def _install_archive(self, app: dict, progress=None) -> dict:
        package_size = app.get("size") or 0
        self._prepare_storage(package_size or 8 * 1024 * 1024)
        operation = tempfile.mkdtemp(
            prefix=app["id"] + "-", dir=os.path.join(self.data_root, "staging")
        )
        package = os.path.join(operation, "package")
        extracted = os.path.join(operation, "extracted")
        os.makedirs(extracted)
        try:
            self.client.download(
                app["package_url"],
                package,
                max_bytes=MAX_PACKAGE_BYTES,
                expected_size=package_size or None,
                progress=(
                    (lambda done, total: progress(done, total, "下载应用包"))
                    if progress is not None
                    else None
                ),
            )
            if _file_sha256(package) != app["sha256"]:
                raise SecurityError("应用包 SHA-256 校验失败")
            self._extract_archive(package, extracted)
            stage = self._locate_archive_app(extracted)
            manifest_path = os.path.join(stage, "manifest.json")
            manifest = _read_json_file(manifest_path, 256 * 1024)
            if manifest.get("schema_version") != 1:
                raise CatalogError("应用包 manifest schema_version 必须为 1")
            if validate_app_id(manifest.get("id")) != app["id"]:
                raise SecurityError("应用包 manifest ID 与目录不一致")
            manifest_version = str(manifest.get("version") or "")
            if not manifest_version:
                raise CatalogError("应用包 manifest 缺少 version")
            if app["version"] != "rolling" and manifest_version != app["version"]:
                raise SecurityError("应用包 manifest 版本与目录不一致")
            persistent = manifest.get("persistent_files", [])
            if persistent:
                if not isinstance(persistent, list):
                    raise CatalogError("manifest persistent_files 必须是数组")
                app = dict(app)
                app["persistent_files"] = list(
                    dict.fromkeys(
                        app["persistent_files"]
                        + [normalize_relative_path(path) for path in persistent]
                    )
                )
            metadata = {
                "schema_version": 1,
                "app_id": app["id"],
                "installed_at": _utc_now(),
                "catalog": app["_catalog_name"],
                "source_kind": "archive",
                "package_url": app["package_url"],
                "sha256": app["sha256"],
                "version": str(manifest.get("version") or app["version"]),
            }
            self._finish_install(stage, app, metadata)
            if progress is not None:
                progress(package_size, package_size, "安装完成")
            return metadata
        finally:
            shutil.rmtree(operation, ignore_errors=True)

    def _prepare_storage(self, expected_size: int) -> None:
        os.makedirs(self.app_root, exist_ok=True)
        os.makedirs(os.path.join(self.data_root, "staging"), exist_ok=True)
        os.makedirs(os.path.join(self.data_root, "backups"), exist_ok=True)
        os.makedirs(os.path.join(self.data_root, "trash"), exist_ok=True)
        free = shutil.disk_usage(self.data_root).free
        required = max(8 * 1024 * 1024, expected_size * 2 + 4 * 1024 * 1024)
        if free < required:
            raise StoreError("存储空间不足，需要至少 %d MB" % (required // 1024 // 1024))
        if os.stat(self.app_root).st_dev != os.stat(self.data_root).st_dev:
            raise StoreError("应用目录与暂存目录必须位于同一文件系统")

    def _finish_install(self, stage: str, app: dict, metadata: dict) -> None:
        self._validate_stage(stage)
        self._preserve_files(app["id"], stage, app.get("persistent_files", []))
        os.chmod(os.path.join(stage, "run.sh"), 0o755)
        _write_json_atomic(os.path.join(stage, ".app-store.json"), metadata)
        self._activate(app["id"], stage)

    def _validate_stage(self, stage: str) -> None:
        for required in ("app.txt", "run.sh", "icon.png"):
            path = os.path.join(stage, required)
            if not os.path.isfile(path) or os.path.islink(path):
                raise CatalogError("应用缺少文件：%s" % required)
        with open(os.path.join(stage, "app.txt"), "r", encoding="utf-8") as handle:
            parse_app_txt(handle.read())
        with open(os.path.join(stage, "icon.png"), "rb") as handle:
            if handle.read(8) != b"\x89PNG\r\n\x1a\n":
                raise CatalogError("icon.png 不是有效的 PNG 文件")

    def _preserve_files(
        self, app_id: str, stage: str, persistent_files: list[str]
    ) -> None:
        old_root = self._target_path(app_id)
        if not os.path.isdir(old_root) or os.path.islink(old_root):
            return
        for relative in persistent_files:
            relative = normalize_relative_path(relative)
            source = os.path.join(old_root, *relative.split("/"))
            destination = os.path.join(stage, *relative.split("/"))
            if os.path.islink(source):
                raise SecurityError("拒绝保留符号链接：%s" % relative)
            if os.path.isfile(source):
                os.makedirs(os.path.dirname(destination), exist_ok=True)
                shutil.copy2(source, destination)
            elif os.path.isdir(source):
                if os.path.exists(destination):
                    shutil.rmtree(destination)
                self._copy_persistent_directory(source, destination, relative)

    @staticmethod
    def _copy_persistent_directory(
        source: str, destination: str, relative_label: str
    ) -> None:
        os.makedirs(destination)
        for root, directories, files in os.walk(source, followlinks=False):
            for name in list(directories):
                path = os.path.join(root, name)
                if os.path.islink(path):
                    raise SecurityError(
                        "保留目录包含符号链接：%s"
                        % os.path.join(relative_label, name)
                    )
            for name in files:
                path = os.path.join(root, name)
                if os.path.islink(path):
                    raise SecurityError(
                        "保留目录包含符号链接：%s"
                        % os.path.join(relative_label, name)
                    )
            subpath = os.path.relpath(root, source)
            target_root = (
                destination
                if subpath == "."
                else os.path.join(destination, subpath)
            )
            os.makedirs(target_root, exist_ok=True)
            for directory in directories:
                os.makedirs(os.path.join(target_root, directory), exist_ok=True)
            for name in files:
                shutil.copy2(os.path.join(root, name), os.path.join(target_root, name))

    def _activate(self, app_id: str, stage: str) -> None:
        target = self._target_path(app_id)
        if os.path.islink(target):
            raise SecurityError("目标应用目录不能是符号链接")
        if os.path.exists(target) and not os.path.isdir(target):
            raise SecurityError("目标应用路径不是目录")
        stamp = "%d" % int(time.time())
        backup = os.path.join(self.data_root, "backups", app_id + "-" + stamp)
        moved_old = False
        try:
            if os.path.isdir(target):
                if os.path.exists(backup):
                    raise StoreError("备份目录已存在")
                os.rename(target, backup)
                moved_old = True
            os.rename(stage, target)
        except Exception:
            if moved_old and not os.path.exists(target) and os.path.isdir(backup):
                os.rename(backup, target)
            raise
        if moved_old:
            shutil.rmtree(backup, ignore_errors=True)

    def recover_incomplete_updates(self) -> list[str]:
        """Restore an old app if power was lost between the two rename calls."""

        restored: list[str] = []
        backup_root = os.path.join(self.data_root, "backups")
        if not os.path.isdir(backup_root):
            return restored
        os.makedirs(self.app_root, exist_ok=True)
        for name in sorted(os.listdir(backup_root), reverse=True):
            path = os.path.join(backup_root, name)
            if not os.path.isdir(path) or os.path.islink(path) or "-" not in name:
                continue
            app_id, stamp = name.rsplit("-", 1)
            if not stamp.isdigit():
                continue
            try:
                app_id = validate_app_id(app_id)
            except CatalogError:
                continue
            target = self._target_path(app_id)
            if not os.path.exists(target):
                os.rename(path, target)
                restored.append(app_id)
        return restored

    def uninstall(self, app_id: str) -> str:
        app_id = validate_app_id(app_id)
        target = self._target_path(app_id)
        if os.path.realpath(target) == os.path.realpath(self.app_dir):
            raise StoreError("应用商店不能在运行时卸载自身")
        if not os.path.isdir(target) or os.path.islink(target):
            raise StoreError("应用未安装")
        self._prepare_storage(0)
        destination = os.path.join(
            self.data_root, "trash", "%s-%d" % (app_id, int(time.time()))
        )
        os.rename(target, destination)
        return destination

    def _target_path(self, app_id: str) -> str:
        return os.path.join(self.app_root, validate_app_id(app_id))

    def _extract_archive(self, package: str, destination: str) -> None:
        if zipfile.is_zipfile(package):
            self._extract_zip(package, destination)
            return
        try:
            with tarfile.open(package, "r:*") as archive:
                members = archive.getmembers()
                self._validate_archive_count_and_size(
                    len(members),
                    sum(member.size for member in members if member.isfile()),
                )
                seen: set[str] = set()
                for member in members:
                    name = self._archive_member_name(member.name, member.isdir())
                    if name is None:
                        continue
                    relative = normalize_relative_path(name)
                    if relative in seen:
                        raise SecurityError("压缩包包含重复路径：%s" % relative)
                    seen.add(relative)
                    target = os.path.join(destination, *relative.split("/"))
                    if member.isdir():
                        os.makedirs(target, exist_ok=True)
                        continue
                    if not member.isfile():
                        raise SecurityError("压缩包包含链接或设备文件：%s" % relative)
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    source = archive.extractfile(member)
                    if source is None:
                        raise SecurityError("无法读取压缩包文件：%s" % relative)
                    with source, open(target, "wb") as output:
                        shutil.copyfileobj(source, output, 128 * 1024)
                    os.chmod(target, 0o755 if member.mode & 0o111 else 0o644)
        except tarfile.TarError as exc:
            raise CatalogError("不支持的应用包格式：%s" % exc)

    def _extract_zip(self, package: str, destination: str) -> None:
        with zipfile.ZipFile(package) as archive:
            infos = archive.infolist()
            self._validate_archive_count_and_size(
                len(infos), sum(info.file_size for info in infos)
            )
            seen: set[str] = set()
            for info in infos:
                if info.flag_bits & 0x1:
                    raise SecurityError("不支持加密 ZIP")
                name = self._archive_member_name(info.filename, info.is_dir())
                if name is None:
                    continue
                relative = normalize_relative_path(name)
                if relative in seen:
                    raise SecurityError("压缩包包含重复路径：%s" % relative)
                seen.add(relative)
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise SecurityError("ZIP 包包含符号链接：%s" % relative)
                target = os.path.join(destination, *relative.split("/"))
                if info.is_dir():
                    os.makedirs(target, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with archive.open(info) as source, open(target, "wb") as output:
                    shutil.copyfileobj(source, output, 128 * 1024)
                os.chmod(target, 0o755 if mode & 0o111 else 0o644)

    @staticmethod
    def _validate_archive_count_and_size(file_count: int, total_size: int) -> None:
        if file_count > MAX_APP_FILES:
            raise SecurityError("应用包文件数量超过限制")
        if total_size > MAX_APP_BYTES:
            raise SecurityError("应用包解压大小超过限制")

    @staticmethod
    def _archive_member_name(name: str, is_directory: bool) -> str | None:
        while name.startswith("./"):
            name = name[2:]
        if is_directory:
            name = name.rstrip("/")
        if name in ("", "."):
            return None
        return name

    @staticmethod
    def _locate_archive_app(extracted: str) -> str:
        if os.path.isfile(os.path.join(extracted, "app.txt")):
            return extracted
        candidates = []
        for name in os.listdir(extracted):
            path = os.path.join(extracted, name)
            if os.path.isdir(path) and os.path.isfile(os.path.join(path, "app.txt")):
                candidates.append(path)
        if len(candidates) != 1:
            raise CatalogError("应用包必须在根目录或唯一一级目录包含 app.txt")
        return candidates[0]
