"""Tests for website contact extraction (emails + social media URLs)."""

from gmaps.website import (
    WebsiteContactExtractor,
    decode_cfemail,
    extract_emails,
    extract_social_links,
    find_contact_pages,
    normalize_website_url,
)


class TestExtractEmails:
    def test_plain_email(self):
        html = "<p>Reach us at info@acmehvac.com today.</p>"
        assert extract_emails(html) == ["info@acmehvac.com"]

    def test_mailto_link(self):
        html = '<a href="mailto:Sales@Example-Biz.com?subject=Hi">Email us</a>'
        assert extract_emails(html) == ["sales@example-biz.com"]

    def test_deduplicates_case_insensitive(self):
        html = "info@shop.com INFO@shop.com Info@Shop.com"
        assert extract_emails(html) == ["info@shop.com"]

    def test_filters_image_filenames(self):
        html = '<img src="logo@2x.png"> contact@real.com <img src="hero@3x.jpg">'
        assert extract_emails(html) == ["contact@real.com"]

    def test_filters_junk_domains(self):
        html = "user@example.com trace@sentry.io real@business.net"
        assert extract_emails(html) == ["real@business.net"]

    def test_filters_example_local_part(self):
        html = "example@gmail.com orders@realcoffee.com"
        assert extract_emails(html) == ["orders@realcoffee.com"]

    def test_filters_hex_hash_locals(self):
        html = "0123456789abcdef0123456789abcdef@builds.example.io hi@ok.com"
        assert extract_emails(html) == ["hi@ok.com"]

    def test_cloudflare_obfuscated(self):
        # Encode "hi@x.co" with key 0x42
        key = 0x42
        email = "hi@x.co"
        encoded = bytes([key] + [ord(c) ^ key for c in email]).hex()
        html = f'<a data-cfemail="{encoded}">[protected]</a>'
        assert extract_emails(html) == ["hi@x.co"]

    def test_empty_html(self):
        assert extract_emails("") == []


class TestDecodeCfemail:
    def test_roundtrip(self):
        key = 0x17
        email = "owner@business.org"
        encoded = bytes([key] + [ord(c) ^ key for c in email]).hex()
        assert decode_cfemail(encoded) == email

    def test_invalid_hex(self):
        assert decode_cfemail("zzzz") is None

    def test_garbage_bytes(self):
        assert decode_cfemail("00ff00ff") is None


class TestExtractSocialLinks:
    def test_linkedin_company(self):
        html = '<a href="https://www.linkedin.com/company/acme-hvac/">LinkedIn</a>'
        links = extract_social_links(html)
        assert links["linkedin"] == "https://www.linkedin.com/company/acme-hvac"

    def test_linkedin_personal(self):
        html = '<a href="https://linkedin.com/in/jane-doe">me</a>'
        assert "linkedin" in extract_social_links(html)

    def test_all_major_platforms(self):
        html = """
        <a href="https://www.facebook.com/acmehvac">f</a>
        <a href="https://www.instagram.com/acmehvac/">i</a>
        <a href="https://x.com/acmehvac">x</a>
        <a href="https://www.youtube.com/@acmehvac">y</a>
        <a href="https://www.tiktok.com/@acmehvac">t</a>
        <a href="https://t.me/acmehvac">tg</a>
        """
        links = extract_social_links(html)
        for platform in ("facebook", "instagram", "twitter", "youtube", "tiktok", "telegram"):
            assert platform in links, platform

    def test_rejects_share_links(self):
        html = """
        <a href="https://www.facebook.com/sharer/sharer.php?u=http://x.com">share</a>
        <a href="https://twitter.com/intent/tweet?url=http://x.com">tweet</a>
        <a href="https://www.linkedin.com/company/real-co">real</a>
        """
        links = extract_social_links(html)
        assert "facebook" not in links
        assert "twitter" not in links
        assert links["linkedin"].endswith("/real-co")

    def test_strips_tracking_query(self):
        html = '<a href="https://www.instagram.com/shop/?utm_source=web&hl=en">ig</a>'
        assert extract_social_links(html)["instagram"] == "https://www.instagram.com/shop"

    def test_whatsapp_keeps_number(self):
        html = '<a href="https://wa.me/15551234567?text=hi">wa</a>'
        assert "wa.me/15551234567" in extract_social_links(html)["whatsapp"]

    def test_first_link_wins(self):
        html = """
        <a href="https://www.facebook.com/first-page">1</a>
        <a href="https://www.facebook.com/second-page">2</a>
        """
        assert extract_social_links(html)["facebook"].endswith("/first-page")

    def test_no_links(self):
        assert extract_social_links("<p>No socials here</p>") == {}


