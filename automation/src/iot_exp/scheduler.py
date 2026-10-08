from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path

from .paths import PathLayout, current_layout
from .process_cleanup import stop_orphaned_child, terminate_owned_tree
from .process_identity import process_is_running, process_start_token
from .resources import release_dead_task_leases
from .task_discovery import TaskDiscovery
from .task_models import TERMINAL_STATUSES
from .task_store import TaskStore


class TaskScheduler:
    def __init__(self, store: TaskStore, root: Path, max_parallel: int = 4,
                 *, layout: PathLayout | None = None):
        self.store = store
        self.root = root
        self.layout = layout or current_layout(root)
        self.max_parallel = max_parallel
        self.processes: dict[str, subprocess.Popen[bytes]] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.discovery = TaskDiscovery(root, store, layout=self.layout)
        self.draining = False
        self.recovering = False
        self._mutex = threading.RLock()
        self._cleanup_signalled: set[tuple[str, int, str]] = set()

    def start(self) -> None:
        self._reconcile_cleanup()
        self._interrupt_unfinished()
        self.recovering = self.store.live_console_processes()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="iot-task-scheduler", daemon=True)
        self._thread.start()

    def _interrupt_unfinished(self) -> None:
        for task in self.store.interrupt_unfinished():
            release_dead_task_leases(self.layout.lock_root, task)

    def close(self) -> None:
        self.begin_drain()
        while self.active_owned_count():
            self.tick()
            time.sleep(0.1)
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def begin_drain(self) -> None:
        with self._mutex:
            if self.draining:
                return
            self.draining = True
            for task in self.store.active() + self.store.queued():
                if task.get("controllable"):
                    self.request_stop(task["id"])
            for child in self.store.owned_live_children():
                task = self.store.get(child["task_id"])
                if task["status"] in TERMINAL_STATUSES:
                    self.store.update(task["id"], status="stopping", stage="cleanup",
                                      stop_requested_at_ns=task.get("stop_requested_at_ns") or time.time_ns(), error="等待所属残留进程清理")
            self._reconcile_cleanup()

    def active_owned_count(self) -> int:
        with self._mutex:
            worker_ids = {task_id for task_id, process in self.processes.items() if process.poll() is None}
            worker_ids.update(task["id"] for task in self.store.owned_live_workers())
            return len(worker_ids) + len(self.store.owned_live_children())

    def _reconcile_cleanup(self) -> None:
        for task in self.store.adopt_cleanup_tasks():
            if process_is_running(task.get("pid"), task.get("process_start_token")):
                cancel_path = self.layout.control_root / f"{task['id']}.cancel"
                cancel_path.parent.mkdir(parents=True, exist_ok=True)
                cancel_path.touch(exist_ok=True)
                continue
            for child in task.get("owned_processes", []):
                if child.get("role") not in {"appium", "capture"} or not child.get("token"):
                    continue
                identity = (task["id"], int(child["pid"]), str(child["token"]))
                if identity in self._cleanup_signalled:
                    continue
                self._cleanup_signalled.add(identity)
                try:
                    stop_orphaned_child(child)
                except (OSError, RuntimeError) as error:
                    self.store.add_log(task["id"], "error", "cleanup", f"所属进程安全停止未完成：{error}")
            if not any(process_is_running(child.get("pid"), child.get("token")) for child in task.get("owned_processes", [])):
                release_dead_task_leases(self.layout.lock_root, task)
                self.store.update(task["id"], status="interrupted", stage="interrupted", error="所属进程已清理；部分产物保留待核查，实验未自动重跑")

    def _loop(self) -> None:
        while not self._stop.wait(0.5):
            self.tick()

    def tick(self) -> None:
        with self._mutex:
            self._tick()

    def _tick(self) -> None:
        self.discovery.refresh()
        for task_id, process in list(self.processes.items()):
            code = process.poll()
            if code is None:
                continue
            task = self.store.get(task_id)
            children_live = task and any(process_is_running(child.get("pid"), child.get("token")) for child in task.get("owned_processes", []))
            if task and children_live:
                self.store.update(task_id, status="stopping", stage="cleanup", error=f"工作进程异常退出：{code}；正在清理所属进程",
                                  stop_requested_at_ns=task.get("stop_requested_at_ns") or time.time_ns())
            if task and task["status"] not in TERMINAL_STATUSES and not children_live:
                self.store.update(task_id, status="interrupted", stage="interrupted", error=f"工作进程异常退出：{code}")
            self.processes.pop(task_id, None)
            if task:
                release_dead_task_leases(self.layout.lock_root, self.store.get(task_id))
        self._reconcile_cleanup()
        for task in self.store.active():
            if task.get("controllable") and task["stage"] == "cleanup" and not process_is_running(task.get("pid"), task.get("process_start_token")) and not any(process_is_running(child.get("pid"), child.get("token")) for child in task.get("owned_processes", [])):
                release_dead_task_leases(self.layout.lock_root, task)
                self.store.update(task["id"], status="interrupted", stage="interrupted", error="工作进程异常退出，所属进程已清理；产物保留待核查")
        if self.recovering:
            self._interrupt_unfinished()
            self.recovering = self.store.live_console_processes()
        if self.draining or self.recovering or len(self.processes) >= self.max_parallel:
            return
        active = self.store.active()
        for task in self.store.queued():
            if len(self.processes) >= self.max_parallel:
                break
            reason = self._conflict(task, active)
            reason = reason or self.discovery.conflict_reason(task["request"])
            if reason:
                self.store.update(task["id"], queue_reason=reason)
                continue
            leased_ports = {int(lease["key"].rsplit(":", 1)[1]) for lease in self.discovery.live_leases()
                            if lease["key"].startswith("port:tcp:")}
            slot = self._free_slot(active, leased_ports)
            if slot >= 64:
                self.store.update(task["id"], queue_reason="等待可用的Appium/UiAutomator2端口")
                continue
            self._launch(task, slot)
            launched = self.store.get(task["id"])
            if launched:
                active.append(launched)

    @staticmethod
    def _conflict(task, active) -> str | None:
        request = task["request"]
        if request.get("mode") == "simulate":
            return None
        for other in active:
            current = other["request"]
            if request.get("udid") and request.get("udid") == current.get("udid"):
                return "等待该手机上的其他实验结束"
            if request["template_id"] == current["template_id"]:
                return "等待同一 IoT 设备上的其他实验结束"
            if (
                request.get("capture_interface")
                and request.get("capture_interface") == current.get("capture_interface")
            ):
                return "等待该抓包接口释放"
        return None

    @staticmethod
    def _free_slot(active, leased_ports: set[int] | None = None) -> int:
        used = {task.get("system_port", 8200) - 8200 for task in active if task.get("system_port")}
        leased_ports = leased_ports or set()
        return next((slot for slot in range(64) if slot not in used
                     and 4723 + slot * 2 not in leased_ports and 8200 + slot not in leased_ports), 64)

    def _launch(self, task, slot: int) -> None:
        task_id = task["id"]
        appium_port = 4723 + slot * 2
        system_port = 8200 + slot
        if not self.store.claim_queued(task_id, appium_port=appium_port, system_port=system_port):
            return
        parent_token = process_start_token(os.getpid())
        command = [
            str(self.layout.python_executable),
            *(["-I", "-B", "-X", "utf8"] if (self.layout.resources_root / "runtime-manifest.json").is_file() else []),
            "-m",
            "iot_exp.worker",
            "--db",
            str(self.store.path),
            "--task",
            task_id,
            "--root",
            str(self.root),
            "--parent-pid",
            str(os.getpid()),
        ]
        if parent_token:
            command.extend(["--parent-start-token", parent_token])
        kwargs: dict[str, object] = {"cwd": str(self.layout.state_root),
                                     "env": self.layout.child_environment(), "stdin": subprocess.DEVNULL}
        self.layout.state_root.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        process = subprocess.Popen(command, **kwargs)
        self.processes[task_id] = process
        self.store.update(task_id, pid=process.pid, process_start_token=process_start_token(process.pid),
                          parent_pid=os.getpid(), parent_start_token=parent_token)
        self.store.add_log(task_id, "info", "dispatching", "任务已分配运行资源")

    def request_stop(self, task_id: str) -> None:
        task = self.store.get(task_id)
        if not task or task["status"] in TERMINAL_STATUSES:
            return
        if not task.get("controllable", True):
            raise RuntimeError("外部实验由原启动进程管理，请在原终端停止")
        if task["status"] == "queued" and self.store.cancel_queued(task_id):
            self.store.add_log(task_id, "warning", "cancelled", "排队任务已取消")
            return
        cancel_path = self.layout.control_root / f"{task_id}.cancel"
        cancel_path.parent.mkdir(parents=True, exist_ok=True)
        cancel_path.touch(exist_ok=True)
        self.store.update(task_id, status="stopping", stage="stopping",
                          stop_requested_at_ns=task.get("stop_requested_at_ns") or time.time_ns())
        self.store.add_log(task_id, "warning", "stopping", "已请求安全停止，将在当前动作边界清理资源")

    def force_stop(self, task_id: str) -> None:
        task = self.store.get(task_id)
        if not task:
            return
        if not task.get("controllable", True):
            raise RuntimeError("外部实验由原启动进程管理，请在原终端停止")
        if task["status"] != "stopping" or time.time_ns() - (task.get("stop_requested_at_ns") or task["updated_at_ns"]) < 60_000_000_000:
            raise RuntimeError("safe stop must be given 60 seconds before force stop")
        root_live = process_is_running(task["pid"], task.get("process_start_token"))
        owned_live = [child for child in task.get("owned_processes", [])
                      if child.get("role") in {"appium", "capture"} and process_is_running(child.get("pid"), child.get("token"))]
        if not root_live and not owned_live:
            raise RuntimeError("进程创建身份不匹配，拒绝强制终止")
        pid = int(task.get("pid") or 0)
        for owned in reversed(owned_live):
            terminate_owned_tree(int(owned["pid"]), owned["token"])
        if root_live:
            terminate_owned_tree(pid, task.get("process_start_token"))
        release_dead_task_leases(self.layout.lock_root, task)
        self.store.update(task_id, status="interrupted", stage="interrupted", error="用户强制终止，产物可能不完整")
        self.store.add_log(task_id, "error", "interrupted", "任务已被强制终止，产物可能不完整")
