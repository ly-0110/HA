from __future__ import annotations

import argparse
import asyncio
import json
import platform
import secrets
import socket
import subprocess
import threading
import time
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from .backends.system import AdbClient, collect_system_checks
from .config import apply_runtime_paths, load_configuration
from .orchestrator import validate_session
from .preflight import run_preflight
from .registry import available_adapters
from .scheduler import TaskScheduler
from .task_models import BatchTaskRequest, TaskRequest
from .task_store import TaskStore
from .worker import _apply_request


def automation_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_runtime_id(root: Path) -> str:
    preferred = "windows-dev" if platform.system() == "Windows" else "ubuntu-dev"
    if (root / "runtime" / f"{preferred}.yaml").is_file():
        return preferred
    return "windows-dev"


def _safe_config(root: Path, folder: str, config_id: str) -> Path:
    if not config_id or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for character in config_id):
        raise HTTPException(400, "配置标识无效")
    path = root / folder / f"{config_id}.yaml"
    if not path.is_file():
        raise HTTPException(404, f"配置不存在：{config_id}")
    return path


def template_payload(path: Path, runtime_path: Path) -> dict[str, Any]:
    """Summarize an experiment template for the console, including event targets."""
    experiment, _runtime = load_configuration(path, runtime_path)
    return {
        "id": path.stem,
        "experiment_id": experiment.experiment_id,
        "adapter": experiment.adapter,
        "adapter_available": experiment.adapter in available_adapters(),
        "device_id": experiment.device.device_id,
        "display_name": experiment.device.display_name,
        "app": {"vendor": experiment.app.vendor, "package": experiment.app.package, "version": experiment.app.version},
        "events": [event.model_dump(mode="json") for event in experiment.events],
        "defaults": experiment.sessions.model_dump(mode="json"),
        "network": experiment.network.model_dump(mode="json"),
    }


