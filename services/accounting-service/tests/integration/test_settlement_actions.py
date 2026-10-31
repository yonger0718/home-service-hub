"""R7 Phase 1 `book_settlement` (portfolio integration spec §4.4, §4.5, §4.7 Locks, §9): the owner / policy action that
books a bank settlement line as a bank ↔ target transfer under one `settlement_booking` owner row, its refusals, the
policy wiring (zero-charged, cap-exempt, owner-deleted → proposed) and its revert. Fictional amounts; the fixture's
own accounts only."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AccountStatement, LedgerEntry, PolicyBudget, ReconciliationAction, ReconciliationCase, ReconciliationProposal,
    SettlementBooking,
)
from app.services import entry_write_service as ews
from app.services import policy_service, proposal_service, reconciliation_actions, settings_service, transfer_service
from app.services.errors import CodedConflictError
from app.services.reconciliation_actions import engine
from app.services.reconciliation_actions.base import LockSet
from tests.conftest import Seed
from tests.integration.settlement_fixtures import (  # noqa: F401  (portfolio_stub, policy_on are fixtures)
    feed, policy_on, portfolio_stub, settle_line, settlement_bank, submit_bank,
)
from tests.integration.statement_fixtures import OCT, _active, _reconcile, _transfer, run  # noqa: F401
from tests.integration.test_reconciliation_actions_ledger import _apply, _code, _fresh, _refuse, _revert
from tests.integration.test_settlement_reconcile import _cases


def test_book_settlement_books_two_legs_one_owner_and_reverts(seed, db_session, run, portfolio_stub):
    bank, target = settlement_bank(seed, db_session)
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5", "ct:7")), amounts=("-12300", "-5"))
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")])
    (case,) = _cases(db_session, sid, "open")
    row = _apply(db_session, case.id, "book_settlement", {"fingerprint": case.context["fingerprint"]})
    booking = db_session.scalar(select(SettlementBooking))
    leg_ids = (booking.out_entry_id, booking.in_entry_id)   # ids, not rows: the reverted legs are gone from the session
    legs = [db_session.get(LedgerEntry, i) for i in leg_ids]
    assert (booking.origin, booking.status, booking.event_id) == ("bank", "booked", case.event_id)
    assert [(l.account_id, l.amount) for l in legs] == [(bank.id, Decimal("-12305")), (target.id, Decimal("12305"))]
    assert sorted((i.source, i.source_id, i.amount) for i in booking.items) == [("ct", "ct:5", Decimal("-12300")), ("ct", "ct:7", Decimal("-5"))]
    _revert(db_session, row.id)
    db_session.refresh(booking)
    assert booking.status == "retired" and booking.items == [] and all(db_session.get(LedgerEntry, i) is None for i in leg_ids)


def test_book_settlement_writes_the_transfer_the_spec_names(seed, db_session, run, portfolio_stub):
    bank, target = settlement_bank(seed, db_session)
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-12", "8000", ("ct:9",)))                  # a sell day: target → bank
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 12), "8000")])
    (case,) = _cases(db_session, sid, "open")
    row = _apply(db_session, case.id, "book_settlement", {"fingerprint": case.context["fingerprint"]})
    booking = db_session.scalar(select(SettlementBooking))
    out_leg, in_leg = db_session.get(LedgerEntry, booking.out_entry_id), db_session.get(LedgerEntry, booking.in_entry_id)
    assert [(l.account_id, l.amount, l.entry_date, l.posted_date, l.source) for l in (out_leg, in_leg)] == [
        (target.id, Decimal("-8000"), date(2026, 9, 12), date(2026, 9, 12), "statement"),
        (bank.id, Decimal("8000"), date(2026, 9, 12), date(2026, 9, 12), "statement")]
    assert {(l.name, l.description, l.needs_review) for l in (out_leg, in_leg)} == {("證券交割", "證券交割 2026-09-10", False)}
    assert (booking.target_account_id, booking.action_id, booking.settlement_date, booking.trade_date) == (
        target.id, row.id, date(2026, 9, 12), date(2026, 9, 10))
    assert booking.facts == case.candidates[0] and booking.transfer_group_id == out_leg.transfer_group_id
    assert [(c.entry_id, c.match_rule) for c in _active(db_session, sid)] == [(in_leg.id, "book_settlement")]


def test_book_settlement_takes_the_account_lock_and_barrier():
    assert reconciliation_actions.ACTIONS["book_settlement"].lock_targets(None, None) == LockSet(account_lock=True, barrier=True)


def test_policy_books_settlements_uncapped_and_charges_nothing(seed, db_session, run, portfolio_stub, policy_on):
    policy_on(max_amount_everywhere="1", per_statement_count=0, per_statement_total="0", monthly_total="0")
    bank, _ = settlement_bank(seed, db_session)
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-999999", ("ct:5",)))
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-999999")])
    policy_service.run_policy(db_session, sid)
    assert db_session.scalar(select(SettlementBooking.status)) == "booked"
    assert db_session.scalar(select(func.coalesce(func.sum(PolicyBudget.used_count), 0))) == 0
    _reconcile(db_session, sid); policy_service.run_policy(db_session, sid)                 # idempotent
    assert db_session.scalar(select(func.count(SettlementBooking.id))) == 1


def _case_and_booking(seed, db_session, run, portfolio_stub, *, net="-12305", ids=("ct:5",)):
    """One live bank statement with one settlement line and its unique feed row; returns (bank, target, sid, case)."""
    bank, target = settlement_bank(seed, db_session)
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", net, ids))
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), net)])
    (case,) = _cases(db_session, sid, "open")
    return bank, target, sid, case


def test_policy_proposes_an_owner_deleted_settlement(seed, db_session, run, portfolio_stub, policy_on):
    policy_on()
    bank, target, sid, case = _case_and_booking(seed, db_session, run, portfolio_stub)
    row = _apply(db_session, case.id, "book_settlement", {"fingerprint": case.context["fingerprint"]})
    booking = db_session.scalar(select(SettlementBooking))
    ews.delete_entry(db_session, booking.out_entry_id); db_session.commit()                    # the owner deletes the pair (both legs go)
    _reconcile(db_session, sid); policy_service.run_policy(db_session, sid)                     # reconcile's housekeep tombstones the row; no job run
    (case,) = _cases(db_session, sid, "open", "proposed")
    assert db_session.get(SettlementBooking, booking.id).status == "owner_deleted"
    assert (case.kind, case.context["owner_deleted"], case.status) == ("settlement", True, "proposed")
    assert db_session.scalar(select(func.count(SettlementBooking.id)).where(SettlementBooking.status == "booked")) == 0


def test_owner_may_book_the_proposal_after_an_owner_delete(seed, db_session, run, portfolio_stub, policy_on):
    """The sweep retires the deleted pair's action (a creation whose entries are all gone), so the effect slot is free
    and the owner's answer to the policy's proposal books the pair again (spec §9: proposed, never auto-booked)."""
    policy_on()
    bank, target, sid, case = _case_and_booking(seed, db_session, run, portfolio_stub)
    first = _apply(db_session, case.id, "book_settlement", {"fingerprint": case.context["fingerprint"]})
    ews.delete_entry(db_session, db_session.scalar(select(SettlementBooking.out_entry_id))); db_session.commit()
    _reconcile(db_session, sid); policy_service.run_policy(db_session, sid)
    assert _fresh(db_session, ReconciliationAction, first.id).status == "reverted"
    (proposal,) = db_session.scalars(select(ReconciliationProposal).where(ReconciliationProposal.status == "pending"))
    assert (proposal.author, proposal.action) == ("policy", "book_settlement")
    proposal_service.apply_proposal(db_session, proposal_id=proposal.id, label="owner-test", request_id=None)
    db_session.commit()
    assert sorted(db_session.scalars(select(SettlementBooking.status))) == ["booked", "owner_deleted"]


def test_policy_refuses_when_an_owner_twin_appeared_under_the_barrier(pg_engine, seed, db_session, run, portfolio_stub, policy_on, monkeypatch):
    policy_on()
    bank, target, sid, case = _case_and_booking(seed, db_session, run, portfolio_stub)
    (cand,) = policy_service.collect_candidates(db_session, db_session.get(AccountStatement, sid), settings_service.reconciliation_policy(db_session))
    original, ids = policy_service._policy_precheck, (bank.id, target.id)

    def racing(candidate, ctx):                                                                # lands between the listing and the apply:
        original(candidate, ctx)                                                               # after the precheck (whose sweep would flag
        with Session(pg_engine) as writer:                                                     # the statement), before the barrier
            _transfer(Seed(writer), *(SimpleNamespace(id=i, currency="TWD") for i in ids), Decimal("12305"), date(2026, 9, 14))
            writer.commit()

    monkeypatch.setattr(policy_service, "_policy_precheck", racing)
    exc = _refuse(db_session, lambda: policy_service.apply_candidate(db_session, db_session.get(AccountStatement, sid), cand, month=date(2026, 9, 1)))
    assert _code(exc) == "candidate_changed" and db_session.scalar(select(func.count(SettlementBooking.id))) == 0


def test_another_event_with_the_same_fingerprint_is_already_booked(seed, db_session, run, portfolio_stub):
    bank, target, sid, case = _case_and_booking(seed, db_session, run, portfolio_stub)
    _apply(db_session, case.id, "book_settlement", {"fingerprint": case.context["fingerprint"]})
    other = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 10, 1), "-12305")], period=OCT)   # a look-alike line, next period
    (pending,) = _cases(db_session, other, "open")
    assert pending.context["hint"] == "settlement_pending"                                     # the booked fingerprint is reserved: never a candidate
    forced = ReconciliationCase(statement_id=other, revision_id=pending.revision_id, event_id=pending.event_id,   # a stale listing that still names it (B5)
                                line_id=pending.line_id, kind="settlement", status="open",
                                candidates=[{**case.candidates[0], "settlement_date": "2026-10-01"}], context={**case.context})
    db_session.add(forced); db_session.commit()
    exc = _refuse(db_session, lambda: _apply(db_session, forced.id, "book_settlement", {"fingerprint": case.context["fingerprint"]}))
    assert _code(exc) == "already_booked" and db_session.scalar(select(func.count(SettlementBooking.id))) == 1


def test_a_second_listing_of_the_booked_event_replays(seed, db_session, run, portfolio_stub):
    """Spec §4.4 item 3: a booking already holds this event (still booked, both legs, its bank leg claiming the line)
    → nothing new: the engine replays the booking's action."""
    bank, target, sid, case = _case_and_booking(seed, db_session, run, portfolio_stub)
    first = _apply(db_session, case.id, "book_settlement", {"fingerprint": case.context["fingerprint"]})
    again = ReconciliationCase(statement_id=sid, revision_id=case.revision_id, event_id=case.event_id, line_id=case.line_id,
                               kind="settlement", status="open", candidates=case.candidates, context=case.context)
    db_session.add(again); db_session.commit()
    row, replayed = engine.apply_action(db_session, case_id=again.id, action="book_settlement",
                                        params={"fingerprint": case.context["fingerprint"]}, actor="owner", label="owner-test")
    db_session.commit()
    assert (row.id, replayed) == (first.id, True) and db_session.scalar(select(func.count(SettlementBooking.id))) == 1
    assert db_session.scalar(select(func.count(LedgerEntry.id))) == 2


