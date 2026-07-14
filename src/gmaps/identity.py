"""Capture-and-replay identity (Phase 4) — real browser artifacts as data.

Two of the scraper's biggest "tells" are hand-fabricated:

* the SOCS consent cookie is invented as ``base64("CAI" + timestamp)[:20]`` —
  it does not match Google's real protobuf format and can be flagged instantly;
* the ``pb=`` search parameter is a giant hand-authored string of reverse-
  engineered ``!Nb1`` flags that drifts from what a real Google Maps client
  sends and must be re-guessed by hand whenever Google changes it.

Both are *human theories* of Google's protocol. The Bitter-Lesson fix is to stop
guessing and instead **capture what a real browser actually sends** — its cookie
jar, User-Agent, and a genuine ``pb=`` string — and **replay** them, treating the
captured artifacts as data (a `CapturedIdentity`) rather than hand-coded
knowledge. A captured ``pb=`` is turned into a reusable template by swapping only
the query/coords/zoom/pagination for placeholders; every real flag rides along
for free.

The capture step itself needs a headless browser (Playwright) and network, so it
is an ops action you run, not part of the unit suite. Everything else here —
templating, storage, freshness, application — is deterministic and tested. All
opt-in: with no identity supplied, the scraper behaves exactly as before.
"""

from __future__ import annotations

import json
import logging
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# pb markers whose value we parameterize; every other captured flag is preserved.
PB_MARKERS: dict[str, str] = {
    "query": "!1s",  # search text
    "lng": "!2d",  # longitude
    "lat": "!3d",  # latitude
    "zoom": "!4f",  # map zoom
    "count": "!7i",  # results per page
    "offset": "!8i",  # pagination offset
}


@dataclass
class CapturedIdentity:
    """Real browser artifacts captured from a live session, stored as data."""

    cookies: dict[str, str] = field(default_factory=dict)
    user_agent: str = ""
    pb_templates: dict[str, str] = field(default_factory=dict)  # name -> template
    captured_at: float = field(default_factory=time.time)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "cookies": dict(self.cookies),
            "user_agent": self.user_agent,
            "pb_templates": dict(self.pb_templates),
            "captured_at": self.captured_at,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CapturedIdentity:
        return cls(
            cookies=dict(d.get("cookies", {})),
            user_agent=str(d.get("user_agent", "")),
            pb_templates=dict(d.get("pb_templates", {})),
            captured_at=float(d.get("captured_at", time.time())),
            note=str(d.get("note", "")),
        )


def save_identity(identity: CapturedIdentity, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(identity.to_dict(), indent=2), encoding="utf-8")


def load_identity(path: str | Path) -> CapturedIdentity | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        return CapturedIdentity.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, ValueError, KeyError):
        return None


def age_hours(identity: CapturedIdentity, now: float | None = None) -> float:
    return ((now or time.time()) - identity.captured_at) / 3600.0


def is_fresh(
    identity: CapturedIdentity, max_age_hours: float = 6.0, now: float | None = None
) -> bool:
    return age_hours(identity, now) <= max_age_hours


# ── pb template: capture-derived data instead of hand-authored flags ──


def parameterize_pb(raw_pb: str, **concretes: str | None) -> str:
    """Turn a captured real ``pb=`` string into a template.

    Pass the exact concrete substrings as they appear in the capture, e.g.
    ``parameterize_pb(pb, query="donut%20shop", lat="30.27", lng="-97.74")``.
    Only the query/coords/zoom/pagination markers are replaced with
    ``{placeholders}``; every other captured flag is left untouched.
    """
    template = raw_pb
    for field_name, value in concretes.items():
        if value is None or field_name not in PB_MARKERS:
            continue
        marker = PB_MARKERS[field_name]
        template = template.replace(f"{marker}{value}", f"{marker}{{{field_name}}}", 1)
    return template


