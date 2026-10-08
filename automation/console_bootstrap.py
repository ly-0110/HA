"""Start the local console with an existing or automatically prepared project venv."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

PYTHON_CHECK = (
    "import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] < (3, 14) else 1)"
)
DEPENDENCY_CHECK = (
    "import fastapi, pydantic, yaml, uvicorn, PIL, appium, iot_exp.web"
)


def project_environment(root: Path) -> dict[str, str]:
    """Load optional local tools without changing the user's global configuration."""
    environment = os.environ.copy()
    tools = root / ".tools"
    paths = []
    java = tools / "jre21"
    sdk = tools / "android-sdk"
    if java.is_dir():
        environment["JAVA_HOME"] = str(java)
        paths.append(java / "bin")
    if sdk.is_dir():
        environment["ANDROID_SDK_ROOT"] = str(sdk)
        environment["ANDROID_HOME"] = str(sdk)
        paths.append(sdk / "platform-tools")
    paths.extend([tools / "node22" / "bin", tools / "uv"])
    environment["PATH"] = os.pathsep.join(
        [str(path) for path in paths if path.is_dir()] + [environment.get("PATH", "")]
    )
    environment.setdefault("UV_CACHE_DIR", str(root / ".uv-cache"))
    environment.setdefault("PIP_CACHE_DIR", str(root / "runs" / "pip-cache"))
    environment["PYTHONUTF8"] = "1"
    source = str(root / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        [source] + ([environment["PYTHONPATH"]] if environment.get("PYTHONPATH") else [])
    )
    return environment


def venv_python(root: Path) -> Path:
    return root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def find_uv(root: Path, environment: dict[str, str]) -> str | None:
    executable = "uv.exe" if os.name == "nt" else "uv"
    home = Path(environment.get("USERPROFILE") or environment.get("HOME") or Path.home())
    candidates = [
        environment.get("IOT_EXP_UV"),
        str(root / ".tools" / "uv" / executable),
        str(root / ".tools" / executable),
        str(home / ".local" / "bin" / executable),
        str(home / ".cargo" / "bin" / executable),
        str(Path(sys.executable).parent / executable),
        shutil.which("uv", path=environment.get("PATH")),
    ]
    if os.name == "nt" and environment.get("APPDATA"):
        python_installs = Path(environment["APPDATA"]) / "Python"
        candidates.extend(str(path) for path in sorted(python_installs.glob("Python*/Scripts/uv.exe")))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def ready(python: Path, root: Path, environment: dict[str, str]) -> bool:
    if not python.is_file():
        return False
    try:
        for check in (PYTHON_CHECK, DEPENDENCY_CHECK):
            result = subprocess.run(
                [str(python), "-c", check], cwd=root, env=environment,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            )
            if result.returncode:
                return False
    except OSError:
        return False
    return True


def prepare_environment(root: Path, environment: dict[str, str]) -> Path:
    python = venv_python(root)
    if ready(python, root, environment):
        return python
    uv = find_uv(root, environment)
    if uv:
        print("正在准备项目 Python 环境（uv）…", flush=True)
        try:
            result = subprocess.run(
                [uv, "sync", "--project", str(root)], cwd=root, env=environment, check=False,
            )
            if result.returncode == 0 and ready(python, root, environment):
                return python
        except OSError:
            pass
        print("uv 准备失败，正在尝试使用现有 Python 安装项目依赖。", flush=True)
    if not (3, 10) <= sys.version_info[:2] < (3, 14):
        raise RuntimeError("需要 Python 3.10–3.13，请安装后重新启动控制台。")
    print("首次启动：正在安装依赖到项目 .venv，完成后自动打开控制台。", flush=True)
    subprocess.run(
        [sys.executable, "-m", "venv", str(root / ".venv")],
        cwd=root, env=environment, check=True,
    )
    subprocess.run(
        [str(python), "-m", "pip", "install", "--disable-pip-version-check", "-e", str(root)],
        cwd=root, env=environment, check=True,
    )
    if not ready(python, root, environment):
        raise RuntimeError("依赖安装后仍无法加载控制台，请查看上方 Python 错误。")
    return python


def main(argv: list[str] | None = None, *, project_root: Path | None = None) -> int:
    root = (project_root or Path(__file__).resolve().parent).resolve()
    if not (root / "web" / "dist" / "index.html").is_file():
        print("前端尚未构建：请在 automation 目录运行 npm install 和 npm run web:build。")
        return 1
    environment = project_environment(root)
    try:
        python = prepare_environment(root, environment)
        return subprocess.call(
            [str(python), "-m", "iot_exp.web", *(sys.argv[1:] if argv is None else argv)],
            cwd=root, env=environment,
        )
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"控制台启动失败：{error}", file=sys.stderr)
        print(
            "请检查网络连接和 Python 安装。Ubuntu 若缺少 venv，请安装 python3-venv。\n"
            "安装只写入项目 .venv；修复后重新启动即可，无需安装全局 uv 或修改 PATH。",
            file=sys.stderr,
        )
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
