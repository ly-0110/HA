"""Build relocatable runtime resources; all downloads are build-time and hash pinned."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import ssl
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen

DESKTOP = Path(__file__).resolve().parents[1]
AUTOMATION = DESKTOP.parent


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def download(item: dict, cache: Path) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    name = unquote(Path(urlsplit(item["url"]).path).name)
    path = cache / (item["sha256"][:12] + "-" + name)
    if not path.is_file() or sha256(path) != item["sha256"]:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_default_certs()
        print(f"下载 {name}", flush=True)
        with urlopen(Request(item["url"], headers={"User-Agent": "iot-exp-builder/1"}),
                     context=context, timeout=60) as response, path.open("wb") as output:
            shutil.copyfileobj(response, output)
    if sha256(path) != item["sha256"]:
        raise ValueError(f"资产SHA-256不匹配：{name}")
    return path


def extract(archive: Path, target: Path, *, node_runtime: bool = False) -> None:
    target.mkdir(parents=True, exist_ok=True)
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zipped:
            for item in zipped.infolist():
                if not (target / item.filename).resolve().is_relative_to(target.resolve()):
                    raise ValueError("非法ZIP路径")
            for item in zipped.infolist():
                if node_runtime and "/node_modules/" in item.filename:
                    continue
                zipped.extract(item, target)
    else:
        with tarfile.open(archive) as tar:
            members = [item for item in tar if not node_runtime or "/node_modules/" not in item.name]
            tar.extractall(target, members=members, filter="data")


def run(command: list[str], *, cwd: Path, environment: dict) -> None:
    print("构建步骤：", Path(command[0]).name, *command[1:4], flush=True)
    subprocess.run(command, cwd=cwd, env=environment, check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DESKTOP / "build-resources")
    parser.add_argument("--uv", default=shutil.which("uv") or os.environ.get("IOT_EXP_UV"))
    parser.add_argument("--npm", default="npm.cmd" if os.name == "nt" else "npm")
    parser.add_argument("--skip-electron", action="store_true")
    parser.add_argument("--cache", type=Path, default=AUTOMATION / "runs/runtime-assets")
    parser.add_argument("--backend-only", action="store_true", help="刷新已有开发制品的wheel/前端及散列")
    args = parser.parse_args()
    if not args.uv:
        parser.error("构建环境需要uv；用--uv指定构建工具路径（终端运行不需要uv）")
    target = "windows-x64" if os.name == "nt" else "linux-x64"
    lock = json.loads((DESKTOP / "runtime-inputs.lock.json").read_text())
    inputs = lock["platforms"][target]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    launcher = "iot-exp.cmd" if os.name == "nt" else "iot-exp.sh"
    shutil.copyfile(DESKTOP / "scripts" / launcher, output / launcher)
    if os.name != "nt":
        (output / launcher).chmod(0o755)
    cache = args.cache.resolve()
    runtime = output / "runtime" / target
    runtime.mkdir(parents=True, exist_ok=True)
    for component in ("python", "node", "java"):
        destination = runtime / component
        if not (destination / ".vendor-sha256").is_file():
            archive = download(inputs[component], cache)
            unpacked = cache / "unpacked" / inputs[component]["sha256"][:12]
            extract(archive, unpacked, node_runtime=component == "node")
            directories = [item for item in unpacked.iterdir() if item.is_dir()]
            if len(directories) != 1:
                raise ValueError(f"发行资产根不唯一：{component}")
            if component == "node":
                destination.mkdir(parents=True, exist_ok=True)
                binary = "node.exe" if os.name == "nt" else "bin/node"
                for relative in (binary, "LICENSE"):
                    target_file = destination / relative
                    target_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(directories[0] / relative, target_file)
                for extra in ("npm", "npm.cmd", "npm.ps1", "npx", "npx.cmd", "npx.ps1", "install_tools.bat", "nodevars.bat", "corepack", "corepack.cmd"):
                    (destination / extra).unlink(missing_ok=True)
            else:
                shutil.copytree(directories[0], destination, dirs_exist_ok=True)
            (destination / ".vendor-sha256").write_text(inputs[component]["sha256"])
    python = runtime / "python" / ("python.exe" if os.name == "nt" else "bin/python3")
    node = runtime / "node" / ("node.exe" if os.name == "nt" else "bin/node")
    java = runtime / "java" / "bin" / ("java.exe" if os.name == "nt" else "java")
    for extra in ("npm", "npm.cmd", "npm.ps1", "npx", "npx.cmd", "npx.ps1", "install_tools.bat", "nodevars.bat", "corepack", "corepack.cmd"):
        (runtime / "node" / extra).unlink(missing_ok=True)
    environment = os.environ.copy()
    environment.pop("SSLKEYLOGFILE", None)
    environment.pop("PYTHONPATH", None)
    environment["UV_CACHE_DIR"] = str(cache / "uv-cache")
    environment["UV_PYTHON_INSTALL_DIR"] = str(AUTOMATION / "runs/uv-python")
    environment["npm_config_cache"] = str(AUTOMATION / ".npm-cache")
    wheels = AUTOMATION / "runs/desktop-wheels" / target
    wheels.mkdir(parents=True, exist_ok=True)
    run([args.uv, "build", "--wheel", "--python", str(sys.executable), "--out-dir", str(wheels)],
        cwd=AUTOMATION, environment=environment)
    requirements = wheels / "requirements.txt"
    run([args.uv, "export", "--quiet", "--no-dev", "--no-emit-project", "--no-hashes",
         "--output-file", str(requirements)], cwd=AUTOMATION, environment=environment)
    run([args.uv, "pip", "install", "--python", str(python), "--reinstall",
         "-r", str(requirements), str(next(wheels.glob("iot_experiment_automation-*.whl")))],
        cwd=AUTOMATION, environment=environment)
    for direct_url in (runtime / "python").rglob("direct_url.json"):
        direct_url.unlink()
    python_root = (runtime / "python").resolve()
    for bytecode in python_root.rglob("__pycache__"):
        if not bytecode.resolve().is_relative_to(python_root) or bytecode.is_symlink():
            raise ValueError("Python缓存路径越界，拒绝清理")
        if bytecode.is_dir():
            shutil.rmtree(bytecode)
    wheel = next(wheels.glob("iot_experiment_automation-*.whl"))
    appium_inputs = DESKTOP / "runtime-appium"
    locks = {"runtime_inputs": sha256(DESKTOP / "runtime-inputs.lock.json"),
             "python": sha256(AUTOMATION / "uv.lock"),
             "appium": sha256(appium_inputs / "package-lock.json"),
             "desktop": sha256(DESKTOP / "package-lock.json")}
    core_wheel = {"name": wheel.name, "sha256": sha256(wheel)}
    release = dict(line.split("=", 1) for line in (runtime / "java/release").read_text().splitlines() if "=" in line)
    actual_versions = {"python":inputs["python"]["version"], "node":inputs["node"]["version"],
                       "java":release["JAVA_RUNTIME_VERSION"].strip('"'),
                       "appium":"3.7.0", "uiautomator2":"6.9.3", "electron":inputs["electron"]["version"]}
    bundle_id = hashlib.sha256(json.dumps({"locks":locks,"wheel":core_wheel}, sort_keys=True).encode()).hexdigest()[:20]
    if args.backend_only:
        manifest_path = output / "runtime-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest.update(bundle_id=bundle_id, dependency_locks=locks, core_wheel=core_wheel, actual_versions=actual_versions,
                        app_version=json.loads((DESKTOP / "package.json").read_text(encoding="utf-8"))["version"])
        prefix = f"runtime/{target}/python/"
        manifest["files"] = {name: value for name, value in manifest["files"].items() if not name.startswith(prefix)}
        manifest["files"] = {name: value for name, value in manifest["files"].items() if (output / name).is_file()}
        for path in (runtime / "python").rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts and not path.is_symlink():
                manifest["files"][path.relative_to(output).as_posix()] = sha256(path)
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        shutil.copytree(AUTOMATION / "web/dist", output / "web", dirs_exist_ok=True)
        print(json.dumps({"refreshed": True, "output": str(output)}))
        return
    appium_source = cache / "appium-build" / target
    appium_source.mkdir(parents=True, exist_ok=True)
    for name in ("package.json", "package-lock.json"):
        shutil.copyfile(appium_inputs / name, appium_source / name)
    npm_marker = appium_source / ".lock-sha256"
    if not (appium_source / "node_modules").is_dir() or not npm_marker.is_file() or npm_marker.read_text() != locks["appium"]:
        full_node = cache / "build-node" / inputs["node"]["sha256"][:12]
        npm_pattern = "*/node_modules/npm/bin/npm-cli.js" if os.name == "nt" else "*/lib/node_modules/npm/bin/npm-cli.js"
        npm_cli = next(full_node.glob(npm_pattern), None)
        if npm_cli is None:
            extract(download(inputs["node"], cache), full_node)
            npm_cli = next(full_node.glob(npm_pattern))
        environment["PATH"] = str(node.parent) + os.pathsep + str(java.parent) + os.pathsep + environment.get("PATH", "")
        environment.update(NODE_OPTIONS="", NODE_PATH="", APPIUM_HOME=str(appium_source))
        run([str(node), str(npm_cli), "ci", "--omit=dev", "--no-audit", "--no-fund"],
            cwd=appium_source, environment=environment)
        npm_marker.write_text(locks["appium"])
    appium = runtime / "appium"
    shutil.copytree(appium_source, appium, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(".cache", ".git", "npm-debug.log"))
    environment.pop("APPIUM_HOME", None)
    run([str(node), str(appium / "node_modules/appium/index.js"), "driver", "list", "--installed", "--json"],
        cwd=appium, environment=environment)
    import yaml
    index = appium / "node_modules/.cache/appium/extensions.yaml"
    template = yaml.safe_load(index.read_text(encoding="utf-8"))
    for driver in template["drivers"].values():
        driver["installPath"] = Path(driver["installPath"]).relative_to(appium).as_posix()
    (appium / "extension-template.json").write_text(json.dumps(template, indent=2), encoding="utf-8")
    index.unlink()
    for direct_url in (runtime / "python").rglob("direct_url.json"):
        direct_url.unlink()
    shutil.copytree(AUTOMATION / "web/dist", output / "web", dirs_exist_ok=True)
    for kind in ("experiment", "runtime"):
        destination = output / "templates" / kind
        destination.mkdir(parents=True, exist_ok=True)
        for path in (AUTOMATION / kind).glob("*.yaml"):
            shutil.copyfile(path, destination / path.name)
    if not args.skip_electron:
        archive = download(inputs["electron"], cache)
        electron = DESKTOP / "node_modules/electron/dist"
        extract(archive, electron)
        (electron.parent / "path.txt").write_text("electron.exe" if os.name == "nt" else "electron")
        if os.name != "nt":
            (electron / "electron").chmod(0o755)
    files = {}
    for path in runtime.rglob("*"):
        if path.is_file() and "__pycache__" not in path.relative_to(runtime).parts and ".cache" not in path.relative_to(runtime).parts and not path.is_symlink():
            files[path.relative_to(output).as_posix()] = sha256(path)
    manifest = {
        "schema": 1, "bundle_id": bundle_id, "target": target, "protocol_version": 1,
        "app_version": json.loads((DESKTOP / "package.json").read_text(encoding="utf-8"))["version"], "inputs": inputs,
        "dependency_locks": locks, "core_wheel": core_wheel,
        "actual_versions": actual_versions,
        "executables": {"python": python.relative_to(output).as_posix(),
                        "node": node.relative_to(output).as_posix(),
                        "java": java.relative_to(output).as_posix(),
                        "appium": (appium / "node_modules/appium/index.js").relative_to(output).as_posix()},
        "files": files,
    }
    (output / "runtime-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "bundle_id": bundle_id, "files": len(files)}), flush=True)


if __name__ == "__main__":
    main()
