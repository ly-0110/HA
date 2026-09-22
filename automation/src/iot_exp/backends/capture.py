from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Protocol

from ..models import CaptureResult


class CaptureBackend(Protocol):
    def start(self, output_path: Path) -> None: ...

    def stop(self) -> CaptureResult: ...


class DisabledCaptureBackend:
    def __init__(self):
        self.started_at: int | None = None

    def start(self, output_path: Path) -> None:
        self.started_at = time.time_ns()

    def stop(self) -> CaptureResult:
        return CaptureResult(enabled=False, started_at_unix_ns=self.started_at, stopped_at_unix_ns=time.time_ns())


class DumpcapCaptureBackend:
    def __init__(self, executable: str, interface: str, capture_filter: str | None = None):
        self.executable = executable
        self.interface = interface
        self.capture_filter = capture_filter
        self.process: subprocess.Popen[str] | None = None
        self.output_path: Path | None = None
        self.started_at: int | None = None

    def start(self, output_path: Path) -> None:
        if self.process is not None:
            raise RuntimeError("capture already started")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        args = [self.executable, "-Q", "-i", self.interface, "-w", str(output_path)]
        if self.capture_filter:
            args.extend(["-f", self.capture_filter])
        kwargs: dict[str, object] = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "text": True}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        elif not os.environ.get("IOT_EXP_WORKER_GROUP"):
            kwargs["start_new_session"] = True
        try:
            self.process = subprocess.Popen(args, **kwargs)
        except FileNotFoundError as exc:
            raise RuntimeError(f"Dumpcap executable not found: {self.executable}") from exc
        self.output_path = output_path
        self.started_at = time.time_ns()
        time.sleep(0.5)
        if self.process.poll() is not None:
            stderr = (self.process.stderr.read() if self.process.stderr else "").strip()
            raise RuntimeError(f"Dumpcap exited during startup: {stderr}")

    def stop(self) -> CaptureResult:
        if self.process is None:
            return CaptureResult(enabled=True, path=self.output_path, error="capture_not_started")
        process = self.process
        if process.poll() is None:
            if os.name == "nt":
                try:
                    process.send_signal(subprocess.CTRL_BREAK_EVENT)
                except (AttributeError, OSError):
                    process.terminate()
            else:
                process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
        stderr = (process.stderr.read() if process.stderr else "").strip()
        result = CaptureResult(
            enabled=True,
            path=self.output_path,
            started_at_unix_ns=self.started_at,
            stopped_at_unix_ns=time.time_ns(),
            return_code=process.returncode,
            error=stderr or None,
        )
        self.process = None
        return result
