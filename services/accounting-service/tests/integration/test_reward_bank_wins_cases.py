"""Bank wins owner cases (reward engine spec 7.2, 7.5, 7.8; plan P5, P6): surplus and early reward lines, the
`accept_early_reward` action and its accepted map, and the one-cycle balance gap.

Fictional card 「測試卡」 closing on the 15th; the statement under test covers 10-16..11-15 and the rule posts
5 days after each purchase, so a purchase on 11-12 posts on 11-17, in the next cycle (an early candidate).
Synthetic data only."""

import time
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.models import (
    Account, AccountStatement, LedgerEntry, ReconciliationAction, ReconciliationCase, ReconciliationOwnerRule,
    RewardAccrual, StatementCoverage,
)
from app.schemas.writes import AccountIn, RewardRuleUpdateIn
from app.services import inbox_service, owner_rule_service, policy_service, reconciliation_actions
from app.services import reward_bank_wins, reward_post_job, reward_rule_service, settings_service
from app.services.errors import RETRY, CodedConflictError, ValidationError
from app.services.reconciliation_actions import engine
from tests.integration.statement_fixtures import _cases, _line, _reconcile, _submit, run  # noqa: F401
from tests.integration.test_reward_bank_wins import (
    PERIOD, SHOP, _accrual_of, _active, _by_line, _post, _reward_line, _rewards, _rule, _spend, _test_card,
)

NEXT = (date(2026, 11, 16), date(2026, 12, 15))
EARLY_DAY = date(2026, 11, 12)  # posts 11-17: after this period, inside the next cycle
REWARD_DAY = date(2026, 11, 14)
NOTE = "銀行提早入帳：請修正規則的入帳日，或按「接受提早入帳」；若是促銷等另一筆回饋，按「記成另一筆回饋」（不會扣掉原本的預估回饋，若其實是提早入帳會重複計算）"
SURPLUS_NOTE = "銀行多給了一筆回饋：若是促銷等另一筆回饋，按「記成另一筆回饋」"
SYSTEM_ON = {"enabled": True, "actions": {"create_system_entry": {"enabled": True}}}


# --- helpers -----------------------------------------------------------------------------------------------------

def _open_on(db, statement_id, line) -> list[ReconciliationCase]:
    return [c for c in _cases(db, statement_id, "open") if c.event_id == line.event_id]


def _one_open(db, statement_id, line) -> ReconciliationCase:
    (case,) = _open_on(db, statement_id, line)
    return case


def _accept(db, case, **params) -> ReconciliationAction:
    row, replayed = engine.apply_action(db, case_id=case.id, action="accept_early_reward", params=params,
                                        actor="owner", label="spa")
    assert not replayed
    return row


def _set_policy(db, policy: dict) -> None:
    current = settings_service.get_reconciliation_settings(db)
    settings_service.update_reconciliation_settings(db, {"dirty_enabled": current["dirty_enabled"], "policy": policy})
    db.commit()


def _actions(db) -> list[ReconciliationAction]:
    db.expire_all()
    return list(db.scalars(select(ReconciliationAction).order_by(ReconciliationAction.id)))


def _policy_case_ids(db, statement_id) -> set[int]:
    statement = db.get(AccountStatement, statement_id)
    found = policy_service.collect_candidates(db, statement, settings_service.reconciliation_policy(db))
    return {c.case_id for c in found}


def _learned_record_rule(seed, db, card, line) -> ReconciliationOwnerRule:
    """A learned owner `record` rule for the reward line's merchant (any amount)."""
    category = seed.category("測試收入", kind="income")
    row = ReconciliationOwnerRule(account_id=card.id, line_kind="reward",
                                  merchant_pattern=inbox_service.pattern(line.merchant_norm), flow_sign="+",
                                  amount=None, action="record", params={"category_id": category.id})
    db.add(row)
    db.commit()
    return row


def _inbox_item(db, case_id) -> dict:
    inbox = inbox_service.build_inbox(db)
    db.commit()
    (item,) = [i for a in inbox["accounts"] for i in a["items"] if i["case_id"] == case_id]
    return item


def _early_statement(db_session, run, card, *, reward=Decimal("9"), day=REWARD_DAY):
    """One early accrual (estimate 10, posts 11-17) and a statement printing its purchase and an early reward line."""
    rule = _rule(db_session, card)
    buy = _spend(db_session, card, 1000, EARLY_DAY, rule)
    db_session.commit()
    stmt, (_, line), _ = _submit(db_session, run, card, [_line(1, EARLY_DAY, SHOP, 1000),
                                                       _reward_line(2, reward, day)], period=PERIOD)
    return rule, buy, stmt, line


# --- surplus -----------------------------------------------------------------------------------------------------

