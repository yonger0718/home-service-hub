"""Dividends on the bank statement (portfolio integration spec §4.6; dividends plan rev 2.3, R1–R7, R11): the
`dividend` / `dividend_ambiguous` / `dividend_mismatch` outcomes of a settlement-source bank statement's dividend
lines, `book_dividend` (one income entry under a `kind='dividend'` booking row), its refusals, the policy wiring
(zero-charged, cap-exempt, never from an ambiguity) and its revert. Fictional amounts; the fixture's own accounts
only."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import (
    Account, AccountStatement, LedgerEntry, PolicyBudget, ReconciliationAction, ReconciliationAudit, ReconciliationCase,
    ReconciliationProposal, SettlementBooking, StatementLine,
)
from app.services import dividend_service as ds
from app.services import entry_write_service as ews
from app.services import (
    pair_locks, policy_service, portfolio_client, proposal_service, reconciliation_actions, settings_service,
)
from app.services import reconciliation_service as rec
from app.services import trade_settlement_service as tss
from app.services.reconciliation_actions import engine, engine_locks
from app.services.reconciliation_actions.base import LockSet
from tests.conftest import Seed
from tests.integration.settlement_fixtures import (  # noqa: F401  (portfolio_stub, policy_on are fixtures)
    bank_line, dividend_line, dividend_row, feed, policy_on, portfolio_stub, put_settings, seed_stand_in, settle_line,
    settlement_bank, submit_bank,
)
from tests.integration.statement_fixtures import OCT, _active, _reconcile, run  # noqa: F401
from tests.integration.test_reconciliation_actions_ledger import _apply, _code, _fresh, _refuse, _revert
from tests.integration.test_settlement_actions import _all_cases, _booked
from tests.integration.test_settlement_reconcile import _cases
from tests.integration.test_stand_ins import _put_body


FACTS = {"fingerprint": "dividend:7", "id": 7, "symbol": "9999", "receipt_date": "2026-09-11", "amount": "990.0000",
         "currency": "TWD"}
CONTEXT = {"fingerprint": "dividend:7", "source": "portfolio:cathay", "owner_deleted": False}


def _dividend_case(seed, db, run, stub, *, rows=None, flow="990", day=date(2026, 9, 12)):
    """A live settlement-source bank statement (SEP) printing one 現金股利 deposit; returns (bank, sid, open cases)."""
    bank, _ = settlement_bank(seed, db, target=False)
    stub.dividends = [dividend_row(7, "2026-09-11", "990")] if rows is None else rows
    sid = submit_bank(db, run, bank, [dividend_line(1, day, flow)])
    return bank, sid, _cases(db, sid, "open")


def _plain(case) -> bool:
    return case.kind == "line_unmatched" and not case.candidates and "hint" not in (case.context or {})


def test_book_dividend_books_one_income_entry_idempotently_and_reverts(seed, db_session, run, portfolio_stub):
    bank, sid, (case,) = _dividend_case(seed, db_session, run, portfolio_stub)
    assert (case.kind, case.candidates, case.context) == ("dividend", [FACTS], CONTEXT)
    assert reconciliation_actions.allowed(case) == ["book_dividend", "match", "dismiss"]
    row = _apply(db_session, case.id, "book_dividend", {"fingerprint": "dividend:7"})
    booking = db_session.scalar(select(SettlementBooking))
    entry_id = booking.in_entry_id
    entry = db_session.get(LedgerEntry, entry_id)
    assert (entry.account_id, entry.kind, entry.amount, entry.entry_date, entry.source, entry.name, entry.category_id,
            entry.needs_review) == (bank.id, "income", Decimal("990"), date(2026, 9, 12), "statement", "股利 9999", None, True)
    assert (booking.kind, booking.origin, booking.status, booking.target_account_id, booking.out_entry_id,
            booking.transfer_group_id, booking.event_id, booking.action_id) == (
        "dividend", "bank", "booked", None, None, None, case.event_id, row.id)
    assert (booking.fingerprint, booking.trade_date, booking.settlement_date, booking.net_amount, booking.facts) == (
        "dividend:7", date(2026, 9, 11), date(2026, 9, 11), Decimal("990"), FACTS)
    assert [(i.source, i.source_id, i.amount) for i in booking.items] == [("dividend", "dividend:7", Decimal("990"))]
    assert [(c.entry_id, c.match_rule) for c in _active(db_session, sid)] == [(entry_id, "book_dividend")]
    _reconcile(db_session, sid)
    assert _cases(db_session, sid, "open") == [] and _booked(db_session) == 1     # claimed line, reserved receipt
    _revert(db_session, row.id)
    booking = _fresh(db_session, SettlementBooking, booking.id)
    assert (booking.status, booking.reason, booking.items) == ("retired", "revert", [])
    assert db_session.get(LedgerEntry, entry_id) is None
    (reopened,) = _cases(db_session, sid, "open")
    assert (reopened.id, reopened.kind, reopened.candidates, reopened.context) == (   # the engine notes the revert
        case.id, "line_unmatched", [], {"reverted_action_id": row.id})
    assert db_session.scalars(select(ReconciliationAudit.detail).where(
        ReconciliationAudit.event == "dividend_retired")).one()["reason"] == "revert"


@pytest.mark.parametrize("receipt_date,amount,kind,delta", [
    ("2026-09-15", "990", "dividend", None), ("2026-09-09", "990", "dividend", None),   # ±3 days is inside
    ("2026-09-16", "990", "line_unmatched", None),                                      # four days: not even near
    ("2026-09-11", "989", "line_unmatched", "+1.0000"),                                 # rev 2.2: a near receipt
    ("2026-09-11", "1000", "line_unmatched", "-10.0000"),                               # gross before 匯費: never deducted
])
def test_only_the_exact_net_amount_within_three_days_is_a_dividend(seed, db_session, run, portfolio_stub, receipt_date,
                                                                  amount, kind, delta):
    _, _, (case,) = _dividend_case(seed, db_session, run, portfolio_stub, rows=[dividend_row(7, receipt_date, amount)])
    assert case.kind == kind
    if kind == "line_unmatched":
        assert _plain(case) if delta is None else (
            case.candidates == [] and (case.context["hint"], case.context["nearest"]["delta"]) == ("dividend_mismatch", delta))


def test_two_receipts_make_an_ambiguous_case_the_owner_override_books_one(seed, db_session, run, portfolio_stub,
                                                                         policy_on):
    """Owner override, an explicit owner ruling of 2026-10-31: Q6 (a) constrains automatic booking. The policy books
    nothing from an ambiguity; the owner picks one receipt."""
    policy_on()
    rows = [dividend_row(7, "2026-09-11", "990"), dividend_row(8, "2026-09-13", "990", symbol="9998")]
    _, sid, (case,) = _dividend_case(seed, db_session, run, portfolio_stub, rows=rows)
    assert (case.kind, case.context, [c["fingerprint"] for c in case.candidates]) == (
        "ambiguous", {"hint": "dividend_ambiguous"}, ["dividend:7", "dividend:8"])
    assert "book_dividend" in reconciliation_actions.allowed(case) and "book_settlement" not in reconciliation_actions.allowed(case)
    result = policy_service.run_policy(db_session, sid); db_session.commit()
    assert (result.applied, result.proposed) == ([], []) and _booked(db_session) == 0   # the policy never answers one
    exc = _refuse(db_session, lambda: engine.apply_action(db_session, case_id=case.id, action="book_dividend",
                                                          params={"fingerprint": "dividend:8"}, actor="policy",
                                                          label="policy"))
    assert _code(exc) == "not_a_candidate"                                              # the override is the owner's
    _apply(db_session, case.id, "book_dividend", {"fingerprint": "dividend:8"})
    assert db_session.scalar(select(SettlementBooking.fingerprint)) == "dividend:8"


def test_one_receipt_beside_two_equal_deposits_books_nothing(seed, db_session, run, portfolio_stub, policy_on):
    policy_on()
    bank, _ = settlement_bank(seed, db_session, target=False)
    portfolio_stub.dividends = [dividend_row(7, "2026-09-12", "990")]
    sid = submit_bank(db_session, run, bank, [dividend_line(1, date(2026, 9, 12), "990"),
                                              dividend_line(2, date(2026, 9, 13), "990")])
    assert [(c.kind, c.context, [x["fingerprint"] for x in c.candidates]) for c in _cases(db_session, sid, "open")] == \
        [("ambiguous", {"hint": "dividend_ambiguous"}, ["dividend:7"])] * 2
    result = policy_service.run_policy(db_session, sid); db_session.commit()
    assert (result.applied, result.proposed) == ([], []) and _booked(db_session) == 0


def test_a_receipt_beside_an_owner_claimed_line_is_never_handed_to_its_neighbour(seed, db_session, run, portfolio_stub,
                                                                                policy_on):
    """The symmetric rule counts the owner-claimed line as a taker: the receipt fits both lines, so the unclaimed
    neighbour is `ambiguous` (listing it) and is never assigned or auto-booked."""
    policy_on()
    bank, _ = settlement_bank(seed, db_session, target=False)
    seed.entry(bank, "990", kind="income", day=date(2026, 9, 9)); db_session.commit()      # claims line 1 (same day)
    portfolio_stub.dividends = [dividend_row(7, "2026-09-12", "990")]                       # 3 days from both lines
    sid = submit_bank(db_session, run, bank, [dividend_line(1, date(2026, 9, 9), "990"),
                                              dividend_line(2, date(2026, 9, 15), "990")])  # 6 days from the owner entry
    (case,) = _cases(db_session, sid, "open")
    assert (case.kind, case.context, [c["fingerprint"] for c in case.candidates]) == (
        "ambiguous", {"hint": "dividend_ambiguous"}, ["dividend:7"])
    policy_service.run_policy(db_session, sid); db_session.commit()
    assert _booked(db_session) == 0


def test_a_competing_receipt_confirmed_after_the_case_is_refused_for_owner_and_policy(seed, db_session, run,
                                                                                     portfolio_stub, policy_on):
    """Rev 2.1 Must 2: the case was unique when listed; a second equal receipt confirmed since, on another day inside
    the line's window, makes it an ambiguity — `_plan` re-runs the assignment and refuses. The policy applies through
    the same action, so it is refused the same way and books nothing."""
    policy_on()
    _, sid, (case,) = _dividend_case(seed, db_session, run, portfolio_stub)          # unique: dividend:7 (2026-09-11)
    assert case.kind == "dividend"
    portfolio_stub.dividends = [dividend_row(7, "2026-09-11", "990"), dividend_row(8, "2026-09-14", "990")]
    exc = _refuse(db_session, lambda: _apply(db_session, case.id, "book_dividend", {"fingerprint": "dividend:7"}))
    assert _code(exc) == "candidate_changed"
    result = policy_service.run_policy(db_session, sid); db_session.commit()
    assert result.applied == [] and result.skipped.get("candidate_changed") == 1 and _booked(db_session) == 0


def test_an_account_currency_change_racing_the_booking_is_refused_under_the_locks(pg_engine, seed, db_session, run,
                                                                                  portfolio_stub, monkeypatch):
    """Rev 2.1 Should 3: prepare's read saw a TWD account; a concurrent writer changes it to USD before the account
    lock. `_plan` re-checks the refreshed account against its source, the statement and the receipt."""
    bank, _, (case,) = _dividend_case(seed, db_session, run, portfolio_stub)
    original = engine_locks.lock_account_and_barrier

    def racing(db, targets, statement):
        with Session(pg_engine) as writer:
            writer.execute(update(Account).where(Account.id == bank.id).values(currency="USD"))
            writer.commit()
        original(db, targets, statement)

    monkeypatch.setattr(engine_locks, "lock_account_and_barrier", racing)
    exc = _refuse(db_session, lambda: _apply(db_session, case.id, "book_dividend", {"fingerprint": "dividend:7"}))
    assert _code(exc) == "currency_mismatch" and _booked(db_session) == 0
    assert db_session.scalar(select(func.count(LedgerEntry.id)).where(LedgerEntry.source == "statement")) == 0


def test_both_feed_reads_come_before_any_pair_or_candidate_lock_with_one_cached_client(seed, db_session, run, portfolio_stub,
                                                                         monkeypatch):
    """Rev 2.1 Should 2, the Phase 1 `test_the_window_read_comes_before_any_row_lock` shape: a statement printing a
    settlement line AND a dividend line; tss.housekeep locks an owner-deleted stand-in (row → legs NOWAIT)."""
    bank, target = settlement_bank(seed, db_session)
    booking = seed_stand_in(db_session, bank, target, settlement_date=date(2026, 9, 14), net="-12305",
                            fingerprint="cathay:2026-09-10:aa", ids=("ct:5",))
    ews.delete_entry(db_session, booking.out_entry_id); db_session.commit()
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)))
    portfolio_stub.dividends = [dividend_row(7, "2026-09-11", "990")]
    seen, clients = [], []

    def spy(name, real):
        def wrapped(*args, **kwargs):
            seen.append((name, len(portfolio_stub.calls), len(portfolio_stub.dividend_calls)))
            return real(*args, **kwargs)
        return wrapped

    for module, name in ((pair_locks, "lock_row"), (pair_locks, "lock_legs"), (rec, "_lock_candidates")):
        monkeypatch.setattr(module, name, spy(name, getattr(module, name)))
    real_cached = portfolio_client.cached
    monkeypatch.setattr(portfolio_client, "cached", lambda client: clients.append(client) or real_cached(client))
    submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305"),
                                        dividend_line(2, date(2026, 9, 12), "990")])
    assert {"lock_row", "_lock_candidates"} <= {name for name, *_ in seen}
    assert {(s, d) for _, s, d in seen} == {(1, 1)} and len(clients) == 1   # every lock after both reads, one client


def test_an_owner_income_entry_claims_the_line_first(seed, db_session, run, portfolio_stub):
    bank, _ = settlement_bank(seed, db_session, target=False)
    seed.entry(bank, "990", kind="income", day=date(2026, 9, 12)); db_session.commit()
    portfolio_stub.dividends = [dividend_row(7, "2026-09-11", "990")]
    sid = submit_bank(db_session, run, bank, [dividend_line(1, date(2026, 9, 12), "990")])
    assert _cases(db_session, sid, "open") == [] and _booked(db_session) == 0


def test_policy_books_dividends_uncapped_and_charges_nothing(seed, db_session, run, portfolio_stub, policy_on):
    policy_on(max_amount_everywhere="1", per_statement_count=0, per_statement_total="0", monthly_total="0")
    _, sid, _ = _dividend_case(seed, db_session, run, portfolio_stub, rows=[dividend_row(7, "2026-09-11", "999999")],
                               flow="999999")
    policy_service.run_policy(db_session, sid); db_session.commit()
    assert db_session.scalar(select(SettlementBooking.status)) == "booked"
    assert db_session.scalar(select(func.coalesce(func.sum(PolicyBudget.used_count), 0))) == 0
    _reconcile(db_session, sid); policy_service.run_policy(db_session, sid)
    assert db_session.scalar(select(func.count(SettlementBooking.id))) == 1


@pytest.mark.parametrize("failure,code", [("down", "http_503"), ("incomplete", "incomplete"), ("usd", "currency_mismatch")])
def test_an_unreadable_dividend_feed_leaves_the_ordinary_case(seed, db_session, run, portfolio_stub, policy_on, failure, code):
    policy_on()
    portfolio_stub.down, portfolio_stub.dividends_complete = failure == "down", failure != "incomplete"
    rows = [dividend_row(7, "2026-09-11", "990", currency="USD" if failure == "usd" else "TWD")]
    _, sid, (case,) = _dividend_case(seed, db_session, run, portfolio_stub, rows=rows)
    result = _reconcile(db_session, sid); policy_service.run_policy(db_session, sid); db_session.commit()
    assert _plain(case) and result.settlement_source_unavailable is True
    assert result.settlements == {"dividend": 0, "dividend_ambiguous": 0, "dividend_mismatch": 0,
                                  "dividend_unavailable_code": code}
    assert _booked(db_session) == 0


def test_one_dividend_read_per_reconcile_and_none_without_a_label_or_source(seed, db_session, run, portfolio_stub):
    bank, sid, _ = _dividend_case(seed, db_session, run, portfolio_stub)
    before = len(portfolio_stub.dividend_calls)
    _reconcile(db_session, sid)
    assert len(portfolio_stub.dividend_calls) == before + 1 and portfolio_stub.calls == []
    assert portfolio_stub.dividend_calls[-1] == {"broker": "cathay", "from": "2026-08-29", "to": "2026-10-03"}
    plain = seed.account("活存", statement_live_from=date(2026, 1, 1)); db_session.commit()
    no_source = submit_bank(db_session, run, plain, [dividend_line(1, date(2026, 9, 12), "990")])  # no settlement_source
    no_label = submit_bank(db_session, run, bank, [bank_line(1, date(2026, 10, 5), "薪資", "990")], period=OCT)
    for other in (no_source, no_label):                                     # R3's gate: nothing dividend-related
        result = _reconcile(db_session, other)
        assert (result.settlements, result.settlement_source_unavailable) == ({}, False)
    assert len(portfolio_stub.dividend_calls) == before + 1


def test_a_receipt_confirmed_later_is_offered_on_the_next_reconcile(seed, db_session, run, portfolio_stub):
    _, sid, (case,) = _dividend_case(seed, db_session, run, portfolio_stub, rows=[])     # still pending: not in the feed
    assert _plain(case) and sid in tss.recheck_targets(db_session, {})
    portfolio_stub.dividends = [dividend_row(7, "2026-09-11", "990")]
    result = _reconcile(db_session, sid)
    (case,) = _cases(db_session, sid, "open")
    assert case.kind == "dividend"
    assert result.settlements == {"dividend": 1, "dividend_ambiguous": 0, "dividend_mismatch": 0,
                                  "dividend_unavailable_code": None}
    assert sid in tss.recheck_targets(db_session, {})                                  # R11: an open `dividend` case
    _apply(db_session, case.id, "book_dividend", {"fingerprint": "dividend:7"})
    assert sid not in tss.recheck_targets(db_session, {})


def test_a_fee_corrected_receipt_turns_the_mismatch_into_a_booking(seed, db_session, run, portfolio_stub, policy_on):
    """Owner ruling 2026-10-31 (fictional figures): a lent-out stock's dividend arrives without the 10 TWD 匯費, so the
    bank line is 2,010 beside a receipt of 2,000 (fee 10). The owner sets the fee to 0 in the portfolio
    (`update_deductions`: amount 2,010, revision + 1, receipt_date unchanged). R11 keeps the statement in the re-check
    while the mismatch case is open, so the next re-check reconciles it, opens the `dividend` case, and the policy
    books it."""
    policy_on()
    _, sid, (case,) = _dividend_case(seed, db_session, run, portfolio_stub, rows=[dividend_row(7, "2026-09-11", "2000")],
                                     flow="2010")
    assert (case.kind, case.candidates, case.context) == ("line_unmatched", [], {
        "hint": "dividend_mismatch",
        "nearest": {"id": 7, "symbol": "9999", "receipt_date": "2026-09-11", "amount": "2000.0000", "delta": "+10.0000"}})
    policy_service.run_policy(db_session, sid); db_session.commit()
    assert _booked(db_session) == 0 and sid in tss.recheck_targets(db_session, {})          # a hint, never a booking
    portfolio_stub.dividends = [dividend_row(7, "2026-09-11", "2010")]                       # the fee corrected to 0
    assert tss.flag_statements(db_session, tss.recheck_targets(db_session, {})) == 1
    result = _reconcile(db_session, sid)
    assert result.settlements == {"dividend": 1, "dividend_ambiguous": 0, "dividend_mismatch": 0,
                                  "dividend_unavailable_code": None}
    (case,) = _cases(db_session, sid, "open")
    assert (case.kind, [c["amount"] for c in case.candidates]) == ("dividend", ["2010.0000"])
    policy_service.run_policy(db_session, sid); db_session.commit()
    assert db_session.scalar(select(SettlementBooking.net_amount)) == Decimal("2010")
    assert tss.recheck_targets(db_session, {}) == set()                                      # the line is claimed


def test_a_dismissed_plain_case_hides_the_dividend_once_the_receipt_is_confirmed(seed, db_session, run, portfolio_stub,
                                                                                policy_on):
    policy_on()
    _, sid, (case,) = _dividend_case(seed, db_session, run, portfolio_stub, rows=[])
    _apply(db_session, case.id, "dismiss", {"note": "owner: not a dividend"})
    portfolio_stub.dividends = [dividend_row(7, "2026-09-11", "990")]
    _reconcile(db_session, sid); policy_service.run_policy(db_session, sid); db_session.commit()
    assert _booked(db_session) == 0 and _all_cases(db_session, sid) == [("line_unmatched", None, "dismissed")]


def test_a_dismissed_ambiguity_holds_when_it_narrows_to_one(seed, db_session, run, portfolio_stub, policy_on):
    policy_on()
    rows = [dividend_row(7, "2026-09-11", "990"), dividend_row(8, "2026-09-13", "990")]
    _, sid, (case,) = _dividend_case(seed, db_session, run, portfolio_stub, rows=rows)
    _apply(db_session, case.id, "dismiss", {"note": "owner: later"})
    portfolio_stub.dividends = rows[:1]
    _reconcile(db_session, sid); policy_service.run_policy(db_session, sid); db_session.commit()
    assert _booked(db_session) == 0 and _all_cases(db_session, sid) == [("ambiguous", "dividend_ambiguous", "dismissed")]


def test_a_dismissed_dividend_case_holds_while_the_feed_is_down(seed, db_session, run, portfolio_stub):
    """Fix round 1, Important 1 (owner wins, R6): the line falls back to its plain case while the feed is down; the
    owner's dismissal of the `dividend` case still holds and nothing reopens."""
    _, sid, (case,) = _dividend_case(seed, db_session, run, portfolio_stub)
    _apply(db_session, case.id, "dismiss", {"note": "owner: handled"})
    portfolio_stub.down = True
    result = _reconcile(db_session, sid)
    assert result.settlement_source_unavailable is True
    assert _all_cases(db_session, sid) == [("dividend", None, "dismissed")] and _cases(db_session, sid, "open") == []


