"""Applied actions a ledger delete made unrevertable (production 2026-10-17: a MOZE re-import deletes and re-creates
every MOZE entry under new ids). The sweep retires them like creations — `match` (its entry or group gone),
`adjust_amount`, `attach_fee`, `move_account`, `split_entry` (the entry they act on gone): `status = reverted`,
`inverse.deleted_by_event` (or `deleted_before_watermark` in the catch-up), audit `action_retired` — but a retired
non-creation never marks the line owner-deleted (a re-import is no owner delete). Synthetic data only."""
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import (
    AccountStatement, EntryGroup, LedgerEntry, ReconciliationAction, ReconciliationAudit, ReconciliationCase, StatementCoverage,
)
from app.services import coverage_service, stand_ins
from app.services import reconciliation_service as rec
from app.services import statement_retire_service as retire_svc
from app.services.errors import CodedConflictError
from app.services.reconciliation_actions import engine
from tests.integration.statement_fixtures import _active, _card, _cases, _line, _submit, run  # noqa: F401
from tests.integration.test_reconciliation_actions_ledger import fx_pair, near_pair  # noqa: F401  (fixtures)
from tests.integration.test_reconciliation_actions_structural import _hand_case

DAY = date(2026, 9, 3)


def _sweep(db, stmt):
    db.expire_all()
    stmt = db.get(AccountStatement, stmt.id)
    with db.begin_nested():
        rec._lock_statement(db, stmt.id)
        result = coverage_service.sweep(db, stmt, run_id=None, barrier_wait=False)
    db.commit()
    return result


def _retire_refused(db, card, stmt, *action_ids):
    """retire_statement keeps refusing while a partially stranded action stays applied."""
    with pytest.raises(CodedConflictError) as exc:
        retire_svc.retire_statement(db, account_id=card.id, statement_id=stmt.id, note="dup", label="owner-test",
                                    request_id=None)
    db.rollback()
    assert exc.value.code == "actions_applied"
    assert all(str(i) in str(exc.value) for i in action_ids)
    assert _fresh(db, AccountStatement, stmt.id).mode == "live"


def _audit(db, event):
    db.expire_all()
    return db.execute(select(ReconciliationAudit).where(ReconciliationAudit.event == event)
                      .order_by(ReconciliationAudit.id)).scalars().all()


