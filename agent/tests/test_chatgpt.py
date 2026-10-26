"""SPEC D18 / ARCHITECTURE 6.9: model steps, Sign in with ChatGPT, the Responses client, and the fallback.

A fake transport stands in for auth.openai.com and api.openai.com (recorded stream shapes from the step-1
spike: `response.output_text.delta` events, then `response.completed` with `usage`); everything else goes to
the fake LM Studio (test_analysis.FakeServer). Tokens are real DPAPI files in the test's own folder.
"""
import asyncio
import base64
import json
import time
import urllib.parse

import httpx
import pytest

from conftest import NOW
from jobforge_agent.analysis.worker import CAPPED_BACKOFF_S, AnalysisWorker
from jobforge_agent.evaluation import store as ev_store
from jobforge_agent.evaluation.match import response_format as match_format
from jobforge_agent.evaluation.worker import EvaluationWorker
from jobforge_agent.filtering import pipeline
from jobforge_agent.inference import chatgpt_auth, configs, steps
from jobforge_agent.inference.chatgpt_auth import ISSUER, SCOPES, ChatGPTAuth
from jobforge_agent.inference.client import InferenceClient, InferenceError, ModelConfig, load_config, request_body
from test_analysis import FakeServer, _run_with_fake, logged
from test_evaluation import E1, e3, env, evals, profile_basic  # noqa: F401 (env is a fixture)
from test_ipc import AUTH

CLOUD = ModelConfig(9, "luna", "https://api.openai.com/v1", "gpt-5.6-luna", {"max_tokens": 16000, "timeout_s": 300},
                    "json_schema", True, NOW, "chatgpt")
MSGS = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]


def jwt(claims: dict) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    return f"{enc({'alg': 'RS256'})}.{enc(claims)}.sig"


def sse(*events) -> bytes:
    return b"".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in events)


def answer(kind: str, payload=None) -> httpx.Response:
    text = json.dumps(payload if payload is not None else {"ok": True})
    deltas = [{"type": "response.output_text.delta", "delta": text[i:i + 7]} for i in range(0, len(text), 7)]
    usage = {"input_tokens": 2000, "output_tokens": 1200, "total_tokens": 3200, "output_tokens_details": {"reasoning_tokens": 118},
             "attribution": {"items": {"msg_1": {"input_tokens": 600}}}}
    if kind == "ok":
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse(
            *deltas, {"type": "response.metadata"},
            {"type": "response.completed", "response": {"model": "gpt-5.6-luna", "usage": usage}}))
    if kind == "incomplete":
        return httpx.Response(200, content=sse(deltas[0], {"type": "response.incomplete", "response": {
            "incomplete_details": {"reason": "max_output_tokens"}, "usage": usage}}))
    if kind == "capped":
        return httpx.Response(200, content=sse({"type": "response.failed", "response": {
            "error": {"code": "subscription_sharing_usage_limit_exceeded", "message": "limit"}}}))
    if kind == "cut":
        return httpx.Response(200, content=sse(*deltas[:2]))
    if kind == "status":
        return httpx.Response(payload[0], text=payload[1])
    raise AssertionError(kind)


class FakeCloud:
    """auth.openai.com and api.openai.com; anything else goes to `local`."""

    def __init__(self, *answers, local: FakeServer | None = None):
        self.answers = list(answers)
        self.local = local
        self.token_answers: list[tuple[int, dict]] = []
        self.models_status = 200
        self.requests: list[httpx.Request] = []
        self.watch: list = []  # _run_with_fake adds connections (FakeServer checks them; nothing here needs to)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "auth.openai.com":
            self.requests.append(request)
            status, body = self.token_answers.pop(0)
            return httpx.Response(status, json=body)
        if request.url.host == "api.openai.com":
            self.requests.append(request)
            if request.url.path.endswith("/models"):
                if self.models_status != 200:
                    return httpx.Response(self.models_status, json={"error": {"code": "invalid_api_key"}})
                return httpx.Response(200, json={"models": [
                    {"slug": "gpt-5.6-luna", "display_name": "GPT-5.6 Luna", "visibility": "list"},
                    {"slug": "internal", "display_name": "x", "visibility": "hide"}]})
            kind, payload = self.answers.pop(0)
            return answer(kind, payload)
        return self.local(request)

    @property
    def cloud_calls(self) -> int:
        return sum(1 for r in self.requests if r.url.path.endswith("/responses"))