def test_a_dismissed_dividend_case_holds_once_the_receipt_leaves_the_feed(seed, db_session, run, portfolio_stub):
    _, sid, (case,) = _dividend_case(seed, db_session, run, portfolio_stub)
    _apply(db_session, case.id, "dismiss", {"note": "owner: handled"})
    portfolio_stub.dividends = []                                           # gone, or no longer confirmed
    result = _reconcile(db_session, sid)
    assert result.settlements["dividend"] == 0 and result.settlement_source_unavailable is False
    assert _all_cases(db_session, sid) == [("dividend", None, "dismissed")] and _cases(db_session, sid, "open") == []


def test_the_recheck_takes_plain_cases_of_dividend_lines_only(seed, db_session, run, portfolio_stub):
    """Fix round 1, Minor 2: the plain-line shape mirrors `is_dividend_line` and `_plain` — no candidates, no
    settlement label, the rules' `dividend_labels`."""
    _, sid, (case,) = _dividend_case(seed, db_session, run, portfolio_stub, rows=[])        # plain: still pending
    assert ds.recheck_statement_ids(db_session) == [sid]
    case.candidates = [{"entry_id": 1}]; db_session.commit()                                # listing candidates
    assert ds.recheck_statement_ids(db_session) == []
    case.candidates = []; db_session.commit()
    line = db_session.get(StatementLine, case.line_id)
    line.merchant_norm = "交割股款 股利"; db_session.commit()                                # a settlement label wins
    assert ds.recheck_statement_ids(db_session) == []
    line.merchant_norm = "配息"; db_session.commit()
    assert ds.recheck_statement_ids(db_session) == []
    put_settings(db_session, rules={"dividend_labels": ["配息"]})                            # the rules' labels
    assert ds.recheck_statement_ids(db_session) == [sid]


