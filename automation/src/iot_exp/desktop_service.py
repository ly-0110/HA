"""Authenticated desktop sidecar. Bootstrap credentials exist only on the parent pipe."""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import threading
from pathlib import Path

import uvicorn

from .paths import PathLayout
from .process_identity import process_is_running, process_start_token
from .runtime_bundle import load_manifest, private_environment
from .web import create_app


def main() -> int:
    bootstrap = json.loads(sys.stdin.readline())
    token, instance = bootstrap["token"], bootstrap["instance_id"]
    if not isinstance(token, str) or len(token) < 40 or not isinstance(instance, str):
        raise ValueError("invalid desktop bootstrap")
    parent_pid = int(bootstrap["parent_pid"])
    if parent_pid != os.getppid():
        raise ValueError("desktop parent identity mismatch")
    parent_identity = process_start_token(parent_pid)
    paths = bootstrap["paths"]
    layout = PathLayout.workspace(
        Path(paths["resources"]), Path(paths["workspace"]), Path(paths["app_state"]),
        runtime=Path(paths["runtime"]) if paths.get("runtime") else None,
        lock_root=Path(paths["locks"]),
        discovery_roots=tuple(Path(item) for item in paths.get("discovery_roots", [])),
    )
    layout.initialize()
    os.environ.update(layout.child_environment())
    os.environ["IOT_EXP_INSTANCE_ID"] = instance
    if layout.runtime_root:
        manifest = load_manifest(layout.resources_root)
        os.environ.update(private_environment(layout.resources_root, layout.app_state_root, manifest))
    app = create_app(layout=layout, desktop_token=token, instance_id=instance)
    parent_closed = threading.Event()

    def watch_pipe() -> None:
        try:
            sys.stdin.read()
        finally:
            parent_closed.set()

    threading.Thread(target=watch_pipe, daemon=True).start()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))

        async def serve() -> None:
            serving = asyncio.create_task(server.serve(sockets=[listener]))
            while not server.started:
                if serving.done():
                    await serving
                    return
                await asyncio.sleep(0.01)
            print(json.dumps({"kind": "ready", "protocol_version": 1, "instance_id": instance,
                              "pid": os.getpid(), "port": port,
                              "parent_start_token": parent_identity,
                              "process_start_token": process_start_token(os.getpid())}), flush=True)
            while not serving.done():
                if parent_closed.is_set() or not process_is_running(parent_pid, parent_identity):
                    app.state.scheduler.begin_drain()
                    if app.state.scheduler.active_owned_count() == 0:
                        server.should_exit = True
                await asyncio.sleep(0.2)
            await serving

        asyncio.run(serve())
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, OSError) as error:
        print(f"桌面后台启动失败：{type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(2) from error
