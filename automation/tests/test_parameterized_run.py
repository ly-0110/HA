"""Simulated-run tests for parameterized events: scheduling, receipts, evidence, validation."""

import json
from pathlib import Path

from iot_exp.adapters import SimulatedLampAdapter
from iot_exp.adapters.base import AdapterError
from iot_exp.backends import DisabledCaptureBackend, DisabledHaProvider
from iot_exp.config import load_configuration
from iot_exp.models import DeviceState, EventResult, ParameterDimension
from iot_exp.orchestrator import ExperimentRunner, validate_session

ROOT = Path(__file__).parents[1]
ADVANCED = ROOT / "experiment" / "mi_desk_lamp_1s_advanced.yaml"


def _advanced_configs(tmp_path, **session_updates):
    experiment, runtime = load_configuration(ADVANCED, ROOT / "runtime" / "windows-dev.yaml")
    runtime_data = runtime.model_dump()
    runtime_data["output_root"] = tmp_path
    runtime = runtime.__class__.model_validate(runtime_data)
    sessions = experiment.sessions.model_copy(update={
        "repetitions_per_event": 1,
        "idle_range_seconds": (0, 0),
        "cooldown_seconds": 0,
        **session_updates,
    })
    experiment = experiment.model_copy(update={"sessions": sessions})
    return experiment, runtime


def _runner(experiment, runtime, adapter, session_id, seed=42):
    return ExperimentRunner(
        experiment,
        runtime,
        adapter=adapter,
        capture=DisabledCaptureBackend(),
        ha=DisabledHaProvider(),
        session_id=session_id,
        seed=seed,
    )


def test_advanced_simulated_run_completes_all_identities(tmp_path):
    experiment, runtime = _advanced_configs(tmp_path)
    adapter = SimulatedLampAdapter.for_experiment(experiment)
    runner = _runner(experiment, runtime, adapter, "session_advanced_sim")
    records = runner.run()
    planned_identities = {f"{e.event_type.value}|{e.target}" for e in experiment.events}
    assert len(records) == len(experiment.events)
    assert all(record.result is EventResult.APP_ACK_ONLY for record in records)
    assert {f"{r.event_type.value}|{r.target_value}" for r in records} == planned_identities
    assert all(record.dimension is not None for record in records)
    assert validate_session(runner.paths.root)["ok"] is True


def test_brightness_targets_round_trip_with_before_after_observations(tmp_path):
    experiment, runtime = _advanced_configs(tmp_path)
    experiment = experiment.model_copy(update={
        "events": [e for e in experiment.events if e.event_type.value == "set_brightness"],
    })
    adapter = SimulatedLampAdapter.for_experiment(experiment)
    runner = _runner(experiment, runtime, adapter, "session_brightness_rt")
    records = runner.run()
    assert len(records) == 2
    by_target = {record.target_value: record for record in records}
    assert set(by_target) == {30, 80}
    for record in by_target.values():
        before = record.observed_before
        after = record.observed_after
        assert before is not None and after is not None
        assert abs(before.value - record.target_value) > record.tolerance
        assert abs(after.value - record.target_value) <= record.tolerance
        assert record.unit == "%"
        assert record.t_app_ack_ns is not None
    # Both brightness targets must have actually executed (round trip, not skip).
    assert len([r for r in records if r.result is EventResult.APP_ACK_ONLY]) == 2


def test_six_scene_targets_execute_with_distinct_targets(tmp_path):
    experiment, runtime = _advanced_configs(tmp_path)
    experiment = experiment.model_copy(update={
        "events": [e for e in experiment.events if e.event_type.value == "select_scene"],
    })
    adapter = SimulatedLampAdapter.for_experiment(experiment)
    runner = _runner(experiment, runtime, adapter, "session_scenes")
    records = runner.run()
    assert len(records) == 6
    assert {record.target_value for record in records} == set(experiment.parameters.scene.scenes)
    assert all(record.result is EventResult.APP_ACK_ONLY for record in records)


def test_mixed_lamp_session_finishes_sliders_before_scene_buttons(tmp_path):
    experiment, runtime = _advanced_configs(tmp_path)
    adapter = SimulatedLampAdapter.for_experiment(experiment)
    runner = _runner(experiment, runtime, adapter, "session_slider_then_scene")
    records = runner.run()
    slider_positions = [
        index for index, record in enumerate(records)
        if record.dimension in {ParameterDimension.BRIGHTNESS, ParameterDimension.COLOR_TEMPERATURE}
    ]
    scene_positions = [
        index for index, record in enumerate(records)
        if record.dimension is ParameterDimension.SCENE
    ]
    assert max(slider_positions) < min(scene_positions)
    assert validate_session(runner.paths.root)["ok"] is True


