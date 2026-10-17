"""Reconciliation orchestration (spec v3 §6.1 populations, §6.3 R9/R10, §6.4 bank gap, §7.4 lock order, §5.8 modes):
reconcile = sweep → (re)claim → cases → deferrals → balance gap → counts, run by the revision hook and the batch."""
import threading
import uuid
from dataclasses import replace as dc_replace
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

from app.models import (
    AccountStatement, CoverageDirty, IngestRun, LedgerEntry, ReconciliationCase, StatementCoverage,
)
from app.schemas.statements import RevisionIn
from app.schemas.writes import EntryUpdateIn
from app.services import coverage_service
from app.services import entry_write_service as ews
from app.services import reconciliation_service as rec
from app.services import reconciliation_actions as registry
from app.services import settings_service
from app.services import statement_revision_service as revs
from app.services.errors import CodedConflictError, ConflictError
from tests.helpers import race, set_dirty, wait_until_blocked
from tests.integration.statement_fixtures import (  # noqa: F401  (run is a fixture)
    OCT, SEP, _active, _batch, _card, _cases, _open_beside_gap, _code, _interleave, _line, _reconcile, _submit, _transfer, run,
)


# --- end to end ---------------------------------------------------------------------------------------------------

@pytest.fixture
def card_month(seed, db_session, run):
    """A live September card statement: purchase, payment from a bank, refund, split group, foreign purchase with a
    1.5 % fee expectation, a line without an entry, an entry without a line and a late entry (deferral)."""
    card = _card(seed, db_session, fx_fee_pct=Decimal("1.5"))
    bank = seed.account("銀行")
    e = {
        "buy": seed.entry(card, "-580", day=date(2026, 9, 3), merchant="全聯"),
        "refund": seed.entry(card, "120", kind="refund", day=date(2026, 9, 10), merchant="全聯"),
        "lone": seed.entry(card, "-250", day=date(2026, 9, 10), merchant="書店"),
        "foreign": seed.entry(card, "-1000", day=date(2026, 9, 15), merchant="AMAZON", original_amount=Decimal("-30"),
                              original_currency="USD"),
        "late": seed.entry(card, "-90", day=date(2026, 9, 29), merchant="咖啡"),
    }
    group = seed.group("split")
    e["m1"] = seed.entry(card, "-300", day=date(2026, 9, 12), merchant="餐廳", group_id=group.id)
    e["m2"] = seed.entry(card, "-200", day=date(2026, 9, 12), merchant="餐廳", group_id=group.id)
    e["out"], e["pay"] = _transfer(seed, bank, card, 5000, date(2026, 9, 20))
    db_session.commit()
    lines = [
        _line(1, date(2026, 9, 3), "全聯", 580),
        _line(2, date(2026, 9, 20), "繳款", -5000, "payment"),
        _line(3, date(2026, 9, 10), "全聯", -120, "refund"),
        _line(4, date(2026, 9, 12), "餐廳", 500),
        _line(5, date(2026, 9, 15), "AMAZON", 1015, foreign_amount=Decimal("-30"), foreign_currency="USD"),
        _line(6, date(2026, 9, 25), "神秘商店", 77),
    ]
    stmt, ls, res = _submit(db_session, run, card, lines)
    return stmt, ls, e, group, card, res


def test_card_statement_end_to_end(card_month, db_session):
    stmt, ls, e, group, card, res = card_month
    rows = _active(db_session, stmt.id)
    assert sorted((r.line_id, r.entry_id, r.role, r.group_id) for r in rows) == sorted([
        (ls[0].id, e["buy"].id, "principal", None),
        (ls[1].id, e["pay"].id, "principal", None),
        (ls[2].id, e["refund"].id, "principal", None),
        (ls[3].id, e["m1"].id, "member", group.id),
        (ls[3].id, e["m2"].id, "member", group.id),
    ])
    assert {r.match_kind for r in rows} == {"auto"}
    open_cases = _cases(db_session, stmt.id, "open")
    by_kind = sorted((c.kind, c.line_id, c.entry_id) for c in open_cases)
    assert by_kind == sorted([
        ("amount_delta", ls[4].id, e["foreign"].id),
        ("line_unmatched", ls[5].id, None),
        ("entry_unmatched", None, e["foreign"].id),
        ("entry_unmatched", None, e["lone"].id),
        ("balance_gap", None, None),
    ])
    # the card balance check: owed −2948, ledger 2700 less the deferred late −90 → 158 = −77 (line 6) + 250 (lone)
    # − 15 (fee); the late entry belongs to October and is no gap
    assert next(c for c in open_cases if c.kind == "balance_gap").context == {"gap": "158.0000", "card": True}
    delta = next(c for c in open_cases if c.kind == "amount_delta")
    assert delta.context["fee_matches"] is True and delta.context["fee_expected"] == "15"
    assert delta.event_id == ls[4].event_id and delta.revision_id == stmt.current_revision_id
    lone = next(c for c in open_cases if c.entry_id == e["lone"].id)
    assert lone.context == {} and lone.event_id is None
    deferred = _cases(db_session, stmt.id, "resolved")
    assert [(c.kind, c.entry_id, c.explanation, c.resolved_by, c.deferred_to_period_end, c.deferred_to_statement_id)
            for c in deferred] == [("entry_unmatched", e["late"].id, "deferred_next_period", None, date(2026, 10, 30), None)]
    assert (stmt.matched_count, stmt.explained_count, stmt.open_case_count, stmt.needs_recheck) == (4, 1, 5, False)
    assert sorted(res.case_ids) == sorted(c.id for c in open_cases)


def test_re_reconcile_is_idempotent(card_month, db_session):
    stmt, *_ = card_month
    before_rows = [(r.id, r.line_id, r.entry_id, r.role) for r in _active(db_session, stmt.id)]
    before_cases = [(c.id, c.kind, c.status) for c in _cases(db_session, stmt.id)]
    result = _reconcile(db_session, stmt.id)
    assert result.skipped is None and result.claims == 4 and result.cases_opened == []
    assert result.explained == 1 and result.unmatched_entries == 2
    assert [(r.id, r.line_id, r.entry_id, r.role) for r in _active(db_session, stmt.id)] == before_rows
    assert [(c.id, c.kind, c.status) for c in _cases(db_session, stmt.id)] == before_cases
    stmt = db_session.get(AccountStatement, stmt.id)
    assert (stmt.matched_count, stmt.explained_count, stmt.open_case_count) == (4, 1, 5)  # 4 + the card gap


def test_changed_outcome_supersedes_the_stale_case_and_keeps_dismissed(card_month, seed, db_session):
    stmt, ls, e, group, card, _ = card_month
    unmatched = next(c for c in _cases(db_session, stmt.id, "open") if c.kind == "line_unmatched")
    lone = next(c for c in _cases(db_session, stmt.id, "open") if c.entry_id == e["lone"].id)
    lone.status = "dismissed"
    seed.entry(card, "-77", day=date(2026, 9, 25), merchant="神秘商店")  # the line now has an entry
    db_session.commit()
    _reconcile(db_session, stmt.id)
    db_session.expire_all()
    assert db_session.get(ReconciliationCase, unmatched.id).status == "superseded"
    assert db_session.get(ReconciliationCase, lone.id).status == "dismissed"
    assert not [c for c in _cases(db_session, stmt.id, "open") if c.entry_id == e["lone"].id]  # not reopened
    assert ls[5].id in {r.line_id for r in _active(db_session, stmt.id)}
    # the card gap moved with the new entry (158 + 77: line 6 is booked now): the old gap case is superseded
    assert [c.context for c in _cases(db_session, stmt.id, "open") if c.kind == "balance_gap"] == [
        {"gap": "235.0000", "card": True}]
    stmt = db_session.get(AccountStatement, stmt.id)
    assert (stmt.matched_count, stmt.open_case_count) == (5, 3)
    assert stmt.explained_count == 2  # the deferral + the dismissed entry case


def test_historical_statement_gets_coverage_but_no_cases(seed, db_session, run):
    card = _card(seed, db_session, live=False)
    buy = seed.entry(card, "-580", day=date(2026, 9, 3), merchant="全聯")
    seed.entry(card, "-250", day=date(2026, 9, 10), merchant="書店")
    seed.entry(card, "-90", day=date(2026, 9, 29), merchant="咖啡")
    db_session.commit()
    stmt, ls, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 3), "全聯", 580),
                                                 _line(2, date(2026, 9, 5), "神秘", 77)])
    assert stmt.mode == "historical"
    assert [(r.line_id, r.entry_id) for r in _active(db_session, stmt.id)] == [(ls[0].id, buy.id)]
    assert _cases(db_session, stmt.id) == []
    assert (stmt.matched_count, stmt.explained_count, stmt.open_case_count) == (1, 0, 0)


def test_a_row_claimed_by_another_statement_is_not_stolen(seed, db_session, run):
    card = _card(seed, db_session)
    late = seed.entry(card, "-580", day=date(2026, 9, 29), merchant="全聯")
    db_session.commit()
    sep, sep_lines, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 29), "全聯", 580)])
    assert [r.entry_id for r in _active(db_session, sep.id)] == [late.id]
    octo, oct_lines, _ = _submit(db_session, run, card, [_line(1, date(2026, 10, 1), "全聯", 580)], period=OCT)
    assert _active(db_session, octo.id) == []
    assert [r.entry_id for r in _active(db_session, sep.id)] == [late.id]
    assert [(c.kind, c.line_id) for c in _cases(db_session, octo.id, "open")] == [("line_unmatched", oct_lines[0].id)]
    _reconcile(db_session, sep.id)
    _reconcile(db_session, octo.id)
    assert [r.entry_id for r in _active(db_session, sep.id)] == [late.id] and _active(db_session, octo.id) == []


def test_combined_child_card_spend_matches_on_the_master(seed, db_session, run):
    master = _card(seed, db_session, "主卡")
    child = seed.account("附卡", is_credit=True, combined_account_id=master.id)
    spend = seed.entry(child, "-300", day=date(2026, 9, 5), merchant="超商")
    db_session.commit()
    stmt, ls, _ = _submit(db_session, run, master, [_line(1, date(2026, 9, 5), "超商", 300)])
    assert [(r.line_id, r.entry_id) for r in _active(db_session, stmt.id)] == [(ls[0].id, spend.id)]
    assert _cases(db_session, stmt.id) == []


