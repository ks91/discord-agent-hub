from discord_agent_hub.bot import _build_agent_choices
from discord_agent_hub.config import Settings
from discord_agent_hub.storage import AgentStore


def test_build_agent_choices_returns_limited_results(tmp_path):
    store = AgentStore(tmp_path / "agents.json")

    choices = _build_agent_choices(store, "")

    assert choices
    assert len(choices) <= 25
    assert any(choice.value == "gpt-default" for choice in choices)


def test_build_agent_choices_filters_by_id_or_name(tmp_path):
    store = AgentStore(tmp_path / "agents.json")

    id_matches = _build_agent_choices(store, "Claude")
    name_matches = _build_agent_choices(store, "Gemini")

    assert [choice.value for choice in id_matches] == ["claude-default"]
    assert [choice.value for choice in name_matches] == ["gemini-default"]


def test_settings_parse_dev_guild_id(tmp_path):
    settings = Settings.model_validate(
        {
            "DISCORD_BOT_TOKEN": "token",
            "DISCORD_CLIENT_ID": None,
            "ALLOWED_SERVER_IDS": "",
            "DISALLOWED_ROLE_IDS": "111,222",
            "DEV_GUILD_ID": "123456789",
            "OPENAI_API_KEY": None,
            "OPENAI_MODEL": "gpt-5.2",
            "ANTHROPIC_API_KEY": None,
            "ANTHROPIC_MODEL": "claude-sonnet-4-0",
            "GEMINI_API_KEY": None,
            "GEMINI_MODEL": "gemini-2.5-pro",
            "PROVIDER_REQUEST_TIMEOUT_SECONDS": "90",
            "PROVIDER_MAX_RETRIES": "2",
            "PROVIDER_RETRY_BACKOFF_SECONDS": "1",
            "DATA_DIR": str(tmp_path / "data"),
            "DEFAULT_AGENT_ID": "gpt-default",
            "CLAUDE_CODE_COMMAND": "claude",
            "GEMINI_CLI_COMMAND": "gemini",
        }
    )

    assert settings.dev_guild_id == 123456789
    assert settings.disallowed_role_ids == {111, 222}
    assert settings.provider_request_timeout_seconds == 90.0
    assert settings.provider_max_retries == 2
    assert settings.provider_retry_backoff_seconds == 1.0
