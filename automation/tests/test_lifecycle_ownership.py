import json
import os
import subprocess
import sys
import time

import pytest

from iot_exp.process_identity import process_is_running, process_start_token
from iot_exp.scheduler import TaskScheduler
from iot_exp.task_models import TaskRequest
from iot_exp.task_store import TaskStore


def test_restart_does_not_mark_a_live_old_worker_dead(tmp_path):
    store = TaskStore(tmp_path / "db.sqlite3")
    task = store.create_batch("old-live-task-123", [TaskRequest(template_id="x")], {"x": 2})[0]
    store.update(task["id"], status="running", pid=os.getpid(), process_start_token=process_start_token(os.getpid()))
    store.interrupt_unfinished()
    assert store.get(task["id"])["status"] == "running"


def test_force_stop_checks_identity_and_elapsed_stop_time(tmp_path, monkeypatch):
    store = TaskStore(tmp_path / "db.sqlite3", owner_instance="owner")
    task = store.create_batch("force-test-123", [TaskRequest(template_id="x")], {"x": 2})[0]
    store.update(task["id"], status="stopping", pid=os.getpid(), process_start_token="wrong",
                 stop_requested_at_ns=time.time_ns() - 61_000_000_000)
    scheduler = TaskScheduler(store, tmp_path)
    monkeypatch.setattr("iot_exp.scheduler.subprocess.run", lambda *_a, **_kw: pytest.fail("must not terminate a mismatched process"))
    with pytest.raises(RuntimeError, match="身份"):
        scheduler.force_stop(task["id"])


def test_another_instance_cannot_control_owned_tasks(tmp_path):
    path = tmp_path / "db.sqlite3"
    first = TaskStore(path, owner_instance="first")
    task = first.create_batch("own-task-123", [TaskRequest(template_id="x")], {"x": 2})[0]
    second = TaskStore(path, owner_instance="second")
    assert first.get(task["id"])["controllable"] is True
    assert second.get(task["id"])["controllable"] is False


def test_force_stop_can_terminate_a_verified_owned_process(tmp_path):
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        store = TaskStore(tmp_path / "db.sqlite3", owner_instance="owner")
        task = store.create_batch("owned-force-123", [TaskRequest(template_id="x")], {"x": 2})[0]
        store.update(task["id"], status="stopping", pid=process.pid,
                     process_start_token=process_start_token(process.pid),
                     stop_requested_at_ns=time.time_ns() - 61_000_000_000)
        TaskScheduler(store, tmp_path).force_stop(task["id"])
        process.wait(timeout=5)
        assert store.get(task["id"])["status"] == "interrupted"
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def test_owned_tree_cleanup_excludes_adb_and_its_children(monkeypatch):
    from iot_exp.process_cleanup import terminate_owned_tree
    killed = []

    class Process:
        def __init__(self, pid, name, children=()):
            self.pid, self.label, self.descendants = pid, name, children
        def name(self):
            return self.label
        def children(self, recursive=True):
            return list(self.descendants)
        def kill(self):
            killed.append(self.pid)

    unrelated = Process(999, "child")
    adb = Process(200, "adb.exe", (unrelated,))
    appium = Process(300, "node.exe")
    owner = Process(100, "python.exe", (adb, unrelated, appium))
    monkeypatch.setattr("iot_exp.process_cleanup.psutil.Process", lambda _pid: owner)
    monkeypatch.setattr("iot_exp.process_cleanup.process_is_running", lambda *_: True)
    monkeypatch.setattr("iot_exp.process_cleanup.process_start_token", lambda _pid: "identity")
    terminate_owned_tree(100, "identity")
    assert killed == [300, 100]


