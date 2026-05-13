from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any

import httpx

from discord_agent_hub.conversation_render import render_message_text
from discord_agent_hub.models import AgentDefinition, GeneratedFile, MessageRecord, ProviderResponse
from discord_agent_hub.provider_instructions import render_provider_instructions
from discord_agent_hub.providers.base import Provider

MAX_GENERATED_FILES = 5
MAX_GENERATED_FILE_BYTES = 8 * 1024 * 1024
CODE_EXECUTION_BETA = "code-execution-2025-08-25"
FILES_API_BETA = "files-api-2025-04-14"


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

        code_execution_enabled = bool(agent.tools.get("code_execution"))
        beta_headers = []
        if code_execution_enabled:
            beta_headers.extend([CODE_EXECUTION_BETA, FILES_API_BETA])
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        if beta_headers:
            headers["anthropic-beta"] = ",".join(beta_headers)

        messages = []
        for item in conversation:
            if item.role == "system":
                continue
            role = "assistant" if item.role == "assistant" else "user"
            content = []
            for attachment in item.attachments:
                if attachment.get("type") not in {"image", "runtime_file"} or role == "assistant":
                    continue
                if attachment.get("type") == "image":
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
                if code_execution_enabled and role == "user":
                    file_id = await self._upload_container_file(
                        attachment=attachment,
                        headers=headers,
                    )
                    if file_id:
                        content.append({"type": "container_upload", "file_id": file_id})
            text = render_message_text(item)
            if text.strip() or not content:
                content.append({"type": "text", "text": text})
            if not any(
                part.get("text", "").strip()
                or part.get("type") in {"image", "container_upload"}
                for part in content
            ):
                continue
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
        if tools:
            payload["tools"] = tools

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
            generated_files=await self._collect_generated_files(body, headers=headers),
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

    async def _collect_generated_files(self, body: dict[str, Any], *, headers: dict[str, str]) -> list[GeneratedFile]:
        file_ids = _extract_file_ids(body)
        generated_files: list[GeneratedFile] = []
        for file_id in file_ids[:MAX_GENERATED_FILES]:
            metadata_response = await self.http_client.get(f"/v1/files/{file_id}", headers=headers)
            try:
                metadata_response.raise_for_status()
            except httpx.HTTPStatusError:
                continue
            metadata = metadata_response.json()
            size = metadata.get("size_bytes") or metadata.get("bytes")
            if isinstance(size, int) and size > MAX_GENERATED_FILE_BYTES:
                continue
            content_response = await self.http_client.get(f"/v1/files/{file_id}/content", headers=headers)
            try:
                content_response.raise_for_status()
            except httpx.HTTPStatusError:
                continue
            data = content_response.content
            if not data or len(data) > MAX_GENERATED_FILE_BYTES:
                continue
            filename = _safe_filename(metadata.get("filename") or file_id)
            media_type = metadata.get("mime_type") or mimetypes.guess_type(filename)[0] or "application/octet-stream"
            generated_files.append(
                GeneratedFile(
                    filename=filename,
                    media_type=media_type,
                    data=data,
                    source_provider="anthropic_messages",
                )
            )
        return generated_files

    async def _upload_container_file(self, *, attachment: dict[str, Any], headers: dict[str, str]) -> str | None:
        data = attachment.get("data")
        if not isinstance(data, str) or not data:
            return None
        try:
            raw = base64.b64decode(data)
        except Exception:
            return None
        filename = _safe_filename(str(attachment.get("filename") or "attachment"))
        media_type = str(attachment.get("media_type") or mimetypes.guess_type(filename)[0] or "application/octet-stream")
        upload_headers = dict(headers)
        upload_headers.pop("content-type", None)
        response = await self.http_client.post(
            "/v1/files",
            headers=upload_headers,
            files={"file": (filename, raw, media_type)},
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError:
            return None
        payload = response.json()
        file_id = payload.get("id")
        return file_id if isinstance(file_id, str) and file_id else None


def _extract_file_ids(value: Any) -> list[str]:
    found: list[str] = []

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            file_id = item.get("file_id")
            if isinstance(file_id, str) and file_id:
                found.append(file_id)
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    return list(dict.fromkeys(found))


def _safe_filename(value: str) -> str:
    name = Path(value).name or "generated-file"
    safe = "".join(char if char.isalnum() or char in "._-" else "_" for char in name)
    return safe[:120] or "generated-file"
