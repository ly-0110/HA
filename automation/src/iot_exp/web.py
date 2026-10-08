from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import secrets
import socket
import sqlite3
import subprocess
import threading
import time
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .artifacts import artifact_response, open_local_path, resolve_artifact, text_preview
from .backends.system import AdbClient, collect_system_checks, installed_android_sdks
from .config import apply_runtime_paths, load_configuration
from .models import RunMode
from .paths import PathLayout, current_layout
from .preflight import run_preflight
from .registry import available_adapters
from .scheduler import TaskScheduler
from .state_files import atomic_json
from .task_discovery import TaskDiscovery
from .task_models import BatchTaskRequest, TaskRequest
from .task_store import TaskStore
from .worker import _apply_request, apply_event_plan
from .workspace import import_legacy_workspace, recover_imports


def automation_root() -> Path:
    return current_layout().config_root


class ToolSelection(BaseModel):
    kind: str
    path: str
    detected: bool = False


class RootSelection(BaseModel):
    path: str


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
    formal_id = (
        "ubuntu-speaker-lab" if experiment.adapter == "mi_home_touchscreen_speaker" else "ubuntu-lab"
    )
    formal_path = path.parent.parent / "runtime" / f"{formal_id}.yaml"
    if not formal_path.is_file():
        formal_path = path.parent.parent / "runtime" / "ubuntu-lab.yaml"
    _formal_experiment, formal_runtime = load_configuration(
        path, formal_path if formal_path.is_file() else runtime_path,
    )
    return {
        "id": path.stem,
        "experiment_id": experiment.experiment_id,
        "adapter": experiment.adapter,
        "adapter_available": experiment.adapter in available_adapters(),
        "device_id": experiment.device.device_id,
        "display_name": experiment.device.display_name,
        "app": {"vendor": experiment.app.vendor, "package": experiment.app.package, "version": experiment.app.version},
        "events": [event.model_dump(mode="json") for event in experiment.events],
        "parameters": experiment.parameters.model_dump(mode="json"),
        "phone_udid": experiment.phone.udid,
        "defaults": experiment.sessions.model_dump(mode="json"),
        "network": experiment.network.model_dump(mode="json"),
        "runtime_defaults": {
            "runtime_id": formal_path.stem if formal_path.is_file() else runtime_path.stem,
            **{key: getattr(formal_runtime, key) for key in (
                "capture_interface", "capture_filter", "pre_roll_seconds", "post_roll_seconds",
            )},
        },
    }


