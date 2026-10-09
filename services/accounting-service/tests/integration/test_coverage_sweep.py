"""Dirty sweep (spec §4.6 "Dirty events" / "Sweep protocol"): the watermark scan over coverage_dirty releases claims
whose ledger rows changed, opens/reopens recheck cases in live mode only and flags population changes."""
import threading
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import sessionmaker

from app.models import (
    AccountStatement, CoverageDirty, LedgerEntry, ReconciliationAction, ReconciliationCase, StatementCoverage,
    StatementLine,
)
from app.schemas.statements import LineIn, RevisionIn
from app.schemas.writes import EntryUpdateIn, SplitIn
from app.services import coverage_service as cov
from app.services import entry_write_service as ews
from app.services import split_service as ss
from app.services import statement_ingest_service as ing
from app.services import statement_revision_service as revs
from app.services.statements import matching
from tests.helpers import set_dirty, wait_until_blocked

# These tests isolate their service: submit_revision runs without the reconcile pass (conftest.matcher_off).
pytestmark = pytest.mark.usefixtures("matcher_off")

SEP = (date(2026, 9, 1), date(2026, 9, 30))
OCT = (date(2026, 10, 1), date(2026, 10, 31))


@pytest.fixture
def run(db_session):
    r = ing.create_worker_run(db_session, trigger="timer", initiator_hint=None, mode="live")
    r, token = ing.claim_run(db_session, r.id, label="worker")
    return r, token


def _submit(db, run, card, period=SEP) -> tuple[AccountStatement, list[StatementLine]]:
    r, token = run
    lines = [LineIn(seq=i, posted_date=period[0].replace(day=3), merchant_raw="全聯", printed_amount=Decimal("580"),
                    line_kind="purchase") for i in (1, 2)]
    rev = RevisionIn(run_id=r.id, lease_token=token, file_id=None, account_id=card.id, kind="card", parser="t",
                     parser_version="1", currency="TWD", period_start=period[0], period_end=period[1],
                     opening_balance=None, statement_total=Decimal("1160"), lines=lines, raw={})
    res = revs.submit_revision(db, r, rev, account_map={})
    db.flush()
    ls = db.query(StatementLine).filter_by(revision_id=res.revision.id).order_by(StatementLine.seq).all()
    return res.statement, ls


def _card(seed, db, *, live=True, name="卡"):
    card = seed.account(name, is_credit=True, statement_live_from=date(2026, 9, 1) if live else None)
    db.commit()
    return card


def _claim(db, stmt, line, entries, groups=None, roles=None):
    roles = roles or ["principal"] * len(entries)
    rows = [matching.Row(e.id, role, Decimal(e.amount), e.group_id) for e, role in zip(entries, roles)]
    return cov.write_claim(db, stmt, line, rows, entries_by_id={e.id: e for e in entries}, match_kind="auto",
                           match_rule="exact", run_id=None)


def _sweep(db, stmt):
    """The caller's statement lock (sweep's documented precondition)."""
    locked = db.execute(select(AccountStatement).where(AccountStatement.id == stmt.id).with_for_update()
                        .execution_options(populate_existing=True)).scalar_one()
    return cov.sweep(db, locked)


def _max_event(db) -> int:
    return db.execute(select(func.coalesce(func.max(CoverageDirty.id), 0))).scalar_one()


def _coverage(db) -> list[StatementCoverage]:
    db.expire_all()
    return db.query(StatementCoverage).order_by(StatementCoverage.id).all()


@pytest.fixture
def claimed(seed, db_session, run):
    """A live September card statement whose line 1 is claimed by one -580 entry; dirty tracking turned on after
    the setup writes, so the first sweep sees only the test's own writes."""
    def build(*, live=True):
        card = _card(seed, db_session, live=live)
        stmt, lines = _submit(db_session, run, card)
        entry = seed.entry(card, "-580", day=date(2026, 9, 3))
        db_session.flush()
        db_session.refresh(entry)
        _claim(db_session, stmt, lines[0], [entry])
        db_session.commit()
        set_dirty(db_session, True)
        return stmt, lines, card, entry
    return build