def test_focus_open_and_close_round_trip(tmp_path):
    experiment, runtime = _advanced_configs(tmp_path)
    experiment = experiment.model_copy(update={
        "events": [e for e in experiment.events if e.event_type.value == "set_focus_mode"],
    })
    adapter = SimulatedLampAdapter.for_experiment(experiment)
    runner = _runner(experiment, runtime, adapter, "session_focus")
    records = runner.run()
    assert {record.target_value for record in records} == {True, False}
    assert all(record.result is EventResult.APP_ACK_ONLY for record in records)
    on_record = next(r for r in records if r.target_value is True)
    assert on_record.observed_before.value is False
    assert on_record.observed_after.value is True


def test_target_already_reached_is_not_counted_as_change(tmp_path):
    experiment, runtime = _advanced_configs(tmp_path)
    # Lamp starts at brightness 30, which is a configured target.
    adapter = SimulatedLampAdapter.for_experiment(experiment)
    adapter.parameters[ParameterDimension.BRIGHTNESS] = 30
    runner = _runner(experiment, runtime, adapter, "session_pre_achieved")
    records = runner.run()
    journal = runner.paths.run_journal_jsonl.read_text(encoding="utf-8")
    # The scheduler defers the pre-achieved target and records why each time.
    assert "target_already_reached" in journal
    # No success record may claim a change when the target was already reached.
    for record in records:
        if record.result is not EventResult.APP_ACK_ONLY:
            continue
        before_value = record.observed_before.value
        if record.dimension in (ParameterDimension.BRIGHTNESS, ParameterDimension.COLOR_TEMPERATURE):
            assert abs(before_value - record.target_value) > record.tolerance
        else:
            assert before_value != record.target_value
    # The pre-achieved target is skipped while reached, then executed honestly
    # once another event moves the brightness away.
    assert any(
        r.event_type.value == "set_brightness" and r.target_value == 30
        and r.result is EventResult.APP_ACK_ONLY
        for r in records
    )
    assert validate_session(runner.paths.root)["ok"] is True


def test_power_lost_during_idle_reselects_and_terminates_cleanly(tmp_path):
    experiment, runtime = _advanced_configs(tmp_path)
    experiment = experiment.model_copy(update={
        "events": [e for e in experiment.events if e.event_type.value == "set_brightness"],
    })
    adapter = SimulatedLampAdapter.for_experiment(experiment)

    reads = {"count": 0}

    class PowerDrops(SimulatedLampAdapter):
        pass

    class DroppingAdapter:
        """Adapter that turns the lamp off after the first scheduling read."""

        def __init__(self, inner):
            self.inner = inner

        def read_state(self):
            reads["count"] += 1
            if reads["count"] >= 2:
                self.inner.state = DeviceState.OFF
            return self.inner.read_state()

        def __getattr__(self, name):
            return getattr(self.inner, name)

    runner = _runner(experiment, runtime, DroppingAdapter(adapter), "session_power_drop")
    records = runner.run()
    assert records[-1].error_code == "no_legal_transition"
    journal = runner.paths.run_journal_jsonl.read_text(encoding="utf-8")
    assert "precommand_state_changed" in journal
    quality = json.loads(runner.paths.quality_report.read_text(encoding="utf-8"))
    assert quality["session_outcome"] == "incomplete"
    assert validate_session(runner.paths.root)["ok"] is False


def test_unreadable_before_never_issues_a_parameter_command(tmp_path):
    experiment, runtime = _advanced_configs(tmp_path)
    experiment = experiment.model_copy(update={
        "events": [e for e in experiment.events if e.event_type.value == "set_brightness" and e.target == 30],
        "sessions": experiment.sessions.model_copy(update={"max_attempts": 1}),
    })

    class UnreadableBefore(SimulatedLampAdapter):
        reads = 0

        def read_parameter(self, dimension, *, evidence=None):
            observation = super().read_parameter(dimension, evidence=evidence)
            self.reads += 1
            if self.reads >= 3:
                return observation.model_copy(update={"known": False, "value": None})
            return observation

    adapter = UnreadableBefore(DeviceState.ON)
    runner = _runner(experiment, runtime, adapter, "session_unknown_before")
    records = runner.run()
    assert all(record.result is EventResult.FAILED for record in records)
    assert all(record.error_code == "parameter_before_unreadable" for record in records)
    assert adapter.action_count == 0
    journal = runner.paths.run_journal_jsonl.read_text(encoding="utf-8")
    assert '"kind": "event_command"' not in journal
    assert validate_session(runner.paths.root)["ok"] is True


