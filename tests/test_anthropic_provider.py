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
        {"role": "user", "content": [{"type": "text", "text": "alice: Hello"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Hi there"}]},
    ]
    assert response.output_text == "First line.\nSecond line."
    assert response.usage == {
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "cache_creation_input_tokens": None,
        "cache_read_input_tokens": None,
    }


async def test_anthropic_provider_computes_total_tokens_from_input_and_output():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "done"}],
                "usage": {
                    "input_tokens": 123,
                    "output_tokens": 45,
                    "cache_creation_input_tokens": 10,
                    "cache_read_input_tokens": 20,
                },
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
    )

    response = await provider.generate(agent=agent, conversation=[], provider_session_id=None)

    assert response.usage == {
        "input_tokens": 123,
        "output_tokens": 45,
        "total_tokens": 168,
        "cache_creation_input_tokens": 10,
        "cache_read_input_tokens": 20,
    }


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


async def test_anthropic_provider_includes_image_attachments_in_user_messages():
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
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
        id="claude-default",
        name="Claude Default",
        provider=ProviderKind.ANTHROPIC_MESSAGES,
    )
    conversation = [
        MessageRecord(
            session_id="s1",
            role="user",
            author_id=1,
            author_name="alice",
            content="Describe this image",
            attachments=[
                {
                    "type": "image",
                    "filename": "cat.png",
                    "media_type": "image/png",
                    "data": "ZmFrZQ==",
                }
            ],
            created_at="2026-04-06T00:00:00+00:00",
        )
    ]

    await provider.generate(agent=agent, conversation=conversation, provider_session_id=None)

    assert captured["json"]["messages"][0]["content"] == [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": "ZmFrZQ==",
            },
        },
        {
            "type": "text",
            "text": "alice: Describe this image",
        },
    ]


async def test_anthropic_provider_omits_empty_text_when_image_only():
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
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
        id="claude-default",
        name="Claude Default",
        provider=ProviderKind.ANTHROPIC_MESSAGES,
    )
    conversation = [
        MessageRecord(
            session_id="s1",
            role="user",
            author_id=1,
            author_name="alice",
            content="",
            attachments=[
                {
                    "type": "image",
                    "filename": "cat.png",
                    "media_type": "image/png",
                    "data": "ZmFrZQ==",
                }
            ],
            created_at="2026-04-06T00:00:00+00:00",
        )
    ]

    await provider.generate(agent=agent, conversation=conversation, provider_session_id=None)

    assert captured["json"]["messages"][0]["content"] == [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": "ZmFrZQ==",
            },
        }
    ]


async def test_anthropic_provider_renders_document_attachments_as_text():
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
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
        id="claude-default",
        name="Claude Default",
        provider=ProviderKind.ANTHROPIC_MESSAGES,
    )
    conversation = [
        MessageRecord(
            session_id="s1",
            role="user",
            author_id=1,
            author_name="alice",
            content="Summarize this document",
            attachments=[
                {
                    "type": "document",
                    "filename": "notes.md",
                    "media_type": "text/markdown",
                    "text": "# Heading\n\nBody text.",
                }
            ],
            created_at="2026-04-06T00:00:00+00:00",
        )
    ]

    await provider.generate(agent=agent, conversation=conversation, provider_session_id=None)

    assert captured["json"]["messages"][0]["content"] == [
        {
            "type": "text",
            "text": "alice: Summarize this document\n\n[Attached document: notes.md]\n# Heading\n\nBody text.",
        }
    ]
