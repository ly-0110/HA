"""Fake-driver tests for the Mi Home desk lamp parameterized controls."""

from pathlib import Path

import pytest
from fake_lamp_driver import SCENE_PRESETS, SCENES, FakeDriver

from iot_exp.adapters.base import AdapterError
from iot_exp.adapters.mi_home_lamp import MiHomeDeskLamp1SAdapter
from iot_exp.config import load_configuration
from iot_exp.models import (
    DeviceState,
    ParameterDimension,
    ParameterizedEventSpec,
    ParameterSettings,
)

ROOT = Path(__file__).parents[1]
BRIGHTNESS = ParameterDimension.BRIGHTNESS
COLOR_TEMPERATURE = ParameterDimension.COLOR_TEMPERATURE
SCENE = ParameterDimension.SCENE
FOCUS = ParameterDimension.FOCUS_MODE


def _adapter(driver: FakeDriver, tmp_path: Path) -> MiHomeDeskLamp1SAdapter:
    experiment, _runtime = load_configuration(
        ROOT / "experiment" / "mi_desk_lamp_1s_advanced.yaml",
        ROOT / "runtime" / "windows-dev.yaml",
    )
    return MiHomeDeskLamp1SAdapter(
        experiment.app,
        experiment.phone,
        appium_url="http://127.0.0.1:4723",
        driver=driver,
        parameters=experiment.parameters,
        screenshot_dir=tmp_path,
    )


def test_brightness_read_and_slide_round_trip(tmp_path):
    driver = FakeDriver(power_on=True, brightness=60)
    adapter = _adapter(driver, tmp_path)
    observation = adapter.read_parameter(BRIGHTNESS)
    assert observation.known and observation.value == 60
    assert observation.unit == "%"
    spec = ParameterizedEventSpec(event_type="set_brightness", target=30)
    adapter.perform_parameterized_event(spec)
    assert driver.swipes, "brightness event must slide the slider"
    after = adapter.wait_for_parameter(spec, 2.0)
    assert after.known and after.value == 30


def test_brightness_slides_to_both_boundaries(tmp_path):
    for target in (1, 100):
        driver = FakeDriver(power_on=True, brightness=60)
        adapter = _adapter(driver, tmp_path)
        spec = ParameterizedEventSpec(event_type="set_brightness", target=target)
        adapter.perform_parameterized_event(spec)
        value = adapter.read_parameter(BRIGHTNESS).value
        assert value is not None and abs(value - target) <= 3


def test_brightness_reverse_adjustment_and_tolerance(tmp_path):
    driver = FakeDriver(power_on=True, brightness=80)
    adapter = _adapter(driver, tmp_path)
    spec = ParameterizedEventSpec(event_type="set_brightness", target=30)
    adapter.perform_parameterized_event(spec)
    swipe = driver.swipes[-1]
    assert swipe["end_x"] < swipe["start_x"], "80->30 must drag left"
    assert adapter.read_parameter(BRIGHTNESS).value == 30
    # A read inside the declared tolerance counts as a hit for wait purposes.
    driver.brightness_value = 31
    assert adapter.wait_for_parameter(spec, 1.0).value == 31


def test_brightness_unreadable_refuses_to_slide(tmp_path):
    driver = FakeDriver(power_on=True, brightness=None)
    adapter = _adapter(driver, tmp_path)
    spec = ParameterizedEventSpec(event_type="set_brightness", target=30)
    with pytest.raises(AdapterError) as excinfo:
        adapter.perform_parameterized_event(spec)
    assert excinfo.value.code == "parameter_unreadable"
    assert driver.swipes == []
    assert adapter.read_parameter(BRIGHTNESS).known is False


def test_brightness_evidence_capture(tmp_path):
    driver = FakeDriver(power_on=True, brightness=60)
    adapter = _adapter(driver, tmp_path)
    adapter.read_parameter(BRIGHTNESS, evidence=(tmp_path, "evt_before"))
    assert (tmp_path / "evt_before.xml").exists()
    assert (tmp_path / "evt_before.png").exists()