def test_drain_waits_for_verified_child_after_worker_has_exited(tmp_path):
    child = subprocess.Popen(
        [sys.executable, "-c", "import signal,time; signal.signal(signal.SIGINT,signal.SIG_IGN); print('ready',flush=True); time.sleep(60)"],
        stdout=subprocess.PIPE,
    )
    try:
        assert child.stdout.readline().strip() == b"ready"
        store = TaskStore(tmp_path / "db.sqlite3", owner_instance="owner")
        task = store.create_batch("orphan-cleanup-123", [TaskRequest(template_id="x")], {"x": 2})[0]
        store.update(task["id"], status="failed", pid=99999999, process_start_token="old",
                     owned_processes_json=[{"role":"capture", "pid":child.pid, "token":process_start_token(child.pid)}])
        scheduler = TaskScheduler(store, tmp_path)
        assert scheduler.active_owned_count() == 1
        scheduler.begin_drain()
        assert store.get(task["id"])["status"] == "stopping"
        with pytest.raises(RuntimeError, match="60 seconds"):
            scheduler.force_stop(task["id"])
        store.update(task["id"], stop_requested_at_ns=time.time_ns()-61_000_000_000)
        scheduler.force_stop(task["id"])
        child.wait(timeout=5)
        assert scheduler.active_owned_count() == 0
        assert store.get(task["id"])["status"] == "interrupted"
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)


def test_worker_crash_stops_verified_appium_child_and_keeps_partial_evidence(tmp_path):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        store = TaskStore(tmp_path / "db.sqlite3", owner_instance="owner")
        task = store.create_batch("worker-crash-123", [TaskRequest(template_id="x")], {"x": 2})[0]
        store.update(task["id"], status="running", pid=99999999, process_start_token="old",
                     owned_processes_json=[{"role":"appium","pid":child.pid,"token":process_start_token(child.pid)}])
        evidence = tmp_path / "partial.pcapng"
        evidence.write_bytes(b"partial-evidence-must-remain")
        scheduler = TaskScheduler(store, tmp_path)
        class DeadWorker:
            def poll(self):
                return 1
        scheduler.processes[task["id"]] = DeadWorker()
        scheduler.tick()
        child.wait(timeout=5)
        scheduler.tick()
        assert store.get(task["id"])["status"] == "interrupted"
        assert evidence.read_bytes() == b"partial-evidence-must-remain"
        assert not scheduler.active_owned_count()
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)


def test_drain_is_idempotent_and_never_dispatches_queued_tasks(tmp_path, monkeypatch):
    store = TaskStore(tmp_path / "db.sqlite3", owner_instance="owner")
    task = store.create_batch("queued-drain-123", [TaskRequest(template_id="x")], {"x": 2})[0]
    scheduler = TaskScheduler(store, tmp_path)
    monkeypatch.setattr(scheduler, "_launch", lambda *_: pytest.fail("drain must not dispatch"))
    scheduler.begin_drain()
    scheduler.begin_drain()
    scheduler.tick()
    assert store.get(task["id"])["status"] == "cancelled"
    assert len(store.logs(task["id"])) == 1


