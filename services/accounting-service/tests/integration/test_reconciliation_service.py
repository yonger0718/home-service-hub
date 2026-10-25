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
from app.services import stand_ins
from app.services import statement_revision_service as revs
from app.services.errors import CodedConflictError, ConflictError
from app.services.statements.matching import Holder
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


def test_zero_amount_unknown_line_opens_no_case_and_an_old_one_is_superseded(seed, db_session, run):
    """#152: a zero-amount `unknown` row (an annual-fee waiver notice) is kept as a line for audit but is not part of
    the reconcilable population: no case, no gap, not counted; a `line_unmatched` case an earlier run opened on such a
    line is superseded by the next reconcile."""
    from app.models import ReconciliationCase
    card = _card(seed, db_session)
    buy = seed.entry(card, "-580", day=date(2026, 9, 2), merchant="全聯")
    db_session.commit()
    stmt, (line, info), _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 3), "全聯", 580),
                                                           _line(2, date(2026, 9, 30), "年費優惠", 0, "unknown")])
    assert [(r.line_id, r.entry_id) for r in _active(db_session, stmt.id)] == [(line.id, buy.id)]
    assert _cases(db_session, stmt.id, "open") == []
    assert (stmt.matched_count, stmt.open_case_count) == (1, 0)

    old = ReconciliationCase(statement_id=stmt.id, revision_id=stmt.current_revision_id, kind="line_unmatched",
                             event_id=info.event_id, line_id=info.id)  # what a run before the fix wrote
    db_session.add(old)
    db_session.commit()
    _reconcile(db_session, stmt.id)
    db_session.expire_all()
    assert (old.status, old.version) == ("superseded", 2)
    assert _cases(db_session, stmt.id, "open") == []


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


@pytest.mark.parametrize("posted, oct_line, gap_after", [
    (date(2026, 9, 29), date(2026, 10, 1), "-200.0000"),  # late: deferred here again once October lets go
    (date(2026, 9, 27), date(2026, 9, 30), "-110.0000"),  # not late: an unprinted ledger row, a real gap
])
def test_held_balance_after_a_later_statement_that_claimed_an_entry_is_retired(seed, db_session, run, dirty_on,
                                                                               posted, oct_line, gap_after):
    """October claims a September-dated coffee first (out of order), so September's held balance leaves it out; the
    retire releases October's coverage, and September's next reconcile and the adjustment use the fresh value."""
    from app.services import statement_retire_service as retire_svc
    from app.services.reconciliation_actions import engine
    card = _card(seed, db_session)
    seed.entry(card, "-580", day=date(2026, 9, 3), merchant="全聯")
    coffee = seed.entry(card, "-90", day=posted, merchant="咖啡")
    db_session.commit()
    octo, _, _ = _submit(db_session, run, card, [_line(1, oct_line, "咖啡", 90)],
                         period=(date(2026, 10, 1), date(2026, 10, 30)), opening="780")
    assert [r.entry_id for r in _active(db_session, octo.id)] == [coffee.id]
    sep, _, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 3), "全聯", 580),
                                                _line(2, date(2026, 9, 8), "神秘", 200)])
    assert _gap_of(db_session, sep.id) == ["-200.0000"]  # only the missing 200: the coffee is October's
    retire_svc.retire_statement(db_session, account_id=card.id, statement_id=octo.id, note="重複", label="t",
                                request_id=None)
    db_session.commit()
    assert _active(db_session, octo.id) == []
    _reconcile(db_session, sep.id)
    (gap,) = [c for c in _cases(db_session, sep.id, "open") if c.kind == "balance_gap"]
    assert gap.context == {"gap": gap_after, "card": True}
    row, _ = engine.apply_action(db_session, case_id=gap.id, action="balance_adjustment_asof", params={},
                                 actor="owner", label="t")
    db_session.commit()
    (adj,) = db_session.execute(select(LedgerEntry).where(LedgerEntry.kind == "balance_adjustment")).scalars().all()
    assert adj.amount == Decimal(gap_after) and row.created_entry_ids == [adj.id]


