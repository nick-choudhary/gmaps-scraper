"""Tests for the cookie-session validity contract."""

from gmaps._auth.session import REQUIRED_COOKIES, CookieSession


def test_required_cookies_match_live_maps_session() -> None:
    assert REQUIRED_COOKIES == ("NID", "AEC", "SOCS")


def test_session_is_valid_without_legacy_consent_cookie() -> None:
    session = CookieSession()
    session.cookies.update({"NID": "nid", "AEC": "aec", "SOCS": "socs"})

    assert session.is_valid


def test_session_is_invalid_when_a_required_cookie_is_missing() -> None:
    session = CookieSession()
    session.cookies.update({"NID": "nid", "AEC": "aec"})

    assert not session.is_valid
