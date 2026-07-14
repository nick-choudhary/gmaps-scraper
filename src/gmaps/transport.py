"""HTTP transport layer for Google Maps internal API.

Handles HTTP session management, headers, cookies, retries, rate limiting,
and anti-detection measures (UA rotation, jitter, backoff).
Patterns adapted from gosom/google-maps-scraper.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

import httpx

from .exceptions import (
    NetworkError,
    RateLimitError,
    TimeoutError,
)
from .rpc.decoder import decode_response as _decode

logger = logging.getLogger(__name__)

# ── User-Agent rotation pool (Chrome + Firefox, Windows + macOS) ──
_USER_AGENTS: list[str] = [
    # Chrome 131 on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    # Chrome 130 on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    # Chrome 131 on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    # Firefox 133 on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    # Firefox 132 on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:132.0) Gecko/20100101 Firefox/132.0",
    # Edge 131 on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
]

# Randomize once at import to avoid predictable ordering
random.shuffle(_USER_AGENTS)

# ── Default base headers (no UA — that's set per-request) ──
BASE_HEADERS: dict[str, str] = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}


def _pick_ua() -> str:
    """Return a random user-agent from the rotation pool."""
    return random.choice(_USER_AGENTS)


def _jitter(base_ms: float, pct: float = 0.3) -> float:
    """Add random jitter ±pct around base_ms.

    gosom pattern: randomization prevents fingerprinting by request timing.
    """
    delta = base_ms * pct
    return base_ms + random.uniform(-delta, delta)


class HTTPTransport:
    """HTTP client wrapper for Google Maps requests.

    Anti-detection features (adapted from gosom):
    - Per-request User-Agent rotation from pool of 6 real browser UAs
    - Jittered rate limiting (base delay ±30% random)
    - Exponential backoff on 429/5xx with jitter
    - Base delay randomization to avoid timing fingerprint
    - Cookie session freshness tracking
    - Proper browser header parity
    """

    def __init__(
        self,
        base_url: str = "https://www.google.com/maps",
        timeout: float = 30.0,
        max_retries: int = 3,
        retry_delay: float = 2.0,
        min_delay: float = 1.0,
        jitter_pct: float = 0.3,
        extra_headers: dict[str, str] | None = None,
        proxy: str | None = None,
        cookie_jar: httpx.Cookies | None = None,
    ):
        self.base_url = base_url
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.min_delay = min_delay
        self.jitter_pct = jitter_pct
        self.proxy = proxy

        self._base_headers = {**BASE_HEADERS, **(extra_headers or {})}
        self._cookie_jar = cookie_jar
        self._client: httpx.AsyncClient | None = None
        self._request_count: int = 0
        self._last_request_time: float = 0.0
        self._ua_index: int = 0  # Round-robin counter for UA pool
        self._session_start: float = time.monotonic()

    async def __aenter__(self) -> HTTPTransport:
        await self.open()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def open(self) -> None:
        """Initialize the HTTP client session."""
        if self._client is None:
            limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
            transport_kwargs: dict[str, Any] = {}
            if self.proxy:
                transport_kwargs["proxy"] = self.proxy

            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=self._build_headers(),
                timeout=httpx.Timeout(self.timeout),
                limits=limits,
                follow_redirects=True,
                http2=False,
                cookies=self._cookie_jar,
                **transport_kwargs,
            )
            # Randomize initial UA position
            self._ua_index = random.randint(0, len(_USER_AGENTS) - 1)

    async def close(self) -> None:
        """Close the HTTP client session."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        """Get the HTTP client, raising if not opened."""
        if self._client is None:
            raise RuntimeError("HTTPTransport not opened. Use 'async with' context manager.")
        return self._client

    @property
    def session_age_seconds(self) -> float:
        """How long this transport session has been alive."""
        return time.monotonic() - self._session_start

    @property
    def is_session_stale(self) -> bool:
        """Check if session is old enough to risk detection (>15 min).

        gosom pattern: Google tracks session duration; cycling sessions
        helps avoid pattern detection.
        """
        return self.session_age_seconds > 900  # 15 minutes

    def _build_headers(self) -> dict[str, str]:
        """Build request headers with rotated User-Agent.

        Uses round-robin UA selection to avoid fingerprinting.
        Each request gets a fresh UA from the pool.
        """
        headers = dict(self._base_headers)
        self._ua_index = (self._ua_index + 1) % len(_USER_AGENTS)
        headers["User-Agent"] = _USER_AGENTS[self._ua_index]
        return headers

    async def _rate_limit_wait(self) -> None:
        """Enforce minimum interval between requests with jitter.

        gosom pattern: jittered delays prevent timing-based detection.
        """
        delay = _jitter(self.min_delay, self.jitter_pct)
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < delay:
            sleep_time = delay - elapsed
            logger.debug("Rate limit: sleeping %.2fs (jittered)", sleep_time)
            await asyncio.sleep(sleep_time)

    async def _rotate_headers(self) -> None:
        """Rotate request headers in the active client.

        Called before each request to ensure fresh UA.
        """
        if self._client is not None:
            self._client.headers.update(self._build_headers())

    async def get(
        self,
        path: str,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        response_type: str = "auto",
    ) -> Any:
        """Perform a GET request with retry logic.

        Args:
            path: URL path (relative to base_url).
            params: Query parameters.
            headers: Extra headers to merge with defaults.
            response_type: Expected response format for decoding.

        Returns:
            Decoded response data.

        Raises:
            RateLimitError: On 429 or blocked response.
            NetworkError: On connection failures after retries.
            TimeoutError: On timeout.
        """
        await self._rate_limit_wait()
        await self._rotate_headers()

        request_headers = {**self._base_headers}
        request_headers["User-Agent"] = _pick_ua()
        if headers:
            request_headers.update(headers)

        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = await self.client.get(
                    path,
                    params=params,
                    headers=request_headers,
                )
                self._last_request_time = time.monotonic()
                self._request_count += 1

                if response.status_code == 429:
                    retry_after = _parse_retry_after(response.headers.get("retry-after"))
                    raise RateLimitError(
                        "Rate limited by Google Maps",
                        retry_after=retry_after,
                    )

                if response.status_code >= 500:
                    logger.warning(
                        "Server error %d on attempt %d/%d",
                        response.status_code,
                        attempt + 1,
                        self.max_retries,
                    )
                    if attempt < self.max_retries - 1:
                        await _exponential_backoff(attempt, self.retry_delay)
                        continue
                    response.raise_for_status()

                response.raise_for_status()
                return _decode(response.text, response_type=response_type)

            except RateLimitError:
                raise

            except httpx.TimeoutException as e:
                last_error = TimeoutError(
                    f"Request timed out after {self.timeout}s",
                    timeout_seconds=self.timeout,
                    original_error=e,
                )
                if attempt < self.max_retries - 1:
                    await _exponential_backoff(attempt, self.retry_delay)
                    continue

            except httpx.ConnectError as e:
                last_error = NetworkError(
                    f"Connection failed: {e}",
                    original_error=e,
                )
                if attempt < self.max_retries - 1:
                    await _exponential_backoff(attempt, self.retry_delay * 2)
                    continue

            except httpx.HTTPStatusError as e:
                if e.response.status_code < 500:
                    raise NetworkError(
                        f"HTTP {e.response.status_code}: {e.response.reason_phrase}",
                        original_error=e,
                    ) from e
                if attempt < self.max_retries - 1:
                    await _exponential_backoff(attempt, self.retry_delay)
                    continue
                raise NetworkError(
                    f"HTTP {e.response.status_code} after {self.max_retries} retries",
                    original_error=e,
                ) from e

            except Exception as e:
                last_error = NetworkError(f"Request failed: {e}", original_error=e)
                if attempt < self.max_retries - 1:
                    await _exponential_backoff(attempt, self.retry_delay)
                    continue

        raise last_error or NetworkError("Request failed")

    async def post(
        self,
        path: str,
        data: dict[str, Any] | bytes | None = None,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        response_type: str = "json",
    ) -> Any:
        """Perform a POST request with retry logic.

        Args:
            path: URL path (relative to base_url).
            data: POST body (dict is sent as JSON, bytes sent raw).
            params: Query parameters.
            headers: Extra headers.
            response_type: Expected response format.

        Returns:
            Decoded response data.
        """
        await self._rate_limit_wait()
        await self._rotate_headers()

        request_headers = {**self._base_headers}
        request_headers["User-Agent"] = _pick_ua()
        if headers:
            request_headers.update(headers)

        # Determine content type
        if isinstance(data, dict):
            request_headers.setdefault("Content-Type", "application/json")
            content: str | bytes | None = None
            json_data: dict[str, Any] | None = data
        elif isinstance(data, bytes):
            request_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
            content = data
            json_data = None
        else:
            content = None
            json_data = None

        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = await self.client.post(
                    path,
                    content=content,
                    json=json_data,
                    params=params,
                    headers=request_headers,
                )
                self._last_request_time = time.monotonic()
                self._request_count += 1

                if response.status_code == 429:
                    retry_after = _parse_retry_after(response.headers.get("retry-after"))
                    raise RateLimitError(
                        "Rate limited by Google Maps",
                        retry_after=retry_after,
                    )

                if response.status_code >= 500:
                    if attempt < self.max_retries - 1:
                        await _exponential_backoff(attempt, self.retry_delay)
                        continue
                    response.raise_for_status()

                response.raise_for_status()
                return _decode(response.text, response_type=response_type)

            except RateLimitError:
                raise

            except httpx.TimeoutException as e:
                last_error = TimeoutError(
                    "Request timed out",
                    timeout_seconds=self.timeout,
                    original_error=e,
                )
                if attempt < self.max_retries - 1:
                    await _exponential_backoff(attempt, self.retry_delay)
                    continue

            except Exception as e:
                last_error = NetworkError(f"POST request failed: {e}", original_error=e)
                if attempt < self.max_retries - 1:
                    await _exponential_backoff(attempt, self.retry_delay)
                    continue

        raise last_error or NetworkError("POST request failed")

    def get_stats(self) -> dict[str, Any]:
        """Get transport statistics."""
        return {
            "request_count": self._request_count,
            "last_request_time": self._last_request_time,
            "min_delay": self.min_delay,
            "jitter_pct": self.jitter_pct,
            "session_age_seconds": self.session_age_seconds,
            "ua_pool_size": len(_USER_AGENTS),
        }

    async def refresh_session(self) -> None:
        """Force session refresh (close + reopen) to reset cookies and timing.

        gosom pattern: rotate sessions periodically to avoid detection
        from long-lived cookie sessions.
        """
        await self.close()
        self._session_start = time.monotonic()
        self._request_count = 0
        await self.open()
        logger.info("Session refreshed (age limit reached)")

    @classmethod
    def shuffle_grid_order(cls, cells: list[Any]) -> list[Any]:
        """Randomize grid cell search order to avoid sequential pattern.

        gosom pattern: searching cells in sequential grid order creates
        a detectable spatial pattern. Random order breaks this.
        """
        shuffled = list(cells)
        random.shuffle(shuffled)
        return shuffled


def _parse_retry_after(header: str | None) -> float | None:
    """Parse Retry-After header value."""
    if not header:
        return None
    try:
        if header.isdigit():
            return float(header)
        return None
    except (ValueError, TypeError):
        return None


async def _exponential_backoff(attempt: int, base_delay: float) -> None:
    """Sleep with exponential backoff and jitter.

    gosom pattern: exponential delay with ±30% jitter prevents
    detection by retry-timing fingerprint.
    """
    delay = base_delay * (2**attempt)
    delay = _jitter(delay, 0.3)
    logger.debug("Backing off for %.2fs (attempt %d, jittered)", delay, attempt + 1)
    await asyncio.sleep(delay)
