# Contributing to gmaps-scraper

## Development Setup

```bash
git clone https://github.com/nick-choudhary/gmaps-scraper.git
cd gmaps-scraper
pip install -e ".[dev]" --no-build-isolation
```

## Running Tests

```bash
python -m pytest tests/ -v
python -m ruff check src tests
python -m ruff format --check src tests
```

## Architecture

Two-phase scraping engine:
- **Phase 1** (`_search.py`): Grid search via `/search?tbm=map&pb=...` — fast, ~15 fields
- **Phase 2** (`_search.py::place_details`): Enrichment via `/maps/preview/place?pb=...` — adds review_count, hours, thumbnail, etc.

Key modules:
- `rpc/parser.py` — Field extraction from nested JSON arrays (47 fields)
- `rpc/decoder.py` — Anti-XSSI stripping, JSON/HTML response handling
- `transport.py` — HTTP client with UA rotation, jittered rate limiting
- `grid.py` — Geographic grid subdivision to overcome 120-result limit
- `_auth/session.py` — Cookie consent flow (NID/AEC/SOCS)

## Adding a New Field

1. Capture a small live response with the CLI or `scripts/e2e_live_test.py`
2. Inspect field structures with `scripts/inspect_data.py`
3. Add the field to `ParsedPlace` in `rpc/parser.py`
4. Add extraction logic in the relevant `_extract_*` helper
5. Add the field to `to_dict()` output groups
6. Write a test in `tests/test_parser.py`

## When Google Changes the API

Google Maps' internal `pb=` format changes periodically. If parsing breaks:

1. Run `python scripts/e2e_live_test.py` for a live protocol smoke test
2. Use `python scripts/inspect_data.py` to inspect known nested structures
3. Compare field indices against the golden fixtures in `tests/golden/`
4. Update the schema/parser constants in one place
5. Update tests and golden expectations to match

## Commit Style

- Conventional commits: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`
- Keep commits atomic — one feature/fix per commit

## PR Checklist

- [ ] Tests pass (`pytest tests/ -v`)
- [ ] Ruff passes (`ruff check src tests` and `ruff format --check src tests`)
- [ ] Package imports (`python -c "import gmaps; print(gmaps.__version__)"`)
- [ ] Live search still works (`gmaps search "test" --lat 30.27 --lng -97.74 -n 1`)
- [ ] Updated relevant documentation