def test_child_rows_are_claimed_with_their_parent(seed, db_session, run):
    card = _card(seed, db_session)
    parent = seed.entry(card, "-100", day=date(2026, 9, 5), merchant="超商")
    fee = seed.entry(card, "-2", kind="fee", day=date(2026, 9, 5), parent_entry_id=parent.id)
    db_session.commit()
    stmt, ls, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 5), "超商", 102)])
    assert sorted((r.entry_id, r.role) for r in _active(db_session, stmt.id)) == [(parent.id, "principal"),
                                                                                   (fee.id, "child")]
    assert _cases(db_session, stmt.id) == []


def test_uncovered_child_is_an_entry_case(seed, db_session, run):
    card = _card(seed, db_session)
    parent = seed.entry(card, "-100", day=date(2026, 9, 5), merchant="超商")
    fee = seed.entry(card, "-2", kind="fee", day=date(2026, 9, 5), parent_entry_id=parent.id)
    db_session.commit()
    stmt, ls, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 5), "超商", 100)])
    assert [r.entry_id for r in _active(db_session, stmt.id)] == [parent.id]
    assert [(c.kind, c.entry_id, c.context) for c in _cases(db_session, stmt.id, "open")] == [
        ("entry_unmatched", fee.id, {"hint": "uncovered_child"}),
        ("balance_gap", None, {"gap": "2.0000", "card": True})]  # the ledger's uncovered fee


def test_installment_matches_through_the_confirmed_plan(seed, db_session, run):
    from app.models import InstallmentPlanMap
    card = _card(seed, db_session)
    definition = seed.definition([seed.line("expense", card, "1000")], kind="installment", name="手機", times=12)
    entry = seed.entry(card, "-1000", day=date(2026, 9, 5), merchant="手機")
    seed.instance(definition, 2, date(2026, 9, 5), status="posted", entries=[entry])
    db_session.add(InstallmentPlanMap(account_id=card.id, plan_key="手機|12|1000.0000", definition_id=definition.id,
                                      confirmed_by="owner"))
    db_session.commit()
    stmt, ls, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 6), "手機", 1000, "installment",
                                                        installment_seq=2, installment_total=12)])
    assert [(r.line_id, r.entry_id, r.match_rule) for r in _active(db_session, stmt.id)] == [
        (ls[0].id, entry.id, "installment")]


# --- re-parses (spec §5.7) ----------------------------------------------------------------------------------------

def test_identical_reparse_keeps_coverage_and_cases(seed, db_session, run):
    card = _card(seed, db_session)
    seed.entry(card, "-580", day=date(2026, 9, 3), merchant="全聯")
    db_session.commit()
    lines = [_line(1, date(2026, 9, 3), "全聯", 580), _line(2, date(2026, 9, 5), "神秘", 77)]
    stmt, first_lines, _ = _submit(db_session, run, card, lines)
    rows = [(r.id, r.entry_id) for r in _active(db_session, stmt.id)]
    (case,) = _open_beside_gap(db_session, stmt.id, "-77.0000")
    stmt, new_lines, res = _submit(db_session, run, card, lines)
    assert [(r.id, r.entry_id) for r in _active(db_session, stmt.id)] == rows
    assert _active(db_session, stmt.id)[0].line_id == new_lines[0].id
    (again,) = _open_beside_gap(db_session, stmt.id, "-77.0000")
    assert (again.id, again.line_id, again.event_id) == (case.id, new_lines[1].id, first_lines[1].event_id)
    assert again.revision_id == stmt.current_revision_id and res.case_ids == []


def test_changed_reparse_in_live_mode_waits_for_the_quarantine(seed, db_session, run):
    card = _card(seed, db_session)
    buy = seed.entry(card, "-580", day=date(2026, 9, 3), merchant="全聯")
    db_session.commit()
    stmt, _, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 3), "全聯", 580)])
    stmt, new_lines, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 3), "全聯福利中心", 580)])
    assert _active(db_session, stmt.id) == []  # released by the quarantine, not re-claimed
    (review,) = _cases(db_session, stmt.id, "open")
    assert review.kind == "parse_review"
    review.status = "dismissed"
    db_session.commit()
    _reconcile(db_session, stmt.id)
    assert [(r.line_id, r.entry_id) for r in _active(db_session, stmt.id)] == [(new_lines[0].id, buy.id)]


def test_changed_reparse_in_historical_mode_rematches(seed, db_session, run):
    card = _card(seed, db_session, live=False)
    buy = seed.entry(card, "-580", day=date(2026, 9, 3), merchant="全聯")
    db_session.commit()
    stmt, old_lines, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 3), "全聯", 580)])
    stmt, new_lines, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 3), "全聯福利中心", 580)])
    db_session.expire_all()
    rows = db_session.execute(select(StatementCoverage).order_by(StatementCoverage.id)).scalars().all()
    assert [(r.line_id, r.status, r.stale_reason) for r in rows] == [
        (old_lines[0].id, "stale", "lineage:changed"), (new_lines[0].id, "active", None)]
    assert rows[1].entry_id == buy.id and _cases(db_session, stmt.id) == []


# --- deferral, cross-account, bank -------------------------------------------------------------------------------

def test_deferral_links_once_the_next_statement_exists(seed, db_session, run):
    card = _card(seed, db_session, closing_day=25)
    late = seed.entry(card, "-90", day=date(2026, 9, 24), merchant="咖啡")
    db_session.commit()
    first, _, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 3), "神秘", 50)],
                          period=(date(2026, 8, 26), date(2026, 9, 25)))
    (case,) = _cases(db_session, first.id, "resolved")
    assert (case.entry_id, case.explanation, case.deferred_to_period_end, case.deferred_to_statement_id) == (
        late.id, "deferred_next_period", date(2026, 10, 25), None)
    assert first.explained_count == 1
    second, ls2, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 26), "咖啡", 90)],
                             period=(date(2026, 9, 26), date(2026, 10, 25)))
    assert [r.entry_id for r in _active(db_session, second.id)] == [late.id]
    db_session.expire_all()
    assert db_session.get(ReconciliationCase, case.id).deferred_to_statement_id == second.id
    # re-reconciling the first statement keeps the explanation and the link (the entry is covered next period)
    _reconcile(db_session, first.id)
    case = db_session.get(ReconciliationCase, case.id)
    assert (case.status, case.deferred_to_statement_id) == ("resolved", second.id)
    assert db_session.get(AccountStatement, first.id).explained_count == 1


def test_deferral_finds_an_existing_next_statement(seed, db_session, run):
    bank = seed.account("銀行", statement_live_from=date(2026, 9, 1))
    db_session.commit()
    octo, _, _ = _submit(db_session, run, bank, [], kind="bank", period=OCT, opening="0")
    late = seed.entry(bank, "-90", day=date(2026, 9, 30), merchant="咖啡")
    db_session.commit()
    sep, _, _ = _submit(db_session, run, bank, [], kind="bank", period=SEP, opening="0")
    (case,) = _cases(db_session, sep.id, "resolved")
    # the earlier statement never references (or locks) the later one: the later statement's reconcile links
    assert (case.entry_id, case.deferred_to_period_end, case.deferred_to_statement_id) == (late.id, OCT[1], None)
    _reconcile(db_session, octo.id)
    assert db_session.get(ReconciliationCase, case.id).deferred_to_statement_id == octo.id
    # re-reconciling the earlier statement keeps the link
    _reconcile(db_session, sep.id)
    assert db_session.get(ReconciliationCase, case.id).deferred_to_statement_id == octo.id


def test_fill_deferral_links_links_every_unlinked_deferral(seed, db_session, run):
    bank = seed.account("銀行", statement_live_from=date(2026, 9, 1))
    db_session.commit()
    octo, _, _ = _submit(db_session, run, bank, [], kind="bank", period=OCT, opening="0")
    seed.entry(bank, "-90", day=date(2026, 9, 30), merchant="咖啡")
    db_session.commit()
    sep, _, _ = _submit(db_session, run, bank, [], kind="bank", period=SEP, opening="0")
    (case,) = _cases(db_session, sep.id, "resolved")
    assert case.deferred_to_statement_id is None
    assert rec.fill_deferral_links(db_session) == 1
    db_session.commit()
    assert db_session.get(ReconciliationCase, case.id).deferred_to_statement_id == octo.id
    assert rec.fill_deferral_links(db_session) == 0


def test_rules_for_coerces_settings_types():
    rules = rec.rules_for({"accept": "0.9", "exact_window_days": "4", "bank_only_patterns": ["年費"], "x": 1},
                          date(2026, 9, 30))
    assert (rules.accept, rules.exact_window_days, rules.bank_only_patterns) == (Decimal("0.9"), 4, ("年費",))
    assert rules.period_end == date(2026, 9, 30) and rules.margin == Decimal("0.05")


def test_next_period_end_rules():
    assert rec.next_period_end(date(2026, 9, 30), 30) == date(2026, 10, 30)
    assert rec.next_period_end(date(2026, 1, 31), 31) == date(2026, 2, 28)  # clamped to the month
    assert rec.next_period_end(date(2026, 2, 28), 31) == date(2026, 3, 31)
    assert rec.next_period_end(date(2026, 9, 5), 10) == date(2026, 9, 10)
    assert rec.next_period_end(date(2026, 12, 25), 25) == date(2027, 1, 25)
    assert rec.next_period_end(date(2026, 9, 30), None) == date(2026, 10, 31)  # bank: next calendar month
    assert rec.next_period_end(date(2026, 1, 31), None) == date(2026, 2, 28)


def test_cross_account_hint_names_the_other_statement_line(seed, db_session, run):
    card = _card(seed, db_session, "甲卡")
    other = _card(seed, db_session, "乙卡")
    wrong = seed.entry(card, "-444", day=date(2026, 9, 8), merchant="誤記")
    db_session.commit()
    other_stmt, other_lines, _ = _submit(db_session, run, other, [_line(1, date(2026, 9, 10), "誤記", 444)])
    stmt, _, _ = _submit(db_session, run, card, [])
    (case,) = _open_beside_gap(db_session, stmt.id, "444.0000")
    assert (case.kind, case.entry_id, case.context) == ("entry_unmatched", wrong.id, {
        "hint": "move_account", "statement_id": other_stmt.id, "line_id": other_lines[0].id})


