"""Fixed lamp acquisition campaign; receipts never control repetition counts.

Run from automation with the project's Python/Node/Java/ADB environment loaded.
Each group owns one continuous PCAP. Roles describe operations, not packet labels.
This acquisition protocol intentionally does not use the success-based scheduler.
"""
from __future__ import annotations

import argparse
import json
import random
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import yaml

from iot_exp.adapters.mi_home_lamp import MiHomeDeskLamp1SAdapter
from iot_exp.backends.capture import DumpcapCaptureBackend
from iot_exp.backends.system import AppiumServer, configure_android_environment
from iot_exp.config import load_configuration
from iot_exp.journal import JsonlJournal
from iot_exp.models import ParameterDimension, ParameterizedEventSpec, RuntimeConfig
from iot_exp.resources import ResourceLease, experiment_resource_keys

GROUPS = {
    "brightness": ("set_brightness", [30, 80], 60),
    "color_temperature": ("set_color_temperature", [3200, 4600], 60),
    "focus_mode": ("set_focus_mode", [True, False], 60),
    "scene": ("select_scene", ["电脑模式", "温馨模式", "休闲模式", "办公模式", "阅读模式", "娱乐模式"], 20),
}


def build_plan(group: str) -> list[dict]:
    event_type, targets, repeats = GROUPS[group]
    operations = []
    for index, target in enumerate(targets * repeats, 1):
        operations.append({"role": "target", "event_type": event_type, "target": target,
                           "target_index": index})
        if index % 10 == 0 and index < len(targets) * repeats:
            operations.extend([
                {"role": "u_source", "event_type": "turn_off", "target": None},
                {"role": "u_source", "event_type": "turn_on", "target": None},
            ])
    return operations


def write_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


