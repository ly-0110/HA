from pathlib import Path

from iot_exp.backends.system import CommandResult
from iot_exp.config import load_configuration
from iot_exp.preflight import run_preflight

ROOT = Path(__file__).parents[1]


def _configs():
    return load_configuration(
        ROOT / "experiment" / "mi_desk_lamp_1s.yaml",
        ROOT / "runtime" / "windows-dev.yaml",
    )


def test_formal_preflight_rejects_placeholder_capture_boundaries(monkeypatch):
    experiment, runtime = _configs()
    runtime = runtime.__class__.model_validate({
        **runtime.model_dump(),
        "mode": "formal",
        "capture_mode": "dumpcap",
        "capture_interface": "REPLACE_WITH_INTERFACE",
        "capture_filter": "host REPLACE_WITH_TARGET_DEVICE_IP",
    })
    monkeypatch.setattr("iot_exp.preflight.collect_system_checks", lambda **_kwargs: [])
    monkeypatch.setattr("iot_exp.preflight.discover_android_sdk", lambda *_args: None)
    report = run_preflight(experiment, runtime)
    checks = {check["name"]: check for check in report["checks"]}
    assert checks["formal_target_device_ip"]["ok"] is False
    assert checks["formal_capture_interface"]["ok"] is False
    assert checks["formal_capture_filter"]["ok"] is False
    assert report["ok"] is False


def test_address_query_failure_never_certifies_isolation(monkeypatch):
    experiment, runtime = _configs()
    experiment = experiment.model_copy(update={
        "phone": experiment.phone.model_copy(update={"udid": "PHONE"}),
    })

    class FakeAdbClient:
        def __init__(self, _executable):
            pass

        @staticmethod
        def is_online(_udid):
            return True

        @staticmethod
        def shell(*_args, **_kwargs):
            return CommandResult(("adb",), 1, "", "device query failed")

    monkeypatch.setattr("iot_exp.preflight.AdbClient", FakeAdbClient)
    monkeypatch.setattr("iot_exp.preflight.collect_system_checks", lambda **_kwargs: [])
    monkeypatch.setattr("iot_exp.preflight.discover_android_sdk", lambda *_args: None)
    report = run_preflight(experiment, runtime)
    check = next(check for check in report["checks"] if check["name"] == "phone_network_isolation")
    assert check["ok"] is False
    assert "exited with 1" in check["detail"]["error"]


def test_loopback_only_address_never_certifies_isolation(monkeypatch):
    experiment, runtime = _configs()
    experiment = experiment.model_copy(update={
        "phone": experiment.phone.model_copy(update={"udid": "PHONE"}),
    })

    class FakeAdbClient:
        def __init__(self, _executable):
            pass

        @staticmethod
        def is_online(_udid):
            return True

        @staticmethod
        def shell(*args, **_kwargs):
            if args[:2] == ("ip", "addr"):
                return CommandResult(("adb",), 0, "inet 127.0.0.1/8 scope host lo", "")
            return CommandResult(("adb",), 1, "", "unreachable")

    monkeypatch.setattr("iot_exp.preflight.AdbClient", FakeAdbClient)
    monkeypatch.setattr("iot_exp.preflight.collect_system_checks", lambda **_kwargs: [])
    monkeypatch.setattr("iot_exp.preflight.discover_android_sdk", lambda *_args: None)
    report = run_preflight(experiment, runtime)
    check = next(check for check in report["checks"] if check["name"] == "phone_network_isolation")
    assert check["ok"] is False
    assert "no usable non-loopback" in check["detail"]["error"]
