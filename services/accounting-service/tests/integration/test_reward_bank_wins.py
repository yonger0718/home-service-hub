"""Bank wins (spec 7, 4.1; plan P12): a card statement's reward lines replace the engine's posted reward amounts.

Fictional card 「測試卡」 closing on the 15th; the statement under test covers 10-16..11-15 and the rule posts
5 days after each purchase, so a purchase in the period posts in the period. Synthetic data only."""

import re
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import sessionmaker

from app.models import AccountStatement, LedgerEntry, RewardAccrual, RewardRule, StatementCoverage
from app.services import coverage_service, policy_service, reward_bank_wins, reward_engine, reward_posting
from app.services import reconciliation_service as rec
from app.schemas.writes import AccountIn, RewardRuleIn
from app.services import reward_rule_service, settings_service
from app.services import statement_revision_service as revs
from app.services.entry_write_service import write_rule_links
from app.services.errors import CodedConflictError
from tests.helpers import make_entry, race, set_dirty
from tests.integration.statement_fixtures import (  # noqa: F401  (run is a fixture)
    _active, _card, _cases, _line, _reconcile, _submit, run,
)

PERIOD = (date(2026, 10, 16), date(2026, 11, 15))
PREVIOUS = (date(2026, 9, 16), date(2026, 10, 15))
SHOP = "測試商店"


# --- fixtures ----------------------------------------------------------------------------------------------------

def _test_card(seed, db):
    return _card(seed, db, "測試卡", closing_day=15)


def _rule(db, card, **columns) -> RewardRule:
    values = dict(account_id=card.id, name="測試回饋", method="percent", rate=Decimal("1"),
                  posting="after_transaction", delay_days=5)
    values.update(columns)
    rule = RewardRule(**values)
    db.add(rule)
    db.flush()
    return rule


def _spend(db, card, amount, day, rule, **columns) -> LedgerEntry:
    entry = make_entry(db, card, f"-{amount}", entry_date=day, posted_date=day, merchant=SHOP, **columns)
    write_rule_links(db, entry.id, [rule.id])
    reward_engine.sync_entry(db, entry.id)
    return entry


def _accrual_of(db, purchase) -> RewardAccrual:
    db.expire_all()
    return db.scalars(select(RewardAccrual).where(RewardAccrual.source_entry_id == purchase.id)).one()


def _post(db, purchase) -> LedgerEntry:
    accrual = _accrual_of(db, purchase)
    assert reward_posting.post_accrual(db, accrual.id) == "posted"
    db.commit()
    return db.get(LedgerEntry, _accrual_of(db, purchase).ledger_entry_id)


def _rewards(db) -> list[LedgerEntry]:
    db.expire_all()
    return list(db.scalars(select(LedgerEntry).where(LedgerEntry.kind == "reward").order_by(LedgerEntry.id)))


def _reward_line(seq, amount, day=date(2026, 11, 10)):
    return _line(seq, day, "回饋入帳", -amount, kind="reward")  # a card credit prints negative


def _by_line(db, statement_id) -> dict[int, list[StatementCoverage]]:
    out: dict[int, list[StatementCoverage]] = {}
    for row in _active(db, statement_id):
        out.setdefault(row.line_id, []).append(row)
    return out


def _line_cases(db, statement_id, line_id):
    return [c for c in _cases(db, statement_id, "open") if c.line_id == line_id]


@pytest.fixture
def no_hook(monkeypatch):
    """submit_revision without the reconcile pass, so the test runs `reconcile` itself."""
    monkeypatch.setattr(revs, "reconciliation_hook", lambda *args: [])


# --- the step ----------------------------------------------------------------------------------------------------

def test_bank_amount_replaces_engine_amount(seed, db_session, run):
    card = _test_card(seed, db_session)
    rule = _rule(db_session, card)
    buy = _spend(db_session, card, 1000, date(2026, 10, 20), rule)
    db_session.commit()
    row = _post(db_session, buy)
    assert row.amount == Decimal("10.0000")

    stmt, (_, reward_line), _ = _submit(db_session, run, card, [_line(1, date(2026, 10, 20), SHOP, 1000),
                                                                  _reward_line(2, 9)], period=PERIOD)

    accrual = _accrual_of(db_session, buy)
    assert (accrual.state, accrual.amount, accrual.confirmed_statement_id) == ("bank_confirmed", Decimal("10.0000"),
                                                                              stmt.id)
    assert [r.id for r in _rewards(db_session)] == [row.id]
    assert db_session.get(LedgerEntry, row.id).amount == Decimal("9.0000")
    (claim,) = _by_line(db_session, stmt.id)[reward_line.id]
    assert (claim.entry_id, claim.match_kind, claim.match_rule, claim.role) == (row.id, "auto", "reward_engine",
                                                                               "principal")
    assert _line_cases(db_session, stmt.id, reward_line.id) == []
    coverage_service.assert_conserved(db_session, db_session.get(AccountStatement, stmt.id))


