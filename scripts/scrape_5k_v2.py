"""5K test with pagination + larger cells + no early exit."""

import asyncio
import json
import logging
from pathlib import Path

from gmaps.client import GMapsClient
from gmaps.grid import BoundingBox
from gmaps.stats import ScraperStats

ROOT = Path(__file__).resolve().parents[1]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
# Reduce httpx noise
logging.getLogger("httpx").setLevel(logging.WARNING)


async def main():
    stats = ScraperStats()

    # NYC 5 boroughs
    bbox = BoundingBox(
        min_lat=40.55,
        min_lon=-74.05,
        max_lat=40.90,
        max_lon=-73.70,
    )

    print("=== 5K SCRAPE TEST (pagination + 1.5km cells) ===")
    print("Query: restaurant, Cell: 1.5km, Zoom: 16, Paginate: True")
    print()

    async with GMapsClient(min_delay=0.8, jitter_pct=0.4) as client:
        results = await client.search.grid_search(
            query="restaurant",
            bbox=bbox,
            cell_size_km=1.5,
            max_results=5000,
            zoom=16.0,
            detect_exhaustion=False,
            stats=stats,
            paginate=True,
        )

        places = [p for p, _ in results]

        print()
        print(stats.summary())
        print()

        if places:
            total = len(places)
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

        output_path = ROOT / "nyc_restaurants_5k_v2.json"
        data = [p.to_dict() for p in places[:5000]]
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"\nSaved {len(data)} places to {output_path}")


asyncio.run(main())
