"""Bootstrap the pinned build-only uv without changing global PATH."""
import json
import subprocess

from build_runtime import AUTOMATION, DESKTOP, download, extract


def main():
    item = {"version": "0.12.12", "url": "https://github.com/astral-sh/uv/releases/download/0.12.12/uv-x86_64-pc-windows-msvc.zip",
            "sha256": "3d54912924c36e862c14f427d04f2ed70a99e8001d1c30caa101f6d5711626d5"}
    target = AUTOMATION / "runs/build-tools/uv-windows"
    extract(download(item, AUTOMATION / "runs/runtime-assets"), target)
    lock_path = DESKTOP / "runtime-inputs.lock.json"
    lock = json.loads(lock_path.read_text())
    lock.setdefault("build_tools", {})["windows_uv"] = item
    lock_path.write_text(json.dumps(lock, indent=2) + "\n")
    uv = next(target.rglob("uv.exe"))
    subprocess.run([str(uv), "--version"], check=True)
    print(uv)


if __name__ == "__main__":
    main()
