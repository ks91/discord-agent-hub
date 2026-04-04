from types import SimpleNamespace

from discord_agent_hub.bot import handle_user_message
from discord_agent_hub.models import ProviderResponse
from discord_agent_hub.providers.base import ProviderRegistry
from discord_agent_hub.storage import AgentStore, HubStore
from discord_agent_hub.structured_log import StructuredLogger


class FakeProvider:
    def __init__(self, response: ProviderResponse) -> None:
        self.response = response
        self.calls = []

    async def generate(self, *, agent, conversation, provider_session_id):
        self.calls.append(
            {
                "agent_id": agent.id,
                "conversation": conversation,
                "provider_session_id": provider_session_id,
            }
        )
        return self.response


class FailingProvider:
    async def generate(self, *, agent, conversation, provider_session_id):
        raise RuntimeError("provider exploded")


class FakeChannel:
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id
        self.sent_messages = []

    async def send(self, content: str) -> None:
        self.sent_messages.append(content)


def _build_fake_bot(tmp_path, provider_name: str, provider) -> SimpleNamespace:
    agent_store = AgentStore(tmp_path / "agents.json")
    hub_store = HubStore(tmp_path / "hub.sqlite3")
    structured_logger = StructuredLogger(tmp_path / "events.jsonl")
    registry = ProviderRegistry()
    registry.register(provider_name, provider)
    return SimpleNamespace(
        agent_store=agent_store,
        hub_store=hub_store,
        structured_logger=structured_logger,
        provider_registry=registry,
    )


async def test_handle_user_message_routes_to_provider_and_persists(tmp_path):
    provider = FakeProvider(
        ProviderResponse(
            output_text="assistant reply",
            provider_session_id="provider-session-1",
            raw_payload={"ok": True},
        )
    )
    bot = _build_fake_bot(tmp_path, "openai_responses", provider)
    session = bot.hub_store.create_session(
        agent_id="openai-default",
        provider="openai_responses",
        discord_channel_id=100,
        discord_thread_id=200,
        discord_guild_id=300,
        created_by_user_id=400,
    )
    channel = FakeChannel(200)
    message = SimpleNamespace(
        author=SimpleNamespace(id=123, display_name="alice"),
        content="hello world",
        channel=channel,
    )

    await handle_user_message(bot, message)

    assert len(provider.calls) == 1
    assert provider.calls[0]["agent_id"] == "openai-default"
    assert [item.role for item in provider.calls[0]["conversation"]] == ["user"]
    assert channel.sent_messages == ["assistant reply"]

    messages = bot.hub_store.list_messages(session.id)
    assert [item.role for item in messages] == ["user", "assistant"]
    assert messages[0].content == "hello world"
    assert messages[1].content == "assistant reply"

    reloaded = bot.hub_store.get_session_by_thread_id(200)
    assert reloaded is not None
    assert reloaded.provider_session_id == "provider-session-1"

    event_log = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    assert "message.user" in event_log
    assert "response.assistant" in event_log


async def test_handle_user_message_reports_provider_error(tmp_path):
    bot = _build_fake_bot(tmp_path, "openai_responses", FailingProvider())
    bot.hub_store.create_session(
        agent_id="openai-default",
        provider="openai_responses",
        discord_channel_id=100,
        discord_thread_id=200,
        discord_guild_id=300,
        created_by_user_id=400,
    )
    channel = FakeChannel(200)
    message = SimpleNamespace(
        author=SimpleNamespace(id=123, display_name="alice"),
        content="hello world",
        channel=channel,
    )

    await handle_user_message(bot, message)

    assert channel.sent_messages == ["Provider error: provider exploded"]
    event_log = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    assert "provider.error" in event_log
