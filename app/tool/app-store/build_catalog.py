#!/usr/bin/env python3
"""Embed Git file manifests into catalog.json.

Run from the repository root whenever an official app directory changes. The
device can then install official apps from Raw GitHub without consuming REST
API quota.
"""

from __future__ import annotations

import json
import os
import subprocess


APP_DIR = os.path.dirname(os.path.abspath(__file__))
REPOSITORY_ROOT = os.path.abspath(os.path.join(APP_DIR, "..", "..", ".."))
CATALOG_PATH = os.path.join(APP_DIR, "catalog.json")


def git(*arguments: str) -> str:
    return subprocess.check_output(
        ["git", *arguments],
        cwd=REPOSITORY_ROOT,
        text=True,
    ).strip()


def files_for(path: str) -> tuple[str, list[dict]]:
    tree_sha = git("rev-parse", "HEAD:" + path)
    output = git("ls-tree", "-r", "-l", "HEAD", "--", path)
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
                "size": int(size_text),
            }
        )
    return tree_sha, files


def main() -> None:
    with open(CATALOG_PATH, encoding="utf-8") as handle:
        catalog = json.load(handle)
    for app in catalog["apps"]:
        source = catalog["sources"][app["source"]]
        if source["kind"] != "github_tree":
            continue
        tree_sha, files = files_for(app["path"])
        app["tree_sha"] = tree_sha
        app["files"] = files
    catalog.pop("generated_from", None)
    temporary = CATALOG_PATH + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(catalog, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temporary, CATALOG_PATH)


if __name__ == "__main__":
    main()
