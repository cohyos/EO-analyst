"""Real lightweight subprocess checks; no model, search or GPU work."""

import ctypes
import os
import subprocess
import sys

import pytest

from eoa.processes import run_process
from eoa.resources import inference


def test_inference_lock_excludes_another_process(tmp_path):
    script = '''
import sys
from pathlib import Path
from types import SimpleNamespace
from eoa.resources import inference, gate
from eoa.errors import ResourceUnavailable
inference.LOCK_PATH = Path(sys.argv[1])
gate.LOCAL_INFERENCE_PAUSE_FILE = Path(sys.argv[2])
inference.settings = lambda: SimpleNamespace(resources=SimpleNamespace(interactive_wait_s=0.2))
try:
    with inference.local_inference_lock(interactive=True):
        print('ACQUIRED')
except ResourceUnavailable:
    print('BUSY')
'''
    args = [sys.executable, "-c", script, str(inference.LOCK_PATH), str(tmp_path / "absent.pause")]
    with inference.local_inference_lock():
        result = run_process(args, capture_output=True, text=True, timeout=10,
                             creationflags=0x08000000 if os.name == "nt" else 0)
        assert result.stdout.strip() == "BUSY", result.stderr
    result = run_process(args, capture_output=True, text=True, timeout=10,
                         creationflags=0x08000000 if os.name == "nt" else 0)
    assert result.stdout.strip() == "ACQUIRED", result.stderr


@pytest.mark.skipif(os.name != "nt", reason="Windows process tree contract")
def test_timed_out_cli_stops_its_descendant(tmp_path):
    pidfile = tmp_path / "child.pid"
    script = '''
import subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
Path(sys.argv[1]).write_text(str(child.pid))
time.sleep(60)
'''
    with pytest.raises(subprocess.TimeoutExpired):
        run_process([sys.executable, "-c", script, str(pidfile)], capture_output=True,
                    text=True, timeout=2, creationflags=0x08000000)
    assert pidfile.exists(), "parent did not start the descendant during the test"
    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x00100000, False, int(pidfile.read_text()))
    if handle:
        try:
            assert kernel.WaitForSingleObject(handle, 2000) == 0, "CLI descendant survived timeout"
        finally:
            kernel.CloseHandle(handle)