def token_set(**over) -> dict:
    t = {"client_id": "oaiapp_test", "access_token": "AT-secret-1", "refresh_token": "RT-secret-1",
         "id_token": jwt({"iss": ISSUER, "aud": ["oaiapp_test"], "exp": time.time() + 3600, "email": "person@example.com"}),
         "scope": SCOPES, "expires_at": time.time() + 3600}
    return {**t, **over}


def signed_in(folder, transport, **over) -> ChatGPTAuth:
    auth = ChatGPTAuth(folder, httpx.AsyncClient(transport=transport))
    auth._save(token_set(**over))
    return auth


# ----- client: request shape and recorded streams -----

def test_responses_body_is_strict_schema_store_false_and_streamed():
    body = request_body(CLOUD, MSGS, match_format("json_schema"))
    assert (body["store"], body["stream"], body["max_output_tokens"], body["model"]) == (False, True, 16000, "gpt-5.6-luna")
    assert body["input"] == MSGS and "messages" not in body and "temperature" not in body
    fmt = body["text"]["format"]
    assert (fmt["type"], fmt["strict"], fmt["name"]) == ("json_schema", True, "matches")
    assert "reasoning" not in body  # no effort set: the model's default (step 1: "none" is refused)
    low = request_body(ModelConfig(**{**CLOUD.__dict__, "params": {"reasoning_effort": "low"}}), MSGS, None)
    assert low["reasoning"] == {"effort": "low"} and "text" not in low
    none = request_body(ModelConfig(**{**CLOUD.__dict__, "params": {"reasoning_effort": "none"}}), MSGS, None)
    assert "reasoning" not in none


def _chat(tmp_path, *answers, **auth_over):
    cloud = FakeCloud(*answers)

    async def go():
        t = httpx.MockTransport(cloud)
        client = InferenceClient(t, chatgpt=signed_in(tmp_path / "auth", t, **auth_over))
        try:
            return await client.chat(CLOUD, MSGS, match_format("json_schema"))
        finally:
            await client.aclose()
            await client.chatgpt.http.aclose()
    return cloud, go


def test_completed_stream_parses_and_usage_is_normalized(tmp_path):
    cloud, go = _chat(tmp_path, ("ok", {"matches": []}))
    r = asyncio.run(go())
    assert (json.loads(r.content), r.finish_reason, r.model_reported) == ({"matches": []}, "stop", "gpt-5.6-luna")
    assert r.usage == {"prompt_tokens": 2000, "completion_tokens": 1200, "total_tokens": 3200, "reasoning_tokens": 118}
    req = cloud.requests[-1]
    assert req.headers["authorization"] == "Bearer AT-secret-1" and json.loads(req.content)["store"] is False


@pytest.mark.parametrize("kind,payload,want", [
    ("capped", None, "capped"),
    ("status", (429, "slow down"), "capped"),
    ("status", (403, '{"error": {"code": "subscription_sharing_usage_unavailable"}}'), "capped"),
    ("status", (401, '{"error": {"code": "invalid_api_key"}}'), "unavailable"),  # signed out, never capped
    ("status", (503, "busy"), "unavailable"),
    ("cut", None, "unavailable"),  # the stream ended before its final event
    ("status", (400, "bad request"), "http_error"),
])
def test_error_shapes_map_to_kinds(tmp_path, kind, payload, want):
    _, go = _chat(tmp_path, (kind, payload))
    with pytest.raises(InferenceError) as exc:
        asyncio.run(go())
    assert exc.value.kind == want
    if payload and payload[0] == 401:
        assert exc.value.detail == "chatgpt_auth_rejected"


def test_incomplete_at_the_output_limit_is_length(tmp_path):
    _, go = _chat(tmp_path, ("incomplete", None))
    assert asyncio.run(go()).finish_reason == "length"


