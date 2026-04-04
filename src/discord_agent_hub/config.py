from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field


class Settings(BaseModel):
    discord_bot_token: str = Field(alias="DISCORD_BOT_TOKEN")
    discord_client_id: str | None = Field(default=None, alias="DISCORD_CLIENT_ID")
    allowed_server_ids_raw: str = Field(default="", alias="ALLOWED_SERVER_IDS")
    dev_guild_id: int | None = Field(default=None, alias="DEV_GUILD_ID")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-5.2", alias="OPENAI_MODEL")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(default="claude-sonnet-4-0", alias="ANTHROPIC_MODEL")
    gemini_api_key: str | None = Field(default=None, alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-2.5-pro", alias="GEMINI_MODEL")
    data_dir: Path = Field(default=Path("./data"), alias="DATA_DIR")
    default_agent_id: str = Field(default="openai-default", alias="DEFAULT_AGENT_ID")
    claude_code_command: str = Field(default="claude", alias="CLAUDE_CODE_COMMAND")
    gemini_cli_command: str = Field(default="gemini", alias="GEMINI_CLI_COMMAND")

    @property
    def allowed_server_ids(self) -> set[int]:
        values = [item.strip() for item in self.allowed_server_ids_raw.split(",") if item.strip()]
        return {int(item) for item in values}


def load_settings() -> Settings:
    load_dotenv()
    raw = {
        "DISCORD_BOT_TOKEN": os.getenv("DISCORD_BOT_TOKEN", ""),
        "DISCORD_CLIENT_ID": os.getenv("DISCORD_CLIENT_ID"),
        "ALLOWED_SERVER_IDS": os.getenv("ALLOWED_SERVER_IDS", ""),
        "DEV_GUILD_ID": os.getenv("DEV_GUILD_ID"),
        "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY"),
        "OPENAI_MODEL": os.getenv("OPENAI_MODEL", "gpt-5.2"),
        "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY"),
        "ANTHROPIC_MODEL": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-0"),
        "GEMINI_API_KEY": os.getenv("GEMINI_API_KEY"),
        "GEMINI_MODEL": os.getenv("GEMINI_MODEL", "gemini-2.5-pro"),
        "DATA_DIR": os.getenv("DATA_DIR", "./data"),
        "DEFAULT_AGENT_ID": os.getenv("DEFAULT_AGENT_ID", "openai-default"),
        "CLAUDE_CODE_COMMAND": os.getenv("CLAUDE_CODE_COMMAND", "claude"),
        "GEMINI_CLI_COMMAND": os.getenv("GEMINI_CLI_COMMAND", "gemini"),
    }
    settings = Settings.model_validate(raw)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings
