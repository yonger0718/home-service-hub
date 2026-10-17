"""Ledger-writing reconciliation actions (R1c Task 6; spec §4.9, C6, C8; rulings 7, 18, 20, 25, 31): `create_entry`,
`create_system_entry`, `create_refund`, `attach_fee` and `adjust_amount` through the action engine — their
refusals (nothing written), the rows they book and claim, the dirty events they tag, the C6 race and each revert.
Synthetic data only."""
import dataclasses
from datetime import date
from decimal import Decimal
from functools import partial
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.models import (
    AccountStatement, Category, CoverageDirty, LedgerEntry, ReconciliationAction, ReconciliationCase,
)
from app.services import coverage_service
from app.services import entry_write_service as ews
from app.services import policy_service
from app.services import reconciliation_actions as registry
from app.services import reconciliation_service as rec
from app.services.errors import CodedConflictError, ValidationError
from app.services.reconciliation_actions import engine
from app.services.reconciliation_actions.ledger_actions import AdjustAmountAction
from tests.integration.test_reconciliation_policy import _reload, _set_policy, _sweep
from tests.integration.statement_fixtures import (  # noqa: F401  (run is a fixture)
    _active, _card, _cases, _line, _reconcile, _submit, run,
)

DAY = date(2026, 9, 3)
TXN = date(2026, 9, 2)
PREFIX = "對帳建立 2026-09-30"


# --- helpers ------------------------------------------------------------------------------------------------------

def _apply(db, case, action, params, **kw):
    kw.setdefault("actor", "owner")
    kw.setdefault("label", "owner-test")
    row, _ = engine.apply_action(db, case_id=case if isinstance(case, int) else case.id, action=action,
                                 params=params, **kw)
    db.commit()
    return row


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


def _booked(db) -> list[LedgerEntry]:
    db.expire_all()
    return db.execute(select(LedgerEntry).where(LedgerEntry.source == "statement")
                      .order_by(LedgerEntry.id)).scalars().all()


def _hand_case(db, stmt, line, kind, **fields) -> ReconciliationCase:
    case = ReconciliationCase(statement_id=stmt.id, revision_id=stmt.current_revision_id, kind=kind,
                              event_id=line.event_id, line_id=line.id, **fields)
    db.add(case)
    db.commit()
    return case


def _delta_case(db, stmt, line, entry, *, fee_matches=True) -> ReconciliationCase:
    """An amount_delta case on `line` naming `entry` (what the matcher writes for a foreign or near pair)."""
    return _hand_case(db, stmt, line, "amount_delta", entry_id=entry.id,
                      candidates=[{"entry_id": entry.id, "reason": "foreign"}],
                      context={"residual": "0", "fee_matches": fee_matches, "entry_id": entry.id})


def _fee_children(db, entry_id) -> list[LedgerEntry]:
    db.expire_all()
    return db.execute(select(LedgerEntry).where(LedgerEntry.parent_entry_id == entry_id)).scalars().all()


@pytest.fixture
def lone_line(seed, db_session, run):
    """A live September card statement with one 580 purchase line (txn 09-02, posted 09-03) nothing covers."""
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "神秘商店", 580, txn_date=TXN)])
    (case,) = _cases(db_session, stmt.id, "open")
    assert case.kind == "line_unmatched"
    return SimpleNamespace(card=card, stmt=stmt, line=line, case=case)


# --- create_entry -------------------------------------------------------------------------------------------------

def test_create_entry_refusals_write_nothing(lone_line, seed, db_session):
    s = lone_line
    income_cat = seed.category("薪資", kind="income")
    expense_cat = seed.category("雜支")
    db_session.commit()

    exc = _refuse(db_session, lambda: _apply(db_session, s.case, "create_entry",
                                             {"kind": "expense", "category_id": income_cat.id}))
    assert isinstance(exc, ValidationError) and exc.field == "category_id"
    exc = _refuse(db_session, lambda: _apply(db_session, s.case, "create_entry",
                                             {"kind": "income", "category_id": income_cat.id}))
    assert _code(exc) == "kind_sign"  # the line is a debit: an income cannot book it
    exc = _refuse(db_session, lambda: _apply(db_session, s.case, "create_entry",
                                             {"kind": "expense", "category_id": expense_cat.id,
                                              "project_id": 999999}))
    assert isinstance(exc, ValidationError) and exc.field == "project_id"

    assert _booked(db_session) == [] and _actions(db_session) == [] and _active(db_session, s.stmt.id) == []
    assert _fresh(db_session, ReconciliationCase, s.case.id).status == "open"