def test_surplus_line_becomes_owner_case(seed, db_session, run, dirty_on, today):
    """Review Focus 3: two reward lines, one engine row → one claim, one `reward_surplus` case (question `other`);
    neither a learned record rule nor the policy books it."""
    today(date(2026, 11, 20))
    card = _test_card(seed, db_session)
    rule = _rule(db_session, card)
    buy = _spend(db_session, card, 1000, date(2026, 10, 20), rule)
    db_session.commit()
    row = _post(db_session, buy)
    stmt, (_, first, second), _ = _submit(db_session, run, card, [
        _line(1, date(2026, 10, 20), SHOP, 1000), _reward_line(2, 9), _reward_line(3, 5)], period=PERIOD)

    claims = _by_line(db_session, stmt.id)
    assert [c.entry_id for c in claims[first.id]] == [row.id] and second.id not in claims
    case = _one_open(db_session, stmt.id, second)
    assert (case.kind, case.context) == ("line_unmatched", {"hint": "reward_surplus", "note": SURPLUS_NOTE})
    item = _inbox_item(db_session, case.id)
    assert (item["question"], item["actions"]) == ("other", ["record_reward", "dismiss"])

    _learned_record_rule(seed, db_session, card, second)
    owner_rule_service.apply_rules(db_session, stmt.id)
    _set_policy(db_session, SYSTEM_ON)
    policy_service.run_policy(db_session, stmt.id)
    assert _actions(db_session) == []
    assert db_session.get(ReconciliationCase, case.id).status == "open"
    assert [r.id for r in _rewards(db_session)] == [row.id]


def test_extra_line_offers_early_when_early_candidates_exist(seed, db_session, run):
    """#295 (M13): with in-period candidates, an extra reward line becomes a `reward_early` case (accept offered) when
    the card also has early candidates, instead of a `reward_surplus` case without accept."""
    card = _test_card(seed, db_session)
    rule = _rule(db_session, card)
    _spend(db_session, card, 1000, date(2026, 10, 20), rule)  # posts 10-25: in the period (posted early)
    _early_buy = _spend(db_session, card, 2000, EARLY_DAY, rule)  # posts 11-17: an early candidate
    db_session.commit()
    stmt, (_, _, first, second), _ = _submit(db_session, run, card, [
        _line(1, date(2026, 10, 20), SHOP, 1000), _line(2, EARLY_DAY, SHOP, 2000),
        _reward_line(3, 10), _reward_line(4, 20)], period=PERIOD)

    assert first.id in _by_line(db_session, stmt.id)
    case = _one_open(db_session, stmt.id, second)
    early = _accrual_of(db_session, _early_buy)
    assert case.context["hint"] == "reward_early" and case.context["default_accrual_ids"] == [early.id]
    assert "accept_early_reward" in reconciliation_actions.allowed(case)

    _accept(db_session, db_session.get(ReconciliationCase, case.id))
    assert _accrual_of(db_session, _early_buy).state == "bank_confirmed"
    assert _open_on(db_session, stmt.id, second) == []


# --- early -------------------------------------------------------------------------------------------------------

def test_early_credit_is_owner_case_not_bank_only(seed, db_session, run, dirty_on, today):
    today(date(2026, 11, 20))
    card = _test_card(seed, db_session)
    rule, buy, stmt, line = _early_statement(db_session, run, card)
    accrual = _accrual_of(db_session, buy)
    assert (accrual.state, accrual.post_on) == ("estimated", date(2026, 11, 17))

    case = _one_open(db_session, stmt.id, line)
    assert (case.kind, case.context) == ("line_unmatched", {
        "hint": "reward_early", "early_accrual_ids": [accrual.id], "rule_ids": [rule.id],
        "default_accrual_ids": [accrual.id], "note": NOTE})
    assert reconciliation_actions.allowed(case) == ["create_system_entry", "accept_early_reward", "dismiss"]
    item = _inbox_item(db_session, case.id)
    assert (item["question"], item["actions"], item["note"]) == ("other", ["accept_early", "record_reward", "dismiss"],
                                                                   NOTE)

    _set_policy(db_session, SYSTEM_ON)
    assert case.id not in _policy_case_ids(db_session, stmt.id)
    policy_service.run_policy(db_session, stmt.id)
    assert _actions(db_session) == [] and _rewards(db_session) == []


def test_no_early_candidates_still_bank_only(seed, db_session, run, dirty_on, today):
    """Unchanged behaviour (control for the cases above): no accrual at all → `bank_only`, the policy books it."""
    today(date(2026, 11, 20))
    card = _test_card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_reward_line(1, 9)], period=PERIOD)
    case = _one_open(db_session, stmt.id, line)
    assert case.context.get("hint") == "bank_only"
    assert "create_system_entry" in reconciliation_actions.allowed(case)
    assert _inbox_item(db_session, case.id)["question"] == "bank_only"
    assert reward_bank_wins.early_candidates(db_session, db_session.get(AccountStatement, stmt.id), [card.id]) == []

    _set_policy(db_session, SYSTEM_ON)
    assert case.id in _policy_case_ids(db_session, stmt.id)
    policy_service.run_policy(db_session, stmt.id)
    (action,) = _actions(db_session)
    assert (action.case_id, action.action) == (case.id, "create_system_entry")


def test_learned_rule_books_a_bank_only_reward_line(seed, db_session, run):
    """Control for the owner-rule assertions: the same learned rule does book a plain `bank_only` reward line."""
    card = _test_card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_reward_line(1, 9)], period=PERIOD)
    case = _one_open(db_session, stmt.id, line)
    _learned_record_rule(seed, db_session, card, line)
    owner_rule_service.apply_rules(db_session, stmt.id)
    (action,) = _actions(db_session)
    assert (action.case_id, action.action) == (case.id, "create_entry")


