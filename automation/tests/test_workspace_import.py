import hashlib
import json
import os
import sqlite3

import pytest

from iot_exp.paths import PathLayout
from iot_exp.task_models import TaskRequest
from iot_exp.task_store import TaskStore
from iot_exp.workspace import import_legacy_workspace, recover_imports


def test_import_uses_consistent_sqlite_backup_and_preserves_originals(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    old = TaskStore(source / "runs/console.sqlite3")
    task = old.create_batch("old-request-123", [TaskRequest(template_id="lamp")], {"lamp": 2})[0]
    old.update(task["id"], status="completed", stage="completed")
    (source / "experiment").mkdir()
    (source / "experiment/lamp.yaml").write_text("old", encoding="utf-8")
    evidence = source / "runs/sessions/old/traffic.pcapng"
    evidence.parent.mkdir(parents=True)
    evidence.write_bytes(b"immutable-test-fixture")
    layout = PathLayout.workspace(tmp_path / "resources", tmp_path / "new", tmp_path / "state")
    layout.initialize()
    current = TaskStore(layout.database, owner_instance="new")
    target = layout.config_root / "experiment/lamp.yaml"
    target.write_text("user changed", encoding="utf-8")
    report = import_legacy_workspace(source, layout, current)
    assert report["tasks_imported"] == 1
    assert target.read_text(encoding="utf-8") == "user changed"
    assert evidence.read_bytes() == b"immutable-test-fixture"
    assert current.list()[0]["controllable"] is False
    assert len(list((layout.state_root / "backups").glob("*.sqlite3"))) == 1
    assert json.loads((layout.state_root / "last-import.json").read_text())["source"] == str(source)
    assert import_legacy_workspace(source, layout, current)["tasks_imported"] == 0


def test_old_program_refuses_newer_database_schema(tmp_path):
    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=99")
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(RuntimeError, match="降级"):
        TaskStore(path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == checksum


def test_import_disk_failure_rolls_back_configs_and_database(tmp_path, monkeypatch):
    source = tmp_path / "source"
    old = TaskStore(source / "runs/console.sqlite3")
    old.create_batch("old-request-123", [TaskRequest(template_id="lamp")], {"lamp": 2})
    (source / "experiment").mkdir()
    (source / "experiment/lamp.yaml").write_text("original")
    layout = PathLayout.workspace(tmp_path / "resources", tmp_path / "new", tmp_path / "state")
    layout.initialize()
    current = TaskStore(layout.database, owner_instance="new")
    def fail(*args):
        raise sqlite3.OperationalError("database or disk is full")
    monkeypatch.setattr("iot_exp.workspace._import_tasks", fail)
    with pytest.raises(sqlite3.OperationalError, match="full"):
        import_legacy_workspace(source, layout, current)
    assert current.list() == []
    assert not (layout.config_root / "experiment/lamp.yaml").exists()
    assert (source / "experiment/lamp.yaml").read_text() == "original"
    assert list((layout.state_root / "backups").glob("*.sqlite3"))


def test_interrupted_import_recovers_published_file_without_deleting_user_edit(tmp_path):
    layout = PathLayout.workspace(tmp_path / "resources", tmp_path / "new", tmp_path / "state")
    layout.initialize()
    store = TaskStore(layout.database)
    stage = layout.state_root / "import-journals/failed"
    stage.mkdir(parents=True)
    original = stage / "0"
    original.write_text("imported")
    target = layout.config_root / "experiment/lamp.yaml"
    os.link(original, target)
    preserved = layout.config_root / "experiment/user.yaml"
    preserved.write_text("user changed")
    other = stage / "1"
    other.write_text("imported")
    journal = stage.parent / "failed.json"
    journal.write_text(json.dumps({"id":"failed", "report":{}, "files":[
        {"path":str(target), "staged":str(original), "sha256":hashlib.sha256(original.read_bytes()).hexdigest()},
        {"path":str(preserved), "staged":str(other), "sha256":hashlib.sha256(other.read_bytes()).hexdigest()},
    ]}))
    recover_imports(layout, store)
    assert not target.exists()
    assert preserved.read_text() == "user changed"
    assert not journal.exists()


def test_import_preserves_uncheckpointed_wal(tmp_path):
    source = tmp_path / "source"
    old = TaskStore(source / "runs/console.sqlite3")
    with old.connect() as writer:
        writer.execute("PRAGMA wal_autocheckpoint=0")
        task = old.create_batch("wal-request-123", [TaskRequest(template_id="lamp")], {"lamp": 2})[0]
        writer.execute("UPDATE tasks SET status='completed',stage='completed' WHERE id=?", (task["id"],))
        writer.commit()
        assert old.path.with_name(old.path.name + "-wal").stat().st_size > 0
        layout = PathLayout.workspace(tmp_path / "resources", tmp_path / "new", tmp_path / "state")
        layout.initialize()
        current = TaskStore(layout.database)
        import_legacy_workspace(source, layout, current)
        assert current.list()[0]["status"] == "completed"


def test_failed_schema_upgrade_rolls_back_ddl_and_retains_backup(tmp_path):
    path = tmp_path / "old.sqlite3"
    store = TaskStore(path)
    store.create_batch("duplicate-123", [TaskRequest(template_id="lamp")], {"lamp": 2})
    with store.connect() as db:
        db.execute("DROP INDEX tasks_dedupe_v2")
        db.execute("ALTER TABLE tasks DROP COLUMN owner_instance")
        db.execute("INSERT INTO tasks(id,client_request_id,item_index,request_json,status,stage,created_at_ns,updated_at_ns) SELECT 'duplicate',client_request_id,item_index,request_json,status,stage,created_at_ns,updated_at_ns FROM tasks")
        db.execute("PRAGMA user_version=0")
    with pytest.raises(sqlite3.IntegrityError):
        TaskStore(path)
    with sqlite3.connect(path) as db:
        assert 'owner_instance' not in {row[1] for row in db.execute('PRAGMA table_info(tasks)')}
        assert db.execute('PRAGMA user_version').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 2
    assert list(tmp_path.glob('old.sqlite3.before-v2-*.bak'))
