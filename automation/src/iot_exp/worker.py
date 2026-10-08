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
from .config import apply_runtime_paths, configuration_provenance, load_configuration
from .models import CaptureMode, ExperimentConfig, RunMode
from .orchestrator import ExperimentRunner, RunCancelled, validate_session
from .paths import current_layout
from .preflight import run_preflight, write_preflight_report
from .process_identity import process_is_running, process_start_token
from .registry import create_adapter
from .resources import ResourceLease, experiment_resource_keys
from .task_models import TaskRequest
from .task_store import TaskStore


def _root() -> Path:
    return current_layout().config_root


def _cancel_path(root: Path, task_id: str) -> Path:
    return current_layout(root).control_root / f"{task_id}.cancel"


def apply_event_plan(experiment: ExperimentConfig, request: TaskRequest) -> ExperimentConfig:
    if request.events is None:
        return experiment
    supported = {event.event_type for event in experiment.events}
    if any(event.event_type not in supported for event in request.events):
        raise ValueError("所选事件类型不属于当前实验模板")
    data = experiment.model_dump(mode="json")
    data["events"] = [event.model_dump(mode="json") for event in request.events]
    return ExperimentConfig.model_validate(data)


def _apply_request(task: dict, root: Path):
    request = TaskRequest.model_validate(task["request"])
    experiment_path = root / "experiment" / f"{request.template_id}.yaml"
    runtime_path = root / "runtime" / f"{request.runtime_id}.yaml"
    experiment, runtime = load_configuration(experiment_path, runtime_path)
    experiment = apply_event_plan(experiment, request)
    if request.mode == "formal" and runtime.mode is not RunMode.FORMAL:
        raise ValueError("正式采集必须选择 formal 运行配置")
    runtime = apply_runtime_paths(runtime, config_dir=root)
    runtime.tool_provenance["template"] = configuration_provenance(experiment_path, current_layout(root))
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


def run_task(db_path: Path, task_id: str, root: Path, parent_pid: int | None = None,
             parent_start_identity: str | None = None) -> int:
    store = TaskStore(db_path)
    task = store.get(task_id)
    if task is None:
        return 2
    cancel_path = _cancel_path(root, task_id)
    cancel_path.parent.mkdir(parents=True, exist_ok=True)
    watchdog_stop = threading.Event()
    parent_token = parent_start_identity or (
        task.get("parent_start_token") if task.get("parent_pid") == parent_pid else None
    ) or (process_start_token(parent_pid) if parent_pid is not None else None)

    def watch_parent() -> None:
        while not watchdog_stop.wait(1):
            if parent_pid is None:
                return
            if not process_is_running(parent_pid, parent_token):
                cancel_path.touch(exist_ok=True)
                store.add_log(task_id, "warning", "stopping", "控制台进程已退出，正在清理任务")
                return

    threading.Thread(target=watch_parent, name="parent-watchdog", daemon=True).start()
    os.environ["IOT_EXP_WORKER_GROUP"] = "1"
    server = None
    owned_processes = []

    def record_process(role, process):
        owned_processes.append({"role": role, "pid": process.pid, "token": process_start_token(process.pid),
                                "isolated_group": os.name == "nt"})
        store.update(task_id, owned_processes_json=owned_processes)
        lease.track_processes(owned_processes)
    try:
        store.update(task_id, status="preflight", stage="preflight", pid=os.getpid(),
                     process_start_token=process_start_token(os.getpid()), queue_reason=None,
                     parent_pid=parent_pid, parent_start_token=parent_token)
        store.add_log(task_id, "info", "preflight", "正在校验实验配置和运行环境")
        request, experiment, runtime = _apply_request(task, root)
        if request.mode == "simulate":
            preflight = {"ok": True, "simulated": True, "checks": []}
        else:
            preflight = run_preflight(experiment, runtime)
            for check in preflight.get("checks", []):
                if not check.get("ok"):
                    store.add_log(
                        task_id, "error" if request.mode == "formal" else "warning", "preflight",
                        f"{check.get('name', 'check')}：{check.get('detail', '未通过')}",
                    )
            if request.mode == "formal" and not preflight["ok"]:
                raise RuntimeError("正式采集预检未通过")
        lease = ResourceLease(
            runtime.output_root,
            [] if request.mode == "simulate" else experiment_resource_keys(experiment, runtime),
            owner=f"gui:{task_id}",
            lock_root=current_layout(root).lock_root,
        )
        lease.__enter__()
        if request.mode == "formal":
            # Dependency diagnostics precede interface canonicalization; after
            # acquiring the lease, recheck the current phone/network conditions.
            preflight = run_preflight(experiment, runtime)
            if not preflight["ok"]:
                for check in preflight.get("checks", []):
                    if not check.get("ok"):
                        store.add_log(task_id, "error", "preflight", f"{check.get('name')}：{check.get('detail')}")
                raise RuntimeError("正式采集预检未通过")
        if cancel_path.exists():
            raise RunCancelled("任务在启动前已取消")
        store.update(task_id, status="starting", stage="starting")
        store.add_log(task_id, "info", "starting", "正在准备实验进程")
        if request.mode == "simulate":
            adapter = SimulatedLampAdapter.for_experiment(experiment)
            capture = DisabledCaptureBackend()
        else:
            configure_android_environment(runtime.adb_executable, runtime.android_sdk_root)
            adapter = create_adapter(experiment, runtime, screenshot_dir=runtime.output_root)
            capture = (
                DumpcapCaptureBackend(runtime.dumpcap_executable, runtime.capture_interface or "", runtime.capture_filter,
                                      process_callback=lambda process: record_process("capture", process))
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
                store.add_log(
                    task_id,
                    "info",
                    "running",
                    f"事件 {event.get('label') or event['event_id']}：{event['result']}",
                ),
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
                project_root=current_layout(root).resources_root,
                process_callback=lambda process: record_process("appium", process),
            )
            if runtime.appium_managed:
                server.start()
        store.add_log(task_id, "info", "running", f"实验已开始，会话 {runner.session_id}")
        runner.run()
        quality = json.loads(runner.paths.quality_report.read_text(encoding="utf-8"))
        quality["validation"] = validate_session(runner.paths.root)
        store.update(task_id, quality_json=quality)
        if not quality["validation"]["ok"]:
            if quality.get("session_outcome") == "incomplete":
                raise RuntimeError("实验未完成全部计划事件，请检查事件目标是否允许重复切换")
            raise RuntimeError("会话产物校验未通过，详见会话校验结果")
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
                store.update(task_id, status="failed", stage="cleanup_failed", error=f"Appium清理未完成：{exc}")
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
    parser.add_argument("--parent-start-token")
    args = parser.parse_args(argv)
    return run_task(args.db, args.task, args.root.resolve(), args.parent_pid, args.parent_start_token)


if __name__ == "__main__":
    raise SystemExit(main())