def test_owner_booking_from_an_ambiguous_case_reserves_the_constituents(seed, db_session, run, portfolio_stub):
    bank, target = settlement_bank(seed, db_session)
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-5000", ("ct:1",)), ("2026-09-11", "2026-09-15", "-5000", ("ct:2",)))
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-5000"), settle_line(2, date(2026, 9, 16), "-5000")])
    first, second = _cases(db_session, sid, "open")
    _apply(db_session, first.id, "book_settlement", {"fingerprint": portfolio_stub.rows[0]["fingerprint"]})
    _reconcile(db_session, sid)
    (case,) = _cases(db_session, sid, "open")
    assert case.line_id == second.line_id and case.kind == "settlement" and case.candidates[0]["fingerprint"] == portfolio_stub.rows[1]["fingerprint"]


def test_a_fingerprint_sharing_a_booked_constituent_is_refused(seed, db_session, run, portfolio_stub):
    bank, target, sid, case = _case_and_booking(seed, db_session, run, portfolio_stub, net="-12305", ids=("ct:1", "ct:2"))
    _apply(db_session, case.id, "book_settlement", {"fingerprint": case.context["fingerprint"]})
    portfolio_stub.rows = feed(("2026-09-29", "2026-10-01", "-6000", ("ct:2",)), fingerprint="cathay:2026-09-29:x")   # a re-import moves ct:2 into an October row
    other = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 10, 1), "-6000")], period=OCT)
    (pending,) = _cases(db_session, other, "open")
    assert (pending.context["hint"], pending.candidates) == ("settlement_pending", [])          # inside the window, yet never a candidate: ct:2 is reserved (C2)
    forced = ReconciliationCase(statement_id=other, revision_id=pending.revision_id, event_id=pending.event_id,
                                line_id=pending.line_id, kind="settlement", status="open",
                                candidates=[{**portfolio_stub.rows[0], "items": [{"source_id": "ct:2", "type": "buy_settle", "amount": "-6000.0000"}]}],
                                context={"fingerprint": "cathay:2026-09-29:x", "source": "portfolio:cathay"})
    db_session.add(forced); db_session.commit()
    exc = _refuse(db_session, lambda: _apply(db_session, forced.id, "book_settlement", {"fingerprint": "cathay:2026-09-29:x"}))
    assert _code(exc) == "constituent_reserved" and _fresh(db_session, SettlementBooking, 1).status == "booked"


