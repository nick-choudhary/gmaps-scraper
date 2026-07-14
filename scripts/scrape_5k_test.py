"""Live 5,000 business scrape test with stats tracking.

Target: Restaurants in NYC area.
Expected: ~3,000-5,000 unique results, ~5-10 min.
"""

import asyncio
import json
import logging
from pathlib import Path

from gmaps.client import GMapsClient
from gmaps.grid import BoundingBox
from gmaps.stats import ScraperStats

ROOT = Path(__file__).resolve().parents[1]

# Enable info logging to see progress
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


async def main():
    stats = ScraperStats()

    # NYC 5 boroughs — restaurants
    # Using 0.7km cells for good coverage without too many requests
    bbox = BoundingBox(
        min_lat=40.55,  # South Brooklyn
        min_lon=-74.05,  # Staten Island
        max_lat=40.90,  # North Bronx
        max_lon=-73.70,  # Eastern Queens
    )

    print("=== 5K BUSINESS SCRAPE TEST ===")
    print("Query: restaurants in NYC")
    print(f"BBox: {bbox.min_lat},{bbox.min_lon} to {bbox.max_lat},{bbox.max_lon}")
    print("Cell size: 0.7km, Zoom: 16, Max: 5000")
    print()

    async with GMapsClient(min_delay=1.0, jitter_pct=0.4) as client:
        results = await client.search.grid_search(
            query="restaurant",
            bbox=bbox,
            cell_size_km=0.7,
            max_results=5000,
            zoom=16.0,
            detect_exhaustion=True,
            stats=stats,
        )

        places = [p for p, _ in results]

        # Print summary
        print()
        print(stats.summary())
        print()

        # Field coverage
        total = len(places)
        if total > 0:
            print(f"=== FIELD COVERAGE ({total} places) ===")
            fields = {
                "name": sum(1 for p in places if p.name),
                "phone": sum(1 for p in places if p.phone),
                "website": sum(1 for p in places if p.website),
                "rating": sum(1 for p in places if p.rating),
                "review_count": sum(1 for p in places if p.review_count),
                "address": sum(1 for p in places if p.address),
                "categories": sum(1 for p in places if p.categories),
                "lat_lng": sum(1 for p in places if p.latitude),
                "timezone": sum(1 for p in places if p.timezone),
                "neighborhood": sum(1 for p in places if p.neighborhood),
            }
            for f, count in sorted(fields.items(), key=lambda x: -x[1]):
                pct = count * 100 / total
                bar = "#" * int(pct / 5) + "." * (20 - int(pct / 5))
                print(f"  {f:15s} [{bar}] {count}/{total} ({pct:.0f}%)")

        # Save results
        output_path = ROOT / "nyc_restaurants_5k.json"
        data = [p.to_dict() for p in places[:5000]]
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"\nSaved {len(data)} places to {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
