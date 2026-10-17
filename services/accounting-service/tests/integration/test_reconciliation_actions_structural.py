"""Structural reconciliation actions (R1c Task 7; spec §4.9, C4; rulings 12, 14, 15, 19, 26): `move_account`
re-homes an unmatched entry and re-reconciles both statements, `split_entry` converts a near entry into a split
and claims the line on the member that equals it, `balance_adjustment_asof` books the gap as of the period end
under the transaction-scoped writer barrier. Refusals write nothing; each revert is exact. Synthetic data only."""
import threading
import time
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.orm import sessionmaker

from app.models import (
    DIRTY_BARRIER_KEY, AccountStatement, CoverageDirty, EntryGroup, EntryRewardRule, LedgerEntry, ReconciliationAction,
    ReconciliationCase,
)
from app.services import coverage_service as cov
from app.services import entry_write_service as ews
from app.services import ledger_service, policy_service
from app.services import reconciliation_service as rec
from app.services.errors import CodedConflictError, ValidationError
from app.services import reconciliation_actions as registry
from app.services.reconciliation_actions import engine, snapshots
from app.services.reconciliation_actions import structural_actions as structural
from tests.helpers import set_dirty, wait_until_blocked
from tests.integration.test_reconciliation_policy import ANCHOR_ON, _budget, _set_policy
from tests.integration.statement_fixtures import (  # noqa: F401  (run is a fixture)
    _active, _card, _cases, _line, _open_beside_gap, _submit, run,
)

DAY = date(2026, 9, 5)
PERIOD_END = date(2026, 9, 30)


# --- helpers ------------------------------------------------------------------------------------------------------

def _apply(db, case, action, params=None, *, commit=True):
    row, replayed = engine.apply_action(db, case_id=case if isinstance(case, int) else case.id, action=action,
                                        params=params or {}, actor="owner", label="owner-test")
    if commit:
        db.commit()
    return row, replayed


def _revert(db, action_id):
    row = engine.revert_action(db, action_id=action_id, actor="owner", label="owner-test")
    db.commit()
    return row


def _refuse(db, fn):
    """The call raises a 422/409 service error; the session is rolled back as the router would."""
    with pytest.raises((ValidationError, CodedConflictError)) as info:
        fn()
    db.rollback()
    return info.value


def _code(exc) -> str | None:
    return getattr(exc, "code", None)


def _fresh(db, model, pk):
    db.expire_all()
    return db.get(model, pk)


def _actions(db) -> list[ReconciliationAction]:
    db.expire_all()
    return db.execute(select(ReconciliationAction).order_by(ReconciliationAction.id)).scalars().all()


def _hand_case(db, stmt, kind, *, line=None, **fields) -> ReconciliationCase:
    case = ReconciliationCase(statement_id=stmt.id, revision_id=stmt.current_revision_id, kind=kind,
                              event_id=None if line is None else line.event_id,
                              line_id=None if line is None else line.id, **fields)
    db.add(case)
    db.commit()
    return case


def _barrier_holders(engine_) -> list[tuple[int, str]]:
    with engine_.connect() as conn:
        return [tuple(r) for r in conn.execute(text(
            "SELECT pid, mode FROM pg_locks WHERE locktype = 'advisory' AND objid = :k AND granted"),
            {"k": DIRTY_BARRIER_KEY}).all()]


def _pid(session) -> int:
    return session.execute(text("SELECT pg_backend_pid()")).scalar_one()


# --- move_account (ruling 12) -------------------------------------------------------------------------------------

@pytest.fixture
def move(seed, db_session, run):
    """Card 甲's September statement leaves a 580 entry unmatched; card 乙's statement prints that 580 line."""
    a, b = _card(seed, db_session, "卡甲"), _card(seed, db_session, "卡乙")
    entry = seed.entry(a, "-580", day=DAY, name="午餐")
    db_session.commit()
    stmt_b, (line_b,), _ = _submit(db_session, run, b, [_line(1, DAY, "餐廳", 580)])
    stmt_a, _, _ = _submit(db_session, run, a, [_line(1, DAY, "其他", 120)])
    (case,) = [c for c in _cases(db_session, stmt_a.id, "open") if c.kind == "entry_unmatched"]
    assert case.entry_id == entry.id
    return SimpleNamespace(a=a, b=b, entry=entry, stmt_a=stmt_a, stmt_b=stmt_b, line_b=line_b, case=case)


def test_move_account_refuses_another_currency(move, seed, db_session):
    usd = _card(seed, db_session, "美元卡", currency="USD")
    exc = _refuse(db_session, lambda: _apply(db_session, move.case, "move_account",
                                             {"entry_id": move.entry.id, "account_id": usd.id}))
    assert (_code(exc), exc.field) == ("currency", "account_id")
    exc = _refuse(db_session, lambda: _apply(db_session, move.case, "move_account",
                                             {"entry_id": move.entry.id, "account_id": move.a.id}))
    assert _code(exc) == "same_account"
    assert _actions(db_session) == []
    assert _fresh(db_session, LedgerEntry, move.entry.id).account_id == move.a.id


