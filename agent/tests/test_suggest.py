"""Profile items suggested from a resume (P1.8c): code checks the model's quotes and dates, suggestions
land unconfirmed in the inbox, accepting one makes a self-reported item with the resume's dates."""
import hashlib
import json

from jobforge_agent.profile import suggest
from test_analysis import SNIPPET_OK, FakeServer, _run_with_fake
from test_ipc import AUTH

RESUME = """Jane Doe
IT SUPPORT TECHNICIAN — Acme Health, Jacksonville FL        03/2019 – Present
• Supported 400+ users across Windows 10/11 and
  Active Directory; reset passwords and managed group policy.
• Reduced ticket backlog by 35% in six months.
HELP DESK ANALYST, Beta Corp (2015 - 2018)
Certifications: CompTIA A+, Network+
Education: A.S. Computer Networking, FSCJ, 2014
"""


def _item(kind, statement, quote, start=None, end=None):
    return {"kind": kind, "statement": statement, "quote": quote, "start_date": start, "end_date": end}


ANSWER = {"items": [
    _item("role", "IT Support Technician at Acme Health", "IT SUPPORT TECHNICIAN — Acme Health", "2019-03", "present"),
    _item("role", "Help Desk Analyst at Beta Corp", "HELP DESK ANALYST, Beta Corp", "2015", "2018"),
    _item("skill", "Active Directory administration", "Windows 10/11 and Active Directory"),  # spans a line break
    _item("achievement", "Reduced ticket backlog by 35%", "reduced ticket backlog by 35%"),
    _item("cert", "CompTIA A+", "CompTIA A+"),
    _item("education", "A.S. Computer Networking, FSCJ", "A.S. Computer Networking, FSCJ", None, "2014-05"),
    _item("skill", "Kubernetes", "managed Kubernetes clusters"),  # not in the resume: dropped
    _item("eligibility", "US citizen", "Jane Doe"),  # not a suggestible kind
    _item("cert", "CompTIA A+ ", "CompTIA A+"),  # same statement again
]}


def test_quotes_must_be_in_the_resume_and_dates_are_not_invented():
    kept, counts = suggest.parse(json.dumps(ANSWER), RESUME)
    assert counts == {"proposed": 9, "quote_not_found": 1, "bad_kind": 1, "repeated": 1}
    by = {k["statement"]: k for k in kept}
    assert "Kubernetes" not in by and len(kept) == 6
    assert by["IT Support Technician at Acme Health"]["fields"] == {"start_date": "2019-03", "end_date": None}  # present
    # Year-only: kept as written, never padded to a month, no dates (an empty end date would mean "present").
    assert by["Help Desk Analyst at Beta Corp"]["fields"] == {"attributes": {"dates_as_written": "2015 to 2018"}}
    assert by["Reduced ticket backlog by 35%"]["fields"] == {}  # only roles and education carry dates
    assert by["A.S. Computer Networking, FSCJ"]["fields"] == {"end_date": "2014-05"}  # completion date alone


def test_unreadable_answer_is_an_error_not_an_empty_list():
    import pytest
    with pytest.raises(suggest.SuggestError, match="schema"):
        suggest.parse('{"items": [{"kind": "skill"}]}', RESUME)


def test_resume_prompt_is_pinned_to_its_version():
    """If this fails: bump RESUME_PROMPT_VERSION in profile/suggest.py, then update the digest."""
    digest = hashlib.sha256((suggest.SYSTEM + json.dumps(suggest.SCHEMA, sort_keys=True)).encode()).hexdigest()[:16]
    assert (suggest.RESUME_PROMPT_VERSION, digest) == ("r1-v1", "9e47a034f324ee3b")


def test_suggest_route_fills_the_inbox_and_accept_keeps_dates(paths):
    server = FakeServer(("ok", SNIPPET_OK), ("ok", ANSWER), ("ok", ANSWER), ("connect", None))

    async def check(client, runtime):
        up = (await client.post("/v1/resumes?filename=jane.txt", headers=AUTH, content=RESUME.encode())).json()
        rid = up["id"]
        # Model not turned on yet: a plain sentence, no model call.
        r = await client.post(f"/v1/resumes/{rid}/suggest-items", headers=AUTH)
        assert r.status_code == 422 and "Turn on the AI model" in r.json()["error"] and server.chat_calls == 0
        cid = [c for c in (await client.get("/v1/model-configs", headers=AUTH)).json() if c["default"]][0]["id"]
        assert (await client.post(f"/v1/model-configs/{cid}/test", headers=AUTH)).json()["ok"] is True
        await client.post(f"/v1/model-configs/{cid}/enable", headers=AUTH)

        rep = (await client.post(f"/v1/resumes/{rid}/suggest-items", headers=AUTH)).json()
        assert (rep["added"], rep["quote_not_found"], rep["prompt_version"]) == (6, 1, "r1-v1")
        body = json.loads(server.requests[-1].content)
        assert body["max_tokens"] >= suggest.MAX_TOKENS and "Active Directory" in body["messages"][1]["content"]
        assert not any(server.in_tx_during_call)  # no transaction spans the model call

        claims = (await client.get("/v1/inferred-claims?state=open", headers=AUTH)).json()
        assert len(claims) == 6 and all(c["proposed_by_resume_id"] == rid for c in claims)
        assert all(c["evidence"]["quote"] and c["evidence"]["prompt_version"] == "r1-v1" for c in claims)
        role = next(c for c in claims if c["statement"].startswith("IT Support"))
        cert = next(c for c in claims if c["kind"] == "cert")
        await client.post(f"/v1/inferred-claims/{cert['id']}/reject", headers=AUTH)

        item_id = (await client.post(f"/v1/inferred-claims/{role['id']}/accept", headers=AUTH, json={})).json()["item_id"]
        item = next(i for i in (await client.get("/v1/experience", headers=AUTH)).json() if i["id"] == item_id)
        assert (item["status"], item["start_date"], item["end_date"]) == ("self_reported", "2019-03", None)
        assert item["source"] == f"resume:{rid}:claim:{role['id']}"

        # A rerun brings back neither accepted, rejected nor still-open suggestions.
        again = (await client.post(f"/v1/resumes/{rid}/suggest-items", headers=AUTH)).json()
        assert (again["added"], again["already_known"]) == (0, 6)

        # Model unreachable: 503 with the reason, nothing stored.
        down = await client.post(f"/v1/resumes/{rid}/suggest-items", headers=AUTH)
        assert down.status_code == 503 and "unavailable" in down.json()["error"]
        assert (await client.post("/v1/resumes/999/suggest-items", headers=AUTH)).status_code == 404

        # Deleting the resume is not blocked by its suggestions (no foreign key, by design).
        assert (await client.delete(f"/v1/resumes/{rid}", headers=AUTH)).status_code == 200

    _run_with_fake(paths, server, check)
