"""Regression tests for Google Maps response framing."""

from __future__ import annotations

import json

from gmaps.rpc.decoder import decode_response


def test_decode_pagination_xhr_wrapper() -> None:
    inner = ')]}\'\n[["business", 20]]'
    wrapped = json.dumps({"c": 0, "d": inner}) + '/*""*/'

    assert decode_response(wrapped) == [["business", 20]]
