from __future__ import annotations

import hashlib
import json
import random
import time
import uuid
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Callable
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
    NumericParameterConfig,
    PageObservation,
    ParameterDimension,
    ParameterizedEventSpec,
    RuntimeConfig,
    SceneParameterConfig,
    SessionPaths,
    event_dimension,
    event_identity,
    event_target_key,
    identity_key,
    value_hits_target,
)


class RunError(RuntimeError):
    pass


class RunCancelled(RunError):
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
        cancel_requested: Callable[[], bool] | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.experiment = experiment
        self.runtime = runtime
        self.adapter = adapter
        self.capture = capture
        self.ha = ha
        self.session_id = session_id or create_session_id()
        self.seed = seed if seed is not None else random.SystemRandom().randrange(1, 2**31)
        self.rng = random.Random(self.seed)
        session_root = runtime.output_root / "sessions" / self.session_id
        if session_root.exists() and any(session_root.iterdir()):
            raise RunError(f"session directory already exists and is not empty: {session_root}")
        self.paths = SessionPaths.create(session_root)
        self.actions = ActionJournal(self.paths.actions_jsonl)
        self.journal = JsonlJournal(self.paths.run_journal_jsonl)
        self.records: list[ActionRecord] = []
        self.completed_events = 0
        self.cancel_requested = cancel_requested or (lambda: False)
        self.progress_callback = progress_callback or (lambda _event: None)

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
        outcome = "completed"
        run_error: Exception | None = None
        try:
            self.ha.start(self.paths.root)
            self.adapter.launch_and_open_device()
            self.capture.start(self.paths.capture)
            self._sleep(self.runtime.pre_roll_seconds)
            self._run_events()
            if self.completed_events < len(self.experiment.events) * self.experiment.sessions.repetitions_per_event:
                outcome = "incomplete"
            self._sleep(self.runtime.post_roll_seconds)
        except KeyboardInterrupt:
            outcome = "cancelled"
            run_error = RunCancelled("experiment interrupted by operator")
        except Exception as exc:  # noqa: BLE001 - outcome and evidence must be persisted.
            outcome = "cancelled" if isinstance(exc, RunCancelled) else "failed"
            run_error = exc
        finally:
            cleanup_errors: list[str] = []
            try:
                capture_result = self.capture.stop()
            except Exception as exc:  # noqa: BLE001 - all owners must still be released.
                cleanup_errors.append(f"capture: {exc}")
            try:
                self.ha.stop()
            except Exception as exc:  # noqa: BLE001
                cleanup_errors.append(f"observation: {exc}")
            try:
                self.adapter.close()
            except Exception as exc:  # noqa: BLE001
                cleanup_errors.append(f"adapter: {exc}")
            if (
                capture_result is not None
                and capture_result.enabled
                and (
                    capture_result.return_code != 0
                    or capture_result.path is None
                    or not capture_result.path.exists()
                )
            ):
                outcome = "failed"
                capture_error = capture_result.error or (
                    f"capture exited with code {capture_result.return_code}"
                    if capture_result.return_code != 0
                    else "capture output is missing"
                )
                if run_error is None:
                    run_error = RunError(capture_error)
            if cleanup_errors and run_error is None:
                outcome = "failed"
                run_error = RunError("; ".join(cleanup_errors))
            self._write_quality_report(capture_result, outcome)
            self.journal.append({
                "kind": "session_finished",
                "session_id": self.session_id,
                "outcome": outcome,
                "error": str(run_error) if run_error else None,
                "cleanup_errors": cleanup_errors,
                "at_unix_ns": time.time_ns(),
            })
        if run_error is not None:
            raise run_error
        return self.records

    def _sleep(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.cancel_requested():
                raise RunCancelled("experiment cancelled by user")
            time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))

    def _run_events(self) -> None:
        remaining = Counter({
            event_identity(event): self.experiment.sessions.repetitions_per_event
            for event in self.experiment.events
        })
        sequence = 0
        changed_during_idle = 0
        while any(remaining.values()):
            if self.cancel_requested():
                raise RunCancelled("experiment cancelled by user")
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
                    notes="No remaining event is legal or executable for the observed state",
                )
                self._persist_record(record)
                break
            idle = self.rng.uniform(*self.experiment.sessions.idle_range_seconds)
            self._sleep(idle)
            command_state = self._precommand_recheck(spec)
            if command_state is None:
                changed_during_idle += 1
                if changed_during_idle >= 10:
                    raise RunError("device state changed during idle ten times; stop and inspect the page state")
                continue
            changed_during_idle = 0
            sequence += 1
            remaining[event_identity(spec)] -= 1
            record = None
            for attempt in range(1, self.experiment.sessions.max_attempts + 1):
                if self.cancel_requested():
                    raise RunCancelled("experiment cancelled by user")
                record = self._run_one(spec, command_state, sequence, attempt)
                succeeded = record.result in {
                    EventResult.CONFIRMED,
                    EventResult.APP_ACK_ONLY,
                    EventResult.HA_ONLY,
                }
                is_last_attempt = attempt >= self.experiment.sessions.max_attempts
                if succeeded or is_last_attempt:
                    break
                if not self._retry_recheck(spec, record):
                    break
            self.completed_events += 1
            if record is not None:
                self.progress_callback({
                    "event_id": record.event_id,
                    "result": record.result.value,
                    "label": self._event_label(spec),
                    "completed": self.completed_events,
                })
            self._sleep(self.experiment.sessions.cooldown_seconds)

    def _event_label(self, spec: EventSpec | ParameterizedEventSpec) -> str:
        if isinstance(spec, ParameterizedEventSpec):
            return f"{spec.event_type.value}({event_target_key(spec.target)})"
        return spec.event_type.value

    def _parameter_tolerance(self, spec: ParameterizedEventSpec) -> int:
        declaration = self.experiment.parameters.for_dimension(spec.dimension)
        if isinstance(declaration, NumericParameterConfig):
            return declaration.tolerance
        return 0

    def _parameter_unit(self, dimension: ParameterDimension) -> str | None:
        declaration = self.experiment.parameters.for_dimension(dimension)
        if isinstance(declaration, NumericParameterConfig):
            return declaration.unit
        return None

    def _precommand_recheck(self, spec: EventSpec | ParameterizedEventSpec) -> DeviceState | None:
        """Re-read the relevant dimensions after the idle wait.

        Returns the fresh device state to record, or None when the planned event
        must not run and another candidate should be selected.
        """
        if isinstance(spec, ParameterizedEventSpec):
            current_state = self.adapter.read_state()
            if spec.required_state is not None and current_state is not spec.required_state:
                self.journal.append({
                    "kind": "precommand_state_changed",
                    "planned_event_type": spec.event_type.value,
                    "planned_target": event_target_key(spec.target),
                    "after": current_state.value,
                    "at_unix_ns": time.time_ns(),
                })
                return None
            tolerance = self._parameter_tolerance(spec)
            observation = self._read_parameter_safe(spec.dimension)
            if observation is None or not observation.known:
                self.journal.append({
                    "kind": "precommand_unreadable",
                    "planned_event_type": spec.event_type.value,
                    "planned_target": event_target_key(spec.target),
                    "at_unix_ns": time.time_ns(),
                })
                return None
            if value_hits_target(spec, observation.value, tolerance=tolerance):
                self.journal.append({
                    "kind": "precommand_target_reached",
                    "planned_event_type": spec.event_type.value,
                    "planned_target": event_target_key(spec.target),
                    "observed": observation.value,
                    "note": "target already reached; not counted as a state change",
                    "at_unix_ns": time.time_ns(),
                })
                return None
            return current_state
        current_state = self.adapter.read_state()
        if current_state is not spec.required_state:
            self.journal.append({
                "kind": "precommand_state_changed",
                "planned_event_type": spec.event_type.value,
                "after": current_state.value,
                "at_unix_ns": time.time_ns(),
            })
            return None
        return current_state

    def _retry_recheck(self, spec: EventSpec | ParameterizedEventSpec, record: ActionRecord) -> bool:
        """Between attempts: abort the retry when the target is already reached or unreadable."""
        if isinstance(spec, ParameterizedEventSpec):
            tolerance = self._parameter_tolerance(spec)
            observation = self._read_parameter_safe(event_dimension(spec))
            if observation is None or not observation.known:
                self.journal.append({
                    "kind": "retry_aborted",
                    "event_id": record.event_id,
                    "reason": "parameter_unreadable",
                    "at_unix_ns": time.time_ns(),
                })
                return False
            if value_hits_target(spec, observation.value, tolerance=tolerance):
                self.journal.append({
                    "kind": "retry_aborted",
                    "event_id": record.event_id,
                    "reason": "target_already_reached",
                    "at_unix_ns": time.time_ns(),
                })
                return False
            if spec.required_state is not None:
                current_state = self.adapter.read_state()
                if current_state is not spec.required_state:
                    self.journal.append({
                        "kind": "retry_aborted",
                        "event_id": record.event_id,
                        "reason": f"state_changed_to_{current_state.value}",
                        "at_unix_ns": time.time_ns(),
                    })
                    return False
            return True
        try:
            state_before = self.adapter.read_state()
        except Exception as exc:  # noqa: BLE001 - retry evidence is already persisted.
            self.journal.append({
                "kind": "retry_aborted",
                "event_id": record.event_id,
                "reason": f"state_read_failed: {exc}",
                "at_unix_ns": time.time_ns(),
            })
            return False
        if state_before is not spec.required_state:
            self.journal.append({
                "kind": "retry_aborted",
                "event_id": record.event_id,
                "reason": f"state_changed_to_{state_before.value}",
                "at_unix_ns": time.time_ns(),
            })
            return False
        return True

    def _read_parameter_safe(self, dimension: ParameterDimension | None) -> PageObservation | None:
        reader = getattr(self.adapter, "read_parameter", None)
        if dimension is None or not callable(reader):
            return None
        try:
            return reader(dimension)
        except Exception as exc:  # noqa: BLE001 - unreadable dimensions are just not executable now.
            self.journal.append({
                "kind": "parameter_read_failed",
                "dimension": dimension.value,
                "error": str(exc),
                "at_unix_ns": time.time_ns(),
            })
            return None

    def _choose_event(
        self,
        state: DeviceState,
        remaining: Counter[tuple[str, str]],
    ) -> EventSpec | ParameterizedEventSpec | None:
        candidates: list[EventSpec | ParameterizedEventSpec] = []
        for spec in self.experiment.events:
            if remaining[event_identity(spec)] <= 0:
                continue
            if isinstance(spec, ParameterizedEventSpec):
                if spec.required_state is not None and spec.required_state is not state:
                    continue
                # Read the target dimension now: only events whose target is not yet
                # reached and whose state is readable are executable. A readable page
                # that marks no scene (value None) is a legitimate executable state.
                observation = self._read_parameter_safe(spec.dimension)
                if observation is None or not observation.known:
                    continue
                if value_hits_target(spec, observation.value, tolerance=self._parameter_tolerance(spec)):
                    self.journal.append({
                        "kind": "target_already_reached",
                        "planned_event_type": spec.event_type.value,
                        "planned_target": event_target_key(spec.target),
                        "observed": observation.value,
                        "note": "deferred; selecting an executable event",
                        "at_unix_ns": time.time_ns(),
                    })
                    continue
                candidates.append(spec)
            elif spec.required_state is state:
                candidates.append(spec)
        # Scene presets can change numeric parameters and take over their controls.
        # Finish executable numeric targets before selecting a scene.
        sliders = [
            spec for spec in candidates
            if isinstance(spec, ParameterizedEventSpec)
            and spec.dimension in {ParameterDimension.BRIGHTNESS, ParameterDimension.COLOR_TEMPERATURE}
        ]
        if sliders:
            candidates = sliders
        return self.rng.choice(candidates) if candidates else None

    def _run_one(
        self,
        spec: EventSpec | ParameterizedEventSpec,
        state_before: DeviceState,
        sequence: int,
        attempt: int,
    ) -> ActionRecord:
        if isinstance(spec, ParameterizedEventSpec):
            return self._run_parameterized_one(spec, state_before, sequence, attempt)
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
            capture_ack = getattr(self.adapter, "capture_ack_evidence", None)
            if callable(capture_ack):
                try:
                    capture_ack(self.paths.screenshots, event_id)
                except Exception as evidence_exc:  # noqa: BLE001 - preserve the observed App result.
                    error_code = "app_evidence_capture_failed"
                    notes = f"{notes}; app_evidence_capture_failed={evidence_exc}"
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
        return record

    @staticmethod
    def _classify(ack: AckEvidence, ha: Any, expected: DeviceState) -> EventResult:
        if ack.acknowledged and ha is not None and ha.observed_state is expected:
            return EventResult.CONFIRMED
        if ack.acknowledged:
            return EventResult.APP_ACK_ONLY
        if ha is not None and ha.observed_state is expected:
            return EventResult.HA_ONLY
        return EventResult.TIMEOUT

    def _parameterized_evidence_files(self, tag: str) -> list[str]:
        return sorted(
            f"screenshots/{path.name}"
            for path in self.paths.screenshots.glob(f"{tag}.*")
        )

    def _run_parameterized_one(
        self,
        spec: ParameterizedEventSpec,
        state_before: DeviceState,
        sequence: int,
        attempt: int,
    ) -> ActionRecord:
        event_id = create_event_id(self.session_id, sequence, attempt)
        self.journal.append({
            "kind": "event_started",
            "event_id": event_id,
            "dimension": spec.dimension.value,
            "target": event_target_key(spec.target),
            "at_unix_ns": time.time_ns(),
        })
        tolerance = self._parameter_tolerance(spec)
        unit = self._parameter_unit(spec.dimension)
        notes = ""
        error_code: str | None = None
        t_before = time.time_ns()
        t_after = t_before
        t_ack: int | None = None
        observed_before: PageObservation | None = None
        observed_after: PageObservation | None = None
        state_after = DeviceState.UNKNOWN
        try:
            observed_before = self.adapter.read_parameter(
                spec.dimension, evidence=(self.paths.screenshots, f"{event_id}_before"),
            )
            observed_before = observed_before.model_copy(update={
                "evidence_files": self._parameterized_evidence_files(f"{event_id}_before"),
            })
            if not observed_before.known:
                raise AdapterError(
                    f"cannot act on unreadable {spec.dimension.value} before value",
                    code="parameter_before_unreadable",
                )
            if value_hits_target(spec, observed_before.value, tolerance=tolerance):
                raise AdapterError(
                    f"{spec.dimension.value} already matches target before the command",
                    code="parameter_precondition",
                )
            t_before = time.time_ns()
            self.journal.append({
                "kind": "event_command",
                "event_id": event_id,
                "event_type": spec.event_type.value,
                "target": event_target_key(spec.target),
                "at_unix_ns": t_before,
            })
            self.adapter.perform_parameterized_event(spec)
            t_after = time.time_ns()
            observed_after = self.adapter.wait_for_parameter(
                spec,
                self.experiment.sessions.parameter_ack_timeout_seconds,
                evidence=(self.paths.screenshots, f"{event_id}_after"),
            )
            observed_after = observed_after.model_copy(update={
                "evidence_files": self._parameterized_evidence_files(f"{event_id}_after"),
            })
            t_ack = observed_after.observed_at_unix_ns
            post_hits = observed_after.known and value_hits_target(
                spec, observed_after.value, tolerance=tolerance,
            )
            pre_hits = observed_before.known and value_hits_target(
                spec, observed_before.value, tolerance=tolerance,
            )
            if post_hits and not pre_hits:
                result = EventResult.APP_ACK_ONLY
                error_code = None
            elif observed_after.known:
                # Readable page that simply never reached the target (including a
                # page that marks no scene) is an honest timeout, not a crash.
                result = EventResult.TIMEOUT
                error_code = "parameter_target_timeout"
            else:
                result = EventResult.FAILED
                error_code = "parameter_unreadable"
            notes = (
                f"target={event_target_key(spec.target)}"
                f" before={observed_before.value!r}"
                f" after={observed_after.value!r}"
                f" tolerance={tolerance}"
            )
            try:
                state_after = self.adapter.read_state()
            except Exception:  # noqa: BLE001 - the parameter receipt is already recorded.
                state_after = DeviceState.UNKNOWN
        except AdapterError as exc:
            t_after = time.time_ns()
            if exc.code == "parameter_before_unreadable":
                result = EventResult.FAILED
            elif exc.code.endswith("timeout"):
                result = EventResult.TIMEOUT
            else:
                result = EventResult.AUTOMATION_ERROR
            error_code = exc.code
            notes = str(exc)
            state_after = DeviceState.UNKNOWN
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
            result = EventResult.AUTOMATION_ERROR
            error_code = "unexpected_exception"
            notes = str(exc)
            state_after = DeviceState.UNKNOWN
            try:
                self.adapter.capture_diagnostics(self.paths.screenshots, event_id)
            except Exception:  # noqa: BLE001,S110 - diagnostics are best effort on unknown failures.
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
            expected_state=DeviceState.UNKNOWN,
            state_after=state_after,
            t_cmd_before_ns=t_before,
            t_cmd_after_ns=t_after,
            t_app_ack_ns=t_ack if result is EventResult.APP_ACK_ONLY else None,
            t_ha_state_ns=None,
            result=result,
            attempt=attempt,
            error_code=error_code,
            notes=notes,
            dimension=spec.dimension,
            target_value=spec.target,
            unit=unit,
            tolerance=tolerance,
            observed_before=observed_before,
            observed_after=observed_after,
        )
        self._persist_record(record)
        self.journal.append({"kind": "event_finished", "event_id": event_id, "result": result.value, "at_unix_ns": time.time_ns()})
        return record

    def _persist_record(self, record: ActionRecord) -> None:
        self.records.append(record)
        self.actions.append_action(record)

    def _write_quality_report(self, capture_result: Any, outcome: str) -> None:
        counts = Counter(record.result.value for record in self.records)
        capture_ok = (
            capture_result is None
            or not capture_result.enabled
            or (
                capture_result.return_code == 0
                and capture_result.path is not None
                and capture_result.path.exists()
            )
        )
        planned_by_identity = {
            identity_key(event_identity(event)): self.experiment.sessions.repetitions_per_event
            for event in self.experiment.events
        }
        results_by_identity: dict[str, dict[str, int]] = {}
        completed_by_identity: Counter[str] = Counter()
        for record in self.records:
            identity = f"{record.event_type.value}|{event_target_key(record.target_value)}"
            if record.error_code == "no_legal_transition":
                # Plan-abort marker; not an attempt against a planned identity.
                continue
            results_by_identity.setdefault(identity, Counter())[record.result.value] += 1
            completed_by_identity[identity] += 1
        report = {
            "session_id": self.session_id,
            "session_outcome": outcome,
            "planned_repetitions_per_event": self.experiment.sessions.repetitions_per_event,
            "record_count": len(self.records),
            "planned_event_count": len(self.experiment.events) * self.experiment.sessions.repetitions_per_event,
            "completed_event_count": self.completed_events,
            "planned_by_identity": planned_by_identity,
            "completed_by_identity": dict(completed_by_identity),
            "results_by_identity": {key: dict(value) for key, value in results_by_identity.items()},
            "result_counts": dict(counts),
            "app_success_rate": sum(record.result in {EventResult.CONFIRMED, EventResult.APP_ACK_ONLY} for record in self.records) / len(self.records) if self.records else 0.0,
            "capture": capture_result.model_dump(mode="json") if capture_result is not None else None,
            "capture_ok": capture_ok,
            "actions_sha256": _sha256(self.paths.actions_jsonl) if self.paths.actions_jsonl.exists() else None,
            "generated_at_unix_ns": time.time_ns(),
        }
        self.paths.quality_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_session_plan(root: Path) -> ExperimentConfig | None:
    session_yaml = root / "session.yaml"
    if not session_yaml.exists():
        return None
    try:
        data = yaml.safe_load(session_yaml.read_text(encoding="utf-8")) or {}
        experiment_data = data.get("experiment")
        if isinstance(experiment_data, dict):
            return ExperimentConfig.model_validate(experiment_data)
    except Exception:  # noqa: BLE001 - a broken session.yaml must not crash the validator.
        return None
    return None


