"""Prepare build-only Linux uv/Node from pinned archives for WSL/native Linux."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from build_runtime import AUTOMATION, DESKTOP, download, extract


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path.home() / ".cache/iot-exp-build")
    args = parser.parse_args()
    lock_path = DESKTOP / "runtime-inputs.lock.json"
    lock = json.loads(lock_path.read_text())
    uv = {"version": "0.12.12", "url": "https://github.com/astral-sh/uv/releases/download/0.12.12/uv-x86_64-unknown-linux-gnu.tar.gz",
          "sha256": "ab9b309d4586403f024e100abaceb396616e178a553e2500c36087d180f09509"}
    lock.setdefault("build_tools", {})["linux_uv"] = uv
    lock_path.write_text(json.dumps(lock, indent=2) + "\n")
    args.output.mkdir(parents=True, exist_ok=True)
    cache = AUTOMATION / "runs/runtime-assets"
    for name, item in (("uv", uv), ("node", lock["platforms"]["linux-x64"]["node"])):
        extract(download(item, cache), args.output / name)
    uv_binary = next((args.output / "uv").glob("*/uv"))
    node_root = next((args.output / "node").glob("node-*"))
    environment = os.environ.copy()
    environment["PATH"] = str(node_root / "bin") + os.pathsep + environment.get("PATH", "")
    environment["UV_CACHE_DIR"] = str(AUTOMATION / ".uv-cache-linux")
    command = [str(uv_binary), "run", "--no-project", "--python", "3.13", "--with", "PyYAML==6.0.3",
               "python", str(DESKTOP / "scripts/build_runtime.py"), "--uv", str(uv_binary),
               "--npm", str(node_root / "bin/npm"), "--output", str(args.output / "resources"),
               "--cache", str(args.output / "assets"), "--skip-electron"]
    subprocess.run(command, env=environment, check=True)


if __name__ == "__main__":
    main()
