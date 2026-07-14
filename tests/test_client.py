"""Tests for GMapsClient initialization and mode configuration."""

from __future__ import annotations

import pytest

from gmaps.client import GMapsClient


class TestGMapsClientInit:
    def test_mode_1_default(self):
        c = GMapsClient()
        assert c.enrich_enabled is False
        assert c.login_cookies is None

    def test_mode_2_enrich(self):
        c = GMapsClient(enrich=True)
        assert c.enrich_enabled is True
        assert c.login_cookies is None

    def test_mode_3_login_cookies(self):
        c = GMapsClient(enrich=True, login_cookies="SID=abc; HSID=xyz")
        assert c.enrich_enabled is True
        assert c.login_cookies == "SID=abc; HSID=xyz"

    def test_not_open_raises(self):
        c = GMapsClient()
        with pytest.raises(RuntimeError, match="not opened"):
            _ = c.search

    def test_enrich_without_flag_raises(self):
        """enrich() should raise if enrich_enabled is False."""
        c = GMapsClient(enrich=False)
        from gmaps.rpc.parser import ParsedPlace

        with pytest.raises(RuntimeError, match="Enrichment not enabled"):
            # Can't actually call enrich without opening, but the check
            # happens before any I/O
            import asyncio

            asyncio.run(c.enrich(ParsedPlace()))
