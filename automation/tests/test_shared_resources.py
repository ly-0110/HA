import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from iot_exp.paths import PathLayout
from iot_exp.resources import ResourceBusyError, ResourceLease, capture_resource_key
from iot_exp.scheduler import TaskScheduler


def test_different_output_roots_share_hardware_lock(tmp_path):
    with (
        ResourceLease(tmp_path / "first", ["phone:A"], "first"),
        pytest.raises(ResourceBusyError),
        ResourceLease(tmp_path / "second", ["phone:A"], "second"),
    ):
        pass
    with ResourceLease(tmp_path / "second", ["phone:A"], "second"):
        pass


def test_partial_legacy_lock_is_not_deleted(tmp_path):
    name = hashlib.sha256(b"phone:A").hexdigest()[:24] + ".lock"
    path = tmp_path / "output/locks" / name
    path.parent.mkdir(parents=True)
    path.write_text("", encoding="utf-8")
    with pytest.raises(ResourceBusyError, match="无法确认"), ResourceLease(tmp_path / "output", ["phone:A"], "new"):
        pass
    assert path.exists() and path.read_text() == ""


def test_live_old_cli_blocks_the_new_lease(tmp_path):
    name = hashlib.sha256(b"phone:A").hexdigest()[:24] + ".lock"
    path = tmp_path / "output/locks" / name
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"pid": os.getpid(), "key": "phone:A", "owner": "old"}))
    with pytest.raises(ResourceBusyError), ResourceLease(tmp_path / "output", ["phone:A"], "new"):
        pass
    assert json.loads(path.read_text())["owner"] == "old"


def test_release_never_removes_a_different_owner(tmp_path):
    lease = ResourceLease(tmp_path, ["phone:A"], "first")
    lease.__enter__()
    path = lease.paths[0]
    data = json.loads(path.read_text())
    data["lease_id"] = "a-different-owner"
    path.write_text(json.dumps(data))
    lease.release()
    assert path.exists()
    path.unlink()


def test_simulation_does_not_create_shared_lock_directory(tmp_path):
    with ResourceLease(tmp_path / "output", [], "simulate") as lease:
        assert not Path(lease.directory).exists()


def test_port_allocation_considers_active_and_external_ports():
    assert TaskScheduler._free_slot([{"system_port": 8201}], {4723, 8202}) == 3


def test_numeric_capture_alias_uses_the_same_nic_lock(monkeypatch):
    name = r"\Device\NPF_{FIXTURE}" if os.name == "nt" else "eth0"
    monkeypatch.setattr("iot_exp.resources.subprocess.run", lambda *_a, **_kw: SimpleNamespace(returncode=0, stdout=f"1. {name} (Fixture NIC)\n"))
    assert capture_resource_key("1", "dumpcap") == capture_resource_key(name, "dumpcap")


def test_unknown_numeric_capture_interface_fails_conservatively(monkeypatch):
    monkeypatch.setattr("iot_exp.resources.subprocess.run", lambda *_a, **_kw: SimpleNamespace(returncode=0, stdout=""))
    with pytest.raises(ResourceBusyError, match="唯一"):
        capture_resource_key("1", "dumpcap")


def test_registered_old_output_blocks_other_output_and_partial_lock(tmp_path, monkeypatch):
    old = tmp_path / 'old-output'
    layout = PathLayout.workspace(tmp_path / 'resources', tmp_path / 'workspace', tmp_path / 'state', discovery_roots=(old,))
    monkeypatch.setenv('IOT_EXP_CONTEXT', layout.child_environment()['IOT_EXP_CONTEXT'])
    lock = old / 'locks' / (hashlib.sha256(b'phone:A').hexdigest()[:24]+'.lock')
    lock.parent.mkdir(parents=True)
    for content in (json.dumps({'pid':os.getpid(),'key':'phone:A'}), ''):
        lock.write_text(content)
        with pytest.raises(ResourceBusyError), ResourceLease(tmp_path / 'other-output', ['phone:A'], 'new'):
            pass
        assert lock.read_text() == content
