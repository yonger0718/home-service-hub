"""httpx.MockTransport fake serving canned JSON per path; records every request."""
from __future__ import annotations

from typing import Any

import httpx


def make_client(routes: dict[str, Any]):
    """routes: path -> JSON value | httpx.Response | list (served in order, last one repeats)."""
    log: list[httpx.Request] = []
    counters: dict[str, int] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        log.append(request)
        path = request.url.path
        if path not in routes:
            return httpx.Response(404, json={"detail": "no route"})
        value = routes[path]
        if isinstance(value, list) and value and isinstance(value[0], httpx.Response):
            i = counters.get(path, 0)
            counters[path] = i + 1
            value = value[min(i, len(value) - 1)]
        elif isinstance(value, dict) and "__pages__" in value:
            i = counters.get(path, 0)
            counters[path] = i + 1
            value = value["__pages__"][min(i, len(value["__pages__"]) - 1)]
        if isinstance(value, httpx.Response):
            return value
        return httpx.Response(200, json=value)

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://api"), log
