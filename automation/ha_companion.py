"""Run on the HA computer: wait for a finished session, export HA history, and send it to the capture computer.

Requires only Python's standard library. HA_TOKEN stays on this computer.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


def _request(base_url: str, path: str, key: bytes | None = None, payload: dict | None = None) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else b""
    method = "POST" if payload is not None else "GET"
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if key is not None:
        stamp = str(int(time.time()))
        message = f"{method}\n{path}\n{stamp}\n{hashlib.sha256(body).hexdigest()}".encode("ascii")
        headers["X-Bridge-Timestamp"] = stamp
        headers["X-Bridge-Signature"] = hmac.new(key, message, hashlib.sha256).hexdigest()
    request = Request(base_url.rstrip("/") + path, data=body if payload is not None else None, headers=headers)
    try:
        with urlopen(request, timeout=10) as response:
            return json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f"bridge returned HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')}") from exc


def measure_clock(base_url: str, samples: int = 20) -> dict:
    measurements = []
    for _ in range(samples):
        before_ns = time.time_ns()
        answer = _request(base_url, "/clock")
        after_ns = time.time_ns()
        receive_ns = int(answer["received_ns"])
        send_ns = int(answer["sent_ns"])
        delay_ns = (after_ns - before_ns) - (send_ns - receive_ns)
        offset_ns = ((receive_ns - before_ns) + (send_ns - after_ns)) // 2
        measurements.append((delay_ns, offset_ns))
        time.sleep(0.1)
    delay_ns, offset_ns = min(measurements)
    return {
        "capture_minus_ha_ms": round(offset_ns / 1_000_000, 3),
        "best_round_trip_ms": round(delay_ns / 1_000_000, 3),
        "uncertainty_at_most_ms": round(delay_ns / 2_000_000, 3),
        "samples": samples,
        "measured_at_ha_utc": datetime.now(timezone.utc).isoformat(),
    }


def _utc(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("HA timestamp has no timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def export_history(ha_url: str, token: str, task: dict) -> dict:
    entity_id = task["entity_id"]
    query = urlencode({"filter_entity_id": entity_id, "end_time": task["end"]})
    address = f"{ha_url.rstrip('/')}/api/history/period/{quote(task['start'], safe='')}?{query}"
    request = Request(address, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    with urlopen(request, timeout=30) as response:
        raw = json.load(response)
    if not isinstance(raw, list):
        raise TypeError("unexpected HA history response")
    events = []
    for group in raw:
        if not isinstance(group, list):
            raise TypeError("unexpected HA state group")
        for state in group:
            if not isinstance(state, dict):
                raise TypeError("unexpected HA state")
            if state.get("entity_id", entity_id) != entity_id:
                continue
            events.append({
                "entity_id": entity_id,
                "state": state["state"],
                "last_changed": _utc(state["last_changed"]),
                "attributes": state.get("attributes", {}),
            })
    events.sort(key=lambda event: event["last_changed"])
    return {
        "schema": "iot_exp_ha_history_v1",
        "entity_id": entity_id,
        "start_time": task["start"],
        "end_time": task["end"],
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "events": events,
    }


def _save_private(path: Path, payload: dict) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bridge-url", default="http://10.42.0.1:8767")
    parser.add_argument("--ha-url", default="http://localhost:8123")
    parser.add_argument("--key-file", type=Path, help="private pairing key copied from the capture computer")
    parser.add_argument("--output-dir", type=Path, default=Path("."))
    parser.add_argument("--poll-seconds", type=float, default=10)
    parser.add_argument("--timeout-seconds", type=float, default=7200)
    args = parser.parse_args()
    token = os.environ.get("HA_TOKEN")
    key = args.key_file.read_text(encoding="ascii").strip() if args.key_file else os.environ.get("IOT_BRIDGE_KEY")
    if not token or not key:
        parser.error("HA_TOKEN and either --key-file or IOT_BRIDGE_KEY are required on the HA computer")
    deadline = time.monotonic() + args.timeout_seconds
    task = None
    while time.monotonic() < deadline:
        try:
            task = _request(args.bridge_url, "/task", key.encode("ascii"))
        except OSError as exc:
            print(f"Waiting for capture computer: {exc}", flush=True)
        else:
            if task.get("ready"):
                break
        time.sleep(args.poll_seconds)
    if not task or not task.get("ready"):
        raise TimeoutError("no completed capture session was announced")
    clock = measure_clock(args.bridge_url)
    if abs(clock["capture_minus_ha_ms"]) + clock["uncertainty_at_most_ms"] > 500:
        raise RuntimeError(f"clock difference exceeds 500 ms: {clock}")
    history = export_history(args.ha_url, token, task)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    local = args.output_dir / f"ha_history_{task['session_id']}.json"
    _save_private(local, history)
    print(f"Saved {len(history['events'])} HA states to {local}; clock: {clock}", flush=True)
    answer = _request(args.bridge_url, "/history", key.encode("ascii"), {"history": history, "clock": clock})
    print(json.dumps(answer, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