def test_the_dividend_family_and_its_dismissal_rule():
    from app.services.reconciliation_service import _dismissed, _settlement_family
    plain = SimpleNamespace(kind="line_unmatched", event_id=7, entry_id=None, context={})
    book = {"kind": "dividend", "event_id": 7, "entry_id": None, "context": {"fingerprint": "dividend:7"}}
    many = {"kind": "ambiguous", "event_id": 7, "entry_id": None, "context": {"hint": "dividend_ambiguous"}}
    settle = {"kind": "settlement", "event_id": 7, "entry_id": None, "context": {"fingerprint": "f"}}
    near = {"kind": "line_unmatched", "event_id": 7, "entry_id": None, "context": {"hint": "dividend_mismatch"}}
    assert _settlement_family("dividend", {}) and _settlement_family("ambiguous", {"hint": "dividend_ambiguous"})
    assert _settlement_family("line_unmatched", {"hint": "dividend_mismatch"})              # rev 2.2
    assert _dismissed([plain], book) and _dismissed([plain], many) and not _dismissed([plain], dict(book, event_id=8))
    assert _dismissed([plain], near) and _dismissed([SimpleNamespace(**near)], book)       # holds across a fee fix
    assert not _dismissed([plain], settle)                   # a plain dismissal stands in for the dividend family only
    want = {"kind": "line_unmatched", "event_id": 7, "entry_id": None, "context": {}}       # fix round 1: both ways
    assert _dismissed([SimpleNamespace(**book)], want) and _dismissed([SimpleNamespace(**many)], want)
    assert not _dismissed([SimpleNamespace(**book)], dict(want, event_id=8))
    assert not _dismissed([SimpleNamespace(**settle)], want)
    deleted = dict(want, context={"hint": "stand_in_deleted", "owner_deleted": True})      # final review M1
    assert _dismissed([SimpleNamespace(**book)], deleted)
    assert not _dismissed([SimpleNamespace(**book)], dict(deleted, event_id=8))
    assert not set(ds.FAMILY_HINTS) & set(tss.SETTLEMENT_CASE_HINTS)                       # never a stand-in hint


