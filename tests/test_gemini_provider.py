import json

import httpx

from discord_agent_hub.models import AgentDefinition, MessageRecord, ProviderKind
from discord_agent_hub.providers.gemini_api import GeminiAPIProvider


async def test_gemini_provider_maps_conversation_and_extracts_text():
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["json"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {"content": {"parts": [{"text": "First answer."}, {"text": "Second answer."}]}}
                ]
            },
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://generativelanguage.googleapis.com",
    )
    provider = GeminiAPIProvider(
        api_key="gemini-key",
        default_model="gemini-2.5-pro",
        http_client=client,
    )
    agent = AgentDefinition(
        id="gemini-default",
        name="Gemini Default",
        provider=ProviderKind.GEMINI_API,
        model="gemini-2.5-pro",
        instructions="Answer concisely.",
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
            author_name="Gemini Default",
            content="Hi",
            created_at="2026-04-04T00:00:01+00:00",
        ),
    ]

    response = await provider.generate(
        agent=agent,
        conversation=conversation,
        provider_session_id=None,
    )

    assert captured["url"] == "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-pro:generateContent?key=gemini-key"
    assert captured["json"]["systemInstruction"] == {
        "parts": [{"text": "Answer concisely."}]
    }
    assert captured["json"]["contents"] == [
        {"role": "user", "parts": [{"text": "alice: Hello"}]},
        {"role": "model", "parts": [{"text": "Gemini Default: Hi"}]},
    ]
    assert response.output_text == "First answer.\nSecond answer."


async def test_gemini_provider_requires_api_key():
    provider = GeminiAPIProvider(api_key=None, default_model="gemini-2.5-pro")
    agent = AgentDefinition(
        id="gemini-default",
        name="Gemini Default",
        provider=ProviderKind.GEMINI_API,
        instructions="",
    )

    try:
        await provider.generate(agent=agent, conversation=[], provider_session_id=None)
    except RuntimeError as exc:
        assert "GEMINI_API_KEY" in str(exc)
    else:
        raise AssertionError("Expected RuntimeError when API key is missing")


async def test_gemini_provider_adds_selected_tools_to_request():
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": "done"}]}}]},
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://generativelanguage.googleapis.com",
    )
    provider = GeminiAPIProvider(
        api_key="gemini-key",
        default_model="gemini-2.5-pro",
        http_client=client,
    )
    agent = AgentDefinition(
        id="gemini-tools",
        name="Gemini Tools",
        provider=ProviderKind.GEMINI_API,
        tools={"web_search": True, "code_execution": True},
    )

    await provider.generate(agent=agent, conversation=[], provider_session_id=None)

    assert captured["json"]["tools"] == [
        {"google_search": {}},
        {"code_execution": {}},
    ]