def test_a_coverage_row_whose_entry_is_gone_does_not_undo_deferrals(seed, db_session, run):
    """An active claim keeps entry_id NULL after its entry is deleted until the sweep releases it (ON DELETE SET NULL;
    a MOZE re-import leaves many): it must not turn the deferral term off on any other statement."""
    other = _card(seed, db_session, "乙卡")
    gone = seed.entry(other, "-50", day=date(2026, 9, 5), merchant="超商")
    db_session.commit()
    o_stmt, _, _ = _submit(db_session, run, other, [_line(1, date(2026, 9, 5), "超商", 50)])
    db_session.delete(db_session.get(LedgerEntry, gone.id))
    db_session.commit()
    assert [r.entry_id for r in _active(db_session, o_stmt.id)] == [None]
    card = _card(seed, db_session)
    seed.entry(card, "-580", day=date(2026, 9, 3), merchant="全聯")
    late = seed.entry(card, "-90", day=date(2026, 9, 29), merchant="咖啡")
    db_session.commit()
    stmt, _, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 3), "全聯", 580)])
    assert [c.entry_id for c in _cases(db_session, stmt.id, "resolved")] == [late.id]
    assert _cases(db_session, stmt.id, "open") == []


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
    """A re-parse with the same lines at a new parser version moves the event's coverage to the new line (row lock on the coverage row) before the
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
                         parser_version="2", currency="TWD", period_start=SEP[0], period_end=SEP[1],
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
                         parser_version="2", currency="TWD", period_start=SEP[0], period_end=SEP[1],
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


def test_a_split_with_a_member_in_another_currency_is_whole_and_its_card_member_stays_home(seed, db_session, run):
    """Spec §3 "Mixed currencies" (final review): the USD member is a structural row the loader marks with its own
    currency (as every row and child it builds); its −20 cannot be summed with TWD lines, so the group is whole by
    fail-closed and the card's member-sized −300 line claims nothing (summed blindly, −320 is 20 off −300, past the
    10 TWD tolerance, and −220 with the +100 child: the group would be apart and the member claimed)."""
    card = _card(seed, db_session)
    usd = _usd_child_card(seed, db_session, card)
    group = seed.group("split")
    day = date(2026, 9, 12)
    card_member = seed.entry(card, "-300", day=day, merchant="餐廳", group_id=group.id)
    db_session.flush()
    kid = seed.entry(card, "100", kind="refund", day=day, parent_entry_id=card_member.id)
    usd_member = seed.entry(usd, "-20", day=day, merchant="餐廳", group_id=group.id)
    db_session.commit()
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, day, "餐廳", 300)])
    entries, _ = rec.load_candidates(db_session, stmt, rec.participating_accounts(db_session, card.id), window_days=15,
                                     include_reward=False)
    by_id = {e.id: e for e in entries}
    assert (by_id[card_member.id].currency, by_id[usd_member.id].currency, by_id[usd_member.id].claimable) == \
        ("TWD", "USD", False)
    assert [(c.id, c.currency) for c in by_id[card_member.id].children] == [(kid.id, "TWD")]
    assert _active(db_session, stmt.id) == []
    named = {x.get("entry_id") for c in _cases(db_session, stmt.id, "open") if c.line_id == line.id
             for x in c.candidates or []} | {c.entry_id for c in _cases(db_session, stmt.id, "open") if c.line_id == line.id}
    assert card_member.id not in named


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


# --- split members printed apart: the loader's structural rows (spec 2026-10-24 §3, §4.1) ---------------------------

def _owner_cover(db, statement, line, entries) -> None:
    """Hand the line to the owner: release whatever the hook claimed on it, then write manual coverage on `entries`
    (each one a principal row, flows summing to the line)."""
    from app.services.statements import matching
    coverage_service.release_line(db, statement, line.id, reason="test_owner")
    coverage_service.write_claim(db, statement, line, [matching.Row(e.id, "principal", Decimal(e.amount)) for e in entries],
                                 entries_by_id={e.id: e for e in entries}, match_kind="manual", match_rule="owner",
                                 run_id=None)
    db.commit()


def _cover_elsewhere(db, run, account, entry, day) -> None:
    """`entry` covered on an October statement of `account` by its own refund line (auto coverage, conserved)."""
    from app.services.statements import matching
    other, (line,), _ = _submit(db, run, account, [_line(1, day, "退款", -Decimal(entry.amount), "refund")], period=OCT)
    coverage_service.write_claim(db, other, line, [matching.Row(entry.id, "principal", Decimal(entry.amount))],
                                 entries_by_id={entry.id: entry}, match_kind="auto", match_rule="exact", run_id=None)
    db.commit()


def _claims_of(db, statement_id) -> list:
    """Active coverage of the statement as (line_id, entry_id, group_id, match_kind), sorted."""
    return sorted((r.line_id, r.entry_id, r.group_id, r.match_kind) for r in _active(db, statement_id))


def test_load_candidates_adds_structural_rows_and_two_child_views(seed, db_session, run):
    card = _card(seed, db_session)
    wallet = seed.account("錢包")
    group = seed.group("split")
    day = date(2026, 9, 12)
    card_member = seed.entry(card, "-500", day=day, merchant="餐廳", group_id=group.id)
    child_100 = seed.entry(card, "100", kind="refund", day=day, parent_entry_id=card_member.id)
    child_200 = seed.entry(card, "200", kind="refund", day=day, parent_entry_id=card_member.id)
    wallet_member = seed.entry(wallet, "-280", day=day, merchant="餐廳", group_id=group.id)
    fee = seed.entry(card, "-20", kind="fee", day=day, group_id=group.id)
    db_session.commit()
    _cover_elsewhere(db_session, run, card, child_200, date(2026, 10, 3))
    statement, _, _ = _submit(db_session, run, card, [_line(1, day, "餐廳", 777)])
    entries, groups = rec.load_candidates(db_session, statement, [card.id], window_days=15, include_reward=False,
                                          exclude={fee.id})
    by_id = {e.id: e for e in entries}
    assert sorted(by_id) == sorted([card_member.id, wallet_member.id, fee.id])
    assert by_id[card_member.id].claimable and not by_id[wallet_member.id].claimable and not by_id[fee.id].claimable
    assert by_id[fee.id].in_reverse is False and by_id[wallet_member.id].in_reverse is False
    assert by_id[wallet_member.id].instance is None and by_id[wallet_member.id].stand_in is False
    # the claimable member: `children` is the claimable view, `all_children` every child as stored
    assert [c.id for c in by_id[card_member.id].children] == [child_100.id]
    assert sorted(c.id for c in by_id[card_member.id].all_children) == sorted([child_100.id, child_200.id])
    assert by_id[wallet_member.id].children == () and by_id[wallet_member.id].all_children == ()
    assert groups[group.id].member_ids == tuple(sorted([card_member.id, wallet_member.id, fee.id]))


def test_load_candidates_structural_member_carries_all_its_children(seed, db_session, run):
    """A structural row's children (another account and currency included, claimed or not) are its full child list in
    both views; no structural row for members of a non-split group."""
    card = _card(seed, db_session)
    wallet = seed.account("錢包")
    usd = seed.account("美金", currency="USD")
    day = date(2026, 9, 12)
    group = seed.group("split")
    card_member = seed.entry(card, "-300", day=day, merchant="餐廳", group_id=group.id)
    wallet_member = seed.entry(wallet, "-280", day=day, merchant="餐廳", group_id=group.id)
    kid_wallet = seed.entry(wallet, "30", kind="refund", day=day, parent_entry_id=wallet_member.id)
    kid_usd = seed.entry(usd, "-2", kind="fee", day=day, parent_entry_id=wallet_member.id)
    reward = seed.group("reward_claim")
    seed.entry(card, "-90", day=day, merchant="咖啡", group_id=reward.id)
    other_reward_member = seed.entry(wallet, "-10", day=day, merchant="咖啡", group_id=reward.id)
    db_session.commit()
    statement, _, _ = _submit(db_session, run, card, [_line(1, day, "餐廳", 777)])
    entries, groups = rec.load_candidates(db_session, statement, [card.id], window_days=15, include_reward=False)
    by_id = {e.id: e for e in entries}
    structural = by_id[wallet_member.id]
    assert sorted(c.id for c in structural.children) == sorted([kid_wallet.id, kid_usd.id])
    assert structural.all_children == structural.children
    assert by_id[card_member.id].children == () and by_id[card_member.id].all_children == ()
    assert other_reward_member.id not in by_id and reward.id in groups


def test_reconcile_keeps_a_group_whole_through_a_covered_child_on_a_claimable_parent(seed, db_session, run):
    """«AGENT-249 1»: the +200 child is covered on another statement, so the claimable view drops it; the guard's
    view (`all_children`) still reads −300 −280 +200 = −380, which the −380 line prints → whole: the −300 line does
    not claim the member. No reading of the claimable view conserves −380 (the members alone sum to −580), so the
    −380 line stays open and the +200 child gets no second coverage row."""
    card = _card(seed, db_session)
    group = seed.group("split")
    day = date(2026, 9, 12)
    m1 = seed.entry(card, "-300", day=day, merchant="餐廳", group_id=group.id)
    seed.entry(card, "-280", day=day, merchant="餐廳", group_id=group.id)
    child = seed.entry(card, "200", kind="refund", day=day, parent_entry_id=m1.id)
    db_session.commit()
    _cover_elsewhere(db_session, run, card, child, date(2026, 10, 3))
    stmt, ls, _ = _submit(db_session, run, card, [_line(1, day, "餐廳", 300), _line(2, day, "餐廳", 380)])
    assert _active(db_session, stmt.id) == []
    assert db_session.execute(select(func.count()).select_from(StatementCoverage).where(
        StatementCoverage.entry_id == child.id, StatementCoverage.status == "active")).scalar_one() == 1
    before = [(c.id, c.kind, c.status, c.line_id) for c in _cases(db_session, stmt.id)]
    assert {c.line_id for c in _cases(db_session, stmt.id, "open") if c.kind != "balance_gap"} >= {ls[0].id, ls[1].id}
    _reconcile(db_session, stmt.id)
    assert _active(db_session, stmt.id) == []
    assert [(c.id, c.kind, c.status, c.line_id) for c in _cases(db_session, stmt.id)] == before


def test_reconcile_claims_a_cross_account_member_and_is_idempotent(seed, db_session, run):
    card = _card(seed, db_session)
    wallet = seed.account("錢包", statement_live_from=date(2026, 9, 1))
    group = seed.group("split")
    day = date(2026, 9, 12)
    card_member = seed.entry(card, "-300", day=day, merchant="餐廳", group_id=group.id)
    wallet_member = seed.entry(wallet, "-280", day=day, merchant="餐廳", group_id=group.id)
    db_session.commit()
    # the 錢包 half is matched on the 錢包 statement (its card sibling is a structural row there)
    wallet_stmt, (wallet_line,), _ = _submit(db_session, run, wallet, [_line(1, day, "餐廳", -280, "withdrawal")],
                                             kind="bank")
    assert _claims_of(db_session, wallet_stmt.id) == [(wallet_line.id, wallet_member.id, None, "auto")]
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, day, "餐廳", 300)])
    rows = _active(db_session, stmt.id)
    assert [(r.line_id, r.entry_id, r.group_id, r.match_kind) for r in rows] == [(line.id, card_member.id, None, "auto")]
    _reconcile(db_session, stmt.id)
    again = _active(db_session, stmt.id)
    assert [(r.id, r.entry_id) for r in again] == [(rows[0].id, card_member.id)]
    assert _cases(db_session, stmt.id, "superseded") == []


def test_reconcile_keeps_a_group_whole_when_its_sum_line_is_owner_covered(seed, db_session, run):
    """(a) The −580 sum line is held by the owner on another entry (a stand-in shape): `lines_all` still carries it,
    so the group is whole and the open −300 line never claims the member."""
    card = _card(seed, db_session)
    group = seed.group("split")
    day = date(2026, 9, 12)
    m1 = seed.entry(card, "-300", day=day, merchant="餐廳", group_id=group.id)
    m2 = seed.entry(card, "-280", day=day, merchant="餐廳", group_id=group.id)
    stand_in = seed.entry(card, "-580", day=day, merchant="代墊", source="statement")  # holders spec §3 stand-in
    db_session.commit()
    stmt, (sum_line, line), _ = _submit(db_session, run, card, [_line(1, day, "餐廳", 580), _line(2, day, "餐廳", 300)])
    _owner_cover(db_session, stmt, sum_line, [stand_in])
    _reconcile(db_session, stmt.id)
    assert _claims_of(db_session, stmt.id) == [(sum_line.id, stand_in.id, None, "manual")]
    open_cases = [c for c in _cases(db_session, stmt.id, "open") if c.line_id == line.id]
    assert not {m1.id, m2.id} & {c.entry_id for c in open_cases}
    assert not {m1.id, m2.id} & {x.get("entry_id") for c in open_cases for x in c.candidates or []}
    _reconcile(db_session, stmt.id)
    assert _claims_of(db_session, stmt.id) == [(sum_line.id, stand_in.id, None, "manual")]


@pytest.mark.parametrize("other", ["-575", "-570"])
def test_reconcile_releases_a_group_whose_near_line_the_owner_matched_to_an_unrelated_entry(seed, db_session, run,
                                                                                            other):
    """Spec #136 shape 5 vs owner-trusted: the −575 line is owner-matched to a plain −575 entry (exact) → unrelated →
    the group is apart and the −300 line claims the member. The same owner match on a −570 entry (inexact,
    amount_differs) keeps the group whole. The −570 entry is owner-matched at −575 and then edited (dirty triggers
    off, so nothing sweeps it): the coverage snapshot still conserves, the ledger row `guard_holders` reads does not."""
    card = _card(seed, db_session)
    group = seed.group("split")
    day = date(2026, 9, 12)
    m1 = seed.entry(card, "-300", day=day, merchant="餐廳", group_id=group.id)
    seed.entry(card, "-280", day=day, merchant="餐廳", group_id=group.id)
    held = seed.entry(card, "-575", day=day, merchant="超市")
    db_session.commit()
    stmt, (near, line), _ = _submit(db_session, run, card, [_line(1, day, "超市", 575), _line(2, day, "餐廳", 300)])
    _owner_cover(db_session, stmt, near, [held])
    held.amount = Decimal(other)
    db_session.commit()
    _reconcile(db_session, stmt.id)
    owner = (near.id, held.id, None, "manual")
    assert _claims_of(db_session, stmt.id) == (
        sorted([owner, (line.id, m1.id, None, "auto")]) if other == "-575" else [owner])


@pytest.mark.parametrize("sum_line", ["stand_in", "uncovered"])
def test_reconcile_keeps_a_group_whole_when_its_sum_line_is_on_the_previous_statement(seed, db_session, run, sum_line):
    """Spec §3 "Guard lines" (final review): the −580 sum line is printed by the August statement — held there by a
    stand-in, or open because August was reconciled before the members were booked. The ±15-day population brings
    the Aug 30 members into September, which prints a member-sized −300 line: the guard reads August's current lines
    too, so the group is whole and no member is claimed or listed; a second reconcile changes nothing."""
    card = _card(seed, db_session)
    day = date(2026, 8, 30)
    aug = (date(2026, 8, 1), date(2026, 8, 31))
    if sum_line == "stand_in":
        stand_in = seed.entry(card, "-580", day=day, merchant="代墊", source="statement")  # holders spec §3 stand-in
        db_session.commit()
        previous, (prev_line,), _ = _submit(db_session, run, card, [_line(1, day, "餐廳", 580)], period=aug)
        _owner_cover(db_session, previous, prev_line, [stand_in])
    else:
        previous, (prev_line,), _ = _submit(db_session, run, card, [_line(1, day, "餐廳", 580)], period=aug)
        assert _active(db_session, previous.id) == []
    group = seed.group("split")
    m1 = seed.entry(card, "-300", day=day, merchant="餐廳", group_id=group.id)
    m2 = seed.entry(card, "-280", day=day, merchant="餐廳", group_id=group.id)
    db_session.commit()
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 1), "餐廳", 300)])
    assert _active(db_session, stmt.id) == []
    named = {c.entry_id for c in _cases(db_session, stmt.id, "open") if c.line_id == line.id} | {
        x.get("entry_id") for c in _cases(db_session, stmt.id, "open") if c.line_id == line.id for x in c.candidates or []}
    assert not {m1.id, m2.id} & named
    before = [(c.id, c.kind, c.status, c.line_id) for c in _cases(db_session, stmt.id)]
    _reconcile(db_session, stmt.id)
    assert _active(db_session, stmt.id) == []
    assert [(c.id, c.kind, c.status, c.line_id) for c in _cases(db_session, stmt.id)] == before


# --- guard holders (issue #136, holders spec §3, §4.2, §6): stable coverage + this run's pass-1 claims -------------

def _guard_holders(db, statement) -> dict:
    """`rec.guard_holders` over the statement's guard lines, as `reconcile()` builds them."""
    accounts = rec.participating_accounts(db, statement.account_id)
    rules = rec.rules_for(settings_service.reconciliation_rules(db), statement.period_end)
    return rec.guard_holders(db, statement, rec.guard_lines(db, statement, accounts, rules),
                             frozenset(stand_ins.stand_in_entry_ids(db, accounts)),
                             rec.guard_statement_ids(db, statement, accounts, rules))


