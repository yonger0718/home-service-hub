from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import AccountStatement, LedgerEntry, ReconciliationAction, ReconciliationAudit, ReconciliationCase
from app.services import coverage_service, policy_service
from app.services import reconciliation_service as rec
from app.services import reconciliation_actions as registry
from app.services import stand_ins
from app.services.statements import matching
from app.services.errors import CodedConflictError, CodedValidationError
from app.services.reconciliation_actions import engine
from app.services.reconciliation_actions.base import NotRevertable
from tests.integration.statement_fixtures import _active, _card, _cases, _line, _submit, run  # noqa: F401
from tests.integration.test_reconciliation_actions_ledger import _apply, _code, _refuse
from tests.integration.test_reconciliation_policy import _budget, _reload, _set_policy

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


# --- R6b.5 replace_stand_in ---------------------------------------------------------------------------------------

def _backfilled(seed, db, run, merchant="全聯", day=DAY, printed=580):
    card = _card(seed, db)
    stmt, (line,), _ = _submit(db, run, card, [_line(1, day, merchant, printed)])
    (case,) = _cases(db, stmt.id, "open")
    row, _ = engine.apply_action(db, case_id=case.id, action="backfill_entry", params={"kind": "expense"}, actor="owner", label="t")
    db.commit()
    # stand_in_id: the id stays readable after the replacement deleted the row (the ORM object then raises on access)
    return SimpleNamespace(card=card, stmt=stmt, line=line, action=row, stand_in=db.get(LedgerEntry, row.created_entry_ids[0]),
                           stand_in_id=row.created_entry_ids[0])


def test_shadow_case_when_the_owner_entry_arrives_while_the_stand_in_holds_the_line(seed, db_session, run, dirty_on):
    s = _backfilled(seed, db_session, run, day=date(2026, 9, 28))
    owner = seed.entry(s.card, "-580", day=date(2026, 10, 11), merchant="全聯")  # 13 days from the line, 11 days outside the period, inside drift 15
    db_session.commit()
    rec.reconcile(db_session, s.stmt.id)
    db_session.commit()
    (case,) = [c for c in _cases(db_session, s.stmt.id, "open") if c.kind == "entry_unmatched"]
    assert case.entry_id == owner.id and case.event_id == s.line.event_id and case.line_id == s.line.id
    assert case.context == {"hint": "shadowed_by_stand_in", "stand_in_entry_id": s.stand_in_id, "backfill_action_id": s.action.id}
    assert "replace_stand_in" in registry.allowed(case)
    s.stand_in.category_id = seed.category("食").id  # cosmetic edit keeps the match and does not block replacement
    db_session.commit()
    row, _ = engine.apply_action(db_session, case_id=case.id, action="replace_stand_in", params={}, actor="owner", label="t")
    db_session.commit()
    assert db_session.get(LedgerEntry, s.stand_in_id) is None
    (cov,) = _active(db_session, s.stmt.id)
    assert cov.entry_id == owner.id and cov.match_rule == "replace_stand_in" and cov.match_kind == "manual"
    old = db_session.get(ReconciliationAction, s.action.id)
    assert old.status == "reverted" and old.reverted_by_action_id == row.id
    assert row.deleted_entry_ids == [s.stand_in_id] and row.before["stand_in_entry_id"] == s.stand_in_id
    with pytest.raises(NotRevertable):
        engine.revert_action(db_session, action_id=row.id, actor="owner", label="t")


def test_shadow_requires_a_unique_line_and_a_conserving_owner_entry(seed, db_session, run, dirty_on):
    s = _backfilled(seed, db_session, run)
    owner = seed.entry(s.card, "-580", day=DAY, merchant="全聯")
    other = seed.entry(s.card, "-580", day=DAY, merchant="全聯")  # Review Focus 5: two owner entries → no hint
    db_session.commit()
    rec.reconcile(db_session, s.stmt.id)
    db_session.commit()
    cases = [c for c in _cases(db_session, s.stmt.id, "open") if c.kind == "entry_unmatched"]
    assert {c.entry_id for c in cases} == {owner.id, other.id} and all("hint" not in (c.context or {}) for c in cases)


