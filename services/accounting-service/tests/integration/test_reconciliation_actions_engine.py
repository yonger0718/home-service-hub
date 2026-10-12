"""Reconciliation action engine (R1c Task 4; spec §4.9, §7.4, C6; rulings 6, 7, 8, 14–16, 18, 24, 25, 29):
idempotent replay, effect slots, the D32 lock order with NOWAIT row locks, snapshot conflicts, audit, `match` /
`unmatch`, `revert`, and owner coverage surviving reconcile. Stand-in actions (`Fake*`) take the names of later
tasks' actions (adjust_amount, create_entry, create_system_entry, dismiss) so the registry table admits them; they
exercise the engine, not those actions. Synthetic data only."""
import threading
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import sessionmaker

from app.models import (
    DIRTY_BARRIER_KEY, AccountStatement, CoverageDirty, LedgerEntry, ReconciliationAction, ReconciliationAudit,
    ReconciliationCase, StatementCoverage,
)
from app.services import coverage_service as cov
from app.services import entry_write_service as ews
from app.services import reconciliation_actions as registry
from app.services import reconciliation_service as rec
from app.services import schedule_locks, schedule_service, settings_service
from app.services import schedule_posting as posting
from app.services.errors import CodedConflictError, ValidationError
from app.services.reconciliation_actions import base, engine
from app.services.reconciliation_actions.coverage_actions import MatchAction
from tests.helpers import race, set_dirty, wait_until_blocked
from tests.integration.statement_fixtures import (  # noqa: F401  (run is a fixture)
    _active, _card, _cases, _code, _interleave, _line, _reconcile, _submit, run,
)

DAY = date(2026, 9, 3)


# --- helpers ------------------------------------------------------------------------------------------------------

def _apply(db, case, action="match", params=None, *, commit=True, **kw):
    kw.setdefault("actor", "owner")
    kw.setdefault("label", "owner-test")
    row, replayed = engine.apply_action(db, case_id=case if isinstance(case, int) else case.id, action=action,
                                        params=params or {}, **kw)
    if commit:
        db.commit()
    return row, replayed


def _revert(db, action_id, **kw):
    row = engine.revert_action(db, action_id=action_id, actor="owner", label="owner-test", **kw)
    db.commit()
    return row


def _refuse(db, fn):
    """The call raises a 422/409 service error; the session is rolled back as the router would."""
    with pytest.raises((ValidationError, CodedConflictError)) as info:
        fn()
    db.rollback()
    return info.value


def _case(db, stmt, line, kind, **fields) -> ReconciliationCase:
    case = ReconciliationCase(statement_id=stmt.id, revision_id=stmt.current_revision_id, kind=kind,
                              event_id=line.event_id if line is not None else None,
                              line_id=line.id if line is not None else None, **fields)
    db.add(case)
    db.commit()
    return case


def _actions(db) -> list[ReconciliationAction]:
    db.expire_all()
    return db.execute(select(ReconciliationAction).order_by(ReconciliationAction.id)).scalars().all()


def _audit(db) -> list[ReconciliationAudit]:
    db.expire_all()
    return db.execute(select(ReconciliationAudit).order_by(ReconciliationAudit.id)).scalars().all()


def _reopen(db, case_id):
    """What the sweep does to a resolved case whose line it released: open again at a new version."""
    case = db.get(ReconciliationCase, case_id)
    case.status, case.version = "open", case.version + 1
    case.resolved_by = case.resolved_action_id = case.resolved_at = None
    db.commit()


def _match(s, **extra) -> dict:
    return {"line_id": s.line.id, "entry_id": s.entry.id, **extra}


@pytest.fixture
def unmatched(seed, db_session, run):
    """A live September card statement whose 580 purchase line had no entry when it was reconciled (an open
    line_unmatched case); the entry is booked afterwards, so only an action claims it."""
    def build(amount="-580", **entry_fields):
        card = _card(seed, db_session)
        stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)])
        (case,) = _cases(db_session, stmt.id, "open")
        assert case.kind == "line_unmatched"
        entry = seed.entry(card, amount, day=DAY, merchant="全聯", **entry_fields)
        db_session.commit()
        return SimpleNamespace(card=card, stmt=stmt, line=line, case=case, entry=entry)
    return build


@pytest.fixture
def claimed(seed, db_session, run):
    """A live statement whose line the matcher claimed, plus an open recheck case on that line."""
    card = _card(seed, db_session)
    entry = seed.entry(card, "-580", day=DAY, merchant="全聯")
    db_session.commit()
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)])
    (row,) = _active(db_session, stmt.id)
    case = _case(db_session, stmt, line, "recheck")
    return SimpleNamespace(card=card, stmt=stmt, line=line, case=case, entry=entry, row=row)


class _AmountParams(base.ActionParams):
    entry_id: int
    amount: Decimal


class FakeAdjust(base.ActionBase):
    """Stands in for Task 6's adjust_amount: one ledger write on a locked entry (default `prepare`)."""
    name = "adjust_amount"
    Params = _AmountParams
    case_kinds = frozenset({"amount_delta"})
    slot = "amount"

    def lock_targets(self, ctx, params):
        return base.LockSet(entry_ids=(params.entry_id,))

    def apply(self, ctx, params, prepared, effect):
        entry = ctx.db.get(LedgerEntry, params.entry_id)
        effect.inverse = {"amount": str(entry.amount)}
        entry.amount = params.amount
        ctx.db.flush()
        effect.touched_entry_ids = [entry.id]


class _NoParams(base.ActionParams):
    pass