def test_create_entry_books_claims_and_its_dirty_events_are_skipped(lone_line, seed, db_session, dirty_on):
    s = lone_line
    category = seed.category("雜支")
    project = seed.project("旅行")
    db_session.commit()

    row = _apply(db_session, s.case, "create_entry",
                 {"kind": "expense", "category_id": category.id, "name": "補記", "project_id": project.id})

    (entry,) = _booked(db_session)
    assert (entry.account_id, entry.currency, entry.kind, entry.amount, entry.entry_date, entry.posted_date) == (
        s.card.id, "TWD", "expense", Decimal("-580"), TXN, DAY)
    assert (entry.category_id, entry.project_id, entry.name, entry.needs_review) == (
        category.id, project.id, "補記", False)  # the owner chose the category
    assert entry.description.startswith(PREFIX)
    assert (row.effect_slot, row.created_entry_ids, row.inverse) == ("create", [entry.id],
                                                                     {"created_entry_id": entry.id})
    (claim,) = _active(db_session, s.stmt.id)
    assert (claim.line_id, claim.entry_id, claim.role, claim.match_kind, claim.match_rule) == (
        s.line.id, entry.id, "principal", "manual", "create_entry")
    assert row.after["coverage_ids"] == [claim.id]
    case = _fresh(db_session, ReconciliationCase, s.case.id)
    assert (case.status, case.resolved_action_id) == ("resolved", row.id)

    # the GUC tagged the action's own insert, so the next reconcile's sweep skips it: no recheck, the claim stays
    tags = db_session.execute(select(CoverageDirty.action_id).where(CoverageDirty.kind == "entry",
                                                                    CoverageDirty.row_id == entry.id)).scalars().all()
    assert tags and set(tags) == {row.id}
    _reconcile(db_session, s.stmt.id)
    assert [c.id for c in _active(db_session, s.stmt.id)] == [claim.id]
    assert [c.kind for c in _cases(db_session, s.stmt.id) if c.kind == "recheck"] == []
    assert _fresh(db_session, AccountStatement, s.stmt.id).needs_recheck is False
    assert _fresh(db_session, ReconciliationCase, s.case.id).status == "resolved"


def test_create_entry_revert_deletes_the_row_and_refuses_after_an_edit(lone_line, seed, db_session):
    s = lone_line
    category = seed.category("雜支")
    db_session.commit()
    params = {"kind": "expense", "category_id": category.id}
    row = _apply(db_session, s.case, "create_entry", params)
    (entry,) = _booked(db_session)
    version = _fresh(db_session, ReconciliationCase, s.case.id).version

    _revert(db_session, row.id)
    assert _booked(db_session) == [] and _active(db_session, s.stmt.id) == []
    case = _fresh(db_session, ReconciliationCase, s.case.id)
    assert (case.status, case.version) == ("open", version + 1)

    again = _apply(db_session, s.case, "create_entry", params)  # a new case version: a new key
    (entry,) = _booked(db_session)
    entry.name = "改名"
    db_session.commit()
    exc = _refuse(db_session, lambda: _revert(db_session, again.id))
    assert isinstance(exc, CodedConflictError) and exc.code == "snapshot_conflict"
    assert [e.id for e in _booked(db_session)] == [entry.id]
    assert _fresh(db_session, ReconciliationAction, again.id).status == "applied"


# --- create_system_entry --------------------------------------------------------------------------------------------

def test_create_system_entry_is_refused_on_an_ordinary_line(lone_line, db_session):
    s = lone_line
    assert "create_system_entry" not in registry.allowed(s.case)
    exc = _refuse(db_session, lambda: _apply(db_session, s.case, "create_system_entry", {"kind": "fee"}))
    assert isinstance(exc, ValidationError) and exc.field == "action"
    assert _booked(db_session) == [] and _actions(db_session) == []


def test_create_system_entry_books_a_bank_only_fee_for_policy_and_reverts(seed, db_session, run, dirty_on):
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "年費", 1200, "fee")])
    (case,) = _cases(db_session, stmt.id, "open")
    assert case.context.get("hint") == "bank_only"

    row = _apply(db_session, case, "create_system_entry", {"kind": "fee"}, actor="policy", label="policy",
                 budget=(date(2026, 10, 1), "TWD", Decimal("1200")))

    (entry,) = _booked(db_session)
    assert (entry.kind, entry.amount, entry.category_id, entry.needs_review, entry.entry_date, entry.posted_date) == (
        "fee", Decimal("-1200"), ews.system_category_id(db_session, "fee"), True, DAY, DAY)
    assert db_session.get(Category, entry.category_id).kind == "fee"
    assert entry.description.startswith(PREFIX)
    (claim,) = _active(db_session, stmt.id)
    assert (claim.entry_id, claim.match_kind, claim.match_rule) == (entry.id, "agent", "create_system_entry")
    assert row.effect_slot == "create"

    _revert(db_session, row.id)
    assert _booked(db_session) == [] and _active(db_session, stmt.id) == []
    assert _fresh(db_session, ReconciliationCase, case.id).status == "open"


