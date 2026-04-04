from __future__ import annotations

import logging

from discord_agent_hub.bot import DiscordAgentHub, attach_message_handler
from discord_agent_hub.config import load_settings
from discord_agent_hub.providers.anthropic_messages import AnthropicMessagesProvider
from discord_agent_hub.providers.base import ProviderRegistry
from discord_agent_hub.providers.cli_stub import CLIStubProvider
from discord_agent_hub.providers.gemini_api import GeminiAPIProvider
from discord_agent_hub.providers.openai_responses import OpenAIResponsesProvider
from discord_agent_hub.storage import AgentStore, HubStore
from discord_agent_hub.structured_log import StructuredLogger


def build_bot() -> DiscordAgentHub:
    settings = load_settings()
    agent_store = AgentStore(settings.data_dir / "agents.json")
    hub_store = HubStore(settings.data_dir / "hub.sqlite3")
    structured_logger = StructuredLogger(settings.data_dir / "events.jsonl")

    providers = ProviderRegistry()
    providers.register(
        "openai_responses",
        OpenAIResponsesProvider(api_key=settings.openai_api_key, default_model=settings.openai_model),
    )
    providers.register(
        "anthropic_messages",
        AnthropicMessagesProvider(
            api_key=settings.anthropic_api_key,
            default_model=settings.anthropic_model,
        ),
    )
    providers.register(
        "gemini_api",
        GeminiAPIProvider(
            api_key=settings.gemini_api_key,
            default_model=settings.gemini_model,
        ),
    )
    providers.register(
        "claude_code",
        CLIStubProvider(name="claude_code", command=settings.claude_code_command),
    )
    providers.register(
        "gemini_cli",
        CLIStubProvider(name="gemini_cli", command=settings.gemini_cli_command),
    )

    bot = DiscordAgentHub(
        settings=settings,
        agent_store=agent_store,
        hub_store=hub_store,
        provider_registry=providers,
        structured_logger=structured_logger,
    )
    attach_message_handler(bot)
    return bot


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    )
    bot = build_bot()
    if not bot.settings.discord_bot_token:
        raise RuntimeError("DISCORD_BOT_TOKEN is not configured")
    bot.run(bot.settings.discord_bot_token)


if __name__ == "__main__":
    main()
