import asyncio
from types import SimpleNamespace

from discord_agent_hub.bot import (
    _compact_conversation_for_provider,
    _extract_downloadable_code_blocks,
    handle_user_message,
)
from discord_agent_hub.models import GeneratedFile, ProviderResponse
from discord_agent_hub.models import MessageRecord
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


class SlowSequentialProvider:
    def __init__(self) -> None:
        self.calls = []

    async def generate(self, *, agent, conversation, provider_session_id):
        self.calls.append([item.content for item in conversation])
        await asyncio.sleep(0.05)
        return ProviderResponse(
            output_text=f"reply {len(self.calls)}",
            provider_session_id=provider_session_id,
            raw_payload={"ok": True},
        )


class FlakyProvider:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    async def generate(self, *, agent, conversation, provider_session_id):
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError("Anthropic API error 503: temporary upstream failure")
        return ProviderResponse(
            output_text="recovered reply",
            provider_session_id=provider_session_id,
            raw_payload={"ok": True},
        )


class HangingProvider:
    async def generate(self, *, agent, conversation, provider_session_id):
        await asyncio.sleep(0.05)
        return ProviderResponse(
            output_text="late reply",
            provider_session_id=provider_session_id,
            raw_payload={"ok": True},
        )


class FakeChannel:
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id
        self.sent_messages = []
        self.sent_files = []

    async def send(self, content: str, file=None) -> None:
        self.sent_messages.append(content)
        if file is not None:
            self.sent_files.append(file)


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
            disallowed_role_ids=set(),
            provider_request_timeout_seconds=1.0,
            provider_max_retries=2,
            provider_retry_backoff_seconds=0.0,
        ),
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
        agent_id="gpt-default",
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
    assert provider.calls[0]["agent_id"] == "gpt-default"
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
    assert '"model": "gpt-5.2"' in event_log