CHANGES = {"amount": lambda s: setattr(s, "dividends", [dividend_row(7, "2026-09-11", "980")]),
           "gone": lambda s: setattr(s, "dividends", []),
           "moved": lambda s: setattr(s, "dividends", [dividend_row(7, "2026-09-13", "990")]),
           "down": lambda s: setattr(s, "down", True)}


@pytest.mark.parametrize("change,code", [("amount", "candidate_changed"), ("gone", "candidate_changed"),
                                         ("moved", "candidate_changed"), ("down", "source_unavailable")])
def test_a_changed_or_unreadable_receipt_is_refused(seed, db_session, run, portfolio_stub, change, code):
    _, _, (case,) = _dividend_case(seed, db_session, run, portfolio_stub)
    CHANGES[change](portfolio_stub)
    exc = _refuse(db_session, lambda: _apply(db_session, case.id, "book_dividend", {"fingerprint": "dividend:7"}))
    assert _code(exc) == code and _booked(db_session) == 0


def test_a_fingerprint_outside_the_candidates_is_refused(seed, db_session, run, portfolio_stub):
    _, _, (case,) = _dividend_case(seed, db_session, run, portfolio_stub)
    exc = _refuse(db_session, lambda: _apply(db_session, case.id, "book_dividend", {"fingerprint": "dividend:8"}))
    assert _code(exc) == "not_a_candidate"


