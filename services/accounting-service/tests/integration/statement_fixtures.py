"""Shared R1b/R1c statement fixtures for the integration tests: a claimed worker run, revision submission through
the real ingest path (the hook reconciles unless `matcher_off`), card/transfer builders and coverage/case readers.
Synthetic data only."""
import threading
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from app.models import AccountStatement, ReconciliationCase, StatementCoverage, StatementLine
from app.schemas.statements import LineIn, RevisionIn
from app.services import reconciliation_service as rec
from app.services import statement_ingest_service as ing
from app.services import statement_revision_service as revs
from tests.helpers import wait_until_blocked

SEP = (date(2026, 9, 1), date(2026, 9, 30))
OCT = (date(2026, 10, 1), date(2026, 10, 31))


@pytest.fixture
def run(db_session):
    r = ing.create_worker_run(db_session, trigger="timer", initiator_hint=None, mode="live")
    r, token = ing.claim_run(db_session, r.id, label="worker")
    return r, token


def _line(seq, day, merchant, printed, kind="purchase", **extra) -> LineIn:
    return LineIn(seq=seq, posted_date=day, merchant_raw=merchant, printed_amount=Decimal(str(printed)), line_kind=kind,
                  **extra)


def _flow(kind: str, line: LineIn) -> Decimal:
    from app.services.statements.derive import flow_amount
    return flow_amount(kind, line.line_kind, line.printed_amount)


def _submit(db, run, account, lines, *, kind="card", period=SEP, opening=None, total=None):
    """Submit a revision whose header passes the guardrails (unless `total` overrides it); the hook reconciles."""
    r, token = run
    flows = sum((_flow(kind, line) for line in lines), Decimal(0))
    if kind == "card":
        expected = Decimal(opening or 0) - flows
    else:
        opening = Decimal(opening if opening is not None else 0)
        expected = opening + flows
    rev = RevisionIn(run_id=r.id, lease_token=token, file_id=None, account_id=account.id, kind=kind, parser="t",
                     parser_version="1", currency=account.currency, period_start=period[0], period_end=period[1],
                     opening_balance=opening, statement_total=expected if total is None else Decimal(total),
                     lines=lines, raw={})
    res = revs.submit_revision(db, r, rev, account_map={})
    db.commit()
    lines = db.query(StatementLine).filter_by(revision_id=res.revision.id).order_by(StatementLine.seq).all()
    return db.get(AccountStatement, res.statement.id), lines, res


def _active(db, statement_id):
    db.expire_all()
    return db.execute(select(StatementCoverage).where(StatementCoverage.statement_id == statement_id,
                                                      StatementCoverage.status == "active")
                      .order_by(StatementCoverage.id)).scalars().all()


def _cases(db, statement_id, *statuses):
    db.expire_all()
    q = select(ReconciliationCase).where(ReconciliationCase.statement_id == statement_id)
    if statuses:
        q = q.where(ReconciliationCase.status.in_(statuses))
    return db.execute(q.order_by(ReconciliationCase.id)).scalars().all()


def _card(seed, db, name="卡", *, live=True, **extra):
    extra.setdefault("closing_day", 30)
    card = seed.account(name, is_credit=True, statement_live_from=date(2026, 9, 1) if live else None, **extra)
    db.commit()
    return card


def _transfer(seed, source, target, amount, day):
    pair = uuid.uuid4()
    out_leg = seed.entry(source, f"-{amount}", kind="transfer_out", day=day, transfer_group_id=pair)
    in_leg = seed.entry(target, str(amount), kind="transfer_in", day=day, transfer_group_id=pair)
    return out_leg, in_leg


def _batch(db, **kw) -> list[dict]:
    """The Task 7 route's loop: one commit per yielded statement (reconcile_each never commits)."""
    out = []
    for item in rec.reconcile_each(db, rec.pending_statement_ids(db), **kw):
        db.commit()
        out.append(item)
    return out


def _reconcile(db, statement_id, **kw):
    result = rec.reconcile(db, statement_id, **kw)
    db.commit()
    return result


def _code(outcome) -> str | None:
    return getattr(outcome, "code", None)


def _interleave(pg_engine, first, second, swept, go) -> dict:
    """first(session) runs in a thread until it pauses after its sweep; second(session) then runs in a thread until
    it blocks on a lock; first resumes. Returns {"first", "second"}: "committed" or the exception raised."""
    factory = sessionmaker(bind=pg_engine, autoflush=False)
    sessions = {"first": factory(), "second": factory()}
    out: dict = {}

    def runner(key, fn):
        session = sessions[key]
        try:
            fn(session)
            session.commit()
            out[key] = "committed"
        except Exception as exc:  # noqa: BLE001  (asserted by the caller)
            session.rollback()
            out[key] = exc

    # the transactions stay open so each session keeps this connection (and pid) for its function
    first_pid, second_pid = (sessions[key].execute(text("SELECT pg_backend_pid()")).scalar_one()
                             for key in ("first", "second"))
    threads = [threading.Thread(target=runner, args=("first", first), daemon=True),
               threading.Thread(target=runner, args=("second", second), daemon=True)]
    threads[0].start()
    try:
        assert swept.wait(10)
        threads[1].start()
        # on a row (coverage/case/statement) the paused first() holds
        wait_until_blocked(pg_engine, second_pid, blocker_pid=first_pid)
    finally:
        go.set()
        for thread in threads:
            thread.join(15)
    assert not any(thread.is_alive() for thread in threads), out
    for session in sessions.values():
        session.close()
    return out