def test_line_kind_and_refund_conditions(seed, db_session, run):
    """create_system_entry serves fee/interest/reward lines, which since R6a always carry the bank_only hint (pattern
    None when no pattern matched); create_refund serves refund lines of card and bank statements alike."""
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "點數折抵", -50, "reward")])
    (case,) = _cases(db_session, stmt.id, "open")
    assert case.context == {"hint": "bank_only", "pattern": None}
    assert "create_system_entry" in registry.allowed(case) and "create_refund" not in registry.allowed(case)

    bank = seed.account("銀行", statement_live_from=date(2026, 9, 1))
    db_session.commit()
    bstmt, (bline,), _ = _submit(db_session, run, bank, [_line(1, DAY, "退款", 300, "refund")], kind="bank",
                                 opening="0")
    (bcase,) = [c for c in _cases(db_session, bstmt.id, "open") if c.kind == "line_unmatched"]
    assert "create_refund" in registry.allowed(bcase)


# --- create_refund ------------------------------------------------------------------------------------------------

@pytest.fixture
def refund_line(seed, db_session, run):
    """A card statement whose 300 refund line (txn 09-02) nothing covers, and the 500 August purchase it refunds."""
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "家電行", -300, "refund", txn_date=TXN)])
    (case,) = _cases(db_session, stmt.id, "open")
    assert case.kind == "line_unmatched" and "create_refund" in registry.allowed(case)
    original = seed.entry(card, "-500", day=date(2026, 8, 20), merchant="家電行", name="吹風機")
    db_session.commit()
    return SimpleNamespace(card=card, stmt=stmt, line=line, case=case, original=original)


def test_create_refund_over_the_remaining_amount_is_refused(refund_line, seed, db_session):
    s = refund_line
    seed.entry(s.card, "250", kind="refund", day=date(2026, 8, 25), refunds_entry_id=s.original.id)
    db_session.commit()
    exc = _refuse(db_session, lambda: _apply(db_session, s.case, "create_refund",
                                             {"original_entry_id": s.original.id}))
    assert _code(exc) == "refund_exceeds"
    assert _booked(db_session) == [] and _actions(db_session) == [] and _active(db_session, s.stmt.id) == []


def test_create_refund_needs_a_participating_expense(refund_line, seed, db_session):
    s = refund_line
    other = seed.account("別張卡")
    elsewhere = seed.entry(other, "-500", day=date(2026, 8, 20))
    income = seed.entry(s.card, "500", kind="income", day=date(2026, 8, 20))
    db_session.commit()
    for entry_id, code in ((elsewhere.id, "wrong_account"), (income.id, "not_refundable"), (999999, "not_found")):
        exc = _refuse(db_session, lambda: _apply(db_session, s.case, "create_refund", {"original_entry_id": entry_id}))
        assert _code(exc) == code
    assert _booked(db_session) == [] and _actions(db_session) == []


def test_create_refund_books_claims_and_reverts(refund_line, db_session):
    s = refund_line
    row = _apply(db_session, s.case, "create_refund", {"original_entry_id": s.original.id})

    (refund,) = _booked(db_session)
    assert (refund.kind, refund.amount, refund.refunds_entry_id, refund.account_id, refund.name) == (
        "refund", Decimal("300"), s.original.id, s.card.id, "吹風機")
    assert (refund.entry_date, refund.posted_date, refund.needs_review) == (TXN, DAY, True)
    assert refund.description.startswith(PREFIX)
    (claim,) = _active(db_session, s.stmt.id)
    assert (claim.entry_id, claim.match_kind, claim.match_rule) == (refund.id, "manual", "create_refund")
    assert (row.effect_slot, row.created_entry_ids, row.inverse) == ("create", [refund.id],
                                                                     {"created_entry_id": refund.id})
    assert s.original.id in row.touched_entry_ids

    _revert(db_session, row.id)
    assert _booked(db_session) == [] and _active(db_session, s.stmt.id) == []
    assert _fresh(db_session, LedgerEntry, s.original.id).amount == Decimal("-500")
    assert _fresh(db_session, ReconciliationCase, s.case.id).status == "open"


# --- attach_fee ---------------------------------------------------------------------------------------------------

@pytest.fixture
def fx_pair(seed, db_session, run):
    """The matcher's own amount_delta case: a −1000 TWD purchase booked as USD −30 and a 1015 statement line
    (USD −30), on a card charging 1.5 % (proposed fee 15): fee_matches is true."""
    card = _card(seed, db_session, fx_fee_pct=Decimal("1.5"))
    entry = seed.entry(card, "-1000", day=date(2026, 9, 15), merchant="AMAZON", original_amount=Decimal("-30"),
                       original_currency="USD")
    db_session.commit()
    stmt, lines, _ = _submit(db_session, run, card, [
        _line(1, date(2026, 9, 15), "AMAZON", 1015, foreign_amount=Decimal("-30"), foreign_currency="USD"),
        _line(2, date(2026, 9, 20), "神秘商店", 77)])
    (case,) = [c for c in _cases(db_session, stmt.id, "open") if c.kind == "amount_delta"]
    assert case.context["fee_matches"] is True and case.entry_id == entry.id
    return SimpleNamespace(card=card, stmt=stmt, line=lines[0], other_line=lines[1], case=case, entry=entry)


