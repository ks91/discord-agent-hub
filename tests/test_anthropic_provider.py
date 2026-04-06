import json

import httpx

from discord_agent_hub.models import AgentDefinition, MessageRecord, ProviderKind
from discord_agent_hub.providers.anthropic_messages import AnthropicMessagesProvider


async def test_anthropic_provider_maps_conversation_and_extracts_text():
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["json"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "msg_123",
                "content": [
                    {"type": "text", "text": "First line."},
                    {"type": "text", "text": "Second line."},
                ],
            },
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.anthropic.com",
    )
    provider = AnthropicMessagesProvider(
        api_key="test-key",
        default_model="claude-sonnet-4-0",
        http_client=client,
    )
    agent = AgentDefinition(
        id="claude-default",
        name="Claude Default",
        provider=ProviderKind.ANTHROPIC_MESSAGES,
        model="claude-sonnet-4-0",
        instructions="Be precise.",
    )
    conversation = [
        MessageRecord(
            session_id="s1",
            role="user",
            author_id=1,
            author_name="alice",
            content="Hello",
            created_at="2026-04-04T00:00:00+00:00",
        ),
        MessageRecord(
            session_id="s1",
            role="assistant",
            author_id=None,
            author_name="Claude Default",
            content="Hi there",
            created_at="2026-04-04T00:00:01+00:00",
        ),
    ]

    response = await provider.generate(
        agent=agent,
        conversation=conversation,
        provider_session_id=None,
    )

    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["headers"]["x-api-key"] == "test-key"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    assert captured["json"]["model"] == "claude-sonnet-4-0"
    assert captured["json"]["system"] == "Be precise."
    assert captured["json"]["messages"] == [
        {"role": "user", "content": "alice: Hello"},
        {"role": "assistant", "content": "Claude Default: Hi there"},
    ]
    assert response.output_text == "First line.\nSecond line."


async def test_anthropic_provider_requires_api_key():
    provider = AnthropicMessagesProvider(api_key=None, default_model="claude-sonnet-4-0")
    agent = AgentDefinition(
        id="claude-default",
        name="Claude Default",
        provider=ProviderKind.ANTHROPIC_MESSAGES,
        instructions="",
    )

    try:
        await provider.generate(agent=agent, conversation=[], provider_session_id=None)
    except RuntimeError as exc:
        assert "ANTHROPIC_API_KEY" in str(exc)
    else:
        raise AssertionError("Expected RuntimeError when API key is missing")


async def test_anthropic_provider_adds_selected_tools_and_beta_header():
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["json"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"content": [{"type": "text", "text": "done"}]})

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.anthropic.com",
    )
    provider = AnthropicMessagesProvider(
        api_key="test-key",
        default_model="claude-sonnet-4-0",
        http_client=client,
    )
    agent = AgentDefinition(
        id="anthropic-tools",
        name="Anthropic Tools",
        provider=ProviderKind.ANTHROPIC_MESSAGES,
        tools={"web_search": True, "code_execution": True},
    )

    await provider.generate(agent=agent, conversation=[], provider_session_id=None)

    assert captured["json"]["tools"] == [
        {"type": "web_search_20250305", "name": "web_search", "max_uses": 5},
        {"type": "code_execution_20250825", "name": "code_execution"},
    ]
    assert captured["headers"]["anthropic-beta"] == "code-execution-2025-08-25"
