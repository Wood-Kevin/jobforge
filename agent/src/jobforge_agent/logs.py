"""Structured JSON logging with redaction (ARCHITECTURE 8).

Logs carry ids, counts, hashes, durations and status codes. Anything that could hold
personal data or secrets (resume text, experience statements, eligibility facts, prompts,
model output, the IPC token) is replaced by a marker before it reaches a handler, whatever
the call site passes.
"""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path
from typing import Any

import structlog

REDACTED = "[redacted]"
SENSITIVE_KEYS = frozenset({
    "token", "authorization", "password", "secret",
    "resume_text", "extracted_text", "original_bytes",
    "statement", "attributes", "evidence", "notes", "note",
    "prompt", "messages", "response", "raw_response", "content", "reasoning_content",
    "description", "description_raw", "description_text", "payload",
    "jd_quote", "text", "sentence", "bridge", "transfer_limits", "rationale",
    "email", "phone",
})


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: (REDACTED if k in SENSITIVE_KEYS else _redact_value(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value(v) for v in value]
    return value


def redact(_logger, _method, event_dict: dict) -> dict:
    for key in list(event_dict):
        if key in SENSITIVE_KEYS:
            event_dict[key] = REDACTED
        else:
            event_dict[key] = _redact_value(event_dict[key])
    return event_dict


def configure(log_dir: Path, *, filename: str = "agent.jsonl") -> Path:
    """Send structlog events as JSON lines to a rotating file (10 MB x 5)."""
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / filename
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger("jobforge")
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    root.propagate = False
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    return path


def get(name: str = "agent"):
    return structlog.get_logger(f"jobforge.{name}")


def close() -> None:
    root = logging.getLogger("jobforge")
    for h in list(root.handlers):
        h.flush()
        root.removeHandler(h)
        h.close()
