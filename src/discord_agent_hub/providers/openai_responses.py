from __future__ import annotations

from openai import AsyncOpenAI

from discord_agent_hub.models import AgentDefinition, MessageRecord, ProviderResponse
from discord_agent_hub.providers.base import Provider


class OpenAIResponsesProvider(Provider):
    def __init__(self, *, api_key: str | None, default_model: str) -> None:
        self.client = AsyncOpenAI(api_key=api_key)
        self.default_model = default_model

    async def generate(
        self,
        *,
        agent: AgentDefinition,
        conversation: list[MessageRecord],
        provider_session_id: str | None,
    ) -> ProviderResponse:
        instructions = agent.instructions or "You are a helpful assistant."
        input_items = []
        for item in conversation:
            if item.role == "system":
                continue
            role = "assistant" if item.role == "assistant" else "user"
            text = item.content if not item.author_name else f"{item.author_name}: {item.content}"
            input_items.append(
                {
                    "role": role,
                    "content": [{"type": "input_text", "text": text}],
                }
            )

        response = await self.client.responses.create(
            model=agent.model or self.default_model,
            instructions=instructions,
            input=input_items,
        )
        return ProviderResponse(
            output_text=response.output_text,
            provider_session_id=provider_session_id,
            raw_payload=response.model_dump(),
        )