def _case_dump():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("r6_case_dump", Path(__file__).parents[2] / "scripts" / "r6_case_dump.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fresh(db, model, pk):
    db.expire_all()
    return db.get(model, pk)


def _matched(seed, db, run):
    """A live card statement whose 580 line the owner matched to an entry booked after the reconcile."""
    card = _card(seed, db)
    stmt, (line,), _ = _submit(db, run, card, [_line(1, DAY, "全聯", 580)])
    (case,) = _cases(db, stmt.id, "open")
    entry = seed.entry(card, "-580", day=DAY, merchant="全聯")
    db.commit()
    row, _ = engine.apply_action(db, case_id=case.id, action="match", actor="owner", label="t",
                                 params={"line_id": line.id, "entry_id": entry.id})
    db.commit()
    return card, stmt, line, entry, row


def _reimport(seed, db, card, entry):
    """The MOZE re-import: the entry is deleted and booked again under a new id."""
    db.delete(db.get(LedgerEntry, entry.id))
    db.commit()
    again = seed.entry(card, "-580", day=DAY, merchant="全聯")
    db.commit()
    return again


def _retire_ok(db, card, stmt):
    result = retire_svc.retire_statement(db, account_id=card.id, statement_id=stmt.id, note="dup", label="owner-test",
                                         request_id=None)
    db.commit()
    assert result.already_retired is False and _fresh(db, AccountStatement, stmt.id).mode == "historical"


def _rematch(db, stmt, line, entry):
    (case,) = [c for c in _cases(db, stmt.id, "open") if c.line_id == line.id]
    row, _ = engine.apply_action(db, case_id=case.id, action="match", actor="owner", label="t",
                                 params={"line_id": line.id, "entry_id": entry.id})
    db.commit()
    return row


def test_a_reimport_retires_the_match_so_the_statement_can_be_retired(seed, db_session, run, dirty_on):
    card, stmt, line, entry, row = _matched(seed, db_session, run)
    _reimport(seed, db_session, card, entry)

    _sweep(db_session, stmt)

    row = _fresh(db_session, ReconciliationAction, row.id)
    assert (row.status, row.reverted_by_action_id) == ("reverted", None) and row.inverse["deleted_by_event"]
    (audit,) = _audit(db_session, "action_retired")
    assert (audit.action_id, audit.actor, audit.detail["action"], audit.detail["entry_ids"]) == (
        row.id, "sweep", "match", [entry.id])
    assert _audit(db_session, "stand_in_retired") == []
    _sweep(db_session, stmt)  # idempotent
    assert len(_audit(db_session, "action_retired")) == 1
    # a re-import is no owner delete: the line stays open to the matcher and the policy
    assert stand_ins.owner_deleted_events(db_session, _fresh(db_session, AccountStatement, stmt.id)) == set()
    # the fix's point: nothing applied is left, so the duplicate can be retired
    _retire_ok(db_session, card, stmt)


def test_the_owner_can_match_the_line_again_after_a_reimport(seed, db_session, run, dirty_on):
    """Also holds WITHOUT the retirement: the sweep's live release bumps the case version, which frees the match's
    `c<case>v<version>` slot. Kept as a guard that the retirement does not get in the way."""
    card, stmt, line, entry, row = _matched(seed, db_session, run)
    new = _reimport(seed, db_session, card, entry)
    _sweep(db_session, stmt)
    again = _rematch(db_session, stmt, line, new)
    assert again.status == "applied"
    assert [c.entry_id for c in _active(db_session, stmt.id)] == [new.id]


def test_the_catch_up_retires_a_match_whose_delete_is_behind_the_watermark(seed, db_session, run, dirty_on,
                                                                          monkeypatch):
    card, stmt, line, entry, row = _matched(seed, db_session, run)
    new = _reimport(seed, db_session, card, entry)
    with monkeypatch.context() as m:  # the sweep before this fix released the line, moved the watermark, kept the row
        m.setattr(coverage_service, "_retire_unrevertable", lambda *args: None, raising=False)
        m.setattr(coverage_service, "_retire_stranded_unrevertable", lambda *args: None, raising=False)
        _sweep(db_session, stmt)
    assert _fresh(db_session, ReconciliationAction, row.id).status == "applied" and _active(db_session, stmt.id) == []
    assert stmt.id in coverage_service.sweep_pending(db_session)

    _sweep(db_session, stmt)

    row = _fresh(db_session, ReconciliationAction, row.id)
    assert row.status == "reverted" and row.inverse["deleted_before_watermark"] is True
    assert "deleted_by_event" not in row.inverse
    (audit,) = _audit(db_session, "action_retired")
    assert audit.detail == {"catch_up": True, "entry_ids": [entry.id], "action": "match"}
    assert stmt.id not in coverage_service.sweep_pending(db_session)


def test_retire_statement_succeeds_when_its_only_applied_action_is_a_stranded_match(seed, db_session, run, dirty_on,
                                                                                    monkeypatch):
    card, stmt, line, entry, row = _matched(seed, db_session, run)
    _reimport(seed, db_session, card, entry)
    with monkeypatch.context() as m:  # production: swept before the fix, the match still `applied`
        m.setattr(coverage_service, "_retire_unrevertable", lambda *args: None, raising=False)
        m.setattr(coverage_service, "_retire_stranded_unrevertable", lambda *args: None, raising=False)
        _sweep(db_session, stmt)

    result = retire_svc.retire_statement(db_session, account_id=card.id, statement_id=stmt.id, note="dup",
                                         label="owner-test", request_id=None)
    db_session.commit()

    assert result.already_retired is False
    assert _fresh(db_session, ReconciliationAction, row.id).status == "reverted"
    assert _fresh(db_session, AccountStatement, stmt.id).mode == "historical"


def test_an_adjust_amount_on_a_deleted_entry_is_retired(near_pair, seed, db_session,
                                                                                    dirty_on):
    s = near_pair(foreign=None)
    adjust, _ = engine.apply_action(db_session, case_id=s.case.id, action="adjust_amount", actor="owner", label="t",
                                    params={"entry_id": s.entry.id, "line_id": s.line.id})
    db_session.commit()
    db_session.delete(db_session.get(LedgerEntry, s.entry.id))
    db_session.commit()

    _sweep(db_session, s.stmt)

    adjust = _fresh(db_session, ReconciliationAction, adjust.id)
    assert adjust.status == "reverted" and adjust.inverse["deleted_by_event"]
    assert adjust.inverse["restore"]  # the stored inverse is kept
    (audit,) = _audit(db_session, "action_retired")
    assert (audit.action_id, audit.detail["action"]) == (adjust.id, "adjust_amount")


def test_a_match_whose_entry_still_exists_is_untouched(seed, db_session, run, dirty_on):
    card, stmt, line, entry, row = _matched(seed, db_session, run)
    unrelated = seed.entry(card, "-99", day=DAY, merchant="別的")
    db_session.commit()
    db_session.delete(db_session.get(LedgerEntry, unrelated.id))
    db_session.commit()

    _sweep(db_session, stmt)
    _sweep(db_session, stmt)

    assert _fresh(db_session, ReconciliationAction, row.id).status == "applied"
    assert [c.entry_id for c in _active(db_session, stmt.id)] == [entry.id]
    assert _audit(db_session, "action_retired") == []


def test_each_action_shape_is_retired_only_when_every_target_is_gone(seed, db_session, run):
    """Hand-made applied rows (dirty off: the deletes leave no event, so the catch-up decides). Lead ruling (PR #78
    review M3): every target gone → retired; some gone → stays applied and counted (`action_partially_stranded`);
    every target alive → untouched; creations are not this pass's."""
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)])
    (case,) = _cases(db_session, stmt.id, "open")
    alive, gone, gone2 = (seed.entry(card, "-1", day=DAY, merchant=m) for m in ("a", "b", "c"))
    group_alive, group_gone = seed.group(), seed.group()
    db_session.commit()
    retired, partial, untouched = "retired", "partial", "untouched"
    shapes = {
        # as the engine records a match: its claimed entries touched, a group match's group touched
        "match_entry_alive": ("match", {"entry_id": alive.id, "group_id": None}, [alive.id], [], [], untouched),
        "match_entry_gone": ("match", {"entry_id": gone.id, "group_id": None}, [gone.id], [], [], retired),
        "match_group_gone": ("match", {"entry_id": None, "group_id": group_gone.id}, [gone.id, gone2.id], [],
                             [group_gone.id], retired),
        "match_group_member_left": ("match", {"entry_id": None, "group_id": group_alive.id}, [gone.id, alive.id], [],
                                    [group_alive.id], partial),
        "match_child_gone": ("match", {"entry_id": alive.id, "group_id": None, "with_children": True},
                             [alive.id, gone.id], [], [], partial),
        "adjust_gone": ("adjust_amount", {"entry_id": gone.id}, [gone.id], [], [], retired),
        "move_alive": ("move_account", {"entry_id": alive.id}, [alive.id], [], [], untouched),
        "fee_child_gone": ("attach_fee", {"entry_id": alive.id}, [alive.id, gone.id], [gone.id], [], partial),
        "fee_all_gone": ("attach_fee", {"entry_id": gone.id}, [gone.id, gone2.id], [gone2.id], [], retired),
        "split_alive": ("split_entry", {"entry_id": alive.id}, [alive.id], [], [group_alive.id], untouched),
        "split_original_gone": ("split_entry", {"entry_id": gone.id}, [gone.id], [alive.id], [group_alive.id], partial),
        "split_group_gone": ("split_entry", {"entry_id": alive.id}, [alive.id], [], [group_gone.id], partial),
        "split_all_gone": ("split_entry", {"entry_id": gone.id}, [gone.id], [gone2.id], [group_gone.id], retired),
        "creation_gone": ("create_entry", {}, [], [gone.id], [], None),  # the creation pass's (stand_in_retired)
    }
    rows = {}
    for i, (name, (action, params, touched, created, groups, _)) in enumerate(shapes.items()):
        rows[name] = ReconciliationAction(
            idempotency_key=f"shape-{i}", event_id=line.event_id, effect_slot=f"t{i}", case_id=case.id,
            action=action, params=params, actor="owner", status="applied", touched_entry_ids=touched,
            created_entry_ids=created, touched_group_ids=groups)
        db_session.add(rows[name])
    db_session.commit()
    for e in (gone, gone2):
        db_session.delete(db_session.get(LedgerEntry, e.id))
    db_session.delete(group_gone)
    db_session.commit()
    assert stmt.id in coverage_service.sweep_pending(db_session)

    result = _sweep(db_session, stmt)

    audited = {a.action_id for a in _audit(db_session, "action_retired")}
    for name, (*_, expect) in shapes.items():
        row = _fresh(db_session, ReconciliationAction, rows[name].id)
        if expect is None:
            assert row.id not in audited, name
            continue
        assert (row.status, row.id in audited) == (("reverted", True) if expect == retired else ("applied", False)), name
        if expect == retired:
            assert row.inverse == {"deleted_before_watermark": True}, name
    partials = sorted(rows[n].id for n, shape in shapes.items() if shape[-1] == partial)
    assert sorted(result.partially_stranded) == partials
    assert sorted(coverage_service.partially_stranded_ids(db_session, _fresh(db_session, AccountStatement, stmt.id))) \
        == partials
    assert stmt.id not in coverage_service.sweep_pending(db_session)  # a partial action is not "pending" work


