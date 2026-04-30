from __future__ import annotations

import asyncio
import base64
from io import BytesIO
from dataclasses import replace
import json
import logging
import mimetypes
import os
import tempfile
import time
from typing import Iterable

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import View
import httpx
from openai import AsyncOpenAI

try:
    from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError
except ImportError:  # pragma: no cover
    APIConnectionError = APITimeoutError = InternalServerError = RateLimitError = tuple()  # type: ignore[assignment]

from discord_agent_hub import __version__
from discord_agent_hub.agent_markdown import AgentMarkdownError, parse_agent_markdown
from discord_agent_hub.conversation_render import render_message_text
from discord_agent_hub.config import Settings
from discord_agent_hub.document_extract import DocumentExtractionError, extract_document_text, is_supported_document
from discord_agent_hub.knowledge import KnowledgeChunk, build_knowledge_context
from discord_agent_hub.models import MessageRecord, utc_now
from discord_agent_hub.provider_instructions import CODE_EXECUTION_CAPABILITY_NOTE
from discord_agent_hub.providers.base import ProviderRegistry
from discord_agent_hub.storage import AgentStore, HubStore
from discord_agent_hub.structured_log import StructuredLogger

logger = logging.getLogger(__name__)

KNOWLEDGE_BACKENDS = {"hub_lexical", "openai_file_search", "gemini_file_search"}


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
        intents.members = True
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
        self.tree.add_command(agent_show_full)
        self.tree.add_command(knowledge_import)
        self.tree.add_command(knowledge_list)
        self.tree.add_command(knowledge_show)
        self.tree.add_command(hub_status)
        self.tree.add_command(chat)
        self.tree.add_command(session_show)
        self.tree.add_command(log_export)
        self.tree.add_command(usage_report)
        await self.tree.sync()
        if self.settings.dev_guild_id:
            guild = discord.Object(id=self.settings.dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)

    def guild_allowed(self, guild: discord.Guild | None) -> bool:
        if guild is None:
            return False
        allowed = self.settings.allowed_server_ids
        return not allowed or guild.id in allowed


async def _send_split(thread: discord.Thread, content: str) -> None:
    chunks = [content[i:i + 1800] for i in range(0, len(content), 1800)] or [""]
    for chunk in chunks:
        await thread.send(chunk)


async def _send_interaction_split(interaction: discord.Interaction, content: str, *, ephemeral: bool = True) -> None:
    chunks = [content[i:i + 1800] for i in range(0, len(content), 1800)] or [""]
    await interaction.response.send_message(chunks[0], ephemeral=ephemeral)
    for chunk in chunks[1:]:
        await interaction.followup.send(chunk, ephemeral=ephemeral)


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


def _is_routable_thread_message(message: discord.Message) -> bool:
    return message.type in {discord.MessageType.default, discord.MessageType.reply}


async def _member_role_ids(bot: DiscordAgentHub, guild: discord.Guild | None, actor) -> set[int]:
    if guild is None or actor is None:
        return set()
    roles = getattr(actor, "roles", None)
    if roles is not None:
        return {
            role.id
            for role in roles
            if getattr(role, "id", None) is not None and getattr(role, "name", "") != "@everyone"
        }
    member_id = getattr(actor, "id", None)
    if member_id is None:
        return set()
    try:
        member = await guild.fetch_member(member_id)
    except Exception:
        return set()
    return {
        role.id
        for role in getattr(member, "roles", [])
        if getattr(role, "id", None) is not None and getattr(role, "name", "") != "@everyone"
    }


async def _is_disallowed_member(bot: DiscordAgentHub, guild: discord.Guild | None, actor) -> bool:
    disallowed = getattr(bot.settings, "disallowed_role_ids", set())
    if not disallowed:
        return False
    return bool(await _member_role_ids(bot, guild, actor) & disallowed)


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