def test_another_event_holding_the_receipt_is_already_booked(seed, db_session, run, portfolio_stub):
    bank, _, (case,) = _dividend_case(seed, db_session, run, portfolio_stub)
    _apply(db_session, case.id, "book_dividend", {"fingerprint": "dividend:7"})
    other = submit_bank(db_session, run, bank, [dividend_line(1, date(2026, 10, 1), "990")], period=OCT)
    (plain,) = _cases(db_session, other, "open")
    forced = ReconciliationCase(statement_id=other, revision_id=plain.revision_id, event_id=plain.event_id,  # a stale listing
                                line_id=plain.line_id, kind="dividend", status="open", candidates=case.candidates,
                                context=case.context)
    db_session.add(forced); db_session.commit()
    exc = _refuse(db_session, lambda: _apply(db_session, forced.id, "book_dividend", {"fingerprint": "dividend:7"}))
    assert _code(exc) == "already_booked" and _booked(db_session) == 1


def test_a_second_listing_of_the_booked_event_replays(seed, db_session, run, portfolio_stub):
    _, sid, (case,) = _dividend_case(seed, db_session, run, portfolio_stub)
    first = _apply(db_session, case.id, "book_dividend", {"fingerprint": "dividend:7"})
    again = ReconciliationCase(statement_id=sid, revision_id=case.revision_id, event_id=case.event_id, line_id=case.line_id,
                               kind="dividend", status="open", candidates=case.candidates, context=case.context)
    db_session.add(again); db_session.commit()
    row, replayed = engine.apply_action(db_session, case_id=again.id, action="book_dividend",
                                        params={"fingerprint": "dividend:7"}, actor="owner", label="owner-test")
    db_session.commit()
    assert (row.id, replayed) == (first.id, True) and db_session.scalar(select(func.count(LedgerEntry.id))) == 1


