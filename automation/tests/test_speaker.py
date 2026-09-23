import io
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from iot_exp.adapters.base import AdapterError
from iot_exp.adapters.mi_home_speaker import MiHomeTouchscreenSpeakerAdapter, classify_playback_icon
from iot_exp.config import load_configuration
from iot_exp.models import DeviceState, EventType

ROOT = Path(__file__).parents[1]


def _icon(state):
    image = Image.new("L", (112, 112), 220)
    draw = ImageDraw.Draw(image)
    if state is DeviceState.PAUSED:
        draw.polygon([(46, 38), (79, 56), (46, 74)], fill=25)
    else:
        draw.rectangle((35, 38, 44, 74), fill=25)
        draw.rectangle((67, 38, 76, 74), fill=25)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class Element:
    def __init__(self, driver):
        self.driver = driver
        self.rect = {"x": 0, "y": 0, "width": 112, "height": 112}

    def click(self):
        self.driver.state = DeviceState.PLAYING if self.driver.state is DeviceState.PAUSED else DeviceState.PAUSED


class Driver:
    def __init__(self):
        self.state = DeviceState.PAUSED
        self.button = Element(self)

    def find_element(self, _by, value):
        if "小爱触屏音箱" in value or "@clickable='true'" in value:
            return self.button
        raise LookupError(value)

    def get_screenshot_as_png(self):
        return _icon(self.state)


def test_icon_classifier_distinguishes_real_observation_from_command():
    rect = {"x": 0, "y": 0, "width": 112, "height": 112}
    assert classify_playback_icon(_icon(DeviceState.PAUSED), rect) is DeviceState.PAUSED
    assert classify_playback_icon(_icon(DeviceState.PLAYING), rect) is DeviceState.PLAYING


def test_speaker_adapter_guards_precondition_and_clicks_button():
    experiment, _runtime = load_configuration(
        ROOT / "experiment" / "xiaomi_touchscreen_speaker_music.yaml",
        ROOT / "runtime" / "ubuntu-dev.yaml",
    )
    driver = Driver()
    adapter = MiHomeTouchscreenSpeakerAdapter(
        experiment.app, experiment.phone, appium_url="http://localhost:4723", driver=driver,
    )
    assert adapter.read_state() is DeviceState.PAUSED
    with pytest.raises(AdapterError, match="state changed"):
        adapter.perform_event(EventType.PAUSE_MUSIC)
    adapter.perform_event(EventType.PLAY_MUSIC)
    assert adapter.read_state() is DeviceState.PLAYING
