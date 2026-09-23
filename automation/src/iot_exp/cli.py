from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .adapters import SimulatedLampAdapter
from .backends import (
    AdbClient,
    AndroidDevice,
    AppiumServer,
    DisabledCaptureBackend,
    DisabledHaProvider,
    DumpcapCaptureBackend,
)
from .backends.system import configure_android_environment, discover_android_sdk
from .config import ConfigError, apply_runtime_paths, is_placeholder, load_configuration
from .ha_reconcile import export_window, reconcile
from .models import CaptureMode
from .orchestrator import ExperimentRunner, validate_session
from .pcap_review import review_pcap
from .preflight import run_preflight, write_preflight_report
from .registry import create_adapter
from .resources import ResourceLease, experiment_resource_keys


def _automation_root() -> Path:
    """Return the source checkout's automation directory."""
    return Path(__file__).resolve().parents[2]


def _default_path(relative: str) -> Path:
    """Allow commands from either the repository root or automation/."""
    from_cwd = Path(relative)
    if from_cwd.exists():
        return from_cwd
    return _automation_root() / relative


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="iot-exp", description="IoT vendor-app automation experiment runner")
    parser.add_argument("--experiment", type=Path, default=_default_path("experiment/mi_desk_lamp_1s.yaml"))
    parser.add_argument("--runtime", type=Path, default=_default_path("runtime/windows-dev.yaml"))
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("doctor", "inspect-app", "preflight", "run"):
        command = sub.add_parser(name)
        command.add_argument("--dry-run", action="store_true", help="use a simulated adapter")
        command.add_argument("--session-id")
        command.add_argument("--seed", type=int)
        command.add_argument("--repetitions", type=int, help="override repetitions per event")
        if name != "doctor":
            command.add_argument("--udid", help="select one connected Android device by ADB serial")
            command.add_argument(
                "--select-device",
                action="store_true",
                help="interactively choose from connected and authorized devices",
            )
            command.add_argument("--phone-id", help="override the logical phone_id recorded in events")
        if name in {"inspect-app", "run"}:
            command.add_argument(
                "--appium-port",
                type=int,
                help="override the managed Appium server port for this process",
            )
            command.add_argument(
                "--system-port",
                type=int,
                help="override the UiAutomator2 systemPort for the selected phone",
            )
    sub.add_parser("devices", help="list connected Android devices and detected app metadata")
    validate = sub.add_parser("validate-session")
    validate.add_argument("session_root", type=Path)
    window = sub.add_parser("ha-window", help="print the UTC time range for an HA export")
    window.add_argument("session_root", type=Path)
    matching = sub.add_parser("reconcile-ha", help="match an exported HA JSON file to a session")
    matching.add_argument("session_root", type=Path)
    matching.add_argument("history_json", type=Path)
    matching.add_argument("--clock-offset-ms", type=float, help="capture_minus_ha_ms from clock_probe.py")
    matching.add_argument("--clock-uncertainty-ms", type=float, help="uncertainty_at_most_ms from clock_probe.py")
    pcap = sub.add_parser("review-pcap", help="summarize target traffic around every App event")
    pcap.add_argument("session_root", type=Path)
    return parser


def _load(args: argparse.Namespace):
    experiment, runtime = load_configuration(args.experiment, args.runtime)
    runtime = apply_runtime_paths(runtime, config_dir=args.runtime.parent.parent.resolve())
    if getattr(args, "repetitions", None) is not None:
        if args.repetitions < 1:
            raise ConfigError("--repetitions must be at least 1")
        experiment = experiment.model_copy(update={
            "sessions": experiment.sessions.model_copy(update={"repetitions_per_event": args.repetitions})
        })
    runtime_updates = {}
    if getattr(args, "appium_port", None) is not None:
        if not 1 <= args.appium_port <= 65535:
            raise ConfigError("--appium-port must be between 1 and 65535")
        endpoint = urlsplit(runtime.appium_url)
        host = endpoint.hostname or "127.0.0.1"
        netloc = f"{host}:{args.appium_port}"
        runtime_updates["appium_url"] = urlunsplit((endpoint.scheme or "http", netloc, "", "", ""))
    if getattr(args, "system_port", None) is not None:
        if not 1024 <= args.system_port <= 65535:
            raise ConfigError("--system-port must be between 1024 and 65535")
        runtime_updates["uiautomator2_system_port"] = args.system_port
    if runtime_updates:
        runtime = runtime.model_copy(update=runtime_updates)
    return experiment, runtime


def _device_label(device: AndroidDevice) -> str:
    details = [device.udid, device.state]
    if device.manufacturer:
        details.append(device.manufacturer)
    if device.model:
        details.append(device.model)
    return " | ".join(details)


