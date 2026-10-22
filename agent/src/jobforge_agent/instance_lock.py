"""One agent per user (SPEC D5).

A byte-range lock on run\\agent.lock. Windows releases it when the owning process dies, so a
crashed agent never leaves a stale lock behind, and startup recovery may treat every
'running' row as an orphan.
"""
from __future__ import annotations

import msvcrt
from pathlib import Path
from typing import BinaryIO


class InstanceLock:
    def __init__(self, path: Path):
        self.path = path
        self._fh: BinaryIO | None = None

    @property
    def held(self) -> bool:
        return self._fh is not None

    def try_acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+b")
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            fh.close()
            return False
        self._fh = fh
        return True

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            self._fh.seek(0)
            msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self._fh.close()
            self._fh = None

    def __enter__(self) -> "InstanceLock":
        return self

    def __exit__(self, *exc) -> None:
        self.release()
