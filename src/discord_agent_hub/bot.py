from __future__ import annotations

import asyncio
import base64
from dataclasses import replace
import logging
import mimetypes
import time
from typing import Iterable

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import View
import httpx

try:
    from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError
except ImportError:  # pragma: no cover
    APIConnectionError = APITimeoutError = InternalServerError = RateLimitError = tuple()  # type: ignore[assignment]

from discord_agent_hub.agent_markdown import AgentMarkdownError, parse_agent_markdown
from discord_agent_hub.config import Settings
from discord_agent_hub.document_extract import DocumentExtractionError, extract_document_text, is_supported_document
from discord_agent_hub.models import MessageRecord, utc_now
from discord_agent_hub.providers.base import ProviderRegistry
from discord_agent_hub.storage import AgentStore, HubStore
from discord_agent_hub.structured_log import StructuredLogger

logger = logging.getLogger(__name__)


class DiscordAgentHub(commands.Bot):
    def __init__(
        self,
        *,
        settings: Settings,
        agent_store: AgentStore,
        hub_store: HubStore,
        provider_registry: ProviderRegistry,
        structured_logger: StructuredLogger,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.messages = True
        super().__init__(command_prefix="!", intents=intents)
        self.settings = settings
        self.agent_store = agent_store
        self.hub_store = hub_store
        self.provider_registry = provider_registry
        self.structured_logger = structured_logger

    async def setup_hook(self) -> None:
        self.tree.add_command(agent_list)
        self.tree.add_command(agent_import)
        self.tree.add_command(agent_delete)
        self.tree.add_command(agent_show)
        self.tree.add_command(hub_status)
        self.tree.add_command(chat)
        if self.settings.dev_guild_id:
            guild = discord.Object(id=self.settings.dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    def guild_allowed(self, guild: discord.Guild | None) -> bool:
        if guild is None:
            return False
        allowed = self.settings.allowed_server_ids
        return not allowed or guild.id in allowed


async def _send_split(thread: discord.Thread, content: str) -> None:
    chunks = [content[i:i + 1800] for i in range(0, len(content), 1800)] or [""]
    for chunk in chunks:
        await thread.send(chunk)


async def _extract_supported_attachments(message: discord.Message) -> list[dict[str, str]]:
    attachments = []
    for attachment in getattr(message, "attachments", []):
        content_type = attachment.content_type or mimetypes.guess_type(attachment.filename)[0]
        raw = await attachment.read()
        if content_type and content_type.startswith("image/"):
            attachments.append(
                {
                    "type": "image",
                    "filename": attachment.filename,
                    "media_type": content_type,
                    "data": base64.b64encode(raw).decode("ascii"),
                }
            )
            continue
        if not is_supported_document(attachment.filename):
            continue
        try:
            extracted_text = extract_document_text(filename=attachment.filename, raw=raw)
        except DocumentExtractionError as exc:
            raise RuntimeError(f"{attachment.filename}: {exc}") from exc
        attachments.append(
            {
                "type": "document",
                "filename": attachment.filename,
                "media_type": content_type or "application/octet-stream",
                "text": extracted_text,
            }
        )
    return attachments


def _thread_lock_for(bot, thread_id: int) -> asyncio.Lock:
    locks = getattr(bot, "_thread_locks", None)
    if locks is None:
        locks = {}
        setattr(bot, "_thread_locks", locks)
    lock = locks.get(thread_id)
    if lock is None:
        lock = asyncio.Lock()
        locks[thread_id] = lock
    return lock


def _queue_depths_for(bot) -> dict[int, int]:
    depths = getattr(bot, "_thread_queue_depths", None)
    if depths is None:
        depths = {}
        setattr(bot, "_thread_queue_depths", depths)
    return depths


def _is_retryable_provider_error(exc: Exception) -> bool:
    if isinstance(exc, (asyncio.TimeoutError, httpx.TimeoutException, httpx.TransportError)):
        return True
    openai_retryable = tuple(
        cls for cls in (APIConnectionError, APITimeoutError, InternalServerError, RateLimitError)
        if isinstance(cls, type)
    )
    if openai_retryable and isinstance(exc, openai_retryable):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {429, 502, 503, 504}
    if isinstance(exc, RuntimeError):
        text = str(exc)
        return any(token in text for token in ("API error 429", "API error 502", "API error 503", "API error 504"))
    return False


async def _generate_with_retry(
    *,
    bot: DiscordAgentHub,
    provider,
    agent,
    conversation: list[MessageRecord],
    provider_session_id: str | None,
    session_id: str,
    provider_name: str,
) -> object:
    timeout_seconds = bot.settings.provider_request_timeout_seconds
    max_retries = max(0, bot.settings.provider_max_retries)
    base_backoff = max(0.0, bot.settings.provider_retry_backoff_seconds)

    for attempt in range(max_retries + 1):
        try:
            return await asyncio.wait_for(
                provider.generate(
                    agent=agent,
                    conversation=conversation,
                    provider_session_id=provider_session_id,
                ),
                timeout=timeout_seconds,
            )
        except Exception as exc:
            retryable = _is_retryable_provider_error(exc)
            is_last_attempt = attempt >= max_retries
            if retryable and not is_last_attempt:
                delay_seconds = base_backoff * (2**attempt)
                bot.structured_logger.append(
                    "provider.retry",
                    session_id=session_id,
                    provider=provider_name,
                    attempt=attempt + 1,
                    next_attempt=attempt + 2,
                    delay_seconds=delay_seconds,
                    error=str(exc),
                )
                if delay_seconds:
                    await asyncio.sleep(delay_seconds)
                continue
            if isinstance(exc, asyncio.TimeoutError):
                raise RuntimeError(f"Provider timed out after {timeout_seconds:g}s") from exc
            raise

    raise RuntimeError("Provider request failed unexpectedly")


def _compact_conversation_for_provider(conversation: list[MessageRecord]) -> list[MessageRecord]:
    latest_user_image_index = None
    for index, item in enumerate(conversation):
        if item.role == "user" and any(att.get("type") == "image" for att in item.attachments):
            latest_user_image_index = index

    compacted = []
    for index, item in enumerate(conversation):
        if not item.attachments:
            compacted.append(item)
            continue

        if index == latest_user_image_index:
            compacted.append(item)
            continue

        filtered = [attachment for attachment in item.attachments if attachment.get("type") != "image"]
        if filtered == item.attachments:
            compacted.append(item)
            continue
        compacted.append(replace(item, attachments=filtered))
    return compacted


def _build_agent_choices(agent_store: AgentStore, current: str) -> list[app_commands.Choice[str]]:
    current_lower = current.lower().strip()
    matches: Iterable = [agent for agent in agent_store.list_agents() if _is_chat_eligible(agent)]
    if current_lower:
        matches = [
            agent
            for agent in matches
            if current_lower in agent.id.lower() or current_lower in agent.name.lower()
        ]
    return [
        app_commands.Choice(
            name=f"{agent.name} [{agent.provider.value}]",
            value=agent.id,
        )
        for agent in list(matches)[:25]
    ]


def _is_chat_eligible(agent) -> bool:
    return bool(agent.enabled)


@app_commands.command(name="agent-list", description="List available agents")
async def agent_list(interaction: discord.Interaction) -> None:
    bot = interaction.client
    assert isinstance(bot, DiscordAgentHub)
    if not bot.guild_allowed(interaction.guild):
        await interaction.response.send_message("This server is not allowed.", ephemeral=True)
        return

    agents = bot.agent_store.list_agents()
    lines = [
        f"- `{agent.id}`: {agent.name} ({agent.provider.value})"
        + (f" - {agent.description}" if agent.description else "")
        for agent in agents
        if agent.enabled
    ]
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@app_commands.command(name="agent-import", description="Import an agent definition from a Markdown file")
@app_commands.describe(
    file="Markdown file containing a ```agent block and instructions body",
    overwrite="Replace an existing agent with the same id",
)
async def agent_import(
    interaction: discord.Interaction,
    file: discord.Attachment,
    overwrite: bool = False,
) -> None:
    bot = interaction.client
    assert isinstance(bot, DiscordAgentHub)
    if not bot.guild_allowed(interaction.guild):
        await interaction.response.send_message("This server is not allowed.", ephemeral=True)
        return
    if not file.filename.lower().endswith(".md"):
        await interaction.response.send_message("Please upload a `.md` file.", ephemeral=True)
        return

    raw = await file.read()
    try:
        agent = parse_agent_markdown(raw.decode("utf-8"))
        bot.agent_store.save_agent(agent, overwrite=overwrite)
    except UnicodeDecodeError:
        await interaction.response.send_message("The uploaded file must be UTF-8 Markdown.", ephemeral=True)
        return
    except AgentMarkdownError as exc:
        await interaction.response.send_message(f"Invalid agent Markdown: {exc}", ephemeral=True)
        return
    except KeyError:
        await interaction.response.send_message(
            f"Agent `{agent.id}` already exists. Re-run with `overwrite:true` to replace it.",
            ephemeral=True,
        )
        return

    bot.structured_logger.append(
        "agent.imported",
        imported_by_user_id=interaction.user.id,
        agent_id=agent.id,
        provider=agent.provider.value,
        source_filename=file.filename,
        overwrite=overwrite,
    )
    tools_text = ", ".join(f"{key}={value}" for key, value in sorted(agent.tools.items())) or "none"
    await interaction.response.send_message(
        "\n".join(
            [
                f"{'Updated' if overwrite else 'Imported'} `{agent.id}`",
                f"Name: `{agent.name}`",
                f"Provider: `{agent.provider.value}`",
                f"Model: `{agent.model or 'default'}`",
                f"Tools: `{tools_text}`",
            ]
        ),
        ephemeral=True,
    )


@app_commands.command(name="agent-show", description="Show an agent definition")
@app_commands.describe(agent_id="Agent ID to inspect")
async def agent_show(interaction: discord.Interaction, agent_id: str) -> None:
    bot = interaction.client
    assert isinstance(bot, DiscordAgentHub)
    if not bot.guild_allowed(interaction.guild):
        await interaction.response.send_message("This server is not allowed.", ephemeral=True)
        return

    try:
        agent = bot.agent_store.get_agent(agent_id)
    except KeyError:
        await interaction.response.send_message(
            f"Unknown agent_id: `{agent_id}`. Use `/agent-list` to see valid options.",
            ephemeral=True,
        )
        return

    tools_text = ", ".join(f"{key}={value}" for key, value in sorted(agent.tools.items())) or "none"
    instructions_preview = agent.instructions.strip()[:1200] or "(empty)"
    lines = [
        f"ID: `{agent.id}`",
        f"Name: `{agent.name}`",
        f"Provider: `{agent.provider.value}`",
        f"Model: `{agent.model or 'default'}`",
        f"Enabled: `{agent.enabled}`",
        f"Public instructions: `{agent.public_instructions}`",
        f"Tools: `{tools_text}`",
    ]
    if agent.description:
        lines.append(f"Description: {agent.description}")
    lines.append("")
    lines.append("Instructions preview:")
    if agent.public_instructions:
        lines.append(instructions_preview)
    else:
        lines.append("(hidden for this agent)")
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@agent_show.autocomplete("agent_id")
async def agent_show_agent_id_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    bot = interaction.client
    assert isinstance(bot, DiscordAgentHub)
    return _build_agent_choices(bot.agent_store, current)


@app_commands.command(name="agent-delete", description="Delete an agent definition")
@app_commands.describe(agent_id="Agent ID to delete")
async def agent_delete(interaction: discord.Interaction, agent_id: str) -> None:
    bot = interaction.client
    assert isinstance(bot, DiscordAgentHub)
    if not bot.guild_allowed(interaction.guild):
        await interaction.response.send_message("This server is not allowed.", ephemeral=True)
        return

    try:
        agent = bot.agent_store.get_agent(agent_id)
    except KeyError:
        await interaction.response.send_message(
            f"Unknown agent_id: `{agent_id}`. Use `/agent-list` to see valid options.",
            ephemeral=True,
        )
        return

    lines = [
        f"Delete `{agent.id}`?",
        f"Name: `{agent.name}`",
        f"Provider: `{agent.provider.value}`",
        "This action cannot be undone.",
    ]
    await interaction.response.send_message(
        "\n".join(lines),
        ephemeral=True,
        view=DeleteAgentConfirmView(bot=bot, agent_id=agent.id, agent_name=agent.name),
    )


@agent_delete.autocomplete("agent_id")
async def agent_delete_agent_id_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    bot = interaction.client
    assert isinstance(bot, DiscordAgentHub)
    return _build_agent_choices(bot.agent_store, current)


class DeleteAgentConfirmView(View):
    def __init__(self, *, bot: DiscordAgentHub, agent_id: str, agent_name: str) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.agent_id = agent_id
        self.agent_name = agent_name

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.red)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            self.bot.agent_store.delete_agent(self.agent_id)
        except KeyError:
            await interaction.response.edit_message(
                content=f"Agent `{self.agent_id}` no longer exists.",
                view=None,
            )
            return

        self.bot.structured_logger.append(
            "agent.deleted",
            deleted_by_user_id=interaction.user.id,
            agent_id=self.agent_id,
            agent_name=self.agent_name,
        )
        await interaction.response.edit_message(
            content=f"Deleted `{self.agent_id}`.",
            view=None,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            content=f"Cancelled deletion of `{self.agent_id}`.",
            view=None,
        )


@app_commands.command(name="hub-status", description="Show configured providers and defaults")
async def hub_status(interaction: discord.Interaction) -> None:
    bot = interaction.client
    assert isinstance(bot, DiscordAgentHub)
    if not bot.guild_allowed(interaction.guild):
        await interaction.response.send_message("This server is not allowed.", ephemeral=True)
        return

    settings = bot.settings
    lines = [
        f"Default agent: `{settings.default_agent_id}`",
        f"OpenAI configured: `{'yes' if bool(settings.openai_api_key) else 'no'}`",
        f"Anthropic configured: `{'yes' if bool(settings.anthropic_api_key) else 'no'}`",
        f"Gemini configured: `{'yes' if bool(settings.gemini_api_key) else 'no'}`",
        f"Dev guild sync: `{settings.dev_guild_id if settings.dev_guild_id else 'disabled'}`",
    ]
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@app_commands.command(name="chat", description="Create a thread-bound chat session")
@app_commands.describe(agent_id="Agent ID to use")
async def chat(interaction: discord.Interaction, agent_id: str | None = None) -> None:
    bot = interaction.client
    assert isinstance(bot, DiscordAgentHub)
    if not bot.guild_allowed(interaction.guild):
        await interaction.response.send_message("This server is not allowed.", ephemeral=True)
        return

    try:
        agent = bot.agent_store.get_agent(agent_id or bot.settings.default_agent_id)
    except KeyError:
        await interaction.response.send_message(
            f"Unknown agent_id: `{agent_id}`. Use `/agent-list` to see valid options.",
            ephemeral=True,
        )
        return
    if not _is_chat_eligible(agent):
        await interaction.response.send_message(
            f"Agent `{agent.id}` is currently disabled.",
            ephemeral=True,
        )
        return
    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("Use this command in a text channel.", ephemeral=True)
        return
    await interaction.response.send_message(
        f"Starting session with `{agent.id}` / `{agent.provider.value}`"
    )
    message = await interaction.original_response()
    thread = await message.create_thread(
        name=f"hub-{interaction.user.display_name[:20]}",
        auto_archive_duration=1440,
        reason="discord-agent-hub",
    )
    session = bot.hub_store.create_session(
        agent_id=agent.id,
        provider=agent.provider.value,
        discord_channel_id=interaction.channel.id,
        discord_thread_id=thread.id,
        discord_guild_id=interaction.guild_id,
        created_by_user_id=interaction.user.id,
    )
    bot.structured_logger.append(
        "session.created",
        session_id=session.id,
        agent_id=agent.id,
        provider=agent.provider,
        discord_thread_id=thread.id,
        discord_channel_id=interaction.channel.id,
        discord_guild_id=interaction.guild_id,
        created_by_user_id=interaction.user.id,
    )
    await thread.send(
        f"Session started.\n"
        f"- session_id: `{session.id}`\n"
        f"- agent_id: `{agent.id}`\n"
        f"- provider: `{agent.provider.value}`"
    )


@chat.autocomplete("agent_id")
async def chat_agent_id_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    bot = interaction.client
    assert isinstance(bot, DiscordAgentHub)
    return _build_agent_choices(bot.agent_store, current)


async def handle_user_message(bot: DiscordAgentHub, message: discord.Message) -> None:
    session = bot.hub_store.get_session_by_thread_id(message.channel.id)
    if session is None:
        return

    thread_id = message.channel.id
    queue_depths = _queue_depths_for(bot)
    queued_at = time.perf_counter()
    queue_depths[thread_id] = queue_depths.get(thread_id, 0) + 1
    queue_depth = queue_depths[thread_id]
    if queue_depth > 1:
        bot.structured_logger.append(
            "queue.wait_started",
            session_id=session.id,
            discord_thread_id=thread_id,
            queue_depth=queue_depth,
            author_id=message.author.id,
        )

    try:
        async with _thread_lock_for(bot, thread_id):
            if queue_depth > 1:
                bot.structured_logger.append(
                    "queue.wait_finished",
                    session_id=session.id,
                    discord_thread_id=thread_id,
                    queue_depth=queue_depth,
                    author_id=message.author.id,
                    waited_ms=int((time.perf_counter() - queued_at) * 1000),
                )
            agent = bot.agent_store.get_agent(session.agent_id)
            provider = bot.provider_registry.get(session.provider)

            try:
                attachments = await _extract_supported_attachments(message)
            except RuntimeError as exc:
                await message.channel.send(f"Attachment error: {exc}")
                return

            user_record = MessageRecord(
                session_id=session.id,
                role="user",
                author_id=message.author.id,
                author_name=message.author.display_name,
                content=message.content,
                attachments=attachments,
                created_at=utc_now(),
            )
            bot.hub_store.add_message(user_record)
            bot.structured_logger.append(
                "message.user",
                session_id=session.id,
                discord_thread_id=message.channel.id,
                author_id=message.author.id,
                author_name=message.author.display_name,
                content=message.content,
            )

            conversation = _compact_conversation_for_provider(bot.hub_store.list_messages(session.id))
            try:
                response = await _generate_with_retry(
                    bot=bot,
                    provider=provider,
                    agent=agent,
                    conversation=conversation,
                    provider_session_id=session.provider_session_id,
                    session_id=session.id,
                    provider_name=session.provider,
                )
            except Exception as exc:
                logger.exception("Provider failed")
                bot.structured_logger.append(
                    "provider.error",
                    session_id=session.id,
                    provider=session.provider,
                    error=str(exc),
                )
                await message.channel.send(f"Provider error: {exc}")
                return

            if response.provider_session_id and response.provider_session_id != session.provider_session_id:
                bot.hub_store.update_provider_session_id(session.id, response.provider_session_id)

            assistant_record = MessageRecord(
                session_id=session.id,
                role="assistant",
                author_id=None,
                author_name=agent.name,
                content=response.output_text,
                created_at=utc_now(),
            )
            bot.hub_store.add_message(assistant_record)
            bot.structured_logger.append(
                "response.assistant",
                session_id=session.id,
                provider=session.provider,
                agent_id=agent.id,
                content=response.output_text,
                raw_payload=response.raw_payload,
            )
            await _send_split(message.channel, response.output_text)
    finally:
        remaining = queue_depths.get(thread_id, 1) - 1
        if remaining <= 0:
            queue_depths.pop(thread_id, None)
        else:
            queue_depths[thread_id] = remaining


def attach_message_handler(bot: DiscordAgentHub) -> None:
    @bot.event
    async def on_ready() -> None:
        logger.info("Logged in as %s", bot.user)

    @bot.event
    async def on_message(message: discord.Message) -> None:
        if message.author.bot:
            return
        if not bot.guild_allowed(message.guild):
            return
        if not isinstance(message.channel, discord.Thread):
            return
        async with message.channel.typing():
            await handle_user_message(bot, message)
