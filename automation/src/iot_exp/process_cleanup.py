from __future__ import annotations

import os
import signal

import psutil

from .process_identity import process_is_running, process_start_token


def stop_orphaned_child(child: dict) -> None:
    pid, token = child.get("pid"), child.get("token")
    if not token or not process_is_running(pid, token):
        return
    if child.get("role") == "appium":
        terminate_owned_tree(int(pid), token)
    elif child.get("role") == "capture":
        if os.name == "nt" and child.get("isolated_group") is not True:
            raise RuntimeError("无法确认抓包进程的独立控制组，保留进程并等待显式强制终止")
        os.kill(int(pid), signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)


def terminate_owned_tree(pid: int, token: str | None) -> None:
    """Terminate a verified owned tree, excluding shared ADB servers and their children."""
    if not token or not process_is_running(pid, token):
        raise RuntimeError("进程创建身份不匹配，拒绝终止")
    try:
        _terminate_verified_tree(pid, token)
    except psutil.NoSuchProcess:
        return
    except psutil.AccessDenied as error:
        raise RuntimeError("无法访问所属进程，保留进程并等待明确处理") from error


def _terminate_verified_tree(pid: int, token: str) -> None:
    parent = psutil.Process(pid)
    if parent.name().lower() in {"adb", "adb.exe"}:
        raise RuntimeError("拒绝终止共享ADB服务")
    descendants = parent.children(recursive=True)
    excluded = set()
    for process in descendants:
        try:
            if process.name().lower() in {"adb", "adb.exe"}:
                excluded.add(process.pid)
                excluded.update(child.pid for child in process.children(recursive=True))
        except psutil.NoSuchProcess:
            continue
    identities = [(process, process_start_token(process.pid)) for process in descendants if process.pid not in excluded]
    for process, identity in reversed(identities):
        if identity and process_is_running(process.pid, identity):
            try:
                process.kill()
            except psutil.NoSuchProcess:
                pass
    if process_is_running(pid, token):
        parent.kill()
