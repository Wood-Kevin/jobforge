"""The only code that talks to an inference server (ARCHITECTURE 6.8).

One OpenAI-compatible client (`/v1/models`, `/v1/chat/completions`); providers are
`model_configs` rows. Every call sends `max_tokens`. Failures are classified so the caller can
decide what an attempt costs:
- unavailable: nothing answered (server stopped). The analysis goes back to pending, no attempt
  is spent, and the health gate stops claims until the server answers again.
- timeout / http_error: the server answered badly; one attempt is spent.
- context_overflow: the posting plus prompt does not fit the loaded context. LM Studio answers
  HTTP 400 `exceed_context_size_error` (measured in P1.6 with a 18,216-token prompt against a
  16,384-token load); it does not truncate silently. Retrying cannot help.
Nothing here logs prompts or responses (ARCHITECTURE 8).
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass

import httpx

DEFAULT_TIMEOUT_S = 120.0
PROBE_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class ModelConfig:
    id: int
    name: str
    base_url: str
    model_id: str
    params: dict
    structured_output_mode: str
    enabled: bool
    tested_at_ms: int | None

    @property
    def timeout_s(self) -> float:
        return float(self.params.get("timeout_s", DEFAULT_TIMEOUT_S))

    @property
    def max_tokens(self) -> int:
        return int(self.params.get("max_tokens", 4000))


def load_config(conn: sqlite3.Connection, config_id: int) -> ModelConfig | None:
    r = conn.execute("SELECT id, name, base_url, model_id, params, structured_output_mode, enabled, tested_at_ms "
                     "FROM model_configs WHERE id = ?", (config_id,)).fetchone()
    if r is None:
        return None
    return ModelConfig(r[0], r[1], r[2].rstrip("/"), r[3], json.loads(r[4]), r[5], bool(r[6]), r[7])


class InferenceError(Exception):
    def __init__(self, kind: str, detail: str):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


@dataclass
class ChatResult:
    content: str
    finish_reason: str | None
    usage: dict | None
    model_reported: str | None
    seconds: float


def request_body(config: ModelConfig, messages: list[dict], response_format: dict | None) -> dict:
    p = config.params
    body = {"model": config.model_id, "messages": messages, "max_tokens": config.max_tokens,
            "temperature": p.get("temperature", 0)}
    if "seed" in p:
        body["seed"] = p["seed"]
    # Reasoning control is per config (spike P1.0b: off by default).
    if "reasoning_effort" in p:
        body["reasoning_effort"] = p["reasoning_effort"]
    body.update(p.get("reasoning_args", {}))
    if response_format is not None:
        body["response_format"] = response_format
    return body


class InferenceClient:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self._http = httpx.AsyncClient(transport=transport)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def list_models(self, base_url: str) -> list[str]:
        try:
            r = await self._http.get(f"{base_url.rstrip('/')}/models", timeout=PROBE_TIMEOUT_S)
        except httpx.TransportError as exc:
            raise InferenceError("unavailable", type(exc).__name__) from None
        if r.status_code != 200:
            raise InferenceError("unavailable", f"HTTP {r.status_code} from /models")
        try:
            return [m["id"] for m in r.json().get("data", [])]
        except (ValueError, KeyError, TypeError, AttributeError):
            raise InferenceError("unavailable", "unreadable /models response") from None

    async def chat(self, config: ModelConfig, messages: list[dict], response_format: dict | None) -> ChatResult:
        body = request_body(config, messages, response_format)
        t0 = time.perf_counter()
        try:
            r = await self._http.post(f"{config.base_url}/chat/completions", json=body, timeout=config.timeout_s)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise InferenceError("unavailable", type(exc).__name__) from None
        except httpx.TimeoutException:
            raise InferenceError("timeout", f"no answer within {config.timeout_s:.0f} s") from None
        except httpx.TransportError as exc:
            # Connection dropped mid-call (server stopped while generating).
            raise InferenceError("unavailable", type(exc).__name__) from None
        seconds = round(time.perf_counter() - t0, 2)
        if r.status_code != 200:
            text = r.text[:500]
            if r.status_code == 400 and ("exceed_context" in text or "context length" in text or "context size" in text):
                raise InferenceError("context_overflow", text)
            raise InferenceError("http_error", f"HTTP {r.status_code}: {text}")
        try:
            resp = r.json()
            choice = resp["choices"][0]
            content = choice["message"].get("content") or ""
        except (ValueError, KeyError, IndexError, TypeError):
            raise InferenceError("http_error", f"unreadable completion: {r.text[:300]}") from None
        return ChatResult(content, choice.get("finish_reason"), resp.get("usage"), resp.get("model"), seconds)