def _state(db, statement_id) -> tuple[list, list]:
    """Active coverage rows and every case, ids included: a second reconcile with nothing changed leaves both equal."""
    return ([(r.id, r.line_id, r.entry_id, r.match_kind) for r in _active(db, statement_id)],
            [(c.id, c.kind, c.status, c.line_id, c.entry_id, c.candidates, c.context)
             for c in _cases(db, statement_id)])


def _named_on_lines(db, statement_id) -> set:
    """Entry ids an open line case names (its `entry_id` or a candidate's)."""
    cases = [c for c in _cases(db, statement_id, "open") if c.line_id is not None]
    return {c.entry_id for c in cases} | {x.get("entry_id") for c in cases for x in c.candidates or []}


def _split(seed, card, wallet, day, card_amount, wallet_amount, merchant="餐廳"):
    """A cross-account split group (card member claimable, 錢包 member structural) → (group, card member)."""
    group = seed.group("split")
    member = seed.entry(card, card_amount, day=day, merchant=merchant, group_id=group.id)
    seed.entry(wallet, wallet_amount, day=day, merchant=merchant, group_id=group.id)
    return group, member


def test_two_passes_release_a_group_on_the_first_run_and_are_idempotent(seed, db_session, run):
    """Shapes 2/9/10: a −575 standalone entry and the −300/−280 split; lines −575 and −300. First run: pass 1 claims
    −575 for the standalone (group whole under stable holders = none), pass 2 sees that claim → apart → −300 claims
    the member. Second run: identical claims and cases (its own auto rows are never read as holders)."""
    card = _card(seed, db_session)
    wallet = seed.account("錢包")
    day = date(2026, 9, 12)
    other = seed.entry(card, "-575", day=day, merchant="超市")
    _, m1 = _split(seed, card, wallet, day, "-300", "-280")
    db_session.commit()
    stmt, (l575, l300), _ = _submit(db_session, run, card, [_line(1, day, "超市", 575), _line(2, day, "餐廳", 300)])
    assert _claims_of(db_session, stmt.id) == sorted([(l575.id, other.id, None, "auto"),
                                                      (l300.id, m1.id, None, "auto")])
    assert _guard_holders(db_session, stmt) == {}  # shape 10: this statement's own auto rows are not stable
    before = _state(db_session, stmt.id)
    _reconcile(db_session, stmt.id)
    assert _state(db_session, stmt.id) == before


def test_guard_holders_counts_a_neighbouring_auto_row_and_ignores_this_statements_own(seed, db_session, run):
    """Shape 4/10 at the function level: August's −575 line is auto-claimed by a standalone; September prints the
    −300 member line (members Aug 30), a −90 line its own auto row holds and a −120 line the owner holds. After a
    reconcile, `guard_holders(september)` has the August line (exact, not a stand-in) and the owner line, nothing for
    September's own auto rows — and the August holder released the group (the −300 line claims the member)."""
    card = _card(seed, db_session)
    wallet = seed.account("錢包")
    standalone = seed.entry(card, "-575", day=date(2026, 8, 28), merchant="超市")
    db_session.commit()
    august, (aug_line,), _ = _submit(db_session, run, card, [_line(1, date(2026, 8, 28), "超市", 575)],
                                     period=(date(2026, 8, 1), date(2026, 8, 31)))
    assert _claims_of(db_session, august.id) == [(aug_line.id, standalone.id, None, "auto")]
    _, m1 = _split(seed, card, wallet, date(2026, 8, 30), "-300", "-280")
    coffee = seed.entry(card, "-90", day=date(2026, 9, 5), merchant="咖啡")
    owned = seed.entry(card, "-120", day=date(2026, 9, 6), merchant="書店")
    db_session.commit()
    sept, (line, coffee_line, owned_line), _ = _submit(db_session, run, card, [
        _line(1, date(2026, 9, 1), "餐廳", 300), _line(2, date(2026, 9, 5), "咖啡", 90),
        _line(3, date(2026, 9, 6), "書店", 120)])
    _owner_cover(db_session, sept, owned_line, [owned])
    _reconcile(db_session, sept.id)
    assert _claims_of(db_session, sept.id) == sorted([
        (line.id, m1.id, None, "auto"), (coffee_line.id, coffee.id, None, "auto"),
        (owned_line.id, owned.id, None, "manual")])
    assert _guard_holders(db_session, sept) == {aug_line.id: Holder(frozenset({standalone.id}), False, True),
                                                owned_line.id: Holder(frozenset({owned.id}), False, True)}


def test_guard_holders_set_null_row_is_never_exact(seed, db_session, run):
    """Shape 7: owner-cover the −575 line, delete the entry (SET NULL, the snapshot keeps the id) → Holder(exact=False)
    → the group is whole again on reconcile and the member's −300 claim is released."""
    card = _card(seed, db_session)
    wallet = seed.account("錢包")
    day = date(2026, 9, 12)
    held = seed.entry(card, "-575", day=day, merchant="超市")
    _, m1 = _split(seed, card, wallet, day, "-300", "-280")
    db_session.commit()
    stmt, (near, line), _ = _submit(db_session, run, card, [_line(1, day, "超市", 575), _line(2, day, "餐廳", 300)])
    _owner_cover(db_session, stmt, near, [held])
    _reconcile(db_session, stmt.id)
    assert _claims_of(db_session, stmt.id) == sorted([(near.id, held.id, None, "manual"),
                                                      (line.id, m1.id, None, "auto")])
    held_id = held.id
    db_session.delete(held)
    db_session.commit()
    assert _guard_holders(db_session, stmt) == {near.id: Holder(frozenset({held_id}), False, False)}
    _reconcile(db_session, stmt.id)
    assert _claims_of(db_session, stmt.id) == [(near.id, None, None, "manual")]
    assert m1.id not in _named_on_lines(db_session, stmt.id)


