import json

from discord_agent_hub.structured_log import StructuredLogger


def test_structured_logger_writes_jsonl(tmp_path):
    path = tmp_path / "events.jsonl"
    logger = StructuredLogger(path)

    logger.append("session.created", session_id="s1", agent_id="a1")

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1

    payload = json.loads(lines[0])
    assert payload["event"] == "session.created"
    assert payload["session_id"] == "s1"
    assert payload["agent_id"] == "a1"
    assert "ts" in payload


def test_structured_logger_filters_events_by_session_id(tmp_path):
    path = tmp_path / "events.jsonl"
    logger = StructuredLogger(path)

    logger.append("session.created", session_id="s1")
    logger.append("session.created", session_id="s2")

    events = logger.list_events(session_id="s1")

    assert len(events) == 1
    assert events[0]["session_id"] == "s1"
