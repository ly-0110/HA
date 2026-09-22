from pathlib import Path

import pytest

from iot_exp.backends.system import (
    AndroidDevice,
    AppiumServer,
    discover_android_sdk,
    parse_adb_devices,
    resolve_executable,
)
from iot_exp.cli import select_device
from iot_exp.config import ConfigError


def test_parse_adb_devices_keeps_online_and_blocked_states():
    output = """List of devices attached
ABC123 device product:p model:Pixel_8 device:husky transport_id:2
XYZ789 unauthorized usb:1-2 transport_id:3
"""
    devices = parse_adb_devices(output)
    assert [(device.udid, device.state) for device in devices] == [
        ("ABC123", "device"),
        ("XYZ789", "unauthorized"),
    ]
    assert devices[0].model == "Pixel_8"
    assert devices[0].transport_id == "2"


def test_select_device_requires_explicit_choice_when_multiple_are_online():
    devices = [
        AndroidDevice("A", "device", model="One"),
        AndroidDevice("B", "device", model="Two"),
    ]
    with pytest.raises(ConfigError, match="multiple Android devices"):
        select_device(devices)
    assert select_device(devices, requested_udid="B").udid == "B"
    assert select_device(devices, interactive=True, input_fn=lambda _: "1").udid == "A"


def test_select_device_rejects_unauthorized_requested_device():
    with pytest.raises(ConfigError, match="unauthorized"):
        select_device([AndroidDevice("A", "unauthorized")], requested_udid="A")


def test_discover_android_sdk_prefers_config_then_environment(tmp_path: Path):
    configured = tmp_path / "configured-sdk"
    environment_sdk = tmp_path / "environment-sdk"
    assert discover_android_sdk(
        "adb",
        configured,
        {"ANDROID_SDK_ROOT": str(environment_sdk)},
    ) == configured.resolve()
    assert discover_android_sdk(
        "adb",
        None,
        {"ANDROID_SDK_ROOT": str(environment_sdk)},
    ) == environment_sdk.resolve()


def test_discover_android_sdk_only_infers_from_platform_tools(tmp_path: Path):
    sdk_root = tmp_path / "android-sdk"
    platform_tools = sdk_root / "platform-tools"
    platform_tools.mkdir(parents=True)
    adb = platform_tools / "adb.exe"
    adb.touch()
    assert discover_android_sdk(str(adb), environment={}) == sdk_root.resolve()

    unrelated_adb = tmp_path / "bin" / "adb.exe"
    unrelated_adb.parent.mkdir()
    unrelated_adb.touch()
    assert discover_android_sdk(str(unrelated_adb), environment={}) is None


def test_resolve_adb_from_standard_windows_sdk_location(tmp_path: Path, monkeypatch):
    adb = tmp_path / "Android" / "Sdk" / "platform-tools" / "adb.exe"
    adb.parent.mkdir(parents=True)
    adb.touch()
    monkeypatch.setattr("iot_exp.backends.system.shutil.which", lambda _: None)
    monkeypatch.delenv("ANDROID_SDK_ROOT", raising=False)
    monkeypatch.delenv("ANDROID_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert resolve_executable("adb") == adb.resolve()


def test_appium_server_uses_port_from_runtime_url(tmp_path: Path, monkeypatch):
    captured = {}

    class FakeProcess:
        returncode = None

        @staticmethod
        def poll():
            return None

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return FakeProcess()

    server = AppiumServer("appium", "http://127.0.0.1:4725", tmp_path / "appium.log")
    monkeypatch.setattr("iot_exp.backends.system.subprocess.Popen", fake_popen)
    monkeypatch.setattr(server, "_wait_for_http", lambda: None)
    server.start()
    assert captured["command"][-4:] == ["--address", "127.0.0.1", "--port", "4725"]