def create_app(root: Path | None = None, *, layout: PathLayout | None = None,
               desktop_token: str | None = None, instance_id: str | None = None) -> FastAPI:
    layout = layout or current_layout(root)
    layout.initialize()
    root = layout.config_root
    if layout.desktop and not desktop_token:
        raise ValueError("桌面后台必须通过私有启动握手提供凭证")
    store = TaskStore(layout.database, owner_instance=instance_id)
    if layout.desktop:
        recover_imports(layout, store)
    discovery = TaskDiscovery(root, store, layout=layout)
    scheduler = TaskScheduler(store, root, layout=layout)
    control_token = desktop_token or secrets.token_urlsafe(32)
    device_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        scheduler.start()
        yield
        scheduler.close()

    app = FastAPI(title="IoT 自动化实验控制台", version="1.0.0", lifespan=lifespan)
    app.state.root = root
    app.state.layout = layout
    app.state.store = store
    app.state.scheduler = scheduler
    app.state.discovery = discovery
    app.state.control_token = control_token
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    @app.middleware("http")
    async def local_control_guard(request: Request, call_next):
        if request.url.path.startswith("/api/") and (layout.desktop or request.method not in {"GET", "HEAD", "OPTIONS"}):
            origin = request.headers.get("origin")
            allowed_origins = {"app://workbench"} if layout.desktop else {
                f"http://127.0.0.1:{request.url.port or 80}",
                f"http://localhost:{request.url.port or 80}",
                "http://testserver",
            }
            if origin and origin not in allowed_origins:
                return HTMLResponse("禁止跨站控制", status_code=403)
            if not secrets.compare_digest(request.headers.get("x-control-token", "").encode("utf-8"), control_token.encode("utf-8")):
                return HTMLResponse("控制令牌无效，请刷新控制台", status_code=403)
        return await call_next(request)

    @app.get("/api/v1/bootstrap")
    def bootstrap():
        return {
            **({} if layout.desktop else {"control_token": control_token}),
            "desktop": layout.desktop,
            "version": app.version,
            "default_runtime_id": default_runtime_id(root),
        }

    @app.get("/api/v1/health")
    def health():
        return {"protocol_version": 1, "instance_id": instance_id,
                "state": "draining" if scheduler.draining else "recovery" if scheduler.recovering else "ready",
                "active_owned": scheduler.active_owned_count()}

    @app.post("/api/v1/desktop/drain")
    def drain():
        if not layout.desktop:
            raise HTTPException(404, "桌面接口未启用")
        scheduler.begin_drain()
        return health()

    @app.post("/api/v1/desktop/tools")
    def configure_tool(selection: ToolSelection):
        if not layout.desktop:
            raise HTTPException(404, "桌面接口未启用")
        if scheduler.active_owned_count():
            raise HTTPException(409, "实验运行中不能切换工具")
        path = Path(selection.path)
        if not path.is_absolute() or selection.kind not in {"sdk", "dumpcap"}:
            raise HTTPException(400, "工具配置无效")
        if selection.detected and (selection.kind != "sdk" or path.resolve() not in {
            Path(item["path"]) for item in installed_android_sdks()
        }):
            raise HTTPException(400, "该目录不是当前检测到的SDK，请手动选择有效SDK目录")
        if selection.kind == "sdk":
            if not (path / "platform-tools" / ("adb.exe" if os.name == "nt" else "adb")).is_file():
                raise HTTPException(400, "所选目录不包含SDK platform-tools/adb")
        elif not path.is_file() or path.name.lower() not in {"dumpcap", "dumpcap.exe"}:
            raise HTTPException(400, "请选择已安装的Dumpcap程序")
        target = layout.app_state_root / "tools.json"
        data = json.loads(target.read_text(encoding="utf-8")) if target.is_file() else {}
        data[selection.kind] = str(path.resolve())
        atomic_json(target, data)
        return {"configured": True, "kind": selection.kind}

    @app.post("/api/v1/desktop/roots")
    def register_root(selection: RootSelection):
        if not layout.desktop:
            raise HTTPException(404, "桌面接口未启用")
        path = Path(selection.path).resolve()
        if not path.is_dir() or path.name.lower() == "legacy":
            raise HTTPException(400, "实验目录不可登记")
        discovery.register_root(path)
        return {"registered": True}

    @app.post("/api/v1/desktop/import")
    def import_workspace(selection: RootSelection):
        if not layout.desktop:
            raise HTTPException(404, "桌面接口未启用")
        try:
            report = import_legacy_workspace(Path(selection.path), layout, store)
            discovery.register_root(Path(selection.path))
            return report
        except (ValueError, OSError, sqlite3.Error) as error:
            raise HTTPException(409, str(error)) from error

    @app.get("/api/v1/experiments")
    def experiments():
        runtime_path = root / "runtime" / f"{default_runtime_id(root)}.yaml"
        return [template_payload(path, runtime_path) for path in sorted((root / "experiment").glob("*.yaml"))]

    @app.get("/api/v1/devices")
    async def devices(template_id: str | None = None):
        templates = sorted((root / "experiment").glob("*.yaml"))
        if not templates:
            return {"devices": [], "error": "尚无实验模板"}
        runtime_id = default_runtime_id(root)
        runtime_path = root / "runtime" / f"{runtime_id}.yaml"
        package = None
        if template_id:
            package = template_payload(
                _safe_config(root, "experiment", template_id), runtime_path,
            )["app"]["package"]
        elif templates:
            package = template_payload(templates[0], runtime_path)["app"]["package"]
        if not runtime_path.exists():
            return {"devices": [], "error": f"缺少 {runtime_id} 运行配置"}
        _experiment, runtime = load_configuration(templates[0], runtime_path)
        runtime = apply_runtime_paths(runtime, config_dir=root, layout=layout)

        def latest_observation(udid: str) -> dict[str, Any] | None:
            session_root = layout.output_root / "sessions"
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
            await asyncio.to_thread(discovery.refresh)
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
        runtime = apply_runtime_paths(runtime, config_dir=root, layout=layout)
        checks = await asyncio.to_thread(
            collect_system_checks,
            adb=runtime.adb_executable,
            appium=runtime.appium_executable,
            dumpcap=runtime.dumpcap_executable,
            require_capture=runtime.capture_mode.value == "dumpcap",
            android_sdk_root=runtime.android_sdk_root,
        )
        interfaces: list[str] = []
        capture_state, capture_detail = "missing", "选择已安装的Dumpcap后重新检查"
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
            capture_state = "prepared" if result.returncode == 0 else "permission_denied" if any(word in result.stderr.lower() for word in ("permission", "权限", "denied")) else "unavailable"
            capture_detail = "工具可用；正式采集仍需本次接口、过滤器、权限与网络预检" if result.returncode == 0 else result.stderr.strip() or "无法枚举抓包接口"
        except (OSError, subprocess.TimeoutExpired):
            pass
        core_state, core_detail = "ready", "可以查看记录和运行模拟实验"
        if layout.desktop and (layout.resources_root / "runtime-manifest.json").is_file():
            from .runtime_bundle import load_manifest
            try:
                await asyncio.to_thread(load_manifest, layout.resources_root)
            except (ValueError, OSError) as error:
                core_state, core_detail = "checksum_failed", str(error)
        device_ok = all(check.ok for check in checks if check.name in {"adb", "node", "java", "appium", "android_sdk_root"})
        return {
            "runtime_id": runtime_id,
            "sdk_candidates": installed_android_sdks() if layout.desktop else [],
            "checks": [check.__dict__ for check in checks],
            "capture_interfaces": interfaces,
            "runtime": runtime.model_dump(mode="json"),
            "discovery_roots": [{"path":str(path), "available":path.is_dir(),
                                 "reason":"" if path.is_dir() else "目录已移动、未挂载或无法读取"}
                                for path in discovery.registered_roots()],
            "capabilities": [
                {"key": "core", "name": "核心功能", "state": core_state, "detail": core_detail},
                {"key": "device", "name": "真机自动化", "state": "prepared" if device_ok else "missing", "detail": "工具已准备；连接Android8/API26及以上手机，完成USB授权和网络预检" if device_ok else "准备Android SDK并核对下方依赖项"},
                {"key": "capture", "name": "正式抓包", "state": capture_state, "detail": capture_detail},
            ],
        }

    @app.post("/api/v1/preflights")
    async def preflight(request: TaskRequest):
        _safe_config(root, "experiment", request.template_id)
        _safe_config(root, "runtime", request.runtime_id)
        synthetic = {"request": request.model_dump(), "appium_port": 4723, "system_port": 8200}
        try:
            _request, experiment, runtime = await asyncio.to_thread(_apply_request, synthetic, root)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if request.mode == "simulate":
            return {"ok": True, "simulated": True, "checks": []}
        return await asyncio.to_thread(run_preflight, experiment, runtime)

    @app.post("/api/v1/tasks")
    def create_tasks(batch: BatchTaskRequest):
        if scheduler.draining or scheduler.recovering:
            raise HTTPException(409, "后台正在清理或等待旧进程退出，暂不能启动新实验")
        counts: dict[str, int] = {}
        for request in batch.tasks:
            experiment_path = _safe_config(root, "experiment", request.template_id)
            runtime_path = _safe_config(root, "runtime", request.runtime_id)
            experiment, runtime = load_configuration(experiment_path, runtime_path)
            if experiment.adapter not in available_adapters():
                raise HTTPException(400, f"实验使用了未注册的适配器：{experiment.adapter}")
            if request.mode == "formal" and runtime.mode is not RunMode.FORMAL:
                raise HTTPException(400, "正式采集必须选择 formal 运行配置")
            try:
                apply_event_plan(experiment, request)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            counts[request.template_id] = len(experiment.events)
        return store.create_batch(batch.client_request_id, batch.tasks, counts)

    @app.get("/api/v1/tasks")
    def list_tasks(limit: int = 100):
        discovery.refresh()
        return store.list(limit=min(max(limit, 1), 500))

    @app.get("/api/v1/tasks/{task_id}")
    def get_task(task_id: str):
        discovery.refresh()
        task = store.get(task_id)
        if not task:
            raise HTTPException(404, "任务不存在")
        return task

    @app.post("/api/v1/tasks/{task_id}/stop")
    def stop_task(task_id: str):
        task = store.get(task_id)
        if not task:
            raise HTTPException(404, "任务不存在")
        if not task.get("controllable", True):
            raise HTTPException(409, "该实验从控制台外启动，请在原入口停止")
        scheduler.request_stop(task_id)
        return store.get(task_id)

    @app.post("/api/v1/tasks/{task_id}/force-stop")
    def force_stop_task(task_id: str):
        task = store.get(task_id)
        if not task:
            raise HTTPException(404, "任务不存在")
        if not task.get("controllable", True):
            raise HTTPException(409, "该实验从控制台外启动，请在原入口停止")
        stop_started = task.get("stop_requested_at_ns") or task["updated_at_ns"]
        if task["status"] != "stopping" or time.time_ns() - stop_started < 60_000_000_000:
            raise HTTPException(409, "安全停止满 60 秒后才能强制终止")
        scheduler.force_stop(task_id)
        return store.get(task_id)

    @app.get("/api/v1/tasks/{task_id}/logs")
    def task_logs(task_id: str, after: int = 0, limit: int = 1000):
        discovery.refresh()
        task = store.get(task_id)
        if not task:
            raise HTTPException(404, "任务不存在")
        if not task.get("controllable", True):
            return discovery.logs(task_id, after=max(after, 0), limit=min(max(limit, 1), 5000))
        return store.logs(task_id, after=max(after, 0), limit=min(max(limit, 1), 5000))

    @app.get("/api/v1/sessions")
    def sessions(limit: int = 100):
        return discovery.sessions(limit=min(max(limit, 1), 500))

    def session_directory(session_id: str) -> Path:
        path = discovery.session_path(session_id)
        if path is None:
            raise HTTPException(404, "会话不存在")
        return path

    def artifact_path(session_id: str, relative: str, *, evidence: bool = False) -> Path:
        directory = session_directory(session_id)
        if evidence:
            if Path(relative).suffix.lower() not in {".png", ".jpg", ".jpeg", ".xml"}:
                raise HTTPException(404, "该文件不是页面证据")
            if "screenshots" not in relative.split("/"):
                relative = f"screenshots/{relative}"
        return resolve_artifact(directory, relative)

    @app.get("/api/v1/sessions/{session_id}/artifacts/{artifact:path}")
    def session_artifact(
        session_id: str, artifact: str, download: bool = False, preview: bool = False,
    ):
        path = artifact_path(session_id, artifact)
        return text_preview(path) if preview and not download else artifact_response(path, download=download)

    @app.get("/api/v1/sessions/{session_id}/evidence/{filename:path}")
    def session_evidence(
        session_id: str, filename: str, download: bool = False, preview: bool = False,
    ):
        path = artifact_path(session_id, filename, evidence=True)
        return text_preview(path) if preview and not download else artifact_response(path, download=download)

    @app.post("/api/v1/sessions/{session_id}/artifacts/{artifact:path}/open")
    async def open_artifact(session_id: str, artifact: str):
        path = artifact_path(session_id, artifact)
        await asyncio.to_thread(open_local_path, path)
        return {"opened": True, "path": str(path)}

    @app.post("/api/v1/sessions/{session_id}/evidence/{filename:path}/open")
    async def open_evidence(session_id: str, filename: str):
        path = artifact_path(session_id, filename, evidence=True)
        await asyncio.to_thread(open_local_path, path)
        return {"opened": True, "path": str(path)}

    @app.post("/api/v1/sessions/{session_id}/open")
    async def open_session(session_id: str):
        path = session_directory(session_id)
        await asyncio.to_thread(open_local_path, path)
        return {"opened": True, "path": str(path)}

    @app.get("/api/v1/sessions/{session_id}/resolve")
    def resolve_session_file(session_id: str, relative: str = "", category: str = "artifacts"):
        if not layout.desktop:
            raise HTTPException(404, "桌面接口未启用")
        directory = session_directory(session_id).resolve()
        if category not in {"artifacts", "evidence", "directory"}:
            raise HTTPException(400, "产物类别无效")
        path = directory if category == "directory" else artifact_path(session_id, relative, evidence=category == "evidence")
        return {"path": str(path), "root": str(directory), "category": category}

    dist = layout.resources_root / "web" / "dist"
    if layout.desktop and not dist.is_dir():
        dist = layout.resources_root / "web"
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}")
    def frontend(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "接口不存在")
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
