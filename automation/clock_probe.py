"""Measure the HA computer clock against the capture computer over the LAN.

Run ``serve`` on the capture computer and ``client`` on the HA computer.
The probe only reads clocks; it does not adjust either machine.
"""

from __future__ import annotations

import argparse
import json
import socket
import time


def serve(host: str, port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((host, port))
        listener.listen(8)
        print(f"Clock probe listening on {host}:{port}", flush=True)
        while True:
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(3)
                connection.recv(1)
                receive_ns = time.time_ns()
                send_ns = time.time_ns()
                connection.sendall(f"{receive_ns} {send_ns}\n".encode("ascii"))


def client(host: str, port: int, samples: int) -> None:
    measurements = []
    for _ in range(samples):
        with socket.create_connection((host, port), timeout=3) as connection:
            before_ns = time.time_ns()
            connection.sendall(b"?")
            response = b""
            while not response.endswith(b"\n"):
                block = connection.recv(128)
                if not block:
                    raise RuntimeError("clock probe closed before a complete reply")
                response += block
            after_ns = time.time_ns()
        receive_ns, send_ns = map(int, response.split())
        delay_ns = (after_ns - before_ns) - (send_ns - receive_ns)
        offset_ns = ((receive_ns - before_ns) + (send_ns - after_ns)) // 2
        measurements.append((delay_ns, offset_ns))
        time.sleep(0.1)
    delay_ns, offset_ns = min(measurements)
    print(json.dumps({
        "capture_minus_ha_ms": round(offset_ns / 1_000_000, 3),
        "best_round_trip_ms": round(delay_ns / 1_000_000, 3),
        "uncertainty_at_most_ms": round(delay_ns / 2_000_000, 3),
        "samples": samples,
    }, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    server = sub.add_parser("serve")
    server.add_argument("--host", default="0.0.0.0")
    server.add_argument("--port", type=int, default=8766)
    probe = sub.add_parser("client")
    probe.add_argument("--host", required=True)
    probe.add_argument("--port", type=int, default=8766)
    probe.add_argument("--samples", type=int, default=20)
    args = parser.parse_args()
    if args.command == "serve":
        try:
            serve(args.host, args.port)
        except KeyboardInterrupt:
            pass
    else:
        client(args.host, args.port, args.samples)


if __name__ == "__main__":
    main()