def test_flag_clears_on_bank_confirm(seed, db_session, run):
    """The purchases' `reward_posted` flags clear on a confirm (three carried rows, one line on statement 2); a flag
    set for another reason stays. The forfeit case is `test_flag_clears_on_forfeit`."""
    card = _test_card(seed, db_session)
    rule = _rule(db_session, card)
    first = _spend(db_session, card, 1000, date(2026, 10, 1), rule)
    second = _spend(db_session, card, 2000, date(2026, 10, 2), rule)
    other = _spend(db_session, card, 3000, date(2026, 10, 3), rule)
    db_session.commit()
    for purchase in (first, second, other):
        _post(db_session, purchase)
    _submit(db_session, run, card, [], period=PREVIOUS)  # no reward line: every row stays posted, carried
    for purchase, reason in ((first, "reward_posted"), (second, "reward_posted"), (other, "duplicate")):
        entry = db_session.get(LedgerEntry, purchase.id)
        entry.needs_review, entry.review_reason = True, reason
    db_session.commit()

    # one line, three carried rows (10, 20, 30): the line is spread 1:2:3 over the rows
    _submit(db_session, run, card, [_reward_line(1, 60)], period=PERIOD)
    assert {_accrual_of(db_session, p).state for p in (first, second, other)} == {"bank_confirmed"}
    db_session.expire_all()
    for purchase in (first, second):
        entry = db_session.get(LedgerEntry, purchase.id)
        assert (entry.needs_review, entry.review_reason) == (False, None)
    entry = db_session.get(LedgerEntry, other.id)
    assert (entry.needs_review, entry.review_reason) == (True, "duplicate")


def test_flag_clears_on_forfeit(seed, db_session, run, monkeypatch):
    t = _carried_and_credited_current(seed, db_session, run, monkeypatch)
    assert _accrual_of(db_session, t.carried).state == "forfeited"
    entry = db_session.get(LedgerEntry, t.carried.id)
    assert (entry.needs_review, entry.review_reason) == (False, None)


def test_reward_engine_claim_survives_sweep(seed, db_session, run, dirty_on):
    """§7.7 / finding 9: the amount update's dirty event predates the claim and equals its snapshot."""
    card = _test_card(seed, db_session)
    rule = _rule(db_session, card)
    buy = _spend(db_session, card, 1000, date(2026, 10, 20), rule)
    db_session.commit()
    row = _post(db_session, buy)
    stmt, (reward_line,), _ = _submit(db_session, run, card, [_reward_line(1, 9)], period=PERIOD)
    (claim,) = _active(db_session, stmt.id)
    assert (claim.entry_id, claim.match_rule) == (row.id, "reward_engine")

    locked = db_session.execute(select(AccountStatement).where(AccountStatement.id == stmt.id).with_for_update()
                                .execution_options(populate_existing=True)).scalar_one()
    swept = coverage_service.sweep(db_session, locked)
    db_session.commit()
    assert swept.stale_lines == []
    assert [(r.id, r.status) for r in _active(db_session, stmt.id)] == [(claim.id, "active")]


def test_estimated_candidate_posted_early_in_reconcile(seed, db_session, run, no_hook, monkeypatch):
    """§7.4: an estimated candidate is posted on the reconcile session; a rollback undoes the post."""
    card = _test_card(seed, db_session)
    rule = _rule(db_session, card)
    buy = _spend(db_session, card, 1000, date(2026, 10, 20), rule)
    db_session.commit()
    stmt, (reward_line,), _ = _submit(db_session, run, card, [_reward_line(1, 9)], period=PERIOD)
    assert _accrual_of(db_session, buy).state == "estimated"

    real = reward_bank_wins.prepare

    def prepare_then_fail(*args, **kwargs):
        plan = real(*args, **kwargs)
        assert plan is not None and _accrual_of_unexpired(db_session, buy).state == "bank_confirmed"
        raise RuntimeError("forced rollback")

    monkeypatch.setattr(reward_bank_wins, "prepare", prepare_then_fail)
    with pytest.raises(RuntimeError, match="forced rollback"):
        rec.reconcile(db_session, stmt.id)
    db_session.rollback()
    accrual = _accrual_of(db_session, buy)
    assert (accrual.state, accrual.ledger_entry_id) == ("estimated", None)
    assert _rewards(db_session) == []

    monkeypatch.setattr(reward_bank_wins, "prepare", real)
    _reconcile(db_session, stmt.id)
    accrual = _accrual_of(db_session, buy)
    assert accrual.state == "bank_confirmed"
    (row,) = _rewards(db_session)
    assert (row.id, row.amount, row.entry_date) == (accrual.ledger_entry_id, Decimal("9.0000"), date(2026, 10, 25))
    assert [r.entry_id for r in _active(db_session, stmt.id)] == [row.id]


