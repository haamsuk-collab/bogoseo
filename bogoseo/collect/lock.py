"""Exclusive non-blocking run lock so only one collector runs at a time."""
from __future__ import annotations

import fcntl
from contextlib import contextmanager
from pathlib import Path


class RunLocked(Exception):
    """Another collect run holds the lock."""


@contextmanager
def run_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RunLocked(str(path)) from None
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