def _update(entry, card, amount="600", day=date(2026, 9, 3)):
    return EntryUpdateIn(account_id=card.id, kind="expense", amount=Decimal(amount), entry_date=day, posted_date=day)


# (a) live amount edit
def test_amount_edit_on_claimed_entry_releases_line_and_opens_recheck_case(claimed, db_session):
    stmt, (l1, _), card, entry = claimed()
    ews.update_entry(db_session, entry.id, _update(entry, card))
    db_session.commit()
    top = _max_event(db_session)
    event_id = db_session.execute(select(CoverageDirty.id).where(CoverageDirty.row_id == entry.id,
                                                                 CoverageDirty.op == "update")).scalar_one()

    res = _sweep(db_session, stmt)
    db_session.commit()

    assert res.stale_lines == [l1.id] and res.reopened_cases == [] and len(res.created_cases) == 1
    assert res.swept_through == top and res.recheck is False
    row = _coverage(db_session)[0]
    assert row.status == "stale" and row.stale_reason == f"dirty:update:{event_id}"
    case = db_session.get(ReconciliationCase, res.created_cases[0])
    assert (case.kind, case.status, case.line_id, case.event_id) == ("recheck", "open", l1.id, l1.event_id)
    assert case.context == {"event_id": event_id, "op": "update", "row_id": entry.id}
    stmt = db_session.get(AccountStatement, stmt.id)
    assert stmt.swept_through_event_id == top and stmt.open_case_count == 1 and stmt.matched_count == 0
    assert stmt.needs_recheck is False
    # the watermark means a second sweep has nothing to do
    again = _sweep(db_session, stmt)
    assert again.stale_lines == [] and again.created_cases == [] and again.swept_through == top


def test_edit_reopens_the_events_resolved_case_instead_of_creating_one(claimed, db_session):
    stmt, (l1, _), card, entry = claimed()
    older = ReconciliationCase(statement_id=stmt.id, revision_id=l1.revision_id, kind="line_unmatched",
                               event_id=l1.event_id, line_id=l1.id, status="dismissed")
    db_session.add(older)
    db_session.flush()
    old = ReconciliationCase(statement_id=stmt.id, revision_id=l1.revision_id, kind="line_unmatched",
                             event_id=l1.event_id, line_id=l1.id, status="resolved", context={"k": 1},
                             resolved_by="owner", resolved_action_id=7, explanation="accepted_exception",
                             resolved_at=func.now())
    db_session.add(old)
    db_session.commit()
    entry.amount = Decimal("-581")
    db_session.commit()
    event_id = _max_event(db_session)

    res = _sweep(db_session, stmt)

    assert res.reopened_cases == [old.id] and res.created_cases == []
    db_session.refresh(old)
    assert (old.status, old.version) == ("open", 2)
    assert (old.resolved_at, old.resolved_by, old.resolved_action_id, old.explanation) == (None, None, None, None)
    prev = old.context.pop("previous_resolution")
    assert old.context == {"k": 1, "reopened_by_event": event_id, "op": "update", "row_id": entry.id}
    assert (prev["resolved_by"], prev["resolved_action_id"], prev["explanation"]) == ("owner", 7, "accepted_exception")
    assert isinstance(prev["resolved_at"], str)
    assert db_session.get(AccountStatement, stmt.id).open_case_count == 1


@pytest.mark.parametrize("status", ["open", "proposed"])
def test_edit_bumps_an_already_open_newest_case_without_inserting(claimed, db_session, status):
    stmt, (l1, _), card, entry = claimed()
    case = ReconciliationCase(statement_id=stmt.id, revision_id=l1.revision_id, kind="amount_delta",
                              event_id=l1.event_id, line_id=l1.id, status=status, context={"k": 1})
    db_session.add(case)
    db_session.commit()
    entry.amount = Decimal("-581")
    db_session.commit()
    event_id = _max_event(db_session)

    res = _sweep(db_session, stmt)

    assert res.created_cases == [] and res.reopened_cases == [case.id] and res.stale_lines == [l1.id]
    db_session.refresh(case)
    assert (case.status, case.version) == (status, 2)
    assert case.context == {"k": 1, "reopened_by_event": event_id, "op": "update", "row_id": entry.id}
    assert db_session.query(ReconciliationCase).count() == 1


