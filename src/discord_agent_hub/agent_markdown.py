from __future__ import annotations

import re

from discord_agent_hub.models import AgentDefinition, ProviderKind


AGENT_BLOCK_RE = re.compile(r"```agent\s*\n(.*?)\n```", re.DOTALL)


class AgentMarkdownError(ValueError):
    pass


def parse_agent_markdown(markdown_text: str) -> AgentDefinition:
    match = AGENT_BLOCK_RE.search(markdown_text)
    if match is None:
        raise AgentMarkdownError("Missing ```agent fenced block")

    metadata_text = match.group(1).strip()
    instructions = (
        markdown_text[: match.start()] + markdown_text[match.end() :]
    ).strip()
    metadata = _parse_agent_block(metadata_text)

    required_fields = ["id", "name", "provider"]
    missing = [field for field in required_fields if field not in metadata]
    if missing:
        raise AgentMarkdownError(f"Missing required agent fields: {', '.join(missing)}")

    provider_raw = metadata["provider"]
    try:
        provider = ProviderKind(provider_raw)
    except ValueError as exc:
        raise AgentMarkdownError(f"Unknown provider: {provider_raw}") from exc

    tools = metadata.get("tools", {})
    if not isinstance(tools, dict):
        raise AgentMarkdownError("tools must be a mapping")
    for key, value in list(tools.items()):
        if not isinstance(value, bool):
            raise AgentMarkdownError(f"tools.{key} must be true or false")

    return AgentDefinition(
        id=str(metadata["id"]),
        name=str(metadata["name"]),
        provider=provider,
        model=str(metadata["model"]) if "model" in metadata else None,
        description=str(metadata.get("description", "")),
        enabled=bool(metadata.get("enabled", True)),
        tools=tools,
        instructions=instructions,
        metadata={"import_format": "markdown-agent-block"},
    )


def _parse_agent_block(block_text: str) -> dict[str, object]:
    result: dict[str, object] = {}
    current_section: str | None = None

    for raw_line in block_text.splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue

        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if indent and current_section is None:
            raise AgentMarkdownError("Unexpected indentation in agent block")

        if indent:
            if current_section != "tools":
                raise AgentMarkdownError("Nested mappings are only supported under tools")
            nested_key, nested_value = _split_key_value(line.strip())
            tools = result.setdefault("tools", {})
            assert isinstance(tools, dict)
            tools[nested_key] = _parse_scalar(nested_value)
            continue

        key, value = _split_key_value(line)
        parsed_value = _parse_scalar(value)
        if key == "tools":
            if value.strip():
                raise AgentMarkdownError("tools must be declared as a nested mapping")
            result["tools"] = {}
            current_section = "tools"
        else:
            result[key] = parsed_value
            current_section = None

    return result


def _split_key_value(line: str) -> tuple[str, str]:
    if ":" not in line:
        raise AgentMarkdownError(f"Invalid line in agent block: {line}")
    key, value = line.split(":", 1)
    key = key.strip()
    if not key:
        raise AgentMarkdownError(f"Invalid key in agent block: {line}")
    return key, value.strip()


def _parse_scalar(raw: str) -> object:
    lowered = raw.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if raw.startswith('"') and raw.endswith('"') and len(raw) >= 2:
        return raw[1:-1]
    if raw.startswith("'") and raw.endswith("'") and len(raw) >= 2:
        return raw[1:-1]
    return raw
