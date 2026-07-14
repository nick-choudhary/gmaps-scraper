# Code Standards

## General

- Keep modules small and single-purpose: one module = one concern (parser, transport, grid, auth)
- Fix root causes, do not layer workarounds — if parsing breaks, fix the field index, don't add a try/except band-aid
- Async-first: all network calls are `async def`. Tests are synchronous where possible using mock data.
- Python 3.10+: use `X | None` not `Optional[X]`, use dataclasses not Pydantic for internal models

## Python Style

- Type hints required on all public functions
- Use `from __future__ import annotations` at top of every module
- Dataclasses for structured data (`ParsedPlace`, `SearchResult`, `ScraperStats`)
- No global mutable state. All state lives on class instances.
- `str | None` not `Optional[str]`

## Naming

- Module-level constants: `UPPER_SNAKE_CASE` (e.g., `F_NAME`, `BASE_HEADERS`)
- Private functions: `_leading_underscore` (e.g., `_safe_str`, `_extract_hours_new`)
- Public API methods: no underscore (e.g., `places()`, `grid_search()`, `enrich()`)

## Field Indices

- All Google Maps response field indices live as constants at the top of `parser.py`
- Never hardcode an index in extraction logic — reference the constant
- When Google changes an index (they will), update only the constant
- Last verified date is documented in the constant comment

## Error Handling

- Exception hierarchy in `exceptions.py`: `GMapsError` → `AuthError`, `RateLimitError`, `ParseError`, `NetworkError`, `TimeoutError`
- Transport retries on 5xx and timeout, never on 429 (raise immediately)
- Grid search wraps each cell in try/except, records error to `ScraperStats`, continues to next cell
- Never swallow exceptions silently — always log at minimum

## Anti-Detection Rules

- Never use `br` in Accept-Encoding (httpx can't decode → silent empty responses)
- Never use `Sec-Fetch-Dest: document` for API calls (Google returns HTML)
- Always jitter rate limiting (±30% minimum)
- Rotate User-Agent per request from the pool
- Shuffle grid cells before search (avoid sequential spatial pattern)

## Output

- `to_dict()` is the canonical JSON output — never expose raw `__dict__`
- Empty/null values are omitted from output (clean JSON, no `"field": null`)
- `is_ad` only appears when `True`
- JSON output uses `ensure_ascii=True` for stdout (Windows terminal compatibility), `ensure_ascii=False` for files

## File Organization

- `src/gmaps/` — all source code
- `src/gmaps/rpc/` — protocol layer (parser, decoder, encoder, types)
- `src/gmaps/_auth/` — cookie session management
- `tests/` — pytest tests, one file per module
- `scripts/` — integration test scripts (not part of the package)
- `context/` — these context files
- `docs/` — RPC reference documentation
