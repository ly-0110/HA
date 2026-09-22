from __future__ import annotations

from .adapters import MiHomeDeskLamp1SAdapter
from .models import ExperimentConfig, RuntimeConfig


def create_adapter(experiment: ExperimentConfig, runtime: RuntimeConfig, **kwargs):
    """Create a real adapter from the explicit experiment adapter id."""
    if experiment.adapter != "mi_home_desk_lamp_1s":
        raise ValueError(f"unknown adapter: {experiment.adapter}")
    return MiHomeDeskLamp1SAdapter(
        experiment.app,
        experiment.phone,
        appium_url=runtime.appium_url,
        system_port=runtime.uiautomator2_system_port,
        **kwargs,
    )


def available_adapters() -> set[str]:
    return {"mi_home_desk_lamp_1s"}
