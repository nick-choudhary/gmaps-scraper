"""Response parser for Google Maps search results.

Parses the deeply-nested JSON array structure returned by Google Maps
internal endpoints. Field indices discovered through reverse-engineering
(see docs/rpc-reference.md for full field map).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ParsedPlace:
    """A place extracted from Google Maps with production-grade grouped fields."""

    # -- Identifiers --
    name: str = ""
    place_id: str = ""
    hex_id: str = ""
    ftid: str = ""
    data_id: str = ""
    cid: str = ""

    # -- Contact --
    phone: str = ""
    website: str = ""
    google_maps_url: str = ""
    plus_code: str = ""
    emails: list[str] = field(default_factory=list)  # from website extraction
    social_links: dict[str, str] = field(default_factory=dict)  # platform -> URL

    # -- Address --
    address: str = ""
    street: str = ""
    city: str = ""
    state: str = ""
    postal_code: str = ""
    country: str = ""
    borough: str = ""
    neighborhood: str = ""

    # -- Ratings --
    rating: float | None = None
    review_count: int = 0
    reviews_per_rating: dict[str, int] = field(default_factory=dict)
    reviews_link: str = ""
    price_range: str = ""
    price_level: int | None = None

    # -- Location --
    latitude: float | None = None
    longitude: float | None = None

    # -- Business details --
    categories: list[str] = field(default_factory=list)
    hours: dict[str, list[str]] = field(default_factory=dict)
    popular_times: dict[str, dict[str, int]] = field(default_factory=dict)
    timezone: str = ""
    status: str = ""
    description: str = ""

    # -- Media --
    photos: list[dict[str, Any]] = field(default_factory=list)
    images: list[dict[str, str]] = field(default_factory=list)
    thumbnail: str = ""
    street_view_url: str = ""
    author_photo: str = ""

    # -- Amenities & links --
    about: list[dict[str, Any]] = field(default_factory=list)
    credit_cards: list[str] = field(default_factory=list)
    reservations: list[dict[str, str]] = field(default_factory=list)
    order_online: list[dict[str, str]] = field(default_factory=list)
    menu: dict[str, str] = field(default_factory=dict)
    owner: dict[str, str] = field(default_factory=dict)
    quick_amenities: list[str] = field(default_factory=list)  # fast preview from [88]

    # -- Meta --
    is_ad: bool = False
    search_token: str = ""  # session token from [1]
    raw: Any = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to grouped JSON with no empty values."""

        def clean(d: dict) -> dict:
            return {k: v for k, v in d.items() if v not in (None, "", [], {}, 0)}

        result: dict[str, Any] = {}
        for k in ("name", "place_id", "hex_id", "ftid", "data_id", "cid"):
            if getattr(self, k):
                result[k] = getattr(self, k)

        for group, keys in [
            (
                "contact",
                ["phone", "website", "google_maps_url", "plus_code", "emails", "social_links"],
            ),
            (
                "address",
                [
                    "address",
                    "street",
                    "city",
                    "state",
                    "postal_code",
                    "country",
                    "borough",
                    "neighborhood",
                ],
            ),
            (
                "rating",
                [
                    "rating",
                    "review_count",
                    "reviews_per_rating",
                    "reviews_link",
                    "price_range",
                    "price_level",
                ],
            ),
            ("location", ["latitude", "longitude"]),
            (
                "business",
                [
                    "categories",
                    "hours",
                    "popular_times",
                    "timezone",
                    "status",
                    "description",
                    "quick_amenities",
                ],
            ),
            ("media", ["photos", "images", "thumbnail", "street_view_url", "author_photo"]),
            (
                "amenities",
                ["about", "credit_cards", "reservations", "order_online", "menu", "owner"],
            ),
        ]:
            sub = {}
            for k in keys:
                v = getattr(self, k)
                if v not in (None, "", [], {}, 0):
                    sub[k] = v
            if sub:
                # Map 'address' key to 'full' in the address group
                if group == "address" and "address" in sub:
                    sub["full"] = sub.pop("address")
                result[group] = sub

        if self.is_ad:
            result["is_ad"] = True
        return result


# Field index constants — reverse-engineered from network traffic
# Format: data[0][1][i] where i is the position in the results array
# Each result entry is accessed via data[0][1][i][14]

