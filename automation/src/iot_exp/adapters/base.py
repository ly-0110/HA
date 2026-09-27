from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..models import (
    AckEvidence,
    DeviceState,
    EventType,
    PageObservation,
    ParameterDimension,
    ParameterizedEventSpec,
)


class AdapterError(RuntimeError):
    def __init__(self, message: str, *, code: str = "adapter_error"):
        super().__init__(message)
        self.code = code


class VendorAppAdapter(Protocol):
    def launch_and_open_device(self) -> None: ...

    def read_state(self) -> DeviceState: ...

    def perform_event(self, event_type: EventType) -> None: ...

    def wait_for_ack(self, expected_state: DeviceState, timeout_seconds: float) -> AckEvidence: ...

    def recover_navigation(self) -> None: ...

    def capture_diagnostics(self, destination: Path, event_id: str) -> None: ...

    def close(self) -> None: ...


class ParameterizedAdapter(Protocol):
    """Incremental capability for adapters that control value/scene/switch dimensions.

    Adapters that do not implement this protocol must refuse parameterized events
    before sending any device action.
    """

    def read_parameter(
        self,
        dimension: ParameterDimension,
        *,
        evidence: tuple[Path, str] | None = None,
    ) -> PageObservation: ...

    def perform_parameterized_event(self, spec: ParameterizedEventSpec) -> None: ...

    def wait_for_parameter(
        self,
        spec: ParameterizedEventSpec,
        timeout_seconds: float,
        *,
        evidence: tuple[Path, str] | None = None,
    ) -> PageObservation: ...