def test_orphan_replacement_succeeds_when_the_stand_in_is_untouched(seed, db_session, run, dirty_on):
    s = _backfilled(seed, db_session, run)
    # release the stand-in's coverage without editing it: an unmatch of the line through the engine's coverage path
    coverage_service.release_line(db_session, s.stmt, s.line.id, reason="test")
    db_session.commit()
    owner = seed.entry(s.card, "-580", day=DAY, merchant="全聯")
    db_session.commit()
    rec.reconcile(db_session, s.stmt.id)
    db_session.commit()
    (cov,) = _active(db_session, s.stmt.id)
    assert cov.entry_id == owner.id
    (case,) = [c for c in _cases(db_session, s.stmt.id, "open") if c.kind == "entry_unmatched"]
    assert case.context["hint"] == "orphaned_stand_in"
    row = _apply(db_session, case, "replace_stand_in", {})
    assert db_session.get(LedgerEntry, s.stand_in_id) is None and row.deleted_entry_ids == [s.stand_in_id]
    (cov,) = _active(db_session, s.stmt.id)
    assert cov.entry_id == owner.id and cov.match_kind == "auto"  # the orphan shape keeps the matcher's claim as is
    assert db_session.get(ReconciliationAction, s.action.id).reverted_by_action_id == row.id


def test_replace_discovers_exactly_once_before_the_locks(seed, db_session, run, dirty_on, monkeypatch):
    # R1: lock_targets is the single unlocked discovery; prepare reuses it; _prove re-derives once under the locks
    s = _backfilled(seed, db_session, run)
    seed.entry(s.card, "-580", day=DAY, merchant="全聯")
    db_session.commit()
    rec.reconcile(db_session, s.stmt.id)
    db_session.commit()
    (case,) = [c for c in _cases(db_session, s.stmt.id, "open") if c.kind == "entry_unmatched"]
    from app.services.reconciliation_actions import ledger_actions as la
    real = la.ReplaceStandInAction._shape
    calls = []

    def counted(self, ctx, *, discovery):
        calls.append(discovery)
        return real(self, ctx, discovery=discovery)
    monkeypatch.setattr(la.ReplaceStandInAction, "_shape", counted)
    _apply(db_session, case, "replace_stand_in", {})
    assert calls[0] is True and calls.count(True) == 1 and all(c is False for c in calls[1:])  # exactly one unlocked discovery; validate_before and apply each re-prove under the locks


def test_replace_refuses_when_the_claimant_disappears_between_discovery_and_lock(seed, db_session, run, dirty_on, monkeypatch):
    # R1: the discovery read (lock_targets) sees the claimant; the locked re-read sees none → claimant_changed, not no_claimant
    s = _backfilled(seed, db_session, run)
    coverage_service.release_line(db_session, s.stmt, s.line.id, reason="test")
    db_session.commit()
    seed.entry(s.card, "-580", day=DAY, merchant="全聯")
    db_session.commit()
    rec.reconcile(db_session, s.stmt.id)
    db_session.commit()
    (case,) = [c for c in _cases(db_session, s.stmt.id, "open") if c.kind == "entry_unmatched"]
    from app.services.reconciliation_actions import ledger_actions as la
    real = la.ReplaceStandInAction._claimant
    calls = []

    def vanishing(self, ctx):
        calls.append(1)
        return real(self, ctx) if len(calls) == 1 else None
    monkeypatch.setattr(la.ReplaceStandInAction, "_claimant", vanishing)
    assert _code(_refuse(db_session, lambda: _apply(db_session, case, "replace_stand_in", {}))) == "claimant_changed"
    monkeypatch.undo()
    coverage_service.release_line(db_session, s.stmt, s.line.id, reason="test2")  # now genuinely no claimant at discovery
    db_session.commit()
    assert _code(_refuse(db_session, lambda: _apply(db_session, case, "replace_stand_in", {}))) == "no_claimant"


def test_replace_refuses_when_the_claimant_changed_between_discovery_and_lock(seed, db_session, run, dirty_on, monkeypatch):
    s = _backfilled(seed, db_session, run)
    coverage_service.release_line(db_session, s.stmt, s.line.id, reason="test")
    db_session.commit()
    seed.entry(s.card, "-580", day=DAY, merchant="全聯")
    db_session.commit()
    rec.reconcile(db_session, s.stmt.id)
    db_session.commit()
    (case,) = [c for c in _cases(db_session, s.stmt.id, "open") if c.kind == "entry_unmatched"]
    from app.services.reconciliation_actions import ledger_actions as la
    real = la.ReplaceStandInAction._claimant
    calls = []

    def moved(self, ctx):
        calls.append(1)
        value = real(self, ctx)
        return value if len(calls) == 1 else (value or 0) + 999999  # discovery sees the owner; the locked re-derivation sees someone else
    monkeypatch.setattr(la.ReplaceStandInAction, "_claimant", moved)
    assert _code(_refuse(db_session, lambda: _apply(db_session, case, "replace_stand_in", {}))) == "claimant_changed"
    assert db_session.get(LedgerEntry, s.stand_in_id) is not None