# Search result field indices (relative to result entry at index 14)
F_DATA_ID = 0
F_SEARCH_TOKEN = 1
F_ADDRESS_PARTS = 2
F_RATING_DATA = 4
F_IS_AD = 6
F_WEBSITE = 7
F_COORDS = 9
F_HEX_ID = 10
F_NAME = 11  # Business name string
F_CATEGORIES = 13
F_NEIGHBORHOOD = 14
F_ADDRESS = 18  # Full address string
F_TIMEZONE = 30
F_DESCRIPTION = 32
F_HOURS = 34  # Opening hours data (old format)
F_PHOTOS = 36  # Photo references
F_MENU = 38
F_RESERVATIONS = 46
F_OWNER = 57
F_REVIEWS = 62
F_ORDER_ONLINE = 75
F_PLACE_ID = 78  # Google Maps place_id
F_QUICK_AMENITIES = 88
F_FTID = 89  # Feature tracking ID
F_ABOUT = 100
F_PRICE = 116  # Price level (1-4)
F_AUTHOR_PHOTO = 157
F_IMAGES = 171
F_REVIEWS_PER_RATING = 175
F_PHONE_DATA = 178  # Sub-array with phone info
F_COMPLETE_ADDRESS = 183
F_HOURS_NEW = 203  # Opening hours data (new format, Jan 2025+)
F_THUMBNAIL = 72

# Child indices and reusable field paths. These are kept beside the top-level
# indices so an upstream response-shape change has one edit point.
WEBSITE_URL_INDEX = 0
PHONE_CONTAINER_INDEX = 0
PHONE_VALUE_INDEX = 0
PLUS_CODE_CONTAINER_INDEX = 2
PLUS_CODE_VALUE_CONTAINER_INDEX = 2
PLUS_CODE_VALUE_INDEX = 0
ADDRESS_STREET_INDEX = 0
ADDRESS_CITY_INDEX = 1
COORD_LAT_INDEX = 2
COORD_LNG_INDEX = 3
DESCRIPTION_CONTAINER_INDEX = 1
DESCRIPTION_TEXT_INDEX = 1
LINK_URL_INDEX = 0
LINK_SOURCE_INDEX = 1
OWNER_ID_INDEX = 0
OWNER_NAME_INDEX = 1
OWNER_LINK_INDEX = 2
RATING_VALUE_INDEX = 7
RATING_REVIEW_COUNT_INDEX = 8
RATING_PRICE_RANGE_INDEX = 2
RATING_REVIEWS_LINK_CONTAINER_INDEX = 3
RATING_REVIEWS_LINK_INDEX = 0
REVIEWS_PER_RATING_COUNTS_INDEX = 3
COMPLETE_ADDRESS_PARTS_INDEX = 1
HOURS_DAYS_INDEX = 0
HOURS_DAY_NAME_INDEX = 0
HOURS_TIME_BLOCKS_INDEX = 3
HOURS_TIME_VALUE_INDEX = 0
PHOTO_REFERENCE_INDEX = 0
PHOTO_URL_INDEX = 1
IMAGE_CATEGORY_INDEX = 2
IMAGE_PHOTO_LIST_INDEX = 3
IMAGE_PHOTO_DATA_INDEX = 6
IMAGE_URL_INDEX = 0
THUMBNAIL_COLLECTION_INDEX = 0
THUMBNAIL_PHOTO_DATA_INDEX = 6
THUMBNAIL_URL_INDEX = 0
ABOUT_SECTIONS_INDEX = 1
ABOUT_SECTION_NAME_INDEX = 0
ABOUT_SECTION_ITEMS_INDEX = 1
ABOUT_OPTION_NAME_INDEX = 0
ABOUT_OPTION_ENABLED_INDEX = 1

F_RATING = (F_RATING_DATA, RATING_VALUE_INDEX)  # Rating value (0.0-5.0)
F_REVIEW_COUNT = (F_RATING_DATA, RATING_REVIEW_COUNT_INDEX)  # Number of reviews
F_LAT = (F_COORDS, COORD_LAT_INDEX)  # Latitude
F_LNG = (F_COORDS, COORD_LNG_INDEX)  # Longitude
F_PHONE = (F_PHONE_DATA, PHONE_CONTAINER_INDEX, PHONE_VALUE_INDEX)  # Phone number
F_PLUS_CODE = (
    F_COMPLETE_ADDRESS,
    PLUS_CODE_CONTAINER_INDEX,
    PLUS_CODE_VALUE_CONTAINER_INDEX,
    PLUS_CODE_VALUE_INDEX,
)
F_WEBSITE_PATH = (F_WEBSITE, WEBSITE_URL_INDEX)
F_DESCRIPTION_PATH = (F_DESCRIPTION, DESCRIPTION_CONTAINER_INDEX, DESCRIPTION_TEXT_INDEX)

# Response envelope indices. Keep these named because Google can change the
# outer response shape independently of the place-data fields above.
SEARCH_ENVELOPE_INDEX = 0
SEARCH_RESULTS_INDEX = 1
SEARCH_FIRST_BUSINESS_INDEX = 1
SEARCH_ENTRY_PLACE_DATA_INDEX = 14
DETAILS_PLACE_DATA_INDEX = 6

