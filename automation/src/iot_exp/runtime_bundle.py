from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path

import yaml


def filesystem_path(path: Path) -> Path:
    """Use Windows extended paths for the deep, immutable dependency tree."""
    if os.name != "nt":
        return path
    value = os.path.abspath(path)
    if value.startswith("\\\\?\\"):
        return Path(value)
    if value.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + value[2:])
    return Path("\\\\?\\" + value)


def canonical_path(path: Path) -> Path:
    value = str(filesystem_path(path).resolve())
    if os.name == "nt":
        if value.startswith("\\\\?\\UNC\\"):
            value = "\\\\" + value[8:]
        elif value.startswith("\\\\?\\"):
            value = value[4:]
    return Path(value)


def contained(root: Path, relative: str) -> Path:
    if not relative or "\\" in relative or ":" in relative:
        raise ValueError("运行时路径无效")
    path = canonical_path(root / relative)
    if not path.is_relative_to(canonical_path(root)):
        raise ValueError("运行时路径越界")
    return path


def digest(path: Path) -> str:
    with filesystem_path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def load_manifest(resources: Path, *, verify_all: bool = False) -> dict:
    manifest = json.loads(filesystem_path(resources / "runtime-manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != 1 or manifest.get("protocol_version") != 1:
        raise ValueError("运行时清单/后台协议版本不兼容")
    target = "windows-x64" if os.name == "nt" else "linux-x64"
    if manifest["target"] != target:
        raise ValueError("运行时制品平台不匹配")
    expected = manifest["files"]
    selected = expected if verify_all else {
        path: checksum for path, checksum in expected.items()
        if path == manifest["executables"]["python"] or "/iot_exp/" in path
    }
    for relative, checksum in selected.items():
        path = contained(resources, relative)
        if not filesystem_path(path).is_file() or digest(path) != checksum:
            raise ValueError(f"内置组件缺失或校验失败：{relative}")
    return manifest


def prepare_appium_home(resources: Path, app_state: Path, manifest: dict) -> Path:
    root = resources / "runtime" / manifest["target"] / "appium"
    home = app_state / "appium" / manifest["bundle_id"]
    marker = filesystem_path(home / "bundle.json")
    if not marker.exists():
        filesystem_path(home).mkdir(parents=True, exist_ok=True)
        shutil.copytree(filesystem_path(root), filesystem_path(home), dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(".cache", "__pycache__"))
    prefix = root.relative_to(resources).as_posix() + "/"
    for relative, checksum in manifest["files"].items():
        if not relative.startswith(prefix) or "/.cache/" in relative:
            continue
        destination = contained(home, relative[len(prefix):])
        if not filesystem_path(destination).is_file() or digest(destination) != checksum:
            raise ValueError(f"私有Appium载荷损坏：{relative[len(prefix):]}")
    extension = json.loads(filesystem_path(home / "extension-template.json").read_text(encoding="utf-8"))
    if extension.get("schemaRev") != 4:
        raise ValueError("Appium扩展索引schema不兼容")
    for driver in extension["drivers"].values():
        path = contained(home, driver["installPath"])
        package = json.loads(filesystem_path(path / "package.json").read_text(encoding="utf-8"))
        if package["version"] != driver["version"]:
            raise ValueError("Appium driver版本不一致")
        driver["installPath"] = str(path)
    index = filesystem_path(home / "node_modules/.cache/appium/extensions.yaml")
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text(yaml.safe_dump(extension, allow_unicode=True), encoding="utf-8")
    marker.write_text(json.dumps({"bundle_id": manifest["bundle_id"], "schema": 1}), encoding="utf-8")
    return home


def private_environment(resources: Path, app_state: Path, manifest: dict) -> dict[str, str]:
    executables = {name: contained(resources, value) for name, value in manifest["executables"].items()}
    for component in ("node", "java"):
        relative = manifest["executables"][component]
        path = executables[component]
        if not filesystem_path(path).is_file() or digest(path) != manifest["files"].get(relative):
            executables[component] = app_state / ("unverified-" + component)
    home = app_state / "appium" / manifest["bundle_id"]
    status_path = filesystem_path(app_state / "runtime-status.json")
    status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.is_file() else {}
    appium_entry = home / "node_modules/appium/index.js" if filesystem_path(home / "bundle.json").is_file() and status.get("prepared") and status.get("bundle_id") == manifest["bundle_id"] else home / "not-ready"
    java_home = executables["java"].parent.parent
    return {
        "IOT_EXP_NODE_EXECUTABLE": str(executables["node"]),
        "IOT_EXP_JAVA_EXECUTABLE": str(executables["java"]),
        "IOT_EXP_APPIUM_ENTRY": str(appium_entry),
        "IOT_EXP_APPIUM_ERROR": str(status.get("error", ""))[:2000] if not status.get("prepared") else "",
        "JAVA_HOME": str(java_home), "APPIUM_HOME": str(home),
        "PYTHONDONTWRITEBYTECODE": "1",
        "NODE_OPTIONS": "", "NODE_PATH": "",
        "JAVA_TOOL_OPTIONS": "", "_JAVA_OPTIONS": "", "JDK_JAVA_OPTIONS": "",
        "PATH": os.pathsep.join((str(executables["node"].parent), str(executables["java"].parent),
                                 os.environ.get("PATH", ""))),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", type=Path, required=True)
    parser.add_argument("--app-state", type=Path, required=True)
    args = parser.parse_args()
    manifest = load_manifest(args.resources)
    try:
        prepare_appium_home(args.resources, args.app_state, manifest)
        status = {"prepared": True, "bundle_id": manifest["bundle_id"]}
    except (OSError, ValueError) as error:
        filesystem_path(args.app_state / "appium" / manifest["bundle_id"] / "bundle.json").unlink(missing_ok=True)
        if isinstance(error, shutil.Error) and error.args and isinstance(error.args[0], list):
            failures = error.args[0]
            detail = f"Appium复制失败（{len(failures)}个文件）：" + "; ".join(str(item) for item in failures[:2])
        else:
            detail = str(error)
        status = {"prepared": False, "bundle_id": manifest["bundle_id"], "error": detail[:2000]}
    filesystem_path(args.app_state).mkdir(parents=True, exist_ok=True)
    filesystem_path(args.app_state / "runtime-status.json").write_text(json.dumps(status), encoding="utf-8")
    print(json.dumps(status))


if __name__ == "__main__":
    main()
