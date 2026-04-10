from concurrent.futures import ThreadPoolExecutor

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
    assert loaded.public_instructions is True
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
    assert loaded.public_instructions is True
    assert loaded.tools == {"web_search": True}


def test_agent_store_persists_public_instructions_flag(tmp_path):
    store = AgentStore(tmp_path / "agents.json")
    agent = AgentDefinition(
        id="secret-agent",
        name="Secret Agent",
        provider=ProviderKind.OPENAI_RESPONSES,
        public_instructions=False,
        instructions="Do not reveal the puzzle rules.",
    )

    store.save_agent(agent)
    loaded = store.get_agent("secret-agent")

    assert loaded.public_instructions is False


def test_agent_store_save_agent_handles_concurrent_imports(tmp_path):
    store = AgentStore(tmp_path / "agents.json")

    def save(index: int) -> None:
        store.save_agent(
            AgentDefinition(
                id=f"concurrent-agent-{index}",
                name=f"Concurrent Agent {index}",
                provider=ProviderKind.OPENAI_RESPONSES,
                instructions=f"agent {index}",
            )
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(save, range(10)))

    ids = {agent.id for agent in store.list_agents()}
    for index in range(10):
        assert f"concurrent-agent-{index}" in ids


def test_agent_store_keeps_generation_backups_on_write(tmp_path):
    store = AgentStore(tmp_path / "agents.json")

    store.save_agent(
        AgentDefinition(
            id="backup-agent",
            name="Backup Agent",
            provider=ProviderKind.OPENAI_RESPONSES,
        )
    )

    backups = sorted((tmp_path / "backups" / "agents").glob("agents-*.json"))
    assert backups
    latest = backups[-1].read_text(encoding="utf-8")
    assert '"id": "gpt-default"' in latest


def test_agent_store_trims_old_backups(tmp_path):
    store = AgentStore(tmp_path / "agents.json")
    store.backup_retention = 3

    for index in range(6):
        store.save_agent(
            AgentDefinition(
                id=f"trim-agent-{index}",
                name=f"Trim Agent {index}",
                provider=ProviderKind.OPENAI_RESPONSES,
            )
        )

    backups = sorted((tmp_path / "backups" / "agents").glob("agents-*.json"))
    assert len(backups) == 3
