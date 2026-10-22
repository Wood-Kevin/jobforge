"""Personal data and secrets never reach the log file (ARCHITECTURE 8)."""
import json

from jobforge_agent import logs

FAKE_RESUME = "Kevin Wood resume text: CCNA, U.S. citizen, Secret clearance"
FAKE_TOKEN = "f00dfeed" * 8


def test_sensitive_fields_are_redacted_at_any_depth(tmp_path):
    path = logs.configure(tmp_path)
    log = logs.get("test")
    log.info("evaluation_done", job_id=7, resume_text=FAKE_RESUME, token=FAKE_TOKEN,
             details={"statement": FAKE_RESUME, "count": 3, "items": [{"jd_quote": FAKE_RESUME}]},
             prompt="system prompt with " + FAKE_RESUME, response={"content": FAKE_RESUME})
    logs.close()
    text = path.read_text(encoding="utf-8")
    assert FAKE_RESUME not in text and FAKE_TOKEN not in text
    record = json.loads(text.strip().splitlines()[-1])
    assert record["job_id"] == 7 and record["details"]["count"] == 3
    assert record["resume_text"] == logs.REDACTED
    assert record["details"]["statement"] == logs.REDACTED
    assert record["details"]["items"][0]["jd_quote"] == logs.REDACTED


def test_log_lines_are_json_with_event_and_timestamp(tmp_path):
    path = logs.configure(tmp_path)
    logs.get("test").info("agent_start", run_id=1)
    logs.close()
    record = json.loads(path.read_text(encoding="utf-8").strip())
    assert record["event"] == "agent_start" and "timestamp" in record and record["level"] == "info"
