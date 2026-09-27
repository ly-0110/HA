from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DeviceState(str, Enum):
    ON = "on"
    OFF = "off"
    PLAYING = "playing"
    PAUSED = "paused"
    UNKNOWN = "unknown"


class EventType(str, Enum):
    TURN_ON = "turn_on"
    TURN_OFF = "turn_off"
    PLAY_MUSIC = "play_music"
    PAUSE_MUSIC = "pause_music"
    SET_BRIGHTNESS = "set_brightness"
    SET_COLOR_TEMPERATURE = "set_color_temperature"
    SELECT_SCENE = "select_scene"
    SET_FOCUS_MODE = "set_focus_mode"


TWO_STATE_TRANSITIONS: dict[EventType, tuple[DeviceState, DeviceState]] = {
    EventType.TURN_ON: (DeviceState.OFF, DeviceState.ON),
    EventType.TURN_OFF: (DeviceState.ON, DeviceState.OFF),
    EventType.PLAY_MUSIC: (DeviceState.PAUSED, DeviceState.PLAYING),
    EventType.PAUSE_MUSIC: (DeviceState.PLAYING, DeviceState.PAUSED),
}


class ParameterDimension(str, Enum):
    BRIGHTNESS = "brightness"
    COLOR_TEMPERATURE = "color_temperature"
    SCENE = "scene"
    FOCUS_MODE = "focus_mode"


EVENT_TYPE_TO_DIMENSION: dict[EventType, ParameterDimension] = {
    EventType.SET_BRIGHTNESS: ParameterDimension.BRIGHTNESS,
    EventType.SET_COLOR_TEMPERATURE: ParameterDimension.COLOR_TEMPERATURE,
    EventType.SELECT_SCENE: ParameterDimension.SCENE,
    EventType.SET_FOCUS_MODE: ParameterDimension.FOCUS_MODE,
}


ParameterValue = bool | int | str


def event_target_key(target: ParameterValue | None) -> str:
    """Stable string key for an event target so identities stay distinct in JSON stores."""
    if target is None:
        return "-"
    if isinstance(target, bool):
        return "true" if target else "false"
    return str(target)


def event_identity(event: EventSpec | ParameterizedEventSpec) -> tuple[str, str]:
    """Event identity is (event_type, target); two-state events have no target."""
    if isinstance(event, ParameterizedEventSpec):
        return (event.event_type.value, event_target_key(event.target))
    return (event.event_type.value, event_target_key(None))


def event_dimension(event: EventSpec | ParameterizedEventSpec) -> ParameterDimension | None:
    if isinstance(event, ParameterizedEventSpec):
        return event.dimension
    return None


def identity_key(identity: tuple[str, str]) -> str:
    return f"{identity[0]}|{identity[1]}"


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
        transition = TWO_STATE_TRANSITIONS.get(self.event_type)
        if transition is None:
            raise ValueError(
                f"{self.event_type.value} is a parameterized event and requires a target, not a state transition"
            )
        required, expected = transition
        if self.expected_state is not expected or self.required_state is not required:
            raise ValueError(f"invalid transition for {self.event_type.value}")
        return self


class NumericParameterConfig(BaseModel):
    """Declared range, unit and tolerance for a numeric dimension such as brightness.

    ``track_start_px``/``track_end_px`` are device-specific calibration values for the
    on-screen slider track (x positions of the declared minimum and maximum); without
    them the adapter falls back to a 3% inset of the matched slider container.
    """

    model_config = ConfigDict(extra="forbid")
    range: tuple[int, int]
    unit: str = Field(min_length=1, max_length=16)
    tolerance: int = Field(default=0, ge=0)
    track_start_px: int | None = Field(default=None, ge=0)
    track_end_px: int | None = Field(default=None, ge=0)

    @field_validator("range")
    @classmethod
    def validate_range(cls, value: tuple[int, int]) -> tuple[int, int]:
        low, high = value
        if low < 0 or high < low:
            raise ValueError("numeric range must be non-negative and ordered")
        return value

    @model_validator(mode="after")
    def validate_track(self) -> NumericParameterConfig:
        if (self.track_start_px is None) != (self.track_end_px is None):
            raise ValueError("track_start_px and track_end_px must be declared together")
        if self.track_start_px is not None and self.track_end_px is not None \
                and self.track_end_px <= self.track_start_px:
            raise ValueError("track_end_px must be greater than track_start_px")
        return self


