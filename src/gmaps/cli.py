"""gmaps-scraper CLI — command-line interface for Google Maps scraping.

Usage:
    gmaps search "coffee shops" --lat 30.27 --lng -97.74
    gmaps search "coffee shops" --lat 30.27 --lng -97.74 --enrich
    gmaps search "hvac" --grid --bbox 40.4,-74.3,40.9,-73.6 --cell-size 0.5
    gmaps place ChIJN1t_tDeuEmsRUsoyG83frY4 --enrich
    gmaps reviews 0x89c259a6bcd5e9d1:0x... --sort newest
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Coroutine, Mapping, Sequence
from pathlib import Path
from typing import Any, TypeVar, cast

import click

from .client import GMapsClient
from .rpc.parser import ParsedPlace

logger = logging.getLogger(__name__)
T = TypeVar("T")


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _run_async(coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def _make_client(
    ctx: click.Context, enrich: bool = False, cookies: str | None = None
) -> GMapsClient:
    """Build GMapsClient from context + flags."""
    login_cookies = cookies
    if cookies and Path(cookies).exists():
        login_cookies = Path(cookies).read_text(encoding="utf-8").strip()

    return GMapsClient(
        enrich=enrich,
        timeout=ctx.obj["timeout"],
        max_retries=ctx.obj["retries"],
        language=ctx.obj["lang"],
        proxy=ctx.obj["proxy"],
        login_cookies=login_cookies if login_cookies else None,
    )


def _output_places(
    places: Sequence[ParsedPlace], fmt: str, output: str | None, query: str = ""
) -> None:
    """Render places in text/json/csv format."""
    if fmt == "json":
        data = [p.to_dict() for p in places]
        out = json.dumps(data, indent=2, ensure_ascii=output is None)
        if output:
            Path(output).write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            click.echo(f"Saved {len(places)} results to {output}")
        else:
            click.echo(out)

    elif fmt == "csv":
        import csv
        import io

        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(
            [
                "name",
                "place_id",
                "rating",
                "reviews",
                "phone",
                "website",
                "emails",
                "socials",
                "address",
                "lat",
                "lng",
            ]
        )
        for p in places:
            emails = "; ".join(getattr(p, "emails", []) or [])
            socials = "; ".join(
                f"{k}: {v}" for k, v in (getattr(p, "social_links", {}) or {}).items()
            )
            w.writerow(
                [
                    p.name,
                    p.place_id,
                    p.rating,
                    p.review_count,
                    p.phone,
                    p.website,
                    emails,
                    socials,
                    p.address,
                    p.latitude,
                    p.longitude,
                ]
            )
        out = buf.getvalue()
        if output:
            Path(output).write_text(out, encoding="utf-8")
            click.echo(f"Saved {len(places)} results to {output}")
        else:
            click.echo(out)

    else:
        for i, p in enumerate(places):
            click.echo(f"\n#{i + 1} {p.name}")
            if p.rating:
                click.echo(f"    Rating: {p.rating} ({p.review_count} reviews)")
            if p.phone:
                click.echo(f"    Phone: {p.phone}")
            if p.website:
                click.echo(f"    Website: {p.website}")
            click.echo(f"    Address: {p.address}")
            if p.place_id:
                click.echo(f"    Place ID: {p.place_id}")
            if p.categories:
                click.echo(f"    Categories: {', '.join(p.categories[:5])}")
            if getattr(p, "emails", None):
                click.echo(f"    Emails: {', '.join(p.emails)}")
            for platform, url in (getattr(p, "social_links", {}) or {}).items():
                click.echo(f"    {platform.capitalize()}: {url}")
        click.echo(f"\nTotal: {len(places)} results")


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Debug logging.")
@click.option("--lang", default="en", help="Language code.")
@click.option("--timeout", default=30.0, help="Request timeout seconds.")
@click.option("--retries", default=3, help="Max retry attempts.")
@click.option("--proxy", default=None, help="Proxy URL.")
@click.pass_context
def main(
    ctx: click.Context,
    verbose: bool,
    lang: str,
    timeout: float,
    retries: int,
    proxy: str | None,
) -> None:
    """gmaps-scraper: Google Maps scraping toolkit.

    No API key required. Uses reverse-engineered internal endpoints.

    \b
    Mode 1 (default): Fast search only
    Mode 2: --enrich (search + place details, no login)
    Mode 3: --enrich --cookies cookies.txt (with login for full fields)
    """
    _setup_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["lang"] = lang
    ctx.obj["timeout"] = timeout
    ctx.obj["retries"] = retries
    ctx.obj["proxy"] = proxy


@main.command()
@click.argument("query")
@click.option("--lat", type=float, default=None, help="Center latitude.")
@click.option("--lng", type=float, default=None, help="Center longitude.")
@click.option("--max-results", "-n", default=20, help="Max results.")
@click.option("--offset", default=0, help="Pagination offset.")
@click.option("--output", "-o", default=None, help="Output file path.")
@click.option("--format", "fmt", type=click.Choice(["text", "json", "csv"]), default="text")
@click.option("--enrich", is_flag=True, help="Enable Phase 2 place details enrichment.")
@click.option(
    "--contacts",
    is_flag=True,
    help="Visit each business website and extract emails + social media URLs (LinkedIn, Facebook, Instagram, etc.).",
)
@click.option(
    "--max-contacts",
    type=click.IntRange(min=0),
    default=None,
    help="Maximum business websites to attempt (also enables --contacts).",
)
@click.option("--cookies", default=None, help="Login cookie string or file path (Mode 3).")
@click.pass_context
def search(
    ctx: click.Context,
    query: str,
    lat: float | None,
    lng: float | None,
    max_results: int,
    offset: int,
    output: str | None,
    fmt: str,
    enrich: bool,
    contacts: bool,
    max_contacts: int | None,
    cookies: str | None,
) -> None:
    """Search for places on Google Maps."""

    async def _search() -> None:
        client = _make_client(ctx, enrich, cookies)
        async with client:
            result = await client.search.places(
                query=query,
                latitude=lat or 0.0,
                longitude=lng or 0.0,
                max_results=max_results,
                offset=offset,
            )

            places = result.places

            if enrich:
                for p in places:
                    await client.enrich(p, query=query)

            if contacts or max_contacts is not None:
                click.echo(f"Extracting contacts from {len(places)} websites...", err=True)
                await client.extract_contacts(places, max_contacts=max_contacts)

            _output_places(places, fmt, output, query)

    _run_async(_search())


@main.command()
@click.argument("query")
@click.option("--bbox", required=True, help="Bounding box: min_lat,min_lon,max_lat,max_lon")
@click.option("--cell-size", default=0.5, help="Grid cell size in km (smaller=more coverage).")
@click.option("--zoom", default=16.0, help="Zoom level (15-17 for max density).")
@click.option("--max-results", "-n", default=500, help="Max total unique results.")
@click.option("--output", "-o", default=None, help="Output JSON file path.")
@click.option("--format", "fmt", type=click.Choice(["text", "json", "csv"]), default="text")
@click.option("--enrich", is_flag=True, help="Enable Phase 2 enrichment.")
@click.option(
    "--contacts",
    is_flag=True,
    help="Visit each business website and extract emails + social media URLs.",
)
@click.option(
    "--max-contacts",
    type=click.IntRange(min=0),
    default=None,
    help="Maximum business websites to attempt (also enables --contacts).",
)
@click.option("--cookies", default=None, help="Login cookie string or file path.")
@click.pass_context
def grid(
    ctx: click.Context,
    query: str,
    bbox: str,
    cell_size: float,
    zoom: float,
    max_results: int,
    output: str | None,
    fmt: str,
    enrich: bool,
    contacts: bool,
    max_contacts: int | None,
    cookies: str | None,
) -> None:
    """Grid search for comprehensive area coverage."""

    async def _grid() -> None:
        from .grid import BoundingBox

        parts = [float(x) for x in bbox.split(",")]
        if len(parts) != 4:
            click.echo("bbox must be: min_lat,min_lon,max_lat,max_lon", err=True)
            return

        box = BoundingBox(min_lat=parts[0], min_lon=parts[1], max_lat=parts[2], max_lon=parts[3])

        client = _make_client(ctx, enrich, cookies)
        async with client:
            results = await client.search.grid_search(
                query=query,
                bbox=box,
                cell_size_km=cell_size,
                max_results=max_results,
                zoom=zoom,
            )

            places = [p for p, _ in results]

            if enrich:
                for p in places:
                    await client.enrich(p, query=query)

            if contacts or max_contacts is not None:
                click.echo(f"Extracting contacts from {len(places)} websites...", err=True)
                await client.extract_contacts(places, max_contacts=max_contacts)

            _output_places(places, fmt, output, query)
            click.echo(f"\nGrid: {len(results)} places from {len({c for _, c in results})} cells")

    _run_async(_grid())


@main.command("rank-grid")
@click.argument("query")
@click.option(
    "--target",
    required=True,
    help="Target Place ID, CID, hex ID, or exact business name.",
)
@click.option(
    "--target-type",
    type=click.Choice(["auto", "place-id", "cid", "hex-id", "name"]),
    default="auto",
    show_default=True,
)
@click.option("--lat", type=click.FloatRange(min=-90, max=90), required=True)
@click.option("--lng", type=click.FloatRange(min=-180, max=180), required=True)
@click.option(
    "--grid-size",
    type=click.IntRange(min=1, max=15),
    default=5,
    show_default=True,
    help="Odd number of points per side.",
)
@click.option(
    "--spacing-km",
    type=click.FloatRange(min=0.01),
    default=1.0,
    show_default=True,
)
@click.option("--zoom", type=click.FloatRange(min=1, max=22), default=14.0, show_default=True)
@click.option(
    "--max-rank",
    type=click.IntRange(min=1, max=100),
    default=20,
    show_default=True,
)
@click.option(
    "--provider",
    type=click.Choice(["auto", "direct", "serper"]),
    default="auto",
    show_default=True,
    help="Auto uses direct Google Maps first and Serper as a configured fallback.",
)
@click.option("--output", "-o", default="rank-grid.json", show_default=True)
@click.option("--html-output", default="rank-grid.html", show_default=True)
@click.pass_context
def rank_grid(
    ctx: click.Context,
    query: str,
    target: str,
    target_type: str,
    lat: float,
    lng: float,
    grid_size: int,
    spacing_km: float,
    zoom: float,
    max_rank: int,
    provider: str,
    output: str,
    html_output: str,
) -> None:
    """Measure a business's Google Maps rank from a coordinate grid."""
    if grid_size % 2 == 0:
        raise click.BadParameter("must be odd", param_hint="--grid-size")
    if Path(output).expanduser().resolve() == Path(html_output).expanduser().resolve():
        raise click.UsageError("--output and --html-output must use different files.")

    serper_api_key = os.environ.get("SERPER_API_KEY", "").strip()
    if provider == "serper" and not serper_api_key:
        raise click.UsageError("Set SERPER_API_KEY before using --provider serper.")

    async def _rank_grid() -> None:
        from .rank_grid import (
            DirectGoogleMapsProvider,
            FallbackRankProvider,
            RankGridResult,
            RankGridScanner,
            RankSearchProvider,
            RankTarget,
            SerperMapsProvider,
            TargetKind,
            write_rank_grid_html,
            write_rank_grid_json,
        )

        rank_target = RankTarget(
            value=target,
            kind=cast(TargetKind, target_type.replace("-", "_")),
        )
        serper = (
            SerperMapsProvider(
                serper_api_key,
                language=ctx.obj["lang"],
                timeout=ctx.obj["timeout"],
                proxy=ctx.obj["proxy"],
            )
            if serper_api_key
            else None
        )

        async def run_scan(selected_provider: RankSearchProvider) -> RankGridResult:
            scanner = RankGridScanner(selected_provider)
            with click.progressbar(
                length=grid_size * grid_size,
                label="Scanning coordinates",
            ) as progress:
                return await scanner.scan(
                    query=query,
                    target=rank_target,
                    center_latitude=lat,
                    center_longitude=lng,
                    grid_size=grid_size,
                    spacing_km=spacing_km,
                    zoom=zoom,
                    max_rank=max_rank,
                    on_point=lambda _point: progress.update(1),
                )

        try:
            if provider == "serper":
                assert serper is not None
                result = await run_scan(serper)
            else:
                client = _make_client(ctx)
                async with client:
                    direct = DirectGoogleMapsProvider(client.search)
                    selected = (
                        FallbackRankProvider(direct, serper) if provider == "auto" else direct
                    )
                    result = await run_scan(selected)
        finally:
            if serper is not None:
                await serper.aclose()

        json_path = write_rank_grid_json(result, output)
        html_path = write_rank_grid_html(result, html_output)
        summary = result.summary()
        click.echo(
            f"Saved {summary['found_points']}/{summary['measured_points']} visible points "
            f"({summary['error_points']} errors) "
            f"to {json_path} and {html_path}"
        )

    _run_async(_rank_grid())


