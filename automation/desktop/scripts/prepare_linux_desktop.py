from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

from build_runtime import DESKTOP, download, extract


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.home() / ".cache/iot-exp-build")
    args = parser.parse_args()
    root = args.root
    desktop = root / "desktop"
    desktop.mkdir(parents=True, exist_ok=True)
    for name in ("package.json", "package-lock.json", "forge.config.cjs"):
        shutil.copyfile(DESKTOP / name, desktop / name)
    for directory in ("src", "installer", "tests", "scripts"):
        shutil.copytree(DESKTOP / directory, desktop / directory, dirs_exist_ok=True)
    resources = root / "resources"
    link = desktop / "build-resources"
    if not link.exists():
        link.symlink_to(resources, target_is_directory=True)
    node_root = next((root / "node").glob("node-*"))
    environment = os.environ.copy()
    environment["PATH"] = str(node_root / "bin") + os.pathsep + environment.get("PATH", "")
    environment["npm_config_cache"] = str(root / "npm-cache")
    if not (desktop / "node_modules").is_dir():
        subprocess.run([str(node_root / "bin/npm"), "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
                       cwd=desktop, env=environment, check=True)
    inputs = json.loads((DESKTOP / "runtime-inputs.lock.json").read_text())
    archive = download(inputs["platforms"]["linux-x64"]["electron"], root / "assets")
    target = desktop / "node_modules/electron/dist"
    extract(archive, target)
    for name in ("electron", "chrome-sandbox", "chrome_crashpad_handler"):
        if (target / name).is_file():
            (target / name).chmod(0o755)
    (target.parent / "path.txt").write_text("electron")
    environment.update({"IOT_EXP_DESKTOP_RESOURCES": str(resources),
                        "IOT_EXP_DESKTOP_STATE": str(root / "gui-smoke-state"),
                        "IOT_EXP_DESKTOP_WORKSPACE": str(root / "gui-smoke-workspace"),
                        "IOT_EXP_LOCK_ROOT": str(root / "gui-smoke-locks")})
    subprocess.run([str(target / "electron"), str(desktop), "--smoke-test"], env=environment, check=True)
    print((root / "gui-smoke-state/smoke-result.json").read_text())


if __name__ == "__main__":
    main()
