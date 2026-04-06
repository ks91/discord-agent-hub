from __future__ import annotations

import base64
from dataclasses import replace
import logging
import mimetypes
from typing import Iterable

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import View

from discord_agent_hub.agent_markdown import AgentMarkdownError, parse_agent_markdown
from discord_agent_hub.config import Settings
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


async def _extract_image_attachments(message: discord.Message) -> list[dict[str, str]]:
    attachments = []
    for attachment in getattr(message, "attachments", []):
        content_type = attachment.content_type or mimetypes.guess_type(attachment.filename)[0]
        if not content_type or not content_type.startswith("image/"):
            continue
        raw = await attachment.read()
        attachments.append(
            {
                "type": "image",
                "filename": attachment.filename,
                "media_type": content_type,
                "data": base64.b64encode(raw).decode("ascii"),
            }
        )
    return attachments


def _compact_conversation_for_provider(conversation: list[MessageRecord]) -> list[MessageRecord]:
    latest_user_image_index = None
    for index, item in enumerate(conversation):
        if item.role == "user" and any(att.get("type") == "image" for att in item.attachments):
            latest_user_image_index = index

    compacted = []
    for index, item in enumerate(conversation):
        if index == latest_user_image_index:
            compacted.append(item)
            continue
        if item.attachments:
            compacted.append(replace(item, attachments=[]))
        else:
            compacted.append(item)
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
        f"Tools: `{tools_text}`",
    ]
    if agent.description:
        lines.append(f"Description: {agent.description}")
    lines.append("")
    lines.append("Instructions preview:")
    lines.append(instructions_preview)
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

    agent = bot.agent_store.get_agent(session.agent_id)
    provider = bot.provider_registry.get(session.provider)

    user_record = MessageRecord(
        session_id=session.id,
        role="user",
        author_id=message.author.id,
        author_name=message.author.display_name,
        content=message.content,
        attachments=await _extract_image_attachments(message),
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
        response = await provider.generate(
            agent=agent,
            conversation=conversation,
            provider_session_id=session.provider_session_id,
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
