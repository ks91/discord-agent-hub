from discord_agent_hub.models import MessageRecord
from discord_agent_hub.storage import AgentStore, HubStore


def test_agent_store_bootstraps_default_agents(tmp_path):
    store = AgentStore(tmp_path / "agents.json")

    agents = store.list_agents()

    assert len(agents) >= 3
    assert agents[0].id == "gpt-default"


def test_hub_store_creates_and_reads_session(tmp_path):
    store = HubStore(tmp_path / "hub.sqlite3")

    session = store.create_session(
        agent_id="gpt-default",
        provider="openai_responses",
        discord_channel_id=100,
        discord_thread_id=200,
        discord_guild_id=300,
        created_by_user_id=400,
    )

    loaded = store.get_session_by_thread_id(200)

    assert loaded is not None
    assert loaded.id == session.id
    assert loaded.agent_id == "gpt-default"


def test_hub_store_updates_provider_session_id(tmp_path):
    store = HubStore(tmp_path / "hub.sqlite3")
    session = store.create_session(
        agent_id="gpt-default",
        provider="openai_responses",
        discord_channel_id=100,
        discord_thread_id=200,
        discord_guild_id=300,
        created_by_user_id=400,
    )

    store.update_provider_session_id(session.id, "resp_123")
    loaded = store.get_session_by_thread_id(200)

    assert loaded is not None
    assert loaded.provider_session_id == "resp_123"


def test_hub_store_persists_message_attachments(tmp_path):
    store = HubStore(tmp_path / "hub.sqlite3")
    session = store.create_session(
        agent_id="gpt-default",
        provider="openai_responses",
        discord_channel_id=100,
        discord_thread_id=200,
        discord_guild_id=300,
        created_by_user_id=400,
    )

    store.add_message(
        MessageRecord(
            session_id=session.id,
            role="user",
            author_id=1,
            author_name="alice",
            content="look at this",
            attachments=[
                {
                    "type": "image",
                    "filename": "cat.png",
                    "media_type": "image/png",
                    "data": "ZmFrZQ==",
                }
            ],
            created_at="2026-04-06T00:00:00+00:00",
        )
    )

    messages = store.list_messages(session.id)

    assert messages[0].attachments == [
        {
            "type": "image",
            "filename": "cat.png",
            "media_type": "image/png",
            "data": "ZmFrZQ==",
        }
    ]


def test_hub_store_enables_wal_and_busy_timeout(tmp_path):
    store = HubStore(tmp_path / "hub.sqlite3")

    with store._connect() as conn:
        journal_mode = conn.execute("pragma journal_mode").fetchone()[0]
        busy_timeout = conn.execute("pragma busy_timeout").fetchone()[0]

    assert str(journal_mode).lower() == "wal"
    assert busy_timeout == 5000


def test_hub_store_imports_and_retrieves_knowledge_chunks(tmp_path):
    store = HubStore(tmp_path / "hub.sqlite3")

    document_id, chunk_count = store.import_knowledge_document(
        source_id="quiz-source",
        filename="notes.md",
        media_type="text/markdown",
        text="金融の未来ではサイバーフィジカル社会と決済インフラが重要です。",
        created_by_user_id=123,
    )

    assert document_id
    assert chunk_count == 1
    sources = store.list_knowledge_sources()
    assert sources == [
        {
            "id": "quiz-source",
            "backend": "hub_lexical",
            "remote_store_id": None,
            "created_by_user_id": 123,
            "created_at": sources[0]["created_at"],
            "document_count": 1,
            "chunk_count": 1,
        }
    ]
    chunks = store.retrieve_knowledge_chunks(
        source_ids=["quiz-source"],
        query="決済インフラについて教えて",
        limit=3,
    )

    assert len(chunks) == 1
    assert chunks[0].source_id == "quiz-source"
    assert chunks[0].filename == "notes.md"
    assert "決済インフラ" in chunks[0].text


def test_hub_store_overwrites_whole_knowledge_source(tmp_path):
    store = HubStore(tmp_path / "hub.sqlite3")
    store.import_knowledge_document(
        source_id="quiz-source",
        filename="old.md",
        media_type="text/markdown",
        text="old settlement notes",
        created_by_user_id=123,
    )

    document_id, chunk_count = store.import_knowledge_document(
        source_id="quiz-source",
        filename="new.md",
        media_type="text/markdown",
        text="new cyber physical notes",
        created_by_user_id=456,
        overwrite=True,
    )

    assert document_id
    assert chunk_count == 1
    sources = store.list_knowledge_sources()
    assert sources[0]["document_count"] == 1
    chunks = store.retrieve_knowledge_chunks(
        source_ids=["quiz-source"],
        query="settlement cyber physical",
        limit=10,
    )
    assert [chunk.filename for chunk in chunks] == ["new.md"]
    assert "new cyber physical notes" in chunks[0].text


def test_hub_store_records_knowledge_backend_and_remote_store(tmp_path):
    store = HubStore(tmp_path / "hub.sqlite3")

    store.import_knowledge_document(
        source_id="gpt-papers-openai",
        filename="paper.pdf",
        media_type="application/pdf",
        text="semantic retrieval",
        created_by_user_id=123,
        backend="openai_file_search",
        remote_store_id="vs_123",
    )

    source = store.get_knowledge_sources(["gpt-papers-openai"])[0]

    assert source["backend"] == "openai_file_search"
    assert source["remote_store_id"] == "vs_123"
