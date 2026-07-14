"""Test place details with an optional login cookie supplied by the operator."""

import asyncio
import json
import os
import sys
import urllib.parse
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gmaps._search import _build_search_url
from gmaps.rpc.decoder import decode_response
from gmaps.rpc.parser import parse_place_details_response, parse_search_response


async def test():
    cookie_str = os.getenv("GMAPS_LOGIN_COOKIES", "")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/149.0.0.0 Safari/537.36",
        "x-client-data": "CKmdygEIlqHLAQiFoM0BCI7NlDAIxs+UMAiO0JQwCOTQlDAIytOUMBjazZQw",
        "x-maps-diversion-context-bin": "CAE=",
        "referer": "https://www.google.com/",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
    }

    client = httpx.AsyncClient(headers=headers, follow_redirects=True)
    if cookie_str:
        client.headers["cookie"] = cookie_str

    # First, get a place from search to have coordinates
    await client.get("https://www.google.com/")
    await client.get(
        "https://consent.google.com/ml?continue=https://www.google.com/maps&gl=US&hl=en"
    )

    path = _build_search_url("coffee", 30.2672, -97.7431, 3).replace("https://www.google.com", "")
    r = await client.get(f"https://www.google.com{path}")
    d = decode_response(r.text, "json")
    places = parse_search_response(d)
    place = places[0]

    print(f"Search result: {place.name}")
    print(f"  hex_id={place.hex_id}")
    print(f"  ftid={place.ftid}")
    print(f"  place_id={place.place_id}")
    print(f"  lat={place.latitude}, lng={place.longitude}")
    print()

    # Build pb parameter using the VERIFIED format
    # !1m22!1s{hex_id}!3m12!1m3!1d{viewport}!2d{lng}!3d{lat}!2m3!1f0!2f0!3f0!3m2!1i{width}!2i{height}!4f13.1!4m2!3d{center_lat}!4d{center_lng}!15m4!1m3!1s{hex_id}!4s{ftid}!5s{place_id}!6s{query}!...{flags}

    hex_id = place.hex_id  # "0x8644b5028b2fa213:0xf76b50a49197c18e"
    ftid = place.ftid  # "/g/11mlg2rrdy"
    pid = place.place_id  # "ChIJE6IviwK1RIYRjsGXkaRQa_c"
    lat, lng = place.latitude or 40.7, place.longitude or -73.99

    # Encode the identifiers for pb (hex_id with 0x, URL-encoded)
    hex_enc = urllib.parse.quote(hex_id, safe="")  # 0x8644b5... -> 0x8644b5...
    ftid_enc = urllib.parse.quote(ftid, safe="")  # /g/11ml... -> %2Fg%2F11ml...
    pid_enc = pid  # ChIJ... doesn't need encoding

    # Viewport: use a large default (~900km gives good results)
    viewport = 898976.2597
    width, height = 1024, 768
    zoom = 13.1
    center_lat, center_lng = lat, lng

    # Build the pb
    pb = (
        f"!1m22"
        f"!1s{hex_enc}"
        f"!3m12!1m3!1d{viewport}!2d{lng}!3d{lat}"
        f"!2m3!1f0.0!2f0.0!3f0.0"
        f"!3m2!1i{width}!2i{height}!4f{zoom}"
        f"!4m2!3d{center_lat}!4d{center_lng}"
        f"!15m4!1m3!1s{hex_enc}!4s{ftid_enc}!5s{pid_enc}!6scoffee"
        # Add the tail flags from the verified format
        f"!12m4!2m3!1i360!2i120!4i8"
        f"!13m57!2m2!1i203!2i100!3m2!2i4!5b1"
        f"!6m6!1m2!1i86!2i86!1m2!1i408!2i240"
        f"!7m33!1m3!1e1!2b0!3e3!1m3!1e2!2b1!3e2!1m3!1e2!2b0!3e3!1m3!1e8!2b0!3e3!1m3!1e10!2b0!3e3!1m3!1e10!2b1!3e2!1m3!1e10!2b0!3e4!1m3!1e9!2b1!3e2!2b1!9b0"
        f"!15m8!1m7!1m2!1m1!1e2!2m2!1i195!2i195!3i20"
        f"!14m2!1s{place.data_id}!7e81"
        f"!15m111!1m29!4e2!13m9!2b1!3b1!4b1!6i1!8b1!9b1!14b1!20b1!25b1"
        f"!18m17!3b1!4b1!5b1!6b1!9b1!13b1!14b1!17b1!20b1!21b1!22b1!30b1!32b1!33m1!1b1!34b1!36e2"
        f"!10m1!8e3!11m1!3e1!17b1!20m2!1e3!1e6!24b1!25b1!26b1!27b1!29b1!30m1!2b1!36b1!37b1"
        f"!39m3!2m2!2i1!3i1!43b1!52b1!54m1!1b1!55b1!56m1!1b1!61m2!1m1!1e1!65m5!3m4!1m3!1m2!1i224!2i298"
        f"!72m22!1m8!2b1!5b1!7b1!12m4!1b1!2b1!4m1!1e1!4b1"
        f"!8m10!1m6!4m1!1e1!4m1!1e3!4m1!1e4!3sother_user_google_review_posts__and__hotel_and_vr_partner_review_posts"
        f"!6m1!1e1!9b1!89b1!90m2!1m1!1e2!98m3!1b1!2b1!3b1!103b1!113b1!114m3!1b1!2m1!1b1!117b1!122m1!1b1!126b1!127b1!128m1!1b0"
        f"!21m0!22m2!1e81!8e4!29m0!30m6!3b1!6m1!2b1!7m1!2b1!9b1"
        f"!34m5!7b1!10b1!14b1!15m1!1b0!37i785"
        f"!39s{urllib.parse.quote(place.name, safe='')}!40b1!41b1"
    )

    print(f"pb length: {len(pb)}")

    resp = await client.get(
        "https://www.google.com/maps/preview/place",
        params={"pb": pb, "authuser": "0", "hl": "en", "gl": "us", "q": place.name},
    )
    print(f"Status: {resp.status_code}, Size: {len(resp.text)}")

    if resp.status_code == 200 and len(resp.text) > 5000:
        print("SUCCESS! Decoding response...")
        d = decode_response(resp.text, "json")
        p = parse_place_details_response(d)
        if p:
            print(f"\nName: {p.name}")
            print(f"Rating: {p.rating}")
            print(f"Review Count: {p.review_count}")
            print(f"Description: {(p.description or '')[:150]}")
            print(f"Phone: {p.phone}")
            print(f"Photos: {len(p.photos)}")
            print(f"About sections: {len(p.about)}")
            print(f"Categories: {p.categories}")
            print(f"Images: {len(p.images)}")
            if isinstance(p.hours, dict):
                print(f"Hours: {list(p.hours.keys())[:5]}")
            else:
                print(f"Hours: {p.hours[:3] if p.hours else 'none'}")
            print("\nGROUPED JSON:")
            print(json.dumps(p.to_dict(), indent=2, ensure_ascii=False)[:1000])
        else:
            print("parse_place_details_response returned None")
            print(f"Raw structure: data[6] type={type(d[6]) if len(d) > 6 else 'N/A'}")
            if len(d) > 6 and isinstance(d[6], list):
                print(f"  data[6] len={len(d[6])}")
                for i in range(min(20, len(d[6]))):
                    v = d[6][i]
                    if v is not None:
                        print(f"  [{i}] {type(v).__name__}: {str(v)[:60]}")
    elif resp.status_code == 400:
        print(f"400: {resp.text[:300]}")

    await client.aclose()


asyncio.run(test())