def _accrual_of_unexpired(db, purchase) -> RewardAccrual:
    """The accrual as this session sees it now (no expire: the reconcile's pending state)."""
    return db.scalars(select(RewardAccrual).where(RewardAccrual.source_entry_id == purchase.id)).one()


def test_two_lines_two_rows_pair_in_order(seed, db_session, run):
    card = _test_card(seed, db_session)
    rule = _rule(db_session, card)
    later = _spend(db_session, card, 2000, date(2026, 10, 22), rule)  # lower id, later post_on (10-27)
    earlier = _spend(db_session, card, 1000, date(2026, 10, 18), rule)  # post_on 10-23
    db_session.commit()
    first_row, second_row = _post(db_session, earlier), _post(db_session, later)

    stmt, (line_5, line_7), _ = _submit(db_session, run, card, [_reward_line(1, 5), _reward_line(2, 7)],
                                        period=PERIOD)
    assert line_5.id < line_7.id
    by_line = _by_line(db_session, stmt.id)
    assert [(r.entry_id, r.snapshot["amount"]) for r in by_line[line_5.id]] == [(first_row.id, "5.0000")]
    assert [(r.entry_id, r.snapshot["amount"]) for r in by_line[line_7.id]] == [(second_row.id, "7.0000")]
    coverage_service.assert_conserved(db_session, db_session.get(AccountStatement, stmt.id))


def test_three_rows_one_line_spread_proportionally(seed, db_session, run):
    card = _test_card(seed, db_session)
    rule = _rule(db_session, card)
    buys = [_spend(db_session, card, amount, date(2026, 10, 17 + i), rule)
            for i, amount in enumerate((1000, 2000, 3000))]
    db_session.commit()
    rows = [_post(db_session, buy) for buy in buys]

    stmt, (line,), _ = _submit(db_session, run, card, [_reward_line(1, 30)], period=PERIOD)
    db_session.expire_all()
    assert [db_session.get(LedgerEntry, r.id).amount for r in rows] == [Decimal("5.0000"), Decimal("10.0000"),
                                                                        Decimal("15.0000")]
    assert [_accrual_of(db_session, b).amount for b in buys] == [Decimal("10.0000"), Decimal("20.0000"),
                                                                 Decimal("30.0000")]
    assert sorted(r.entry_id for r in _by_line(db_session, stmt.id)[line.id]) == [r.id for r in rows]
    coverage_service.assert_conserved(db_session, db_session.get(AccountStatement, stmt.id))


def test_no_reward_line_keeps_current_posted(seed, db_session, run):
    card = _test_card(seed, db_session)
    rule = _rule(db_session, card)
    buy = _spend(db_session, card, 1000, date(2026, 10, 20), rule)
    db_session.commit()
    row = _post(db_session, buy)

    stmt, _, _ = _submit(db_session, run, card, [_line(1, date(2026, 10, 20), SHOP, 1000)], period=PERIOD)
    accrual = _accrual_of(db_session, buy)
    assert (accrual.state, accrual.confirmed_statement_id) == ("posted", None)
    assert db_session.get(LedgerEntry, row.id).amount == Decimal("10.0000")
    assert row.id not in {r.entry_id for r in _active(db_session, stmt.id)}


def test_carried_row_confirmed_next_statement(seed, db_session, run):
    card = _test_card(seed, db_session)
    rule = _rule(db_session, card)
    buy = _spend(db_session, card, 1000, date(2026, 10, 5), rule)  # posts 10-10, inside the previous period
    db_session.commit()
    row = _post(db_session, buy)
    first, _, _ = _submit(db_session, run, card, [], period=PREVIOUS)
    assert (_accrual_of(db_session, buy).state, _accrual_of(db_session, buy).confirmed_statement_id) == ("posted",
                                                                                                         None)

    second, (line,), _ = _submit(db_session, run, card, [_reward_line(1, 8)], period=PERIOD)
    accrual = _accrual_of(db_session, buy)
    assert (accrual.state, accrual.confirmed_statement_id) == ("bank_confirmed", second.id)
    assert db_session.get(LedgerEntry, row.id).amount == Decimal("8.0000")
    assert [r.entry_id for r in _by_line(db_session, second.id)[line.id]] == [row.id]