def test_bank_balance_gap(seed, db_session, run):
    bank = seed.account("銀行", opening="1000", statement_live_from=date(2026, 9, 1))
    salary = seed.entry(bank, "500", kind="income", day=date(2026, 9, 5), merchant="薪資")
    seed.entry(bank, "-70", day=date(2026, 10, 2), merchant="後來")  # after period_end: outside the balance
    db_session.commit()
    stmt, ls, _ = _submit(db_session, run, bank, [_line(1, date(2026, 9, 5), "薪資", 500, "deposit"),
                                                 _line(2, date(2026, 9, 6), "神秘", 100, "deposit")],
                          kind="bank", opening="1000")
    assert [(r.line_id, r.entry_id) for r in _active(db_session, stmt.id)] == [(ls[0].id, salary.id)]
    gaps = [c for c in _cases(db_session, stmt.id, "open") if c.kind == "balance_gap"]
    assert [(c.context, c.line_id, c.entry_id) for c in gaps] == [({"gap": "100.0000"}, None, None)]
    assert {c.kind for c in _cases(db_session, stmt.id, "open")} == {"balance_gap", "line_unmatched"}
    # idempotent: the same gap keeps its case; a balanced statement gets none
    _reconcile(db_session, stmt.id)
    assert [c.id for c in _cases(db_session, stmt.id, "open") if c.kind == "balance_gap"] == [gaps[0].id]


def test_bank_without_gap_has_no_gap_case(seed, db_session, run):
    bank = seed.account("銀行", opening="1000", statement_live_from=date(2026, 9, 1))
    seed.entry(bank, "500", kind="income", day=date(2026, 9, 5), merchant="薪資")
    db_session.commit()
    stmt, _, _ = _submit(db_session, run, bank, [_line(1, date(2026, 9, 5), "薪資", 500, "deposit")],
                         kind="bank", opening="1000")
    assert _cases(db_session, stmt.id) == []
    assert stmt.matched_count == 1


# --- card balance gap: −statement_total − Σ balance(participating accounts, period end) ---------------------------

def test_card_whose_ledger_matches_has_no_gap_case(seed, db_session, run):
    card = _card(seed, db_session, opening="-1000")  # the previous statement's 1000 still owed
    seed.entry(card, "-580", day=date(2026, 9, 3), merchant="全聯")
    seed.entry(card, "-70", day=date(2026, 10, 2), merchant="後來")  # after period_end: outside the balance
    db_session.commit()
    stmt, _, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 3), "全聯", 580)], opening="1000")
    assert stmt.statement_total == Decimal("1580")
    assert _cases(db_session, stmt.id) == [] and stmt.matched_count == 1


def test_card_missing_purchase_opens_a_signed_card_gap(seed, db_session, run):
    card = _card(seed, db_session)
    seed.entry(card, "-580", day=date(2026, 9, 3), merchant="全聯")
    db_session.commit()
    stmt, ls, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 3), "全聯", 580),
                                                 _line(2, date(2026, 9, 8), "神秘", 200)])
    # owed 780, ledger −580: the ledger lacks an outflow of 200 — negative like a bank missing a withdrawal
    gaps = [c for c in _cases(db_session, stmt.id, "open") if c.kind == "balance_gap"]
    assert [(c.context, c.line_id, c.entry_id) for c in gaps] == [({"gap": "-200.0000", "card": True}, None, None)]
    assert {c.kind for c in _cases(db_session, stmt.id, "open")} == {"balance_gap", "line_unmatched"}
    assert registry.allowed(gaps[0]) == ["balance_adjustment_asof", "dismiss"]  # anchor_opening stays bank-only
    _reconcile(db_session, stmt.id)  # idempotent: the same gap keeps its case
    assert [c.id for c in _cases(db_session, stmt.id, "open") if c.kind == "balance_gap"] == [gaps[0].id]
    seed.entry(card, "-200", day=date(2026, 9, 8), merchant="神秘")
    db_session.commit()
    _reconcile(db_session, stmt.id)
    assert [c.kind for c in _cases(db_session, stmt.id, "open")] == []


def test_bank_missing_withdrawal_gap_is_negative_like_the_card_gap(seed, db_session, run):
    bank = seed.account("銀行", opening="1000", statement_live_from=date(2026, 9, 1))
    db_session.commit()
    stmt, _, _ = _submit(db_session, run, bank, [_line(1, date(2026, 9, 8), "神秘", -200, "withdrawal")],
                         kind="bank", opening="1000")
    (gap,) = [c for c in _cases(db_session, stmt.id, "open") if c.kind == "balance_gap"]
    assert gap.context == {"gap": "-200.0000"}


def test_card_gap_counts_a_co_branded_account_billed_on_the_same_statement(seed, db_session, run):
    card = _card(seed, db_session)
    co = seed.account("聯名卡", is_credit=True, combined_account_id=card.id)
    seed.entry(card, "-580", day=date(2026, 9, 3), merchant="全聯")
    seed.entry(co, "-300", day=date(2026, 9, 9), merchant="加油")
    db_session.commit()
    stmt, _, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 3), "全聯", 580),
                                                 _line(2, date(2026, 9, 9), "加油", 300)])
    assert [c.kind for c in _cases(db_session, stmt.id, "open") if c.kind == "balance_gap"] == []
    seed.entry(co, "-50", day=date(2026, 9, 12), merchant="多記")  # the co-branded ledger now owes 50 too much
    db_session.commit()
    _reconcile(db_session, stmt.id)
    (gap,) = [c for c in _cases(db_session, stmt.id, "open") if c.kind == "balance_gap"]
    assert gap.context == {"gap": "50.0000", "card": True}
    # the residual may sit on either account: no adjustment booked on the statement's account alone
    assert registry.allowed(gap) == ["dismiss"]


@pytest.mark.parametrize("kind", ["card", "bank"])
def test_entries_deferred_to_the_next_statement_are_no_gap(seed, db_session, run, kind):
    """A late entry (09-29, within deferral_days of the close) the bank prints next month is in the ledger balance at
    period_end but belongs to the next statement: the matcher's deferral explains it, so no balance_gap."""
    account = _card(seed, db_session) if kind == "card" else seed.account("銀行", statement_live_from=date(2026, 9, 1))
    sign = -1 if kind == "card" else 1
    seed.entry(account, str(sign * 580), kind="expense" if kind == "card" else "income", day=date(2026, 9, 3),
               merchant="全聯")
    late = seed.entry(account, "-90", day=date(2026, 9, 29), merchant="咖啡")
    seed.entry(account, "-50", day=date(2026, 10, 1), merchant="超商")  # after period_end: outside the balance
    db_session.commit()
    line = _line(1, date(2026, 9, 3), "全聯", 580, "purchase" if kind == "card" else "deposit")
    stmt, _, _ = _submit(db_session, run, account, [line], kind=kind)
    deferred = [c for c in _cases(db_session, stmt.id, "resolved") if c.explanation == "deferred_next_period"]
    assert [c.entry_id for c in deferred] == [late.id]
    assert _cases(db_session, stmt.id, "open") == []


def test_owner_deferral_is_no_gap_and_a_missing_line_still_is(seed, db_session, run):
    card = _card(seed, db_session)
    seed.entry(card, "-580", day=date(2026, 9, 3), merchant="全聯")
    entry = seed.entry(card, "-77", day=date(2026, 9, 10), merchant="早餐")  # far from the close: no matcher deferral
    db_session.commit()
    stmt, _, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 3), "全聯", 580),
                                                 _line(2, date(2026, 9, 8), "神秘", 200)])
    assert sorted(c.context.get("gap") for c in _cases(db_session, stmt.id, "open") if c.kind == "balance_gap") == [
        "-123.0000"]  # 77 too much in the ledger, 200 missing
    (case,) = [c for c in _cases(db_session, stmt.id, "open") if c.entry_id == entry.id]
    case.status, case.explanation, case.resolved_by = "resolved", "deferred_next_period", "owner"  # an owner deferral
    db_session.commit()
    _reconcile(db_session, stmt.id)
    assert [c.context for c in _cases(db_session, stmt.id, "open") if c.kind == "balance_gap"] == [
        {"gap": "-200.0000", "card": True}]  # only the missing purchase


# --- held balance: what other statements claim (review N1) ----------------------------------------------------------

def _gap_of(db, statement_id):
    return [c.context["gap"] for c in _cases(db, statement_id, "open") if c.kind == "balance_gap"]


def test_an_ambiguous_late_alternative_still_defers_and_stays_out_after_the_next_statement_claims_it(seed, db_session, run):
    card = _card(seed, db_session)
    a = seed.entry(card, "-120", day=date(2026, 9, 28), merchant="咖啡")
    b = seed.entry(card, "-120", day=date(2026, 9, 29), merchant="咖啡")  # late: a second coffee printed in October
    db_session.commit()
    sep, (line,), _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 28), "咖啡", 120)])
    (amb,) = [c for c in _cases(db_session, sep.id, "open") if c.kind == "ambiguous"]
    assert {c["entry_id"] for c in amb.candidates} == {a.id, b.id}
    assert _gap_of(db_session, sep.id) == []  # b deferred: the line is one coffee, the other is October's
    from app.services.reconciliation_actions import engine
    engine.apply_action(db_session, case_id=amb.id, action="match", params={"line_id": line.id, "entry_id": a.id},
                        actor="owner", label="t")  # the owner picks a, so October's line has one candidate
    db_session.commit()
    octo, _, _ = _submit(db_session, run, card, [_line(1, date(2026, 10, 1), "咖啡", 120)],
                         period=(date(2026, 10, 1), date(2026, 10, 30)), opening="120")
    assert [r.entry_id for r in _active(db_session, octo.id)] == [b.id] and _gap_of(db_session, octo.id) == []
    _reconcile(db_session, sep.id)
    assert _gap_of(db_session, sep.id) == []  # b is October's claim now: still out of September's balance