def test_guard_holders_a_multi_row_holder_with_one_set_null_row_is_never_exact(seed, db_session, run):
    """Shape 7 on a two-row holder: the owner covers the −575 line with a −575 entry and a 0 entry (exact, the group
    is released and the −300 line claims the member); the 0 entry is deleted (SET NULL). The resolved row alone still
    sums to the line, but an unresolved row makes the holder not exact → the group is whole again and the member's
    claim is released."""
    card = _card(seed, db_session)
    wallet = seed.account("錢包")
    day = date(2026, 9, 12)
    held = seed.entry(card, "-575", day=day, merchant="超市")
    zero = seed.entry(card, "0", day=day, merchant="超市")
    _, m1 = _split(seed, card, wallet, day, "-300", "-280")
    db_session.commit()
    stmt, (near, line), _ = _submit(db_session, run, card, [_line(1, day, "超市", 575), _line(2, day, "餐廳", 300)])
    _owner_cover(db_session, stmt, near, [held, zero])
    _reconcile(db_session, stmt.id)
    owner = sorted([(near.id, held.id, None, "manual"), (near.id, zero.id, None, "manual")])
    assert _claims_of(db_session, stmt.id) == sorted(owner + [(line.id, m1.id, None, "auto")])
    zero_id = zero.id
    db_session.delete(zero)
    db_session.commit()
    assert _guard_holders(db_session, stmt) == {near.id: Holder(frozenset({held.id, zero_id}), False, False)}
    _reconcile(db_session, stmt.id)
    assert {(r.line_id, r.entry_id, r.match_kind) for r in _active(db_session, stmt.id)} == {
        (near.id, held.id, "manual"), (near.id, None, "manual")}
    assert m1.id not in _named_on_lines(db_session, stmt.id)


@pytest.mark.parametrize("other_group", [("-400", "-175"), ("-400", "-170")])
def test_guard_holders_sums_the_rows_of_a_group_reading(seed, db_session, run, other_group):
    """Shape 8: another split group claimed the −575 line as a group reading (two coverage rows, owner-covered so they
    are stable). Flows summing to the line → Holder(exact=True) with both ids and the −300/−280 group is released;
    −400/−170 (sum −570 ≠ −575) → exact=False and the group stays whole (AGENT-254 3). The −170 row is owner-matched
    at −175 and then edited (dirty triggers off): the snapshot conserves, the ledger rows do not."""
    card = _card(seed, db_session)
    wallet = seed.account("錢包")
    day = date(2026, 9, 12)
    _, m1 = _split(seed, card, wallet, day, "-300", "-280")
    other = seed.group("split")
    o1 = seed.entry(card, "-400", day=day, merchant="超市", group_id=other.id)
    o2 = seed.entry(card, "-175", day=day, merchant="超市", group_id=other.id)
    db_session.commit()
    stmt, (near, line), _ = _submit(db_session, run, card, [_line(1, day, "超市", 575), _line(2, day, "餐廳", 300)])
    _owner_cover(db_session, stmt, near, [o1, o2])
    o2.amount = Decimal(other_group[1])
    db_session.commit()
    exact = other_group == ("-400", "-175")
    assert _guard_holders(db_session, stmt) == {near.id: Holder(frozenset({o1.id, o2.id}), False, exact)}
    _reconcile(db_session, stmt.id)
    owner = sorted([(near.id, o1.id, None, "manual"), (near.id, o2.id, None, "manual")])
    assert _claims_of(db_session, stmt.id) == (sorted(owner + [(line.id, m1.id, None, "auto")]) if exact else owner)


@pytest.mark.parametrize("line_foreign, currency, original, exact", [
    ("-15000", "JPY", "-15000", True), ("-15000", "USD", "-15000", False), ("-15000", "JPY", "-14000", False),
    ("0", "JPY", "0", False)])
def test_guard_holders_foreign_exactness_from_the_db(seed, db_session, run, line_foreign, currency, original, exact):
    """AGENT-254 3, DB side: an owner-covered JPY line (−3010 TWD / −15000 JPY) held by a plain entry booked at −3005
    TWD / −15000 JPY → exact=True (foreign rule); the same entry with original_currency USD, or −14000 JPY, → not
    exact; a zero-foreign line (0 JPY) held by a 0 JPY entry → not exact (a zero foreign amount never matches). The
    entry is owner-matched at −3010, then edited to −3005 (dirty off): only the foreign rule can hold."""
    card = _card(seed, db_session)
    day = date(2026, 9, 12)
    held = seed.entry(card, "-3010", day=day, merchant="東京", original_amount=Decimal(original),
                      original_currency=currency)
    db_session.commit()
    stmt, (line,), _ = _submit(db_session, run, card, [
        _line(1, day, "東京", 3010, foreign_amount=Decimal(line_foreign), foreign_currency="JPY")])
    assert line.foreign_amount == Decimal(line_foreign)
    _owner_cover(db_session, stmt, line, [held])
    held.amount = Decimal("-3005")
    db_session.commit()
    assert _guard_holders(db_session, stmt) == {line.id: Holder(frozenset({held.id}), False, exact)}


def test_reconcile_releases_a_jpy_group_held_only_by_a_foreign_exact_holder(seed, db_session, run):
    """Shape 1 end to end in JPY: split −3000/−2800 (−15000/−14000 JPY) on the card; a −5790 TWD / −29100 JPY line
    held by an unrelated entry (−5785 TWD, −29100 JPY, owner-covered: matched at −5790, then edited with dirty off) —
    exact only by the foreign rule, and printing the group's −5800 / −29000 JPY total only near (a −29000 JPY line
    would print it exactly: a tie, whole — §9 errata); member lines −3000/−15000 JPY and −2800/−14000 JPY free → both
    members claimed (`member_alone`) in the first run after the edit, and a second run is identical."""
    card = _card(seed, db_session)
    day = date(2026, 9, 12)
    group = seed.group("split")
    jpy = {"original_currency": "JPY"}
    m1 = seed.entry(card, "-3000", day=day, merchant="東京", group_id=group.id, original_amount=Decimal("-15000"), **jpy)
    m2 = seed.entry(card, "-2800", day=day, merchant="東京", group_id=group.id, original_amount=Decimal("-14000"), **jpy)
    held = seed.entry(card, "-5790", day=day, merchant="大阪", original_amount=Decimal("-29100"), **jpy)
    db_session.commit()
    stmt, (sum_line, l1, l2), _ = _submit(db_session, run, card, [
        _line(1, day, "大阪", 5790, foreign_amount=Decimal("-29100"), foreign_currency="JPY"),
        _line(2, day, "東京", 3000, foreign_amount=Decimal("-15000"), foreign_currency="JPY"),
        _line(3, day, "東京", 2800, foreign_amount=Decimal("-14000"), foreign_currency="JPY")])
    _owner_cover(db_session, stmt, sum_line, [held])
    held.amount = Decimal("-5785")
    db_session.commit()
    _reconcile(db_session, stmt.id)
    assert _claims_of(db_session, stmt.id) == sorted([
        (sum_line.id, held.id, None, "manual"), (l1.id, m1.id, None, "auto"), (l2.id, m2.id, None, "auto")])
    assert {r.match_rule for r in _active(db_session, stmt.id) if r.match_kind == "auto"} == {"exact"}
    before = _state(db_session, stmt.id)
    _reconcile(db_session, stmt.id)
    assert _state(db_session, stmt.id) == before


def test_reconcile_does_not_follow_a_third_order_chain(seed, db_session, run):
    """Shape 13: group A (−300 card / −280 錢包) is released in pass 2 by a standalone −575 claiming the −575 line; A's
    member then claims the −300 line — which is within 5 % of group B's total (−100 card / −205 錢包 = −305). Lines:
    −575, −300, −100. B stays whole in this run (the −300 line was not an own claim in pass 1), so B's −100 member
    does **not** claim the free −100 line (AGENT-254 2: a discriminating line — reading stored pass-2 rows would
    release B and claim it); a second run is identical."""
    card = _card(seed, db_session)
    wallet = seed.account("錢包")
    day = date(2026, 9, 12)
    standalone = seed.entry(card, "-575", day=day, merchant="超市")
    _, a1 = _split(seed, card, wallet, day, "-300", "-280")
    _, b1 = _split(seed, card, wallet, day, "-100", "-205", merchant="咖啡")
    db_session.commit()
    stmt, (l575, l300, l100), _ = _submit(db_session, run, card, [
        _line(1, day, "超市", 575), _line(2, day, "餐廳", 300), _line(3, day, "咖啡", 100)])
    assert _claims_of(db_session, stmt.id) == sorted([
        (l575.id, standalone.id, None, "auto"), (l300.id, a1.id, None, "auto")])
    assert [(c.kind, list(c.candidates or [])) for c in _cases(db_session, stmt.id, "open") if c.line_id == l100.id] \
        == [("line_unmatched", [])]
    assert b1.id not in _named_on_lines(db_session, stmt.id)
    before = _state(db_session, stmt.id)
    _reconcile(db_session, stmt.id)
    assert _state(db_session, stmt.id) == before


