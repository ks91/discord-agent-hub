from __future__ import annotations

from openai import AsyncOpenAI

from discord_agent_hub.conversation_render import render_message_text
from discord_agent_hub.models import AgentDefinition, MessageRecord, ProviderResponse
from discord_agent_hub.provider_instructions import render_provider_instructions
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
        instructions = render_provider_instructions(agent)
        input_items = []
        for item in conversation:
            if item.role == "system":
                continue
            role = "assistant" if item.role == "assistant" else "user"
            content_type = "output_text" if role == "assistant" else "input_text"
            content = []
            for attachment in item.attachments:
                if attachment.get("type") != "image" or role == "assistant":
                    continue
                data_url = (
                    f"data:{attachment['media_type']};base64,{attachment['data']}"
                )
                content.append({"type": "input_image", "image_url": data_url, "detail": "auto"})
            text = render_message_text(item)
            if text.strip() or not content:
                content.append({"type": content_type, "text": text})
            input_items.append(
                {
                    "role": role,
                    "content": content,
                }
            )

        request = {
            "model": agent.model or self.default_model,
            "instructions": instructions,
            "input": input_items,
        }
        tools = []
        if agent.tools.get("web_search"):
            tools.append({"type": "web_search"})
        if agent.tools.get("code_execution"):
            tools.append({"type": "code_interpreter", "container": {"type": "auto"}})
        vector_store_ids = agent.metadata.get("openai_vector_store_ids")
        if isinstance(vector_store_ids, list) and vector_store_ids:
            tools.append(
                {
                    "type": "file_search",
                    "vector_store_ids": [str(item) for item in vector_store_ids if str(item)],
                }
            )
        if tools:
            request["tools"] = tools
            request["tool_choice"] = "auto"

        response = await self.client.responses.create(**request)
        return ProviderResponse(
            output_text=response.output_text,
            provider_session_id=provider_session_id,
            raw_payload=response.model_dump(),
            usage=self._extract_usage(response.model_dump()),
        )

    @staticmethod
    def _extract_usage(payload: dict) -> dict:
        usage = payload.get("usage") or {}
        input_details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
        return {
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "cached_input_tokens": input_details.get("cached_tokens"),
        }