def test_move_account_refuses_a_grouped_entry(move, seed, db_session):
    group = seed.group()
    member = seed.entry(move.a, "-50", day=DAY, group_id=group.id)
    seed.entry(move.a, "-30", day=DAY, group_id=group.id)
    db_session.commit()
    case = _hand_case(db_session, move.stmt_a, "entry_unmatched", entry_id=member.id)
    exc = _refuse(db_session, lambda: _apply(db_session, case, "move_account",
                                             {"entry_id": member.id, "account_id": move.b.id}))
    assert (_code(exc), exc.field) == ("not_movable", "entry_id")
    assert _actions(db_session) == []
    assert _fresh(db_session, LedgerEntry, member.id).account_id == move.a.id


def test_move_account_refuses_an_entry_outside_the_case(move, seed, db_session):
    other = seed.entry(move.a, "-70", day=DAY)
    db_session.commit()
    exc = _refuse(db_session, lambda: _apply(db_session, move.case, "move_account",
                                             {"entry_id": other.id, "account_id": move.b.id}))
    assert _code(exc) == "entry_out_of_scope"
    assert _actions(db_session) == []


def test_move_account_re_reconciles_both_statements_and_reverts(dirty_on, move, db_session):
    s = move
    version = s.case.version
    assert _active(db_session, s.stmt_b.id) == []
    row, replayed = _apply(db_session, s.case, "move_account", {"entry_id": s.entry.id, "account_id": s.b.id})
    assert replayed is False
    assert (row.effect_slot, row.inverse["account_id"]) == ("move", s.a.id)
    assert _fresh(db_session, LedgerEntry, s.entry.id).account_id == s.b.id
    case = _fresh(db_session, ReconciliationCase, s.case.id)
    assert (case.status, case.resolved_action_id, case.version) == ("resolved", row.id, version)
    (claim,) = _active(db_session, s.stmt_b.id)  # the target statement's reconcile claimed the moved entry
    assert (claim.entry_id, claim.line_id) == (s.entry.id, s.line_b.id)
    assert row.after["coverage_ids"] == [claim.id]
    (moved,) = db_session.execute(select(CoverageDirty).where(
        CoverageDirty.kind == "entry", CoverageDirty.row_id == s.entry.id, CoverageDirty.op == "update")).scalars()
    assert (moved.old_account_id, moved.new_account_id, moved.action_id) == (s.a.id, s.b.id, row.id)
    # no twin, no new entry case; 甲's card gap is now its unbooked 120 line alone (and 乙 balances)
    assert [c.kind for c in _open_beside_gap(db_session, s.stmt_a.id, "-120.0000")] == ["line_unmatched"]
    assert [c for c in _cases(db_session, s.stmt_b.id, "open") if c.kind == "balance_gap"] == []

    revert = _revert(db_session, row.id)
    assert _fresh(db_session, LedgerEntry, s.entry.id).account_id == s.a.id
    assert _active(db_session, s.stmt_b.id) == []  # the target claim was released before the move back
    case = _fresh(db_session, ReconciliationCase, s.case.id)
    assert (case.status, case.version) == ("open", version + 1)
    open_entry_cases = [c for c in _cases(db_session, s.stmt_a.id, "open") if c.kind == "entry_unmatched"]
    assert [c.id for c in open_entry_cases] == [s.case.id]
    assert _fresh(db_session, ReconciliationAction, row.id).status == "reverted"
    assert revert.action == "revert"


def test_move_account_refuses_a_participating_account_of_the_case_statement(move, seed, db_session):
    """A move inside the statement's own population would resolve the case only until the next reconcile."""
    child = seed.account("附卡", combined_account_id=move.a.id)
    db_session.commit()
    exc = _refuse(db_session, lambda: _apply(db_session, move.case, "move_account",
                                             {"entry_id": move.entry.id, "account_id": child.id}))
    assert (_code(exc), exc.field) == ("not_movable", "account_id")
    assert _actions(db_session) == []
    assert _fresh(db_session, LedgerEntry, move.entry.id).account_id == move.a.id


def test_move_account_yields_when_a_writer_holds_the_barrier_at_its_reconcile(dirty_on, move, db_session,
                                                                              pg_engine, monkeypatch):
    """The in-action reconcile runs while the action holds entry and coverage row locks, so its sweep must never
    wait at the writer barrier: a writer holding the shared side makes it 409 `sweep_barrier_busy` at once (no
    5 s wait), nothing moved, no action row."""
    holder = pg_engine.connect()
    original = rec.reconcile
    calls = []

    def squeezed(db, statement_id, **kwargs):
        if not calls:
            holder.execute(text("SELECT pg_advisory_xact_lock_shared(:k)"), {"k": DIRTY_BARRIER_KEY})
        calls.append(kwargs)
        return original(db, statement_id, **kwargs)

    monkeypatch.setattr(rec, "reconcile", squeezed)
    started = time.monotonic()
    try:
        exc = _refuse(db_session, lambda: _apply(db_session, move.case, "move_account",
                                                 {"entry_id": move.entry.id, "account_id": move.b.id}))
    finally:
        holder.rollback()
        holder.close()
    assert _code(exc) == "sweep_barrier_busy"
    assert time.monotonic() - started < 3  # never waited out the 5 s barrier timeout
    assert calls and all(call.get("barrier_wait") is False for call in calls)
    assert _actions(db_session) == []
    assert _fresh(db_session, LedgerEntry, move.entry.id).account_id == move.a.id
    assert _active(db_session, move.stmt_b.id) == []


