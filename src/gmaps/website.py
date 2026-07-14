"""Website contact extraction — emails and social media URLs.

Visits a business's website (from Phase 1/2 search data) and extracts
contact emails plus social profile links (LinkedIn, Facebook, Instagram,
X/Twitter, YouTube, TikTok, Pinterest, WhatsApp, Telegram).

Inspired by gosom/google-maps-scraper's -email feature, extended with
social media URL extraction. Fetches the homepage and up to two likely
contact/about pages per site. All errors are captured per-site and never
raised — a dead website must not abort a 5,000-place scrape.

Performance is fully auto-tuned from the batch size — concurrency,
per-site timeout, and pages-per-site are decided automatically, and the
timeout tightens at runtime to shed the slow tail. No user configuration.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from urllib.parse import unquote, urljoin, urlparse

if TYPE_CHECKING:
    from .contacts import ContactExtractor
    from .fetchers import FetcherChain

logger = logging.getLogger(__name__)

# ── Email extraction ──

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,24}")

# mailto: links may carry ?subject=... etc.
MAILTO_RE = re.compile(r"""mailto:([^"'<>?\s]+)""", re.IGNORECASE)

# Cloudflare email obfuscation: <a data-cfemail="hexstring">
CFEMAIL_RE = re.compile(r'data-cfemail="([0-9a-fA-F]+)"')

# Domains/patterns that are never real contact emails
_EMAIL_JUNK_DOMAINS = (
    "example.com",
    "example.org",
    "domain.com",
    "email.com",
    "yoursite.com",
    "sentry.io",
    "wixpress.com",
    "sentry-next.wixpress.com",
    "godaddy.com",
    "schema.org",
    "w3.org",
    "placeholder.com",
    "yourdomain.com",
    "mysite.com",
    "youremail.com",
)

_CONSUMER_EMAIL_DOMAINS = {
    "gmail.com",
    "googlemail.com",
    "yahoo.com",
    "outlook.com",
    "hotmail.com",
    "live.com",
    "icloud.com",
    "me.com",
    "aol.com",
    "proton.me",
    "protonmail.com",
    "zoho.com",
}
# Filename-like matches: logo@2x.png etc.
_EMAIL_JUNK_SUFFIXES = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".ico",
    ".css",
    ".js",
    ".woff",
    ".woff2",
    ".ttf",
    ".mp4",
    ".pdf",
    ".webm",
)

# ── Social link extraction ──

# platform -> (regex on full URL, reject substrings)
SOCIAL_PATTERNS: dict[str, tuple[re.Pattern[str], tuple[str, ...]]] = {
    "linkedin": (
        re.compile(
            r"https?://(?:[\w\-]+\.)?linkedin\.com/(?:company|in|school|showcase)/[^\s\"'<>)]+",
            re.I,
        ),
        ("/share", "shareArticle"),
    ),
    "facebook": (
        re.compile(r"https?://(?:www\.|m\.|web\.)?facebook\.com/[^\s\"'<>)]+", re.I),
        (
            "sharer",
            "/share",
            "/plugins/",
            "/tr?",
            "/tr/",
            "facebook.com/2008",
            "/dialog/",
            "/login",
            "/hashtag/",
        ),
    ),
    "instagram": (
        re.compile(r"https?://(?:www\.)?instagram\.com/[^\s\"'<>)]+", re.I),
        ("/p/", "/reel/", "/share", "/embed"),
    ),
    "twitter": (
        re.compile(r"https?://(?:www\.)?(?:twitter\.com|x\.com)/[^\s\"'<>)]+", re.I),
        ("/intent/", "/share", "/hashtag/", "/search?", "twitter.com/home"),
    ),
    "youtube": (
        re.compile(r"https?://(?:www\.)?youtube\.com/(?:channel/|user/|c/|@)[^\s\"'<>)]+", re.I),
        ("/embed/",),
    ),
    "tiktok": (
        re.compile(r"https?://(?:www\.)?tiktok\.com/@[^\s\"'<>)]+", re.I),
        (),
    ),
    "pinterest": (
        re.compile(r"https?://(?:[\w\-]+\.)?pinterest\.\w{2,6}/[^\s\"'<>)]+", re.I),
        ("/pin/create", "/pin/", "pinterest.com/js/"),
    ),
    "whatsapp": (
        re.compile(r"https?://(?:wa\.me|api\.whatsapp\.com/send)[^\s\"'<>)]*", re.I),
        (),
    ),
    "telegram": (
        re.compile(r"https?://(?:t\.me|telegram\.me)/[^\s\"'<>)]+", re.I),
        ("/share",),
    ),
}

