"""Posting text: compressed, and kept only while wanted (migration 0010, P1.10).

At built-in-list scale (thousands of boards, about 200k open jobs) plain posting text made the
database grow by about 30 KB per posting. The description of a posting version lives in
posting_texts, zlib-compressed, and is deleted when the search profile rejects the posting
(discovery, filtering/pipeline.py). SQL reads it with the unpack_text function, which
open_connection registers on every connection:
    LEFT JOIN posting_texts pt ON pt.raw_posting_id = rp.id
    coalesce(rp.description_text, unpack_text(pt.description_text_z))
Rows written before 0010 keep their plain text in raw_postings, so the coalesce reads both.
"""
from __future__ import annotations

import sqlite3
import zlib

SQL_FUNCTION = "unpack_text"

# SQL fragments shared by every reader of posting text (joins on `rp`, the raw_postings alias).
JOIN = "LEFT JOIN posting_texts pt ON pt.raw_posting_id = rp.id"
TEXT = "coalesce(rp.description_text, unpack_text(pt.description_text_z))"
HTML = "coalesce(rp.description_raw, unpack_text(pt.description_raw_z))"
KEPT = "(rp.description_text IS NOT NULL OR pt.raw_posting_id IS NOT NULL)"


def pack(text: str | None) -> bytes | None:
    return None if text is None else zlib.compress(text.encode("utf-8"), 6)


def unpack(blob: bytes | None) -> str | None:
    return None if blob is None else zlib.decompress(blob).decode("utf-8")


def register(conn: sqlite3.Connection) -> None:
    conn.create_function(SQL_FUNCTION, 1, unpack, deterministic=True)


def store(conn: sqlite3.Connection, raw_id: int, description_raw: str | None, description_text: str | None) -> None:
    """Keep a posting version's text (no-op if it is already kept)."""
    conn.execute("INSERT INTO posting_texts (raw_posting_id, description_raw_z, description_text_z) VALUES (?, ?, ?) "
                 "ON CONFLICT (raw_posting_id) DO NOTHING", (raw_id, pack(description_raw), pack(description_text)))


def drop(conn: sqlite3.Connection, raw_id: int) -> int:
    return conn.execute("DELETE FROM posting_texts WHERE raw_posting_id = ?", (raw_id,)).rowcount