def test_replace_rolls_back_entirely_when_a_failure_hits_after_the_delete(seed, db_session, run, dirty_on, monkeypatch):
    # R3: the injected failure fires AFTER the stand-in was deleted and the creating action marked reverted (the audit
    # call is the last statement of apply), run as the POLICY so the budget charge is part of what must roll back
    s = _backfilled(seed, db_session, run)
    seed.entry(s.card, "-580", day=DAY, merchant="全聯")
    db_session.commit()
    rec.reconcile(db_session, s.stmt.id)
    db_session.commit()
    (case,) = [c for c in _cases(db_session, s.stmt.id, "open") if c.kind == "entry_unmatched"]
    from app.services.reconciliation_actions import audit
    reached = {}
    real_record = audit.record  # `la.audit` IS this module: keep the unpatched function for other events

    def boom(db, *, event, **kw):
        if event == "stand_in_replaced":
            reached["deleted"] = db.get(LedgerEntry, s.stand_in_id) is None  # proves the failure point is past the delete
            reached["reverted"] = db.get(ReconciliationAction, s.action.id).status == "reverted"
            raise RuntimeError("boom")
        return real_record(db, event=event, **kw)
    monkeypatch.setattr(audit, "record", boom)
    _set_policy(db_session, {"enabled": True})
    with pytest.raises(RuntimeError, match="boom"):  # run_policy catches domain errors only; a RuntimeError propagates
        policy_service.run_policy(db_session, s.stmt.id)
    assert reached == {"deleted": True, "reverted": True}
    db_session.rollback()  # release the outer transaction the exception left open; the engine's savepoint already rolled back
    db_session.expire_all()
    assert db_session.get(LedgerEntry, s.stand_in_id) is not None
    (cov,) = _active(db_session, s.stmt.id)
    assert cov.entry_id == s.stand_in_id
    assert db_session.get(ReconciliationAction, s.action.id).status == "applied" and db_session.get(ReconciliationAction, s.action.id).reverted_by_action_id is None
    assert [a.action for a in db_session.execute(select(ReconciliationAction)).scalars()] == ["backfill_entry"]
    assert _reload(db_session, ReconciliationCase, case.id).status == "open"
    assert _budget(db_session) is None or _budget(db_session).used_count == 0


def test_concurrent_delete_of_the_stand_in_is_refused(seed, db_session, run, dirty_on, pg_engine):
    # R3: the entry locks are NOWAIT, so a replacement that meets a held row lock fails at once (`action_busy`);
    # `tests.helpers.race` expects the second side to BLOCK, so this test drives two sessions by hand instead
    from sqlalchemy.orm import sessionmaker
    s = _backfilled(seed, db_session, run)
    seed.entry(s.card, "-580", day=DAY, merchant="全聯")
    db_session.commit()
    rec.reconcile(db_session, s.stmt.id)
    db_session.commit()
    (case,) = [c for c in _cases(db_session, s.stmt.id, "open") if c.kind == "entry_unmatched"]
    Session = sessionmaker(bind=pg_engine)
    holder = Session()
    holder.execute(select(LedgerEntry).where(LedgerEntry.id == s.stand_in_id).with_for_update())  # held, not committed
    try:
        with Session() as other, pytest.raises(CodedConflictError) as exc:
            engine.apply_action(other, case_id=case.id, action="replace_stand_in", params={}, actor="owner", label="t")
        assert exc.value.code == "action_busy"
    finally:
        holder.delete(holder.get(LedgerEntry, s.stand_in_id))
        holder.commit()
        holder.close()
    with Session() as other:
        with pytest.raises(CodedValidationError) as exc:
            engine.apply_action(other, case_id=case.id, action="replace_stand_in", params={}, actor="owner", label="t")
        assert exc.value.code == "stand_in_changed"
    db_session.expire_all()
    assert db_session.get(LedgerEntry, s.stand_in_id) is None and db_session.get(ReconciliationAction, s.action.id).status == "applied"