def test_a_designated_entry_the_next_statement_claims_leaves_no_permanent_residual(seed, db_session, run):
    card = _card(seed, db_session)
    entry = seed.entry(card, "-1000", day=date(2026, 9, 30), merchant="全聯")
    db_session.commit()
    sep, _, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 30), "全聯", 1005)])
    assert _gap_of(db_session, sep.id) == ["-5.0000"]  # the designated near entry is taken as printed here
    _submit(db_session, run, card, [_line(1, date(2026, 10, 1), "全聯", 1000)],
            period=(date(2026, 10, 1), date(2026, 10, 30)), opening="1005")
    _reconcile(db_session, sep.id)
    # October claims the 1000: September's 1005 line has no ledger row at all
    assert _gap_of(db_session, sep.id) == ["-1005.0000"]
    assert [c.kind for c in _cases(db_session, sep.id, "open") if c.kind != "balance_gap"] == ["line_unmatched"]
    assert entry.id not in {c.entry_id for c in _cases(db_session, sep.id, "open")}


def test_an_entry_this_statement_claims_after_its_period_end_is_held(seed, db_session, run):
    card = _card(seed, db_session)
    entry = seed.entry(card, "-580", day=date(2026, 10, 1), merchant="全聯")  # booked a day after the close
    db_session.commit()
    sep, _, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 29), "全聯", 580)])
    assert [r.entry_id for r in _active(db_session, sep.id)] == [entry.id]
    assert _cases(db_session, sep.id, "open") == []


def test_out_of_order_reconcile_the_next_statement_claimed_the_late_entry_first(seed, db_session, run):
    card = _card(seed, db_session)
    seed.entry(card, "-580", day=date(2026, 9, 3), merchant="全聯")
    late = seed.entry(card, "-90", day=date(2026, 9, 29), merchant="咖啡")
    db_session.commit()
    octo, _, _ = _submit(db_session, run, card, [_line(1, date(2026, 10, 1), "咖啡", 90)],
                         period=(date(2026, 10, 1), date(2026, 10, 30)), opening="580")
    assert [r.entry_id for r in _active(db_session, octo.id)] == [late.id]
    sep, _, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 3), "全聯", 580)])
    assert not [c for c in _cases(db_session, sep.id) if c.explanation == "deferred_next_period"]  # claimed already
    assert _cases(db_session, sep.id, "open") == []


# --- closing-day guardrail at ingest -------------------------------------------------------------------------------

HSBC_LIKE = (date(2026, 8, 7), date(2026, 9, 6))  # closing day 6
DUE_AS_CLOSING = (date(2026, 8, 25), date(2026, 9, 24))  # the payment due date (24th) read as the closing date


def test_misread_closing_date_goes_to_parse_review_and_matches_nothing(seed, db_session, run):
    card = _card(seed, db_session, closing_day=6)
    seed.entry(card, "-580", day=date(2026, 9, 3), merchant="全聯")
    db_session.commit()
    stmt, _, res = _submit(db_session, run, card, [_line(1, date(2026, 9, 3), "全聯", 580)], period=DUE_AS_CLOSING,
                           closing_date=DUE_AS_CLOSING[1])
    assert res.revision.guardrail_ok is False and res.revision.guardrail["checks"]["closing_day"] is False
    assert res.revision.guardrail["detail"]["closing_day"] == {
        "expected_day": 6, "period_end": "2026-09-24", "period_end_days_off": -12, "closing_date": "2026-09-24",
        "closing_date_days_off": -12}
    assert [c.kind for c in _cases(db_session, stmt.id)] == ["parse_review"]
    assert _active(db_session, stmt.id) == [] and stmt.matched_count == 0


def test_correct_closing_date_goes_live_and_reconciles(seed, db_session, run):
    card = _card(seed, db_session, closing_day=6)
    seed.entry(card, "-580", day=date(2026, 9, 3), merchant="全聯")
    db_session.commit()
    stmt, _, res = _submit(db_session, run, card, [_line(1, date(2026, 9, 3), "全聯", 580)], period=HSBC_LIKE,
                           closing_date=date(2026, 9, 8))  # a +2 weekend shift of the 6th
    assert res.revision.guardrail_ok is True and res.revision.guardrail["checks"]["closing_day"] is True
    assert stmt.current_revision_id == res.revision.id and stmt.mode == "live"
    assert _cases(db_session, stmt.id) == [] and stmt.matched_count == 1


def test_reparse_with_misread_closing_date_is_not_made_current(seed, db_session, run):
    card = _card(seed, db_session, closing_day=6)
    seed.entry(card, "-580", day=date(2026, 9, 3), merchant="全聯")
    db_session.commit()
    lines = [_line(1, date(2026, 9, 3), "全聯", 580)]
    stmt, _, good = _submit(db_session, run, card, lines, period=HSBC_LIKE, closing_date=HSBC_LIKE[1])
    rows = [r.id for r in _active(db_session, stmt.id)]
    stmt, _, bad = _submit(db_session, run, card, lines, period=HSBC_LIKE, closing_date=date(2026, 9, 24))
    assert bad.revision.guardrail_ok is False and stmt.current_revision_id == good.revision.id
    assert [c.kind for c in _cases(db_session, stmt.id, "open")] == ["parse_review"]
    assert [r.id for r in _active(db_session, stmt.id)] == rows


def test_bank_statement_on_an_account_with_a_closing_day_is_not_checked(seed, db_session, run):
    """Review I3: MOZE import sets closing_day on any account whose startDay is not 1; a bank's calendar month passes."""
    bank = seed.account("銀行", opening="1000", statement_live_from=date(2026, 9, 1), closing_day=23)
    seed.entry(bank, "500", kind="income", day=date(2026, 9, 5), merchant="薪資")
    db_session.commit()
    stmt, _, res = _submit(db_session, run, bank, [_line(1, date(2026, 9, 5), "薪資", 500, "deposit")], kind="bank",
                           opening="1000")
    assert res.revision.guardrail_ok is True and "closing_day" not in res.revision.guardrail["checks"]
    assert _cases(db_session, stmt.id) == [] and stmt.matched_count == 1


def test_account_without_closing_day_has_no_closing_check(seed, db_session, run):
    card = _card(seed, db_session, closing_day=None)
    seed.entry(card, "-580", day=date(2026, 9, 3), merchant="全聯")
    db_session.commit()
    stmt, _, res = _submit(db_session, run, card, [_line(1, date(2026, 9, 3), "全聯", 580)], period=DUE_AS_CLOSING)
    assert res.revision.guardrail_ok is True and "closing_day" not in res.revision.guardrail["checks"]
    assert _cases(db_session, stmt.id) == [] and stmt.matched_count == 1


# --- skips, recheck, batch ---------------------------------------------------------------------------------------

def test_needs_recheck_is_cleared(card_month, db_session):
    stmt, *_ = card_month
    stmt.needs_recheck = True
    db_session.commit()
    _reconcile(db_session, stmt.id)
    assert db_session.get(AccountStatement, stmt.id).needs_recheck is False


def test_open_parse_review_skips(card_month, db_session):
    stmt, ls, *_ = card_month
    db_session.add(ReconciliationCase(statement_id=stmt.id, revision_id=stmt.current_revision_id,
                                      kind="parse_review", context={}))
    stmt.needs_recheck = True
    db_session.commit()
    rows = [r.id for r in _active(db_session, stmt.id)]
    result = _reconcile(db_session, stmt.id)
    assert result.skipped == "parse_review" and result.claims == 0 and result.cases_opened == []
    assert [r.id for r in _active(db_session, stmt.id)] == rows
    assert db_session.get(AccountStatement, stmt.id).needs_recheck is True


def test_failed_current_revision_skips(seed, db_session, run):
    card = _card(seed, db_session)
    seed.entry(card, "-580", day=date(2026, 9, 3), merchant="全聯")
    db_session.commit()
    stmt, _, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 3), "全聯", 580)], total="1")
    assert _active(db_session, stmt.id) == []
    assert _reconcile(db_session, stmt.id).skipped == "no_current_revision"
    assert [c.kind for c in _cases(db_session, stmt.id)] == ["parse_review"]


def test_hook_leaves_the_pass_to_the_batch_while_an_import_runs(seed, db_session, run, pg_engine):
    from sqlalchemy import text
    from app.services.moze_import_service import IMPORT_LOCK_KEY
    card = _card(seed, db_session)
    seed.entry(card, "-580", day=date(2026, 9, 3), merchant="全聯")
    db_session.commit()
    with pg_engine.connect() as importer:
        importer.execute(text("SELECT pg_advisory_lock(:key)"), {"key": IMPORT_LOCK_KEY})
        try:
            stmt, _, res = _submit(db_session, run, card, [_line(1, date(2026, 9, 3), "全聯", 580)])
        finally:
            importer.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": IMPORT_LOCK_KEY})
    assert stmt.current_revision_id == res.revision.id and stmt.needs_recheck is True
    assert _active(db_session, stmt.id) == [] and res.case_ids == []
    (item,) = _batch(db_session)
    assert item["statement_id"] == stmt.id and item["result"].claims == 1
    assert len(_active(db_session, stmt.id)) == 1


def test_population_window_is_the_max_of_the_rule_windows():
    from app.services.statements import matching as m
    rules = m.Rules(period_end=date(2026, 9, 30))
    assert m.population_window_days(rules) == 15
    small = dc_replace(rules, candidate_window_days=5, drift_window_days=15)
    assert m.population_window_days(small) == 15
    wide = dc_replace(rules, candidate_window_days=20)
    assert m.population_window_days(wide) == 20
    assert m.population_window_days(dc_replace(rules, exact_window_days=40)) == 40


def test_stored_overrides_without_a_version_read_as_r1b(db_session):
    # a row PUT before R6a may carry rules overrides and no rules_version key: its scale overrides are stale too
    row = settings_service._reconciliation_row(db_session)
    row.data = {**(row.data or {}), "rules": {"margin": "0.15", "exact_window_days": 4}}
    row.data.pop("rules_version", None)
    db_session.flush()
    raw = settings_service.reconciliation_rules(db_session)
    assert raw["rules_version"] == "r1b-1" and settings_service.read_reconciliation_rules(db_session)["rules_version"] == "r1b-1"
    rules = rec.rules_for(raw, date(2026, 9, 30))
    assert rules.margin == Decimal("0.05") and rules.exact_window_days == 4
    assert settings_service.get_reconciliation_settings(db_session)["rules_version"] == "r1b-1"


def test_drift_window_is_validated_like_the_other_int_rules(db_session):
    with pytest.raises(settings_service.ValidationError):
        settings_service.update_reconciliation_settings(db_session, {"dirty_enabled": False, "rules": {"drift_window_days": 10001}})
    with pytest.raises(settings_service.ValidationError):
        settings_service.update_reconciliation_settings(db_session, {"dirty_enabled": False, "rules": {"drift_window_days": "15"}})


