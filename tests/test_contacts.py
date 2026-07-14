"""Tests for Phase 2 contact extraction strategies (regex + optional model)."""

from types import SimpleNamespace

from gmaps.contacts import (
    ModelContactExtractor,
    ModelContacts,
    RegexContactExtractor,
    email_grounded,
    ground_contacts,
    url_grounded,
)
from gmaps.website import WebsiteContactExtractor


class TestGrounding:
    def test_email_literal(self):
        assert email_grounded("a@b.com", "reach a@B.com now")

    def test_email_token_deobfuscation(self):
        # de-obfuscated email not literally present but tokens are
        assert email_grounded("sales@acme.com", "sales [at] acme [dot] com")

    def test_email_hallucination_rejected(self):
        assert not email_grounded("ceo@acme.com", "welcome to acme, call us")

    def test_email_foreign_domain_rejected(self):
        assert not email_grounded("info@randomcorp.com", "this is the acme site")

    def test_url_grounded(self):
        assert url_grounded(
            "https://www.linkedin.com/company/acme/", "see linkedin.com/company/acme"
        )
        assert not url_grounded("https://linkedin.com/company/ghost", "nothing relevant")

    def test_ground_contacts_filters(self):
        mc = ModelContacts(
            emails=["real@acme.com", "ghost@acme.com"],
            socials={
                "linkedin": "https://linkedin.com/company/acme",
                "twitter": "https://x.com/ghosthandle",
            },
        )
        page = "email real@acme.com — linkedin.com/company/acme"
        g = ground_contacts(mc, page)
        assert g.emails == ["real@acme.com"]
        assert "linkedin" in g.socials and "twitter" not in g.socials


class TestRegexContactExtractor:
    def test_wraps_pure_functions(self):
        html = '<a href="mailto:info@acme.com">e</a><a href="https://www.linkedin.com/company/acme">l</a>'
        mc = RegexContactExtractor().extract(html)
        assert mc.emails == ["info@acme.com"]
        assert mc.socials.get("linkedin", "").endswith("/acme")


class TestModelContactExtractor:
    def test_normalizes_and_grounds(self):
        page = "contact sales@acme.com or linkedin.com/company/acme"

        def fn(t, u):
            return {
                "emails": ["Sales@ACME.com"],
                "socials": {"linkedin": "https://www.linkedin.com/company/acme"},
            }

        mc = ModelContactExtractor(fn=fn).extract(page, "u")
        assert mc.emails == ["sales@acme.com"]
        assert mc.socials["linkedin"].endswith("/acme")

    def test_rejects_hallucination(self):
        page = "no contact info here"
        mc = ModelContactExtractor(fn=lambda t, u: {"emails": ["ceo@acme.com"]}).extract(page, "u")
        assert mc.emails == []

    def test_model_exception_is_safe(self):
        def boom(t, u):
            raise RuntimeError("model down")

        assert ModelContactExtractor(fn=boom).extract("page", "u").emails == []

    def test_junk_output_is_safe(self):
        assert ModelContactExtractor(fn=lambda t, u: "notadict").extract("page", "u").emails == []

    def test_grounding_can_be_disabled(self):
        mc = ModelContactExtractor(
            fn=lambda t, u: {"emails": ["ghost@x.com"]}, ground=False
        ).extract("empty page", "u")
        assert mc.emails == ["ghost@x.com"]


# ── Integration: residual-only invocation inside WebsiteContactExtractor ──


class _FakeChain:
    def __init__(self, html):
        self.html = html
        self.calls = 0

    async def fetch(self, url, timeout=10.0):
        self.calls += 1
        return SimpleNamespace(
            ok=bool(self.html), text=self.html, error="" if self.html else "empty"
        )


class TestResidualIntegration:
    async def test_model_runs_on_residual_and_grounds(self):
        # Obfuscated email + schemeless social -> regex finds NOTHING -> residual
        page = "<html>Reach us: sales [at] acme [dot] com — linkedin.com/company/acme</html>"

        def fn(text, url):
            return {
                "emails": ["sales@acme.com"],
                "socials": {"linkedin": "https://linkedin.com/company/acme"},
            }

        ext = WebsiteContactExtractor(
            fetcher_chain=_FakeChain(page),
            model_extractor=ModelContactExtractor(fn=fn),
        )
        async with ext:
            info = await ext.extract("acme.com")
        assert info.emails == ["sales@acme.com"]
        assert info.social_links.get("linkedin", "").endswith("/acme")
        assert info.used_model is True

    async def test_model_not_called_when_regex_succeeds(self):
        page = '<a href="mailto:info@acme.com">e</a><a href="https://www.linkedin.com/company/acme">l</a>'
        called = {"n": 0}

        def fn(text, url):
            called["n"] += 1
            return {}

        ext = WebsiteContactExtractor(
            fetcher_chain=_FakeChain(page),
            model_extractor=ModelContactExtractor(fn=fn),
        )
        async with ext:
            info = await ext.extract("acme.com")
        assert info.emails == ["info@acme.com"]
        assert called["n"] == 0  # both found by regex -> model skipped
        assert info.used_model is False

    async def test_off_by_default_no_model(self):
        page = "<html>Reach us: sales [at] acme [dot] com</html>"
        ext = WebsiteContactExtractor(fetcher_chain=_FakeChain(page))  # no model_extractor
        async with ext:
            info = await ext.extract("acme.com")
        assert info.emails == []  # obfuscated email invisible to regex
        assert info.used_model is False
