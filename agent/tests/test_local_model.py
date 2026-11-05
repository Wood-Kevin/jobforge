"""SPEC D19: the local model on another of the person's computers (reached only through Tailscale), and ChatGPT's
"not eligible" answer, which must move work to that local model like a cap instead of failing jobs.
"""
import asyncio
import json

import pytest

from conftest import NOW
from jobforge_agent.analysis import store as analysis_store
from jobforge_agent.inference import configs, steps
from jobforge_agent.inference.client import load_config
from test_analysis import MODEL, SNIPPET_OK, FakeServer, _run_with_fake
from test_chatgpt import FakeCloud, _cloud_config, _workers
from test_evaluation import E1, e3, env, evals, profile_basic  # noqa: F401 (env is a fixture)
from test_ipc import AUTH

MAC = "100.101.102.103"


@pytest.mark.parametrize("given,want", [
    ("http://127.0.0.1:1234/v1", "http://127.0.0.1:1234/v1"),
    ("localhost:1234", "http://localhost:1234/v1"),
    (f"{MAC}:11434", f"http://{MAC}:11434/v1"),  # no scheme, no path: as typed into the window
    (f"http://{MAC}:11434/", f"http://{MAC}:11434/v1"),
    ("https://Mac.Tail1234.ts.net:11434/v1", "https://mac.tail1234.ts.net:11434/v1"),
    ("http://[fd7a:115c:a1e0::5]:11434", "http://[fd7a:115c:a1e0::5]:11434/v1"),
])
def test_this_pc_and_tailscale_addresses_are_allowed(given, want):
    assert configs.check_local_url(given)[0] == want


@pytest.mark.parametrize("given", [
    "http://192.168.1.50:11434",  # the same Mac on the home network: prompts would cross it as plain text
    "http://10.0.0.5:1234",
    "http://100.128.0.1:11434",  # just outside Tailscale's 100.64.0.0/10
    "https://api.example.com/v1",
    "http://mac.ts.net.example.com:11434",
    "ftp://127.0.0.1:1234",
    "http://user:pw@127.0.0.1:1234",
    "http://127.0.0.1:1234/v1?x=1",
])
def test_home_network_and_public_addresses_are_refused(given):
    with pytest.raises(configs.ConfigError):
        configs.check_local_url(given)


def test_a_config_per_address_and_model_with_the_longer_wait_off_this_pc(db):
    mac = configs.upsert_local_config(db, f"{MAC}:11434", "qwen3.5:9b")
    assert configs.upsert_local_config(db, f"http://{MAC}:11434/v1", " qwen3.5:9b ") == mac  # the same server and model
    here = configs.upsert_local_config(db, "127.0.0.1:11434", "qwen3.5:9b")
    rows = {r[0]: (r[1], r[2], json.loads(r[3])) for r in db.execute(
        "SELECT id, name, provider_label, params FROM model_configs WHERE id IN (?, ?)", (mac, here))}
    assert rows[mac][:2] == (f"qwen3.5:9b ({MAC}:11434)", "Another computer")
    assert rows[mac][2]["timeout_s"] == configs.REMOTE_TIMEOUT_S
    assert rows[here][1] == "This PC" and "timeout_s" not in rows[here][2]
    with pytest.raises(configs.ConfigError):
        configs.upsert_local_config(db, f"{MAC}:11434", "  ")


def test_the_test_call_waits_at_most_the_test_limit(db):
    mac =configs.upsert_local_config(db, f"{MAC}:11434", MODEL)
    seen = []

    class Client:
        async def models(self, cfg):
            return [MODEL]

        async def chat(self, cfg, msgs, fmt):
            seen.append(cfg.timeout_s)
            raise configs.InferenceError("timeout", "slow")
    report = asyncio.run(configs.run_test(Client(), load_config(db, mac)))
    assert seen == [configs.TEST_TIMEOUT_S] and report["ok"] is False  # the window waits 180 s for a test (host)


def test_use_local_model_moves_the_local_steps_and_keeps_chatgpt(paths):
    server = FakeServer(("ok", SNIPPET_OK))

    async def check(client, runtime):
        cloud = _cloud_config(runtime.user)
        runtime.user.execute("UPDATE settings SET value = ? WHERE key = 'model.e3'", (str(cloud),))
        r = await client.post("/v1/model-configs/local", headers=AUTH,
                              json={"base_url": "http://192.168.1.50:11434", "model_id": MODEL})
        assert r.status_code == 422 and "Tailscale" in r.json()["error"]
        assert runtime.reader.execute("SELECT count(*) FROM model_configs").fetchone()[0] == 2  # nothing was added
        r = (await client.post("/v1/model-configs/local", headers=AUTH,
                               json={"base_url": f"{MAC}:11434", "model_id": MODEL})).json()
        assert r["test"]["ok"] is True
        mac = r["model_config_id"]
        assert r["steps"] == {"e1": mac, "e3": cloud, "resume": mac, "fallback": mac}  # matching stays on ChatGPT
        assert {q.url.host for q in server.requests} == {MAC}
        cfg = [c for c in (await client.get("/v1/model-configs", headers=AUTH)).json() if c["id"] == mac][0]
        assert cfg["enabled"] is True and cfg["base_url"] == f"http://{MAC}:11434/v1"
    _run_with_fake(paths, FakeCloud(local=server), check)


