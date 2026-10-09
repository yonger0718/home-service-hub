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

from app.models import AccountStatement, CoverageDirty, ReconciliationCase, StatementCoverage, StatementLine
from app.schemas.statements import LineIn, RevisionIn
from app.schemas.writes import EntryUpdateIn
from app.services import entry_write_service as ews
from app.services import reconciliation_service as rec
from app.services import settings_service
from app.services import statement_ingest_service as ing
from app.services import statement_revision_service as revs
from tests.helpers import race, set_dirty, wait_until_blocked

SEP = (date(2026, 9, 1), date(2026, 9, 30))
OCT = (date(2026, 10, 1), date(2026, 10, 31))


@pytest.fixture
def run(db_session):
    r = ing.create_worker_run(db_session, trigger="timer", initiator_hint=None, mode="live")
    r, token = ing.claim_run(db_session, r.id, label="worker")
    return r, token


def _line(seq, day, merchant, printed, kind="purchase", **extra) -> LineIn:
    return LineIn(seq=seq, posted_date=day, merchant_raw=merchant, printed_amount=Decimal(str(printed)), line_kind=kind,
                  **extra)


def _flow(kind: str, line: LineIn) -> Decimal:
    from app.services.statements.derive import flow_amount
    return flow_amount(kind, line.line_kind, line.printed_amount)


def _submit(db, run, account, lines, *, kind="card", period=SEP, opening=None, total=None):
    """Submit a revision whose header passes the guardrails (unless `total` overrides it); the hook reconciles."""
    r, token = run
    flows = sum((_flow(kind, line) for line in lines), Decimal(0))
    if kind == "card":
        expected = Decimal(opening or 0) - flows
    else:
        opening = Decimal(opening if opening is not None else 0)
        expected = opening + flows
    rev = RevisionIn(run_id=r.id, lease_token=token, file_id=None, account_id=account.id, kind=kind, parser="t",
                     parser_version="1", currency=account.currency, period_start=period[0], period_end=period[1],
                     opening_balance=opening, statement_total=expected if total is None else Decimal(total),
                     lines=lines, raw={})
    res = revs.submit_revision(db, r, rev, account_map={})
    db.commit()
    lines = db.query(StatementLine).filter_by(revision_id=res.revision.id).order_by(StatementLine.seq).all()
    return db.get(AccountStatement, res.statement.id), lines, res


def _active(db, statement_id):
    db.expire_all()
    return db.execute(select(StatementCoverage).where(StatementCoverage.statement_id == statement_id,
                                                      StatementCoverage.status == "active")
                      .order_by(StatementCoverage.id)).scalars().all()


def _cases(db, statement_id, *statuses):
    db.expire_all()
    q = select(ReconciliationCase).where(ReconciliationCase.statement_id == statement_id)
    if statuses:
        q = q.where(ReconciliationCase.status.in_(statuses))
    return db.execute(q.order_by(ReconciliationCase.id)).scalars().all()


def _card(seed, db, name="卡", *, live=True, **extra):
    extra.setdefault("closing_day", 30)
    card = seed.account(name, is_credit=True, statement_live_from=date(2026, 9, 1) if live else None, **extra)
    db.commit()
    return card


def _transfer(seed, source, target, amount, day):
    pair = uuid.uuid4()
    out_leg = seed.entry(source, f"-{amount}", kind="transfer_out", day=day, transfer_group_id=pair)
    in_leg = seed.entry(target, str(amount), kind="transfer_in", day=day, transfer_group_id=pair)
    return out_leg, in_leg


def _batch(db, **kw) -> list[dict]:
    """The Task 7 route's loop: one commit per yielded statement (reconcile_each never commits)."""
    out = []
    for item in rec.reconcile_each(db, rec.pending_statement_ids(db), **kw):
        db.commit()
        out.append(item)
    return out


def _reconcile(db, statement_id, **kw):
    result = rec.reconcile(db, statement_id, **kw)
    db.commit()
    return result


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
    ])
    delta = next(c for c in open_cases if c.kind == "amount_delta")
    assert delta.context["fee_matches"] is True and delta.context["fee_expected"] == "15"
    assert delta.event_id == ls[4].event_id and delta.revision_id == stmt.current_revision_id
    lone = next(c for c in open_cases if c.entry_id == e["lone"].id)
    assert lone.context == {} and lone.event_id is None
    deferred = _cases(db_session, stmt.id, "resolved")
    assert [(c.kind, c.entry_id, c.explanation, c.resolved_by, c.deferred_to_period_end, c.deferred_to_statement_id)
            for c in deferred] == [("entry_unmatched", e["late"].id, "deferred_next_period", None, date(2026, 10, 30), None)]
    assert (stmt.matched_count, stmt.explained_count, stmt.open_case_count, stmt.needs_recheck) == (4, 1, 4, False)
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
    assert (stmt.matched_count, stmt.explained_count, stmt.open_case_count) == (4, 1, 4)


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
    stmt = db_session.get(AccountStatement, stmt.id)
    assert (stmt.matched_count, stmt.open_case_count) == (5, 2)
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
        ("entry_unmatched", fee.id, {"hint": "uncovered_child"})]


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
    (case,) = _cases(db_session, stmt.id, "open")
    stmt, new_lines, res = _submit(db_session, run, card, lines)
    assert [(r.id, r.entry_id) for r in _active(db_session, stmt.id)] == rows
    assert _active(db_session, stmt.id)[0].line_id == new_lines[0].id
    (again,) = _cases(db_session, stmt.id, "open")
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
    assert rules.period_end == date(2026, 9, 30) and rules.margin == Decimal("0.15")


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
    (case,) = _cases(db_session, stmt.id, "open")
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


def test_rules_defaults_in_settings(db_session):
    settings = settings_service.get_reconciliation_settings(db_session)
    assert settings["rules_version"] == "r1b-1"
    assert settings["rules"]["accept"] == "0.80" and settings["rules"]["candidate_window_days"] == 10
    assert "period_end" not in settings["rules"] and "年費" in settings["rules"]["bank_only_patterns"]


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
    assert stmt.open_case_count == 1
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
    assert {c.kind for c in _cases(db_session, stmt.id, "open")} == {"amount_delta", "entry_unmatched"}


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
    assert {c.kind for c in _cases(db_session, stmt.id, "open")} == {"amount_delta", "entry_unmatched"}
