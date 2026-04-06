from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProviderKind(str, Enum):
    OPENAI_RESPONSES = "openai_responses"
    ANTHROPIC_MESSAGES = "anthropic_messages"
    GEMINI_API = "gemini_api"
    CLAUDE_CODE = "claude_code"
    GEMINI_CLI = "gemini_cli"


@dataclass(slots=True)
class AgentDefinition:
    id: str
    name: str
    provider: ProviderKind
    model: str | None = None
    description: str = ""
    enabled: bool = True
    public_instructions: bool = True
    tools: dict[str, bool] = field(default_factory=dict)
    instructions: str = ""
    command: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SessionRecord:
    id: str
    agent_id: str
    provider: str
    discord_channel_id: int
    discord_thread_id: int
    discord_guild_id: int
    created_by_user_id: int
    created_at: str
    provider_session_id: str | None = None


@dataclass(slots=True)
class MessageRecord:
    session_id: str
    role: str
    author_id: int | None
    author_name: str | None
    content: str
    created_at: str
    attachments: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class ProviderResponse:
    output_text: str
    provider_session_id: str | None = None
    raw_payload: dict[str, Any] | None = None
