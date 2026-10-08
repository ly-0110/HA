from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import time
import uuid
from pathlib import Path

from .paths import PathLayout
from .process_identity import process_is_running
from .state_files import atomic_json
from .task_models import TERMINAL_STATUSES
from .task_store import TaskStore


def recover_imports(layout: PathLayout, store: TaskStore) -> None:
    """Roll back only files published by an uncommitted import, never user edits."""
    journals = layout.state_root / "import-journals"
    with store.connect() as db:
        db.execute("CREATE TABLE IF NOT EXISTS import_receipts(id TEXT PRIMARY KEY)")
        for journal in journals.glob("*.json"):
            value = json.loads(journal.read_text(encoding="utf-8"))
            committed = db.execute("SELECT 1 FROM import_receipts WHERE id=?", (value["id"],)).fetchone()
            if committed:
                atomic_json(layout.state_root / "last-import.json", value["report"])
            else:
                for entry in value["files"]:
                    target = Path(entry["path"]).resolve()
                    if not target.is_relative_to(layout.config_root.resolve()):
                        raise ValueError("导入恢复路径越界")
                    staged = Path(entry["staged"]).resolve()
                    if not staged.is_relative_to(journals.resolve()):
                        raise ValueError("导入暂存路径越界")
                    if target.is_file() and staged.is_file() and os.path.samefile(target, staged) and hashlib.sha256(target.read_bytes()).hexdigest() == entry["sha256"]:
                        target.unlink()
            for entry in value["files"]:
                staged = Path(entry["staged"]).resolve()
                if not staged.is_relative_to(journals.resolve()):
                    raise ValueError("导入暂存路径越界")
                staged.unlink(missing_ok=True)
                if staged.parent.is_dir() and not any(staged.parent.iterdir()):
                    staged.parent.rmdir()
            journal.unlink()


def import_legacy_workspace(source: Path, layout: PathLayout, store: TaskStore) -> dict:
    source = source.resolve()
    if "legacy" in {part.lower() for part in source.parts} or not source.is_dir():
        raise ValueError("旧研究数据目录不属于可导入工作区")
    if any(task.get("controllable") for task in store.active() + store.queued()):
        raise ValueError("当前工作区仍有任务，不能迁移")
    recover_imports(layout, store)
    report = {"source": str(source), "configs_imported": [], "configs_preserved": [], "tasks_imported": 0}
    original = source / "runs/console.sqlite3"
    snapshot = None
    if original.is_file():
        connection = sqlite3.connect(original.as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            if connection.execute("PRAGMA user_version").fetchone()[0] > 2:
                raise ValueError("旧数据库schema版本高于当前应用，拒绝降级读取")
            for row in connection.execute("SELECT * FROM tasks"):
                data = dict(row)
                if data.get("source", "console") == "console" and process_is_running(data.get("pid"), data.get("process_start_token")):
                    raise ValueError("原工作区进程仍在运行，请先在原入口完成停止")
            backups = layout.state_root / "backups"
            backups.mkdir(parents=True, exist_ok=True)
            snapshot = backups / f"import-{time.time_ns()}.sqlite3"
            with sqlite3.connect(snapshot) as target:
                connection.backup(target)
        finally:
            connection.close()
    pending = []
    for kind in ("experiment", "runtime"):
        destination = layout.config_root / kind
        destination.mkdir(parents=True, exist_ok=True)
        for path in (source / kind).glob("*.yaml"):
            target = destination / path.name
            if target.exists():
                report["configs_preserved"].append(str(target))
            else:
                pending.append((path, target))
                report["configs_imported"].append(str(target))
    identifier = uuid.uuid4().hex
    journal = layout.state_root / "import-journals" / (identifier + ".json")
    stage = layout.state_root / "import-journals" / identifier
    stage.mkdir(parents=True)
    try:
        files = []
        for index, (original_config, destination) in enumerate(pending):
            staged = stage / str(index)
            shutil.copyfile(original_config, staged)
            files.append({"path": str(destination), "staged": str(staged), "sha256": hashlib.sha256(staged.read_bytes()).hexdigest()})
        atomic_json(journal, {"id": identifier, "files": files, "report": report})
        with store.connect() as target:
            target.execute("BEGIN IMMEDIATE")
            for index, (_original_config, destination) in enumerate(pending):
                # Exclusive creation keeps a concurrent user edit intact.
                os.link(stage / str(index), destination)
            if snapshot:
                _import_tasks(snapshot, source, target, report)
            atomic_json(journal, {"id": identifier, "files": files, "report": report})
            target.execute("INSERT INTO import_receipts(id) VALUES(?)", (identifier,))
        recover_imports(layout, store)
    except BaseException:
        recover_imports(layout, store)
        raise
    finally:
        if stage.is_dir() and not journal.exists():
            for staged in stage.iterdir():
                staged.unlink()
            stage.rmdir()
    return report


def _import_tasks(snapshot: Path, source: Path, target: sqlite3.Connection, report: dict) -> None:
    source_key = hashlib.sha256(str(source).encode()).hexdigest()[:12]
    with sqlite3.connect(snapshot) as db:
            db.row_factory = sqlite3.Row
            for row in db.execute("SELECT * FROM tasks"):
                data = dict(row)
                identifier = f"import-{source_key}-{data['id']}"
                status = data["status"] if data["status"] in TERMINAL_STATUSES else "interrupted"
                cursor = target.execute(
                    "INSERT OR IGNORE INTO tasks(id,client_request_id,item_index,request_json,status,stage,error,session_id,session_root,completed_events,planned_events,quality_json,source,metadata_json,created_at_ns,updated_at_ns) VALUES(?,?,0,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (identifier, identifier, data["request_json"], status, status,
                     data.get("error") or ("导入时未发现有效结束证据" if status == "interrupted" else None),
                     data.get("session_id"), data.get("session_root"), data.get("completed_events", 0),
                     data.get("planned_events", 0), data.get("quality_json"), "cli",
                     json.dumps({"imported": True, "original_task_id": data["id"], "original_root": str(source)}),
                     data["created_at_ns"], data["updated_at_ns"]),
                )
                report["tasks_imported"] += cursor.rowcount
