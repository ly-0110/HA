import json
import os
import shutil
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from iot_exp.artifacts import MAX_PREVIEW_BYTES, artifact_response, resolve_artifact, text_preview
from iot_exp.config import load_configuration
from iot_exp.task_models import TaskRequest
from iot_exp.web import create_app, template_payload
from iot_exp.worker import _apply_request, apply_event_plan, run_task

ROOT = Path(__file__).parents[1]


@pytest.fixture
def console_root(tmp_path):
    shutil.copytree(ROOT / "experiment", tmp_path / "experiment")
    shutil.copytree(ROOT / "runtime", tmp_path / "runtime")
    return tmp_path


def event_request(events=None, **changes):
    return TaskRequest(
        template_id="mi_desk_lamp_1s_advanced", events=events,
        idle_min_seconds=0, idle_max_seconds=0, cooldown_seconds=0, **changes,
    )


def test_template_defaults_reuse_the_existing_formal_speaker_environment():
    payload = template_payload(
        ROOT / "experiment/xiaomi_touchscreen_speaker_music.yaml", ROOT / "runtime/windows-dev.yaml",
    )
    assert payload["runtime_defaults"]["runtime_id"] == "ubuntu-speaker-lab"
    assert payload["runtime_defaults"]["capture_interface"] == "wlp2s0"
    assert payload["runtime_defaults"]["capture_filter"] == "host 10.42.0.196"


def test_event_plan_rejects_invalid_targets_and_keeps_template_unchanged():
    experiment, _ = load_configuration(
        ROOT / "experiment/mi_desk_lamp_1s_advanced.yaml", ROOT / "runtime/windows-dev.yaml",
    )
    scene = {"event_type": "select_scene", "target": "阅读模式", "required_state": "on"}
    with pytest.raises(ValidationError, match="on or off"):
        event_request([{**scene, "required_state": "playing"}])
    adjusted = apply_event_plan(experiment, event_request([scene]))
    assert len(adjusted.events) == 1
    assert len(experiment.events) == 12
    for events in (
        [{**scene, "target": "不存在的场景"}],
        [{"event_type": "set_brightness", "target": 101}],
        [scene, scene],
    ):
        with pytest.raises(ValidationError):
            apply_event_plan(experiment, event_request(events))


def test_formal_request_cannot_use_dev_runtime(console_root):
    request = event_request(
        mode="formal", udid="PHONE", target_device_ip="192.168.1.2",
        capture_interface="eth0", capture_filter="host 192.168.1.2",
    )
    with pytest.raises(ValueError, match="formal"):
        _apply_request(
            {"request": request.model_dump(), "appium_port": 4723, "system_port": 8200},
            console_root,
        )


def test_edited_scene_plan_is_counted_and_executed_per_task(console_root):
    app = create_app(console_root)
    client = TestClient(app)
    token = client.get("/api/v1/bootstrap").json()["control_token"]
    events = [{"event_type": "select_scene", "target": "阅读模式", "required_state": "on"}]
    response = client.post(
        "/api/v1/tasks", headers={"x-control-token": token},
        json={"client_request_id": "workbench-plan-123", "tasks": [
            event_request(events).model_dump(mode="json"),
            event_request().model_dump(mode="json"),
        ]},
    )
    assert response.status_code == 200, response.text
    first, second = response.json()
    assert first["planned_events"] == 1
    assert second["planned_events"] == 12
    app.state.store.update(first["id"], appium_port=4723, system_port=8200)
    assert run_task(app.state.store.path, first["id"], console_root) == 0
    task = app.state.store.get(first["id"])
    assert task["completed_events"] == 1
    snapshot = Path(task["session_root"]) / "session.yaml"
    assert "阅读模式" in snapshot.read_text(encoding="utf-8")


def test_preview_and_local_open_use_original_file(console_root, monkeypatch):
    directory = console_root / "runs/sessions/evidence-test"
    directory.mkdir(parents=True)
    path = directory / "quality_report.json"
    path.write_text(json.dumps({"ok": True}), encoding="utf-8")
    (directory / "session.yaml").write_text(
        "experiment:\n  experiment_id: test\n  device:\n    display_name: 测试设备\n",
        encoding="utf-8",
    )
    app = create_app(console_root)
    client = TestClient(app)
    token = client.get("/api/v1/bootstrap").json()["control_token"]
    opened = []
    monkeypatch.setattr("iot_exp.web.open_local_path", lambda value: opened.append(value))
    url = "/api/v1/sessions/evidence-test/artifacts/quality_report.json"
    preview = client.get(url)
    assert preview.status_code == 200
    assert preview.headers["content-disposition"].startswith("inline;")
    assert preview.json() == {"ok": True}
    assert client.get(url + "?preview=true").json()["text"] == '{"ok": true}'
    assert client.get(url + "?download=true").headers["content-disposition"].startswith(
        "attachment;",
    )
    assert client.post(url + "/open").status_code == 403
    assert client.post(
        url + "/open", headers={"x-control-token": token, "origin": "http://evil.invalid"},
    ).status_code == 403
    response = client.post(url + "/open", headers={"x-control-token": token})
    assert response.status_code == 200, response.text
    assert opened == [path.resolve()]