class ScenePreset(BaseModel):
    """Numeric App readback expected after selecting one lamp scene."""

    model_config = ConfigDict(extra="forbid")
    brightness: int
    color_temperature: int


class SceneParameterConfig(BaseModel):
    """Scene ids and optional, calibrated App readback signatures."""

    model_config = ConfigDict(extra="forbid")
    scenes: list[str] = Field(min_length=1, max_length=16)
    presets: dict[str, ScenePreset] = Field(default_factory=dict)

    @field_validator("scenes")
    @classmethod
    def validate_scenes(cls, value: list[str]) -> list[str]:
        cleaned = [scene.strip() for scene in value]
        if any(not scene for scene in cleaned):
            raise ValueError("scene ids must be non-empty")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("scene ids must be unique")
        return cleaned

    @model_validator(mode="after")
    def validate_presets(self) -> SceneParameterConfig:
        if self.presets and set(self.presets) != set(self.scenes):
            raise ValueError("scene presets must cover exactly the declared scene ids")
        return self


class ParameterSettings(BaseModel):
    """Device-level declarations for parameterized events; unset dimensions stay unusable."""

    model_config = ConfigDict(extra="forbid")
    brightness: NumericParameterConfig | None = None
    color_temperature: NumericParameterConfig | None = None
    scene: SceneParameterConfig | None = None

    @model_validator(mode="after")
    def validate_scene_readbacks(self) -> ParameterSettings:
        if self.scene is None or not self.scene.presets:
            return self
        if self.brightness is None or self.color_temperature is None:
            raise ValueError("scene presets require brightness and color_temperature declarations")
        brightness_low, brightness_high = self.brightness.range
        temperature_low, temperature_high = self.color_temperature.range
        for name, preset in self.scene.presets.items():
            if not brightness_low <= preset.brightness <= brightness_high:
                raise ValueError(f"scene {name} brightness is outside the declared range")
            if not temperature_low <= preset.color_temperature <= temperature_high:
                raise ValueError(f"scene {name} color_temperature is outside the declared range")
        presets = list(self.scene.presets.items())
        for index, (first_name, first) in enumerate(presets):
            for second_name, second in presets[index + 1:]:
                if (
                    abs(first.brightness - second.brightness) <= 2 * self.brightness.tolerance
                    and abs(first.color_temperature - second.color_temperature)
                    <= 2 * self.color_temperature.tolerance
                ):
                    raise ValueError(
                        f"scene presets {first_name} and {second_name} overlap within readback tolerances"
                    )
        return self

    def for_dimension(self, dimension: ParameterDimension) -> NumericParameterConfig | SceneParameterConfig | None:
        mapping = {
            ParameterDimension.BRIGHTNESS: self.brightness,
            ParameterDimension.COLOR_TEMPERATURE: self.color_temperature,
            ParameterDimension.SCENE: self.scene,
        }
        return mapping.get(dimension)


class ParameterizedEventSpec(BaseModel):
    """An event with an explicit target (value, scene id or switch state)."""

    model_config = ConfigDict(extra="forbid")
    event_type: EventType
    target: bool | int | str
    required_state: DeviceState | None = None

    @property
    def dimension(self) -> ParameterDimension:
        return EVENT_TYPE_TO_DIMENSION[self.event_type]

    @model_validator(mode="after")
    def validate_parameterized(self) -> ParameterizedEventSpec:
        dimension = EVENT_TYPE_TO_DIMENSION.get(self.event_type)
        if dimension is None:
            raise ValueError(
                f"event_type {self.event_type.value} is a two-state event and requires "
                "required_state/expected_state instead of a target"
            )
        if dimension is ParameterDimension.FOCUS_MODE:
            if not isinstance(self.target, bool):
                raise ValueError("set_focus_mode target must be a boolean (true=on, false=off)")
            if self.required_state is not None:
                raise ValueError("set_focus_mode takes no power precondition; the switch state decides")
        elif dimension is ParameterDimension.SCENE:
            if not isinstance(self.target, str) or not self.target.strip():
                raise ValueError("select_scene target must be a non-empty scene id")
            if self.required_state is None:
                self.required_state = DeviceState.ON
        else:
            if isinstance(self.target, bool) or not isinstance(self.target, int):
                raise ValueError(f"{dimension.value} target must be an integer")
            if self.required_state is None:
                self.required_state = DeviceState.ON
        if self.required_state is not None and self.required_state is DeviceState.UNKNOWN:
            raise ValueError("required_state must be on or off")
        return self


class SessionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    count: int = Field(default=1, ge=1)
    repetitions_per_event: int = Field(default=10, ge=1)
    idle_range_seconds: tuple[float, float] = (1.0, 3.0)
    cooldown_seconds: float = Field(default=1.0, ge=0)
    max_attempts: int = Field(default=1, ge=1)
    parameter_ack_timeout_seconds: float = Field(default=15.0, gt=0)

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
    adapter: str = "mi_home_desk_lamp_1s"
    phone: PhoneConfig
    app: AppConfig
    device: DeviceConfig
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    sessions: SessionConfig = Field(default_factory=SessionConfig)
    parameters: ParameterSettings = Field(default_factory=ParameterSettings)
    events: list[EventSpec | ParameterizedEventSpec]

    @model_validator(mode="after")
    def validate_events(self) -> ExperimentConfig:
        if not self.events:
            raise ValueError("at least one event is required")
        seen: set[tuple[str, str]] = set()
        for event in self.events:
            identity = event_identity(event)
            if identity in seen:
                target_note = f" with target {event.target!r}" if isinstance(event, ParameterizedEventSpec) else ""
                raise ValueError(
                    f"duplicate event {identity[0]}{target_note}; entries must be unique by (event_type, target)"
                )
            seen.add(identity)
            if isinstance(event, ParameterizedEventSpec):
                self._validate_parameterized_against_parameters(event)
        return self

    def _validate_parameterized_against_parameters(self, event: ParameterizedEventSpec) -> None:
        dimension = event.dimension
        declaration = self.parameters.for_dimension(dimension)
        if declaration is None:
            if dimension is ParameterDimension.FOCUS_MODE:
                return  # focus mode only needs its boolean target; the switch lives in app.selectors
            raise ValueError(
                f"{event.event_type.value} requires a parameters.{dimension.value} declaration "
                "(range, unit and tolerance, or the scene candidate list)"
            )
        if isinstance(declaration, NumericParameterConfig):
            low, high = declaration.range
            if not low <= event.target <= high:
                raise ValueError(
                    f"{dimension.value} target {event.target} is outside the declared range [{low}, {high}]"
                )
        else:
            if event.target not in declaration.scenes:
                raise ValueError(
                    f"scene target {event.target!r} is not one of the declared scenes {declaration.scenes}"
                )


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


class PageObservation(BaseModel):
    """A value read from the vendor app page for one parameter dimension.

    ``known=False`` means the page could not be parsed; ``value=None`` with
    ``known=True`` means the page was readable and explicitly shows no state
    (for example no scene marked as current).
    """

    model_config = ConfigDict(extra="forbid")
    dimension: ParameterDimension
    known: bool = True
    value: bool | int | str | None = None
    unit: str | None = None
    observed_at_unix_ns: int
    source: str = "vendor_app"
    detail: str = ""
    readback_values: dict[str, int] = Field(default_factory=dict)
    evidence_files: list[str] = Field(default_factory=list)


def value_hits_target(
    spec: ParameterizedEventSpec,
    value: ParameterValue | None,
    *,
    tolerance: int = 0,
) -> bool:
    """Compare an observed value against the target; numeric dims use the declared tolerance."""
    if value is None:
        return False
    dimension = spec.dimension
    if dimension is ParameterDimension.FOCUS_MODE or dimension is ParameterDimension.SCENE:
        return isinstance(value, (bool, str)) and value == spec.target
    if isinstance(value, bool) or not isinstance(value, int):
        return False
    return abs(value - spec.target) <= max(tolerance, 0)


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
    # Parameterized-event extension; all fields stay optional so legacy records parse unchanged.
    dimension: ParameterDimension | None = None
    target_value: bool | int | str | None = None
    unit: str | None = None
    tolerance: int | None = None
    observed_before: PageObservation | None = None
    observed_after: PageObservation | None = None


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
