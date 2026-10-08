from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from iot_exp import cli
from iot_exp.process_identity import process_is_running, process_start_token
from iot_exp.resources import ResourceLease
from iot_exp.scheduler import TaskScheduler
from iot_exp.task_discovery import TaskDiscovery
from iot_exp.task_models import TaskRequest
from iot_exp.task_store import TaskStore

ROOT = Path(__file__).parents[1]


def setup_root(root: Path):
    (root / "experiment").mkdir()
    (root / "runtime").mkdir()
    shutil.copy(ROOT / "experiment" / "mi_desk_lamp_1s.yaml", root / "experiment")
    shutil.copy(ROOT / "runtime" / "windows-dev.yaml", root / "runtime")
    store = TaskStore(root / "runs" / "console.sqlite3")
    return store, TaskDiscovery(root, store, interval_seconds=0)


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def snapshot(path: Path, **overrides):
    value = {
        "created_at_unix_ns": time.time_ns(),
        "experiment": {
            "experiment_id": "mi_desk_lamp_1s_v1",
            "device": {"device_id": "lamp", "display_name": "台灯"},
            "phone": {"udid": "PHONE"}, "events": [{}, {}],
            "sessions": {"repetitions_per_event": 2},
        },
        "runtime": {"mode": "dev", "appium_url": "http://127.0.0.1:4723"},
        **overrides,
    }
    path.mkdir(parents=True)
    (path / "session.yaml").write_text(yaml.safe_dump(value), encoding="utf-8")
    return value


def test_real_cli_completed_session_is_discovered_idempotently(tmp_path, monkeypatch):
    store, discovery = setup_root(tmp_path)
    experiment_path = tmp_path / "experiment" / "mi_desk_lamp_1s.yaml"
    experiment = yaml.safe_load(experiment_path.read_text(encoding="utf-8"))
    experiment["sessions"].update(idle_range_seconds=[0, 0], cooldown_seconds=0)
    experiment_path.write_text(yaml.safe_dump(experiment), encoding="utf-8")
    monkeypatch.setattr("iot_exp.cli.run_preflight", lambda *_args: {"ok": True, "checks": []})
    monkeypatch.setattr("iot_exp.orchestrator.collect_system_checks", lambda **_kwargs: [])
    assert cli.main([
        "--experiment", str(experiment_path), "--runtime", str(tmp_path / "runtime" / "windows-dev.yaml"),
        "run", "--dry-run", "--session-id", "external_cli", "--repetitions", "1",
    ]) == 0
    discovery.refresh()
    first = store.list()
    assert len(first) == 1 and first[0]["source"] == "cli"
    assert first[0]["status"] == "completed" and first[0]["controllable"] is False
    assert first[0]["completed_events"] == first[0]["planned_events"] == 2
    assert first[0]["request"]["mode"] == "simulate"
    discovery.refresh(force=True)
    assert store.list() == first
    assert discovery.session_path("external_cli") == tmp_path / "runs" / "sessions" / "external_cli"
    assert discovery.sessions()[0]["validation"]["ok"] is True
    logs = discovery.logs(first[0]["id"])
    assert any("session_finished" in row["message"] for row in logs)
    assert discovery.logs(first[0]["id"], after=logs[-1]["id"]) == []


