from __future__ import annotations

import ipaddress
import json
import time
from pathlib import Path
from typing import Any

from .backends.system import (
    AdbClient,
    SystemCommandError,
    collect_system_checks,
    discover_android_sdk,
)
from .config import is_placeholder, redact_environment
from .models import ExperimentConfig, RuntimeConfig


def _parse_ipv4_addresses(text: str) -> list[ipaddress.IPv4Address]:
    addresses: list[ipaddress.IPv4Address] = []
    for token in text.replace("/", " ").split():
        try:
            value = ipaddress.ip_address(token)
        except ValueError:
            continue
        if isinstance(value, ipaddress.IPv4Address):
            addresses.append(value)
    return addresses


def run_preflight(experiment: ExperimentConfig, runtime: RuntimeConfig) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    checks.extend(check.__dict__ for check in collect_system_checks(
        adb=runtime.adb_executable,
        appium=runtime.appium_executable,
        dumpcap=runtime.dumpcap_executable,
        require_capture=runtime.capture_mode.value == "dumpcap",
        android_sdk_root=runtime.android_sdk_root,
    ))

    checks.append({
        "name": "phone_udid_configured",
        "ok": not is_placeholder(experiment.phone.udid),
        "detail": experiment.phone.udid,
    })

    if runtime.mode.value == "formal":
        checks.extend([
            {
                "name": "formal_target_device_ip",
                "ok": not is_placeholder(experiment.network.target_device_ip),
                "detail": experiment.network.target_device_ip or "not configured",
            },
            {
                "name": "formal_capture_interface",
                "ok": not is_placeholder(runtime.capture_interface),
                "detail": runtime.capture_interface or "not configured",
            },
            {
                "name": "formal_capture_filter",
                "ok": not is_placeholder(runtime.capture_filter),
                "detail": runtime.capture_filter or "not configured",
            },
        ])

    adb = AdbClient(runtime.adb_executable)
    device_online = False
    adb_error = None
    try:
        device_online = adb.is_online(experiment.phone.udid)
    except SystemCommandError as exc:
        adb_error = str(exc)
    checks.append({
        "name": "adb_device_online",
        "ok": device_online,
        "detail": adb_error or ("online" if device_online else "not online"),
    })

    network_detail: dict[str, Any] = {"addresses": [], "forbidden_cidrs": experiment.network.forbidden_cidrs}
    network_ok = True
    if device_online:
        try:
            addr_result = adb.shell("ip", "addr", "show", udid=experiment.phone.udid, timeout=10)
            addresses = _parse_ipv4_addresses(addr_result.stdout)
            network_detail["addresses"] = [str(address) for address in addresses]
            if addr_result.returncode != 0:
                raise SystemCommandError(
                    f"address query exited with {addr_result.returncode}: {addr_result.stderr.strip()}"
                )
            usable_addresses = [
                address
                for address in addresses
                if not (
                    address.is_loopback
                    or address.is_link_local
                    or address.is_multicast
                    or address.is_unspecified
                )
            ]
            network_detail["usable_addresses"] = [str(address) for address in usable_addresses]
            if not usable_addresses:
                raise SystemCommandError("address query returned no usable non-loopback IPv4 address")
            forbidden = [ipaddress.ip_network(cidr, strict=False) for cidr in experiment.network.forbidden_cidrs]
            conflicts = [
                str(address)
                for address in usable_addresses
                if any(address in net for net in forbidden)
            ]
            network_detail["conflicts"] = conflicts
            network_ok = not conflicts
        except Exception as exc:  # noqa: BLE001 - Android shell output varies by vendor.
            network_ok = False
            network_detail["error"] = str(exc)
    else:
        network_ok = False
    checks.append({"name": "phone_network_isolation", "ok": network_ok, "detail": network_detail})

    target_reachability = {"target_device_ip": experiment.network.target_device_ip, "reachable": None}
    if experiment.network.target_device_ip and device_online:
        result = adb.shell(
            "ping", "-c", "1", "-W", "1", experiment.network.target_device_ip,
            udid=experiment.phone.udid,
            timeout=5,
        )
        target_reachability["reachable"] = result.returncode == 0
    checks.append({
        "name": "target_device_unreachable",
        "ok": target_reachability["reachable"] is not True,
        "detail": target_reachability,
    })

    sdk_root = discover_android_sdk(runtime.adb_executable, runtime.android_sdk_root)
    report = {
        "checked_at_unix_ns": time.time_ns(),
        "environment": redact_environment(),
        "selected_phone": experiment.phone.model_dump(mode="json"),
        "app": {"package": experiment.app.package, "version": experiment.app.version},
        "automation_runtime": {
            "adb_executable": runtime.adb_executable,
            "android_sdk_root": str(sdk_root) if sdk_root is not None else None,
            "appium_url": runtime.appium_url,
            "uiautomator2_system_port": runtime.uiautomator2_system_port,
            "capture_mode": runtime.capture_mode.value,
        },
        "checks": checks,
        "ok": all(check["ok"] for check in checks),
    }
    return report


def write_preflight_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
