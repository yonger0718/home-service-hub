from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import AccountStatement, LedgerEntry, ReconciliationAction, ReconciliationAudit, ReconciliationCase
from app.services import coverage_service
from app.services import reconciliation_service as rec
from app.services import reconciliation_actions as registry
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


def _audit(db, *events):
    db.expire_all()
    return db.execute(select(ReconciliationAudit).where(ReconciliationAudit.event.in_(events))
                      .order_by(ReconciliationAudit.id)).scalars().all()


@pytest.mark.parametrize("action, line_kind, printed, params", [
    ("backfill_entry", "purchase", 580, {"kind": "expense"}), ("create_entry", "purchase", 580, None),
    ("create_system_entry", "fee", 50, {"kind": "fee"}), ("create_refund", "refund", -100, {})])
def test_ledger_delete_retires_the_creation_and_marks_the_event(seed, db_session, run, dirty_on, action, line_kind, printed, params):
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "x", printed, line_kind, txn_date=date(2026, 9, 2))])
    (case,) = _cases(db_session, stmt.id, "open")
    if action == "create_refund":  # a refund line needs the purchase it refunds
        params = {"original_entry_id": seed.entry(card, "-500", day=date(2026, 8, 20), merchant="x").id}
        db_session.commit()
    if params is None:
        params = {"kind": "expense", "category_id": seed.category("食").id}
    row, _ = engine.apply_action(db_session, case_id=case.id, action=action, params=params, actor="owner", label="t")
    db_session.commit()
    (entry_id,) = row.created_entry_ids
    db_session.delete(db_session.get(LedgerEntry, entry_id))
    db_session.commit()
    _sweep(db_session, stmt)
    row = db_session.get(ReconciliationAction, row.id)
    assert row.status == "reverted" and row.reverted_by_action_id is None and row.inverse["deleted_by_event"]
    assert _active(db_session, stmt.id) == []
    _sweep(db_session, stmt)  # idempotent
    assert db_session.get(ReconciliationAction, row.id).inverse["deleted_by_event"] == row.inverse["deleted_by_event"]
    rec.reconcile(db_session, stmt.id)
    db_session.commit()
    (case,) = [c for c in _cases(db_session, stmt.id, "open") if c.line_id == line.id]
    assert case.context.get("owner_deleted") is True
    assert "create_entry" in registry.allowed(case) or action == "create_refund"


def test_retirement_never_overwrites_an_engine_revert_attribution(seed, db_session, run, dirty_on):
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)])
    (case,) = _cases(db_session, stmt.id, "open")
    row, _ = engine.apply_action(db_session, case_id=case.id, action="backfill_entry", params={"kind": "expense"}, actor="owner", label="t")
    db_session.commit()
    rev = engine.revert_action(db_session, action_id=row.id, actor="owner", label="t")
    db_session.commit()
    _sweep(db_session, stmt)
    row = db_session.get(ReconciliationAction, row.id)
    assert row.status == "reverted" and row.reverted_by_action_id == rev.id and "deleted_by_event" not in (row.inverse or {})
    rec.reconcile(db_session, stmt.id)
    db_session.commit()
    (case,) = [c for c in _cases(db_session, stmt.id, "open") if c.line_id == line.id]
    assert "owner_deleted" not in (case.context or {})  # an engine revert is not an owner delete: the policy may backfill again


@pytest.mark.parametrize("order", ["sweep_first", "revert_first"])
def test_sweep_retirement_and_engine_revert_serialise_on_the_statement_lock(seed, db_session, run, dirty_on, pg_engine, order):
    # both orders: whichever holds the statement lock first wins; the loser sees the committed state. Sweep-first:
    # the revert then finds the entry gone -> NotRevertable, the action stays retired with deleted_by_event and a NULL
    # attribution. Revert-first: the sweep then finds the action already reverted -> no retirement, attribution kept.
    from tests.helpers import race
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)])
    (case,) = _cases(db_session, stmt.id, "open")
    row, _ = engine.apply_action(db_session, case_id=case.id, action="backfill_entry", params={"kind": "expense"}, actor="owner", label="t")
    db_session.commit()
    entry_id = row.created_entry_ids[0]
    if order == "sweep_first":
        db_session.delete(db_session.get(LedgerEntry, entry_id))
        db_session.commit()
    outcome = {}

    def sweep(session):
        st = session.get(AccountStatement, stmt.id)
        rec._lock_statement(session, st.id)
        coverage_service.sweep(session, st, run_id=None, barrier_wait=False)

    def revert(session):
        try:
            engine.revert_action(session, action_id=row.id, actor="owner", label="t")
            outcome["revert"] = "ok"
        except Exception as exc:  # NotRevertable / snapshot conflict are both acceptable losers
            outcome["revert"] = type(exc).__name__
            raise

    first, second = (sweep, revert) if order == "sweep_first" else (revert, sweep)
    result = race(pg_engine, first, second)
    db_session.expire_all()
    row = db_session.get(ReconciliationAction, row.id)
    assert row.status == "reverted"
    if order == "sweep_first":
        assert result != "committed" and row.reverted_by_action_id is None and row.inverse.get("deleted_by_event")
    else:
        assert result == "committed" and row.reverted_by_action_id is not None and "deleted_by_event" not in (row.inverse or {})
    assert len([a for a in _audit(db_session, "stand_in_retired")]) == (1 if order == "sweep_first" else 0)
    _sweep(db_session, stmt)  # repeated sweep: no second audit row
    assert len([a for a in _audit(db_session, "stand_in_retired")]) == (1 if order == "sweep_first" else 0)


def test_edit_release_then_delete_still_retires(seed, db_session, run, dirty_on):
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)])
    (case,) = _cases(db_session, stmt.id, "open")
    row, _ = engine.apply_action(db_session, case_id=case.id, action="backfill_entry", params={"kind": "expense"}, actor="owner", label="t")
    db_session.commit()
    entry = db_session.get(LedgerEntry, row.created_entry_ids[0])
    entry.amount = Decimal("-1")
    db_session.commit()
    _sweep(db_session, stmt)
    assert _active(db_session, stmt.id) == []
    db_session.delete(db_session.get(LedgerEntry, entry.id))
    db_session.commit()
    _sweep(db_session, stmt)
    assert db_session.get(ReconciliationAction, row.id).status == "reverted"