def test_artifact_paths_reject_traversal_executables_and_symlinks(tmp_path):
    directory = tmp_path / "session"
    directory.mkdir()
    for name in ("../outside.json", "/outside.json", "C:/outside.json", "..\\outside.json"):
        with pytest.raises(HTTPException) as error:
            resolve_artifact(directory, name)
        assert error.value.status_code == 400
    executable = directory / "malicious.cmd"
    executable.write_text("echo unexpected", encoding="utf-8")
    with pytest.raises(HTTPException) as error:
        resolve_artifact(directory, executable.name)
    assert error.value.status_code == 404
    # Windows may forbid symlink creation without developer mode.
    try:
        (directory / "linked").symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        return
    with pytest.raises(HTTPException) as error:
        resolve_artifact(directory, "linked/outside.json")
    assert error.value.status_code == 400


def test_xml_evidence_is_inert_text(tmp_path):
    path = tmp_path / "page.xml"
    path.write_text("<script>alert(1)</script>", encoding="utf-8")
    response = artifact_response(path)
    assert response.media_type.startswith("text/plain")
    assert response.headers["x-content-type-options"] == "nosniff"


def test_large_log_preview_is_bounded_and_preserves_original(tmp_path):
    path = tmp_path / "actions.jsonl"
    data = b"x" * (MAX_PREVIEW_BYTES + 500)
    path.write_bytes(data)
    result = json.loads(text_preview(path).body)
    assert result["truncated"] is True
    assert len(result["text"]) == MAX_PREVIEW_BYTES
    assert result["size_bytes"] == len(data)
    assert path.read_bytes() == data


def test_worker_preserves_cancel_requested_before_process_start(console_root):
    app = create_app(console_root)
    task = app.state.store.create_batch("cancel-before-worker", [event_request()], {
        "mi_desk_lamp_1s_advanced": 12,
    })[0]
    app.state.store.update(task["id"], appium_port=4723, system_port=8200)
    marker = console_root / "runs/control" / f"{task['id']}.cancel"
    marker.parent.mkdir(parents=True)
    marker.touch()
    assert run_task(app.state.store.path, task["id"], console_root) == 0
    assert app.state.store.get(task["id"])["status"] == "cancelled"
    assert app.state.store.get(task["id"])["completed_events"] == 0


def test_incomplete_single_target_repetitions_are_not_reported_completed(console_root):
    app = create_app(console_root)
    request = event_request([{"event_type": "set_brightness", "target": 50}], repetitions=2)
    task = app.state.store.create_batch("incomplete-target-test", [request], {
        "mi_desk_lamp_1s_advanced": 12,
    })[0]
    app.state.store.update(task["id"], appium_port=4723, system_port=8200)
    assert run_task(app.state.store.path, task["id"], console_root) == 2
    finished = app.state.store.get(task["id"])
    assert finished["status"] == "failed"
    assert finished["quality"]["session_outcome"] == "incomplete"
    assert finished["quality"]["validation"]["ok"] is False


def test_formal_preflight_failure_records_actionable_detail(console_root, monkeypatch):
    app = create_app(console_root)
    request = event_request(
        mode="formal", runtime_id="ubuntu-lab", udid="PHONE",
        target_device_ip="192.168.1.2", capture_interface="eth0",
        capture_filter="host 192.168.1.2",
    )
    task = app.state.store.create_batch("formal-failed-check", [request], {
        "mi_desk_lamp_1s_advanced": 12,
    })[0]
    experiment, runtime = load_configuration(
        console_root / "experiment/mi_desk_lamp_1s_advanced.yaml",
        console_root / "runtime/ubuntu-lab.yaml",
    )
    runtime = runtime.model_copy(update={"output_root": console_root / "runs"})
    monkeypatch.setattr("iot_exp.worker._apply_request", lambda *_: (request, experiment, runtime))
    monkeypatch.setattr("iot_exp.worker.run_preflight", lambda *_: {
        "ok": False, "checks": [{"name": "dumpcap", "ok": False, "detail": "permission denied"}],
    })
    assert run_task(app.state.store.path, task["id"], console_root) == 2
    assert app.state.store.get(task["id"])["status"] == "failed"
    assert any("permission denied" in log["message"] for log in app.state.store.logs(task["id"]))


def test_worker_parent_watchdog_uses_read_only_process_check(console_root, monkeypatch):
    app = create_app(console_root)
    request = event_request(
        [{"event_type": "set_focus_mode", "target": True}],
    ).model_copy(update={"idle_min_seconds": 1.1, "idle_max_seconds": 1.1})
    task = app.state.store.create_batch("parent-watchdog-check", [request], {
        "mi_desk_lamp_1s_advanced": 12,
    })[0]
    app.state.store.update(task["id"], appium_port=4723, system_port=8200)
    probes = []
    monkeypatch.setattr(
        "iot_exp.worker.process_is_running", lambda pid, token: probes.append((pid, token)) or True,
    )
    assert run_task(app.state.store.path, task["id"], console_root, parent_pid=os.getpid()) == 0
    assert probes and all(pid == os.getpid() for pid, _ in probes)