def test_attach_fee_books_the_expected_fee_claims_both_and_reverts(fx_pair, db_session):
    s = fx_pair
    row = _apply(db_session, s.case, "attach_fee", {"entry_id": s.entry.id})

    (child,) = _fee_children(db_session, s.entry.id)
    assert (child.kind, child.amount, child.name, child.source, child.account_id) == (
        "fee", Decimal("-15"), ews.FX_FEE_CHILD_NAME, "statement", s.card.id)
    rows = _active(db_session, s.stmt.id)
    assert sorted((r.line_id, r.entry_id, r.role, r.match_kind, r.match_rule) for r in rows) == sorted([
        (s.line.id, s.entry.id, "principal", "manual", "attach_fee"),
        (s.line.id, child.id, "child", "manual", "attach_fee")])
    assert (row.effect_slot, row.inverse, row.created_entry_ids) == ("fee", {"child_id": child.id}, [child.id])
    assert _fresh(db_session, ReconciliationCase, s.case.id).status == "resolved"
    assert _fresh(db_session, LedgerEntry, s.entry.id).amount == Decimal("-1000")  # the principal is untouched

    _revert(db_session, row.id)
    assert _fee_children(db_session, s.entry.id) == [] and _active(db_session, s.stmt.id) == []
    assert _fresh(db_session, ReconciliationCase, s.case.id).status == "open"

    again = _apply(db_session, s.case, "attach_fee", {"entry_id": s.entry.id})
    (child,) = _fee_children(db_session, s.entry.id)
    child.name = "改名"
    db_session.commit()
    exc = _refuse(db_session, lambda: _revert(db_session, again.id))
    assert isinstance(exc, CodedConflictError) and exc.code == "snapshot_conflict"
    assert [c.id for c in _fee_children(db_session, s.entry.id)] == [child.id]


def test_attach_fee_policy_claims_as_agent(fx_pair, db_session):
    s = fx_pair
    _apply(db_session, s.case, "attach_fee", {"entry_id": s.entry.id}, actor="policy", label="policy",
           budget=(date(2026, 10, 1), "TWD", Decimal("15")))
    assert {r.match_kind for r in _active(db_session, s.stmt.id)} == {"agent"}


def test_policy_attach_fee_survives_a_reconcile_and_reverts(db_session, fx_pair):
    s = fx_pair
    row = _apply(db_session, s.case, "attach_fee", {"entry_id": s.entry.id}, actor="policy", label="policy",
                 budget=(date(2026, 10, 1), "TWD", Decimal("15")))
    before = sorted(c.id for c in _active(db_session, s.stmt.id))
    assert {c.match_kind for c in _active(db_session, s.stmt.id)} == {"agent"}

    _reconcile(db_session, s.stmt.id)

    assert sorted(c.id for c in _active(db_session, s.stmt.id)) == before
    _revert(db_session, row.id)
    assert _active(db_session, s.stmt.id) == []
    assert _fresh(db_session, ReconciliationAction, row.id).status == "reverted"


def test_a_second_fee_on_the_same_entry_is_fee_exists(fx_pair, db_session):
    s = fx_pair
    _apply(db_session, s.case, "attach_fee", {"entry_id": s.entry.id})
    other = _delta_case(db_session, s.stmt, s.other_line, s.entry)
    exc = _refuse(db_session, lambda: _apply(db_session, other, "attach_fee", {"entry_id": s.entry.id}))
    assert isinstance(exc, CodedConflictError) and exc.code == "fee_exists"
    assert len(_fee_children(db_session, s.entry.id)) == 1 and len(_actions(db_session)) == 1


def test_attach_fee_entry_must_be_the_cases(fx_pair, seed, db_session, run):
    """Another entry — here the one of an October statement's own amount_delta case — is out of scope."""
    s = fx_pair
    later = seed.entry(s.card, "-2000", day=date(2026, 10, 15), merchant="AMAZON", original_amount=Decimal("-60"),
                       original_currency="USD")
    db_session.commit()
    oct_stmt, _, _ = _submit(db_session, run, s.card, [
        _line(1, date(2026, 10, 15), "AMAZON", 2030, foreign_amount=Decimal("-60"), foreign_currency="USD")],
        period=(date(2026, 10, 1), date(2026, 10, 31)), opening=Decimal("1092"))
    (oct_case,) = [c for c in _cases(db_session, oct_stmt.id, "open") if c.kind == "amount_delta"]
    assert oct_case.entry_id == later.id

    exc = _refuse(db_session, lambda: _apply(db_session, s.case, "attach_fee", {"entry_id": later.id}))
    assert _code(exc) == "entry_out_of_scope"
    exc = _refuse(db_session, lambda: _apply(db_session, oct_case, "attach_fee", {"entry_id": s.entry.id}))
    assert _code(exc) == "entry_out_of_scope"
    assert _fee_children(db_session, s.entry.id) == [] and _fee_children(db_session, later.id) == []
    assert _actions(db_session) == []


