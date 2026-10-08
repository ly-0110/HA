"""Generate a concrete component inventory from the staged distribution."""
from __future__ import annotations

import argparse
import json
import shutil
from email.parser import Parser
from pathlib import Path

DESKTOP = Path(__file__).resolve().parents[1]


def generate(resources: Path, electron_dist: Path | None = None) -> dict:
    manifest = json.loads((resources / "runtime-manifest.json").read_text())
    target = resources / "runtime" / manifest["target"]
    licenses = resources / "licenses"
    licenses.mkdir(parents=True, exist_ok=True)
    components = []
    electron_dist = electron_dist or DESKTOP / "node_modules/electron/dist"
    electron_version = (electron_dist / "version").read_text(encoding="utf-8").strip()
    if electron_version.lstrip("v") != manifest["inputs"]["electron"]["version"]:
        raise ValueError("Electron许可来源版本与运行时清单不一致")
    electron_materials = licenses / "electron"
    electron_materials.mkdir(parents=True, exist_ok=True)
    for name in ("LICENSE", "LICENSES.chromium.html"):
        source = electron_dist / name
        if not source.is_file():
            raise ValueError(f"Electron上游许可材料缺失：{name}")
        shutil.copyfile(source, electron_materials / name)
    frontend = json.loads((DESKTOP.parent / "package-lock.json").read_text())
    for name in ("react", "react-dom", "scheduler"):
        package = frontend["packages"]["node_modules/" + name]
        source = DESKTOP.parent / "node_modules" / name / "LICENSE"
        if not source.is_file():
            raise ValueError(f"前端许可缺失：{name}")
        destination = licenses / "frontend" / name
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination / "LICENSE")
        components.append({"type":"library", "name":name,"version":package["version"],
                           "licenses":[{"license":{"name":package.get("license", "MIT")}}]})
    for name, item in manifest["inputs"].items():
        components.append({"type": "application", "name": name, "version": item["version"],
                           "externalReferences": [{"type": "distribution", "url": item["url"]}],
                           "hashes": [{"alg": "SHA-256", "content": item["sha256"]}]})
    for metadata in (target / "python").rglob("*.dist-info/METADATA"):
        fields = Parser().parsestr(metadata.read_text(encoding="utf-8", errors="replace"))
        components.append({"type": "library", "name": fields["Name"], "version": fields["Version"],
                           "licenses": [{"license": {"name": fields.get("License-Expression") or fields.get("License") or "See package license files"}}]})
        for directory in (metadata.parent / "licenses",):
            if directory.is_dir():
                shutil.copytree(directory, licenses / "python-packages" / metadata.parent.name, dirs_exist_ok=True)
    npm = json.loads((target / "appium/package-lock.json").read_text())
    npm_inventory = []
    absent_optional_packages = []
    for location, package in npm["packages"].items():
        if not location or not package.get("version"):
            continue
        directory = target / "appium" / location
        package_file = directory / "package.json"
        if not package_file.is_file():
            absent_optional_packages.append(location)
            continue
        installed = json.loads(package_file.read_text(encoding="utf-8"))
        if installed.get("version") != package["version"]:
            raise ValueError(f"实际npm载荷与锁版本不符：{location}")
        material_files = [
            path.relative_to(resources).as_posix()
            for path in directory.iterdir()
            if path.is_file() and any(
                token in path.name.lower() for token in ("license", "licence", "copying", "notice")
            )
        ]
        npm_inventory.append({
            "location": location,
            "name": installed.get("name"),
            "version": installed.get("version"),
            "declared_license": installed.get("license"),
            "material_files": material_files,
            "readme_present": any(
                path.is_file() and path.name.lower().startswith("readme")
                for path in directory.iterdir()
            ),
        })
        components.append({"type": "library", "name": package.get("name") or location.split("node_modules/")[-1],
                           "version": package["version"],
                           "licenses": [{"license": {"name": str(package.get("license", "See package license files"))}}],
                           "externalReferences": [{"type": "distribution", "url": package["resolved"]}] if package.get("resolved") else [],
                           "properties": [{"name": "npm-integrity", "value": package.get("integrity", "")},
                                          {"name": "installed-location", "value": location}]})
    for component, relative in (("python", "LICENSE.txt"), ("node", "LICENSE")):
        path = target / component / relative
        if component == "python" and not path.is_file():
            path = licenses / "python-upstream/python/licenses/LICENSE.cpython.txt"
        if not path.is_file():
            raise ValueError(f"缺少上游许可文件：{component}")
        shutil.copyfile(path, licenses / (component + "-LICENSE.txt"))
    java_legal = target / "java/legal"
    if not java_legal.is_dir():
        raise ValueError("JDK legal目录缺失")
    shutil.copytree(java_legal, licenses / "java", dirs_exist_ok=True)
    java_notice = target / "java/NOTICE"
    if java_notice.is_file():
        shutil.copyfile(java_notice, licenses / "java-NOTICE.txt")
    notices = DESKTOP / "THIRD_PARTY_NOTICES.md"
    if notices.exists():
        shutil.copyfile(notices, licenses / "THIRD_PARTY_NOTICES.md")
    bom = {"bomFormat": "CycloneDX", "specVersion": "1.6", "version": 1,
           "metadata": {"component": {"type": "application", "name": "IoTExperimentWorkbench", "version": manifest["app_version"]}},
           "components": components,
           "properties": [{"name": "target", "value": manifest["target"]},
                          {"name": "excluded-system-components", "value": "Google SDK, Npcap, USB drivers, system Dumpcap"}]}
    (resources / "sbom.json").write_text(json.dumps(bom, indent=2), encoding="utf-8")
    audit = {
        "schema": "iot_exp_license_inventory_v1",
        "target": manifest["target"],
        "bundle_id": manifest["bundle_id"],
        "scope": "actual installed npm payload; package-level material discovery is not a legal clearance",
        "npm_lock_entries_not_installed": absent_optional_packages,
        "npm_packages": npm_inventory,
        "license_clearance_complete": False,
    }
    (licenses / "component-materials.json").write_text(
        json.dumps(audit, indent=2), encoding="utf-8"
    )
    return {"components": len(components), "target": manifest["target"],
            "npm_installed": len(npm_inventory),
            "npm_lock_entries_not_installed": len(absent_optional_packages)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", type=Path, default=DESKTOP / "build-resources")
    parser.add_argument("--electron-dist", type=Path,
                        help="目标平台同版本Electron发行目录；默认使用当前desktop/node_modules/electron/dist")
    args = parser.parse_args()
    print(json.dumps(generate(args.resources, args.electron_dist)))
