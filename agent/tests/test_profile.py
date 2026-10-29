"""Profile (P1.5): resumes, experience items incl. eligibility facts, verify, completeness,
inferred-claims inbox (SPEC D2; ARCHITECTURE 4.2, 4.3). Synthetic data only."""
import ast
import hashlib
import io
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from conftest import NOW
from jobforge_agent.db.connect import open_connection
from test_ipc import AUTH, _run

SRC = Path(__file__).resolve().parents[1] / "src" / "jobforge_agent"


def _docx(paragraphs) -> bytes:
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    xml = f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{w}"><w:body>{body}</w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


def _pdf(text: str) -> bytes:
    """A minimal one-page PDF with real text, offsets computed so the xref is valid."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


CASES = [
    ("resume.md", "# Synthetic Person\n\n- NOC monitoring\n".encode(), "NOC monitoring"),
    ("resume.txt", b"Help desk \x96 tier 2", "Help desk \u2013 tier 2"),  # cp1252 en dash: legacy Notepad encoding
    ("resume.docx", _docx(["Synthetic Person", "Network operations, 2 years"]), "Network operations, 2 years"),
    ("resume.pdf", _pdf("Synthetic Person NOC Analyst"), "NOC Analyst"),
]


# ----- resumes -----

@pytest.mark.parametrize("name, data, expect_text", CASES)
def test_resume_round_trip_is_byte_identical(paths, name, data, expect_text):
    async def check(client, runtime, _app):
        r = await client.post("/v1/resumes", headers=AUTH, params={"filename": name}, content=data)
        assert r.status_code == 200, r.text
        up = r.json()
        assert up["sha256"] == hashlib.sha256(data).hexdigest() and up["extraction_error"] is None
        orig = await client.get(f"/v1/resumes/{up['id']}/original", headers=AUTH)
        assert orig.content == data and hashlib.sha256(orig.content).hexdigest() == up["sha256"]
        assert orig.headers["x-sha256"] == up["sha256"]
        meta = (await client.get(f"/v1/resumes/{up['id']}", headers=AUTH)).json()
        assert expect_text in meta["extracted_text"] and "original_bytes" not in meta
    _run(paths, check)


def test_unreadable_pdf_is_kept_with_a_visible_extraction_error(paths):
    async def check(client, runtime, _app):
        data = b"%PDF-1.4 this is not really a pdf"
        r = (await client.post("/v1/resumes", headers=AUTH, params={"filename": "scan.pdf"}, content=data)).json()
        assert r["extraction_error"] and r["text_chars"] == 0
        assert (await client.get(f"/v1/resumes/{r['id']}/original", headers=AUTH)).content == data
    _run(paths, check)


@pytest.mark.parametrize("name, data, status", [("tool.exe", b"MZ", 422), ("empty.txt", b"", 422), ("noext", b"x", 422)])
def test_rejected_uploads_store_nothing(paths, name, data, status):
    async def check(client, runtime, _app):
        r = await client.post("/v1/resumes", headers=AUTH, params={"filename": name}, content=data)
        assert r.status_code == status
        assert runtime.reader.execute("SELECT count(*) FROM resumes").fetchone()[0] == 0
    _run(paths, check)


def test_oversized_upload_is_413(paths, monkeypatch):
    from jobforge_agent.profile import resumes
    monkeypatch.setattr(resumes, "MAX_BYTES", 10)

    async def check(client, runtime, _app):
        r = await client.post("/v1/resumes", headers=AUTH, params={"filename": "big.txt"}, content=b"x" * 11)
        assert r.status_code == 413
    _run(paths, check)


def test_filename_path_is_stripped_and_resume_deletes(paths):
    async def check(client, runtime, _app):
        r = (await client.post("/v1/resumes", headers=AUTH, params={"filename": r"C:\Users\x\cv.md", "label": "Ops"},
                               content=b"hello")).json()
        [listed] = (await client.get("/v1/resumes", headers=AUTH)).json()
        assert (listed["original_filename"], listed["label"], listed["bytes"]) == ("cv.md", "Ops", 5)
        assert (await client.delete(f"/v1/resumes/{r['id']}", headers=AUTH)).json() == {"deleted": r["id"]}
        assert (await client.delete(f"/v1/resumes/{r['id']}", headers=AUTH)).status_code == 404
    _run(paths, check)


# ----- experience items and verification -----

async def _item(client, **body):
    r = await client.post("/v1/experience", headers=AUTH, json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_items_start_self_reported_and_status_cannot_be_sent(paths):
    async def check(client, runtime, _app):
        i = await _item(client, kind="skill", statement="Splunk searches", years=1.5)
        [item] = (await client.get("/v1/experience", headers=AUTH)).json()
        assert (item["id"], item["status"], item["verified_at_ms"], item["source"]) == (i, "self_reported", None, "user")
        r = await client.post("/v1/experience", headers=AUTH, json={"kind": "skill", "statement": "x", "status": "verified"})
        assert r.status_code == 422 and "status" in r.json()["error"]
        r = await client.patch(f"/v1/experience/{i}", headers=AUTH, json={"verified_at_ms": 1})
        assert r.status_code == 422
    _run(paths, check)


def test_verify_sets_status_and_time_and_keeps_an_optional_note(paths):
    async def check(client, runtime, _app):
        i = await _item(client, kind="cert", statement="CompTIA Security+")
        v = (await client.post(f"/v1/experience/{i}/verify", headers=AUTH, json={"note": "cert id checked"})).json()
        assert v["status"] == "verified" and v["verified_at_ms"] and v["attributes"]["verify_note"] == "cert id checked"
        u = (await client.post(f"/v1/experience/{i}/unverify", headers=AUTH)).json()
        assert (u["status"], u["verified_at_ms"]) == ("self_reported", None)
        assert (await client.post("/v1/experience/999/verify", headers=AUTH)).status_code == 404
    _run(paths, check)


def test_editing_what_a_verified_item_says_clears_verification(paths):
    async def check(client, runtime, _app):
        i = await _item(client, kind="role", statement="L1 NOC/SOC engineer", start_date="2024-01")
        await client.post(f"/v1/experience/{i}/verify", headers=AUTH)
        same = (await client.patch(f"/v1/experience/{i}", headers=AUTH, json={"source": "resume:1"})).json()
        assert same == {"verification_cleared": False}  # where it came from is not what it says
        assert (await client.get("/v1/experience", headers=AUTH)).json()[0]["status"] == "verified"
        r = (await client.patch(f"/v1/experience/{i}", headers=AUTH, json={"statement": "L2 NOC/SOC engineer"})).json()
        assert r == {"verification_cleared": True}
        item = (await client.get("/v1/experience", headers=AUTH)).json()[0]
        assert (item["status"], item["verified_at_ms"], item["statement"]) == ("self_reported", None, "L2 NOC/SOC engineer")
    _run(paths, check)


@pytest.mark.parametrize("attrs, msg", [
    ({"type": "clearance", "level": "secret"}, "attributes.status is required"),
    ({"type": "clearance", "level": "ultra", "status": "inactive"}, "attributes.level must be"),
    ({"type": "clearance", "level": "secret", "status": "inactive", "agency": "x"}, "do not take agency"),
    ({"type": "work_authorization", "us_citizen": "yes", "authorized_to_work_us": True}, "us_citizen must be"),
    ({"type": "travel", "max_travel_percent": 150}, "max_travel_percent must be"),
    ({"type": "physical"}, "needs at least one of"),
    ({"type": "availability"}, "needs at least one of"),
    ({"type": "availability", "on_call": "yes"}, "on_call must be"),
    ({"type": "availability", "time_zone": "eastern"}, "do not take time_zone"),
    ({"type": "residency", "country": "US", "time_zone": "atlantic"}, "time_zone must be"),
    ({"type": "astrology"}, "attributes.type must be one of"),
    ({}, "attributes.type must be one of"),
])
def test_eligibility_facts_are_typed(paths, attrs, msg):
    async def check(client, runtime, _app):
        r = await client.post("/v1/experience", headers=AUTH, json={"kind": "eligibility", "statement": "fact", "attributes": attrs})
        assert r.status_code == 422 and msg in r.json()["error"], r.text
    _run(paths, check)


def test_one_fact_per_eligibility_type_except_licenses(paths):
    async def check(client, runtime, _app):
        clr = {"type": "clearance", "level": "secret", "status": "inactive", "last_active_year": 2015}
        await _item(client, kind="eligibility", statement="Secret clearance, inactive", attributes=clr)
        r = await client.post("/v1/experience", headers=AUTH, json={"kind": "eligibility", "statement": "again", "attributes": clr})
        assert r.status_code == 409
        avail = {"type": "availability", "on_call": True, "weekends": False}
        r = await client.post("/v1/experience", headers=AUTH, json={"kind": "eligibility", "statement": "on call, no weekends", "attributes": avail})
        assert r.status_code == 200, r.text
        lic = {"type": "license", "name": "FCC GROL", "active": True}
        await _item(client, kind="eligibility", statement="license 1", attributes=lic)
        await _item(client, kind="eligibility", statement="license 2", attributes={**lic, "name": "CDL"})
        r = await client.post("/v1/experience", headers=AUTH, json={"kind": "skill", "statement": "s", "attributes": {"type": "clearance"}})
        assert r.status_code == 422 and "use kind 'eligibility'" in r.json()["error"]
    _run(paths, check)


@pytest.mark.parametrize("body, msg", [
    ({"kind": "hobby", "statement": "x"}, "kind must be"),
    ({"kind": "skill", "statement": "  "}, "statement must be"),
    ({"kind": "skill", "statement": "x", "start_date": "2024-13"}, "start_date"),
    ({"kind": "skill", "statement": "x", "years": -1}, "years"),
    ({"kind": "skill", "statement": "x", "attributes": []}, "attributes must be an object"),
])
def test_item_validation_names_the_field(paths, body, msg):
    async def check(client, runtime, _app):
        r = await client.post("/v1/experience", headers=AUTH, json=body)
        assert r.status_code == 422 and msg in r.json()["error"]
        assert runtime.reader.execute("SELECT count(*) FROM experience_items").fetchone()[0] == 0
    _run(paths, check)


# ----- completeness -----

def test_completeness_defaults_off_and_sets_partially(paths):
    async def check(client, runtime, _app):
        got = (await client.get("/v1/profile/completeness", headers=AUTH)).json()
        assert (got["cert_list_complete"], got["eligibility_facts_complete"]) == (False, False)
        r = (await client.put("/v1/profile/completeness", headers=AUTH, json={"cert_list_complete": True})).json()
        assert (r["cert_list_complete"], r["eligibility_facts_complete"]) == (True, False) and r["updated_at_ms"]
        r = (await client.put("/v1/profile/completeness", headers=AUTH, json={"eligibility_facts_complete": True})).json()
        assert (r["cert_list_complete"], r["eligibility_facts_complete"]) == (True, True)
        assert (await client.put("/v1/profile/completeness", headers=AUTH, json={"cert_list_complete": 1})).status_code == 422
        assert (await client.put("/v1/profile/completeness", headers=AUTH, json={})).status_code == 422
    _run(paths, check)


def test_i_dont_have_this_saves_the_postings_own_line_once_and_can_be_removed(paths):
    """0012: the window names the requirement; the agent saves its full line (questions show it cut at 160
    characters), once however often it is answered, and Profile removes it by replacing the list."""
    async def check(client, runtime, _app):
        # Setup only: a bare requirement row (no analysis behind it), so a raw connection without the FK check.
        db = sqlite3.connect(paths.db)
        db.execute("PRAGMA foreign_keys = OFF")
        rid = db.execute("INSERT INTO requirements (posting_analysis_id, text, jd_quote, quote_found, final_class, interpretation_status) "
                         "VALUES (1, 'sql', ?, 1, 'negotiable', 'confirmed') RETURNING id",
                         ("Experience with SQL or PostgreSQL " + "x" * 200,)).fetchone()[0]
        db.commit()
        db.close()
        r = await client.post(f"/v1/requirements/{rid}/lacking", headers=AUTH)
        assert r.status_code == 200 and r.json()["lacking"] == ["Experience with SQL or PostgreSQL " + "x" * 200]
        again = (await client.post(f"/v1/requirements/{rid}/lacking", headers=AUTH)).json()
        assert len(again["lacking"]) == 1
        assert (await client.post("/v1/requirements/999999/lacking", headers=AUTH)).status_code == 404
        r = (await client.put("/v1/profile/completeness", headers=AUTH, json={"lacking": []})).json()
        assert r["lacking"] == [] and r["cert_list_complete"] is False
        assert (await client.put("/v1/profile/completeness", headers=AUTH, json={"lacking": "SQL"})).status_code == 422
        events = runtime.reader.execute("SELECT ref FROM change_events WHERE type = 'profile_changed'").fetchall()
        assert any(json.loads(e[0]) == {"what": "lacking_added", "requirement_id": rid} for e in events)
        assert not any("SQL" in e[0] for e in events)  # ids only, never the line
    _run(paths, check)


# ----- inferred-claims inbox -----

def _claim(paths, statement="Probably knows DNS troubleshooting", kind="skill") -> int:
    ev = open_connection(paths.db, "evaluation")  # only evaluation proposes claims
    cid = ev.execute("INSERT INTO inferred_claims (statement, kind, rationale, created_at_ms) VALUES (?, ?, 'from NOC role', ?) RETURNING id",
                     (statement, kind, NOW)).fetchone()[0]
    ev.close()
    return cid


def test_accepting_a_claim_creates_a_self_reported_item(paths):
    async def check(client, runtime, _app):
        cid = _claim(paths)
        assert [c["id"] for c in (await client.get("/v1/inferred-claims?state=open", headers=AUTH)).json()] == [cid]
        r = (await client.post(f"/v1/inferred-claims/{cid}/accept", headers=AUTH, json={"statement": "DNS troubleshooting"})).json()
        item = [i for i in (await client.get("/v1/experience", headers=AUTH)).json() if i["id"] == r["item_id"]][0]
        assert (item["status"], item["statement"], item["source"]) == ("self_reported", "DNS troubleshooting", f"inferred_claim:{cid}")
        [claim] = (await client.get("/v1/inferred-claims?state=accepted", headers=AUTH)).json()
        assert claim["accepted_as_item_id"] == r["item_id"]
        assert (await client.post(f"/v1/inferred-claims/{cid}/accept", headers=AUTH)).status_code == 409
        assert (await client.post(f"/v1/inferred-claims/{cid}/reject", headers=AUTH)).status_code == 409
        # The accepted item keeps its claim link, so it cannot be deleted out from under it.
        assert (await client.delete(f"/v1/experience/{r['item_id']}", headers=AUTH)).status_code == 409
    _run(paths, check)


def test_rejecting_a_claim_and_refusing_eligibility_from_inference(paths):
    async def check(client, runtime, _app):
        cid = _claim(paths)
        assert (await client.post(f"/v1/inferred-claims/{cid}/reject", headers=AUTH)).json() == {"rejected": cid}
        assert (await client.get("/v1/inferred-claims?state=open", headers=AUTH)).json() == []
        cid2 = _claim(paths, "US citizen", "skill")
        r = await client.post(f"/v1/inferred-claims/{cid2}/accept", headers=AUTH, json={"kind": "eligibility"})
        assert r.status_code == 422
        assert (await client.get("/v1/inferred-claims?state=bogus", headers=AUTH)).status_code == 422
        assert (await client.post("/v1/inferred-claims/999/accept", headers=AUTH)).status_code == 404
    _run(paths, check)


def test_profile_events_and_logs_carry_ids_not_content(paths, capsys, caplog):
    caplog.set_level("INFO")
    async def check(client, runtime, _app):
        i = await _item(client, kind="skill", statement="SECRET-STATEMENT-TEXT")
        await client.post(f"/v1/experience/{i}/verify", headers=AUTH, json={"note": "SECRET-NOTE"})
        await client.post("/v1/resumes", headers=AUTH, params={"filename": "a.md"}, content=b"SECRET-RESUME-TEXT")
        refs = [json.loads(e["ref"]) for e in runtime.events_after(0) if e["type"] == "profile_changed"]
        assert [r["what"] for r in refs] == ["item_created", "item_verified", "resume_uploaded"]
        assert all(set(r) <= {"what", "id", "claim_id"} for r in refs)
    _run(paths, check)
    out = capsys.readouterr()
    logged = out.out + out.err + caplog.text  # structlog prints, or goes via stdlib logging once configured
    assert "profile_changed" in logged and "SECRET-" not in logged


# ----- the guarantees D2 rests on, pinned at the source -----

def _string_writes():
    """(file, enclosing function, SQL string) for every string literal that writes experience_items."""
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        owner = {}  # constant -> innermost enclosing function (outer functions are walked first)
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for node in ast.walk(fn):
                    owner[id(node)] = fn.name
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                s = " ".join(node.value.split()).upper()
                if "EXPERIENCE_ITEMS" in s and ("UPDATE" in s or "INSERT" in s):
                    yield path.relative_to(SRC).as_posix(), owner.get(id(node), "<module>"), node.value


def test_only_verify_item_writes_verified():
    writers = {(f, fn) for f, fn, sql in _string_writes() if "'verified'" in sql}
    assert writers == {("profile/items.py", "verify_item")}, writers
    inserts = [sql for _, _, sql in _string_writes() if "INSERT" in sql.upper()]
    assert inserts and all("'self_reported'" in s and "'verified'" not in s for s in inserts)


def test_only_the_runtime_opens_the_user_action_connection():
    opened = sorted(p.relative_to(SRC).as_posix() for p in SRC.rglob("*.py")
                    if '"user")' in p.read_text(encoding="utf-8") and "open_connection" in p.read_text(encoding="utf-8"))
    assert opened == ["agent.py"], opened
