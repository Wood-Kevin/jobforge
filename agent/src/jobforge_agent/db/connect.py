"""The only way agent code opens the database (ARCHITECTURE 3.4, 4.3).

Every connection belongs to a component. The component's authorizer is the SQLite stand-in
for the Postgres role grants of earlier drafts: it allows writes only to the tables (and for
UPDATE, the columns) that component owns. Anything else fails with
`sqlite3.DatabaseError: not authorized`.

Honest limit: this defends against bugs and against model output steering a code path. It
does not defend against other software running as the same Windows user, which could open
the file directly.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

ALL_COLUMNS = None  # marker for "any column" in an update grant


@dataclass(frozen=True)
class Grant:
    insert: bool = False
    update: frozenset[str] | None | bool = False  # False: no UPDATE; ALL_COLUMNS: any; frozenset: those columns
    delete: bool = False


def ins() -> Grant:
    return Grant(insert=True)


def upd(*cols: str) -> Grant:
    return Grant(update=frozenset(cols))


def full(delete: bool = False) -> Grant:
    return Grant(insert=True, update=ALL_COLUMNS, delete=delete)


def _merge(*grants: Grant) -> Grant:
    insert = any(g.insert for g in grants)
    delete = any(g.delete for g in grants)
    updates = [g.update for g in grants if g.update is not False]
    if not updates:
        update: frozenset[str] | None | bool = False
    elif any(u is ALL_COLUMNS for u in updates):
        update = ALL_COLUMNS
    else:
        update = frozenset().union(*updates)
    return Grant(insert=insert, update=update, delete=delete)


# Write allow-lists per component (SPEC section 4). Reads are allowed everywhere.
GRANTS: dict[str, dict[str, Grant]] = {
    # Polling, change detection, filtering, queueing analyses. Never touches personal profile data.
    "discovery": {
        "sources": upd("backoff_until_ms", "backoff_reason"),
        "employers": upd("status"),
        "employer_poll_state": full(),
        "robots_cache": full(),
        "source_fetches": full(),
        "raw_postings": ins(),
        "jobs": full(),
        "change_events": ins(),
        "filter_results": ins(),
        "posting_analyses": _merge(ins(), upd("status")),
    },
    # Model calls and code-computed assessments. May snapshot the profile, never change it.
    "evaluation": {
        "posting_analyses": full(),
        "requirements": ins(),
        "evaluations": full(),
        "requirement_matches": ins(),
        "match_citations": ins(),
        "recommendations": ins(),
        "inferred_claims": ins(),
        "profile_snapshots": ins(),
        "profile_snapshot_items": ins(),
        "change_events": ins(),
    },
    # Explicit user commands arriving over IPC. The only writer of the profile and applications.
    "user": {
        "settings": full(),
        "sources": full(),
        "employers": full(),
        "employer_poll_state": _merge(ins(), upd("next_poll_at_ms")),
        "search_profiles": ins(),
        "filter_overrides": ins(),
        "resumes": full(delete=True),
        "experience_items": full(delete=True),
        "profile_completeness": full(),
        "inferred_claims": upd("state", "accepted_as_item_id"),
        "profile_snapshots": ins(),
        "profile_snapshot_items": ins(),
        "model_configs": full(),
        "evaluations": ins(),
        "applications": full(),
        "application_events": ins(),
        "change_events": ins(),
    },
    # Agent lifecycle: run history, startup recovery, notification-log pruning.
    "lifecycle": {
        "agent_runs": full(),
        "source_fetches": upd("finished_at_ms", "outcome", "error"),
        "employer_poll_state": upd("lease_until_ms"),
        "posting_analyses": upd("status", "attempts", "lease_until_ms", "last_error"),
        "evaluations": upd("status", "attempts", "lease_until_ms", "last_error"),
        "change_events": Grant(delete=True),
    },
    # Read-only users (status queries, backups).
    "readonly": {},
}

_DDL = {
    sqlite3.SQLITE_CREATE_INDEX, sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_CREATE_TEMP_INDEX,
    sqlite3.SQLITE_CREATE_TEMP_TABLE, sqlite3.SQLITE_CREATE_TEMP_TRIGGER, sqlite3.SQLITE_CREATE_TEMP_VIEW,
    sqlite3.SQLITE_CREATE_TRIGGER, sqlite3.SQLITE_CREATE_VIEW, sqlite3.SQLITE_DROP_INDEX, sqlite3.SQLITE_DROP_TABLE,
    sqlite3.SQLITE_DROP_TEMP_INDEX, sqlite3.SQLITE_DROP_TEMP_TABLE, sqlite3.SQLITE_DROP_TEMP_TRIGGER,
    sqlite3.SQLITE_DROP_TEMP_VIEW, sqlite3.SQLITE_DROP_TRIGGER, sqlite3.SQLITE_DROP_VIEW, sqlite3.SQLITE_ALTER_TABLE,
    sqlite3.SQLITE_CREATE_VTABLE, sqlite3.SQLITE_DROP_VTABLE, sqlite3.SQLITE_REINDEX, sqlite3.SQLITE_ANALYZE,
    sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH,
}

EXPECTED_PRAGMAS = {"journal_mode": "wal", "synchronous": 2, "foreign_keys": 1}


class AgentConnection(sqlite3.Connection):
    """sqlite3.Connection that knows which component it belongs to."""
    component: str = "unknown"
    authorizer_log: "AuthorizerLog | None" = None


@dataclass
class AuthorizerLog:
    """Denials seen on a connection; tests and diagnostics read it."""
    denied: list[tuple[str, str, str | None]] = field(default_factory=list)


def make_authorizer(component: str, log: AuthorizerLog):
    grants = GRANTS[component]

    def authorizer(action: int, arg1, arg2, _db, _source) -> int:
        if action in _DDL:
            log.denied.append(("ddl", str(arg1), arg2))
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_PRAGMA:
            # Reading a pragma is fine; setting one is reserved for open_connection.
            if arg2 is not None:
                log.denied.append(("pragma", str(arg1), arg2))
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_INSERT:
            g = grants.get(arg1)
            if g is None or not g.insert:
                log.denied.append(("insert", arg1, None))
                return sqlite3.SQLITE_DENY
        elif action == sqlite3.SQLITE_DELETE:
            g = grants.get(arg1)
            if g is None or not g.delete:
                log.denied.append(("delete", arg1, None))
                return sqlite3.SQLITE_DENY
        elif action == sqlite3.SQLITE_UPDATE:
            g = grants.get(arg1)
            if g is None or g.update is False or (g.update is not ALL_COLUMNS and arg2 not in g.update):
                log.denied.append(("update", arg1, arg2))
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    return authorizer


def open_connection(db_path: Path, component: str, *, readonly: bool = False) -> AgentConnection:
    """Open a connection for one component.

    component "migration" gets no authorizer and is used only by the migration runner and
    restore. All others get pragmas set and verified, then the component's authorizer.
    Connections stay bound to the creating thread (sqlite3's default): DB work runs on the
    event-loop thread (ARCHITECTURE 3.4).
    """
    if component != "migration" and component not in GRANTS:
        raise ValueError(f"unknown component {component!r}")
    if readonly:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, isolation_level=None, timeout=5, factory=AgentConnection)
    else:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, isolation_level=None, timeout=5, factory=AgentConnection)
    conn.component = component
    conn.row_factory = sqlite3.Row
    if not readonly:
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    if readonly:
        conn.execute("PRAGMA query_only=ON")
    actual = pragma_values(conn)
    expected = dict(EXPECTED_PRAGMAS)
    if readonly:
        expected.pop("journal_mode")  # a read-only connection reports the file's mode, set by the writer
    mismatched = {k: (actual[k], v) for k, v in expected.items() if actual[k] != v}
    if mismatched:
        conn.close()
        raise RuntimeError(f"connection pragmas not as required: {mismatched}")
    if component != "migration":
        log = AuthorizerLog()
        conn.set_authorizer(make_authorizer(component, log))
        conn.authorizer_log = log
    return conn


def pragma_values(conn: sqlite3.Connection) -> dict:
    return {
        "journal_mode": conn.execute("PRAGMA journal_mode").fetchone()[0],
        "synchronous": conn.execute("PRAGMA synchronous").fetchone()[0],
        "foreign_keys": conn.execute("PRAGMA foreign_keys").fetchone()[0],
    }
