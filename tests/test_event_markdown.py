from discord_agent_hub.event_markdown import render_event_markdown


def test_render_event_markdown_includes_timeline_and_usage():
    events = [
        {
            "ts": "2026-04-07T00:00:00+00:00",
            "event": "session.created",
            "session_id": "s1",
            "agent_id": "gpt-default",
            "provider": "openai_responses",
            "created_by_user_id": 123,
        },
        {
            "ts": "2026-04-07T00:00:10+00:00",
            "event": "message.user",
            "session_id": "s1",
            "author_name": "alice",
            "user_id": 123,
            "content": "Hello",
        },
        {
            "ts": "2026-04-07T00:00:12+00:00",
            "event": "response.assistant",
            "session_id": "s1",
            "model": "gpt-5.2",
            "content": "Hi",
            "usage": {
                "input_tokens": 11,
                "output_tokens": 7,
                "total_tokens": 18,
            },
        },
    ]

    markdown = render_event_markdown(events)

    assert "# Session Events" in markdown
    assert "### session.created" in markdown
    assert "### message.user" in markdown
    assert "### response.assistant" in markdown
    assert "- model: `gpt-5.2`" in markdown
    assert "- total_tokens: `18`" in markdown
    assert "```text\nHello\n```" in markdown
