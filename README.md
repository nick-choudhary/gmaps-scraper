# gmaps-scraper

**Turn any place into a spreadsheet of local businesses.**

`gmaps-scraper` pulls business listings from Google Maps — names, addresses, phone
numbers, websites, ratings, reviews, categories, and opening hours — and can also find
**emails and social profiles** so you can build lead lists. Export to **JSON or CSV**
with a single command.

No API key. No sign-up. No browser to install. Just `pip install` and run.

---

## What you get for each business

- Name, full address, and neighborhood
- Phone number and website
- Star rating and review count
- Category (e.g. "Chiropractor", "Coffee shop")
- Opening hours *(with `--enrich`)*
- Emails and social profiles — Facebook, Instagram, LinkedIn, and more *(with `--contacts`)*

## Install

```bash
pip install gmaps-scraper
```

That's the whole setup. (Requires Python 3.10 or newer.)

## Quick start

Search an area in plain English:

```bash
gmaps search "coffee shops in Austin, Texas"
```

Save the results as a CSV (opens in Excel or Google Sheets) or JSON:

```bash
gmaps search "coffee shops in Austin, Texas" --format csv -o coffee.csv
gmaps search "coffee shops in Austin, Texas" --format json -o coffee.json
```

## Collect an entire city (recommended)

A single search returns about 20 businesses. To cover a whole city or region
comprehensively — automatically, with duplicates removed — use `collect`:

```bash
gmaps collect "chiropractors" --location "Nashville, Tennessee" -o chiropractors.json
```

You get one clean file with every business found, plus a short report telling you
whether the run captured the full area. If a long run is interrupted, add `--resume`
to pick up exactly where it stopped — nothing is lost.

### Build a lead list (emails + socials)

```bash
gmaps collect "chiropractors" --location "Nashville, Tennessee" \
  --enrich --contacts --max-contacts 50 -o leads.json
```

- `--enrich` adds opening hours and extra details.
- `--contacts` visits each business's website and extracts emails and social profiles.
- `--max-contacts 50` caps how many websites to visit, so runs stay fast and focused.

### Common options

| Option | What it does |
|---|---|
| `--location "City, State"` | The area to cover, in plain English |
| `-o results.json` | Where to save the output |
| `--format csv` \| `json` | Output format (see below) |
| `--enrich` | Add opening hours and detailed fields |
| `--contacts` | Add emails and social profiles |
| `--max-contacts N` | Limit how many websites to check |
| `-n 500` | Maximum number of businesses to collect |
| `--resume` | Continue an interrupted collection |

Run `gmaps --help` (or `gmaps collect --help`) to see everything.

## Output formats

- **JSON** *(default)* — a clean list of businesses with all fields. Best for feeding
  into other tools or scripts.
- **CSV** — open directly in Excel or Google Sheets: add `--format csv -o file.csv`.
- Large `collect` runs also stream results to disk as they're found, so a long job is
  always safe to stop and `--resume`.

*More export options (Excel and direct database output) are on the way.*

## Use it from Python

```python
import asyncio
from gmaps import GMapsClient

async def main():
    async with GMapsClient() as client:
        result = await client.search.places(query="dentists in Miami", max_results=20)
        for business in result.places:
            print(business.name, business.phone, business.website)

asyncio.run(main())
```

## Use it with AI assistants

`gmaps-scraper` ships with a built-in server (MCP) so AI assistants like Claude can
search Google Maps and build business lists for you on request. Point your assistant's
tool configuration at the `gmaps` MCP server and ask it, for example, to "collect all
dentists in Miami with contact details."

## License

MIT — free for personal and commercial use.