def test_carried_row_stays_posted_without_reward_line(seed, db_session, run):
    """#292: a statement with no reward line at all is no evidence the bank withheld the reward (a late credit, a
    parser or OCR gap): the carried row stays `posted`, unconfirmed, at its accrual amount."""
    card = _test_card(seed, db_session)
    rule = _rule(db_session, card)
    buy = _spend(db_session, card, 1000, date(2026, 10, 5), rule)
    db_session.commit()
    row = _post(db_session, buy)
    _submit(db_session, run, card, [], period=PREVIOUS)
    _submit(db_session, run, card, [_line(1, date(2026, 10, 20), SHOP, 500)], period=PERIOD)

    accrual = _accrual_of(db_session, buy)
    assert (accrual.state, accrual.confirmed_statement_id, accrual.amount) == ("posted", None, Decimal("10.0000"))
    assert db_session.get(LedgerEntry, row.id).amount == Decimal("10.0000")


class _EveryLine(dict):
    """An accepted map naming the same accrual ids for every reward line (`_accepted` keeps the reward lines)."""

    def __init__(self, ids):
        super().__init__()
        self.ids = ids

    def __contains__(self, line_id):
        return True

    def __getitem__(self, line_id):
        return self.ids


def _carried_and_credited_current(seed, db, run, monkeypatch):
    """A carried row (posted 10-10) and an in-period row (posted 10-25); the second statement's one reward line is
    accepted for the in-period accrual only (the accepted map, as an applied `accept_early_reward` would name it),
    so pairing leaves the carried row assigned 0 while the statement does print a reward line."""
    card = _test_card(seed, db)
    rule = _rule(db, card)
    carried = _spend(db, card, 1000, date(2026, 10, 5), rule)
    current = _spend(db, card, 2000, date(2026, 10, 20), rule)
    db.commit()
    carried_row = _post(db, carried)
    _post(db, current)
    _submit(db, run, card, [], period=PREVIOUS)
    entry = db.get(LedgerEntry, carried.id)
    entry.needs_review, entry.review_reason = True, "reward_posted"
    db.commit()
    current_id = _accrual_of(db, current).id
    monkeypatch.setattr(reward_bank_wins, "accepted_map", lambda db, s: _EveryLine([current_id]))
    stmt, _, _ = _submit(db, run, card, [_reward_line(1, 20)], period=PERIOD)
    return SimpleNamespace(stmt=stmt, carried=carried, current=current, carried_row=carried_row)


def test_carried_row_forfeited_when_reward_line_credits_others(seed, db_session, run, monkeypatch):
    """The statement prints a reward line but pairing leaves the carried row assigned 0: forfeited (unchanged)."""
    t = _carried_and_credited_current(seed, db_session, run, monkeypatch)
    accrual = _accrual_of(db_session, t.carried)
    assert (accrual.state, accrual.confirmed_statement_id, accrual.amount) == ("forfeited", t.stmt.id,
                                                                               Decimal("10.0000"))
    assert db_session.get(LedgerEntry, t.carried_row.id).amount == Decimal("0.0000")
    assert accrual.ledger_entry_id == t.carried_row.id  # kept, visible as 未獲得
    assert _accrual_of(db_session, t.current).state == "bank_confirmed"


def test_rerun_restores_row_forfeited_without_reward_line(seed, db_session, run):
    """#292: a row an earlier run forfeited on a statement with no reward line (the old rule) goes back to `posted`
    at its accrual amount on the next reconcile of that statement."""
    card = _test_card(seed, db_session)
    rule = _rule(db_session, card)
    buy = _spend(db_session, card, 1000, date(2026, 10, 5), rule)
    db_session.commit()
    row = _post(db_session, buy)
    _submit(db_session, run, card, [], period=PREVIOUS)
    second, _, _ = _submit(db_session, run, card, [], period=PERIOD)
    accrual = _accrual_of(db_session, buy)
    accrual.state, accrual.confirmed_statement_id = "forfeited", second.id  # as the old rule left it
    db_session.get(LedgerEntry, row.id).amount = Decimal("0")
    db_session.commit()

    _reconcile(db_session, second.id)
    accrual = _accrual_of(db_session, buy)
    assert (accrual.state, accrual.confirmed_statement_id, accrual.amount) == ("posted", None, Decimal("10.0000"))
    assert db_session.get(LedgerEntry, row.id).amount == Decimal("10.0000")


