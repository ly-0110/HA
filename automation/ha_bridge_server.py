"""Receive an HA history export over the experiment hotspot after a session ends."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from iot_exp.ha_reconcile import export_window, reconcile
from iot_exp.pcap_review import review_pcap


def _load_key(path: Path) -> bytes:
    if path.exists():
        return path.read_text(encoding="ascii").strip().encode("ascii")
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_hex(32)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="ascii") as handle:
        handle.write(key + "\n")
    return key.encode("ascii")


def _signature(key: bytes, method: str, path: str, stamp: str, body: bytes) -> str:
    message = f"{method}\n{path}\n{stamp}\n{hashlib.sha256(body).hexdigest()}".encode("ascii")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def _write_private_json(path: Path, payload: dict) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def make_handler(session_root: Path, key: bytes):
    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status: int, payload: dict) -> None:
            body = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self, body: bytes) -> bool:
            stamp = self.headers.get("X-Bridge-Timestamp", "")
            signature = self.headers.get("X-Bridge-Signature", "")
            try:
                recent = abs(time.time() - int(stamp)) <= 60
            except ValueError:
                recent = False
            return recent and hmac.compare_digest(
                signature, _signature(key, self.command, self.path, stamp, body),
            )

        def do_GET(self) -> None:
            if self.path == "/clock":
                received = time.time_ns()
                self._reply(200, {"received_ns": received, "sent_ns": time.time_ns()})
                return
            if self.path != "/task" or not self._authorized(b""):
                self._reply(403, {"error": "unauthorized"})
                return
            try:
                window = export_window(session_root)
            except (FileNotFoundError, ValueError):
                self._reply(200, {"ready": False})
                return
            self._reply(200, {"ready": True, **window})

        def do_POST(self) -> None:
            if self.path != "/history":
                self._reply(404, {"error": "unknown endpoint"})
                return
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > 16_000_000:
                self._reply(413, {"error": "invalid upload size"})
                return
            body = self.rfile.read(size)
            if not self._authorized(body):
                self._reply(403, {"error": "unauthorized"})
                return
            try:
                window = export_window(session_root)
                payload = json.loads(body)
                history = payload["history"]
                clock = payload["clock"]
                if (
                    history.get("schema") != "iot_exp_ha_history_v1"
                    or history.get("entity_id") != window["entity_id"]
                    or history.get("start_time") != window["start"]
                    or history.get("end_time") != window["end"]
                    or not isinstance(history.get("events"), list)
                ):
                    raise ValueError("history metadata does not match the completed session")
                if any(
                    not isinstance(item, dict)
                    or item.get("entity_id") != window["entity_id"]
                    or not isinstance(item.get("state"), str)
                    or not isinstance(item.get("last_changed"), str)
                    for item in history["events"]
                ):
                    raise ValueError("history contains invalid or foreign states")
                offset = float(clock["capture_minus_ha_ms"])
                uncertainty = float(clock["uncertainty_at_most_ms"])
                if not (uncertainty >= 0 and abs(offset) + uncertainty <= 500):
                    raise ValueError("clock difference exceeds the 500 ms threshold")
                history_path = session_root / "ha_history.json"
                _write_private_json(history_path, history)
                _write_private_json(session_root / "ha_clock_probe.json", clock)
                if not (session_root / "pcap_review.json").exists():
                    review_pcap(session_root)
                report = reconcile(session_root, history_path, offset, uncertainty)
                self._reply(200, {
                    "accepted": True,
                    "session_id": window["session_id"],
                    "events": report["action_count"],
                    "candidate_gold_count": report["candidate_gold_count"],
                    "manual_review_required": report["manual_review_required"],
                })
            except (KeyError, TypeError, ValueError, FileNotFoundError, FileExistsError, RuntimeError) as exc:
                self._reply(400, {"error": str(exc)})

        def log_message(self, format: str, *args: object) -> None:
            print("HA bridge:", format % args, flush=True)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session_root", type=Path)
    parser.add_argument("--host", default="10.42.0.1")
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--key-file", type=Path, default=Path("runs/ha_bridge.key"))
    args = parser.parse_args()
    key = _load_key(args.key_file)
    with HTTPServer((args.host, args.port), make_handler(args.session_root, key)) as server:
        print(f"HA bridge listening on {args.host}:{args.port}; pairing key: {args.key_file}", flush=True)
        try:
            server.serve_forever(poll_interval=0.5)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
