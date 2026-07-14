"""Provider registry (Phase 5) — capability boundaries selected by config.

Phases 1–4 each introduced a swappable boundary behind an interface:

* ``parse_repair``      — `SchemaRepair` (deterministic value-search, or a model)
* ``contact_extractor`` — `ContactExtractor` (regex default, or a model)
* ``content_fetcher``   — `ContentFetcher` chain (TinyFish / Firecrawl / HTTP)

This module puts those behind a single registry so *which* implementation runs
for each capability is a **config value**, not code. Registering a stronger
model under a name and flipping the config from ``"regex"`` to ``"model"`` is the
whole change — that is the "model upgrade = config bump" goal.

Deterministic, dependency-free. Registering the model-backed providers is left
to the caller (they carry your model callable); only the no-arg defaults are
pre-registered.
"""

from __future__ import annotations

from typing import Any

CAPABILITIES: tuple[str, ...] = ("parse_repair", "contact_extractor", "content_fetcher")


class ProviderRegistry:
    """Maps (capability, name) → a provider object, so config can select one."""

    def __init__(self) -> None:
        self._providers: dict[str, dict[str, Any]] = {c: {} for c in CAPABILITIES}

    def register(self, capability: str, name: str, provider: Any) -> ProviderRegistry:
        if capability not in self._providers:
            raise KeyError(f"unknown capability {capability!r}; valid: {CAPABILITIES}")
        self._providers[capability][name] = provider
        return self

    def get(self, capability: str, name: str) -> Any:
        if capability not in self._providers:
            raise KeyError(f"unknown capability {capability!r}; valid: {CAPABILITIES}")
        bucket = self._providers[capability]
        if name not in bucket:
            raise KeyError(f"no provider {name!r} for {capability!r}; registered: {sorted(bucket)}")
        return bucket[name]

    def names(self, capability: str) -> list[str]:
        return sorted(self._providers.get(capability, {}))

    def has(self, capability: str, name: str) -> bool:
        return name in self._providers.get(capability, {})


def build_registry() -> ProviderRegistry:
    """A registry pre-loaded with the no-arg default providers.

    Model-backed providers (which need your callable) are registered by you:

        reg = build_registry()
        reg.register("contact_extractor", "model", ModelContactExtractor(fn=my_llm))
        providers = resolve_providers({"contact_extractor": "model"}, reg)
    """
    reg = ProviderRegistry()
    from .contacts import RegexContactExtractor

    reg.register("contact_extractor", "regex", RegexContactExtractor())
    return reg


def resolve_providers(
    config: dict[str, str], registry: ProviderRegistry | None = None
) -> dict[str, Any]:
    """Resolve a ``{capability: provider_name}`` config into provider objects.

    Swapping a value (e.g. ``"regex"`` → ``"model"``) swaps the implementation
    with no other code change — the model-swap-readiness contract.
    """
    registry = registry or build_registry()
    return {capability: registry.get(capability, name) for capability, name in config.items()}