# --- split_entry (ruling 19) --------------------------------------------------------------------------------------

@pytest.fixture
def split(seed, db_session, run):
    """A 1000 department-store entry and a 580 line printed for part of it: an amount_delta case naming it."""
    card = _card(seed, db_session)
    food, gift = seed.category("餐飲"), seed.category("禮品")
    entry = seed.entry(card, "-1000", day=DAY, name="百貨", merchant="百貨公司", tags=["週末"], description="週年慶")
    db_session.commit()
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "百貨公司", 580)])

    def delta_case(target: LedgerEntry) -> ReconciliationCase:
        return _hand_case(db_session, stmt, "amount_delta", line=line, entry_id=target.id,
                          candidates=[{"entry_id": target.id, "reason": "near"}], context={"entry_id": target.id})

    return SimpleNamespace(card=card, food=food, gift=gift, entry=entry, stmt=stmt, line=line,
                           case=delta_case(entry), delta_case=delta_case)


def _parts(s, *amounts) -> list[dict]:
    cats = [s.food.id, s.gift.id]
    return [{"amount": str(a), "category_id": cats[i % 2], **({"name": "禮物"} if i == 1 else {})}
            for i, a in enumerate(amounts)]


def test_split_refuses_parts_that_do_not_sum(split, db_session):
    s = split
    exc = _refuse(db_session, lambda: _apply(db_session, s.case, "split_entry",
                                             {"entry_id": s.entry.id, "parts": _parts(s, 400, 500)}))
    assert (_code(exc), exc.field) == ("parts_sum", "parts")
    exc = _refuse(db_session, lambda: _apply(db_session, s.case, "split_entry",
                                             {"entry_id": s.entry.id, "parts": _parts(s, 1000)}))
    assert isinstance(exc, ValidationError) and exc.field == "params"  # fewer than two parts
    assert _actions(db_session) == []
    entry = _fresh(db_session, LedgerEntry, s.entry.id)
    assert (entry.group_id, entry.amount) == (None, Decimal("-1000"))
    assert db_session.execute(select(EntryGroup)).scalars().all() == []


def test_split_converts_and_claims_the_matching_member(split, db_session):
    s = split
    row, _ = _apply(db_session, s.case, "split_entry", {"entry_id": s.entry.id, "parts": _parts(s, 420, 580)})
    gid = row.inverse["group_id"]
    anchor_id, new_id = row.inverse["member_ids"]
    assert (row.effect_slot, row.inverse["anchor_id"], anchor_id) == ("split", s.entry.id, s.entry.id)
    assert row.created_entry_ids == [new_id] and row.touched_group_ids == [gid]
    anchor, member = _fresh(db_session, LedgerEntry, anchor_id), db_session.get(LedgerEntry, new_id)
    assert (anchor.amount, anchor.group_id, anchor.category_id, anchor.name, anchor.merchant) == (
        Decimal("-420"), gid, s.food.id, "百貨", "百貨公司")
    assert (member.amount, member.group_id, member.category_id, member.name, member.account_id) == (
        Decimal("-580"), gid, s.gift.id, "禮物", s.card.id)
    assert (member.entry_date, member.posted_date, member.tags, member.kind) == (DAY, DAY, ["週末"], "expense")
    assert (anchor.source, member.source) == ("manual", "statement")  # a new member is the statement's (M3)
    (claim,) = _active(db_session, s.stmt.id)
    assert (claim.line_id, claim.entry_id, claim.role, claim.match_kind, claim.match_rule) == (
        s.line.id, new_id, "principal", "manual", "split_entry")
    case = _fresh(db_session, ReconciliationCase, s.case.id)
    assert (case.status, case.resolved_action_id) == ("resolved", row.id)


def test_split_without_a_matching_member_keeps_the_case_open(split, db_session):
    s = split
    row, _ = _apply(db_session, s.case, "split_entry", {"entry_id": s.entry.id, "parts": _parts(s, 500, 500)})
    gid = row.inverse["group_id"]
    assert _active(db_session, s.stmt.id) == []
    assert row.after["coverage_ids"] == []
    case = _fresh(db_session, ReconciliationCase, s.case.id)
    assert case.status == "open" and case.context["split_group_id"] == gid
    assert _fresh(db_session, LedgerEntry, s.entry.id).group_id == gid