def test_parameterized_timeout_is_not_success(tmp_path):
    experiment, runtime = _advanced_configs(tmp_path)
    experiment = experiment.model_copy(update={
        "events": [e for e in experiment.events if e.event_type.value == "set_brightness"],
        "sessions": experiment.sessions.model_copy(update={"repetitions_per_event": 1}),
    })

    class StuckAdapter(SimulatedLampAdapter):
        def perform_parameterized_event(self, spec):
            self.action_count += 1  # "action" happens but the value never moves

    adapter = StuckAdapter(
        DeviceState.ON,
        initial_parameters={ParameterDimension.BRIGHTNESS: 60},
    )
    runner = _runner(experiment, runtime, adapter, "session_timeout")
    records = runner.run()
    for record in records:
        assert record.result is EventResult.TIMEOUT
        assert record.error_code == "parameter_target_timeout"
        assert record.t_app_ack_ns is None
    report = json.loads(runner.paths.quality_report.read_text(encoding="utf-8"))
    assert report["result_counts"].get("timeout") == len(records)
    # The session is internally consistent even though nothing succeeded.
    assert validate_session(runner.paths.root)["ok"] is True
    # Every attempt is honest: no success was fabricated, retries kept the same target.
    assert all(r.target_value == 30 for r in records if r.event_id.startswith(runner.session_id + "_event_000001"))
    assert report["results_by_identity"]["set_brightness|30"]["timeout"] == 2
    assert report["results_by_identity"]["set_brightness|80"]["timeout"] == 2


def test_parameterized_unreadable_after_is_failed_not_success(tmp_path):
    experiment, runtime = _advanced_configs(tmp_path)
    experiment = experiment.model_copy(update={
        "events": [e for e in experiment.events if e.event_type.value == "select_scene"],
    })

    class BlindSceneAdapter(SimulatedLampAdapter):
        def wait_for_parameter(self, spec, timeout_seconds, *, evidence=None):
            observation = super().wait_for_parameter(spec, timeout_seconds)
            if evidence is not None:
                self._write_evidence(evidence[0], evidence[1])
            return observation.model_copy(update={"known": False, "value": None})

    adapter = BlindSceneAdapter(DeviceState.ON)
    runner = _runner(experiment, runtime, adapter, "session_blind")
    records = runner.run()
    assert len(records) == 6
    assert all(record.result is EventResult.FAILED for record in records)
    assert all(record.error_code == "parameter_unreadable" for record in records)
    report = json.loads(runner.paths.quality_report.read_text(encoding="utf-8"))
    assert report["result_counts"].get("failed") == 6
    assert validate_session(runner.paths.root)["ok"] is True  # consistent, but visibly all failed


def test_retry_uses_new_event_id_and_keeps_failed_evidence(tmp_path):
    experiment, runtime = _advanced_configs(
        tmp_path,
        repetitions_per_event=1,
        max_attempts=2,
    )
    experiment = experiment.model_copy(update={
        "events": [e for e in experiment.events if e.event_type.value == "select_scene"],
    })

    class FlakySceneAdapter(SimulatedLampAdapter):
        failures = 0

        def perform_parameterized_event(self, spec):
            if self.failures == 0:  # fail the first attempt once
                self.failures += 1
                raise AdapterError("scene button disappeared", code="event_control_not_found")
            super().perform_parameterized_event(spec)

    adapter = FlakySceneAdapter(DeviceState.ON)
    runner = _runner(experiment, runtime, adapter, "session_retry")
    records = runner.run()
    assert len({record.event_id for record in records}) == len(records)
    failures = [r for r in records if r.result is EventResult.AUTOMATION_ERROR]
    successes = [r for r in records if r.result is EventResult.APP_ACK_ONLY]
    assert len(failures) == 1 and len(successes) == 6
    failed = failures[0]
    retry = next(
        r for r in records
        if r.event_id.rsplit("_attempt_", 1)[0] == failed.event_id.rsplit("_attempt_", 1)[0]
        and r.attempt == 2
    )
    assert retry.result is EventResult.APP_ACK_ONLY
    assert retry.target_value == failed.target_value
    assert failed.observed_before is not None  # pre-command observation survives
    assert (runner.paths.root / "screenshots" / f"{failed.event_id}.txt").exists()