def test_recovery_adopts_dead_workers_orphan_and_releases_exact_lease(tmp_path):
    first = TaskStore(tmp_path / "db.sqlite3", owner_instance="old")
    task = first.create_batch("actual-orphan-recovery", [TaskRequest(template_id="x")], {"x": 2})[0]
    output, locks, ready = tmp_path / "output", tmp_path / "locks", tmp_path / "fixture.json"
    script = """
import json, os, subprocess, sys, time
from pathlib import Path
from iot_exp.resources import ResourceLease
from iot_exp.process_identity import process_start_token
output, locks, ready, task = map(str, sys.argv[1:])
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
owned = [{'role':'appium', 'pid':child.pid, 'token':process_start_token(child.pid)}]
lease = ResourceLease(Path(output), ['phone:self-test'], 'gui:'+task, lock_root=Path(locks))
lease.__enter__()
lease.track_processes(owned)
Path(ready).write_text(json.dumps({'pid':os.getpid(), 'token':process_start_token(os.getpid()), 'owned':owned}))
time.sleep(120)
"""
    environment = {**os.environ, "PYTHONPATH": str(__import__("pathlib").Path(__file__).parents[1] / "src")}
    process = subprocess.Popen([sys.executable, "-c", script, str(output), str(locks), str(ready), task["id"]],
                               env=environment, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    child = None
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            assert process.poll() is None
            time.sleep(0.05)
        value = json.loads(ready.read_text())
        child = value["owned"][0]
        first.update(task["id"], status="running", pid=value["pid"], process_start_token=value["token"],
                     owned_processes_json=value["owned"])
        evidence = output / "partial.pcapng"
        evidence.write_bytes(b"synthetic-partial-evidence")
        # Kill only the self-owned fixture root; leave its recorded child for recovery.
        import psutil

        from iot_exp.process_cleanup import terminate_owned_tree
        psutil.Process(value["pid"]).kill()
        process.wait(timeout=5)
        assert process_is_running(child["pid"], child["token"])
        from iot_exp.paths import PathLayout
        layout = PathLayout.source(tmp_path)
        from dataclasses import replace
        layout = replace(layout, lock_root=locks)
        current = TaskStore(first.path, owner_instance="new")
        scheduler = TaskScheduler(current, tmp_path, layout=layout)
        scheduler.start()
        try:
            deadline = time.monotonic() + 5
            while scheduler.recovering and time.monotonic() < deadline:
                time.sleep(0.05)
            scheduler.tick()
            recovered = current.get(task["id"])
            assert recovered["status"] == "interrupted"
            assert recovered["owner_instance"] == "new"
            assert recovered["metadata"]["recovered_owner_instance"] == "old"
            assert not process_is_running(child["pid"], child["token"])
            assert not list(locks.glob("*.lock")) and not list((output / "locks").glob("*.lock"))
            assert evidence.read_bytes() == b"synthetic-partial-evidence"
            assert current.queued() == [] and len(current.list()) == 1
        finally:
            scheduler.close()
    finally:
        if child and process_is_running(child["pid"], child["token"]):
            from iot_exp.process_cleanup import terminate_owned_tree
            terminate_owned_tree(child["pid"], child["token"])
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def test_alive_worker_is_adopted_only_if_recorded_sidecar_identity_is_dead(tmp_path):
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        old = TaskStore(tmp_path / "db.sqlite3", owner_instance="old")
        task = old.create_batch("parent-proven-recovery", [TaskRequest(template_id="x")], {"x": 2})[0]
        old.update(task["id"], status="running", pid=process.pid, process_start_token=process_start_token(process.pid),
                   parent_pid=os.getpid(), parent_start_token=process_start_token(os.getpid()))
        current = TaskStore(old.path, owner_instance="new")
        assert current.adopt_cleanup_tasks() == []
        assert current.get(task["id"])["owner_instance"] == "old"
        old.update(task["id"], parent_start_token="reused-parent-identity")
        scheduler = TaskScheduler(current, tmp_path)
        scheduler._reconcile_cleanup()
        adopted = current.get(task["id"])
        assert adopted["owner_instance"] == "new" and adopted["status"] == "stopping"
        assert (scheduler.layout.control_root / f"{task['id']}.cancel").is_file()
        assert scheduler.active_owned_count() == 1
        with pytest.raises(RuntimeError, match="60 seconds"):
            scheduler.force_stop(task["id"])
        current.update(task["id"], stop_requested_at_ns=time.time_ns() - 61_000_000_000)
        scheduler.force_stop(task["id"])
        process.wait(timeout=5)
        assert current.get(task["id"])["status"] == "interrupted"
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def test_appium_is_recorded_before_http_readiness(tmp_path, monkeypatch):
    from iot_exp.backends.system import AppiumServer
    actual_spawn = subprocess.Popen
    process = None
    recorded = []
    def spawn(_command, **options):
        nonlocal process
        process = actual_spawn([sys.executable, "-c", "import time; time.sleep(60)"], **options)
        return process
    monkeypatch.setattr("iot_exp.backends.system.subprocess.Popen", spawn)
    server = AppiumServer("own-fixture", "http://127.0.0.1:1", tmp_path / "appium.log",
                          process_callback=lambda child: recorded.append(child.pid))
    def fail_readiness():
        assert recorded == [server.process.pid]
        assert server.process.poll() is None
        raise RuntimeError("fixture HTTP never ready")
    monkeypatch.setattr(server, "_wait_for_http", fail_readiness)
    try:
        with pytest.raises(RuntimeError, match="never ready"):
            server.start()
    finally:
        server.stop()
        if process and process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def test_capture_stop_timeout_keeps_real_child_and_partial_file(tmp_path, monkeypatch):
    from iot_exp.backends.capture import DumpcapCaptureBackend
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    actual_wait, actual_terminate, actual_signal = process.wait, process.terminate, process.send_signal
    evidence = tmp_path / "capture.pcapng"
    evidence.write_bytes(b"synthetic-pcap-prefix")
    capture = DumpcapCaptureBackend("not-executed", "not-used")
    capture.process, capture.output_path = process, evidence
    monkeypatch.setattr(process, "send_signal", lambda _signal: None)
    def timeout(**_options):
        raise subprocess.TimeoutExpired("self-fixture", 15)
    monkeypatch.setattr(process, "wait", timeout)
    monkeypatch.setattr(process, "terminate", lambda: pytest.fail("no implicit force stop"))
    try:
        with pytest.raises(RuntimeError, match="60秒"):
            capture.stop()
        assert capture.process is process and process.poll() is None
        assert evidence.read_bytes() == b"synthetic-pcap-prefix"
    finally:
        monkeypatch.setattr(process, "send_signal", actual_signal)
        actual_terminate()
        actual_wait(timeout=5)


def test_owned_tree_refuses_adb_even_if_ledger_mislabels_root(monkeypatch):
    from iot_exp.process_cleanup import terminate_owned_tree
    class Adb:
        def name(self):
            return "adb.exe"
        def children(self, **_kwargs):
            pytest.fail("shared ADB tree must not be enumerated or killed")
    monkeypatch.setattr("iot_exp.process_cleanup.process_is_running", lambda *_: True)
    monkeypatch.setattr("iot_exp.process_cleanup.psutil.Process", lambda *_: Adb())
    with pytest.raises(RuntimeError, match="ADB"):
        terminate_owned_tree(1234, "fixture-identity")


def test_task_ledger_live_child_prevents_releasing_a_not_yet_updated_lease(tmp_path):
    from iot_exp.resources import release_dead_task_leases
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        locks = tmp_path / "locks"
        locks.mkdir()
        owner = {"schema": 2, "owner": "gui:fixture", "pid": 99999999, "process_start_token": "dead",
                 "lease_id": "own-lease", "output_root": str(tmp_path / "output")}
        path = locks / "own.lock"
        original = json.dumps(owner).encode()
        path.write_bytes(original)
        task = {"id": "fixture", "pid": 99999999, "process_start_token": "dead",
                "owned_processes": [{"role": "capture", "pid": process.pid, "token": process_start_token(process.pid)}]}
        release_dead_task_leases(locks, task)
        assert path.read_bytes() == original
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_unrelated_unreadable_lease_is_preserved_without_blocking_own_cleanup(tmp_path):
    from iot_exp.resources import release_dead_task_leases
    locks = tmp_path / "locks"
    locks.mkdir()
    (locks / "unknown.lock").write_text("{")
    owner = {"schema": 2, "owner": "gui:fixture", "pid": 99999999, "process_start_token": "dead",
             "lease_id": "own-lease", "output_root": str(tmp_path / "output")}
    (locks / "own.lock").write_text(json.dumps(owner))
    task = {"id": "fixture", "pid": 99999999, "process_start_token": "dead", "owned_processes": []}
    release_dead_task_leases(locks, task)
    assert not (locks / "own.lock").exists()
    assert (locks / "unknown.lock").read_text() == "{"


@pytest.mark.parametrize("error_kind", ["NoSuchProcess", "AccessDenied"])
def test_process_cleanup_handles_disappearance_and_permissions(monkeypatch, error_kind):
    import psutil

    from iot_exp.process_cleanup import terminate_owned_tree
    monkeypatch.setattr("iot_exp.process_cleanup.process_is_running", lambda *_: True)
    def lookup(pid):
        raise getattr(psutil, error_kind)(pid)
    monkeypatch.setattr("iot_exp.process_cleanup.psutil.Process", lookup)
    if error_kind == "NoSuchProcess":
        terminate_owned_tree(123, "identity")
    else:
        with pytest.raises(RuntimeError, match="无法访问"):
            terminate_owned_tree(123, "identity")


def test_worker_preserves_recorded_parent_identity_instead_of_resampling_pid(tmp_path, monkeypatch):
    from iot_exp.worker import run_task
    store = TaskStore(tmp_path / "db.sqlite3")
    task = store.create_batch("recorded-parent-123", [TaskRequest(template_id="fixture")], {"fixture": 2})[0]
    store.update(task["id"], parent_pid=os.getpid(), parent_start_token="original-controller-identity")
    def stop_before_experiment(*_args):
        raise ValueError("self fixture stops before any experiment")
    monkeypatch.setattr("iot_exp.worker._apply_request", stop_before_experiment)
    assert run_task(store.path, task["id"], tmp_path, parent_pid=os.getpid()) == 2
    assert store.get(task["id"])["parent_start_token"] == "original-controller-identity"


def test_launch_passes_parent_creation_identity_with_pid(tmp_path, monkeypatch):
    store = TaskStore(tmp_path / "db.sqlite3")
    task = store.create_batch("spawn-parent-identity", [TaskRequest(template_id="fixture")], {"fixture": 2})[0]
    commands = []
    class Child:
        pid = 99999999
    def spawn(command, **_kwargs):
        commands.append(command)
        return Child()
    monkeypatch.setattr("iot_exp.scheduler.subprocess.Popen", spawn)
    TaskScheduler(store, tmp_path)._launch(task, 0)
    assert commands[0][commands[0].index("--parent-start-token") + 1] == process_start_token(os.getpid())


def test_async_child_exit_final_cleanup_releases_exact_dead_leases(tmp_path, monkeypatch):
    from dataclasses import replace

    from iot_exp.paths import PathLayout
    store = TaskStore(tmp_path / "db.sqlite3", owner_instance="owner")
    task = store.create_batch("late-exit-lease-cleanup", [TaskRequest(template_id="fixture")], {"fixture": 2})[0]
    store.update(task["id"], status="stopping", stage="cleanup", pid=99999999,
                 process_start_token="dead-worker", owned_processes_json=[
                     {"role": "appium", "pid": 99999998, "token": "dead-child"},
                 ])
    locks, output = tmp_path / "shared-locks", tmp_path / "output"
    locks.mkdir()
    (output / "locks").mkdir(parents=True)
    owner = {"schema": 2, "owner": "gui:" + task["id"], "pid": 99999999,
             "process_start_token": "dead-worker", "lease_id": "exact-owned-lease", "output_root": str(output)}
    for path in (locks / "own.lock", output / "locks/own.lock"):
        path.write_text(json.dumps(owner))
    scheduler = TaskScheduler(store, tmp_path, layout=replace(PathLayout.source(tmp_path), lock_root=locks))
    # Simulate a child becoming dead after reconcile's earlier live check.
    monkeypatch.setattr(scheduler, "_reconcile_cleanup", lambda: None)
    scheduler.tick()
    assert store.get(task["id"])["status"] == "interrupted"
    assert not list(locks.glob("*.lock"))
    assert not list((output / "locks").glob("*.lock"))
