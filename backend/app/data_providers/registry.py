"""Provider registry."""
from __future__ import annotations

from app.data_providers.tickflow_provider import TickFlowProvider
from app.data_providers.free_public_provider import FreePublicProvider
from app.data_providers.opentdx_provider import OpenTDXProvider

_PROVIDERS = {
    "tickflow": TickFlowProvider,
    "free_public": FreePublicProvider,
    "opentdx": OpenTDXProvider,
}


def get_provider(name: str = "tickflow"):
    provider_cls = _PROVIDERS.get((name or "tickflow").lower())
    if provider_cls is None:
        raise ValueError(f"Unsupported data provider: {name}")
    return provider_cls()
