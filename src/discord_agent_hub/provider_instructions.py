from __future__ import annotations

from discord_agent_hub.models import AgentDefinition


DEFAULT_INSTRUCTIONS = "You are a helpful assistant."

CODE_EXECUTION_CAPABILITY_NOTE = (
    "Environment capability note: This environment provides provider-side code execution "
    "when useful. If calculation, data transformation, simulation, or verification would "
    "improve the answer, you may use code execution. Do not claim that code execution is "
    "unavailable merely because you are an AI model."
)


def render_provider_instructions(agent: AgentDefinition) -> str:
    instructions = agent.instructions or DEFAULT_INSTRUCTIONS
    if not agent.tools.get("code_execution"):
        return instructions
    return f"{instructions.rstrip()}\n\n{CODE_EXECUTION_CAPABILITY_NOTE}"
