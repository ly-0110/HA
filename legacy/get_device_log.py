"""Export one Home Assistant entity's state history as JSON on the HA computer.

The access token is read from HA_TOKEN and is never written to the output.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

DEFAULT_ENTITY_ID = "media_player.xiaomi_cn_636575596_lx04"


def utc_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("start and end times must include a timezone")
    return parsed.astimezone(timezone.utc)


def normalize_history(payload: object, entity_id: str) -> list[dict]:
    if not isinstance(payload, list):
        raise TypeError("unexpected HA history response: expected a list")
    events = []
    for group in payload:
        if not isinstance(group, list):
            raise TypeError("unexpected HA history response: expected a list of state lists")
        for state in group:
            if not isinstance(state, dict):
                raise TypeError("unexpected HA history state")
            state_entity_id = state.get("entity_id", entity_id)
            if state_entity_id != entity_id:
                continue
            changed = state.get("last_changed")
            if not isinstance(changed, str) or not isinstance(state.get("state"), str):
                raise TypeError("HA state is missing last_changed or state")
            events.append({
                "entity_id": entity_id,
                "last_changed": utc_time(changed).isoformat(),
                "state": state["state"],
                "attributes": state.get("attributes", {}),
            })
    return sorted(events, key=lambda event: event["last_changed"])


def fetch_history(ha_url: str, token: str, entity_id: str, start: str, end: str) -> list[dict]:
    query = urlencode({"filter_entity_id": entity_id, "end_time": end})
    url = f"{ha_url.rstrip('/')}/api/history/period/{quote(start, safe='')}?{query}"
    request = Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    with urlopen(request, timeout=30) as response:
        return normalize_history(json.load(response), entity_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export HA entity history to JSON")
    parser.add_argument("--ha-url", default="http://localhost:8123")
    parser.add_argument("--entity-id", default=DEFAULT_ENTITY_ID)
    parser.add_argument("--start", required=True, help="ISO 8601 time with timezone")
    parser.add_argument("--end", required=True, help="ISO 8601 time with timezone")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    token = os.environ.get("HA_TOKEN")
    if not token:
        parser.error("HA_TOKEN environment variable is required")
    start = utc_time(args.start)
    end = utc_time(args.end)
    if end <= start:
        parser.error("--end must be after --start")
    events = fetch_history(args.ha_url, token, args.entity_id, start.isoformat(), end.isoformat())
    payload = {
        "schema": "iot_exp_ha_history_v1",
        "entity_id": args.entity_id,
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "events": events,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(f"Exported {len(events)} HA states to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
