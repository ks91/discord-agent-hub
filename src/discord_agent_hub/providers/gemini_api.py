from __future__ import annotations

from typing import Any

import httpx

from discord_agent_hub.conversation_render import render_message_text
from discord_agent_hub.models import AgentDefinition, MessageRecord, ProviderResponse
from discord_agent_hub.providers.base import Provider


class GeminiAPIProvider(Provider):
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
            base_url="https://generativelanguage.googleapis.com",
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
            raise RuntimeError("GEMINI_API_KEY is not configured")

        contents = []
        for item in conversation:
            if item.role == "system":
                continue
            role = "model" if item.role == "assistant" else "user"
            parts = []
            for attachment in item.attachments:
                if attachment.get("type") != "image" or role == "model":
                    continue
                parts.append(
                    {
                        "inline_data": {
                            "mime_type": attachment["media_type"],
                            "data": attachment["data"],
                        }
                    }
                )
            text = render_message_text(item)
            if text.strip() or not parts:
                parts.append({"text": text})
            contents.append(
                {
                    "role": role,
                    "parts": parts,
                }
            )

        payload = {
            "systemInstruction": {
                "parts": [{"text": agent.instructions or "You are a helpful assistant."}]
            },
            "contents": contents,
        }
        tools = []
        if agent.tools.get("web_search"):
            tools.append({"google_search": {}})
        if agent.tools.get("code_execution"):
            tools.append({"code_execution": {}})
        if tools:
            payload["tools"] = tools
        model = agent.model or self.default_model
        response = await self.http_client.post(
            f"/v1beta/models/{model}:generateContent",
            params={"key": self.api_key},
            json=payload,
        )
        response.raise_for_status()
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
        candidates = body.get("candidates", [])
        chunks: list[str] = []
        for candidate in candidates:
            content = candidate.get("content", {})
            for part in content.get("parts", []):
                text = part.get("text")
                if text:
                    chunks.append(text)
        return "\n".join(chunks).strip()

    @staticmethod
    def _extract_usage(body: dict[str, Any]) -> dict[str, Any]:
        usage = body.get("usageMetadata") or {}
        return {
            "input_tokens": usage.get("promptTokenCount"),
            "output_tokens": usage.get("candidatesTokenCount"),
            "total_tokens": usage.get("totalTokenCount"),
        }