class FakeCreate(base.ActionBase):
    """Stands in for Task 6's creations (slot `create`): books the line as a new entry."""
    Params = _NoParams
    case_kinds = frozenset({"line_unmatched"})
    slot = "create"
    resolves = True

    def __init__(self, name):
        self.name = name

    def apply(self, ctx, params, prepared, effect):
        entry = LedgerEntry(account_id=ctx.statement.account_id, kind="expense", amount=ctx.line.flow_amount,
                            currency=ctx.statement.currency, entry_date=ctx.line.posted_date, source="statement")
        ctx.db.add(entry)
        ctx.db.flush()
        effect.created_entry_ids = [entry.id]
        effect.resolves_case = self.resolves
        if not self.resolves:
            effect.case_context = {"created_entry_id": entry.id}


class FakeDismiss(base.ActionBase):
    name = "dismiss"
    Params = _NoParams
    case_kinds = frozenset(registry.KIND_ACTIONS)  # no ledger write: the default v<version> slot (ruling 31)

    def apply(self, ctx, params, prepared, effect):
        effect.case_status = "dismissed"


class FakeBarrier(base.ActionBase):
    """Stands in for Task 7's balance_adjustment_asof: account lock + the exclusive writer barrier."""
    name = "balance_adjustment_asof"
    Params = _NoParams
    case_kinds = frozenset({"balance_gap"})
    slot = "balance"
    seen: list = []

    def lock_targets(self, ctx, params):
        return base.LockSet(account_lock=True, barrier=True)

    def apply(self, ctx, params, prepared, effect):
        FakeBarrier.seen.append(ctx.db.execute(text(
            "SELECT mode FROM pg_locks WHERE locktype = 'advisory' AND objid = :k AND pid = pg_backend_pid() "
            "AND granted"), {"k": DIRTY_BARRIER_KEY}).scalars().all())


@pytest.fixture
def fakes(monkeypatch):
    def register(*actions):
        for action in actions:
            monkeypatch.setitem(registry.ACTIONS, action.name, action)
    return register


# --- 1. match ------------------------------------------------------------------------------------------------------

def test_match_claims_resolves_recounts_and_audits(unmatched, db_session):
    s = unmatched()
    version = s.case.version
    set_dirty(db_session, True)
    row, replayed = _apply(db_session, s.case, params=_match(s))

    assert replayed is False
    (claim,) = _active(db_session, s.stmt.id)
    assert (claim.line_id, claim.entry_id, claim.role, claim.match_kind, claim.match_rule) == (
        s.line.id, s.entry.id, "principal", "manual", "owner")
    action = db_session.get(ReconciliationAction, row.id)
    assert (action.action, action.effect_slot, action.status, action.actor, action.event_id, action.case_id) == (
        "match", f"v{version}", "applied", "owner", s.line.event_id, s.case.id)
    assert action.params == {"line_id": s.line.id, "entry_id": s.entry.id, "group_id": None, "with_children": False,
                             "allow_sibling": False}
    assert (action.touched_entry_ids, action.created_entry_ids, action.deleted_entry_ids) == ([s.entry.id], [], [])
    key = str(s.entry.id)
    assert action.before["entries"][key]["coverage_ids"] == []
    assert action.after["entries"][key]["coverage_ids"] == [claim.id] and action.after["coverage_ids"] == [claim.id]
    assert action.after["entries"][key]["amount"] == action.before["entries"][key]["amount"]
    assert action.inverse == {"release_line": s.line.id}
    case = db_session.get(ReconciliationCase, s.case.id)
    assert (case.status, case.resolved_by, case.resolved_action_id, case.version) == (
        "resolved", "owner", row.id, version)
    assert case.resolved_at is not None
    stmt = db_session.get(AccountStatement, s.stmt.id)
    assert (stmt.matched_count, stmt.open_case_count, stmt.needs_recheck) == (1, 0, False)
    (audit,) = _audit(db_session)
    assert (audit.event, audit.action_id, audit.case_id, audit.statement_id, audit.actor) == (
        "action_applied", row.id, s.case.id, s.stmt.id, "owner-test")
    assert audit.detail["action"] == "match" and audit.detail["coverage_ids"] == [claim.id]
    # coverage only: no ledger row was written, so no dirty event
    assert db_session.execute(select(func.count()).select_from(CoverageDirty)).scalar_one() == 0


def test_match_with_children_and_a_group(unmatched, seed, db_session):
    s = unmatched(amount="-560")
    child = seed.entry(s.card, "-20", kind="fee", day=DAY, parent_entry_id=s.entry.id)
    db_session.commit()
    exc = _refuse(db_session, lambda: _apply(db_session, s.case, params=_match(s)))  # -560 alone ≠ 580
    assert _code(exc) == "not_conserved"
    row, _ = _apply(db_session, s.case, params=_match(s, with_children=True))
    assert sorted((c.entry_id, c.role) for c in _active(db_session, s.stmt.id)) == sorted(
        [(s.entry.id, "principal"), (child.id, "child")])
    assert sorted(row.touched_entry_ids) == sorted([s.entry.id, child.id])
    _revert(db_session, row.id)

    group = seed.group("split")
    m1 = seed.entry(s.card, "-300", day=DAY, group_id=group.id)
    m2 = seed.entry(s.card, "-280", day=DAY, group_id=group.id)
    db_session.commit()
    exc = _refuse(db_session, lambda: _apply(db_session, s.case, params={"line_id": s.line.id, "entry_id": m1.id}))
    assert _code(exc) == "entry_in_group"
    row, _ = _apply(db_session, s.case, params={"line_id": s.line.id, "group_id": group.id})
    assert sorted((c.entry_id, c.role, c.group_id) for c in _active(db_session, s.stmt.id)) == sorted(
        [(m1.id, "member", group.id), (m2.id, "member", group.id)])
    assert row.touched_group_ids == [group.id] and str(group.id) in row.after["groups"]
    assert row.after["groups"][str(group.id)]["member_ids"] == sorted([m1.id, m2.id])


