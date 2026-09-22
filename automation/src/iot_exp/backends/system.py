from __future__ import annotations

import json
import os
import platform
import re
import shutil
import signal
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


class SystemCommandError(RuntimeError):
    pass


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class AndroidDevice:
    udid: str
    state: str
    model: str | None = None
    product: str | None = None
    device: str | None = None
    transport_id: str | None = None
    manufacturer: str | None = None
    android_version: str | None = None
    sdk_level: str | None = None
    app_version: str | None = None

    @property
    def online(self) -> bool:
        return self.state == "device"


def parse_adb_devices(output: str) -> list[AndroidDevice]:
    devices: list[AndroidDevice] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("List of devices", "*")):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        metadata = dict(part.split(":", 1) for part in parts[2:] if ":" in part)
        devices.append(AndroidDevice(
            udid=parts[0],
            state=parts[1],
            model=metadata.get("model"),
            product=metadata.get("product"),
            device=metadata.get("device"),
            transport_id=metadata.get("transport_id"),
        ))
    return devices


class AdbClient:
    def __init__(self, executable: str = "adb"):
        resolved = resolve_executable(executable)
        self.executable = str(resolved) if resolved is not None else executable

    def run(self, args: Sequence[str], *, timeout: float = 30) -> CommandResult:
        command = (self.executable, *map(str, args))
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise SystemCommandError(f"ADB executable not found: {self.executable}") from exc
        return CommandResult(command, completed.returncode, completed.stdout, completed.stderr)

    def list_devices(self) -> list[AndroidDevice]:
        result = self.run(("devices", "-l"))
        if result.returncode != 0:
            raise SystemCommandError(result.stderr.strip() or "adb devices failed")
        return parse_adb_devices(result.stdout)

    def devices(self) -> list[str]:
        return [device.udid for device in self.list_devices() if device.online]

    def shell(self, *args: str, udid: str | None = None, timeout: float = 30) -> CommandResult:
        prefix = ("-s", udid) if udid else ()
        return self.run((*prefix, "shell", *args), timeout=timeout)

    def is_online(self, udid: str) -> bool:
        return udid in self.devices()

    def describe_device(self, device: AndroidDevice, package: str | None = None) -> AndroidDevice:
        if not device.online:
            return device
        properties = self.shell("getprop", udid=device.udid, timeout=10)
        prop_map = dict(re.findall(r"^\[([^]]+)\]: \[(.*)\]$", properties.stdout, re.MULTILINE))
        app_version = None
        if package:
            package_info = self.shell("dumpsys", "package", package, udid=device.udid, timeout=20)
            match = re.search(r"^\s*versionName=(.+)$", package_info.stdout, re.MULTILINE)
            if match:
                app_version = match.group(1).strip()
        return AndroidDevice(
            udid=device.udid,
            state=device.state,
            model=device.model or prop_map.get("ro.product.model"),
            product=device.product or prop_map.get("ro.build.product"),
            device=device.device or prop_map.get("ro.product.device"),
            transport_id=device.transport_id,
            manufacturer=prop_map.get("ro.product.manufacturer"),
            android_version=prop_map.get("ro.build.version.release"),
            sdk_level=prop_map.get("ro.build.version.sdk"),
            app_version=app_version,
        )


def resolve_executable(name: str) -> Path | None:
    candidate = Path(name).expanduser()
    if candidate.parent != Path(".") and candidate.exists():
        return candidate.resolve()
    located = shutil.which(name)
    if located:
        return Path(located).resolve()
    if candidate.name.lower() in {"adb", "adb.exe"}:
        sdk_roots = [
            os.environ.get("ANDROID_SDK_ROOT"),
            os.environ.get("ANDROID_HOME"),
        ]
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            sdk_roots.append(str(Path(local_app_data) / "Android" / "Sdk"))
        for sdk_root in sdk_roots:
            if not sdk_root:
                continue
            adb_name = "adb.exe" if os.name == "nt" else "adb"
            adb_path = Path(sdk_root) / "platform-tools" / adb_name
            if adb_path.exists():
                return adb_path.resolve()
    return None


