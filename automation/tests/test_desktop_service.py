import shutil
import time
from pathlib import Path

from fastapi.testclient import TestClient

from iot_exp.paths import PathLayout
from iot_exp.task_models import TaskRequest
from iot_exp.web import create_app


def desktop_app(tmp_path):
    resources = tmp_path / "resources"
    source = Path(__file__).parents[1]
    shutil.copytree(source / "experiment", resources / "experiment")
    shutil.copytree(source / "runtime", resources / "runtime")
    layout = PathLayout.workspace(resources, tmp_path / "workspace", tmp_path / "state")
    return create_app(layout=layout, desktop_token="a" * 43, instance_id="test-instance")


def test_desktop_authenticates_get_and_never_returns_credentials(tmp_path):
    app = desktop_app(tmp_path)
    client = TestClient(app)
    assert client.get("/api/v1/bootstrap").status_code == 403
    headers = {"x-control-token": "a" * 43, "origin": "app://workbench"}
    response = client.get("/api/v1/bootstrap", headers=headers)
    assert response.status_code == 200
    assert "control_token" not in response.json()
    assert client.get("/api/v1/health", headers=headers).json()["instance_id"] == "test-instance"
    assert client.get("/api/v1/tasks", headers={**headers, "origin": "https://evil.invalid"}).status_code == 403
    assert client.get("/api/v1/tasks", headers={**headers, "origin": "http://testserver"}).status_code == 403
    assert client.get("/api/v1/tasks", headers={**headers, "x-control-token": "old-token"}).status_code == 403


def test_desktop_draining_rejects_new_tasks(tmp_path):
    app = desktop_app(tmp_path)
    client = TestClient(app)
    headers = {"x-control-token": "a" * 43}
    assert client.post("/api/v1/desktop/drain", headers=headers).status_code == 200
    response = client.post("/api/v1/tasks", headers=headers, json={
        "client_request_id": "draining-test-123", "tasks": [{"template_id": "mi_desk_lamp_1s"}],
    })
    assert response.status_code == 409


def test_force_endpoint_uses_first_stop_time_after_cleanup_progress(tmp_path, monkeypatch):
    app = desktop_app(tmp_path)
    store = app.state.store
    task = store.create_batch("force-grace-test-123", [TaskRequest(template_id="mi_desk_lamp_1s")], {"mi_desk_lamp_1s":2})[0]
    store.update(task["id"], status="stopping", stage="cleanup", stop_requested_at_ns=time.time_ns()-61_000_000_000)
    called = []
    monkeypatch.setattr(app.state.scheduler, "force_stop", lambda identifier: called.append(identifier))
    client = TestClient(app)
    headers = {"x-control-token":"a"*43, "origin":"app://workbench"}
    response = client.post(f"/api/v1/tasks/{task['id']}/force-stop", headers=headers)
    assert response.status_code == 200
    assert called == [task["id"]]
    store.update(task["id"], stop_requested_at_ns=time.time_ns())
    assert client.post(f"/api/v1/tasks/{task['id']}/force-stop", headers=headers).status_code == 409


def test_detected_sdk_is_suggested_without_silent_selection_and_can_be_reused(tmp_path, monkeypatch):
    import json
    import os

    from iot_exp.backends.system import installed_android_sdks

    sdk = tmp_path / 'existing-sdk'
    (sdk / 'platform-tools').mkdir(parents=True)
    (sdk / 'platform-tools' / ('adb.exe' if os.name == 'nt' else 'adb')).write_bytes(b'fixture')
    monkeypatch.setenv('ANDROID_SDK_ROOT', str(sdk))
    assert str(sdk.resolve()) in [item['path'] for item in installed_android_sdks()]
    app = desktop_app(tmp_path)
    tools = tmp_path / 'state/tools.json'
    assert not tools.exists()
    headers = {'x-control-token':'a'*43, 'origin':'app://workbench'}
    client = TestClient(app)
    response = client.post('/api/v1/desktop/tools', headers=headers,
                           json={'kind':'sdk','path':str(sdk),'detected':True})
    assert response.status_code == 200
    assert json.loads(tools.read_text())['sdk'] == str(sdk.resolve())
    other = tmp_path / 'other-sdk'
    (other / 'platform-tools').mkdir(parents=True)
    (other / 'platform-tools' / ('adb.exe' if os.name == 'nt' else 'adb')).write_bytes(b'fixture')
    assert client.post('/api/v1/desktop/tools', headers=headers,
                       json={'kind':'sdk','path':str(other),'detected':True}).status_code == 400
    assert json.loads(tools.read_text())['sdk'] == str(sdk.resolve())
