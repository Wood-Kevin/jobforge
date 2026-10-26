from __future__ import annotations

import json

import pytest

from jobforge_agent.db import migrate
from jobforge_agent.db.connect import open_connection
from jobforge_agent.paths import Paths

NOW = 1_800_000_000_000  # fixed epoch ms for seeded rows


@pytest.fixture(autouse=True)
def no_built_in_list(monkeypatch):
    """Agents started by tests skip the built-in company list (thousands of real boards, P1.10).
    tests/test_directory.py turns it back on with a small list of its own."""
    monkeypatch.setenv("JOBFORGE_BUILT_IN_LIST", "0")


@pytest.fixture(autouse=True)
def no_config_out():
    """inference/steps.OUT is process-wide (the health gate's view of which configs are out): each test starts
    with none, as an agent does."""
    from jobforge_agent.inference import steps
    steps.OUT.clear()
    yield
    steps.OUT.clear()


@pytest.fixture
def paths(tmp_path) -> Paths:
    p = Paths(tmp_path / "jf")
    p.ensure()
    return p


@pytest.fixture
def db(paths):
    """A migrated database; yields an owner ('migration') connection with no authorizer."""
    conn = open_connection(paths.db, "migration")
    migrate.migrate(conn, paths.db, paths.backups)
    yield conn
    conn.close()


def seed_basics(conn) -> dict:
    """Minimal rows that most tables reference. Uses the owner connection."""
    ids = {}
    # Migration 0002 seeds the three reviewed sources.
    ids["source"] = conn.execute("SELECT id FROM sources WHERE kind = 'greenhouse'").fetchone()[0]
    ids["employer"] = conn.execute(
        "INSERT INTO employers (source_id, slug, display_name) VALUES (?, 'acme', 'Acme') RETURNING id", (ids["source"],)
    ).fetchone()[0]
    ids["raw"] = conn.execute(
        # Plain text as written before migration 0010 (adapters always stored a string, possibly empty).
        "INSERT INTO raw_postings (employer_id, source_job_id, fetched_at_ms, payload, description_text, payload_sha256, material_sha256, adapter_version) "
        "VALUES (?, 'greenhouse:acme:1', ?, ?, '', 'p1', 'm1', 'gh-1') RETURNING id",
        (ids["employer"], NOW, json.dumps({"id": 1})),
    ).fetchone()[0]
    ids["job"] = conn.execute(
        "INSERT INTO jobs (source_job_id, current_raw_posting_id, last_payload_sha256, title, company, first_seen_at_ms, last_seen_at_ms) "
        "VALUES ('greenhouse:acme:1', ?, 'p1', 'NOC Engineer', 'Acme', ?, ?) RETURNING id",
        (ids["raw"], NOW, NOW),
    ).fetchone()[0]
    ids["model"] = conn.execute(
        "INSERT INTO model_configs (name, provider_label, base_url, model_id) VALUES ('gemma', 'LM Studio', 'http://127.0.0.1:1234/v1', 'google/gemma-4-12b-qat') RETURNING id"
    ).fetchone()[0]
    ids["item"] = conn.execute(
        "INSERT INTO experience_items (kind, statement, source) VALUES ('cert', 'CCNA', 'user') RETURNING id"
    ).fetchone()[0]
    return ids


def add_analysis(conn, ids, *, raw_id=None, status="pending", attempts=0, version="e1-1") -> int:
    return conn.execute(
        "INSERT INTO posting_analyses (raw_posting_id, job_id, model_config_id, extraction_prompt_version, segmenter_version, "
        "interpreter_version, status, attempts, created_at_ms) VALUES (?, ?, ?, ?, 's1', 'i1', ?, ?, ?) RETURNING id",
        (raw_id or ids["raw"], ids["job"], ids["model"], version, status, attempts, NOW),
    ).fetchone()[0]
