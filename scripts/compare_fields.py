"""Compare our fields vs gosom's 34 data points."""

import json
from pathlib import Path

input_path = Path(__file__).resolve().parents[1] / "nyc_restaurants_5k_v2.json"
with input_path.open(encoding="utf-8") as source:
    d = json.load(source)
p = d[0]  # sample place

# All groups we output
groups = set()
for place in d[:100]:
    for k in place:
        groups.add(k)
print("=== OUR OUTPUT GROUPS ===")
for g in sorted(groups):
    print(f"  {g}")

print()
print("=== GOSOM 34 FIELDS vs OURS ===")

gosom_fields = [
    ("input_id", "search_token"),
    ("link", "contact.google_maps_url"),
    ("title", "name"),
    ("category", "business.categories"),
    ("address", "address.full"),
    ("open_hours", "business.hours"),
    ("popular_times", "business.popular_times"),
    ("website", "contact.website"),
    ("phone", "contact.phone"),
    ("plus_code", "contact.plus_code"),
    ("review_count", "rating.review_count"),
    ("review_rating", "rating.rating"),
    ("reviews_per_rating", "rating.reviews_per_rating"),
    ("latitude", "location.latitude"),
    ("longitude", "location.longitude"),
    ("cid", "cid"),
    ("status", "business.status"),
    ("descriptions", "business.description"),
    ("reviews_link", "rating.reviews_link"),
    ("thumbnail", "media.thumbnail"),
    ("timezone", "business.timezone"),
    ("price_range", "rating.price_range"),
    ("data_id", "data_id"),
    ("images", "media.images"),
    ("reservations", "amenities.reservations"),
    ("order_online", "amenities.order_online"),
    ("menu", "amenities.menu"),
    ("owner", "amenities.owner"),
    ("complete_address", "address (structured)"),
    ("about", "amenities.about"),
    ("user_reviews", "(reviews endpoint - separate)"),
    ("emails", "contact.emails"),
    ("user_reviews_extended", "(reviews endpoint - up to 300)"),
    ("place_id", "place_id"),
]

our_fields = set()
for place in d[:100]:
    for k, v in place.items():
        if isinstance(v, dict):
            for sub in v:
                our_fields.add(f"{k}.{sub}")
        else:
            our_fields.add(k)

for gosom_name, our_path in gosom_fields:
    in_our = our_path in our_fields or our_path == "(not implemented)"
    # Check actual presence in sample
    parts = our_path.split(".")
    val = p
    for part in parts:
        if isinstance(val, dict):
            val = val.get(part)
        else:
            val = None
            break
    present = val is not None and val != "" and val != []

    if "(not implemented)" in our_path:
        status = "MISSING"
    elif present:
        status = "OK"
    elif in_our:
        status = "~ (sparse)"
    else:
        status = "MISSING"

    print(f"  [{status:8s}] {gosom_name:25s} -> {our_path}")

# Count
has = sum(1 for _, op in gosom_fields if "(not implemented)" not in op)
has_ok = sum(1 for _, op in gosom_fields if "(not implemented)" not in op and op in our_fields)
missing = [gn for gn, op in gosom_fields if "(not implemented)" in op]
print("\n=== SUMMARY ===")
print(f"gosom fields: {len(gosom_fields)}")
print(f"We have: {has_ok}/{has} (excluding not-implemented)")
print(f"Not implemented: {missing}")

# Extra fields we have that gosom doesn't
extras = our_fields - {op for _, op in gosom_fields}
if extras:
    print("\n=== OUR EXTRA FIELDS (not in gosom) ===")
    for e in sorted(extras):
        print(f"  + {e}")
