import json
from pathlib import Path

input_path = Path(__file__).resolve().parents[1] / "nyc_restaurants_5k_v2.json"
with input_path.open(encoding="utf-8") as source:
    d = json.load(source)
print(f"Total places: {len(d)}")
print(f"With phone: {sum(1 for p in d if p.get('contact', {}).get('phone'))}")
print(f"With website: {sum(1 for p in d if p.get('contact', {}).get('website'))}")
print(f"With rating: {sum(1 for p in d if p.get('rating', {}).get('rating'))}")
print(f"With review_count: {sum(1 for p in d if p.get('rating', {}).get('review_count'))}")
print(f"With neighborhood: {sum(1 for p in d if p.get('address', {}).get('neighborhood'))}")
print(f"With categories: {sum(1 for p in d if p.get('business', {}).get('categories'))}")
