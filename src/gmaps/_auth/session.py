"""Cookie and session management for Google Maps.

Google Maps does not require authentication (no Google login needed),
but it DOES require a valid cookie session. Without proper cookies,
requests get redirected to consent pages or blocked with HTTP 429.

The cookie chain established through reverse-engineering:
1. Visit google.com → get 1P_JAR and initial session cookies
2. Visit consent.google.com → establish consent state (cookie names vary)
3. Visit google.com/maps → get NID and AEC session cookies
4. Generate SOCS cookie with current timestamp
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Cookie names required for Google Maps requests
REQUIRED_COOKIES = ("NID", "AEC", "SOCS")

# Minimal browser headers that look like a real Chrome
BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    # No "br": httpx cannot decode brotli without the optional brotli
    # package — advertising it causes silent empty responses (see
    # transport.py BASE_HEADERS, same fix).
    "Accept-Encoding": "gzip, deflate",
}


class CookieSession:
    """Manages cookie acquisition and persistence for Google Maps.

    Handles the cookie consent flow and session establishment without
    requiring a Google account login.
    """

    def __init__(
        self,
        cookie_file: str | None = None,
        timeout: float = 30.0,
    ):
        self._cookie_file = cookie_file
        self._timeout = timeout
        self._cookies: dict[str, str] = {}
        self._client: httpx.AsyncClient | None = None

    @property
    def cookies(self) -> dict[str, str]:
        """Current session cookies."""
        return self._cookies

    @property
    def is_valid(self) -> bool:
        """Check if the session has all required cookies."""
        return all(c in self._cookies for c in REQUIRED_COOKIES)

    async def __aenter__(self) -> CookieSession:
        await self._init_client()
        await self.ensure_session()
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _init_client(self) -> None:
        """Initialize the HTTP client for cookie acquisition."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers=BROWSER_HEADERS,
                timeout=httpx.Timeout(self._timeout),
                follow_redirects=True,
                http2=False,
            )

    async def ensure_session(self) -> None:
        """Ensure valid cookies exist, acquiring fresh ones if needed."""
        if self.is_valid:
            return

        # Try loading from file first
        if self._cookie_file:
            self._load_from_file()

        if self.is_valid:
            logger.info("Loaded valid cookies from file")
            return

        # Acquire fresh cookies
        logger.info("Acquiring fresh Google Maps session cookies...")
        await self._acquire_fresh_cookies()

        if self._cookie_file:
            self._save_to_file()

    async def refresh(self) -> None:
        """Force refresh the cookie session."""
        self._cookies.clear()
        await self._acquire_fresh_cookies()
        if self._cookie_file:
            self._save_to_file()

    async def _acquire_fresh_cookies(self) -> None:
        """Execute the cookie acquisition chain.

        Step 1: Visit google.com → get initial cookies
        Step 2: Visit the consent flow (the returned cookie names vary)
        Step 3: Visit google.com/maps → get NID + AEC
        Step 4: Generate SOCS cookie
        """
        assert self._client is not None

        # Step 1: Initial visit to google.com
        logger.debug("Step 1: Visiting google.com...")
        try:
            await self._client.get("https://www.google.com/")
            self._extract_cookies()
            logger.debug("Step 1 complete: %d cookies", len(self._cookies))
        except Exception as e:
            logger.warning("Step 1 failed: %s", e)

        await asyncio.sleep(1.0)

        # Step 2: Accept consent
        logger.debug("Step 2: Accepting consent...")
        try:
            consent_url = (
                "https://consent.google.com/ml?"
                "continue=https://www.google.com/maps&"
                "gl=US&hl=en&pc=m&"
                "src=1&"
                "cm=2&"
            )
            await self._client.get(consent_url)
            self._extract_cookies()

            # Also accept via the simplified consent endpoint
            await self._client.get(
                "https://consent.google.com/save",
                params={
                    "continue": "https://www.google.com/maps",
                    "gl": "US",
                    "hl": "en",
                },
            )
            self._extract_cookies()
            logger.debug("Step 2 complete")
        except Exception as e:
            logger.warning("Step 2 failed: %s", e)

        await asyncio.sleep(1.0)

        # Step 3: Visit Google Maps
        logger.debug("Step 3: Visiting google.com/maps...")
        try:
            await self._client.get("https://www.google.com/maps")
            self._extract_cookies()
            logger.debug("Step 3 complete: %d cookies", len(self._cookies))
        except Exception as e:
            logger.warning("Step 3 failed: %s", e)

        # Step 4: Generate SOCS cookie with current timestamp
        socs_value = _generate_socs_cookie()
        self._cookies["SOCS"] = socs_value

        if self.is_valid:
            logger.info(
                "Cookie session established: NID=%s, AEC=%s, SOCS=%s",
                "present" if "NID" in self._cookies else "missing",
                "present" if "AEC" in self._cookies else "missing",
                "present" if "SOCS" in self._cookies else "missing",
            )
        else:
            missing = [c for c in REQUIRED_COOKIES if c not in self._cookies]
            logger.warning(
                "Cookie session may be incomplete. Missing: %s. Available: %s",
                missing,
                list(self._cookies.keys()),
            )

    def _extract_cookies(self) -> None:
        """Extract cookies from the HTTP client's cookie jar."""
        if self._client is None:
            return
        for cookie in self._client.cookies.jar:
            if cookie.name and cookie.value:
                self._cookies[cookie.name] = cookie.value

    def get_cookie_string(self) -> str:
        """Get cookies as a Cookie header string."""
        return "; ".join(f"{name}={value}" for name, value in self._cookies.items())

    def get_cookie_dict(self) -> dict[str, str]:
        """Get cookies as a simple dict for httpx."""
        return dict(self._cookies)

    def _save_to_file(self) -> None:
        """Persist cookies to a JSON file."""
        if not self._cookie_file:
            return
        import json
        import os

        os.makedirs(os.path.dirname(self._cookie_file) or ".", exist_ok=True)
        with open(self._cookie_file, "w") as f:
            json.dump(
                {
                    "cookies": self._cookies,
                    "saved_at": time.time(),
                },
                f,
                indent=2,
            )
        logger.debug("Cookies saved to %s", self._cookie_file)

    def _load_from_file(self) -> None:
        """Load cookies from a JSON file."""
        if not self._cookie_file:
            return
        import json
        import os

        if not os.path.exists(self._cookie_file):
            return
        try:
            with open(self._cookie_file) as f:
                data = json.load(f)
                saved_cookies = data.get("cookies", {})
                saved_at = data.get("saved_at", 0)

                # Cookies expire after ~6 hours, refresh if older
                if time.time() - saved_at < 6 * 3600:
                    self._cookies.update(saved_cookies)
                    logger.debug(
                        "Loaded %d cookies from file (age: %.0fm)",
                        len(saved_cookies),
                        (time.time() - saved_at) / 60,
                    )
                else:
                    logger.debug(
                        "Cookie file too old (%.0fh), will refresh", (time.time() - saved_at) / 3600
                    )
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("Failed to load cookies: %s", e)


def _generate_socs_cookie() -> str:
    """Generate a SOCS cookie value with current timestamp.

    The SOCS cookie format observed: CAESNwgDEgk... (base64-encoded data).
    For scraping purposes, a minimal valid-looking value works.

    Returns:
        SOCS cookie value string.
    """
    # Google's SOCS cookie is complex but a minimal value works for scraping.
    # Format: "CAI" + base64(timestamp + data)
    import base64

    timestamp_ms = int(time.time() * 1000)
    raw = f"CAI{timestamp_ms}"
    return base64.b64encode(raw.encode()).decode()[:20]