# --- 2. replay, keys after a revert, reverted keys --------------------------------------------------------------------

def test_replay_returns_the_stored_action_and_a_revert_frees_the_key(unmatched, db_session):
    s = unmatched()
    first, _ = _apply(db_session, s.case, params=_match(s))
    again, replayed = _apply(db_session, s.case, params=_match(s))
    assert (again.id, replayed) == (first.id, True)
    # key order and explicit defaults canonicalise to the same key
    same, replayed = _apply(db_session, s.case, params={"entry_id": s.entry.id, "with_children": False,
                                                         "line_id": s.line.id})
    assert (same.id, replayed) == (first.id, True)
    assert len(_active(db_session, s.stmt.id)) == 1
    assert [a.event for a in _audit(db_session)] == ["action_applied", "action_replayed", "action_replayed"]

    _revert(db_session, first.id)
    fresh, replayed = _apply(db_session, s.case, params=_match(s))
    assert fresh.id != first.id and replayed is False and fresh.idempotency_key != first.idempotency_key
    assert len(_active(db_session, s.stmt.id)) == 1

    _revert(db_session, fresh.id)
    case = db_session.get(ReconciliationCase, s.case.id)
    key = engine.idempotency_key(case.id, case.version, "match",
                                 engine.canonical_params(MatchAction.Params(**_match(s))))
    db_session.add(ReconciliationAction(idempotency_key=key, event_id=s.line.event_id, effect_slot=f"v{case.version}",
                                        case_id=case.id, action="match", params=_match(s), actor="owner",
                                        status="reverted"))
    db_session.commit()
    exc = _refuse(db_session, lambda: _apply(db_session, s.case, params=_match(s)))
    assert isinstance(exc, CodedConflictError) and exc.code == "action_reverted"
    assert _active(db_session, s.stmt.id) == []


# --- 3. duplicate effect on a new case version --------------------------------------------------------------------

def test_a_match_on_a_reopened_case_claims_again(unmatched, db_session):
    """Ruling 31: coverage-only actions are one effect per case VERSION (slot v<version>): after the sweep released
    the owner's claim and reopened the case, the owner can match the event again."""
    s = unmatched()
    first, _ = _apply(db_session, s.case, params=_match(s))
    stmt = rec._lock_statement(db_session, s.stmt.id)
    cov.release_line(db_session, stmt, s.line.id, reason="dirty:test")  # what the sweep does on a dirty edit
    db_session.commit()
    _reopen(db_session, s.case.id)

    again, replayed = _apply(db_session, s.case, params=_match(s))

    assert replayed is False and again.id != first.id
    assert (first.effect_slot, again.effect_slot) == ("v1", "v2")
    assert [(c.line_id, c.entry_id, c.match_kind) for c in _active(db_session, s.stmt.id)] == [
        (s.line.id, s.entry.id, "manual")]
    assert [a.status for a in _actions(db_session)] == ["applied", "applied"]
    assert db_session.get(ReconciliationCase, s.case.id).status == "resolved"


def test_a_second_match_on_the_same_case_version_is_a_duplicate_effect(unmatched, seed, db_session):
    """Within one case version the slot still guards: a different-params match (a new idempotency key) on the same
    event and version is refused by the partial unique index."""
    s = unmatched()
    other = seed.entry(s.card, "-580", day=DAY, merchant="全聯")
    db_session.commit()
    _apply(db_session, s.case, params=_match(s))
    stmt = rec._lock_statement(db_session, s.stmt.id)
    cov.release_line(db_session, stmt, s.line.id, reason="test")
    case = db_session.get(ReconciliationCase, s.case.id)
    case.status = "open"  # open again WITHOUT a version bump
    db_session.commit()

    exc = _refuse(db_session, lambda: _apply(db_session, s.case, params={"line_id": s.line.id,
                                                                          "entry_id": other.id}))
    assert isinstance(exc, ValidationError) and exc.code == "duplicate_effect" and exc.field == "action"
    assert _active(db_session, s.stmt.id) == [] and len(_actions(db_session)) == 1


def test_a_failed_row_under_the_key_is_action_failed(unmatched, db_session):
    s = unmatched()
    key = engine.idempotency_key(s.case.id, s.case.version, "match",
                                 engine.canonical_params(MatchAction.Params(**_match(s))))
    db_session.add(ReconciliationAction(idempotency_key=key, event_id=s.line.event_id, effect_slot="v1",
                                        case_id=s.case.id, action="match", params=_match(s), actor="owner",
                                        status="failed"))
    db_session.commit()
    exc = _refuse(db_session, lambda: _apply(db_session, s.case, params=_match(s)))
    assert isinstance(exc, CodedConflictError) and exc.code == "action_failed"
    assert _active(db_session, s.stmt.id) == []


# --- 4. participating accounts and siblings ------------------------------------------------------------------------

