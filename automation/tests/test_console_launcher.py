"""First-run preparation and launcher argument forwarding without network or hardware."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("console_bootstrap", ROOT / "console_bootstrap.py")
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


def built_project(tmp_path: Path) -> Path:
    root = tmp_path / "实验平台 with spaces"
    dist = root / "web" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html></html>", encoding="utf-8")
    return root


def test_existing_venv_does_not_require_uv_or_install(tmp_path, monkeypatch):
    root = built_project(tmp_path)
    monkeypatch.setattr(bootstrap, "ready", lambda *_args: True)
    monkeypatch.setattr(
        bootstrap, "find_uv", lambda *_args: pytest.fail("ready venv should bypass uv discovery")
    )
    assert bootstrap.prepare_environment(root, {}) == bootstrap.venv_python(root)


def test_no_uv_creates_project_venv_and_installs_locally(tmp_path, monkeypatch):
    root = built_project(tmp_path)
    attempts = iter([False, True])
    monkeypatch.setattr(bootstrap, "ready", lambda *_args: next(attempts))
    monkeypatch.setattr(bootstrap, "find_uv", lambda *_args: None)
    calls = []
    monkeypatch.setattr(bootstrap.subprocess, "run", lambda command, **_kwargs: calls.append(command))
    assert bootstrap.prepare_environment(root, {}) == bootstrap.venv_python(root)
    assert calls == [
        [sys.executable, "-m", "venv", str(root / ".venv")],
        [str(bootstrap.venv_python(root)), "-m", "pip", "install",
         "--disable-pip-version-check", "-e", str(root)],
    ]


def test_uv_failure_falls_back_to_existing_python(tmp_path, monkeypatch):
    root = built_project(tmp_path)
    attempts = iter([False, True])
    monkeypatch.setattr(bootstrap, "ready", lambda *_args: next(attempts))
    monkeypatch.setattr(bootstrap, "find_uv", lambda *_args: "uv-test")
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, returncode=1 if command[0] == "uv-test" else 0)

    monkeypatch.setattr(bootstrap.subprocess, "run", run)
    bootstrap.prepare_environment(root, {})
    assert calls[0] == ["uv-test", "sync", "--project", str(root)]
    assert calls[1] == [sys.executable, "-m", "venv", str(root / ".venv")]
    assert calls[2][1:4] == ["-m", "pip", "install"]


def test_uv_discovery_in_standard_install_directory_without_path(tmp_path, monkeypatch):
    home = tmp_path / "profile"
    executable = home / ".local" / "bin" / ("uv.exe" if os.name == "nt" else "uv")
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setattr(bootstrap.shutil, "which", lambda *_args, **_kwargs: None)
    assert bootstrap.find_uv(tmp_path, {"USERPROFILE": str(home), "PATH": ""}) == str(executable)


def test_missing_build_and_install_error_are_actionable(tmp_path, monkeypatch, capsys):
    assert bootstrap.main([], project_root=tmp_path) == 1
    assert "npm run web:build" in capsys.readouterr().out
    root = built_project(tmp_path)

    def fail(*_args):
        raise subprocess.CalledProcessError(1, ["python", "-m", "pip"])

    monkeypatch.setattr(bootstrap, "prepare_environment", fail)
    assert bootstrap.main([], project_root=root) == 1
    assert "无需安装全局 uv" in capsys.readouterr().err


def test_local_tools_load_independently_of_uv(tmp_path, monkeypatch):
    monkeypatch.setenv("JAVA_HOME", "user-java")
    sdk = tmp_path / ".tools" / "android-sdk"
    sdk.mkdir(parents=True)
    (tmp_path / ".tools" / "node22" / "bin").mkdir(parents=True)
    environment = bootstrap.project_environment(tmp_path)
    assert environment["JAVA_HOME"] == "user-java"
    assert environment["ANDROID_SDK_ROOT"] == str(sdk)
    assert str(tmp_path / ".tools" / "node22" / "bin") in environment["PATH"]


def test_launcher_reuses_real_venv_without_uv_and_forwards_arguments(tmp_path):
    root = built_project(tmp_path)
    venv.EnvBuilder(with_pip=False).create(root / ".venv")
    source = root / "src"
    package = source / "iot_exp"
    package.mkdir(parents=True)
    (package / "__init__.py").touch()
    (package / "web.py").write_text(
        "import json, sys\nprint('LAUNCH_ARGS=' + json.dumps(sys.argv[1:]))\n", encoding="utf-8"
    )
    for dependency in ("fastapi", "pydantic", "yaml", "uvicorn", "PIL", "appium"):
        (source / f"{dependency}.py").touch()
    launcher_name = "start-console.cmd" if os.name == "nt" else "start-console.sh"
    for filename in (launcher_name, "console_bootstrap.py"):
        shutil.copy2(ROOT / filename, root / filename)
    environment = os.environ.copy()
    system_root = os.environ.get("SystemRoot", "C:/Windows")
    environment["PATH"] = str(Path(system_root) / "System32") if os.name == "nt" else "/usr/bin:/bin"
    for key in ("IOT_EXP_PYTHON", "IOT_EXP_UV"):
        environment.pop(key, None)
    arguments = ["--no-browser", "--port", "8877", "argument with spaces"]
    if os.name == "nt":
        command = [environment.get("COMSPEC", "cmd.exe"), "/d", "/c", launcher_name, *arguments]
    else:
        command = ["sh", str(root / launcher_name), *arguments]
    result = subprocess.run(
        command, cwd=root, env=environment, capture_output=True, encoding="utf-8",
        timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = result.stdout.split("LAUNCH_ARGS=", 1)[1].strip()
    assert json.loads(payload) == arguments


@pytest.mark.skipif(os.name != "nt", reason="Windows Python installation discovery")
def test_windows_launcher_accepts_explicit_python_without_path(tmp_path):
    root = built_project(tmp_path)
    shutil.copy2(ROOT / "start-console.cmd", root / "start-console.cmd")
    (root / "console_bootstrap.py").write_text(
        "import sys\nprint('PYTHON_DISCOVERED=' + sys.executable)\n", encoding="utf-8"
    )
    environment = os.environ.copy()
    environment["PATH"] = str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32")
    environment["IOT_EXP_PYTHON"] = sys.executable
    environment.pop("IOT_EXP_UV", None)
    result = subprocess.run(
        [environment.get("COMSPEC", "cmd.exe"), "/d", "/c", "start-console.cmd"],
        cwd=root, env=environment, capture_output=True, encoding="utf-8", timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PYTHON_DISCOVERED=" + sys.executable in result.stdout