def _knowledge_source_ids(agent) -> list[str]:
    value = agent.metadata.get("knowledge_source_ids")
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _native_knowledge_metadata(*, agent, sources: list[dict]) -> dict:
    metadata = dict(agent.metadata or {})
    if agent.provider.value == "openai_responses":
        vector_store_ids = [
            source["remote_store_id"]
            for source in sources
            if source.get("backend") == "openai_file_search" and source.get("remote_store_id")
        ]
        if vector_store_ids:
            metadata["openai_vector_store_ids"] = vector_store_ids
    if agent.provider.value == "gemini_api":
        store_names = [
            source["remote_store_id"]
            for source in sources
            if source.get("backend") == "gemini_file_search" and source.get("remote_store_id")
        ]
        if store_names:
            metadata["gemini_file_search_store_names"] = store_names
    return metadata


def _hub_lexical_source_ids_for_provider(*, agent, sources: list[dict]) -> list[str]:
    if agent.provider.value not in {"openai_responses", "anthropic_messages", "gemini_api"}:
        return []
    return [source["id"] for source in sources if source.get("backend") == "hub_lexical"]


def _attach_knowledge_context(
    conversation: list[MessageRecord],
    chunks: list[KnowledgeChunk],
) -> list[MessageRecord]:
    context = build_knowledge_context(chunks)
    if not context:
        return conversation
    for index in range(len(conversation) - 1, -1, -1):
        item = conversation[index]
        if item.role != "user":
            continue
        attachments = list(item.attachments)
        attachments.append(
            {
                "type": "document",
                "filename": "retrieved-knowledge.md",
                "media_type": "text/markdown",
                "text": context,
            }
        )
        updated = replace(item, attachments=attachments)
        return conversation[:index] + [updated] + conversation[index + 1:]
    return conversation


async def _import_openai_file_search_source(
    *,
    settings: Settings,
    source_id: str,
    filename: str,
    media_type: str,
    raw: bytes,
    remote_store_id: str | None = None,
) -> str:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    vector_store_id = remote_store_id
    if not vector_store_id:
        vector_store = await client.vector_stores.create(name=source_id)
        vector_store_id = vector_store.id
    uploaded_file = await client.files.create(
        file=(filename, raw, media_type),
        purpose="assistants",
    )
    await client.vector_stores.files.create(
        vector_store_id=vector_store_id,
        file_id=uploaded_file.id,
    )
    return vector_store_id


async def _import_gemini_file_search_source(
    *,
    settings: Settings,
    source_id: str,
    filename: str,
    raw: bytes,
    remote_store_id: str | None = None,
) -> str:
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")

    def import_sync() -> str:
        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("google-genai is required for gemini_file_search imports") from exc

        client = genai.Client(api_key=settings.gemini_api_key)
        store_name = remote_store_id
        if not store_name:
            file_search_store = client.file_search_stores.create(config={"display_name": source_id})
            store_name = file_search_store.name
        with tempfile.NamedTemporaryFile(delete=False, suffix=f"-{filename}") as handle:
            handle.write(raw)
            temp_name = handle.name
        try:
            operation = client.file_search_stores.upload_to_file_search_store(
                file=temp_name,
                file_search_store_name=store_name,
                config={"display_name": filename},
            )
            while not operation.done:
                time.sleep(2)
                operation = client.operations.get(operation)
        finally:
            os.unlink(temp_name)
        return store_name

    return await asyncio.to_thread(import_sync)


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


def _build_knowledge_source_choices(hub_store: HubStore, current: str) -> list[app_commands.Choice[str]]:
    current_lower = current.lower().strip()
    sources: Iterable[dict] = hub_store.list_knowledge_sources()
    if current_lower:
        sources = [
            source
            for source in sources
            if current_lower in str(source["id"]).lower()
        ]
    return [
        app_commands.Choice(
            name=f"{source['id']} [{source['backend']}]",
            value=str(source["id"]),
        )
        for source in list(sources)[:25]
    ]


def _is_chat_eligible(agent) -> bool:
    return bool(agent.enabled)


def _current_session(bot: DiscordAgentHub, channel) -> object | None:
    channel_id = getattr(channel, "id", None)
    if channel_id is None:
        return None
    return bot.hub_store.get_session_by_thread_id(channel_id)


def _summarize_usage(events: list[dict]) -> dict[str, int]:
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "cached_input_tokens": 0,
    }
    for event in events:
        if event.get("event") != "response.assistant":
            continue
        usage = event.get("usage") or {}
        for key in totals:
            value = usage.get(key)
            if isinstance(value, int):
                totals[key] += value
    return totals


