"""Move the public runtime within the workspace, test it, and restore in finally."""
import argparse
import json
import os
import subprocess
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", type=Path, default=ROOT / "desktop/build-resources")
    parser.add_argument("--base", type=Path, default=ROOT / "runs/relocation")
    args = parser.parse_args()
    source = args.resources.resolve()
    base = args.base.resolve()
    # This script never moves experiment data or any directory outside its
    # explicitly selected build-resource parent and test root.
    if not source.is_dir() or not (source / "runtime-manifest.json").is_file():
        raise ValueError("Not a build-resource directory")
    base.mkdir(parents=True, exist_ok=True)
    destination = source.parent / "搬迁验证 有空格"
    if destination.exists():
        raise ValueError("Relocation destination already exists")
    if destination.parent.resolve() != source.parent.resolve():
        raise ValueError("Relocation target escaped the resource parent")
    original_environment = os.environ.copy()
    source.rename(destination)
    try:
        manifest = json.loads((destination / "runtime-manifest.json").read_text())
        node = destination / manifest["executables"]["node"]
        environment = os.environ.copy()
        # Remove globally installed development tools from child lookup.
        environment["PATH"] = str(base / "empty-path")
        environment["IOT_EXP_DESKTOP_RESOURCES"] = str(destination)
        environment["IOT_EXP_SMOKE_ROOT"] = str(base / "backend")
        (base / 'iot_exp.py').write_text('raise RuntimeError("cwd module must not be imported")')
        subprocess.run([str(node), str(ROOT / "desktop/scripts/smoke_backend.cjs")],
                       cwd=base, env=environment, check=True)
        python = destination / manifest["executables"]["python"]
        probe = (
            "import os,subprocess; from pathlib import Path; "
            "from iot_exp.runtime_bundle import load_manifest,private_environment; "
            f"r=Path({str(destination)!r}); s=Path({str(base / 'backend/state')!r}); "
            "e=os.environ.copy(); e.update(private_environment(r,s,load_manifest(r))); "
            "subprocess.run([e['IOT_EXP_NODE_EXECUTABLE'],e['IOT_EXP_APPIUM_ENTRY'],'driver','list','--installed','--json'],env=e,check=True)"
        )
        subprocess.run([str(python), '-I', '-B', '-X', 'utf8', '-c', probe], cwd=base, env=environment, check=True)
        session_id = 'relocated_cli_' + uuid.uuid4().hex[:12]
        subprocess.run([str(python), "-I", "-B", "-X", "utf8", "-m", "iot_exp.cli", "--resources", str(destination),
                        "--workspace", str(base / "cli"), "--app-state", str(base / "state"),
                        "run", "--dry-run", "--repetitions", "1", "--seed", "42",
                        "--session-id", session_id], cwd=base, env=environment, check=True)
        subprocess.run([str(python), "-I", "-B", "-X", "utf8", "-m", "iot_exp.cli", "--resources", str(destination),
                        "--workspace", str(base / "cli"), "--app-state", str(base / "state"),
                        "validate-session", str(base / "cli/runs/sessions" / session_id)],
                       cwd=base, env=environment, check=True)
        assert os.environ == original_environment
        (base / "result.json").write_text(json.dumps({"relocated":True,"unicode_spaces":True,
                "global_tools_removed_from_path":True,"backend_worker_cli":True,"restored":True}))
    finally:
        destination.rename(source)


if __name__ == "__main__":
    main()
