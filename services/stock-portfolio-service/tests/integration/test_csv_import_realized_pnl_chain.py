"""CSV import to snapshot realized-PnL parity coverage."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from app.models.portfolio_snapshot import PortfolioSnapshot
from app.models.price_history import PriceHistory
from app.services import broker_cathay_service
from app.services.networth_backfill_service import replay_snapshots_range


_CATHAY_HEADER = (
    "股名,日期,成交股數,淨收付金額,買賣別,成交價,成本,手續費,交易稅,"
    "融資金額/券擔保品,資自備款/券保證金,利息,稅款,券手續費/標借費,委託書號\n"
)


def _cathay_csv(*rows: str) -> bytes:
    return (
        "根據您篩選的結果，總計有4筆資料\n" + _CATHAY_HEADER + "\n".join(rows) + "\n"
    ).encode("utf-8")


def _row(
    *,
    name: str,
    trade_date: str,
    side: str,
    price: str,
    order_id: str,
    tax: str = "0",
) -> str:
    return (
        f'{name},{trade_date},"100","0",{side},"{price}","0",0,{tax},'
        f"0,0,0,0,0,{order_id}"
    )


def _seed_price(db: Any, *, symbol: str, d: date, close: str) -> None:
    db.add(
        PriceHistory(
            symbol=symbol,
            date=d,
            close=Decimal(close),
            source="TWSE",
        )
    )
    db.flush()


def test_cathay_csv_import_snapshot_realized_pnl_matches_endpoint(
    client: Any,
    db_session: Any,
    monkeypatch: Any,
) -> None:
    monkeypatch.setitem(broker_cathay_service.NAME_TO_SYMBOL, "融券股", ["7788"])
    monkeypatch.setitem(broker_cathay_service.NAME_TO_SYMBOL, "沖賣股", ["8899"])

    result = broker_cathay_service.parse_cathay_transactions_csv(
        _cathay_csv(
            _row(
                name="融券股",
                trade_date="2026/05/11",
                side="券賣",
                price="100",
                order_id="s-open",
            ),
            _row(
                name="融券股",
                trade_date="2026/05/12",
                side="券買",
                price="90",
                order_id="s-cover",
            ),
            _row(
                name="沖賣股",
                trade_date="2026/05/13",
                side="沖買",
                price="50",
                order_id="dt-buy",
            ),
            _row(
                name="沖賣股",
                trade_date="2026/05/13",
                side="沖賣",
                price="55",
                tax="8",
                order_id="dt-sell",
            ),
        ),
        dry_run=False,
        db=db_session,
    )
    assert result.errors == []
    assert result.created == 4

    for d in (date(2026, 5, 11), date(2026, 5, 12), date(2026, 5, 13)):
        _seed_price(db_session, symbol="7788", d=d, close="95")
        _seed_price(db_session, symbol="8899", d=d, close="55")
    db_session.commit()

    replay_snapshots_range(db_session, date(2026, 5, 11), date(2026, 5, 13))

    snapshot = db_session.get(PortfolioSnapshot, date(2026, 5, 13))
    endpoint = client.get("/api/portfolio/realized-pnl")

    assert endpoint.status_code == 200
    assert snapshot is not None
    assert Decimal(endpoint.json()["summary"]["filter_scope_total"]) == Decimal(
        snapshot.total_realized_pnl
    )
