"""RPC method IDs and URL constants for Google Maps internal API.

Google Maps uses a more complex URL structure than NotebookLM's single
batchexecute endpoint. Different features use different internal endpoints:

- Search: POST to /maps/_/... search-related endpoints
- Place details: POST to /maps/_/... place-related endpoints
- Reviews: POST to paginated endpoints
- Photos: GET to image proxy endpoints
- Directions: POST to /maps/_/... directions endpoints

These constants and method IDs are discovered through network traffic analysis.
"""

from __future__ import annotations

from enum import Enum
from typing import Final

# Base URLs
GMAPS_BASE_URL: Final[str] = "https://www.google.com/maps"
GMAPS_ALT_BASE_URL: Final[str] = "https://maps.google.com/maps"

# Internal data API paths (to be confirmed via traffic capture)
# These are the common patterns observed in Google Maps web app
GMAPS_DATA_PATH: Final[str] = "/_/DataService/DataService"  # Primary data endpoint
GMAPS_SEARCH_PATH: Final[str] = "/search"  # Search results page (HTML with embedded data)
GMAPS_PREVIEW_PATH: Final[str] = "/preview"  # Place preview endpoint

# Derived full URLs
GMAPS_SEARCH_URL: Final[str] = f"{GMAPS_BASE_URL}{GMAPS_SEARCH_PATH}"
GMAPS_PLACE_URL: Final[str] = f"{GMAPS_BASE_URL}{GMAPS_PREVIEW_PATH}"


class RPCMethod(str, Enum):
    """RPC method identifiers for Google Maps operations.

    Google Maps uses a variety of internal endpoints. Unlike NotebookLM's
    consistent 6-char batchexecute IDs, Maps uses:
    - URL path-based routing (/maps/_/pb, /maps/preview/place, etc.)
    - !-encoded parameter strings in URL paths
    - JSON payloads embedded in HTML or returned directly
    - Protobuf payloads for some internal services

    The member names here represent known Maps operations. The actual endpoint
    paths and request formats are documented per-method.
    """

    # Search operations
    SEARCH_PLACES = "search_places"  # Search for places by text query
    SEARCH_NEARBY = "search_nearby"  # Search for places near a location
    AUTOCOMPLETE = "autocomplete"  # Search query autocomplete/suggestions

    # Place operations
    GET_PLACE_DETAILS = "place_details"  # Get detailed info about a place
    GET_PLACE_PHOTOS = "place_photos"  # Get photos for a place
    GET_PLACE_REVIEWS = "place_reviews"  # Get reviews for a place

    # Directions
    GET_DIRECTIONS = "directions"  # Get directions between locations

    # Utility
    GEOCODE = "geocode"  # Convert address to coordinates
    REVERSE_GEOCODE = "reverse_geocode"  # Convert coordinates to address


class SearchType(str, Enum):
    """Types of search queries."""

    TEXT = "text"  # Free-text search
    NEARBY = "nearby"  # Search near a location
    CATEGORY = "category"  # Search by business category
    RESTAURANTS = "restaurants"
    HOTELS = "hotels"
    ATTRACTIONS = "attractions"
    GAS_STATIONS = "gas_stations"
    GROCERY = "grocery"
    SHOPPING = "shopping"


class PlaceField(str, Enum):
    """Fields that can be requested for place details.

    Maps to internal API response structures at various nesting levels.
    """

    NAME = "name"
    ADDRESS = "address"
    PHONE = "phone"
    WEBSITE = "website"
    RATING = "rating"
    REVIEW_COUNT = "review_count"
    HOURS = "hours"
    PRICE_LEVEL = "price_level"
    PHOTOS = "photos"
    REVIEWS = "reviews"
    COORDINATES = "coordinates"
    CATEGORIES = "categories"
    DESCRIPTION = "description"
    POPULAR_TIMES = "popular_times"
    AMENITIES = "amenities"
    GOOGLE_MAPS_URL = "maps_url"
    PLACE_ID = "place_id"