@pytest.mark.parametrize("printed, kind, code", [
    ("1000", "purchase", "fee_out_of_bounds"),  # zero residual
    ("990", "purchase", "fee_sign"),  # a positive residual on a debit line
    ("-1015", "refund", "fee_sign"),  # a credit line
    ("1055", "purchase", "fee_out_of_bounds"),  # 5.5 %
    ("1001.5", "purchase", "fee_quantum"),  # TWD residual 1.5
    ("1020", "purchase", "fee_unexpected"),  # 20 against a proposed 15
])
def test_attach_fee_refusals_write_no_child(seed, db_session, run, printed, kind, code):
    card = _card(seed, db_session, fx_fee_pct=Decimal("1.5"))
    stmt, (line,), _ = _submit(db_session, run, card, [
        _line(1, date(2026, 9, 15), "AMAZON", Decimal(printed), kind, foreign_amount=Decimal("-30"),
              foreign_currency="USD")])
    entry = seed.entry(card, "-1000", day=date(2026, 9, 15), merchant="AMAZON", original_amount=Decimal("-30"),
                       original_currency="USD")
    db_session.commit()
    case = _delta_case(db_session, stmt, line, entry)

    exc = _refuse(db_session, lambda: _apply(db_session, case, "attach_fee", {"entry_id": entry.id}))
    assert isinstance(exc, ValidationError) and exc.code == code, exc
    assert _fee_children(db_session, entry.id) == [] and _actions(db_session) == []
    assert _active(db_session, stmt.id) == []


# --- adjust_amount ------------------------------------------------------------------------------------------------

@pytest.fixture
def near_pair(seed, db_session, run):
    """build(foreign=…, **entry_fields): a card line of 1015 (USD −31 unless `foreign` is None), a −1000 entry
    booked after the reconcile, and a hand-made amount_delta case naming it. `pair` replaces the line's printed
    foreign fields outright (e.g. only one half of the pair)."""
    def build(*, foreign: Decimal | None = Decimal("-31"), pair: dict | None = None, **entry_fields):
        card = _card(seed, db_session)
        extra = {} if foreign is None else {"foreign_amount": foreign, "foreign_currency": "USD"}
        extra = extra if pair is None else pair
        stmt, (line,), _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 15), "AMAZON", 1015, **extra)])
        entry_fields.setdefault("description", "原說明")
        entry = seed.entry(card, "-1000", day=date(2026, 9, 14), merchant="AMAZON", **entry_fields)
        db_session.commit()
        case = _delta_case(db_session, stmt, line, entry, fee_matches=False)
        return SimpleNamespace(card=card, stmt=stmt, line=line, entry=entry, case=case)
    return build


def _adjust(s) -> dict:
    return {"entry_id": s.entry.id, "line_id": s.line.id}


def test_adjust_amount_rewrites_the_pair_claims_and_reverts(near_pair, db_session):
    s = near_pair(original_amount=Decimal("-30"), original_currency="USD", fx_rate=Decimal("33.3333333333"),
                  fx_source="fx_api")
    row = _apply(db_session, s.case, "adjust_amount", _adjust(s))

    entry = _fresh(db_session, LedgerEntry, s.entry.id)
    assert (entry.amount, entry.original_amount, entry.original_currency) == (
        Decimal("-1015"), Decimal("-31"), "USD")
    assert entry.fx_rate == (Decimal("1015") / Decimal("31")).quantize(ews.RATE_QUANTUM)
    assert entry.fx_source == "manual"  # fx_source has no 'statement' value (no migration in R1c; see the report)
    assert entry.description == "原說明\n對帳調整 原金額 -1000"
    assert entry.entry_date == date(2026, 9, 14)  # the date is unchanged
    (claim,) = _active(db_session, s.stmt.id)
    assert (claim.entry_id, claim.role, claim.match_kind, claim.match_rule) == (
        s.entry.id, "principal", "manual", "adjust_amount")
    assert row.effect_slot == "amount" and row.touched_entry_ids == [s.entry.id]
    assert row.inverse == {"restore": {"amount": "-1000.0000", "original_amount": "-30.0000",
                                       "original_currency": "USD", "fx_rate": "33.3333333333",
                                       "fx_source": "fx_api", "description": "原說明"}}
    assert _fresh(db_session, ReconciliationCase, s.case.id).status == "resolved"

    _revert(db_session, row.id)
    entry = _fresh(db_session, LedgerEntry, s.entry.id)
    assert (entry.amount, entry.original_amount, entry.fx_rate, entry.fx_source, entry.description) == (
        Decimal("-1000"), Decimal("-30"), Decimal("33.3333333333"), "fx_api", "原說明")
    assert _active(db_session, s.stmt.id) == []
    assert _fresh(db_session, ReconciliationCase, s.case.id).status == "open"

    again = _apply(db_session, s.case, "adjust_amount", _adjust(s))
    _fresh(db_session, LedgerEntry, s.entry.id).name = "改名"
    db_session.commit()
    exc = _refuse(db_session, lambda: _revert(db_session, again.id))
    assert isinstance(exc, CodedConflictError) and exc.code == "snapshot_conflict"
    assert _fresh(db_session, LedgerEntry, s.entry.id).amount == Decimal("-1015")


