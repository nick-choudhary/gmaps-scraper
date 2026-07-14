"""Tests for Phase 4 capture-and-replay identity (deterministic parts)."""

import sys
import tempfile
from pathlib import Path
from unittest import mock

from gmaps.identity import (
    CapturedIdentity,
    ManualCapture,
    PlaywrightCapture,
    age_hours,
    apply_identity,
    is_fresh,
    load_identity,
    parameterize_pb,
    render_pb,
    save_identity,
)

REAL_PB = (
    "!1sdonut%20shop!4m8!1m3!1d10000!2d-97.74!3d30.27"
    "!3m2!1i1024!2i768!4f16!7i20!8i0!12m50!85b1!99b1"
)


class TestPbTemplate:
    def test_parameterize_inserts_placeholders(self):
        t = parameterize_pb(
            REAL_PB,
            query="donut%20shop",
            lat="30.27",
            lng="-97.74",
            zoom="16",
            count="20",
            offset="0",
        )
        for ph in ("{query}", "{lat}", "{lng}", "{zoom}", "{count}", "{offset}"):
            assert ph in t
        # every other captured flag is preserved verbatim
        assert "!85b1!99b1" in t and "!12m50" in t

    def test_render_produces_new_search(self):
        t = parameterize_pb(
            REAL_PB,
            query="donut%20shop",
            lat="30.27",
            lng="-97.74",
            zoom="16",
            count="20",
            offset="0",
        )
        r = render_pb(
            t, query="taco%20truck", lat="40.7", lng="-73.9", zoom="17", count="20", offset="20"
        )
        assert "!1staco%20truck" in r
        assert "!3d40.7" in r and "!2d-73.9" in r and "!4f17" in r and "!8i20" in r
        assert "!85b1!99b1" in r  # real flags carried through
        assert "donut" not in r  # old query gone

    def test_roundtrip_identity_when_same_values(self):
        t = parameterize_pb(
            REAL_PB,
            query="donut%20shop",
            lat="30.27",
            lng="-97.74",
            zoom="16",
            count="20",
            offset="0",
        )
        r = render_pb(
            t, query="donut%20shop", lat="30.27", lng="-97.74", zoom="16", count="20", offset="0"
        )
        assert r == REAL_PB

    def test_missing_marker_is_noop(self):
        t = parameterize_pb("!1sx!4f16", query="notpresent")
        assert "{query}" not in t  # 'notpresent' isn't in the string


class TestCapturedIdentitySerialization:
    def test_roundtrip(self):
        ident = CapturedIdentity(
            cookies={"NID": "abc", "SOCS": "real"},
            user_agent="UA/1.0",
            pb_templates={"search": "!1s{query}"},
            note="test",
        )
        back = CapturedIdentity.from_dict(ident.to_dict())
        assert back.cookies == ident.cookies
        assert back.user_agent == "UA/1.0"
        assert back.pb_templates == {"search": "!1s{query}"}

    def test_save_load(self):
        p = Path(tempfile.mkdtemp()) / "identity.json"
        ident = CapturedIdentity(cookies={"NID": "abc"}, user_agent="UA/1.0")
        save_identity(ident, p)
        loaded = load_identity(p)
        assert loaded is not None and loaded.cookies == {"NID": "abc"}

    def test_load_missing_is_none(self):
        assert load_identity("/nonexistent/identity.json") is None


class TestFreshness:
    def test_age_and_fresh(self):
        ident = CapturedIdentity(captured_at=1000.0)
        assert age_hours(ident, now=1000.0 + 3600) == 1.0
        assert is_fresh(ident, max_age_hours=6.0, now=1000.0 + 3600)
        assert not is_fresh(ident, max_age_hours=6.0, now=1000.0 + 7 * 3600)


class _FakeCookies:
    def __init__(self):
        self.jar = {}

    def set(self, name, value, domain=None):
        self.jar[(name, domain)] = value


class _FakeClient:
    def __init__(self):
        self.cookies = _FakeCookies()
        self.headers = {}


class TestApplyIdentity:
    def test_injects_cookies_and_ua(self):
        client = _FakeClient()
        ident = CapturedIdentity(cookies={"NID": "n", "SOCS": "s"}, user_agent="RealUA/9")
        apply_identity(client, ident)
        assert client.headers["User-Agent"] == "RealUA/9"
        assert client.cookies.jar[("NID", ".google.com")] == "n"
        assert client.cookies.jar[("SOCS", ".google.com")] == "s"


class TestManualCapture:
    async def test_returns_identity(self):
        cap = ManualCapture(
            cookies={"NID": "x"}, user_agent="UA", pb_templates={"search": "!1s{query}"}
        )
        ident = await cap.capture()
        assert ident.cookies == {"NID": "x"} and ident.user_agent == "UA"
        assert ident.pb_templates == {"search": "!1s{query}"}


class TestPlaywrightCaptureUnavailable:
    async def test_raises_clear_error_without_playwright(self):
        # Simulate the browser extra not being installed so `import
        # playwright.async_api` fails, regardless of the local environment.
        raised = ""
        with mock.patch.dict(sys.modules, {"playwright.async_api": None}):
            try:
                await PlaywrightCapture().capture()
            except RuntimeError as e:
                raised = str(e)
        assert "playwright" in raised.lower()