# Links likely to be a contact/about page (matched against href, lowercased)
_CONTACT_HINTS = (
    "contact",
    "kontakt",
    "contacto",
    "contatti",
    "about",
    "impressum",
    "reach-us",
    "reachus",
    "get-in-touch",
)

HREF_RE = re.compile(r"""href=["']([^"']+)["']""", re.IGNORECASE)


@dataclass
class ContactInfo:
    """Contacts extracted from one business website."""

    website: str = ""
    emails: list[str] = field(default_factory=list)
    social_links: dict[str, str] = field(default_factory=dict)
    pages_fetched: list[str] = field(default_factory=list)
    email_sources: dict[str, str] = field(default_factory=dict)
    social_sources: dict[str, str] = field(default_factory=dict)
    error: str = ""
    used_model: bool = False  # True if the optional model extractor contributed


# ── Pure extraction functions (unit-testable, no network) ──


def decode_cfemail(hex_str: str) -> str | None:
    """Decode a Cloudflare-obfuscated email (data-cfemail attribute)."""
    try:
        data = bytes.fromhex(hex_str)
        key = data[0]
        email = bytes(b ^ key for b in data[1:]).decode("utf-8")
        return email if EMAIL_RE.fullmatch(email) else None
    except (ValueError, UnicodeDecodeError):
        return None


def _is_junk_email(email: str) -> bool:
    e = email.lower()
    if e.endswith(_EMAIL_JUNK_SUFFIXES):
        return True
    local, domain = e.rsplit("@", 1)
    if local == "example":
        return True
    if domain in _EMAIL_JUNK_DOMAINS or domain.endswith(
        tuple("." + d for d in _EMAIL_JUNK_DOMAINS)
    ):
        return True
    # Hex-ish local parts (build hashes, sentry DSNs)
    return len(local) >= 24 and re.fullmatch(r"[0-9a-f]+", local) is not None


def _email_matches_website(email: str, website_url: str) -> bool:
    """Keep same-domain custom addresses plus common consumer mailboxes."""
    email_domain = email.rsplit("@", 1)[1].lower().removeprefix("www.")
    if email_domain in _CONSUMER_EMAIL_DOMAINS:
        return True
    website_host = (urlparse(normalize_website_url(website_url)).hostname or "").lower()
    website_host = website_host.removeprefix("www.")
    if not website_host:
        return True
    return (
        email_domain == website_host
        or email_domain.endswith("." + website_host)
        or website_host.endswith("." + email_domain)
    )


def extract_emails(html: str, *, website_url: str = "") -> list[str]:
    """Extract deduplicated, filtered emails from HTML (order-preserving)."""
    found: list[str] = []

    # mailto: first — highest signal
    for m in MAILTO_RE.findall(html):
        candidate = m.strip()
        if EMAIL_RE.fullmatch(candidate):
            found.append(candidate)

    # Cloudflare-obfuscated
    for hex_str in CFEMAIL_RE.findall(html):
        decoded = decode_cfemail(hex_str)
        if decoded:
            found.append(decoded)

    # Plain-text occurrences
    found.extend(EMAIL_RE.findall(html))

    result: list[str] = []
    seen: set[str] = set()
    for e in found:
        e = unquote(e).strip().strip(".").lower()
        if (
            e
            and e not in seen
            and not _is_junk_email(e)
            and (not website_url or _email_matches_website(e, website_url))
        ):
            seen.add(e)
            result.append(e)
    return result


