"""The only code that talks to an inference server (ARCHITECTURE 6.8, 6.9).

Providers are `model_configs` rows, and `provider_kind` picks the request format:
- openai_compatible: `/v1/models`, `/v1/chat/completions` (LM Studio).
- chatgpt: the person's ChatGPT plan through Sign in with ChatGPT (SPEC D18): `/v1/models` answers `models[]`
  with `slug`, and calls go to the Responses API (`/v1/responses`, streamed, `store: false`) with the same
  messages and the same strict JSON schema, so prompts and E4-E6 do not change.
Every call sends an output limit. Failures are classified so the caller can decide what an attempt costs:
- unavailable: nothing answered (server stopped). The analysis goes back to pending, no attempt
  is spent, and the health gate stops claims until the server answers again.
- timeout / http_error: the server answered badly; one attempt is spent.
- context_overflow: the posting plus prompt does not fit the loaded context. LM Studio answers
  HTTP 400 `exceed_context_size_error` (measured in P1.6 with a 18,216-token prompt against a
  16,384-token load); it does not truncate silently. Retrying cannot help.
- capped (chatgpt): the plan's usage limit (`subscription_sharing_usage_limit_exceeded`,
  `subscription_sharing_usage_unavailable`) or HTTP 429. Like unavailable, no attempt is spent; the step
  moves to its fallback config until the cap clears (inference/steps.py).
For chatgpt, no sign-in or a refused token (HTTP 401) is `unavailable` with detail `signed_out` /
`chatgpt_auth_rejected` (never `capped`), a 5xx is `unavailable`, and a stream that breaks or ends before
its final event is `unavailable`.
Nothing here logs prompts or responses (ARCHITECTURE 8).
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass

import httpx

from jobforge_agent.inference.chatgpt_auth import AuthError, ChatGPTAuth

DEFAULT_TIMEOUT_S = 120.0
PROBE_TIMEOUT_S = 5.0
CAP_CODES = ("subscription_sharing_usage_limit_exceeded", "subscription_sharing_usage_unavailable")


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
    provider_kind: str = "openai_compatible"

    @property
    def timeout_s(self) -> float:
        return float(self.params.get("timeout_s", DEFAULT_TIMEOUT_S))

    @property
    def max_tokens(self) -> int:
        return int(self.params.get("max_tokens", 4000))


def load_config(conn: sqlite3.Connection, config_id: int) -> ModelConfig | None:
    r = conn.execute("SELECT id, name, base_url, model_id, params, structured_output_mode, enabled, tested_at_ms, provider_kind "
                     "FROM model_configs WHERE id = ?", (config_id,)).fetchone()
    if r is None:
        return None
    return ModelConfig(r[0], r[1], r[2].rstrip("/"), r[3], json.loads(r[4]), r[5], bool(r[6]), r[7], r[8])


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
    """The exact body sent (also hashed for E3 answer reuse, evaluation/store.prepare)."""
    p = config.params
    if config.provider_kind == "chatgpt":
        body = {"model": config.model_id, "input": [{"role": m["role"], "content": m["content"]} for m in messages],
                "max_output_tokens": config.max_tokens, "store": False, "stream": True}
        if response_format is not None and response_format.get("type") == "json_schema":
            js = response_format["json_schema"]
            body["text"] = {"format": {"type": "json_schema", "name": js["name"], "schema": js["schema"],
                                       "strict": js.get("strict", True)}}
        # "none" is refused (HTTP 400, ARCHITECTURE 6.9 step 1); without a setting the model's default applies.
        if p.get("reasoning_effort") not in (None, "none"):
            body["reasoning"] = {"effort": p["reasoning_effort"]}
        return body
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


def _usage(u: dict | None) -> dict | None:
    """Responses usage under the chat-completions names, without the per-item attribution map."""
    if not u:
        return None
    out = {"prompt_tokens": u.get("input_tokens"), "completion_tokens": u.get("output_tokens"),
           "total_tokens": u.get("total_tokens")}
    reasoning = (u.get("output_tokens_details") or {}).get("reasoning_tokens")
    if reasoning is not None:
        out["reasoning_tokens"] = reasoning
    return out


def _chatgpt_error(status: int, text: str) -> InferenceError:
    if status == 429 or any(c in text for c in CAP_CODES):
        return InferenceError("capped", f"HTTP {status}: {text[:300]}")
    if status == 401:
        return InferenceError("unavailable", "chatgpt_auth_rejected")
    if status == 400 and ("context_length" in text or "context window" in text):
        return InferenceError("context_overflow", text)
    if status >= 500:
        return InferenceError("unavailable", f"HTTP {status}")
    return InferenceError("http_error", f"HTTP {status}: {text}")


class InferenceClient:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None, chatgpt: ChatGPTAuth | None = None):
        # trust_env=False: an HTTP_PROXY or Windows system proxy must never receive prompts carrying profile
        # items and resume text (httpx does not exempt loopback); ChatGPT calls go straight to OpenAI.
        self._http = httpx.AsyncClient(transport=transport, trust_env=False)
        self.chatgpt = chatgpt

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

    async def models(self, config: ModelConfig) -> list[str]:
        """Model ids the config's server offers (raises InferenceError like list_models)."""
        if config.provider_kind == "chatgpt":
            return [m["slug"] for m in await self.chatgpt_models(config.base_url)]
        return await self.list_models(config.base_url)

    async def _chatgpt_token(self) -> str:
        if self.chatgpt is None:
            raise InferenceError("unavailable", "signed_out")
        try:
            return await self.chatgpt.access_token()
        except AuthError as exc:
            raise InferenceError("unavailable", "signed_out" if exc.kind == "signed_out" else exc.detail) from None

    async def chatgpt_models(self, base_url: str) -> list[dict]:
        """The plan's models: `models[]` with `slug` and `display_name` (6.9 step 1); only `visibility == "list"`."""
        token = await self._chatgpt_token()
        try:
            r = await self._http.get(f"{base_url.rstrip('/')}/models", headers={"Authorization": f"Bearer {token}"},
                                     timeout=15.0)
        except httpx.TransportError as exc:
            raise InferenceError("unavailable", type(exc).__name__) from None
        if r.status_code != 200:
            raise _chatgpt_error(r.status_code, r.text[:500])
        try:
            rows = r.json().get("models") or []
            return [{"slug": m["slug"], "display_name": m.get("display_name") or m["slug"]}
                    for m in rows if m.get("visibility", "list") == "list"]
        except (ValueError, KeyError, TypeError, AttributeError):
            raise InferenceError("unavailable", "unreadable /models response") from None

    async def chat(self, config: ModelConfig, messages: list[dict], response_format: dict | None) -> ChatResult:
        if config.provider_kind == "chatgpt":
            return await self._chat_chatgpt(config, messages, response_format)
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

    async def _chat_chatgpt(self, config: ModelConfig, messages: list[dict], response_format: dict | None) -> ChatResult:
        """One Responses call, streamed. Success only on `response.completed` (finish "stop");
        `response.incomplete` at the output limit is finish "length" (budget_exhausted upstream)."""
        token = await self._chatgpt_token()
        body = request_body(config, messages, response_format)
        t0 = time.perf_counter()
        text: list[str] = []
        final: dict | None = None
        try:
            async with self._http.stream("POST", f"{config.base_url}/responses", json=body,
                                         headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
                                         timeout=httpx.Timeout(config.timeout_s, connect=15.0)) as r:
                if r.status_code != 200:
                    raise _chatgpt_error(r.status_code, (await r.aread()).decode(errors="replace")[:500])
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        ev = json.loads(data)
                    except ValueError:
                        raise InferenceError("unavailable", "unreadable stream event") from None
                    kind = ev.get("type")
                    if kind == "response.output_text.delta":
                        text.append(ev.get("delta") or "")
                    elif kind in ("response.completed", "response.incomplete", "response.failed", "error"):
                        final = ev
                        break
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise InferenceError("unavailable", type(exc).__name__) from None
        except httpx.TimeoutException:
            raise InferenceError("timeout", f"no answer within {config.timeout_s:.0f} s") from None
        except httpx.TransportError as exc:
            raise InferenceError("unavailable", f"stream broken: {type(exc).__name__}") from None
        seconds = round(time.perf_counter() - t0, 2)
        if final is None:
            raise InferenceError("unavailable", "stream ended before the answer finished")
        resp = final.get("response") or {}
        if final["type"] in ("response.failed", "error"):
            err = resp.get("error") or final.get("error") or final
            code = str(err.get("code") or err.get("type") or "")
            if code in CAP_CODES:
                raise InferenceError("capped", code)
            raise InferenceError("http_error", f"response failed: {code} {str(err.get('message') or '')[:300]}")
        finish = "stop"
        if final["type"] == "response.incomplete":
            reason = (resp.get("incomplete_details") or {}).get("reason") or "incomplete"
            finish = "length" if reason == "max_output_tokens" else reason
        return ChatResult("".join(text), finish, _usage(resp.get("usage")), resp.get("model"), seconds)