def test_early_candidates_window(seed, db_session, run):
    """`early_candidates`: estimated, `post_on` in (period_end, end of the next cycle]."""
    card = _test_card(seed, db_session)
    rule = _rule(db_session, card)
    in_period = _spend(db_session, card, 1000, date(2026, 11, 10), rule)  # 11-15: the period's last day
    early = _spend(db_session, card, 1000, EARLY_DAY, rule)  # 11-17
    last = _spend(db_session, card, 1000, date(2026, 12, 10), rule)  # 12-15: the next cycle's last day
    _spend(db_session, card, 1000, date(2026, 12, 11), rule)  # 12-16: two cycles out
    posted = _spend(db_session, card, 1000, date(2026, 11, 13), rule)  # 11-18, but posted
    db_session.commit()
    _post(db_session, posted)
    stmt, _, _ = _submit(db_session, run, card, [], period=PERIOD)
    statement = db_session.get(AccountStatement, stmt.id)
    assert _accrual_of(db_session, in_period).post_on == date(2026, 11, 15)
    # fix round 1 ruling: a posted, unconfirmed early accrual is still early (the job posted it on its formula date)
    assert reward_bank_wins.early_candidates(db_session, statement, [card.id]) == [
        _accrual_of(db_session, early).id, _accrual_of(db_session, last).id, _accrual_of(db_session, posted).id]
    confirmed = _accrual_of(db_session, posted)
    confirmed.state, confirmed.confirmed_statement_id = "bank_confirmed", stmt.id
    db_session.commit()
    assert reward_bank_wins.early_candidates(db_session, statement, [card.id]) == [
        _accrual_of(db_session, early).id, _accrual_of(db_session, last).id]


# --- accept_early_reward -----------------------------------------------------------------------------------------

def test_accept_early_reward_books_once(seed, db_session, run, pg_engine):
    card = _test_card(seed, db_session)
    _, buy, stmt, line = _early_statement(db_session, run, card)
    case = _one_open(db_session, stmt.id, line)
    accrual_id = _accrual_of(db_session, buy).id

    action = _accept(db_session, case, accrual_ids=[accrual_id])
    # in the action's own transaction: posted on the line's date, then confirmed at the bank figure
    accrual = db_session.get(RewardAccrual, accrual_id)
    assert (accrual.state, accrual.post_on, accrual.confirmed_statement_id) == ("bank_confirmed", REWARD_DAY,
                                                                                stmt.id)
    row = db_session.get(LedgerEntry, accrual.ledger_entry_id)
    assert (row.amount, row.posted_date) == (Decimal("9.0000"), REWARD_DAY)
    (claim,) = db_session.scalars(select(StatementCoverage).where(
        StatementCoverage.line_id == line.id, StatementCoverage.status == "active")).all()
    assert (claim.entry_id, claim.match_rule) == (row.id, "reward_engine")
    db_session.commit()

    case = db_session.get(ReconciliationCase, case.id)
    assert (case.status, case.resolved_action_id, case.version) == ("resolved", action.id, 1)
    assert action.params == {"accrual_ids": [accrual_id]}
    assert [r.id for r in _rewards(db_session)] == [row.id]

    report = reward_post_job.run(pg_engine, "manual", today=date(2027, 1, 10))
    assert (report["posted"], report["forfeited"]) == (0, 0)
    assert [r.id for r in _rewards(db_session)] == [row.id]


def test_accept_is_not_revertible(seed, db_session, run):
    card = _test_card(seed, db_session)
    _, buy, stmt, line = _early_statement(db_session, run, card)
    action = _accept(db_session, _one_open(db_session, stmt.id, line))
    db_session.commit()
    with pytest.raises(CodedConflictError) as info:
        engine.revert_action(db_session, action_id=action.id, actor="owner", label="spa")
    assert info.value.code == "not_revertible"
    db_session.rollback()
    assert _actions(db_session)[-1].status == "applied"


def test_accept_locks_accepted_window_in_the_single_call(seed, db_session, run, monkeypatch):
    """Controller decision 1: the accepted accrual's window joins bank wins' ONE `lock_windows` call (with the
    in-period candidate's), and its purchase is in the tier-1 purchase lock taken before it."""
    card = _test_card(seed, db_session)
    rule = _rule(db_session, card)
    other_rule = _rule(db_session, card, name="測試回饋二")  # its own window: the accepted pair is distinct
    current = _spend(db_session, card, 1000, date(2026, 10, 20), rule)  # in-period candidate
    early = _spend(db_session, card, 3000, EARLY_DAY, other_rule)
    db_session.commit()
    stmt, (_, _, kept, line), _ = _submit(db_session, run, card, [
        _line(1, date(2026, 10, 20), SHOP, 1000), _line(2, EARLY_DAY, SHOP, 3000), _reward_line(3, 10),
        _reward_line(4, 30)], period=PERIOD)
    case = _one_open(db_session, stmt.id, line)
    assert case.context["hint"] == "reward_early"  # #295 (M13): the card has an early candidate

    # the accepted map naming the early accrual for the second line (as an applied accept_early_reward would)
    calls: list[set] = []
    real = reward_bank_wins.reward_engine.lock_windows

    def spy(db, pairs, **kwargs):
        calls.append(set(pairs))
        return real(db, pairs, **kwargs)

    monkeypatch.setattr(reward_bank_wins.reward_engine, "lock_windows", spy)
    early_accrual = _accrual_of(db_session, early)
    current_accrual = _accrual_of(db_session, current)
    statement = db_session.get(AccountStatement, stmt.id)
    monkeypatch.setattr(reward_bank_wins, "accepted_map", lambda db, s: {line.id: [early_accrual.id]})
    plan = reward_bank_wins.prepare(db_session, statement, [card.id], [kept, line], set())
    assert len(calls) == 1
    assert calls[0] == {(early_accrual.rule_id, early_accrual.window_start),
                        (current_accrual.rule_id, current_accrual.window_start)}
    early_accrual = db_session.get(RewardAccrual, early_accrual.id)
    assert (early_accrual.state, early_accrual.post_on) == ("bank_confirmed", line.posted_date)  # the line's date
    assert {c.line_id for c in plan.claims} == {kept.id, line.id} and plan.surplus_line_ids == []
    db_session.rollback()


