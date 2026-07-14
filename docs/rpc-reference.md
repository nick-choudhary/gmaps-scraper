# Google Maps Internal API — RPC Reference

Reverse-engineered field indices for Google Maps internal API responses.

## Overview

Google Maps uses a custom `pb=` (protobuf-encoded) URL parameter format for
its internal data endpoints. Responses are JSON with `)]}'` anti-XSSI prefix
and deeply nested arrays with positional field indices.

## Endpoints

### Search

```
GET https://www.google.com/search?tbm=map&pb=...&hl=en
```

**pb structure:**
```
!1m2                         # Field 1 = message with 2 children
  !2s{query}                 #   Field 2 = search query string
  !3d{lat}!4d{lng}           #   Field 3-4 = center coordinates
!5i{zoom}                    # Field 5 = zoom level
!6s{lang}                    # Field 6 = language
```

**Response structure:**
```
data[0][1]           → results array
  data[0][1][i][14]  → place data for result i
```

**Place data field indices** (relative to `data[0][1][i][14]`):

| Index | Field | Type | Description |
|-------|-------|------|-------------|
| 10 | hex_id | str | Internal hex identifier (e.g., 0x89c2...) |
| 11 | name | str | Business/place name |
| 13 | categories | list | Business category labels |
| 14 | ? | str | Short/vicinity address |
| 18 | address | str | Full formatted address |
| 78 | place_id | str | Google Maps place_id |
| 89 | ftid | str | Feature tracking ID |
| 4 | rating_data | list | Sub-array for rating info |
| 4[7] | rating | float | Star rating (0.0–5.0) |
| 4[8] | review_count | int | Number of reviews |
| 9 | coords | list | Coordinate sub-array |
| 9[2] | latitude | float | Latitude |
| 9[3] | longitude | float | Longitude |
| 178 | phone_data | list | Phone number data |
| 178[0][0] | phone | str | Phone number |
| 61 | website | str | Website URL |
| 116 | price_level | int | Price level (1-4) |
| 34 | hours_old | list | Opening hours (old format) |
| 203 | hours_new | list | Opening hours (new format, 2025+) |
| 36 | photos | list | Photo references |
| 6 | is_ad | bool | Whether result is a sponsored ad |

### Place Details

```
GET https://www.google.com/maps/preview/place?pb=...&hl=en
```

**Response structure:**
```
data[6]  → main place data object
```

**Place details field indices** (relative to `data[6]`):

| Index | Field | Description |
|-------|-------|-------------|
| 11 | name | Business name |
| 18 | address | Full address |
| 78 | place_id | Google Maps place ID |
| 178 | phone | Phone number (nested) |
| 61 | website | Website URL |
| 4[7] | rating | Star rating |
| 4[8] | review_count | Review count |
| 9[2] | lat | Latitude |
| 9[3] | lng | Longitude |
| 203 | hours | Opening hours (new format) |
| 34 | hours_old | Opening hours (old format) |
| 36 | photos | Photo references |
| 32 | description | Editorial summary |
| 100 | amenities | Amenities/attributes |

### Reviews

```
GET https://www.google.com/maps/rpc/listugcposts?pb=...&hl=en
```

**pb structure:**
```
!1m6!1s{hex_id}              # Place identifier
!6m4!4m1!1e1!4m1!1e3         # Fixed structure
!2m2!1i{limit}!2s{token}     # Pagination
!5m2!1s!7e81                 # Sort control
!8m9!2b1!3b1!5b1!7b1        # Fixed flags
```

**Response structure:**
```
data[2]  → reviews array
data[1]  → next page token
```

**Review entry field indices** (relative to `review_entry`):

| Index | Field | Description |
|-------|-------|-------------|
| 0 | review_id | Unique review identifier |
| 1[4][5][0] | author_name | Reviewer display name |
| 1[4][2][0] | author_photo | Reviewer photo URL |
| 2[0][0] | rating | Star rating (1-5) |
| 2[15][0][0] | text | Review body text |
| 3 | timestamp | Review timestamp |
| 2[?] | photos | Review photos |

### Photos

Photo references from search/place responses can be resolved to CDN URLs:
```
https://lh5.googleusercontent.com/p/{photo_reference}=...
```

## Discovery / Development

### Capturing New Endpoints

1. Open Chrome DevTools → Network tab
2. Visit maps.google.com and perform the desired action
3. Filter requests by domain: `google.com`
4. Look for requests to `/search`, `/preview/place`, `/rpc/`
5. Copy the `pb=` parameter and decode with `gmaps.rpc.pb_encoder.decode_pb()`

### Decoding pb Parameters

```python
from gmaps.rpc.pb_encoder import decode_pb

# Paste the pb= value from DevTools
pb_string = "!1m2!2scoffee!3d30.2672!4d-97.7431"
decoded = decode_pb(pb_string)
for field in decoded:
    print(f"Field {field['field']} ({field['type']}): {field['value']}")
```

### Adding New Fields

When Google updates their response structure:
1. Capture fresh network traffic
2. Compare old vs new response arrays
3. Map shifted indices in `rpc/parser.py`
4. Update field constants and this reference doc

## Prior Art

- [promisingcoder/GoogleMapsCollector](https://github.com/promisingcoder/GoogleMapsCollector) — comprehensive reverse-engineering with protobuf decoding
- [likha7/google-maps-scraper-with-request](https://github.com/likha7) — simpler HTTP-based scraper
- [SerpApi: How we reverse-engineered Google Maps pagination](https://serpapi.com/blog/how-we-reverse-engineered-google-maps-pagination/)

## Notes

- Field indices are **not stable** — Google frequently reorders internal response structures
- Hex IDs can be used interchangeably with place IDs for most queries
- The `authuser` parameter selects which Google account to use (0 = default)
- Adding `&gl=us` parameter sets the geographic region context