@pytest.mark.parametrize("shape", ["child", "rule_link", "reward_rule", "foreign"])
def test_split_refuses_an_anchor_it_cannot_invert(split, seed, db_session, shape):
    s = split
    extra = {"original_currency": "USD", "original_amount": Decimal("-33"), "fx_rate": Decimal("30.3030303030"),
             "fx_source": "manual"} if shape == "foreign" else {}
    anchor = seed.entry(s.card, "-1000", day=DAY, **extra)
    if shape == "child":
        seed.entry(s.card, "-15", kind="fee", day=DAY, parent_entry_id=anchor.id)
    elif shape == "rule_link":
        db_session.add(EntryRewardRule(entry_id=anchor.id, rule_id=seed.rule(s.card).id))
    elif shape == "reward_rule":
        anchor.reward_rule_id = seed.rule(s.card).id
    db_session.commit()
    case = s.delta_case(anchor)
    exc = _refuse(db_session, lambda: _apply(db_session, case, "split_entry",
                                             {"entry_id": anchor.id, "parts": _parts(s, 420, 580)}))
    assert (_code(exc), exc.field) == ("not_splittable", "entry_id")
    assert _actions(db_session) == []
    assert _fresh(db_session, LedgerEntry, anchor.id).group_id is None


def test_split_revert_refuses_an_edited_member_and_otherwise_restores_the_anchor(split, db_session):
    s = split
    version = s.case.version
    db_session.expire_all()
    before = snapshots.entry_snapshot(db_session, db_session.get(LedgerEntry, s.entry.id))
    row, _ = _apply(db_session, s.case, "split_entry", {"entry_id": s.entry.id, "parts": _parts(s, 420, 580)})
    gid, member_ids = row.inverse["group_id"], row.inverse["member_ids"]
    new_id = member_ids[1]
    stamp = db_session.get(LedgerEntry, new_id).updated_at

    db_session.execute(update(LedgerEntry).where(LedgerEntry.id == new_id).values(name="改過"))
    db_session.commit()
    exc = _refuse(db_session, lambda: _revert(db_session, row.id))
    assert _code(exc) == "snapshot_conflict"
    assert _fresh(db_session, ReconciliationAction, row.id).status == "applied"
    assert _fresh(db_session, LedgerEntry, new_id) is not None

    db_session.execute(update(LedgerEntry).where(LedgerEntry.id == new_id).values(name="禮物", updated_at=stamp))
    db_session.commit()
    _revert(db_session, row.id)
    db_session.expire_all()
    assert snapshots.entry_snapshot(db_session, db_session.get(LedgerEntry, s.entry.id)) == before
    assert db_session.get(LedgerEntry, new_id) is None
    assert db_session.get(EntryGroup, gid) is None
    assert _active(db_session, s.stmt.id) == []
    case = _fresh(db_session, ReconciliationCase, s.case.id)
    assert (case.status, case.version) == ("open", version + 1)


# --- balance_adjustment_asof (C4; rulings 15, 26) -----------------------------------------------------------------

@pytest.fixture
def gap(seed, db_session, run):
    """A live bank statement printing a 100 deposit the ledger lacks: a balance_gap of 100 at 09-30."""
    bank = seed.account("銀行", statement_live_from=date(2026, 9, 1))
    other = seed.account("錢包")
    db_session.commit()
    stmt, _, _ = _submit(db_session, run, bank, [_line(1, DAY, "存入", 100, "deposit")], kind="bank")
    (case,) = [c for c in _cases(db_session, stmt.id, "open") if c.kind == "balance_gap"]
    return SimpleNamespace(bank=bank, other=other, stmt=stmt, case=case)


def _adjustments(db) -> list[LedgerEntry]:
    db.expire_all()
    return db.execute(select(LedgerEntry).where(LedgerEntry.kind == "balance_adjustment")
                      .order_by(LedgerEntry.id)).scalars().all()


def _income(account, amount, day=date(2026, 9, 10)) -> LedgerEntry:
    return LedgerEntry(account_id=account.id, kind="income", amount=Decimal(amount), currency=account.currency,
                       entry_date=day, posted_date=day, source="manual")


def test_balance_adjustment_refuses_when_dirty_tracking_is_off(gap, db_session, monkeypatch):
    taken = []
    original = engine.locks.lock_account_and_barrier
    monkeypatch.setattr(engine.locks, "lock_account_and_barrier",
                        lambda *args: taken.append(args) or original(*args))
    exc = _refuse(db_session, lambda: _apply(db_session, gap.case, "balance_adjustment_asof"))
    assert (_code(exc), exc.field) == ("dirty_disabled", "action")
    assert taken == []  # refused before the account row and the barrier were taken
    assert _actions(db_session) == [] and _adjustments(db_session) == []


def test_balance_adjustment_refuses_a_zero_gap(dirty_on, gap, db_session):
    db_session.add(_income(gap.bank, "100"))
    db_session.commit()
    exc = _refuse(db_session, lambda: _apply(db_session, gap.case, "balance_adjustment_asof"))
    assert _code(exc) == "zero_delta"
    assert _actions(db_session) == [] and _adjustments(db_session) == []


