import pytest


@pytest.fixture(autouse=True)
def isolated_device_lock_space(tmp_path, monkeypatch):
    """Tests share locks with their subprocesses, never with laboratory hardware."""
    monkeypatch.setenv("IOT_EXP_LOCK_ROOT", str(tmp_path / "shared-locks"))
    monkeypatch.delenv("IOT_EXP_CONTEXT", raising=False)
