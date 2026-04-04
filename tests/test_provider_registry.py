import pytest

from discord_agent_hub.providers.base import ProviderRegistry
from discord_agent_hub.providers.cli_stub import CLIStubProvider


def test_provider_registry_returns_registered_provider():
    registry = ProviderRegistry()
    provider = CLIStubProvider(name="stub", command="stub")

    registry.register("stub", provider)

    assert registry.get("stub") is provider


def test_provider_registry_raises_for_unknown_provider():
    registry = ProviderRegistry()

    with pytest.raises(KeyError):
        registry.get("missing")
