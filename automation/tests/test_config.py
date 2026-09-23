from pathlib import Path

import pytest

from iot_exp.config import ConfigError, load_configuration
from iot_exp.models import DeviceState, EventType

ROOT = Path(__file__).parents[1]


def test_example_configuration_loads():
    experiment, runtime = load_configuration(
        ROOT / "experiment" / "mi_desk_lamp_1s.yaml",
        ROOT / "runtime" / "windows-dev.yaml",
    )
    assert experiment.device.device_id == "desk_lamp_1s"
    assert experiment.events[0].event_type is EventType.TURN_ON
    assert experiment.events[0].required_state is DeviceState.OFF
    assert runtime.capture_mode.value == "disabled"


def test_formal_runtime_requires_interface():
    path = ROOT / "tests" / "_formal.yaml"
    path.write_text("mode: formal\ncapture_mode: dumpcap\ncapture_interface: null\n", encoding="utf-8")
    try:
        with pytest.raises(ConfigError):
            load_configuration(ROOT / "experiment" / "mi_desk_lamp_1s.yaml", path)
    finally:
        path.unlink(missing_ok=True)


def test_invalid_transition_is_rejected(tmp_path):
    experiment = tmp_path / "experiment.yaml"
    runtime = tmp_path / "runtime.yaml"
    experiment.write_text(
        """experiment_id: x
phone: {phone_id: p, udid: u}
app: {package: com.x}
device: {device_id: d}
events:
  - {event_type: turn_on, required_state: "on", expected_state: "on"}
""",
        encoding="utf-8",
    )
    runtime.write_text("mode: dev\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_configuration(experiment, runtime)


def test_speaker_configuration_has_real_music_transitions():
    experiment, _runtime = load_configuration(
        ROOT / "experiment" / "xiaomi_touchscreen_speaker_music.yaml",
        ROOT / "runtime" / "ubuntu-dev.yaml",
    )
    assert experiment.device.entity_id == "media_player.xiaomi_cn_636575596_lx04"
    assert [(event.event_type, event.required_state, event.expected_state) for event in experiment.events] == [
        (EventType.PLAY_MUSIC, DeviceState.PAUSED, DeviceState.PLAYING),
        (EventType.PAUSE_MUSIC, DeviceState.PLAYING, DeviceState.PAUSED),
    ]


def test_speaker_event_cannot_use_lamp_state(tmp_path):
    experiment = tmp_path / "speaker.yaml"
    experiment.write_text(
        """experiment_id: speaker
phone: {phone_id: p, udid: u}
app: {package: com.x}
device: {device_id: d}
events:
  - {event_type: play_music, required_state: "off", expected_state: "on"}
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_configuration(experiment, ROOT / "runtime" / "ubuntu-dev.yaml")
