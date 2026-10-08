from __future__ import annotations

import time
from pathlib import Path

from ..models import (
    AckEvidence,
    DeviceState,
    EventType,
    ExperimentConfig,
    PageObservation,
    ParameterDimension,
    ParameterizedEventSpec,
)
from .base import AdapterError


class SimulatedLampAdapter:
    """Deterministic adapter used by dry-run and contract tests."""

    def __init__(
        self,
        initial_state: DeviceState = DeviceState.OFF,
        *,
        initial_parameters: dict[ParameterDimension, bool | int | str | None] | None = None,
        scene_presets: dict[str, tuple[int, int]] | None = None,
        brightness_tolerance: int = 0,
        temperature_tolerance: int = 0,
    ):
        self.state = initial_state
        self.closed = False
        self.parameters: dict[ParameterDimension, bool | int | str | None] = {
            ParameterDimension.BRIGHTNESS: 50,
            ParameterDimension.COLOR_TEMPERATURE: 4000,
            ParameterDimension.SCENE: None,
            ParameterDimension.FOCUS_MODE: False,
        }
        if initial_parameters:
            self.parameters.update(initial_parameters)
        self.scene_presets = scene_presets or {}
        self.brightness_tolerance = brightness_tolerance
        self.temperature_tolerance = temperature_tolerance
        self.action_count = 0

    @classmethod
    def for_experiment(cls, experiment: ExperimentConfig) -> SimulatedLampAdapter:
        """Choose initial states so that no configured target is already reached."""
        two_state = [event for event in experiment.events if not isinstance(event, ParameterizedEventSpec)]
        parameterized = [event for event in experiment.events if isinstance(event, ParameterizedEventSpec)]
        if two_state:
            initial_state = two_state[0].required_state
        elif parameterized:
            required = parameterized[0].required_state
            initial_state = required if required is not None else DeviceState.OFF
        else:
            initial_state = DeviceState.OFF
        initial: dict[ParameterDimension, bool | int | str | None] = {}
        for dimension in (ParameterDimension.BRIGHTNESS, ParameterDimension.COLOR_TEMPERATURE):
            targets = [event.target for event in parameterized if event.dimension is dimension]
            declaration = experiment.parameters.for_dimension(dimension)
            value = None
            if targets and declaration is not None:
                low, high = declaration.range
                candidate = max(low, min(targets) - declaration.tolerance - 1)
                if candidate < low or any(abs(candidate - target) <= declaration.tolerance for target in targets):
                    candidate = min(high, max(targets) + declaration.tolerance + 1)
                if low <= candidate <= high:
                    value = candidate
            elif declaration is not None:
                value = declaration.range[0]
            if value is not None:
                initial[dimension] = value
        scene_targets = [event.target for event in parameterized if event.dimension is ParameterDimension.SCENE]
        if scene_targets:
            initial[ParameterDimension.SCENE] = None  # default mode: no scene marked
        focus_targets = [event.target for event in parameterized if event.dimension is ParameterDimension.FOCUS_MODE]
        if focus_targets:
            initial[ParameterDimension.FOCUS_MODE] = not focus_targets[0]
        presets = experiment.parameters.scene.presets if experiment.parameters.scene else {}
        return cls(
            initial_state,
            initial_parameters=initial,
            scene_presets={name: (preset.brightness, preset.color_temperature) for name, preset in presets.items()},
            brightness_tolerance=experiment.parameters.brightness.tolerance if experiment.parameters.brightness else 0,
            temperature_tolerance=(
                experiment.parameters.color_temperature.tolerance
                if experiment.parameters.color_temperature else 0
            ),
        )

    def launch_and_open_device(self) -> None:
        if self.closed:
            raise RuntimeError("adapter is closed")

    def read_state(self) -> DeviceState:
        return self.state

    def read_parameter(
        self,
        dimension: ParameterDimension,
        *,
        evidence: tuple[Path, str] | None = None,
    ) -> PageObservation:
        if self.closed:
            raise AdapterError("adapter is closed", code="adapter_closed")
        value = self.parameters.get(dimension)
        readback_values: dict[str, int] = {}
        if dimension is ParameterDimension.SCENE and self.scene_presets:
            brightness = self.parameters[ParameterDimension.BRIGHTNESS]
            temperature = self.parameters[ParameterDimension.COLOR_TEMPERATURE]
            if isinstance(brightness, int) and isinstance(temperature, int):
                readback_values = {"brightness": brightness, "color_temperature": temperature}
                matches = [
                    name for name, (preset_brightness, preset_temperature) in self.scene_presets.items()
                    if abs(brightness - preset_brightness) <= self.brightness_tolerance
                    and abs(temperature - preset_temperature) <= self.temperature_tolerance
                ]
                value = matches[0] if len(matches) == 1 else None
        observation = PageObservation(
            dimension=dimension,
            known=True,
            value=value,
            observed_at_unix_ns=time.time_ns(),
            source="vendor_app",
            detail="simulated",
            readback_values=readback_values,
        )
        if evidence is not None:
            self._write_evidence(evidence[0], evidence[1])
        return observation

    def perform_parameterized_event(self, spec: ParameterizedEventSpec) -> None:
        if self.closed:
            raise AdapterError("adapter is closed", code="adapter_closed")
        if spec.required_state is not None and self.state is not spec.required_state:
            raise AdapterError(
                f"simulated lamp is {self.state.value}, {spec.event_type.value} requires "
                f"{spec.required_state.value}",
                code="parameter_precondition",
            )
        current = self.parameters.get(spec.dimension)
        if current == spec.target:
            raise AdapterError(
                f"{spec.dimension.value} already at target", code="parameter_precondition"
            )
        self.action_count += 1
        self.parameters[spec.dimension] = spec.target
        if spec.dimension is ParameterDimension.SCENE and spec.target in self.scene_presets:
            brightness, temperature = self.scene_presets[spec.target]
            self.parameters[ParameterDimension.BRIGHTNESS] = brightness
            self.parameters[ParameterDimension.COLOR_TEMPERATURE] = temperature

    def wait_for_parameter(
        self,
        spec: ParameterizedEventSpec,
        timeout_seconds: float,
        *,
        evidence: tuple[Path, str] | None = None,
    ) -> PageObservation:
        observation = self.read_parameter(spec.dimension)
        if evidence is not None:
            self._write_evidence(evidence[0], evidence[1])
        return observation

    def perform_event(self, event_type: EventType) -> None:
        self.state = {
            EventType.TURN_ON: DeviceState.ON,
            EventType.TURN_OFF: DeviceState.OFF,
            EventType.PLAY_MUSIC: DeviceState.PLAYING,
            EventType.PAUSE_MUSIC: DeviceState.PAUSED,
        }[event_type]

    def wait_for_ack(self, expected_state: DeviceState, timeout_seconds: float) -> AckEvidence:
        return AckEvidence(
            acknowledged=self.state is expected_state,
            observed_state=self.state,
            observed_at_unix_ns=time.time_ns(),
            message="simulated",
        )

    def recover_navigation(self) -> None:
        return None

    def capture_diagnostics(self, destination: Path, event_id: str) -> None:
        self._write_evidence(destination, event_id)

    def _write_evidence(self, destination: Path, tag: str) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        (destination / f"{tag}.txt").write_text(
            f"simulated adapter; state={self.state.value}; parameters="
            + ",".join(f"{key.value}={value!r}" for key, value in self.parameters.items())
            + "\n",
            encoding="utf-8",
        )

    def close(self) -> None:
        self.closed = True
