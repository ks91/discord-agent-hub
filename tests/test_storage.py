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