def test_accept_busy_reconcile_refuses_the_action(seed, db_session, run, pg_engine):
    """Controller decision 2: a `reconcile_busy` inside the action is the action's error; nothing is written."""
    card = _test_card(seed, db_session)
    _, buy, stmt, line = _early_statement(db_session, run, card)
    case = _one_open(db_session, stmt.id, line)
    with pg_engine.connect() as other:
        other.execute(select(LedgerEntry.id).where(LedgerEntry.id == buy.id).with_for_update())
        with pytest.raises(CodedConflictError) as info:
            _accept(db_session, case)
        assert info.value.code == "reconcile_busy"
        db_session.rollback()
        other.rollback()
    assert _actions(db_session) == [] and _rewards(db_session) == []
    assert db_session.get(ReconciliationCase, case.id).status == "open"
    assert _accrual_of(db_session, buy).state == "estimated"


def test_accept_rerun_keeps_claim(seed, db_session, run):
    """Lead carry (AGENT-395): a later reconcile keeps the same `reward_engine` coverage rows, posts nothing new and
    changes no ledger amount."""
    card = _test_card(seed, db_session)
    _, buy, stmt, line = _early_statement(db_session, run, card)
    _accept(db_session, _one_open(db_session, stmt.id, line))
    db_session.commit()
    before = [(c.id, c.entry_id, c.match_rule) for c in _active(db_session, stmt.id) if c.line_id == line.id]
    amounts = [(r.id, r.amount, r.posted_date) for r in _rewards(db_session)]

    _reconcile(db_session, stmt.id)
    _reconcile(db_session, stmt.id)

    assert [(c.id, c.entry_id, c.match_rule) for c in _active(db_session, stmt.id) if c.line_id == line.id] == before
    assert before and before[0][2] == "reward_engine"
    assert [(r.id, r.amount, r.posted_date) for r in _rewards(db_session)] == amounts
    accrual = _accrual_of(db_session, buy)
    assert (accrual.state, accrual.post_on, accrual.confirmed_statement_id) == ("bank_confirmed", REWARD_DAY, stmt.id)
    assert _open_on(db_session, stmt.id, line) == []


def test_accept_names_one_of_two(seed, db_session, run):
    """Re-check A: two next-cycle accruals, one line; the default names the closer estimate. The other stays
    `estimated` at its formula `post_on` and is a candidate (a) of the next statement. No system entry, no
    `bank_only` case on either statement."""
    card = _test_card(seed, db_session)
    rule = _rule(db_session, card)
    near = _spend(db_session, card, 1000, EARLY_DAY, rule)  # estimate 10, posts 11-17
    far = _spend(db_session, card, 2000, date(2026, 11, 13), rule)  # estimate 20, posts 11-18
    db_session.commit()
    stmt, (_, _, line), _ = _submit(db_session, run, card, [
        _line(1, EARLY_DAY, SHOP, 1000), _line(2, date(2026, 11, 13), SHOP, 2000), _reward_line(3, 10)],
        period=PERIOD)
    case = _one_open(db_session, stmt.id, line)
    near_id, far_id = _accrual_of(db_session, near).id, _accrual_of(db_session, far).id
    assert case.context["early_accrual_ids"] == [near_id, far_id]
    assert case.context["default_accrual_ids"] == [near_id]

    action = _accept(db_session, case)  # the default
    db_session.commit()
    assert action.params == {"accrual_ids": [near_id]}
    assert _accrual_of(db_session, near).state == "bank_confirmed"
    other = _accrual_of(db_session, far)
    assert (other.state, other.post_on, other.ledger_entry_id) == ("estimated", date(2026, 11, 18), None)

    nxt, (next_line,), _ = _submit(db_session, run, card, [_reward_line(1, 20, date(2026, 12, 10))], period=NEXT)
    statement = db_session.get(AccountStatement, nxt.id)
    assert far_id in {a.id for a in reward_bank_wins.candidates(db_session, statement, [card.id])}
    other = _accrual_of(db_session, far)
    assert (other.state, other.confirmed_statement_id) == ("bank_confirmed", nxt.id)
    assert next_line.id in _by_line(db_session, nxt.id)
    for statement_id in (stmt.id, nxt.id):
        hints = {(c.context or {}).get("hint") for c in _cases(db_session, statement_id, "open")}
        assert "bank_only" not in hints
    assert "create_system_entry" not in {a.action for a in _actions(db_session)}


