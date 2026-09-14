"""One EO Ollama call at a time, across API and worker processes."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

from eoa.config import REPO_ROOT, settings
from eoa.errors import ResourceUnavailable
from eoa.execution import checkpoint, sleep
from eoa.resources.gate import _check_local_inference_pause

_thread_lock = threading.Lock()
LOCK_PATH = REPO_ROOT / "runtime" / "local-inference.lock"


@contextmanager
def local_inference_lock(*, interactive: bool = False, role: str | None = None) -> Iterator[None]:
    """Hold an OS file lock for admission AND inference; a process crash releases it."""
    rc = settings().resources
    end = time.monotonic() + (rc.interactive_wait_s if interactive else rc.queue_timeout_min * 60)

    def check() -> None:
        checkpoint()
        _check_local_inference_pause(role)
        if time.monotonic() >= end:
            raise ResourceUnavailable("Another EO local inference call is still running")

    check()
    while not _thread_lock.acquire(blocking=False):
        check()
        sleep(0.1)
    try:
        LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOCK_PATH.open("a+b") as handle:
            if handle.seek(0, 2) == 0:
                handle.write(b"0")
                handle.flush()
            while True:
                check()
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except (BlockingIOError, PermissionError):
                    sleep(0.1)
            try:
                check()
                yield
                checkpoint()
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        _thread_lock.release()
