import json
import subprocess

import yaml

from iot_exp import pcap_review


def test_review_pcap_counts_both_directions_for_event_window(tmp_path, monkeypatch):
    (tmp_path / "session.yaml").write_text(yaml.safe_dump({
        "session_id": "speaker_test", "experiment": {"network": {"target_device_ip": "10.42.0.196"}},
    }), encoding="utf-8")
    (tmp_path / "actions.jsonl").write_text(json.dumps({
        "event_id": "event_1", "event_type": "play_music",
        "t_cmd_before_ns": 100_000_000_000,
        "t_cmd_after_ns": 101_000_000_000,
        "t_app_ack_ns": 102_000_000_000,
    }) + "\n", encoding="utf-8")
    (tmp_path / "traffic.pcapng").write_bytes(b"test packet")
    output = (
        "99.000000 IP 10.42.0.196.443 > 1.2.3.4.111: data\n"
        "101.000000 IP 1.2.3.4.111 > 10.42.0.196.443: data\n"
        "110.000000 IP 10.42.0.196.443 > 1.2.3.4.111: late\n"
    )
    monkeypatch.setattr(pcap_review.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(
        args[0], 0, output, "",
    ))
    report = pcap_review.review_pcap(tmp_path)
    assert report["bidirectional_event_count"] == 1
    assert report["events"][0]["packets_from_device"] == 1
    assert report["events"][0]["packets_to_device"] == 1
    assert report["events"][0]["manual_review"] == "pending"
