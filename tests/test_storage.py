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