def test_match_needs_a_participating_account_or_an_allowed_sibling(unmatched, seed, db_session):
    s = unmatched()
    sibling = seed.account("姊妹")
    stranger = seed.account("路人")
    theirs = seed.entry(sibling, "-580", day=DAY)
    other = seed.entry(stranger, "-580", day=DAY)
    db_session.commit()

    def attempt(entry, allow):
        return _refuse(db_session, lambda: _apply(db_session, s.case, params={
            "line_id": s.line.id, "entry_id": entry.id, "allow_sibling": allow}))

    for entry, allow in ((other, False), (other, True), (theirs, False), (theirs, True)):
        assert _code(attempt(entry, allow)) == "wrong_account"
    settings_service.update_reconciliation_settings(db_session, {"account_siblings": {str(s.card.id): [sibling.id]}})
    db_session.commit()
    assert _code(attempt(other, True)) == "wrong_account"
    assert _code(attempt(theirs, False)) == "wrong_account"

    _apply(db_session, s.case, params={"line_id": s.line.id, "entry_id": theirs.id, "allow_sibling": True})
    (claim,) = _active(db_session, s.stmt.id)
    assert claim.entry_id == theirs.id and claim.snapshot["account_id"] == sibling.id


# --- 5. conservation, scoping, refusals ------------------------------------------------------------------------------

def test_match_refusals_write_nothing(unmatched, seed, db_session, run):
    s = unmatched(amount="-500")
    exc = _refuse(db_session, lambda: _apply(db_session, s.case, params=_match(s)))
    assert isinstance(exc, ValidationError) and exc.code == "not_conserved"
    exc = _refuse(db_session, lambda: _apply(db_session, s.case, params={"line_id": s.line.id}))
    assert isinstance(exc, ValidationError) and exc.field == "params"
    exc = _refuse(db_session, lambda: _apply(db_session, s.case, action="teleport"))
    assert isinstance(exc, ValidationError) and exc.field == "action"
    exc = _refuse(db_session, lambda: _apply(db_session, s.case, "unmatch", {"line_id": s.line.id}))
    assert isinstance(exc, ValidationError) and exc.field == "action"  # not allowed on line_unmatched
    exc = _refuse(db_session, lambda: _apply(db_session, s.case, "unmatch", {"line_id": s.line.id}, actor="policy"))
    assert isinstance(exc, ValidationError) and exc.field == "actor"  # unmatch is owner-only (match is not)
    other_line = _submit(db_session, run, s.card, [_line(1, date(2026, 10, 3), "全聯", 500)],
                         period=(date(2026, 10, 1), date(2026, 10, 31)))[1][0]
    exc = _refuse(db_session, lambda: _apply(db_session, s.case, params={"line_id": other_line.id,
                                                                          "entry_id": s.entry.id}))
    assert _code(exc) == "line_not_in_case"
    assert _active(db_session, s.stmt.id) == [] and _actions(db_session) == []
    case = db_session.get(ReconciliationCase, s.case.id)
    case.status = "resolved"
    db_session.commit()
    exc = _refuse(db_session, lambda: _apply(db_session, s.case, params=_match(s)))
    assert isinstance(exc, CodedConflictError) and exc.code == "case_closed"


def test_allowed_actions_follow_the_kind_table(unmatched, db_session, fakes):
    s = unmatched()
    # the real create_entry (Task 6) and dismiss (Task 5) are registered; create_refund needs a refund line
    assert registry.allowed(s.case) == ["match", "create_entry", "dismiss"]
    recheck = _case(db_session, s.stmt, s.line, "recheck")
    duplicate = _case(db_session, s.stmt, s.line, "duplicate_claim")
    assert registry.allowed(recheck) == ["match", "unmatch", "dismiss"]
    assert registry.allowed(duplicate) == ["unmatch", "dismiss"]
    fakes(FakeCreate("create_entry"), FakeCreate("create_system_entry"), FakeDismiss())
    assert registry.allowed(s.case) == ["match", "create_entry", "dismiss"]
    s.case.context = {"hint": "bank_only"}
    db_session.commit()
    assert registry.allowed(s.case) == ["match", "create_entry", "create_system_entry", "dismiss"]
    s.case.status = "resolved"
    assert registry.allowed(s.case) == []


# --- 6. concurrency ---------------------------------------------------------------------------------------------------

def test_concurrent_identical_matches_produce_one_effect(unmatched, db_session, pg_engine):
    s = unmatched()
    results = []

    def act(session):
        row, replayed = engine.apply_action(session, case_id=s.case.id, action="match", params=_match(s),
                                            actor="owner", label="t")
        results.append((row.id, replayed))

    outcome = race(pg_engine, act, act)

    assert outcome == "committed", outcome  # the loser replays under the statement lock (ruling 24)
    (first, replayed_first), (second, replayed_second) = results
    assert second == first and (replayed_first, replayed_second) == (False, True)
    assert len(_active(db_session, s.stmt.id)) == 1 and len(_actions(db_session)) == 1


def test_an_action_after_a_committed_delete_is_a_snapshot_conflict(unmatched, db_session, pg_engine, fakes):
    """The delete holds the shared writer barrier (dirty on): the action waits at its sweep, then finds the entry
    gone (C6) — no deadlock, nothing written."""
    s = unmatched(amount="-600")
    case = _case(db_session, s.stmt, s.line, "amount_delta", entry_id=s.entry.id)
    fakes(FakeAdjust())
    set_dirty(db_session, True)
    entry_id = s.entry.id

    outcome = race(pg_engine, lambda db: ews.delete_entry(db, entry_id),
                   lambda db: engine.apply_action(db, case_id=case.id, action="adjust_amount",
                                                  params={"entry_id": entry_id, "amount": "-580"}, actor="owner",
                                                  label="t"))

    assert _code(outcome) == "snapshot_conflict", outcome
    db_session.expire_all()
    assert db_session.get(LedgerEntry, entry_id) is None and _actions(db_session) == []