def test_a_tie_between_a_pass_one_claimant_and_a_released_member_is_ambiguous_and_stable(seed, db_session, run):
    """Shape 14: standalone −575 claims the −575 line in pass 1 and releases the −300/−280 group; a second standalone
    −300 and the member tie on the −300 line → `ambiguous` in pass 2, no claim; a second run writes the same case."""
    card = _card(seed, db_session)
    wallet = seed.account("錢包")
    day = date(2026, 9, 12)
    standalone = seed.entry(card, "-575", day=day, merchant="超市")
    twin = seed.entry(card, "-300", day=day, merchant="餐廳")
    _, m1 = _split(seed, card, wallet, day, "-300", "-280")
    db_session.commit()
    stmt, (l575, l300), _ = _submit(db_session, run, card, [_line(1, day, "超市", 575), _line(2, day, "餐廳", 300)])
    assert _claims_of(db_session, stmt.id) == [(l575.id, standalone.id, None, "auto")]
    (case,) = [c for c in _cases(db_session, stmt.id, "open") if c.line_id == l300.id]
    assert case.kind == "ambiguous" and {x.get("entry_id") for x in case.candidates} == {twin.id, m1.id}
    before = _state(db_session, stmt.id)
    _reconcile(db_session, stmt.id)
    assert _state(db_session, stmt.id) == before


def test_a_plain_entry_equal_to_the_groups_total_keeps_the_group_whole(seed, db_session, run):
    """Holders spec §9 errata (final review): the −300 card / −280 錢包 split and a plain −580 card entry X; lines
    −580 and −300. Pass 1: the group is whole and cannot compete for the −580 line (its 錢包 member is structural), X
    claims it. Pass 2: X is an unrelated exact holder, but the −580 line prints the group's total exactly — a tie the
    owner resolves, not X's line — so the group stays whole: the −300 line stays line_unmatched with no member
    candidate, and a second run is identical."""
    card = _card(seed, db_session)
    wallet = seed.account("錢包")
    day = date(2026, 9, 12)
    other = seed.entry(card, "-580", day=day, merchant="超市")
    _, m1 = _split(seed, card, wallet, day, "-300", "-280")
    db_session.commit()
    stmt, (l580, l300), _ = _submit(db_session, run, card, [_line(1, day, "超市", 580), _line(2, day, "餐廳", 300)])
    assert _claims_of(db_session, stmt.id) == [(l580.id, other.id, None, "auto")]
    assert [c.kind for c in _cases(db_session, stmt.id, "open") if c.line_id == l300.id] == ["line_unmatched"]
    assert m1.id not in _named_on_lines(db_session, stmt.id)
    before = _state(db_session, stmt.id)
    _reconcile(db_session, stmt.id)
    assert _state(db_session, stmt.id) == before


def test_a_plain_entry_equal_to_the_groups_total_on_the_previous_statement_keeps_the_group_whole(seed, db_session,
                                                                                                    run):
    """The same tie across statements: August's −580 line is auto-claimed by a plain −580 entry X (a stable holder in
    September: exact, not a stand-in, unrelated). The Aug 30 −300/−280 split reaches September, which prints a −300
    line. The August line prints the group's total exactly → whole: the −300 line claims nothing and names no member;
    a second run is identical."""
    card = _card(seed, db_session)
    wallet = seed.account("錢包")
    day = date(2026, 8, 30)
    other = seed.entry(card, "-580", day=day, merchant="超市")
    db_session.commit()
    august, (aug_line,), _ = _submit(db_session, run, card, [_line(1, day, "超市", 580)],
                                     period=(date(2026, 8, 1), date(2026, 8, 31)))
    assert _claims_of(db_session, august.id) == [(aug_line.id, other.id, None, "auto")]
    _, m1 = _split(seed, card, wallet, day, "-300", "-280")
    db_session.commit()
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 1), "餐廳", 300)])
    assert _guard_holders(db_session, stmt) == {aug_line.id: Holder(frozenset({other.id}), False, True)}
    assert _active(db_session, stmt.id) == []
    assert [c.kind for c in _cases(db_session, stmt.id, "open") if c.line_id == line.id] == ["line_unmatched"]
    assert m1.id not in _named_on_lines(db_session, stmt.id)
    before = _state(db_session, stmt.id)
    _reconcile(db_session, stmt.id)
    assert _state(db_session, stmt.id) == before


def test_reconcile_keeps_a_group_whole_when_a_real_stand_in_holds_the_sum_line_on_the_neighbour(seed, db_session, run):
    """Shape 4: August's −580 sum line is held (auto, by August's own run) by a `source="statement"` entry (what the
    policy books) → a stand-in holder, exact but never unrelated → whole in September; the −300 line stays
    line_unmatched with no member candidate."""
    card = _card(seed, db_session)
    wallet = seed.account("錢包")
    day = date(2026, 8, 30)
    stand_in = seed.entry(card, "-580", day=day, merchant="代墊", source="statement")
    db_session.commit()
    august, (aug_line,), _ = _submit(db_session, run, card, [_line(1, day, "代墊", 580)],
                                     period=(date(2026, 8, 1), date(2026, 8, 31)))
    assert _claims_of(db_session, august.id) == [(aug_line.id, stand_in.id, None, "auto")]
    _, m1 = _split(seed, card, wallet, day, "-300", "-280")
    db_session.commit()
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 1), "餐廳", 300)])
    assert _guard_holders(db_session, stmt) == {aug_line.id: Holder(frozenset({stand_in.id}), True, True)}
    assert _active(db_session, stmt.id) == []
    assert [c.kind for c in _cases(db_session, stmt.id, "open") if c.line_id == line.id] == ["line_unmatched"]
    assert m1.id not in _named_on_lines(db_session, stmt.id)


def test_reconcile_keeps_a_group_whole_when_its_fee_member_is_owner_covered(seed, db_session, run):
    """(b) The fee member is owner-matched to its fee line: a structural row, printed alone by that covered line, so
    the −580 reduced reading marks the group whole. The claimable rows lack the fee member, so `_group_variants` has
    no reading (today's behaviour, «AGENT-249 2»): no claim at all, the −580 and −300 lines stay open."""
    card = _card(seed, db_session)
    group = seed.group("split")
    day = date(2026, 9, 12)
    m1 = seed.entry(card, "-300", day=day, merchant="餐廳", group_id=group.id)
    seed.entry(card, "-280", day=day, merchant="餐廳", group_id=group.id)
    fee = seed.entry(card, "-10", kind="fee", day=day, group_id=group.id)
    db_session.commit()
    stmt, (line, sum_line, fee_line), _ = _submit(db_session, run, card, [
        _line(1, day, "餐廳", 300), _line(2, day, "餐廳", 580), _line(3, day, "手續費", 10, "fee")])
    _owner_cover(db_session, stmt, fee_line, [fee])
    _reconcile(db_session, stmt.id)
    assert _claims_of(db_session, stmt.id) == [(fee_line.id, fee.id, None, "manual")]
    open_lines = {c.line_id for c in _cases(db_session, stmt.id, "open")}
    assert {line.id, sum_line.id} <= open_lines
    assert m1.id not in {c.entry_id for c in _cases(db_session, stmt.id, "open") if c.line_id == line.id}


def test_reconcile_claims_the_claimable_children_reading_when_a_child_is_covered_elsewhere(seed, db_session, run):
    """Execution finding (Task 3, spec §3): the +200 child is covered on another statement, the +100 child is not, so
    the claim side reads −300 −280 +100 = −480 while the all-children total is −280. The guard tests the
    claimable-children total too: the group is whole, −480 is claimed with both members and the +100 child, the −300
    line claims nothing; a second reconcile keeps the same rows."""
    card = _card(seed, db_session)
    group = seed.group("split")
    day = date(2026, 9, 12)
    m1 = seed.entry(card, "-300", day=day, merchant="餐廳", group_id=group.id)
    m2 = seed.entry(card, "-280", day=day, merchant="餐廳", group_id=group.id)
    child_100 = seed.entry(card, "100", kind="refund", day=day, parent_entry_id=m1.id)
    child_200 = seed.entry(card, "200", kind="refund", day=day, parent_entry_id=m1.id)
    db_session.commit()
    _cover_elsewhere(db_session, run, card, child_200, date(2026, 10, 3))
    stmt, (line, sum_line), _ = _submit(db_session, run, card, [_line(1, day, "餐廳", 300), _line(2, day, "餐廳", 480)])
    rows = _active(db_session, stmt.id)
    assert sorted((r.line_id, r.entry_id) for r in rows) == sorted(
        (sum_line.id, i) for i in (m1.id, m2.id, child_100.id))
    _reconcile(db_session, stmt.id)
    assert [(r.id, r.line_id, r.entry_id) for r in _active(db_session, stmt.id)] == \
        [(r.id, r.line_id, r.entry_id) for r in rows]