def test_accept_first_of_two_lines(seed, db_session, run):
    """Re-check A: two `reward_early` lines; accepting the first claims only its line; the second case stays
    `reward_early` (on what remains) and accepting it claims its own ids."""
    card = _test_card(seed, db_session)
    rule = _rule(db_session, card)
    a = _spend(db_session, card, 1000, EARLY_DAY, rule)  # 10
    b = _spend(db_session, card, 2000, date(2026, 11, 13), rule)  # 20
    db_session.commit()
    stmt, (_, _, first, second), _ = _submit(db_session, run, card, [
        _line(1, EARLY_DAY, SHOP, 1000), _line(2, date(2026, 11, 13), SHOP, 2000),
        _reward_line(3, 10), _reward_line(4, 20)], period=PERIOD)
    a_id, b_id = _accrual_of(db_session, a).id, _accrual_of(db_session, b).id
    first_case = _one_open(db_session, stmt.id, first)
    assert first_case.context["default_accrual_ids"] == [a_id]
    assert _one_open(db_session, stmt.id, second).context["default_accrual_ids"] == [b_id]

    _accept(db_session, first_case)
    db_session.commit()
    claims = _by_line(db_session, stmt.id)
    assert [c.entry_id for c in claims[first.id]] == [_accrual_of(db_session, a).ledger_entry_id]
    assert second.id not in claims
    assert _accrual_of(db_session, b).state == "estimated"
    second_case = _one_open(db_session, stmt.id, second)
    assert second_case.context["hint"] == "reward_early"
    assert second_case.context["early_accrual_ids"] == [b_id]
    assert "accept_early_reward" in reconciliation_actions.allowed(second_case)

    _accept(db_session, second_case)
    db_session.commit()
    claims = _by_line(db_session, stmt.id)
    assert [c.entry_id for c in claims[second.id]] == [_accrual_of(db_session, b).ledger_entry_id]
    assert [c.entry_id for c in claims[first.id]] == [_accrual_of(db_session, a).ledger_entry_id]
    for accrual in (_accrual_of(db_session, a), _accrual_of(db_session, b)):
        assert (accrual.state, accrual.confirmed_statement_id) == ("bank_confirmed", stmt.id)
    assert [c for c in _cases(db_session, stmt.id, "open") if c.kind == "line_unmatched"] == []


def test_accept_after_job_posted_confirms_once(seed, db_session, run, pg_engine):
    """Fix round 1 ruling: the formula `post_on` passed before the owner tapped, so the daily job posted the early
    accrual (row dated 11-17). Accepting it still confirms it once at the bank figure, its row moved to the line's
    date; neither statement gets a `bank_only` case."""
    card = _test_card(seed, db_session)
    _, buy, stmt, line = _early_statement(db_session, run, card)
    case = _one_open(db_session, stmt.id, line)
    report = reward_post_job.run(pg_engine, "manual", today=date(2026, 11, 20))
    assert report["posted"] == 1
    (posted_row,) = _rewards(db_session)
    assert posted_row.posted_date == date(2026, 11, 17)

    _accept(db_session, db_session.get(ReconciliationCase, case.id))
    db_session.commit()
    accrual = _accrual_of(db_session, buy)
    assert (accrual.state, accrual.post_on, accrual.confirmed_statement_id) == ("bank_confirmed", REWARD_DAY,
                                                                                stmt.id)
    (row,) = _rewards(db_session)
    assert (row.id, row.amount, row.entry_date, row.posted_date) == (posted_row.id, Decimal("9.0000"), REWARD_DAY,
                                                                     REWARD_DAY)
    assert [c.entry_id for c in _by_line(db_session, stmt.id)[line.id]] == [row.id]
    assert db_session.get(ReconciliationCase, case.id).status == "resolved"

    nxt, _, _ = _submit(db_session, run, card, [], period=NEXT)
    for statement_id in (stmt.id, nxt.id):
        hints = {(c.context or {}).get("hint") for c in _cases(db_session, statement_id, "open")}
        assert "bank_only" not in hints
    assert _accrual_of(db_session, buy).confirmed_statement_id == stmt.id
    assert [r.id for r in _rewards(db_session)] == [row.id]


def test_posted_early_accrual_keeps_case_after_recheck(seed, db_session, run, pg_engine):
    """Fix round 1 ruling: once the job posted the early accrual, a re-run keeps the line's `reward_early` case with
    that id (not `bank_only`, not superseded into a booking)."""
    card = _test_card(seed, db_session)
    _, buy, stmt, line = _early_statement(db_session, run, card)
    case = _one_open(db_session, stmt.id, line)
    assert reward_post_job.run(pg_engine, "manual", today=date(2026, 11, 20))["posted"] == 1
    accrual_id = _accrual_of(db_session, buy).id

    _reconcile(db_session, stmt.id)
    kept = _one_open(db_session, stmt.id, line)
    assert kept.id == case.id
    assert (kept.context["hint"], kept.context["early_accrual_ids"]) == ("reward_early", [accrual_id])
    assert reconciliation_actions.allowed(kept) == ["create_system_entry", "accept_early_reward", "dismiss"]
    assert _accrual_of(db_session, buy).state == "posted"


def test_accept_then_rate_put_keeps_line_date(seed, db_session, run):
    """Re-check A: a rate PUT after the accept leaves the posted accrual frozen at the line's date and amount."""
    card = _test_card(seed, db_session)
    rule, buy, stmt, line = _early_statement(db_session, run, card)
    _accept(db_session, _one_open(db_session, stmt.id, line))
    db_session.commit()
    row_id = _accrual_of(db_session, buy).ledger_entry_id

    reward_rule_service.update(db_session, card.id, rule.id, RewardRuleUpdateIn(
        name="測試回饋", method="percent", rate=Decimal("2"), posting="after_transaction", delay_days=5))
    db_session.commit()
    accrual = _accrual_of(db_session, buy)
    assert (accrual.state, accrual.post_on, accrual.amount) == ("bank_confirmed", REWARD_DAY, Decimal("10.0000"))
    assert db_session.get(LedgerEntry, row_id).amount == Decimal("9.0000")