def test_first_statement_has_no_carry(seed, db_session, run):
    """An older posted row with no earlier statement is not a candidate: the reward line meets the matcher."""
    card = _test_card(seed, db_session)
    rule = _rule(db_session, card)
    buy = _spend(db_session, card, 1000, date(2026, 10, 5), rule)
    db_session.commit()
    _post(db_session, buy)

    stmt, (line,), _ = _submit(db_session, run, card, [_reward_line(1, 10)], period=PERIOD)
    accrual = _accrual_of(db_session, buy)
    assert (accrual.state, accrual.confirmed_statement_id) == ("posted", None)
    assert _active(db_session, stmt.id) == []
    (case,) = _line_cases(db_session, stmt.id, line.id)
    assert (case.kind, case.context.get("hint")) == ("line_unmatched", "bank_only")


def test_rerun_rebuilds_identical_claims(seed, db_session, run):
    card = _test_card(seed, db_session)
    rule = _rule(db_session, card)
    buys = [_spend(db_session, card, amount, date(2026, 10, 17 + i), rule)
            for i, amount in enumerate((1000, 2000, 3000))]
    db_session.commit()
    rows = [_post(db_session, buy) for buy in buys]
    stmt, _, _ = _submit(db_session, run, card, [_reward_line(1, 7), _reward_line(2, 31)], period=PERIOD)
    before = [(r.id, r.line_id, r.entry_id, r.snapshot) for r in _active(db_session, stmt.id)]
    amounts = [db_session.get(LedgerEntry, r.id).amount for r in rows]
    assert len(before) == 3

    _reconcile(db_session, stmt.id)
    _reconcile(db_session, stmt.id)
    db_session.expire_all()
    assert [(r.id, r.line_id, r.entry_id, r.snapshot) for r in _active(db_session, stmt.id)] == before
    assert [db_session.get(LedgerEntry, r.id).amount for r in rows] == amounts
    assert len(_rewards(db_session)) == 3


def test_moze_exact_match_stays_in_matcher(seed, db_session, run):
    """§7.3: a line with exactly one equal no-accrual reward row stays in the matcher; the other is pulled."""
    card = _test_card(seed, db_session)
    rule = _rule(db_session, card)
    buy = _spend(db_session, card, 1000, date(2026, 10, 20), rule)
    moze = seed.entry(card, "9", kind="reward", day=date(2026, 11, 9), merchant="回饋入帳", source="moze_import")
    db_session.commit()
    row = _post(db_session, buy)

    stmt, (moze_line, engine_line), _ = _submit(db_session, run, card,
                                                [_reward_line(1, 9, day=date(2026, 11, 9)), _reward_line(2, 10)],
                                                period=PERIOD)
    by_line = _by_line(db_session, stmt.id)
    (held,) = by_line[moze_line.id]
    assert held.entry_id == moze.id and held.match_rule != "reward_engine"
    (engine,) = by_line[engine_line.id]
    assert (engine.entry_id, engine.match_rule) == (row.id, "reward_engine")
    assert reward_bank_wins.pulled_filter(db_session, stmt, [card.id], [moze_line, engine_line]) == {engine_line.id}


def test_empty_candidates_unchanged_behaviour(seed, db_session, run):
    card = _test_card(seed, db_session)
    seed.entry(card, "-1000", day=date(2026, 10, 20), merchant=SHOP)
    db_session.commit()
    stmt, (_, line), _ = _submit(db_session, run, card, [_line(1, date(2026, 10, 20), SHOP, 1000),
                                                       _reward_line(2, 9)], period=PERIOD)
    (case,) = _line_cases(db_session, stmt.id, line.id)
    assert (case.kind, case.context.get("hint")) == ("line_unmatched", "bank_only")
    assert reward_bank_wins.prepare(db_session, stmt, [card.id], [line], set()) is None
    assert reward_bank_wins.pulled_filter(db_session, stmt, [card.id], [line]) == set()


def test_engine_rows_never_matcher_candidates(seed, db_session, run):
    """An engine row outside the period with an equal amount is not claimed by the matcher (§7)."""
    card = _test_card(seed, db_session)
    rule = _rule(db_session, card)
    buy = _spend(db_session, card, 1000, date(2026, 11, 15), rule)  # posts 11-20: after the period, inside the window
    db_session.commit()
    row = _post(db_session, buy)
    assert row.entry_date == date(2026, 11, 20)

    stmt, (line,), _ = _submit(db_session, run, card, [_reward_line(1, 10)], period=PERIOD)
    assert row.id not in {r.entry_id for r in _active(db_session, stmt.id)}
    (case,) = _line_cases(db_session, stmt.id, line.id)
    # Task 9 fix round 1 ruling: a posted, unconfirmed row posting in the next cycle is an early candidate, so the
    # unmatched line is the owner's `reward_early` case, never `bank_only`
    assert (case.kind, case.context.get("hint")) == ("line_unmatched", "reward_early")
    assert case.context["early_accrual_ids"] == [_accrual_of(db_session, buy).id]
    entries, _ = rec.load_candidates(db_session, stmt, [card.id], window_days=10, include_reward=True)
    assert row.id not in {e.id for e in entries}


