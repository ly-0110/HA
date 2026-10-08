import argparse
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--desktop', type=Path, default=ROOT)
    parser.add_argument('--base', type=Path, default=ROOT.parent / 'runs/single-instance')
    args = parser.parse_args()
    desktop = args.desktop.resolve()
    base = args.base.resolve()
    base.mkdir(parents=True, exist_ok=True)
    state = base / 'state'
    ready = state / 'instance-probe-ready.json'
    stop = state / 'instance-probe-stop'
    ready.unlink(missing_ok=True)
    stop.unlink(missing_ok=True)
    environment = os.environ.copy()
    environment.update(IOT_EXP_DESKTOP_STATE=str(state), IOT_EXP_DESKTOP_WORKSPACE=str(base / 'workspace'),
                       IOT_EXP_LOCK_ROOT=str(base / 'locks'))
    electron = desktop / 'node_modules/electron/dist' / ('electron.exe' if os.name == 'nt' else 'electron')
    first = subprocess.Popen([str(electron), str(desktop), '--instance-probe'], env=environment)
    try:
        for _ in range(1200):
            if ready.exists():
                break
            if first.poll() is not None:
                raise RuntimeError('First instance failed before readiness')
            time.sleep(0.1)
        if not ready.exists():
            raise RuntimeError('Single-instance probe initialization timed out')
        identity = json.loads(ready.read_text())
        subprocess.run([str(electron), str(desktop), '--instance-probe'], env=environment, check=True, timeout=20)
        alternate = base / 'other-installation'
        shutil.copytree(desktop / 'src', alternate / 'src', dirs_exist_ok=True)
        package = json.loads((desktop / 'package.json').read_text(encoding='utf-8'))
        package['version'] = '0.0.9'
        (alternate / 'package.json').write_text(json.dumps(package))
        subprocess.run([str(electron), str(alternate), '--instance-probe'], env=environment, check=True, timeout=20)
        assert first.poll() is None
        assert json.loads(ready.read_text()) == identity
        stop.touch()
        assert first.wait(timeout=30) == 0
        (base / 'result.json').write_text(json.dumps({'same_path':True,'different_path_version':True,'single_backend':True,'clean_exit':True}))
        print((base / 'result.json').read_text())
    finally:
        stop.touch()
        if first.poll() is None:
            first.wait(timeout=30)


if __name__ == '__main__':
    main()