def _clean_social_url(url: str) -> str:
    """Normalize a social URL: strip tracking queries, fragments, trailing junk."""
    url = url.rstrip(").,;\"'")
    parsed = urlparse(url)
    # Keep query only for WhatsApp (phone lives in ?phone=)
    keep_query = "whatsapp" in parsed.netloc or parsed.netloc == "wa.me"
    query = f"?{parsed.query}" if (keep_query and parsed.query) else ""
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{path}{query}"


def extract_social_links(html: str) -> dict[str, str]:
    """Extract social profile URLs from HTML.

    Returns {platform: url} with the first valid (non-share) link per
    platform. Platforms: linkedin, facebook, instagram, twitter, youtube,
    tiktok, pinterest, whatsapp, telegram.
    """
    links: dict[str, str] = {}
    for platform, (pattern, rejects) in SOCIAL_PATTERNS.items():
        if platform in links:
            continue
        for m in pattern.finditer(html):
            url = m.group(0)
            if any(r.lower() in url.lower() for r in rejects):
                continue
            links[platform] = _clean_social_url(url)
            break
    return links


def find_contact_pages(html: str, base_url: str, limit: int = 2) -> list[str]:
    """Find likely contact/about page URLs on the same domain."""
    base_domain = urlparse(base_url).netloc.lower().removeprefix("www.")
    candidates: list[str] = []
    seen: set[str] = set()

    for href in HREF_RE.findall(html):
        h = href.strip()
        if h.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        low = h.lower()
        if not any(hint in low for hint in _CONTACT_HINTS):
            continue
        absolute = urljoin(base_url, h)
        p = urlparse(absolute)
        if p.scheme not in ("http", "https"):
            continue
        if p.netloc.lower().removeprefix("www.") != base_domain:
            continue
        normalized = absolute.split("#")[0]
        if normalized not in seen and normalized != base_url:
            seen.add(normalized)
            candidates.append(normalized)
        if len(candidates) >= limit:
            break
    return candidates


def normalize_website_url(url: str) -> str:
    """Ensure the website URL has a scheme."""
    url = url.strip()
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


# ── Extractor (network) ──