def test_balance_adjustment_books_the_gap_at_period_end_and_reverts(dirty_on, gap, db_session):
    row, _ = _apply(db_session, gap.case, "balance_adjustment_asof")
    (adj,) = _adjustments(db_session)
    assert row.effect_slot == "balance" and row.inverse == {"created_entry_id": adj.id}
    assert row.created_entry_ids == [adj.id]
    assert (adj.account_id, adj.amount, adj.entry_date, adj.posted_date, adj.source, adj.needs_review) == (
        gap.bank.id, Decimal("100"), PERIOD_END, PERIOD_END, "statement", True)
    assert adj.description == "對帳調整 2026-09-30"
    assert adj.category_id == ews.system_category_id(db_session, "balance_adjustment")
    assert ledger_service.account_balance(db_session, gap.bank.id, as_of=PERIOD_END) == Decimal("100")
    case = _fresh(db_session, ReconciliationCase, gap.case.id)
    assert (case.status, case.resolved_action_id) == ("resolved", row.id)
    assert _barrier_holders(db_session.get_bind()) == []

    _revert(db_session, row.id)
    assert _adjustments(db_session) == []
    assert _fresh(db_session, ReconciliationCase, gap.case.id).status == "open"


def test_balance_adjustment_double_submit_replays(dirty_on, gap, db_session):
    first, replayed = _apply(db_session, gap.case, "balance_adjustment_asof")
    assert replayed is False
    second, replayed = _apply(db_session, gap.case, "balance_adjustment_asof")
    assert (second.id, replayed) == (first.id, True)
    assert len(_adjustments(db_session)) == 1


def _card_gap(seed, db_session, run, *, co_branded=False):
    """A live card statement printing a 200 purchase the ledger lacks: a card balance_gap of −200 at 09-30."""
    card = _card(seed, db_session)
    if co_branded:
        seed.account("聯名卡", is_credit=True, combined_account_id=card.id)
    seed.entry(card, "-580", day=DAY, merchant="全聯")
    db_session.commit()
    stmt, _, _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580), _line(2, DAY, "神秘", 200)])
    (case,) = [c for c in _cases(db_session, stmt.id, "open") if c.kind == "balance_gap"]
    assert case.context == {"gap": "-200.0000", "card": True}
    return SimpleNamespace(card=card, stmt=stmt, case=case)


def test_balance_adjustment_books_a_card_gap_with_the_card_sign(dirty_on, seed, db_session, run):
    g = _card_gap(seed, db_session, run)
    row, _ = _apply(db_session, g.case, "balance_adjustment_asof")
    (adj,) = _adjustments(db_session)
    assert (adj.account_id, adj.amount, adj.posted_date) == (g.card.id, Decimal("-200"), PERIOD_END)
    assert ledger_service.account_balance(db_session, g.card.id, as_of=PERIOD_END) == Decimal("-780")
    rec.reconcile(db_session, g.stmt.id)
    db_session.commit()
    assert [c for c in _cases(db_session, g.stmt.id, "open") if c.kind == "balance_gap"] == []
    _revert(db_session, row.id)
    assert _adjustments(db_session) == []


def test_balance_adjustment_refuses_a_combined_card_gap(dirty_on, seed, db_session, run, monkeypatch):
    g = _card_gap(seed, db_session, run, co_branded=True)
    assert "balance_adjustment_asof" not in registry.allowed(g.case)
    exc = _refuse(db_session, lambda: _apply(db_session, g.case, "balance_adjustment_asof"))
    assert isinstance(exc, ValidationError) and str(exc) == "action: not allowed for this case"
    monkeypatch.delitem(registry.CONDITIONS, ("balance_gap", "balance_adjustment_asof"))
    assert _code(_refuse(db_session, lambda: _apply(db_session, g.case, "balance_adjustment_asof"))) == "combined_card"
    assert _actions(db_session) == [] and _adjustments(db_session) == []


def test_balance_adjustment_end_to_end_c4_counts_an_inflight_writer(dirty_on, gap, db_session, pg_engine):
    """C4 end to end (AGENT-101 M3, out-of-order commit): writer A inserted an entry on the account and holds the
    shared barrier, uncommitted; the action waits for A (at its sweep's barrier, and at the account row A's insert
    holds KEY SHARE on) and then books the gap including A's row. It passes without the transaction-scoped barrier
    too; `test_the_action_barrier_waits_for_a_writer_that_started_after_its_sweep` is the proof of that barrier."""
    factory = sessionmaker(bind=pg_engine, autoflush=False)
    writer, actor = factory(), factory()
    out: dict = {}
    try:
        writer.add(_income(gap.bank, "30"))
        writer.flush()  # the dirty trigger holds the shared barrier until this transaction ends
        writer_pid, actor_pid = _pid(writer), _pid(actor)

        def act():
            try:
                row, _ = engine.apply_action(actor, case_id=gap.case.id, action="balance_adjustment_asof", params={},
                                             actor="owner", label="owner-test")
                actor.commit()
                out["row"] = row.id
            except Exception as exc:  # noqa: BLE001  (asserted below)
                actor.rollback()
                out["error"] = exc

        thread = threading.Thread(target=act, daemon=True)
        thread.start()
        try:
            wait_until_blocked(pg_engine, actor_pid, blocker_pid=writer_pid)
            assert "row" not in out
        finally:
            writer.commit()
            thread.join(15)
        assert not thread.is_alive()
    finally:
        writer.close()
        actor.close()
    assert "row" in out, out
    (adj,) = _adjustments(db_session)
    assert adj.amount == Decimal("70")  # 100 printed − (A's committed 30)