@main.command("rank-web")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", type=click.IntRange(min=1, max=65535), default=8765, show_default=True)
@click.pass_context
def rank_web(ctx: click.Context, host: str, port: int) -> None:
    """Run the local multi-keyword, multi-location rank tracker."""
    from .geocoding import NominatimResolver
    from .rank_grid import (
        DirectGoogleMapsProvider,
        FallbackRankProvider,
        RankGridScanner,
        RankSearchProvider,
        RankTarget,
        SerperMapsProvider,
        TargetKind,
    )
    from .rank_web import RankWebRequest, serve_rank_web

    async def scan_batch(payload: Mapping[str, object]) -> Mapping[str, object]:
        request = RankWebRequest.from_payload(payload)
        serper_api_key = os.environ.get("SERPER_API_KEY", "").strip()
        if request.provider == "serper" and not serper_api_key:
            raise ValueError("Set SERPER_API_KEY before using the Serper provider.")

        resolver = NominatimResolver(timeout=ctx.obj["timeout"])
        locations: list[dict[str, object]] = []
        for location in request.locations:
            if location.latitude is not None and location.longitude is not None:
                latitude = location.latitude
                longitude = location.longitude
            else:
                resolved = await resolver.resolve(location.name, language=ctx.obj["lang"])
                latitude, longitude = resolved.center
            locations.append(
                {
                    "name": location.name,
                    "latitude": latitude,
                    "longitude": longitude,
                }
            )

        rank_target = RankTarget(
            value=request.target,
            kind=cast(TargetKind, request.target_type),
        )
        serper = (
            SerperMapsProvider(
                serper_api_key,
                language=ctx.obj["lang"],
                timeout=ctx.obj["timeout"],
                proxy=ctx.obj["proxy"],
            )
            if serper_api_key
            else None
        )
        scans: list[dict[str, object]] = []

        async def run(selected_provider: RankSearchProvider) -> None:
            scanner = RankGridScanner(selected_provider)
            for location in locations:
                for keyword in request.keywords:
                    result = await scanner.scan(
                        query=keyword,
                        target=rank_target,
                        center_latitude=cast(float, location["latitude"]),
                        center_longitude=cast(float, location["longitude"]),
                        grid_size=request.grid_size,
                        spacing_km=request.spacing_km,
                        zoom=request.zoom,
                        max_rank=request.max_rank,
                    )
                    scans.append({"location": location, "result": result.to_dict()})

        try:
            if request.provider == "serper":
                assert serper is not None
                await run(serper)
            else:
                client = _make_client(ctx)
                async with client:
                    direct = DirectGoogleMapsProvider(client.search)
                    selected = (
                        FallbackRankProvider(direct, serper)
                        if request.provider == "auto"
                        else direct
                    )
                    await run(selected)
        finally:
            if serper is not None:
                await serper.aclose()

        results = [cast(dict[str, object], scan["result"]) for scan in scans]
        summaries = [cast(dict[str, object], result["summary"]) for result in results]
        best_ranks = [
            cast(int, summary["best_rank"])
            for summary in summaries
            if summary["best_rank"] is not None
        ]
        return {
            "request": {
                "target": request.target,
                "target_type": request.target_type,
                "keywords": list(request.keywords),
                "locations": locations,
                "grid_size": request.grid_size,
                "spacing_km": request.spacing_km,
                "zoom": request.zoom,
                "max_rank": request.max_rank,
                "provider": request.provider,
            },
            "summary": {
                "total_scans": len(scans),
                "total_points": sum(cast(int, summary["total_points"]) for summary in summaries),
                "measured_points": sum(
                    cast(int, summary["measured_points"]) for summary in summaries
                ),
                "found_points": sum(cast(int, summary["found_points"]) for summary in summaries),
                "error_points": sum(cast(int, summary["error_points"]) for summary in summaries),
                "best_rank": min(best_ranks) if best_ranks else None,
            },
            "scans": scans,
        }

    click.echo(f"Maps Visibility Grid: http://{host}:{port}")
    click.echo("Press Ctrl+C to stop.")
    try:
        serve_rank_web(lambda payload: _run_async(scan_batch(payload)), host=host, port=port)
    except KeyboardInterrupt:
        click.echo("\nStopped.")


