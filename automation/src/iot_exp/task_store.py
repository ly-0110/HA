from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from .task_models import TERMINAL_STATUSES, TaskRequest


class TaskStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _initialize(self) -> None:
        with self.connect() as db:
            db.executescript("""
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
            db.execute("DROP INDEX IF EXISTS tasks_dedupe")
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS tasks_dedupe_v2 ON tasks(client_request_id, item_index)"
            )

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["request"] = json.loads(result.pop("request_json"))
        quality_json = result.pop("quality_json")
        result["quality"] = json.loads(quality_json) if quality_json else None
        return result

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
                planned = request.repetitions * event_count[request.template_id]
                db.execute(
                    "INSERT INTO tasks(id,client_request_id,item_index,request_json,status,stage,planned_events,created_at_ns,updated_at_ns) VALUES(?,?,?,?,?,?,?,?,?)",
                    (task_id, client_request_id, item_index, payload, "queued", "queued", planned, now, now),
                )
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
        }
        values = {key: value for key, value in fields.items() if key in allowed}
        if "quality_json" in values and not isinstance(values["quality_json"], str):
            values["quality_json"] = json.dumps(values["quality_json"], ensure_ascii=False)
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

    def interrupt_unfinished(self) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE tasks SET status='interrupted',stage='interrupted',error='控制台重启，任务未自动恢复',updated_at_ns=? "
                "WHERE status NOT IN ('completed','failed','cancelled','interrupted')",
                (time.time_ns(),),
            )