def test_no_sign_in_is_unavailable_signed_out(tmp_path):
    async def go():
        client = InferenceClient(httpx.MockTransport(FakeCloud()), chatgpt=ChatGPTAuth(tmp_path / "auth"))
        try:
            await client.chat(CLOUD, MSGS, None)
        finally:
            await client.aclose()
            await client.chatgpt.http.aclose()
    with pytest.raises(InferenceError) as exc:
        asyncio.run(go())
    assert (exc.value.kind, exc.value.detail) == ("unavailable", "signed_out")


def test_models_lists_only_visible_slugs(tmp_path):
    async def go():
        t = httpx.MockTransport(FakeCloud())
        client = InferenceClient(t, chatgpt=signed_in(tmp_path / "auth", t))
        try:
            return await client.models(CLOUD), await client.chatgpt_models(CLOUD.base_url)
        finally:
            await client.aclose()
            await client.chatgpt.http.aclose()
    ids, rows = asyncio.run(go())
    assert ids == ["gpt-5.6-luna"] and rows == [{"slug": "gpt-5.6-luna", "display_name": "GPT-5.6 Luna"}]


# ----- sign-in and tokens -----

def _sign_in(tmp_path, callback_query, token_answer=None):
    """Start a sign-in, answer it on the real loopback listener, return (auth, http status, attempt, cloud)."""
    cloud = FakeCloud()

    async def go():
        auth = ChatGPTAuth(tmp_path / "auth", httpx.AsyncClient(transport=httpx.MockTransport(cloud)))
        url = await auth.start_sign_in()
        q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(url).query).items()}
        assert (q["client_id"], q["code_challenge_method"], q["scope"]) == ("dynamic_agent_client", "S256", SCOPES)
        assert q["redirect_uri"].startswith("http://127.0.0.1:") and q["redirect_uri"].endswith("/auth/callback")
        cloud.token_answers.append(token_answer(q) if token_answer else (200, {
            "access_token": "AT-secret-1", "refresh_token": "RT-secret-1", "expires_in": 3600, "scope": SCOPES,
            "id_token": jwt({"iss": ISSUER, "aud": ["oaiapp_new"], "exp": time.time() + 3600, "nonce": q["nonce"],
                             "email": "person@example.com"})}))
        params = callback_query(q)
        async with httpx.AsyncClient(trust_env=False) as browser:
            r = await browser.get(q["redirect_uri"] + "?" + urllib.parse.urlencode(params))
        await auth.close()
        return auth, r.status_code, auth.attempt
    auth, status, attempt = asyncio.run(go())
    return auth, status, attempt, cloud


def good_callback(q):
    return {"code": "c-1", "state": q["state"], "client_id": "oaiapp_new", "scope": SCOPES}


def test_sign_in_stores_dpapi_tokens_and_never_logs_them(tmp_path, caplog, capsys):
    auth, status, attempt, cloud = _sign_in(tmp_path, good_callback)
    assert status == 200 and attempt.state == "done" and auth.status()["signed_in"] is True
    assert auth.status()["email"] == "person@example.com"
    exchange = urllib.parse.parse_qs(cloud.requests[0].content.decode())
    assert exchange["grant_type"] == ["authorization_code"] and exchange["client_id"] == ["oaiapp_new"]
    assert "code_verifier" in exchange and "client_secret" not in exchange
    raw = (tmp_path / "auth" / "chatgpt.bin").read_bytes()
    assert b"AT-secret-1" not in raw and b"RT-secret-1" not in raw  # encrypted at rest
    out = logged(caplog, capsys)
    assert "chatgpt_signed_in" in out and "AT-secret-1" not in out and "RT-secret-1" not in out and "c-1" not in out


def test_sign_in_with_the_wrong_state_is_refused(tmp_path):
    auth, status, attempt, cloud = _sign_in(tmp_path, lambda q: {**good_callback(q), "state": "forged"})
    assert status == 400 and attempt.state == "failed" and "state" in attempt.error
    assert not auth.signed_in() and cloud.requests == []  # the code was never exchanged


def test_sign_in_without_the_plan_scope_is_refused(tmp_path):
    def no_plan(q):
        return 200, {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600, "scope": "openid email",
                     "id_token": jwt({"iss": ISSUER, "aud": ["oaiapp_new"], "exp": time.time() + 3600, "nonce": q["nonce"]})}
    auth, status, attempt, _ = _sign_in(tmp_path, good_callback, no_plan)
    assert status == 400 and "plan" in attempt.error and not auth.signed_in()