def _usage_report_lines(events: list[dict], *, guild_id: int | None = None) -> list[str]:
    filtered = [
        event
        for event in events
        if event.get("event") == "response.assistant"
        and (guild_id is None or event.get("discord_guild_id") == guild_id)
    ]
    if not filtered:
        return ["No usage recorded yet."]

    totals = _summarize_usage(filtered)
    by_provider: dict[str, int] = {}
    by_agent: dict[str, int] = {}
    by_user: dict[str, int] = {}

    for event in filtered:
        provider = event.get("provider") or "unknown"
        agent_id = event.get("agent_id") or "unknown"
        user_id = str(event.get("created_by_user_id") or event.get("user_id") or "unknown")
        by_provider[provider] = by_provider.get(provider, 0) + 1
        by_agent[agent_id] = by_agent.get(agent_id, 0) + 1
        by_user[user_id] = by_user.get(user_id, 0) + 1

    def top_lines(title: str, values: dict[str, int]) -> list[str]:
        lines = [title]
        for key, count in sorted(values.items(), key=lambda item: (-item[1], item[0]))[:5]:
            lines.append(f"- `{key}`: {count}")
        return lines

    lines = [
        f"Responses: `{len(filtered)}`",
        f"Input tokens: `{totals['input_tokens']}`",
        f"Output tokens: `{totals['output_tokens']}`",
        f"Total tokens: `{totals['total_tokens']}`",
        "",
    ]
    lines.extend(top_lines("Top providers", by_provider))
    lines.append("")
    lines.extend(top_lines("Top agents", by_agent))
    lines.append("")
    lines.extend(top_lines("Top user IDs", by_user))
    return lines


async def _usage_report_lines_for_guild(events: list[dict], *, guild) -> list[str]:
    lines = _usage_report_lines(events, guild_id=getattr(guild, "id", None))
    if not guild or "Top user IDs" not in lines:
        return lines

    user_ids: list[int] = []
    for line in lines:
        if not line.startswith("- `"):
            continue
        key = line.split("`", 2)[1]
        if key.isdigit():
            user_ids.append(int(key))

    labels: dict[str, str] = {}
    for user_id in user_ids:
        member = None
        getter = getattr(guild, "get_member", None)
        if callable(getter):
            member = getter(user_id)
        if member is None:
            fetcher = getattr(guild, "fetch_member", None)
            if callable(fetcher):
                try:
                    member = await fetcher(user_id)
                except Exception:
                    member = None
        if member is not None:
            display_name = getattr(member, "display_name", None) or getattr(member, "name", None)
            if display_name:
                labels[str(user_id)] = f"{display_name} ({user_id})"

    rewritten: list[str] = []
    in_user_section = False
    for line in lines:
        if line == "Top user IDs":
            in_user_section = True
            rewritten.append("Top users")
            continue
        if in_user_section and line == "":
            in_user_section = False
            rewritten.append(line)
            continue
        if in_user_section and line.startswith("- `"):
            key = line.split("`", 2)[1]
            count = line.split(": ", 1)[1]
            rewritten.append(f"- `{labels.get(key, key)}`: {count}")
            continue
        rewritten.append(line)
    return rewritten


def _agent_show_lines(*, agent, full: bool) -> list[str]:
    tools_text = ", ".join(f"{key}={value}" for key, value in sorted(agent.tools.items())) or "none"
    knowledge_sources_text = ", ".join(_knowledge_source_ids(agent)) or "none"
    instructions_text = agent.instructions.strip() or "(empty)"
    if not full:
        instructions_text = instructions_text[:1200]
    lines = [
        f"ID: `{agent.id}`",
        f"Name: `{agent.name}`",
        f"Provider: `{agent.provider.value}`",
        f"Model: `{agent.model or 'default'}`",
        f"Enabled: `{agent.enabled}`",
        f"Public instructions: `{agent.public_instructions}`",
        f"Tools: `{tools_text}`",
        f"Knowledge sources: `{knowledge_sources_text}`",
    ]
    if agent.description:
        lines.append(f"Description: {agent.description}")
    lines.append("")
    lines.append("Instructions:")
    if agent.public_instructions:
        lines.append(instructions_text)
    else:
        lines.append("(hidden for this agent)")
    if full and agent.tools.get("code_execution"):
        lines.append("")
        lines.append("Hub-injected capability note:")
        lines.append(CODE_EXECUTION_CAPABILITY_NOTE)
    return lines


