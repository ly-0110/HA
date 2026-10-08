from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import yaml

from .models import ExperimentConfig, RuntimeConfig
from .paths import PathLayout, current_layout


class ConfigError(ValueError):
    """Raised when experiment or runtime configuration is invalid."""


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"configuration file does not exist: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"configuration root must be a mapping: {path}")
    return data


def _merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load_configuration(experiment_path: Path, runtime_path: Path) -> tuple[ExperimentConfig, RuntimeConfig]:
    experiment_data = _read_yaml(experiment_path)
    runtime_data = _read_yaml(runtime_path)
    try:
        experiment = ExperimentConfig.model_validate(experiment_data)
        runtime = RuntimeConfig.model_validate(runtime_data)
    except Exception as exc:  # pydantic exposes a rich validation error; wrap it for CLI users.
        raise ConfigError(str(exc)) from exc
    return experiment, runtime


def resolve_path(path: Path, *, base: Path) -> Path:
    return path if path.is_absolute() else (base / path).resolve()


def apply_runtime_paths(runtime: RuntimeConfig, *, config_dir: Path,
                        layout: PathLayout | None = None) -> RuntimeConfig:
    """Resolve relative output paths while keeping executable names discoverable."""
    data = runtime.model_dump()
    layout = layout or current_layout(config_dir)
    data["output_root"] = resolve_path(runtime.output_root, base=layout.output_base)
    if runtime.android_sdk_root is not None:
        data["android_sdk_root"] = resolve_path(runtime.android_sdk_root, base=layout.output_base)
    if layout.desktop and (layout.resources_root / "runtime-manifest.json").is_file():
        preferences = layout.app_state_root / "tools.json"
        tools = json.loads(preferences.read_text(encoding="utf-8")) if preferences.is_file() else {}
        sdk = tools.get("sdk") or data.get("android_sdk_root")
        data["android_sdk_root"] = Path(sdk) if sdk else layout.app_state_root / "unconfigured-sdk"
        data["adb_executable"] = str(Path(sdk) / "platform-tools" / ("adb.exe" if os.name == "nt" else "adb")) if sdk else str(layout.app_state_root / "unconfigured-sdk/adb")
        configured = tools.get("dumpcap") or runtime.dumpcap_executable
        data["dumpcap_executable"] = configured if Path(configured).is_absolute() else str(layout.app_state_root / "unconfigured-capture/dumpcap")
        manifest_path = layout.resources_root / "runtime-manifest.json"
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            data["tool_provenance"] = {"bundle_id": manifest["bundle_id"], "target": manifest["target"],
                                       "versions": manifest.get("actual_versions") or {name: item["version"] for name, item in manifest["inputs"].items()},
                                       "sdk": str(sdk) if sdk else None, "dumpcap": data["dumpcap_executable"]}
    return RuntimeConfig.model_validate(data)


def is_placeholder(value: str | None) -> bool:
    if not value:
        return True
    normalized = value.strip().lower()
    return "replace_with" in normalized or normalized in {"unknown", "todo", "changeme"}


def configuration_provenance(path: Path, layout: PathLayout) -> dict:
    builtin = layout.resources_root / "templates/experiment" / path.name
    if not builtin.is_file():
        builtin = layout.resources_root / "experiment" / path.name
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    original = hashlib.sha256(builtin.read_bytes()).hexdigest() if builtin.is_file() else None
    return {"template_id": path.stem, "configured_sha256": checksum,
            "builtin_sha256": original, "user_override": original is None or original != checksum}


def redact_environment() -> dict[str, str]:
    """Return only non-sensitive runtime facts for diagnostics."""
    allow = {
        "OS", "PROCESSOR_ARCHITECTURE", "PYTHON_VERSION", "JAVA_HOME",
        "ANDROID_HOME", "ANDROID_SDK_ROOT",
    }
    return {key: value for key, value in os.environ.items() if key in allow}
