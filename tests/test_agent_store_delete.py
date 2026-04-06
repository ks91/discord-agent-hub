import pytest

from discord_agent_hub.storage import AgentStore


def test_agent_store_delete_agent_removes_existing_agent(tmp_path):
    store = AgentStore(tmp_path / "agents.json")

    store.delete_agent("gpt-default")

    ids = [agent.id for agent in store.list_agents()]
    assert "gpt-default" not in ids


def test_agent_store_delete_agent_raises_for_missing_agent(tmp_path):
    store = AgentStore(tmp_path / "agents.json")

    with pytest.raises(KeyError):
        store.delete_agent("missing-agent")