def select_device(
    devices: list[AndroidDevice],
    *,
    requested_udid: str | None = None,
    interactive: bool = False,
    input_fn=None,
) -> AndroidDevice:
    if requested_udid:
        match = next((device for device in devices if device.udid == requested_udid), None)
        if match is None:
            raise ConfigError(f"requested Android device is not connected: {requested_udid}")
        if not match.online:
            raise ConfigError(f"requested Android device is {match.state}: {requested_udid}")
        return match
    online = [device for device in devices if device.online]
    if not online:
        observed = ", ".join(_device_label(device) for device in devices) or "none"
        raise ConfigError(f"no authorized online Android device; observed: {observed}")
    if len(online) == 1:
        return online[0]
    if not interactive:
        choices = ", ".join(device.udid for device in online)
        raise ConfigError(
            f"multiple Android devices are online ({choices}); use --udid <serial> or --select-device"
        )
    if not sys.stdin.isatty() and input_fn is None:
        raise ConfigError("--select-device requires an interactive terminal; use --udid in scripts")
    for index, device in enumerate(online, 1):
        print(f"[{index}] {_device_label(device)}", file=sys.stderr)
    reader = input if input_fn is None else input_fn
    try:
        choice = int(reader("Select Android device number: "))
    except (TypeError, ValueError, EOFError) as exc:
        raise ConfigError("invalid Android device selection") from exc
    if choice < 1 or choice > len(online):
        raise ConfigError("Android device selection is out of range")
    return online[choice - 1]


def _resolved_phone_id(configured: str, device: AndroidDevice, override: str | None) -> str:
    if override:
        return override
    if configured and configured != "android_phone_01":
        return configured
    model = re.sub(r"[^a-z0-9]+", "_", (device.model or "android").lower()).strip("_")
    return f"{model or 'android'}_{device.udid[-8:]}"


def resolve_selected_device(args, experiment, runtime):
    adb = AdbClient(runtime.adb_executable)
    devices = adb.list_devices()
    configured_udid = None if is_placeholder(experiment.phone.udid) else experiment.phone.udid
    interactive = bool(getattr(args, "select_device", False))
    selected = select_device(
        devices,
        requested_udid=getattr(args, "udid", None) or (None if interactive else configured_udid),
        interactive=interactive,
    )
    selected = adb.describe_device(selected, experiment.app.package)
    phone = experiment.phone.model_copy(update={
        "phone_id": _resolved_phone_id(
            experiment.phone.phone_id,
            selected,
            getattr(args, "phone_id", None),
        ),
        "udid": selected.udid,
        "model": selected.model,
        "product": selected.product,
        "device": selected.device,
        "transport_id": selected.transport_id,
        "manufacturer": selected.manufacturer,
        "android_version": selected.android_version,
        "sdk_level": selected.sdk_level,
    })
    app = experiment.app.model_copy(update={
        "version": selected.app_version or experiment.app.version,
    })
    return experiment.model_copy(update={"phone": phone, "app": app}), selected


def runtime_payload(runtime) -> dict:
    sdk_root = discover_android_sdk(runtime.adb_executable, runtime.android_sdk_root)
    return {
        "adb_executable": runtime.adb_executable,
        "android_sdk_root": str(sdk_root) if sdk_root is not None else None,
        "appium_url": runtime.appium_url,
        "uiautomator2_system_port": runtime.uiautomator2_system_port,
        "capture_mode": runtime.capture_mode.value,
    }


def selection_payload(experiment, selected: AndroidDevice, runtime) -> dict:
    return {
        "selected_phone": experiment.phone.model_dump(mode="json"),
        "app": {"package": experiment.app.package, "version": experiment.app.version},
        "adb_device": asdict(selected),
        "automation_runtime": runtime_payload(runtime),
    }


