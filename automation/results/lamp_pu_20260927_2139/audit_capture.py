"""Describe saved captures and other-event window coverage without labeling packets."""
import json
import subprocess
from pathlib import Path

from iot_exp.pcap_review import PACKET_LINE, _address_matches

root = Path(__file__).resolve().parent
address = "10.42.0.250"
overview = {}
for folder in root.iterdir():
    if not folder.is_dir() or not (folder / "acquisition_report.json").exists():
        continue
    destination = folder / "capture_inventory.json"
    if destination.exists():
        report = json.loads(destination.read_text())
    else:
        rows = [json.loads(line) for line in (folder / "actions.jsonl").read_text().splitlines()]
        parsed = subprocess.run(
            ["tcpdump", "-tt", "-nn", "-r", str(folder / "traffic.pcapng"), "ip and host " + address],
            capture_output=True, text=True, check=True, timeout=30,
        )
        packets = []
        for line in parsed.stdout.splitlines():
            match = PACKET_LINE.match(line)
            if match:
                packets.append((float(match[1]), _address_matches(match[2], address), _address_matches(match[3], address)))
        windows = []
        for row in rows:
            if row["role"] != "u_source" or not row["dispatched"]:
                continue
            start = row["t_cmd_before_ns"] / 1e9
            end = row["finished_at_unix_ns"] / 1e9 + row["wait_after_seconds"]
            selected = [packet for packet in packets if start <= packet[0] <= end]
            windows.append({"event_id": row["event_id"], "event_type": row["event_type"],
                            "packets_in_operation_window": len(selected),
                            "outgoing": sum(packet[1] for packet in selected),
                            "incoming": sum(packet[2] for packet in selected)})
        report = {"ipv4_packet_count": len(packets), "pcap_bytes": (folder / "traffic.pcapng").stat().st_size,
                  "target_dispatched": sum(row["role"] == "target" and row["dispatched"] for row in rows),
                  "u_dispatched": sum(row["role"] == "u_source" and row["dispatched"] for row in rows),
                  "unique_event_ids": len({row["event_id"] for row in rows}),
                  "wait_range_seconds": [min(row["wait_after_seconds"] for row in rows),
                                         max(row["wait_after_seconds"] for row in rows)],
                  "u_operation_windows": windows,
                  "note": "Window counts describe temporal coverage of other-event operations; raw packets remain unlabeled."}
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    overview[folder.name] = {key: value for key, value in report.items() if key not in {"u_operation_windows", "note"}}
    overview[folder.name]["u_windows_with_bidirectional_traffic"] = sum(
        window["outgoing"] > 0 and window["incoming"] > 0 for window in report["u_operation_windows"]
    )
(root / "capture_inventory.json").write_text(json.dumps(overview, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(overview, ensure_ascii=False))