def _parameterized_record_issues(
    row: dict[str, Any],
    plan: ExperimentConfig | None,
    session_root: Path,
) -> list[str]:
    issues: list[str] = []
    event_id = row.get("event_id") or "<unknown>"
    if row.get("error_code") == "no_legal_transition":
        return issues  # plan-abort marker, not an attempt against a planned identity
    dimension = row.get("dimension")
    if not dimension:
        return issues  # legacy two-state record
    event_type = row.get("event_type")
    target = row.get("target_value")
    identity = f"{event_type}|{event_target_key(target)}"
    result = row.get("result")
    before = row.get("observed_before") or {}
    after = row.get("observed_after") or {}
    scene_declaration: SceneParameterConfig | None = None

    if plan is not None:
        planned = {
            identity_key(event_identity(event))
            for event in plan.events
            if isinstance(event, ParameterizedEventSpec)
        }
        if identity not in planned:
            issues.append(f"{event_id}: identity {identity} is not part of the session plan")
        try:
            dimension_enum = ParameterDimension(dimension)
        except ValueError:
            dimension_enum = None
        declaration = plan.parameters.for_dimension(dimension_enum) if dimension_enum else None
        if isinstance(declaration, SceneParameterConfig):
            scene_declaration = declaration
        if isinstance(declaration, NumericParameterConfig) and isinstance(target, int) and not isinstance(target, bool):
            low, high = declaration.range
            if not low <= target <= high:
                issues.append(f"{event_id}: target {target} outside declared range [{low}, {high}]")

    if result in {"app_ack_only", "confirmed"}:
        if result == "confirmed":
            issues.append(
                f"{event_id}: parameterized success must be app_ack_only, not independent confirmation"
            )
        if not before or not after:
            issues.append(f"{event_id}: success record is missing before/after page observations")
            return issues
        spec_like = {
            "event_type": event_type,
            "target": target,
            "required_state": None,
        }
        try:
            spec = ParameterizedEventSpec.model_validate(spec_like)
        except Exception:  # noqa: BLE001 - malformed targets are reported below.
            issues.append(f"{event_id}: malformed target {target!r}")
            return issues
        tolerance = row.get("tolerance")
        tolerance = tolerance if isinstance(tolerance, int) else 0
        if not before.get("known"):
            issues.append(f"{event_id}: success record has no readable before value")
        elif value_hits_target(spec, before.get("value"), tolerance=tolerance):
            issues.append(f"{event_id}: success record shows the target already reached before the action")
        if not after.get("known"):
            issues.append(f"{event_id}: success record has no readable after value")
        elif not value_hits_target(spec, after.get("value"), tolerance=tolerance):
            issues.append(
                f"{event_id}: after value {after.get('value')!r} misses target {target!r} (tolerance {tolerance})"
            )
        if dimension == ParameterDimension.SCENE.value and scene_declaration is not None \
                and scene_declaration.presets and plan is not None:
            brightness_tolerance = plan.parameters.brightness.tolerance
            temperature_tolerance = plan.parameters.color_temperature.tolerance
            for phase, observation in (("before", before), ("after", after)):
                readback = observation.get("readback_values") or {}
                brightness = readback.get("brightness")
                temperature = readback.get("color_temperature")
                if (
                    isinstance(brightness, bool) or not isinstance(brightness, int)
                    or isinstance(temperature, bool) or not isinstance(temperature, int)
                ):
                    issues.append(f"{event_id}: {phase} scene is missing numeric preset readback")
                    continue
                matching = [
                    name for name, preset in scene_declaration.presets.items()
                    if abs(brightness - preset.brightness) <= brightness_tolerance
                    and abs(temperature - preset.color_temperature) <= temperature_tolerance
                ]
                inferred = matching[0] if len(matching) == 1 else None
                if len(matching) > 1 or observation.get("value") != inferred:
                    issues.append(
                        f"{event_id}: {phase} scene {observation.get('value')!r} "
                        f"does not match numeric readback {readback!r}"
                    )
        t_before_cmd = row.get("t_cmd_before_ns")
        t_after_cmd = row.get("t_cmd_after_ns")
        observed_before_at = before.get("observed_at_unix_ns")
        observed_after_at = after.get("observed_at_unix_ns")
        stamps = [observed_before_at, t_before_cmd, t_after_cmd, observed_after_at]
        if any(isinstance(stamp, bool) or not isinstance(stamp, int) or stamp <= 0 for stamp in stamps):
            issues.append(f"{event_id}: success record is missing observation or command timestamps")
        elif stamps != sorted(stamps):
            issues.append(f"{event_id}: observation and command timestamps are out of order")
        if row.get("t_app_ack_ns") != observed_after_at:
            issues.append(f"{event_id}: App acknowledgment time does not match after observation")
        for phase, observation in (("before", before), ("after", after)):
            refs = observation.get("evidence_files") or []
            if not refs:
                issues.append(f"{event_id}: {phase} evidence references are empty")
            for ref in refs:
                relative = Path(ref)
                if (
                    relative.is_absolute() or ".." in relative.parts
                    or len(relative.parts) != 2 or relative.parts[0] != "screenshots"
                    or not relative.name.startswith(f"{event_id}_{phase}.")
                ):
                    issues.append(f"{event_id}: invalid {phase} evidence reference: {ref}")
                    continue
                evidence_path = session_root / relative
                if not evidence_path.is_file():
                    issues.append(f"{event_id}: evidence file missing: {ref}")
                    continue
                if dimension == ParameterDimension.SCENE.value and relative.suffix == ".xml" \
                        and observation.get("readback_values"):
                    try:
                        texts = {
                            node.attrib.get("text") for node in ET.parse(evidence_path).getroot().iter()
                        }
                    except (ET.ParseError, OSError):
                        issues.append(f"{event_id}: unreadable scene XML evidence: {ref}")
                        continue
                    readback = observation["readback_values"]
                    if (
                        f"{readback.get('brightness')}%" not in texts
                        or f"{readback.get('color_temperature')}K" not in texts
                    ):
                        issues.append(f"{event_id}: {phase} XML disagrees with scene readback: {ref}")
    return issues