def test_adjust_amount_of_an_fx_entry_needs_the_printed_pair(near_pair, db_session):
    s = near_pair(foreign=None, original_amount=Decimal("-30"), original_currency="USD")
    exc = _refuse(db_session, lambda: _apply(db_session, s.case, "adjust_amount", _adjust(s)))
    assert _code(exc) == "fx_pair_required"
    assert _fresh(db_session, LedgerEntry, s.entry.id).amount == Decimal("-1000") and _actions(db_session) == []


@pytest.mark.parametrize("pair", [{"foreign_amount": Decimal("-31")}, {"foreign_currency": "USD"}],
                         ids=["no_currency", "no_amount"])
def test_adjust_amount_refuses_half_a_printed_pair(near_pair, db_session, pair):
    """Final review M1: a line printing only one half of the foreign pair cannot rewrite the entry's fx fields."""
    s = near_pair(pair=pair)
    exc = _refuse(db_session, lambda: _apply(db_session, s.case, "adjust_amount", _adjust(s)))
    assert _code(exc) == "fx_pair_required"
    entry = _fresh(db_session, LedgerEntry, s.entry.id)
    assert (entry.amount, entry.original_currency) == (Decimal("-1000"), None) and _actions(db_session) == []


def test_adjust_amount_of_a_plain_entry_without_a_pair(near_pair, db_session):
    s = near_pair(foreign=None)
    _apply(db_session, s.case, "adjust_amount", _adjust(s))
    entry = _fresh(db_session, LedgerEntry, s.entry.id)
    assert (entry.amount, entry.original_amount, entry.original_currency, entry.fx_rate) == (
        Decimal("-1015"), None, None, None)


def test_adjust_amount_of_a_protected_entry_is_refused(near_pair, seed, db_session):
    s = near_pair()
    seed.entry(s.card, "100", kind="refund", day=date(2026, 9, 16), refunds_entry_id=s.entry.id)
    db_session.commit()
    exc = _refuse(db_session, lambda: _apply(db_session, s.case, "adjust_amount", _adjust(s)))
    assert isinstance(exc, CodedConflictError) and exc.code == "protected"
    assert _fresh(db_session, LedgerEntry, s.entry.id).amount == Decimal("-1000") and _actions(db_session) == []


def test_adjust_amount_needs_a_standalone_candidate(near_pair, seed, db_session):
    s = near_pair()
    stranger = seed.entry(s.card, "-1000", day=date(2026, 9, 14))
    db_session.commit()
    exc = _refuse(db_session, lambda: _apply(db_session, s.case, "adjust_amount",
                                             {"entry_id": stranger.id, "line_id": s.line.id}))
    assert _code(exc) == "entry_out_of_scope"
    group = seed.group("split")
    _fresh(db_session, LedgerEntry, s.entry.id).group_id = group.id
    db_session.commit()
    exc = _refuse(db_session, lambda: _apply(db_session, s.case, "adjust_amount", _adjust(s)))
    assert _code(exc) == "not_standalone"
    assert _actions(db_session) == []


def test_an_amount_edit_between_prepare_and_the_locks_is_a_snapshot_conflict(near_pair, db_session, pg_engine,
                                                                             monkeypatch):
    """Review Focus 2 (C6): a second session changes the amount after `prepare` read it."""
    s = near_pair()
    entry_id = s.entry.id
    real = AdjustAmountAction.prepare

    def prepare_then_edit(self, ctx, params):
        prepared = real(self, ctx, params)
        other = sessionmaker(bind=pg_engine, autoflush=False)()
        try:
            other.get(LedgerEntry, entry_id).amount = Decimal("-1001")
            other.commit()
        finally:
            other.close()
        return prepared

    monkeypatch.setattr(AdjustAmountAction, "prepare", prepare_then_edit)
    exc = _refuse(db_session, lambda: _apply(db_session, s.case, "adjust_amount", _adjust(s)))
    assert isinstance(exc, CodedConflictError) and exc.code == "snapshot_conflict"
    entry = _fresh(db_session, LedgerEntry, entry_id)
    assert (entry.amount, entry.description) == (Decimal("-1001"), "原說明")  # the other session's edit only
    assert _actions(db_session) == [] and _active(db_session, s.stmt.id) == []