# --- review of 9741f7d: group and child matches, one lookup per sweep, leftover coverage, attach_fee events --------

def _split_and_group_match(seed, db, run):
    """split with no hit → the owner matches the split group (its members: the original anchor and a new one)."""
    card = _card(seed, db)
    stmt, (line,), _ = _submit(db, run, card, [_line(1, DAY, "百貨公司", 1000)])
    anchor = seed.entry(card, "-1000", day=DAY, name="百貨", merchant="百貨公司")
    food = seed.category("餐飲")
    db.commit()
    case = _hand_case(db, stmt, "amount_delta", line=line, entry_id=anchor.id,
                      candidates=[{"entry_id": anchor.id, "reason": "near"}], context={"entry_id": anchor.id})
    split, _ = engine.apply_action(db, case_id=case.id, action="split_entry", actor="owner", label="t",
                                   params={"entry_id": anchor.id, "parts": [
                                       {"amount": "600", "category_id": food.id},
                                       {"amount": "400", "category_id": food.id, "name": "禮物"}]})
    db.commit()
    gid = split.inverse["group_id"]
    assert _fresh(db, ReconciliationCase, case.id).status == "open"  # no member conserves the line
    match, _ = engine.apply_action(db, case_id=case.id, action="match", actor="owner", label="t",
                                   params={"line_id": line.id, "group_id": gid})
    db.commit()
    assert sorted(match.touched_entry_ids) == sorted(split.inverse["member_ids"]) and match.touched_group_ids == [gid]
    return card, stmt, anchor, split, match


