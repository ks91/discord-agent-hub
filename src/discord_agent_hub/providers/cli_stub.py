from __future__ import annotations

from discord_agent_hub.models import AgentDefinition, MessageRecord, ProviderResponse
from discord_agent_hub.providers.base import Provider


class CLIStubProvider(Provider):
    def __init__(self, name: str, command: str) -> None:
        self.name = name
        self.command = command

    async def generate(
        self,
        *,
        agent: AgentDefinition,
        conversation: list[MessageRecord],
        provider_session_id: str | None,
    ) -> ProviderResponse:
        last_user = next((item for item in reversed(conversation) if item.role == "user"), None)
        prompt = last_user.content if last_user else ""
        return ProviderResponse(
            output_text=(
                f"[{self.name} provider stub]\n"
                f"`{self.command}` を使う provider はまだ最小実装です。\n"
                f"agent={agent.id}\n"
                f"last_user_message={prompt[:500]}"
            ),
            provider_session_id=provider_session_id,
            raw_payload={"provider": self.name, "stub": True},
        )