def test_policy_validator_agrees(seed, db_session, run, monkeypatch):
    """`_single_admissible` builds pass 2 over reconcile's claimable lines: the pulled line is in neither, and a
    case on a pulled line is refused."""
    card = _test_card(seed, db_session)
    rule = _rule(db_session, card)
    buy = _spend(db_session, card, 1000, date(2026, 10, 20), rule)
    db_session.commit()
    _post(db_session, buy)

    seen: list[list[int]] = []
    real = rec.pass_two_holders

    def spy(db, statement, accounts, rules, current, claimable_lines, *args, **kwargs):
        seen.append(sorted(line.id for line in claimable_lines))
        return real(db, statement, accounts, rules, current, claimable_lines, *args, **kwargs)

    monkeypatch.setattr(rec, "pass_two_holders", spy)
    stmt, (unknown, reward_line), _ = _submit(db_session, run, card, [_line(1, date(2026, 10, 21), "別家", 50),
                                                                     _reward_line(2, 9)], period=PERIOD)
    assert seen == [[unknown.id]]
    (case,) = _line_cases(db_session, stmt.id, unknown.id)

    statement = db_session.get(AccountStatement, stmt.id)
    rules = rec.rules_for(settings_service.reconciliation_rules(db_session), statement.period_end)
    candidate = policy_service.Candidate(case.id, case.kind, case.status, "match",
                                         {"line_id": unknown.id, "entry_id": buy.id}, Decimal(0))
    with pytest.raises(CodedConflictError):
        policy_service._single_admissible(SimpleNamespace(db=db_session, statement=statement, case=case,
                                                          line=unknown, rules=rules), candidate)
    assert seen[1:] == [[unknown.id]]

    pulled_case = SimpleNamespace(id=0, candidates=[])
    with pytest.raises(CodedConflictError) as info:
        policy_service._single_admissible(SimpleNamespace(db=db_session, statement=statement, case=pulled_case,
                                                          line=reward_line, rules=rules), candidate)
    assert info.value.code == "not_single_admissible"
    assert len(seen) == 2  # refused before any matcher pass


def test_rerun_after_lines_move_to_matcher_reverts_confirmations(seed, db_session, run):
    """Ruling (fix round 1): when the step stops (every reward line stays in the matcher), this statement's earlier
    outcomes get the assigned-0 outcome of §7.6: an in-period row goes back to `posted` at its accrual amount with
    `confirmed_statement_id` cleared, a carried row is `forfeited` (ledger 0). No post, no claim."""
    card = _test_card(seed, db_session)
    rule = _rule(db_session, card)
    carried = _spend(db_session, card, 1000, date(2026, 10, 5), rule)  # posts 10-10, previous period
    current = _spend(db_session, card, 2000, date(2026, 10, 20), rule)  # posts 10-25
    db_session.commit()
    carried_row, current_row = _post(db_session, carried), _post(db_session, current)
    _submit(db_session, run, card, [], period=PREVIOUS)
    stmt, (line,), _ = _submit(db_session, run, card, [_reward_line(1, 9)], period=PERIOD)
    assert {_accrual_of(db_session, p).state for p in (carried, current)} == {"bank_confirmed"}
    assert [db_session.get(LedgerEntry, r.id).amount for r in (carried_row, current_row)] == [Decimal("3.0000"),
                                                                                           Decimal("6.0000")]

    moze = seed.entry(card, "9", kind="reward", day=date(2026, 11, 10), merchant="回饋入帳", source="moze_import")
    db_session.commit()
    assert reward_bank_wins.pulled_filter(db_session, stmt, [card.id], [line]) == set()
    _reconcile(db_session, stmt.id)

    db_session.expire_all()
    back = _accrual_of(db_session, current)
    assert (back.state, back.confirmed_statement_id, back.amount) == ("posted", None, Decimal("20.0000"))
    assert db_session.get(LedgerEntry, current_row.id).amount == Decimal("20.0000")
    lost = _accrual_of(db_session, carried)
    assert (lost.state, lost.confirmed_statement_id, lost.amount) == ("forfeited", stmt.id, Decimal("10.0000"))
    assert db_session.get(LedgerEntry, carried_row.id).amount == Decimal("0.0000")
    (held,) = _by_line(db_session, stmt.id)[line.id]
    assert held.entry_id == moze.id and held.match_rule != "reward_engine"
    assert len(_rewards(db_session)) == 3  # the two engine rows (kept) and the MOZE row; nothing posted


