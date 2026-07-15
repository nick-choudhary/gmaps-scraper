"""Unit tests for ParsedPlace, field extraction, and grouped JSON output.

Run: pytest tests/test_parser.py -v
"""

from __future__ import annotations

from gmaps.rpc.parser import (
    ParsedPlace,
    _extract_about_new,
    _extract_complete_address_new,
    _extract_hours_new,
    _extract_media_new,
    _extract_rating_new,
    _safe_bool,
    _safe_deep_str,
    _safe_float,
    _safe_int,
    _safe_list,
    _safe_str,
    parse_place_details_response,
    parse_search_response,
)

# ── ParsedPlace dataclass tests ──


class TestParsedPlace:
    def test_default_values(self):
        p = ParsedPlace()
        assert p.name == ""
        assert p.place_id == ""
        assert p.rating is None
        assert p.review_count == 0
        assert p.categories == []
        assert p.hours == {}
        assert p.is_ad is False

    def test_canonical_json_round_trips_for_resume(self):
        original = ParsedPlace(
            name="Acme Chiropractic",
            place_id="ChIJ-resume",
            phone="(404) 555-0100",
            website="https://acme.example",
            address="123 Main St, Atlanta, GA",
            rating=4.9,
            review_count=123,
            latitude=33.75,
            longitude=-84.39,
            categories=["Chiropractor"],
            emails=["hello@acme.example"],
            social_links={"instagram": "https://instagram.com/acme"},
        )

        restored = ParsedPlace.from_dict(original.to_dict())

        assert restored.to_dict() == original.to_dict()

    def test_to_dict_removes_empty(self):
        p = ParsedPlace(name="Test Cafe", place_id="ChIJ123")
        d = p.to_dict()
        assert d["name"] == "Test Cafe"
        assert d["place_id"] == "ChIJ123"
        assert "rating" not in d  # None excluded
        assert "categories" not in d  # Empty list excluded
        assert "is_ad" not in d  # False excluded

    def test_to_dict_groups_contact(self):
        p = ParsedPlace(
            name="Test",
            phone="(512) 555-1000",
            website="https://example.com",
            place_id="ChIJ123",
            google_maps_url="https://www.google.com/maps/place/?q=place_id:ChIJ123",
        )
        d = p.to_dict()
        assert d["contact"]["phone"] == "(512) 555-1000"
        assert d["contact"]["website"] == "https://example.com"
        assert (
            d["contact"]["google_maps_url"]
            == "https://www.google.com/maps/place/?q=place_id:ChIJ123"
        )

    def test_to_dict_groups_address(self):
        p = ParsedPlace(
            name="Test",
            address="123 Main St, Austin, TX",
            street="123 Main St",
            city="Austin",
            state="Texas",
            postal_code="78701",
            country="US",
            borough="Downtown",
        )
        d = p.to_dict()
        addr = d["address"]
        assert addr["full"] == "123 Main St, Austin, TX"
        assert addr["street"] == "123 Main St"
        assert addr["city"] == "Austin"
        assert addr["state"] == "Texas"
        assert addr["postal_code"] == "78701"
        assert addr["country"] == "US"
        assert addr["borough"] == "Downtown"

    def test_to_dict_groups_rating(self):
        p = ParsedPlace(
            name="Test",
            rating=4.5,
            review_count=120,
            reviews_per_rating={"5": 80, "4": 30},
            price_range="$$",
        )
        d = p.to_dict()
        assert d["rating"]["rating"] == 4.5
        assert d["rating"]["review_count"] == 120
        assert d["rating"]["reviews_per_rating"] == {"5": 80, "4": 30}
        assert d["rating"]["price_range"] == "$$"

    def test_to_dict_groups_business_hours(self):
        p = ParsedPlace(
            name="Test",
            categories=["Coffee shop", "Cafe"],
            hours={"Monday": ["8AM-6PM"], "Tuesday": ["8AM-6PM"]},
            timezone="America/Chicago",
        )
        d = p.to_dict()
        biz = d["business"]
        assert biz["categories"] == ["Coffee shop", "Cafe"]
        assert biz["hours"] == {"Monday": ["8AM-6PM"], "Tuesday": ["8AM-6PM"]}
        assert biz["timezone"] == "America/Chicago"

    def test_to_dict_is_ad_only_when_true(self):
        p1 = ParsedPlace(name="Test", is_ad=False)
        assert "is_ad" not in p1.to_dict()

        p2 = ParsedPlace(name="Test", is_ad=True)
        assert p2.to_dict()["is_ad"] is True

    def test_field_count(self):
        """Verify the documented place schema remains intentional."""
        fields = ParsedPlace.__dataclass_fields__
        assert len(fields) == 58, f"Expected 58 fields, got {len(fields)}"
        # Check key fields
        for f in [
            "name",
            "place_id",
            "hex_id",
            "ftid",
            "data_id",
            "cid",
            "phone",
            "website",
            "google_maps_url",
            "plus_code",
            "address",
            "street",
            "city",
            "state",
            "postal_code",
            "country",
            "borough",
            "neighborhood",
            "rating",
            "review_count",
            "reviews_per_rating",
            "latitude",
            "longitude",
            "categories",
            "hours",
            "popular_times",
            "timezone",
            "description",
            "status",
            "photos",
            "images",
            "thumbnail",
            "street_view_url",
            "author_photo",
            "about",
            "credit_cards",
            "reservations",
            "order_online",
            "menu",
            "owner",
            "quick_amenities",
            "is_ad",
            "search_token",
            "raw",
            "reviews_link",
            "price_range",
            "price_level",
        ]:
            assert f in fields, f"Missing field: {f}"