def test_parameterized_evidence_files_exist_and_match_event_ids(tmp_path):
    experiment, runtime = _advanced_configs(tmp_path)
    adapter = SimulatedLampAdapter.for_experiment(experiment)
    runner = _runner(experiment, runtime, adapter, "session_evidence")
    records = runner.run()
    screenshots = runner.paths.root / "screenshots"
    for record in records:
        if record.dimension is None:
            continue
        for phase, observation in (("before", record.observed_before), ("after", record.observed_after)):
            refs = observation.evidence_files
            assert refs, f"{record.event_id} missing {phase} evidence references"
            for ref in refs:
                assert ref.startswith("screenshots/")
                assert (runner.paths.root / ref).exists(), ref
                assert record.event_id in Path(ref).name
        assert (screenshots / f"{record.event_id}_before.txt").exists()
        assert (screenshots / f"{record.event_id}_after.txt").exists()


def test_quality_report_counts_per_identity(tmp_path):
    experiment, runtime = _advanced_configs(tmp_path)
    adapter = SimulatedLampAdapter.for_experiment(experiment)
    runner = _runner(experiment, runtime, adapter, "session_quality")
    runner.run()
    report = json.loads(runner.paths.quality_report.read_text(encoding="utf-8"))
    planned = report["planned_by_identity"]
    assert planned["set_brightness|30"] == 1
    assert planned["select_scene|阅读模式"] == 1
    assert planned["set_focus_mode|true"] == 1
    completed = report["completed_by_identity"]
    for identity in planned:
        assert completed[identity] == 1
    results = report["results_by_identity"]
    assert all(v == {"app_ack_only": 1} for v in results.values())
    # Each of the six scene identities stays distinct.
    assert len([k for k in planned if k.startswith("select_scene|")]) == 6


def test_validator_rejects_success_without_after_observation(tmp_path):
    experiment, runtime = _advanced_configs(tmp_path)
    adapter = SimulatedLampAdapter.for_experiment(experiment)
    runner = _runner(experiment, runtime, adapter, "session_tamper_after")
    runner.run()
    lines = runner.paths.actions_jsonl.read_text(encoding="utf-8").splitlines()
    tampered = []
    dropped = False
    for line in lines:
        row = json.loads(line)
        if row.get("dimension") and row["result"] == "app_ack_only" and not dropped:
            row["observed_after"] = None
            dropped = True
        tampered.append(json.dumps(row, ensure_ascii=False))
    runner.paths.actions_jsonl.write_text("\n".join(tampered) + "\n", encoding="utf-8")
    report = validate_session(runner.paths.root)
    assert report["ok"] is False
    assert any("after" in issue for issue in report["parameterized_record_issues"])


def test_validator_rejects_confirmed_classification_for_parameterized(tmp_path):
    experiment, runtime = _advanced_configs(tmp_path)
    adapter = SimulatedLampAdapter.for_experiment(experiment)
    runner = _runner(experiment, runtime, adapter, "session_tamper_confirmed")
    runner.run()
    lines = runner.paths.actions_jsonl.read_text(encoding="utf-8").splitlines()
    tampered = []
    flipped = False
    for line in lines:
        row = json.loads(line)
        if row.get("dimension") and row["result"] == "app_ack_only" and not flipped:
            row["result"] = "confirmed"
            flipped = True
        tampered.append(json.dumps(row, ensure_ascii=False))
    runner.paths.actions_jsonl.write_text("\n".join(tampered) + "\n", encoding="utf-8")
    report = validate_session(runner.paths.root)
    assert report["ok"] is False
    assert any("confirmed" in issue for issue in report["parameterized_record_issues"])


def test_validator_rejects_out_of_plan_target(tmp_path):
    experiment, runtime = _advanced_configs(tmp_path)
    adapter = SimulatedLampAdapter.for_experiment(experiment)
    runner = _runner(experiment, runtime, adapter, "session_tamper_target")
    runner.run()
    lines = runner.paths.actions_jsonl.read_text(encoding="utf-8").splitlines()
    tampered = []
    changed = False
    for line in lines:
        row = json.loads(line)
        if row.get("dimension") == "brightness" and not changed:
            row["target_value"] = 95
            changed = True
        tampered.append(json.dumps(row, ensure_ascii=False))
    runner.paths.actions_jsonl.write_text("\n".join(tampered) + "\n", encoding="utf-8")
    report = validate_session(runner.paths.root)
    assert report["ok"] is False
    assert any("not part of the session plan" in issue for issue in report["parameterized_record_issues"])


