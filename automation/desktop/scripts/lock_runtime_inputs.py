"""Freeze public vendor assets before building the private desktop runtime."""
from __future__ import annotations

import argparse
import json
import re
import ssl
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen


def fetch(url: str, *, as_json: bool = True):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_default_certs()
    request = Request(url, headers={"User-Agent": "iot-exp-runtime-builder/1"})
    with urlopen(request, context=context, timeout=45) as response:
        text = response.read().decode("utf-8")
    return json.loads(text) if as_json else text


def checksum(text: str, name: str) -> str:
    for line in text.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[1].lstrip("*") == name:
            value = fields[0].lower()
            if re.fullmatch(r"[0-9a-f]{64}", value):
                return value
    raise ValueError(f"vendor checksum not found: {name}")


def asset(url: str, digest: str, **metadata) -> dict:
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError(f"invalid sha256: {url}")
    return {"url": url, "sha256": digest, **metadata}


def collect() -> dict:
    node = next(item for item in fetch("https://nodejs.org/dist/index.json")
                if item["version"].startswith("v24.") and item["lts"])
    node_base = f"https://nodejs.org/dist/{node['version']}"
    node_checksums = fetch(f"{node_base}/SHASUMS256.txt", as_json=False)
    npm = {}
    for name in ("electron", "@electron-forge/cli", "appium", "appium-uiautomator2-driver"):
        version = {"appium": "3.7.0", "appium-uiautomator2-driver": "6.9.3"}.get(name, "latest")
        data = fetch(f"https://registry.npmjs.org/{name.replace('/', '%2f')}/{version}")
        npm[name] = {"version": data["version"], "url": data["dist"]["tarball"],
                     "integrity": data["dist"]["integrity"], "license": data.get("license")}
    electron_version = npm["electron"]["version"]
    electron_base = f"https://github.com/electron/electron/releases/download/v{electron_version}"
    electron_sums = fetch(f"{electron_base}/SHASUMS256.txt", as_json=False)
    python_release = fetch("https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest")
    result = {
        "schema": "iot_exp_runtime_inputs_v1",
        "locked_at": datetime.now(timezone.utc).isoformat(),
        "source": "public vendor metadata; asset hashes verified during bundle build",
        "npm": npm, "platforms": {},
    }
    for target, node_os, python_triple, java_os, electron_os in (
        ("windows-x64", "win", "x86_64-pc-windows-msvc", "windows", "win32"),
        ("linux-x64", "linux", "x86_64-unknown-linux-gnu", "linux", "linux"),
    ):
        node_name = f"node-{node['version']}-{node_os}-x64.{'zip' if node_os == 'win' else 'tar.xz'}"
        choices = [item for item in python_release["assets"]
                   if re.match(r"cpython-3\.13\.\d+\+", item["name"])
                   and item["name"].endswith(f"-{python_triple}-install_only_stripped.tar.gz")]
        if len(choices) != 1:
            raise ValueError(f"expected one CPython 3.13 asset for {target}: {len(choices)}")
        python = choices[0]
        digest = python.get("digest", "")
        if not digest.startswith("sha256:"):
            digest = "sha256:" + fetch(python["browser_download_url"] + ".sha256", as_json=False).split()[0]
        java = fetch(f"https://api.adoptium.net/v3/assets/latest/17/hotspot?architecture=x64&image_type=jdk&os={java_os}")
        selected = next(item for item in java if item["binary"]["image_type"] == "jdk")
        package = selected["binary"]["package"]
        electron_name = f"electron-v{electron_version}-{electron_os}-x64.zip"
        result["platforms"][target] = {
            "python": asset(python["browser_download_url"], digest.removeprefix("sha256:"),
                            version=re.match(r"cpython-([^+]+)", python["name"])[1],
                            release=python_release["tag_name"], size=python["size"]),
            "node": asset(f"{node_base}/{node_name}", checksum(node_checksums, node_name),
                          version=node["version"].removeprefix("v")),
            "java": asset(package["link"], package["checksum"],
                          version=selected["version"]["semver"], size=package["size"]),
            "electron": asset(f"{electron_base}/{electron_name}",
                              checksum(electron_sums, electron_name), version=electron_version),
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).parents[1] / "runtime-inputs.lock.json")
    args = parser.parse_args()
    result = collect()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "npm": result["npm"],
                      "targets": list(result["platforms"])}))


if __name__ == "__main__":
    main()