def test_a_scheduled_member_gets_no_structural_row_and_keeps_its_group_whole(seed, db_session, run):
    """Review round 2 (spec §3): the 錢包 member carries a recurring instance, so it gets no structural row: the group
    misses a member and is whole by fail-closed, and the card's member-sized −300 line claims nothing."""
    card = _card(seed, db_session)
    wallet = seed.account("錢包")
    group = seed.group("split")
    day = date(2026, 9, 12)
    card_member = seed.entry(card, "-300", day=day, merchant="餐廳", group_id=group.id)
    scheduled = seed.entry(wallet, "-280", day=day, merchant="餐廳", group_id=group.id)
    definition = seed.definition([seed.line("expense", wallet, "280")], kind="recurring", name="月費")
    seed.instance(definition, 1, day, status="posted", entries=[scheduled])
    db_session.commit()
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, day, "餐廳", 300)])
    entries, groups = rec.load_candidates(db_session, stmt, [card.id], window_days=15, include_reward=False)
    assert [e.id for e in entries] == [card_member.id] and groups[group.id].member_ids == (card_member.id, scheduled.id)
    assert _active(db_session, stmt.id) == []
    _reconcile(db_session, stmt.id)
    assert _active(db_session, stmt.id) == []


def test_a_structural_rows_child_never_supersedes_a_deferral(seed, db_session, run):
    """Review round 2: `evaluated` counts claimable rows' children only, so the matcher's deferral of a child that now
    sits under a structural row (another account's member) keeps its explanation, as an entry that left the
    population does."""
    card = _card(seed, db_session)
    wallet = seed.account("錢包")
    group = seed.group("split")
    day = date(2026, 9, 12)
    seed.entry(card, "-300", day=day, merchant="餐廳", group_id=group.id)
    wallet_member = seed.entry(wallet, "-280", day=day, merchant="餐廳", group_id=group.id)
    child = seed.entry(wallet, "20", kind="refund", day=day, parent_entry_id=wallet_member.id)
    db_session.commit()
    stmt, _, _ = _submit(db_session, run, card, [_line(1, day, "餐廳", 300)])
    deferral = ReconciliationCase(statement_id=stmt.id, revision_id=stmt.current_revision_id, kind="entry_unmatched",
                                  entry_id=child.id, status="resolved", explanation="deferred_next_period",
                                  deferred_to_period_end=date(2026, 10, 30), resolved_at=func.now(), context={})
    db_session.add(deferral)
    db_session.commit()
    _reconcile(db_session, stmt.id)
    assert db_session.get(ReconciliationCase, deferral.id).status == "resolved"


# --- fee children of covered parents: carrier rows and their lock set (fee-child spec 2026-10-24 §5) ---------------

AUG = (date(2026, 8, 1), date(2026, 8, 31))


def _with_currency(db, entry, currency):
    """`entry` stored in `currency` on its own account (a combined account may hold another currency)."""
    entry.currency = currency
    db.flush()
    return entry


def _cover_fee_elsewhere(db, run, account, fee, day) -> None:
    """The fee child `fee` covered on an October statement of `account` by its own fee line (auto coverage)."""
    from app.services.statements import matching
    other, (line,), _ = _submit(db, run, account, [_line(1, day, "手續費", -Decimal(fee.amount), "fee")], period=OCT)
    coverage_service.release_line(db, other, line.id, reason="test_owner")
    coverage_service.write_claim(db, other, line, [matching.Row(fee.id, "principal", Decimal(fee.amount))],
                                 entries_by_id={fee.id: fee}, match_kind="auto", match_rule="exact", run_id=None)
    db.commit()


def _fee_line_cases(db, statement_id, line_id, *statuses):
    return [c for c in _cases(db, statement_id, *statuses) if c.line_id == line_id]


def test_load_candidates_builds_a_carrier_for_a_covered_standalone_parent(seed, db_session, run):
    card = _card(seed, db_session)
    day = date(2026, 9, 16)
    # submitted first: the hook finds no entries, so nothing on this statement claims the rows below
    stmt, ls, _ = _submit(db_session, run, card, [_line(1, day, "AMAZON", 1000), _line(2, day, "手續費", 15, "fee")])
    parent = seed.entry(card, "-1000", day=day, merchant="AMAZON")
    fee = seed.entry(card, "-15", kind="fee", day=day, parent_entry_id=parent.id)
    _with_currency(db_session, seed.entry(card, "-1", kind="fee", day=day, parent_entry_id=parent.id), "USD")
    seed.entry(_card(seed, db_session, "別卡"), "-2", kind="fee", day=day, parent_entry_id=parent.id)  # not participating
    db_session.commit()
    entries, _ = rec.load_candidates(db_session, stmt, [card.id], window_days=15, include_reward=False,
                                     exclude={parent.id})
    (carrier,) = [e for e in entries if e.id == parent.id]
    assert carrier.carrier and carrier.claimable and not carrier.in_reverse and carrier.all_children == ()
    assert carrier.instance is None and carrier.stand_in is False and carrier.transfer_peer_is_card is None
    assert [c.id for c in carrier.children] == [fee.id]  # the USD child and the other account's child are not in it
    # an uncovered parent is an ordinary row, not a carrier
    (plain,) = rec.load_candidates(db_session, stmt, [card.id], window_days=15, include_reward=False)[0]
    assert plain.id == parent.id and not plain.carrier and plain.in_reverse
    # every child claimed elsewhere → no carrier
    _cover_fee_elsewhere(db_session, run, card, fee, date(2026, 10, 3))
    entries, _ = rec.load_candidates(db_session, stmt, [card.id], window_days=15, include_reward=False,
                                     exclude={parent.id})
    assert all(e.id != parent.id for e in entries)


def test_load_candidates_skips_installment_split_member_and_out_of_window_parents(seed, db_session, run):
    card = _card(seed, db_session)
    day = date(2026, 9, 10)
    stmt, _, _ = _submit(db_session, run, card, [_line(1, day, "x", 1)])

    def with_fee(parent):
        return seed.entry(card, "-15", kind="fee", day=parent.entry_date, parent_entry_id=parent.id)

    installment = seed.entry(card, "-1000", day=day, merchant="手機")
    seed.instance(seed.definition([seed.line("expense", card, "1000")], kind="installment", name="手機", times=12),
                  2, day, status="posted", entries=[installment])
    recurring = seed.entry(card, "-280", day=day, merchant="月費")
    seed.instance(seed.definition([seed.line("expense", card, "280")], kind="recurring", name="月費"),
                  1, day, status="posted", entries=[recurring])
    early = seed.entry(card, "-500", day=date(2026, 8, 12), merchant="早")  # 20 days before period_start: outside 15
    group = seed.group("split")
    member = seed.entry(card, "-300", day=day, merchant="餐廳", group_id=group.id)
    seed.entry(card, "-200", day=day, merchant="餐廳", group_id=group.id)  # uncovered: the group is loaded
    kids = {e.id: with_fee(e) for e in (installment, recurring, early, member)}
    db_session.commit()
    covered = {installment.id, recurring.id, early.id, member.id}
    entries, _ = rec.load_candidates(db_session, stmt, [card.id], window_days=15, include_reward=False, exclude=covered)
    by_id = {e.id: e for e in entries}
    assert installment.id not in by_id and early.id not in by_id
    assert by_id[recurring.id].carrier and [c.id for c in by_id[recurring.id].children] == [kids[recurring.id].id]
    assert by_id[recurring.id].instance is None
    # the covered split member stays a structural row as today: never claimable, never a carrier
    assert not by_id[member.id].claimable and not by_id[member.id].carrier
    assert [e.id for e in entries if e.carrier] == [recurring.id]


def _matching_entry(i, children, group_id=None, **kw):
    from app.services.statements import matching as m
    return m.Entry(id=i, kind="expense", flow=Decimal("-1000"), posted_date=date(2026, 9, 16),
                   entry_date=date(2026, 9, 16), name=None, merchant="AMAZON", original_amount=None,
                   original_currency=None, account_id=1, group_id=group_id, parent_entry_id=None, is_settlement=False,
                   refunds_entry_id=None, transfer_group_id=None, transfer_peer_is_card=None, children=tuple(children),
                   instance=None, **kw)


