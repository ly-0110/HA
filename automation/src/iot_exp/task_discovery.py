from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from .artifacts import ARTIFACT_SUFFIXES
from .orchestrator import validate_session
from .paths import PathLayout, current_layout
from .process_identity import process_is_running
from .resources import lease_alive
from .state_files import atomic_json
from .task_store import TaskStore


def _mapping(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8-sig")) if path.suffix == ".yaml" else json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, yaml.YAMLError):
        return {}


def _rows(path: Path) -> list[dict[str, Any]]:
    """A concurrent append can leave a partial final line; retain complete evidence."""
    try:
        result = []
        with path.open(encoding="utf-8-sig") as handle:
            for line in handle:
                try:
                    value = json.loads(line)
                    if isinstance(value, dict):
                        result.append(value)
                except ValueError:
                    continue
        return result
    except OSError:
        return []


def _stamp(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def _integer(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _object(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _children(path: Path) -> list[Path]:
    try:
        return sorted(
            child for child in path.iterdir()
            if child.name.lower() != "legacy" and child.is_dir() and child.resolve().is_relative_to(path.resolve())
        )
    except OSError:
        return []


def _identity(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:20]


class TaskDiscovery:
    """Read CLI sessions, acquisition campaigns and archives without modifying them."""

    def __init__(self, root: Path, store: TaskStore, interval_seconds: float = 2,
                 *, layout: PathLayout | None = None):
        self.root = root.resolve()
        self.layout = layout or current_layout(root)
        self.store = store
        self.interval_seconds = interval_seconds
        self._last_refresh = float("-inf")
        self._lock = threading.RLock()
        self._tasks: list[dict[str, Any]] = []
        self._session_paths: dict[str, Path] = {}
        self.registry_path = self.layout.state_root / "discovery-roots.json"

    def registered_roots(self) -> list[Path]:
        roots = set(self.layout.discovery_roots)
        if self.registry_path.is_file():
            roots.update(Path(value).resolve() for value in _mapping(self.registry_path).get("roots", []))
        return sorted(roots)

    def register_root(self, path: Path) -> None:
        path = path.resolve()
        if not path.is_dir() or "legacy" in {part.lower() for part in path.parts}:
            raise ValueError("实验目录不可登记")
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        roots = sorted(set(self.registered_roots()) | {path})
        atomic_json(self.registry_path, {"schema": 1, "roots": [str(value) for value in roots]})
        self.refresh(force=True)

    def output_roots(self) -> list[Path]:
        default = self.layout.output_root
        result = {default} if (self.layout.desktop or default.resolve().is_relative_to(self.root)) else set()
        for root in self.registered_roots():
            result.add(root)
            if (root / "runs").is_dir():
                result.add((root / "runs").resolve())
        for path in (self.root / "runtime").glob("*.yaml"):
            data = _mapping(path)
            runtime = data.get("runtime", data)
            if isinstance(runtime, dict) and runtime.get("output_root"):
                output = Path(str(runtime["output_root"]))
                resolved = output.resolve() if output.is_absolute() else (self.layout.output_base / output).resolve()
                if output.is_absolute() or resolved.is_relative_to(self.layout.output_base):
                    result.add(resolved)
        return sorted(path for path in result if "legacy" not in {part.lower() for part in path.parts})

    def live_leases(self) -> list[dict[str, Any]]:
        leases = []
        directories = [(self.layout.lock_root, None)] + [(output / "locks", output) for output in self.output_roots()]
        seen = set()
        for directory, output in directories:
            for path in directory.glob("*.lock"):
                value = _mapping(path)
                if not value.get("key") or not value.get("pid"):
                    leases.append({"key":"*", "output_root":str(directory), "unreadable":True})
                    continue
                if value.get("key") and lease_alive(value):
                    identity = (value["key"], value.get("lease_id"), value.get("pid"))
                    if identity not in seen:
                        leases.append({**value, "output_root": str(output.resolve()) if output else value.get("output_root")})
                        seen.add(identity)
        return leases

    def conflict_reason(self, request: dict[str, Any]) -> str | None:
        if request.get("mode") == "simulate":
            return None
        keys = {f"phone:{request.get('udid')}", f"capture:{request.get('capture_interface')}"}
        template = self.root / "experiment" / f"{request['template_id']}.yaml"
        device_id = (_mapping(template).get("device") or {}).get("device_id")
        if device_id:
            keys.add(f"iot:{device_id}")
        if any(lease["key"] == "*" or lease["key"] in keys for lease in self.live_leases()):
            return "等待外部实验释放手机、IoT 设备或抓包接口"
        return None

    def _candidates(self) -> list[tuple[Path, str]]:
        result: dict[Path, str] = {}
        for output in self.output_roots():
            session_directory = output / "sessions"
            campaign_directory = output / "campaigns"
            sessions = _children(session_directory) if session_directory.resolve().is_relative_to(output.resolve()) else []
            campaigns = _children(campaign_directory) if campaign_directory.resolve().is_relative_to(output.resolve()) else []
            for path in sessions:
                if any((path / name).is_file() for name in ("session.yaml", "quality_report.json", "run_journal.jsonl")):
                    result[path.resolve()] = "cli"
            for path in campaigns:
                if (path / "campaign.json").is_file():
                    result[path.resolve()] = "campaign"
        archive_directories = [(self.root / "results", self.root)]
        for root in self.registered_roots():
            archive_directories.extend([(root, root), (root / "results", root)])
        for archive_directory, allowed_root in archive_directories:
            archives = _children(archive_directory) if archive_directory.resolve().is_relative_to(allowed_root) else []
            for path in archives:
                if (path / "campaign.json").is_file() or (path / "session.yaml").is_file():
                    result[path.resolve()] = "archive" if (path / "archive_manifest.json").is_file() else (
                        "campaign" if (path / "campaign.json").is_file() else "cli"
                    )
        return sorted(result.items(), key=lambda item: (item[1] == "archive", str(item[0])))

    def _template_ids(self) -> dict[str, str]:
        return {
            str(data["experiment_id"]): path.stem
            for path in (self.root / "experiment").glob("*.yaml")
            if (data := _mapping(path)).get("experiment_id")
        }

    @staticmethod
    def _matching_lease(path: Path, snapshot: dict, leases: list[dict]) -> dict | None:
        experiment = _object(snapshot.get("experiment"))
        phone = _object(experiment.get("phone"))
        device = _object(experiment.get("device"))
        keys = {f"phone:{phone.get('udid')}", f"iot:{device.get('device_id')}"}
        expected_output = path.parent.parent if path.parent.name in {"sessions", "campaigns"} else None
        configured_output = _object(snapshot.get("runtime")).get("output_root")
        if configured_output and Path(str(configured_output)).is_absolute():
            expected_output = Path(str(configured_output)).resolve()
        for lease in leases:
            if expected_output and lease.get("output_root") != str(expected_output.resolve()):
                continue
            owner = str(lease.get("owner", ""))
            if owner.startswith("gui:"):
                continue
            if owner in {path.name, f"cli:{path.name}"}:
                return lease
            # Older CLI generated-session leases carry resource identity instead.
            if owner == "cli:generated" and lease.get("key") in keys:
                created = _integer(snapshot.get("created_at_unix_ns"))
                leased = _integer(lease.get("created_at_ns"))
                if created and leased and 0 <= created - leased < 300_000_000_000:
                    return lease
        return None

    def _inspect(self, path: Path, source: str, templates: dict[str, str], leases: list[dict]) -> dict:
        campaign = _mapping(path / "campaign.json")
        snapshot = _mapping(path / ("configuration.yaml" if campaign else "session.yaml"))
        experiment = _object(snapshot.get("experiment"))
        runtime = _object(snapshot.get("runtime"))
        sessions = _object(experiment.get("sessions"))
        journal = _rows(path / "run_journal.jsonl")
        finished = next((row for row in reversed(journal) if row.get("kind") == "session_finished"), {})
        quality = campaign or _mapping(path / "quality_report.json")
        groups = _object(campaign.get("groups"))
        raw_outcome = finished.get("outcome") or quality.get("session_outcome") or campaign.get("status")
        outcome = str(raw_outcome) if raw_outcome is not None else None
        terminal = {
            "completed": "completed", "failed": "failed", "cancelled": "cancelled",
            "interrupted": "interrupted", "incomplete": "interrupted",
        }
        lease = self._matching_lease(path, snapshot, leases) if source != "archive" else None
        pid = snapshot.get("process_id") or (lease or {}).get("pid")
        # An archive's historical PID never denotes a process on this machine.
        live = source != "archive" and (
            lease is not None or (
                snapshot.get("process_start_token") is not None
                and process_is_running(pid, snapshot.get("process_start_token"))
            )
        )
        status = terminal.get(str(outcome)) or ("running" if live else "interrupted")
        error = finished.get("error") or campaign.get("error")
        if (not outcome or outcome in {"running", "starting"}) and not live:
            error = error or "未找到仍在运行的原始进程；保留现有实验产物"
        repetitions = _integer(sessions.get("repetitions_per_event"), 1)
        events = experiment.get("events")
        planned = _integer(quality.get("planned_event_count"), repetitions * (len(events) if isinstance(events, list) else 0))
        completed = _integer(quality.get("completed_event_count"), sum(row.get("kind") == "event_finished" for row in journal))
        if campaign:
            planned = sum(
                _integer(group.get("planned_target")) + _integer(group.get("planned_u_operations"))
                for group in groups.values() if isinstance(group, dict)
            )
            completed = sum(_integer(group.get("processed")) for group in groups.values() if isinstance(group, dict))
        created = _integer(campaign.get("started_at_unix_ns") or snapshot.get("created_at_unix_ns"), _stamp(path))
        updated = max(
            [created, _integer(campaign.get("finished_at_unix_ns")), _integer(quality.get("generated_at_unix_ns"))]
            + [_integer(row.get("at_unix_ns")) for row in journal]
            + [_integer(group.get("finished_at_unix_ns") or group.get("started_at_unix_ns")) for group in groups.values() if isinstance(group, dict)]
        )
        task_id = f"external-{_identity(path)}"
        session_id = path.name if path.parent == self.layout.output_root / "sessions" else f"{source}_{_identity(path)[:8]}_{path.name}"
        if session_id in self._session_paths and self._session_paths[session_id] != path:
            session_id = f"external_{_identity(path)[:8]}_{path.name}"
        self._session_paths[session_id] = path
        phone = _object(experiment.get("phone"))
        device = _object(experiment.get("device"))
        mode = "simulate" if snapshot.get("simulated") else ("formal" if runtime.get("mode") == "formal" else "device")
        try:
            appium_port = urlsplit(str(runtime.get("appium_url") or "")).port
        except ValueError:
            appium_port = None
        return {
            "id": task_id, "source": "archive" if source == "archive" else ("campaign" if campaign else "cli"),
            "controllable": False, "session_id": session_id, "session_root": str(path),
            "status": status, "stage": str(campaign.get("current_group") or status), "error": error,
            "pid": _integer(pid) or None if live else None,
            "appium_port": appium_port, "system_port": runtime.get("uiautomator2_system_port"),
            "completed_events": completed, "planned_events": planned, "quality": quality or None,
            "created_at_ns": created, "updated_at_ns": updated,
            "request": {
                "template_id": templates.get(str(experiment.get("experiment_id")), str(experiment.get("experiment_id") or path.name)),
                "mode": mode, "udid": phone.get("udid"), "repetitions": repetitions,
                "target_device_ip": _object(experiment.get("network")).get("target_device_ip"),
                "capture_interface": runtime.get("capture_interface"),
                "events": experiment.get("events") if not campaign and isinstance(experiment.get("events"), list) else None,
            },
            "metadata": {
                "experiment_id": experiment.get("experiment_id"), "device_id": device.get("device_id"),
                "display_name": device.get("display_name") or path.name, "runtime_mode": runtime.get("mode"),
                "original_session_id": snapshot.get("session_id") or path.name,
                "protocol": campaign.get("protocol"), "is_campaign": bool(campaign),
                "state_evidence": "terminal_report" if outcome in terminal else ("live_process" if live else "process_unavailable"),
            },
        }

    def refresh(self, *, force: bool = False) -> None:
        with self._lock:
            now = time.monotonic()
            if not force and now - self._last_refresh < self.interval_seconds:
                return
            self._session_paths = {}
            templates = self._template_ids()
            leases = self.live_leases()
            tasks = [self._inspect(path, source, templates, leases) for path, source in self._candidates()]
            self.store.sync_discovered(tasks)
            managed = self.store.managed_sessions()
            for task in tasks:
                if task["session_root"] in managed:
                    original = managed[task["session_root"]]
                    task.update({key: original[key] for key in ("id", "source", "controllable", "status", "stage", "request")})
            self._tasks = tasks
            self._last_refresh = now

    def session_path(self, session_id: str) -> Path | None:
        self.refresh()
        if session_id not in self._session_paths:
            self.refresh(force=True)
        with self._lock:
            return self._session_paths.get(session_id)

    def sessions(self, limit: int = 100) -> list[dict[str, Any]]:
        self.refresh()
        with self._lock:
            result = []
            for task in sorted(self._tasks, key=lambda value: value["created_at_ns"], reverse=True)[:min(max(limit, 1), 500)]:
                path = Path(task["session_root"])
                subfolders = _children(path) if task["metadata"]["is_campaign"] else []
                folders = [path] + [folder for folder in subfolders if (folder / "capture_plan.json").is_file() or (folder / "actions.jsonl").is_file()]
                artifacts = []
                evidence = []
                for folder in folders:
                    try:
                        artifacts.extend(
                            item.relative_to(path).as_posix() for item in sorted(folder.iterdir())
                            if item.is_file() and item.suffix.lower() in ARTIFACT_SUFFIXES
                            and item.resolve().is_relative_to(path)
                        )
                        evidence.extend(
                            item.relative_to(path).as_posix() for item in sorted((folder / "screenshots").glob("*"))
                            if item.is_file() and item.suffix.lower() in {".png", ".jpg", ".jpeg", ".xml"}
                            and item.resolve().is_relative_to(path)
                        )
                    except OSError:
                        continue
                validation = None
                if (path / "session.yaml").is_file() and task["status"] != "running":
                    try:
                        validation = validate_session(path)
                    except (OSError, ValueError, TypeError, KeyError):
                        validation = {"ok": False, "errors": ["会话日志不完整或无法读取"]}
                result.append({
                    "session_id": task["session_id"], "task_id": task["id"], "session_root": str(path),
                    "status": task["status"], "source": task["source"], "updated_at_ns": task["updated_at_ns"],
                    "quality": task["quality"], "validation": validation,
                    "artifacts": artifacts, "evidence": evidence, "phone_udid": task["request"].get("udid"),
                    **task["metadata"],
                })
            return result

    def logs(self, task_id: str, after: int = 0, limit: int = 1000) -> list[dict[str, Any]]:
        self.refresh()
        task = self.store.get(task_id)
        if not task or task.get("controllable", True):
            return self.store.logs(task_id, after=max(after, 0), limit=min(max(limit, 1), 5000))
        path = Path(task["session_root"])
        journals = [path / "run_journal.jsonl"]
        if task.get("metadata", {}).get("is_campaign"):
            journals.extend(folder / "run_journal.jsonl" for folder in _children(path))
        result = []
        index = 0
        for journal in journals:
            for row in _rows(journal):
                index += 1
                if index <= max(after, 0):
                    continue
                stage = str(row.get("kind") or "record")
                parts = [stage]
                for key in ("event_type", "event_id", "result", "outcome", "error"):
                    if row.get(key) is not None:
                        parts.append(str(row[key]))
                result.append({
                    "id": index, "task_id": task_id, "created_at_ns": _integer(row.get("at_unix_ns")),
                    "level": "error" if row.get("error") else "info", "stage": stage,
                    "message": " · ".join(parts),
                })
                if len(result) >= min(max(limit, 1), 5000):
                    return result
        return result