class Campaign:
    def __init__(self, root: Path, *, udid: str, ip: str, interface: str, seed: int):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=False)
        experiment, runtime = load_configuration(
            Path("experiment/mi_desk_lamp_1s_advanced.yaml"), Path("runtime/ubuntu-dev.yaml"),
        )
        self.experiment = experiment.model_copy(update={
            "phone": experiment.phone.model_copy(update={"udid": udid, "phone_id": "lm_v405_d6421e56"}),
            "network": experiment.network.model_copy(update={
                "target_device_ip": ip, "forbidden_cidrs": ["10.42.0.0/24"],
            }),
        })
        self.runtime = RuntimeConfig.model_validate({
            **runtime.model_dump(), "mode": "formal", "capture_mode": "dumpcap",
            "capture_interface": interface, "capture_filter": f"host {ip}",
            "output_root": Path("runs").resolve(), "pre_roll_seconds": 5, "post_roll_seconds": 5,
        })
        self.seed = seed
        self.rng = random.Random(seed)
        self.adapter = MiHomeDeskLamp1SAdapter(
            self.experiment.app, self.experiment.phone, appium_url=self.runtime.appium_url,
            parameters=self.experiment.parameters,
        )
        self.server = AppiumServer("npx", self.runtime.appium_url, root / "appium.log", Path.cwd())
        self.summary = {"protocol": "lamp_pu_fixed_acquisition_v1", "root": str(root.resolve()),
                        "seed": seed, "started_at_unix_ns": time.time_ns(), "status": "starting",
                        "groups": {}, "clock_sync": "omitted_per_user_instruction",
                        "ha_logs": "external; not a collection gate",
                        "count_basis": "one dispatched UI action; no success-rate gate or retry",
                        "wait_after_operation_seconds": [5, 10],
                        "u_schedule": "off/on pair after every 10 targets, between target blocks"}
        write_json(root / "campaign.json", self.summary)
        (root / "configuration.yaml").write_text(yaml.safe_dump({
            "experiment": self.experiment.model_dump(mode="json"),
            "runtime": self.runtime.model_dump(mode="json"),
        }, allow_unicode=True, sort_keys=False), encoding="utf-8")

    def observe(self, dimension: ParameterDimension) -> dict:
        try:
            if dimension is ParameterDimension.FOCUS_MODE:
                self.adapter._open_focus_page()
                return self.adapter._read_focus_switch_now().model_dump(mode="json")
            return self.adapter.read_parameter(dimension).model_dump(mode="json")
        except Exception as exc:  # noqa: BLE001 - preserve third-party observation errors.
            return {"known": False, "value": None, "error": str(exc),
                    "observed_at_unix_ns": time.time_ns()}

    def evidence(self, folder: Path, tag: str) -> str | None:
        try:
            self.adapter._capture_evidence(folder, tag)
        except Exception as exc:  # noqa: BLE001 - evidence errors must not stop acquisition.
            return str(exc)
        return None

    def dispatch(self, operation: dict, before: dict, row: dict, journal: JsonlJournal) -> None:
        """Exactly one physical control action; no correction swipes or polling."""
        kind = operation["event_type"]
        spec = None
        if kind in {"turn_on", "turn_off"}:
            self.adapter._return_to_device_page()
            element = self.adapter._find(kind)
            action = element.click
        else:
            spec = ParameterizedEventSpec(event_type=kind, target=operation["target"])
            if spec.dimension is ParameterDimension.FOCUS_MODE:
                self.adapter._open_focus_page()
                element = self.adapter._find("focus_switch")
                # Toggle once; preserve requested and observed values separately.
                action = element.click
            elif spec.dimension is ParameterDimension.SCENE:
                element = self.adapter._find_scene_element(str(spec.target))
                action = element.click
            else:
                if not before.get("known") or isinstance(before.get("value"), bool) \
                        or not isinstance(before.get("value"), int):
                    raise RuntimeError("numeric value unreadable; cannot locate slider thumb")
                config = self.adapter._numeric_config(spec.dimension)
                slider = self.adapter._find_innermost(f"{spec.dimension.value}_slider")
                rect = slider.rect
                start_x = self.adapter._slider_x(rect, before["value"], config)
                end_x = self.adapter._slider_x(rect, int(spec.target), config)
                y = int(rect["y"] + rect["height"] / 2)
                row["gesture"] = {"start_x": start_x, "end_x": end_x, "y": y, "duration_ms": 600}
                action = lambda: self.adapter.driver.swipe(start_x, y, end_x, y, 600)
        row["t_cmd_before_ns"] = time.time_ns()
        journal.append({"kind": "event_command", "event_id": row["event_id"],
                        "event_type": kind, "target": operation["target"], "role": row["role"],
                        "at_unix_ns": row["t_cmd_before_ns"]})
        action()
        row["t_cmd_after_ns"] = time.time_ns()
        row["dispatched"] = True

    def run_group(self, group: str) -> None:
        folder = self.root / group
        folder.mkdir()
        (folder / "screenshots").mkdir()
        operations = build_plan(group)
        write_json(folder / "capture_plan.json", {"group": group, "operations": operations})
        journal = JsonlJournal(folder / "run_journal.jsonl")
        actions = JsonlJournal(folder / "actions.jsonl")
        capture = DumpcapCaptureBackend("dumpcap", self.runtime.capture_interface, self.runtime.capture_filter)
        report = {"planned_target": 120, "planned_u_operations": 22, "processed": 0,
                  "target_dispatched": 0, "u_dispatched": 0, "errors": 0, "status": "running",
                  "started_at_unix_ns": time.time_ns(), "by_target": {}}
        self.summary["groups"][group] = report
        self.summary["current_group"] = group
        self.summary["status"] = "running"
        write_json(self.root / "campaign.json", self.summary)
        self.adapter._return_to_device_page()
        if self.adapter.read_state().value == "off":
            raise RuntimeError("lamp is off before group; setup requires an explicit recorded power-on")
        capture.start(folder / "traffic.pcapng")
        rows = []
        try:
            time.sleep(5)
            for index, operation in enumerate(operations, 1):
                if capture.process.poll() is not None:
                    raise RuntimeError("capture process exited during acquisition")
                row = {**operation, "group": group, "event_id": f"{self.root.name}_{group}_{index:04d}",
                       "sequence": index, "phone_udid": self.experiment.phone.udid,
                       "device_id": self.experiment.device.device_id, "dispatched": False,
                       "started_at_unix_ns": time.time_ns()}
                dimension = ParameterDimension(group) if operation["role"] == "target" else None
                journal.append({"kind": "event_started", "event_id": row["event_id"],
                                "at_unix_ns": row["started_at_unix_ns"]})
                try:
                    if dimension is not None:
                        row["observed_before"] = self.observe(dimension)
                    row["evidence_before_error"] = self.evidence(folder / "screenshots", row["event_id"] + "_before")
                    self.dispatch(operation, row.get("observed_before", {}), row, journal)
                    time.sleep(1.0)
                    if dimension is not None:
                        row["observed_after"] = self.observe(dimension)
                    else:
                        row["state_after"] = self.adapter.read_state().value
                except Exception as exc:  # noqa: BLE001 - retain failed operation and continue plan.
                    row["error"] = str(exc)
                    report["errors"] += 1
                    # Preserve the failed operation, then continue the fixed plan.
                    try:
                        self.adapter.recover_navigation()
                    except Exception as recovery:  # noqa: BLE001 - recovery is best effort.
                        row["recovery_error"] = str(recovery)
                row["evidence_after_error"] = self.evidence(folder / "screenshots", row["event_id"] + "_after")
                row["finished_at_unix_ns"] = time.time_ns()
                row["wait_after_seconds"] = self.rng.uniform(5, 10)
                actions.append(row)
                rows.append(row)
                journal.append({"kind": "event_finished", "event_id": row["event_id"],
                                "dispatched": row["dispatched"], "at_unix_ns": row["finished_at_unix_ns"]})
                report["processed"] = index
                key = "target_dispatched" if row["role"] == "target" else "u_dispatched"
                report[key] += int(row["dispatched"])
                if row["role"] == "target" and row["dispatched"]:
                    target = str(row["target"])
                    report["by_target"][target] = report["by_target"].get(target, 0) + 1
                write_json(self.root / "campaign.json", self.summary)
                print(json.dumps({"group": group, "processed": index, "total": len(operations),
                                  "target_dispatched": report["target_dispatched"],
                                  "u_dispatched": report["u_dispatched"], "errors": report["errors"]}, ensure_ascii=False), flush=True)
                time.sleep(row["wait_after_seconds"])
            time.sleep(5)
            report["status"] = "completed" if report["target_dispatched"] == 120 and report["u_dispatched"] == 22 else "incomplete"
        finally:
            result = capture.stop()
            report["capture"] = result.model_dump(mode="json")
            report["finished_at_unix_ns"] = time.time_ns()
            report["pcap_bytes"] = (folder / "traffic.pcapng").stat().st_size
            report["results"] = dict(Counter("error" if r.get("error") else "dispatched" for r in rows))
            write_json(folder / "acquisition_report.json", report)
            write_json(self.root / "campaign.json", self.summary)

    def run(self) -> None:
        configure_android_environment(self.runtime.adb_executable, self.runtime.android_sdk_root)
        with ResourceLease(self.runtime.output_root, experiment_resource_keys(self.experiment, self.runtime), self.root.name):
            try:
                self.server.start()
                self.adapter.launch_and_open_device()
                for group in GROUPS:
                    self.run_group(group)
                self.summary["status"] = "completed" if all(g["status"] == "completed" for g in self.summary["groups"].values()) else "incomplete"
            except BaseException as exc:
                self.summary["status"] = "interrupted"
                self.summary["error"] = str(exc)
                raise
            finally:
                self.summary["finished_at_unix_ns"] = time.time_ns()
                write_json(self.root / "campaign.json", self.summary)
                try:
                    self.adapter.close()
                finally:
                    self.server.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, help="Output directory; default: results/lamp_pu_<timestamp>")
    parser.add_argument("--udid", required=True)
    parser.add_argument("--ip", required=True)
    parser.add_argument("--interface", required=True)
    parser.add_argument("--seed", type=int, default=20260927)
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S")
    root = args.root or Path("results") / f"lamp_pu_{stamp}"
    Campaign(root, udid=args.udid, ip=args.ip, interface=args.interface, seed=args.seed).run()


if __name__ == "__main__":
    main()
