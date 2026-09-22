from __future__ import annotations

import argparse
import json
import os
import threading
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .adapters import SimulatedLampAdapter
from .backends import (
    AppiumServer,
    DisabledCaptureBackend,
    DisabledHaProvider,
    DumpcapCaptureBackend,
)
from .backends.system import configure_android_environment
from .cli import resolve_selected_device
from .config import apply_runtime_paths, load_configuration
from .models import CaptureMode, DeviceState
from .orchestrator import ExperimentRunner, RunCancelled, validate_session
from .preflight import run_preflight, write_preflight_report
from .registry import create_adapter
from .resources import ResourceLease, experiment_resource_keys
from .task_models import TaskRequest
from .task_store import TaskStore


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def _cancel_path(root: Path, task_id: str) -> Path:
    return root / "runs" / "control" / f"{task_id}.cancel"


def _apply_request(task: dict, root: Path):
    request = TaskRequest.model_validate(task["request"])
    experiment_path = root / "experiment" / f"{request.template_id}.yaml"
    runtime_path = root / "runtime" / f"{request.runtime_id}.yaml"
    experiment, runtime = load_configuration(experiment_path, runtime_path)
    runtime = apply_runtime_paths(runtime, config_dir=root)
    experiment = experiment.model_copy(update={
        "sessions": experiment.sessions.model_copy(update={
            "count": 1,
            "repetitions_per_event": request.repetitions,
            "idle_range_seconds": (request.idle_min_seconds, request.idle_max_seconds),
            "cooldown_seconds": request.cooldown_seconds,
            "max_attempts": 1,
        }),
        "network": experiment.network.model_copy(update={
            "target_device_ip": request.target_device_ip or experiment.network.target_device_ip,
        }),
    })
    endpoint = urlsplit(runtime.appium_url)
    runtime_updates = {
        "appium_url": urlunsplit((endpoint.scheme or "http", f"127.0.0.1:{task['appium_port']}", "", "", "")),
        "uiautomator2_system_port": task["system_port"],
    }
    for key in ("capture_interface", "capture_filter", "pre_roll_seconds", "post_roll_seconds"):
        value = getattr(request, key)
        if value is not None:
            runtime_updates[key] = value
    if request.mode != "formal":
        runtime_updates["capture_mode"] = CaptureMode.DISABLED
    runtime = runtime.model_copy(update=runtime_updates)
    if request.mode != "simulate":
        args = argparse.Namespace(
            udid=request.udid,
            select_device=False,
            phone_id=None,
        )
        experiment, _selected = resolve_selected_device(args, experiment, runtime)
    return request, experiment, runtime


def run_task(db_path: Path, task_id: str, root: Path, parent_pid: int | None = None) -> int:
    store = TaskStore(db_path)
    task = store.get(task_id)
    if task is None:
        return 2
    cancel_path = _cancel_path(root, task_id)
    cancel_path.parent.mkdir(parents=True, exist_ok=True)
    cancel_path.unlink(missing_ok=True)
    watchdog_stop = threading.Event()

    def watch_parent() -> None:
        while not watchdog_stop.wait(1):
            if parent_pid is None:
                return
            try:
                os.kill(parent_pid, 0)
            except OSError:
                cancel_path.touch(exist_ok=True)
                store.add_log(task_id, "warning", "stopping", "控制台进程已退出，正在清理任务")
                return

    threading.Thread(target=watch_parent, name="parent-watchdog", daemon=True).start()
    os.environ["IOT_EXP_WORKER_GROUP"] = "1"
    server = None
    try:
        store.update(task_id, status="preflight", stage="preflight", pid=os.getpid(), queue_reason=None)
        store.add_log(task_id, "info", "preflight", "正在校验实验配置和运行环境")
        request, experiment, runtime = _apply_request(task, root)
        lease = ResourceLease(
            runtime.output_root,
            [] if request.mode == "simulate" else experiment_resource_keys(experiment, runtime),
            owner=f"gui:{task_id}",
        )
        lease.__enter__()
        if request.mode == "simulate":
            preflight = {"ok": True, "simulated": True, "checks": []}
        else:
            preflight = run_preflight(experiment, runtime)
            if request.mode == "formal" and not preflight["ok"]:
                raise RuntimeError("正式采集预检未通过")
        if cancel_path.exists():
            raise RunCancelled("任务在启动前已取消")
        store.update(task_id, status="starting", stage="starting")
        store.add_log(task_id, "info", "starting", "正在准备实验进程")
        if request.mode == "simulate":
            adapter = SimulatedLampAdapter(DeviceState.OFF)
            capture = DisabledCaptureBackend()
        else:
            configure_android_environment(runtime.adb_executable, runtime.android_sdk_root)
            adapter = create_adapter(experiment, runtime, screenshot_dir=runtime.output_root)
            capture = (
                DumpcapCaptureBackend(runtime.dumpcap_executable, runtime.capture_interface or "", runtime.capture_filter)
                if runtime.capture_mode is CaptureMode.DUMPCAP
                else DisabledCaptureBackend()
            )
        runner = ExperimentRunner(
            experiment,
            runtime,
            adapter=adapter,
            capture=capture,
            ha=DisabledHaProvider(),
            session_id=None,
            seed=request.seed,
            cancel_requested=cancel_path.exists,
            progress_callback=lambda event: (
                store.update(task_id, completed_events=event["completed"]),
                store.add_log(task_id, "info", "running", f"事件 {event['event_id']}：{event['result']}"),
            ),
        )
        store.update(
            task_id,
            status="running",
            stage="running",
            session_id=runner.session_id,
            session_root=str(runner.paths.root),
        )
        write_preflight_report(runner.paths.network_isolation_check, preflight)
        if request.mode != "simulate":
            server = AppiumServer(
                runtime.appium_executable,
                runtime.appium_url,
                runner.paths.appium_log,
                project_root=root,
            )
            if runtime.appium_managed:
                server.start()
        store.add_log(task_id, "info", "running", f"实验已开始，会话 {runner.session_id}")
        runner.run()
        quality = json.loads(runner.paths.quality_report.read_text(encoding="utf-8"))
        quality["validation"] = validate_session(runner.paths.root)
        store.update(task_id, status="completed", stage="completed", quality_json=quality)
        store.add_log(task_id, "info", "completed", "实验执行完成")
        return 0
    except RunCancelled as exc:
        store.update(task_id, status="cancelled", stage="cancelled", error=str(exc))
        store.add_log(task_id, "warning", "cancelled", "实验已安全停止")
        return 0
    except BaseException as exc:  # noqa: BLE001 - worker must persist all terminal failures.
        store.update(task_id, status="failed", stage="failed", error=str(exc))
        store.add_log(task_id, "error", "failed", str(exc))
        return 2
    finally:
        if server is not None:
            try:
                server.stop()
            except Exception as exc:  # noqa: BLE001
                store.add_log(task_id, "error", "cleanup", f"Appium 清理失败：{exc}")
        cancel_path.unlink(missing_ok=True)
        if "lease" in locals():
            lease.release()
        watchdog_stop.set()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--root", type=Path, default=_root())
    parser.add_argument("--parent-pid", type=int)
    args = parser.parse_args(argv)
    return run_task(args.db, args.task, args.root.resolve(), args.parent_pid)


if __name__ == "__main__":
    raise SystemExit(main())
