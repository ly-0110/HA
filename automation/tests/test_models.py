import pytest
from pydantic import ValidationError

from iot_exp.models import (
    DeviceState,
    EventSpec,
    EventType,
    ExperimentConfig,
    PageObservation,
    ParameterDimension,
    ParameterizedEventSpec,
    ParameterSettings,
    event_identity,
    event_target_key,
    value_hits_target,
)


def _base(**overrides):
    data = {
        "experiment_id": "advanced",
        "phone": {"phone_id": "p", "udid": "u"},
        "app": {"package": "com.x"},
        "device": {"device_id": "d"},
        "parameters": {
            "brightness": {"range": [1, 100], "unit": "%", "tolerance": 2},
            "color_temperature": {"range": [2700, 6500], "unit": "K", "tolerance": 100},
            "scene": {"scenes": ["电脑模式", "温馨模式", "休闲模式", "办公模式", "阅读模式", "娱乐模式"]},
        },
        "events": [],
    }
    data.update(overrides)
    return data


def test_four_target_types_and_defaults():
    brightness = ParameterizedEventSpec(event_type="set_brightness", target=30)
    color_temperature = ParameterizedEventSpec(event_type="set_color_temperature", target=4000)
    scene = ParameterizedEventSpec(event_type="select_scene", target="阅读模式")
    focus = ParameterizedEventSpec(event_type="set_focus_mode", target=True)
    assert brightness.target == 30 and brightness.dimension is ParameterDimension.BRIGHTNESS
    assert color_temperature.dimension is ParameterDimension.COLOR_TEMPERATURE
    assert scene.target == "阅读模式" and scene.dimension is ParameterDimension.SCENE
    assert focus.target is True and focus.dimension is ParameterDimension.FOCUS_MODE
    # Lamp dimensions default to "lamp on" preconditions; focus keys on its own switch state.
    assert brightness.required_state is DeviceState.ON
    assert color_temperature.required_state is DeviceState.ON
    assert scene.required_state is DeviceState.ON
    assert focus.required_state is None


def test_target_type_mismatches_are_rejected():
    with pytest.raises(ValidationError):
        ParameterizedEventSpec(event_type="set_brightness", target=True)
    with pytest.raises(ValidationError):
        ParameterizedEventSpec(event_type="set_brightness", target="30")
    with pytest.raises(ValidationError):
        ParameterizedEventSpec(event_type="select_scene", target=3)
    with pytest.raises(ValidationError):
        ParameterizedEventSpec(event_type="set_focus_mode", target=1)
    with pytest.raises(ValidationError):
        ParameterizedEventSpec(event_type="set_focus_mode", target="on")
    with pytest.raises(ValidationError):
        ParameterizedEventSpec(event_type="select_scene", target="  ")


def test_two_state_model_rejects_parameterized_types_and_vice_versa():
    EventSpec(event_type="turn_on", required_state="off", expected_state="on")
    with pytest.raises(ValidationError):
        EventSpec(event_type="set_brightness", required_state="off", expected_state="on")
    with pytest.raises(ValidationError):
        ParameterizedEventSpec(event_type="turn_on", target=1)


def test_experiment_keeps_range_unit_tolerance_and_validates_targets():
    experiment = ExperimentConfig.model_validate({
        **_base(),
        "events": [
            {"event_type": "set_brightness", "target": 30},
            {"event_type": "set_color_temperature", "target": 4000},
            {"event_type": "select_scene", "target": "阅读模式"},
            {"event_type": "set_focus_mode", "target": False},
        ],
    })
    brightness = experiment.parameters.brightness
    assert brightness.range == (1, 100) and brightness.unit == "%" and brightness.tolerance == 2
    color_temperature = experiment.parameters.color_temperature
    assert color_temperature.range == (2700, 6500) and color_temperature.unit == "K"
    assert experiment.events[0].target == 30
    assert experiment.events[3].target is False


def test_out_of_range_target_rejected():
    with pytest.raises(ValidationError, match="outside the declared range"):
        ExperimentConfig.model_validate({
            **_base(),
            "events": [{"event_type": "set_brightness", "target": 120}],
        })
    with pytest.raises(ValidationError, match="outside the declared range"):
        ExperimentConfig.model_validate({
            **_base(),
            "events": [{"event_type": "set_color_temperature", "target": 2000}],
        })


def test_scene_target_must_be_in_candidates():
    with pytest.raises(ValidationError, match="declared scenes"):
        ExperimentConfig.model_validate({
            **_base(),
            "events": [{"event_type": "select_scene", "target": "夜灯模式"}],
        })


def test_missing_parameter_declaration_rejected():
    data = _base()
    del data["parameters"]["brightness"]
    with pytest.raises(ValidationError, match="parameters.brightness"):
        ExperimentConfig.model_validate({**data, "events": [{"event_type": "set_brightness", "target": 30}]})


