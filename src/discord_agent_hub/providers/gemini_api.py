from __future__ import annotations

import base64
import mimetypes
from typing import Any

import httpx

from discord_agent_hub.conversation_render import render_message_text
from discord_agent_hub.models import AgentDefinition, GeneratedFile, MessageRecord, ProviderResponse
from discord_agent_hub.provider_instructions import render_provider_instructions
from discord_agent_hub.providers.base import Provider

MAX_GENERATED_FILES = 5
MAX_GENERATED_FILE_BYTES = 8 * 1024 * 1024


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
            if not any(part.get("text", "").strip() or "inline_data" in part for part in parts):
                continue
            contents.append(
                {
                    "role": role,
                    "parts": parts,
                }
            )

        payload = {
            "systemInstruction": {
                "parts": [{"text": render_provider_instructions(agent)}]
            },
            "contents": contents,
        }
        tools = []
        file_search_store_names = agent.metadata.get("gemini_file_search_store_names")
        if isinstance(file_search_store_names, list) and file_search_store_names:
            tools.append(
                {
                    "file_search": {
                        "file_search_store_names": [
                            str(item) for item in file_search_store_names if str(item)
                        ]
                    }
                }
            )
        if agent.tools.get("web_search") and not file_search_store_names:
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
            generated_files=self._extract_generated_files(body),
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
            "cached_input_tokens": usage.get("cachedContentTokenCount"),
        }

    @staticmethod
    def _extract_generated_files(body: dict[str, Any]) -> list[GeneratedFile]:
        generated_files: list[GeneratedFile] = []
        for candidate in body.get("candidates", []):
            content = candidate.get("content", {})
            for part in content.get("parts", []):
                inline_data = part.get("inline_data") or part.get("inlineData")
                if not isinstance(inline_data, dict):
                    continue
                media_type = (
                    inline_data.get("mime_type")
                    or inline_data.get("mimeType")
                    or "application/octet-stream"
                )
                encoded = inline_data.get("data")
                if not isinstance(encoded, str) or not encoded:
                    continue
                try:
                    data = base64.b64decode(encoded)
                except Exception:
                    continue
                if not data or len(data) > MAX_GENERATED_FILE_BYTES:
                    continue
                index = len(generated_files) + 1
                generated_files.append(
                    GeneratedFile(
                        filename=f"gemini-generated-{index}{_extension_for_media_type(media_type)}",
                        media_type=media_type,
                        data=data,
                        source_provider="gemini_api",
                    )
                )
                if len(generated_files) >= MAX_GENERATED_FILES:
                    return generated_files
        return generated_files


def _extension_for_media_type(media_type: str) -> str:
    if media_type == "image/png":
        return ".png"
    if media_type in {"image/jpeg", "image/jpg"}:
        return ".jpg"
    guessed = mimetypes.guess_extension(media_type)
    return guessed or ".bin"