def test_validator_rejects_missing_evidence_file(tmp_path):
    experiment, runtime = _advanced_configs(tmp_path)
    adapter = SimulatedLampAdapter.for_experiment(experiment)
    runner = _runner(experiment, runtime, adapter, "session_tamper_evidence")
    runner.run()
    screenshots = runner.paths.root / "screenshots"
    victim = next(
        record for record in runner.records if record.dimension is not None
    )
    for ref in victim.observed_before.evidence_files:
        (runner.paths.root / ref).unlink()
    remaining = [p for p in screenshots.iterdir() if p.is_file()]
    assert remaining, "other evidence must stay intact"
    report = validate_session(runner.paths.root)
    assert report["ok"] is False
    assert any("evidence file missing" in issue for issue in report["parameterized_record_issues"])


def test_validator_rejects_scene_receipt_with_inconsistent_numeric_readback(tmp_path):
    experiment, runtime = _advanced_configs(tmp_path)
    adapter = SimulatedLampAdapter.for_experiment(experiment)
    runner = _runner(experiment, runtime, adapter, "session_tamper_scene_tuple")
    runner.run()
    rows = [json.loads(line) for line in runner.paths.actions_jsonl.read_text(encoding="utf-8").splitlines()]
    scene = next(row for row in rows if row.get("dimension") == "scene")
    scene["observed_after"]["readback_values"]["brightness"] = 1
    runner.paths.actions_jsonl.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8",
    )
    report = validate_session(runner.paths.root)
    assert report["ok"] is False
    assert any("does not match numeric readback" in issue for issue in report["parameterized_record_issues"])


def test_validator_rejects_no_change_success(tmp_path):
    experiment, runtime = _advanced_configs(tmp_path)
    adapter = SimulatedLampAdapter.for_experiment(experiment)
    runner = _runner(experiment, runtime, adapter, "session_tamper_nochange")
    runner.run()
    lines = runner.paths.actions_jsonl.read_text(encoding="utf-8").splitlines()
    tampered = []
    changed = False
    for line in lines:
        row = json.loads(line)
        if row.get("dimension") and row["result"] == "app_ack_only" and not changed:
            row["observed_before"]["value"] = row["target_value"]  # claim target already reached before
            changed = True
        tampered.append(json.dumps(row, ensure_ascii=False))
    runner.paths.actions_jsonl.write_text("\n".join(tampered) + "\n", encoding="utf-8")
    report = validate_session(runner.paths.root)
    assert report["ok"] is False
    assert any("already reached" in issue for issue in report["parameterized_record_issues"])


def test_legacy_two_state_session_still_validates(tmp_path):
    experiment, runtime = load_configuration(
        ROOT / "experiment" / "mi_desk_lamp_1s.yaml",
        ROOT / "runtime" / "windows-dev.yaml",
    )
    runtime_data = runtime.model_dump()
    runtime_data["output_root"] = tmp_path
    runtime = runtime.__class__.model_validate(runtime_data)
    experiment = experiment.model_copy(update={
        "sessions": experiment.sessions.model_copy(update={
            "repetitions_per_event": 1, "idle_range_seconds": (0, 0), "cooldown_seconds": 0,
        }),
    })
    adapter = SimulatedLampAdapter(DeviceState.OFF)
    runner = _runner(experiment, runtime, adapter, "session_legacy")
    runner.run()
    report = validate_session(runner.paths.root)
    assert report["ok"] is True
    assert report["parameterized_record_issues"] == []


def test_mixed_config_with_legacy_events_runs(tmp_path):
    experiment, runtime = _advanced_configs(tmp_path)
    from iot_exp.models import EventSpec

    experiment = experiment.model_copy(update={
        "events": [
            EventSpec(event_type="turn_on", required_state="off", expected_state="on"),
            *experiment.events,
        ],
    })
    adapter = SimulatedLampAdapter.for_experiment(experiment)
    runner = _runner(experiment, runtime, adapter, "session_mixed")
    records = runner.run()
    assert any(r.dimension is None and r.result is EventResult.APP_ACK_ONLY for r in records)
    assert any(r.dimension is not None for r in records)
    assert validate_session(runner.paths.root)["ok"] is True