@pytest.mark.parametrize("kind", ["income", "expense", "missing"])
def test_the_dividend_category_must_be_an_existing_income_category(seed, db_session, run, portfolio_stub, kind):
    category_id = 999999 if kind == "missing" else seed.category("股利收入", kind).id
    db_session.commit()
    block = settings_service.read_settlement_settings(db_session)
    put_settings(db_session, settlement={**{k: v for k, v in block.items() if k in settings_service.SETTLEMENT_DEFAULTS},
                                         "dividend_category_id": category_id})
    _, _, (case,) = _dividend_case(seed, db_session, run, portfolio_stub)
    _apply(db_session, case.id, "book_dividend", {"fingerprint": "dividend:7"})
    entry = db_session.get(LedgerEntry, db_session.scalar(select(SettlementBooking.in_entry_id)))
    assert (entry.category_id, entry.needs_review) == ((category_id, False) if kind == "income" else (None, True))


def test_book_dividend_takes_the_account_lock_and_barrier():
    assert reconciliation_actions.ACTIONS["book_dividend"].lock_targets(None, None) == LockSet(account_lock=True, barrier=True)


def test_policy_refuses_when_an_owner_income_entry_appears_under_the_barrier(pg_engine, seed, db_session, run, portfolio_stub,
                                                                            policy_on, monkeypatch):
    policy_on()
    bank, sid, _ = _dividend_case(seed, db_session, run, portfolio_stub)
    (cand,) = policy_service.collect_candidates(db_session, db_session.get(AccountStatement, sid),
                                                settings_service.reconciliation_policy(db_session))
    original = policy_service._policy_precheck

    def racing(candidate, ctx):                    # after the precheck, before the account lock and the barrier
        original(candidate, ctx)
        with Session(pg_engine) as writer:
            Seed(writer).entry(writer.get(Account, bank.id), "990", kind="income", day=date(2026, 9, 13))
            writer.commit()

    monkeypatch.setattr(policy_service, "_policy_precheck", racing)
    exc = _refuse(db_session, lambda: policy_service.apply_candidate(db_session, db_session.get(AccountStatement, sid), cand,
                                                                     month=date(2026, 9, 1)))
    assert _code(exc) == "candidate_changed" and _booked(db_session) == 0


