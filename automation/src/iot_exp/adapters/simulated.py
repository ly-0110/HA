from __future__ import annotations

import time
from pathlib import Path

from ..models import AckEvidence, DeviceState, EventType


class SimulatedLampAdapter:
    """Deterministic adapter used by dry-run and contract tests."""

    def __init__(self, initial_state: DeviceState = DeviceState.OFF):
        self.state = initial_state
        self.closed = False

    def launch_and_open_device(self) -> None:
        if self.closed:
            raise RuntimeError("adapter is closed")

    def read_state(self) -> DeviceState:
        return self.state

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
        destination.mkdir(parents=True, exist_ok=True)
        (destination / f"{event_id}.txt").write_text("simulated adapter\n", encoding="utf-8")

    def close(self) -> None:
        self.closed = True
