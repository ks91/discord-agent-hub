from __future__ import annotations

from discord_agent_hub.models import MessageRecord


MAX_DOCUMENT_TEXT_CHARS = 12000


def render_message_text(item: MessageRecord) -> str:
    segments: list[str] = []
    text = item.content.strip()
    if text and item.author_name:
        text = f"{item.author_name}: {text}"
    if text:
        segments.append(text)

    for attachment in item.attachments:
        if attachment.get("type") != "document":
            continue
        extracted = (attachment.get("text") or "").strip()
        if not extracted:
            continue
        if len(extracted) > MAX_DOCUMENT_TEXT_CHARS:
            extracted = extracted[:MAX_DOCUMENT_TEXT_CHARS].rstrip() + "\n...[truncated]"
        filename = attachment.get("filename", "document")
        segments.append(f"[Attached document: {filename}]\n{extracted}")

    return "\n\n".join(segments).strip()