def test_accept_refuses_line_outside_period(seed, db_session, run, monkeypatch):
    card = _test_card(seed, db_session)
    _, buy, stmt, line = _early_statement(db_session, run, card, day=date(2026, 11, 16))
    case = _one_open(db_session, stmt.id, line)
    assert case.context["hint"] == "reward_early"
    # the registry no longer offers it (fix round 1, minor 3): no accept, and no accept button (#295: the promo
    # booking stays)
    assert reconciliation_actions.allowed(case) == ["create_system_entry", "dismiss"]
    assert _inbox_item(db_session, case.id)["actions"] == ["record_reward", "dismiss"]
    with pytest.raises(ValidationError) as refused:
        _accept(db_session, case)
    assert refused.value.field == "action"
    db_session.rollback()
    # the action's own 409 stays as the race guard behind the condition
    monkeypatch.setitem(reconciliation_actions.CONDITIONS, ("line_unmatched", "accept_early_reward"),
                        lambda case: True)
    with pytest.raises(CodedConflictError) as info:
        _accept(db_session, db_session.get(ReconciliationCase, case.id))
    assert info.value.code == "line_outside_period"
    db_session.rollback()
    accrual = _accrual_of(db_session, buy)
    assert (accrual.state, accrual.post_on, accrual.ledger_entry_id) == ("estimated", date(2026, 11, 17), None)
    assert _actions(db_session) == [] and _rewards(db_session) == []


def test_accept_early_reward_refuses_moved_accrual(seed, db_session, run):
    """A named accrual not in the case's list, or already confirmed by a statement → 409 `accrual_not_estimated`,
    nothing changed (a posted, unconfirmed one is accepted: `test_accept_after_job_posted_confirms_once`)."""
    card = _test_card(seed, db_session)
    rule, buy, stmt, line = _early_statement(db_session, run, card)
    case = _one_open(db_session, stmt.id, line)
    stranger = _spend(db_session, card, 500, date(2026, 11, 14), rule)  # not in the case's list
    db_session.commit()
    stranger_id = _accrual_of(db_session, stranger).id
    with pytest.raises(CodedConflictError) as info:
        _accept(db_session, case, accrual_ids=[_accrual_of(db_session, buy).id, stranger_id])
    assert info.value.code == "accrual_not_estimated"
    db_session.rollback()

    row = _post(db_session, buy)  # posted by the job, then confirmed elsewhere: no longer early
    moved = _accrual_of(db_session, buy)
    moved.state, moved.confirmed_statement_id = "bank_confirmed", stmt.id
    db_session.commit()
    with pytest.raises(CodedConflictError) as info:
        _accept(db_session, db_session.get(ReconciliationCase, case.id))
    assert info.value.code == "accrual_not_estimated"
    db_session.rollback()
    assert _actions(db_session) == []
    assert [r.id for r in _rewards(db_session)] == [row.id]
    assert db_session.get(ReconciliationCase, case.id).status == "open"
    assert _accrual_of(db_session, buy).post_on == date(2026, 11, 17)


def test_accept_not_policy_or_rule_learnable(seed, db_session, run, dirty_on, today):
    """Neither the policy nor a learned owner rule applies `accept_early_reward`; it is not learnable; dismiss writes
    no ledger row."""
    today(date(2026, 11, 20))
    card = _test_card(seed, db_session)
    _, buy, stmt, line = _early_statement(db_session, run, card)
    case = _one_open(db_session, stmt.id, line)
    _learned_record_rule(seed, db_session, card, line)
    owner_rule_service.apply_rules(db_session, stmt.id)
    _set_policy(db_session, SYSTEM_ON)
    policy_service.run_policy(db_session, stmt.id)
    assert _actions(db_session) == []
    assert "accept_early_reward" not in policy_service.POLICY_ACTIONS
    assert not inbox_service.learnable("other", "accept_early")

    engine.apply_action(db_session, case_id=case.id, action="dismiss", params={"note": "稍後再看"}, actor="owner",
                        label="spa")
    db_session.commit()
    assert db_session.get(ReconciliationCase, case.id).status == "dismissed"
    assert _rewards(db_session) == []
    assert _accrual_of(db_session, buy).state == "estimated"


def test_accept_refused_for_policy_actor(seed, db_session, run):
    card = _test_card(seed, db_session)
    _, _, stmt, line = _early_statement(db_session, run, card)
    case = _one_open(db_session, stmt.id, line)
    with pytest.raises(ValidationError) as info:
        engine.apply_action(db_session, case_id=case.id, action="accept_early_reward", params={}, actor="policy",
                            label="policy", budget=(date(2026, 11, 1), "TWD", Decimal("9")))
    assert type(info.value) is ValidationError and info.value.field == "actor"
    db_session.rollback()


def test_accept_via_inbox_one_tap(seed, db_session, run):
    """The inbox button `accept_early` applies the action with the case's `default_accrual_ids`."""
    card = _test_card(seed, db_session)
    _, buy, stmt, line = _early_statement(db_session, run, card)
    case = _one_open(db_session, stmt.id, line)
    body = inbox_service.InboxApplyIn(cases=[{"case_id": case.id, "case_version": case.version}],
                                      action="accept_early", params={}, answer="接受提早入帳")
    out = inbox_service.apply_batch(db_session, body=body, label="spa", request_id=None)
    assert [r["status"] for r in out["results"]] == ["applied"]
    (action,) = _actions(db_session)
    assert (action.action, action.params) == ("accept_early_reward", {"accrual_ids": [_accrual_of(db_session, buy).id]})
    assert _accrual_of(db_session, buy).state == "bank_confirmed"