class WebsiteContactExtractor:
    """Fetch business websites and extract emails + social links.

    Performance is auto-tuned from the batch size; there is nothing to
    configure. Larger batches get more parallelism and a shorter per-site
    timeout (so the slow tail is dropped automatically), and the timeout
    tightens further at runtime based on observed latencies. Tiny batches
    get gentler, more thorough settings.

    The constructor arguments exist only as optional overrides for the
    low-level Python API; leaving them as None (the default) means "decide
    automatically", which is what every user-facing path does.

    Usage:
        async with WebsiteContactExtractor() as extractor:
            info = await extractor.extract("https://example-business.com")
            # or mutate ParsedPlace objects in bulk (auto-tuned):
            await extractor.extract_batch(places)
    """

    # Auto-tuning bounds
    _MIN_TIMEOUT = 3.0
    _MAX_CONCURRENCY = 24
    _ADAPTIVE_MIN_SAMPLES = 15

    def __init__(
        self,
        timeout: float | None = None,
        max_pages: int | None = None,
        max_html_bytes: int = 2_000_000,
        concurrency: int | None = None,
        proxy: str | None = None,
        fetcher_chain: FetcherChain | None = None,
        model_extractor: ContactExtractor | None = None,
    ):
        # None => auto-decided per batch in configure_for_batch(). Explicit
        # values are respected as overrides (low-level API only).
        self._timeout_override = timeout
        self._max_pages_override = max_pages
        self._concurrency_override = concurrency
        self.max_html_bytes = max_html_bytes

        # Effective values (safe auto defaults; refined per batch)
        self.concurrency = concurrency if concurrency is not None else 5
        self.max_pages = max(1, max_pages) if max_pages is not None else 3
        self._base_timeout = timeout if timeout is not None else 10.0

        self._latencies: list[float] = []
        # Content fetching goes through a provider fallback chain (TinyFish →
        # Firecrawl → proxied HTTP → basic HTTP). If none is injected, one is
        # auto-built from the environment; with no keys/proxy it is exactly the
        # basic direct-HTTP fetch used before this feature existed.
        self._proxy = proxy
        self._chain = fetcher_chain
        self._owns_chain = fetcher_chain is None
        # Optional Phase 2 model extractor; runs ONLY on the residual (sites the
        # regex pass found nothing on). None (default) = pure regex behaviour.
        self._model_extractor = model_extractor

    # ── Auto-tuning ──

    @classmethod
    def auto_params(cls, n_sites: int) -> tuple[int, float, int]:
        """Decide (concurrency, per-site timeout, max_pages) from batch size.

        Larger batches → more parallelism and a shorter per-site timeout so
        the slow tail is shed automatically; tiny batches → gentler and more
        thorough. Fully automatic; no user input.
        """
        n = max(1, n_sites)
        concurrency = min(cls._MAX_CONCURRENCY, max(4, (n + 39) // 40))
        if n <= 50:
            timeout, max_pages = 12.0, 3
        elif n <= 500:
            timeout, max_pages = 9.0, 3
        elif n <= 2000:
            timeout, max_pages = 7.0, 2
        else:
            timeout, max_pages = 5.0, 1
        return concurrency, timeout, max_pages

    def configure_for_batch(self, n_sites: int) -> None:
        """Apply auto-tuned params for a batch, honoring explicit overrides."""
        auto_c, auto_t, auto_p = self.auto_params(n_sites)
        self.concurrency = (
            self._concurrency_override if self._concurrency_override is not None else auto_c
        )
        self._base_timeout = (
            self._timeout_override if self._timeout_override is not None else auto_t
        )
        self.max_pages = (
            max(1, self._max_pages_override) if self._max_pages_override is not None else auto_p
        )
        self._latencies = []

    def _effective_timeout(self) -> float:
        """Adaptive per-request timeout: tighten toward the observed p90 to
        drop the pathological slow tail, floored at _MIN_TIMEOUT and never
        above the batch's base timeout."""
        base = self._base_timeout
        if len(self._latencies) >= self._ADAPTIVE_MIN_SAMPLES:
            ordered = sorted(self._latencies)
            p90 = ordered[int(len(ordered) * 0.9)]
            return max(self._MIN_TIMEOUT, min(base, p90 * 1.5))
        return base

    # ── Lifecycle ──

    async def __aenter__(self) -> WebsiteContactExtractor:
        # Build the provider fallback chain if one wasn't injected, then open
        # it. Per-request adaptive timeouts are applied in _fetch_html.
        if self._chain is None:
            from .fetchers import build_default_chain

            self._chain = build_default_chain(
                proxy=self._proxy,
                max_html_bytes=self.max_html_bytes,
            )
        if self._owns_chain:
            await self._chain.__aenter__()
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._chain is not None and self._owns_chain:
            await self._chain.__aexit__(*args)

    async def _fetch_html(self, url: str) -> str:
        """Fetch a page's HTML via the provider chain.

        Raises on hard failure (so the caller records it per-page); returns
        the page text (capped) on success. The chain already applies the
        content-type / size checks in its HTTP fetchers.
        """
        assert self._chain is not None, "Use 'async with WebsiteContactExtractor()'"
        t0 = time.monotonic()
        result = await self._chain.fetch(url, timeout=self._effective_timeout())
        self._latencies.append(time.monotonic() - t0)
        if not result.ok:
            raise RuntimeError(result.error or "fetch failed")
        return result.text[: self.max_html_bytes]

    async def extract(self, website_url: str) -> ContactInfo:
        """Extract contacts from one website (homepage + contact pages)."""
        url = normalize_website_url(website_url)
        info = ContactInfo(website=url)
        if not url:
            info.error = "no website URL"
            return info

        pages: list[str] = [url]
        emails: list[str] = []
        socials: dict[str, str] = {}
        seen_emails: set[str] = set()
        email_sources: dict[str, str] = {}
        social_sources: dict[str, str] = {}
        page_texts: list[str] = []

        for i, page_url in enumerate(pages):
            if len(info.pages_fetched) >= self.max_pages:
                break
            try:
                html = await self._fetch_html(page_url)
            except Exception as exc:  # noqa: BLE001 — never abort a batch
                if i == 0:
                    info.error = f"{type(exc).__name__}: {exc}"
                logger.debug("Contact fetch failed for %s: %s", page_url, exc)
                continue

            info.pages_fetched.append(page_url)
            if not html:
                continue
            page_texts.append(html)

            for email in extract_emails(html, website_url=url):
                if email not in seen_emails:
                    seen_emails.add(email)
                    emails.append(email)
                    email_sources[email] = page_url
            for platform, link in extract_social_links(html).items():
                if platform not in socials:
                    socials[platform] = link
                    social_sources[platform] = page_url

            # From the homepage, queue likely contact pages
            if i == 0:
                pages.extend(find_contact_pages(html, page_url, limit=self.max_pages - 1))

        # Phase 2 (opt-in): only when a model extractor is configured AND the
        # deterministic pass left a gap. Results are source-grounded inside the
        # extractor, so nothing not present on the page can be added.
        if self._model_extractor is not None and page_texts and (not emails or not socials):
            combined = "\n".join(page_texts)
            mc = self._model_extractor.extract(combined, url)
            for email in mc.emails:
                if email not in seen_emails:
                    seen_emails.add(email)
                    emails.append(email)
                    email_sources[email] = info.pages_fetched[0]
                    info.used_model = True
            for platform, link in mc.socials.items():
                if platform not in socials:
                    socials[platform] = link
                    social_sources[platform] = info.pages_fetched[0]
                    info.used_model = True

        info.emails = emails
        info.social_links = socials
        info.email_sources = email_sources
        info.social_sources = social_sources
        return info

    async def extract_batch(
        self,
        places: Sequence[object],
        concurrency: int | None = None,
        max_contacts: int | None = None,
    ) -> list[ContactInfo]:
        """Extract contacts for a batch of ParsedPlace objects.

        Auto-tunes concurrency, per-site timeout, and pages-per-site from the
        batch size before running. Mutates each place in-place, setting
        `emails` and `social_links` when the place has a `website`. Places
        without websites are skipped.

        Returns the list of ContactInfo (aligned with input order).
        """
        self.configure_for_batch(len(places))
        effective_concurrency = concurrency or self.concurrency
        sem = asyncio.Semaphore(effective_concurrency)
        eligible_indices = [
            index for index, place in enumerate(places) if getattr(place, "website", "")
        ]
        budget = (
            len(eligible_indices)
            if max_contacts is None
            else min(len(eligible_indices), max(0, max_contacts))
        )
        selected_indices = set(eligible_indices[:budget])
        logger.info(
            "Contact extraction: %d sites | auto concurrency=%d, timeout=%.0fs, pages=%d",
            len(places),
            effective_concurrency,
            self._base_timeout,
            self.max_pages,
        )

        async def _one(index: int, place: object) -> ContactInfo:
            website = getattr(place, "website", "") or ""
            if not website:
                place.contact_status = "not_eligible_no_website"  # type: ignore[attr-defined]
                return ContactInfo(error="no website")
            if index not in selected_indices:
                place.contact_status = "not_attempted_limit"  # type: ignore[attr-defined]
                return ContactInfo(website=website)
            place.contact_status = "in_progress"  # type: ignore[attr-defined]
            place.contact_attempted_at = datetime.now(timezone.utc).isoformat()  # type: ignore[attr-defined]
            async with sem:
                info = await self.extract(website)
            if info.emails:
                place.emails = info.emails  # type: ignore[attr-defined]
            if info.social_links:
                place.social_links = info.social_links  # type: ignore[attr-defined]
            place.contact_pages = info.pages_fetched  # type: ignore[attr-defined]
            place.contact_sources = {  # type: ignore[attr-defined]
                "emails": info.email_sources,
                "social_links": info.social_sources,
            }
            place.contact_error = info.error  # type: ignore[attr-defined]
            place.contact_status = "failed" if info.error else "completed"  # type: ignore[attr-defined]
            return info

        results = await asyncio.gather(*(_one(index, place) for index, place in enumerate(places)))
        found = sum(1 for r in results if r.emails or r.social_links)
        logger.info(
            "Contact extraction: %d/%d sites yielded contacts",
            found,
            len(places),
        )
        return list(results)
