"""Inspect raw [203] hours and [171]/[72] image structures."""

import asyncio
import sys
import urllib.parse
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gmaps._search import _build_search_url
from gmaps.rpc.decoder import decode_response
from gmaps.rpc.parser import parse_search_response


async def main():
    c = httpx.AsyncClient(
        headers={"User-Agent": "Mozilla/5.0 Chrome/131.0.0.0"}, follow_redirects=True
    )
    await c.get("https://www.google.com/")
    await c.get("https://consent.google.com/ml?continue=https://www.google.com/maps&gl=US&hl=en")
    await c.get("https://www.google.com/maps")

    path = _build_search_url("coffee", 30.2672, -97.7431, 3).replace("https://www.google.com", "")
    r = await c.get(f"https://www.google.com{path}")
    d = decode_response(r.text, "json")
    places_raw = parse_search_response(d)
    p = places_raw[0]

    # Place details
    hex_enc = urllib.parse.quote(p.hex_id, safe="")
    ftid_enc = urllib.parse.quote(p.ftid, safe="")
    lat, lng = p.latitude, p.longitude
    pb = (
        f"!1m22!1s{hex_enc}"
        f"!3m12!1m3!1d898976.2597!2d{lng}!3d{lat}"
        f"!2m3!1f0.0!2f0.0!3f0.0"
        f"!3m2!1i1024!2i768!4f13.1"
        f"!4m2!3d{lat}!4d{lng}"
        f"!15m4!1m3!1s{hex_enc}!4s{ftid_enc}!5s{p.place_id}!6scoffee"
        f"!12m4!2m3!1i360!2i120!4i8"
        f"!13m57!2m2!1i203!2i100!3m2!2i4!5b1"
        f"!6m6!1m2!1i86!2i86!1m2!1i408!2i240"
        f"!7m33!1m3!1e1!2b0!3e3!1m3!1e2!2b1!3e2!1m3!1e2!2b0!3e3!1m3!1e8!2b0!3e3!1m3!1e10!2b0!3e3!1m3!1e10!2b1!3e2!1m3!1e10!2b0!3e4!1m3!1e9!2b1!3e2!2b1!9b0"
        f"!15m8!1m7!1m2!1m1!1e2!2m2!1i195!2i195!3i20"
        f"!14m2!1s{p.data_id}!7e81"
        f"!15m111!1m29!4e2!13m9!2b1!3b1!4b1!6i1!8b1!9b1!14b1!20b1!25b1"
        f"!18m17!3b1!4b1!5b1!6b1!9b1!13b1!14b1!17b1!20b1!21b1!22b1!30b1!32b1!33m1!1b1!34b1!36e2"
        f"!10m1!8e3!11m1!3e1!17b1!20m2!1e3!1e6!24b1!25b1!26b1!27b1!29b1!30m1!2b1!36b1!37b1"
        f"!39m3!2m2!2i1!3i1!43b1!52b1!54m1!1b1!55b1!56m1!1b1!61m2!1m1!1e1!65m5!3m4!1m3!1m2!1i224!2i298"
        f"!72m22!1m8!2b1!5b1!7b1!12m4!1b1!2b1!4m1!1e1!4b1"
        f"!8m10!1m6!4m1!1e1!4m1!1e3!4m1!1e4!3sother_user_google_review_posts__and__hotel_and_vr_partner_review_posts"
        f"!6m1!1e1!9b1!89b1!90m2!1m1!1e2!98m3!1b1!2b1!3b1!103b1!113b1!114m3!1b1!2m1!1b1!117b1!122m1!1b1!126b1!127b1!128m1!1b0"
        f"!21m0!22m2!1e81!8e4!29m0!30m6!3b1!6m1!2b1!7m1!2b1!9b1"
        f"!34m5!7b1!10b1!14b1!15m1!1b0!37i785"
        f"!39s{urllib.parse.quote(p.name, safe='')}!40b1!41b1"
    )
    resp = await c.get(
        "https://www.google.com/maps/preview/place",
        params={"pb": pb, "authuser": "0", "hl": "en", "gl": "us", "q": p.name},
    )
    d2 = decode_response(resp.text, "json")
    pd = d2[6]

    # Hours at [203]
    print("=== [203] HOURS ===")
    h203 = pd[203] if len(pd) > 203 else None
    if isinstance(h203, list):
        for i, day_entry in enumerate(h203):
            safe = str(day_entry).encode("ascii", "backslashreplace").decode("ascii")
            print(f"  [{i}] type={type(day_entry).__name__}: {safe[:200]}")

    # Images at [171]
    print("\n=== [171] IMAGES ===")
    im171 = pd[171] if len(pd) > 171 else None
    if isinstance(im171, list):
        print(f"  list[{len(im171)}]")
        for i, img in enumerate(im171[:2]):
            if isinstance(img, list):
                safe = str(img[:5]).encode("ascii", "backslashreplace").decode("ascii")
                print(f"  [{i}] list[{len(img)}]: {safe[:200]}")

    # Thumbnail at [72]
    print("\n=== [72] THUMBNAIL ===")
    th72 = pd[72] if len(pd) > 72 else None
    if isinstance(th72, list):
        print(f"  list[{len(th72)}]")
        if th72 and isinstance(th72[0], list):
            t0 = th72[0]
            print(f"  [0] list[{len(t0)}]")
            for j in range(min(len(t0), 5)):
                v = t0[j]
                if isinstance(v, list) and len(v) > 6 and isinstance(v[6], list) and v[6]:
                    url = v[6][0] if v[6] else None
                    print(f"    [{j}] ...[6][0] = {str(url)[:100]}")

    await c.aclose()


asyncio.run(main())