def test_early_case_keeps_version_when_a_new_purchase_joins(seed, db_session, run):
    """#295 (M11): a new early purchase on the card that does not change the default refreshes the case's context in
    place; the case keeps its id and version (no flicker, no `case_moved` for an in-flight tap)."""
    card = _test_card(seed, db_session)
    rule, buy, stmt, line = _early_statement(db_session, run, card)
    case = _one_open(db_session, stmt.id, line)
    version, first = case.version, _accrual_of(db_session, buy)

    other = _spend(db_session, card, 5000, date(2026, 11, 13), rule)  # estimate 50: further from the line's 9
    db_session.commit()
    _reconcile(db_session, stmt.id)
    db_session.expire_all()
    kept = _one_open(db_session, stmt.id, line)
    assert (kept.id, kept.version) == (case.id, version)
    assert kept.context["early_accrual_ids"] == sorted([first.id, _accrual_of(db_session, other).id])
    assert kept.context["default_accrual_ids"] == [first.id]


def test_early_case_reversioned_when_the_default_changes(seed, db_session, run):
    """#295 (M11): a new early purchase whose estimate is closer to the line changes the default the one-tap
    applies, so the case is superseded and reopened (an in-flight tap gets `stale_case`)."""
    card = _test_card(seed, db_session)
    rule, buy, stmt, line = _early_statement(db_session, run, card, reward=Decimal("20"))
    case = _one_open(db_session, stmt.id, line)

    closer = _spend(db_session, card, 2000, date(2026, 11, 13), rule)  # estimate 20 == the line
    db_session.commit()
    _reconcile(db_session, stmt.id)
    db_session.expire_all()
    assert db_session.get(ReconciliationCase, case.id).status == "superseded"
    assert _one_open(db_session, stmt.id, line).context["default_accrual_ids"] == [_accrual_of(db_session, closer).id]


@pytest.mark.parametrize("early", [True, False])
def test_record_reward_books_a_promo_credit(seed, db_session, run, early):
    """#295: the inbox button `record_reward` books the reward line as its own reward entry (`create_system_entry`
    kind `reward`) from a `reward_early` or a `reward_surplus` case; the engine's accruals are untouched."""
    card = _test_card(seed, db_session)
    if early:
        _, buy, stmt, line = _early_statement(db_session, run, card)
    else:
        rule = _rule(db_session, card)
        buy = _spend(db_session, card, 1000, date(2026, 10, 20), rule)
        db_session.commit()
        _post(db_session, buy)
        stmt, (_, _, line), _ = _submit(db_session, run, card, [
            _line(1, date(2026, 10, 20), SHOP, 1000), _reward_line(2, 9), _reward_line(3, 5)], period=PERIOD)
    state = _accrual_of(db_session, buy).state
    case = _one_open(db_session, stmt.id, line)
    body = inbox_service.InboxApplyIn(cases=[{"case_id": case.id, "case_version": case.version}],
                                      action="record_reward", params={}, answer="記成另一筆回饋")
    out = inbox_service.apply_batch(db_session, body=body, label="spa", request_id=None)
    assert [r["status"] for r in out["results"]] == ["applied"]
    (action,) = _actions(db_session)
    assert (action.action, action.params) == ("create_system_entry", {"kind": "reward"})
    assert _accrual_of(db_session, buy).state == state
    assert not inbox_service.learnable("other", "record_reward")


def test_early_case_superseded_after_posting_fix(seed, db_session, run):
    """Lead carry 2 (P6): a rule PUT moving `post_on` into the period flags the statement; the re-run supersedes the
    case and the line is claimed by the engine row."""
    card = _test_card(seed, db_session)
    rule, buy, stmt, line = _early_statement(db_session, run, card)
    case = _one_open(db_session, stmt.id, line)

    reward_rule_service.update(db_session, card.id, rule.id, RewardRuleUpdateIn(
        name="測試回饋", method="percent", rate=Decimal("1"), posting="after_transaction", delay_days=1))
    db_session.commit()
    assert db_session.get(AccountStatement, stmt.id).needs_recheck is True
    assert _accrual_of(db_session, buy).post_on == date(2026, 11, 13)

    _reconcile(db_session, stmt.id)
    assert db_session.get(ReconciliationCase, case.id).status == "superseded"
    (claim,) = _by_line(db_session, stmt.id)[line.id]
    assert (claim.entry_id, claim.match_rule) == (_accrual_of(db_session, buy).ledger_entry_id, "reward_engine")
    assert _open_on(db_session, stmt.id, line) == []


# --- the one-cycle balance gap -----------------------------------------------------------------------------------