# --- owner wins (R6, spec §4.7 single leg) ----------------------------------------------------------------------------


def test_an_owner_delete_tombstones_the_booking_and_the_policy_proposes(seed, db_session, run, portfolio_stub, policy_on):
    policy_on()
    _, sid, (case,) = _dividend_case(seed, db_session, run, portfolio_stub)
    _apply(db_session, case.id, "book_dividend", {"fingerprint": "dividend:7"})
    booking = db_session.scalar(select(SettlementBooking))
    ews.delete_entry(db_session, booking.in_entry_id); db_session.commit()
    _reconcile(db_session, sid); policy_service.run_policy(db_session, sid); db_session.commit()
    row = _fresh(db_session, SettlementBooking, booking.id)
    assert (row.status, row.items) == ("owner_deleted", [])
    (case,) = _cases(db_session, sid, "open", "proposed")
    assert (case.kind, case.context["owner_deleted"], case.status) == ("dividend", True, "proposed")
    assert _booked(db_session) == 0
    assert db_session.scalar(select(func.count(ReconciliationAudit.id)).where(
        ReconciliationAudit.event == "dividend_owner_deleted")) == 1


def test_an_owner_delete_during_a_feed_outage_stays_rechecked_and_the_policy_proposes(seed, db_session, run,
                                                                                       portfolio_stub, policy_on):
    """Final review M1: book, the owner deletes the income entry, reconcile with the feed down — the line's case is
    `line_unmatched` hinted `stand_in_deleted`; R11 still re-checks the statement, and once the feed answers the
    `dividend` case (owner-deleted) is back and the policy proposes it."""
    policy_on()
    _, sid, (case,) = _dividend_case(seed, db_session, run, portfolio_stub)
    _apply(db_session, case.id, "book_dividend", {"fingerprint": "dividend:7"})
    ews.delete_entry(db_session, db_session.scalar(select(SettlementBooking.in_entry_id))); db_session.commit()
    portfolio_stub.down = True
    _reconcile(db_session, sid); policy_service.run_policy(db_session, sid); db_session.commit()
    (case,) = _cases(db_session, sid, "open", "proposed")
    assert (case.kind, case.context["hint"], case.context["owner_deleted"], case.candidates) == (
        "line_unmatched", "stand_in_deleted", True, [])
    assert ds.recheck_statement_ids(db_session) == [sid]
    portfolio_stub.down = False
    _reconcile(db_session, sid); policy_service.run_policy(db_session, sid); db_session.commit()
    (case,) = _cases(db_session, sid, "open", "proposed")
    assert (case.kind, case.context["owner_deleted"], case.status) == ("dividend", True, "proposed")
    assert _booked(db_session) == 0


def test_the_owner_may_book_the_proposal_after_an_owner_delete(seed, db_session, run, portfolio_stub, policy_on):
    """The sweep retires the deleted entry's `book_dividend` action (`RETIRABLE_ACTIONS`), so the create slot is free
    and the owner's answer to the policy's proposal books the receipt again (the Phase 1 settlement test's shape)."""
    policy_on()
    _, sid, (case,) = _dividend_case(seed, db_session, run, portfolio_stub)
    first = _apply(db_session, case.id, "book_dividend", {"fingerprint": "dividend:7"})
    ews.delete_entry(db_session, db_session.scalar(select(SettlementBooking.in_entry_id))); db_session.commit()
    _reconcile(db_session, sid); policy_service.run_policy(db_session, sid)
    assert _fresh(db_session, ReconciliationAction, first.id).status == "reverted"
    (proposal,) = db_session.scalars(select(ReconciliationProposal).where(ReconciliationProposal.status == "pending"))
    assert (proposal.author, proposal.action) == ("policy", "book_dividend")
    proposal_service.apply_proposal(db_session, proposal_id=proposal.id, label="owner-test", request_id=None)
    db_session.commit()
    assert sorted(db_session.scalars(select(SettlementBooking.status))) == ["booked", "owner_deleted"]


