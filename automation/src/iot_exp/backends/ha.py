from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from ..models import DeviceState, HaEvidence


class HaObservationProvider(Protocol):
    enabled: bool

    def start(self, session_root: Path) -> None: ...

    def await_state(self, entity_id: str | None, expected_state: DeviceState, timeout_seconds: float) -> HaEvidence | None: ...

    def stop(self) -> None: ...


class DisabledHaProvider:
    enabled = False

    def __init__(self):
        self.session_root: Path | None = None

    def start(self, session_root: Path) -> None:
        self.session_root = session_root
        (session_root / "ha_events.json").write_text(
            json.dumps({"enabled": False, "events": []}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def await_state(self, entity_id: str | None, expected_state: DeviceState, timeout_seconds: float) -> HaEvidence | None:
        return None

    def stop(self) -> None:
        return None
