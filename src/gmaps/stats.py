"""Progress tracking and observability for scraping operations.

Provides structured stats, error collection, and progress reporting.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ScraperStats:
    """Accumulates metrics during a scraping run.

    Pass to grid_search or use manually for any batch operation.
    Call summary() at the end for a printable report.
    """

    # Counters
    total_requests: int = 0
    successful: int = 0
    failed: int = 0
    rate_limited: int = 0
    parse_errors: int = 0
    network_errors: int = 0

    # Results
    total_places: int = 0
    unique_places: int = 0
    enriched: int = 0

    # Timing
    start_time: float = field(default_factory=time.monotonic)
    end_time: float | None = None
    _seen_ids: set[str] = field(default_factory=set)

    # Error log
    errors: list[dict[str, Any]] = field(default_factory=list)

    @property
    def elapsed_seconds(self) -> float:
        end = self.end_time or time.monotonic()
        return end - self.start_time

    @property
    def success_rate(self) -> float:
        total = self.successful + self.failed
        return self.successful / total * 100 if total > 0 else 0.0

    @property
    def places_per_minute(self) -> float:
        elapsed_min = self.elapsed_seconds / 60
        return self.unique_places / elapsed_min if elapsed_min > 0 else 0.0

    def record_request(self) -> None:
        self.total_requests += 1

    def record_success(self, place_count: int = 0) -> None:
        self.successful += 1
        self.total_places += place_count

    def record_unique(self, place_id: str) -> bool:
        """Returns True if place_id is new (not seen before)."""
        if place_id in self._seen_ids:
            return False
        self._seen_ids.add(place_id)
        self.unique_places += 1
        return True

    def record_error(self, error_type: str, message: str, context: dict | None = None) -> None:
        self.failed += 1
        error_cat = error_type.lower()
        if "rate" in error_cat or "429" in error_cat:
            self.rate_limited += 1
        elif "parse" in error_cat:
            self.parse_errors += 1
        elif "network" in error_cat or "timeout" in error_cat or "connect" in error_cat:
            self.network_errors += 1
        self.errors.append(
            {
                "type": error_type,
                "message": message[:200],
                "context": context or {},
                "timestamp": time.time(),
            }
        )

    def progress(self) -> str:
        """One-line progress string for logging."""
        return (
            f"[{self.unique_places} places | {self.total_requests} req | "
            f"{self.failed} errors | {self.places_per_minute:.0f}/min | "
            f"{self.elapsed_seconds:.0f}s]"
        )

    def summary(self) -> str:
        """Full summary report for end of run."""
        self.end_time = time.monotonic()
        lines = [
            "=== SCRAPE SUMMARY ===",
            f"Total places found:  {self.total_places}",
            f"Unique places:       {self.unique_places}",
            f"Enriched:            {self.enriched}",
            "",
            f"Requests:            {self.total_requests}",
            f"Successful:          {self.successful}",
            f"Failed:              {self.failed}",
            f"  Rate limited (429):  {self.rate_limited}",
            f"  Parse errors:        {self.parse_errors}",
            f"  Network errors:      {self.network_errors}",
            "",
            f"Success rate:        {self.success_rate:.1f}%",
            f"Throughput:          {self.places_per_minute:.1f} places/min",
            f"Total time:          {self.elapsed_seconds:.1f}s ({self.elapsed_seconds / 60:.1f} min)",
        ]
        if self.errors:
            lines.append("\nFirst 5 errors:")
            for e in self.errors[:5]:
                lines.append(f"  [{e['type']}] {e['message'][:100]}")
        return "\n".join(lines)
