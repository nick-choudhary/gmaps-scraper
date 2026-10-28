"""Tests for GMapsClient initialization and mode configuration."""

from __future__ import annotations

import pytest

from gmaps.client import GMapsClient
from gmaps.rpc.parser import ParsedPlace


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


class TestEnrich:
    async def test_enrich_merges_dated_hours_and_status(self):
        place_data: list = [None] * 260
        place_data[11] = "Test Shop"
        place_data[203] = [
            [["Wednesday", 3, [2026, 7, 1], [["9 AM–5 PM", [[9], [17]]]], None, 1]],
            [None, None, None, None, ["Open · Closes 5 PM"]],
        ]

        class FakeSearch:
            async def place_details(self, **kwargs):
                return [None] * 6 + [place_data]

        c = GMapsClient(enrich=True)
        c._search = FakeSearch()  # type: ignore[assignment]
        place = await c.enrich(ParsedPlace(name="Test Shop"))

        assert place.opening_hours == [
            {
                "date": "2026-07-01",
                "weekday": "Wednesday",
                "text": ["9 AM–5 PM"],
                "periods": [{"open": "09:00", "close": "17:00"}],
            }
        ]
        assert place.status == "Open · Closes 5 PM"
