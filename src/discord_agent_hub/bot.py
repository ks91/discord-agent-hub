from __future__ import annotations

import logging
from typing import Iterable

import discord
from discord import app_commands
from discord.ext import commands

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


def _build_agent_choices(agent_store: AgentStore, current: str) -> list[app_commands.Choice[str]]:
    current_lower = current.lower().strip()
    matches: Iterable = agent_store.list_agents()
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


@app_commands.command(name="agent-list", description="List available agents")
async def agent_list(interaction: discord.Interaction) -> None:
    bot = interaction.client
    assert isinstance(bot, DiscordAgentHub)
    if not bot.guild_allowed(interaction.guild):
        await interaction.response.send_message("This server is not allowed.", ephemeral=True)
        return

    agents = bot.agent_store.list_agents()
    lines = [f"- `{agent.id}`: {agent.name} ({agent.provider.value})" for agent in agents]
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


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
    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("Use this command in a text channel.", ephemeral=True)
        return

    try:
        agent = bot.agent_store.get_agent(agent_id or bot.settings.default_agent_id)
    except KeyError:
        await interaction.response.send_message(
            f"Unknown agent_id: `{agent_id}`. Use `/agent-list` to see valid options.",
            ephemeral=True,
        )
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

    conversation = bot.hub_store.list_messages(session.id)
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
