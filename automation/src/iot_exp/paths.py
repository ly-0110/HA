from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from .runtime_bundle import filesystem_path


def source_root() -> Path:
    explicit = os.environ.get("IOT_EXP_RESOURCES_ROOT")
    return Path(explicit).resolve() if explicit else Path(__file__).resolve().parents[2]


def shared_lock_root() -> Path:
    explicit = os.environ.get("IOT_EXP_LOCK_ROOT")
    if explicit:
        return Path(explicit).resolve()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
        return base / "IoTExperimentWorkbench" / "locks"
    return Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "iot-exp/locks"


def app_state_root() -> Path:
    base = Path(os.environ.get("APPDATA", str(Path.home() / "AppData/Roaming"))) if os.name == "nt" else Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return base / "org.iotexp.workbench"


@dataclass(frozen=True)
class PathLayout:
    resources_root: Path
    config_root: Path
    state_root: Path
    output_root: Path
    lock_root: Path
    app_state_root: Path
    discovery_roots: tuple[Path, ...] = ()
    runtime_root: Path | None = None
    python_executable: Path = Path(sys.executable)
    desktop: bool = False

    @classmethod
    def source(cls, root: Path | None = None) -> PathLayout:
        root = (root or source_root()).resolve()
        return cls(root, root, root / "runs", root / "runs", shared_lock_root(), root / "runs")

    @classmethod
    def workspace(cls, resources: Path, workspace: Path, app_state: Path,
                  *, runtime: Path | None = None, lock_root: Path | None = None,
                  discovery_roots: tuple[Path, ...] = ()) -> PathLayout:
        resources, workspace, app_state = resources.resolve(), workspace.resolve(), app_state.resolve()
        return cls(resources, workspace / "config", workspace / "state", workspace / "runs",
                   (lock_root or shared_lock_root()).resolve(), app_state,
                   tuple(path.resolve() for path in discovery_roots),
                   runtime.resolve() if runtime else None, Path(sys.executable).resolve(), True)

    @property
    def output_base(self) -> Path:
        return self.config_root.parent if self.desktop else self.config_root

    @property
    def database(self) -> Path:
        return self.state_root / "console.sqlite3"

    @property
    def control_root(self) -> Path:
        return self.state_root / "control"

    def initialize(self) -> None:
        if not self.desktop:
            return
        for path in (self.config_root, self.state_root, self.output_root, self.app_state_root):
            if path.is_relative_to(self.resources_root):
                raise ValueError("桌面可写路径不能位于安装资源目录内")
            path.mkdir(parents=True, exist_ok=True)
        for kind in ("experiment", "runtime"):
            source = self.resources_root / "templates" / kind
            if not filesystem_path(source).is_dir():
                source = self.resources_root / kind
            target = self.config_root / kind
            target.mkdir(exist_ok=True)
            for path in filesystem_path(source).glob("*.yaml"):
                destination = target / path.name
                if not destination.exists():
                    shutil.copyfile(path, filesystem_path(destination))
        marker = self.state_root / "workspace.json"
        if not marker.exists():
            marker.write_text(json.dumps({"schema": 1, "resources": str(self.resources_root)},
                                         ensure_ascii=False), encoding="utf-8")

    def as_dict(self) -> dict:
        return {key: ([str(path) for path in value] if isinstance(value, tuple)
                      else str(value) if isinstance(value, Path) else value)
                for key, value in self.__dict__.items()}

    def child_environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        environment["IOT_EXP_CONTEXT"] = json.dumps(self.as_dict())
        environment["IOT_EXP_LOCK_ROOT"] = str(self.lock_root)
        environment["PYTHONUTF8"] = "1"
        if not self.desktop:
            source = str(self.resources_root / "src")
            environment["PYTHONPATH"] = os.pathsep.join(
                [source] + ([environment["PYTHONPATH"]] if environment.get("PYTHONPATH") else []))
        return environment


def current_layout(root: Path | None = None) -> PathLayout:
    value = os.environ.get("IOT_EXP_CONTEXT")
    if value:
        data = json.loads(value)
        paths = {key: Path(item).resolve() for key, item in data.items()
                 if key.endswith("_root") and item is not None and key != "discovery_roots"}
        layout = PathLayout(**paths,
                            discovery_roots=tuple(Path(item).resolve() for item in data.get("discovery_roots", [])),
                            python_executable=Path(data.get("python_executable", sys.executable)),
                            desktop=bool(data.get("desktop")))
        if root is None or root.resolve() == layout.config_root:
            return layout
    return PathLayout.source(root)
