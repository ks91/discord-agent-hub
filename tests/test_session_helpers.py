import pytest

from discord_agent_hub.bot import (
    _build_transcript_markdown,
    _summarize_usage,
    _usage_report_lines,
    _usage_report_lines_for_guild,
)
from discord_agent_hub.models import AgentDefinition, MessageRecord, ProviderKind, SessionRecord


def test_summarize_usage_totals_response_events():
    usage = _summarize_usage(
        [
            {"event": "response.assistant", "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}},
            {"event": "response.assistant", "usage": {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}},
            {"event": "message.user"},
        ]
    )

    assert usage == {
        "input_tokens": 17,
        "output_tokens": 8,
        "total_tokens": 25,
    }


def test_build_transcript_markdown_includes_metadata_and_messages():
    session = SessionRecord(
        id="s1",
        agent_id="gpt-default",
        provider="openai_responses",
        discord_channel_id=1,
        discord_thread_id=2,
        discord_guild_id=3,
        created_by_user_id=4,
        created_at="2026-04-06T00:00:00+00:00",
    )
    agent = AgentDefinition(
        id="gpt-default",
        name="GPT Default",
        provider=ProviderKind.OPENAI_RESPONSES,
    )
    messages = [
        MessageRecord(
            session_id="s1",
            role="user",
            author_id=1,
            author_name="alice",
            content="hello",
            created_at="2026-04-06T00:00:01+00:00",
        ),
        MessageRecord(
            session_id="s1",
            role="assistant",
            author_id=None,
            author_name="GPT Default",
            content="hi",
            created_at="2026-04-06T00:00:02+00:00",
        ),
    ]

    markdown = _build_transcript_markdown(
        session=session,
        agent=agent,
        messages=messages,
        usage={"input_tokens": 10, "output_tokens": 6, "total_tokens": 16},
    )

    assert "session_id: `s1`" in markdown
    assert "agent_name: GPT Default" in markdown
    assert "alice: hello" in markdown
    assert "### GPT Default (assistant)" in markdown
    assert "\nhi\n" in markdown


def test_usage_report_lines_aggregate_top_counts():
    lines = _usage_report_lines(
        [
            {
                "event": "response.assistant",
                "provider": "openai_responses",
                "agent_id": "gpt-default",
                "created_by_user_id": 1,
                "discord_guild_id": 100,
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            },
            {
                "event": "response.assistant",
                "provider": "anthropic_messages",
                "agent_id": "claude-default",
                "created_by_user_id": 2,
                "discord_guild_id": 100,
                "usage": {"input_tokens": 8, "output_tokens": 4, "total_tokens": 12},
            },
            {
                "event": "response.assistant",
                "provider": "openai_responses",
                "agent_id": "gpt-default",
                "created_by_user_id": 1,
                "discord_guild_id": 100,
                "usage": {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10},
            },
        ],
        guild_id=100,
    )

    payload = "\n".join(lines)
    assert "Responses: `3`" in payload
    assert "Input tokens: `25`" in payload
    assert "- `openai_responses`: 2" in payload
    assert "- `gpt-default`: 2" in payload
    assert "- `1`: 2" in payload


@pytest.mark.asyncio
async def test_usage_report_lines_for_guild_prefers_display_names():
    class FakeGuild:
        id = 100

        def get_member(self, user_id):
            if user_id == 1:
                return type("Member", (), {"display_name": "Alice"})()
            return None

    lines = await _usage_report_lines_for_guild(
        [
            {
                "event": "response.assistant",
                "provider": "openai_responses",
                "agent_id": "gpt-default",
                "created_by_user_id": 1,
                "discord_guild_id": 100,
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            }
        ],
        guild=FakeGuild(),
    )

    payload = "\n".join(lines)
    assert "Top users" in payload
    assert "- `Alice (1)`: 1" in payload
