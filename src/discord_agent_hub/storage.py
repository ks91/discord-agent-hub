from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import uuid
from pathlib import Path

from discord_agent_hub.models import AgentDefinition, MessageRecord, ProviderKind, SessionRecord, utc_now


DEFAULT_AGENTS = [
    {
        "id": "gpt-default",
        "name": "GPT Default",
        "provider": "openai_responses",
        "model": "gpt-5.2",
        "description": "Stable GPT default agent",
        "enabled": True,
        "public_instructions": True,
        "tools": {},
        "instructions": "You are a helpful multi-user research assistant in Discord.",
        "metadata": {"supports_threads": True},
    },
    {
        "id": "claude-default",
        "name": "Claude Default",
        "provider": "anthropic_messages",
        "model": "claude-sonnet-4-0",
        "description": "Stable Claude default agent",
        "enabled": True,
        "public_instructions": True,
        "tools": {},
        "instructions": "You are a helpful multi-user research assistant in Discord.",
        "metadata": {"supports_threads": True},
    },
    {
        "id": "gemini-default",
        "name": "Gemini Default",
        "provider": "gemini_api",
        "model": "gemini-2.5-pro",
        "description": "Stable Gemini default agent",
        "enabled": True,
        "public_instructions": True,
        "tools": {},
        "instructions": "You are a helpful multi-user research assistant in Discord.",
        "metadata": {"supports_threads": True},
    },
]


class AgentStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write_json_atomic(DEFAULT_AGENTS)

    def list_agents(self) -> list[AgentDefinition]:
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        return [
            AgentDefinition(
                id=item["id"],
                name=item["name"],
                provider=ProviderKind(item["provider"]),
                model=item.get("model"),
                description=item.get("description", ""),
                enabled=item.get("enabled", True),
                public_instructions=item.get("public_instructions", True),
                tools=item.get("tools", {}),
                instructions=item.get("instructions", ""),
                command=item.get("command", []),
                metadata=item.get("metadata", {}),
            )
            for item in raw
        ]

    def get_agent(self, agent_id: str) -> AgentDefinition:
        for agent in self.list_agents():
            if agent.id == agent_id:
                return agent
        raise KeyError(f"Unknown agent_id: {agent_id}")

    def save_agent(self, agent: AgentDefinition, *, overwrite: bool = False) -> None:
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        existing_index = next((i for i, item in enumerate(raw) if item["id"] == agent.id), None)
        serialized = {
            "id": agent.id,
            "name": agent.name,
            "provider": agent.provider.value,
            "model": agent.model,
            "description": agent.description,
            "enabled": agent.enabled,
            "public_instructions": agent.public_instructions,
            "tools": agent.tools,
            "instructions": agent.instructions,
            "command": agent.command,
            "metadata": agent.metadata,
        }
        if existing_index is None:
            raw.append(serialized)
        elif overwrite:
            raw[existing_index] = serialized
        else:
            raise KeyError(f"Agent already exists: {agent.id}")
        self._write_json_atomic(raw)

    def delete_agent(self, agent_id: str) -> None:
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        filtered = [item for item in raw if item["id"] != agent_id]
        if len(filtered) == len(raw):
            raise KeyError(f"Unknown agent_id: {agent_id}")
        self._write_json_atomic(filtered)

    def _write_json_atomic(self, payload: list[dict]) -> None:
        fd, temp_name = tempfile.mkstemp(
            dir=str(self.path.parent),
            prefix=f"{self.path.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)


class HubStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("pragma journal_mode=WAL")
        conn.execute("pragma busy_timeout = 5000")
        conn.execute("pragma synchronous = NORMAL")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                create table if not exists sessions (
                    id text primary key,
                    agent_id text not null,
                    provider text not null,
                    discord_channel_id integer not null,
                    discord_thread_id integer not null,
                    discord_guild_id integer not null,
                    created_by_user_id integer not null,
                    created_at text not null,
                    provider_session_id text
                );

                create table if not exists messages (
                    id integer primary key autoincrement,
                    session_id text not null,
                    role text not null,
                    author_id integer,
                    author_name text,
                    content text not null,
                    attachments text not null default '[]',
                    created_at text not null
                );
                """
            )
            existing_columns = {
                row["name"] for row in conn.execute("pragma table_info(messages)").fetchall()
            }
            if "attachments" not in existing_columns:
                conn.execute("alter table messages add column attachments text not null default '[]'")

    def create_session(
        self,
        *,
        agent_id: str,
        provider: str,
        discord_channel_id: int,
        discord_thread_id: int,
        discord_guild_id: int,
        created_by_user_id: int,
    ) -> SessionRecord:
        record = SessionRecord(
            id=str(uuid.uuid4()),
            agent_id=agent_id,
            provider=provider,
            discord_channel_id=discord_channel_id,
            discord_thread_id=discord_thread_id,
            discord_guild_id=discord_guild_id,
            created_by_user_id=created_by_user_id,
            created_at=utc_now(),
            provider_session_id=None,
        )
        with self._connect() as conn:
            conn.execute(
                """
                insert into sessions (
                    id, agent_id, provider, discord_channel_id, discord_thread_id,
                    discord_guild_id, created_by_user_id, created_at, provider_session_id
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.agent_id,
                    record.provider,
                    record.discord_channel_id,
                    record.discord_thread_id,
                    record.discord_guild_id,
                    record.created_by_user_id,
                    record.created_at,
                    record.provider_session_id,
                ),
            )
        return record

    def get_session_by_thread_id(self, discord_thread_id: int) -> SessionRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "select * from sessions where discord_thread_id = ?",
                (discord_thread_id,),
            ).fetchone()
        if row is None:
            return None
        return SessionRecord(**dict(row))

    def update_provider_session_id(self, session_id: str, provider_session_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "update sessions set provider_session_id = ? where id = ?",
                (provider_session_id, session_id),
            )

    def add_message(self, message: MessageRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                insert into messages (session_id, role, author_id, author_name, content, attachments, created_at)
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.session_id,
                    message.role,
                    message.author_id,
                    message.author_name,
                    message.content,
                    json.dumps(message.attachments, ensure_ascii=False),
                    message.created_at,
                ),
            )

    def list_messages(self, session_id: str) -> list[MessageRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "select session_id, role, author_id, author_name, content, attachments, created_at from messages where session_id = ? order by id asc",
                (session_id,),
            ).fetchall()
        messages = []
        for row in rows:
            payload = dict(row)
            payload["attachments"] = json.loads(payload.get("attachments") or "[]")
            messages.append(MessageRecord(**payload))
        return messages
