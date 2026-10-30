"""R7 Phase 1 settlement fixtures (portfolio integration spec §4.3–§4.7): a settlement-source bank account and its
target, bank statements printing settlement lines, a stubbed portfolio ledger feed served over `httpx.MockTransport`
(filtered by the request's two axes as the portfolio does, bounds echoed) and stand-in bookings written in the
Task 9 job's shape. The accounts are the fixture's own — no owner account ids anywhere. Fictional amounts only."""
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from app.models import SettlementBooking, SettlementBookingItem
from app.schemas.writes import TransferIn
from app.services import portfolio_client, transfer_service
from app.services import trade_settlement_service as tss
from app.services.entry_write_service import STATEMENT_SOURCE
from app.services.portfolio_client import Item, Settlement
from tests.integration.statement_fixtures import _line, _submit

AUG = (date(2026, 8, 1), date(2026, 8, 31))
SOURCE = "portfolio:cathay"


def settlement_target(seed, db):
    """The TWD market-value account a settlement transfers to."""
    target = seed.account("證券市值")
    db.commit()
    return target


def settlement_bank(seed, db):
    """(a live bank account with `settlement_source`, its target account). Live from 2026-01-01 so every period the
    tests use is a live statement."""
    bank = seed.account("交割戶", statement_live_from=date(2026, 1, 1), settlement_source=SOURCE)
    db.commit()
    return bank, settlement_target(seed, db)


def submit_bank(db, run, bank, lines, **kw) -> int:
    """A bank statement through the real ingest path (the hook reconciles); its id."""
    statement, _, _ = _submit(db, run, bank, lines, kind="bank", **kw)
    return statement.id


def bank_line(seq, day, merchant, flow):
    flow = Decimal(str(flow))
    return _line(seq, day, merchant, flow, "withdrawal" if flow < 0 else "deposit")


def settle_line(seq, day, flow):
    return bank_line(seq, day, "交割股款", flow)


def _money(value) -> str:
    return f"{Decimal(str(value)).quantize(Decimal('0.0001'))}"


def _amounts(net, ids, amounts):
    if amounts is not None:
        return [Decimal(str(a)) for a in amounts]
    return [Decimal(str(net))] + [Decimal(0)] * (len(ids) - 1)


def _fingerprint(trade_date, ids, fingerprint):
    return fingerprint or f"cathay:{trade_date}:{','.join(ids)}"


def feed(*rows, fingerprint=None, amounts=None) -> list[dict]:
    """Feed rows (the portfolio's JSON) from `(trade_date, settlement_date | None, net, ids[, overrides])`: the whole
    net on the first item and 0 on the rest unless `amounts` says otherwise; `overrides` patch `bookable` / `reason` /
    `date_source`; a dated row is a statement day (`date_source "statement"`), an undated one a CSV-only day."""
    out = []
    for row in rows:
        trade_date, settlement_date, net, ids, *rest = row
        values = _amounts(net, ids, amounts)
        item = {"trade_date": trade_date, "settlement_date": settlement_date,
                "date_source": "statement" if settlement_date else "legs", "net_amount": _money(net),
                "legs_net": _money(net), "currency": "TWD", "bookable": True, "reason": None,
                "items": [{"source_id": i, "type": ("buy_settle" if Decimal(str(net)) < 0 else "sell_settle")
                           if n == 0 else "fee", "amount": _money(a)} for n, (i, a) in enumerate(zip(ids, values))],
                "fingerprint": _fingerprint(trade_date, ids, fingerprint)}
        item.update(rest[0] if rest else {})
        out.append(item)
    return out


@pytest.fixture
def portfolio_stub(monkeypatch):
    """`portfolio_client.client_for` → a client over a MockTransport serving `.rows` filtered by the two axes (trade
    date in `from … to`, or a printed `settlement_date` in `settle_from … settle_to`) with the request's bounds
    echoed; `.down = True` → 503; `.narrow = True` → the echoed `from` is one day later; `.calls` = each request's
    query params."""
    stub = SimpleNamespace(rows=[], down=False, narrow=False, calls=[])

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        stub.calls.append(params)
        if stub.down:
            return httpx.Response(503)
        lo, hi = date.fromisoformat(params["from"]), date.fromisoformat(params["to"])
        s_lo, s_hi = date.fromisoformat(params["settle_from"]), date.fromisoformat(params["settle_to"])

        def wanted(row) -> bool:
            if lo <= date.fromisoformat(row["trade_date"]) <= hi:
                return True
            return row["settlement_date"] is not None and s_lo <= date.fromisoformat(row["settlement_date"]) <= s_hi

        echoed = (lo + timedelta(days=1)) if stub.narrow else lo
        return httpx.Response(200, json={
            "rows": [r for r in stub.rows if wanted(r)],
            "bounds": {"settle_from": params["settle_from"], "settle_to": params["settle_to"],
                       "from": echoed.isoformat(), "to": params["to"]},
            "complete": True})

    monkeypatch.setattr(portfolio_client, "client_for",
                        lambda: portfolio_client.PortfolioClient("http://portfolio.test",
                                                                 transport=httpx.MockTransport(handler)))
    return stub


def seed_stand_in(db, bank, target, *, settlement_date, net, fingerprint, ids, origin="stand_in", amounts=None,
                  trade_date=None) -> SettlementBooking:
    """A `booked` settlement row of `origin` with its items over a `create_transfer` pair of statement source (bank →
    target for a negative net, target → bank for a positive one), both legs dated the settlement date — the Task 9
    job's shape, written directly. `facts` is `facts_of` of the same values `feed()` builds, so a feed row with the
    same arguments compares equal (M6)."""
    net = Decimal(str(net))
    trade_date = trade_date or date.fromisoformat(fingerprint.split(":")[1])
    values = _amounts(net, ids, amounts)
    settlement = Settlement(
        trade_date=trade_date, settlement_date=settlement_date, date_source="statement", net_amount=net, legs_net=net,
        currency="TWD", bookable=True, reason=None, fingerprint=fingerprint,
        items=tuple(Item(i, ("buy_settle" if net < 0 else "sell_settle") if n == 0 else "fee", a)
                    for n, (i, a) in enumerate(zip(ids, values))))
    source, sink = (bank, target) if net < 0 else (target, bank)
    group_id = transfer_service.create_transfer(db, TransferIn(
        from_account_id=source.id, to_account_id=sink.id, out_amount=abs(net), entry_date=settlement_date,
        posted_date=settlement_date, name="證券交割", description=f"證券交割 {trade_date}"),
        source=STATEMENT_SOURCE, remember=False)
    out_leg, in_leg = transfer_service.transfer_legs(db, group_id)
    row = SettlementBooking(
        kind="settlement", origin=origin, account_id=bank.id, target_account_id=target.id, source=SOURCE,
        fingerprint=fingerprint, trade_date=trade_date, settlement_date=settlement_date, net_amount=net,
        facts=tss.facts_of(settlement), transfer_group_id=group_id, out_entry_id=out_leg.id, in_entry_id=in_leg.id,
        status="booked", items=[SettlementBookingItem(source=i.split(":")[0], source_id=i, amount=a)
                                for i, a in zip(ids, values)])
    db.add(row)
    db.commit()
    return row