def test_non_live_statement_no_bank_wins(seed, db_session, run):
    """Ruling (fix round 1): bank wins runs for live statements only; a historical statement posts nothing early and
    changes no reward amount or accrual state."""
    card = _card(seed, db_session, "測試卡", closing_day=15, live=False)
    rule = _rule(db_session, card)
    posted = _spend(db_session, card, 1000, date(2026, 10, 18), rule)
    estimated = _spend(db_session, card, 2000, date(2026, 10, 20), rule)
    db_session.commit()
    row = _post(db_session, posted)

    stmt, (line,), _ = _submit(db_session, run, card, [_reward_line(1, 25)], period=PERIOD)
    assert stmt.mode != "live"
    assert (_accrual_of(db_session, posted).state, _accrual_of(db_session, estimated).state) == ("posted",
                                                                                                "estimated")
    assert _accrual_of(db_session, posted).confirmed_statement_id is None
    assert [(r.id, r.amount) for r in _rewards(db_session)] == [(row.id, Decimal("10.0000"))]
    assert row.id not in {r.entry_id for r in _active(db_session, stmt.id)}


# --- locks -------------------------------------------------------------------------------------------------------

_LOCK = re.compile(r"FOR (UPDATE|SHARE|NO KEY UPDATE)")


def test_prepare_lock_order(seed, db_session, run, no_hook):
    """P12: the statement row, then the purchases, then ONE accrual window lock, then the reward ledger rows; no
    accrual lock after any ledger lock or write of the step."""
    card = _test_card(seed, db_session)
    rule = _rule(db_session, card)
    posted = _spend(db_session, card, 1000, date(2026, 10, 18), rule)
    estimated = _spend(db_session, card, 2000, date(2026, 10, 20), rule)
    db_session.commit()
    _post(db_session, posted)
    stmt, _, _ = _submit(db_session, run, card, [_reward_line(1, 25)], period=PERIOD)

    captured: list[str] = []
    params: list[object] = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        captured.append(" ".join(statement.split()))
        params.append(parameters)

    bind = db_session.connection().engine
    event.listen(bind, "before_cursor_execute", capture)
    try:
        rec.reconcile(db_session, stmt.id)
    finally:
        event.remove(bind, "before_cursor_execute", capture)
    db_session.commit()
    assert _accrual_of(db_session, estimated).state == "bank_confirmed"

    def locks(table):
        return [i for i, s in enumerate(captured) if _LOCK.search(s) and f"FROM {table} " in s + " "]

    statement_lock = locks("account_statement")[0]
    (accrual_lock,) = locks("reward_accrual")
    assert " IN (" in captured[accrual_lock] and "ORDER BY reward_accrual.id" in captured[accrual_lock]
    ledger_locks = locks("ledger_entry")
    # bank wins' own tier-1 lock: the candidates' purchases, ids only, in id order (the population lock reads rows)
    purchase_locks = [i for i in ledger_locks
                      if captured[i].startswith("SELECT ledger_entry.id FROM ledger_entry WHERE ledger_entry.id IN (")
                      and "ORDER BY ledger_entry.id FOR UPDATE" in captured[i]]
    assert len(purchase_locks) == 1, captured
    assert set(_values(params[purchase_locks[0]])) == {posted.id, estimated.id}
    assert statement_lock < purchase_locks[0] < accrual_lock
    assert not [i for i in ledger_locks if i < statement_lock]
    writes = [i for i, s in enumerate(captured) if s.startswith(("UPDATE ledger_entry", "INSERT INTO ledger_entry"))]
    reward_locks = [i for i in ledger_locks if i > accrual_lock]
    assert writes and reward_locks and min(writes) > accrual_lock
    assert not any(i > min(writes + reward_locks) for i in locks("reward_accrual"))


def test_window_lock_record_survives_savepoint_release(seed, db_session):
    """SQLAlchemy fires `after_commit` for a released savepoint: the window record must stay (its rows are still
    locked), so a later `locked=True` call inside reconcile passes; a savepoint rollback and the commit forget it."""
    card = _test_card(seed, db_session)
    rule = _rule(db_session, card)
    buy = _spend(db_session, card, 1000, date(2026, 10, 20), rule)
    db_session.commit()
    accrual = _accrual_of(db_session, buy)
    key = (accrual.rule_id, accrual.window_start)
    with db_session.begin_nested():
        reward_engine.lock_windows(db_session, [key])
    assert key in reward_engine.held_windows(db_session)
    reward_engine.require_window_locked(db_session, *key)
    with pytest.raises(RuntimeError, match="inner"):
        with db_session.begin_nested():
            raise RuntimeError("inner")
    assert key not in reward_engine.held_windows(db_session)  # conservative: re-lock after any rollback
    reward_engine.lock_windows(db_session, [key])
    db_session.commit()
    assert reward_engine.held_windows(db_session) == frozenset()