def test_a_changed_feed_row_is_candidate_changed(seed, db_session, run, portfolio_stub):
    bank, target, sid, case = _case_and_booking(seed, db_session, run, portfolio_stub)
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5", "ct:6")), fingerprint=case.context["fingerprint"],
                               amounts=("-12300", "-5"))                                       # an edited import under the same fingerprint
    exc = _refuse(db_session, lambda: _apply(db_session, case.id, "book_settlement", {"fingerprint": case.context["fingerprint"]}))
    assert _code(exc) == "candidate_changed" and db_session.scalar(select(func.count(SettlementBooking.id))) == 0


def test_a_fingerprint_outside_the_candidates_is_refused(seed, db_session, run, portfolio_stub):
    bank, target, sid, case = _case_and_booking(seed, db_session, run, portfolio_stub)
    exc = _refuse(db_session, lambda: _apply(db_session, case.id, "book_settlement", {"fingerprint": "cathay:2026-09-10:other"}))
    assert _code(exc) == "not_a_candidate" and db_session.scalar(select(func.count(SettlementBooking.id))) == 0


def test_refused_without_a_target_account(seed, db_session, run, portfolio_stub):
    bank, _ = settlement_bank(seed, db_session, target=False)
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)))
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")])
    (case,) = _cases(db_session, sid, "open")
    exc = _refuse(db_session, lambda: _apply(db_session, case.id, "book_settlement", {"fingerprint": case.context["fingerprint"]}))
    assert _code(exc) == "no_target" and db_session.scalar(select(func.count(SettlementBooking.id))) == 0