# ── Safe accessor tests ──


class TestSafeAccessors:
    def test_safe_str_basic(self):
        data = ["hello", "world", None, 42]
        assert _safe_str(data, 0) == "hello"
        assert _safe_str(data, 1) == "world"
        assert _safe_str(data, 2) == ""
        assert _safe_str(data, 3) == "42"

    def test_safe_str_out_of_bounds(self):
        assert _safe_str([1, 2], 10) == ""

    def test_safe_int(self):
        assert _safe_int([10, 20, None], 0) == 10
        assert _safe_int([10, 20, None], 1) == 20
        assert _safe_int([10, 20, None], 2) == 0

    def test_safe_float(self):
        assert _safe_float([1.5, 2.5], 0) == 1.5
        assert _safe_float([1.5, None], 1) is None

    def test_safe_list(self):
        assert _safe_list([[1], [2]], 0) == [1]
        assert _safe_list([[1], [2]], 5) is None

    def test_safe_deep_str(self):
        data = [["a", ["b", ["c"]]]]
        assert _safe_deep_str(data, 0, 1, 1, 0) == "c"
        assert _safe_deep_str(data, 0, 5, 0) == ""

    def test_safe_bool(self):
        assert _safe_bool([True, 1, 0, None], 0) is True
        assert _safe_bool([True, 1, 0, None], 1) is True
        assert _safe_bool([True, 1, 0, None], 2) is False
        assert _safe_bool([True, 1, 0, None], 3) is False


# ── Extraction helper tests ──


class TestExtractRating:
    def test_basic_rating(self):
        place = ParsedPlace()
        pd = [None] * 200
        pd[4] = [None, None, "$$", None, None, None, None, 4.5, 120]
        pd[116] = 2
        pd[175] = [None, None, 0, [3, 5, 10, 30, 80]]
        _extract_rating_new(pd, place)
        assert place.rating == 4.5
        assert place.review_count == 120
        assert place.price_range == "$$"
        assert place.price_level == 2
        assert place.reviews_per_rating.get("1") == 3
        assert place.reviews_per_rating.get("5") == 80

    def test_missing_rating(self):
        place = ParsedPlace()
        pd = [None] * 200
        _extract_rating_new(pd, place)
        assert place.rating is None
        assert place.review_count == 0

    def test_partial_rating(self):
        place = ParsedPlace()
        pd = [None] * 200
        pd[4] = [None, None, None, None, None, None, None, 4.0]
        _extract_rating_new(pd, place)
        assert place.rating == 4.0
        assert place.review_count == 0


class TestExtractHours:
    def test_new_format_203(self):
        place = ParsedPlace()
        # Simulate [203][0] = list of day entries
        pd = [None] * 250
        pd[203] = [
            [  # [203][0] = detailed hours
                ["Monday", 1, [2026, 7, 6], [["8AM-6PM", [[8], [18]]]], 0, 1],
                ["Tuesday", 2, [2026, 7, 7], [["8AM-6PM", [[8], [18]]]], 0, 1],
            ],
            None,  # status
            3,  # day index
        ]
        _extract_hours_new(pd, place)
        assert "Monday" in place.hours
        assert "8AM-6PM" in place.hours["Monday"]
        assert "Tuesday" in place.hours

    def test_missing_hours(self):
        place = ParsedPlace()
        pd = [None] * 250
        _extract_hours_new(pd, place)
        assert place.hours == {}


