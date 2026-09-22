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
