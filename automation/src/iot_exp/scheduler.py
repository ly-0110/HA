from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from .task_models import TERMINAL_STATUSES
from .task_store import TaskStore


class TaskScheduler:
    def __init__(self, store: TaskStore, root: Path, max_parallel: int = 4):
        self.store = store
        self.root = root
        self.max_parallel = max_parallel
        self.processes: dict[str, subprocess.Popen[bytes]] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.store.interrupt_unfinished()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="iot-task-scheduler", daemon=True)
        self._thread.start()

    def close(self) -> None:
        for task_id in list(self.processes):
            self.request_stop(task_id)
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.wait(0.5):
            self.tick()

    def tick(self) -> None:
        for task_id, process in list(self.processes.items()):
            code = process.poll()
            if code is None:
                continue
            task = self.store.get(task_id)
            if task and task["status"] not in TERMINAL_STATUSES:
                self.store.update(task_id, status="interrupted", stage="interrupted", error=f"工作进程异常退出：{code}")
            self.processes.pop(task_id, None)
        if len(self.processes) >= self.max_parallel:
            return
        active = self.store.active()
        for task in self.store.queued():
            if len(self.processes) >= self.max_parallel:
                break
            reason = self._conflict(task, active)
            if reason:
                self.store.update(task["id"], queue_reason=reason)
                continue
            slot = self._free_slot(active)
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
    def _free_slot(active) -> int:
        used = {task.get("system_port", 8200) - 8200 for task in active if task.get("system_port")}
        return next(slot for slot in range(64) if slot not in used)

    def _launch(self, task, slot: int) -> None:
        task_id = task["id"]
        appium_port = 4723 + slot * 2
        system_port = 8200 + slot
        self.store.update(
            task_id,
            status="preflight",
            stage="dispatching",
            queue_reason=None,
            appium_port=appium_port,
            system_port=system_port,
        )
        command = [
            sys.executable,
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
        kwargs: dict[str, object] = {"cwd": str(self.root)}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        process = subprocess.Popen(command, **kwargs)
        self.processes[task_id] = process
        self.store.update(task_id, pid=process.pid)
        self.store.add_log(task_id, "info", "dispatching", "任务已分配运行资源")

    def request_stop(self, task_id: str) -> None:
        task = self.store.get(task_id)
        if not task or task["status"] in TERMINAL_STATUSES:
            return
        if task["status"] == "queued":
            self.store.update(task_id, status="cancelled", stage="cancelled", queue_reason=None)
            self.store.add_log(task_id, "warning", "cancelled", "排队任务已取消")
            return
        cancel_path = self.root / "runs" / "control" / f"{task_id}.cancel"
        cancel_path.parent.mkdir(parents=True, exist_ok=True)
        cancel_path.touch(exist_ok=True)
        self.store.update(task_id, status="stopping", stage="stopping")
        self.store.add_log(task_id, "warning", "stopping", "已请求安全停止，将在当前动作边界清理资源")

    def force_stop(self, task_id: str) -> None:
        task = self.store.get(task_id)
        if not task or not task.get("pid"):
            return
        if task["status"] != "stopping" or time.time_ns() - task["updated_at_ns"] < 60_000_000_000:
            raise RuntimeError("safe stop must be given 60 seconds before force stop")
        pid = int(task["pid"])
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
        else:
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.store.update(task_id, status="interrupted", stage="interrupted", error="用户强制终止，产物可能不完整")
        self.store.add_log(task_id, "error", "interrupted", "任务已被强制终止，产物可能不完整")
