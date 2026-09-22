from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DeviceState(str, Enum):
    ON = "on"
    OFF = "off"
    UNKNOWN = "unknown"


class EventType(str, Enum):
    TURN_ON = "turn_on"
    TURN_OFF = "turn_off"


class EventResult(str, Enum):
    CONFIRMED = "confirmed"
    APP_ACK_ONLY = "app_ack_only"
    HA_ONLY = "ha_only"
    FAILED = "failed"
    TIMEOUT = "timeout"
    UNEXPECTED_STATE = "unexpected_state"
    AUTOMATION_ERROR = "automation_error"


class CaptureMode(str, Enum):
    DISABLED = "disabled"
    DUMPCAP = "dumpcap"


class RunMode(str, Enum):
    DEV = "dev"
    FORMAL = "formal"


class Selector(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy: str
    value: str


class PhoneConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phone_id: str
    udid: str
    network: str = "public_wifi"
    experiment_network_forbidden: bool = True
    model: str | None = None
    product: str | None = None
    device: str | None = None
    transport_id: str | None = None
    manufacturer: str | None = None
    android_version: str | None = None
    sdk_level: str | None = None


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vendor: str = "xiaomi"
    package: str
    version: str = "unknown"
    selectors: dict[str, list[Selector]] = Field(default_factory=dict)


class DeviceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: str
    display_name: str = "米家台灯 1S"
    entity_id: str | None = None
    firmware: str = "unknown"


class EventSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_type: EventType
    required_state: DeviceState
    expected_state: DeviceState

    @model_validator(mode="after")
    def validate_transition(self) -> EventSpec:
        expected = DeviceState.ON if self.event_type is EventType.TURN_ON else DeviceState.OFF
        required = DeviceState.OFF if self.event_type is EventType.TURN_ON else DeviceState.ON
        if self.expected_state is not expected or self.required_state is not required:
            raise ValueError(f"invalid transition for {self.event_type.value}")
        return self


class SessionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    count: int = Field(default=1, ge=1)
    repetitions_per_event: int = Field(default=10, ge=1)
    idle_range_seconds: tuple[float, float] = (1.0, 3.0)
    cooldown_seconds: float = Field(default=1.0, ge=0)
    max_attempts: int = Field(default=1, ge=1)

    @field_validator("idle_range_seconds")
    @classmethod
    def validate_idle_range(cls, value: tuple[float, float]) -> tuple[float, float]:
        if value[0] < 0 or value[1] < value[0]:
            raise ValueError("idle_range_seconds must be non-negative and ordered")
        return value


class NetworkConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    forbidden_cidrs: list[str] = Field(default_factory=list)
    target_device_ip: str | None = None
    require_no_usb_tethering: bool = True


class ExperimentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    experiment_id: str
    phone: PhoneConfig
    app: AppConfig
    device: DeviceConfig
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    sessions: SessionConfig = Field(default_factory=SessionConfig)
    events: list[EventSpec]

    @model_validator(mode="after")
    def validate_events(self) -> ExperimentConfig:
        if not self.events:
            raise ValueError("at least one event is required")
        if len({event.event_type for event in self.events}) != len(self.events):
            raise ValueError("event_type entries must be unique")
        return self


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: RunMode = RunMode.DEV
    output_root: Path = Path("runs")
    appium_url: str = "http://127.0.0.1:4723"
    uiautomator2_system_port: int = Field(default=8200, ge=1024, le=65535)
    appium_managed: bool = True
    appium_executable: str = "appium"
    adb_executable: str = "adb"
    android_sdk_root: Path | None = None
    dumpcap_executable: str = "dumpcap"
    capture_mode: CaptureMode = CaptureMode.DISABLED
    capture_interface: str | None = None
    capture_filter: str | None = None
    pre_roll_seconds: float = Field(default=2.0, ge=0)
    post_roll_seconds: float = Field(default=2.0, ge=0)
    min_free_space_gb: float = Field(default=2.0, ge=0)
    timezone: str = "UTC"

    @model_validator(mode="after")
    def validate_formal_capture(self) -> RuntimeConfig:
        if self.mode is RunMode.FORMAL:
            if self.capture_mode is not CaptureMode.DUMPCAP:
                raise ValueError("formal mode requires dumpcap capture")
            if not self.capture_interface:
                raise ValueError("formal mode requires an explicit capture_interface")
        return self


class AckEvidence(BaseModel):
    acknowledged: bool
    observed_state: DeviceState = DeviceState.UNKNOWN
    observed_at_unix_ns: int | None = None
    message: str = ""


class HaEvidence(BaseModel):
    observed_state: DeviceState = DeviceState.UNKNOWN
    observed_at_unix_ns: int | None = None
    source: str = ""


class ActionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    experiment_id: str
    session_id: str
    event_id: str
    device_id: str
    event_type: EventType
    trigger_source: str = "vendor_app"
    trigger_network: str
    phone_id: str
    phone_udid: str
    app_version: str
    device_firmware: str
    state_before: DeviceState
    expected_state: DeviceState
    state_after: DeviceState = DeviceState.UNKNOWN
    t_cmd_before_ns: int | None = None
    t_cmd_after_ns: int | None = None
    t_app_ack_ns: int | None = None
    t_ha_state_ns: int | None = None
    result: EventResult
    attempt: int = Field(ge=1)
    error_code: str | None = None
    notes: str = ""


class CaptureResult(BaseModel):
    enabled: bool
    path: Path | None = None
    started_at_unix_ns: int | None = None
    stopped_at_unix_ns: int | None = None
    return_code: int | None = None
    packets: int | None = None
    error: str | None = None


class SessionPaths(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    root: Path
    session_yaml: Path
    actions_jsonl: Path
    run_journal_jsonl: Path
    appium_log: Path
    ha_events_json: Path
    network_isolation_check: Path
    quality_report: Path
    clock_sync: Path
    capture: Path
    screenshots: Path

    @classmethod
    def create(cls, root: Path) -> SessionPaths:
        root.mkdir(parents=True, exist_ok=True)
        screenshots = root / "screenshots"
        screenshots.mkdir(exist_ok=True)
        return cls(
            root=root,
            session_yaml=root / "session.yaml",
            actions_jsonl=root / "actions.jsonl",
            run_journal_jsonl=root / "run_journal.jsonl",
            appium_log=root / "appium.log",
            ha_events_json=root / "ha_events.json",
            network_isolation_check=root / "network_isolation_check.json",
            quality_report=root / "quality_report.json",
            clock_sync=root / "clock_sync.json",
            capture=root / "traffic.pcapng",
            screenshots=screenshots,
        )


def model_dump_jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Path):
        return str(value)
    return value