def test_survivors_drop_a_carrier_without_a_locked_child():
    from app.services.statements import matching as m
    kids = (m.Child(21, "fee", Decimal("-15")), m.Child(22, "fee", Decimal("-15")))
    carrier = _matching_entry(20, kids, carrier=True, claimable=True, in_reverse=False)
    plain = _matching_entry(30, ())
    structural = _matching_entry(40, kids, claimable=False, in_reverse=False)
    locked = {22: object(), 30: object()}  # 21 joined after the lock; the carrier's own id is never locked
    groups = {20: None, 30: None, 40: None}
    out = rec._survivors([carrier, plain, structural], locked, groups)
    assert [(e.id, [c.id for c in e.children]) for e in out] == [(20, [22]), (30, []), (40, [21, 22])]
    assert out[2] is structural  # non-claimable rows pass unchanged (both child views)
    assert rec._survivors([carrier], {}, {20: None}) == []  # no locked child → dropped
    assert rec._survivors([plain], {}, {30: None}) == []  # a claimable non-carrier still needs its own lock
    assert rec._survivors([plain], {30: object()}, {}) == []  # … and must have been in the first read
    regrouped = _matching_entry(30, (), group_id=7)
    assert rec._survivors([regrouped], {30: object()}, {30: None}) == []  # its group changed in between


def _owner_matched_fee_month(seed, db_session, run, lines, parents):
    """Submit `lines` first (the hook finds nothing), then book `parents` [(amount, merchant, [child amounts])] and
    hand the purchase lines (the first len(parents)) to the owner, one parent each."""
    card = _card(seed, db_session)
    day = date(2026, 9, 16)
    stmt, ls, _ = _submit(db_session, run, card, lines)
    booked = []
    for amount, merchant, kids in parents:
        parent = seed.entry(card, amount, day=day, merchant=merchant)
        booked.append((parent, [seed.entry(card, k, kind="fee", day=day, parent_entry_id=parent.id) for k in kids]))
    db_session.commit()
    for line, (parent, _) in zip(ls, booked):
        _owner_cover(db_session, stmt, line, [parent])
    return card, stmt, ls, booked


def test_owner_matched_parent_fee_child_claims_the_fee_line_and_is_idempotent(seed, db_session, run):
    day = date(2026, 9, 16)
    _, stmt, ls, [(parent, [fee])] = _owner_matched_fee_month(
        seed, db_session, run, [_line(1, day, "AMAZON", 1000), _line(2, day, "國外交易手續費", 15, "fee")],
        [("-1000", "AMAZON", ["-15"])])
    assert [(c.kind, c.context.get("hint")) for c in _fee_line_cases(db_session, stmt.id, ls[1].id, "open")] == [
        ("line_unmatched", "bank_only")]
    _reconcile(db_session, stmt.id)
    rows = _active(db_session, stmt.id)
    assert sorted((r.line_id, r.entry_id, r.role, r.match_kind, r.match_rule) for r in rows) == [
        (ls[0].id, parent.id, "principal", "manual", "owner"), (ls[1].id, fee.id, "principal", "auto", "exact")]
    assert _fee_line_cases(db_session, stmt.id, ls[1].id, "open") == []
    before = sorted((r.id, r.line_id, r.entry_id) for r in rows)
    cases_before = [(c.id, c.status) for c in _cases(db_session, stmt.id)]
    _reconcile(db_session, stmt.id)
    assert sorted((r.id, r.line_id, r.entry_id) for r in _active(db_session, stmt.id)) == before  # kept, not rewritten
    assert [(c.id, c.status) for c in _cases(db_session, stmt.id)] == cases_before


def test_parent_claimed_on_the_previous_statement_fee_line_on_the_next(seed, db_session, run):
    card = _card(seed, db_session)
    sep, (fee_line,), _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 1), "國外交易手續費", 15, "fee")])
    parent = seed.entry(card, "-1000", day=date(2026, 8, 30), merchant="AMAZON")
    fee = seed.entry(card, "-15", kind="fee", day=date(2026, 8, 30), parent_entry_id=parent.id)
    db_session.commit()
    aug, (buy_line,), _ = _submit(db_session, run, card, [_line(1, date(2026, 8, 30), "AMAZON", 1000)], period=AUG)
    assert [(r.line_id, r.entry_id, r.match_kind) for r in _active(db_session, aug.id)] == [
        (buy_line.id, parent.id, "auto")]
    assert [(c.kind, c.context.get("hint")) for c in _fee_line_cases(db_session, sep.id, fee_line.id, "open")] == [
        ("line_unmatched", "bank_only")]
    _reconcile(db_session, sep.id)
    assert [(r.line_id, r.entry_id, r.role, r.match_kind, r.match_rule) for r in _active(db_session, sep.id)] == [
        (fee_line.id, fee.id, "principal", "auto", "exact")]
    assert _fee_line_cases(db_session, sep.id, fee_line.id, "open") == []
    # the previous statement's claim on the parent is untouched
    assert [(r.line_id, r.entry_id, r.match_kind) for r in _active(db_session, aug.id)] == [
        (buy_line.id, parent.id, "auto")]


def test_two_manual_parents_two_identical_children_two_identical_lines_pair(seed, db_session, run):
    day = date(2026, 9, 16)
    _, stmt, ls, [(p1, [f1]), (p2, [f2])] = _owner_matched_fee_month(
        seed, db_session, run,
        [_line(1, day, "AMAZON", 1000), _line(2, day, "APPLE", 2000), _line(3, day, "國外交易手續費", 15, "fee"),
         _line(4, day, "國外交易手續費", 15, "fee")],
        [("-1000", "AMAZON", ["-15"]), ("-2000", "APPLE", ["-15"])])
    assert [c.context.get("hint") for line in ls[2:] for c in _fee_line_cases(db_session, stmt.id, line.id, "open")] \
        == ["bank_only", "bank_only"]
    _reconcile(db_session, stmt.id)
    rows = _active(db_session, stmt.id)
    assert sorted((r.line_id, r.entry_id, r.match_kind, r.match_rule) for r in rows) == [
        (ls[0].id, p1.id, "manual", "owner"), (ls[1].id, p2.id, "manual", "owner"),
        (ls[2].id, f1.id, "auto", "exact_pair"), (ls[3].id, f2.id, "auto", "exact")]
    coverage_service.assert_conserved(db_session, db_session.get(AccountStatement, stmt.id))
    assert [c for line in ls[2:] for c in _fee_line_cases(db_session, stmt.id, line.id, "open")] == []
    before = sorted((r.id, r.line_id, r.entry_id, r.match_rule) for r in rows)
    _reconcile(db_session, stmt.id)
    assert sorted((r.id, r.line_id, r.entry_id, r.match_rule) for r in _active(db_session, stmt.id)) == before


def test_one_line_two_identical_children_stays_ambiguous(seed, db_session, run):
    day = date(2026, 9, 16)
    _, stmt, ls, [(parent, [f1, f2])] = _owner_matched_fee_month(
        seed, db_session, run, [_line(1, day, "AMAZON", 1000), _line(2, day, "國外交易手續費", 15, "fee")],
        [("-1000", "AMAZON", ["-15", "-15"])])
    _reconcile(db_session, stmt.id)
    (case,) = _fee_line_cases(db_session, stmt.id, ls[1].id, "open")
    assert case.kind == "ambiguous" and sorted(x["entry_id"] for x in case.candidates) == sorted([f1.id, f2.id])
    assert [(r.line_id, r.entry_id) for r in _active(db_session, stmt.id)] == [(ls[0].id, parent.id)]


def test_near_child_is_named_and_a_corrected_child_is_claimed_next_run(seed, db_session, run):
    card = _card(seed, db_session)
    day = date(2026, 9, 16)
    parent = seed.entry(card, "-1000", day=day, merchant="AMAZON")
    fee = seed.entry(card, "-5", kind="fee", day=day, parent_entry_id=parent.id)
    db_session.commit()
    stmt, ls, _ = _submit(db_session, run, card, [_line(1, day, "AMAZON", 1000), _line(2, day, "國外交易手續費", 6, "fee")])
    assert [(r.line_id, r.entry_id, r.match_kind) for r in _active(db_session, stmt.id)] == [
        (ls[0].id, parent.id, "auto")]
    (case,) = _fee_line_cases(db_session, stmt.id, ls[1].id, "open")
    assert (case.kind, case.context) == ("line_unmatched", {
        "hint": "bank_only", "pattern": None,
        "near_children": [{"entry_id": fee.id, "parent_entry_id": parent.id, "residual": "-1.0000"}]})
    db_session.get(LedgerEntry, fee.id).amount = Decimal("-6")  # the owner corrects the child to the bank's amount
    db_session.commit()
    _reconcile(db_session, stmt.id)
    assert sorted((r.line_id, r.entry_id, r.match_rule) for r in _active(db_session, stmt.id)) == [
        (ls[0].id, parent.id, "exact"), (ls[1].id, fee.id, "exact")]
    assert _fee_line_cases(db_session, stmt.id, ls[1].id, "open") == []
    assert db_session.get(ReconciliationCase, case.id).status != "open"