def _agent_notify_user_ids(agent) -> list[int]:
    notify_user_ids = agent.metadata.get("notify_user_ids")
    if isinstance(notify_user_ids, list):
        normalized: list[int] = []
        for value in notify_user_ids:
            try:
                normalized.append(int(value))
            except (TypeError, ValueError):
                continue
        if normalized:
            return list(dict.fromkeys(normalized))

    created_by = agent.metadata.get("created_by_user_id")
    try:
        created_by_id = int(created_by)
    except (TypeError, ValueError):
        return []
    return [created_by_id]


def _merge_agent_metadata(*, existing_agent, imported_agent, actor_user_id: int) -> dict:
    metadata = dict(getattr(existing_agent, "metadata", {}) or {})
    metadata.update(imported_agent.metadata or {})
    metadata["created_by_user_id"] = metadata.get("created_by_user_id", actor_user_id)
    metadata["notify_user_ids"] = _agent_notify_user_ids(
        replace(imported_agent, metadata=metadata)
    )
    metadata["last_imported_by_user_id"] = actor_user_id
    metadata["last_imported_at"] = utc_now()
    return metadata


def _agent_update_notification_recipient_ids(*, existing_agent, actor_user_id: int) -> list[int]:
    return [user_id for user_id in _agent_notify_user_ids(existing_agent) if user_id != actor_user_id]


def _actor_label(actor) -> str:
    display_name = getattr(actor, "display_name", None)
    if isinstance(display_name, str) and display_name:
        return display_name
    name = getattr(actor, "name", None)
    if isinstance(name, str) and name:
        return name
    actor_id = getattr(actor, "id", None)
    return f"user {actor_id}" if actor_id is not None else "another user"


async def _notify_agent_watchers(
    *,
    bot: DiscordAgentHub,
    user_ids: list[int],
    content: str,
    event_name: str,
    agent_id: str,
) -> None:
    for user_id in user_ids:
        try:
            user = bot.get_user(user_id) or await bot.fetch_user(user_id)
            await user.send(content)
            bot.structured_logger.append(
                event_name,
                agent_id=agent_id,
                notified_user_id=user_id,
            )
        except Exception as exc:
            logger.warning("Failed to notify user %s for agent %s: %s", user_id, agent_id, exc)
            bot.structured_logger.append(
                f"{event_name}.failed",
                agent_id=agent_id,
                notified_user_id=user_id,
                error=str(exc),
            )


def _response_model_name(*, agent, response) -> str | None:
    raw_payload = getattr(response, "raw_payload", None) or {}
    model = raw_payload.get("model")
    if isinstance(model, str) and model:
        return model
    agent_model = getattr(agent, "model", None)
    if isinstance(agent_model, str) and agent_model:
        return agent_model
    return None


