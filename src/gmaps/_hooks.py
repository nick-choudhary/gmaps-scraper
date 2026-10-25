"""Adapt caller request hooks without treating their failures as network errors."""

from collections.abc import Awaitable, Callable

import httpx

from .exceptions import RequestHookError

RequestHook = Callable[[httpx.Request], Awaitable[None]]


def _request_hooks(hook: RequestHook | None) -> list[RequestHook]:
    if hook is None:
        return []

    async def run(request: httpx.Request) -> None:
        try:
            await hook(request)
        except Exception as exc:
            raise RequestHookError("Request hook failed") from exc

    return [run]
