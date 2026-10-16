from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import AccountStatement, LedgerEntry, ReconciliationAction, ReconciliationCase
from app.services import coverage_service
from app.services import reconciliation_service as rec
from app.services import stand_ins
from app.services.statements import matching
from app.services.reconciliation_actions import engine
from tests.integration.statement_fixtures import _active, _card, _cases, _line, _submit, run  # noqa: F401

DAY = date(2026, 9, 3)


def _sweep(db, stmt):
    db.expire_all()
    stmt = db.get(AccountStatement, stmt.id)
    with db.begin_nested():
        rec._lock_statement(db, stmt.id)
        coverage_service.sweep(db, stmt, run_id=None, barrier_wait=False)
    db.commit()


def _fake_backfill(db, stmt, line, entry):
    """A stand-in as Task 5 will create it: an applied backfill_entry action whose created_entry_ids names the entry."""
    (case,) = [c for c in _cases(db, stmt.id, "open") if c.line_id == line.id]
    row = ReconciliationAction(idempotency_key=f"t-{entry.id}", event_id=line.event_id, effect_slot="create", case_id=case.id,
                               action="backfill_entry", params={"kind": "expense"}, actor="policy", status="applied",
                               created_entry_ids=[entry.id], inverse={"created_entry_id": entry.id})
    db.add(row)
    db.commit()
    return row


def test_stand_in_ids_and_creation_lookup(seed, db_session, run):
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)])
    entry = seed.entry(card, "-580", day=DAY, source="statement")
    db_session.commit()
    row = _fake_backfill(db_session, stmt, line, entry)
    assert stand_ins.stand_in_entry_ids(db_session, [card.id]) == {entry.id}
    assert stand_ins.creation_for_entry(db_session, entry.id).id == row.id
    row.status = "reverted"
    db_session.commit()
    assert stand_ins.stand_in_entry_ids(db_session, [card.id]) == set()


def test_population_marks_stand_ins_and_owner_entry_wins_on_reconcile(seed, db_session, run):
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)])
    stand_in = seed.entry(card, "-580", day=DAY, source="statement", merchant="全聯")
    db_session.commit()
    _fake_backfill(db_session, stmt, line, stand_in)
    owner = seed.entry(card, "-580", day=DAY, merchant="全聯")
    db_session.commit()
    rec.reconcile(db_session, stmt.id)
    db_session.commit()
    (cov,) = _active(db_session, stmt.id)
    assert cov.entry_id == owner.id
    orphan = [c for c in _cases(db_session, stmt.id, "open") if c.kind == "entry_unmatched" and c.entry_id == stand_in.id]
    assert orphan  # Task 8 adds the orphaned_stand_in hint; here only the unmatched stand-in is asserted


def test_stand_in_alone_self_heals_after_a_real_release(seed, db_session, run, dirty_on):
    # the stand-in holds the line (agent coverage) → a structural edit makes the sweep release it → the next reconcile
    # re-claims it as an auto claim because nothing else competes
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)])
    stand_in = seed.entry(card, "-580", day=DAY, source="statement", merchant="全聯")
    db_session.commit()
    _fake_backfill(db_session, stmt, line, stand_in)
    coverage_service.write_claim(db_session, stmt, line, [matching.Row(stand_in.id, "principal", Decimal("-580"))],
                                 entries_by_id={stand_in.id: stand_in}, match_kind="agent", match_rule="backfill_entry", run_id=None)
    db_session.commit()
    stand_in.posted_date = date(2026, 9, 4)  # structural: released by the sweep (Task 2)
    db_session.commit()
    _sweep(db_session, stmt)
    assert _active(db_session, stmt.id) == []
    rec.reconcile(db_session, stmt.id)
    db_session.commit()
    (cov,) = _active(db_session, stmt.id)
    assert cov.entry_id == stand_in.id and cov.match_kind == "auto"


def test_owner_deleted_events_are_derived_from_the_action_table(seed, db_session, run):
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)])
    entry = seed.entry(card, "-580", day=DAY, source="statement")
    db_session.commit()
    row = _fake_backfill(db_session, stmt, line, entry)
    assert stand_ins.owner_deleted_events(db_session, stmt) == set()
    row.status, row.inverse = "reverted", {**row.inverse, "deleted_by_event": 1}
    db_session.delete(entry)  # the owner removed the created entry, so the line has nothing to match
    db_session.commit()
    assert stand_ins.owner_deleted_events(db_session, stmt) == {line.event_id}
    rec.reconcile(db_session, stmt.id)
    db_session.commit()
    (case,) = [c for c in _cases(db_session, stmt.id, "open") if c.line_id == line.id]
    assert case.context.get("owner_deleted") is True and case.context.get("hint") == "stand_in_deleted"
    rec.reconcile(db_session, stmt.id)  # re-derived, not carried
    db_session.commit()
    (case,) = [c for c in _cases(db_session, stmt.id, "open") if c.line_id == line.id]
    assert case.context.get("owner_deleted") is True
    _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)])  # identical reparse: same statement identity, revision 2, same event, new line id
    db_session.commit()
    (case,) = [c for c in _cases(db_session, stmt.id, "open") if c.event_id == line.event_id]
    assert case.context.get("owner_deleted") is True and case.line_id != line.id
    later = ReconciliationAction(idempotency_key="t-later", event_id=line.event_id, effect_slot="create", case_id=case.id,
                                 action="create_entry", params={}, actor="owner", status="applied", created_entry_ids=[])
    db_session.add(later)
    db_session.commit()
    assert stand_ins.owner_deleted_events(db_session, stmt) == set()


def test_owner_deleted_keeps_an_existing_hint(seed, db_session, run):
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "年費", 1200, "fee")])
    (case,) = _cases(db_session, stmt.id, "open")
    assert case.context["hint"] == "bank_only"
    row = ReconciliationAction(idempotency_key="t-fee", event_id=line.event_id, effect_slot="create", case_id=case.id,
                               action="create_system_entry", params={"kind": "fee"}, actor="policy", status="reverted",
                               created_entry_ids=[], inverse={"created_entry_id": 0, "deleted_by_event": 1})
    db_session.add(row)
    db_session.commit()
    rec.reconcile(db_session, stmt.id)
    db_session.commit()
    (case,) = _cases(db_session, stmt.id, "open")
    assert case.context["hint"] == "bank_only" and case.context["owner_deleted"] is True