def _values(parameters) -> list:
    return list(parameters.values()) if isinstance(parameters, dict) else list(parameters or ())


@pytest.mark.parametrize("job_first", [True, False])
def test_job_and_reconcile_same_window_no_deadlock(pg_engine, seed, db_session, run, no_hook, job_first):
    """P12 (carried from Task 7): the daily job's post and a reconcile whose candidates share that window serialise:
    both commit, or the job finds the accrual already posted (`skipped`); no deadlock."""
    set_dirty(db_session, True)
    card = _test_card(seed, db_session)
    rule = _rule(db_session, card)
    first_buy = _spend(db_session, card, 1000, date(2026, 10, 18), rule)
    second_buy = _spend(db_session, card, 2000, date(2026, 10, 20), rule)
    db_session.commit()
    stmt, _, _ = _submit(db_session, run, card, [_reward_line(1, 27)], period=PERIOD)
    accrual_id, stmt_id = _accrual_of(db_session, first_buy).id, stmt.id
    results: list[str] = []

    def job(db):
        results.append(reward_posting.post_due_accrual(db, accrual_id, date(2026, 11, 1)))

    def reconcile(db):
        rec.reconcile(db, stmt_id)

    first, second = (job, reconcile) if job_first else (reconcile, job)
    assert race(pg_engine, first, second) == "committed"
    assert results == (["posted"] if job_first else ["skipped"])
    states = {_accrual_of(db_session, b).state for b in (first_buy, second_buy)}
    assert states == {"bank_confirmed"}
    assert len(_rewards(db_session)) == 2
    assert sum(r.amount for r in _rewards(db_session)) == Decimal("27.0000")


@pytest.mark.parametrize("write", ["put", "reorder"])
def test_reconcile_is_busy_while_a_rule_write_holds_the_rule(seed, db_session, run, pg_engine, write):
    """P12 rule-row tier: a rule write in flight (a PUT, which also holds the purchase; a reorder, which holds only
    the rule rows) makes the reconcile's bank-wins step answer `reconcile_busy` (its FOR SHARE on the candidates'
    rules is NOWAIT) instead of re-deriving an estimate on a rule that is being rewritten; nothing waits."""
    card = _test_card(seed, db_session)
    rule = _rule(db_session, card)
    _spend(db_session, card, 1000, date(2026, 10, 20), rule)
    card_id, rule_id = card.id, rule.id
    db_session.commit()
    stmt, _, _ = _submit(db_session, run, card, [_reward_line(1, 9)], period=PERIOD)
    with sessionmaker(bind=pg_engine, autoflush=False)() as rule_write:
        if write == "put":
            reward_rule_service.update(rule_write, card_id, rule_id, RewardRuleIn(
                name="測試回饋", method="percent", rate="2", posting="after_transaction", delay_days=5))
        else:
            reward_rule_service.reorder(rule_write, card_id, [rule_id])
        with pytest.raises(CodedConflictError) as info:
            rec.reconcile(db_session, stmt.id)
        db_session.rollback()
        rule_write.rollback()
    assert info.value.code == "reconcile_busy"


def test_reconcile_is_busy_while_a_card_write_holds_the_card(seed, db_session, run, pg_engine):
    """P12 card tier (re-check 81f88597): another card is being re-parented onto this card (a card write on the new
    parent: this card FOR UPDATE, none of its rules). The bank-wins step's FOR KEY SHARE on the candidates' card is
    NOWAIT: `reconcile_busy`, nothing waits, nothing re-derived on a card whose children are changing. (Before: the
    re-parent locked nothing of this card.)"""
    card = _test_card(seed, db_session)
    other = _card(seed, db_session, "測試附卡", closing_day=None)
    rule = _rule(db_session, card)
    _spend(db_session, card, 1000, date(2026, 10, 20), rule)
    other_id, body = other.id, dict(name=other.name, currency=other.currency, is_credit=True, closing_day=None,
                                    combined_account_id=card.id)
    db_session.commit()
    stmt, _, _ = _submit(db_session, run, card, [_reward_line(1, 9)], period=PERIOD)
    with sessionmaker(bind=pg_engine, autoflush=False)() as card_write:
        settings_service.update_account(card_write, other_id, AccountIn(**body))
        with pytest.raises(CodedConflictError) as info:
            rec.reconcile(db_session, stmt.id)
        db_session.rollback()
        card_write.rollback()
    assert info.value.code == "reconcile_busy"