def test_adjust_amount_tags_its_dirty_event(near_pair, db_session, dirty_on):
    s = near_pair()
    row = _apply(db_session, s.case, "adjust_amount", _adjust(s))
    tags = db_session.execute(select(CoverageDirty.action_id).where(
        CoverageDirty.kind == "entry", CoverageDirty.row_id == s.entry.id, CoverageDirty.op == "update")
    ).scalars().all()
    assert tags == [row.id]  # the fixture's own insert is untagged
    _reconcile(db_session, s.stmt.id)
    (claim,) = _active(db_session, s.stmt.id)
    assert claim.entry_id == s.entry.id
    assert db_session.execute(select(func.count()).select_from(ReconciliationCase).where(
        ReconciliationCase.statement_id == s.stmt.id, ReconciliationCase.kind == "recheck")).scalar_one() == 0


def test_backfill_category_is_created_once_per_kind(db_session):
    a = ews.backfill_category_id(db_session, "expense")
    b = ews.backfill_category_id(db_session, "expense")
    c = ews.backfill_category_id(db_session, "income")
    assert a == b and a != c
    rows = db_session.execute(select(Category).where(Category.name == "對帳待分類").order_by(Category.id)).scalars().all()
    assert [(r.kind, r.parent_id) for r in rows] == [("expense", None), ("income", None)]
    with pytest.raises(KeyError):
        ews.backfill_category_id(db_session, "fee")


def test_audit_accepts_the_r6b_events(db_session):
    from app.services.reconciliation_actions import audit
    assert {"stand_in_retired", "anchor_applied", "stand_in_replaced"} <= audit.EVENTS


def test_create_entry_is_not_offered_on_a_needs_peer_case(seed, db_session, run):
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "繳款", -20000, "payment")])
    (case,) = _cases(db_session, stmt.id, "open")
    assert case.context == {"hint": "needs_peer"}
    assert "create_entry" not in registry.allowed(case) and {"match", "dismiss"} <= set(registry.allowed(case))


# --- R6b.1 backfill_entry ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("stmt_kind, line_kind, printed, expected", [
    ("card", "purchase", 580, "expense"), ("bank", "withdrawal", 580, "expense"), ("bank", "purchase", 580, "expense"),
    ("bank", "deposit", 3000, "income"), ("card", "fee", 50, None), ("bank", "unknown", 10, None),
    ("bank", "transfer_out", 500, None), ("card", "payment", 20000, None), ("card", "refund", 100, None)])
def test_backfill_kind_map(stmt_kind, line_kind, printed, expected):
    from app.services.reconciliation_actions.ledger_actions import backfill_kind
    assert backfill_kind(stmt_kind, line_kind) == expected


def test_owner_backfill_books_a_stand_in_and_reverts(seed, db_session, run):
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)])
    (case,) = _cases(db_session, stmt.id, "open")
    assert "backfill_entry" in registry.allowed(case)
    row = _apply(db_session, case, "backfill_entry", {"kind": "expense"})
    (entry,) = _booked(db_session)
    assert entry.kind == "expense" and entry.amount == Decimal("-580") and entry.needs_review is True
    assert entry.category_id == ews.backfill_category_id(db_session, "expense") and entry.source == "statement"
    (cov,) = _active(db_session, stmt.id)
    assert cov.entry_id == entry.id and cov.match_rule == "backfill_entry" and cov.match_kind == "manual"
    assert _reload(db_session, ReconciliationCase, case.id).status == "resolved"
    _revert(db_session, row.id)
    assert _booked(db_session) == [] and _active(db_session, stmt.id) == []


def test_backfill_refuses_a_kind_the_line_cannot_take(seed, db_session, run):
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)])
    (case,) = _cases(db_session, stmt.id, "open")
    assert _code(_refuse(db_session, lambda: _apply(db_session, case, "backfill_entry", {"kind": "income"}))) == "kind_sign"


