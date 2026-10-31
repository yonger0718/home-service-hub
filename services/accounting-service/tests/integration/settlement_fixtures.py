"""R7 Phase 1 settlement fixtures (portfolio integration spec §4.3–§4.7): a settlement-source bank account and its
target, bank statements printing settlement lines, a stubbed portfolio ledger feed served over `httpx.MockTransport`
(filtered by the request's two axes as the portfolio does, bounds echoed) and stand-in bookings written in the
Task 9 job's shape; `stand_ins_on` switches the stand-in job on and `ingest_fixture` opens an ingest run on another
session. The accounts are the fixture's own — no owner account ids anywhere. Fictional amounts only."""
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from app.models import ReconciliationSettings, SettlementBooking, SettlementBookingItem
from app.schemas.statements import ReconciliationSettingsIn
from app.schemas.writes import TransferIn
from app.services import portfolio_client, settings_service, transfer_service
from app.services import statement_ingest_service as ing
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


def put_settings(db, **blocks) -> dict:
    """`settings_service.update_reconciliation_settings` with the stored blocks kept and `blocks` replacing theirs (a
    PUT replaces every block it carries); committed."""
    row = db.get(ReconciliationSettings, 1)
    stored = dict(row.data or {}) if row is not None else {}
    data = {key: stored[key] for key in ReconciliationSettingsIn.model_fields if key in stored}
    out = settings_service.update_reconciliation_settings(db, {**data, **blocks})
    db.commit()
    return out


def settlement_bank(seed, db, *, target=True):
    """(a live bank account with `settlement_source`, its target account). Live from 2026-01-01 so every period the
    tests use is a live statement. `target=True` maps the bank to the target in `settlement.target_accounts` (the
    other `settlement` keys as stored)."""
    bank = seed.account("交割戶", statement_live_from=date(2026, 1, 1), settlement_source=SOURCE)
    db.commit()
    market = settlement_target(seed, db)
    if target:
        block = settings_service.read_settlement_settings(db)
        stored = {k: v for k, v in block.items() if k in settings_service.SETTLEMENT_DEFAULTS}
        put_settings(db, settlement={**stored, "target_accounts": {**stored["target_accounts"], str(bank.id): market.id}})
    return bank, market


@pytest.fixture
def stand_ins_on(db_session):
    """`stand_ins_on(bank, target, *, enabled_from)`: `settlement.stand_in_enabled = true`, `enabled_from` (ISO) and
    `target_accounts[bank] = target`, the other `settlement` keys and every other block as stored (GET the current
    settings, PUT them back with the block merged: the endpoint resets omitted blocks); committed."""
    def on(bank, target, *, enabled_from: str) -> dict:
        block = settings_service.read_settlement_settings(db_session)
        stored = {k: v for k, v in block.items() if k in settings_service.SETTLEMENT_DEFAULTS}
        return put_settings(db_session, settlement={
            **stored, "stand_in_enabled": True, "enabled_from": enabled_from,
            "target_accounts": {**stored["target_accounts"], str(bank.id): target.id}})
    return on


def ingest_fixture(session):
    """A claimed worker ingest run on `session` (the `statement_fixtures.run` fixture's `(run, token)` pair), for a
    second session submitting a statement while the test's own session holds its state."""
    r = ing.create_worker_run(session, trigger="timer", initiator_hint=None, mode="live")
    return ing.claim_run(session, r.id, label="worker")


@pytest.fixture
def policy_on(db_session):
    """`policy_on(**caps)`: the policy enabled with `book_settlement` on and the dirty triggers on (the owner-twin
    re-check needs the writer barrier), written through `update_reconciliation_settings`; `caps` are policy keys
    (`per_statement_count`, `per_statement_total`, `monthly_total`, ...), and `max_amount_everywhere` sets every
    action's `max_amount`."""
    def on(*, max_amount_everywhere: str | None = None, **caps):
        actions = {name: {"max_amount": max_amount_everywhere} for name in settings_service.POLICY_DEFAULTS["actions"]} \
            if max_amount_everywhere is not None else {}
        actions["book_settlement"] = {**actions.get("book_settlement", {}), "enabled": True}
        return put_settings(db_session, dirty_enabled=True, policy={"enabled": True, "actions": actions, **caps})
    return on


def submit_bank(db, run, bank, lines, **kw) -> int:
    """A bank statement through the real ingest path (the hook reconciles, then the routes' post-commit neighbour
    flag runs as they run it); its id."""
    statement, _, res = _submit(db, run, bank, lines, kind="bank", **kw)
    if res.neighbours_recheck:
        tss.flag_neighbours_safe(db, statement.id)
    return statement.id


def bank_line(seq, day, merchant, flow):
    flow = Decimal(str(flow))
    return _line(seq, day, merchant, flow, "withdrawal" if flow < 0 else "deposit")


def settle_line(seq, day, flow):
    return bank_line(seq, day, "交割股款", flow)


def _money(value) -> str:
    return f"{Decimal(str(value)).quantize(Decimal('0.0001'))}"


def dividend_line(seq, day, flow, merchant="現金股利"):
    return bank_line(seq, day, merchant, flow)


def dividend_row(id: int, receipt_date: str, amount, *, symbol="9999", currency="TWD") -> dict:
    """A dividend feed row (the portfolio's JSON): a confirmed receipt, `amount` net of 匯費 / 補充保費."""
    return {"id": id, "symbol": symbol, "receipt_date": receipt_date, "amount": _money(amount), "currency": currency,
            "fingerprint": f"dividend:{id}"}


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
    echoed; `.down = True` → 503; `.fail_after = n` → 503 from the request after the n-th on; `.narrow = True` → the
    echoed `from` is one day later; `.calls` = each request's query params. The dividends path serves `.dividends`
    with `receipt_date` in `from … to` and `complete: .dividends_complete`; its params go to `.dividend_calls` only."""
    stub = SimpleNamespace(rows=[], down=False, fail_after=None, narrow=False, calls=[], dividends=[],
                           dividends_complete=True, dividend_calls=[])

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if request.url.path.endswith("/dividends"):
            stub.dividend_calls.append(params)
            if stub.down:
                return httpx.Response(503)
            return httpx.Response(200, json={
                "rows": [r for r in stub.dividends if params["from"] <= r["receipt_date"] <= params["to"]],
                "complete": stub.dividends_complete})
        stub.calls.append(params)
        if stub.down or (stub.fail_after is not None and len(stub.calls) > stub.fail_after):
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
