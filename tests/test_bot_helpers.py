from discord_agent_hub.bot import _agent_show_lines, _build_agent_choices, _send_interaction_split
from discord_agent_hub.config import Settings
from discord_agent_hub.models import AgentDefinition, ProviderKind
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


def test_agent_show_lines_uses_preview_by_default():
    agent = AgentDefinition(
        id="long-agent",
        name="Long Agent",
        provider=ProviderKind.OPENAI_RESPONSES,
        instructions="x" * 1500,
    )

    lines = _agent_show_lines(agent=agent, full=False)

    assert lines[-1] == "x" * 1200


def test_agent_show_lines_can_show_full_instructions():
    agent = AgentDefinition(
        id="long-agent",
        name="Long Agent",
        provider=ProviderKind.OPENAI_RESPONSES,
        instructions="x" * 1500,
    )

    lines = _agent_show_lines(agent=agent, full=True)

    assert lines[-1] == "x" * 1500


def test_agent_show_lines_respects_hidden_instructions():
    agent = AgentDefinition(
        id="hidden-agent",
        name="Hidden Agent",
        provider=ProviderKind.OPENAI_RESPONSES,
        instructions="secret",
        public_instructions=False,
    )

    lines = _agent_show_lines(agent=agent, full=True)

    assert lines[-1] == "(hidden for this agent)"


class _FakeResponse:
    def __init__(self) -> None:
        self.calls = []

    async def send_message(self, content: str, ephemeral: bool) -> None:
        self.calls.append((content, ephemeral))


class _FakeFollowup:
    def __init__(self) -> None:
        self.calls = []

    async def send(self, content: str, ephemeral: bool) -> None:
        self.calls.append((content, ephemeral))


class _FakeInteraction:
    def __init__(self) -> None:
        self.response = _FakeResponse()
        self.followup = _FakeFollowup()


async def test_send_interaction_split_sends_followups_for_long_content():
    interaction = _FakeInteraction()
    content = "x" * 4000

    await _send_interaction_split(interaction, content, ephemeral=True)

    assert len(interaction.response.calls) == 1
    assert len(interaction.followup.calls) == 2
    assert interaction.response.calls[0][1] is True
    assert all(ephemeral is True for _, ephemeral in interaction.followup.calls)
    assert len(interaction.response.calls[0][0]) <= 1800
    assert all(len(text) <= 1800 for text, _ in interaction.followup.calls)