def test_an_action_yields_to_a_delete_in_flight(unmatched, db_session, pg_engine, fakes):
    """Review Focus 6: the action holds the statement and the case row; a delete of its entry locks the entry and
    waits on the case row (ON DELETE SET NULL). The action's NOWAIT entry lock yields with action_busy and the
    delete commits (waiting would deadlock)."""
    s = unmatched(amount="-600")
    case = _case(db_session, s.stmt, s.line, "amount_delta", entry_id=s.entry.id)
    fakes(FakeAdjust())
    set_dirty(db_session, True)
    entry_id, case_id = s.entry.id, case.id
    paused, go = threading.Event(), threading.Event()

    def hold(ctx):
        paused.set()
        go.wait(10)

    out = _interleave(pg_engine,
                      lambda db: engine.apply_action(db, case_id=case_id, action="adjust_amount",
                                                     params={"entry_id": entry_id, "amount": "-580"},
                                                     actor="owner", label="t", precheck=hold),
                      lambda db: ews.delete_entry(db, entry_id), paused, go)

    assert _code(out["first"]) == "action_busy" and out["second"] == "committed", out
    db_session.expire_all()
    assert db_session.get(LedgerEntry, entry_id) is None and _actions(db_session) == []


@pytest.mark.parametrize("change", ["amount", "child_edit", "child_insert", "member_relink"])
def test_a_ledger_edit_between_prepare_and_the_locks_is_a_snapshot_conflict(unmatched, seed, db_session, pg_engine,
                                                                            monkeypatch, change):
    s = unmatched(amount="-560")
    child = seed.entry(s.card, "-20", kind="fee", day=DAY, parent_entry_id=s.entry.id)
    group = seed.group("split")
    seed.entry(s.card, "-300", day=DAY, group_id=group.id)
    m2 = seed.entry(s.card, "-280", day=DAY, group_id=group.id)
    db_session.commit()
    ids = SimpleNamespace(entry=s.entry.id, child=child.id, m2=m2.id, card=s.card.id)
    params = ({"line_id": s.line.id, "group_id": group.id} if change == "member_relink"
              else _match(s, with_children=True))
    edits = {
        "amount": lambda db: setattr(db.get(LedgerEntry, ids.entry), "amount", Decimal("-561")),
        "child_edit": lambda db: setattr(db.get(LedgerEntry, ids.child), "name", "改名"),
        "child_insert": lambda db: db.add(LedgerEntry(account_id=ids.card, kind="discount", amount=Decimal("1"),
                                                      currency="TWD", entry_date=DAY, source="manual",
                                                      parent_entry_id=ids.entry)),
        "member_relink": lambda db: setattr(db.get(LedgerEntry, ids.m2), "group_id", None),
    }
    real = MatchAction.prepare

    def prepare_then_edit(self, ctx, p):
        prepared = real(self, ctx, p)
        other = sessionmaker(bind=pg_engine, autoflush=False)()
        try:
            edits[change](other)
            other.commit()
        finally:
            other.close()
        return prepared

    monkeypatch.setattr(MatchAction, "prepare", prepare_then_edit)
    exc = _refuse(db_session, lambda: _apply(db_session, s.case, params=params))
    assert isinstance(exc, CodedConflictError) and exc.code == "snapshot_conflict"
    assert _active(db_session, s.stmt.id) == [] and _actions(db_session) == []


def test_creation_family_is_one_effect_per_event(unmatched, db_session, fakes):
    s = unmatched()
    s.case.context = {"hint": "bank_only"}
    db_session.commit()
    fakes(FakeCreate("create_entry"), FakeCreate("create_system_entry"))
    set_dirty(db_session, True)
    row, _ = _apply(db_session, s.case, "create_entry")
    (created,) = row.created_entry_ids
    # the GUC tags the action's own ledger write, so the next sweep skips it
    tags = db_session.execute(select(CoverageDirty.action_id).where(CoverageDirty.row_id == created)).scalars().all()
    assert tags == [row.id]
    assert str(created) in row.after["entries"]
    _reopen(db_session, s.case.id)

    exc = _refuse(db_session, lambda: _apply(db_session, s.case, "create_system_entry"))
    assert isinstance(exc, ValidationError) and exc.code == "duplicate_effect"
    assert db_session.execute(select(func.count()).select_from(LedgerEntry)
                              .where(LedgerEntry.source == "statement")).scalar_one() == 1


def test_concurrent_creations_on_one_event_book_one_row(unmatched, db_session, pg_engine, fakes, monkeypatch):
    s = unmatched()
    s.case.context = {"hint": "bank_only"}
    db_session.commit()
    monkeypatch.setattr(FakeCreate, "resolves", False)  # the case stays open: the loser reaches the family check
    fakes(FakeCreate("create_entry"), FakeCreate("create_system_entry"))
    case_id = s.case.id

    outcome = race(pg_engine,
                   lambda db: engine.apply_action(db, case_id=case_id, action="create_entry", params={},
                                                  actor="owner", label="t"),
                   lambda db: engine.apply_action(db, case_id=case_id, action="create_system_entry", params={},
                                                  actor="owner", label="t"))

    assert _code(outcome) == "duplicate_effect", outcome
    assert db_session.execute(select(func.count()).select_from(LedgerEntry)
                              .where(LedgerEntry.source == "statement")).scalar_one() == 1
    db_session.expire_all()
    case = db_session.get(ReconciliationCase, case_id)
    assert case.status == "open" and "created_entry_id" in case.context


# --- schedule rows: NOWAIT and the repost race ------------------------------------------------------------------------

