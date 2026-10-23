"""Posting text stored compressed (migration 0010, P1.10).

At built-in-list scale (thousands of boards, about 200k open jobs) plain posting text made the
database grow by about 30 KB per posting. New raw_postings rows store the description zlib-
compressed in description_raw_z / description_text_z. SQL reads it back with the unpack_text
function, which open_connection registers on every connection:
    coalesce(rp.description_text, unpack_text(rp.description_text_z))
Rows written before 0010 keep their plain text, so the coalesce reads both.
"""
from __future__ import annotations

import sqlite3
import zlib

SQL_FUNCTION = "unpack_text"


def pack(text: str | None) -> bytes | None:
    return None if text is None else zlib.compress(text.encode("utf-8"), 6)


def unpack(blob: bytes | None) -> str | None:
    return None if blob is None else zlib.decompress(blob).decode("utf-8")


def register(conn: sqlite3.Connection) -> None:
    conn.create_function(SQL_FUNCTION, 1, unpack, deterministic=True)