def render_pb(template: str, **values: Any) -> str:
    """Fill a captured pb template with new values (placeholders → concrete)."""
    out = template
    for field_name, value in values.items():
        if value is None:
            continue
        out = out.replace(f"{{{field_name}}}", str(value))
    return out


def apply_identity(client: Any, identity: CapturedIdentity, domain: str = ".google.com") -> None:
    """Inject a captured identity's real cookies + User-Agent into an httpx client.

    Real cookies (including a genuine SOCS/NID) supersede the fabricated consent
    cookie, and the UA is pinned to the captured browser's so the cookie session
    and request UA are consistent (fixing the identity mismatch the audit flags).
    """
    if identity.user_agent:
        with suppress(Exception):  # headers may be immutable in odd clients
            client.headers["User-Agent"] = identity.user_agent
    for name, value in identity.cookies.items():
        try:
            client.cookies.set(name, value, domain=domain)
        except TypeError:
            client.cookies.set(name, value)


# ── Capture backends ──


class CaptureBackend(Protocol):
    async def capture(self, query: str, lat: float, lng: float) -> CapturedIdentity: ...


@dataclass
class ManualCapture:
    """Supply already-captured artifacts directly (testable, no browser).

    Useful when you export cookies/UA/pb from your own browser devtools and want
    to feed them in without running Playwright.
    """

    cookies: dict[str, str]
    user_agent: str
    pb_templates: dict[str, str] = field(default_factory=dict)

    async def capture(
        self, query: str = "", lat: float = 0.0, lng: float = 0.0
    ) -> CapturedIdentity:
        return CapturedIdentity(
            cookies=dict(self.cookies),
            user_agent=self.user_agent,
            pb_templates=dict(self.pb_templates),
            note="manual capture",
        )


class PlaywrightCapture:
    """Capture a real identity by driving a headless browser (ops-only).

    Requires ``playwright`` and a browser; run it yourself to refresh identities,
    not in CI. It visits Google Maps, accepts consent, exports the real cookie
    jar + UA, and intercepts a genuine search request to grab a real ``pb=``,
    which is then parameterized into a reusable template.
    """

    def __init__(self, headless: bool = True):
        self.headless = headless

    async def capture(
        self, query: str = "coffee", lat: float = 40.7128, lng: float = -74.0060
    ) -> CapturedIdentity:
        try:
            from playwright.async_api import async_playwright
        except ImportError as e:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "PlaywrightCapture requires the browser extra: "
                "pip install 'gmaps-scraper[browser]' and run where a browser is available."
            ) from e

        captured_pb: dict[str, str] = {}

        async with async_playwright() as pw:  # pragma: no cover - needs a browser
            browser = await pw.chromium.launch(headless=self.headless)
            context = await browser.new_context()
            page = await context.new_page()

            async def on_request(req: Any) -> None:
                if "/search" in req.url and "pb=" in req.url:
                    pb = req.url.split("pb=", 1)[1].split("&", 1)[0]
                    captured_pb.setdefault("search", pb)

            page.on("request", on_request)
            await page.goto("https://www.google.com/maps", wait_until="domcontentloaded")
            # Accept consent if present (best-effort; selectors vary by locale).
            for sel in ("button[aria-label*='Accept']", "form[action*='consent'] button"):
                try:
                    await page.click(sel, timeout=2000)
                    break
                except Exception:  # noqa: BLE001
                    pass
            await page.fill("#searchboxinput", query)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(4000)

            cookies_list = await context.cookies()
            ua = await page.evaluate("navigator.userAgent")
            await browser.close()

        cookies = {c["name"]: c["value"] for c in cookies_list}
        templates: dict[str, str] = {}
        if "search" in captured_pb:
            templates["search"] = parameterize_pb(
                captured_pb["search"],
                lat=str(lat),
                lng=str(lng),
            )
        return CapturedIdentity(
            cookies=cookies, user_agent=ua, pb_templates=templates, note="playwright capture"
        )
