"""Which model config each model step uses (SPEC D18, ARCHITECTURE 6.9; migration 0011).

Steps: 'e1' (posting reading), 'e3' (matching), 'resume' (resume suggestions). Each has a settings row
`model.<step>`; `model.fallback` is the config a step uses while its own config is a cloud config that is
capped or unavailable.

"Is it out" lives in memory, in `OUT` (config id -> reason): the health gate (analysis/worker.HealthGate) is the
only writer, and it clears it when created, so after a restart every config counts as available until a probe or
a call says otherwise. A process-wide value rather than a parameter, because every reader of "the current
recommendation" (store, views, golden) needs the same answer the worker queued by; tests reset it (conftest).
"""
from __future__ import annotations

import sqlite3
from typing import Callable

STEPS = ("e1", "e3", "resume")
FALLBACK = "fallback"
KEYS = {s: f"model.{s}" for s in (*STEPS, FALLBACK)}

IsOut = Callable[[int], bool]
OUT: dict[int, str] = {}


def gate_says_out(config_id: int) -> bool:
    return config_id in OUT


def configured(conn: sqlite3.Connection, step: str) -> int | None:
    """The config the person chose for a step (or the fallback, for step 'fallback')."""
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (KEYS[step],)).fetchone()
    return int(row[0]) if row and row[0].isdigit() else None


def is_cloud(conn: sqlite3.Connection, config_id: int | None) -> bool:
    if config_id is None:
        return False
    row = conn.execute("SELECT provider_kind FROM model_configs WHERE id = ?", (config_id,)).fetchone()
    return row is not None and row[0] != "openai_compatible"


def active(conn: sqlite3.Connection, step: str, is_out: IsOut = gate_says_out) -> int | None:
    """The config a step runs on now: its own, or the fallback while its own is a cloud config that is out.
    Only a cloud config falls back: a local config that is down simply waits (as before D18)."""
    own = configured(conn, step)
    if own is None or not is_out(own) or not is_cloud(conn, own):
        return own
    fb = configured(conn, FALLBACK)
    return fb if fb is not None else own


def set_step(conn: sqlite3.Connection, step: str, config_id: int) -> None:
    """User action (caller holds the transaction). The config must exist and be enabled."""
    if step not in KEYS:
        raise ValueError(f"unknown model step {step!r}")
    row = conn.execute("SELECT enabled, provider_kind FROM model_configs WHERE id = ?", (config_id,)).fetchone()
    if row is None:
        raise LookupError(config_id)
    if not row[0]:
        raise ValueError("turn the model on (test it) before using it for a step")
    if step == FALLBACK and row[1] != "openai_compatible":
        raise ValueError("the fallback must be a model on this PC")
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                 (KEYS[step], str(config_id)))


def listing(conn: sqlite3.Connection) -> dict[str, int | None]:
    return {s: configured(conn, s) for s in KEYS}
