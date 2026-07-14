"""Tests for the Phase 5 provider registry (config-selected boundaries)."""

from gmaps.registry import (
    CAPABILITIES,
    ProviderRegistry,
    build_registry,
    resolve_providers,
)


class TestProviderRegistry:
    def test_register_and_get(self):
        r = ProviderRegistry()
        obj = object()
        r.register("contact_extractor", "x", obj)
        assert r.get("contact_extractor", "x") is obj

    def test_names_and_has(self):
        r = ProviderRegistry()
        r.register("contact_extractor", "a", 1).register("contact_extractor", "b", 2)
        assert r.names("contact_extractor") == ["a", "b"]
        assert r.has("contact_extractor", "a") and not r.has("contact_extractor", "z")

    def test_unknown_capability_raises(self):
        r = ProviderRegistry()
        raised = False
        try:
            r.register("not_a_capability", "x", 1)
        except KeyError:
            raised = True
        assert raised

    def test_unknown_provider_raises_helpfully(self):
        r = ProviderRegistry()
        r.register("contact_extractor", "regex", 1)
        try:
            r.get("contact_extractor", "model")
        except KeyError as e:
            assert "model" in str(e) and "regex" in str(e)  # lists what's available

    def test_capabilities_constant(self):
        assert "parse_repair" in CAPABILITIES
        assert "contact_extractor" in CAPABILITIES
        assert "content_fetcher" in CAPABILITIES


class TestBuildAndResolve:
    def test_default_registry_has_regex(self):
        reg = build_registry()
        assert reg.has("contact_extractor", "regex")

    def test_resolve_maps_config_to_providers(self):
        reg = build_registry()
        regex = reg.get("contact_extractor", "regex")
        resolved = resolve_providers({"contact_extractor": "regex"}, reg)
        assert resolved["contact_extractor"] is regex

    def test_config_bump_swaps_implementation(self):
        # The model-swap-readiness contract: flip the config value, get a
        # different provider — no other code change.
        reg = build_registry()
        model = object()
        reg.register("contact_extractor", "model", model)
        assert (
            resolve_providers({"contact_extractor": "regex"}, reg)["contact_extractor"]
            is not resolve_providers({"contact_extractor": "model"}, reg)["contact_extractor"]
        )
        assert resolve_providers({"contact_extractor": "model"}, reg)["contact_extractor"] is model