def cmd_devices(experiment, runtime) -> int:
    adb = AdbClient(runtime.adb_executable)
    devices = [adb.describe_device(device, experiment.app.package) for device in adb.list_devices()]
    print(json.dumps({
        "devices": [asdict(device) for device in devices],
        "automation_runtime": runtime_payload(runtime),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_doctor(experiment, runtime) -> int:
    from .backends.system import collect_system_checks

    checks = collect_system_checks(
        adb=runtime.adb_executable,
        appium=runtime.appium_executable,
        dumpcap=runtime.dumpcap_executable,
        require_capture=runtime.capture_mode is CaptureMode.DUMPCAP,
        android_sdk_root=runtime.android_sdk_root,
    )
    if runtime.mode.value == "formal":
        checks.append(type(checks[0])(
            "formal_target_device_ip",
            bool(experiment.network.target_device_ip),
            experiment.network.target_device_ip or "not configured",
        ))
        checks.append(type(checks[0])(
            "formal_capture_filter",
            bool(runtime.capture_filter and "REPLACE_WITH" not in runtime.capture_filter),
            runtime.capture_filter or "not configured",
        ))
    for check in checks:
        print(f"{'OK' if check.ok else 'FAIL'} {check.name}: {check.detail}")
    if runtime.mode.value == "formal" and any(not check.ok for check in checks):
        return 2
    return 0


def cmd_preflight(experiment, runtime) -> int:
    report = run_preflight(experiment, runtime)
    output = runtime.output_root / "preflight_latest.json"
    write_preflight_report(output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else (0 if runtime.mode.value == "dev" else 2)


def cmd_inspect(experiment, runtime, dry_run: bool) -> int:
    if dry_run:
        print("dry-run inspect does not require a real device; use run for the simulated contract")
        return 0
    configure_android_environment(runtime.adb_executable, runtime.android_sdk_root)
    adapter = create_adapter(experiment, runtime)
    server = AppiumServer(
        runtime.appium_executable,
        runtime.appium_url,
        runtime.output_root / "appium_inspect.log",
        project_root=_automation_root(),
    )
    try:
        if runtime.appium_managed:
            server.start()
        adapter.connect()
        try:
            adapter.launch_and_open_device()
            state = adapter.read_state()
        except Exception:
            adapter.capture_diagnostics(runtime.output_root / "inspect", "inspect_failure")
            raise
        print(f"current_state={state.value}; configure selectors in experiment YAML")
        return 0
    finally:
        adapter.close()
        server.stop()


def cmd_run(args, experiment, runtime) -> int:
    preflight_report = run_preflight(experiment, runtime)
    write_preflight_report(runtime.output_root / "preflight_latest.json", preflight_report)
    if runtime.mode.value == "formal" and not preflight_report["ok"]:
        print(json.dumps(preflight_report, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    if args.dry_run:
        initial_state = experiment.events[0].required_state
        adapter = SimulatedLampAdapter(initial_state)
        capture = DisabledCaptureBackend()
        ha = DisabledHaProvider()
        server = None
    else:
        configure_android_environment(runtime.adb_executable, runtime.android_sdk_root)
        adapter = create_adapter(experiment, runtime, screenshot_dir=runtime.output_root)
        capture = (
            DumpcapCaptureBackend(runtime.dumpcap_executable, runtime.capture_interface or "", runtime.capture_filter)
            if runtime.capture_mode is CaptureMode.DUMPCAP
            else DisabledCaptureBackend()
        )
        ha = DisabledHaProvider()
        server = None
    try:
        runner = ExperimentRunner(
            experiment,
            runtime,
            adapter=adapter,
            capture=capture,
            ha=ha,
            session_id=args.session_id,
            seed=args.seed,
        )
        write_preflight_report(runner.paths.network_isolation_check, preflight_report)
        if not args.dry_run:
            server = AppiumServer(
                runtime.appium_executable,
                runtime.appium_url,
                runner.paths.appium_log,
                project_root=_automation_root(),
            )
        if server is not None and runtime.appium_managed:
            server.start()
        records = runner.run()
        print(json.dumps({
            "session_id": runner.session_id,
            "records": len(records),
            "root": str(runner.paths.root),
            "selected_phone": experiment.phone.model_dump(mode="json"),
            "app": {"package": experiment.app.package, "version": experiment.app.version},
            "automation_runtime": runtime_payload(runtime),
        }, ensure_ascii=False))
        return 0
    finally:
        if server is not None:
            server.stop()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate-session":
            report = validate_session(args.session_root)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["ok"] else 2
        if args.command == "ha-window":
            print(json.dumps(export_window(args.session_root), ensure_ascii=False, indent=2))
            return 0
        if args.command == "reconcile-ha":
            report = reconcile(
                args.session_root, args.history_json,
                args.clock_offset_ms, args.clock_uncertainty_ms,
            )
            print(json.dumps({
                "session_id": report["session_id"],
                "candidate_gold_count": report["candidate_gold_count"],
                "action_count": report["action_count"],
                "report": str(args.session_root / "ha_reconciliation.json"),
            }, ensure_ascii=False, indent=2))
            return 0
        if args.command == "review-pcap":
            report = review_pcap(args.session_root)
            print(json.dumps({
                "session_id": report["session_id"],
                "event_count": report["event_count"],
                "bidirectional_event_count": report["bidirectional_event_count"],
                "report": str(args.session_root / "pcap_review.json"),
            }, ensure_ascii=False, indent=2))
            return 0
        experiment, runtime = _load(args)
        if args.command == "devices":
            return cmd_devices(experiment, runtime)
        selected = None
        if args.command in {"preflight", "inspect-app", "run"} and not getattr(args, "dry_run", False):
            experiment, selected = resolve_selected_device(args, experiment, runtime)
            print(json.dumps(selection_payload(experiment, selected, runtime), ensure_ascii=False, indent=2))
        if args.command == "doctor":
            return cmd_doctor(experiment, runtime)
        if args.command == "preflight":
            return cmd_preflight(experiment, runtime)
        if args.command == "inspect-app":
            return cmd_inspect(experiment, runtime, args.dry_run)
        if args.command == "run":
            if args.dry_run:
                return cmd_run(args, experiment, runtime)
            with ResourceLease(
                runtime.output_root,
                experiment_resource_keys(experiment, runtime),
                owner=f"cli:{args.session_id or 'generated'}",
            ):
                return cmd_run(args, experiment, runtime)
    except (ConfigError, RuntimeError, OSError, ValueError, TypeError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