def validate_session(root: Path) -> dict[str, Any]:
    actions_path = root / "actions.jsonl"
    journal_path = root / "run_journal.jsonl"
    actions = ActionJournal(actions_path).read()
    journal = JsonlJournal(journal_path).read()
    ids = [row.get("event_id") for row in actions]
    logical_ids = {event_id.rsplit("_attempt_", 1)[0] for event_id in ids if event_id}
    unique = len(ids) == len(set(ids))
    started = {row.get("event_id") for row in journal if row.get("kind") == "event_started"}
    finished = {row.get("event_id") for row in journal if row.get("kind") == "event_finished"}
    open_events = sorted(event_id for event_id in started - finished if event_id)
    quality_path = root / "quality_report.json"
    quality = None
    if quality_path.exists():
        try:
            quality = json.loads(quality_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            quality = None
    planned_event_count = quality.get("planned_event_count") if isinstance(quality, dict) else None
    record_count_matches = bool(quality) and quality.get("record_count") == len(actions)
    capture_ok = bool(quality) and quality.get("capture_ok") is True
    session_completed = bool(quality) and quality.get("session_outcome") == "completed"
    planned_events_complete = (
        isinstance(planned_event_count, int)
        and planned_event_count == len(logical_ids)
        and quality.get("completed_event_count") == planned_event_count
    )
    plan = _load_session_plan(root)
    parameterized_issues: list[str] = []
    for row in actions:
        parameterized_issues.extend(_parameterized_record_issues(row, plan, root))
    parameterized_identity_match: bool | None = None
    if isinstance(quality, dict) and "planned_by_identity" in quality and plan is not None:
        expected = {
            identity_key(event_identity(event)): plan.sessions.repetitions_per_event
            for event in plan.events
        }
        parameterized_identity_match = quality.get("planned_by_identity") == expected
    result = {
        "session_root": str(root),
        "actions_exist": actions_path.exists(),
        "unique_event_ids": unique,
        "event_count": len(actions),
        "open_events": open_events,
        "quality_report_exists": quality_path.exists(),
        "quality_report_valid": quality is not None,
        "record_count_matches": record_count_matches,
        "capture_ok": capture_ok,
        "session_completed": session_completed,
        "planned_events_complete": planned_events_complete,
        "planned_event_count": planned_event_count,
        "completed_logical_events": len(logical_ids),
        "parameterized_record_issues": parameterized_issues,
        "planned_identity_match": parameterized_identity_match,
        "ok": (
            actions_path.exists()
            and unique
            and not open_events
            and quality is not None
            and record_count_matches
            and capture_ok
            and session_completed
            and planned_events_complete
            and not parameterized_issues
            and parameterized_identity_match is not False
        ),
    }
    return result
