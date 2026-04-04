from discord_agent_hub.models import AgentDefinition, MessageRecord, ProviderKind
from discord_agent_hub.providers.cli_stub import CLIStubProvider


async def test_cli_stub_provider_returns_traceable_message():
    provider = CLIStubProvider(name="claude_code", command="claude")
    agent = AgentDefinition(
        id="claude-code-default",
        name="Claude Code Default",
        provider=ProviderKind.CLAUDE_CODE,
        instructions="",
    )
    conversation = [
        MessageRecord(
            session_id="s1",
            role="user",
            author_id=1,
            author_name="alice",
            content="Investigate this bug",
            created_at="2026-04-04T00:00:00+00:00",
        )
    ]

    response = await provider.generate(
        agent=agent,
        conversation=conversation,
        provider_session_id=None,
    )

    assert "[claude_code provider stub]" in response.output_text
    assert "Investigate this bug" in response.output_text