@pytest.mark.parametrize("status", ["dismissed", "superseded"])
def test_edit_after_a_closed_newest_case_opens_a_new_recheck(claimed, db_session, status):
    stmt, (l1, _), card, entry = claimed()
    case = ReconciliationCase(statement_id=stmt.id, revision_id=l1.revision_id, kind="amount_delta",
                              event_id=l1.event_id, line_id=l1.id, status=status)
    db_session.add(case)
    db_session.commit()
    entry.amount = Decimal("-581")
    db_session.commit()

    res = _sweep(db_session, stmt)

    assert res.reopened_cases == [] and len(res.created_cases) == 1
    assert db_session.get(ReconciliationCase, case.id).status == status


# (b) historical amount edit
def test_amount_edit_on_historical_statement_releases_without_case(claimed, db_session):
    stmt, (l1, _), card, entry = claimed(live=False)
    assert stmt.mode == "historical"
    ews.update_entry(db_session, entry.id, _update(entry, card))
    db_session.commit()

    res = _sweep(db_session, stmt)

    assert res.stale_lines == [l1.id] and res.created_cases == [] and res.reopened_cases == []
    assert _coverage(db_session)[0].status == "stale"
    assert db_session.query(ReconciliationCase).count() == 0
    assert db_session.get(AccountStatement, stmt.id).swept_through_event_id == _max_event(db_session)


# (c) delete of the claimed entry
def test_delete_of_claimed_entry_nulls_row_keeps_snapshot_and_releases(claimed, db_session):
    stmt, (l1, _), card, entry = claimed()
    entry_id = entry.id
    ews.delete_entry(db_session, entry_id)
    db_session.commit()

    res = _sweep(db_session, stmt)

    row = _coverage(db_session)[0]
    assert row.entry_id is None and row.status == "stale" and row.stale_reason.startswith("dirty:delete:")
    assert row.snapshot["amount"] == "-580.0000" and row.snapshot["account_id"] == card.id
    case = db_session.get(ReconciliationCase, res.created_cases[0])
    assert case.context["op"] == "delete" and case.context["row_id"] == entry_id and case.entry_id is None


# (d) split dissolve: group event + member events release the whole group claim once
def test_split_dissolve_releases_the_group_claim_as_a_whole(seed, db_session, run):
    card = _card(seed, db_session)
    stmt, (l1, _) = _submit(db_session, run, card)
    g = seed.group()
    a = seed.entry(card, "-300", day=date(2026, 9, 3), group_id=g.id)
    b = seed.entry(card, "-280", day=date(2026, 9, 3), group_id=g.id)
    db_session.flush(); db_session.refresh(a); db_session.refresh(b)
    _claim(db_session, stmt, l1, [a, b], roles=["principal", "member"])
    db_session.commit()
    set_dirty(db_session, True)

    ss.update_split(db_session, g.id, SplitIn.model_validate(
        {"entry_date": "2026-09-03", "members": [{"id": a.id, "keep": True}]}))
    db_session.commit()
    kinds = {(r.kind, r.op) for r in db_session.query(CoverageDirty)}
    assert {("group", "delete"), ("entry", "delete"), ("entry", "update")} <= kinds

    res = _sweep(db_session, stmt)

    assert res.stale_lines == [l1.id] and len(res.created_cases) == 1
    rows = _coverage(db_session)
    assert [r.status for r in rows] == ["stale", "stale"]


def test_group_only_edit_on_a_covered_group_releases_its_lines(seed, db_session, run):
    card = _card(seed, db_session)
    stmt, (l1, _) = _submit(db_session, run, card)
    g = seed.group(name="聚餐")
    a = seed.entry(card, "-300", day=date(2026, 9, 3), group_id=g.id)
    b = seed.entry(card, "-280", day=date(2026, 9, 3), group_id=g.id)
    db_session.flush(); db_session.refresh(a); db_session.refresh(b)
    _claim(db_session, stmt, l1, [a, b], roles=["principal", "member"])
    db_session.commit()
    set_dirty(db_session, True)
    g.name = "改名"
    db_session.commit()
    event_id = _max_event(db_session)

    res = _sweep(db_session, stmt)

    assert res.stale_lines == [l1.id]
    assert {r.stale_reason for r in _coverage(db_session)} == {f"dirty:group:{event_id}"}