def test_a_day_another_fingerprint_holds_is_day_booked(seed, db_session, run, portfolio_stub):
    bank, target, sid, case = _case_and_booking(seed, db_session, run, portfolio_stub)
    db_session.add(SettlementBooking(kind="settlement", origin="stand_in", account_id=bank.id, target_account_id=target.id,
                                     source="portfolio:cathay", fingerprint="cathay:2026-09-10:elsewhere",
                                     settlement_date=date(2026, 9, 14), net_amount=Decimal("-1"), status="booked"))
    db_session.commit()                                                                        # a stand-in of other figures on that day
    exc = _refuse(db_session, lambda: _apply(db_session, case.id, "book_settlement", {"fingerprint": case.context["fingerprint"]}))
    assert _code(exc) == "day_booked" and db_session.scalar(select(func.count(SettlementBooking.id))) == 1


@pytest.mark.parametrize("index, code, competitor", [
    ("ux_settlement_booking_fingerprint_booked", "already_booked", {"settlement_date": date(2026, 9, 20)}),
    ("ux_settlement_booking_day_booked", "day_booked", {"fingerprint": "cathay:2026-09-11:racer"})])
def test_a_booking_lost_to_a_unique_index_maps_its_code_and_rolls_back(seed, db_session, run, portfolio_stub, monkeypatch,
                                                                         index, code, competitor):
    """A competing `booked` row lands after the action's locked checks and before its insert (the race the partial
    unique indexes settle, spec §4.4 item 1 / §9): the index refuses the booking, its code is mapped, and the attempt
    savepoint undoes the transfer — the session stays usable. The competitor is written in the same transaction (a
    second session would wait on the account row the action holds) and goes with the rollback."""
    bank, target, sid, case = _case_and_booking(seed, db_session, run, portfolio_stub)
    fingerprint = case.context["fingerprint"]
    original = transfer_service.create_transfer

    def racing(db, payload, **kw):
        group = original(db, payload, **kw)
        db.add(SettlementBooking(**{"kind": "settlement", "origin": "stand_in", "account_id": bank.id,
                                    "target_account_id": target.id, "source": "portfolio:cathay",
                                    "fingerprint": fingerprint, "settlement_date": date(2026, 9, 14),
                                    "net_amount": Decimal("-12305"), "status": "booked", **competitor}))
        db.flush()
        return group

    monkeypatch.setattr(transfer_service, "create_transfer", racing)
    with pytest.raises(CodedConflictError) as info:
        engine.apply_action(db_session, case_id=case.id, action="book_settlement", params={"fingerprint": fingerprint},
                            actor="owner", label="owner-test")
    assert info.value.code == code and index in str(info.value.__cause__.orig)
    assert db_session.scalar(select(func.count(LedgerEntry.id)).where(LedgerEntry.source == "statement")) == 0
    assert db_session.scalar(select(func.count(SettlementBooking.id))) == 0                    # nothing of the attempt left
    assert db_session.scalar(select(func.count(ReconciliationAction.id))) == 0
    db_session.rollback()
    assert _fresh(db_session, ReconciliationCase, case.id).status == "open"


