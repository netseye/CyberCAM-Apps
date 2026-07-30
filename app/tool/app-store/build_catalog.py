#!/usr/bin/env python3
"""Embed Git file manifests into catalog.json.

Run from the repository root whenever an official app directory changes. The
device can then install official apps from Raw GitHub without consuming REST
API quota.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone

from store_core import parse_app_txt


APP_DIR = os.path.dirname(os.path.abspath(__file__))
REPOSITORY_ROOT = os.path.abspath(os.path.join(APP_DIR, "..", "..", ".."))
CATALOG_PATH = os.path.join(APP_DIR, "catalog.json")


def git(*arguments: str) -> str:
    return subprocess.check_output(
        ["git", *arguments],
        cwd=REPOSITORY_ROOT,
        text=True,
    ).strip()


def blob_sha256(blob_sha: str) -> str:
    digest = hashlib.sha256()
    process = subprocess.Popen(
        ["git", "cat-file", "blob", blob_sha],
        cwd=REPOSITORY_ROOT,
        stdout=subprocess.PIPE,
    )
    assert process.stdout is not None
    while True:
        chunk = process.stdout.read(256 * 1024)
        if not chunk:
            break
        digest.update(chunk)
    if process.wait() != 0:
        raise SystemExit("cannot read Git blob: " + blob_sha)
    return digest.hexdigest()


def revision_for(path: str) -> str:
    if git("status", "--porcelain", "--", path):
        raise SystemExit(
            "%s has uncommitted changes; commit the app before rebuilding the catalog"
            % path
        )
    revision = git("log", "-1", "--format=%H", "HEAD", "--", path)
    if len(revision) != 40:
        raise SystemExit("cannot resolve app revision: " + path)
    return revision


def files_for(path: str, revision: str) -> tuple[str, list[dict]]:
    tree_sha = git("rev-parse", revision + ":" + path)
    output = git("ls-tree", "-r", "-l", revision, "--", path)
    files = []
    marker = path.rstrip("/") + "/"
    for line in output.splitlines():
        metadata, repository_path = line.split("\t", 1)
        mode, kind, sha, size_text = metadata.split()
        if kind != "blob" or mode not in ("100644", "100755"):
            raise SystemExit("unsupported Git entry: " + line)
        if not repository_path.startswith(marker):
            raise SystemExit("path outside app directory: " + repository_path)
        files.append(
            {
                "path": repository_path[len(marker) :],
                "mode": mode,
                "sha": sha,
                "sha256": blob_sha256(sha),
                "size": int(size_text),
            }
        )
    return tree_sha, files


def version_for(path: str, revision: str) -> str:
    app_txt = git("show", "%s:%s/app.txt" % (revision, path))
    return parse_app_txt(app_txt).get("version", "rolling")


def generated_at(revisions: set[str]) -> str:
    timestamps = [
        datetime.fromisoformat(git("show", "-s", "--format=%cI", revision))
        for revision in revisions
    ]
    latest = max(timestamps).astimezone(timezone.utc).replace(microsecond=0)
    return latest.isoformat().replace("+00:00", "Z")


def main() -> None:
    with open(CATALOG_PATH, encoding="utf-8") as handle:
        catalog = json.load(handle)
    revisions: set[str] = set()
    for app in catalog["apps"]:
        source = catalog["sources"][app["source"]]
        if source["kind"] != "github_tree":
            continue
        revision = revision_for(app["path"])
        tree_sha, files = files_for(app["path"], revision)
        app["version"] = version_for(app["path"], revision)
        app["revision"] = revision
        app["tree_sha"] = tree_sha
        app["files"] = files
        revisions.add(revision)
    if revisions:
        catalog["generated_at"] = generated_at(revisions)
    catalog.pop("generated_from", None)
    temporary = CATALOG_PATH + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(catalog, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temporary, CATALOG_PATH)


if __name__ == "__main__":
    main()