class TestExtractCompleteAddress:
    def test_structured_address(self):
        place = ParsedPlace()
        pd = [None] * 200
        pd[183] = [None, ["Manhattan", "123 Main St", None, "New York", "10001", "NY", "US"]]
        _extract_complete_address_new(pd, place)
        assert place.borough == "Manhattan"
        assert place.street == "123 Main St"
        assert place.city == "New York"
        assert place.postal_code == "10001"
        assert place.state == "NY"
        assert place.country == "US"

    def test_missing_address(self):
        place = ParsedPlace()
        pd = [None] * 200
        _extract_complete_address_new(pd, place)
        assert place.borough == ""


class TestExtractMedia:
    def test_thumbnail_extraction(self):
        place = ParsedPlace()
        pd = [None] * 200
        # [72][0] = list of items, each item[6] = [url]
        pd[72] = [[None, [None, None, None, None, None, None, ["https://example.com/photo.jpg"]]]]
        _extract_media_new(pd, place)
        assert place.thumbnail == "https://example.com/photo.jpg"

    def test_photos_extraction(self):
        place = ParsedPlace()
        pd = [None] * 200
        pd[36] = [["ref1", "https://example.com/p1.jpg"], ["ref2", "https://example.com/p2.jpg"]]
        _extract_media_new(pd, place)
        assert len(place.photos) == 2
        assert place.photos[0]["url"] == "https://example.com/p1.jpg"

    def test_images_with_login_data(self):
        place = ParsedPlace()
        pd = [None] * 200
        # [171] = list of category blocks
        pd[171] = [
            [
                "CgIgAQ==",
                "token",
                "All",
                [
                    [
                        "CIABIhD",
                        10,
                        12,
                        None,
                        None,
                        None,
                        ["https://lh3.googleusercontent.com/photo1.jpg"],
                    ]
                ],
            ]
        ]
        _extract_media_new(pd, place)
        assert len(place.images) == 1
        assert place.images[0]["url"] == "https://lh3.googleusercontent.com/photo1.jpg"
        assert place.images[0]["title"] == "All"


class TestExtractAbout:
    def test_credit_cards(self):
        place = ParsedPlace()
        pd = [None] * 200
        pd[100] = [None, [["Credit cards", ["Visa", "Mastercard", "Amex"]]]]
        _extract_about_new(pd, place)
        assert place.credit_cards == ["Visa", "Mastercard", "Amex"]

    def test_about_sections(self):
        place = ParsedPlace()
        pd = [None] * 200
        pd[100] = [
            None,
            [["Service options", [["Dine-in", True], ["Takeout", True], ["Delivery", False]]]],
        ]
        _extract_about_new(pd, place)
        assert len(place.about) == 1
        assert place.about[0]["name"] == "Service options"
        assert len(place.about[0]["options"]) == 3

    def test_missing_about(self):
        place = ParsedPlace()
        pd = [None] * 200
        _extract_about_new(pd, place)
        assert place.about == []
        assert place.credit_cards == []


# ── parse_search_response tests ──


class TestParseSearchResponse:
    def _make_search_response(self):
        """Build a minimal valid search response matching live format.

        data[0][1] = [metadata_entry, business1, business2, ...]
        each business: list where [14] = place_data
        """
        pd = [None] * 260
        pd[0] = "data_id_123"
        pd[1] = "search_token_abc"
        pd[10] = "0x8644b56516bc162b:0x3a42efe8264e399c"
        pd[11] = "Test Coffee Shop"
        pd[13] = ["Coffee shop", "Cafe"]
        pd[14] = "Downtown"
        pd[18] = "Test Coffee Shop, 123 Main St, Austin, TX 78701"
        pd[30] = "America/Chicago"
        pd[78] = "ChIJ123456789"
        pd[89] = "/g/11abc123"
        pd[4] = [None, None, "$", ["https://reviews.link"], None, None, None, 4.5, 100]
        pd[9] = [None, None, 30.2672, -97.7431]
        pd[7] = ["https://example.com", None, None, None, None]
        pd[178] = [
            ["(512) 555-1000", [["(512) 555-1000", 1], ["+15125551000", 2]], None, "+15125551000"]
        ]
        pd[2] = ["123 Main St", "Austin, TX"]
        pd[157] = "https://lh3.googleusercontent.com/photo.jpg"

        business_entry = [None] * 15
        business_entry[14] = pd

        metadata_entry = [None] * 15

        return [[None, [metadata_entry, business_entry]]]

    def test_parse_basic(self):
        raw = self._make_search_response()
        places = parse_search_response(raw)
        assert len(places) == 1
        p = places[0]
        assert p.name == "Test Coffee Shop"
        assert p.place_id == "ChIJ123456789"
        assert p.rating == 4.5
        assert p.review_count == 100
        assert p.latitude == 30.2672
        assert p.longitude == -97.7431
        assert p.website == "https://example.com"
        assert p.phone == "(512) 555-1000"
        assert p.categories == ["Coffee shop", "Cafe"]
        assert p.timezone == "America/Chicago"
        assert p.neighborhood == "Downtown"

    def test_parse_empty_response(self):
        assert parse_search_response(None) == []
        assert parse_search_response([]) == []
        assert parse_search_response([None]) == []

    def test_parse_skips_metadata_entry(self):
        raw = self._make_search_response()
        # The metadata entry [0] should be skipped
        places = parse_search_response(raw)
        # Only 1 business, not 2 (metadata excluded)
        assert len(places) == 1

    def test_parse_current_maps_ui_envelope(self):
        legacy = self._make_search_response()
        place_data = legacy[0][1][1][14]
        current = [None] * 65
        current[64] = [["search metadata"], [None, place_data]]

        places = parse_search_response(current)

        assert len(places) == 1
        assert places[0].name == "Test Coffee Shop"
        assert places[0].place_id == "ChIJ123456789"

    def test_parse_grouped_json(self):
        raw = self._make_search_response()
        places = parse_search_response(raw)
        d = places[0].to_dict()
        assert d["name"] == "Test Coffee Shop"
        assert "contact" in d
        assert d["contact"]["phone"] == "(512) 555-1000"
        assert d["contact"]["website"] == "https://example.com"
        assert "address" in d
        assert d["address"]["neighborhood"] == "Downtown"
        assert "rating" in d
        assert d["rating"]["rating"] == 4.5
        assert "business" in d
        assert "America/Chicago" in d["business"]["timezone"]


