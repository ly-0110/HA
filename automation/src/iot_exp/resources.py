from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from .paths import app_state_root, current_layout, shared_lock_root
from .process_identity import process_is_running, process_start_token


class ResourceBusyError(RuntimeError):
    pass


@contextmanager
def _gate(path: Path, *, blocking: bool = False):
    """Kernel-owned mutex: process death releases it, malformed leases do not."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if path.stat().st_size == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except OSError as exc:
            raise ResourceBusyError("资源锁正在被其他进程更新，请稍后重试") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_owner(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or int(value.get("pid", 0)) <= 0:
            raise ValueError("missing process identity")
        return value
    except (OSError, ValueError, TypeError) as exc:
        raise ResourceBusyError(f"无法确认锁所有者，保留锁并拒绝抢占：{path}") from exc


def lease_alive(owner: dict) -> bool:
    return process_is_running(owner.get("pid"), owner.get("process_start_token")) or any(
        process_is_running(child.get("pid"), child.get("token"))
        for child in owner.get("owned_processes", []) if isinstance(child, dict))


def release_dead_task_leases(lock_root: Path, task: dict) -> None:
    """Release only a dead console task's exact recorded lease and its compatibility copy."""
    if process_is_running(task.get("pid"), task.get("process_start_token")) or any(
        process_is_running(child.get("pid"), child.get("token"))
        for child in task.get("owned_processes", [])
    ):
        return
    for path in lock_root.glob("*.lock"):
        try:
            candidate = _read_owner(path)
        except ResourceBusyError:
            continue  # An unrelated or unknown lease is retained, never reclaimed.
        if candidate.get("owner") != f"gui:{task['id']}":
            continue
        try:
            _release_dead_task_lease(lock_root, path, task)
        except ResourceBusyError:
            continue


def _release_dead_task_lease(lock_root: Path, path: Path, task: dict) -> None:
    with _gate(lock_root / (path.name + ".guard"), blocking=True):
        if not path.exists():
            return
        owner = _read_owner(path)
        if owner.get("owner") != f"gui:{task['id']}" or lease_alive(owner):
            return
        if owner.get("schema") != 2 or not owner.get("lease_id") or not owner.get("output_root"):
            return
        if owner.get("process_start_token") != task.get("process_start_token"):
            return
        compatibility = Path(owner["output_root"]) / "locks" / path.name
        if compatibility != path and compatibility.is_file():
            legacy = _read_owner(compatibility)
            if legacy.get("lease_id") == owner.get("lease_id") and not lease_alive(legacy):
                compatibility.unlink()
        path.unlink()