def test_identity_is_event_type_and_target():
    assert event_identity(ParameterizedEventSpec(event_type="set_brightness", target=30)) == ("set_brightness", "30")
    assert event_identity(ParameterizedEventSpec(event_type="set_brightness", target=80)) != (
        event_identity(ParameterizedEventSpec(event_type="set_brightness", target=30))
    )
    assert event_identity(ParameterizedEventSpec(event_type="set_focus_mode", target=True)) == ("set_focus_mode", "true")
    assert event_identity(ParameterizedEventSpec(event_type="set_focus_mode", target=False)) == ("set_focus_mode", "false")
    on = EventSpec(event_type="turn_on", required_state="off", expected_state="on")
    assert event_identity(on) == ("turn_on", "-")
    assert event_target_key(True) == "true" and event_target_key(30) == "30"


def test_duplicate_target_rejected_but_different_targets_coexist():
    events = [
        {"event_type": "set_brightness", "target": 30},
        {"event_type": "set_brightness", "target": 80},
    ]
    ExperimentConfig.model_validate({**_base(), "events": events})
    with pytest.raises(ValidationError, match="unique by \\(event_type, target\\)"):
        ExperimentConfig.model_validate({
            **_base(),
            "events": [{"event_type": "set_brightness", "target": 30}, {"event_type": "set_brightness", "target": 30}],
        })
    with pytest.raises(ValidationError, match="unique"):
        ExperimentConfig.model_validate({
            **_base(),
            "events": [
                {"event_type": "select_scene", "target": "阅读模式"},
                {"event_type": "select_scene", "target": "阅读模式"},
            ],
        })


def test_six_scene_targets_coexist():
    scenes = ["电脑模式", "温馨模式", "休闲模式", "办公模式", "阅读模式", "娱乐模式"]
    experiment = ExperimentConfig.model_validate({
        **_base(),
        "events": [{"event_type": "select_scene", "target": scene} for scene in scenes],
    })
    assert len(experiment.events) == 6
    assert len({event_identity(event)[1] for event in experiment.events}) == 6


def test_scene_preset_signatures_must_be_complete_and_unambiguous():
    data = _base()
    data["parameters"]["scene"]["presets"] = {
        "电脑模式": {"brightness": 50, "color_temperature": 2700},
    }
    with pytest.raises(ValidationError, match="cover exactly"):
        ExperimentConfig.model_validate(data)

    data["parameters"]["scene"]["scenes"] = ["电脑模式", "温馨模式"]
    data["parameters"]["scene"]["presets"]["温馨模式"] = {
        "brightness": 53, "color_temperature": 2800,
    }
    with pytest.raises(ValidationError, match="overlap within readback tolerances"):
        ExperimentConfig.model_validate(data)


def test_value_hits_target_semantics():
    spec = ParameterizedEventSpec(event_type="set_brightness", target=30)
    assert value_hits_target(spec, 31, tolerance=2)
    assert value_hits_target(spec, 28, tolerance=2)
    assert not value_hits_target(spec, 33, tolerance=2)
    assert not value_hits_target(spec, 33, tolerance=0)
    assert not value_hits_target(spec, None)
    assert not value_hits_target(spec, "30")
    scene = ParameterizedEventSpec(event_type="select_scene", target="阅读模式")
    assert value_hits_target(scene, "阅读模式", tolerance=5)  # tolerance never applies to scenes
    assert not value_hits_target(scene, "电脑模式")
    assert not value_hits_target(scene, None)
    focus = ParameterizedEventSpec(event_type="set_focus_mode", target=True)
    assert value_hits_target(focus, True)
    assert not value_hits_target(focus, False)


def test_page_observation_records_evidence_and_source():
    observation = PageObservation(
        dimension=ParameterDimension.BRIGHTNESS,
        known=True,
        value=60,
        unit="%",
        observed_at_unix_ns=123,
        evidence_files=["screenshots/e_before.xml"],
    )
    assert observation.source == "vendor_app"
    assert observation.evidence_files == ["screenshots/e_before.xml"]
    unknown = PageObservation(
        dimension=ParameterDimension.SCENE, known=False, observed_at_unix_ns=124, detail="unparseable",
    )
    assert unknown.value is None and unknown.known is False


def test_parameter_settings_default_empty():
    settings = ParameterSettings()
    assert settings.brightness is None and settings.scene is None
    assert settings.for_dimension(ParameterDimension.FOCUS_MODE) is None


def test_event_type_enum_round_trip_in_json():
    experiment = ExperimentConfig.model_validate({
        **_base(),
        "events": [
            {"event_type": "set_brightness", "target": 30},
            {"event_type": "turn_off", "required_state": "on", "expected_state": "off"},
        ],
    })
    dumped = experiment.model_dump(mode="json")
    assert dumped["events"][0]["target"] == 30
    assert dumped["events"][1]["event_type"] == "turn_off"
    reloaded = ExperimentConfig.model_validate(dumped)
    assert reloaded.events[0].target == 30
    assert isinstance(reloaded.events[1], EventSpec)
    assert reloaded.events[1].event_type is EventType.TURN_OFF