def test_color_temperature_read_reports_kelvin_unit(tmp_path):
    driver = FakeDriver(power_on=True, color_temperature=5078)
    adapter = _adapter(driver, tmp_path)
    observation = adapter.read_parameter(COLOR_TEMPERATURE)
    assert observation.known and observation.value == 5078
    assert observation.unit == "K"


def test_color_temperature_slides_to_both_boundaries(tmp_path):
    experiment, _runtime = load_configuration(
        ROOT / "experiment" / "mi_desk_lamp_1s_advanced.yaml",
        ROOT / "runtime" / "windows-dev.yaml",
    )
    low, high = experiment.parameters.color_temperature.range
    tolerance = experiment.parameters.color_temperature.tolerance
    for target in (low, high):
        driver = FakeDriver(power_on=True, color_temperature=4000)
        adapter = _adapter(driver, tmp_path)
        spec = ParameterizedEventSpec(event_type="set_color_temperature", target=target)
        adapter.perform_parameterized_event(spec)
        value = adapter.read_parameter(COLOR_TEMPERATURE).value
        assert value is not None and abs(value - target) <= tolerance


def test_color_temperature_timeout_keeps_last_reading(tmp_path):
    driver = FakeDriver(power_on=True, color_temperature=5078, ignore_swipes=True)
    adapter = _adapter(driver, tmp_path)
    spec = ParameterizedEventSpec(event_type="set_color_temperature", target=3200)
    adapter.perform_parameterized_event(spec)
    observation = adapter.wait_for_parameter(spec, 0.8)
    assert observation.known and observation.value == 5078
    assert observation.value != spec.target


def test_six_scene_buttons_map_to_numeric_readback_without_selected_attributes(tmp_path):
    for scene in SCENES:
        driver = FakeDriver(power_on=True)
        adapter = _adapter(driver, tmp_path)
        assert adapter.read_parameter(SCENE).value is None  # default mode marks nothing
        spec = ParameterizedEventSpec(event_type="select_scene", target=scene)
        adapter.perform_parameterized_event(spec)
        observation = adapter.wait_for_parameter(spec, 1.0)
        assert observation.known and observation.value == scene
        brightness, temperature = SCENE_PRESETS[scene]
        assert observation.readback_values == {
            "brightness": brightness, "color_temperature": temperature,
        }
        assert 'selected="true"' not in driver.page_source
        assert 'checked="true"' not in driver.page_source


def test_scene_unrecognizable_is_not_a_hit(tmp_path):
    driver = FakeDriver(power_on=True, mark_scene_on_click=False)
    adapter = _adapter(driver, tmp_path)
    spec = ParameterizedEventSpec(event_type="select_scene", target=SCENES[0])
    adapter.perform_parameterized_event(spec)
    observation = adapter.wait_for_parameter(spec, 0.8)
    assert observation.known and observation.value is None


def test_scene_numeric_readback_missing_is_unknown(tmp_path):
    driver = FakeDriver(power_on=True, brightness=None)
    adapter = _adapter(driver, tmp_path)
    observation = adapter.read_parameter(SCENE)
    assert observation.known is False
    assert observation.value is None


def test_focus_read_toggles_and_returns_to_device_page(tmp_path):
    driver = FakeDriver(power_on=True, focus_on=False)
    adapter = _adapter(driver, tmp_path)
    observation = adapter.read_parameter(FOCUS)
    assert observation.known and observation.value is False
    assert driver.page == "device", "reading focus must return to the device page"
    on_spec = ParameterizedEventSpec(event_type="set_focus_mode", target=True)
    adapter.perform_parameterized_event(on_spec)
    after = adapter.wait_for_parameter(on_spec, 1.0)
    assert after.known and after.value is True
    assert driver.page == "device"
    off_spec = ParameterizedEventSpec(event_type="set_focus_mode", target=False)
    adapter.perform_parameterized_event(off_spec)
    assert adapter.wait_for_parameter(off_spec, 1.0).value is False


