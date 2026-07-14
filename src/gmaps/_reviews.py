"""Updated Reviews API using Google Maps internal /rpc/listugcposts endpoint."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .rpc.parser import parse_reviews_response
from .rpc.pb_encoder import build_reviews_pb

if TYPE_CHECKING:
    from .transport import HTTPTransport

logger = logging.getLogger(__name__)


@dataclass
class Review:
    """A single Google Maps review."""

    review_id: str = ""
    author_name: str = "Anonymous"
    author_photo: str = ""
    rating: int = 0
    text: str = ""
    timestamp: str = ""
    photos: list[str] = field(default_factory=list)
    owner_response: str = ""


@dataclass
class ReviewsResult:
    """Result from a reviews fetch."""

    place_id: str
    reviews: list[dict[str, Any]]
    total_fetched: int
    sort_by: str
    next_page_token: str | None = None
    raw: Any = None


class ReviewsAPI:
    """Fetch reviews using Google Maps internal /rpc/listugcposts endpoint."""

    def __init__(self, transport: HTTPTransport, language: str = "en"):
        self._transport = transport
        self._language = language

    async def list(
        self,
        hex_id: str,
        sort_by: str = "most_relevant",
        max_reviews: int = 50,
    ) -> ReviewsResult:
        """Fetch reviews for a place by its hex ID.

        Args:
            hex_id: Internal hex identifier from place data (e.g., "0x89c2...").
            sort_by: "most_relevant", "newest", "highest_rating", "lowest_rating".
            max_reviews: Maximum reviews to fetch.

        Returns:
            ReviewsResult with parsed review data.
        """
        sort_map = {
            "most_relevant": 0,
            "newest": 1,
            "highest_rating": 2,
            "lowest_rating": 3,
        }
        sort_code = sort_map.get(sort_by, 0)

        all_reviews: list[dict[str, Any]] = []
        page_token: str | None = None
        start_index = 0

        logger.info("Fetching reviews for hex_id=%s", hex_id)

        while len(all_reviews) < max_reviews:
            page_size = min(10, max_reviews - len(all_reviews))

            pb_param = build_reviews_pb(
                feature_id=hex_id,
                sort_by=sort_code,
                start_index=start_index,
                page_size=page_size,
                language=self._language,
            )

            params: dict[str, str] = {
                "authuser": "0",
                "hl": self._language,
                "pb": pb_param,
            }

            raw = await self._transport.get(
                path="/maps/rpc/listugcposts",
                params=params,
                response_type="json",
            )

            batch, next_token = parse_reviews_response(raw)
            all_reviews.extend(batch)

            if not batch or not next_token:
                break

            page_token = next_token
            start_index += len(batch)

            if len(all_reviews) >= max_reviews:
                break

        return ReviewsResult(
            place_id=hex_id,
            reviews=all_reviews[:max_reviews],
            total_fetched=len(all_reviews),
            sort_by=sort_by,
            next_page_token=page_token,
            raw=None,
        )