def test_a_writer_after_the_barrier_waits_for_the_commit_not_the_return(dirty_on, gap, db_session, pg_engine):
    """The barrier is transaction-scoped: with the action applied but the route parked before `db.commit()`, a ledger
    writer (another account, so only the barrier is in its way) blocks on the action's backend; the commit lets it
    through and leaves no barrier holder behind."""
    action_pid = _pid(db_session)
    row, _ = _apply(db_session, gap.case, "balance_adjustment_asof", commit=False)
    holders = _barrier_holders(pg_engine)  # the exclusive barrier (and the shared side its own insert took)
    assert {pid for pid, _ in holders} == {action_pid} and (action_pid, "ExclusiveLock") in holders
    factory = sessionmaker(bind=pg_engine, autoflush=False)
    writer = factory()
    out: dict = {}
    try:
        writer_pid = _pid(writer)

        def write():
            try:
                writer.add(_income(gap.other, "5"))
                writer.flush()
                writer.commit()
                out["done"] = True
            except Exception as exc:  # noqa: BLE001
                writer.rollback()
                out["error"] = exc

        thread = threading.Thread(target=write, daemon=True)
        thread.start()
        try:
            wait_until_blocked(pg_engine, writer_pid, blocker_pid=action_pid)
            assert out == {}  # the service returned, the writer still waits
        finally:
            db_session.commit()
            thread.join(15)
        assert not thread.is_alive()
    finally:
        writer.close()
    assert out == {"done": True}
    assert _barrier_holders(pg_engine) == []
    assert _fresh(db_session, ReconciliationAction, row.id).status == "applied"


def test_a_failing_apply_after_the_barrier_rolls_back_and_leaves_no_holder(dirty_on, gap, db_session, pg_engine,
                                                                          monkeypatch):
    action_pid = _pid(db_session)
    seen = []

    def boom(*args, **kwargs):
        seen.append(_barrier_holders(pg_engine))
        raise RuntimeError("forced failure after the barrier")

    monkeypatch.setattr(structural.rec, "balance_gap", boom)  # the gap read under the barrier
    with pytest.raises(RuntimeError):
        _apply(db_session, gap.case, "balance_adjustment_asof")
    db_session.rollback()
    assert seen == [[(action_pid, "ExclusiveLock")]]
    assert _barrier_holders(pg_engine) == []
    assert _actions(db_session) == [] and _adjustments(db_session) == []


def test_a_barrier_wait_beyond_the_timeout_is_409_with_no_row(dirty_on, gap, db_session, pg_engine, monkeypatch):
    """A writer takes the shared barrier after the action's sweep and before its exclusive barrier and keeps it past
    the (shortened) lock timeout: 409 `sweep_barrier_busy`, nothing written, nothing held."""
    monkeypatch.setattr(cov, "BARRIER_LOCK_TIMEOUT", "200ms")
    original = cov.take_barrier_xact
    holder = pg_engine.connect()

    def squeezed(db):
        holder.execute(text("SELECT pg_advisory_xact_lock_shared(:k)"), {"k": DIRTY_BARRIER_KEY})
        return original(db)

    monkeypatch.setattr(cov, "take_barrier_xact", squeezed)
    try:
        exc = _refuse(db_session, lambda: _apply(db_session, gap.case, "balance_adjustment_asof"))
    finally:
        holder.rollback()
        holder.close()
    assert _code(exc) == "sweep_barrier_busy"
    assert _actions(db_session) == [] and _adjustments(db_session) == []
    assert _barrier_holders(pg_engine) == []
    assert _fresh(db_session, ReconciliationCase, gap.case.id).status == "open"


def test_the_action_barrier_waits_for_a_writer_that_started_after_its_sweep(dirty_on, gap, db_session, pg_engine,
                                                                           monkeypatch):
    """The proof of the transaction-scoped barrier (rulings 15/26): after the action's sweep and account lock,
    writer W edits an existing entry on the account (no account-row lock in its way) and holds the shared barrier
    uncommitted; the action's `take_barrier_xact` waits for W's commit and then reads W's amount. Without that
    barrier the action would book 90 from the stale 10 (verified by disabling it: this test fails)."""
    existing = _income(gap.bank, "10")
    db_session.add(existing)
    db_session.commit()
    factory = sessionmaker(bind=pg_engine, autoflush=False)
    writer, actor = factory(), factory()
    at_barrier, go = threading.Event(), threading.Event()
    original = cov.take_barrier_xact

    def paused(db):
        at_barrier.set()
        assert go.wait(10)
        return original(db)

    monkeypatch.setattr(cov, "take_barrier_xact", paused)
    out: dict = {}
    try:
        writer_pid, actor_pid = _pid(writer), _pid(actor)

        def act():
            try:
                row, _ = engine.apply_action(actor, case_id=gap.case.id, action="balance_adjustment_asof", params={},
                                             actor="owner", label="owner-test")
                actor.commit()
                out["row"] = row.id
            except Exception as exc:  # noqa: BLE001
                actor.rollback()
                out["error"] = exc

        thread = threading.Thread(target=act, daemon=True)
        thread.start()
        try:
            assert at_barrier.wait(10)
            writer.execute(update(LedgerEntry).where(LedgerEntry.id == existing.id).values(amount=Decimal("40")))
            go.set()
            wait_until_blocked(pg_engine, actor_pid, blocker_pid=writer_pid)
            assert out == {}
        finally:
            go.set()
            writer.commit()
            thread.join(15)
        assert not thread.is_alive()
    finally:
        writer.close()
        actor.close()
    assert "row" in out, out
    (adj,) = _adjustments(db_session)
    assert adj.amount == Decimal("60")  # 100 printed − W's committed 40, not the 10 it replaced


