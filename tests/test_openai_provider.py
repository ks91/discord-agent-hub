from types import SimpleNamespace

from discord_agent_hub.models import AgentDefinition, MessageRecord, ProviderKind
from discord_agent_hub.providers.openai_responses import OpenAIResponsesProvider


class FakeResponsesAPI:
    def __init__(self) -> None:
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            output_text="reply",
            model_dump=lambda: {"id": "resp_123"},
        )


class FakeOpenAIClient:
    def __init__(self) -> None:
        self.responses = FakeResponsesAPI()


async def test_openai_provider_uses_input_text_for_user_and_output_text_for_assistant():
    provider = OpenAIResponsesProvider(api_key="test-key", default_model="gpt-5.2")
    provider.client = FakeOpenAIClient()
    agent = AgentDefinition(
        id="openai-default",
        name="OpenAI Default",
        provider=ProviderKind.OPENAI_RESPONSES,
        model="gpt-5.2",
        instructions="Be precise.",
    )
    conversation = [
        MessageRecord(
            session_id="s1",
            role="user",
            author_id=1,
            author_name="alice",
            content="Hello",
            created_at="2026-04-05T00:00:00+00:00",
        ),
        MessageRecord(
            session_id="s1",
            role="assistant",
            author_id=None,
            author_name="OpenAI Default",
            content="Hi there",
            created_at="2026-04-05T00:00:01+00:00",
        ),
    ]

    response = await provider.generate(
        agent=agent,
        conversation=conversation,
        provider_session_id=None,
    )

    call = provider.client.responses.calls[0]
    assert call["model"] == "gpt-5.2"
    assert call["instructions"] == "Be precise."
    assert call["input"] == [
        {
            "role": "user",
            "content": [{"type": "input_text", "text": "alice: Hello"}],
        },
        {
            "role": "assistant",
            "content": [{"type": "output_text", "text": "OpenAI Default: Hi there"}],
        },
    ]
    assert response.output_text == "reply"


async def test_openai_provider_adds_selected_tools_to_request():
    provider = OpenAIResponsesProvider(api_key="test-key", default_model="gpt-5.2")
    provider.client = FakeOpenAIClient()
    agent = AgentDefinition(
        id="openai-tools",
        name="OpenAI Tools",
        provider=ProviderKind.OPENAI_RESPONSES,
        model="gpt-5.2",
        tools={"web_search": True, "code_execution": True},
    )

    await provider.generate(agent=agent, conversation=[], provider_session_id=None)

    call = provider.client.responses.calls[0]
    assert call["tools"] == [
        {"type": "web_search"},
        {"type": "code_interpreter", "container": {"type": "auto"}},
    ]
    assert call["tool_choice"] == "auto"