# (e) MOZE re-import path
def test_bulk_delete_without_session_sync_releases(claimed, db_session):
    stmt, (l1, _), card, entry = claimed()
    db_session.execute(delete(LedgerEntry).where(LedgerEntry.id.in_([entry.id]))
                       .execution_options(synchronize_session=False))
    db_session.commit()

    res = _sweep(db_session, stmt)

    assert res.stale_lines == [l1.id]
    row = _coverage(db_session)[0]
    assert row.status == "stale" and row.entry_id is None


# (f) account config
@pytest.mark.parametrize("live", [True, False])
def test_opening_balance_edit_flags_recheck_in_live_mode_only(claimed, db_session, live):
    stmt, _, card, _ = claimed(live=live)
    card.opening_balance = Decimal("5")
    db_session.commit()

    res = _sweep(db_session, stmt)
    db_session.commit()

    assert res.recheck is live and res.stale_lines == []
    stmt = db_session.get(AccountStatement, stmt.id)
    assert stmt.needs_recheck is live and stmt.swept_through_event_id == _max_event(db_session)
    assert _coverage(db_session)[0].status == "active"


# (g) insert into the period
def test_insert_into_the_period_flags_recheck_and_outside_does_not(claimed, seed, db_session):
    stmt, _, card, _ = claimed()
    seed.entry(card, "-99", day=date(2026, 10, 10))
    db_session.commit()
    assert _sweep(db_session, stmt).recheck is False
    seed.entry(card, "-99", day=date(2026, 9, 10))
    db_session.commit()

    res = _sweep(db_session, stmt)

    assert res.recheck is True and res.stale_lines == []
    assert db_session.get(AccountStatement, stmt.id).needs_recheck is True


def test_insert_on_a_combined_child_account_counts_as_the_statements_population(claimed, seed, db_session):
    stmt, _, card, _ = claimed()
    child = seed.account("附卡", is_credit=True, combined_account_id=card.id)
    other = seed.account("錢包")
    db_session.commit()
    assert cov.participating_accounts(db_session, card.id) == [card.id, child.id]
    assert cov.participating_accounts(db_session, other.id) == [other.id]
    seed.entry(child, "-10", day=date(2026, 9, 12))
    db_session.commit()

    assert _sweep(db_session, stmt).recheck is True


# (h) uncovered in-period entry edit; cross-period move
def test_edit_of_uncovered_in_period_entry_flags_recheck_without_release(claimed, seed, db_session):
    stmt, _, card, _ = claimed()
    set_dirty(db_session, False)
    loose = seed.entry(card, "-50", day=date(2026, 9, 5))
    db_session.commit()
    set_dirty(db_session, True)
    loose.amount = Decimal("-60")
    db_session.commit()

    res = _sweep(db_session, stmt)

    assert res.recheck is True and res.stale_lines == [] and res.created_cases == []
    assert _coverage(db_session)[0].status == "active"


def test_cross_period_move_of_unclaimed_entry_flags_both_statements(claimed, seed, db_session, run):
    sep, _, card, _ = claimed()
    set_dirty(db_session, False)
    octo, _ = _submit(db_session, run, card, OCT)
    loose = seed.entry(card, "-50", day=date(2026, 9, 5))
    db_session.commit()
    set_dirty(db_session, True)
    loose.entry_date = loose.posted_date = date(2026, 10, 5)
    db_session.commit()

    a, b = _sweep(db_session, sep), _sweep(db_session, octo)

    assert a.recheck is True and b.recheck is True and a.stale_lines == b.stale_lines == []
    assert _coverage(db_session)[0].status == "active"