def test_focus_already_at_target_refuses(tmp_path):
    driver = FakeDriver(power_on=True, focus_on=True)
    adapter = _adapter(driver, tmp_path)
    spec = ParameterizedEventSpec(event_type="set_focus_mode", target=True)
    with pytest.raises(AdapterError) as excinfo:
        adapter.perform_parameterized_event(spec)
    assert excinfo.value.code == "parameter_precondition"
    assert all("focus_switch" not in click for click in driver.clicks)


def test_focus_settings_page_unreachable_is_an_error(tmp_path):
    driver = FakeDriver(power_on=True)
    driver.settings_page_available = False
    adapter = _adapter(driver, tmp_path)
    spec = ParameterizedEventSpec(event_type="set_focus_mode", target=True)
    with pytest.raises(AdapterError) as excinfo:
        adapter.perform_parameterized_event(spec)
    assert excinfo.value.code == "focus_navigation"
    assert all("focus_switch" not in click for click in driver.clicks)


def test_focus_navigation_recovers_after_failed_return(tmp_path):
    driver = FakeDriver(power_on=True, focus_on=False, hide_device_marker=False)
    driver.hide_device_marker = True
    adapter = _adapter(driver, tmp_path)
    observation = adapter.read_parameter(FOCUS)
    assert observation.known and observation.value is False
    assert driver.page == "device"
    assert driver.hide_device_marker is False, "launch_and_open_device must clear the broken state"


def test_focus_page_evidence_written_while_on_settings(tmp_path):
    driver = FakeDriver(power_on=True)
    adapter = _adapter(driver, tmp_path)
    adapter.read_parameter(FOCUS, evidence=(tmp_path, "focus_evt"))
    xml = (tmp_path / "focus_evt.xml").read_text(encoding="utf-8")
    assert "专注模式" in xml, "focus evidence must show the settings page"


def test_inspect_pages_exports_all_pages(tmp_path):
    driver = FakeDriver(power_on=True, brightness=60, color_temperature=5078)
    adapter = _adapter(driver, tmp_path)
    summary = adapter.inspect_pages(tmp_path)
    pages = {entry["page"]: entry for entry in summary["pages"]}
    assert pages["device_page"]["state"] == "on"
    assert pages["brightness"]["value"] == 60
    assert pages["color_temperature"]["value"] == 5078
    assert set(pages["scenes"]["buttons"].values()) == {"found"}
    assert pages["focus_page"]["value"] is False
    for name in ("device_page", "scenes_page", "focus_page"):
        assert (tmp_path / f"{name}.xml").exists()
        assert (tmp_path / f"{name}.png").exists()


def test_unconfigured_parameters_refuse_actions(tmp_path):
    driver = FakeDriver(power_on=True)
    experiment, _runtime = load_configuration(
        ROOT / "experiment" / "mi_desk_lamp_1s_advanced.yaml",
        ROOT / "runtime" / "windows-dev.yaml",
    )
    adapter = MiHomeDeskLamp1SAdapter(
        experiment.app,
        experiment.phone,
        appium_url="http://127.0.0.1:4723",
        driver=driver,
        parameters=ParameterSettings(),
        screenshot_dir=tmp_path,
    )
    observation = adapter.read_parameter(BRIGHTNESS)
    assert observation.known and observation.value == 60
    assert observation.unit is None, "no unit can be claimed without a declaration"
    spec = ParameterizedEventSpec(event_type="set_brightness", target=30)
    with pytest.raises(AdapterError) as excinfo:
        adapter.perform_parameterized_event(spec)
    assert excinfo.value.code == "parameter_unconfigured"
    assert driver.swipes == []


def test_device_page_power_path_still_works(tmp_path):
    driver = FakeDriver(power_on=True)
    adapter = _adapter(driver, tmp_path)
    assert adapter.read_state() is DeviceState.ON
    adapter.perform_event("turn_off")
    assert adapter.read_state() is DeviceState.OFF
    assert adapter.wait_for_ack(DeviceState.OFF, 1.0).acknowledged is True