def _build_transcript_markdown(*, session, agent, messages: list[MessageRecord], usage: dict[str, int]) -> str:
    lines = [
        f"# Session Export",
        "",
        f"- session_id: `{session.id}`",
        f"- agent_id: `{agent.id}`",
        f"- agent_name: {agent.name}",
        f"- provider: `{session.provider}`",
        f"- created_at: `{session.created_at}`",
        f"- messages: `{len(messages)}`",
        f"- input_tokens: `{usage['input_tokens']}`",
        f"- output_tokens: `{usage['output_tokens']}`",
        f"- total_tokens: `{usage['total_tokens']}`",
        "",
        "## Transcript",
        "",
    ]
    for item in messages:
        speaker = item.author_name or item.role
        lines.append(f"### {speaker} ({item.role})")
        lines.append(f"_ts: {item.created_at}_")
        lines.append("")
        rendered = render_message_text(item) or "(empty)"
        lines.append(rendered)
        lines.append("")
    return "\n".join(lines)


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
    await _send_interaction_split(interaction, "\n".join(lines), ephemeral=True)


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
    previous_agent = None
    try:
        agent = parse_agent_markdown(raw.decode("utf-8"))
        if overwrite:
            try:
                previous_agent = bot.agent_store.get_agent(agent.id)
            except KeyError:
                previous_agent = None
        agent = replace(
            agent,
            metadata=_merge_agent_metadata(
                existing_agent=previous_agent,
                imported_agent=agent,
                actor_user_id=interaction.user.id,
            ),
        )
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
    if previous_agent is not None:
        recipient_ids = _agent_update_notification_recipient_ids(
            existing_agent=previous_agent,
            actor_user_id=interaction.user.id,
        )
        if recipient_ids:
            await _notify_agent_watchers(
                bot=bot,
                user_ids=recipient_ids,
                event_name="agent.updated_notified",
                agent_id=agent.id,
                content="\n".join(
                    [
                        f"Agent `{agent.id}` was updated in Discord.",
                        f"Updated by: `{_actor_label(interaction.user)}`",
                        f"Name: `{agent.name}`",
                        f"Provider: `{agent.provider.value}`",
                        f"Model: `{agent.model or 'default'}`",
                        f"Source file: `{file.filename}`",
                    ]
                ),
            )
    tools_text = ", ".join(f"{key}={value}" for key, value in sorted(agent.tools.items())) or "none"
    knowledge_sources_text = ", ".join(_knowledge_source_ids(agent)) or "none"
    await interaction.response.send_message(
        "\n".join(
            [
                f"{'Updated' if overwrite else 'Imported'} `{agent.id}`",
                f"Name: `{agent.name}`",
                f"Provider: `{agent.provider.value}`",
                f"Model: `{agent.model or 'default'}`",
                f"Tools: `{tools_text}`",
                f"Knowledge sources: `{knowledge_sources_text}`",
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

    lines = _agent_show_lines(agent=agent, full=False)
    await _send_interaction_split(interaction, "\n".join(lines), ephemeral=True)


@app_commands.command(name="agent-show-full", description="Show the full public instructions for an agent")
@app_commands.describe(agent_id="Agent ID to inspect")
async def agent_show_full(interaction: discord.Interaction, agent_id: str) -> None:
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

    lines = _agent_show_lines(agent=agent, full=True)
    await _send_interaction_split(interaction, "\n".join(lines), ephemeral=True)


@agent_show.autocomplete("agent_id")
async def agent_show_agent_id_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    bot = interaction.client
    assert isinstance(bot, DiscordAgentHub)
    return _build_agent_choices(bot.agent_store, current)


@agent_show_full.autocomplete("agent_id")
async def agent_show_full_agent_id_autocomplete(
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
        view=DeleteAgentConfirmView(
            bot=bot,
            agent_id=agent.id,
            agent_name=agent.name,
            notify_user_ids=_agent_notify_user_ids(agent),
        ),
    )


@agent_delete.autocomplete("agent_id")
async def agent_delete_agent_id_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    bot = interaction.client
    assert isinstance(bot, DiscordAgentHub)
    return _build_agent_choices(bot.agent_store, current)


class DeleteAgentConfirmView(View):
    def __init__(self, *, bot: DiscordAgentHub, agent_id: str, agent_name: str, notify_user_ids: list[int] | None = None) -> None:
        super().__init__(timeout=300)
        self.bot = bot
        self.agent_id = agent_id
        self.agent_name = agent_name
        self.notify_user_ids = notify_user_ids or []

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
        recipient_ids = [user_id for user_id in self.notify_user_ids if user_id != interaction.user.id]
        if recipient_ids:
            await _notify_agent_watchers(
                bot=self.bot,
                user_ids=recipient_ids,
                event_name="agent.deleted_notified",
                agent_id=self.agent_id,
                content="\n".join(
                    [
                        f"Agent `{self.agent_id}` was deleted in Discord.",
                        f"Deleted by: `{_actor_label(interaction.user)}`",
                        f"Name: `{self.agent_name}`",
                    ]
                ),
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


@app_commands.command(name="knowledge-import", description="Import a document into a knowledge source")
@app_commands.describe(
    source_id="Knowledge source ID to create or extend",
    file="Document file to extract and index",
    overwrite="Replace the whole knowledge source before importing this file",
    backend="Knowledge backend to use",
)
@app_commands.choices(
    backend=[
        app_commands.Choice(name="hub_lexical", value="hub_lexical"),
        app_commands.Choice(name="openai_file_search", value="openai_file_search"),
        app_commands.Choice(name="gemini_file_search", value="gemini_file_search"),
    ]
)
async def knowledge_import(
    interaction: discord.Interaction,
    source_id: str,
    file: discord.Attachment,
    overwrite: bool = False,
    backend: str = "hub_lexical",
) -> None:
    bot = interaction.client
    assert isinstance(bot, DiscordAgentHub)
    if not bot.guild_allowed(interaction.guild):
        await interaction.response.send_message("This server is not allowed.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    if not is_supported_document(file.filename):
        await interaction.followup.send("Please upload a supported document file.", ephemeral=True)
        return
    backend = backend.strip()
    if backend not in KNOWLEDGE_BACKENDS:
        await interaction.followup.send(
            f"Unknown knowledge backend `{backend}`. Use one of: {', '.join(sorted(KNOWLEDGE_BACKENDS))}.",
            ephemeral=True,
        )
        return

    raw = await file.read()
    try:
        text = extract_document_text(filename=file.filename, raw=raw)
    except DocumentExtractionError as exc:
        await interaction.followup.send(f"Attachment error: {file.filename}: {exc}", ephemeral=True)
        return
    existing_sources = bot.hub_store.get_knowledge_sources([source_id])
    existing_remote_store_id = None if overwrite or not existing_sources else existing_sources[0].get("remote_store_id")
    remote_store_id = None
    try:
        if backend == "openai_file_search":
            remote_store_id = await _import_openai_file_search_source(
                settings=bot.settings,
                source_id=source_id,
                filename=file.filename,
                media_type=file.content_type or mimetypes.guess_type(file.filename)[0] or "application/octet-stream",
                raw=raw,
                remote_store_id=existing_remote_store_id,
            )
        elif backend == "gemini_file_search":
            remote_store_id = await _import_gemini_file_search_source(
                settings=bot.settings,
                source_id=source_id,
                filename=file.filename,
                raw=raw,
                remote_store_id=existing_remote_store_id,
            )
    except Exception as exc:
        logger.exception("Knowledge backend import failed")
        await interaction.followup.send(f"Knowledge backend error: {exc}", ephemeral=True)
        return
    try:
        document_id, chunk_count = bot.hub_store.import_knowledge_document(
            source_id=source_id,
            filename=file.filename,
            media_type=file.content_type or mimetypes.guess_type(file.filename)[0] or "application/octet-stream",
            text=text,
            created_by_user_id=interaction.user.id,
            overwrite=overwrite,
            backend=backend,
            remote_store_id=remote_store_id,
        )
    except ValueError as exc:
        await interaction.followup.send(str(exc), ephemeral=True)
        return

    bot.structured_logger.append(
        "knowledge.imported",
        source_id=source_id,
        document_id=document_id,
        filename=file.filename,
        chunk_count=chunk_count,
        imported_by_user_id=interaction.user.id,
        overwrite=overwrite,
        backend=backend,
        remote_store_id=remote_store_id,
    )
    await interaction.followup.send(
        "\n".join(
            [
                f"{'Replaced' if overwrite else 'Imported'} `{file.filename}` into knowledge source `{source_id}`.",
                f"Document ID: `{document_id}`",
                f"Chunks: `{chunk_count}`",
                f"Backend: `{backend}`",
                f"Remote store: `{remote_store_id or 'none'}`",
            ]
        ),
        ephemeral=True,
    )


@app_commands.command(name="knowledge-list", description="List knowledge sources")
async def knowledge_list(interaction: discord.Interaction) -> None:
    bot = interaction.client
    assert isinstance(bot, DiscordAgentHub)
    if not bot.guild_allowed(interaction.guild):
        await interaction.response.send_message("This server is not allowed.", ephemeral=True)
        return

    sources = bot.hub_store.list_knowledge_sources()
    if not sources:
        await interaction.response.send_message("No knowledge sources imported yet.", ephemeral=True)
        return
    lines = [
        f"- `{source['id']}`: {source['document_count']} documents, {source['chunk_count']} chunks, "
        f"backend `{source['backend']}`"
        + (f", remote `{source['remote_store_id']}`" if source.get("remote_store_id") else "")
        for source in sources
    ]
    await _send_interaction_split(interaction, "\n".join(lines), ephemeral=True)


@app_commands.command(name="knowledge-show", description="Show documents in a knowledge source")
@app_commands.describe(source_id="Knowledge source ID to inspect")
async def knowledge_show(interaction: discord.Interaction, source_id: str) -> None:
    bot = interaction.client
    assert isinstance(bot, DiscordAgentHub)
    if not bot.guild_allowed(interaction.guild):
        await interaction.response.send_message("This server is not allowed.", ephemeral=True)
        return

    sources = bot.hub_store.get_knowledge_sources([source_id])
    if not sources:
        await interaction.response.send_message(
            f"Unknown knowledge source: `{source_id}`. Use `/knowledge-list` to see valid sources.",
            ephemeral=True,
        )
        return

    source = sources[0]
    documents = bot.hub_store.list_knowledge_documents(source_id)
    lines = [
        f"ID: `{source['id']}`",
        f"Backend: `{source['backend']}`",
        f"Remote store: `{source.get('remote_store_id') or 'none'}`",
        f"Created at: `{source['created_at']}`",
        f"Created by user ID: `{source.get('created_by_user_id') or 'unknown'}`",
        "",
        "Documents:",
    ]
    if not documents:
        lines.append("(none)")
    for document in documents:
        lines.append(
            f"- `{document['filename']}`: {document['media_type']}, "
            f"{document['text_chars']} chars, {document['chunk_count']} chunks, "
            f"created `{document['created_at']}`"
        )
    await _send_interaction_split(interaction, "\n".join(lines), ephemeral=True)


@knowledge_show.autocomplete("source_id")
async def knowledge_show_source_id_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    bot = interaction.client
    assert isinstance(bot, DiscordAgentHub)
    return _build_knowledge_source_choices(bot.hub_store, current)


@app_commands.command(name="hub-status", description="Show configured providers and defaults")
async def hub_status(interaction: discord.Interaction) -> None:
    bot = interaction.client
    assert isinstance(bot, DiscordAgentHub)
    if not bot.guild_allowed(interaction.guild):
        await interaction.response.send_message("This server is not allowed.", ephemeral=True)
        return

    settings = bot.settings
    lines = [
        f"Version: `{__version__}`",
        f"Default agent: `{settings.default_agent_id}`",
        f"OpenAI configured: `{'yes' if bool(settings.openai_api_key) else 'no'}`",
        f"Anthropic configured: `{'yes' if bool(settings.anthropic_api_key) else 'no'}`",
        f"Gemini configured: `{'yes' if bool(settings.gemini_api_key) else 'no'}`",
        f"Dev guild sync: `{settings.dev_guild_id if settings.dev_guild_id else 'disabled'}`",
    ]
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@app_commands.command(name="session-show", description="Show details for the current session thread")
async def session_show(interaction: discord.Interaction) -> None:
    bot = interaction.client
    assert isinstance(bot, DiscordAgentHub)
    if not bot.guild_allowed(interaction.guild):
        await interaction.response.send_message("This server is not allowed.", ephemeral=True)
        return

    session = _current_session(bot, interaction.channel)
    if session is None:
        await interaction.response.send_message("This command must be used inside a session thread.", ephemeral=True)
        return

    agent = bot.agent_store.get_agent(session.agent_id)
    messages = bot.hub_store.list_messages(session.id)
    events = bot.structured_logger.list_events(session_id=session.id)
    usage = _summarize_usage(events)
    queue_depth = _queue_depths_for(bot).get(session.discord_thread_id, 0)
    lines = [
        f"Session ID: `{session.id}`",
        f"Agent: `{agent.id}` ({agent.name})",
        f"Provider: `{session.provider}`",
        f"Model: `{agent.model or 'default'}`",
        f"Created at: `{session.created_at}`",
        f"Created by user ID: `{session.created_by_user_id}`",
        f"Messages: `{len(messages)}`",
        f"Queue depth: `{queue_depth}`",
        f"Input tokens: `{usage['input_tokens']}`",
        f"Output tokens: `{usage['output_tokens']}`",
        f"Total tokens: `{usage['total_tokens']}`",
        f"Cache creation tokens: `{usage['cache_creation_input_tokens']}`",
        f"Cache read tokens: `{usage['cache_read_input_tokens']}`",
        f"Cached input tokens: `{usage['cached_input_tokens']}`",
    ]
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@app_commands.command(name="log-export", description="Export the current session transcript and events")
async def log_export(interaction: discord.Interaction) -> None:
    bot = interaction.client
    assert isinstance(bot, DiscordAgentHub)
    if not bot.guild_allowed(interaction.guild):
        await interaction.response.send_message("This server is not allowed.", ephemeral=True)
        return

    session = _current_session(bot, interaction.channel)
    if session is None:
        await interaction.response.send_message("This command must be used inside a session thread.", ephemeral=True)
        return

    agent = bot.agent_store.get_agent(session.agent_id)
    messages = bot.hub_store.list_messages(session.id)
    events = bot.structured_logger.list_events(session_id=session.id)
    usage = _summarize_usage(events)
    transcript = _build_transcript_markdown(session=session, agent=agent, messages=messages, usage=usage)
    events_jsonl = "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events)

    files = [
        discord.File(BytesIO(transcript.encode("utf-8")), filename=f"{session.id}-transcript.md"),
        discord.File(BytesIO(events_jsonl.encode("utf-8")), filename=f"{session.id}-events.jsonl"),
    ]
    await interaction.response.send_message("Session export", files=files, ephemeral=True)


@app_commands.command(name="usage-report", description="Show a lightweight usage summary for this server")
async def usage_report(interaction: discord.Interaction) -> None:
    bot = interaction.client
    assert isinstance(bot, DiscordAgentHub)
    if not bot.guild_allowed(interaction.guild):
        await interaction.response.send_message("This server is not allowed.", ephemeral=True)
        return

    events = bot.structured_logger.list_events()
    lines = await _usage_report_lines_for_guild(events, guild=interaction.guild)
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@app_commands.command(name="chat", description="Create a thread-bound chat session")
@app_commands.describe(agent_id="Agent ID to use")
async def chat(interaction: discord.Interaction, agent_id: str | None = None) -> None:
    bot = interaction.client
    assert isinstance(bot, DiscordAgentHub)
    if not bot.guild_allowed(interaction.guild):
        await interaction.response.send_message("This server is not allowed.", ephemeral=True)
        return
    if await _is_disallowed_member(bot, interaction.guild, interaction.user):
        bot.structured_logger.append(
            "auth.denied_role",
            discord_guild_id=interaction.guild_id,
            discord_channel_id=getattr(interaction.channel, "id", None),
            user_id=interaction.user.id,
            command="chat",
        )
        await interaction.response.send_message(
            "You are not allowed to start AI chat sessions on this server.",
            ephemeral=True,
        )
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
    guild = getattr(message, "guild", None)
    if await _is_disallowed_member(bot, guild, message.author):
        bot.structured_logger.append(
            "auth.denied_role",
            session_id=session.id,
            discord_thread_id=message.channel.id,
            discord_guild_id=getattr(guild, "id", None),
            user_id=message.author.id,
            command="message",
        )
        await message.channel.send("You are not allowed to use AI chat in this server.")
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
            source_ids = _knowledge_source_ids(agent)
            if source_ids:
                sources = bot.hub_store.get_knowledge_sources(source_ids)
                retrieved_chunks = bot.hub_store.retrieve_knowledge_chunks(
                    source_ids=_hub_lexical_source_ids_for_provider(agent=agent, sources=sources),
                    query=message.content,
                    limit=5,
                )
                if retrieved_chunks:
                    conversation = _attach_knowledge_context(conversation, retrieved_chunks)
                    bot.structured_logger.append(
                        "knowledge.retrieved",
                        session_id=session.id,
                        agent_id=agent.id,
                        source_ids=source_ids,
                        chunk_ids=[chunk.id for chunk in retrieved_chunks],
                    )
                native_metadata = _native_knowledge_metadata(agent=agent, sources=sources)
                if native_metadata != agent.metadata:
                    agent = replace(agent, metadata=native_metadata)
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
                model=_response_model_name(agent=agent, response=response),
                discord_guild_id=session.discord_guild_id,
                created_by_user_id=session.created_by_user_id,
                content=response.output_text,
                usage=response.usage,
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
        if not _is_routable_thread_message(message):
            return
        async with message.channel.typing():
            await handle_user_message(bot, message)
