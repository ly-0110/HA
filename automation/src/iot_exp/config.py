from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from .models import ExperimentConfig, RuntimeConfig


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


def apply_runtime_paths(runtime: RuntimeConfig, *, config_dir: Path) -> RuntimeConfig:
    """Resolve relative output paths while keeping executable names discoverable."""
    data = runtime.model_dump()
    data["output_root"] = resolve_path(runtime.output_root, base=config_dir)
    if runtime.android_sdk_root is not None:
        data["android_sdk_root"] = resolve_path(runtime.android_sdk_root, base=config_dir)
    return RuntimeConfig.model_validate(data)


def is_placeholder(value: str | None) -> bool:
    if not value:
        return True
    normalized = value.strip().lower()
    return normalized.startswith("replace_") or normalized in {"unknown", "todo", "changeme"}


def redact_environment() -> dict[str, str]:
    """Return only non-sensitive runtime facts for diagnostics."""
    allow = {
        "OS", "PROCESSOR_ARCHITECTURE", "PYTHON_VERSION", "JAVA_HOME",
        "ANDROID_HOME", "ANDROID_SDK_ROOT",
    }
    return {key: value for key, value in os.environ.items() if key in allow}
