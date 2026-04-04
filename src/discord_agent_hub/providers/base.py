from __future__ import annotations

from abc import ABC, abstractmethod

from discord_agent_hub.models import AgentDefinition, MessageRecord, ProviderResponse


class Provider(ABC):
    @abstractmethod
    async def generate(
        self,
        *,
        agent: AgentDefinition,
        conversation: list[MessageRecord],
        provider_session_id: str | None,
    ) -> ProviderResponse:
        raise NotImplementedError


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, Provider] = {}

    def register(self, name: str, provider: Provider) -> None:
        self._providers[name] = provider

    def get(self, name: str) -> Provider:
        try:
            return self._providers[name]
        except KeyError as exc:
            raise KeyError(f"Unknown provider: {name}") from exc
