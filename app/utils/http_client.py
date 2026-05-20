"""Shared async HTTP clients with keep-alive connection reuse."""

from __future__ import annotations

import httpx

_CLIENTS: dict[float, httpx.AsyncClient] = {}


def shared_async_client(*, timeout: float = 20.0) -> httpx.AsyncClient:
    client = _CLIENTS.get(timeout)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(timeout=timeout)
        _CLIENTS[timeout] = client
    return client


async def close_shared_async_clients() -> None:
    for client in list(_CLIENTS.values()):
        if not client.is_closed:
            await client.aclose()
    _CLIENTS.clear()