def test_a_group_match_whose_split_original_is_deleted_stays_applied_and_counted(seed, db_session, run, dirty_on):
    """The re-import deletes the (MOZE) original; the group and the new member survive: the split's and the match's
    remaining effects are not dropped silently — both stay applied, counted, and retire keeps refusing."""
    card, stmt, anchor, split, match = _split_and_group_match(seed, db_session, run)
    db_session.delete(db_session.get(LedgerEntry, anchor.id))
    db_session.commit()

    result = _sweep(db_session, stmt)

    assert [_fresh(db_session, ReconciliationAction, r.id).status for r in (split, match)] == ["applied", "applied"]
    assert sorted(result.partially_stranded) == sorted([split.id, match.id])
    assert _audit(db_session, "action_retired") == []
    _retire_refused(db_session, card, stmt, split.id, match.id)


def test_a_group_match_and_its_split_are_retired_once_every_member_and_the_group_are_gone(seed, db_session, run,
                                                                                         dirty_on):
    card, stmt, anchor, split, match = _split_and_group_match(seed, db_session, run)
    for entry_id in split.inverse["member_ids"]:
        db_session.delete(db_session.get(LedgerEntry, entry_id))
        db_session.commit()
    db_session.delete(db_session.get(EntryGroup, split.inverse["group_id"]))
    db_session.commit()

    result = _sweep(db_session, stmt)

    for row in (split, match):
        row = _fresh(db_session, ReconciliationAction, row.id)
        assert row.status == "reverted" and row.inverse["deleted_by_event"], row.action
    assert sorted(a.action_id for a in _audit(db_session, "action_retired")) == sorted([split.id, match.id])
    assert result.partially_stranded == []
    _retire_ok(db_session, card, stmt)


