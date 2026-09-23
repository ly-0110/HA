from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import yaml

from .journal import ActionJournal

PACKET_LINE = re.compile(r"^(\d+\.\d+) IP (\S+) > (\S+):")


def _address_matches(endpoint: str, address: str) -> bool:
    return endpoint == address or endpoint.startswith(f"{address}.")


def review_pcap(session_root: Path, *, window_before_seconds: float = 2, window_after_seconds: float = 5) -> dict[str, Any]:
    snapshot = yaml.safe_load((session_root / "session.yaml").read_text(encoding="utf-8"))
    address = snapshot["experiment"]["network"]["target_device_ip"]
    if not address:
        raise ValueError("session has no target device IP")
    pcap = session_root / "traffic.pcapng"
    if not pcap.is_file() or pcap.stat().st_size == 0:
        raise FileNotFoundError(f"session PCAP is missing or empty: {pcap}")
    actions = ActionJournal(session_root / "actions.jsonl").read()
    rows = []
    for action in actions:
        before = action.get("t_cmd_before_ns")
        after = action.get("t_app_ack_ns") or action.get("t_cmd_after_ns")
        valid = isinstance(before, int) and isinstance(after, int)
        rows.append({
            "event_id": action["event_id"],
            "event_type": action["event_type"],
            "window_start_unix_ns": before - round(window_before_seconds * 1_000_000_000) if valid else None,
            "window_end_unix_ns": after + round(window_after_seconds * 1_000_000_000) if valid else None,
            "packets_from_device": 0,
            "packets_to_device": 0,
            "manual_review": "pending",
        })
    command = ["tcpdump", "-tt", "-nn", "-r", str(pcap), "ip and host " + address]
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"tcpdump could not read session PCAP: {result.stderr.strip()}")
    packets_from = 0
    packets_to = 0
    for line in result.stdout.splitlines():
        match = PACKET_LINE.match(line)
        if not match:
            continue
        at_ns = round(float(match.group(1)) * 1_000_000_000)
        source, destination = match.group(2), match.group(3)
        from_device = _address_matches(source, address)
        to_device = _address_matches(destination, address)
        packets_from += from_device
        packets_to += to_device
        for row in rows:
            start = row["window_start_unix_ns"]
            end = row["window_end_unix_ns"]
            if start is not None and start <= at_ns <= end:
                row["packets_from_device"] += from_device
                row["packets_to_device"] += to_device
    for row in rows:
        row["has_bidirectional_packets"] = bool(row["packets_from_device"] and row["packets_to_device"])
    report = {
        "schema": "iot_exp_pcap_review_v1",
        "session_id": snapshot["session_id"],
        "target_device_ip": address,
        "pcap_sha256": hashlib.sha256(pcap.read_bytes()).hexdigest(),
        "packets_from_device_total": packets_from,
        "packets_to_device_total": packets_to,
        "event_count": len(rows),
        "bidirectional_event_count": sum(row["has_bidirectional_packets"] for row in rows),
        "events": rows,
    }
    output = session_root / "pcap_review.json"
    if output.exists():
        raise FileExistsError(f"PCAP review already exists: {output}")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report
