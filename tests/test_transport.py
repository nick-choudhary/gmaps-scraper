"""Tests for transport anti-detection features."""

from __future__ import annotations

import httpx
import pytest

from gmaps.exceptions import AuthError, NetworkError, ParseError
from gmaps.transport import (
    _USER_AGENTS,
    BASE_HEADERS,
    HTTPTransport,
    _jitter,
    _pick_ua,
)


class TestUserAgentPool:
    def test_pool_has_multiple_uas(self):
        assert len(_USER_AGENTS) >= 5

    def test_uas_are_real_browsers(self):
        for ua in _USER_AGENTS:
            assert "Mozilla/5.0" in ua
            assert any(b in ua for b in ["Chrome", "Firefox", "Edg"])

    def test_pick_ua_returns_string(self):
        ua = _pick_ua()
        assert isinstance(ua, str)
        assert len(ua) > 50

    def test_pick_ua_varies(self):
        picks = [_pick_ua() for _ in range(20)]
        assert len(set(picks)) >= 2  # At least some variety


class TestJitter:
    def test_jitter_within_range(self):
        for _ in range(100):
            val = _jitter(1.0, 0.3)
            assert 0.7 <= val <= 1.3

    def test_jitter_zero_pct(self):
        val = _jitter(2.0, 0.0)
        assert val == 2.0

    def test_jitter_full_pct(self):
        for _ in range(100):
            val = _jitter(1.0, 1.0)
            assert 0.0 <= val <= 2.0


class TestBaseHeaders:
    def test_no_brotli(self):
        assert "br" not in BASE_HEADERS.get("Accept-Encoding", "")

    def test_cors_headers(self):
        assert BASE_HEADERS["Sec-Fetch-Dest"] == "empty"
        assert BASE_HEADERS["Sec-Fetch-Mode"] == "cors"

    def test_no_user_agent_in_base(self):
        assert "User-Agent" not in BASE_HEADERS


class TestTransportInit:
    def test_defaults(self):
        t = HTTPTransport()
        assert t.min_delay > 0
        assert t.jitter_pct > 0
        assert t.max_retries >= 1

    def test_custom_values(self):
        t = HTTPTransport(min_delay=3.0, jitter_pct=0.5, max_retries=5)
        assert t.min_delay == 3.0
        assert t.jitter_pct == 0.5
        assert t.max_retries == 5

    def test_stats_keys(self):
        t = HTTPTransport()
        stats = t.get_stats()
        assert "request_count" in stats
        assert "min_delay" in stats
        assert "jitter_pct" in stats
        assert "session_age_seconds" in stats
        assert "ua_pool_size" in stats

    def test_session_not_stale_initially(self):
        t = HTTPTransport()
        assert t.is_session_stale is False

    def test_shuffle_grid_order(self):
        cells = list(range(100))
        shuffled = HTTPTransport.shuffle_grid_order(cells)
        assert set(shuffled) == set(cells)
        assert shuffled != cells  # Very unlikely to be same order


class TestErrorClassification:
    """Google's own answers (a sign-in wall, an unreadable body) are raised as such, at once."""

    def _transport(self, handler):
        transport = HTTPTransport(
            base_url="https://www.google.com", max_retries=3, retry_delay=0.0, min_delay=0.0
        )
        # A client that only ever talks to the mock: no request leaves the test.
        transport._client = httpx.AsyncClient(
            base_url="https://www.google.com", transport=httpx.MockTransport(handler)
        )
        return transport

    async def test_sign_in_wall_is_auth_error_without_retries(self):
        page = (
            "<html><body>Sign in to continue "
            "<a href='https://accounts.google.com/ServiceLogin'>Log in</a></body></html>"
        )
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(200, text=page)

        transport = self._transport(handler)
        try:
            with pytest.raises(AuthError):
                await transport.get("/maps/x", response_type="json")
        finally:
            await transport.close()
        assert len(seen) == 1

    async def test_unreadable_body_is_parse_error_without_retries(self):
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(200, text=")]}'\n[not json")

        transport = self._transport(handler)
        try:
            with pytest.raises(ParseError):
                await transport.get("/maps/x", response_type="json")
        finally:
            await transport.close()
        assert len(seen) == 1

    async def test_server_errors_are_still_retried(self):
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(503, text="busy")

        transport = self._transport(handler)
        try:
            with pytest.raises(NetworkError):
                await transport.get("/maps/x", response_type="json")
        finally:
            await transport.close()
        assert len(seen) == 3
