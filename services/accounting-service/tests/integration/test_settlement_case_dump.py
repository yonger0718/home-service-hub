"""R7 Phase 1 dry run (runbook step 5, final review I2): `scripts/r6_case_dump.py` derives the settlement outcomes the
reconcile would write — read only, inside `SET TRANSACTION READ ONLY` as the script runs — and
`r6_dryrun.settlements_listing` over that dump lists them. Fictional amounts; the fixture's own accounts only."""
import importlib.util
from datetime import date
from pathlib import Path

from sqlalchemy import func, select, text

from app.models import AccountStatement, ReconciliationAudit, SettlementBooking
from tests.integration.settlement_fixtures import (  # noqa: F401  (portfolio_stub is a fixture)
    feed, portfolio_stub, seed_stand_in, settle_line, settlement_bank, submit_bank,
)
from tests.integration.statement_fixtures import run  # noqa: F401
from tests.integration.test_settlement_reconcile import _cases

SCRIPTS = Path(__file__).parents[2] / "scripts"


def _script(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _dump(db, sid) -> dict:
    """`_statement` of the statement as `main` runs it: one READ ONLY transaction, rolled back (a write would raise)."""
    db.commit()
    db.execute(text("SET TRANSACTION READ ONLY"))
    try:
        return _script("r6_case_dump")._statement(db, db.get(AccountStatement, sid))
    finally:
        db.rollback()


def _stored(db, sid):
    return sorted((c.event_id, c.kind, (c.context or {}).get("hint")) for c in _cases(db, sid, "open", "proposed"))


def _dumped(dump):
    return sorted((c[0], c[1], c[2]) for c in dump["cases"] if c[1] != "balance_gap")


def _listing(sid, dump):
    return _script("r6_dryrun").settlements_listing({"sha": "x", "statements": {str(sid): dump}}, None)["lines"]


def test_the_dump_carries_a_settlement_case_and_the_listing_shows_book_settlement(seed, db_session, run, portfolio_stub):
    bank, _ = settlement_bank(seed, db_session)
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)))
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")])
    stored = _stored(db_session, sid)
    dump = _dump(db_session, sid)
    assert _dumped(dump) == stored and [k for _, k, _ in stored] == ["settlement"]
    fingerprint = portfolio_stub.rows[0]["fingerprint"]
    ((event_id, *_),) = stored
    assert _listing(sid, dump) == [{"statement_id": str(sid), "event_id": event_id, "outcome": "book_settlement",
                                    "kind": "settlement", "hint": None, "candidates": [fingerprint]}]


def test_the_dump_carries_the_pending_hint_while_the_feed_is_down(seed, db_session, run, portfolio_stub):
    bank, _ = settlement_bank(seed, db_session)
    portfolio_stub.down = True
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")])
    dump = _dump(db_session, sid)
    assert _dumped(dump) == _stored(db_session, sid)
    assert [(r["outcome"], r["kind"], r["hint"]) for r in _listing(sid, dump)] == [
        ("pending", "line_unmatched", "settlement_pending")]


def test_the_dump_previews_a_settlement_the_stored_cases_do_not_hold_yet(seed, db_session, run, portfolio_stub):
    """The step-5 shape: the statement was reconciled while the feed held nothing for the line (pending); the feed now
    answers. The dump shows the booking the next reconcile would propose, and writes nothing."""
    bank, _ = settlement_bank(seed, db_session)
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")])
    assert [h for _, _, h in _stored(db_session, sid)] == ["settlement_pending"]
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)))
    audits = db_session.scalar(select(func.count(ReconciliationAudit.id)))
    dump = _dump(db_session, sid)
    assert [(r["outcome"], r["kind"]) for r in _listing(sid, dump)] == [("book_settlement", "settlement")]
    assert [h for _, _, h in _stored(db_session, sid)] == ["settlement_pending"]            # nothing written
    assert db_session.scalar(select(func.count(ReconciliationAudit.id))) == audits


def test_the_dump_lists_a_stand_in_claim(seed, db_session, run, portfolio_stub):
    bank, target = settlement_bank(seed, db_session)
    fingerprint = "cathay:2026-09-10:ct:5"
    row = seed_stand_in(db_session, bank, target, settlement_date=date(2026, 9, 14), net="-12305",
                        fingerprint=fingerprint, ids=("ct:5",))
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)))
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")])
    dump = _dump(db_session, sid)
    assert [(r["outcome"], r["entries"]) for r in _listing(sid, dump)] == [
        ("stand_in_claim", [row.out_entry_id])]
    assert db_session.get(SettlementBooking, row.id).status == "booked"
