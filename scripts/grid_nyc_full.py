"""Grid search: HVAC in all 5 NYC boroughs - 1000+ target with website field."""

import asyncio
import json
import math
import time
from contextlib import suppress
from pathlib import Path
from urllib.parse import quote

import httpx

# All 5 boroughs coverage
REGIONS = [
    ("Manhattan+North", {"min_lat": 40.70, "max_lat": 40.88, "min_lon": -74.02, "max_lon": -73.92}),
    ("Brooklyn", {"min_lat": 40.55, "max_lat": 40.72, "min_lon": -74.05, "max_lon": -73.85}),
    ("Queens", {"min_lat": 40.55, "max_lat": 40.80, "min_lon": -73.95, "max_lon": -73.70}),
    ("Bronx+Upper", {"min_lat": 40.80, "max_lat": 40.92, "min_lon": -73.95, "max_lon": -73.80}),
    (
        "South Brooklyn+SI",
        {"min_lat": 40.49, "max_lat": 40.58, "min_lon": -74.26, "max_lon": -73.85},
    ),
]

CELL_KM = 1.5
KM_PER_DEG = 111.32
TARGET = 1000


def gen_cells(bbox, ckm):
    ls = ckm / KM_PER_DEG
    ml = (bbox["min_lat"] + bbox["max_lat"]) / 2
    lons = ckm / (KM_PER_DEG * math.cos(math.radians(ml)))
    cells = []
    lat = bbox["min_lat"] + ls / 2
    while lat < bbox["max_lat"]:
        lon = bbox["min_lon"] + lons / 2
        while lon < bbox["max_lon"]:
            cells.append((round(lat, 5), round(lon, 5)))
            lon += lons
        lat += ls
    return cells


def url(q, lat, lng, cell_km=1.5):
    e = quote(q)
    vp = int(cell_km * 500)  # half-diagonal in meters = zoomed in per cell
    return (
        "https://www.google.com/search"
        f"?tbm=map&authuser=0&hl=en&gl=us"
        f"&q={e.replace('%20', '+')}"
        f"&pb=!1s{e}"
        f"!4m8!1m3!1d{vp}!2d{lng}!3d{lat}"
        "!3m2!1i1024!2i768!4f16.0"
        "!7i20!8i0!10b1"
        "!12m50!1m5!18b1!30b1!31m1!1b1!34e1"
        "!2m4!5m1!6e2!20e3!39b1"
        f"!6m23!49b1!63m0!66b1!74i{int(cell_km * 750)}"
        "!85b1!91b1!114b1!149b1!206b1!209b1!212b1!213b1"
        "!223b1!232b1!233b1!234b1!244b1!246b1!250b1!253b1"
        "!258b1!260b1!263b1"
        "!10b1!12b1!13b1!14b1!16b1"
        "!17m1!3e1!20m3!5e2!6b1!14b1!46m1!1b0!96b1!99b1"
    )


def parse(txt):
    if not txt:
        return None
    if txt.startswith(")]}'\n"):
        txt = txt[5:]
    elif txt.startswith(")]}'"):
        txt = txt[4:]
    d, s = 0, None
    for i, ch in enumerate(txt):
        if ch == "[":
            if d == 0:
                s = i
            d += 1
        elif ch == "]":
            d -= 1
            if d == 0 and s is not None:
                return json.loads(txt[s : i + 1])
    return json.loads(txt)


def extract(data):
    if not isinstance(data, list) or not data:
        return []
    d0 = data[0]
    if not isinstance(d0, list) or len(d0) < 2:
        return []
    res = d0[1]
    if not isinstance(res, list) or len(res) < 2:
        return []
    out = []
    for idx in range(1, len(res)):
        e = res[idx]
        if not isinstance(e, list) or len(e) < 15:
            continue
        pd = e[14]
        if not isinstance(pd, list) or len(pd) < 20:
            continue
        b = {}
        if len(pd) > 11 and pd[11]:
            b["name"] = str(pd[11])
        if len(pd) > 18 and pd[18]:
            b["address"] = str(pd[18])
        if len(pd) > 78 and pd[78]:
            b["place_id"] = str(pd[78])
        if len(pd) > 10 and pd[10]:
            b["hex_id"] = str(pd[10])
        if len(pd) > 7 and isinstance(pd[7], list) and pd[7] and pd[7][0]:
            b["website"] = str(pd[7][0])
        if len(pd) > 4 and isinstance(pd[4], list):
            rd = pd[4]
            if len(rd) > 7 and rd[7] is not None:
                with suppress(ValueError, TypeError):
                    b["rating"] = float(rd[7])
            if len(rd) > 8 and rd[8] is not None:
                with suppress(ValueError, TypeError):
                    b["review_count"] = int(float(rd[8]))
        if len(pd) > 9 and isinstance(pd[9], list):
            cd = pd[9]
            if len(cd) > 2 and cd[2] is not None:
                with suppress(ValueError, TypeError):
                    b["latitude"] = float(cd[2])
            if len(cd) > 3 and cd[3] is not None:
                with suppress(ValueError, TypeError):
                    b["longitude"] = float(cd[3])
        if len(pd) > 178 and isinstance(pd[178], list) and pd[178]:
            inner = pd[178][0]
            if isinstance(inner, list) and inner and inner[0]:
                b["phone"] = str(inner[0])
        if len(pd) > 13 and isinstance(pd[13], list):
            b["categories"] = [str(c) for c in pd[13] if c]
        if b.get("name"):
            out.append(b)
    return out


