"""Dry run of a reading-fix rollout on a copy of the database: the agent's startup sweep, then the real
analysis worker, with a model server that answers only the model list and counts every chat call (there
should be none: each new analysis reuses the posting's stored E1 answer). Then, for each job, whether its
new evaluation's E3 request is byte-identical to a stored one (reused, no call) or needs the model.

Usage (from agent/): python ../scripts/rollout_dry_run.py DB_COPY [AFTER.json]
With AFTER.json (from compare_reading_fix.py run), also checks the worker's requirements equal the ones the
before/after assumed. Writes only into DB_COPY. Prints ids and counts only.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

from jobforge_agent.analysis.worker import AnalysisWorker
from jobforge_agent.db.connect import open_connection
from jobforge_agent.evaluation import store as ev_store
from jobforge_agent.filtering import pipeline
from jobforge_agent.inference.client import InferenceClient, load_config


def main(db: str, after: str | None) -> None:
    conn = open_connection(Path(db), "discovery")
    now = int(time.time() * 1000)
    conn.execute("BEGIN IMMEDIATE")
    queued = pipeline.queue_current_analyses(conn, now)
    conn.execute("COMMIT")
    conn.close()
    print(f"startup sweep queued {queued} analyses")

    ev = open_connection(Path(db), "evaluation")
    model_id = load_config(ev, pipeline.default_model_config_id(ev)).model_id
    chat = []

    def server(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": model_id}]})
        chat.append(request.url.path)
        return httpx.Response(500, text="dry run: no model calls allowed")

    client = InferenceClient(httpx.MockTransport(server))
    worker = AnalysisWorker(ev, client)
    outcomes = []
    while True:
        o = asyncio.run(worker.run_once())
        if o is None:
            break
        outcomes.append(o)
    asyncio.run(client.aclose())
    print(f"analyses processed {len(outcomes)}: {sorted(set(outcomes))}; model chat calls {len(chat)}")

    if after:
        a = json.load(open(after, encoding="utf-8"))["jobs"]
        same = differ = 0
        for job, info in a.items():
            row = ev.execute("SELECT id FROM posting_analyses WHERE job_id = ? AND status = 'done' ORDER BY id DESC LIMIT 1",
                             (int(job),)).fetchone()
            got = {r[0]: (r[1], r[2]) for r in ev.execute(
                "SELECT jd_quote, eligibility_type, final_class FROM requirements WHERE posting_analysis_id = ?", (row[0],))}
            want = {q: (v["type"], v["class"]) for q, v in info["lines"].items()}
            same, differ = (same + 1, differ) if got == want else (same, differ + 1)
        print(f"worker requirements equal the before/after's: {same} jobs; differ: {differ}")

    sync = ev_store.sync(ev, now)
    cfg = load_config(ev, pipeline.default_model_config_id(ev))
    reuse = need = 0
    for aid, job, sid, sha in ev.execute(
            "SELECT posting_analysis_id, job_id, profile_snapshot_id, request_sha256 FROM evaluations WHERE status = 'pending'"):
        if sha is None or ev_store.stored_answer(ev, sha, cfg.id) is not None:
            reuse += 1
        else:
            need += 1
    print(f"evaluations queued {sync.queued}: stored E3 answer reused {reuse}, model call needed {need}")
    ev.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