def test_running_cli_process_then_exit_is_detected(tmp_path):
    store, discovery = setup_root(tmp_path)
    experiment_path = tmp_path / "experiment" / "mi_desk_lamp_1s.yaml"
    experiment = yaml.safe_load(experiment_path.read_text(encoding="utf-8"))
    experiment["sessions"].update(idle_range_seconds=[1, 1], cooldown_seconds=1)
    experiment_path.write_text(yaml.safe_dump(experiment), encoding="utf-8")
    script = """
import sys
from pathlib import Path
from iot_exp import cli, orchestrator
cli.run_preflight = lambda *args: {"ok": True, "checks": []}
orchestrator.collect_system_checks = lambda **kwargs: []
runner_init = cli.ExperimentRunner.__init__
def initialize(self, *args, **kwargs):
    runner_init(self, *args, cancel_requested=lambda: Path("stop-test-cli").exists(), **kwargs)
cli.ExperimentRunner.__init__ = initialize
raise SystemExit(cli.main(sys.argv[1:]))
"""
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    process = subprocess.Popen(
        [sys.executable, "-c", script, "--experiment", str(experiment_path),
         "--runtime", str(tmp_path / "runtime" / "windows-dev.yaml"), "run", "--dry-run",
         "--session-id", "live_cli", "--repetitions", "20"],
        cwd=tmp_path, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if (tmp_path / "runs" / "sessions" / "live_cli" / "run_journal.jsonl").exists():
                break
            assert process.poll() is None
            time.sleep(0.05)
        discovery.refresh()
        assert len(store.active()) == 1
        task = store.active()[0]
        recorded = yaml.safe_load((Path(task["session_root"]) / "session.yaml").read_text(encoding="utf-8"))
        assert task["status"] == "running" and task["pid"] == recorded["process_id"]
        assert task["metadata"]["state_evidence"] == "live_process"
        assert not task["controllable"]
    finally:
        (tmp_path / "stop-test-cli").touch()
        process.wait(timeout=5)
    discovery.refresh(force=True)
    assert store.get(task["id"])["status"] == "cancelled"
    assert store.get(task["id"])["pid"] is None


def test_legacy_generated_cli_lease_identifies_active_session(tmp_path):
    store, discovery = setup_root(tmp_path)
    with ResourceLease(tmp_path / "runs", ["phone:PHONE"], "cli:generated"):
        snapshot(tmp_path / "runs" / "sessions" / "old_generated_cli")
        discovery.refresh()
        assert store.active()[0]["status"] == "running"
    discovery.refresh()
    assert store.list()[0]["status"] == "interrupted"


def test_campaign_archive_nested_evidence_and_session_id_collision(tmp_path):
    store, discovery = setup_root(tmp_path)
    snapshot(tmp_path / "runs" / "sessions" / "same_name")
    archive = tmp_path / "results" / "same_name"
    snapshot(archive)
    write_json(archive / "quality_report.json", {"session_outcome": "completed"})
    campaign = tmp_path / "results" / "lamp_pu"
    write_json(campaign / "campaign.json", {
        "protocol": "lamp_pu_fixed_acquisition_v1", "status": "completed",
        "groups": {"scene": {"planned_target": 120, "planned_u_operations": 22, "processed": 142}},
    })
    (campaign / "scene" / "screenshots").mkdir(parents=True)
    (campaign / "scene" / "actions.jsonl").touch()
    (campaign / "scene" / "screenshots" / "scene_after.png").touch()
    discovery.refresh()
    assert len(store.list()) == 3
    sessions = discovery.sessions()
    assert len({item["session_id"] for item in sessions}) == 3
    campaign_session = next(item for item in sessions if item.get("is_campaign"))
    assert "scene/actions.jsonl" in campaign_session["artifacts"]
    assert "scene/screenshots/scene_after.png" in campaign_session["evidence"]
    assert discovery.session_path(campaign_session["session_id"]) == campaign
    assert store.get(campaign_session["task_id"])["planned_events"] == 142
    assert all(not item["controllable"] for item in store.list())


def test_default_script_results_campaign_can_be_running_before_archival(tmp_path):
    store, discovery = setup_root(tmp_path)
    campaign = tmp_path / "results" / "live_lamp"
    write_json(campaign / "campaign.json", {"status": "running", "groups": {}, "current_group": "scene"})
    (campaign / "configuration.yaml").write_text(yaml.safe_dump({
        "experiment": {"phone": {"udid": "PHONE"}, "device": {"device_id": "lamp"}},
        "runtime": {"mode": "formal", "output_root": str(tmp_path / "runs")},
    }), encoding="utf-8")
    with ResourceLease(tmp_path / "runs", ["phone:PHONE"], "live_lamp"):
        discovery.refresh()
        task = store.active()[0]
        assert task["source"] == "campaign" and task["status"] == "running"
    discovery.refresh()
    assert store.get(task["id"])["status"] == "interrupted"


def test_campaign_becoming_archive_updates_source_and_session_identity(tmp_path):
    store, discovery = setup_root(tmp_path)
    campaign = tmp_path / "results" / "lamp"
    write_json(campaign / "campaign.json", {"status": "completed", "groups": {}})
    discovery.refresh()
    first = store.list()[0]
    assert first["source"] == "campaign"
    write_json(campaign / "archive_manifest.json", {"schema": "experiment_archive_v1"})
    discovery.refresh()
    archived = store.list()
    assert len(archived) == 1 and archived[0]["id"] == first["id"]
    assert archived[0]["source"] == "archive"
    assert archived[0]["session_id"] != first["session_id"]
    assert discovery.session_path(archived[0]["session_id"]) == campaign
    assert discovery.sessions()[0]["session_id"] == archived[0]["session_id"]


def test_same_legacy_session_name_in_another_output_root_is_not_a_live_match(tmp_path):
    store, discovery = setup_root(tmp_path)
    alternate = tmp_path / "alternate-runs"
    (tmp_path / "runtime" / "alternate.yaml").write_text(yaml.safe_dump({"output_root": str(alternate)}), encoding="utf-8")
    snapshot(tmp_path / "runs" / "sessions" / "duplicate")
    with ResourceLease(alternate, ["phone:PHONE"], "cli:duplicate"):
        discovery.refresh()
        assert store.list()[0]["status"] == "interrupted"


def test_controller_session_is_not_duplicated_or_replaced(tmp_path):
    store, discovery = setup_root(tmp_path)
    request = TaskRequest(template_id="mi_desk_lamp_1s")
    task = store.create_batch("managed-session", [request], {request.template_id: 2})[0]
    path = tmp_path / "runs" / "sessions" / "managed"
    snapshot(path)
    store.update(task["id"], status="running", session_id="managed", session_root=str(path))
    discovery.refresh()
    assert len(store.list()) == 1
    assert store.list()[0]["source"] == "console" and store.list()[0]["status"] == "running"
    assert discovery.sessions()[0]["task_id"] == task["id"]


def test_partial_or_corrupt_manifest_never_claims_running(tmp_path):
    store, discovery = setup_root(tmp_path)
    path = tmp_path / "runs" / "sessions" / "partial"
    snapshot(path, process_id=os.getpid(), process_start_token="reused-pid-token")
    (path / "run_journal.jsonl").write_text('{"kind":"event_finished"}\n{"kind":', encoding="utf-8")
    (path / "quality_report.json").write_text('{"session_outcome":', encoding="utf-8")
    discovery.refresh()
    assert store.list()[0]["status"] == "interrupted"
    assert store.list()[0]["completed_events"] == 1
    assert process_is_running(os.getpid(), process_start_token(os.getpid()))
    assert not process_is_running(os.getpid(), "reused-pid-token")


def test_moved_live_session_does_not_leave_stale_active_task(tmp_path):
    store, discovery = setup_root(tmp_path)
    path = tmp_path / "runs" / "sessions" / "moving"
    snapshot(path, process_id=os.getpid(), process_start_token=process_start_token(os.getpid()))
    discovery.refresh()
    task = store.active()[0]
    path.rename(tmp_path / "moved-session")
    discovery.refresh()
    assert store.get(task["id"])["status"] == "interrupted"
    assert store.active() == []


def test_scheduler_does_not_manage_external_process_or_ignore_its_lease(tmp_path, monkeypatch):
    store, discovery = setup_root(tmp_path)
    path = tmp_path / "runs" / "sessions" / "outside"
    snapshot(path)
    scheduler = TaskScheduler(store, tmp_path)
    request = TaskRequest(template_id="mi_desk_lamp_1s", mode="device", udid="PHONE")
    queued = store.create_batch("wait-for-external", [request], {request.template_id: 2})[0]
    launched = []
    monkeypatch.setattr(scheduler, "_launch", lambda *args: launched.append(args))
    with ResourceLease(tmp_path / "runs", ["phone:PHONE"], "cli:outside"):
        discovery.refresh()
        external = next(task for task in store.list() if not task["controllable"])
        with pytest.raises(RuntimeError, match="原启动进程"):
            scheduler.request_stop(external["id"])
        with pytest.raises(RuntimeError, match="原启动进程"):
            scheduler.force_stop(external["id"])
        store.interrupt_unfinished()
        assert store.get(external["id"])["status"] == "running"
        # Restart interruption affects managed tasks only. Submit a new one for dispatch.
        queued = store.create_batch("wait-for-external-again", [request], {request.template_id: 2})[0]
        scheduler.tick()
        assert not launched and store.get(queued["id"])["queue_reason"]


def test_runtime_configured_output_root_is_discovered(tmp_path):
    store, discovery = setup_root(tmp_path)
    output = tmp_path / "custom-output"
    (tmp_path / "runtime" / "custom.yaml").write_text(yaml.safe_dump({"output_root": str(output)}), encoding="utf-8")
    path = output / "sessions" / "custom"
    snapshot(path)
    discovery.refresh()
    assert store.list()[0]["session_root"] == str(path)
    assert discovery.session_path(store.list()[0]["session_id"]) == path


def test_cancelled_queue_snapshot_cannot_launch_a_worker(tmp_path, monkeypatch):
    store, _discovery = setup_root(tmp_path)
    request = TaskRequest(template_id="mi_desk_lamp_1s")
    stale = store.create_batch("cancel-before-claim", [request], {request.template_id: 2})[0]
    scheduler = TaskScheduler(store, tmp_path)
    scheduler.request_stop(stale["id"])
    launched = []
    monkeypatch.setattr("iot_exp.scheduler.subprocess.Popen", lambda *args, **kwargs: launched.append(args))
    scheduler._launch(stale, 0)
    assert not launched
    assert store.get(stale["id"])["status"] == "cancelled"


def test_evidence_inventory_omits_source_scripts_and_escaping_symlinks(tmp_path):
    _store, discovery = setup_root(tmp_path)
    path = tmp_path / "results" / "campaign"
    write_json(path / "campaign.json", {"status": "completed", "groups": {}})
    (path / "capture_lamp_pu_source.py").touch()
    outside = tmp_path / "not-results"
    snapshot(outside)
    try:
        (tmp_path / "results" / "outside-link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pass  # Symlink creation can require a Windows policy not needed by the console.
    discovery.refresh()
    sessions = discovery.sessions()
    assert len(sessions) == 1
    assert "capture_lamp_pu_source.py" not in sessions[0]["artifacts"]


@pytest.mark.parametrize("relative", ["results", "runs/sessions", "runs/campaigns"])
def test_collection_parent_symlink_cannot_expand_discovery_scope(tmp_path, relative):
    store, discovery = setup_root(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-unrelated"
    path = outside / "run"
    if relative == "runs/campaigns":
        write_json(path / "campaign.json", {"status": "completed"})
    else:
        snapshot(path)
    link = tmp_path / relative
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Windows symlink creation is unavailable")
    discovery.refresh()
    assert store.list() == []


def test_registered_roots_keep_same_named_sessions_distinct_and_preserve_missing_index(tmp_path):
    store, discovery = setup_root(tmp_path)
    roots = [tmp_path / 'external-one', tmp_path / 'external-two']
    for root in roots:
        path = root / 'sessions/same_name'
        snapshot(path)
        write_json(path / 'quality_report.json', {'session_outcome':'completed'})
        discovery.register_root(root)
    sessions = discovery.sessions()
    assert len(sessions) == 2
    assert len({session['session_id'] for session in sessions}) == 2
    assert {discovery.session_path(session['session_id']) for session in sessions} == {root / 'sessions/same_name' for root in roots}
    assert all(not task['controllable'] for task in store.list())
    roots[1].rename(tmp_path / 'moved')
    discovery.refresh(force=True)
    assert roots[1] in discovery.registered_roots()
    assert len(store.list()) == 2


def test_legacy_child_registration_and_automatic_scan_are_rejected(tmp_path):
    store, discovery = setup_root(tmp_path)
    blocked = tmp_path / 'legacy/nested'
    blocked.mkdir(parents=True)
    with pytest.raises(ValueError, match='不可登记'):
        discovery.register_root(blocked)
    hidden = tmp_path / 'results/legacy'
    snapshot(hidden)
    discovery.refresh(force=True)
    assert store.list() == []
