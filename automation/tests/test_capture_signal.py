import io
import os
import signal

import pytest

from iot_exp.backends.capture import DumpcapCaptureBackend


@pytest.mark.skipif(os.name != "nt", reason="Windows console-control constant regression")
def test_windows_capture_stop_uses_signal_constant_and_flushes_without_force(tmp_path):
    signals = []
    class OwnedCapture:
        returncode = None
        stderr = io.StringIO("")
        def poll(self):
            return self.returncode
        def send_signal(self, value):
            signals.append(value)
        def wait(self, timeout):
            self.returncode = 0
    capture = DumpcapCaptureBackend("not-executed", "fixture")
    capture.process = OwnedCapture()
    capture.output_path = tmp_path / "fixture.pcapng"
    result = capture.stop()
    assert signals == [signal.CTRL_BREAK_EVENT]
    assert result.return_code == 0
    assert capture.process is None