def test_settings_readers_log_stale_scale_overrides(db_session, caplog, monkeypatch):
    import logging
    monkeypatch.setattr(logging.getLogger("app.services.settings_service"), "disabled", False)
    row = settings_service._reconciliation_row(db_session)
    for margin, expected in (("0.20", 1), ("0.15", 0)):
        row.data = {**(row.data or {}), "rules": {"margin": margin}, "rules_version": "r1b-1"}
        db_session.flush()
        for reader in (settings_service.reconciliation_rules, settings_service.read_reconciliation_rules):
            caplog.clear()
            with caplog.at_level(logging.WARNING, logger="app.services.settings_service"):
                got = reader(db_session)
            records = [r for r in caplog.records if "ignored stale rule override" in r.message]
            assert len(records) == expected and all(r.name == "app.services.settings_service" for r in records)
            assert got["margin"] == "0.05"


def test_rules_for_ignores_old_scale_overrides_with_a_warning(caplog, monkeypatch):
    import logging
    # alembic's fileConfig (the session's in-process upgrade) disables loggers that already exist: re-enable ours
    monkeypatch.setattr(logging.getLogger("app.services.reconciliation_service"), "disabled", False)
    with caplog.at_level(logging.WARNING, logger="app.services.reconciliation_service"):
        rules = rec.rules_for({"accept": "0.95", "margin": "0.20", "exact_window_days": "4", "rules_version": "r1b-7"}, date(2026, 9, 30))
    assert rules.accept == Decimal("0.80") and rules.margin == Decimal("0.05") and rules.exact_window_days == 4
    assert sum("ignored stale rule override" in r.message for r in caplog.records) == 2
    caplog.clear()
    plain = rec.rules_for({**settings_service.RULE_DEFAULTS, "rules_version": "r1b-1"}, date(2026, 9, 30))
    assert plain.margin == Decimal("0.05") and not any("ignored stale rule override" in r.message for r in caplog.records)
    fresh = rec.rules_for({"accept": "0.95", "rules_version": "r6a-9"}, date(2026, 9, 30))
    assert fresh.accept == Decimal("0.95")


def test_rules_for_stale_r1b_defaults_never_pin_the_old_values(caplog, monkeypatch):
    import logging
    monkeypatch.setattr(logging.getLogger("app.services.reconciliation_service"), "disabled", False)
    pe = date(2026, 9, 30)
    stale = {"rules_version": "r1b-1"}
    with caplog.at_level(logging.WARNING, logger="app.services.reconciliation_service"):
        r = rec.rules_for({**stale, "margin": "0.15", "near_tolerance_pct": "0.03", "exact_window_days": 4}, pe)
        assert r.margin == Decimal("0.05") and r.near_tolerance_pct == Decimal("0.05") and r.exact_window_days == 4
        assert not any("ignored stale rule override" in x.message for x in caplog.records)
        r = rec.rules_for({**stale, "margin": "0.20"}, pe)
        assert r.margin == Decimal("0.05")
        assert sum("ignored stale rule override" in x.message for x in caplog.records) == 1
        caplog.clear()
        assert rec.rules_for({**stale, "near_tolerance_pct": "0.04"}, pe).near_tolerance_pct == Decimal("0.04")
        assert rec.rules_for({"rules_version": "r6a-9", "near_tolerance_pct": "0.03"}, pe).near_tolerance_pct == Decimal("0.03")
        assert not any("ignored stale rule override" in x.message for x in caplog.records)


def test_rules_defaults_in_settings(db_session):
    settings = settings_service.get_reconciliation_settings(db_session)
    assert settings["rules_version"] == "r6a-1"
    assert settings["rules"]["accept"] == "0.80" and settings["rules"]["margin"] == "0.05"
    assert settings["rules"]["candidate_window_days"] == 10 and settings["rules"]["drift_window_days"] == 15
    assert "period_end" not in settings["rules"] and "年費" in settings["rules"]["bank_only_patterns"]


def test_rule_change_bumps_rules_version_with_the_r6a_prefix(db_session):
    current = settings_service.get_reconciliation_settings(db_session)
    out = settings_service.update_reconciliation_settings(db_session, {"dirty_enabled": current["dirty_enabled"], "rules": {"drift_window_days": 20}})
    assert out["rules_version"] == f"r6a-{out['version']}" and out["rules"]["drift_window_days"] == 20
    assert settings_service.reconciliation_rules(db_session)["rules_version"] == out["rules_version"]


def test_drift_candidate_eleven_days_outside_the_period_is_loaded(seed, db_session, run):
    # an entry 11 days after period end is outside the old +-10 window but inside the drift window
    card = _card(seed, db_session, "window 卡")
    entry = seed.entry(card, "-580", day=date(2026, 10, 11), merchant="全聯")
    stmt, lines, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 28), "全聯", 580, "purchase")])
    case = next(c for c in _cases(db_session, stmt.id, "open") if c.kind == "amount_delta")
    assert case.context["hint"] == "date_drift" and case.entry_id == entry.id and case.context["date_distance"] == 13


def test_batch_records_a_failure_and_goes_on(seed, db_session, run, monkeypatch):
    good_card = _card(seed, db_session, "好卡")
    bad_card = _card(seed, db_session, "壞卡")
    buy = seed.entry(good_card, "-580", day=date(2026, 9, 3), merchant="全聯")
    db_session.commit()
    good, _, _ = _submit(db_session, run, good_card, [_line(1, date(2026, 9, 3), "全聯", 580)])
    bad, _, _ = _submit(db_session, run, bad_card, [_line(1, date(2026, 9, 3), "全聯", 580)])
    for statement in (good, bad):
        statement.needs_recheck = True
    db_session.commit()
    real = rec.load_candidates

    def flaky(db, statement, accounts, **kw):
        if statement.id == bad.id:
            raise RuntimeError("boom")
        return real(db, statement, accounts, **kw)

    monkeypatch.setattr(rec, "load_candidates", flaky)
    assert rec.pending_statement_ids(db_session) == sorted([good.id, bad.id])
    commits = []
    items = []
    for item in rec.reconcile_each(db_session, rec.pending_statement_ids(db_session)):
        commits.append(db_session.in_transaction())  # the service leaves the commit to the caller
        db_session.commit()
        items.append(item)
    assert commits == [True, True]
    by_id = {item["statement_id"]: item for item in items}
    assert by_id[good.id]["result"].claims == 1 and "error" not in by_id[good.id]
    assert by_id[bad.id] == {"statement_id": bad.id, "error": "RuntimeError"}
    db_session.expire_all()
    assert db_session.get(AccountStatement, good.id).needs_recheck is False
    assert db_session.get(AccountStatement, bad.id).needs_recheck is True
    assert [r.entry_id for r in _active(db_session, good.id)] == [buy.id]


def test_batch_picks_up_dirty_statements(seed, db_session, run):
    card = _card(seed, db_session)
    stmt, ls, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 3), "全聯", 580)])
    assert stmt.open_case_count == 2 and len(_open_beside_gap(db_session, stmt.id, "-580.0000")) == 1
    set_dirty(db_session, True)
    buy = seed.entry(card, "-580", day=date(2026, 9, 3), merchant="全聯")
    db_session.commit()
    (item,) = _batch(db_session)
    assert item["statement_id"] == stmt.id and item["result"].claims == 1
    assert [r.entry_id for r in _active(db_session, stmt.id)] == [buy.id]
    stmt = db_session.get(AccountStatement, stmt.id)
    assert (stmt.needs_recheck, stmt.open_case_count, stmt.matched_count) == (False, 0, 1)
    assert rec.pending_statement_ids(db_session) == []


# --- lock order --------------------------------------------------------------------------------------------------

def test_edit_waits_for_reconcile_and_the_next_sweep_releases(seed, db_session, run, pg_engine):
    card = _card(seed, db_session)
    buy = seed.entry(card, "-580", day=date(2026, 9, 3), merchant="全聯")
    db_session.commit()
    stmt, ls, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 3), "全聯", 580)])
    (claim,) = _active(db_session, stmt.id)
    set_dirty(db_session, True)
    payload = EntryUpdateIn(account_id=card.id, kind="expense", amount=Decimal("590"), entry_date=date(2026, 9, 3),
                            posted_date=date(2026, 9, 3), merchant="全聯")

    outcome = race(pg_engine, lambda s: rec.reconcile(s, stmt.id), lambda s: ews.update_entry(s, buy.id, payload))

    assert outcome == "committed"
    result = _reconcile(db_session, stmt.id)
    assert result.claims == 0
    db_session.expire_all()
    old = db_session.get(StatementCoverage, claim.id)
    assert old.status == "stale" and old.stale_reason.startswith("dirty:update:")
    assert _active(db_session, stmt.id) == []
    assert {c.kind for c in _open_beside_gap(db_session, stmt.id, "10.0000")} == {"amount_delta", "entry_unmatched"}


def test_rederived_case_carries_the_sweep_event(seed, db_session, run):
    card = _card(seed, db_session)
    buy = seed.entry(card, "-580", day=date(2026, 9, 3), merchant="全聯")
    db_session.commit()
    stmt, ls, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 3), "全聯", 580)])
    set_dirty(db_session, True)
    buy = db_session.get(type(buy), buy.id)
    buy.amount = Decimal("-590")
    db_session.commit()
    event_id = db_session.execute(select(func.max(CoverageDirty.id))).scalar_one()
    _reconcile(db_session, stmt.id)
    superseded = _cases(db_session, stmt.id, "superseded")
    assert [c.kind for c in superseded] == ["recheck"]
    (delta,) = [c for c in _cases(db_session, stmt.id, "open") if c.kind == "amount_delta"]
    assert delta.context["sweep"] == {"reopened_by_event": event_id, "op": "update", "row_id": buy.id}
    # a quiet re-run keeps the case (the carried sweep note is not part of its identity)
    _reconcile(db_session, stmt.id)
    assert [c.id for c in _cases(db_session, stmt.id, "open") if c.kind == "amount_delta"] == [delta.id]


