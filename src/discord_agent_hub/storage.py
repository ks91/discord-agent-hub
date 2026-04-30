from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from discord_agent_hub.knowledge import KnowledgeChunk, score_chunk, split_text_into_chunks
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
    backup_retention = 10

    def __init__(self, path: Path) -> None:
        self.path = path
        self.backup_dir = self.path.parent / "backups" / "agents"
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            if not self.path.exists():
                self._write_json_atomic(DEFAULT_AGENTS)

    def list_agents(self) -> list[AgentDefinition]:
        with self._lock:
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
        with self._lock:
            for agent in self.list_agents():
                if agent.id == agent_id:
                    return agent
        raise KeyError(f"Unknown agent_id: {agent_id}")

    def save_agent(self, agent: AgentDefinition, *, overwrite: bool = False) -> None:
        with self._lock:
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
        with self._lock:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            filtered = [item for item in raw if item["id"] != agent_id]
            if len(filtered) == len(raw):
                raise KeyError(f"Unknown agent_id: {agent_id}")
            self._write_json_atomic(filtered)

    def _write_json_atomic(self, payload: list[dict]) -> None:
        self._backup_current_file()
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

    def _backup_current_file(self) -> None:
        if not self.path.exists():
            return
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        backup_path = self.backup_dir / f"{self.path.stem}-{timestamp}.json"
        backup_path.write_text(self.path.read_text(encoding="utf-8"), encoding="utf-8")
        backups = sorted(self.backup_dir.glob(f"{self.path.stem}-*.json"))
        excess = len(backups) - self.backup_retention
        if excess > 0:
            for old_path in backups[:excess]:
                old_path.unlink()


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

                create table if not exists knowledge_sources (
                    id text primary key,
                    backend text not null default 'hub_lexical',
                    remote_store_id text,
                    created_by_user_id integer,
                    created_at text not null
                );

                create table if not exists knowledge_documents (
                    id text primary key,
                    source_id text not null,
                    filename text not null,
                    media_type text not null,
                    text text not null,
                    created_at text not null
                );

                create table if not exists knowledge_chunks (
                    id text primary key,
                    source_id text not null,
                    document_id text not null,
                    chunk_index integer not null,
                    filename text not null,
                    text text not null
                );
                """
            )
            existing_columns = {
                row["name"] for row in conn.execute("pragma table_info(messages)").fetchall()
            }
            if "attachments" not in existing_columns:
                conn.execute("alter table messages add column attachments text not null default '[]'")
            source_columns = {
                row["name"] for row in conn.execute("pragma table_info(knowledge_sources)").fetchall()
            }
            if "backend" not in source_columns:
                conn.execute("alter table knowledge_sources add column backend text not null default 'hub_lexical'")
            if "remote_store_id" not in source_columns:
                conn.execute("alter table knowledge_sources add column remote_store_id text")

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

    def import_knowledge_document(
        self,
        *,
        source_id: str,
        filename: str,
        media_type: str,
        text: str,
        created_by_user_id: int | None,
        overwrite: bool = False,
        backend: str = "hub_lexical",
        remote_store_id: str | None = None,
    ) -> tuple[str, int]:
        source_id = source_id.strip()
        if not source_id:
            raise ValueError("source_id is required")
        backend = backend.strip() or "hub_lexical"
        document_id = str(uuid.uuid4())
        created_at = utc_now()
        chunks = split_text_into_chunks(text)
        with self._connect() as conn:
            if overwrite:
                conn.execute("delete from knowledge_chunks where source_id = ?", (source_id,))
                conn.execute("delete from knowledge_documents where source_id = ?", (source_id,))
                conn.execute("delete from knowledge_sources where id = ?", (source_id,))
            conn.execute(
                """
                insert into knowledge_sources (id, backend, remote_store_id, created_by_user_id, created_at)
                values (?, ?, ?, ?, ?)
                on conflict(id) do update set
                    backend = excluded.backend,
                    remote_store_id = coalesce(excluded.remote_store_id, knowledge_sources.remote_store_id)
                """,
                (source_id, backend, remote_store_id, created_by_user_id, created_at),
            )
            conn.execute(
                """
                insert into knowledge_documents (id, source_id, filename, media_type, text, created_at)
                values (?, ?, ?, ?, ?, ?)
                """,
                (document_id, source_id, filename, media_type, text, created_at),
            )
            for index, chunk in enumerate(chunks, start=1):
                conn.execute(
                    """
                    insert into knowledge_chunks (id, source_id, document_id, chunk_index, filename, text)
                    values (?, ?, ?, ?, ?, ?)
                    """,
                    (str(uuid.uuid4()), source_id, document_id, index, filename, chunk),
                )
        return document_id, len(chunks)

    def list_knowledge_sources(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select
                    s.id,
                    s.backend,
                    s.remote_store_id,
                    s.created_by_user_id,
                    s.created_at,
                    count(distinct d.id) as document_count,
                    count(c.id) as chunk_count
                from knowledge_sources s
                left join knowledge_documents d on d.source_id = s.id
                left join knowledge_chunks c on c.source_id = s.id
                group by s.id, s.backend, s.remote_store_id, s.created_by_user_id, s.created_at
                order by s.id asc
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_knowledge_sources(self, source_ids: list[str]) -> list[dict]:
        if not source_ids:
            return []
        placeholders = ",".join("?" for _ in source_ids)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                select id, backend, remote_store_id, created_by_user_id, created_at
                from knowledge_sources
                where id in ({placeholders})
                """,
                tuple(source_ids),
            ).fetchall()
        by_id = {row["id"]: dict(row) for row in rows}
        return [by_id[source_id] for source_id in source_ids if source_id in by_id]

    def list_knowledge_documents(self, source_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select
                    d.id,
                    d.source_id,
                    d.filename,
                    d.media_type,
                    d.created_at,
                    length(d.text) as text_chars,
                    count(c.id) as chunk_count
                from knowledge_documents d
                left join knowledge_chunks c on c.document_id = d.id
                where d.source_id = ?
                group by d.id, d.source_id, d.filename, d.media_type, d.created_at, d.text
                order by d.created_at asc, d.filename asc
                """,
                (source_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def retrieve_knowledge_chunks(
        self,
        *,
        source_ids: list[str],
        query: str,
        limit: int = 5,
    ) -> list[KnowledgeChunk]:
        if not source_ids or not query.strip() or limit <= 0:
            return []
        placeholders = ",".join("?" for _ in source_ids)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                select id, source_id, document_id, chunk_index, filename, text
                from knowledge_chunks
                where source_id in ({placeholders})
                """,
                tuple(source_ids),
            ).fetchall()
        scored = []
        for row in rows:
            payload = dict(row)
            score = score_chunk(query, payload["text"])
            if score <= 0:
                continue
            scored.append(KnowledgeChunk(**payload, score=score))
        scored.sort(key=lambda chunk: (-chunk.score, chunk.source_id, chunk.filename, chunk.chunk_index))
        return scored[:limit]