# --- anchor_opening (R6b.2) ---------------------------------------------------------------------------------------

@pytest.fixture
def anchored_bank(seed, db_session, run, dirty_on):
    bank = seed.account("銀行", opening="1000", statement_live_from=date(2026, 8, 1))
    db_session.commit()
    aug, _, _ = _submit(db_session, run, bank, [_line(1, date(2026, 8, 5), "存入", 100, "deposit")], kind="bank",
                        period=(date(2026, 8, 1), date(2026, 8, 31)), opening=Decimal("1500"))
    sep, _, _ = _submit(db_session, run, bank, [_line(1, DAY, "存入", 100, "deposit")], kind="bank", opening=Decimal("1600"))
    cases = {c.statement_id: c for c in db_session.execute(select(ReconciliationCase).where(
        ReconciliationCase.kind == "balance_gap", ReconciliationCase.status == "open")).scalars()}
    return SimpleNamespace(bank=bank, aug=aug, sep=sep, cases=cases)


def test_anchor_books_the_opening_delta_before_the_first_live_period_and_flags_every_statement(anchored_bank, db_session, monkeypatch):
    a = anchored_bank
    row, _ = _apply(db_session, a.cases[a.sep.id], "anchor_opening", {})  # any balance_gap case of the account
    (adj,) = _adjustments(db_session)
    assert adj.posted_date == date(2026, 7, 31) and adj.entry_date == date(2026, 7, 31)
    assert adj.amount == Decimal("500") and adj.description == "期初對齊 2026-07-31" and adj.needs_review is True
    assert ledger_service.account_balance(db_session, a.bank.id, as_of=date(2026, 7, 31)) == Decimal("1500")
    db_session.expire_all()
    assert all(db_session.get(AccountStatement, sid).needs_recheck for sid in (a.aug.id, a.sep.id))
    assert sorted(row.inverse["statement_ids"]) == sorted([a.aug.id, a.sep.id])
    # the action table hides the anchor on an anchored account (CONDITIONS); with the condition lifted the action's own guard refuses
    exc = _refuse(db_session, lambda: _apply(db_session, a.cases[a.aug.id], "anchor_opening", {}))
    assert isinstance(exc, ValidationError) and _code(exc) is None and str(exc) == "action: not allowed for this case"
    monkeypatch.delitem(registry.CONDITIONS, ("balance_gap", "anchor_opening"))
    assert _code(_refuse(db_session, lambda: _apply(db_session, a.cases[a.aug.id], "anchor_opening", {}))) == "already_anchored"


def test_anchor_refuses_zero_delta_dirty_off_and_closing_adjustments(seed, db_session, run, dirty_on, anchored_bank):
    a = anchored_bank
    db_session.add(_income(a.bank, "500", day=date(2026, 7, 1)))
    db_session.commit()
    assert _code(_refuse(db_session, lambda: _apply(db_session, a.cases[a.sep.id], "anchor_opening", {}))) == "zero_delta"
    db_session.add(LedgerEntry(account_id=a.bank.id, kind="balance_adjustment", amount=Decimal("1"), currency="TWD",
                               entry_date=date(2026, 8, 31), posted_date=date(2026, 8, 31), source="statement",
                               category_id=ews.system_category_id(db_session, "balance_adjustment")))
    db_session.commit()
    assert _code(_refuse(db_session, lambda: _apply(db_session, a.cases[a.sep.id], "anchor_opening", {}))) == "closing_adjustments_present"
    set_dirty(db_session, False)
    db_session.commit()
    assert _code(_refuse(db_session, lambda: _apply(db_session, a.cases[a.sep.id], "anchor_opening", {}))) == "dirty_disabled"


def test_anchor_revert_relocks_every_statement_and_flags_them_again(anchored_bank, db_session, run):
    a = anchored_bank
    row, _ = _apply(db_session, a.cases[a.sep.id], "anchor_opening", {})
    for sid in (a.aug.id, a.sep.id):
        rec.reconcile(db_session, sid)
    db_session.commit()
    _revert(db_session, row.id)
    assert _adjustments(db_session) == []
    db_session.expire_all()
    assert all(db_session.get(AccountStatement, sid).needs_recheck for sid in (a.aug.id, a.sep.id))
    _apply(db_session, [c for c in _cases(db_session, a.sep.id, "open") if c.kind == "balance_gap"][0], "anchor_opening", {})