@pytest.fixture
def scheduled(seed, db_session, run, today):
    """A live statement line and a recurring expense posted by the scheduler for the same day and amount."""
    today(date(2026, 10, 22))
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "影音", 390)])
    (case,) = _cases(db_session, stmt.id, "open")
    definition = seed.definition([seed.line("expense", card, "390")], name="影音", anchor=DAY, auto_post_from=DAY)
    instance = seed.instance(definition, 1, DAY)
    (entry_id,) = posting.post_instance(db_session, instance.id, actor="auto").entry_ids
    db_session.commit()
    entry = db_session.get(LedgerEntry, entry_id)
    assert entry.amount == Decimal("-390") and entry.posted_date == DAY
    return SimpleNamespace(card=card, stmt=stmt, line=line, case=case, definition=definition, instance=instance,
                           entry=entry)


@pytest.mark.parametrize("what", ["definition", "instance"])
def test_a_busy_schedule_row_makes_the_action_yield(scheduled, db_session, pg_engine, what):
    s = scheduled
    holder = sessionmaker(bind=pg_engine, autoflush=False)()
    try:
        if what == "definition":
            schedule_locks.lock_definition(holder, s.definition.id)
        else:
            schedule_locks.lock_instance(holder, s.instance.id)
        exc = _refuse(db_session, lambda: _apply(db_session, s.case, params=_match(s)))
        assert isinstance(exc, CodedConflictError) and exc.code == "action_busy"
    finally:
        holder.rollback()
        holder.close()
    assert _active(db_session, s.stmt.id) == [] and _actions(db_session) == []
    _apply(db_session, s.case, params=_match(s))  # free again: the same request applies
    assert [c.entry_id for c in _active(db_session, s.stmt.id)] == [s.entry.id]


def test_schedule_nowait_keywords(scheduled, db_session, pg_engine):
    from psycopg2.errors import LockNotAvailable
    from sqlalchemy.exc import OperationalError

    s = scheduled
    holder = sessionmaker(bind=pg_engine, autoflush=False)()
    try:
        schedule_locks.lock_definition(holder, s.definition.id)
        schedule_locks.lock_instance(holder, s.instance.id)
        for call in (lambda: schedule_locks.lock_definition(db_session, s.definition.id, share=True, nowait=True),
                     lambda: schedule_locks.lock_instance(db_session, s.instance.id, nowait=True)):
            with pytest.raises(OperationalError) as info:
                call()
            assert isinstance(info.value.orig, LockNotAvailable)
            db_session.rollback()
    finally:
        holder.rollback()
        holder.close()


def test_a_repost_first_leaves_the_match_a_snapshot_conflict(scheduled, db_session, pg_engine):
    s = scheduled
    set_dirty(db_session, True)
    instance_id, case_id, params = s.instance.id, s.case.id, _match(s)

    outcome = race(pg_engine, lambda db: schedule_service.repost_instance(db, instance_id, ["400"]),
                   lambda db: engine.apply_action(db, case_id=case_id, action="match", params=params,
                                                  actor="owner", label="t"))

    assert _code(outcome) == "snapshot_conflict", outcome
    assert _active(db_session, s.stmt.id) == [] and _actions(db_session) == []


def test_a_match_first_makes_the_repost_wait_and_both_commit(scheduled, db_session, pg_engine):
    s = scheduled
    set_dirty(db_session, True)
    instance_id, case_id, params, entry_id = s.instance.id, s.case.id, _match(s), s.entry.id

    outcome = race(pg_engine,
                   lambda db: engine.apply_action(db, case_id=case_id, action="match", params=params, actor="owner",
                                                  label="t"),
                   lambda db: schedule_service.repost_instance(db, instance_id, ["400"]))

    assert outcome == "committed"
    db_session.expire_all()
    assert db_session.get(LedgerEntry, entry_id) is None
    # the next reconcile's sweep releases the owner's claim on the deleted entry and reopens the case
    _reconcile(db_session, s.stmt.id)
    assert [r for r in _active(db_session, s.stmt.id) if r.match_kind == "manual"] == []
    assert any(c.event_id == s.line.event_id for c in _cases(db_session, s.stmt.id, "open"))


# --- 7. case moved between prepare and the statement lock -------------------------------------------------------------

def test_case_moved_between_prepare_and_the_statement_lock(unmatched, db_session, pg_engine):
    s = unmatched()
    stmt_id, case_id, params = s.stmt.id, s.case.id, _match(s)
    factory = sessionmaker(bind=pg_engine, autoflush=False)
    holder, actor = factory(), factory()
    outcome, pids, started = [], [], threading.Event()

    def act():
        try:
            pids.append(actor.execute(text("SELECT pg_backend_pid()")).scalar_one())
            started.set()
            engine.apply_action(actor, case_id=case_id, action="match", params=params, actor="owner", label="t")
            actor.commit()
            outcome.append("committed")
        except Exception as exc:  # noqa: BLE001
            actor.rollback()
            outcome.append(exc)

    try:
        holder.execute(select(AccountStatement).where(AccountStatement.id == stmt_id).with_for_update())
        holder.execute(update(ReconciliationCase).where(ReconciliationCase.id == case_id)
                       .values(version=ReconciliationCase.version + 1))
        holder_pid = holder.execute(text("SELECT pg_backend_pid()")).scalar_one()
        thread = threading.Thread(target=act, daemon=True)
        thread.start()
        assert started.wait(5)
        wait_until_blocked(pg_engine, pids[0], blocker_pid=holder_pid)
        holder.commit()
        thread.join(10)
        assert not thread.is_alive()
    finally:
        holder.close()
        actor.close()
    assert _code(outcome[0]) == "case_moved", outcome
    assert _active(db_session, s.stmt.id) == [] and _actions(db_session) == []


# --- 8. unmatch and its revert ----------------------------------------------------------------------------------------

