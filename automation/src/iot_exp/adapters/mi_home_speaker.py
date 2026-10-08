from __future__ import annotations

import io
import time
from pathlib import Path
from typing import Any

from PIL import Image

from ..models import AckEvidence, DeviceState, EventType
from .base import AdapterError
from .mi_home_lamp import MiHomeDeskLamp1SAdapter, SelectorError


def classify_playback_icon(png: bytes, rect: dict[str, Any]) -> DeviceState:
    """Read the triangle/bars in the LX04 playback button, not the last command."""
    image = Image.open(io.BytesIO(png)).convert("L")
    x = int(rect["x"])
    y = int(rect["y"])
    width = int(rect["width"])
    height = int(rect["height"])
    if width < 32 or height < 32:
        return DeviceState.UNKNOWN
    if x < 0 or y < 0 or x + width > image.width or y + height > image.height:
        return DeviceState.UNKNOWN
    icon = image.crop((x, y, x + width, y + height)).resize((112, 112))
    row_counts = []
    for row in (40, 44, 48, 52, 56, 60, 64, 68, 72):
        dark = [column for column in range(25, 86) if icon.getpixel((column, row)) < 120]
        runs: list[list[int]] = []
        for column in dark:
            if not runs or column > runs[-1][-1] + 1:
                runs.append([column])
            else:
                runs[-1].append(column)
        row_counts.append(len([run for run in runs if len(run) >= 3]))
    if row_counts.count(2) >= 7:
        return DeviceState.PLAYING  # The button displays two pause bars.
    if row_counts.count(1) >= 7:
        return DeviceState.PAUSED  # The button displays one play triangle.
    return DeviceState.UNKNOWN


class MiHomeTouchscreenSpeakerAdapter(MiHomeDeskLamp1SAdapter):
    """Read and control LX04 music on its Mi Home device page."""

    def launch_and_open_device(self) -> None:
        self.connect()
        assert self.driver is not None
        self.driver.activate_app(self.app.package)
        for _ in range(4):
            self._dismiss_known_popups()
            if self._find_optional("device_page_marker") is not None:
                return
            entry = self._find_optional("device_entry")
            if entry is not None:
                entry.click()
                for _ in range(20):
                    if self._find_optional("device_page_marker") is not None:
                        return
                    time.sleep(0.25)
                break
            self.driver.back()
            time.sleep(0.5)
        raise AdapterError("unable to open Mi Home speaker page", code="open_device")

    def read_state(self) -> DeviceState:
        self._dismiss_known_popups()
        if self._find_optional("device_page_marker") is None:
            return DeviceState.UNKNOWN
        button = self._find_optional("playback_button")
        if button is None:
            return DeviceState.UNKNOWN
        assert self.driver is not None
        return classify_playback_icon(self.driver.get_screenshot_as_png(), button.rect)

    def perform_event(self, event_type: EventType) -> None:
        required = {
            EventType.PLAY_MUSIC: DeviceState.PAUSED,
            EventType.PAUSE_MUSIC: DeviceState.PLAYING,
        }.get(event_type)
        if required is None:
            raise SelectorError(f"unsupported speaker event: {event_type.value}", code="speaker_event")
        observed = self.read_state()
        if observed is not required:
            raise AdapterError(
                f"speaker state changed before {event_type.value}: {observed.value}",
                code="speaker_precondition_changed",
            )
        try:
            self._find("playback_button").click()
        except Exception as exc:
            raise SelectorError(f"cannot click playback button: {exc}", code="event_control_not_found") from exc

    def capture_ack_evidence(self, destination: Path, event_id: str) -> None:
        self.capture_diagnostics(destination, event_id)

    def wait_for_ack(self, expected_state: DeviceState, timeout_seconds: float) -> AckEvidence:
        return super().wait_for_ack(expected_state, min(timeout_seconds, 10))
