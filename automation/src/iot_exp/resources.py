from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path


class ResourceBusyError(RuntimeError):
    pass


class ResourceLease:
    """Small cross-process lock shared by CLI and the graphical console."""

    def __init__(self, output_root: Path, keys: list[str], owner: str):
        self.directory = output_root / "locks"
        self.keys = sorted(set(keys))
        self.owner = owner
        self.paths: list[Path] = []

    def __enter__(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            for key in self.keys:
                name = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24] + ".lock"
                path = self.directory / name
                try:
                    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                except FileExistsError as exc:
                    detail = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
                    try:
                        owner = json.loads(detail)
                        os.kill(int(owner["pid"]), 0)
                    except (OSError, ValueError, KeyError, json.JSONDecodeError):
                        path.unlink(missing_ok=True)
                        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    else:
                        raise ResourceBusyError(f"resource is busy: {key}; {detail}") from exc
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump({"key": key, "owner": self.owner, "pid": os.getpid(), "created_at_ns": time.time_ns()}, handle)
                self.paths.append(path)
        except Exception:
            self.release()
            raise
        return self

    def release(self) -> None:
        for path in reversed(self.paths):
            path.unlink(missing_ok=True)
        self.paths.clear()

    def __exit__(self, _type, _value, _traceback):
        self.release()


def experiment_resource_keys(experiment, runtime) -> list[str]:
    keys = [f"phone:{experiment.phone.udid}", f"iot:{experiment.device.device_id}"]
    if runtime.capture_mode.value == "dumpcap" and runtime.capture_interface:
        keys.append(f"capture:{runtime.capture_interface}")
    return keys