def test_entry_whose_group_changed_between_reads_is_left_out(seed, db_session, run, monkeypatch):
    card = _card(seed, db_session)
    seed.entry(card, "-580", day=date(2026, 9, 3), merchant="全聯")
    db_session.commit()
    real = rec.load_candidates
    calls = []

    def regrouped(db, statement, accounts, **kw):
        entries, groups = real(db, statement, accounts, **kw)
        calls.append(1)
        if len(calls) == 2:  # the re-read under the locks sees the entry joined a group meanwhile
            entries = [dc_replace(e, group_id=999) for e in entries]
        return entries, groups

    monkeypatch.setattr(rec, "load_candidates", regrouped)
    stmt, ls, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 3), "全聯", 580)])
    assert len(calls) == 2 and _active(db_session, stmt.id) == []
    assert [c.kind for c in _cases(db_session, stmt.id, "open")] == ["line_unmatched"]


def test_reconcile_waits_at_the_sweep_barrier_for_a_writer(seed, db_session, run, pg_engine):
    """Writer first: it holds the shared dirty barrier (its trigger fired) and a candidate's row lock; reconcile must
    block at the sweep's exclusive barrier (before any row lock), not deadlock, and finish after the writer commits."""
    card = _card(seed, db_session)
    buy = seed.entry(card, "-580", day=date(2026, 9, 3), merchant="全聯")
    db_session.commit()
    stmt, ls, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 3), "全聯", 580)])
    (claim,) = _active(db_session, stmt.id)
    set_dirty(db_session, True)
    payload = EntryUpdateIn(account_id=card.id, kind="expense", amount=Decimal("590"), entry_date=date(2026, 9, 3),
                            posted_date=date(2026, 9, 3), merchant="全聯")

    def writer(session):
        ews.update_entry(session, buy.id, payload)
        session.flush()  # the trigger runs: shared barrier + the row lock are held until commit

    factory = sessionmaker(bind=pg_engine, autoflush=False)
    writer_session, recon_session = factory(), factory()
    pids: list[int] = []
    ready, outcome = threading.Event(), []

    def reconcile_in_thread():
        try:
            pids.append(recon_session.execute(text("SELECT pg_backend_pid()")).scalar_one())
            ready.set()
            rec.reconcile(recon_session, stmt.id)
            recon_session.commit()
            outcome.append("committed")
        except Exception as exc:  # noqa: BLE001
            recon_session.rollback()
            outcome.append(exc)
        finally:
            ready.set()
            recon_session.close()

    thread = threading.Thread(target=reconcile_in_thread, daemon=True)
    committed = started = False
    try:
        writer(writer_session)
        writer_pid = writer_session.execute(text("SELECT pg_backend_pid()")).scalar_one()
        thread.start()
        started = True
        assert ready.wait(5) and pids
        # reconcile waits on a lock held by the writer's backend: the exclusive advisory barrier (before any row lock)
        assert wait_until_blocked(pg_engine, pids[0], blocker_pid=writer_pid) == "advisory"
        assert not outcome
        writer_session.commit()
        committed = True
        thread.join(timeout=5)
        assert not thread.is_alive()
    finally:
        if not committed:
            writer_session.rollback()
        if started:
            thread.join(timeout=5)
            if thread.is_alive() and pids:
                with pg_engine.connect() as conn:
                    conn.execute(text("SELECT pg_cancel_backend(:pid)"), {"pid": pids[0]})
                thread.join(timeout=5)
        writer_session.close()

    assert outcome == ["committed"]
    db_session.expire_all()
    old = db_session.get(StatementCoverage, claim.id)
    assert old.status == "stale" and old.stale_reason.startswith("dirty:update:")  # the sweep saw the writer's event
    assert _active(db_session, stmt.id) == []
    assert {c.kind for c in _open_beside_gap(db_session, stmt.id, "10.0000")} == {"amount_delta", "entry_unmatched"}


# --- lock conflicts: reconcile yields (NOWAIT on group/entry locks, bounded barrier wait) -----------------------

def _pause_after_the_sweep(monkeypatch):
    """reconcile's first `load_candidates` (after the sweep wrote its coverage/case rows, before the group/entry
    locks) sets `swept` and waits (bounded) for `go`."""
    swept, go = threading.Event(), threading.Event()
    real, calls = rec.load_candidates, []

    def paused(db, statement, accounts, **kw):
        calls.append(1)
        if len(calls) == 1:
            swept.set()
            go.wait(10)
        return real(db, statement, accounts, **kw)

    monkeypatch.setattr(rec, "load_candidates", paused)
    return swept, go


def test_reconcile_yields_to_a_delete_of_a_claimed_entry(seed, db_session, run, pg_engine, monkeypatch):
    """A dirty edit makes the sweep release the claim (row lock on the coverage row); a delete of the entry then
    locks the entry and waits on that coverage row (ON DELETE SET NULL). Reconcile must not wait on the entry:
    it yields with reconcile_busy and the delete commits (before the fix: DeadlockDetected)."""
    card = _card(seed, db_session)
    buy = seed.entry(card, "-580", day=date(2026, 9, 3), merchant="全聯")
    db_session.commit()
    buy_id = buy.id
    stmt, ls, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 3), "全聯", 580)])
    stmt_id = stmt.id
    assert len(_active(db_session, stmt_id)) == 1
    set_dirty(db_session, True)
    db_session.get(LedgerEntry, buy_id).amount = Decimal("-590")
    db_session.commit()
    swept, go = _pause_after_the_sweep(monkeypatch)

    out = _interleave(pg_engine, lambda s: rec.reconcile(s, stmt_id), lambda s: ews.delete_entry(s, buy_id),
                      swept, go)

    assert _code(out["first"]) == "reconcile_busy", out
    assert isinstance(out["first"], CodedConflictError) and out["second"] == "committed", out
    assert db_session.execute(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.id == buy_id)).scalar_one() == 0
    assert stmt_id in rec.pending_statement_ids(db_session)  # the next pass picks it up


def test_reparse_hook_yields_to_a_delete_and_the_ingest_commits(seed, db_session, run, pg_engine, monkeypatch):
    """An identical re-parse moves the event's coverage to the new line (row lock on the coverage row) before the
    hook reconciles; a delete of the claimed entry waits on that row. The hook yields: the ingest commits with
    needs_recheck and the delete commits (before the fix: DeadlockDetected)."""
    card = _card(seed, db_session)
    buy = seed.entry(card, "-580", day=date(2026, 9, 3), merchant="全聯")
    db_session.commit()
    lines = [_line(1, date(2026, 9, 3), "全聯", 580)]
    stmt, _, _ = _submit(db_session, run, card, lines)
    assert len(_active(db_session, stmt.id)) == 1
    swept, go = _pause_after_the_sweep(monkeypatch)
    r, token = run
    submitted = []

    def ingest(session):
        rev = RevisionIn(run_id=r.id, lease_token=token, file_id=None, account_id=card.id, kind="card", parser="t",
                         parser_version="1", currency="TWD", period_start=SEP[0], period_end=SEP[1],
                         opening_balance=None, statement_total=Decimal("580"), lines=lines, raw={})
        submitted.append(revs.submit_revision(session, session.get(IngestRun, r.id), rev, account_map={}).revision.id)

    out = _interleave(pg_engine, ingest, lambda s: ews.delete_entry(s, buy.id), swept, go)

    assert out == {"first": "committed", "second": "committed"}, out
    db_session.expire_all()
    stmt = db_session.get(AccountStatement, stmt.id)
    assert stmt.current_revision_id == submitted[0] and stmt.needs_recheck is True
    (item,) = _batch(db_session)
    assert item["statement_id"] == stmt.id and item["result"].claims == 0 and _active(db_session, stmt.id) == []


@pytest.mark.parametrize("table", ["ledger_entry", "entry_group"])
def test_reconcile_is_busy_while_a_candidate_row_is_locked(card_month, db_session, pg_engine, table):
    stmt, _, e, group, _, _ = card_month
    row_id = e["buy"].id if table == "ledger_entry" else group.id
    stmt = db_session.get(AccountStatement, stmt.id)
    stmt.needs_recheck = True
    db_session.commit()
    with pg_engine.connect() as holder:
        holder.execute(text(f"SELECT id FROM {table} WHERE id = :id FOR UPDATE"), {"id": row_id})
        with pytest.raises(CodedConflictError) as busy:
            rec.reconcile(db_session, stmt.id)
        assert busy.value.code == "reconcile_busy"
        assert db_session.execute(text("SELECT 1")).scalar_one() == 1  # the session is still usable
        db_session.rollback()
        (item,) = _batch(db_session)
        assert item == {"statement_id": stmt.id, "error": "reconcile_busy"}
        holder.rollback()
    (item,) = _batch(db_session)
    assert "error" not in item and item["result"].claims == 4


def test_hook_isolates_a_matcher_failure(seed, db_session, run, monkeypatch):
    """A conflict inside the hook's reconcile rolls back only the reconcile: the revision is stored and current,
    its coverage writes are undone and the statement waits for the batch (needs_recheck)."""
    card = _card(seed, db_session)
    seed.entry(card, "-580", day=date(2026, 9, 3), merchant="全聯")
    db_session.commit()

    def broken(db, statement):
        raise ConflictError("coverage not conserved")

    monkeypatch.setattr(rec.coverage_service, "assert_conserved", broken)
    stmt, ls, res = _submit(db_session, run, card, [_line(1, date(2026, 9, 3), "全聯", 580)])
    db_session.expire_all()
    stmt = db_session.get(AccountStatement, stmt.id)
    assert stmt.current_revision_id == res.revision.id and len(ls) == 1
    assert stmt.needs_recheck is True and _active(db_session, stmt.id) == [] and res.case_ids == []
    monkeypatch.undo()
    (item,) = _batch(db_session)
    assert item["result"].claims == 1