def test_sign_in_with_a_replayed_id_token_is_refused(tmp_path):
    def other_nonce(q):
        return 200, {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600, "scope": SCOPES,
                     "id_token": jwt({"iss": ISSUER, "aud": ["oaiapp_new"], "exp": time.time() + 3600, "nonce": "old"})}
    auth, _, attempt, _ = _sign_in(tmp_path, good_callback, other_nonce)
    assert attempt.error == "nonce mismatch" and not auth.signed_in()


def test_expiring_token_is_refreshed_once_and_rotated(tmp_path):
    cloud = FakeCloud()
    cloud.token_answers.append((200, {"access_token": "AT-2", "refresh_token": "RT-2", "expires_in": 3600}))

    async def go():
        auth = signed_in(tmp_path / "auth", httpx.MockTransport(cloud), expires_at=time.time() + 30)
        first, second = await auth.access_token(), await auth.access_token()
        await auth.close()
        return auth, first, second
    auth, first, second = asyncio.run(go())
    assert first == second == "AT-2" and len(cloud.requests) == 1  # the second call used the new token
    assert urllib.parse.parse_qs(cloud.requests[0].content.decode())["refresh_token"] == ["RT-secret-1"]
    assert auth._load()["refresh_token"] == "RT-2"


def test_no_refresh_before_earliest_refresh_at(tmp_path):
    cloud = FakeCloud()

    async def go():
        auth = signed_in(tmp_path / "auth", httpx.MockTransport(cloud), expires_at=time.time() + 60,
                         earliest_refresh_at=time.time() + 600)
        token = await auth.access_token()
        await auth.close()
        return token
    assert asyncio.run(go()) == "AT-secret-1" and cloud.requests == []


def test_refused_refresh_signs_out(tmp_path):
    cloud = FakeCloud()
    cloud.token_answers.append((400, {"error": "invalid_grant"}))

    async def go():
        auth = signed_in(tmp_path / "auth", httpx.MockTransport(cloud), expires_at=time.time() - 5)
        try:
            await auth.access_token()
        finally:
            await auth.close()
        return auth
    with pytest.raises(chatgpt_auth.AuthError) as exc:
        asyncio.run(go())
    assert exc.value.kind == "signed_out" and not (tmp_path / "auth" / "chatgpt.bin").exists()


# ----- migration and steps -----

def test_migration_copies_the_default_into_every_step(db):
    rows = dict(db.execute("SELECT key, value FROM settings WHERE key LIKE 'model.%' OR key = 'default_model_config_id'"))
    gemma = db.execute("SELECT id FROM model_configs WHERE model_id = 'google/gemma-4-12b-qat'").fetchone()[0]
    assert rows == {k: str(gemma) for k in ("model.e1", "model.e3", "model.resume", "model.fallback")}
    assert db.execute("SELECT provider_kind FROM model_configs WHERE id = ?", (gemma,)).fetchone()[0] == "openai_compatible"
    with pytest.raises(Exception):
        db.execute("UPDATE model_configs SET provider_kind = 'other' WHERE id = ?", (gemma,))


def _cloud_config(db, enabled=True) -> int:
    return db.execute(
        "INSERT INTO model_configs (name, provider_label, provider_kind, base_url, model_id, params, enabled, tested_at_ms) "
        "VALUES ('GPT-5.6 Luna (ChatGPT)', 'ChatGPT', 'chatgpt', 'https://api.openai.com/v1', 'gpt-5.6-luna', "
        "'{\"max_tokens\": 16000}', ?, ?) RETURNING id", (int(enabled), NOW if enabled else None)).fetchone()[0]


def test_only_a_cloud_step_falls_back(db):
    local = steps.configured(db, "e3")
    cloud = _cloud_config(db)
    db.execute("UPDATE settings SET value = ? WHERE key = 'model.e3'", (str(cloud),))
    assert steps.active(db, "e3") == cloud
    steps.OUT[cloud] = "capped"
    assert steps.active(db, "e3") == local
    steps.OUT[local] = "ConnectError"
    assert steps.active(db, "e1") == local  # a local config that is down waits; it has nothing to fall back to