class TestFindContactPages:
    def test_finds_contact_page(self):
        html = '<a href="/contact-us">Contact</a><a href="/menu">Menu</a>'
        pages = find_contact_pages(html, "https://acme.com")
        assert pages == ["https://acme.com/contact-us"]

    def test_same_domain_only(self):
        html = '<a href="https://other-site.com/contact">External</a>'
        assert find_contact_pages(html, "https://acme.com") == []

    def test_www_variant_is_same_domain(self):
        html = '<a href="https://www.acme.com/about">About</a>'
        pages = find_contact_pages(html, "https://acme.com")
        assert pages == ["https://www.acme.com/about"]

    def test_limit(self):
        html = "".join(f'<a href="/contact-{i}">c</a>' for i in range(5))
        assert len(find_contact_pages(html, "https://acme.com", limit=2)) == 2

    def test_skips_mailto_and_anchors(self):
        html = '<a href="mailto:contact@a.com">m</a><a href="#contact">a</a>'
        assert find_contact_pages(html, "https://acme.com") == []


class TestNormalizeWebsiteUrl:
    def test_adds_scheme(self):
        assert normalize_website_url("acme.com") == "https://acme.com"

    def test_keeps_existing_scheme(self):
        assert normalize_website_url("http://acme.com") == "http://acme.com"

    def test_empty(self):
        assert normalize_website_url("") == ""


class TestAutoParams:
    def test_concurrency_scales_with_batch(self):
        c_small, _, _ = WebsiteContactExtractor.auto_params(10)
        c_big, _, _ = WebsiteContactExtractor.auto_params(5000)
        assert c_big > c_small

    def test_concurrency_capped(self):
        c, _, _ = WebsiteContactExtractor.auto_params(1_000_000)
        assert c <= WebsiteContactExtractor._MAX_CONCURRENCY

    def test_concurrency_floor(self):
        c, _, _ = WebsiteContactExtractor.auto_params(1)
        assert c >= 4

    def test_timeout_shrinks_for_large_batches(self):
        _, t_small, _ = WebsiteContactExtractor.auto_params(20)
        _, t_big, _ = WebsiteContactExtractor.auto_params(5000)
        assert t_big < t_small

    def test_large_batch_fetches_single_page(self):
        _, _, pages = WebsiteContactExtractor.auto_params(5000)
        assert pages == 1

    def test_small_batch_fetches_multiple_pages(self):
        _, _, pages = WebsiteContactExtractor.auto_params(10)
        assert pages >= 2

    def test_zero_is_safe(self):
        c, t, p = WebsiteContactExtractor.auto_params(0)
        assert c >= 4 and t > 0 and p >= 1


class TestConfigureForBatch:
    def test_auto_applied_without_overrides(self):
        ext = WebsiteContactExtractor()
        ext.configure_for_batch(5000)
        expected_c, expected_t, expected_p = WebsiteContactExtractor.auto_params(5000)
        assert ext.concurrency == expected_c
        assert ext._base_timeout == expected_t
        assert ext.max_pages == expected_p

    def test_overrides_respected(self):
        ext = WebsiteContactExtractor(timeout=30.0, concurrency=2, max_pages=5)
        ext.configure_for_batch(5000)  # would otherwise force 1 page, 5s
        assert ext.concurrency == 2
        assert ext._base_timeout == 30.0
        assert ext.max_pages == 5

    def test_reconfigure_between_batches(self):
        ext = WebsiteContactExtractor()
        ext.configure_for_batch(10)
        small_c = ext.concurrency
        ext.configure_for_batch(5000)
        assert ext.concurrency > small_c


class TestAdaptiveTimeout:
    def test_returns_base_before_enough_samples(self):
        ext = WebsiteContactExtractor()
        ext.configure_for_batch(100)
        assert ext._effective_timeout() == ext._base_timeout

    def test_tightens_with_fast_samples(self):
        ext = WebsiteContactExtractor(timeout=10.0)
        ext.configure_for_batch(100)
        ext._latencies = [0.5] * 20
        eff = ext._effective_timeout()
        assert eff < 10.0
        assert eff >= ext._MIN_TIMEOUT

    def test_never_below_floor(self):
        ext = WebsiteContactExtractor(timeout=10.0)
        ext.configure_for_batch(100)
        ext._latencies = [0.01] * 20
        assert ext._effective_timeout() == ext._MIN_TIMEOUT

    def test_never_above_base(self):
        ext = WebsiteContactExtractor(timeout=8.0)
        ext.configure_for_batch(100)
        ext._latencies = [50.0] * 20  # very slow samples
        assert ext._effective_timeout() <= 8.0


class TestParsedPlaceIntegration:
    def test_contact_fields_in_to_dict(self):
        from gmaps.rpc.parser import ParsedPlace

        p = ParsedPlace(
            name="Acme",
            emails=["info@acme.com"],
            social_links={"linkedin": "https://linkedin.com/company/acme"},
        )
        d = p.to_dict()
        assert d["contact"]["emails"] == ["info@acme.com"]
        assert d["contact"]["social_links"]["linkedin"].endswith("/acme")

    def test_empty_contacts_omitted(self):
        from gmaps.rpc.parser import ParsedPlace

        d = ParsedPlace(name="Acme", phone="555").to_dict()
        assert "emails" not in d.get("contact", {})
        assert "social_links" not in d.get("contact", {})