def test_unmatch_releases_flags_recheck_and_reverts(claimed, db_session):
    s = claimed
    version = s.case.version
    row, _ = _apply(db_session, s.case, "unmatch", {"line_id": s.line.id})

    old = db_session.get(StatementCoverage, s.row.id)
    assert (old.status, old.stale_reason) == ("stale", "owner")
    stmt = db_session.get(AccountStatement, s.stmt.id)
    assert (stmt.needs_recheck, stmt.matched_count) == (True, 0)
    assert row.touched_entry_ids == [s.entry.id] and row.after["coverage_ids"] == []
    assert row.inverse == {"line_id": s.line.id, "rows": [{"entry_id": s.entry.id, "role": "principal",
                                                            "group_id": None}],
                           "match_kind": "auto", "match_rule": s.row.match_rule}
    assert db_session.get(ReconciliationCase, s.case.id).status == "resolved"

    revert = _revert(db_session, row.id)
    (again,) = _active(db_session, s.stmt.id)
    assert (again.entry_id, again.line_id, again.match_kind, again.match_rule) == (
        s.entry.id, s.line.id, "auto", s.row.match_rule)
    assert revert.after["coverage_ids"] == [again.id]
    stmt = db_session.get(AccountStatement, s.stmt.id)
    assert (stmt.needs_recheck, stmt.matched_count) == (True, 1)  # the counts-only recount leaves the flag
    case = db_session.get(ReconciliationCase, s.case.id)
    assert (case.status, case.version) == ("open", version + 1)

    second, _ = _apply(db_session, case, "unmatch", {"line_id": s.line.id})
    db_session.get(LedgerEntry, s.entry.id).amount = Decimal("-581")
    db_session.commit()
    exc = _refuse(db_session, lambda: _revert(db_session, second.id))
    assert isinstance(exc, CodedConflictError) and exc.code == "snapshot_conflict"
    assert db_session.get(ReconciliationAction, second.id).status == "applied"

    _reconcile(db_session, s.stmt.id)  # only a completed reconcile discharges needs_recheck (ruling 29)
    assert db_session.get(AccountStatement, s.stmt.id).needs_recheck is False


def test_unmatch_needs_an_active_claim(claimed, db_session):
    s = claimed
    _apply(db_session, s.case, "unmatch", {"line_id": s.line.id})
    other = _case(db_session, s.stmt, s.line, "duplicate_claim")
    exc = _refuse(db_session, lambda: _apply(db_session, other, "unmatch", {"line_id": s.line.id}))
    assert isinstance(exc, ValidationError) and exc.code == "not_claimed"


# --- 9. revert of a match ---------------------------------------------------------------------------------------------

def test_revert_of_a_match_releases_and_reopens(unmatched, db_session):
    s = unmatched()
    version = s.case.version
    row, _ = _apply(db_session, s.case, params=_match(s))
    (claim,) = _active(db_session, s.stmt.id)

    revert = _revert(db_session, row.id)

    old = db_session.get(StatementCoverage, claim.id)
    assert (old.status, old.stale_reason) == ("stale", "revert")
    case = db_session.get(ReconciliationCase, s.case.id)
    assert (case.status, case.version, case.resolved_by, case.resolved_action_id, case.resolved_at) == (
        "open", version + 1, None, None, None)
    assert case.context["reverted_action_id"] == row.id
    original = db_session.get(ReconciliationAction, row.id)
    assert (original.status, original.reverted_by_action_id) == ("reverted", revert.id)
    assert (revert.action, revert.effect_slot, revert.status, revert.case_id, revert.event_id) == (
        "revert", f"r{row.id}", "applied", s.case.id, s.line.event_id)
    assert revert.params == {"action_id": row.id} and revert.touched_entry_ids == [s.entry.id]
    assert revert.before["entries"] == original.after["entries"]
    assert revert.after["entries"][str(s.entry.id)]["coverage_ids"] == []
    stmt = db_session.get(AccountStatement, s.stmt.id)
    assert (stmt.matched_count, stmt.open_case_count) == (0, 1)
    assert [a.event for a in _audit(db_session)] == ["action_applied", "action_reverted"]

    for action_id in (row.id, revert.id):
        exc = _refuse(db_session, lambda: _revert(db_session, action_id))
        assert isinstance(exc, ValidationError) and exc.code == "not_revertable" and exc.field == "action_id"


def test_revert_refuses_when_a_later_action_touched_the_same_rows(unmatched, db_session):
    s = unmatched()
    matched, _ = _apply(db_session, s.case, params=_match(s))
    recheck = _case(db_session, s.stmt, s.line, "recheck")
    unmatched_row, _ = _apply(db_session, recheck, "unmatch", {"line_id": s.line.id})

    exc = _refuse(db_session, lambda: _revert(db_session, matched.id))
    assert exc.code == "not_revertable" and f"later action {unmatched_row.id}" in exc.message
    assert db_session.get(ReconciliationAction, matched.id).status == "applied"


# --- 10. historical statements ---------------------------------------------------------------------------------------

def test_historical_statement_allows_only_coverage_actions(seed, db_session, run, fakes):
    card = _card(seed, db_session, live=False)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)])
    assert stmt.mode == "historical" and _cases(db_session, stmt.id) == []
    entry = seed.entry(card, "-580", day=DAY)
    db_session.commit()
    case = _case(db_session, stmt, line, "line_unmatched")  # cases never exist there; built by hand
    fakes(FakeDismiss())

    exc = _refuse(db_session, lambda: _apply(db_session, case, "dismiss"))
    assert isinstance(exc, ValidationError) and exc.code == "historical_statement"
    row, _ = _apply(db_session, case, params={"line_id": line.id, "entry_id": entry.id})
    assert [c.entry_id for c in _active(db_session, stmt.id)] == [entry.id]
    _revert(db_session, row.id)
    assert _active(db_session, stmt.id) == []


