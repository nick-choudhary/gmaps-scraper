"""Tests for the pluggable content-fetcher chain (no network)."""

import os
from contextlib import contextmanager

from gmaps.fetchers import (
    ENV_FIRECRAWL_KEY,
    ENV_TINYFISH_KEY,
    ContentFetcher,
    FetcherChain,
    FetchResult,
    FirecrawlFetcher,
    HTTPFetcher,
    TinyFishFetcher,
    build_default_chain,
    extract_api_error,
    parse_firecrawl,
    parse_tinyfish,
)

_MANAGED_ENV = (
    ENV_TINYFISH_KEY,
    ENV_FIRECRAWL_KEY,
    "GMAPS_PROXY",
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
)


@contextmanager
def env(**overrides):
    """Set/clear env vars for the duration of the block (value None = unset).

    Clears ALL managed vars first so ambient environment can't leak in.
    """
    saved = {k: os.environ.get(k) for k in _MANAGED_ENV}
    for k in _MANAGED_ENV:
        os.environ.pop(k, None)
    for k, v in overrides.items():
        if v is not None:
            os.environ[k] = v
    try:
        yield
    finally:
        for k in _MANAGED_ENV:
            os.environ.pop(k, None)
            if saved[k] is not None:
                os.environ[k] = saved[k]


class _Dummy(ContentFetcher):
    """Test fetcher that returns a canned result or a failure."""

    def __init__(self, name, ok=True, text="<html>ok</html>"):
        self.name = name
        self._ok = ok
        self._text = text
        self.calls = 0

    def is_available(self):
        return True

    async def fetch(self, url, timeout):
        self.calls += 1
        if self._ok:
            return FetchResult(url=url, text=self._text, provider=self.name)
        return self._fail(url, "boom")


class TestParseTinyFish:
    def test_success(self):
        payload = {
            "results": [{"url": "u", "final_url": "f", "title": "T", "text": "<html>hi</html>"}],
            "errors": [],
        }
        text, final_url, title, error = parse_tinyfish(payload)
        assert text == "<html>hi</html>" and final_url == "f" and title == "T" and error == ""

    def test_per_url_error(self):
        payload = {"results": [], "errors": [{"url": "u", "error": "blocked"}]}
        text, _, _, error = parse_tinyfish(payload)
        assert text == "" and error == "blocked"

    def test_empty(self):
        text, _, _, error = parse_tinyfish({"results": [], "errors": []})
        assert text == "" and "empty" in error.lower()


class TestParseFirecrawl:
    def test_success_html(self):
        payload = {
            "success": True,
            "data": {"html": "<html>x</html>", "metadata": {"sourceURL": "s", "title": "T"}},
        }
        text, final_url, title, error = parse_firecrawl(payload)
        assert text == "<html>x</html>" and final_url == "s" and title == "T" and error == ""

    def test_markdown_fallback(self):
        payload = {"success": True, "data": {"markdown": "# hi", "metadata": {}}}
        text, _, _, _ = parse_firecrawl(payload)
        assert text == "# hi"

    def test_failure_flag(self):
        text, _, _, error = parse_firecrawl({"success": False, "error": "quota"})
        assert text == "" and error == "quota"

    def test_empty_data(self):
        text, _, _, error = parse_firecrawl({"success": True, "data": {}})
        assert text == "" and "empty" in error.lower()


class TestAvailability:
    def test_tinyfish_needs_key(self):
        assert TinyFishFetcher(api_key="k").is_available() is True
        assert TinyFishFetcher(api_key="").is_available() is False

    def test_firecrawl_needs_key(self):
        assert FirecrawlFetcher(api_key="k").is_available() is True
        assert FirecrawlFetcher(api_key="").is_available() is False

    def test_basic_always_available(self):
        assert HTTPFetcher(proxy=None).is_available() is True

    def test_proxy_variant_needs_proxy(self):
        assert HTTPFetcher(proxy="http://p:8080").is_available() is True

    def test_env_key_detected(self):
        with env(**{ENV_TINYFISH_KEY: "abc"}):
            assert TinyFishFetcher().is_available() is True
        with env():
            assert TinyFishFetcher().is_available() is False


class TestBuildDefaultChain:
    def test_none_configured_is_basic_only(self):
        with env():
            chain = build_default_chain()
        assert chain.active_names == ["basic"]

    def test_all_configured_order(self):
        with env(**{ENV_TINYFISH_KEY: "t", ENV_FIRECRAWL_KEY: "f", "GMAPS_PROXY": "http://p:1"}):
            chain = build_default_chain()
        assert chain.active_names == ["tinyfish", "firecrawl", "proxy", "basic"]

    def test_only_firecrawl(self):
        with env(**{ENV_FIRECRAWL_KEY: "f"}):
            chain = build_default_chain()
        assert chain.active_names == ["firecrawl", "basic"]

    def test_explicit_proxy_arg(self):
        with env():
            chain = build_default_chain(proxy="http://p:2")
        assert chain.active_names == ["proxy", "basic"]

    def test_standard_proxy_env_detected(self):
        with env(**{"HTTPS_PROXY": "http://p:3"}):
            chain = build_default_chain()
        assert chain.active_names == ["proxy", "basic"]


class TestFetcherChainFallback:
    async def test_first_success_wins(self):
        a = _Dummy("a", ok=True)
        b = _Dummy("b", ok=True)
        chain = FetcherChain([a, b])
        result = await chain.fetch("http://x", timeout=5)
        assert result.provider == "a" and result.ok
        assert b.calls == 0  # short-circuits

    async def test_falls_through_on_failure(self):
        a = _Dummy("a", ok=False)
        b = _Dummy("b", ok=True)
        chain = FetcherChain([a, b])
        result = await chain.fetch("http://x", timeout=5)
        assert result.provider == "b" and result.ok
        assert a.calls == 1 and b.calls == 1

    async def test_all_fail_returns_last(self):
        a = _Dummy("a", ok=False)
        b = _Dummy("b", ok=False)
        chain = FetcherChain([a, b])
        result = await chain.fetch("http://x", timeout=5)
        assert not result.ok and result.status == "failed" and result.provider == "b"

    async def test_empty_chain(self):
        chain = FetcherChain([])
        result = await chain.fetch("http://x", timeout=5)
        assert not result.ok


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class TestExtractApiError:
    def test_tinyfish_envelope(self):
        msg = extract_api_error(
            _Resp(401, {"error": {"code": "INVALID_API_KEY", "message": "bad key"}})
        )
        assert "INVALID_API_KEY" in msg and "bad key" in msg

    def test_firecrawl_string(self):
        assert extract_api_error(_Resp(402, {"error": "quota exceeded"})) == "quota exceeded"

    def test_fallback_non_json(self):
        assert extract_api_error(_Resp(500, ValueError("no json"))) == "HTTP 500"

    def test_fallback_no_error_field(self):
        assert extract_api_error(_Resp(503, {})) == "HTTP 503"


class TestFetchResult:
    def test_ok_requires_text(self):
        assert FetchResult(url="u", text="x").ok is True
        assert FetchResult(url="u", text="").ok is False
        assert FetchResult(url="u", text="x", status="failed").ok is False