def test_sweep_barrier_wait_is_bounded(seed, db_session, run, pg_engine, monkeypatch):
    """A writer holding the shared dirty barrier (an uncommitted dirty write) makes the sweep give up after
    lock_timeout with sweep_barrier_busy; the session stays usable with its lock_timeout restored."""
    from app.services import coverage_service
    card = _card(seed, db_session)
    buy = seed.entry(card, "-580", day=date(2026, 9, 3), merchant="全聯")
    db_session.commit()
    stmt, _, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 3), "全聯", 580)])
    stmt.needs_recheck = True
    db_session.commit()
    set_dirty(db_session, True)
    monkeypatch.setattr(coverage_service, "BARRIER_LOCK_TIMEOUT", "200ms")
    writer = sessionmaker(bind=pg_engine, autoflush=False)()
    try:
        writer.get(LedgerEntry, buy.id).name = "renamed"
        writer.flush()  # the trigger holds the shared barrier until the writer's transaction ends
        before = db_session.execute(text("SHOW lock_timeout")).scalar_one()
        with pytest.raises(CodedConflictError) as busy:
            rec.reconcile(db_session, stmt.id)
        assert busy.value.code == "sweep_barrier_busy"
        assert db_session.execute(text("SHOW lock_timeout")).scalar_one() == before  # usable, timeout restored
        db_session.rollback()
        (item,) = _batch(db_session)
        assert item == {"statement_id": stmt.id, "error": "sweep_barrier_busy"}
    finally:
        writer.rollback()
        writer.close()
    (item,) = _batch(db_session)
    assert "error" not in item and item["result"].claims == 1


def test_reparse_hook_never_waits_for_the_barrier(seed, db_session, run, pg_engine, monkeypatch):
    """The re-parse transfers coverage row C (held by the ingest) before the hook sweeps. A writer that already holds
    the shared dirty barrier (it updated another entry) then deletes C's entry and waits on C. Waiting for the
    barrier would close the cycle, so the hook try-locks it: the ingest commits with needs_recheck and the writer
    commits (before the fix: DeadlockDetected)."""
    from app.services import coverage_service
    card = _card(seed, db_session)
    bank = seed.account("銀行")
    buy = seed.entry(card, "-580", day=date(2026, 9, 3), merchant="全聯")
    other = seed.entry(bank, "-50", day=date(2026, 9, 3), merchant="早餐")
    db_session.commit()
    buy_id, other_id = buy.id, other.id
    lines = [_line(1, date(2026, 9, 3), "全聯", 580)]
    stmt, _, _ = _submit(db_session, run, card, lines)
    stmt_id = stmt.id
    assert len(_active(db_session, stmt_id)) == 1
    set_dirty(db_session, True)
    paused, go = threading.Event(), threading.Event()
    real, calls = coverage_service._take_barrier, []

    def pause_before_the_barrier(db, **kw):
        calls.append(kw)
        if len(calls) == 1:
            paused.set()
            go.wait(10)
        return real(db, **kw)

    monkeypatch.setattr(coverage_service, "_take_barrier", pause_before_the_barrier)
    r, token = run
    submitted = []

    def ingest(session):
        rev = RevisionIn(run_id=r.id, lease_token=token, file_id=None, account_id=card.id, kind="card", parser="t",
                         parser_version="1", currency="TWD", period_start=SEP[0], period_end=SEP[1],
                         opening_balance=None, statement_total=Decimal("580"), lines=lines, raw={})
        submitted.append(revs.submit_revision(session, session.get(IngestRun, r.id), rev, account_map={}).revision.id)

    def writer(session):
        session.execute(text("UPDATE ledger_entry SET name = 'x' WHERE id = :id"), {"id": other_id})  # shared barrier
        ews.delete_entry(session, buy_id)  # waits on the coverage row the ingest transferred

    out = _interleave(pg_engine, ingest, writer, paused, go)

    assert out == {"first": "committed", "second": "committed"}, out
    assert calls and calls[0].get("wait") is False, calls  # the hook try-locks; a waiting barrier must not be masked
    db_session.expire_all()
    stmt = db_session.get(AccountStatement, stmt_id)
    assert stmt.current_revision_id == submitted[0] and stmt.needs_recheck is True


@pytest.mark.parametrize("orig", ["deadlock", "other"])
def test_hook_tolerates_a_deadlock_but_not_other_driver_errors(seed, db_session, run, monkeypatch, orig):
    """DeadlockDetected inside the hook's pass (a residual wait on a coverage/case row) is treated like a conflict:
    the ingest commits with needs_recheck. Any other OperationalError still fails the submission."""
    import psycopg2.errors
    from sqlalchemy.exc import OperationalError
    card = _card(seed, db_session)
    seed.entry(card, "-580", day=date(2026, 9, 3), merchant="全聯")
    db_session.commit()
    cause = psycopg2.errors.DeadlockDetected() if orig == "deadlock" else psycopg2.errors.DiskFull()

    def broken(db, statement):
        raise OperationalError("SELECT 1", {}, cause)

    monkeypatch.setattr(rec.coverage_service, "assert_conserved", broken)
    lines = [_line(1, date(2026, 9, 3), "全聯", 580)]
    if orig == "other":
        with pytest.raises(OperationalError):
            _submit(db_session, run, card, lines)
        db_session.rollback()
        assert db_session.execute(select(func.count()).select_from(AccountStatement)).scalar_one() == 0
        return
    stmt, ls, res = _submit(db_session, run, card, lines)
    db_session.expire_all()
    stmt = db_session.get(AccountStatement, stmt.id)
    assert stmt.current_revision_id == res.revision.id and stmt.needs_recheck is True
    assert _active(db_session, stmt.id) == []


# --- currency and account gates on every claimable row (M1) ------------------------------------------------------

def _usd_child_card(seed, db, card):
    usd = seed.account("美金卡", currency="USD", is_credit=True, combined_account_id=card.id)
    db.commit()
    return usd


def test_entry_in_another_currency_is_never_claimed(seed, db_session, run):
    card = _card(seed, db_session)
    usd = _usd_child_card(seed, db_session, card)
    seed.entry(usd, "-580", day=date(2026, 9, 3), merchant="全聯")  # same date, merchant and number, but USD
    db_session.commit()
    stmt, ls, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 3), "全聯", 580)])
    assert _active(db_session, stmt.id) == []
    # the USD child is not in the TWD statement's balance either: the whole 580 is missing
    assert [(c.kind, c.line_id) for c in _open_beside_gap(db_session, stmt.id, "-580.0000")] == [
        ("line_unmatched", ls[0].id)]


def test_group_with_a_member_in_another_currency_is_not_claimed(seed, db_session, run):
    card = _card(seed, db_session)
    usd = _usd_child_card(seed, db_session, card)
    group = seed.group("split")
    seed.entry(card, "-300", day=date(2026, 9, 12), merchant="餐廳", group_id=group.id)
    seed.entry(usd, "-200", day=date(2026, 9, 12), merchant="餐廳", group_id=group.id)
    db_session.commit()
    stmt, _, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 12), "餐廳", 500)])
    assert _active(db_session, stmt.id) == []


def test_child_in_another_currency_does_not_complete_its_parent(seed, db_session, run):
    card = _card(seed, db_session)
    usd = _usd_child_card(seed, db_session, card)
    parent = seed.entry(card, "-100", day=date(2026, 9, 5), merchant="書店")
    seed.entry(usd, "-2", kind="fee", day=date(2026, 9, 5), parent_entry_id=parent.id)
    db_session.commit()
    stmt, _, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 5), "書店", 102)])
    assert _active(db_session, stmt.id) == []


def test_mapped_instance_beyond_the_window_gets_the_drift_hint(seed, db_session, run):
    """S1: the confirmed plan's instance entry lies 25 days past the candidate window: never claimed, but the line's
    case names it (installment_date_drift) instead of a plain line_unmatched."""
    from app.models import InstallmentPlanMap
    card = _card(seed, db_session)
    definition = seed.definition([seed.line("expense", card, "1000")], kind="installment", name="手機", times=12)
    late = seed.entry(card, "-1000", day=date(2026, 11, 4), merchant="手機")  # window ends 2026-10-10
    seed.instance(definition, 2, date(2026, 11, 4), status="posted", entries=[late])
    db_session.add(InstallmentPlanMap(account_id=card.id, plan_key="手機|12|1000.0000", definition_id=definition.id,
                                      confirmed_by="owner"))
    db_session.commit()
    stmt, ls, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 6), "手機", 1000, "installment",
                                                        installment_seq=2, installment_total=12)])
    assert _active(db_session, stmt.id) == []
    (case,) = _open_beside_gap(db_session, stmt.id, "-1000.0000")
    assert (case.kind, case.line_id) == ("line_unmatched", ls[0].id)
    assert case.context == {"hint": "installment_date_drift", "entry_id": late.id, "definition_id": definition.id}


def test_bank_payment_line_claims_a_scheduled_loan_installment_group(seed, db_session, run):
    bank = seed.account("銀行", opening="0", statement_live_from=date(2026, 9, 1))
    definition = seed.definition([seed.line("expense", bank, "10500")], kind="installment", name="房貸", times=12)
    group = seed.group("installment")
    principal = seed.entry(bank, "-10000", kind="payable", is_settlement=True, day=date(2026, 9, 3), group_id=group.id)
    interest = seed.entry(bank, "-500", kind="interest", day=date(2026, 9, 3), group_id=group.id)
    seed.instance(definition, 3, date(2026, 9, 3), status="posted", entries=[principal, interest])
    db_session.commit()
    stmt, ls, _ = _submit(db_session, run, bank, [_line(1, date(2026, 9, 3), "房貸", -10500, "payment")], kind="bank")
    assert sorted((r.line_id, r.entry_id, r.match_rule) for r in _active(db_session, stmt.id)) == sorted(
        [(ls[0].id, principal.id, "installment_group"), (ls[0].id, interest.id, "installment_group")])


def test_bank_transfer_in_line_claims_a_recurring_transfer_in_instance(seed, db_session, run):
    bank = seed.account("銀行", opening="0", statement_live_from=date(2026, 9, 1))
    definition = seed.definition([seed.line("transfer_in", bank, "3000")], kind="recurring", name="房租")
    entry = seed.entry(bank, "3000", kind="transfer_in", day=date(2026, 9, 3))
    seed.instance(definition, 1, date(2026, 9, 3), status="posted", entries=[entry])
    db_session.commit()
    stmt, ls, _ = _submit(db_session, run, bank, [_line(1, date(2026, 9, 3), "房租", 3000, "transfer_in")], kind="bank")
    assert [(r.line_id, r.entry_id) for r in _active(db_session, stmt.id)] == [(ls[0].id, entry.id)]


# --- sibling candidates on unmatched lines (R1c Task 3) -------------------------------------------------------------

def _set_siblings(db, mapping):
    settings_service.update_reconciliation_settings(db, {"account_siblings": {str(k): [v.id for v in vs] for k, vs in mapping.items()}})
    db.commit()


def _sibling_case(db, stmt):
    (case,) = [c for c in _cases(db, stmt.id, "open") if c.kind == "line_unmatched"]
    return case