# (i) self-event skip
def _applied_action(db, stmt, line, key):
    case = ReconciliationCase(statement_id=stmt.id, revision_id=line.revision_id, kind="recheck",
                              event_id=line.event_id, line_id=line.id, status="resolved")
    db.add(case)
    db.flush()
    action = ReconciliationAction(idempotency_key=key, event_id=line.event_id, effect_slot="match", case_id=case.id,
                                  action="match", params={}, actor="owner", status="applied")
    db.add(action)
    db.commit()
    return action


def test_events_of_an_action_applied_on_this_statement_are_skipped(claimed, db_session):
    stmt, (l1, _), card, entry = claimed()
    action = _applied_action(db_session, stmt, l1, "self")
    db_session.execute(text(f"SET LOCAL app.reconciliation_action_id = '{action.id}'"))
    entry.amount = Decimal("-600")
    db_session.commit()
    assert db_session.query(CoverageDirty).filter_by(action_id=action.id).count() == 1

    res = _sweep(db_session, stmt)

    assert res.stale_lines == [] and res.created_cases == [] and res.reopened_cases == []
    assert res.swept_through == _max_event(db_session)
    assert _coverage(db_session)[0].status == "active"


def test_events_of_an_action_applied_on_another_statement_are_not_skipped(claimed, db_session, run):
    stmt, (l1, _), card, entry = claimed()
    set_dirty(db_session, False)
    octo, oct_lines = _submit(db_session, run, card, OCT)
    db_session.commit()
    action = _applied_action(db_session, octo, oct_lines[0], "other")
    set_dirty(db_session, True)
    db_session.execute(text(f"SET LOCAL app.reconciliation_action_id = '{action.id}'"))
    entry.amount = Decimal("-600")
    db_session.commit()

    assert _sweep(db_session, stmt).stale_lines == [l1.id]


# (j) unrelated events, batches
def test_watermark_advances_past_unrelated_events(claimed, seed, db_session):
    stmt, _, card, _ = claimed()
    wallet = seed.account("錢包")
    seed.entry(wallet, "-10", day=date(2026, 9, 3))
    db_session.commit()
    wallet.opening_balance = Decimal("9")
    db_session.commit()
    top = _max_event(db_session)
    assert top > 0

    res = _sweep(db_session, stmt)
    db_session.commit()

    assert (res.stale_lines, res.created_cases, res.recheck, res.swept_through) == ([], [], False, top)
    stmt = db_session.get(AccountStatement, stmt.id)
    assert stmt.swept_through_event_id == top and stmt.needs_recheck is False
    assert _coverage(db_session)[0].status == "active"


def test_scan_crosses_batches_and_reaches_the_relevant_event(claimed, db_session):
    stmt, (l1, _), card, entry = claimed()
    db_session.execute(text("INSERT INTO coverage_dirty (kind, row_id, op, old_account_id, new_account_id) "
                            "SELECT 'entry', g, 'update', 0, 0 FROM generate_series(1, 2500) g"))
    entry.amount = Decimal("-1")
    db_session.commit()
    top = _max_event(db_session)

    res = _sweep(db_session, stmt)

    assert res.stale_lines == [l1.id] and res.swept_through == top
    assert db_session.query(CoverageDirty).count() == 2501  # never deletes events


# (k) gated triggers
def test_dirty_disabled_leaves_nothing_to_sweep(claimed, db_session):
    stmt, _, card, entry = claimed()
    set_dirty(db_session, False)
    entry.amount = Decimal("-600")
    db_session.commit()
    assert db_session.query(CoverageDirty).count() == 0

    res = _sweep(db_session, stmt)
    db_session.commit()

    assert (res.stale_lines, res.swept_through, res.recheck) == ([], 0, False)
    assert db_session.get(AccountStatement, stmt.id).swept_through_event_id == 0
    assert _coverage(db_session)[0].status == "active"


# (l) sweep_pending
def test_sweep_pending_lists_statements_with_unswept_relevant_events(claimed, seed, db_session):
    stmt, _, card, entry = claimed()
    wallet = seed.account("錢包")
    db_session.commit()
    assert stmt.id not in cov.sweep_pending(db_session)
    seed.entry(wallet, "-1")
    db_session.commit()
    assert stmt.id not in cov.sweep_pending(db_session)
    entry.amount = Decimal("-600")
    db_session.commit()
    assert cov.sweep_pending(db_session) == [stmt.id]
    _sweep(db_session, stmt)
    db_session.commit()
    assert cov.sweep_pending(db_session) == []


