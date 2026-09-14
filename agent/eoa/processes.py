"""Run owned CLI process trees with bounded lifetime."""

from __future__ import annotations

import os
import signal
import subprocess
from typing import Any


def run_process(args: list[str], *, input: str | None = None, capture_output: bool = False,
                timeout: float | None = None, **kwargs: Any) -> subprocess.CompletedProcess:
    if input is not None:
        kwargs["stdin"] = subprocess.PIPE
    if capture_output:
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if os.name != "nt":
        kwargs["start_new_session"] = True
    with subprocess.Popen(args, **kwargs) as proc:
        try:
            stdout, stderr = proc.communicate(input=input, timeout=timeout)
        except BaseException:
            if proc.poll() is None:
                try:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                       capture_output=True, timeout=10, creationflags=0x08000000)
                    else:
                        os.killpg(proc.pid, signal.SIGKILL)
                finally:
                    if proc.poll() is None:
                        proc.kill()
                    proc.wait(timeout=10)
            raise
        return subprocess.CompletedProcess(args, proc.returncode, stdout, stderr)
