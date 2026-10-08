"""Keep license texts/build metadata from the matching full upstream Python asset."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

from build_runtime import DESKTOP, download
from lock_runtime_inputs import fetch


def collect(resources: Path, cache: Path) -> dict:
    manifest = json.loads((resources / "runtime-manifest.json").read_text())
    inputs_path = DESKTOP / "runtime-inputs.lock.json"
    inputs = json.loads(inputs_path.read_text())
    target = manifest["target"]
    python = inputs["platforms"][target]["python"]
    triple = "x86_64-pc-windows-msvc-pgo" if target == "windows-x64" else "x86_64-unknown-linux-gnu-pgo+lto"
    name = f"cpython-{python['version']}+{python['release']}-{triple}-full.tar.zst"
    release = fetch(f"https://api.github.com/repos/astral-sh/python-build-standalone/releases/tags/{python['release']}")
    vendor = next(item for item in release["assets"] if item["name"] == name)
    item = {"url": vendor["browser_download_url"], "sha256": vendor["digest"].removeprefix("sha256:"),
            "version": python["version"], "release": python["release"]}
    archive = download(item, cache)
    destination = resources / "licenses/python-upstream"
    destination.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        subprocess.run(["tar.exe", "-xf", str(archive), "-C", str(destination),
                        "python/PYTHON.json", "python/licenses"], check=True)
    else:
        import zstandard
        with (archive.open("rb") as handle,
              zstandard.ZstdDecompressor().stream_reader(handle) as decoded,
              tarfile.open(fileobj=decoded, mode="r|") as source):
            for member in source:
                if member.name == "python/PYTHON.json" or member.name.startswith("python/licenses/"):
                    source.extract(member, destination, filter="data")
    info = destination / "python/PYTHON.json"
    if not info.is_file() or not any((destination / "python/licenses").iterdir()):
        raise ValueError("上游Python许可/构建元数据未完整提取")
    shutil.copyfile(DESKTOP / "THIRD_PARTY_NOTICES.md", resources / "licenses/THIRD_PARTY_NOTICES.md")
    (destination / "source.json").write_text(json.dumps(item, indent=2), encoding="utf-8")
    inputs.setdefault("license_assets", {})[target] = item
    inputs_path.write_text(json.dumps(inputs, indent=2) + "\n")
    return {"target": target, "license_files": sum(path.is_file() for path in destination.rglob("*"))}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", type=Path, default=DESKTOP / "build-resources")
    parser.add_argument("--cache", type=Path, default=DESKTOP.parent / "runs/runtime-assets")
    args = parser.parse_args()
    print(json.dumps(collect(args.resources, args.cache)))
