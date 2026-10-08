"""Prepare pinned Electron dependencies in the existing Linux checkout."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from build_runtime import DESKTOP, download, extract


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.home() / ".cache/iot-exp-build")
    parser.add_argument("--launch", action="store_true", help="准备后启动桌面；需要图形会话")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    resources = root / "resources"
    if not (resources / "runtime-manifest.json").is_file():
        parser.error("请先运行prepare_linux_build.py，生成同一--root下的resources")
    link = DESKTOP / "build-resources"
    if link.is_symlink():
        if link.resolve() != resources:
            parser.error(f"已有build-resources指向{link.resolve()}，请先确认并调整该链接")
    elif link.exists():
        parser.error("build-resources已是实体目录；请确认其用途后再准备Linux资源链接")
    else:
        link.symlink_to(resources, target_is_directory=True)
    node_root = next((root / "node").glob("node-*"))
    environment = os.environ.copy()
    environment["PATH"] = str(node_root / "bin") + os.pathsep + environment.get("PATH", "")
    environment["npm_config_cache"] = str(root / "npm-cache")
    subprocess.run([str(node_root / "bin/npm"), "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
                   cwd=DESKTOP, env=environment, check=True)
    inputs = json.loads((DESKTOP / "runtime-inputs.lock.json").read_text())
    archive = download(inputs["platforms"]["linux-x64"]["electron"], root / "assets")
    target = DESKTOP / "node_modules/electron/dist"
    extract(archive, target)
    for name in ("electron", "chrome-sandbox", "chrome_crashpad_handler"):
        if (target / name).is_file():
            (target / name).chmod(0o755)
    (target.parent / "path.txt").write_text("electron")
    print(f"桌面源码：{DESKTOP}；运行资源：{resources}", flush=True)
    if args.launch:
        subprocess.run([str(target / "electron"), str(DESKTOP)], env=environment, check=True)


if __name__ == "__main__":
    main()