async def run_region(
    name, bbox, shared_biz, shared_seen, lock, stop_signal, total_cells_done, target_per
):
    cells = gen_cells(bbox, CELL_KM)
    print(f"[{name}] {len(cells)} cells")

    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/132.0.0.0 Safari/537.36"
    hdrs = {
        "User-Agent": ua,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }

    async with httpx.AsyncClient(
        headers=hdrs, timeout=httpx.Timeout(20.0), follow_redirects=True
    ) as c:
        await c.get("https://www.google.com/")
        await c.get("https://consent.google.com/")
        await c.get("https://www.google.com/maps")

        for ci, (lat, lng) in enumerate(cells):
            if stop_signal[0]:
                break

            try:
                r = await c.get(url("hvac", lat, lng, CELL_KM))
                if r.status_code == 200 and len(r.text) > 500:
                    data = parse(r.text)
                    if data:
                        biz_list = extract(data)
                        async with lock:
                            for b in biz_list:
                                pid = b.get("place_id") or b.get("hex_id")
                                if pid and pid not in shared_seen:
                                    shared_seen.add(pid)
                                    shared_biz[pid] = b
                            if len(shared_biz) >= TARGET:
                                stop_signal[0] = True
            except (httpx.HTTPError, ValueError, TypeError, IndexError):
                pass

            async with lock:
                total_cells_done[0] += 1

            if (ci + 1) % 15 == 0:
                async with lock:
                    print(
                        f"  [{name}] {ci + 1}/{len(cells)} | total={len(shared_biz)} | "
                        f"cells_done={total_cells_done[0]}"
                    )

            await asyncio.sleep(0.35)

    async with lock:
        print(f"[{name}] DONE: contributed to {len(shared_biz)} total")


async def main():
    print("=" * 60)
    print("HVAC IN NYC - ALL 5 BOROUGHS (1000+ target)")
    print(f"Cell size: {CELL_KM}km")
    print("=" * 60)

    shared_biz = {}
    shared_seen = set()
    lock = asyncio.Lock()
    stop = [False]
    done_count = [0]
    t0 = time.time()

    tasks = [
        run_region(name, bbox, shared_biz, shared_seen, lock, stop, done_count, 250)
        for name, bbox in REGIONS
    ]
    await asyncio.gather(*tasks)

    biz_list = list(shared_biz.values())
    elapsed = time.time() - t0

    print("\n" + "=" * 60)
    print(f"TOTAL: {len(biz_list)} HVAC businesses in NYC")
    print(f"Time: {elapsed:.0f}s ({elapsed / 60:.1f}m)")
    print(f"Cells total: {done_count[0]}")
    print("=" * 60)

    # Stats
    w_web = sum(1 for b in biz_list if b.get("website"))
    w_phone = sum(1 for b in biz_list if b.get("phone"))
    w_rating = sum(1 for b in biz_list if b.get("rating"))
    print(f"With website: {w_web}/{len(biz_list)}")
    print(f"With phone: {w_phone}/{len(biz_list)}")
    print(f"With rating: {w_rating}/{len(biz_list)}")

    # Top rated
    ranked = sorted(
        [b for b in biz_list if b.get("rating")],
        key=lambda x: (-x["rating"], -x.get("review_count", 0)),
    )
    print("\n--- TOP 10 (by rating) ---")
    for i, b in enumerate(ranked[:10]):
        stars = f"{b.get('rating', '?')}*"
        rc = b.get("review_count", 0)
        web = b.get("website", "")[:50]
        print(f"{i + 1}. {b['name']} - {stars} ({rc} reviews)")
        print(f"   {b.get('address', 'N/A')}")
        if b.get("phone"):
            print(f"   Phone: {b['phone']}")
        if web:
            print(f"   Web: {web}")

    # Most reviewed
    by_rc = sorted([b for b in biz_list if b.get("review_count")], key=lambda x: -x["review_count"])
    print("\n--- TOP 10 (by reviews) ---")
    for i, b in enumerate(by_rc[:10]):
        stars = f"{b.get('rating', '?')}*" if b.get("rating") else "?"
        web = b.get("website", "")[:60]
        print(f"{i + 1}. {b['name']} - {stars} ({b['review_count']} reviews)")
        if web:
            print(f"   Web: {web}")

    # Borough distribution
    bk = sum(1 for b in biz_list if "Brooklyn" in (b.get("address", "")))
    mn = sum(
        1
        for b in biz_list
        if "New York, NY" in (b.get("address", "")) and "Brooklyn" not in (b.get("address", ""))
    )
    qn = sum(
        1
        for b in biz_list
        if any(
            x in (b.get("address", ""))
            for x in (
                "Queens",
                "Long Island City",
                "Astoria",
                "Flushing",
                "Jamaica",
                "Rego Park",
                "Forest Hills",
                "Sunnyside",
            )
        )
    )
    bx = sum(1 for b in biz_list if "Bronx" in (b.get("address", "")))
    si = sum(1 for b in biz_list if "Staten Island" in (b.get("address", "")))
    print("\n--- BOROUGH DISTRIBUTION ---")
    print(f"Brooklyn: {bk}  |  Manhattan: {mn}  |  Queens: {qn}")
    print(f"Bronx: {bx}  |  Staten Island: {si}")

    out = Path(__file__).with_name("nyc_hvac_1000.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(biz_list, f, indent=2, ensure_ascii=False)
    print(f"\nSaved to {out}")


asyncio.run(main())