@main.command()
@click.argument("query")
@click.option(
    "--location",
    help='Human-readable area, for example "Atlanta, Georgia".',
)
@click.option(
    "--bbox",
    default=None,
    help="Advanced boundary override: min_lat,min_lon,max_lat,max_lon.",
)
@click.option("--cell-size", type=float, default=None, help="Advanced grid cell size in km.")
@click.option("--max-results", "-n", type=click.IntRange(min=1), default=500)
@click.option("--output", "-o", default="gmaps-results.json", show_default=True)
@click.option("--enrich", is_flag=True, help="Fetch detailed Google Maps fields.")
@click.option("--contacts", is_flag=True, help="Extract emails and social media links.")
@click.option(
    "--max-contacts",
    type=click.IntRange(min=0),
    default=None,
    help="Maximum business websites to attempt (also enables --contacts).",
)
@click.option("--resume", is_flag=True, help="Continue from the output checkpoint.")
@click.option("--cookies", default=None, help="Login cookie string or file path.")
@click.pass_context
def collect(
    ctx: click.Context,
    query: str,
    location: str | None,
    bbox: str | None,
    cell_size: float | None,
    max_results: int,
    output: str,
    enrich: bool,
    contacts: bool,
    max_contacts: int | None,
    resume: bool,
    cookies: str | None,
) -> None:
    """Comprehensively collect businesses from a named place.

    Example: gmaps collect "chiropractors" --location "Atlanta, Georgia"
    """

    async def _collect() -> None:
        from .collection import CollectionRunner, CollectionState, CollectionStore, choose_cell_size
        from .geocoding import NominatimResolver
        from .grid import BoundingBox

        store = CollectionStore(output)
        if resume:
            state = store.load_state()
            if state.query != query:
                raise click.UsageError(f"Checkpoint query is {state.query!r}, not {query!r}.")
        else:
            if store.output_path.exists() or store.state_path.exists() or store.jsonl_path.exists():
                raise click.UsageError(
                    f"Output already exists for {output!r}; use --resume or choose another path."
                )
            if bbox:
                box = BoundingBox.from_string(bbox)
                resolved_location: dict[str, Any] = {
                    "query": location or "advanced bounding box",
                    "display_name": location or "Advanced bounding box",
                    "provider": "user",
                    "bbox": {
                        "min_lat": box.min_lat,
                        "min_lon": box.min_lon,
                        "max_lat": box.max_lat,
                        "max_lon": box.max_lon,
                    },
                }
            else:
                if not location:
                    raise click.UsageError("Provide --location, or use the advanced --bbox option.")
                click.echo(f"Resolving location: {location}", err=True)
                resolved = await NominatimResolver().resolve(location, language=ctx.obj["lang"])
                box = resolved.bbox
                resolved_location = resolved.to_dict()

            chosen_cell_size = cell_size or choose_cell_size(box)
            state = CollectionState(
                query=query,
                location=location or resolved_location["display_name"],
                bbox={
                    "min_lat": box.min_lat,
                    "min_lon": box.min_lon,
                    "max_lat": box.max_lat,
                    "max_lon": box.max_lon,
                },
                cell_size_km=chosen_cell_size,
                max_results=max_results,
                resolved_location=resolved_location,
                enrich=enrich,
                contacts=contacts or max_contacts is not None,
                max_contacts=max_contacts,
            )

        from .grid import estimate_cell_count

        planned_cells = estimate_cell_count(BoundingBox(**state.bbox), state.cell_size_km)
        phases = ["discovery"]
        if state.enrich:
            phases.append("enrichment")
        if state.contacts:
            phases.append(
                f"contacts (max {state.max_contacts})"
                if state.max_contacts is not None
                else "contacts (all eligible websites)"
            )
        click.echo(
            f"Plan: ~{planned_cells} seed cells at {state.cell_size_km:g} km | "
            f"phase1 mini-maps (fence-filtered seeds, max 6 pages/cell) | "
            f"UI z16 / protocol 13.1; phases: {', '.join(phases)}",
            err=True,
        )

        client = _make_client(ctx, state.enrich, cookies)
        async with client:
            runner = CollectionRunner(
                client=client,
                store=store,
                state=state,
                progress=lambda message: click.echo(message, err=True),
            )
            places, manifest = await runner.run()

        click.echo(f"Saved {len(places)} results to {store.output_path}")
        click.echo(f"Run status: {manifest['status']}")
        click.echo(f"Manifest: {store.manifest_path}")

    _run_async(_collect())