def test_reconcile_locks_a_carriers_child_never_the_carrier(seed, db_session, run, pg_engine):
    day = date(2026, 9, 16)
    _, stmt, ls, [(parent, [fee])] = _owner_matched_fee_month(
        seed, db_session, run, [_line(1, day, "AMAZON", 1000), _line(2, day, "國外交易手續費", 15, "fee")],
        [("-1000", "AMAZON", ["-15"])])
    with pg_engine.connect() as holder:
        # the child is in the lock set: held elsewhere → reconcile_busy, the session stays usable
        holder.execute(text("SELECT id FROM ledger_entry WHERE id = :id FOR UPDATE"), {"id": fee.id})
        with pytest.raises(CodedConflictError) as busy:
            rec.reconcile(db_session, stmt.id)
        assert busy.value.code == rec.RECONCILE_BUSY
        db_session.rollback()
        holder.rollback()
        # the carrier (owner-covered parent) is not: held elsewhere, reconcile still claims its child
        holder.execute(text("SELECT id FROM ledger_entry WHERE id = :id FOR UPDATE"), {"id": parent.id})
        db_session.execute(text("SET LOCAL lock_timeout = '5s'"))  # a wait on the parent fails the test, never hangs
        rec.reconcile(db_session, stmt.id)
        db_session.commit()
        holder.rollback()
    assert sorted((r.line_id, r.entry_id, r.match_kind) for r in _active(db_session, stmt.id)) == [
        (ls[0].id, parent.id, "manual"), (ls[1].id, fee.id, "auto")]


def _owner_match_month(seed, db_session, run, kids):
    """A purchase line and a fee line submitted first (the hook finds nothing), then a parent with `kids` booked; the
    owner matches the purchase line's `line_unmatched` case to the parent through the action engine."""
    from app.services.reconciliation_actions import engine
    card = _card(seed, db_session)
    day = date(2026, 9, 16)
    stmt, ls, _ = _submit(db_session, run, card, [_line(1, day, "AMAZON", 1000), _line(2, day, "國外交易手續費", 15, "fee")])
    parent = seed.entry(card, "-1000", day=day, merchant="AMAZON")
    children = [seed.entry(card, k, kind="fee", day=day, parent_entry_id=parent.id) for k in kids]
    db_session.commit()
    assert stmt.id not in rec.pending_statement_ids(db_session)
    (case,) = [c for c in _fee_line_cases(db_session, stmt.id, ls[0].id, "open") if c.kind == "line_unmatched"]
    engine.apply_action(db_session, case_id=case.id, action="match",
                        params={"line_id": ls[0].id, "entry_id": parent.id}, actor="owner", label="t")
    db_session.commit()
    return db_session.get(AccountStatement, stmt.id), ls, parent, children


def test_owner_match_of_a_parent_with_a_fee_child_schedules_a_recheck_that_claims_the_fee_line(seed, db_session, run):
    stmt, ls, parent, [fee] = _owner_match_month(seed, db_session, run, ["-15"])
    assert stmt.needs_recheck is True  # the fee child is still claimable: a matching reason (ruling 29)
    assert stmt.id in rec.pending_statement_ids(db_session)
    _reconcile(db_session, stmt.id)
    assert sorted((r.line_id, r.entry_id, r.match_kind, r.match_rule) for r in _active(db_session, stmt.id)) == [
        (ls[0].id, parent.id, "manual", "owner"), (ls[1].id, fee.id, "auto", "exact")]
    assert db_session.get(AccountStatement, stmt.id).needs_recheck is False
    assert stmt.id not in rec.pending_statement_ids(db_session)


def test_owner_match_with_children_takes_the_fee_child_and_does_not_flag_a_recheck(seed, db_session, run):
    from app.services.reconciliation_actions import engine
    card = _card(seed, db_session)
    day = date(2026, 9, 16)
    stmt, ls, _ = _submit(db_session, run, card, [_line(1, day, "AMAZON", 1015)])
    parent = seed.entry(card, "-1000", day=day, merchant="AMAZON")
    seed.entry(card, "-15", kind="fee", day=day, parent_entry_id=parent.id)
    db_session.commit()
    (case,) = [c for c in _fee_line_cases(db_session, stmt.id, ls[0].id, "open") if c.kind == "line_unmatched"]
    engine.apply_action(db_session, case_id=case.id, action="match",
                        params={"line_id": ls[0].id, "entry_id": parent.id, "with_children": True}, actor="owner", label="t")
    db_session.commit()
    assert db_session.get(AccountStatement, stmt.id).needs_recheck is False


def test_owner_match_of_a_parent_whose_only_child_is_covered_by_another_statement_does_not_flag_a_recheck(seed, db_session, run):
    from app.services.reconciliation_actions import engine
    card = _card(seed, db_session)
    day = date(2026, 9, 16)
    stmt, ls, _ = _submit(db_session, run, card, [_line(1, day, "AMAZON", 1000)])
    parent = seed.entry(card, "-1000", day=day, merchant="AMAZON")
    fee = seed.entry(card, "-15", kind="fee", day=day, parent_entry_id=parent.id)
    db_session.commit()
    _cover_fee_elsewhere(db_session, run, card, fee, date(2026, 10, 2))
    (case,) = [c for c in _fee_line_cases(db_session, stmt.id, ls[0].id, "open") if c.kind == "line_unmatched"]
    engine.apply_action(db_session, case_id=case.id, action="match",
                        params={"line_id": ls[0].id, "entry_id": parent.id}, actor="owner", label="t")
    db_session.commit()
    assert db_session.get(AccountStatement, stmt.id).needs_recheck is False


def test_owner_match_of_a_parent_whose_only_child_is_in_another_currency_does_not_flag_a_recheck(seed, db_session, run):
    """AGENT-275 finding 2: the flag applies the loader's claimable-child filter — a child the next reconcile cannot
    load (other currency / non-participating account) is no matching reason."""
    from app.services.reconciliation_actions import engine
    card = _card(seed, db_session)
    day = date(2026, 9, 16)
    stmt, ls, _ = _submit(db_session, run, card, [_line(1, day, "AMAZON", 1000)])
    parent = seed.entry(card, "-1000", day=day, merchant="AMAZON")
    fee = seed.entry(card, "-0.50", kind="fee", day=day, parent_entry_id=parent.id)
    fee.currency = "USD"
    db_session.commit()
    (case,) = [c for c in _fee_line_cases(db_session, stmt.id, ls[0].id, "open") if c.kind == "line_unmatched"]
    engine.apply_action(db_session, case_id=case.id, action="match",
                        params={"line_id": ls[0].id, "entry_id": parent.id}, actor="owner", label="t")
    db_session.commit()
    assert db_session.get(AccountStatement, stmt.id).needs_recheck is False


def test_owner_match_of_a_childless_entry_does_not_flag_a_recheck(seed, db_session, run):
    stmt, ls, parent, _ = _owner_match_month(seed, db_session, run, [])
    assert stmt.needs_recheck is False
    assert stmt.id not in rec.pending_statement_ids(db_session)
    assert [(r.line_id, r.entry_id, r.match_kind) for r in _active(db_session, stmt.id)] == [
        (ls[0].id, parent.id, "manual")]


def test_a_carrier_never_supersedes_its_parents_deferral(seed, db_session, run):
    """A parent this statement deferred and the next statement then covered becomes a carrier here (its fee child is
    unclaimed): the carrier is never evaluated as a parent, so the deferral keeps its explanation, as an entry that
    left the population does."""
    card = _card(seed, db_session)
    day = date(2026, 9, 29)
    stmt, _, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 3), "全聯", 580)])
    parent = seed.entry(card, "-90", day=day, merchant="咖啡")
    seed.entry(card, "-3", kind="fee", day=day, parent_entry_id=parent.id)
    db_session.commit()
    deferral = ReconciliationCase(statement_id=stmt.id, revision_id=stmt.current_revision_id, kind="entry_unmatched",
                                  entry_id=parent.id, status="resolved", explanation="deferred_next_period",
                                  deferred_to_period_end=date(2026, 10, 30), resolved_at=func.now(), context={})
    db_session.add(deferral)
    db_session.commit()
    octo, (late_line,), _ = _submit(db_session, run, card, [_line(1, date(2026, 10, 2), "咖啡", 90)], period=OCT)
    assert [(r.line_id, r.entry_id) for r in _active(db_session, octo.id)] == [(late_line.id, parent.id)]
    entries, _ = rec.load_candidates(db_session, db_session.get(AccountStatement, stmt.id), [card.id], window_days=15,
                                     include_reward=False)
    assert [e.id for e in entries if e.carrier] == [parent.id]
    _reconcile(db_session, stmt.id)
    assert db_session.get(ReconciliationCase, deferral.id).status == "resolved"