# --- 11. audit request ids --------------------------------------------------------------------------------------------

def test_audit_rows_carry_the_request_id(unmatched, db_session):
    s = unmatched()
    row, _ = _apply(db_session, s.case, params=_match(s), request_id="req-1")
    _apply(db_session, s.case, params=_match(s), request_id="req-2")
    revert = _revert(db_session, row.id, request_id="req-3")
    assert [(a.event, a.request_id, a.action_id) for a in _audit(db_session)] == [
        ("action_applied", "req-1", row.id), ("action_replayed", "req-2", row.id),
        ("action_reverted", "req-3", revert.id)]


# --- writer barrier (ruling 15/26) -----------------------------------------------------------------------------------

def _barrier_modes(engine_, pid) -> list[str]:
    with engine_.connect() as conn:
        return conn.execute(text(
            "SELECT mode FROM pg_locks WHERE locktype = 'advisory' AND objid = :k AND pid = :pid AND granted"),
            {"k": DIRTY_BARRIER_KEY, "pid": pid}).scalars().all()


def test_barrier_xact_is_held_until_commit_and_leaves_no_session_lock(db_session, pg_engine, monkeypatch):
    pid = db_session.execute(text("SELECT pg_backend_pid()")).scalar_one()
    timeout = db_session.execute(text("SELECT current_setting('lock_timeout')")).scalar_one()
    cov.take_barrier_xact(db_session)
    assert _barrier_modes(pg_engine, pid) == ["ExclusiveLock"]
    assert db_session.execute(text("SELECT current_setting('lock_timeout')")).scalar_one() == timeout  # restored
    with pg_engine.connect() as writer:  # a ledger writer's trigger cannot take the shared side meanwhile
        assert writer.execute(text("SELECT pg_try_advisory_xact_lock_shared(:k)"),
                              {"k": DIRTY_BARRIER_KEY}).scalar_one() is False
        writer.rollback()
    db_session.commit()
    assert _barrier_modes(pg_engine, pid) == []

    monkeypatch.setattr(cov, "BARRIER_LOCK_TIMEOUT", "100ms")
    with pg_engine.connect() as writer:  # a writer in flight holds the shared side: the action yields
        writer.execute(text("SELECT pg_advisory_xact_lock_shared(:k)"), {"k": DIRTY_BARRIER_KEY})
        with pytest.raises(CodedConflictError) as info:
            cov.take_barrier_xact(db_session)
        assert info.value.code == "sweep_barrier_busy"
        assert db_session.execute(text("SELECT 1")).scalar_one() == 1  # the session stays usable
        writer.rollback()
    db_session.rollback()
    assert _barrier_modes(pg_engine, pid) == []


def test_lockset_barrier_is_held_by_the_action(seed, db_session, run, fakes):
    bank = seed.account("銀行", statement_live_from=date(2026, 9, 1))
    db_session.commit()
    stmt, _, _ = _submit(db_session, run, bank, [_line(1, DAY, "存入", 100, "deposit")], kind="bank")
    (gap,) = [c for c in _cases(db_session, stmt.id, "open") if c.kind == "balance_gap"]
    FakeBarrier.seen = []
    fakes(FakeBarrier())
    pid = db_session.execute(text("SELECT pg_backend_pid()")).scalar_one()
    _apply(db_session, gap, "balance_adjustment_asof", commit=False)
    assert FakeBarrier.seen == [["ExclusiveLock"]]
    db_session.commit()
    assert _barrier_modes(db_session.get_bind(), pid) == []


# --- reconcile keeps owner coverage (ruling 16) -----------------------------------------------------------------------

def test_owner_match_survives_reconcile(unmatched, db_session):
    s = unmatched()  # the matcher would claim this entry on its own now
    _apply(db_session, s.case, params=_match(s))
    (before,) = _active(db_session, s.stmt.id)
    _reconcile(db_session, s.stmt.id)
    (after,) = _active(db_session, s.stmt.id)
    assert (after.id, after.match_kind, after.match_rule) == (before.id, "manual", "owner")
    assert db_session.get(ReconciliationCase, s.case.id).status == "resolved"
    assert _cases(db_session, s.stmt.id, "open") == []


def test_sibling_match_survives_reconcile(unmatched, seed, db_session):
    s = unmatched(amount="-1")  # the card's own entry does not fit the line
    sibling = seed.account("姊妹")
    theirs = seed.entry(sibling, "-580", day=DAY)
    db_session.commit()
    settings_service.update_reconciliation_settings(db_session, {"account_siblings": {str(s.card.id): [sibling.id]}})
    db_session.commit()
    _apply(db_session, s.case, params={"line_id": s.line.id, "entry_id": theirs.id, "allow_sibling": True})
    (before,) = _active(db_session, s.stmt.id)
    _reconcile(db_session, s.stmt.id)
    assert [r.id for r in _active(db_session, s.stmt.id)] == [before.id]


def test_a_dirty_edit_releases_a_manual_claim_through_the_sweep(unmatched, db_session):
    s = unmatched()
    _apply(db_session, s.case, params=_match(s))
    (claim,) = _active(db_session, s.stmt.id)
    set_dirty(db_session, True)
    db_session.get(LedgerEntry, s.entry.id).amount = Decimal("-600")
    db_session.commit()

    _reconcile(db_session, s.stmt.id)

    old = db_session.get(StatementCoverage, claim.id)
    assert old.status == "stale" and old.stale_reason.startswith("dirty:update:")
    assert _active(db_session, s.stmt.id) == []
    assert any(c.event_id == s.line.event_id for c in _cases(db_session, s.stmt.id, "open"))
