from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

from discord_agent_hub.bot import handle_user_message
from discord_agent_hub.models import ProviderResponse
from discord_agent_hub.providers.base import ProviderRegistry
from discord_agent_hub.storage import AgentStore, HubStore
from discord_agent_hub.structured_log import StructuredLogger


class TimedProvider:
    def __init__(self, delay: float = 0.01) -> None:
        self.delay = delay
        self.calls: list[dict] = []

    async def generate(self, *, agent, conversation, provider_session_id):
        self.calls.append(
            {
                "agent_id": agent.id,
                "conversation_size": len(conversation),
                "contents": [item.content for item in conversation],
            }
        )
        await asyncio.sleep(self.delay)
        return ProviderResponse(
            output_text=f"reply {len(self.calls)}",
            provider_session_id=provider_session_id,
            raw_payload={"ok": True},
        )


class FakeChannel:
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id
        self.sent_messages: list[str] = []

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
        settings=SimpleNamespace(
            provider_request_timeout_seconds=1.0,
            provider_max_retries=2,
            provider_retry_backoff_seconds=0.0,
        ),
    )


async def test_load_same_thread_is_serialized_under_burst(tmp_path):
    provider = TimedProvider(delay=0.01)
    bot = _build_fake_bot(tmp_path, "openai_responses", provider)
    session = bot.hub_store.create_session(
        agent_id="gpt-default",
        provider="openai_responses",
        discord_channel_id=100,
        discord_thread_id=200,
        discord_guild_id=300,
        created_by_user_id=400,
    )
    channel = FakeChannel(200)

    messages = [
        SimpleNamespace(
            author=SimpleNamespace(id=1000 + index, display_name=f"user{index}"),
            content=f"message {index}",
            channel=channel,
        )
        for index in range(12)
    ]

    started = time.perf_counter()
    await asyncio.gather(*(handle_user_message(bot, message) for message in messages))
    elapsed = time.perf_counter() - started

    assert len(provider.calls) == 12
    assert provider.calls[0]["contents"] == ["message 0"]
    assert provider.calls[-1]["contents"] == [
        "message 0",
        "reply 1",
        "message 1",
        "reply 2",
        "message 2",
        "reply 3",
        "message 3",
        "reply 4",
        "message 4",
        "reply 5",
        "message 5",
        "reply 6",
        "message 6",
        "reply 7",
        "message 7",
        "reply 8",
        "message 8",
        "reply 9",
        "message 9",
        "reply 10",
        "message 10",
        "reply 11",
        "message 11",
    ]
    assert len(bot.hub_store.list_messages(session.id)) == 24
    assert len(channel.sent_messages) == 12
    assert elapsed >= 0.11


async def test_load_multiple_threads_can_progress_concurrently(tmp_path):
    provider = TimedProvider(delay=0.05)
    bot = _build_fake_bot(tmp_path, "openai_responses", provider)

    channels = []
    messages = []
    for thread_id in range(200, 210):
        bot.hub_store.create_session(
            agent_id="gpt-default",
            provider="openai_responses",
            discord_channel_id=100,
            discord_thread_id=thread_id,
            discord_guild_id=300,
            created_by_user_id=400,
        )
        channel = FakeChannel(thread_id)
        channels.append(channel)
        messages.append(
            SimpleNamespace(
                author=SimpleNamespace(id=thread_id, display_name=f"user{thread_id}"),
                content=f"hello from {thread_id}",
                channel=channel,
            )
        )

    started = time.perf_counter()
    await asyncio.gather(*(handle_user_message(bot, message) for message in messages))
    elapsed = time.perf_counter() - started

    assert len(provider.calls) == 10
    assert elapsed < 0.30
    assert all(channel.sent_messages for channel in channels)
