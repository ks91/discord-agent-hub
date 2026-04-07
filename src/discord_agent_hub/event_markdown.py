from __future__ import annotations

from typing import Any


def render_event_markdown(events: list[dict[str, Any]]) -> str:
    session_id = next((event.get("session_id") for event in events if event.get("session_id")), "unknown")
    lines = [
        f"# Session Events",
        "",
        f"- session_id: `{session_id}`",
        f"- events: `{len(events)}`",
        "",
        "## Timeline",
        "",
    ]
    for event in events:
        lines.extend(_render_event(event))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_event(event: dict[str, Any]) -> list[str]:
    title = event.get("event", "unknown")
    ts = event.get("ts", "unknown")
    lines = [f"### {title}", f"_ts: {ts}_"]
    body = _event_body_lines(event)
    if body:
        lines.append("")
        lines.extend(body)
    return lines


def _event_body_lines(event: dict[str, Any]) -> list[str]:
    event_type = event.get("event")
    if event_type == "session.created":
        return _kv_lines(
            {
                "agent_id": event.get("agent_id"),
                "provider": event.get("provider"),
                "model": event.get("model"),
                "discord_thread_id": event.get("discord_thread_id"),
                "created_by_user_id": event.get("created_by_user_id"),
            }
        )
    if event_type == "message.user":
        return _message_lines("User", event)
    if event_type == "response.assistant":
        lines = _message_lines("Assistant", event)
        model_lines = _kv_lines({"model": event.get("model")})
        if model_lines:
            lines = model_lines + lines
        usage = event.get("usage") or {}
        usage_lines = _kv_lines(
            {
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "total_tokens": usage.get("total_tokens"),
            }
        )
        if usage_lines:
            lines.append("")
            lines.append("Usage:")
            lines.extend(usage_lines)
        return lines
    if event_type == "provider.retry":
        return _kv_lines(
            {
                "provider": event.get("provider"),
                "attempt": event.get("attempt"),
                "delay_seconds": event.get("delay_seconds"),
                "error": event.get("error"),
            }
        )
    if event_type == "provider.error":
        return _kv_lines(
            {
                "provider": event.get("provider"),
                "error": event.get("error"),
            }
        )
    if event_type in {"queue.wait_started", "queue.wait_finished"}:
        return _kv_lines(
            {
                "discord_thread_id": event.get("discord_thread_id"),
                "queue_depth": event.get("queue_depth"),
                "wait_seconds": event.get("wait_seconds"),
            }
        )
    if event_type in {"agent.imported", "agent.deleted", "auth.denied_role"}:
        return _kv_lines({key: value for key, value in event.items() if key not in {"event", "ts"}})
    return _kv_lines({key: value for key, value in event.items() if key not in {"event", "ts"}})


def _message_lines(label: str, event: dict[str, Any]) -> list[str]:
    lines = _kv_lines(
        {
            "author_name": event.get("author_name"),
            "user_id": event.get("user_id"),
            "created_by_user_id": event.get("created_by_user_id"),
        }
    )
    content = event.get("content")
    if content:
        if lines:
            lines.append("")
        lines.append(f"{label} content:")
        lines.append("```text")
        lines.append(str(content))
        lines.append("```")
    return lines


def _kv_lines(payload: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for key, value in payload.items():
        if value is None or value == "":
            continue
        lines.append(f"- {key}: `{value}`")
    return lines