def discover_android_sdk(
    adb: str,
    configured: str | Path | None = None,
    environment: dict[str, str] | None = None,
) -> Path | None:
    env = os.environ if environment is None else environment
    candidates = [configured, env.get("ANDROID_SDK_ROOT"), env.get("ANDROID_HOME")]
    for candidate in candidates:
        if candidate:
            return Path(candidate).expanduser().resolve()
    adb_path = resolve_executable(adb)
    if adb_path is not None and adb_path.parent.name.lower() == "platform-tools":
        return adb_path.parent.parent
    return None


def configure_android_environment(adb: str, configured: str | Path | None = None) -> Path:
    sdk_root = discover_android_sdk(adb, configured)
    if sdk_root is None:
        raise SystemCommandError(
            "Android SDK root could not be determined; set runtime.android_sdk_root, "
            "ANDROID_SDK_ROOT, or ANDROID_HOME"
        )
    os.environ["ANDROID_SDK_ROOT"] = str(sdk_root)
    os.environ.setdefault("ANDROID_HOME", str(sdk_root))
    return sdk_root


class AppiumServer:
    def __init__(self, executable: str, url: str, log_path: Path, project_root: Path | None = None):
        self.executable = executable
        self.url = url
        self.log_path = log_path
        self.project_root = project_root
        self.process: subprocess.Popen[str] | None = None

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = self.log_path.open("a", encoding="utf-8")
        try:
            kwargs: dict[str, object] = {
                "stdout": log_handle,
                "stderr": subprocess.STDOUT,
                "text": True,
            }
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kwargs["start_new_session"] = True
            executable_name = Path(self.executable).stem.lower()
            command = [self.executable]
            if executable_name == "npx":
                if self.project_root is None:
                    raise SystemCommandError("project_root is required for project-local Appium")
                command = ["node", str(self.project_root / "node_modules" / "appium" / "index.js")]
            endpoint = urlsplit(self.url)
            address = endpoint.hostname or "127.0.0.1"
            port = endpoint.port or (443 if endpoint.scheme == "https" else 80)
            command.extend(["--address", address, "--port", str(port)])
            if self.project_root is not None:
                kwargs["cwd"] = str(self.project_root)
            self.process = subprocess.Popen(command, **kwargs)
        except FileNotFoundError as exc:
            log_handle.close()
            raise SystemCommandError(f"Appium executable not found: {self.executable}") from exc
        # The child owns the file descriptor after spawn on both supported platforms.
        log_handle.close()
        self._wait_for_http()

    def _wait_for_http(self, timeout: float = 20) -> None:
        import urllib.request

        status_url = f"{self.url.rstrip('/')}/status"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                raise SystemCommandError(f"Appium exited with code {self.process.returncode}")
            try:
                with urllib.request.urlopen(status_url, timeout=1) as response:
                    if response.status < 500:
                        return
            except (OSError, ValueError):
                time.sleep(0.25)
        raise SystemCommandError(f"Appium did not become ready at {self.url}")

    def stop(self) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            else:
                self.process.send_signal(signal.SIGINT)
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=5)
        self.process = None


@dataclass(frozen=True)
class SystemCheck:
    name: str
    ok: bool
    detail: str


def check_executable(name: str) -> SystemCheck:
    path = resolve_executable(name)
    return SystemCheck(name, path is not None, str(path) if path else "not found")


def collect_system_checks(
    *,
    adb: str,
    appium: str,
    dumpcap: str,
    require_capture: bool,
    android_sdk_root: str | Path | None = None,
) -> list[SystemCheck]:
    checks = [check_executable(adb), check_executable("node"), check_executable("java")]
    sdk_root = discover_android_sdk(adb, android_sdk_root)
    checks.append(SystemCheck(
        "android_sdk_root",
        sdk_root is not None and sdk_root.exists(),
        str(sdk_root) if sdk_root is not None else "not determined",
    ))
    checks.append(check_executable(appium))
    if require_capture:
        checks.append(check_executable(dumpcap))
    checks.append(SystemCheck("platform", platform.system() in {"Windows", "Linux"}, platform.platform()))
    return checks


def write_check_report(path: Path, checks: list[SystemCheck]) -> None:
    path.write_text(
        json.dumps([check.__dict__ for check in checks], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
