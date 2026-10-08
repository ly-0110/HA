from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from .process_identity import process_is_running
from .task_models import TERMINAL_STATUSES, TaskRequest


class _ManagedConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class TaskStore:
    def __init__(self, path: Path, owner_instance: str | None = None):
        self.path = path
        self.owner_instance = owner_instance or os.environ.get("IOT_EXP_INSTANCE_ID")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            with sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True,
                                 factory=_ManagedConnection) as db:
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version > 2:
                    raise RuntimeError("数据库schema高于本程序，拒绝降级写入")
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "tasks" in tables:
                    columns = {row[1] for row in db.execute("PRAGMA table_info(tasks)")}
                    if "owner_instance" not in columns:
                        for row in db.execute("SELECT pid FROM tasks WHERE status NOT IN ('completed','failed','cancelled','interrupted')"):
                            if process_is_running(row[0]):
                                raise RuntimeError("旧工作进程仍在运行，不能升级数据库")
                        backup = self.path.with_name(self.path.name + f".before-v2-{time.time_ns()}.bak")
                        with sqlite3.connect(backup) as destination:
                            db.backup(destination)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, factory=_ManagedConnection)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _initialize(self) -> None:
        with self.connect() as db:
            db.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    client_request_id TEXT NOT NULL,
                    item_index INTEGER NOT NULL DEFAULT 0,
                    request_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    queue_reason TEXT,
                    error TEXT,
                    session_id TEXT,
                    session_root TEXT,
                    pid INTEGER,
                    appium_port INTEGER,
                    system_port INTEGER,
                    completed_events INTEGER NOT NULL DEFAULT 0,
                    planned_events INTEGER NOT NULL DEFAULT 0,
                    quality_json TEXT,
                    source TEXT NOT NULL DEFAULT 'console',
                    metadata_json TEXT,
                    created_at_ns INTEGER NOT NULL,
                    updated_at_ns INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS task_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    created_at_ns INTEGER NOT NULL,
                    level TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    message TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS task_logs_task_id ON task_logs(task_id, id);
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(tasks)")}
            if "item_index" not in columns:
                db.execute("ALTER TABLE tasks ADD COLUMN item_index INTEGER NOT NULL DEFAULT 0")
            if "source" not in columns:
                db.execute("ALTER TABLE tasks ADD COLUMN source TEXT NOT NULL DEFAULT 'console'")
            if "metadata_json" not in columns:
                db.execute("ALTER TABLE tasks ADD COLUMN metadata_json TEXT")
            for name, kind in (("owner_instance", "TEXT"), ("process_start_token", "TEXT"), ("stop_requested_at_ns", "INTEGER"), ("owned_processes_json", "TEXT"), ("parent_pid", "INTEGER"), ("parent_start_token", "TEXT")):
                if name not in columns:
                    db.execute(f"ALTER TABLE tasks ADD COLUMN {name} {kind}")
            db.execute("DROP INDEX IF EXISTS tasks_dedupe")
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS tasks_dedupe_v2 ON tasks(client_request_id, item_index)"
            )
            db.execute("PRAGMA user_version=2")

    def _row(self, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["request"] = json.loads(result.pop("request_json"))
        quality_json = result.pop("quality_json")
        result["quality"] = json.loads(quality_json) if quality_json else None
        metadata_json = result.pop("metadata_json", None)
        result["metadata"] = json.loads(metadata_json) if metadata_json else {}
        result["controllable"] = result["source"] == "console" and (
            self.owner_instance is None or result.get("owner_instance") == self.owner_instance)
        result["owned_processes"] = json.loads(result.pop("owned_processes_json", None) or "[]")
        return result

    def sync_discovered(self, tasks: list[dict[str, Any]]) -> None:
        """Index external evidence without taking ownership of its processes."""
        with self.connect() as db:
            managed_roots = {
                str(Path(row[0]).resolve())
                for row in db.execute("SELECT session_root FROM tasks WHERE source='console' AND session_root IS NOT NULL")
            }
            imported_roots = {
                str(Path(row["session_root"]).resolve()): self._row(row)
                for row in db.execute("SELECT * FROM tasks WHERE source!='console' AND session_root IS NOT NULL")
                if json.loads(row["metadata_json"] or "{}").get("imported")
            }
            for task in tasks:
                if task["session_root"] in managed_roots:
                    continue
                if task["session_root"] in imported_roots:
                    original = imported_roots[task["session_root"]]
                    task["id"] = original["id"]
                    task["metadata"] = {**original["metadata"], **task.get("metadata", {})}
                db.execute(
                    "INSERT INTO tasks(id,client_request_id,item_index,request_json,status,stage,error,"
                    "session_id,session_root,pid,appium_port,system_port,completed_events,planned_events,"
                    "quality_json,source,metadata_json,created_at_ns,updated_at_ns) "
                    "VALUES(?,?,0,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET request_json=excluded.request_json,status=excluded.status,"
                    "stage=excluded.stage,error=excluded.error,pid=excluded.pid,source=excluded.source,"
                    "session_id=excluded.session_id,session_root=excluded.session_root,"
                    "appium_port=excluded.appium_port,system_port=excluded.system_port,"
                    "completed_events=excluded.completed_events,planned_events=excluded.planned_events,"
                    "quality_json=excluded.quality_json,metadata_json=excluded.metadata_json,"
                    "updated_at_ns=excluded.updated_at_ns WHERE tasks.source != 'console'",
                    (
                        task["id"], task["id"], json.dumps(task["request"], ensure_ascii=False),
                        task["status"], task["stage"], task.get("error"), task.get("session_id"),
                        task["session_root"], task.get("pid"), task.get("appium_port"), task.get("system_port"),
                        task.get("completed_events", 0), task.get("planned_events", 0),
                        json.dumps(task.get("quality"), ensure_ascii=False) if task.get("quality") is not None else None,
                        task["source"], json.dumps(task.get("metadata", {}), ensure_ascii=False),
                        task["created_at_ns"], task["updated_at_ns"],
                    ),
                )
            discovered_ids = [task["id"] for task in tasks]
            missing_clause = f"AND id NOT IN ({','.join('?' for _ in discovered_ids)})" if discovered_ids else ""
            db.execute(
                "UPDATE tasks SET status='interrupted',stage='interrupted',pid=NULL,"
                "error='原实验产物目录已移动或不可读取，无法继续检测状态',updated_at_ns=? "
                "WHERE source != 'console' AND status='running' " + missing_clause,
                (time.time_ns(), *discovered_ids),
            )

    def create_batch(self, client_request_id: str, requests: list[TaskRequest], event_count: dict[str, int]) -> list[dict[str, Any]]:
        created: list[dict[str, Any]] = []
        now = time.time_ns()
        with self.connect() as db:
            for item_index, request in enumerate(requests):
                payload = request.model_dump_json()
                existing = db.execute(
                    "SELECT * FROM tasks WHERE client_request_id=? AND item_index=?",
                    (client_request_id, item_index),
                ).fetchone()
                if existing:
                    created.append(self._row(existing))
                    continue
                task_id = uuid.uuid4().hex
                planned = request.repetitions * (
                    len(request.events) if request.events is not None else event_count[request.template_id]
                )
                db.execute(
                    "INSERT INTO tasks(id,client_request_id,item_index,request_json,status,stage,planned_events,created_at_ns,updated_at_ns) VALUES(?,?,?,?,?,?,?,?,?)",
                    (task_id, client_request_id, item_index, payload, "queued", "queued", planned, now, now),
                )
                db.execute("UPDATE tasks SET owner_instance=? WHERE id=?", (self.owner_instance, task_id))
                created.append(self.get(task_id, db=db))
        return created

    def get(self, task_id: str, *, db: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        owned = db is None
        connection = db or self.connect()
        try:
            row = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            return self._row(row) if row else None
        finally:
            if owned:
                connection.close()

    def list(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM tasks ORDER BY created_at_ns DESC LIMIT ?", (limit,)).fetchall()
            return [self._row(row) for row in rows]

    def managed_sessions(self) -> dict[str, dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM tasks WHERE source='console' AND session_root IS NOT NULL").fetchall()
            return {str(Path(row["session_root"]).resolve()): self._row(row) for row in rows}

    def claim_queued(self, task_id: str, *, appium_port: int, system_port: int) -> bool:
        with self.connect() as db:
            cursor = db.execute(
                "UPDATE tasks SET status='preflight',stage='dispatching',queue_reason=NULL,"
                "appium_port=?,system_port=?,updated_at_ns=? WHERE id=? AND status='queued' AND source='console'",
                (appium_port, system_port, time.time_ns(), task_id),
            )
            return cursor.rowcount == 1

    def cancel_queued(self, task_id: str) -> bool:
        with self.connect() as db:
            cursor = db.execute(
                "UPDATE tasks SET status='cancelled',stage='cancelled',queue_reason=NULL,updated_at_ns=? "
                "WHERE id=? AND status='queued' AND source='console'",
                (time.time_ns(), task_id),
            )
            return cursor.rowcount == 1

    def queued(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM tasks WHERE status='queued' ORDER BY created_at_ns").fetchall()
            return [self._row(row) for row in rows]

    def active(self) -> list[dict[str, Any]]:
        placeholders = ",".join("?" for _ in TERMINAL_STATUSES)
        with self.connect() as db:
            rows = db.execute(
                f"SELECT * FROM tasks WHERE status NOT IN ({placeholders}) AND status != 'queued'",
                tuple(TERMINAL_STATUSES),
            ).fetchall()
            return [self._row(row) for row in rows]

    def update(self, task_id: str, **fields: Any) -> None:
        allowed = {
            "status", "stage", "queue_reason", "error", "session_id", "session_root", "pid",
            "appium_port", "system_port", "completed_events", "quality_json",
            "owner_instance", "process_start_token", "stop_requested_at_ns",
            "owned_processes_json",
            "parent_pid", "parent_start_token",
        }
        values = {key: value for key, value in fields.items() if key in allowed}
        if "quality_json" in values and not isinstance(values["quality_json"], str):
            values["quality_json"] = json.dumps(values["quality_json"], ensure_ascii=False)
        if "owned_processes_json" in values and not isinstance(values["owned_processes_json"], str):
            values["owned_processes_json"] = json.dumps(values["owned_processes_json"])
        values["updated_at_ns"] = time.time_ns()
        assignments = ",".join(f"{key}=?" for key in values)
        with self.connect() as db:
            db.execute(f"UPDATE tasks SET {assignments} WHERE id=?", (*values.values(), task_id))

    def add_log(self, task_id: str, level: str, stage: str, message: str) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO task_logs(task_id,created_at_ns,level,stage,message) VALUES(?,?,?,?,?)",
                (task_id, time.time_ns(), level, stage, message),
            )

    def logs(self, task_id: str, after: int = 0, limit: int = 1000) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM task_logs WHERE task_id=? AND id>? ORDER BY id LIMIT ?",
                (task_id, after, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def interrupt_unfinished(self) -> list[dict]:
        interrupted = []
        with self.connect() as db:
            for row in db.execute("SELECT * FROM tasks WHERE source='console' AND status NOT IN ('completed','failed','cancelled','interrupted')").fetchall():
                if process_is_running(row["pid"], row["process_start_token"]) or any(
                    process_is_running(child.get("pid"), child.get("token"))
                    for child in json.loads(row["owned_processes_json"] or "[]")
                ):
                    continue
                db.execute(
                "UPDATE tasks SET status='interrupted',stage='interrupted',error='控制台重启，任务未自动恢复',updated_at_ns=? "
                "WHERE id=?", (time.time_ns(), row["id"]),
                )
                interrupted.append(self._row(row))
        return interrupted

    def adopt_cleanup_tasks(self) -> list[dict]:
        """Recover cleanup ownership only after its recorded worker or sidecar died."""
        with self.connect() as db:
            rows = db.execute("SELECT * FROM tasks WHERE source='console'").fetchall()
            for row in rows:
                task = self._row(row)
                root_live = process_is_running(task.get("pid"), task.get("process_start_token"))
                children_live = any(
                    child.get("role") in {"appium", "capture"}
                    and process_is_running(child.get("pid"), child.get("token"))
                    for child in task["owned_processes"]
                )
                if not root_live and not children_live:
                    continue
                parent_dead = bool(task.get("parent_start_token")) and not process_is_running(
                    task.get("parent_pid"), task.get("parent_start_token"),
                )
                if root_live and not parent_dead:
                    continue
                if task["controllable"] and task["status"] == "stopping" and task["stage"] == "cleanup":
                    continue
                metadata = dict(task.get("metadata", {}))
                if not task["controllable"]:
                    metadata.setdefault("recovered_owner_instance", task.get("owner_instance"))
                db.execute(
                    "UPDATE tasks SET owner_instance=?,metadata_json=?,status='stopping',stage='cleanup',"
                    "stop_requested_at_ns=?,error=?,updated_at_ns=? WHERE id=?",
                    (self.owner_instance, json.dumps(metadata, ensure_ascii=False),
                     task.get("stop_requested_at_ns") or time.time_ns(),
                     "原后台或工作进程已退出，正在核对并清理所属进程；不会重新运行实验",
                     time.time_ns(), task["id"]),
                )
            current = db.execute("SELECT * FROM tasks WHERE source='console' AND stage='cleanup'").fetchall()
            return [task for row in current if (task := self._row(row))["controllable"]]

    def owned_live_workers(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM tasks WHERE source='console'").fetchall()
            return [task for row in rows if (task := self._row(row))["controllable"]
                    and process_is_running(task.get("pid"), task.get("process_start_token"))]

    def live_console_processes(self) -> bool:
        with self.connect() as db:
            for row in db.execute("SELECT pid,process_start_token,owned_processes_json FROM tasks WHERE source='console'"):
                if process_is_running(row["pid"], row["process_start_token"]):
                    return True
                if any(process_is_running(child.get("pid"), child.get("token"))
                       for child in json.loads(row["owned_processes_json"] or "[]")):
                    return True
        return False

    def owned_live_children(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM tasks WHERE source='console'").fetchall()
        result = []
        for row in rows:
            task = self._row(row)
            if not task["controllable"]:
                continue
            for child in task["owned_processes"]:
                if child.get("role") in {"appium", "capture"} and process_is_running(child.get("pid"), child.get("token")):
                    result.append({**child, "task_id": task["id"]})
        return result
