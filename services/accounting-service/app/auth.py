"""Optional bearer-token auth for API clients (agents, scripts and the SPA).

`ACCOUNTING_API_TOKENS` is a comma-separated list of `label:token` or bare `token` values. Unset or empty, the
middleware is a pure pass-through and every request behaves as before. Set, every request except `GET /health`
(and `GET /docs`, `GET /openapi.json` when `ACCOUNTING_DOCS_PUBLIC=true`) needs `Authorization: Bearer <token>`
matching one configured token; otherwise 401 `{"detail": "unauthorized"}`. The matching token's label is put on
`request.state.client_label` (None for a bare token). Tokens are never logged.

Both variables are read per request (like ACCOUNTING_IMPORT_LOCKED) so tests can toggle them; parsing is cached.
"""

import hmac
import os
from functools import lru_cache

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

TOKENS_ENV = "ACCOUNTING_API_TOKENS"
DOCS_PUBLIC_ENV = "ACCOUNTING_DOCS_PUBLIC"
ALWAYS_PUBLIC = frozenset({"/health"})
DOCS_PATHS = frozenset({"/docs", "/openapi.json"})


@lru_cache(maxsize=8)
def parse_tokens(raw: str | None) -> tuple[tuple[str | None, str], ...]:
    """`"spa:abc,agent:def,ghi"` -> `(("spa", "abc"), ("agent", "def"), (None, "ghi"))`.

    The label ends at the first colon, so a token containing a colon needs a label. An item with an empty token
    raises ValueError (naming the label, never a token) rather than silently leaving auth off.
    """
    pairs = []
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        label, sep, token = item.partition(":")
        if not sep:
            label, token = "", item
        label, token = label.strip(), token.strip()
        if not token:
            raise ValueError(f"{TOKENS_ENV}: empty token for label {label!r}")
        pairs.append((label or None, token))
    return tuple(pairs)


def configured_tokens() -> tuple[tuple[str | None, str], ...]:
    return parse_tokens(os.getenv(TOKENS_ENV))


def docs_public() -> bool:
    return os.getenv(DOCS_PUBLIC_ENV, "").strip().lower() == "true"


def api_auth_status() -> str:
    """The startup log line; names labels and a count, never a token."""
    tokens = configured_tokens()
    if not tokens:
        return "API auth: disabled"
    labels = ", ".join(label or "(unlabelled)" for label, _ in tokens)
    return f"API auth: enabled ({len(tokens)} tokens: {labels})"


def _bearer(scope: Scope) -> str | None:
    for name, value in scope.get("headers") or ():
        if name == b"authorization":
            scheme, _, credentials = value.decode("latin-1").partition(" ")
            if scheme.lower() == "bearer" and credentials.strip():
                return credentials.strip()
            return None
    return None


def _match(presented: str, tokens: tuple[tuple[str | None, str], ...]) -> tuple[bool, str | None]:
    """Constant-time per token, and every configured token is compared (no early exit)."""
    presented_bytes = presented.encode()
    found, found_label = False, None
    for label, token in tokens:
        if hmac.compare_digest(presented_bytes, token.encode()) and not found:
            found, found_label = True, label
    return found, found_label


class ApiTokenMiddleware:
    """Pure ASGI middleware; with no tokens configured it forwards scope, receive and send untouched."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        tokens = configured_tokens()
        if not tokens or self._exempt(scope):
            await self.app(scope, receive, send)
            return
        presented = _bearer(scope)
        ok, label = _match(presented, tokens) if presented is not None else (False, None)
        if not ok:
            response = JSONResponse({"detail": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
            await response(scope, receive, send)
            return
        scope.setdefault("state", {})["client_label"] = label
        await self.app(scope, receive, send)

    @staticmethod
    def _exempt(scope: Scope) -> bool:
        if scope.get("method") != "GET":
            return False
        path = scope.get("path", "")
        return path in ALWAYS_PUBLIC or (path in DOCS_PATHS and docs_public())
