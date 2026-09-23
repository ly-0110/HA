from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .journal import ActionJournal, JsonlJournal


def _iso_ns(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("HA timestamp must include a timezone")
    return int(parsed.timestamp() * 1_000_000_000)


def _ns_iso(value: int) -> str:
    return datetime.fromtimestamp(value / 1_000_000_000, timezone.utc).isoformat()


def export_window(session_root: Path, padding_seconds: int = 30) -> dict[str, str]:
    snapshot = yaml.safe_load((session_root / "session.yaml").read_text(encoding="utf-8"))
    journal = JsonlJournal(session_root / "run_journal.jsonl").read()
    starts = [row["at_unix_ns"] for row in journal if row.get("kind") == "session_started"]
    ends = [row["at_unix_ns"] for row in journal if row.get("kind") == "session_finished"]
    if len(starts) != 1 or len(ends) != 1:
        raise ValueError("session must have exactly one start and finish marker")
    entity_id = snapshot["experiment"]["device"].get("entity_id")
    if not entity_id:
        raise ValueError("session has no HA entity_id")
    padding = padding_seconds * 1_000_000_000
    return {
        "session_id": snapshot["session_id"],
        "entity_id": entity_id,
        "start": _ns_iso(starts[0] - padding),
        "end": _ns_iso(ends[0] + padding),
    }


def reconcile(
    session_root: Path,
    history_path: Path,
    clock_offset_ms: float | None,
    clock_uncertainty_ms: float | None = None,
) -> dict[str, Any]:
    window = export_window(session_root)
    history_bytes = history_path.read_bytes()
    history = json.loads(history_bytes)
    if history.get("schema") != "iot_exp_ha_history_v1":
        raise ValueError("unsupported HA history schema")
    if history.get("entity_id") != window["entity_id"]:
        raise ValueError("HA history entity_id does not match the session")
    if _iso_ns(history["start_time"]) > _iso_ns(window["start"]):
        raise ValueError("HA history starts after the requested session window")
    if _iso_ns(history["end_time"]) < _iso_ns(window["end"]):
        raise ValueError("HA history ends before the requested session window")
    events = history.get("events")
    if not isinstance(events, list):
        raise TypeError("HA history events must be a list")
    offset_ns = round(clock_offset_ms * 1_000_000) if clock_offset_ms is not None else 0
    parsed_events = []
    for item in events:
        if not isinstance(item, dict) or item.get("entity_id") != window["entity_id"]:
            raise ValueError("HA history contains an invalid or foreign event")
        parsed_events.append({**item, "at_ns": _iso_ns(item["last_changed"]) + offset_ns})
    parsed_events.sort(key=lambda item: item["at_ns"])

    actions = ActionJournal(session_root / "actions.jsonl").read()
    quality = json.loads((session_root / "quality_report.json").read_text(encoding="utf-8"))
    clock_ok = (
        clock_offset_ms is not None
        and clock_uncertainty_ms is not None
        and clock_uncertainty_ms >= 0
        and abs(clock_offset_ms) + clock_uncertainty_ms <= 500
    )
    capture = quality.get("capture") or {}
    pcap = session_root / "traffic.pcapng"
    capture_ok = bool(quality.get("capture_ok") and capture.get("enabled") and pcap.is_file() and pcap.stat().st_size > 0)
    pcap_review_path = session_root / "pcap_review.json"
    pcap_reviews: dict[str, dict[str, Any]] = {}
    if capture_ok and pcap_review_path.is_file():
        pcap_review = json.loads(pcap_review_path.read_text(encoding="utf-8"))
        if (
            pcap_review.get("schema") == "iot_exp_pcap_review_v1"
            and pcap_review.get("session_id") == window["session_id"]
            and pcap_review.get("pcap_sha256") == hashlib.sha256(pcap.read_bytes()).hexdigest()
        ):
            pcap_reviews = {item["event_id"]: item for item in pcap_review.get("events", [])}
    used: set[int] = set()
    rows = []
    for position, action in enumerate(actions):
        before = action.get("t_cmd_before_ns")
        after = action.get("t_cmd_after_ns")
        expected = action.get("expected_state")
        reasons = []
        if action.get("result") not in {"app_ack_only", "confirmed"}:
            reasons.append("app_not_acknowledged")
        app_screenshot = session_root / "screenshots" / f"{action['event_id']}.png"
        if not app_screenshot.is_file():
            reasons.append("app_screenshot_missing")
        if expected not in {"playing", "paused"} or not isinstance(before, int) or not isinstance(after, int):
            reasons.append("invalid_action_window")
            candidates = []
        else:
            next_starts = [
                later["t_cmd_before_ns"] for later in actions[position + 1:]
                if isinstance(later.get("t_cmd_before_ns"), int)
            ]
            upper_bound = after + 30_000_000_000
            if next_starts:
                upper_bound = min(upper_bound, min(next_starts) - 500_000_000)
            candidates = [
                index for index, event in enumerate(parsed_events)
                if index not in used
                and event.get("state") == expected
                and before - 500_000_000 <= event["at_ns"] <= upper_bound
            ]
        match = None
        if len(candidates) == 1:
            index = candidates[0]
            match = parsed_events[index]
            used.add(index)
            previous_state = parsed_events[index - 1].get("state") if index > 0 else None
            if previous_state != action.get("state_before"):
                reasons.append("ha_previous_state_mismatch")
        else:
            reasons.append("ha_missing" if not candidates else "ha_ambiguous")
        if not clock_ok:
            reasons.append("clock_offset_unverified")
        if not capture_ok:
            reasons.append("capture_unverified")
        pcap_event = pcap_reviews.get(action["event_id"])
        if not pcap_event or not pcap_event.get("has_bidirectional_packets"):
            reasons.append("pcap_event_unverified")
        rows.append({
            "event_id": action["event_id"],
            "event_type": action["event_type"],
            "expected_state": expected,
            "ha_last_changed": match["last_changed"] if match else None,
            "ha_state": match["state"] if match else None,
            "app_screenshot": str(app_screenshot) if app_screenshot.is_file() else None,
            "pcap_packets_from_device": pcap_event.get("packets_from_device") if pcap_event else None,
            "pcap_packets_to_device": pcap_event.get("packets_to_device") if pcap_event else None,
            "automatic_match": not reasons,
            "candidate_gold": not reasons,
            "manual_review": "pending",
            "reasons": reasons,
        })
    report = {
        "schema": "iot_exp_ha_reconciliation_v1",
        "session_id": window["session_id"],
        "entity_id": window["entity_id"],
        "ha_history_sha256": hashlib.sha256(history_bytes).hexdigest(),
        "clock_offset_ms": clock_offset_ms,
        "clock_uncertainty_ms": clock_uncertainty_ms,
        "clock_ok": clock_ok,
        "capture_ok": capture_ok,
        "action_count": len(actions),
        "candidate_gold_count": sum(row["candidate_gold"] for row in rows),
        "manual_review_required": len(rows),
        "unmatched_ha_events": [
            {"last_changed": event["last_changed"], "state": event["state"]}
            for index, event in enumerate(parsed_events)
            if index not in used
        ],
        "events": rows,
    }
    output = session_root / "ha_reconciliation.json"
    if output.exists():
        raise FileExistsError(f"reconciliation already exists: {output}")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report