@main.command()
@click.argument("place_id")
@click.option("--output", "-o", default=None, help="Output JSON file path.")
@click.option("--enrich", is_flag=True, help="Use Phase 2 details endpoint.")
@click.option("--cookies", default=None, help="Login cookie string or file path.")
@click.pass_context
def place(
    ctx: click.Context,
    place_id: str,
    output: str | None,
    enrich: bool,
    cookies: str | None,
) -> None:
    """Get details about a place by its Google Maps place ID."""

    async def _place() -> None:
        client = _make_client(ctx, enrich, cookies)
        async with client:
            result = await client.places.get(place_id)
            if result is None:
                click.echo(f"Place not found: {place_id}", err=True)
                return
            data = result.to_dict()
            out = json.dumps(data, indent=2, ensure_ascii=output is None)
            if output:
                Path(output).write_text(out, encoding="utf-8")
                click.echo(f"Saved to {output}")
            else:
                click.echo(out)

    _run_async(_place())


@main.command()
@click.argument("hex_id")
@click.option(
    "--sort",
    "sort_by",
    type=click.Choice(["most_relevant", "newest", "highest_rating", "lowest_rating"]),
    default="most_relevant",
)
@click.option("--max", "max_reviews", default=20, help="Max reviews.")
@click.option("--output", "-o", default=None, help="Output JSON file path.")
@click.pass_context
def reviews(
    ctx: click.Context,
    hex_id: str,
    sort_by: str,
    max_reviews: int,
    output: str | None,
) -> None:
    """Fetch reviews for a place by its hex ID."""

    async def _reviews() -> None:
        async with GMapsClient(
            timeout=ctx.obj["timeout"],
            max_retries=ctx.obj["retries"],
            language=ctx.obj["lang"],
            proxy=ctx.obj["proxy"],
        ) as client:
            result = await client.reviews.list(
                hex_id=hex_id,
                sort_by=sort_by,
                max_reviews=max_reviews,
            )

            data = [
                {
                    "author": r.get("author_name", "Anonymous"),
                    "rating": r.get("rating", 0),
                    "text": r.get("text", ""),
                    "timestamp": r.get("timestamp", ""),
                }
                for r in result.reviews
            ]

            out = json.dumps(data, indent=2, ensure_ascii=output is None)
            if output:
                Path(output).write_text(out, encoding="utf-8")
                click.echo(f"Saved {len(data)} reviews to {output}")
            else:
                click.echo(out)

    _run_async(_reviews())


if __name__ == "__main__":
    main()
