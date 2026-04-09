import discord

from discord_agent_hub.bot import _build_agent_choices, _is_chat_eligible, _is_routable_thread_message
from discord_agent_hub.storage import AgentStore


def test_chat_choices_hide_disabled_cli_defaults(tmp_path):
    store = AgentStore(tmp_path / "agents.json")

    choices = _build_agent_choices(store, "")
    values = [choice.value for choice in choices]

    assert "gpt-default" in values
    assert "claude-default" in values
    assert "gemini-default" in values


def test_chat_eligibility_accepts_default_api_agents(tmp_path):
    store = AgentStore(tmp_path / "agents.json")
    agent = store.get_agent("gpt-default")

    assert _is_chat_eligible(agent) is True


def test_routable_thread_message_accepts_default_message_type():
    message = type("Message", (), {"type": discord.MessageType.default})()

    assert _is_routable_thread_message(message) is True


def test_routable_thread_message_rejects_channel_name_change():
    message = type("Message", (), {"type": discord.MessageType.channel_name_change})()

    assert _is_routable_thread_message(message) is False
