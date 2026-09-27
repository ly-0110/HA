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


README_PARAMETERS_SNIPPET = """\
experiment_id: readme_example
phone: {phone_id: p, udid: u}
app: {package: com.x}
device: {device_id: d}
parameters:
  brightness: {range: [1, 100], unit: "%", tolerance: 2}
  color_temperature: {range: [2600, 5100], unit: "K", tolerance: 100}
  scene:
    scenes: [电脑模式, 温馨模式, 休闲模式, 办公模式, 阅读模式, 娱乐模式]
    presets:
      电脑模式: {brightness: 50, color_temperature: 2700}
      温馨模式: {brightness: 60, color_temperature: 3500}
      休闲模式: {brightness: 50, color_temperature: 4000}
      办公模式: {brightness: 100, color_temperature: 4500}
      阅读模式: {brightness: 100, color_temperature: 5000}
      娱乐模式: {brightness: 80, color_temperature: 3000}
events:
  - {event_type: set_brightness, target: 30}
  - {event_type: set_color_temperature, target: 3500}
  - {event_type: select_scene, target: 阅读模式}
  - {event_type: set_focus_mode, target: true}
"""


def test_readme_parameters_example_parses_exactly_as_documented(tmp_path):
    """The README 10.2 snippet must keep the documented meaning when parsed."""
    experiment = tmp_path / "exp.yaml"
    experiment.write_text(README_PARAMETERS_SNIPPET, encoding="utf-8")
    parsed, _runtime = load_configuration(experiment, ROOT / "runtime" / "windows-dev.yaml")
    assert parsed.parameters.brightness.range == (1, 100)
    assert parsed.parameters.brightness.unit == "%"
    assert parsed.parameters.brightness.tolerance == 2
    assert parsed.parameters.color_temperature.unit == "K"
    assert len(parsed.parameters.scene.scenes) == 6
    assert parsed.parameters.scene.presets["阅读模式"].color_temperature == 5000
    assert parsed.events[0].target == 30
    assert parsed.events[1].target == 3500
    assert parsed.events[2].target == "阅读模式"
    assert parsed.events[3].target is True
    # 越界示例：把亮度目标改成 120 后必须被拒绝，与文档描述一致。
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text(README_PARAMETERS_SNIPPET.replace("target: 30", "target: 120"), encoding="utf-8")
    with pytest.raises(ConfigError, match="outside the declared range"):
        load_configuration(invalid, ROOT / "runtime" / "windows-dev.yaml")
