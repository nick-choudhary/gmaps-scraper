"""Main client for Google Maps scraping.

High-level async API for searching places, getting details, and fetching reviews,
powered by reverse-engineered internal Google Maps endpoints using the pb= protobuf
protocol.

Three operating modes:
  1. Phase 1 only (default) — fast search, no login
  2. Phase 1 + Phase 2 enrich — place details, no login
  3. Phase 1 + Phase 2 enrich — with Google account login cookies
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

from ._auth.session import CookieSession
from ._places import PlacesAPI
from ._reviews import ReviewsAPI
from ._search import SearchAPI
from .rpc.parser import ParsedPlace, parse_place_details_response
from .transport import HTTPTransport

logger = logging.getLogger(__name__)

DEFAULT_COOKIE_FILE = Path.home() / ".gmaps_scraper" / "cookies.json"


class GMapsClient:
    """Async client for Google Maps internal API.

    Modes:
        Mode 1 (default): GMapsClient() — Phase 1 search only, no login
        Mode 2: GMapsClient(enrich=True) — Phase 1 + Phase 2 details, no login
        Mode 3: GMapsClient(enrich=True, cookie_file="login_cookies.json") — full

    Usage:
        async with GMapsClient() as client:
            results = await client.search.places("coffee", latitude=30.27, longitude=-97.74)

        async with GMapsClient(enrich=True) as client:
            results = await client.search.places("coffee", latitude=30.27, longitude=-97.74)
            for place in results.places:
                await client.enrich(place)
    """

    def __init__(
        self,
        enrich: bool = False,
        timeout: float = 30.0,
        max_retries: int = 3,
        retry_delay: float = 2.0,
        min_delay: float = 1.5,
        jitter_pct: float = 0.3,
        language: str = "en",
        proxy: str | None = None,
        cookie_file: str | None = None,
        login_cookies: str | None = None,
        extra_headers: dict[str, str] | None = None,
        validate: str | bool = "warn",
        identity: Any = None,
    ):
        """Initialize Google Maps client.

        Args:
            enrich: Enable Phase 2 place details enrichment.
            timeout: HTTP request timeout in seconds.
            max_retries: Maximum retry attempts per request.
            retry_delay: Base delay between retries (exponential backoff).
            min_delay: Minimum seconds between requests (jittered ±jitter_pct).
            jitter_pct: Jitter percentage for rate limiting (0.3 = ±30%).
            language: Language code for results.
            proxy: Optional proxy URL.
            cookie_file: Path to persist scraped cookies.
            login_cookies: Raw cookie string from a logged-in Google account
                           (enables Mode 3 — full field enrichment).
            extra_headers: Additional HTTP headers.
        """
        self.enrich_enabled = enrich
        self.login_cookies = login_cookies
        self._language = language
        # Drift validation mode: "warn" (default, log-only, non-breaking),
        # "strict" (raise DriftError on unhealthy first page), or False (off).
        self._validate = validate
        # Optional Phase 4 captured identity (real cookies/UA). A path string is
        # loaded lazily; None (default) = existing fabricated-cookie behavior.
        self._identity = identity

        cookie_path = cookie_file or str(DEFAULT_COOKIE_FILE)
        self._cookie_session = CookieSession(cookie_file=cookie_path, timeout=timeout)

        self._transport = HTTPTransport(
            base_url="https://www.google.com",
            timeout=timeout,
            max_retries=max_retries,
            retry_delay=retry_delay,
            min_delay=min_delay,
            jitter_pct=jitter_pct,
            proxy=proxy,
            extra_headers=extra_headers,
        )

        self._search: SearchAPI | None = None
        self._places: PlacesAPI | None = None
        self._reviews: ReviewsAPI | None = None
        self._opened = False

    async def __aenter__(self) -> GMapsClient:
        # Step 1: Establish cookie session (scraped NID/AEC/SOCS)
        await self._cookie_session.__aenter__()
        try:
            return await self._finish_open()
        except BaseException:
            # Don't leak the cookie session / transport if setup fails partway
            await self._transport.close()
            await self._cookie_session.__aexit__(None, None, None)
            raise

    async def _finish_open(self) -> GMapsClient:
        cookie_dict = self._cookie_session.get_cookie_dict()
        logger.info("Cookie session: %d cookies (%s)", len(cookie_dict), list(cookie_dict.keys()))

        # Step 2: Open transport
        await self._transport.open()

        # Step 3: Inject cookies into transport
        if self._transport._client is not None:
            jar = httpx.Cookies()
            for name, value in cookie_dict.items():
                jar.set(name, value, domain=".google.com")
            self._transport._client.cookies = jar

            # If login cookies provided, add them too
            if self.login_cookies:
                for pair in self.login_cookies.split(";"):
                    pair = pair.strip()
                    if "=" in pair:
                        name, value = pair.split("=", 1)
                        self._transport._client.cookies.set(
                            name.strip(), value.strip(), domain=".google.com"
                        )
                logger.info("Login cookies injected (%d pairs)", len(self.login_cookies.split(";")))

            # Phase 4: replay a captured real identity (cookies + UA), superseding
            # the fabricated consent cookie. Opt-in; None (default) = unchanged.
            if self._identity is not None:
                from .identity import (
                    CapturedIdentity,
                    age_hours,
                    apply_identity,
                    is_fresh,
                    load_identity,
                )

                ident = (
                    load_identity(self._identity)
                    if isinstance(self._identity, str)
                    else self._identity
                )
                if isinstance(ident, CapturedIdentity):
                    if not is_fresh(ident):
                        logger.warning(
                            "captured identity is stale (%.1fh); consider re-capturing",
                            age_hours(ident),
                        )
                    apply_identity(self._transport._client, ident)
                    logger.info("captured identity applied (%d cookies)", len(ident.cookies))
                else:
                    logger.warning("identity provided but could not be loaded: %r", self._identity)

        # Step 4: Initialize feature APIs
        self._search = SearchAPI(self._transport, self._language, validate=self._validate)
        self._places = PlacesAPI(self._transport, self._language)
        self._reviews = ReviewsAPI(self._transport, self._language)

        self._opened = True
        logger.info(
            "GMapsClient ready (enrich=%s, login=%s)", self.enrich_enabled, bool(self.login_cookies)
        )
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self._transport.close()
        await self._cookie_session.__aexit__(*args)
        self._opened = False

    @property
    def search(self) -> SearchAPI:
        self._check_open()
        return self._search  # type: ignore[return-value]

    @property
    def places(self) -> PlacesAPI:
        self._check_open()
        return self._places  # type: ignore[return-value]

    @property
    def reviews(self) -> ReviewsAPI:
        self._check_open()
        return self._reviews  # type: ignore[return-value]

    async def enrich(self, place: ParsedPlace, query: str = "") -> ParsedPlace:
        """Enrich a Phase 1 place with Phase 2 details (Mode 2/3).

        Fetches /maps/preview/place and merges review_count, hours,
        thumbnail, plus_code, owner, and (with login) description,
        photos, about, popular_times.

        Args:
            place: A ParsedPlace from Phase 1 search results.
            query: Original search query (improves place matching).

        Returns:
            The same ParsedPlace, mutated with enriched fields.
        """
        if not self.enrich_enabled:
            raise RuntimeError("Enrichment not enabled. Pass enrich=True to GMapsClient().")

        raw = await self._search.place_details(  # type: ignore[union-attr]
            place_id=place.place_id,
            hex_id=place.hex_id,
            ftid=place.ftid,
            data_id=place.data_id,
            name=place.name,
            latitude=place.latitude or 0.0,
            longitude=place.longitude or 0.0,
            query=query,
        )

        if raw:
            enriched = parse_place_details_response(raw)
            if enriched:
                # Merge enriched fields onto the original place
                for attr in (
                    "review_count",
                    "reviews_per_rating",
                    "reviews_link",
                    "plus_code",
                    "thumbnail",
                    "street_view_url",
                    "images",
                    "about",
                    "credit_cards",
                    "reservations",
                    "order_online",
                    "menu",
                    "owner",
                    "description",
                    "popular_times",
                    "status",
                ):
                    val = getattr(enriched, attr)
                    if val:
                        setattr(place, attr, val)
                # Overwrite hours with structured version if available
                if enriched.hours:
                    place.hours = enriched.hours

        return place

    async def extract_contacts(
        self,
        places: list[ParsedPlace] | ParsedPlace,
        concurrency: int | None = None,
        timeout: float | None = None,
        max_pages: int | None = None,
        model_extractor: Any = None,
    ) -> list[ParsedPlace]:
        """Visit each place's website and extract emails + social media URLs.

        gosom-style email extraction, extended with social links (LinkedIn,
        Facebook, Instagram, X/Twitter, YouTube, TikTok, Pinterest, WhatsApp,
        Telegram). Fetches homepage + up to two contact/about pages per site.

        Performance is auto-tuned from the batch size — concurrency, per-site
        timeout, and pages-per-site are decided automatically, and the timeout
        tightens at runtime to shed slow sites. The keyword arguments are
        optional overrides for advanced programmatic use; leave them as None
        (the default) for fully automatic behaviour.

        Uses a separate HTTP client (these are third-party sites, not Google),
        so it does not consume the Google rate-limit budget. Sites that fail
        are skipped silently — check logs at DEBUG for details.

        Args:
            places: One ParsedPlace or a list of them (mutated in-place:
                    sets `emails` and `social_links`).
            concurrency: Override simultaneous website fetches (None = auto).
            timeout: Override per-request timeout in seconds (None = auto).
            max_pages: Override pages fetched per site (None = auto).

        Returns:
            The same places (list), mutated with extracted contacts.
        """
        from .website import WebsiteContactExtractor

        batch = [places] if isinstance(places, ParsedPlace) else list(places)
        async with WebsiteContactExtractor(
            timeout=timeout,
            max_pages=max_pages,
            concurrency=concurrency,
            proxy=getattr(self._transport, "proxy", None),
            model_extractor=model_extractor,
        ) as extractor:
            await extractor.extract_batch(batch)
        return batch

    def get_stats(self) -> dict[str, Any]:
        return self._transport.get_stats()

    def _check_open(self) -> None:
        if not self._opened:
            raise RuntimeError("Client not opened. Use 'async with GMapsClient() as client:'")