def _children_match(seed, db, run):
    card = _card(seed, db)
    stmt, (line,), _ = _submit(db, run, card, [_line(1, DAY, "全聯", 600)])
    (case,) = _cases(db, stmt.id, "open")
    parent = seed.entry(card, "-580", day=DAY, merchant="全聯")
    child = seed.entry(card, "-20", kind="fee", day=DAY, parent_entry_id=parent.id)
    db.commit()
    row, _ = engine.apply_action(db, case_id=case.id, action="match", actor="owner", label="t",
                                 params={"line_id": line.id, "entry_id": parent.id, "with_children": True})
    db.commit()
    return card, stmt, parent, child, row


def test_a_match_with_children_whose_child_is_deleted_stays_applied_and_counted(seed, db_session, run, dirty_on):
    card, stmt, parent, child, row = _children_match(seed, db_session, run)
    db_session.delete(db_session.get(LedgerEntry, child.id))
    db_session.commit()

    result = _sweep(db_session, stmt)

    assert _fresh(db_session, ReconciliationAction, row.id).status == "applied"
    assert result.partially_stranded == [row.id]
    dump = _case_dump()._policy_section(db_session, [_fresh(db_session, AccountStatement, stmt.id)])
    assert dump["action_partially_stranded"] == {str(stmt.id): 1}
    _retire_refused(db_session, card, stmt, row.id)


def test_a_match_with_children_is_retired_once_parent_and_child_are_gone(seed, db_session, run, dirty_on):
    card, stmt, parent, child, row = _children_match(seed, db_session, run)
    for entry_id in (child.id, parent.id):
        db_session.delete(db_session.get(LedgerEntry, entry_id))
        db_session.commit()

    _sweep(db_session, stmt)

    row = _fresh(db_session, ReconciliationAction, row.id)
    assert row.status == "reverted" and row.inverse["deleted_by_event"]
    _retire_ok(db_session, card, stmt)


def _fee(db, s):
    row, _ = engine.apply_action(db, case_id=s.case.id, action="attach_fee", actor="owner", label="t",
                                 params={"entry_id": s.entry.id})
    db.commit()
    return row


def test_attach_fee_whose_fee_child_is_deleted_stays_applied_and_counted(fx_pair, db_session, dirty_on):
    s = fx_pair
    row = _fee(db_session, s)
    db_session.delete(db_session.get(LedgerEntry, row.inverse["child_id"]))
    db_session.commit()

    result = _sweep(db_session, s.stmt)

    assert _fresh(db_session, ReconciliationAction, row.id).status == "applied"
    assert result.partially_stranded == [row.id] and _audit(db_session, "action_retired") == []
    assert _active(db_session, s.stmt.id) == []  # the event released the line as before


