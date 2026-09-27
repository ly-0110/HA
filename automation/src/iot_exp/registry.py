from __future__ import annotations

from .adapters import MiHomeDeskLamp1SAdapter, MiHomeTouchscreenSpeakerAdapter
from .models import ExperimentConfig, RuntimeConfig


def create_adapter(experiment: ExperimentConfig, runtime: RuntimeConfig, **kwargs):
    """Create a real adapter from the explicit experiment adapter id."""
    adapters = {
        "mi_home_desk_lamp_1s": MiHomeDeskLamp1SAdapter,
        "mi_home_touchscreen_speaker": MiHomeTouchscreenSpeakerAdapter,
    }
    adapter_class = adapters.get(experiment.adapter)
    if adapter_class is None:
        raise ValueError(f"unknown adapter: {experiment.adapter}")
    return adapter_class(
        experiment.app,
        experiment.phone,
        appium_url=runtime.appium_url,
        system_port=runtime.uiautomator2_system_port,
        parameters=experiment.parameters,
        **kwargs,
    )


def available_adapters() -> set[str]:
    return {"mi_home_desk_lamp_1s", "mi_home_touchscreen_speaker"}
