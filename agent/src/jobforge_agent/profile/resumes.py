"""Resumes (ARCHITECTURE A6, Q7): original bytes kept exactly, text extracted for display and matching.

Formats: Markdown, plain text, DOCX (standard library: the document XML inside the zip) and PDF
(pypdf). A file whose text cannot be extracted is still stored; `extracted_text` is NULL and the
caller is told why, so a scanned PDF is visible as "no text", never silently empty.
"""
from __future__ import annotations

import hashlib
import io
import sqlite3
import zipfile
from pathlib import PurePath
from xml.etree import ElementTree

MAX_BYTES = 10 * 1024 * 1024
MIME = {
    ".md": "text/markdown", ".markdown": "text/markdown", ".txt": "text/plain",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pdf": "application/pdf",
}
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class ResumeError(ValueError):
    """Rejected upload (unsupported type, empty, too large); nothing is stored."""


def _docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = ElementTree.fromstring(z.read("word/document.xml"))
    lines = []
    for p in root.iter(f"{_W}p"):
        parts = []
        for el in p.iter():
            if el.tag == f"{_W}t" and el.text:
                parts.append(el.text)
            elif el.tag == f"{_W}tab":
                parts.append("\t")
            elif el.tag in (f"{_W}br", f"{_W}cr"):
                parts.append("\n")
        lines.append("".join(parts))
    return "\n".join(lines)


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader  # imported on use: only PDF uploads pay for it

    return "\n".join((page.extract_text() or "") for page in PdfReader(io.BytesIO(data)).pages)


def _plain_text(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")  # Notepad's legacy default on Windows


def extract_text(ext: str, data: bytes) -> tuple[str | None, str | None]:
    """(text, error). Text is None when nothing usable came out."""
    try:
        if ext == ".docx":
            text = _docx_text(data)
        elif ext == ".pdf":
            text = _pdf_text(data)
        else:
            text = _plain_text(data)
    except Exception as exc:  # any parser failure: keep the original, report the reason
        return None, f"{type(exc).__name__}: {exc}"[:300]
    text = text.replace("\r\n", "\n").strip()
    if not text:
        return None, "no text found (scanned image PDF or empty document?)"
    return text, None


def store(conn: sqlite3.Connection, filename: str, label: str | None, data: bytes, now: int) -> dict:
    """Insert one resume. The caller holds the user-action transaction."""
    name = PurePath(filename or "").name
    ext = PurePath(name).suffix.lower()
    if ext not in MIME:
        raise ResumeError(f"unsupported file type {ext or '(none)'}: use {', '.join(sorted(MIME))}")
    if not data:
        raise ResumeError("empty file")
    if len(data) > MAX_BYTES:
        raise ResumeError(f"file is {len(data)} bytes; the limit is {MAX_BYTES}")
    text, error = extract_text(ext, data)
    sha = hashlib.sha256(data).hexdigest()
    resume_id = conn.execute(
        "INSERT INTO resumes (label, original_filename, original_bytes, mime, extracted_text, sha256, uploaded_at_ms) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id",
        (label or PurePath(name).stem, name, data, MIME[ext], text, sha, now)).fetchone()[0]
    return {"id": resume_id, "sha256": sha, "bytes": len(data), "mime": MIME[ext],
            "text_chars": len(text) if text else 0, "extraction_error": error}


def listing(conn: sqlite3.Connection) -> list[dict]:
    return [{"id": r[0], "label": r[1], "original_filename": r[2], "mime": r[3], "sha256": r[4], "bytes": r[5],
             "text_chars": r[6], "uploaded_at_ms": r[7]}
            for r in conn.execute("SELECT id, label, original_filename, mime, sha256, length(original_bytes), "
                                  "coalesce(length(extracted_text), 0), uploaded_at_ms FROM resumes ORDER BY id")]


def get(conn: sqlite3.Connection, resume_id: int, *, original: bool = False) -> dict | None:
    row = conn.execute("SELECT id, label, original_filename, mime, sha256, extracted_text, original_bytes, uploaded_at_ms "
                       "FROM resumes WHERE id = ?", (resume_id,)).fetchone()
    if row is None:
        return None
    out = {"id": row[0], "label": row[1], "original_filename": row[2], "mime": row[3], "sha256": row[4],
           "extracted_text": row[5], "uploaded_at_ms": row[7]}
    if original:
        out["original_bytes"] = bytes(row[6])
    return out


def delete(conn: sqlite3.Connection, resume_id: int) -> bool:
    return conn.execute("DELETE FROM resumes WHERE id = ?", (resume_id,)).rowcount == 1
