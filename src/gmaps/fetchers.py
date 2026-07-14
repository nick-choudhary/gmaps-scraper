"""Pluggable web-content fetchers with an availability-based fallback chain.

Fetching a business website's content is treated as a swappable capability
behind a common interface, so we can use a managed scraping API when one is
configured and gracefully fall back to direct HTTP when it is not. Nothing
here is user-facing — the chain auto-detects what is available from the
environment and picks the best option automatically.

Fallback order (first available wins per URL; each falls through on failure):
    1. TinyFish Fetch   — if TINYFISH_API_KEY is set
    2. Firecrawl Scrape — if FIRECRAWL_API_KEY is set
    3. Proxied HTTP     — if a proxy is configured
    4. Basic HTTP       — always available (this is the pre-existing behaviour)

With no API keys and no proxy configured, the chain is exactly the basic
direct-HTTP fetch that the scraper used before — so default behaviour is
unchanged. All fetchers request HTML so downstream email/social extraction
(which looks for `href=`/URLs in markup) works identically across providers.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Env var names (conventional for each provider)
ENV_TINYFISH_KEY = "TINYFISH_API_KEY"
ENV_FIRECRAWL_KEY = "FIRECRAWL_API_KEY"
# Proxy: our own var first, then standard ones
ENV_PROXY_VARS = ("GMAPS_PROXY", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy")

# Endpoints (verified against official OpenAPI specs, July 2026).
# TinyFish "Fetch API" — server https://api.fetch.tinyfish.ai, path "/", X-API-Key.
# This is deliberately the lightweight Fetch API (URL -> clean content), NOT the
# heavier goal-driven Automation API (agent.tinyfish.ai/v1/automation/run-sse),
# which is an SSE-streaming AI browser agent meant for interactive multi-step
# tasks and is overkill/costlier for plain content retrieval.
TINYFISH_FETCH_URL = "https://api.fetch.tinyfish.ai/"
FIRECRAWL_SCRAPE_URL = "https://api.firecrawl.dev/v2/scrape"

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
}

# API providers can be slower than a direct GET; never cut them off too early.
_API_MIN_TIMEOUT = 20.0


@dataclass
class FetchResult:
    """Result of fetching one URL through one provider."""

    url: str
    text: str = ""  # readable page content (HTML preferred)
    final_url: str = ""  # after redirects, when the provider reports it
    title: str = ""
    provider: str = ""  # which fetcher produced this
    status: str = "ok"  # "ok" | "failed"
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok" and bool(self.text)


# ── Pure response parsers (unit-testable, no network) ──


def parse_tinyfish(payload: dict[str, Any]) -> tuple[str, str, str, str]:
    """Parse a TinyFish Fetch response → (text, final_url, title, error)."""
    results = payload.get("results") or []
    if results:
        r0 = results[0] or {}
        text = r0.get("text") or r0.get("html") or ""
        if text:
            return text, r0.get("final_url", "") or r0.get("url", ""), r0.get("title", ""), ""
    errors = payload.get("errors") or []
    if errors:
        e0 = errors[0] or {}
        return "", "", "", str(e0.get("error") or e0)
    return "", "", "", "empty TinyFish response"


def parse_firecrawl(payload: dict[str, Any]) -> tuple[str, str, str, str]:
    """Parse a Firecrawl scrape response → (text, final_url, title, error)."""
    if payload.get("success") is False:
        return "", "", "", str(payload.get("error") or "firecrawl reported failure")
    data = payload.get("data") or {}
    text = data.get("html") or data.get("rawHtml") or data.get("markdown") or ""
    meta = data.get("metadata") or {}
    if text:
        final_url = meta.get("sourceURL") or meta.get("url") or ""
        return text, final_url, meta.get("title", ""), ""
    return "", "", "", "empty Firecrawl response"


def extract_api_error(resp: Any) -> str:
    """Best-effort human-readable error from a 4xx/5xx provider response.

    Both TinyFish and Firecrawl return a JSON error envelope: TinyFish uses
    ``{"error": {"code", "message"}}`` (codes like INVALID_API_KEY,
    RATE_LIMIT_EXCEEDED, INSUFFICIENT_CREDITS); Firecrawl uses an ``error``
    string. Falls back to the bare status code.
    """
    try:
        body = resp.json()
        err = body.get("error")
        if isinstance(err, dict):
            msg = f"{err.get('code', '')} {err.get('message', '')}".strip()
            return msg or f"HTTP {resp.status_code}"
        if isinstance(err, str) and err:
            return err
    except Exception:  # noqa: BLE001 — non-JSON body, just use the status
        pass
    return f"HTTP {resp.status_code}"


# ── Fetcher interface + implementations ──


class ContentFetcher:
    """Base class for a single content-fetching provider."""

    name: str = "base"

    def is_available(self) -> bool:  # pragma: no cover - overridden
        return False

    async def open(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def fetch(self, url: str, timeout: float) -> FetchResult:  # pragma: no cover
        raise NotImplementedError

    def _fail(self, url: str, error: str) -> FetchResult:
        return FetchResult(url=url, provider=self.name, status="failed", error=error)


class TinyFishFetcher(ContentFetcher):
    """TinyFish Fetch API — URL in, clean content out (X-API-Key auth)."""

    name = "tinyfish"

    def __init__(self, api_key: str | None = None):
        self.api_key: str = api_key or os.getenv(ENV_TINYFISH_KEY, "") or ""
        self._client: httpx.AsyncClient | None = None

    def is_available(self) -> bool:
        return bool(self.api_key)

    async def open(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={"X-API-Key": self.api_key, "Content-Type": "application/json"},
            )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def fetch(self, url: str, timeout: float) -> FetchResult:
        assert self._client is not None
        try:
            resp = await self._client.post(
                TINYFISH_FETCH_URL,
                # format=html returns cleaned semantic HTML (keeps <a href> so
                # downstream contact-page discovery works). urls is a 1-10 array.
                json={"urls": [url], "format": "html"},
                timeout=max(timeout, _API_MIN_TIMEOUT),
            )
            if resp.status_code >= 400:
                return self._fail(url, extract_api_error(resp))
            text, final_url, title, error = parse_tinyfish(resp.json())
        except Exception as e:  # noqa: BLE001 — fall through to next provider
            return self._fail(url, f"{type(e).__name__}: {e}")
        if not text:
            return self._fail(url, error or "no content")
        return FetchResult(url=url, text=text, final_url=final_url, title=title, provider=self.name)


class FirecrawlFetcher(ContentFetcher):
    """Firecrawl scrape API — URL in, markdown/HTML out (Bearer auth)."""

    name = "firecrawl"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv(ENV_FIRECRAWL_KEY, "")
        self._client: httpx.AsyncClient | None = None

    def is_available(self) -> bool:
        return bool(self.api_key)

    async def open(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def fetch(self, url: str, timeout: float) -> FetchResult:
        assert self._client is not None
        eff = max(timeout, _API_MIN_TIMEOUT)
        try:
            resp = await self._client.post(
                FIRECRAWL_SCRAPE_URL,
                # onlyMainContent=False keeps footers/headers where contact
                # emails and social links usually live.
                json={
                    "url": url,
                    "formats": ["html"],
                    "onlyMainContent": False,
                    "timeout": int(eff * 1000),
                },
                timeout=eff,
            )
            if resp.status_code >= 400:
                return self._fail(url, extract_api_error(resp))
            text, final_url, title, error = parse_firecrawl(resp.json())
        except Exception as e:  # noqa: BLE001
            return self._fail(url, f"{type(e).__name__}: {e}")
        if not text:
            return self._fail(url, error or "no content")
        return FetchResult(url=url, text=text, final_url=final_url, title=title, provider=self.name)


class HTTPFetcher(ContentFetcher):
    """Direct HTTP fetch. Used for both the proxied and basic fallbacks."""

    def __init__(
        self, proxy: str | None = None, name: str | None = None, max_html_bytes: int = 2_000_000
    ):
        self.proxy = proxy
        self.name = name or ("proxy" if proxy else "basic")
        self.max_html_bytes = max_html_bytes
        self._client: httpx.AsyncClient | None = None

    def is_available(self) -> bool:
        # Basic HTTP is always available; the proxy variant needs a proxy.
        return True if not self.proxy else bool(self.proxy)

    async def open(self) -> None:
        if self._client is None:
            kwargs: dict[str, Any] = {}
            if self.proxy:
                kwargs["proxy"] = self.proxy
            self._client = httpx.AsyncClient(
                headers=_BROWSER_HEADERS,
                follow_redirects=True,
                limits=httpx.Limits(max_connections=40),
                **kwargs,
            )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def fetch(self, url: str, timeout: float) -> FetchResult:
        assert self._client is not None
        try:
            resp = await self._client.get(url, timeout=timeout)
            resp.raise_for_status()
        except Exception as e:  # noqa: BLE001
            return self._fail(url, f"{type(e).__name__}: {e}")
        ctype = resp.headers.get("content-type", "")
        if "html" not in ctype and "text" not in ctype:
            return self._fail(url, f"non-HTML content-type: {ctype!r}")
        text = resp.text[: self.max_html_bytes]
        if not text:
            return self._fail(url, "empty body")
        return FetchResult(
            url=url,
            text=text,
            final_url=str(resp.url),
            provider=self.name,
        )


# ── The chain ──


class FetcherChain:
    """Try providers in order; first success wins, failures fall through.

    A provider that is configured but errors at runtime (quota, rate limit,
    network) simply yields to the next provider for that URL. The basic HTTP
    fetcher is always present as the final fallback, so a fetch only fully
    fails if direct HTTP also fails.
    """

    def __init__(self, fetchers: list[ContentFetcher]):
        self.fetchers = fetchers

    @property
    def active_names(self) -> list[str]:
        return [f.name for f in self.fetchers]

    async def __aenter__(self) -> FetcherChain:
        for f in self.fetchers:
            await f.open()
        logger.info("Fetcher chain active: %s", " -> ".join(self.active_names))
        return self

    async def __aexit__(self, *args: object) -> None:
        for f in self.fetchers:
            await f.close()

    async def fetch(self, url: str, timeout: float = 10.0) -> FetchResult:
        last: FetchResult | None = None
        for f in self.fetchers:
            result = await f.fetch(url, timeout)
            if result.ok:
                if last is not None:
                    logger.debug("Fetched %s via %s (after %s failed)", url, f.name, last.provider)
                return result
            last = result
            logger.debug("Provider %s failed for %s: %s", f.name, url, result.error)
        return last or FetchResult(url=url, status="failed", error="no fetchers configured")


def _detect_proxy(explicit: str | None) -> str | None:
    """Return an explicit proxy, else the first proxy env var that is set."""
    if explicit:
        return explicit
    for var in ENV_PROXY_VARS:
        val = os.getenv(var)
        if val:
            return val
    return None


def build_default_chain(
    proxy: str | None = None,
    max_html_bytes: int = 2_000_000,
) -> FetcherChain:
    """Construct the fallback chain from what is available in the environment.

    Order: TinyFish → Firecrawl → Proxied HTTP → Basic HTTP. Providers whose
    credentials/proxy are absent are skipped; Basic HTTP is always included as
    the final fallback (this is the original direct-fetch behaviour).
    """
    fetchers: list[ContentFetcher] = []

    tinyfish = TinyFishFetcher()
    if tinyfish.is_available():
        fetchers.append(tinyfish)

    firecrawl = FirecrawlFetcher()
    if firecrawl.is_available():
        fetchers.append(firecrawl)

    resolved_proxy = _detect_proxy(proxy)
    if resolved_proxy:
        fetchers.append(
            HTTPFetcher(proxy=resolved_proxy, name="proxy", max_html_bytes=max_html_bytes)
        )

    # Always-present final fallback: basic direct HTTP.
    fetchers.append(HTTPFetcher(proxy=None, name="basic", max_html_bytes=max_html_bytes))

    return FetcherChain(fetchers)
