from __future__ import annotations

import inspect
import mimetypes
from pathlib import Path

from openai import AsyncOpenAI

from discord_agent_hub.conversation_render import render_message_text
from discord_agent_hub.models import AgentDefinition, GeneratedFile, MessageRecord, ProviderResponse
from discord_agent_hub.provider_instructions import render_provider_instructions
from discord_agent_hub.providers.base import Provider


MAX_GENERATED_FILES = 5
MAX_GENERATED_FILE_BYTES = 8 * 1024 * 1024


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
            if not any(part.get("text", "").strip() or part.get("type") == "input_image" for part in content):
                continue
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
        raw_payload = response.model_dump()
        return ProviderResponse(
            output_text=response.output_text,
            provider_session_id=provider_session_id,
            raw_payload=raw_payload,
            usage=self._extract_usage(raw_payload),
            generated_files=await self._collect_generated_files(raw_payload),
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

    async def _collect_generated_files(self, payload: dict) -> list[GeneratedFile]:
        container_ids = _extract_container_ids(payload)
        if not container_ids:
            return []

        generated_files: list[GeneratedFile] = []
        for container_id in container_ids:
            page = await _maybe_await(
                self.client.containers.files.list(container_id=container_id, limit=100)
            )
            for file_info in getattr(page, "data", []) or []:
                if len(generated_files) >= MAX_GENERATED_FILES:
                    return generated_files
                if getattr(file_info, "source", None) == "user":
                    continue
                size = getattr(file_info, "bytes", None)
                if isinstance(size, int) and size > MAX_GENERATED_FILE_BYTES:
                    continue
                file_id = getattr(file_info, "id", None)
                if not file_id:
                    continue
                content = await _maybe_await(
                    self.client.containers.files.content.retrieve(
                        file_id=file_id,
                        container_id=container_id,
                    )
                )
                data = await _read_content_bytes(content)
                if not data or len(data) > MAX_GENERATED_FILE_BYTES:
                    continue
                filename = _filename_from_path(getattr(file_info, "path", None), str(file_id))
                generated_files.append(
                    GeneratedFile(
                        filename=filename,
                        media_type=mimetypes.guess_type(filename)[0] or "application/octet-stream",
                        data=data,
                        source_provider="openai_responses",
                    )
                )
        return generated_files


def _extract_container_ids(value) -> list[str]:
    found: list[str] = []

    def walk(item) -> None:
        if isinstance(item, dict):
            container_id = item.get("container_id")
            if isinstance(container_id, str) and container_id:
                found.append(container_id)
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    return list(dict.fromkeys(found))


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


async def _read_content_bytes(content) -> bytes:
    reader = getattr(content, "read", None)
    if callable(reader):
        data = reader()
        data = await _maybe_await(data)
    else:
        data = content
    if isinstance(data, bytes):
        return data
    if isinstance(data, str):
        return data.encode("utf-8")
    return bytes(data)


def _filename_from_path(path: str | None, fallback: str) -> str:
    name = Path(path or fallback).name or fallback
    safe = "".join(char if char.isalnum() or char in "._-" else "_" for char in name)
    return safe[:120] or "generated-file"
