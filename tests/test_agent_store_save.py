import pytest

from discord_agent_hub.models import AgentDefinition, ProviderKind
from discord_agent_hub.storage import AgentStore


def test_agent_store_save_agent_persists_new_agent(tmp_path):
    store = AgentStore(tmp_path / "agents.json")
    agent = AgentDefinition(
        id="custom-agent",
        name="Custom Agent",
        provider=ProviderKind.OPENAI_RESPONSES,
        model="gpt-5.2",
        description="Imported agent",
        enabled=True,
        tools={"web_search": True},
        instructions="Be useful.",
    )

    store.save_agent(agent)
    loaded = store.get_agent("custom-agent")

    assert loaded.name == "Custom Agent"
    assert loaded.description == "Imported agent"
    assert loaded.tools == {"web_search": True}


def test_agent_store_save_agent_rejects_duplicate_without_overwrite(tmp_path):
    store = AgentStore(tmp_path / "agents.json")
    agent = AgentDefinition(
        id="duplicate-agent",
        name="Duplicate Agent",
        provider=ProviderKind.OPENAI_RESPONSES,
    )

    store.save_agent(agent)

    with pytest.raises(KeyError):
        store.save_agent(agent)


def test_agent_store_save_agent_overwrites_existing_when_requested(tmp_path):
    store = AgentStore(tmp_path / "agents.json")
    original = AgentDefinition(
        id="overwrite-agent",
        name="Original",
        provider=ProviderKind.OPENAI_RESPONSES,
        description="before",
    )
    updated = AgentDefinition(
        id="overwrite-agent",
        name="Updated",
        provider=ProviderKind.OPENAI_RESPONSES,
        description="after",
        tools={"web_search": True},
    )

    store.save_agent(original)
    store.save_agent(updated, overwrite=True)
    loaded = store.get_agent("overwrite-agent")

    assert loaded.name == "Updated"
    assert loaded.description == "after"
    assert loaded.tools == {"web_search": True}
