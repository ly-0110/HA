import hashlib
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from iot_exp.adapters import SimulatedLampAdapter
from iot_exp.backends import DisabledCaptureBackend, DisabledHaProvider
from iot_exp.config import load_configuration
from iot_exp.ha_reconcile import export_window, reconcile
from iot_exp.models import DeviceState
from iot_exp.orchestrator import ExperimentRunner

ROOT = Path(__file__).parents[1]
ENTITY_ID = "media_player.xiaomi_cn_636575596_lx04"


def _timestamp(ns):
    return datetime.fromtimestamp(ns / 1_000_000_000, timezone.utc).isoformat()


def _session(tmp_path):
    experiment, runtime = load_configuration(
        ROOT / "experiment" / "xiaomi_touchscreen_speaker_music.yaml",
        ROOT / "runtime" / "ubuntu-dev.yaml",
    )
    experiment = experiment.model_copy(update={
        "events": experiment.events[:1],
        "sessions": experiment.sessions.model_copy(update={
            "repetitions_per_event": 1, "idle_range_seconds": (0, 0), "cooldown_seconds": 0,
        }),
    })
    runtime = runtime.model_copy(update={"output_root": tmp_path})
    runner = ExperimentRunner(
        experiment, runtime, adapter=SimulatedLampAdapter(DeviceState.PAUSED),
        capture=DisabledCaptureBackend(), ha=DisabledHaProvider(), session_id="speaker_test",
    )
    action = runner.run()[0]
    (runner.paths.root / "traffic.pcapng").write_bytes(b"test packet")
    (runner.paths.root / "pcap_review.json").write_text(json.dumps({
        "schema": "iot_exp_pcap_review_v1",
        "session_id": "speaker_test",
        "pcap_sha256": hashlib.sha256(b"test packet").hexdigest(),
        "events": [{"event_id": action.event_id, "has_bidirectional_packets": True,
                    "packets_from_device": 1, "packets_to_device": 1}],
    }), encoding="utf-8")
    runner.paths.screenshots.mkdir(exist_ok=True)
    (runner.paths.screenshots / f"{action.event_id}.png").write_bytes(b"test screenshot")
    quality = json.loads(runner.paths.quality_report.read_text(encoding="utf-8"))
    quality["capture"] = {"enabled": True}
    runner.paths.quality_report.write_text(json.dumps(quality), encoding="utf-8")
    return runner.paths.root, action


def _history(path, action, *, duplicate=False):
    window = export_window(path)
    events = [
        {"entity_id": ENTITY_ID, "state": "paused", "last_changed": _timestamp(action.t_cmd_before_ns - 1_000_000_000), "attributes": {}},
        {"entity_id": ENTITY_ID, "state": "playing", "last_changed": _timestamp(action.t_cmd_before_ns), "attributes": {}},
    ]
    if duplicate:
        events.append({"entity_id": ENTITY_ID, "state": "playing", "last_changed": _timestamp(action.t_cmd_after_ns + 1_000_000_000), "attributes": {}})
    history = path.parent / "ha_export.json"
    history.write_text(json.dumps({
        "schema": "iot_exp_ha_history_v1", "entity_id": ENTITY_ID,
        "start_time": window["start"], "end_time": window["end"], "events": events,
    }), encoding="utf-8")
    return history


def test_reconcile_matches_one_ha_transition_without_rewriting_actions(tmp_path):
    path, action = _session(tmp_path)
    original_actions = (path / "actions.jsonl").read_bytes()
    report = reconcile(path, _history(path, action), clock_offset_ms=100, clock_uncertainty_ms=20)
    assert report["candidate_gold_count"] == 1
    assert report["events"][0]["manual_review"] == "pending"
    assert (path / "actions.jsonl").read_bytes() == original_actions
    with pytest.raises(FileExistsError):
        reconcile(path, path.parent / "ha_export.json", clock_offset_ms=100, clock_uncertainty_ms=20)


def test_reconcile_marks_duplicate_ha_states_ambiguous(tmp_path):
    path, action = _session(tmp_path)
    report = reconcile(path, _history(path, action, duplicate=True), clock_offset_ms=100, clock_uncertainty_ms=20)
    assert report["candidate_gold_count"] == 0
    assert "ha_ambiguous" in report["events"][0]["reasons"]


def test_reconcile_keeps_out_of_window_change_pending(tmp_path):
    path, action = _session(tmp_path)
    history = _history(path, action)
    payload = json.loads(history.read_text(encoding="utf-8"))
    payload["events"][1]["last_changed"] = _timestamp(action.t_cmd_after_ns + 31_000_000_000)
    history.write_text(json.dumps(payload), encoding="utf-8")
    report = reconcile(path, history, clock_offset_ms=100, clock_uncertainty_ms=20)
    assert report["candidate_gold_count"] == 0
    assert "ha_missing" in report["events"][0]["reasons"]


def test_reconcile_requires_measured_clock_uncertainty(tmp_path):
    path, action = _session(tmp_path)
    report = reconcile(path, _history(path, action), clock_offset_ms=100)
    assert report["candidate_gold_count"] == 0
    assert "clock_offset_unverified" in report["events"][0]["reasons"]


def test_exporter_normalizes_ha_history_and_never_needs_token_at_import():
    script = ROOT.parent / "legacy" / "get_device_log.py"
    spec = importlib.util.spec_from_file_location("ha_exporter", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    values = module.normalize_history([[{
        "entity_id": ENTITY_ID, "state": "playing",
        "last_changed": "2026-09-23T07:05:30Z", "attributes": {"media_title": "Song"},
    }]], ENTITY_ID)
    assert values[0]["state"] == "playing"
    assert values[0]["last_changed"].endswith("+00:00")
