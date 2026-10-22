"""Time sources, injectable for tests (clock jumps, sleep/resume)."""
from __future__ import annotations

import time


class Clock:
    def now_ms(self) -> int:
        """Wall clock, UTC epoch ms: what gets stored."""
        return int(time.time() * 1000)

    def monotonic(self) -> float:
        """Never jumps backwards; used to detect gaps (sleep, suspended process)."""
        return time.monotonic()


class FakeClock(Clock):
    def __init__(self, now_ms: int = 1_800_000_000_000):
        self._now = now_ms
        self._mono = 1000.0

    def now_ms(self) -> int:
        return self._now

    def monotonic(self) -> float:
        return self._mono

    def advance(self, seconds: float, *, wall_too: bool = True) -> None:
        self._mono += seconds
        if wall_too:
            self._now += int(seconds * 1000)

    def jump_wall(self, seconds: float) -> None:
        """Wall clock moves (NTP correction, resume) without monotonic time passing."""
        self._now += int(seconds * 1000)
