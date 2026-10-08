"""Owned UDP-only Windows Loopback capture probe; never an IoT experiment."""
from __future__ import annotations

import ctypes
import json
import os
import signal
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

from iot_exp.backends.capture import DumpcapCaptureBackend
from iot_exp.process_cleanup import terminate_owned_tree
from iot_exp.process_identity import process_is_running, process_start_token
from iot_exp.resources import ResourceLease


def main() -> int:
    root = Path(sys.argv[1]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    diagnostic = "--signal-constant-diagnostic" in sys.argv
    if diagnostic:
        # Probe only: reach the OS signal path beyond the product's wrong module constant.
        subprocess.CTRL_BREAK_EVENT = signal.CTRL_BREAK_EVENT
    dumpcap = Path(r"C:\Program Files\Wireshark\dumpcap.exe")
    tshark = dumpcap.with_name("tshark.exe")
    interface = r"\Device\NPF_Loopback"
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind(("127.0.0.1", 0))
    sender.bind(("127.0.0.1", 0))
    receiver.settimeout(2)
    destination, source = receiver.getsockname()[1], sender.getsockname()[1]
    bpf = f"ip and udp and src host 127.0.0.1 and dst host 127.0.0.1 and src port {source} and dst port {destination}"
    marker = "owned-loopback-" + uuid.uuid4().hex
    ctypes.windll.kernel32.GetConsoleWindow.restype = ctypes.c_void_p
    result = {"purpose": "local-loopback-only-capture-drain", "python_pid": os.getpid(),
              "python_console_handle": ctypes.windll.kernel32.GetConsoleWindow(),
              "interface": interface, "filter": bpf, "source_port": source,
              "destination_port": destination, "marker": marker, "hardware_used": False,
              "iot_traffic_captured": False, "normal_stop_ok": False, "sent": 0, "received": 0}
    result["signal_constant_diagnostic"] = diagnostic
    ledger = []
    lease = ResourceLease(root / "output", ["capture:" + interface.casefold()], "owned-loopback-probe",
                          lock_root=root / "locks")
    capture_path = root / "output" / "self-loopback.pcapng"
    process = None
    def record(child):
        nonlocal process
        process = child
        token = process_start_token(child.pid)
        ledger.append({"role": "capture", "pid": child.pid, "token": token, "isolated_group": True})
        (root / "ledger.json").write_text(json.dumps(ledger, indent=2), encoding="utf-8")
        lease.track_processes(ledger)
        result.update(capture_pid=child.pid, capture_start_token=token)
    capture = DumpcapCaptureBackend(str(dumpcap), interface, bpf, process_callback=record)
    try:
        lease.__enter__()
        capture.start(capture_path)
        time.sleep(0.5)
        for index in range(24):
            payload = f"{marker}:{index:02d}".encode("ascii")
            sender.sendto(payload, ("127.0.0.1", destination))
            result["sent"] += 1
            received, peer = receiver.recvfrom(4096)
            if received != payload or peer != ("127.0.0.1", source):
                raise RuntimeError("owned UDP receipt mismatch")
            result["received"] += 1
            time.sleep(0.02)
        time.sleep(0.5)
        started = time.monotonic()
        try:
            stopped = capture.stop()
            result["normal_stop_result"] = stopped.model_dump(mode="json")
            result["normal_stop_ok"] = stopped.return_code == 0
        except (RuntimeError, OSError) as error:
            result["normal_stop_error"] = str(error)
            result["normal_stop_cause"] = repr(error.__cause__)
            result["normal_stop_winerror"] = getattr(error.__cause__, "winerror", None)
            result["normal_stop_errno"] = getattr(error.__cause__, "errno", None)
        result["normal_stop_elapsed_seconds"] = time.monotonic() - started
    except (RuntimeError, OSError, ValueError, AttributeError) as error:
        result["probe_error"] = repr(error)
    finally:
        receiver.close()
        sender.close()
        if process and process_is_running(process.pid, result.get("capture_start_token")):
            result["forced_test_cleanup"] = True
            result["capture_complete"] = False
            terminate_owned_tree(process.pid, result["capture_start_token"])
            process.wait(timeout=5)
            result["forced_cleanup_return_code"] = process.returncode
        elif process:
            result["forced_test_cleanup"] = False
            result["capture_complete"] = result["normal_stop_ok"]
        lease.release()
        result["remaining_capture_pid"] = bool(process and process_is_running(process.pid, result.get("capture_start_token")))
        result["remaining_locks"] = [str(item) for folder in (root / "locks", root / "output/locks") for item in folder.glob("*.lock")]
        result["pcap_bytes"] = capture_path.stat().st_size if capture_path.exists() else 0
        if capture_path.is_file() and tshark.is_file():
            read = subprocess.run([str(tshark), "-n", "-r", str(capture_path), "-T", "fields",
                                   "-e", "ip.src", "-e", "ip.dst", "-e", "udp.srcport", "-e", "udp.dstport", "-e", "udp.payload"],
                                  capture_output=True, text=True, timeout=15, check=False)
            (root / "packet-fields.tsv").write_text(read.stdout, encoding="utf-8")
            result["pcap_read_return_code"] = read.returncode
            result["pcap_read_diagnostic"] = read.stderr[-2000:]
            rows = [line.split("\t") for line in read.stdout.splitlines() if line]
            result["packet_count"] = len(rows)
            result["only_owned_loopback_packets"] = bool(rows) and all(
                len(row) == 5 and row[:4] == ["127.0.0.1", "127.0.0.1", str(source), str(destination)]
                and bytes.fromhex(row[4].replace(":", "")).startswith(marker.encode("ascii")) for row in rows)
        (root / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result.get(key) for key in ("normal_stop_ok", "python_console_handle", "normal_stop_error", "normal_stop_cause", "normal_stop_winerror", "normal_stop_elapsed_seconds", "packet_count", "only_owned_loopback_packets", "forced_test_cleanup", "remaining_capture_pid", "remaining_locks")}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