def test_carry_gap_offers_dismiss_only(seed, db_session, run, dirty_on, today):
    """7.8: a posted current-period row the statement does not print raises a card balance gap naming the row's
    accrual; the case offers `dismiss` only and the policy books nothing."""
    today(date(2026, 11, 20))
    card = _test_card(seed, db_session)
    rule = _rule(db_session, card)
    buy = _spend(db_session, card, 1000, date(2026, 10, 20), rule)
    db_session.commit()
    _post(db_session, buy)
    stmt, _, _ = _submit(db_session, run, card, [_line(1, date(2026, 10, 20), SHOP, 1000)], period=PERIOD)

    accrual = _accrual_of(db_session, buy)
    assert (accrual.state, accrual.confirmed_statement_id) == ("posted", None)
    (gap,) = [c for c in _cases(db_session, stmt.id, "open") if c.kind == "balance_gap"]
    assert gap.context == {"gap": gap.context["gap"], "card": True, "carried_accrual_ids": [accrual.id]}
    assert Decimal(gap.context["gap"]) != 0
    assert reconciliation_actions.allowed(gap) == ["dismiss"]
    assert _inbox_item(db_session, gap.id)["actions"] == ["dismiss"]

    _set_policy(db_session, {"enabled": True, "actions": {
        "create_system_entry": {"enabled": True}, "anchor_opening": {"enabled": True, "max_amount": "200000"}}})
    assert gap.id not in _policy_case_ids(db_session, stmt.id)
    policy_service.run_policy(db_session, stmt.id)
    assert _actions(db_session) == []


def test_uncredited_carry_keeps_gap_on_next_statement(seed, db_session, run, today):
    """#292: a carried row the next statement does not credit either (no reward line at all) stays `posted`, so
    that statement's card balance gap stays open and names it: a withheld reward surfaces as a gap, not a forfeit."""
    today(date(2027, 1, 20))
    card = _test_card(seed, db_session)
    rule = _rule(db_session, card)
    buy = _spend(db_session, card, 1000, date(2026, 10, 20), rule)  # posts 10-25, inside PERIOD
    db_session.commit()
    _post(db_session, buy)
    _submit(db_session, run, card, [_line(1, date(2026, 10, 20), SHOP, 1000)], period=PERIOD)
    stmt, _, _ = _submit(db_session, run, card, [_line(1, date(2026, 11, 20), SHOP, 500)], period=NEXT)

    accrual = _accrual_of(db_session, buy)
    assert (accrual.state, accrual.confirmed_statement_id) == ("posted", None)
    (gap,) = [c for c in _cases(db_session, stmt.id, "open") if c.kind == "balance_gap"]
    assert gap.context["carried_accrual_ids"] == [accrual.id] and Decimal(gap.context["gap"]) != 0
    assert reconciliation_actions.allowed(gap) == ["dismiss"]

    # a third statement: the row left the carried window; the gap stays, without the ids, with the usual actions
    third, _, _ = _submit(db_session, run, card, [_line(1, date(2026, 12, 20), SHOP, 300)],
                          period=(date(2026, 12, 16), date(2027, 1, 15)))
    assert _accrual_of(db_session, buy).state == "posted"
    (gap,) = [c for c in _cases(db_session, third.id, "open") if c.kind == "balance_gap"]
    assert "carried_accrual_ids" not in gap.context and Decimal(gap.context["gap"]) != 0
    assert reconciliation_actions.allowed(gap) == ["balance_adjustment_asof", "dismiss"]


def test_card_gap_without_engine_rows_unchanged(seed, db_session, run):
    """No carried row: the card gap context is as before and `balance_adjustment_asof` stays offered."""
    card = _test_card(seed, db_session)
    stmt, _, _ = _submit(db_session, run, card, [_line(1, date(2026, 10, 20), SHOP, 1000)], period=PERIOD)
    (gap,) = [c for c in _cases(db_session, stmt.id, "open") if c.kind == "balance_gap"]
    assert set(gap.context) == {"gap", "card"}
    assert reconciliation_actions.allowed(gap) == ["balance_adjustment_asof", "dismiss"]


def test_closing_day_change_while_an_inbox_action_holds_an_early_statement_is_retry(pg_engine, seed, db_session, run):
    """#296 M2: an inbox action with `account_lock` takes the statement row and then the account row. A closing_day
    change holds the account row and then flags the card's statements with an open `reward_early` case
    (`mark_early_rechecks`): that statement lock is NOWAIT, so the change answers 409 `retry` at once and never waits
    on the statement while it holds the account (no statement <-> account cycle). Nothing changes."""
    card = _test_card(seed, db_session)
    _, _, stmt, line = _early_statement(db_session, run, card)
    assert _one_open(db_session, stmt.id, line).context["hint"] == "reward_early"
    card_id, stmt_id = card.id, stmt.id
    body = AccountIn(name=card.name, currency=card.currency, is_credit=True, closing_day=20, combined_account_id=None)
    db_session.commit()
    holder = pg_engine.connect()
    try:
        holder.begin()
        holder.execute(text("SELECT id FROM account_statement WHERE id = :id FOR UPDATE"), {"id": stmt_id})
        db_session.execute(text("SET lock_timeout = '3s'"))
        started = time.monotonic()
        with pytest.raises(CodedConflictError) as caught:
            settings_service.update_account(db_session, card_id, body)
        assert caught.value.code == RETRY and time.monotonic() - started < 1.5, "the change waited on the statement"
        db_session.rollback()
        # the inbox action's next lock, the account row, is free again
        holder.execute(text("SELECT id FROM account WHERE id = :id FOR UPDATE NOWAIT"), {"id": card_id})
    finally:
        holder.rollback()
        holder.close()
        db_session.execute(text("SET lock_timeout = 0"))
    db_session.expire_all()
    assert db_session.get(Account, card_id).closing_day == 15
