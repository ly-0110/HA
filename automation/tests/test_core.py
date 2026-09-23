from pathlib import Path

import pytest

from iot_exp.adapters import SimulatedLampAdapter
from iot_exp.adapters.base import AdapterError
from iot_exp.backends import DisabledCaptureBackend, DisabledHaProvider
from iot_exp.config import load_configuration
from iot_exp.models import CaptureResult, DeviceState, EventResult
from iot_exp.orchestrator import ExperimentRunner, RunError, create_event_id, validate_session

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


def test_runner_refuses_to_reuse_nonempty_session_directory(tmp_path):
    experiment, runtime = _configs(tmp_path)
    existing = tmp_path / "sessions" / "session_existing"
    existing.mkdir(parents=True)
    (existing / "actions.jsonl").write_text("evidence\n", encoding="utf-8")
    with pytest.raises(RunError, match="already exists"):
        ExperimentRunner(
            experiment,
            runtime,
            adapter=SimulatedLampAdapter(),
            capture=DisabledCaptureBackend(),
            ha=DisabledHaProvider(),
            session_id="session_existing",
        )


def test_validator_rejects_session_without_quality_report(tmp_path):
    experiment, runtime = _configs(tmp_path)
    runner = ExperimentRunner(
        experiment,
        runtime,
        adapter=SimulatedLampAdapter(),
        capture=DisabledCaptureBackend(),
        ha=DisabledHaProvider(),
        session_id="session_incomplete",
        seed=42,
    )
    runner.run()
    runner.paths.quality_report.unlink()
    report = validate_session(runner.paths.root)
    assert report["quality_report_exists"] is False
    assert report["ok"] is False


def test_runner_retries_transient_adapter_failure(tmp_path):
    experiment, runtime = _configs(tmp_path)
    experiment = experiment.model_copy(update={
        "sessions": experiment.sessions.model_copy(update={
            "repetitions_per_event": 1,
            "max_attempts": 2,
        }),
    })

    class FlakyAdapter(SimulatedLampAdapter):
        failures = 0

        def perform_event(self, event_type):
            if self.failures == 0:
                self.failures += 1
                raise AdapterError("temporary selector failure", code="selector_missing")
            super().perform_event(event_type)

    runner = ExperimentRunner(
        experiment,
        runtime,
        adapter=FlakyAdapter(),
        capture=DisabledCaptureBackend(),
        ha=DisabledHaProvider(),
        session_id="session_retry",
        seed=42,
    )
    records = runner.run()
    assert len(records) == 3
    assert records[0].attempt == 1
    assert records[0].result is EventResult.AUTOMATION_ERROR
    assert records[1].attempt == 2
    assert records[1].result is EventResult.APP_ACK_ONLY
    assert validate_session(runner.paths.root)["ok"] is True


def test_runner_reselects_event_if_state_changes_during_idle(tmp_path):
    experiment, runtime = _configs(tmp_path)
    experiment = experiment.model_copy(update={
        "sessions": experiment.sessions.model_copy(update={"repetitions_per_event": 1}),
    })

    class ChangingAdapter(SimulatedLampAdapter):
        reads = 0

        def read_state(self):
            self.reads += 1
            if self.reads == 2:
                self.state = DeviceState.ON
            return super().read_state()

    runner = ExperimentRunner(
        experiment, runtime, adapter=ChangingAdapter(DeviceState.OFF),
        capture=DisabledCaptureBackend(), ha=DisabledHaProvider(),
        session_id="state_changed_during_idle",
    )
    records = runner.run()
    assert len(records) == 2
    assert all(record.result is EventResult.APP_ACK_ONLY for record in records)
    assert any('"kind":"precommand_state_changed"' in line for line in runner.paths.run_journal_jsonl.read_text().splitlines())


def test_capture_failure_fails_runner_and_validator(tmp_path):
    experiment, runtime = _configs(tmp_path)

    class FailedCapture:
        output_path = None

        def start(self, output_path):
            self.output_path = output_path
            output_path.write_bytes(b"partial")

        def stop(self):
            return CaptureResult(
                enabled=True,
                path=self.output_path,
                return_code=1,
                error="capture interface disappeared",
            )

    runner = ExperimentRunner(
        experiment,
        runtime,
        adapter=SimulatedLampAdapter(),
        capture=FailedCapture(),
        ha=DisabledHaProvider(),
        session_id="session_capture_failure",
        seed=42,
    )
    with pytest.raises(RunError, match="capture interface disappeared"):
        runner.run()
    report = validate_session(runner.paths.root)
    assert report["capture_ok"] is False
    assert report["ok"] is False
