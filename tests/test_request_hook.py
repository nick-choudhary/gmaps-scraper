"""Request hooks run on the real httpx path, before mocked network I/O."""

from unittest.mock import AsyncMock

import httpx
import pytest

from gmaps import GMapsClient, RequestHookError
from gmaps._auth.session import CookieSession
from gmaps.transport import HTTPTransport


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize("with_hook", [False, True])
async def test_transport_hooks_include_every_redirect(httpx_mock, method, with_hook):
    seen = []

    async def hook(request):
        assert len(httpx_mock.get_requests()) == len(seen)
        seen.append(str(request.url))
        request.headers["X-Caller"] = "observed"

    def respond(request):
        assert request.headers.get("X-Caller") == ("observed" if with_hook else None)
        if request.url.path == "/start":
            return httpx.Response(307, headers={"Location": "/middle"})
        if request.url.path == "/middle":
            return httpx.Response(307, headers={"Location": "https://other.example.test/end"})
        return httpx.Response(200, json={"ok": True})

    for _ in range(3):
        httpx_mock.add_callback(respond)
    async with HTTPTransport(
        base_url="https://maps.example.test",
        min_delay=0,
        request_hook=hook if with_hook else None,
    ) as transport:
        assert await getattr(transport, method)("/start", response_type="json") == {"ok": True}

    sent = httpx_mock.get_requests()
    assert [r.url.path for r in sent] == ["/start", "/middle", "/end"]
    assert [r.method for r in sent] == [method.upper()] * 3
    assert seen == ([str(r.url) for r in sent] if with_hook else [])


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize("refused_path", ["/start", "/middle", "/end"])
@pytest.mark.parametrize("error_type", [ValueError, httpx.ConnectError, httpx.ReadTimeout])
async def test_transport_hook_failures_are_not_sent_or_retried(
    httpx_mock, monkeypatch, method, refused_path, error_type
):
    failure = error_type("caller refusal")
    seen = []
    backoff = AsyncMock()
    monkeypatch.setattr("gmaps.transport._exponential_backoff", backoff)

    async def hook(request):
        seen.append(request.url.path)
        if request.url.path == refused_path:
            raise failure

    def respond(request):
        if request.url.path == "/start":
            return httpx.Response(307, headers={"Location": "/middle"})
        return httpx.Response(307, headers={"Location": "https://other.example.test/end"})

    paths = ["/start", "/middle", "/end"]
    stop = paths.index(refused_path)
    for _ in range(stop):
        httpx_mock.add_callback(respond)
    async with HTTPTransport(
        base_url="https://maps.example.test", min_delay=0, max_retries=3, request_hook=hook
    ) as transport:
        with pytest.raises(RequestHookError, match="^Request hook failed$") as exc:
            await getattr(transport, method)("/start")

    assert exc.value.__cause__ is failure
    assert seen == paths[: stop + 1]
    assert [r.url.path for r in httpx_mock.get_requests()] == paths[:stop]
    backoff.assert_not_awaited()


@pytest.mark.parametrize("with_hook", [False, True])
async def test_client_passes_hook_to_consent_and_transport(
    httpx_mock, monkeypatch, tmp_path, with_hook
):
    monkeypatch.setattr("gmaps._auth.session.asyncio.sleep", AsyncMock())
    seen = []

    async def hook(request):
        assert len(httpx_mock.get_requests()) == len(seen)
        seen.append(str(request.url))
        request.headers["X-Caller"] = "observed"

    def respond(request):
        assert request.headers.get("X-Caller") == ("observed" if with_hook else None)
        if request.url.host == "consent.google.com" and request.url.path == "/ml":
            return httpx.Response(302, headers={"Location": "/after-consent"})
        return httpx.Response(
            200,
            json={"ok": True},
            headers=[("Set-Cookie", "NID=synthetic"), ("Set-Cookie", "AEC=synthetic")],
        )

    for _ in range(7):
        httpx_mock.add_callback(respond)
    async with GMapsClient(
        cookie_file=str(tmp_path / "cookies.json"),
        min_delay=0,
        request_hook=hook if with_hook else None,
    ) as client:
        assert client._cookie_session.is_valid
        assert await client._transport.get("/ordinary", response_type="json") == {"ok": True}
        assert await client._transport.post("/ordinary", response_type="json") == {"ok": True}

    sent = httpx_mock.get_requests()
    assert [r.url.path for r in sent] == [
        "/",
        "/ml",
        "/after-consent",
        "/save",
        "/maps",
        "/ordinary",
        "/ordinary",
    ]
    assert seen == ([str(r.url) for r in sent] if with_hook else [])


@pytest.mark.parametrize("refused_path", ["/", "/ml", "/save", "/maps"])
@pytest.mark.parametrize("redirect", [False, True])
async def test_consent_refusal_stops_setup_and_closes_client(
    httpx_mock, monkeypatch, tmp_path, refused_path, redirect
):
    monkeypatch.setattr("gmaps._auth.session.asyncio.sleep", AsyncMock())
    original_init = CookieSession._init_client
    opened = []

    async def capture_client(session):
        await original_init(session)
        opened.append(session._client)

    monkeypatch.setattr(CookieSession, "_init_client", capture_client)
    failure = RuntimeError("caller refusal")
    seen = []

    async def hook(request):
        seen.append(request.url.path)
        if request.url.path == ("/refused" if redirect else refused_path):
            raise failure

    def respond(request):
        if redirect and request.url.path == refused_path:
            return httpx.Response(302, headers={"Location": "https://other.example.test/refused"})
        return httpx.Response(200, json={})

    paths = ["/", "/ml", "/save", "/maps"]
    stop = paths.index(refused_path)
    for _ in range(stop + int(redirect)):
        httpx_mock.add_callback(respond)
    cookie_path = tmp_path / "cookies.json"
    client = GMapsClient(cookie_file=str(cookie_path), request_hook=hook)
    with pytest.raises(RequestHookError, match="^Request hook failed$") as exc:
        async with client:
            pytest.fail("refused client must not open")

    assert exc.value.__cause__ is failure
    assert seen == (paths[: stop + 1] + ["/refused"] if redirect else paths[: stop + 1])
    assert [r.url.path for r in httpx_mock.get_requests()] == paths[: stop + int(redirect)]
    assert len(opened) == 1 and opened[0].is_closed
    assert client._cookie_session._client is None
    assert client._transport._client is None
    assert not client._opened
    assert not cookie_path.exists()


async def test_reopening_transport_retains_hook(httpx_mock):
    seen = []

    async def hook(request):
        seen.append(request.url.path)

    for _ in range(2):
        httpx_mock.add_response(json={})
    async with HTTPTransport(
        base_url="https://maps.example.test", min_delay=0, request_hook=hook
    ) as transport:
        await transport.get("/first", response_type="json")
        await transport.refresh_session()
        await transport.get("/second", response_type="json")

    assert seen == ["/first", "/second"]


async def test_cookie_refresh_retains_hook(httpx_mock, monkeypatch):
    monkeypatch.setattr("gmaps._auth.session.asyncio.sleep", AsyncMock())
    seen = []

    async def hook(request):
        seen.append(request.url.path)

    for _ in range(8):
        httpx_mock.add_response(json={})
    async with CookieSession(request_hook=hook) as session:
        await session.refresh()

    assert seen == ["/", "/ml", "/save", "/maps"] * 2