# Reviews response and entry indices.
REVIEWS_COLLECTION_INDEX = 2
REVIEWS_PAGINATION_INDEX = 1
REVIEWS_NEXT_TOKEN_INDEX = 1
REVIEW_ID_INDEX = 0
REVIEW_AUTHOR_INDEX = 1
REVIEW_CONTENT_INDEX = 2
REVIEW_TIMESTAMP_INDEX = 3
REVIEW_AUTHOR_PROFILE_INDEX = 4
REVIEW_AUTHOR_NAME_CONTAINER_INDEX = 5
REVIEW_AUTHOR_NAME_INDEX = 0
REVIEW_RATING_CONTAINER_INDEX = 0
REVIEW_RATING_VALUE_INDEX = 0
REVIEW_TEXT_CONTAINER_INDEX = 15
REVIEW_TEXT_VALUE_CONTAINER_INDEX = 0
REVIEW_TEXT_VALUE_INDEX = 0


def parse_search_response(raw_data: Any) -> list[ParsedPlace]:
    """Parse Google Maps search response into structured place objects.

    The search response has this structure:
        data[0][1] → array of result entries
        Each entry: entry[14] → place data

    Args:
        raw_data: Decoded response from anti-XSSI stripping + JSON parse.

    Returns:
        List of ParsedPlace objects.
    """
    places: list[ParsedPlace] = []

    if not isinstance(raw_data, list):
        return places

    try:
        # Navigate to results array: data[0][1]
        results = (
            raw_data[SEARCH_ENVELOPE_INDEX][SEARCH_RESULTS_INDEX]
            if len(raw_data) > SEARCH_ENVELOPE_INDEX
            and isinstance(raw_data[SEARCH_ENVELOPE_INDEX], list)
            else []
        )
    except (IndexError, TypeError) as exc:
        logger.debug("Malformed search result envelope: %s", exc)
        return places

    if not isinstance(results, list) or len(results) < 2:
        return places

    # Skip entry[0] — it's search metadata/suggestion, not a business.
    # Actual businesses start from index 1.
    for idx in range(SEARCH_FIRST_BUSINESS_INDEX, len(results)):
        entry = results[idx]
        if not isinstance(entry, list):
            continue

        place = ParsedPlace(raw=entry)

        # Each business result has place data at index 14
        if len(entry) <= SEARCH_ENTRY_PLACE_DATA_INDEX:
            continue

        place_data = entry[SEARCH_ENTRY_PLACE_DATA_INDEX]
        if not isinstance(place_data, list):
            continue

        # Extract identifiers
        place.name = _safe_str(place_data, F_NAME)
        place.place_id = _safe_str(place_data, F_PLACE_ID)
        place.hex_id = _safe_str(place_data, F_HEX_ID)
        place.ftid = _safe_str(place_data, F_FTID)
        place.data_id = _safe_str(place_data, F_DATA_ID)
        place.cid = _safe_str(place_data, F_HEX_ID)
        place.search_token = _safe_str(place_data, F_SEARCH_TOKEN)

        # Contact
        place.phone = _safe_deep_str(place_data, *F_PHONE)
        place.website = _safe_deep_str(place_data, *F_WEBSITE_PATH)
        if place.place_id:
            place.google_maps_url = (
                f"https://www.google.com/maps/place/?q=place_id:{place.place_id}"
            )

        # Address
        place.address = _safe_str(place_data, F_ADDRESS)
        place.neighborhood = _safe_str(place_data, F_NEIGHBORHOOD)
        addr_parts = _safe_list(place_data, F_ADDRESS_PARTS)
        if addr_parts:
            if len(addr_parts) > ADDRESS_STREET_INDEX:
                place.street = (
                    str(addr_parts[ADDRESS_STREET_INDEX])
                    if addr_parts[ADDRESS_STREET_INDEX]
                    else ""
                )
            if len(addr_parts) > ADDRESS_CITY_INDEX:
                place.city = (
                    str(addr_parts[ADDRESS_CITY_INDEX]) if addr_parts[ADDRESS_CITY_INDEX] else ""
                )
        _extract_complete_address_new(place_data, place)

        # Rating
        _extract_rating_new(place_data, place)

        # Location
        coords = _safe_list(place_data, F_COORDS)
        if coords and len(coords) > COORD_LNG_INDEX:
            place.latitude = _safe_float(coords, COORD_LAT_INDEX)
            place.longitude = _safe_float(coords, COORD_LNG_INDEX)

        # Categories
        cats = _safe_list(place_data, F_CATEGORIES)
        if cats:
            place.categories = [str(c) for c in cats if c]

        # Hours
        _extract_hours_new(place_data, place)

        # Business details
        place.timezone = _safe_str(place_data, F_TIMEZONE)
        place.status = _safe_str(place_data, F_HOURS)
        desc = _safe_list(place_data, F_DESCRIPTION)
        if desc and len(desc) > DESCRIPTION_CONTAINER_INDEX:
            description_data = desc[DESCRIPTION_CONTAINER_INDEX]
            if (
                isinstance(description_data, list)
                and len(description_data) > DESCRIPTION_TEXT_INDEX
                and description_data[DESCRIPTION_TEXT_INDEX]
            ):
                place.description = str(description_data[DESCRIPTION_TEXT_INDEX])

        # Media
        _extract_media_new(place_data, place)
        place.author_photo = _safe_str(place_data, F_AUTHOR_PHOTO)

        # Amenities
        _extract_about_new(place_data, place)
        # Quick amenities from [88]
        qa = _safe_list(place_data, F_QUICK_AMENITIES)
        if qa:
            place.quick_amenities = [str(x) for x in qa if isinstance(x, str)]

        # Ad flag
        place.is_ad = bool(_safe_bool(place_data, F_IS_AD))

        places.append(place)

    return places


