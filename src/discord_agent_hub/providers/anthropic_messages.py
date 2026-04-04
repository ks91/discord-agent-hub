from __future__ import annotations

from typing import Any

import httpx

from discord_agent_hub.models import AgentDefinition, MessageRecord, ProviderResponse
from discord_agent_hub.providers.base import Provider


class AnthropicMessagesProvider(Provider):
    def __init__(
        self,
        *,
        api_key: str | None,
        default_model: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.default_model = default_model
        self.http_client = http_client or httpx.AsyncClient(
            base_url="https://api.anthropic.com",
            timeout=60.0,
        )

    async def generate(
        self,
        *,
        agent: AgentDefinition,
        conversation: list[MessageRecord],
        provider_session_id: str | None,
    ) -> ProviderResponse:
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not configured")

        messages = []
        for item in conversation:
            if item.role == "system":
                continue
            role = "assistant" if item.role == "assistant" else "user"
            text = item.content if not item.author_name else f"{item.author_name}: {item.content}"
            messages.append(
                {
                    "role": role,
                    "content": text,
                }
            )

        payload = {
            "model": agent.model or self.default_model,
            "max_tokens": 2048,
            "system": agent.instructions or "You are a helpful assistant.",
            "messages": messages,
        }
        response = await self.http_client.post(
            "/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        body = response.json()
        output_text = self._extract_text(body)
        return ProviderResponse(
            output_text=output_text,
            provider_session_id=provider_session_id,
            raw_payload=body,
        )

    @staticmethod
    def _extract_text(body: dict[str, Any]) -> str:
        content = body.get("content", [])
        chunks = []
        for item in content:
            if item.get("type") == "text":
                chunks.append(item.get("text", ""))
        return "\n".join(chunk for chunk in chunks if chunk).strip()