async def test_handle_user_message_reports_provider_error(tmp_path):
    bot = _build_fake_bot(tmp_path, "openai_responses", FailingProvider())
    bot.hub_store.create_session(
        agent_id="gpt-default",
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


async def test_handle_user_message_ignores_empty_message_without_attachments(tmp_path):
    provider = FakeProvider(
        ProviderResponse(
            output_text="assistant reply",
            provider_session_id=None,
            raw_payload={"ok": True},
        )
    )
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
    message = SimpleNamespace(
        author=SimpleNamespace(id=123, display_name="alice"),
        content="   ",
        attachments=[],
        channel=channel,
    )

    await handle_user_message(bot, message)

    assert provider.calls == []
    assert bot.hub_store.list_messages(session.id) == []
    assert channel.sent_messages == []
    event_log = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    assert "message.empty_ignored" in event_log


async def test_handle_user_message_ignores_empty_message_with_unsupported_attachment(tmp_path):
    provider = FakeProvider(
        ProviderResponse(
            output_text="assistant reply",
            provider_session_id=None,
            raw_payload={"ok": True},
        )
    )
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

    async def read_attachment():
        return b"ignored"

    attachment = SimpleNamespace(
        filename="archive.bin",
        content_type="application/octet-stream",
        read=read_attachment,
    )
    message = SimpleNamespace(
        author=SimpleNamespace(id=123, display_name="alice"),
        content="",
        attachments=[attachment],
        channel=channel,
    )

    await handle_user_message(bot, message)

    assert provider.calls == []
    assert bot.hub_store.list_messages(session.id) == []
    assert channel.sent_messages == []
    event_log = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    assert "message.empty_ignored" in event_log
    assert '"original_attachment_count": 1' in event_log


async def test_handle_user_message_attaches_latex_source_when_present(tmp_path):
    provider = FakeProvider(
        ProviderResponse(
            output_text=(
                "Here is the source:\n\n"
                "```latex\n"
                "\\documentclass{article}\n"
                "\\begin{document}\n"
                "Hello\n"
                "\\end{document}\n"
                "```"
            ),
            provider_session_id=None,
            raw_payload={"ok": True},
        )
    )
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
    message = SimpleNamespace(
        author=SimpleNamespace(id=123, display_name="alice"),
        content="make latex",
        channel=channel,
    )

    await handle_user_message(bot, message)

    assert channel.sent_messages[-1] == "LaTeX source attached."
    assert len(channel.sent_files) == 1
    assert channel.sent_files[0].filename == f"{session.id}-latex-source.tex"
    channel.sent_files[0].fp.seek(0)
    assert channel.sent_files[0].fp.read().decode("utf-8") == (
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "Hello\n"
        "\\end{document}\n"
    )


async def test_handle_user_message_attaches_python_source_when_present(tmp_path):
    provider = FakeProvider(
        ProviderResponse(
            output_text=(
                "Here is a script:\n\n"
                "```python\n"
                "print('hello')\n"
                "```"
            ),
            provider_session_id=None,
            raw_payload={"ok": True},
        )
    )
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
    message = SimpleNamespace(
        author=SimpleNamespace(id=123, display_name="alice"),
        content="make python",
        channel=channel,
    )

    await handle_user_message(bot, message)

    assert channel.sent_messages[-1] == "Python source attached."
    assert len(channel.sent_files) == 1
    assert channel.sent_files[0].filename == f"{session.id}-python-source.py"
    channel.sent_files[0].fp.seek(0)
    assert channel.sent_files[0].fp.read().decode("utf-8") == "print('hello')\n"


async def test_handle_user_message_attaches_generated_files(tmp_path):
    provider = FakeProvider(
        ProviderResponse(
            output_text="I generated a CSV.",
            provider_session_id=None,
            raw_payload={"ok": True},
            generated_files=[
                GeneratedFile(
                    filename="result.csv",
                    media_type="text/csv",
                    data=b"name,score\nalice,42\n",
                    source_provider="openai_responses",
                )
            ],
        )
    )
    bot = _build_fake_bot(tmp_path, "openai_responses", provider)
    bot.hub_store.create_session(
        agent_id="gpt-default",
        provider="openai_responses",
        discord_channel_id=100,
        discord_thread_id=200,
        discord_guild_id=300,
        created_by_user_id=400,
    )
    channel = FakeChannel(200)
    message = SimpleNamespace(
        author=SimpleNamespace(id=123, display_name="alice"),
        content="make csv",
        channel=channel,
    )

    await handle_user_message(bot, message)

    assert channel.sent_messages == [
        "I generated a CSV.",
        "Generated file attached: `result.csv`",
    ]
    assert len(channel.sent_files) == 1
    assert channel.sent_files[0].filename == "result.csv"
    channel.sent_files[0].fp.seek(0)
    assert channel.sent_files[0].fp.read() == b"name,score\nalice,42\n"


async def test_handle_user_message_serializes_same_thread_requests(tmp_path):
    provider = SlowSequentialProvider()
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
    first = SimpleNamespace(
        author=SimpleNamespace(id=123, display_name="alice"),
        content="first message",
        channel=channel,
    )
    second = SimpleNamespace(
        author=SimpleNamespace(id=124, display_name="bob"),
        content="second message",
        channel=channel,
    )

    await asyncio.gather(
        handle_user_message(bot, first),
        handle_user_message(bot, second),
    )

    assert provider.calls[0] == ["first message"]
    assert provider.calls[1] == ["first message", "reply 1", "second message"]

    messages = bot.hub_store.list_messages(session.id)
    assert [item.content for item in messages] == [
        "first message",
        "reply 1",
        "second message",
        "reply 2",
    ]

    event_log = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    assert "queue.wait_started" in event_log
    assert "queue.wait_finished" in event_log


async def test_handle_user_message_retries_retryable_provider_errors(tmp_path):
    provider = FlakyProvider(failures=1)
    bot = _build_fake_bot(tmp_path, "openai_responses", provider)
    bot.hub_store.create_session(
        agent_id="gpt-default",
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

    assert provider.calls == 2
    assert channel.sent_messages == ["recovered reply"]
    event_log = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    assert "provider.retry" in event_log


async def test_handle_user_message_reports_timeout_after_retry_budget(tmp_path):
    bot = _build_fake_bot(tmp_path, "openai_responses", HangingProvider())
    bot.settings.provider_request_timeout_seconds = 0.01
    bot.settings.provider_max_retries = 0
    bot.hub_store.create_session(
        agent_id="gpt-default",
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

    assert channel.sent_messages == ["Provider error: Provider timed out after 0.01s"]


async def test_handle_user_message_blocks_disallowed_roles(tmp_path):
    provider = FakeProvider(
        ProviderResponse(
            output_text="assistant reply",
            provider_session_id="provider-session-1",
            raw_payload={"ok": True},
        )
    )
    bot = _build_fake_bot(tmp_path, "openai_responses", provider)
    bot.settings.disallowed_role_ids = {2025}
    bot.hub_store.create_session(
        agent_id="gpt-default",
        provider="openai_responses",
        discord_channel_id=100,
        discord_thread_id=200,
        discord_guild_id=300,
        created_by_user_id=400,
    )
    channel = FakeChannel(200)
    message = SimpleNamespace(
        author=SimpleNamespace(
            id=123,
            display_name="alice",
            roles=[SimpleNamespace(id=2025, name="2025s-student")],
        ),
        content="hello world",
        channel=channel,
        guild=SimpleNamespace(id=300),
    )

    await handle_user_message(bot, message)

    assert provider.calls == []
    assert channel.sent_messages == ["You are not allowed to use AI chat in this server."]
    event_log = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    assert "auth.denied_role" in event_log


def test_extract_downloadable_code_blocks_groups_supported_languages():
    files = _extract_downloadable_code_blocks(
        "A\n```latex\n\\documentclass{article}\n```\nB\n```tex\n\\section{X}\n```"
        "\nC\n```python\nprint('hello')\n```"
    )

    assert [item.filename for item in files] == ["latex-source.tex", "python-source.py"]
    assert files[0].data.decode("utf-8") == "\\documentclass{article}\n\n\\section{X}\n"
    assert files[1].data.decode("utf-8") == "print('hello')\n"


def test_compact_conversation_keeps_only_latest_user_image():
    conversation = [
        MessageRecord(
            session_id="s1",
            role="user",
            author_id=1,
            author_name="alice",
            content="first image",
            created_at="2026-04-06T00:00:00+00:00",
            attachments=[{"type": "image", "data": "first"}],
        ),
        MessageRecord(
            session_id="s1",
            role="assistant",
            author_id=None,
            author_name="GPT Default",
            content="I saw it",
            created_at="2026-04-06T00:00:01+00:00",
        ),
        MessageRecord(
            session_id="s1",
            role="user",
            author_id=1,
            author_name="alice",
            content="second image",
            created_at="2026-04-06T00:00:02+00:00",
            attachments=[{"type": "image", "data": "second"}],
        ),
    ]

    compacted = _compact_conversation_for_provider(conversation)

    assert compacted[0].attachments == []
    assert compacted[2].attachments == [{"type": "image", "data": "second"}]


def test_compact_conversation_keeps_document_attachments_while_dropping_old_images():
    conversation = [
        MessageRecord(
            session_id="s1",
            role="user",
            author_id=1,
            author_name="alice",
            content="see image and document",
            created_at="2026-04-06T00:00:00+00:00",
            attachments=[
                {"type": "image", "data": "first"},
                {"type": "document", "filename": "notes.txt", "text": "first doc"},
            ],
        ),
        MessageRecord(
            session_id="s1",
            role="user",
            author_id=1,
            author_name="alice",
            content="new image",
            created_at="2026-04-06T00:00:01+00:00",
            attachments=[{"type": "image", "data": "second"}],
        ),
    ]

    compacted = _compact_conversation_for_provider(conversation)

    assert compacted[0].attachments == [
        {"type": "document", "filename": "notes.txt", "text": "first doc"}
    ]
    assert compacted[1].attachments == [{"type": "image", "data": "second"}]