def parse_place_details_response(raw_data: Any) -> ParsedPlace | None:
    """Parse Google Maps place details response.

    Place details have this structure:
        data[6] -> main place data (same format as entry[14] in search)

    With scraped cookies, this returns less data than logged-in cookies
    but still contains all essential fields.
    """
    if not isinstance(raw_data, list) or len(raw_data) <= DETAILS_PLACE_DATA_INDEX:
        return None

    place_data = raw_data[DETAILS_PLACE_DATA_INDEX]
    if not isinstance(place_data, list):
        return None

    place = ParsedPlace(raw=raw_data)

    # Reuse the same extraction helpers from search parsing
    place.name = _safe_str(place_data, F_NAME)
    place.place_id = _safe_str(place_data, F_PLACE_ID)
    place.hex_id = _safe_str(place_data, F_HEX_ID)
    place.ftid = _safe_str(place_data, F_FTID)
    place.data_id = _safe_str(place_data, F_DATA_ID)
    place.cid = _safe_str(place_data, F_HEX_ID)

    # Contact
    place.phone = _safe_deep_str(place_data, *F_PHONE)
    if place.place_id:
        place.google_maps_url = f"https://www.google.com/maps/place/?q=place_id:{place.place_id}"
    # Plus code from [183]
    place.plus_code = _safe_deep_str(place_data, *F_PLUS_CODE)

    # Address
    place.address = _safe_str(place_data, F_ADDRESS)
    place.neighborhood = _safe_str(place_data, F_NEIGHBORHOOD)
    addr_parts = _safe_list(place_data, F_ADDRESS_PARTS)
    if addr_parts:
        if len(addr_parts) > ADDRESS_STREET_INDEX:
            place.street = (
                str(addr_parts[ADDRESS_STREET_INDEX]) if addr_parts[ADDRESS_STREET_INDEX] else ""
            )
        if len(addr_parts) > ADDRESS_CITY_INDEX:
            place.city = (
                str(addr_parts[ADDRESS_CITY_INDEX]) if addr_parts[ADDRESS_CITY_INDEX] else ""
            )
    _extract_complete_address_new(place_data, place)

    # Rating (place details format may differ from search)
    _extract_rating_new(place_data, place)

    # Location
    coords = _safe_list(place_data, F_COORDS)
    if coords and len(coords) > COORD_LNG_INDEX:
        place.latitude = _safe_float(coords, COORD_LAT_INDEX)
        place.longitude = _safe_float(coords, COORD_LNG_INDEX)

    # Categories
    cats = _safe_list(place_data, F_CATEGORIES)
    if cats:
        place.categories = [str(c) for c in cats if c]

    # Hours
    _extract_hours_new(place_data, place)

    # Business details
    place.timezone = _safe_str(place_data, F_TIMEZONE)
    place.description = _safe_deep_str(place_data, *F_DESCRIPTION_PATH)

    # Media
    _extract_media_new(place_data, place)
    place.author_photo = _safe_str(place_data, F_AUTHOR_PHOTO)

    # Amenities
    _extract_about_new(place_data, place)
    qa = _safe_list(place_data, F_QUICK_AMENITIES)
    if qa:
        place.quick_amenities = [str(x) for x in qa if isinstance(x, str)]

    # Links (reservations, order, menu, owner)
    res = _safe_list(place_data, F_RESERVATIONS)
    if res:
        for r in res:
            if isinstance(r, list) and len(r) > LINK_SOURCE_INDEX:
                place.reservations.append(
                    {
                        "link": str(r[LINK_URL_INDEX]) if r[LINK_URL_INDEX] else "",
                        "source": str(r[LINK_SOURCE_INDEX]) if r[LINK_SOURCE_INDEX] else "",
                    }
                )

    oo = _safe_list(place_data, F_ORDER_ONLINE)
    if oo:
        for item in oo:
            if isinstance(item, list) and len(item) > LINK_SOURCE_INDEX:
                place.order_online.append(
                    {
                        "link": str(item[LINK_URL_INDEX]) if item[LINK_URL_INDEX] else "",
                        "source": str(item[LINK_SOURCE_INDEX]) if item[LINK_SOURCE_INDEX] else "",
                    }
                )

    menu = _safe_list(place_data, F_MENU)
    if menu and len(menu) > LINK_SOURCE_INDEX:
        place.menu = {
            "link": str(menu[LINK_URL_INDEX]) if menu[LINK_URL_INDEX] else "",
            "source": str(menu[LINK_SOURCE_INDEX]) if menu[LINK_SOURCE_INDEX] else "",
        }

    owner = _safe_list(place_data, F_OWNER)
    if owner and len(owner) > OWNER_LINK_INDEX:
        place.owner = {
            "id": str(owner[OWNER_ID_INDEX]) if owner[OWNER_ID_INDEX] else "",
            "name": str(owner[OWNER_NAME_INDEX]) if owner[OWNER_NAME_INDEX] else "",
            "link": str(owner[OWNER_LINK_INDEX]) if owner[OWNER_LINK_INDEX] else "",
        }

    return place