def test_a_model_that_fails_its_test_changes_no_step(paths):
    server = FakeServer()
    server.down = True

    async def check(client, runtime):
        before = steps.listing(runtime.reader)
        r = (await client.post("/v1/model-configs/local", headers=AUTH,
                               json={"base_url": f"{MAC}:11434", "model_id": MODEL})).json()
        assert r["test"]["ok"] is False and r["steps"] == before
        cfg = [c for c in (await client.get("/v1/model-configs", headers=AUTH)).json() if c["id"] == r["model_config_id"]][0]
        assert cfg["enabled"] is False
    _run_with_fake(paths, server, check)


def test_postings_queued_on_the_old_local_model_are_read_by_the_new_one(env, tmp_path):
    old = env.ids["model"]
    mac = configs.upsert_local_config(env.db, f"{MAC}:11434", MODEL)
    env.db.execute("UPDATE model_configs SET enabled = 1, tested_at_ms = ? WHERE id = ?", (NOW, mac))
    env.db.execute("UPDATE settings SET value = ? WHERE key LIKE 'model.%'", (str(mac),))
    # The hand-over runs on the user's connection (agent.use_local_model); its authorizer must allow it.
    assert analysis_store.move_pending(env.user, old, mac, revive=False) == 1
    server = FakeServer(("ok", E1))
    a, _ = _workers(env, tmp_path, FakeCloud(local=server))
    assert asyncio.run(a.run_once()) == "done"
    assert [tuple(r) for r in env.db.execute("SELECT model_config_id, status FROM posting_analyses")] == [(mac, "done")]
    assert {q.url.host for q in server.requests} == {MAC}  # the old server (LM Studio on this PC) was never asked


def test_a_posting_the_new_model_already_failed_stays_failed(env):
    old = env.ids["model"]
    mac = configs.upsert_local_config(env.db, f"{MAC}:11434", MODEL)
    env.db.execute("INSERT INTO posting_analyses (raw_posting_id, job_id, model_config_id, extraction_prompt_version, segmenter_version, "
                   "interpreter_version, status, attempts, created_at_ms) VALUES (?, ?, ?, 'e1-v1', 'seg-v1', 'interp-v1', 'failed', 2, ?)",
                   (env.ids["raw"], env.ids["job"], mac, NOW))
    assert analysis_store.move_pending(env.user, old, mac, revive=False) == 1
    rows = {r[0]: (r[1], r[2]) for r in env.db.execute("SELECT model_config_id, status, attempts FROM posting_analyses")}
    assert rows == {old: ("superseded", 0), mac: ("failed", 2)}  # the user's action never resets tries


def test_not_eligible_moves_matching_to_the_local_model_and_spends_no_attempt(env, tmp_path):
    profile_basic(env)
    local = env.ids["model"]
    cloud_id = _cloud_config(env.db)
    env.db.execute("UPDATE settings SET value = ? WHERE key = 'model.e3'", (str(cloud_id),))
    refused = (403, '{"error": {"code": "subscription_sharing_user_not_eligible", "message": "not eligible"}}')
    cloud = FakeCloud(("status", refused), local=FakeServer(("ok", E1), ("ok", e3())))
    a, e = _workers(env, tmp_path, cloud)
    assert asyncio.run(a.run_once()) == "done"
    assert asyncio.run(e.run_once()) == "deferred"
    (ev,) = evals(env.db)
    assert (ev["model_config_id"], ev["status"], ev["attempts"]) == (cloud_id, "pending", 0)
    assert steps.OUT == {cloud_id: "not_eligible"}  # the window names it; it is not "at its usage limit"
    assert asyncio.run(e.run_once()) == "done"
    assert [(r["model_config_id"], r["status"]) for r in evals(env.db)] == [(cloud_id, "superseded"), (local, "done")]
    # Waited out like a cap: no probe brings it back early, so no second refused call meanwhile.
    asyncio.run(e.gate.recheck_due(env.ev))
    assert steps.OUT == {cloud_id: "not_eligible"} and cloud.cloud_calls == 1
