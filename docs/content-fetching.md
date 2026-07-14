# Content Fetching — provider fallback chain

Fetching a business website's content (for email + social extraction) goes
through a **pluggable provider chain** that auto-detects what's configured and
picks the best available option per URL. There is nothing to turn on and no
flags to set — it reads the environment and decides.

## Fallback order

For each website, providers are tried in order; the first success wins, and any
provider that fails (missing key, quota, rate limit, network error) simply falls
through to the next:

1. **TinyFish Fetch** — used if `TINYFISH_API_KEY` is set
2. **Firecrawl Scrape** — used if `FIRECRAWL_API_KEY` is set
3. **Proxied HTTP** — used if a proxy is configured
4. **Basic HTTP** — always available; the final fallback

**Behavior preservation:** with no API keys and no proxy configured, the chain
is exactly `[basic]` — the same direct-HTTP fetch the scraper used before this
feature. Turning nothing on changes nothing.

## Configuration (all optional, all auto-detected)

| What | How to provide | Notes |
|------|----------------|-------|
| TinyFish | `TINYFISH_API_KEY` env var | `X-API-Key` auth; returns clean page content |
| Firecrawl | `FIRECRAWL_API_KEY` env var | `fc-...` Bearer key; `onlyMainContent=false` so footers/contact blocks are kept |
| Proxy | `--proxy` CLI flag, or `GMAPS_PROXY` / `HTTPS_PROXY` / `HTTP_PROXY` env | Any httpx-compatible proxy URL |

Example:

```bash
export TINYFISH_API_KEY=tf-xxxx
export FIRECRAWL_API_KEY=fc-xxxx
gmaps grid "hvac" --bbox 40.4,-74.3,40.9,-73.6 --contacts --format csv -o leads.csv
```

With both keys set, each site is tried on TinyFish first, then Firecrawl, then
(if a proxy is set) a proxied direct fetch, then a plain direct fetch.

## Why a chain (design note)

This is the "capability behind a swappable interface" pattern: the *what*
(get a page's HTML) is stable, the *how* (managed API vs. proxy vs. direct) is a
provider you can add, reorder, or drop without touching the extractor. Managed
APIs handle JS rendering and blocking that a plain `httpx.get` cannot, so
coverage improves when a key is present — but the system still works, and stays
deterministic in output shape, when nothing is configured.

All providers are requested in **HTML** format so the downstream email/social
extraction (which scans markup for `href=`/URLs) behaves identically regardless
of which provider served the page.

## Adding a provider

Implement `ContentFetcher` in `src/gmaps/fetchers.py`:

```python
class MyFetcher(ContentFetcher):
    name = "myprovider"
    def is_available(self) -> bool: ...      # e.g. bool(self.api_key)
    async def open(self) -> None: ...        # create an httpx client
    async def close(self) -> None: ...
    async def fetch(self, url, timeout) -> FetchResult: ...
```

Return `FetchResult(url=..., text=<html>, provider=self.name)` on success, or
`self._fail(url, reason)` to fall through. Then slot it into
`build_default_chain()` at the desired priority. Keep a pure `parse_*` helper for
the response shape so it can be unit-tested without network (see
`parse_tinyfish` / `parse_firecrawl` and `tests/test_fetchers.py`).

## Verified endpoints (July 2026, from official OpenAPI specs)

| Provider | Endpoint | Auth | Request | Content at |
|----------|----------|------|---------|-----------|
| TinyFish **Fetch** | `POST https://api.fetch.tinyfish.ai/` | `X-API-Key` | `{"urls":[url],"format":"html"}` | `results[0].text` (`errors[]` per-URL) |
| Firecrawl **Scrape** | `POST https://api.firecrawl.dev/v2/scrape` | `Bearer fc-…` | `{"url":url,"formats":["html"],"onlyMainContent":false}` | `data.html` / `data.markdown` |

**TinyFish Fetch vs. Automation — why we use Fetch.** TinyFish also exposes an
*Automation/Agent API* (`https://agent.tinyfish.ai/v1/automation/run-sse`) — a
goal-driven AI browser agent that streams Server-Sent Events and is billed per
run. That is the right tool for interactive, multi-step tasks (logging in,
filling forms, navigating), but it is heavier and costlier than needed to simply
retrieve a page's content. For "fetch a business site → extract emails/socials,"
the lightweight **Fetch API** (URL in, clean HTML out) is the correct primitive,
so that is what the `tinyfish` provider uses. If you ever need agentic handling
of gated/interactive sites, the Automation API could be added as a separate,
opt-in provider.

## Files

- `src/gmaps/fetchers.py` — providers, chain, `build_default_chain()`
- `src/gmaps/website.py` — `WebsiteContactExtractor` delegates fetching to the chain
- `tests/test_fetchers.py` — parsers, availability, ordering, fallback (no network)