def parse_reviews_response(raw_data: Any) -> tuple[list[dict[str, Any]], str | None]:
    """Parse reviews from /rpc/listugcposts response.

    Args:
        raw_data: Decoded response.

    Returns:
        Tuple of (reviews list, next_page_token or None).
    """
    reviews: list[dict[str, Any]] = []
    next_token: str | None = None

    if not isinstance(raw_data, list):
        return reviews, next_token

    try:
        # Reviews array at index 2
        reviews_data = (
            raw_data[REVIEWS_COLLECTION_INDEX] if len(raw_data) > REVIEWS_COLLECTION_INDEX else []
        )
        if isinstance(reviews_data, list):
            for review_entry in reviews_data:
                if not isinstance(review_entry, list):
                    continue
                review = _parse_single_review(review_entry)
                if review:
                    reviews.append(review)
    except (IndexError, TypeError) as exc:
        logger.debug("Malformed reviews collection: %s", exc)

    # Pagination token at index 1
    try:
        token_data = raw_data[REVIEWS_PAGINATION_INDEX]
        if isinstance(token_data, list) and len(token_data) > REVIEWS_NEXT_TOKEN_INDEX:
            next_token = (
                str(token_data[REVIEWS_NEXT_TOKEN_INDEX])
                if token_data[REVIEWS_NEXT_TOKEN_INDEX]
                else None
            )
    except (IndexError, TypeError) as exc:
        logger.debug("Malformed review pagination token: %s", exc)

    return reviews, next_token


