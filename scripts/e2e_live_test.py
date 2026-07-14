"""End-to-end test - fixed cookie propagation."""

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx

from gmaps._search import _build_search_url
from gmaps.grid import BoundingBox, generate_cells
from gmaps.rpc.decoder import decode_response
from gmaps.rpc.parser import parse_search_response


async def test():
    results = {}
    t0 = time.monotonic()

    # === STEP 1: Acquire cookies (same client reused for search) ===
    print("=== STEP 1: Cookie Session ===")
    client = httpx.AsyncClient(
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        },
        follow_redirects=True,
    )
    await client.get("https://www.google.com/")
    await client.get(
        "https://consent.google.com/ml?continue=https://www.google.com/maps&gl=US&hl=en&pc=m&src=1&cm=2"
    )
    await client.get("https://www.google.com/maps")

    cookie_names = [c.name for c in client.cookies.jar]
    print(f"Cookies: {cookie_names}")
    results["cookies"] = len(cookie_names)
    assert "NID" in cookie_names, "Missing NID!"

    # === STEP 2: Single search ===
    print("\n=== STEP 2: Search ===")
    t_s = time.monotonic()
    path = _build_search_url("coffee", 30.2672, -97.7431, 10).replace("https://www.google.com", "")
    resp = await client.get(f"https://www.google.com{path}")
    data = decode_response(resp.text, response_type="json")
    places = parse_search_response(data)
    print(f"Status: {resp.status_code}, {len(places)} places in {time.monotonic() - t_s:.1f}s")
    results["single_search"] = len(places)
    assert len(places) > 0, "No search results!"

    # === STEP 3: Grouped JSON ===
    print("\n=== STEP 3: Grouped JSON ===")
    for i, p in enumerate(places[:3]):
        d = p.to_dict()
        groups = sorted(d.keys())
        has_website = "website" in d.get("contact", {})
        print(f"  #{i + 1}: {p.name}")
        print(
            f"       rating={p.rating} | phone={p.phone[:20] if p.phone else 'N/A'} | web={'YES' if has_website else 'no'}"
        )
        print(f"       categories={p.categories}")
        print(f"       groups={groups}")
        if i == 0:
            out = json.dumps(d, indent=2, ensure_ascii=False)
            print(f"  {out[:600]}")
    results["grouped_json"] = "ok"

    # === STEP 4: Field coverage stats ===
    print("\n=== STEP 4: Field Coverage ===")
    total = len(places)
    fields = {
        "name": sum(1 for p in places if p.name),
        "place_id": sum(1 for p in places if p.place_id),
        "ftid": sum(1 for p in places if p.ftid),
        "phone": sum(1 for p in places if p.phone),
        "website": sum(1 for p in places if p.website),
        "address": sum(1 for p in places if p.address),
        "rating": sum(1 for p in places if p.rating),
        "review_count": sum(1 for p in places if p.review_count),
        "lat_lng": sum(1 for p in places if p.latitude),
        "categories": sum(1 for p in places if p.categories),
        "timezone": sum(1 for p in places if p.timezone),
        "street": sum(1 for p in places if p.street),
        "borough": sum(1 for p in places if p.borough),
        "thumbnail": sum(1 for p in places if p.thumbnail),
        "author_photo": sum(1 for p in places if p.author_photo),
        "quick_amenities": sum(1 for p in places if p.quick_amenities),
        "description": sum(1 for p in places if p.description),
    }
    for field, count in sorted(fields.items()):
        pct = count * 100 / total
        bar = "#" * int(pct / 5) + "." * (20 - int(pct / 5))
        print(f"  {field:20s} [{bar}] {count}/{total}")
    results["fields"] = {f: f"{c}/{total}" for f, c in fields.items()}

    # === STEP 5: Grid search (small area) ===
    print("\n=== STEP 5: Grid Search (0.5km cells, 2x2 area) ===")
    bbox = BoundingBox(min_lat=30.264, min_lon=-97.748, max_lat=30.270, max_lon=-97.740)
    cells = generate_cells(bbox, 0.3)
    print(f"Grid: {len(cells)} cells in 0.3km grid")

    all_places = []
    seen_ids = set()
    t_g = time.monotonic()
    for i, cell in enumerate(cells[:4]):  # limit to 4 cells
        path = _build_search_url("coffee", cell.lat, cell.lon, 20, viewport_dist=150).replace(
            "https://www.google.com", ""
        )
        resp = await client.get(f"https://www.google.com{path}")
        data = decode_response(resp.text, response_type="json")
        cell_places = parse_search_response(data)
        new = 0
        for p in cell_places:
            if p.place_id and p.place_id not in seen_ids:
                seen_ids.add(p.place_id)
                all_places.append(p)
                new += 1
        print(
            f"  Cell {i + 1}: ({cell.lat:.4f}, {cell.lon:.4f}) -> {new} new, {len(cell_places)} total"
        )
    grid_time = time.monotonic() - t_g
    print(f"Grid total: {len(all_places)} unique places in {grid_time:.1f}s")
    results["grid_total"] = len(all_places)
    results["grid_time"] = f"{grid_time:.1f}s"

    # Show top from grid
    print("\nTop 3 from grid:")
    for p in sorted(all_places, key=lambda x: x.rating or 0, reverse=True)[:3]:
        print(f"  {p.name} | {p.rating}* | {p.address}")

    # === SUMMARY ===
    total_time = time.monotonic() - t0
    results["total_time_s"] = round(total_time, 1)
    results["pass"] = True
    print(f"\n{'=' * 60}")
    print(f"ALL PASSED in {total_time:.1f}s")
    for k, v in results.items():
        print(f"  {k}: {v}")

    await client.aclose()


if __name__ == "__main__":
    asyncio.run(test())
