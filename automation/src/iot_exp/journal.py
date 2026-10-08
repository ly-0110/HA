from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .models import ActionRecord


class JsonlJournal:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, value: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {self.path}:{line_number}: {exc}") from exc
            if not isinstance(row, dict):
                raise TypeError(f"JSONL row must be an object at {self.path}:{line_number}")
            rows.append(row)
        return rows


class ActionJournal(JsonlJournal):
    def append_action(self, record: ActionRecord) -> None:
        self.append(record.model_dump(mode="json"))


def find_open_events(journal: JsonlJournal) -> set[str]:
    """Find journaled event starts that do not have a corresponding finish."""
    open_events: set[str] = set()
    for row in journal.read():
        event_id = row.get("event_id")
        kind = row.get("kind")
        if not event_id:
            continue
        if kind == "event_started":
            open_events.add(event_id)
        elif kind == "event_finished":
            open_events.discard(event_id)
    return open_events