# ── parse_place_details_response tests ──


class TestParsePlaceDetails:
    def _make_details_response(self):
        """Build a minimal place details response."""
        pd = [None] * 260
        pd[0] = "data_id_456"
        pd[1] = "0ahUKEwi_token"
        pd[2] = ["123 Main St", "Austin, TX 78701"]
        pd[4] = [None, None, None, [None, "92 reviews"], None, None, None, 4.9]
        pd[9] = [None, None, 30.265, -97.732]
        pd[10] = "0x8644b5:0xf76b50"
        pd[11] = "Detail Test Shop"
        pd[13] = ["Coffee shop"]
        pd[14] = "East Austin"
        pd[18] = "Detail Test Shop, 123 Main St, Austin, TX 78701"
        pd[30] = "America/Chicago"
        pd[39] = "123 Main St, Austin, TX 78701"
        pd[78] = "ChIJDetail123"
        pd[89] = "/g/11detail456"
        pd[157] = "https://lh3.googleusercontent.com/author.jpg"
        pd[178] = [["(512) 999-8888"]]
        pd[203] = [
            [
                ["Wednesday", 3, [2026, 7, 1], [["9AM-5PM", [[9], [17]]]], 0, 1],
            ],
            None,
            3,
        ]
        pd[183] = [None, ["East Austin", "123 Main St", None, "Austin", "78701", "Texas", "US"]]
        pd[72] = [[None, [None, None, None, None, None, None, ["https://example.com/thumb.jpg"]]]]
        pd[57] = [None, "Detail Test Shop (Owner)", "123456789"]
        return [None] * 6 + [pd]  # data[6] = place data

    def test_parse_basic(self):
        raw = self._make_details_response()
        p = parse_place_details_response(raw)
        assert p is not None
        assert p.name == "Detail Test Shop"
        assert p.place_id == "ChIJDetail123"
        assert p.rating == 4.9
        assert p.latitude == 30.265
        assert p.longitude == -97.732
        assert p.phone == "(512) 999-8888"

    def test_parse_hours(self):
        raw = self._make_details_response()
        p = parse_place_details_response(raw)
        assert "Wednesday" in p.hours
        assert "9AM-5PM" in p.hours["Wednesday"]

    def test_parse_structured_address(self):
        raw = self._make_details_response()
        p = parse_place_details_response(raw)
        assert p.borough == "East Austin"
        assert p.city == "Austin"
        assert p.postal_code == "78701"
        assert p.state == "Texas"
        assert p.country == "US"

    def test_parse_thumbnail(self):
        raw = self._make_details_response()
        p = parse_place_details_response(raw)
        assert p.thumbnail == "https://example.com/thumb.jpg"

    def test_parse_owner(self):
        raw = self._make_details_response()
        p = parse_place_details_response(raw)
        assert p.owner["name"] == "Detail Test Shop (Owner)"

    def test_parse_returns_none_on_invalid(self):
        assert parse_place_details_response(None) is None
        assert parse_place_details_response([]) is None
        assert parse_place_details_response([1, 2, 3]) is None