def test_attach_fee_is_retired_through_the_event_once_parent_and_fee_child_are_gone(fx_pair, db_session, dirty_on):
    s = fx_pair
    row = _fee(db_session, s)
    child_id = row.inverse["child_id"]
    for entry_id in (child_id, s.entry.id):
        db_session.delete(db_session.get(LedgerEntry, entry_id))
        db_session.commit()

    _sweep(db_session, s.stmt)

    row = _fresh(db_session, ReconciliationAction, row.id)
    assert row.status == "reverted" and row.inverse["deleted_by_event"] and row.inverse["child_id"] == child_id
    (audit,) = _audit(db_session, "action_retired")
    assert (audit.action_id, audit.detail["action"]) == (row.id, "attach_fee")
    assert child_id in audit.detail["entry_ids"] and "catch_up" not in audit.detail
    assert _active(db_session, s.stmt.id) == []


def test_a_batch_of_deletes_retires_each_row_once_with_one_lookup_per_sweep(seed, db_session, run, dirty_on,
                                                                          monkeypatch):
    card = _card(seed, db_session)
    stmt, lines, _ = _submit(db_session, run, card, [_line(i, DAY, f"店{i}", 100 + i) for i in range(1, 4)])
    rows, entries = [], []
    for line in lines:
        (case,) = [c for c in _cases(db_session, stmt.id, "open") if c.line_id == line.id]
        parent = seed.entry(card, f"-{line.printed_amount - 1}", day=DAY, merchant="x")
        child = seed.entry(card, "-1", kind="fee", day=DAY, parent_entry_id=parent.id)
        db_session.commit()
        row, _ = engine.apply_action(db_session, case_id=case.id, action="match", actor="owner", label="t",
                                     params={"line_id": line.id, "entry_id": parent.id, "with_children": True})
        db_session.commit()
        rows.append(row)
        entries += [child, parent]  # two delete events per row
    noise = [seed.entry(card, "-5", day=DAY, merchant=f"雜{i}") for i in range(40)]
    db_session.commit()
    for e in [*entries, *noise]:  # one by one: a child goes before its parent
        db_session.delete(db_session.get(LedgerEntry, e.id))
        db_session.commit()
    calls = []
    real = coverage_service._unrevertable_rows
    monkeypatch.setattr(coverage_service, "_unrevertable_rows", lambda *a, **k: calls.append(1) or real(*a, **k))

    _sweep(db_session, stmt)

    assert len(calls) <= 2  # the catch-up's and the event loop's, never one per delete event
    assert all(_fresh(db_session, ReconciliationAction, r.id).status == "reverted" for r in rows)
    assert sorted(a.action_id for a in _audit(db_session, "action_retired")) == sorted(r.id for r in rows)


def test_the_catch_up_releases_coverage_a_delete_without_dirty_tracking_left_active(seed, db_session, run):
    """dirty off: the delete leaves no event, and the claim row survives with entry_id NULL (ON DELETE SET NULL)."""
    card, stmt, line, entry, row = _matched(seed, db_session, run)
    (case,) = _cases(db_session, stmt.id)
    version = case.version
    db_session.delete(db_session.get(LedgerEntry, entry.id))
    db_session.commit()
    (claim,) = _active(db_session, stmt.id)
    assert claim.entry_id is None

    _sweep(db_session, stmt)

    row = _fresh(db_session, ReconciliationAction, row.id)
    assert row.status == "reverted" and row.inverse["deleted_before_watermark"] is True
    claim = _fresh(db_session, StatementCoverage, claim.id)
    assert (claim.status, claim.stale_reason) == ("stale", "retired")
    case = _fresh(db_session, ReconciliationCase, case.id)
    assert (case.status, case.version) == ("open", version + 1)  # reopened as the sweep's release does (live)
    assert _fresh(db_session, AccountStatement, stmt.id).matched_count == 0
