"""Read-only client for the portfolio service's ledger feed (國泰 settlements and a point-in-time valuation).

Every failure surfaces as `PortfolioUnavailable` whose message is its code only, so neither the token nor a
response body can reach a log line. Completeness is asserted here, once: a `Feed` that exists is complete.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

import httpx

_SETTLEMENTS_PATH = "/api/portfolio/ledger-feed/settlements"
_VALUATION_PATH = "/api/portfolio/ledger-feed/valuation"


class PortfolioUnavailable(RuntimeError):
    """connect | timeout | http_<status> | bad_body | incomplete | calendar (the last is raised by callers, not here)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Item:
    source_id: str
    type: str
    amount: Decimal


@dataclass(frozen=True)
class Settlement:
    trade_date: date
    settlement_date: date | None
    date_source: str
    net_amount: Decimal
    legs_net: Decimal
    currency: str
    bookable: bool
    reason: str | None
    items: tuple[Item, ...]
    fingerprint: str

    @property
    def source_ids(self) -> tuple[str, ...]:
        return tuple(sorted(item.source_id for item in self.items))


@dataclass(frozen=True)
class Bounds:
    settle_from: date
    settle_to: date
    trade_from: date
    trade_to: date


@dataclass(frozen=True)
class Feed:
    rows: tuple[Settlement, ...]
    bounds: Bounds
    complete: bool  # always True once the client returns it; kept so the object states its own contract


@dataclass(frozen=True)
class Valuation:
    broker: str
    currency: str
    as_of: date
    priced_through: date | None
    market_value: Decimal
    cash: Decimal
    total: Decimal
    complete: bool
    missing: tuple[str, ...]


def _date(value: object) -> date:
    return date.fromisoformat(value)  # type: ignore[arg-type]


def _opt_date(value: object) -> date | None:
    return None if value is None else _date(value)


def _dec(value: object) -> Decimal:
    if not isinstance(value, str):
        raise ValueError("amount must be a string")
    return Decimal(value)


def _item(raw: dict) -> Item:
    return Item(source_id=raw["source_id"], type=raw["type"], amount=_dec(raw["amount"]))


def _settlement(raw: dict) -> Settlement:
    return Settlement(
        trade_date=_date(raw["trade_date"]),
        settlement_date=_opt_date(raw["settlement_date"]),
        date_source=raw["date_source"],
        net_amount=_dec(raw["net_amount"]),
        legs_net=_dec(raw["legs_net"]),
        currency=raw["currency"],
        bookable=bool(raw["bookable"]),
        reason=raw["reason"],
        items=tuple(_item(i) for i in raw["items"]),
        fingerprint=raw["fingerprint"],
    )


def _bounds(raw: dict) -> Bounds:
    return Bounds(_date(raw["settle_from"]), _date(raw["settle_to"]), _date(raw["from"]), _date(raw["to"]))


def _valuation(raw: dict) -> Valuation:
    return Valuation(
        broker=raw["broker"],
        currency=raw["currency"],
        as_of=_date(raw["as_of"]),
        priced_through=_opt_date(raw["priced_through"]),
        market_value=_dec(raw["market_value"]),
        cash=_dec(raw["cash"]),
        total=_dec(raw["total"]),
        complete=bool(raw["complete"]),
        missing=tuple(raw["missing"]),
    )


class PortfolioClient:
    __repr__ = object.__repr__  # the default repr carries no attributes, so the token cannot leak through it

    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        *,
        timeout: float = 5.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._timeout = timeout
        self._transport = transport

    def _get(self, path: str, params: dict[str, str]) -> dict:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            with httpx.Client(base_url=self.base_url, timeout=self._timeout, transport=self._transport) as http:
                response = http.get(path, params=params, headers=headers)
        except httpx.TimeoutException:
            raise PortfolioUnavailable("timeout") from None
        except httpx.HTTPError:
            raise PortfolioUnavailable("connect") from None
        if response.status_code != 200:
            raise PortfolioUnavailable(f"http_{response.status_code}")
        try:
            body = response.json()
        except ValueError:
            raise PortfolioUnavailable("bad_body") from None
        if not isinstance(body, dict):
            raise PortfolioUnavailable("bad_body")
        return body

    def settlements(self, broker: str, *, settle_from: date, settle_to: date, trade_from: date, trade_to: date) -> Feed:
        body = self._get(
            _SETTLEMENTS_PATH,
            {
                "broker": broker,
                "settle_from": settle_from.isoformat(),
                "settle_to": settle_to.isoformat(),
                "from": trade_from.isoformat(),
                "to": trade_to.isoformat(),
            },
        )
        try:
            bounds = _bounds(body["bounds"])
            rows = tuple(_settlement(r) for r in body["rows"])
            complete = body["complete"]
        except (KeyError, TypeError, ValueError, InvalidOperation):
            raise PortfolioUnavailable("bad_body") from None
        if complete is not True or bounds != Bounds(settle_from, settle_to, trade_from, trade_to):
            raise PortfolioUnavailable("incomplete")
        return Feed(rows=rows, bounds=bounds, complete=True)

    def valuation(self, broker: str, as_of: date) -> Valuation:
        body = self._get(_VALUATION_PATH, {"broker": broker, "as_of": as_of.isoformat()})
        try:
            return _valuation(body)
        except (KeyError, TypeError, ValueError, InvalidOperation):
            raise PortfolioUnavailable("bad_body") from None


class _CachedClient:
    """Memoises reads per (method, args) for the length of one `cached()` block."""

    __repr__ = object.__repr__

    def __init__(self, client: PortfolioClient) -> None:
        self._client = client
        self._memo: dict[tuple, object] = {}

    def settlements(self, broker: str, **window: date) -> Feed:
        key = ("settlements", broker, tuple(sorted(window.items())))
        if key not in self._memo:
            self._memo[key] = self._client.settlements(broker, **window)
        return self._memo[key]  # type: ignore[return-value]

    def valuation(self, broker: str, as_of: date) -> Valuation:
        key = ("valuation", broker, as_of)
        if key not in self._memo:
            self._memo[key] = self._client.valuation(broker, as_of)
        return self._memo[key]  # type: ignore[return-value]


def client_for() -> PortfolioClient:
    return PortfolioClient(
        os.environ.get("PORTFOLIO_BASE_URL") or "http://127.0.0.1:8001",
        os.environ.get("PORTFOLIO_READ_TOKEN") or None,
    )


@contextmanager
def cached(client: PortfolioClient) -> Iterator[PortfolioClient]:
    yield _CachedClient(client)  # type: ignore[misc]  # same two read methods; callers type it as PortfolioClient
