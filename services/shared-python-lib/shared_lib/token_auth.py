"""Bearer-token auth with per-label read/write scopes for a service's API (issue #117).

`install_token_auth(app, prefix="PORTFOLIO")` reads three variables per request (parsing is cached):

- `<PREFIX>_API_TOKENS`: comma-separated `label:token` items. Unset or empty, the middleware is a pure pass-through
  and every request behaves as before (the rollout turns auth on last, after every client sends a token).
- `<PREFIX>_TOKEN_SCOPES`: `label=read,write; label=read`. Required whenever tokens are set: every label needs an entry
  and every entry needs a token. `read` covers GET and HEAD; `write` covers every other method.
- `<PREFIX>_DOCS_PUBLIC`: `true` keeps the docs pages (`/docs`, `/redoc`, `/openapi.json`) open without a token.

`GET`/`HEAD` on `/health` and `/health/ready` never need a token. A missing or unknown token is 401
`{"detail": "unauthorized"}`; a known token without the method's scope is 403 `{"detail": "scope"}`. A configuration
the startup check would refuse (tokens set at runtime by a test, say) answers 500 `{"detail": "auth misconfigured"}`
rather than passing the request through. Tokens are never logged or put in an error message; labels may be.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache

from fastapi import FastAPI
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

ALWAYS_PUBLIC = frozenset({"/health", "/health/ready"})
READ_METHODS = frozenset({"GET", "HEAD"})
VALID_SCOPES = frozenset({"read", "write"})


@dataclass(frozen=True)
class EnvNames:
    tokens: str
    scopes: str
    docs_public: str

    @classmethod
    def for_prefix(cls, prefix: str) -> "EnvNames":
        return cls(f"{prefix}_API_TOKENS", f"{prefix}_TOKEN_SCOPES", f"{prefix}_DOCS_PUBLIC")


@dataclass(frozen=True)
class AuthConfig:
    tokens: tuple[tuple[str, bytes], ...]  # (label, sha256(token))
    scopes: dict[str, frozenset[str]]

    @property
    def enabled(self) -> bool:
        return bool(self.tokens)


def _parse_tokens(raw: str, env: str) -> list[tuple[str, str]]:
    pairs = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        label, sep, token = item.partition(":")
        label, token = label.strip(), token.strip()
        if not sep or not label:
            raise ValueError(f"{env}: every item needs a label (label:token)")
        if not token:
            raise ValueError(f"{env}: empty token for label {label!r}")
        pairs.append((label, token))
    return pairs


def _parse_scopes(raw: str, env: str) -> dict[str, frozenset[str]]:
    out: dict[str, frozenset[str]] = {}
    for item in raw.split(";"):
        item = item.strip()
        if not item:
            continue
        label, sep, text = item.partition("=")
        label = label.strip()
        if not sep or not label:
            raise ValueError(f"{env}: malformed item {item!r}")
        if label in out:
            raise ValueError(f"{env}: duplicate label {label!r}")
        scopes = frozenset(s.strip() for s in text.split(",") if s.strip())
        if not scopes:
            raise ValueError(f"{env}: no scopes for label {label!r}")
        unknown = scopes - VALID_SCOPES
        if unknown:
            raise ValueError(f"{env}: unknown scope {sorted(unknown)!r} for label {label!r}")
        out[label] = scopes
    return out


@lru_cache(maxsize=16)
def _build(raw_tokens: str, raw_scopes: str, names: EnvNames) -> AuthConfig:
    pairs = _parse_tokens(raw_tokens, names.tokens)
    if not pairs:
        return AuthConfig((), {})
    labels = [label for label, _ in pairs]
    if len(set(labels)) != len(labels):
        raise ValueError(f"{names.tokens}: duplicate labels")
    if len({token for _, token in pairs}) != len(pairs):
        raise ValueError(f"{names.tokens}: duplicate token values")
    if not raw_scopes.strip():
        raise ValueError(f"{names.tokens} is set: {names.scopes} is required")
    scopes = _parse_scopes(raw_scopes, names.scopes)
    missing = set(labels) - set(scopes)
    if missing:
        raise ValueError(f"{names.scopes}: labels without a scope entry: {sorted(missing)!r}")
    ghosts = set(scopes) - set(labels)
    if ghosts:
        raise ValueError(f"{names.scopes}: labels without a token: {sorted(ghosts)!r}")
    return AuthConfig(tuple((label, _digest(token.encode())) for label, token in pairs), scopes)


def load_config(names: EnvNames) -> AuthConfig:
    """The current configuration; raises ValueError (naming variables and labels, never a token) when malformed."""
    return _build(os.getenv(names.tokens) or "", os.getenv(names.scopes) or "", names)


def auth_status(names: EnvNames) -> str:
    """The startup log line; labels and a count, never a token."""
    config = load_config(names)
    if not config.enabled:
        return "API auth: disabled"
    labels = ", ".join(f"{label}={'+'.join(sorted(config.scopes[label]))}" for label, _ in config.tokens)
    return f"API auth: enabled ({len(config.tokens)} tokens: {labels})"


def _digest(value: bytes) -> bytes:
    return hashlib.sha256(value).digest()


def _bearer(scope: Scope) -> bytes | None:
    """The token from exactly `<scheme> <token>`: scheme `Bearer` in any case (RFC 7235), one space, no other whitespace."""
    for name, value in scope.get("headers") or ():
        if name == b"authorization":
            if value[:7].lower() != b"bearer ":
                return None
            token = value[7:]
            if not token or any(ch in token for ch in b" \t\r\n"):
                return None
            return token
    return None


def _match(presented: bytes, config: AuthConfig) -> str | None:
    """Compare digests of equal length against every configured token with no early exit; the matching label or None."""
    presented_digest = _digest(presented)
    found = None
    for label, digest in config.tokens:
        if hmac.compare_digest(presented_digest, digest) and found is None:
            found = label
    return found


def _json(status: int, detail: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status, headers=headers)


class TokenAuthMiddleware:
    """Pure ASGI middleware; with no tokens configured it forwards scope, receive and send untouched."""

    def __init__(self, app: ASGIApp, names: EnvNames, docs_paths: Iterable[str] = ()) -> None:
        self.app = app
        self.names = names
        self.docs_paths = frozenset(docs_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        try:
            config = load_config(self.names)
        except ValueError:
            await _json(500, "auth misconfigured")(scope, receive, send)
            return
        if not config.enabled or self._exempt(scope):
            await self.app(scope, receive, send)
            return
        presented = _bearer(scope)
        label = _match(presented, config) if presented is not None else None
        if label is None:
            await _json(401, "unauthorized", {"WWW-Authenticate": "Bearer"})(scope, receive, send)
            return
        needed = "read" if scope.get("method") in READ_METHODS else "write"
        if needed not in config.scopes[label]:
            await _json(403, "scope")(scope, receive, send)
            return
        scope.setdefault("state", {})["client_label"] = label
        await self.app(scope, receive, send)

    def _exempt(self, scope: Scope) -> bool:
        if scope.get("method") not in READ_METHODS:
            return False
        path = scope.get("path", "")
        if path in ALWAYS_PUBLIC:
            return True
        return path in self.docs_paths and (os.getenv(self.names.docs_public) or "").strip().lower() == "true"


def docs_paths(app: FastAPI) -> frozenset[str]:
    """The app's enabled docs, ReDoc, OAuth2 redirect (only while the Swagger UI is served) and OpenAPI paths."""
    names = ["docs_url", "redoc_url", "openapi_url"]
    if getattr(app, "docs_url", None):
        names.append("swagger_ui_oauth2_redirect_url")
    return frozenset(path for path in (getattr(app, name, None) for name in names) if path)


def install_token_auth(app: FastAPI, prefix: str) -> str:
    """Validate the configuration now (a malformed one stops startup) and add the middleware as the outermost layer.

    Returns the startup log line (`auth_status`).
    """
    names = EnvNames.for_prefix(prefix)
    status = auth_status(names)
    app.add_middleware(TokenAuthMiddleware, names=names, docs_paths=docs_paths(app))
    return status