@pytest.mark.parametrize("mutate", ["out_amount", "in_date", "child"])
def test_revert_is_refused_when_either_leg_was_edited(seed, db_session, run, portfolio_stub, mutate):
    bank, target, sid, case = _case_and_booking(seed, db_session, run, portfolio_stub)
    row = _apply(db_session, case.id, "book_settlement", {"fingerprint": case.context["fingerprint"]})
    booking = db_session.scalar(select(SettlementBooking))
    out_leg, in_leg = db_session.get(LedgerEntry, booking.out_entry_id), db_session.get(LedgerEntry, booking.in_entry_id)
    if mutate == "out_amount":
        out_leg.amount = Decimal("-12300")
    elif mutate == "in_date":
        in_leg.entry_date = date(2026, 9, 15)
    else:
        seed.entry(bank, "-1", kind="expense", parent_entry_id=out_leg.id)
    db_session.commit()
    exc = _refuse(db_session, lambda: _revert(db_session, row.id))
    assert _code(exc) == "snapshot_conflict" and _fresh(db_session, SettlementBooking, booking.id).status == "booked"


def test_revert_after_reconcile_reopens_pending(seed, db_session, run, portfolio_stub):
    bank, target, sid, case = _case_and_booking(seed, db_session, run, portfolio_stub)
    row = _apply(db_session, case.id, "book_settlement", {"fingerprint": case.context["fingerprint"]})
    _reconcile(db_session, sid)                                                               # the claim is owned coverage: kept
    assert [c.match_rule for c in _active(db_session, sid)] == ["book_settlement"]
    _revert(db_session, row.id)
    booking = db_session.scalar(select(SettlementBooking))
    (case,) = _cases(db_session, sid, "open")
    assert (booking.status, booking.out_entry_id, booking.in_entry_id, booking.items) == ("retired", None, None, [])
    assert (case.kind, case.context["hint"], case.candidates) == ("line_unmatched", "settlement_pending", [])
    _reconcile(db_session, sid)
    assert [c.kind for c in _cases(db_session, sid, "open")] == ["settlement"]                # re-derived: the bank path books again (spec §9)


def test_refused_while_the_statement_needs_recheck(seed, db_session, run, portfolio_stub):
    bank, target, sid, case = _case_and_booking(seed, db_session, run, portfolio_stub)
    db_session.get(AccountStatement, sid).needs_recheck = True; db_session.commit()
    exc = _refuse(db_session, lambda: _apply(db_session, case.id, "book_settlement", {"fingerprint": case.context["fingerprint"]}))
    assert _code(exc) == "needs_recheck"