def _parse_single_review(entry: list[Any]) -> dict[str, Any] | None:
    """Parse a single review entry from the response."""
    try:
        review_id = ""
        author_name = "Anonymous"
        author_photo = ""
        rating = 0
        text = ""
        timestamp = ""

        # Review ID at entry[0]
        if len(entry) > REVIEW_ID_INDEX and entry[REVIEW_ID_INDEX]:
            review_id = str(entry[REVIEW_ID_INDEX])

        # Author info at entry[1]
        if len(entry) > REVIEW_AUTHOR_INDEX and isinstance(entry[REVIEW_AUTHOR_INDEX], list):
            author_data = entry[REVIEW_AUTHOR_INDEX]
            # author_data[4][5][0] → author name
            if len(author_data) > REVIEW_AUTHOR_PROFILE_INDEX and isinstance(
                author_data[REVIEW_AUTHOR_PROFILE_INDEX], list
            ):
                name_data = author_data[REVIEW_AUTHOR_PROFILE_INDEX]
                if (
                    len(name_data) > REVIEW_AUTHOR_NAME_CONTAINER_INDEX
                    and isinstance(name_data[REVIEW_AUTHOR_NAME_CONTAINER_INDEX], list)
                    and name_data[REVIEW_AUTHOR_NAME_CONTAINER_INDEX]
                ):
                    author_name = str(
                        name_data[REVIEW_AUTHOR_NAME_CONTAINER_INDEX][REVIEW_AUTHOR_NAME_INDEX]
                    )

        # Rating at entry[2][0][0]
        if len(entry) > REVIEW_CONTENT_INDEX and isinstance(entry[REVIEW_CONTENT_INDEX], list):
            rating_data = entry[REVIEW_CONTENT_INDEX]
            if (
                rating_data
                and isinstance(rating_data[REVIEW_RATING_CONTAINER_INDEX], list)
                and rating_data[REVIEW_RATING_CONTAINER_INDEX]
            ):
                try:
                    rating = int(
                        rating_data[REVIEW_RATING_CONTAINER_INDEX][REVIEW_RATING_VALUE_INDEX]
                    )
                except (ValueError, TypeError) as exc:
                    logger.debug("Invalid inline review rating: %s", exc)

            # Review text at entry[2][15][0][0]
            if len(rating_data) > REVIEW_TEXT_CONTAINER_INDEX and isinstance(
                rating_data[REVIEW_TEXT_CONTAINER_INDEX], list
            ):
                text_data = rating_data[REVIEW_TEXT_CONTAINER_INDEX]
                if (
                    text_data
                    and isinstance(text_data[REVIEW_TEXT_VALUE_CONTAINER_INDEX], list)
                    and text_data[REVIEW_TEXT_VALUE_CONTAINER_INDEX]
                ):
                    text = str(
                        text_data[REVIEW_TEXT_VALUE_CONTAINER_INDEX][REVIEW_TEXT_VALUE_INDEX]
                    )

        # Timestamp at entry[3]
        if len(entry) > REVIEW_TIMESTAMP_INDEX and entry[REVIEW_TIMESTAMP_INDEX]:
            timestamp = str(entry[REVIEW_TIMESTAMP_INDEX])

        if not text and not author_name:
            return None

        return {
            "review_id": review_id,
            "author_name": author_name,
            "author_photo": author_photo,
            "rating": rating,
            "text": text,
            "timestamp": timestamp,
        }

    except (IndexError, TypeError) as exc:
        logger.debug("Malformed inline review entry: %s", exc)
        return None


# ── Safe accessors for nested lists ──


def _safe_str(data: list[Any], index: int, default: str = "") -> str:
    """Safely get a string from a list at given index."""
    try:
        if index < len(data) and data[index] is not None:
            return str(data[index])
    except (IndexError, TypeError) as exc:
        logger.debug("Invalid string field %d: %s", index, exc)
    return default


def _safe_int(data: list[Any], index: int, default: int = 0) -> int:
    """Safely get an int from a list."""
    try:
        if index < len(data) and data[index] is not None:
            return int(data[index])
    except (IndexError, TypeError, ValueError) as exc:
        logger.debug("Invalid integer field %d: %s", index, exc)
    return default


def _safe_float(data: list[Any], index: int, default: float | None = None) -> float | None:
    """Safely get a float from a list."""
    try:
        if index < len(data) and data[index] is not None:
            return float(data[index])
    except (IndexError, TypeError, ValueError) as exc:
        logger.debug("Invalid float field %d: %s", index, exc)
    return default


def _safe_list(data: list[Any], index: int) -> list[Any] | None:
    """Safely get a sub-list."""
    try:
        if index < len(data) and isinstance(data[index], list):
            return data[index]
    except (IndexError, TypeError) as exc:
        logger.debug("Invalid list field %d: %s", index, exc)
    return None


def _safe_bool(data: list[Any], index: int, default: bool = False) -> bool:
    """Safely get a boolean from a list."""
    try:
        if index < len(data):
            val = data[index]
            if isinstance(val, bool):
                return val
            if isinstance(val, int):
                return bool(val)
    except (IndexError, TypeError) as exc:
        logger.debug("Invalid boolean field %d: %s", index, exc)
    return default


def _safe_deep_str(data: list[Any], *indices: int, default: str = "") -> str:
    """Navigate nested lists to fetch a string value."""
    try:
        current: Any = data
        for idx in indices:
            if not isinstance(current, list) or idx >= len(current):
                return default
            current = current[idx]
        return str(current) if current is not None else default
    except (IndexError, TypeError) as exc:
        logger.debug("Invalid nested field path %s: %s", indices, exc)
        return default