def test_backfill_not_offered_on_needs_peer_fee_or_candidate_cases(seed, db_session, run):
    card = _card(seed, db_session)
    entry = seed.entry(card, "-580", day=date(2026, 9, 10), merchant="全聯")
    stmt, lines, _ = _submit(db_session, run, card, [_line(1, DAY, "繳款", -20000, "payment"), _line(2, DAY, "年費", 1200, "fee"),
                                                     _line(3, DAY, "全聯", 580)])
    by_line = {c.line_id: c for c in _cases(db_session, stmt.id, "open")}
    assert "backfill_entry" not in registry.allowed(by_line[lines[0].id])  # needs_peer
    assert "backfill_entry" not in registry.allowed(by_line[lines[1].id])  # bank_only
    assert by_line[lines[2].id].kind == "amount_delta"  # date drift proposal: not a backfill case


def test_backfill_eligibility_matrix_through_allowed_and_the_policy(seed, db_session, run, dirty_on, today):
    # one row per cell of spec R6b.1's table, exercised through registry.allowed (owner column) and the policy
    # builder (policy column); the grace is 0 so only the hint/candidate/kind rules differ
    _set_policy(db_session, {"enabled": True, "backfill_grace_days": 0, "actions": {"backfill_entry": {"enabled": True, "max_amount": "5000"}}})
    card = _card(seed, db_session)
    stmt, lines, _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580), _line(2, DAY, "家樂福", 300), _line(3, DAY, "?", 10, "unknown")])
    by_line = {c.line_id: c for c in _cases(db_session, stmt.id, "open")}
    plain, other, unknown = by_line[lines[0].id], by_line[lines[1].id], by_line[lines[2].id]
    assert "backfill_entry" in registry.allowed(plain) and policy_service._backfill_candidate(db_session, plain, stmt) is not None
    assert "backfill_entry" not in registry.allowed(unknown) and policy_service._backfill_candidate(db_session, unknown, stmt) is None
    # owner-deleted event: owner may, policy may not (the sweep retires the creation action)
    row = _apply(db_session, plain, "backfill_entry", {"kind": "expense"})
    db_session.delete(db_session.get(LedgerEntry, row.created_entry_ids[0]))
    db_session.commit()
    _sweep(db_session, stmt)
    rec.reconcile(db_session, stmt.id)
    db_session.commit()
    (plain,) = [c for c in _cases(db_session, stmt.id, "open") if c.line_id == lines[0].id]
    assert plain.context["hint"] == "stand_in_deleted" and "backfill_entry" in registry.allowed(plain)
    assert policy_service._backfill_candidate(db_session, plain, stmt) is None
    # engine-revert reopened case: both may
    row2 = _apply(db_session, other, "backfill_entry", {"kind": "expense"})
    _revert(db_session, row2.id)
    other = _reload(db_session, ReconciliationCase, other.id)
    assert other.status == "open" and "reverted_action_id" in other.context
    assert "backfill_entry" in registry.allowed(other) and policy_service._backfill_candidate(db_session, other, stmt) is not None
    # candidates present (a drift case is amount_delta, not line_unmatched; a sibling-candidate case is line_unmatched with candidates)
    other.candidates = [{"entry_id": 1}]
    db_session.commit()
    assert "backfill_entry" not in registry.allowed(other)
    # changed amount under the lock → candidate_changed
    other.candidates = []
    db_session.commit()
    cand = policy_service._backfill_candidate(db_session, other, stmt)
    stale = dataclasses.replace(cand, amount=Decimal("1"))
    with pytest.raises(Exception) as exc:
        engine.apply_action(db_session, case_id=other.id, action="backfill_entry", params=stale.params, actor="policy", label="policy",
                            budget=(date(2026, 10, 1), "TWD", stale.amount), precheck=partial(policy_service._policy_precheck, stale))
    assert "candidate_changed" in str(exc.value)
    db_session.rollback()


def test_second_backfill_of_the_same_event_is_refused_while_the_first_is_applied(seed, db_session, run):
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)])
    (case,) = _cases(db_session, stmt.id, "open")
    _apply(db_session, case, "backfill_entry", {"kind": "expense"})
    coverage_service.release_statement(db_session, stmt, reason="test")  # what the sweep does before it reopens a case
    db_session.commit()
    reopened = _hand_case(db_session, stmt, line, "line_unmatched")  # a sweep-reopened case for the same event
    assert _code(_refuse(db_session, lambda: _apply(db_session, reopened, "backfill_entry", {"kind": "expense"}))) == "duplicate_effect"


def test_backfill_may_run_again_after_an_engine_revert(seed, db_session, run):
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)])
    (case,) = _cases(db_session, stmt.id, "open")
    row = _apply(db_session, case, "backfill_entry", {"kind": "expense"})
    _revert(db_session, row.id)
    case = _reload(db_session, ReconciliationCase, case.id)
    assert case.status == "open"
    second = _apply(db_session, case, "backfill_entry", {"kind": "expense"})
    assert len(_booked(db_session)) == 1
    assert _reload(db_session, ReconciliationAction, second.id).status == "applied"
    assert _reload(db_session, ReconciliationAction, row.id).status == "reverted"