def test_step_needs_a_turned_on_config_and_a_local_fallback(db):
    off = _cloud_config(db, enabled=False)
    with pytest.raises(ValueError):
        steps.set_step(db, "e3", off)
    on = db.execute("UPDATE model_configs SET enabled = 1, tested_at_ms = ? WHERE id = ? RETURNING id", (NOW, off)).fetchone()[0]
    with pytest.raises(ValueError):
        steps.set_step(db, "fallback", on)
    with pytest.raises(ValueError):
        steps.set_step(db, "e2", on)
    steps.set_step(db, "e3", on)
    assert steps.configured(db, "e3") == on


# ----- the fallback, end to end on the workers -----

def _workers(env, tmp_path, cloud):
    cloud.local.watch += [env.ev, env.user]
    t = httpx.MockTransport(cloud)
    client = InferenceClient(t, chatgpt=signed_in(tmp_path / "auth", t))
    env.clients.append(client)
    a = AnalysisWorker(env.ev, client, now_ms=env.clock)
    return a, EvaluationWorker(env.ev, client, a.gate, now_ms=env.clock)


def test_cap_moves_matching_to_the_fallback_then_back_to_the_cloud(env, tmp_path, caplog, capsys):
    profile_basic(env)
    local = env.ids["model"]
    cloud_id = _cloud_config(env.db)
    env.db.execute("UPDATE settings SET value = ? WHERE key = 'model.e3'", (str(cloud_id),))
    cloud = FakeCloud(("capped", None), ("ok", e3()), local=FakeServer(("ok", E1), ("ok", e3())))
    a, e = _workers(env, tmp_path, cloud)
    assert asyncio.run(a.run_once()) == "done"  # posting reading stays on this PC (model.e1)

    # 1. The cloud answers "usage limit": no attempt spent, the config is out.
    assert asyncio.run(e.run_once()) == "deferred"
    (ev,) = evals(env.db)
    assert (ev["model_config_id"], ev["status"], ev["attempts"]) == (cloud_id, "pending", 0)
    assert steps.OUT == {cloud_id: "capped"}

    # 2. Jobs never wait on a cap: the next pass supersedes it and matches the job on this PC.
    assert asyncio.run(e.run_once()) == "done"
    rows = evals(env.db)
    assert [(r["model_config_id"], r["status"]) for r in rows] == [(cloud_id, "superseded"), (local, "done")]
    rec = ev_store.job_recommendation(env.ev, env.ids["job"])
    assert rec["current"] is True and rec["judged_by"]["fallback"] is True  # the job page says "judged on this PC"

    # 3. Within the wait nothing goes back to the cloud.
    env.clock.t += int(CAPPED_BACKOFF_S[0] * 1000) - 1000
    assert asyncio.run(e.run_once()) is None and cloud.cloud_calls == 1

    # 4. After it, the job is matched again on the cloud (Kevin: fallback-matched jobs are rejudged).
    env.clock.t += 2000
    assert asyncio.run(e.run_once()) == "done"
    rows = evals(env.db)
    assert [(r["model_config_id"], r["status"]) for r in rows] == [(cloud_id, "done"), (local, "done")]
    rec = ev_store.job_recommendation(env.ev, env.ids["job"])
    assert rec["evaluation"]["model_config_id"] == cloud_id and rec["current"] is True
    assert rec["judged_by"] == {"name": "GPT-5.6 Luna (ChatGPT)", "provider_kind": "chatgpt", "fallback": False}
    assert cloud.cloud_calls == 2 and steps.OUT == {}

    # 5. A later outage keeps the cloud's verdict: the job is not judged again on this PC meanwhile.
    e.gate.mark_down(load_config(env.ev, cloud_id), "ConnectError")
    assert e.sync().queued == 0 and ev_store.job_recommendation(env.ev, env.ids["job"])["evaluation"]["model_config_id"] == cloud_id
    assert ev_store.job_recommendation(env.ev, env.ids["job"])["current"] is True
    out = logged(caplog, capsys)
    assert out.index("inference_capped") < out.index("model_fallback") < out.index("model_cloud_retry")

    # Usage: one cloud call made a verdict (the capped one stored no answer), one local E1 and one local E3.
    usage = {(r["step"], r["model_config_id"]): r["calls"] for r in configs.usage(env.ev, 0)}
    assert usage == {("e1", local): 1, ("e3", local): 1, ("e3", cloud_id): 1}