def create_app(root: Path | None = None) -> FastAPI:
    root = (root or automation_root()).resolve()
    store = TaskStore(root / "runs" / "console.sqlite3")
    scheduler = TaskScheduler(store, root)
    control_token = secrets.token_urlsafe(32)
    device_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        scheduler.start()
        yield
        scheduler.close()

    app = FastAPI(title="IoT 自动化实验控制台", version="1.0.0", lifespan=lifespan)
    app.state.root = root
    app.state.store = store
    app.state.scheduler = scheduler
    app.state.control_token = control_token
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    @app.middleware("http")
    async def local_control_guard(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"} and request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            if origin and origin not in {
                f"http://127.0.0.1:{request.url.port or 80}",
                f"http://localhost:{request.url.port or 80}",
                "http://testserver",
            }:
                return HTMLResponse("禁止跨站控制", status_code=403)
            if request.headers.get("x-control-token") != control_token:
                return HTMLResponse("控制令牌无效，请刷新控制台", status_code=403)
        return await call_next(request)

    @app.get("/api/v1/bootstrap")
    def bootstrap():
        return {
            "control_token": control_token,
            "version": app.version,
            "default_runtime_id": default_runtime_id(root),
        }

    @app.get("/api/v1/experiments")
    def experiments():
        runtime_path = root / "runtime" / f"{default_runtime_id(root)}.yaml"
        return [template_payload(path, runtime_path) for path in sorted((root / "experiment").glob("*.yaml"))]

    @app.get("/api/v1/devices")
    async def devices(template_id: str | None = None):
        templates = sorted((root / "experiment").glob("*.yaml"))
        package = None
        if template_id:
            package = template_payload(_safe_config(root, "experiment", template_id))["app"]["package"]
        elif templates:
            package = template_payload(templates[0])["app"]["package"]
        runtime_id = default_runtime_id(root)
        runtime_path = root / "runtime" / f"{runtime_id}.yaml"
        if not runtime_path.exists():
            return {"devices": [], "error": f"缺少 {runtime_id} 运行配置"}
        _experiment, runtime = load_configuration(templates[0], runtime_path)
        runtime = apply_runtime_paths(runtime, config_dir=root)

        def latest_observation(udid: str) -> dict[str, Any] | None:
            session_root = root / "runs" / "sessions"
            paths = sorted(session_root.glob("*/actions.jsonl"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
            for actions_path in paths[:50]:
                try:
                    rows = actions_path.read_text(encoding="utf-8").splitlines()
                    for line in reversed(rows):
                        row = json.loads(line)
                        if row.get("phone_udid") == udid:
                            return {
                                "state": row.get("state_after", "unknown"),
                                "observed_at_ns": row.get("t_app_ack_ns") or row.get("t_cmd_after_ns"),
                                "source": "vendor_app",
                            }
                except (OSError, json.JSONDecodeError):
                    continue
            return None

        def discover():
            adb = AdbClient(runtime.adb_executable)
            result = []
            for device in adb.list_devices():
                key = (device.udid, package or "")
                cached = device_cache.get(key)
                if device.online and (not cached or time.monotonic() - cached[0] > 15):
                    details = adb.describe_device(device, package).__dict__
                    device_cache[key] = (time.monotonic(), details)
                elif device.online and cached:
                    details = {**cached[1], "state": device.state, "transport_id": device.transport_id}
                else:
                    details = device.__dict__
                details["last_observation"] = latest_observation(device.udid)
                result.append(details)
            return result

        try:
            values = await asyncio.to_thread(discover)
            active_by_udid = {
                task["request"].get("udid"): task["status"]
                for task in store.active()
                if task["request"].get("udid")
            }
            for value in values:
                value["busy_status"] = active_by_udid.get(value["udid"], "idle")
            return {"devices": values, "error": None}
        except Exception as exc:  # noqa: BLE001
            return {"devices": [], "error": str(exc)}

    @app.get("/api/v1/environment")
    async def environment(runtime_id: str | None = None):
        runtime_id = runtime_id or default_runtime_id(root)
        runtime_path = _safe_config(root, "runtime", runtime_id)
        templates = sorted((root / "experiment").glob("*.yaml"))
        _experiment, runtime = load_configuration(templates[0], runtime_path)
        runtime = apply_runtime_paths(runtime, config_dir=root)
        checks = await asyncio.to_thread(
            collect_system_checks,
            adb=runtime.adb_executable,
            appium=runtime.appium_executable,
            dumpcap=runtime.dumpcap_executable,
            require_capture=runtime.capture_mode.value == "dumpcap",
            android_sdk_root=runtime.android_sdk_root,
        )
        interfaces: list[str] = []
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                [runtime.dumpcap_executable, "-D"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            interfaces = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        except (OSError, subprocess.TimeoutExpired):
            pass
        return {
            "runtime_id": runtime_id,
            "checks": [check.__dict__ for check in checks],
            "capture_interfaces": interfaces,
            "runtime": runtime.model_dump(mode="json"),
        }

    @app.post("/api/v1/preflights")
    async def preflight(request: TaskRequest):
        _safe_config(root, "experiment", request.template_id)
        _safe_config(root, "runtime", request.runtime_id)
        if request.mode == "simulate":
            return {"ok": True, "simulated": True, "checks": []}
        synthetic = {"request": request.model_dump(), "appium_port": 4723, "system_port": 8200}
        _request, experiment, runtime = await asyncio.to_thread(_apply_request, synthetic, root)
        return await asyncio.to_thread(run_preflight, experiment, runtime)

    @app.post("/api/v1/tasks")
    def create_tasks(batch: BatchTaskRequest):
        counts: dict[str, int] = {}
        for request in batch.tasks:
            experiment_path = _safe_config(root, "experiment", request.template_id)
            runtime_path = _safe_config(root, "runtime", request.runtime_id)
            experiment, _runtime = load_configuration(experiment_path, runtime_path)
            if experiment.adapter not in available_adapters():
                raise HTTPException(400, f"实验使用了未注册的适配器：{experiment.adapter}")
            counts[request.template_id] = len(experiment.events)
        return store.create_batch(batch.client_request_id, batch.tasks, counts)

    @app.get("/api/v1/tasks")
    def list_tasks(limit: int = 100):
        return store.list(limit=min(max(limit, 1), 500))

    @app.get("/api/v1/tasks/{task_id}")
    def get_task(task_id: str):
        task = store.get(task_id)
        if not task:
            raise HTTPException(404, "任务不存在")
        return task

    @app.post("/api/v1/tasks/{task_id}/stop")
    def stop_task(task_id: str):
        if not store.get(task_id):
            raise HTTPException(404, "任务不存在")
        scheduler.request_stop(task_id)
        return store.get(task_id)

    @app.post("/api/v1/tasks/{task_id}/force-stop")
    def force_stop_task(task_id: str):
        task = store.get(task_id)
        if not task:
            raise HTTPException(404, "任务不存在")
        if task["status"] != "stopping" or time.time_ns() - task["updated_at_ns"] < 60_000_000_000:
            raise HTTPException(409, "安全停止满 60 秒后才能强制终止")
        scheduler.force_stop(task_id)
        return store.get(task_id)

    @app.get("/api/v1/tasks/{task_id}/logs")
    def task_logs(task_id: str, after: int = 0, limit: int = 1000):
        if not store.get(task_id):
            raise HTTPException(404, "任务不存在")
        return store.logs(task_id, after=max(after, 0), limit=min(max(limit, 1), 5000))

    @app.get("/api/v1/sessions")
    def sessions(limit: int = 100):
        result = []
        session_root = root / "runs" / "sessions"
        for path in sorted(session_root.glob("*"), key=lambda item: item.stat().st_mtime_ns, reverse=True)[:limit]:
            if not path.is_dir():
                continue
            quality_path = path / "quality_report.json"
            session_path = path / "session.yaml"
            metadata: dict[str, Any] = {}
            if session_path.exists():
                try:
                    snapshot = yaml.safe_load(session_path.read_text(encoding="utf-8")) or {}
                    experiment = snapshot.get("experiment", {})
                    metadata = {
                        "experiment_id": experiment.get("experiment_id"),
                        "display_name": experiment.get("device", {}).get("display_name"),
                        "device_id": experiment.get("device", {}).get("device_id"),
                        "phone_udid": experiment.get("phone", {}).get("udid"),
                        "runtime_mode": snapshot.get("runtime", {}).get("mode"),
                    }
                except (OSError, yaml.YAMLError):
                    pass
            screenshot_dir = path / "screenshots"
            result.append({
                "session_id": path.name,
                "updated_at_ns": path.stat().st_mtime_ns,
                "quality": json.loads(quality_path.read_text(encoding="utf-8")) if quality_path.exists() else None,
                "validation": validate_session(path) if session_path.exists() else None,
                "artifacts": [item.name for item in path.iterdir() if item.is_file()],
                "evidence": [item.name for item in screenshot_dir.iterdir() if item.is_file()] if screenshot_dir.is_dir() else [],
                **metadata,
            })
        return result

    @app.get("/api/v1/sessions/{session_id}/artifacts/{artifact}")
    def session_artifact(session_id: str, artifact: str):
        if not session_id or Path(session_id).name != session_id or Path(artifact).name != artifact:
            raise HTTPException(400, "产物路径无效")
        allowed = {
            "session.yaml", "actions.jsonl", "run_journal.jsonl", "quality_report.json", "appium.log",
            "clock_sync.json", "network_isolation_check.json", "ha_events.json", "traffic.pcapng",
            "ha_reconciliation.json", "pcap_review.json", "ha_history.json", "ha_clock_probe.json",
        }
        if artifact not in allowed:
            raise HTTPException(404, "该产物不可下载")
        path = root / "runs" / "sessions" / session_id / artifact
        if not path.is_file():
            raise HTTPException(404, "产物不存在")
        return FileResponse(path, filename=artifact)

    @app.get("/api/v1/sessions/{session_id}/evidence/{filename}")
    def session_evidence(session_id: str, filename: str):
        if Path(session_id).name != session_id or Path(filename).name != filename:
            raise HTTPException(400, "证据路径无效")
        if Path(filename).suffix.lower() not in {".png", ".jpg", ".jpeg", ".xml"}:
            raise HTTPException(404, "该证据不可下载")
        path = root / "runs" / "sessions" / session_id / "screenshots" / filename
        if not path.is_file():
            raise HTTPException(404, "证据不存在")
        return FileResponse(path, filename=filename)

    dist = root / "web" / "dist"
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}")
    def frontend(path: str):
        index = dist / "index.html"
        if index.exists():
            return FileResponse(index)
        return HTMLResponse(
            "<h1>前端尚未构建</h1><p>请在 automation 目录运行 npm install 和 npm run web:build。</p>",
            status_code=503,
        )

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="iot-exp-gui")
    parser.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1", "localhost"])
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    with socket.socket() as probe:
        try:
            probe.bind((args.host, args.port))
        except OSError:
            print(f"ERROR: 端口 {args.port} 已被占用；控制台可能已经启动。")
            return 2
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(f"http://{args.host}:{args.port}")).start()
    import uvicorn

    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
