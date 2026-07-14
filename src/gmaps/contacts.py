"""Contact extraction strategies (Phase 2) — regex default + optional model.

The deterministic regex/blocklist/platform-table extractor in ``website.py``
stays the default and does all the steady-state work. This module adds a common
interface plus an *optional* model-backed extractor used only on the residual —
sites where the cheap path found nothing — where a model generalizes to unseen
platforms, locales, and obfuscated/JS-rendered contacts a fixed regex cannot.

Two safety rails from the red-team review are enforced here, because feeding
third-party page HTML to a model creates real risks:

* **Source-grounding** — a model-returned email/URL is kept only if it literally
  appears in the fetched page bytes. This blocks hallucinated contacts (a real
  hazard for a lead-gen tool) and defangs prompt-injection attempts to inject a
  contact that is not actually on the page.
* **Schema-stable output** — the adapter always returns the same
  ``ModelContacts`` shape regardless of what the model emits, so downstream
  consumers (CSV columns, MCP schema) never see surprises.

No model or API is wired here. The user supplies a callable; nothing runs unless
a model extractor is explicitly passed in.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)


@dataclass
class ModelContacts:
    """Normalized contact result from any extractor."""

    emails: list[str] = field(default_factory=list)
    socials: dict[str, str] = field(default_factory=dict)
    raw: Any = None


# ── Source-grounding helpers ──


def _norm_url(u: str) -> str:
    return (
        u.lower()
        .strip()
        .removeprefix("https://")
        .removeprefix("http://")
        .removeprefix("www.")
        .rstrip("/")
    )


def email_grounded(email: str, page_text: str) -> bool:
    """True if the email is backed by the page — either literally, or by both its
    local-part and domain label appearing in the text.

    The token form permits legitimate de-obfuscation (a model turning
    ``sales [at] acme [dot] com`` into ``sales@acme.com``) while still blocking
    hallucination: a model cannot invent ``ceo@acme.com`` unless ``ceo`` actually
    appears on the page.
    """
    if not email or "@" not in email:
        return False
    email_l = email.lower()
    text = page_text.lower()
    if email_l in text:
        return True
    local, _, domain = email_l.partition("@")
    domain_label = domain.split(".")[0] if domain else ""
    return bool(local) and bool(domain_label) and local in text and domain_label in text


def url_grounded(url: str, page_text: str) -> bool:
    """True only if the (normalized) URL appears in the page text."""
    core = _norm_url(url)
    return bool(core) and core in page_text.lower()


def ground_contacts(contacts: ModelContacts, page_text: str) -> ModelContacts:
    """Drop any email/social not backed by the source page (anti-hallucination)."""
    emails = [e for e in contacts.emails if email_grounded(e, page_text)]
    socials = {k: v for k, v in contacts.socials.items() if url_grounded(v, page_text)}
    return ModelContacts(emails=emails, socials=socials, raw=contacts.raw)


# ── Extractor interface + implementations ──


class ContactExtractor(Protocol):
    """Extract contacts from a page's text/HTML."""

    def extract(self, page_text: str, url: str) -> ModelContacts: ...


@dataclass
class RegexContactExtractor:
    """Default deterministic extractor — wraps the pure regex functions."""

    def extract(self, page_text: str, url: str = "") -> ModelContacts:
        from .website import extract_emails, extract_social_links

        return ModelContacts(
            emails=extract_emails(page_text),
            socials=extract_social_links(page_text),
        )


@dataclass
class ModelContactExtractor:
    """Optional model-backed extractor (pluggable; no API wired by default).

    ``fn(page_text, url) -> {"emails": [...], "socials": {platform: url}}``.
    You supply the callable (e.g. a JSON-returning LLM prompt). Its output is
    normalized and, by default, source-grounded before being returned. A model
    that raises or returns junk degrades to an empty result — it never breaks a
    batch.
    """

    fn: Callable[[str, str], dict[str, Any] | None]
    ground: bool = True

    def extract(self, page_text: str, url: str = "") -> ModelContacts:
        try:
            out = self.fn(page_text, url) or {}
        except Exception as e:  # noqa: BLE001 — never abort a batch on model error
            logger.debug("model contact extractor failed for %s: %s", url, e)
            return ModelContacts()

        raw_emails = out.get("emails") if isinstance(out, dict) else None
        raw_socials = out.get("socials") if isinstance(out, dict) else None
        emails = [e.strip() for e in (raw_emails or []) if isinstance(e, str) and e.strip()]
        socials = {
            str(k): v.strip()
            for k, v in (raw_socials or {}).items()
            if isinstance(v, str) and v.strip()
        }
        # dedupe emails, lowercase, preserve order
        emails = list(dict.fromkeys(e.lower() for e in emails))

        result = ModelContacts(emails=emails, socials=socials, raw=out)
        if self.ground:
            result = ground_contacts(result, page_text)
        return result
