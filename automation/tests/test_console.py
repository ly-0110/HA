import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from iot_exp.resources import ResourceBusyError, ResourceLease
from iot_exp.scheduler import TaskScheduler
from iot_exp.task_models import TaskRequest
from iot_exp.task_store import TaskStore
from iot_exp.web import default_runtime_id, template_payload
from iot_exp.worker import run_task

ROOT = Path(__file__).parents[1]


def test_default_runtime_matches_host_platform(tmp_path, monkeypatch):
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    (runtime_root / "windows-dev.yaml").touch()
    (runtime_root / "ubuntu-dev.yaml").touch()
    monkeypatch.setattr("iot_exp.web.platform.system", lambda: "Linux")
    assert default_runtime_id(tmp_path) == "ubuntu-dev"
    monkeypatch.setattr("iot_exp.web.platform.system", lambda: "Windows")
    assert default_runtime_id(tmp_path) == "windows-dev"


def request(**updates):
    values = {
        "template_id": "mi_desk_lamp_1s",
        "runtime_id": "windows-dev",
        "mode": "simulate",
        "repetitions": 1,
        "idle_min_seconds": 0,
        "idle_max_seconds": 0,
        "cooldown_seconds": 0,
    }
    values.update(updates)
    return TaskRequest.model_validate(values)


def test_task_store_deduplicates_same_submission(tmp_path):
    store = TaskStore(tmp_path / "console.sqlite3")
    first = store.create_batch("request-1234", [request(), request()], {"mi_desk_lamp_1s": 2})
    second = store.create_batch("request-1234", [request(), request()], {"mi_desk_lamp_1s": 2})
    assert [task["id"] for task in first] == [task["id"] for task in second]
    assert first[0]["id"] != first[1]["id"]
    assert first[0]["planned_events"] == 2


def test_formal_task_requires_all_capture_boundaries():
    with pytest.raises(ValidationError, match="target IP"):
        request(mode="formal", udid="PHONE", capture_interface="1")


def test_scheduler_allows_simulations_but_blocks_same_real_device():
    simulated = {"request": request().model_dump()}
    assert TaskScheduler._conflict(simulated, [simulated]) is None
    real = {"request": request(mode="device", udid="PHONE").model_dump()}
    assert "手机" in TaskScheduler._conflict(real, [real])


def test_resource_lease_is_cross_process_safe(tmp_path):
    with (
        ResourceLease(tmp_path, ["phone:A"], "first"),
        pytest.raises(ResourceBusyError),
        ResourceLease(tmp_path, ["phone:A"], "second"),
    ):
        pass
    with ResourceLease(tmp_path, ["phone:A"], "third"):
        pass


def test_simulated_worker_creates_a_complete_session(tmp_path):
    (tmp_path / "experiment").mkdir()
    (tmp_path / "runtime").mkdir()
    shutil.copy(ROOT / "experiment" / "mi_desk_lamp_1s.yaml", tmp_path / "experiment")
    shutil.copy(ROOT / "runtime" / "windows-dev.yaml", tmp_path / "runtime")
    store = TaskStore(tmp_path / "runs" / "console.sqlite3")
    task = store.create_batch("request-worker", [request()], {"mi_desk_lamp_1s": 2})[0]
    store.update(task["id"], appium_port=4723, system_port=8200)
    assert run_task(store.path, task["id"], tmp_path) == 0
    finished = store.get(task["id"])
    assert finished["status"] == "completed"
    assert finished["completed_events"] == 2
    assert finished["quality"]["validation"]["ok"] is True
    assert len(store.logs(task["id"])) >= 4


def test_advanced_template_payload_keeps_targets_distinct():
    payload = template_payload(
        ROOT / "experiment" / "mi_desk_lamp_1s_advanced.yaml",
        ROOT / "runtime" / "windows-dev.yaml",
    )
    events = payload["events"]
    assert ("set_brightness", 30) in {(e["event_type"], e.get("target")) for e in events}
    assert ("set_brightness", 80) in {(e["event_type"], e.get("target")) for e in events}
    scene_targets = [e["target"] for e in events if e["event_type"] == "select_scene"]
    assert len(scene_targets) == 6 and len(set(scene_targets)) == 6
    focus_targets = sorted(e["target"] for e in events if e["event_type"] == "set_focus_mode")
    assert focus_targets == [False, True]


def test_simulated_worker_runs_advanced_template(tmp_path):
    (tmp_path / "experiment").mkdir()
    (tmp_path / "runtime").mkdir()
    shutil.copy(ROOT / "experiment" / "mi_desk_lamp_1s_advanced.yaml", tmp_path / "experiment")
    shutil.copy(ROOT / "runtime" / "windows-dev.yaml", tmp_path / "runtime")
    store = TaskStore(tmp_path / "runs" / "console.sqlite3")
    task = store.create_batch(
        "request-worker-advanced",
        [request(template_id="mi_desk_lamp_1s_advanced")],
        {"mi_desk_lamp_1s_advanced": 12},
    )[0]
    store.update(task["id"], appium_port=4723, system_port=8200)
    assert run_task(store.path, task["id"], tmp_path) == 0
    finished = store.get(task["id"])
    assert finished["status"] == "completed"
    assert finished["completed_events"] == 12
    assert finished["quality"]["validation"]["ok"] is True
    log_messages = [row["message"] for row in store.logs(task["id"])]
    assert any("set_brightness(30)" in message for message in log_messages)
