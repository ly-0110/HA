from __future__ import annotations

import hashlib
import json
import random
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from .adapters.base import AdapterError, VendorAppAdapter
from .backends.capture import CaptureBackend
from .backends.ha import HaObservationProvider
from .backends.system import collect_system_checks
from .journal import ActionJournal, JsonlJournal
from .models import (
    AckEvidence,
    ActionRecord,
    DeviceState,
    EventResult,
    EventSpec,
    ExperimentConfig,
    RuntimeConfig,
    SessionPaths,
)


class RunError(RuntimeError):
    pass


def create_session_id() -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return f"session_{stamp}_{uuid.uuid4().hex[:8]}"


def create_event_id(session_id: str, sequence: int, attempt: int) -> str:
    return f"{session_id}_event_{sequence:06d}_attempt_{attempt:02d}"


def _utc_config_dump(experiment: ExperimentConfig, runtime: RuntimeConfig, session_id: str, seed: int) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "random_seed": seed,
        "experiment": experiment.model_dump(mode="json"),
        "runtime": runtime.model_dump(mode="json"),
        "tool_checks": [check.__dict__ for check in collect_system_checks(
            adb=runtime.adb_executable,
            appium=runtime.appium_executable,
            dumpcap=runtime.dumpcap_executable,
            require_capture=runtime.capture_mode.value == "dumpcap",
            android_sdk_root=runtime.android_sdk_root,
        )],
        "created_at_unix_ns": time.time_ns(),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class ExperimentRunner:
    def __init__(
        self,
        experiment: ExperimentConfig,
        runtime: RuntimeConfig,
        *,
        adapter: VendorAppAdapter,
        capture: CaptureBackend,
        ha: HaObservationProvider,
        session_id: str | None = None,
        seed: int | None = None,
    ):
        self.experiment = experiment
        self.runtime = runtime
        self.adapter = adapter
        self.capture = capture
        self.ha = ha
        self.session_id = session_id or create_session_id()
        self.seed = seed if seed is not None else random.SystemRandom().randrange(1, 2**31)
        self.rng = random.Random(self.seed)
        self.paths = SessionPaths.create(runtime.output_root / "sessions" / self.session_id)
        self.actions = ActionJournal(self.paths.actions_jsonl)
        self.journal = JsonlJournal(self.paths.run_journal_jsonl)
        self.records: list[ActionRecord] = []

    def run(self) -> list[ActionRecord]:
        self.paths.session_yaml.write_text(
            yaml.safe_dump(_utc_config_dump(self.experiment, self.runtime, self.session_id, self.seed), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        self.journal.append({"kind": "session_started", "session_id": self.session_id, "at_unix_ns": time.time_ns()})
        self.paths.clock_sync.write_text(json.dumps({
            "captured_at_unix_ns": time.time_ns(),
            "captured_at_monotonic_ns": time.monotonic_ns(),
            "clock_source": "host_system_clock",
            "timezone": self.runtime.timezone,
        }, indent=2) + "\n", encoding="utf-8")
        if not self.paths.network_isolation_check.exists():
            self.paths.network_isolation_check.write_text(json.dumps({
                "status": "not_collected",
                "note": "Run preflight before formal acquisition; CLI overwrites this file with its report.",
            }, indent=2) + "\n", encoding="utf-8")
        capture_result = None
        try:
            self.ha.start(self.paths.root)
            self.adapter.launch_and_open_device()
            self.capture.start(self.paths.capture)
            time.sleep(self.runtime.pre_roll_seconds)
            self._run_events()
            time.sleep(self.runtime.post_roll_seconds)
        finally:
            try:
                capture_result = self.capture.stop()
            finally:
                try:
                    self.ha.stop()
                finally:
                    self.adapter.close()
            self._write_quality_report(capture_result)
            self.journal.append({"kind": "session_finished", "session_id": self.session_id, "at_unix_ns": time.time_ns()})
        return self.records

    def _run_events(self) -> None:
        remaining = Counter({event.event_type.value: self.experiment.sessions.repetitions_per_event for event in self.experiment.events})
        sequence = 0
        while any(remaining.values()):
            state_before = self.adapter.read_state()
            spec = self._choose_event(state_before, remaining)
            if spec is None:
                sequence += 1
                event_id = create_event_id(self.session_id, sequence, 1)
                record = ActionRecord(
                    experiment_id=self.experiment.experiment_id,
                    session_id=self.session_id,
                    event_id=event_id,
                    device_id=self.experiment.device.device_id,
                    event_type=self.experiment.events[0].event_type,
                    trigger_network=self.experiment.phone.network,
                    phone_id=self.experiment.phone.phone_id,
                    phone_udid=self.experiment.phone.udid,
                    app_version=self.experiment.app.version,
                    device_firmware=self.experiment.device.firmware,
                    state_before=state_before,
                    expected_state=DeviceState.UNKNOWN,
                    result=EventResult.UNEXPECTED_STATE,
                    attempt=1,
                    error_code="no_legal_transition",
                    notes="No remaining event is legal for the observed state",
                )
                self._persist_record(record)
                break
            sequence += 1
            remaining[spec.event_type.value] -= 1
            idle = self.rng.uniform(*self.experiment.sessions.idle_range_seconds)
            time.sleep(idle)
            self._run_one(spec, state_before, sequence)
            time.sleep(self.experiment.sessions.cooldown_seconds)

    def _choose_event(self, state: DeviceState, remaining: Counter[str]) -> EventSpec | None:
        candidates = [
            spec for spec in self.experiment.events
            if remaining[spec.event_type.value] > 0 and spec.required_state is state
        ]
        return self.rng.choice(candidates) if candidates else None

    def _run_one(self, spec: EventSpec, state_before: DeviceState, sequence: int) -> None:
        attempt = 1
        event_id = create_event_id(self.session_id, sequence, attempt)
        self.journal.append({"kind": "event_started", "event_id": event_id, "at_unix_ns": time.time_ns()})
        self.journal.append({"kind": "event_command", "event_id": event_id, "event_type": spec.event_type.value})
        t_before = time.time_ns()
        t_after = t_before
        t_ack = None
        ha_evidence = None
        try:
            self.adapter.perform_event(spec.event_type)
            t_after = time.time_ns()
            ack = self.adapter.wait_for_ack(spec.expected_state, timeout_seconds=30)
            t_ack = ack.observed_at_unix_ns if ack.acknowledged else None
            ha_evidence = self.ha.await_state(self.experiment.device.entity_id, spec.expected_state, timeout_seconds=30)
            result = self._classify(ack, ha_evidence, spec.expected_state)
            state_after = ack.observed_state
            error_code = None if ack.acknowledged else "app_ack_timeout"
            notes = ack.message
        except AdapterError as exc:
            t_after = time.time_ns()
            ack = AckEvidence(acknowledged=False, message=str(exc))
            ha_evidence = None
            state_after = DeviceState.UNKNOWN
            result = EventResult.TIMEOUT if exc.code.endswith("timeout") else EventResult.AUTOMATION_ERROR
            error_code = exc.code
            notes = str(exc)
            try:
                self.adapter.capture_diagnostics(self.paths.screenshots, event_id)
            except Exception as diagnostic_exc:  # noqa: BLE001 - diagnostics must not mask the action result.
                notes = f"{notes}; diagnostics={diagnostic_exc}"
            try:
                self.adapter.recover_navigation()
            except Exception as recovery_exc:  # noqa: BLE001 - recovery is best effort after an action error.
                notes = f"{notes}; recovery={recovery_exc}"
        except Exception as exc:  # noqa: BLE001 - classify unknown third-party driver failures.
            t_after = time.time_ns()
            ack = AckEvidence(acknowledged=False, message=str(exc))
            ha_evidence = None
            state_after = DeviceState.UNKNOWN
            result = EventResult.AUTOMATION_ERROR
            error_code = "unexpected_exception"
            notes = str(exc)
            try:
                self.adapter.capture_diagnostics(self.paths.screenshots, event_id)
            except Exception:  # noqa: BLE001, S110 - diagnostics are best effort on unknown failures.
                pass
        record = ActionRecord(
            experiment_id=self.experiment.experiment_id,
            session_id=self.session_id,
            event_id=event_id,
            device_id=self.experiment.device.device_id,
            event_type=spec.event_type,
            trigger_network=self.experiment.phone.network,
            phone_id=self.experiment.phone.phone_id,
            phone_udid=self.experiment.phone.udid,
            app_version=self.experiment.app.version,
            device_firmware=self.experiment.device.firmware,
            state_before=state_before,
            expected_state=spec.expected_state,
            state_after=state_after,
            t_cmd_before_ns=t_before,
            t_cmd_after_ns=t_after,
            t_app_ack_ns=t_ack,
            t_ha_state_ns=ha_evidence.observed_at_unix_ns if ha_evidence else None,
            result=result,
            attempt=attempt,
            error_code=error_code,
            notes=notes,
        )
        self._persist_record(record)
        self.journal.append({"kind": "event_finished", "event_id": event_id, "result": result.value, "at_unix_ns": time.time_ns()})

    @staticmethod
    def _classify(ack: AckEvidence, ha: Any, expected: DeviceState) -> EventResult:
        if ack.acknowledged and ha is not None and ha.observed_state is expected:
            return EventResult.CONFIRMED
        if ack.acknowledged:
            return EventResult.APP_ACK_ONLY
        if ha is not None and ha.observed_state is expected:
            return EventResult.HA_ONLY
        return EventResult.TIMEOUT

    def _persist_record(self, record: ActionRecord) -> None:
        self.records.append(record)
        self.actions.append_action(record)

    def _write_quality_report(self, capture_result: Any) -> None:
        counts = Counter(record.result.value for record in self.records)
        report = {
            "session_id": self.session_id,
            "planned_repetitions_per_event": self.experiment.sessions.repetitions_per_event,
            "record_count": len(self.records),
            "result_counts": dict(counts),
            "app_success_rate": sum(record.result in {EventResult.CONFIRMED, EventResult.APP_ACK_ONLY} for record in self.records) / len(self.records) if self.records else 0.0,
            "capture": capture_result.model_dump(mode="json") if capture_result is not None else None,
            "actions_sha256": _sha256(self.paths.actions_jsonl) if self.paths.actions_jsonl.exists() else None,
            "generated_at_unix_ns": time.time_ns(),
        }
        self.paths.quality_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_session(root: Path) -> dict[str, Any]:
    actions_path = root / "actions.jsonl"
    journal_path = root / "run_journal.jsonl"
    actions = ActionJournal(actions_path).read()
    journal = JsonlJournal(journal_path).read()
    ids = [row.get("event_id") for row in actions]
    unique = len(ids) == len(set(ids))
    started = {row.get("event_id") for row in journal if row.get("kind") == "event_started"}
    finished = {row.get("event_id") for row in journal if row.get("kind") == "event_finished"}
    open_events = sorted(event_id for event_id in started - finished if event_id)
    result = {
        "session_root": str(root),
        "actions_exist": actions_path.exists(),
        "unique_event_ids": unique,
        "event_count": len(actions),
        "open_events": open_events,
        "quality_report_exists": (root / "quality_report.json").exists(),
        "ok": actions_path.exists() and unique and not open_events,
    }
    return result