def _extract_rating_new(pd: list[Any], place: ParsedPlace) -> None:
    """Extract all rating-related fields from [4] and [116], [175]."""
    rd = _safe_list(pd, F_RATING_DATA)
    if not rd:
        return
    if len(rd) > RATING_VALUE_INDEX and rd[RATING_VALUE_INDEX] is not None:
        try:
            place.rating = float(rd[RATING_VALUE_INDEX])
        except (ValueError, TypeError) as exc:
            logger.debug("Invalid rating at field %s: %s", F_RATING, exc)
    if len(rd) > RATING_REVIEW_COUNT_INDEX and rd[RATING_REVIEW_COUNT_INDEX] is not None:
        try:
            place.review_count = int(float(rd[RATING_REVIEW_COUNT_INDEX]))
        except (ValueError, TypeError) as exc:
            logger.debug("Invalid review count at field %s: %s", F_REVIEW_COUNT, exc)
    if len(rd) > RATING_PRICE_RANGE_INDEX and rd[RATING_PRICE_RANGE_INDEX]:
        place.price_range = str(rd[RATING_PRICE_RANGE_INDEX])
    if (
        len(rd) > RATING_REVIEWS_LINK_CONTAINER_INDEX
        and isinstance(rd[RATING_REVIEWS_LINK_CONTAINER_INDEX], list)
        and rd[RATING_REVIEWS_LINK_CONTAINER_INDEX]
        and rd[RATING_REVIEWS_LINK_CONTAINER_INDEX][RATING_REVIEWS_LINK_INDEX]
    ):
        place.reviews_link = str(rd[RATING_REVIEWS_LINK_CONTAINER_INDEX][RATING_REVIEWS_LINK_INDEX])
    if len(pd) > F_PRICE and pd[F_PRICE] is not None:
        try:
            place.price_level = int(pd[F_PRICE])
        except (ValueError, TypeError) as exc:
            logger.debug("Invalid price level at field %d: %s", F_PRICE, exc)
    # Reviews per rating at [175][3]
    rpr = _safe_list(pd, F_REVIEWS_PER_RATING)
    if (
        rpr
        and len(rpr) > REVIEWS_PER_RATING_COUNTS_INDEX
        and isinstance(rpr[REVIEWS_PER_RATING_COUNTS_INDEX], list)
    ):
        review_counts = rpr[REVIEWS_PER_RATING_COUNTS_INDEX]
        for star in range(1, 6):
            if star - 1 < len(review_counts) and review_counts[star - 1] is not None:
                try:
                    v = int(float(review_counts[star - 1]))
                    if v > 0:
                        place.reviews_per_rating[str(star)] = v
                except (ValueError, TypeError) as exc:
                    logger.debug("Invalid per-star review count for star %d: %s", star, exc)


def _extract_complete_address_new(pd: list[Any], place: ParsedPlace) -> None:
    """Extract structured address from [183][1] (7 components)."""
    ca = _safe_list(pd, F_COMPLETE_ADDRESS)
    if not ca or len(ca) <= COMPLETE_ADDRESS_PARTS_INDEX:
        return
    parts = ca[COMPLETE_ADDRESS_PARTS_INDEX]
    if not isinstance(parts, list):
        return
    keys = ["borough", "street", "_unused", "city", "postal_code", "state", "country"]
    for i, key in enumerate(keys):
        if i < len(parts) and parts[i] and key != "_unused":
            setattr(place, key, str(parts[i]))


def _extract_hours_new(pd: list[Any], place: ParsedPlace) -> None:
    """Extract structured opening hours.

    Format at [203]: [
        [  # [203][0] = detailed hours
            ['Wednesday', 3, [2026,7,1], [['8 AM-4 PM', [[8],[16]]]], 0, 1],
            ['Thursday', 4, [2026,7,2], [['8 AM-4 PM', [[8],[16]]]], 0, 1],
            ...
        ],
        [  # [203][1] = status info (open/closed text)
            ...
        ]
    ]
    """
    hn = _safe_list(pd, F_HOURS_NEW)
    if hn and len(hn) > HOURS_DAYS_INDEX and isinstance(hn[HOURS_DAYS_INDEX], list):
        # [203][0] contains the per-day hours
        days = hn[HOURS_DAYS_INDEX]
        for day_entry in days:
            if not isinstance(day_entry, list) or len(day_entry) <= HOURS_TIME_BLOCKS_INDEX:
                continue
            day_name = (
                str(day_entry[HOURS_DAY_NAME_INDEX]) if day_entry[HOURS_DAY_NAME_INDEX] else ""
            )
            time_blocks = day_entry[HOURS_TIME_BLOCKS_INDEX]
            if not isinstance(time_blocks, list):
                continue
            for tb in time_blocks:
                if (
                    isinstance(tb, list)
                    and len(tb) > HOURS_TIME_VALUE_INDEX
                    and tb[HOURS_TIME_VALUE_INDEX]
                ):
                    time_str = str(tb[HOURS_TIME_VALUE_INDEX])
                    if day_name and time_str:
                        place.hours.setdefault(day_name, []).append(time_str)
        return
    # Old format fallback at [34]
    ho = _safe_list(pd, F_HOURS)
    if ho:
        place.hours["raw"] = [str(h) for h in ho if h]


