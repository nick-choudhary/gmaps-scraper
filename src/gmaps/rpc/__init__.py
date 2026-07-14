"""RPC protocol implementation for Google Maps internal API.

Google Maps uses multiple internal endpoints (unlike NotebookLM's single
batchexecute endpoint). This module provides a unified RPC framework.
"""

from __future__ import annotations

from .decoder import decode_response, strip_anti_xssi
from .encoder import build_request_params, encode_rpc_request
from .parser import (
    ParsedPlace,
    parse_place_details_response,
    parse_reviews_response,
    parse_search_response,
)
from .pb_encoder import (
    build_place_details_pb,
    build_reviews_pb,
    build_search_pb,
    decode_pb,
    encode_pb,
)
from .types import (
    GMAPS_BASE_URL,
    GMAPS_PLACE_URL,
    GMAPS_SEARCH_URL,
    PlaceField,
    RPCMethod,
    SearchType,
)

__all__ = [
    "GMAPS_BASE_URL",
    "GMAPS_SEARCH_URL",
    "GMAPS_PLACE_URL",
    "RPCMethod",
    "SearchType",
    "PlaceField",
    "encode_rpc_request",
    "build_request_params",
    "decode_response",
    "strip_anti_xssi",
    "encode_pb",
    "decode_pb",
    "build_search_pb",
    "build_place_details_pb",
    "build_reviews_pb",
    "parse_search_response",
    "parse_place_details_response",
    "parse_reviews_response",
    "ParsedPlace",
]