@pytest.mark.parametrize("edit,status", [("category", "booked"), ("amount", "owner_edited")])
def test_an_owner_edit_tombstones_the_booking_only_when_structural(seed, db_session, run, portfolio_stub, client, policy_on,
                                                                  edit, status):
    """Owner ruling 2026-10-31, Phase 1 parity (`trade_settlement_service.py:312-318`): a structural edit tombstones the
    row `owner_edited` and KEEPS its item, so the receipt stays reserved. Neither the next reconcile nor the policy
    opens or proposes anything for it, and nothing is booked."""
    policy_on()
    _, sid, (case,) = _dividend_case(seed, db_session, run, portfolio_stub)
    _apply(db_session, case.id, "book_dividend", {"fingerprint": "dividend:7"})
    booking = db_session.scalar(select(SettlementBooking))
    entry = db_session.get(LedgerEntry, booking.in_entry_id)
    # 500, far from 990: the line is unmatched again, yet the kept item still reserves the receipt
    fields = {"category_id": seed.category("股利收入", "income").id} if edit == "category" else {"amount": "500"}
    db_session.commit()
    assert client.put(f"/entries/{entry.id}", json=_put_body(entry, **fields)).status_code == 200
    for _ in range(2):                                                               # the next runs, too
        _reconcile(db_session, sid); policy_service.run_policy(db_session, sid); db_session.commit()
        row = _fresh(db_session, SettlementBooking, booking.id)
        assert row.status == status and [(i.source, i.source_id) for i in row.items] == [("dividend", "dividend:7")]
        assert tss.reservations(db_session, kind="dividend") == (
            ({"dividend:7"}, {"dividend:7"}) if status == "booked" else (set(), {"dividend:7"}))
        assert _booked(db_session) == (1 if status == "booked" else 0)
        assert db_session.scalar(select(func.count(SettlementBooking.id))) == 1
        assert [c.id for c in _cases(db_session, sid, "open", "proposed")
                if c.kind == "dividend" or (c.context or {}).get("hint") in ds.FAMILY_HINTS] == []
        assert db_session.scalar(select(func.count(ReconciliationProposal.id))) == 0
    assert db_session.scalar(select(func.count(ReconciliationAudit.id)).where(
        ReconciliationAudit.event == "dividend_owner_edited")) == (1 if status == "owner_edited" else 0)


def test_dividend_housekeeping_locks_come_after_the_dividend_read(seed, db_session, run, portfolio_stub, monkeypatch):
    """Rev 2.1 Should 2: `dividend_service.housekeep` locks the owner-deleted booking (row → entry NOWAIT) only after
    the reconcile's one dividend read."""
    _, sid, (case,) = _dividend_case(seed, db_session, run, portfolio_stub)
    _apply(db_session, case.id, "book_dividend", {"fingerprint": "dividend:7"})
    ews.delete_entry(db_session, db_session.scalar(select(SettlementBooking.in_entry_id))); db_session.commit()
    before, seen = len(portfolio_stub.dividend_calls), []

    def spy(name, real):
        def wrapped(*args, **kwargs):
            seen.append((name, len(portfolio_stub.dividend_calls)))
            return real(*args, **kwargs)
        return wrapped

    for module, name in ((pair_locks, "lock_row"), (pair_locks, "lock_legs"), (rec, "_lock_candidates")):
        monkeypatch.setattr(module, name, spy(name, getattr(module, name)))
    _reconcile(db_session, sid)
    assert {"lock_row", "_lock_candidates"} <= {name for name, _ in seen}
    assert {calls for _, calls in seen} == {before + 1}


def test_an_owner_income_entry_arriving_later_retires_the_booking(seed, db_session, run, portfolio_stub):
    bank, sid, (case,) = _dividend_case(seed, db_session, run, portfolio_stub)
    _apply(db_session, case.id, "book_dividend", {"fingerprint": "dividend:7"})
    booking = db_session.scalar(select(SettlementBooking))
    system_id = booking.in_entry_id
    owner = seed.entry(bank, "990", kind="income", day=date(2026, 9, 13)); db_session.commit()
    _reconcile(db_session, sid)
    row = _fresh(db_session, SettlementBooking, booking.id)
    assert (row.status, row.reason, row.items) == ("retired", "twin", []) and db_session.get(LedgerEntry, system_id) is None
    assert [c.entry_id for c in _active(db_session, sid)] == [owner.id] and _cases(db_session, sid, "open") == []
    assert db_session.scalars(select(ReconciliationAudit.detail).where(
        ReconciliationAudit.event == "dividend_retired")).one()["reason"] == "twin"