def _extract_media_new(pd: list[Any], place: ParsedPlace) -> None:
    """Extract photos, images, thumbnail, street_view.

    Photos at [36]: list of [ref, url] pairs
    Images at [171]: list of category blocks (only with login cookies)
    Thumbnail at [72][0][j][6][0]: photo URLs
    """
    # Photos at [36]
    ph = _safe_list(pd, F_PHOTOS)
    if ph:
        for p in ph:
            if isinstance(p, list) and len(p) > PHOTO_URL_INDEX:
                place.photos.append(
                    {
                        "reference": (
                            str(p[PHOTO_REFERENCE_INDEX]) if p[PHOTO_REFERENCE_INDEX] else ""
                        ),
                        "url": str(p[PHOTO_URL_INDEX]) if p[PHOTO_URL_INDEX] else "",
                    }
                )

    # Images at [171] — only present with login cookies
    im = _safe_list(pd, F_IMAGES)
    if im:
        for img_block in im:
            if not isinstance(img_block, list):
                continue
            # Each img_block is a category like ['CgIgAQ==', token, 'All', [[photo_data, ...]]]
            category_name = (
                str(img_block[IMAGE_CATEGORY_INDEX])
                if len(img_block) > IMAGE_CATEGORY_INDEX and img_block[IMAGE_CATEGORY_INDEX]
                else ""
            )
            photo_list = (
                img_block[IMAGE_PHOTO_LIST_INDEX]
                if len(img_block) > IMAGE_PHOTO_LIST_INDEX
                else None
            )
            if isinstance(photo_list, list):
                for photo in photo_list:
                    if isinstance(photo, list) and len(photo) > IMAGE_PHOTO_DATA_INDEX:
                        photo_data = photo[IMAGE_PHOTO_DATA_INDEX]
                        if (
                            isinstance(photo_data, list)
                            and len(photo_data) > IMAGE_URL_INDEX
                            and photo_data[IMAGE_URL_INDEX]
                        ):
                            url = str(photo_data[IMAGE_URL_INDEX])
                            if url.startswith("http"):
                                place.images.append(
                                    {
                                        "title": category_name,
                                        "url": url,
                                    }
                                )
                                if "street view" in category_name.lower():
                                    place.street_view_url = url

    # Thumbnail at [72][0][*][6][0]
    th = _safe_list(pd, F_THUMBNAIL)
    if th and isinstance(th[THUMBNAIL_COLLECTION_INDEX], list):
        for item in th[THUMBNAIL_COLLECTION_INDEX]:
            if (
                isinstance(item, list)
                and len(item) > THUMBNAIL_PHOTO_DATA_INDEX
                and isinstance(item[THUMBNAIL_PHOTO_DATA_INDEX], list)
                and item[THUMBNAIL_PHOTO_DATA_INDEX]
                and item[THUMBNAIL_PHOTO_DATA_INDEX][THUMBNAIL_URL_INDEX]
            ):
                url = str(item[THUMBNAIL_PHOTO_DATA_INDEX][THUMBNAIL_URL_INDEX])
                if url.startswith("http") and not place.thumbnail:
                    place.thumbnail = url


def _extract_about_new(pd: list[Any], place: ParsedPlace) -> None:
    """Extract about/amenities/credit cards from [100]."""
    ab = _safe_list(pd, F_ABOUT)
    if not ab or len(ab) <= ABOUT_SECTIONS_INDEX:
        return
    sections = ab[ABOUT_SECTIONS_INDEX]
    if not isinstance(sections, list):
        return
    for section in sections:
        if not isinstance(section, list) or len(section) <= ABOUT_SECTION_ITEMS_INDEX:
            continue
        name = str(section[ABOUT_SECTION_NAME_INDEX]) if section[ABOUT_SECTION_NAME_INDEX] else ""
        items = section[ABOUT_SECTION_ITEMS_INDEX]
        if name == "Credit cards" and isinstance(items, list):
            place.credit_cards = [str(c) for c in items if c]
            continue
        if isinstance(items, list):
            options = []
            for opt in items:
                if isinstance(opt, list) and len(opt) > ABOUT_OPTION_ENABLED_INDEX:
                    options.append(
                        {
                            "name": (
                                str(opt[ABOUT_OPTION_NAME_INDEX])
                                if opt[ABOUT_OPTION_NAME_INDEX]
                                else ""
                            ),
                            "enabled": bool(opt[ABOUT_OPTION_ENABLED_INDEX]),
                        }
                    )
            if options:
                place.about.append({"name": name, "options": options})