def test_signed_out_cloud_hands_waiting_postings_to_the_fallback(env, tmp_path):
    local = env.ids["model"]
    cloud_id = _cloud_config(env.db)
    env.db.execute("UPDATE settings SET value = ? WHERE key = 'model.e1'", (str(cloud_id),))
    env.db.execute("UPDATE posting_analyses SET model_config_id = ?", (cloud_id,))
    cloud = FakeCloud(local=FakeServer(("ok", E1)))
    cloud.models_status = 401  # the token was refused
    a, _ = _workers(env, tmp_path, cloud)
    assert asyncio.run(a.run_once()) == "moved"
    assert steps.OUT == {cloud_id: "chatgpt_auth_rejected"}
    assert asyncio.run(a.run_once()) == "done"
    assert [tuple(r) for r in env.db.execute("SELECT model_config_id, status FROM posting_analyses")] == [(local, "done")]
    assert cloud.cloud_calls == 0


def test_a_posting_read_by_another_config_is_not_read_again(env):
    cloud_id = _cloud_config(env.db)
    db, job, raw = env.db, env.ids["job"], env.ids["raw"]
    db.execute("DELETE FROM posting_analyses")
    assert pipeline.queue_analysis(db, job, raw, env.ids["model"], NOW) is True
    db.execute("UPDATE posting_analyses SET status = 'done'")
    assert pipeline.queue_analysis(db, job, raw, cloud_id, NOW) is False  # the fallback's reading stays
    db.execute("UPDATE posting_analyses SET status = 'failed'")
    assert pipeline.queue_analysis(db, job, raw, cloud_id, NOW) is True  # a failure elsewhere is tried here


# ----- IPC -----

def test_chatgpt_routes_signed_out_and_step_errors(paths):
    async def check(client, runtime):
        s = (await client.get("/v1/chatgpt", headers=AUTH)).json()
        assert (s["signed_in"], s["default_model"], s["configs"]) == (False, "gpt-5.6-luna", [])
        assert set(s["steps"]) == {"e1", "e3", "resume", "fallback"}
        r = await client.get("/v1/chatgpt/models", headers=AUTH)
        assert r.status_code == 503 and r.json()["kind"] == "unavailable"
        r = await client.put("/v1/model-steps", headers=AUTH, json={"step": "e3", "model_config_id": 999})
        assert r.status_code == 404
        r = await client.put("/v1/model-steps", headers=AUTH, json={"step": "e9", "model_config_id": s["steps"]["e3"]})
        assert r.status_code == 409
        assert (await client.post("/v1/chatgpt/sign-out", headers=AUTH)).json() == {"signed_out": False}
        assert (await client.get("/v1/model-usage", headers=AUTH)).json() == {"days": 7, "rows": []}
    _run_with_fake(paths, FakeCloud(), check)


def test_use_model_creates_tests_and_turns_on_a_chatgpt_config(paths):
    from test_analysis import SNIPPET_OK
    cloud = FakeCloud(("ok", SNIPPET_OK))

    async def check(client, runtime):
        runtime.chatgpt_auth()._save(token_set())
        r = (await client.post("/v1/chatgpt/use-model", headers=AUTH, json={"slug": "gpt-5.6-luna"})).json()
        assert r["test"]["ok"] is True
        cfg = [c for c in (await client.get("/v1/model-configs", headers=AUTH)).json() if c["id"] == r["model_config_id"]][0]
        assert (cfg["provider_kind"], cfg["enabled"], cfg["name"], cfg["steps"]) == ("chatgpt", True, "GPT-5.6 Luna (ChatGPT)", [])
        steps_now = (await client.put("/v1/model-steps", headers=AUTH,
                                      json={"step": "e3", "model_config_id": cfg["id"]})).json()["steps"]
        assert steps_now["e3"] == cfg["id"] and steps_now["fallback"] != cfg["id"]
        r = await client.post("/v1/chatgpt/use-model", headers=AUTH, json={"slug": "internal"})
        assert r.status_code == 404  # hidden models are not offered
    _run_with_fake(paths, cloud, check)
