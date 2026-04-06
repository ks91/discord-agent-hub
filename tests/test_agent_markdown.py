import pytest

from discord_agent_hub.agent_markdown import AgentMarkdownError, parse_agent_markdown
from discord_agent_hub.models import ProviderKind


def test_parse_agent_markdown_extracts_agent_block_and_body():
    markdown = """# GAMER PAT

Short intro.

```agent
id: gamer-pat
name: GAMER PAT
provider: anthropic_messages
model: claude-sonnet-4-0
description: Game studies assistant
enabled: true
public_instructions: false
tools:
  web_search: true
  code_execution: false
```

## Instructions

You are GAMER PAT.

Focus on evidence.
"""

    agent = parse_agent_markdown(markdown)

    assert agent.id == "gamer-pat"
    assert agent.name == "GAMER PAT"
    assert agent.provider == ProviderKind.ANTHROPIC_MESSAGES
    assert agent.model == "claude-sonnet-4-0"
    assert agent.description == "Game studies assistant"
    assert agent.enabled is True
    assert agent.public_instructions is False
    assert agent.tools == {"web_search": True, "code_execution": False}
    assert "You are GAMER PAT." in agent.instructions
    assert "## Instructions" in agent.instructions


def test_parse_agent_markdown_requires_agent_block():
    with pytest.raises(AgentMarkdownError):
        parse_agent_markdown("# No agent block here")


def test_parse_agent_markdown_rejects_unknown_provider():
    markdown = """```agent
id: bad-agent
name: Bad Agent
provider: unknown_provider
```

Instructions.
"""

    with pytest.raises(AgentMarkdownError):
        parse_agent_markdown(markdown)