def test_unmatched_line_lists_sibling_candidates(seed, db_session, run):
    card = _card(seed, db_session, "甲卡")
    sibling = seed.account("姊妹")
    stranger = seed.account("路人")
    near = seed.entry(sibling, "-777", day=date(2026, 9, 12), merchant="誤記")
    seed.entry(sibling, "-777", day=date(2026, 9, 20), merchant="太遠")
    seed.entry(sibling, "-778", day=date(2026, 9, 10), merchant="金額不同")
    seed.entry(stranger, "-777", day=date(2026, 9, 10), merchant="非姊妹")
    db_session.commit()
    _set_siblings(db_session, {card.id: [sibling]})
    stmt, _, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 10), "誤記", 777)])
    case = _sibling_case(db_session, stmt)
    assert case.context["sibling_candidates"] == [
        {"entry_id": near.id, "account_id": sibling.id, "flow": "-777.0000", "posted_date": "2026-09-12"}]


def test_no_siblings_leaves_the_context_alone(seed, db_session, run):
    card = _card(seed, db_session, "甲卡")
    other = seed.account("他")
    seed.entry(other, "-777", day=date(2026, 9, 10), merchant="誤記")
    db_session.commit()
    stmt, _, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 10), "誤記", 777)])
    assert "sibling_candidates" not in _sibling_case(db_session, stmt).context


def test_claimed_sibling_entry_is_not_listed(seed, db_session, run):
    card = _card(seed, db_session, "甲卡")
    sibling = _card(seed, db_session, "乙卡")
    taken = seed.entry(sibling, "-777", day=date(2026, 9, 10), merchant="已對")
    db_session.commit()
    _submit(db_session, run, sibling, [_line(1, date(2026, 9, 10), "已對", 777)])  # claims the sibling's entry
    _set_siblings(db_session, {card.id: [sibling]})
    stmt, _, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 10), "誤記", 777)])
    assert "sibling_candidates" not in _sibling_case(db_session, stmt).context


def test_sibling_candidates_capped_and_ordered(seed, db_session, run):
    card = _card(seed, db_session, "甲卡")
    sibling = seed.account("姊妹")
    entries = [seed.entry(sibling, "-777", day=date(2026, 9, d), merchant=f"m{d}") for d in (11, 9, 12, 8, 10, 13, 7)]
    db_session.commit()
    _set_siblings(db_session, {card.id: [sibling]})
    stmt, _, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 10), "誤記", 777)])
    got = _sibling_case(db_session, stmt).context["sibling_candidates"]
    by_day = {e.id: e for e in entries}
    assert [c["posted_date"] for c in got] == ["2026-09-10", "2026-09-11", "2026-09-09", "2026-09-12", "2026-09-08"]
    assert len(got) == 5 and all(c["entry_id"] in by_day for c in got)


def test_date_drift_case_is_written_and_resolves_with_match(seed, db_session, run):
    from app.services.reconciliation_actions import engine
    card = _card(seed, db_session, "drift 卡")
    entry = seed.entry(card, "-580", day=date(2026, 9, 15), merchant="全聯")
    stmt, lines, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 5), "全聯", 580, "purchase")])
    case = next(c for c in _cases(db_session, stmt.id, "open") if c.kind == "amount_delta")
    assert case.context["hint"] == "date_drift" and case.context["date_distance"] == 10 and case.entry_id == entry.id
    assert case.candidates == [{"entry_id": entry.id, "reason": "date_drift", "date_distance": 10}]
    engine.apply_action(db_session, case_id=case.id, action="match", params={"line_id": lines[0].id, "entry_id": entry.id},
                        actor="owner", label="owner")
    db_session.commit()
    assert db_session.get(ReconciliationCase, case.id).status == "resolved" and len(_active(db_session, stmt.id)) == 1


def test_r6b_policy_defaults_and_validation(db_session):
    settings = settings_service.get_reconciliation_settings(db_session)
    policy = settings["policy"]
    assert policy["backfill_grace_days"] == 14 and policy["backfill_accounts"] == []
    assert policy["actions"]["backfill_entry"] == {"enabled": False, "max_amount": "5000"}
    assert policy["actions"]["anchor_opening"] == {"enabled": False, "max_amount": "200000"}
    assert policy["actions"]["replace_stand_in"] == {"enabled": True, "max_amount": "0"}
    assert settings["policy_version"] == "r6b-1"  # no stored row: the default label
    out = settings_service.update_reconciliation_settings(
        db_session, {"dirty_enabled": False, "policy": {"backfill_grace_days": 7, "backfill_accounts": [3, 5]}})
    assert out["policy_version"] == f"r6b-{out['version']}" and out["policy"]["backfill_grace_days"] == 7
    for bad in ({"backfill_grace_days": 400}, {"backfill_grace_days": "7"}, {"backfill_accounts": ["3"]},
                {"backfill_accounts": 3}, {"actions": {"backfill_entry": {"grace": 1}}}):
        with pytest.raises(settings_service.ValidationError):
            settings_service.update_reconciliation_settings(db_session, {"dirty_enabled": False, "policy": bad})


# --- R6b.0 structural-only sweep release ----------------------------------------------------------------------------

def _dirty(db):
    set_dirty(db, True)
    db.commit()


def _claimed_line(seed, db, run, merchant="全聯", **entry_fields):
    card = _card(seed, db, f"卡{merchant}")  # one card per claim: account names are unique
    entry = seed.entry(card, "-580", day=date(2026, 9, 3), merchant=merchant, **entry_fields)
    stmt, (line,), _ = _submit(db, run, card, [_line(1, date(2026, 9, 3), merchant, 580)])
    assert len(_active(db, stmt.id)) == 1
    return card, entry, stmt, line


def _sweep(db, stmt):
    db.expire_all()
    with db.begin_nested():
        locked = rec._lock_statement(db, stmt.id)
        coverage_service.sweep(db, locked, run_id=None, barrier_wait=False)
    db.commit()
    return stmt


@pytest.mark.parametrize("field, value", [("category_id", None), ("name", "改名"), ("merchant", "改商家"),
                                          ("description", "備註"), ("needs_review", True)])
def test_cosmetic_edit_keeps_the_match(seed, db_session, run, field, value):
    _dirty(db_session)
    card, entry, stmt, line = _claimed_line(seed, db_session, run)
    if field == "category_id":
        value = seed.category("其他").id
    setattr(entry, field, value)
    db_session.commit()
    _sweep(db_session, stmt)
    assert len(_active(db_session, stmt.id)) == 1 and not [c for c in _cases(db_session, stmt.id, "open")]


@pytest.mark.parametrize("field, value", [("amount", "-581"), ("posted_date", date(2026, 9, 4)),
                                          ("entry_date", date(2026, 9, 4)), ("currency", "USD")])
def test_structural_edit_releases_the_match(seed, db_session, run, field, value):
    _dirty(db_session)
    card, entry, stmt, line = _claimed_line(seed, db_session, run)
    setattr(entry, field, Decimal(value) if field == "amount" else value)
    db_session.commit()
    _sweep(db_session, stmt)
    assert _active(db_session, stmt.id) == []


def test_refund_link_change_releases(seed, db_session, run):
    _dirty(db_session)
    card, entry, stmt, line = _claimed_line(seed, db_session, run)
    other = seed.entry(card, "-100", day=date(2026, 9, 5))
    db_session.commit()
    entry.refunds_entry_id = other.id
    db_session.commit()
    _sweep(db_session, stmt)
    assert _active(db_session, stmt.id) == []


PRE_R6B_SNAPSHOT_KEYS = {"id", "amount", "flow", "currency", "original_amount", "original_currency", "entry_date",
                        "posted_date", "account_id", "kind", "group_id", "parent_entry_id", "transfer_group_id",
                        "refunds_entry_id", "settles_entry_id", "is_settlement", "name", "merchant"}  # R1 write_claim shape


def test_pre_r6b_snapshot_shape_is_compared_the_same_way(seed, db_session, run):
    # a coverage row written before R6b has exactly these keys (pinned literally): the projection applies to it as is
    _dirty(db_session)
    card, entry, stmt, line = _claimed_line(seed, db_session, run)
    (row,) = _active(db_session, stmt.id)
    assert set(row.snapshot) == PRE_R6B_SNAPSHOT_KEYS
    assert coverage_service.STRUCTURAL_KEYS == PRE_R6B_SNAPSHOT_KEYS - {"id", "name", "merchant"}
    old_shape = {k: row.snapshot[k] for k in PRE_R6B_SNAPSHOT_KEYS}  # an R1-era row, re-created literally
    assert coverage_service.structurally_changed(old_shape, entry) == []
    entry.name = "x"
    assert coverage_service.structurally_changed(old_shape, entry) == []
    entry.amount = Decimal("-1")
    assert coverage_service.structurally_changed(old_shape, entry) == ["amount", "flow"]


def test_transfer_group_parent_and_settlement_link_changes_release(seed, db_session, run):
    _dirty(db_session)
    card, entry, stmt, line = _claimed_line(seed, db_session, run)
    entry.transfer_group_id = uuid.uuid4()
    db_session.commit()
    _sweep(db_session, stmt)
    assert _active(db_session, stmt.id) == []
    card2, entry2, stmt2, line2 = _claimed_line(seed, db_session, run, merchant="家樂福")
    other = seed.entry(card2, "100", day=date(2026, 9, 5), kind="receivable", is_settlement=True)
    db_session.commit()
    entry2.settles_entry_id = other.id
    db_session.commit()
    _sweep(db_session, stmt2)
    assert _active(db_session, stmt2.id) == []
    card3, entry3, stmt3, line3 = _claimed_line(seed, db_session, run, merchant="7-11")
    g = seed.group("split")
    entry3.group_id = g.id
    db_session.commit()
    _sweep(db_session, stmt3)
    assert _active(db_session, stmt3.id) == []
    card4, entry4, stmt4, line4 = _claimed_line(seed, db_session, run, merchant="momo")
    parent = seed.entry(card4, "-900", day=date(2026, 9, 2), merchant="momo")
    db_session.commit()
    entry4.parent_entry_id = parent.id  # parent link: the entry becomes a child -> structural -> release
    db_session.commit()
    _sweep(db_session, stmt4)
    assert _active(db_session, stmt4.id) == []