def test_sweep_pending_sees_combined_child_and_covered_group_events(seed, db_session, run):
    card = _card(seed, db_session)
    child = seed.account("附卡", is_credit=True, combined_account_id=card.id)
    stmt, (l1, _) = _submit(db_session, run, card)
    g = seed.group(name="聚餐")
    a = seed.entry(card, "-580", day=date(2026, 9, 3), group_id=g.id)
    db_session.flush(); db_session.refresh(a)
    _claim(db_session, stmt, l1, [a])
    db_session.commit()
    set_dirty(db_session, True)
    seed.entry(child, "-1", day=date(2026, 9, 4))
    db_session.commit()
    assert cov.sweep_pending(db_session) == [stmt.id]
    _sweep(db_session, stmt)
    db_session.commit()
    g.name = "改名"
    db_session.commit()
    assert cov.sweep_pending(db_session) == [stmt.id]


# fix round 1 ------------------------------------------------------------------------------------------------------

def test_covered_entry_moved_to_a_non_participating_account_releases(claimed, seed, db_session):
    stmt, (l1, _), card, entry = claimed()
    wallet = seed.account("錢包")
    db_session.commit()
    entry.account_id = wallet.id
    db_session.commit()

    res = _sweep(db_session, stmt)

    assert res.stale_lines == [l1.id] and len(res.created_cases) == 1


def test_group_delete_without_member_events_releases_via_the_snapshot(seed, db_session, run):
    card = _card(seed, db_session)
    stmt, (l1, _) = _submit(db_session, run, card)
    g = seed.group()
    a = seed.entry(card, "-300", day=date(2026, 9, 3), group_id=g.id)
    b = seed.entry(card, "-280", day=date(2026, 9, 3), group_id=g.id)
    db_session.flush(); db_session.refresh(a); db_session.refresh(b)
    _claim(db_session, stmt, l1, [a, b], roles=["principal", "member"])
    a.group_id = b.group_id = None  # detached while tracking was off: no member events
    db_session.commit()
    set_dirty(db_session, True)
    db_session.execute(text("DELETE FROM entry_group WHERE id = :id"), {"id": g.id})
    db_session.commit()
    assert [(r.kind, r.op) for r in db_session.query(CoverageDirty)] == [("group", "delete")]
    assert {r.group_id for r in _coverage(db_session)} == {None}  # SET NULL
    assert cov.sweep_pending(db_session) == [stmt.id]

    res = _sweep(db_session, stmt)

    assert res.stale_lines == [l1.id]


def test_deleted_covered_entry_outside_the_population_is_pending_via_the_snapshot(seed, db_session, run):
    card = _card(seed, db_session)
    stmt, (l1, _) = _submit(db_session, run, card)
    wallet = seed.account("錢包")
    foreign = seed.entry(wallet, "-580", day=date(2026, 9, 3))
    db_session.flush(); db_session.refresh(foreign)
    _claim(db_session, stmt, l1, [foreign])
    db_session.commit()
    set_dirty(db_session, True)
    db_session.execute(delete(LedgerEntry).where(LedgerEntry.id == foreign.id)
                       .execution_options(synchronize_session=False))
    db_session.commit()
    assert _coverage(db_session)[0].entry_id is None

    assert cov.sweep_pending(db_session) == [stmt.id]
    assert _sweep(db_session, stmt).stale_lines == [l1.id]