def test_anchor_refuses_when_the_statement_set_changed_between_discovery_and_lock(anchored_bank, db_session, run, monkeypatch):
    a = anchored_bank
    from app.services.reconciliation_actions import structural_actions as sa
    real = sa.live_bank_statement_ids
    calls = []

    def grown(db, account_id):
        calls.append(1)
        ids = real(db, account_id)
        return ids if len(calls) > 1 else ids[:-1]  # discovery saw one statement fewer than the locked read
    monkeypatch.setattr(sa, "live_bank_statement_ids", grown)
    assert _code(_refuse(db_session, lambda: _apply(db_session, a.cases[a.sep.id], "anchor_opening", {}))) == "targets_changed"


def test_anchor_revert_refuses_with_dirty_off_or_a_changed_statement_set(anchored_bank, db_session, run, monkeypatch):
    a = anchored_bank
    row, _ = _apply(db_session, a.cases[a.sep.id], "anchor_opening", {})
    set_dirty(db_session, False)
    db_session.commit()
    assert _code(_refuse(db_session, lambda: _revert(db_session, row.id))) == "dirty_disabled"
    set_dirty(db_session, True)
    db_session.commit()
    from app.services.reconciliation_actions import structural_actions as sa
    real = sa.live_bank_statement_ids
    calls = []

    def grown(db, account_id):  # the locked re-read sees a statement id the discovery did not (a new live statement)
        calls.append(1)
        ids = real(db, account_id)
        return ids if len(calls) == 1 else ids + (ids[-1] + 1000,)
    monkeypatch.setattr(sa, "live_bank_statement_ids", grown)
    assert _code(_refuse(db_session, lambda: _revert(db_session, row.id))) == "targets_changed"
    monkeypatch.undo()
    _, _, _ = _submit(db_session, run, a.bank, [_line(1, date(2026, 10, 5), "存入", 100, "deposit")], kind="bank",
                         period=(date(2026, 10, 1), date(2026, 10, 31)), opening=Decimal("1700"))
    _revert(db_session, row.id)  # a statement added BEFORE the revert's discovery is in recorded ∪ current on both reads: not a change
    assert _adjustments(db_session) == []


def test_anchor_refuses_when_a_writer_changed_the_balance_between_precheck_and_barrier(seed, db_session, run, dirty_on, monkeypatch):
    # M3: the policy reserves and charges |delta| before the account/barrier locks; a ledger write committed in
    # between must not be booked under the charged amount. Simulated by a balance that differs on the second read.
    bank = seed.account("銀行", opening="1000", statement_live_from=date(2026, 9, 1))
    db_session.commit()
    stmt, _, _ = _submit(db_session, run, bank, [_line(1, DAY, "存入", 100, "deposit")], kind="bank", opening=Decimal("1600"))
    from app.services import ledger_service as ls
    real = ls.account_balance
    reads = []

    def drifting(db, account_id, as_of=None):
        reads.append(as_of)
        value = real(db, account_id, as_of=as_of)
        return value + Decimal("50") if len(reads) >= 3 else value  # candidate read, precheck read, then the post-barrier read
    monkeypatch.setattr(ls, "account_balance", drifting)
    _set_policy(db_session, ANCHOR_ON)
    result = policy_service.run_policy(db_session, stmt.id)
    assert result.applied == [] and result.skipped.get("amount_changed", 0) == 1
    assert _adjustments(db_session) == [] and (_budget(db_session) is None or _budget(db_session).used_count == 0)


def test_later_closing_adjustment_books_only_the_residual_after_the_anchor(anchored_bank, db_session, run):
    a = anchored_bank
    _apply(db_session, a.cases[a.sep.id], "anchor_opening", {})
    rec.reconcile(db_session, a.sep.id)
    db_session.commit()
    gaps = [c for c in _cases(db_session, a.sep.id, "open") if c.kind == "balance_gap"]
    assert gaps
    _apply(db_session, gaps[0], "balance_adjustment_asof", {})
    assert sum(x.amount for x in _adjustments(db_session)) == Decimal("500") + Decimal(gaps[0].context["gap"])


def test_anchor_revert_refuses_while_a_later_closing_adjustment_exists(anchored_bank, db_session, run):
    a = anchored_bank
    anchor, _ = _apply(db_session, a.cases[a.sep.id], "anchor_opening", {})
    rec.reconcile(db_session, a.sep.id)
    db_session.commit()
    (gap_case,) = [c for c in _cases(db_session, a.sep.id, "open") if c.kind == "balance_gap"]
    closing, _ = _apply(db_session, gap_case, "balance_adjustment_asof", {})
    assert _code(_refuse(db_session, lambda: _revert(db_session, anchor.id))) == "closing_adjustments_present"
    _revert(db_session, closing.id)
    _revert(db_session, anchor.id)
    assert _adjustments(db_session) == []
