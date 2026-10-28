"""Consent-cache persistence uses only synthetic cookies and temporary files."""

import json
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from gmaps._auth.session import CookieSession
from gmaps.client import GMapsClient

CONSENT = {"NID": "synthetic-nid", "AEC": "synthetic-aec", "SOCS": "synthetic-socs"}


def test_cache_roundtrip_is_private_and_atomic(tmp_path, monkeypatch):
    path = tmp_path / "nested" / "cookies.json"
    session = CookieSession(cookie_file=str(path))
    session.cookies.update(CONSENT)
    original_replace = os.replace
    replacements = []

    def replace(source, dest):
        # The cache appears only after the complete replacement is ready.
        assert not path.exists()
        assert json.loads(Path(source).read_text())["cookies"] == CONSENT
        replacements.append((source, dest))
        original_replace(source, dest)

    monkeypatch.setattr(os, "replace", replace)
    session._save_to_file()
    assert len(replacements) == 1
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
    assert list(path.parent.iterdir()) == [path]
    loaded = CookieSession(cookie_file=str(path))
    loaded._load_from_file()
    assert loaded.cookies == CONSENT and loaded.is_valid


@pytest.mark.skipif(os.name == "nt", reason="Windows symlinks require extra privileges")
def test_saving_replaces_symlink_without_overwriting_its_target(tmp_path):
    target = tmp_path / "unrelated.txt"
    target.write_text("keep this file")
    path = tmp_path / "cookies.json"
    path.symlink_to(target)
    session = CookieSession(cookie_file=str(path))
    session.cookies.update(CONSENT)
    session._save_to_file()
    assert not path.is_symlink()
    assert target.read_text() == "keep this file"
    assert json.loads(path.read_text())["cookies"] == CONSENT


def test_failed_save_keeps_previous_cache_and_cleans_temporary_file(tmp_path, monkeypatch):
    path = tmp_path / "cookies.json"
    path.write_text("previous complete file")
    session = CookieSession(cookie_file=str(path))
    session.cookies.update(CONSENT)

    def fail(_source, _dest):
        raise OSError("synthetic write failure")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="synthetic write failure"):
        session._save_to_file()
    assert path.read_text() == "previous complete file"
    assert list(tmp_path.iterdir()) == [path]


def test_only_consent_cookies_are_saved(tmp_path):
    path = tmp_path / "cookies.json"
    session = CookieSession(cookie_file=str(path))
    session.cookies.update({**CONSENT, "SID": "synthetic-login", "OTHER": "synthetic-other"})
    session._save_to_file()
    assert json.loads(path.read_text())["cookies"] == CONSENT
    assert "synthetic-login" not in path.read_text()


def test_only_consent_cookies_are_loaded_from_cache(tmp_path, caplog):
    path = tmp_path / "cookies.json"
    path.write_text(
        json.dumps(
            {
                "cookies": {**CONSENT, "SID": "synthetic-login", "HSID": "synthetic-login"},
                "saved_at": time.time(),
            }
        )
    )
    session = CookieSession(cookie_file=str(path))
    session._load_from_file()
    assert session.cookies == CONSENT
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "Ignoring 2 non-consent cookie(s)" in warnings[0].getMessage()
    assert "synthetic-login" not in caplog.text


@pytest.mark.parametrize(
    "data",
    [
        [],
        None,
        "not an object",
        {"cookies": []},
        {"cookies": None},
        {"cookies": CONSENT, "saved_at": "invalid"},
    ],
)
def test_malformed_cache_is_ignored(tmp_path, data):
    path = tmp_path / "cookies.json"
    path.write_text(json.dumps(data))
    session = CookieSession(cookie_file=str(path))
    session._load_from_file()
    assert not session.cookies


@pytest.mark.parametrize("saved_at", [0, time.time() - 7 * 3600])
def test_expired_cache_is_ignored(tmp_path, saved_at):
    path = tmp_path / "cookies.json"
    path.write_text(json.dumps({"cookies": CONSENT, "saved_at": saved_at}))
    session = CookieSession(cookie_file=str(path))
    session._load_from_file()
    assert not session.cookies


def test_non_string_cookie_values_are_ignored(tmp_path):
    path = tmp_path / "cookies.json"
    path.write_text(
        json.dumps(
            {"cookies": {"NID": {}, "AEC": 123, "SOCS": "synthetic"}, "saved_at": time.time()}
        )
    )
    session = CookieSession(cookie_file=str(path))
    session._load_from_file()
    assert session.cookies == {"SOCS": "synthetic"}


def test_unreadable_cache_does_not_log_file_contents(tmp_path, caplog):
    path = tmp_path / "cookies.json"
    path.write_text("synthetic-sensitive invalid JSON")
    session = CookieSession(cookie_file=str(path))
    session._load_from_file()
    assert not session.cookies
    assert "synthetic-sensitive" not in caplog.text


def test_invalid_utf8_cache_is_ignored_without_logging_contents(tmp_path, caplog):
    path = tmp_path / "cookies.json"
    path.write_bytes(b'{"cookies": "synthetic-sensitive\xff"}')
    session = CookieSession(cookie_file=str(path))
    session._load_from_file()
    assert not session.cookies
    assert "synthetic-sensitive" not in caplog.text


async def test_explicit_login_cookies_reach_transport_without_entering_consent_cache(
    tmp_path, monkeypatch, httpx_mock
):
    monkeypatch.setattr("gmaps._auth.session.asyncio.sleep", AsyncMock())
    path = tmp_path / "cookies.json"
    for _ in range(4):
        httpx_mock.add_response(
            json={},
            headers=[("Set-Cookie", "NID=synthetic-nid"), ("Set-Cookie", "AEC=synthetic-aec")],
        )
    httpx_mock.add_response(json={})
    async with GMapsClient(
        cookie_file=str(path),
        login_cookies="SID=explicit-synthetic; HSID=explicit-synthetic",
        min_delay=0,
    ) as client:
        assert await client._transport.get("/maps/probe", response_type="json") == {}
        assert "SID" not in client._cookie_session.cookies
        assert "HSID" not in client._cookie_session.cookies

    requests = httpx_mock.get_requests()
    assert len(requests) == 5
    assert all("explicit-synthetic" not in r.headers.get("cookie", "") for r in requests[:4])
    assert "SID=explicit-synthetic" in requests[-1].headers["cookie"]
    assert "HSID=explicit-synthetic" in requests[-1].headers["cookie"]
    cached = json.loads(path.read_text())["cookies"]
    assert set(cached) == {"NID", "AEC", "SOCS"}
    assert "explicit-synthetic" not in path.read_text()
