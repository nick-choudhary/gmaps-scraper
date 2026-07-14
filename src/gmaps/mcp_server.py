"""MCP (Model Context Protocol) server for gmaps-scraper.

Exposes the scraper as tools callable by AI agents (Claude, Cursor, etc.).

Run standalone:
    python -m gmaps.mcp_server

Or add to Claude Desktop config:
    {
        "mcpServers": {
            "gmaps": {
                "command": "python",
                "args": ["-m", "gmaps.mcp_server"]
            }
        }
    }
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

# MCP server using stdio transport (works with Claude Desktop, Cursor, etc.)
# Falls back to simple JSON-RPC over stdio if mcp package not installed.

try:
    import mcp.types as mcp_types
    from mcp.server import Server
    from mcp.server.stdio import stdio_server

    _HAS_MCP = True
except ImportError:
    _HAS_MCP = False

from gmaps.client import GMapsClient
from gmaps.grid import BoundingBox


async def _do_search(
    query: str,
    lat: float = 0.0,
    lng: float = 0.0,
    max_results: int = 20,
    enrich: bool = False,
    contacts: bool = False,
) -> list[dict[str, Any]]:
    """Execute a search and return list of grouped JSON dicts."""
    async with GMapsClient(enrich=enrich) as client:
        result = await client.search.places(
            query=query,
            latitude=lat,
            longitude=lng,
            max_results=max_results,
        )
        places = result.places
        if enrich:
            for p in places:
                await client.enrich(p, query=query)
        if contacts:
            await client.extract_contacts(places)
        return [p.to_dict() for p in places]


async def _do_grid_search(
    query: str,
    min_lat: float,
    min_lon: float,
    max_lat: float,
    max_lon: float,
    cell_size_km: float = 0.5,
    max_results: int = 500,
    enrich: bool = False,
    contacts: bool = False,
) -> list[dict[str, Any]]:
    """Execute a grid search and return list of grouped JSON dicts."""
    async with GMapsClient(enrich=enrich) as client:
        bbox = BoundingBox(min_lat=min_lat, min_lon=min_lon, max_lat=max_lat, max_lon=max_lon)
        results = await client.search.grid_search(
            query=query,
            bbox=bbox,
            cell_size_km=cell_size_km,
            max_results=max_results,
        )
        places = [p for p, _ in results]
        if contacts:
            await client.extract_contacts(places)
        return [p.to_dict() for p in places]


async def _do_place_details(
    place_id: str,
    hex_id: str,
    ftid: str,
    data_id: str,
    name: str,
    lat: float = 0.0,
    lng: float = 0.0,
) -> dict[str, Any]:
    """Fetch place details for a single place."""
    async with GMapsClient(enrich=True) as client:
        from gmaps.rpc.parser import ParsedPlace

        p = ParsedPlace(
            name=name,
            place_id=place_id,
            hex_id=hex_id,
            ftid=ftid,
            data_id=data_id,
            latitude=lat,
            longitude=lng,
        )
        await client.enrich(p)
        return p.to_dict()


if _HAS_MCP:
    # Full MCP server
    server = Server("gmaps-scraper")

    @server.list_tools()
    async def list_tools() -> list[Any]:
        return [
            mcp_types.Tool(
                name="search",
                description="Search for businesses on Google Maps. Returns name, address, phone, website, rating, categories, coordinates.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query (e.g., 'coffee shops', 'hvac')",
                        },
                        "lat": {"type": "number", "description": "Center latitude", "default": 0},
                        "lng": {"type": "number", "description": "Center longitude", "default": 0},
                        "max_results": {"type": "integer", "default": 20, "maximum": 120},
                        "enrich": {
                            "type": "boolean",
                            "default": False,
                            "description": "Fetch detailed info (review_count, hours, thumbnail)",
                        },
                        "contacts": {
                            "type": "boolean",
                            "default": False,
                            "description": "Visit each business website and extract emails + social media URLs (LinkedIn, Facebook, Instagram, etc.)",
                        },
                    },
                    "required": ["query"],
                },
            ),
            mcp_types.Tool(
                name="grid_search",
                description="Grid search for comprehensive area coverage. Overcomes Google's 120-result limit by dividing area into cells. Good for scraping thousands of businesses.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "min_lat": {"type": "number"},
                        "min_lon": {"type": "number"},
                        "max_lat": {"type": "number"},
                        "max_lon": {"type": "number"},
                        "cell_size_km": {"type": "number", "default": 0.5},
                        "max_results": {"type": "integer", "default": 500},
                        "enrich": {"type": "boolean", "default": False},
                        "contacts": {
                            "type": "boolean",
                            "default": False,
                            "description": "Extract emails + social media URLs from business websites",
                        },
                    },
                    "required": ["query", "min_lat", "min_lon", "max_lat", "max_lon"],
                },
            ),
            mcp_types.Tool(
                name="place_details",
                description="Get detailed information for a single place (review_count, hours, plus_code, thumbnail, owner). Requires place identifiers from a prior search.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "place_id": {"type": "string"},
                        "hex_id": {"type": "string"},
                        "ftid": {"type": "string"},
                        "data_id": {"type": "string"},
                        "name": {"type": "string"},
                        "lat": {"type": "number"},
                        "lng": {"type": "number"},
                    },
                    "required": ["place_id", "hex_id", "ftid", "data_id", "name"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[Any]:
        if name == "search":
            results = await _do_search(**arguments)
        elif name == "grid_search":
            results = await _do_grid_search(**arguments)
        elif name == "place_details":
            results = [await _do_place_details(**arguments)]
        else:
            results = [{"error": f"Unknown tool: {name}"}]

        return [
            mcp_types.TextContent(
                type="text",
                text=json.dumps(results, indent=2, ensure_ascii=True),
            )
        ]

    async def main() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

else:
    # Fallback: simple JSON-RPC over stdio (no mcp package needed)
    async def main() -> None:
        print("gmaps-scraper MCP server (fallback mode)", file=sys.stderr)
        print("Install 'mcp' package for full protocol support", file=sys.stderr)

        while True:
            line = await asyncio.get_event_loop().run_in_executor(None, sys.stdin.readline)
            if not line:
                break
            try:
                req = json.loads(line)
                tool = req.get("tool") or req.get("method", "")
                args = req.get("arguments") or req.get("params", {})

                if tool in ("search", "tools/call/search"):
                    results = await _do_search(**args)
                elif tool in ("grid_search", "tools/call/grid_search"):
                    results = await _do_grid_search(**args)
                elif tool in ("place_details", "tools/call/place_details"):
                    results = [await _do_place_details(**args)]
                else:
                    results = [{"error": f"Unknown tool: {tool}"}]

                response = {"jsonrpc": "2.0", "id": req.get("id", 0), "result": results}
                sys.stdout.write(json.dumps(response, ensure_ascii=True) + "\n")
                sys.stdout.flush()
            except Exception as e:
                response = {
                    "jsonrpc": "2.0",
                    "id": req.get("id", 0) if isinstance(req, dict) else 0,
                    "error": {"code": -32603, "message": str(e)},
                }
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()


if __name__ == "__main__":
    asyncio.run(main())