@pytest.mark.parametrize("order", ["sweep_first", "replace_first"])
def test_sweep_retirement_and_replacement_serialise_on_the_statement_lock(seed, db_session, run, dirty_on, pg_engine, order):
    # R3: sweep-first — the owner deleted the stand-in and the sweep retired its action; the replacement then finds
    # the stand-in gone → stand_in_changed, attribution stays NULL + deleted_by_event. Replace-first — the sweep then
    # finds the action already reverted by the replacement → nothing to retire, attribution kept, no audit row.
    from tests.helpers import race
    s = _backfilled(seed, db_session, run)
    seed.entry(s.card, "-580", day=DAY, merchant="全聯")
    db_session.commit()
    rec.reconcile(db_session, s.stmt.id)
    db_session.commit()
    (case,) = [c for c in _cases(db_session, s.stmt.id, "open") if c.kind == "entry_unmatched"]
    if order == "sweep_first":
        db_session.delete(db_session.get(LedgerEntry, s.stand_in_id))
        db_session.commit()

    def sweep(session):
        st = session.get(AccountStatement, s.stmt.id)
        rec._lock_statement(session, st.id)
        coverage_service.sweep(session, st, run_id=None, barrier_wait=False)

    def replace(session):
        engine.apply_action(session, case_id=case.id, action="replace_stand_in", params={}, actor="owner", label="t")

    first, second = (sweep, replace) if order == "sweep_first" else (replace, sweep)
    outcome = race(pg_engine, first, second)
    db_session.expire_all()
    row = db_session.get(ReconciliationAction, s.action.id)
    assert row.status == "reverted"
    if order == "sweep_first":
        assert outcome != "committed" and getattr(outcome, "code", None) == "stand_in_changed"
        assert row.reverted_by_action_id is None and row.inverse.get("deleted_by_event")
        assert len(_audit(db_session, "stand_in_retired")) == 1
    else:
        assert outcome == "committed" and row.reverted_by_action_id is not None and "deleted_by_event" not in (row.inverse or {})
        assert _audit(db_session, "stand_in_retired") == []
    _sweep(db_session, s.stmt)  # repeat: no double retirement
    assert len(_audit(db_session, "stand_in_retired")) == (1 if order == "sweep_first" else 0)


def test_orphan_case_after_the_sweep_released_the_stand_in(seed, db_session, run, dirty_on):
    s = _backfilled(seed, db_session, run)
    s.stand_in.posted_date = date(2026, 9, 4)  # structural edit → sweep releases the line
    db_session.commit()
    owner = seed.entry(s.card, "-580", day=DAY, merchant="全聯")
    db_session.commit()
    rec.reconcile(db_session, s.stmt.id)
    db_session.commit()
    (cov,) = _active(db_session, s.stmt.id)
    assert cov.entry_id == owner.id
    (case,) = [c for c in _cases(db_session, s.stmt.id, "open") if c.kind == "entry_unmatched"]
    assert case.entry_id == s.stand_in_id and case.context["hint"] == "orphaned_stand_in" and case.context["claimant_entry_id"] == owner.id
    assert _code(_refuse(db_session, lambda: _apply(db_session, case, "replace_stand_in", {}))) == "stand_in_changed"  # date edited


def test_replace_refuses_when_the_claimant_or_stand_in_moved(seed, db_session, run, dirty_on):
    s = _backfilled(seed, db_session, run)
    owner = seed.entry(s.card, "-580", day=DAY, merchant="全聯")
    db_session.commit()
    rec.reconcile(db_session, s.stmt.id)
    db_session.commit()
    (case,) = [c for c in _cases(db_session, s.stmt.id, "open") if c.kind == "entry_unmatched"]
    owner.amount = Decimal("-581")
    db_session.commit()
    assert _code(_refuse(db_session, lambda: _apply(db_session, case, "replace_stand_in", {}))) == "owner_entry_taken"
    owner.amount = Decimal("-580")
    s.stand_in.amount = Decimal("-1")
    db_session.commit()
    # deviation from the brief (reported): while the edit is unswept, the engine's own sweep releases the stand-in's
    # coverage inside the attempt, after the unlocked prepare, so C6 refuses first (409 snapshot_conflict, nothing
    # written); once a sweep has committed the release, the action's own proof answers stand_in_changed
    assert _code(_refuse(db_session, lambda: _apply(db_session, case, "replace_stand_in", {}))) == "snapshot_conflict"
    _sweep(db_session, s.stmt)
    assert _code(_refuse(db_session, lambda: _apply(db_session, case, "replace_stand_in", {}))) == "stand_in_changed"
    assert db_session.get(LedgerEntry, s.stand_in_id) is not None


def test_system_entries_never_shadow(seed, db_session, run, dirty_on):
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "年費", 1200, "fee")])
    (case,) = _cases(db_session, stmt.id, "open")
    engine.apply_action(db_session, case_id=case.id, action="create_system_entry", params={"kind": "fee"}, actor="owner", label="t")
    db_session.commit()
    seed.entry(card, "-1200", day=DAY, kind="fee", merchant="年費")
    db_session.commit()
    rec.reconcile(db_session, stmt.id)
    db_session.commit()
    cases = [c for c in _cases(db_session, stmt.id, "open") if c.kind == "entry_unmatched"]
    assert cases and all("hint" not in (c.context or {}) for c in cases)