def test_combined_child_relink_flags_both_parents(seed, db_session, run):
    p1 = _card(seed, db_session, name="P1")
    p2 = _card(seed, db_session, name="P2")
    child = seed.account("附卡", is_credit=True, combined_account_id=p1.id)
    s1, _ = _submit(db_session, run, p1)
    s2, _ = _submit(db_session, run, p2)
    db_session.commit()
    set_dirty(db_session, True)
    child.combined_account_id = p2.id
    db_session.commit()

    assert cov.sweep_pending(db_session) == sorted([s1.id, s2.id])
    a, b = _sweep(db_session, s1), _sweep(db_session, s2)
    db_session.commit()

    assert a.recheck is True and b.recheck is True
    assert db_session.get(AccountStatement, s1.id).needs_recheck is True
    assert db_session.get(AccountStatement, s2.id).needs_recheck is True
    assert cov.sweep_pending(db_session) == []


def test_sweep_waits_for_uncommitted_writers_so_no_event_is_skipped(claimed, seed, pg_engine, db_session):
    """The writer barrier: an event whose id is assigned but not yet committed must not fall under the watermark
    when a later-id event commits first."""
    stmt, (l1, _), card, entry = claimed()
    wallet = seed.account("錢包")
    db_session.commit()
    factory = sessionmaker(bind=pg_engine, autoflush=False)
    holder, sweeper = factory(), factory()
    outcome: list = []
    pid: list = []

    def run_sweep():
        try:
            pid.append(sweeper.execute(text("SELECT pg_backend_pid()")).scalar_one())
            ready.set()
            outcome.append(_sweep(sweeper, sweeper.get(AccountStatement, stmt.id)))
            sweeper.commit()
        except Exception as exc:  # noqa: BLE001  (asserted below)
            sweeper.rollback()
            outcome.append(exc)

    ready = threading.Event()
    thread = threading.Thread(target=run_sweep, daemon=True)
    try:
        holder.execute(text("UPDATE ledger_entry SET amount = -600 WHERE id = :id"), {"id": entry.id})  # id N, open
        seed.entry(wallet, "-10")
        db_session.commit()  # a later id (N+1) commits first
        thread.start()
        assert ready.wait(5)
        assert wait_until_blocked(pg_engine, pid[0]) == "advisory"
        assert not outcome, "the sweep finished while a writer still held the barrier"
        holder.commit()
        thread.join(5)
        assert not thread.is_alive()
    finally:
        holder.rollback()
        thread.join(5)
        holder.close(); sweeper.close()
    res = outcome[0]
    assert not isinstance(res, Exception), res
    assert res.stale_lines == [l1.id] and res.swept_through == _max_event(db_session)
    assert cov.sweep(db_session, db_session.get(AccountStatement, stmt.id)).stale_lines == []


def _barrier_holders(engine) -> int:
    with engine.connect() as conn:
        return conn.execute(text("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND objid = :key"),
                            {"key": cov.DIRTY_BARRIER_KEY}).scalar_one()


@pytest.mark.parametrize("failure", ["raises", "returns_false"])
def test_a_failed_barrier_unlock_invalidates_the_connection(pg_engine, monkeypatch, failure):
    class FuncProxy:
        def __getattr__(self, name):
            if name != "pg_advisory_unlock":
                return getattr(func, name)
            if failure == "raises":
                return lambda key: func.pg_advisory_unlock_no_such_function(key)
            return lambda key: func.pg_advisory_unlock(key + 1)  # a lock this session does not hold: returns false

    session = sessionmaker(bind=pg_engine, autoflush=False)()
    try:
        connection = session.connection()
        monkeypatch.setattr(cov, "func", FuncProxy())
        with pytest.raises(Exception):
            cov._committed_cap(session)
        assert connection.invalidated
        monkeypatch.undo()
        session.rollback()
    finally:
        session.close()
    assert _barrier_holders(pg_engine) == 0  # the invalidated connection took its session lock with it
    fresh = sessionmaker(bind=pg_engine, autoflush=False)()
    try:
        cov._committed_cap(fresh)
        assert _barrier_holders(pg_engine) == 0
    finally:
        fresh.close()


def test_dirty_id_sequence_is_not_cached(db_session):
    """The sweep's cap argument needs ids handed out one at a time (CACHE 1); see coverage_service.sweep."""
    cache = db_session.execute(text(
        "SELECT cache_size FROM pg_sequences WHERE sequencename = 'coverage_dirty_id_seq'")).scalar_one()
    assert cache == 1
