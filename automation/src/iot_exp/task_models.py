from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TaskStatus(str, Enum):
    QUEUED = "queued"
    PREFLIGHT = "preflight"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


TERMINAL_STATUSES = {
    TaskStatus.COMPLETED.value,
    TaskStatus.FAILED.value,
    TaskStatus.CANCELLED.value,
    TaskStatus.INTERRUPTED.value,
}


class TaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_id: str
    runtime_id: str = "windows-dev"
    mode: Literal["simulate", "device", "formal"] = "simulate"
    udid: str | None = None
    repetitions: int = Field(default=1, ge=1, le=10000)
    idle_min_seconds: float = Field(default=1, ge=0, le=3600)
    idle_max_seconds: float = Field(default=3, ge=0, le=3600)
    cooldown_seconds: float = Field(default=1, ge=0, le=3600)
    seed: int | None = Field(default=None, ge=1, lt=2**31)
    target_device_ip: str | None = None
    capture_interface: str | None = None
    capture_filter: str | None = None
    pre_roll_seconds: float | None = Field(default=None, ge=0, le=3600)
    post_roll_seconds: float | None = Field(default=None, ge=0, le=3600)

    @model_validator(mode="after")
    def validate_request(self):
        if self.idle_max_seconds < self.idle_min_seconds:
            raise ValueError("idle_max_seconds must be greater than or equal to idle_min_seconds")
        if self.mode != "simulate" and not self.udid:
            raise ValueError("a phone UDID is required for a real experiment")
        if self.mode == "formal" and not all((self.target_device_ip, self.capture_interface, self.capture_filter)):
            raise ValueError("formal acquisition requires target IP, capture interface and capture filter")
        return self


class BatchTaskRequest(BaseModel):
    client_request_id: str = Field(min_length=8, max_length=128)
    tasks: list[TaskRequest] = Field(min_length=1, max_length=16)


class LogEntry(BaseModel):
    id: int
    task_id: str
    created_at_ns: int
    level: str
    stage: str
    message: str