def test_revert_is_settlement_busy_while_another_session_holds_the_booking_row(pg_engine, seed, db_session, run, portfolio_stub):
    bank, target, sid, case = _case_and_booking(seed, db_session, run, portfolio_stub)
    row = _apply(db_session, case.id, "book_settlement", {"fingerprint": case.context["fingerprint"]})
    with Session(pg_engine) as other:
        other.execute(select(SettlementBooking).where(SettlementBooking.action_id == row.id).with_for_update())   # a job mid-housekeeping
        exc = _refuse(db_session, lambda: _revert(db_session, row.id))
        other.rollback()
    assert _code(exc) == "settlement_busy" and _fresh(db_session, SettlementBooking, 1).status == "booked"   # nothing released


def _booked(db) -> int:
    return db.scalar(select(func.count(SettlementBooking.id)).where(SettlementBooking.status == "booked"))


def _all_cases(db, sid):
    return [(c.kind, (c.context or {}).get("hint"), c.status)
            for c in _cases(db, sid, "open", "proposed", "resolved", "dismissed")]


def test_a_dismissal_after_a_revert_holds_across_the_kind_change(seed, db_session, run, portfolio_stub, policy_on):
    """Owner ruling A (final review I1): the bank path re-books on the next reconcile UNLESS the case is dismissed —
    the reverted case reopens as `line_unmatched` + `settlement_pending`, the next reconcile derives a `settlement`
    case for the same event, and the dismissal of the first must hide the second."""
    policy_on()
    bank, target = settlement_bank(seed, db_session)
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)))
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")])
    policy_service.run_policy(db_session, sid); db_session.commit()
    booking = db_session.scalar(select(SettlementBooking))
    assert booking.status == "booked"
    _revert(db_session, booking.action_id)
    (case,) = _cases(db_session, sid, "open")
    assert (case.kind, case.context["hint"]) == ("line_unmatched", "settlement_pending")
    _apply(db_session, case.id, "dismiss", {"note": "owner: no"})
    _reconcile(db_session, sid); policy_service.run_policy(db_session, sid); db_session.commit()
    assert _booked(db_session) == 0
    assert _all_cases(db_session, sid) == [("line_unmatched", "settlement_pending", "dismissed")]


def test_a_dismissed_pending_case_hides_the_settlement_once_the_feed_answers(seed, db_session, run, portfolio_stub,
                                                                            policy_on):
    policy_on()
    bank, target = settlement_bank(seed, db_session)
    portfolio_stub.down = True
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")])
    (case,) = _cases(db_session, sid, "open")
    assert (case.kind, case.context["hint"]) == ("line_unmatched", "settlement_pending")
    _apply(db_session, case.id, "dismiss", {"note": "owner: not a trade"})
    portfolio_stub.down = False
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)))
    _reconcile(db_session, sid); policy_service.run_policy(db_session, sid); db_session.commit()
    assert _booked(db_session) == 0
    assert _all_cases(db_session, sid) == [("line_unmatched", "settlement_pending", "dismissed")]


def test_source_drift_and_orphaned_stand_in_stay_outside_the_settlement_family():
    """The family match covers the cases about whether to book (a `settlement` case other than `source_drift`, or a
    line case with a settlement hint); `source_drift` (a booked pair's facts moved) and `orphaned_stand_in` keep the
    same-kind rule."""
    from app.services.reconciliation_service import _dismissed
    book = SimpleNamespace(kind="settlement", event_id=7, entry_id=None, context={"fingerprint": "f"})
    pending = SimpleNamespace(kind="line_unmatched", event_id=7, entry_id=None, context={"hint": "settlement_pending"})
    drift = SimpleNamespace(kind="settlement", event_id=7, entry_id=None, context={"hint": "source_drift"})
    want = {"kind": "ambiguous", "event_id": 7, "entry_id": None, "context": {"hint": "settlement_ambiguous"}}
    want_book = {"kind": "settlement", "event_id": 7, "entry_id": None, "context": {"fingerprint": "g"}}
    orphan = {"kind": "entry_unmatched", "event_id": 7, "entry_id": 3, "context": {"hint": "orphaned_stand_in"}}
    assert _dismissed([book], want) is True and _dismissed([pending], want_book) is True
    assert _dismissed([book], dict(want, event_id=8)) is False                       # another event
    assert _dismissed([drift], want) is False and _dismissed([drift], want_book) is False
    assert _dismissed([book, pending], orphan) is False
