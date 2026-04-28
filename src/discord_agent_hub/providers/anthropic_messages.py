from __future__ import annotations

from typing import Any

import httpx

from discord_agent_hub.conversation_render import render_message_text
from discord_agent_hub.models import AgentDefinition, MessageRecord, ProviderResponse
from discord_agent_hub.provider_instructions import render_provider_instructions
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
            content = []
            for attachment in item.attachments:
                if attachment.get("type") != "image" or role == "assistant":
                    continue
                content.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": attachment["media_type"],
                            "data": attachment["data"],
                        },
                    }
                )
            text = render_message_text(item)
            if text.strip() or not content:
                content.append({"type": "text", "text": text})
            messages.append(
                {
                    "role": role,
                    "content": content,
                }
            )

        payload = {
            "model": agent.model or self.default_model,
            "max_tokens": 4096,
            "cache_control": {"type": "ephemeral"},
            "system": render_provider_instructions(agent),
            "messages": messages,
        }
        tools = []
        beta_headers = []
        if agent.tools.get("web_search"):
            tools.append(
                {
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": 5,
                }
            )
        if agent.tools.get("code_execution"):
            tools.append(
                {
                    "type": "code_execution_20250825",
                    "name": "code_execution",
                }
            )
            beta_headers.append("code-execution-2025-08-25")
        if tools:
            payload["tools"] = tools

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        if beta_headers:
            headers["anthropic-beta"] = ",".join(beta_headers)

        response = await self.http_client.post(
            "/v1/messages",
            headers=headers,
            json=payload,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = response.text.strip()
            if detail:
                raise RuntimeError(
                    f"Anthropic API error {response.status_code}: {detail}"
                ) from exc
            raise
        body = response.json()
        output_text = self._extract_text(body)
        return ProviderResponse(
            output_text=output_text,
            provider_session_id=provider_session_id,
            raw_payload=body,
            usage=self._extract_usage(body),
        )

    @staticmethod
    def _extract_text(body: dict[str, Any]) -> str:
        content = body.get("content", [])
        chunks = []
        for item in content:
            if item.get("type") == "text":
                chunks.append(item.get("text", ""))
        return "\n".join(chunk for chunk in chunks if chunk).strip()

    @staticmethod
    def _extract_usage(body: dict[str, Any]) -> dict[str, Any]:
        usage = body.get("usage") or {}
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        total_tokens = None
        if isinstance(input_tokens, int) and isinstance(output_tokens, int):
            total_tokens = input_tokens + output_tokens
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
            "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
        }
