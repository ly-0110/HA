import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from iot_exp.paths import shared_lock_root
from iot_exp.process_identity import process_start_token


def test_two_processes_compete_without_double_ownership(tmp_path):
    expired = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(.1)"])
    token = process_start_token(expired.pid)
    expired.wait(timeout=5)
    directory = shared_lock_root()
    directory.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(b"phone:race").hexdigest()[:24] + ".lock"
    (directory / name).write_text(json.dumps({"pid": expired.pid, "process_start_token": token,
                                            "key": "phone:race", "owner": "expired"}))
    script = """
import json,sys,time
from pathlib import Path
from iot_exp.resources import ResourceLease,ResourceBusyError
base=Path(sys.argv[1]); who=sys.argv[2]
while not (base/'start').exists(): time.sleep(.01)
try:
 with ResourceLease(base/who,['phone:race'],'race:'+who):
  (base/(who+'.json')).write_text(json.dumps({'owned':True}))
  while not (base/'release').exists(): time.sleep(.01)
except ResourceBusyError:
 (base/(who+'.json')).write_text(json.dumps({'owned':False}))
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    processes = [subprocess.Popen([sys.executable, "-c", script, str(tmp_path), name], env=environment)
                 for name in ("one", "two")]
    try:
        (tmp_path / "start").touch()
        deadline = time.monotonic() + 10
        while not all((tmp_path / (name + ".json")).exists() for name in ("one", "two")) and time.monotonic() < deadline:
            time.sleep(0.02)
        outcomes = [json.loads((tmp_path / (name + ".json")).read_text())["owned"] for name in ("one", "two")]
        assert sum(outcomes) == 1
    finally:
        (tmp_path / "release").touch()
        for process in processes:
            process.wait(timeout=5)
