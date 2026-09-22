from pathlib import Path

from iot_exp.adapters import SimulatedLampAdapter
from iot_exp.backends import DisabledCaptureBackend, DisabledHaProvider
from iot_exp.config import load_configuration
from iot_exp.models import DeviceState, EventResult
from iot_exp.orchestrator import ExperimentRunner, create_event_id, validate_session

ROOT = Path(__file__).parents[1]


def _configs(tmp_path):
    experiment, runtime = load_configuration(
        ROOT / "experiment" / "mi_desk_lamp_1s.yaml",
        ROOT / "runtime" / "windows-dev.yaml",
    )
    runtime_data = runtime.model_dump()
    runtime_data["output_root"] = tmp_path
    runtime = runtime.__class__.model_validate(runtime_data)
    experiment = experiment.model_copy(update={
        "sessions": experiment.sessions.model_copy(update={"repetitions_per_event": 2, "idle_range_seconds": (0, 0), "cooldown_seconds": 0}),
    })
    return experiment, runtime


def test_runner_dry_contract(tmp_path):
    experiment, runtime = _configs(tmp_path)
    runner = ExperimentRunner(
        experiment,
        runtime,
        adapter=SimulatedLampAdapter(DeviceState.OFF),
        capture=DisabledCaptureBackend(),
        ha=DisabledHaProvider(),
        session_id="session_test",
        seed=42,
    )
    records = runner.run()
    assert len(records) == 4
    assert all(record.result is EventResult.APP_ACK_ONLY for record in records)
    assert {record.event_type.value for record in records} == {"turn_on", "turn_off"}
    assert validate_session(runner.paths.root)["ok"]
    lines = runner.paths.actions_jsonl.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4
    assert all("token" not in line.lower() for line in lines)
    assert all(f'"phone_udid":"{experiment.phone.udid}"' in line for line in lines)


def test_event_id_is_sortable_and_unique():
    first = create_event_id("session_x", 1, 1)
    second = create_event_id("session_x", 2, 1)
    retry = create_event_id("session_x", 1, 2)
    assert first < second
    assert first != retry