class ResourceLease:
    """Small cross-process lock shared by CLI and the graphical console."""

    def __init__(self, output_root: Path, keys: list[str], owner: str,
                 *, lock_root: Path | None = None):
        self.output_root = output_root.resolve()
        self.directory = (lock_root or shared_lock_root()).resolve()
        self.legacy_directory = self.output_root / "locks"
        self.keys = sorted(set(keys))
        self.owner = owner
        self.paths: list[Path] = []
        self.lease_id = uuid.uuid4().hex
        self.owned_processes: list[dict] = []

    def __enter__(self):
        try:
            known_directories = self._known_legacy_directories() if self.keys else []
            for key in self.keys:
                name = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24] + ".lock"
                with _gate(self.directory / (name + ".guard")):
                    for directory in known_directories:
                        path = directory / name
                        if path.is_file() and lease_alive(_read_owner(path)):
                            raise ResourceBusyError(f"已登记旧入口仍占用资源：{key}; {directory}")
                    for directory in dict.fromkeys((self.directory, self.legacy_directory)):
                        directory.mkdir(parents=True, exist_ok=True)
                        path = directory / name
                        if path.exists():
                            owner = _read_owner(path)
                            if lease_alive(owner):
                                raise ResourceBusyError(f"resource is busy: {key}; {owner['pid']}")
                            path.unlink()
                        temporary = directory / (name + "." + self.lease_id + ".tmp")
                        temporary.write_text(json.dumps({
                        "key": key, "owner": self.owner, "pid": os.getpid(),
                        "process_start_token": process_start_token(os.getpid()),
                        "created_at_ns": time.time_ns(), "lease_id": self.lease_id,
                        "output_root": str(self.output_root), "schema": 2,
                        }), encoding="utf-8")
                        try:
                            # An atomic hard-link publishes complete JSON without overwriting an old CLI.
                            os.link(temporary, path)
                        except FileExistsError as exc:
                            raise ResourceBusyError(f"resource is busy: {key}") from exc
                        finally:
                            temporary.unlink(missing_ok=True)
                        self.paths.append(path)
        except Exception:
            self.release()
            raise
        return self

    def _known_legacy_directories(self) -> list[Path]:
        layout = current_layout()
        roots = set(layout.discovery_roots)
        sources = [(layout.state_root / "discovery-roots.json", "roots"),
                   (layout.app_state_root / "preferences.json", "discovery_roots"),
                   (app_state_root() / "preferences.json", "discovery_roots")]
        for path, key in sources:
            if not path.is_file():
                continue
            try:
                value = json.loads(path.read_text(encoding="utf-8-sig"))
                if not isinstance(value, dict):
                    raise TypeError("invalid root registry")
                entries = value.get(key, [])
                if not isinstance(entries, list) or any(not isinstance(entry, str) for entry in entries):
                    raise ValueError("invalid discovery roots")
                roots.update(Path(entry).resolve() for entry in entries)
            except (OSError, ValueError, TypeError) as error:
                raise ResourceBusyError(f"无法核实已登记旧入口：{path}") from error
        return [directory for root in roots if "legacy" not in {part.lower() for part in root.parts}
                for directory in (root / "locks", root / "runs/locks")
                if directory not in {self.directory, self.legacy_directory}]

    def release(self) -> None:
        if any(process_is_running(child.get("pid"), child.get("token")) for child in self.owned_processes):
            return
        for path in reversed(self.paths):
            with _gate(self.directory / (path.name + ".guard"), blocking=True):
                if path.exists() and _read_owner(path).get("lease_id") == self.lease_id:
                    path.unlink()
        self.paths.clear()

    def track_processes(self, children: list[dict]) -> None:
        self.owned_processes = list(children)
        for path in self.paths:
            with _gate(self.directory / (path.name + ".guard"), blocking=True):
                owner = _read_owner(path)
                if owner.get("lease_id") != self.lease_id:
                    raise ResourceBusyError("锁所有权改变，拒绝更新")
                owner["owned_processes"] = children
                temporary = path.with_name(path.name + ".update-" + self.lease_id)
                temporary.write_text(json.dumps(owner), encoding="utf-8")
                os.replace(temporary, path)

    def __exit__(self, _type, _value, _traceback):
        self.release()


def capture_resource_key(interface: str, executable: str) -> str:
    selected = interface.strip()
    # Linux device names are stable; numeric IDs and Windows friendly names
    # must be resolved, otherwise aliases can acquire two leases for one NIC.
    if os.name != "nt" and not selected.isdecimal() and not re.match(r"^\d+\.\s", selected):
        return "capture:" + selected
    try:
        result = subprocess.run([executable, "-D"], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ResourceBusyError("无法核实抓包接口身份，请检查Dumpcap和接口权限") from error
    if result.returncode:
        raise ResourceBusyError("无法枚举抓包接口，拒绝取得不明确的接口锁")
    compare = str.casefold if os.name == "nt" else lambda value: value
    matches = []
    for line in result.stdout.splitlines():
        match = re.match(r"^(\d+)\.\s+(\S+)(?:\s+\((.*)\))?$", line.strip())
        if match and compare(selected) in {compare(item) for item in (match[1], match[2], match[3] or "", line.strip())}:
            matches.append(match[2])
    if len(set(matches)) != 1:
        raise ResourceBusyError("抓包接口无法唯一对应到当前网卡，请重新选择接口")
    return "capture:" + compare(matches[0])


def experiment_resource_keys(experiment, runtime) -> list[str]:
    keys = [f"phone:{experiment.phone.udid}", f"iot:{experiment.device.device_id}"]
    if runtime.capture_mode.value == "dumpcap" and runtime.capture_interface:
        keys.append(capture_resource_key(runtime.capture_interface, runtime.dumpcap_executable))
    port = urlsplit(runtime.appium_url).port
    if port:
        keys.append(f"port:tcp:{port}")
    keys.append(f"port:tcp:{runtime.uiautomator2_system_port}")
    return keys
