"""R7 Phase 1 reconcile orchestration (portfolio integration spec §4.3–§4.5, §4.7, §9): the settlement feed read
by settlement window, the global assignment's cases, the stand-in claim and its link, mismatches and orphans,
drift over claimed lines, twin retirement, owner-delete housekeeping and the neighbour re-check. Fictional amounts;
the fixture's own accounts only."""
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.orm import sessionmaker

from app import auth
from app.models import AccountStatement, LedgerEntry, SettlementBooking
from app.schemas.statements import RevisionIn
from app.services import entry_write_service as ews
from app.services import pair_locks
from app.services import reconciliation_service as rec
from app.services import statement_retire_service
from app.services import trade_settlement_service as tss
from app.services.errors import CodedConflictError
from tests.integration import statement_fixtures
from tests.integration.settlement_fixtures import (  # noqa: F401  (portfolio_stub, policy_on are fixtures)
    AUG, bank_line, feed, policy_on, portfolio_stub, seed_stand_in, settle_line, settlement_bank, submit_bank,
)
from tests.integration.statement_fixtures import OCT, SEP, _active, _reconcile, _transfer, run  # noqa: F401


def _cases(db, statement_id, *statuses):
    """The statement's line and entry cases: a bank statement whose ledger lacks a printed settlement also opens a
    `balance_gap` case, which none of these tests is about."""
    return [c for c in statement_fixtures._cases(db, statement_id, *statuses) if c.kind != "balance_gap"]


def test_unique_settlement_writes_a_settlement_case_and_never_a_backfill(seed, db_session, run, portfolio_stub):
    bank, _ = settlement_bank(seed, db_session)
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5", "ct:7")))
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")])
    (case,) = _cases(db_session, sid, "open")
    assert (case.kind, case.context["fingerprint"], case.candidates[0]["settlement_date"]) == (
        "settlement", portfolio_stub.rows[0]["fingerprint"], "2026-09-14")


def test_net_mismatch_stays_pending(seed, db_session, run, portfolio_stub):
    bank, _ = settlement_bank(seed, db_session)
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12306", ("ct:5",)))
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")])
    (case,) = _cases(db_session, sid, "open")
    assert (case.kind, case.context["hint"]) == ("line_unmatched", "settlement_pending")
    assert case.context["nearest"] == {"date": "2026-09-14", "delta": "-1.0000"}  # spec §9: spot the missing import


def test_portfolio_unavailable_reconciles_the_rest(seed, db_session, run, portfolio_stub):
    bank, _ = settlement_bank(seed, db_session)
    portfolio_stub.down = True
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305"),
                                              bank_line(2, date(2026, 9, 15), "ATM", "-1000")])
    result = _reconcile(db_session, sid)
    assert result.settlement_source_unavailable is True and result.settlements["unavailable_code"] == "http_503"
    assert {(c.kind, (c.context or {}).get("hint")) for c in _cases(db_session, sid, "open")} >= {
        ("line_unmatched", "settlement_pending")}


def test_owner_twin_claims_first_and_contests_its_settlement(seed, db_session, run, portfolio_stub):
    bank, target = settlement_bank(seed, db_session)
    _transfer(seed, bank, target, Decimal("12305"), date(2026, 9, 14))  # the owner's own record
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)))
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305"),
                                              settle_line(2, date(2026, 9, 15), "-12305")])
    kinds = {c.line_id: (c.kind, c.context.get("hint")) for c in _cases(db_session, sid, "open")}
    assert len(_active(db_session, sid)) == 1 and list(kinds.values()) == [("ambiguous", "settlement_ambiguous")]


def test_stand_in_is_claimed_on_its_exact_date_and_linked(seed, db_session, run, portfolio_stub):
    bank, target = settlement_bank(seed, db_session)
    booking = seed_stand_in(db_session, bank, target, settlement_date=date(2026, 9, 14), net="-12305",
                            fingerprint="cathay:2026-09-10:aa", ids=("ct:5",))
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)), fingerprint="cathay:2026-09-10:aa")
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")])
    db_session.refresh(booking)
    assert _cases(db_session, sid, "open") == [] and booking.event_id is not None and booking.status == "booked"
    # §4.7 no duplicate effect: the claimed stand-in alone closes the statement's balance (no `balance_gap` either)
    assert statement_fixtures._cases(db_session, sid, "open") == []
    assert [c.match_rule for c in _active(db_session, sid)] == ["settlement_stand_in"]
    result = _reconcile(db_session, sid)  # the claim is kept and counted; the link is idempotent
    assert (result.settlements["claimed"], result.settlements["drift"]) == (1, 0)


def test_bank_line_on_another_date_is_a_mismatch_and_an_orphan(seed, db_session, run, portfolio_stub):
    bank, target = settlement_bank(seed, db_session)
    booking = seed_stand_in(db_session, bank, target, settlement_date=date(2026, 9, 14), net="-12305",
                            fingerprint="cathay:2026-09-10:aa", ids=("ct:5",))
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)), fingerprint="cathay:2026-09-10:aa")
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 15), "-12305")])
    hints = {c.context.get("hint"): c for c in _cases(db_session, sid, "open")}
    assert hints["settlement_mismatch"].context["settlement_booking_id"] == booking.id
    assert hints["orphaned_stand_in"].context["settlement_booking_id"] == booking.id
    db_session.refresh(booking)
    assert (booking.status, booking.event_id) == ("booked", None)  # nothing moved


def test_drift_on_a_claimed_stand_in_opens_source_drift(seed, db_session, run, portfolio_stub):
    bank, target = settlement_bank(seed, db_session)
    seed_stand_in(db_session, bank, target, settlement_date=date(2026, 9, 14), net="-12305",
                  fingerprint="cathay:2026-09-10:aa", ids=("ct:5", "ct:7"))
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5", "ct:7")),
                               fingerprint="cathay:2026-09-10:aa")
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")])
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5", "ct:7")),
                               fingerprint="cathay:2026-09-10:aa", amounts=("-12299", "-6"))  # offsetting edit
    _reconcile(db_session, sid)
    (case,) = _cases(db_session, sid, "open")
    assert (case.kind, case.context["hint"]) == ("settlement", "source_drift") and len(_active(db_session, sid)) == 1


def test_owner_twin_retires_an_unclaimed_stand_in_at_reconcile(seed, db_session, run, portfolio_stub):
    bank, target = settlement_bank(seed, db_session)
    booking = seed_stand_in(db_session, bank, target, settlement_date=date(2026, 9, 14), net="-12305",
                            fingerprint="cathay:2026-09-10:aa", ids=("ct:5",))
    _transfer(seed, bank, target, Decimal("12305"), date(2026, 9, 14))
    submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")])
    db_session.refresh(booking)
    assert (booking.status, booking.reason, booking.out_entry_id) == ("retired", "twin", None)


def test_a_new_statement_flags_its_live_neighbour_for_recheck(seed, db_session, run, portfolio_stub):
    bank, _ = settlement_bank(seed, db_session)
    aug = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 8, 31), "-5000")], period=AUG)
    submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 1), "-5000")])
    assert db_session.get(AccountStatement, aug).needs_recheck is True


def test_a_window_past_the_calendar_assigns_nothing(seed, db_session, run, portfolio_stub):
    bank, _ = settlement_bank(seed, db_session)
    portfolio_stub.rows = feed(("2028-01-04", "2028-01-06", "-1000", ("ct:5",)))
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2028, 1, 6), "-1000")],
                      period=(date(2028, 1, 1), date(2028, 1, 31)))
    result = _reconcile(db_session, sid)
    assert portfolio_stub.calls == [] and result.settlements["unavailable_code"] == "calendar"  # no provable bound
    assert _cases(db_session, sid, "open")[0].context["hint"] == "settlement_pending"


def test_a_long_holiday_settlement_is_fetched_by_settlement_date(seed, db_session, run, portfolio_stub):
    bank, _ = settlement_bank(seed, db_session)
    portfolio_stub.rows = feed(("2026-02-13", "2026-02-24", "-12305", ("ct:5",)),  # last session before 春節, printed
                               ("2026-02-23", None, "-12305", ("ct:6",), {"date_source": "legs"}))  # CSV-only, 02-25
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 2, 24), "-12305")],
                      period=(date(2026, 2, 24), date(2026, 3, 23)))
    (case,) = _cases(db_session, sid, "open")
    assert (case.kind, case.context["hint"]) == ("ambiguous", "settlement_ambiguous") and len(case.candidates) == 2
    # T+2(02-12) = 02-23 ≥ 02-19, T+2(02-11) = 02-13 < 02-19: from business_days, not a constant
    assert portfolio_stub.calls[-1]["settle_from"] == "2026-02-19" and portfolio_stub.calls[-1]["from"] == "2026-02-12"


def test_an_incomplete_feed_answer_assigns_nothing(seed, db_session, run, portfolio_stub):
    bank, _ = settlement_bank(seed, db_session)
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)))
    portfolio_stub.narrow = True
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")])
    result = _reconcile(db_session, sid)
    (case,) = _cases(db_session, sid, "open")
    assert (case.context["hint"], result.settlement_source_unavailable, result.settlements["unavailable_code"]) == (
        "settlement_pending", True, "incomplete")


def test_undated_rows_are_counted_through_to_the_result(seed, db_session, run, portfolio_stub):
    bank, _ = settlement_bank(seed, db_session)
    portfolio_stub.rows = feed(("2027-12-29", None, "-1000", ("ct:5",), {"date_source": "legs"}))  # T+2 past 2027-12-31
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2027, 12, 26), "-1000")],
                      period=(date(2027, 12, 1), date(2027, 12, 26)))  # settle_hi = 12-31, still covered (B3)
    result = _reconcile(db_session, sid)
    assert result.settlements["unknown_dates"] == 1
    assert _cases(db_session, sid, "open")[0].context["hint"] == "settlement_pending"


def test_a_covered_neighbour_line_still_contests(seed, db_session, run, portfolio_stub):
    bank, target = settlement_bank(seed, db_session)
    _transfer(seed, bank, target, Decimal("5000"), date(2026, 9, 30))  # the owner recorded September's line
    portfolio_stub.rows = feed(("2026-09-29", "2026-10-01", "-5000", ("ct:5",)))
    sep = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 30), "-5000")])  # owner twin → covered
    assert _cases(db_session, sep, "open") == []
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 10, 2), "-5000")], period=OCT)
    (case,) = _cases(db_session, sid, "open")
    assert (case.kind, case.context["hint"]) == ("ambiguous", "settlement_ambiguous")  # 09-30 contests 10-01


def test_date_only_drift_on_a_claimed_row_opens_source_drift(seed, db_session, run, portfolio_stub):
    bank, target = settlement_bank(seed, db_session)
    seed_stand_in(db_session, bank, target, settlement_date=date(2026, 9, 14), net="-12305",
                  fingerprint="cathay:2026-09-10:aa", ids=("ct:5",))
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)), fingerprint="cathay:2026-09-10:aa")
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")])
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-15", "-12305", ("ct:5",)),
                               fingerprint="cathay:2026-09-10:aa")  # only the printed date moved
    _reconcile(db_session, sid)
    (case,) = _cases(db_session, sid, "open")
    assert (case.kind, case.context["hint"], case.context["now"]["settlement_date"]) == (
        "settlement", "source_drift", "2026-09-15")
    assert len(_active(db_session, sid)) == 1 and db_session.scalar(select(SettlementBooking.status)) == "booked"


def test_drift_case_is_carried_while_the_feed_is_down(seed, db_session, run, portfolio_stub):
    bank, target = settlement_bank(seed, db_session)
    seed_stand_in(db_session, bank, target, settlement_date=date(2026, 9, 14), net="-12305",
                  fingerprint="cathay:2026-09-10:aa", ids=("ct:5",))
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-15", "-12305", ("ct:5",)), fingerprint="cathay:2026-09-10:aa")
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")])
    (drift,) = _cases(db_session, sid, "open")
    portfolio_stub.down = True
    _reconcile(db_session, sid)  # the owner still sees it: silence never supersedes a drift case
    assert [c.id for c in _cases(db_session, sid, "open")] == [drift.id]


def test_reconcile_tombstones_an_owner_deleted_bank_booking_before_reserving(seed, db_session, run, portfolio_stub):
    bank, target = settlement_bank(seed, db_session)
    booking = seed_stand_in(db_session, bank, target, settlement_date=date(2026, 9, 14), net="-12305",
                            fingerprint="cathay:2026-09-10:aa", ids=("ct:5",), origin="bank")
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)), fingerprint="cathay:2026-09-10:aa")
    ews.delete_entry(db_session, booking.out_entry_id)
    db_session.commit()
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")])  # no job ran
    db_session.refresh(booking)
    (case,) = _cases(db_session, sid, "open")
    assert (booking.status, booking.items) == ("owner_deleted", [])
    assert (case.kind, case.context["owner_deleted"]) == ("settlement", True)


def test_a_retired_or_historical_neighbours_line_is_not_in_the_graph(seed, db_session, run, portfolio_stub):
    bank, _ = settlement_bank(seed, db_session)
    portfolio_stub.rows = feed(("2026-09-29", "2026-10-01", "-5000", ("ct:5",)))
    old = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 30), "-5000")])  # would contest 10-01
    statement_retire_service.retire_statement(db_session, account_id=bank.id, statement_id=old, note="t",
                                              label="owner-test", request_id=None)
    db_session.commit()
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 10, 2), "-5000")], period=OCT)
    assert [c.kind for c in _cases(db_session, sid, "open")] == ["settlement"]  # the retired line does not contest (B4)


def test_neighbour_lines_at_the_window_edge_are_in_the_graph(seed, db_session, run, portfolio_stub):
    bank, _ = settlement_bank(seed, db_session)
    portfolio_stub.rows = feed(("2026-09-29", "2026-10-01", "-5000", ("ct:5",)))
    sep = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 30), "-5000")])  # within 10-01's window
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 10, 2), "-5000")], period=OCT)
    assert {c.context.get("hint") for c in _cases(db_session, sid, "open")} == {"settlement_ambiguous"}
    assert db_session.get(AccountStatement, sep).needs_recheck is True  # the hook only flags September …
    _reconcile(db_session, sep)  # … its own reconcile sees October's line (B6)
    assert {c.context.get("hint") for c in _cases(db_session, sep, "open")} == {"settlement_ambiguous"}


def test_the_reconcile_route_reports_the_settlement_outcome(client, seed, db_session, run, portfolio_stub,
                                                            monkeypatch):
    monkeypatch.setenv(auth.TOKENS_ENV, "spa:spa-token")
    monkeypatch.setenv(auth.SCOPES_ENV, "spa=legacy")
    monkeypatch.setenv(auth.FEATURE_ENV, "true")
    bank, _ = settlement_bank(seed, db_session)
    portfolio_stub.down = True
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")])
    done = client.post(f"/accounts/{bank.id}/statements/{sid}/reconcile", headers={"Authorization": "Bearer spa-token"})
    assert done.status_code == 200, done.text
    body = done.json()
    assert (body["settlement_source_unavailable"], body["settlements"]["unavailable_code"],
            body["settlements"]["pending"]) == (True, "http_503", 1)


def test_an_owner_edited_leg_tombstones_the_row_and_keeps_its_reservation(seed, db_session, run, portfolio_stub):
    bank, target = settlement_bank(seed, db_session)
    booking = seed_stand_in(db_session, bank, target, settlement_date=date(2026, 9, 14), net="-12305",
                            fingerprint="cathay:2026-09-10:aa", ids=("ct:5",))
    db_session.execute(update(LedgerEntry).where(LedgerEntry.id == booking.in_entry_id).values(source="manual"))
    db_session.commit()  # the owner took the target leg over (PUT /transfers)
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)), fingerprint="cathay:2026-09-10:aa")
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 15), "-12305")])
    db_session.refresh(booking)
    assert (booking.status, [i.source_id for i in booking.items]) == ("owner_edited", ["ct:5"])
    # the edited pair still records the event: no longer a stand-in, its bank leg meets the line as any owner entry
    # does, and ct:5 stays reserved — never offered to another line
    (held,) = _active(db_session, sid)
    assert (held.entry_id, _cases(db_session, sid, "open")) == (booking.out_entry_id, [])
    assert "ct:5" in tss.reservations(db_session)[1]


def test_a_claimed_stand_in_is_never_retired_for_a_source_reason(seed, db_session, run, portfolio_stub):
    bank, target = settlement_bank(seed, db_session)
    booking = seed_stand_in(db_session, bank, target, settlement_date=date(2026, 9, 14), net="-12305",
                            fingerprint="cathay:2026-09-10:aa", ids=("ct:5",))
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")])
    assert len(_active(db_session, sid)) == 1
    assert tss.retire_pair(db_session, db_session.get(SettlementBooking, booking.id), reason="gone") is None
    db_session.commit()
    db_session.refresh(booking)
    assert (booking.status, len(_active(db_session, sid))) == ("booked", 1)  # AGENT-360 major 2


def test_a_held_booking_row_is_settlement_busy_never_a_wait(pg_engine, seed, db_session, run, portfolio_stub):
    bank, target = settlement_bank(seed, db_session)
    booking = seed_stand_in(db_session, bank, target, settlement_date=date(2026, 9, 14), net="-12305",
                            fingerprint="cathay:2026-09-10:aa", ids=("ct:5",))
    ews.delete_entry(db_session, booking.out_entry_id)  # the owner deleted the pair
    db_session.commit()
    holder = sessionmaker(bind=pg_engine)()
    try:
        holder.execute(text("SELECT id FROM settlement_booking WHERE id = :id FOR UPDATE"), {"id": booking.id})
        with pytest.raises(CodedConflictError) as caught:
            tss.lock_pair(db_session, db_session.get(SettlementBooking, booking.id))
        assert caught.value.code == tss.SETTLEMENT_BUSY
        # the session is still usable after the busy savepoint; the held row waits for the next run, still reserving
        assert tss.housekeep(db_session, bank.id) == {"owner_deleted": 0, "owner_edited": 0, "settlement_busy": 1}
        db_session.commit()
    finally:
        holder.rollback()
        holder.close()
    assert tss.housekeep(db_session, bank.id)["owner_deleted"] == 1


def test_the_neighbour_flag_lands_after_the_ingest_commit_when_the_hook_pass_yields(
        client, seed, db_session, run, portfolio_stub, monkeypatch):
    monkeypatch.setenv(auth.TOKENS_ENV, "worker:worker-token")
    monkeypatch.setenv(auth.SCOPES_ENV, "worker=ingest")
    monkeypatch.setenv(auth.FEATURE_ENV, "true")
    bank, _ = settlement_bank(seed, db_session)
    aug = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 8, 31), "-5000")], period=AUG)
    assert db_session.get(AccountStatement, aug).needs_recheck is False
    real = rec.reconcile

    def yielded(*args, **kwargs):  # the hook pass meets a held candidate row
        raise CodedConflictError("reconcile_busy", "held")

    monkeypatch.setattr(rec, "reconcile", yielded)
    r, token = run
    body = RevisionIn(run_id=r.id, lease_token=token, file_id=None, account_id=bank.id, kind="bank", parser="t",
                      parser_version="1", currency=bank.currency, period_start=SEP[0], period_end=SEP[1],
                      opening_balance=Decimal(0), statement_total=Decimal("-5000"),
                      lines=[settle_line(1, date(2026, 9, 1), "-5000")], raw={})
    done = client.post("/statements/revisions", headers={"Authorization": "Bearer worker-token"},
                       json=body.model_dump(mode="json"))
    assert done.status_code == 201, done.text
    sep = done.json()["statement_id"]
    db_session.expire_all()
    # the pass yielded (its own statement waits for the batch), yet the neighbour flag was written after the commit
    assert (db_session.get(AccountStatement, sep).needs_recheck, db_session.get(AccountStatement, aug).needs_recheck) \
        == (True, True)
    monkeypatch.setattr(rec, "reconcile", real)
    (item,) = list(rec.reconcile_each(db_session, [sep]))  # the batch path then completes the yielded pass
    db_session.commit()
    assert item["result"].skipped is None and len(_cases(db_session, sep, "open")) == 1
    assert db_session.get(AccountStatement, aug).needs_recheck is True


def test_drift_reads_stop_after_the_first_failure(seed, db_session, run, portfolio_stub):
    bank, target = settlement_bank(seed, db_session)
    for day, net, fp in ((14, "-12305", "cathay:2026-09-10:aa"), (15, "-4000", "cathay:2026-09-11:bb")):
        seed_stand_in(db_session, bank, target, settlement_date=date(2026, 9, day), net=net, fingerprint=fp,
                      ids=(f"ct:{day}",))
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:14",)), fingerprint="cathay:2026-09-10:aa") \
        + feed(("2026-09-11", "2026-09-15", "-4000", ("ct:15",)), fingerprint="cathay:2026-09-11:bb")
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305"),
                                              settle_line(2, date(2026, 9, 15), "-4000")])
    assert len(_active(db_session, sid)) == 2
    portfolio_stub.fail_after = len(portfolio_stub.calls) + 1  # the window read answers, every later read fails
    result = _reconcile(db_session, sid)
    # one failed drift re-read; the second claimed line is not re-read (and nothing is superseded by the silence)
    assert len(portfolio_stub.calls) == portfolio_stub.fail_after + 1
    assert (result.settlement_source_unavailable, result.settlements["drift"]) == (False, 0)
    assert _cases(db_session, sid, "open") == []


def test_a_historical_reconcile_makes_no_drift_read(seed, db_session, run, portfolio_stub):
    bank, target = settlement_bank(seed, db_session)
    bank.statement_live_from = date(2026, 12, 1)  # September is a historical statement
    db_session.commit()
    seed_stand_in(db_session, bank, target, settlement_date=date(2026, 9, 14), net="-12305",
                  fingerprint="cathay:2026-09-10:aa", ids=("ct:5",))
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)), fingerprint="cathay:2026-09-10:aa")
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")])
    assert db_session.get(AccountStatement, sid).mode == "historical" and len(_active(db_session, sid)) == 1
    before = len(portfolio_stub.calls)
    result = _reconcile(db_session, sid)
    assert len(portfolio_stub.calls) == before + 1 and result.settlements["drift"] == 0  # the window read only


def test_the_window_read_comes_before_any_row_lock(seed, db_session, run, portfolio_stub, monkeypatch):
    bank, target = settlement_bank(seed, db_session)
    booking = seed_stand_in(db_session, bank, target, settlement_date=date(2026, 9, 14), net="-12305",
                            fingerprint="cathay:2026-09-10:aa", ids=("ct:5",))
    ews.delete_entry(db_session, booking.out_entry_id)  # housekeep locks the row and its legs
    db_session.commit()
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)))
    seen: list[tuple[str, int]] = []

    def spy(name, real):
        def wrapped(*args, **kwargs):
            seen.append((name, len(portfolio_stub.calls)))
            return real(*args, **kwargs)
        return wrapped

    for module, name in ((pair_locks, "lock_row"), (pair_locks, "lock_legs"), (rec, "_lock_candidates")):
        monkeypatch.setattr(module, name, spy(name, getattr(module, name)))
    submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305"),
                                        bank_line(2, date(2026, 9, 15), "ATM", "-1000")])
    db_session.refresh(booking)
    assert booking.status == "owner_deleted" and {"lock_row", "_lock_candidates"} <= {n for n, _ in seen}
    assert {calls for _, calls in seen} == {1}  # every lock was taken after the one window read


@pytest.mark.parametrize("stored", ["", "portfolio:cathy"])
def test_an_unknown_stored_source_is_neither_rechecked_nor_read_by_the_job(seed, db_session, run, portfolio_stub, stored):
    """Final review Minor 3: owner SQL can store what the PATCH schema refuses; the re-check list and the job's
    account list both take the known sources only, so "" and a typo are treated alike."""
    from app.services import settings_service
    bank, target = settlement_bank(seed, db_session)
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")])   # feed empty -> pending
    seed_stand_in(db_session, bank, target, settlement_date=date(2026, 9, 21), net="-500",
                  fingerprint="cathay:2026-09-17:ct:9", ids=("ct:9",))
    settings = settings_service.read_settlement_settings(db_session)
    assert tss.recheck_statement_ids(db_session) == [sid] and [a for a, _ in tss._accounts(db_session, settings)] == [bank.id]
    db_session.execute(update(type(bank)).where(type(bank).id == bank.id).values(settlement_source=stored))
    db_session.commit()
    assert tss.recheck_statement_ids(db_session) == [] and tss._accounts(db_session, settings) == []


def test_a_failed_neighbour_flag_logs_the_class_name_only(seed, db_session, run, portfolio_stub, monkeypatch, caplog):
    """Final review Minor 5: no message, no traceback (a driver message can carry amounts)."""
    import logging
    bank, _ = settlement_bank(seed, db_session)
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")])

    def boom(db, statement):
        raise ValueError("amount 12305")

    monkeypatch.setattr(tss, "flag_neighbours", boom)
    monkeypatch.setattr(tss.logger, "disabled", False)  # alembic's fileConfig disables loggers created before it
    with caplog.at_level(logging.INFO, logger=tss.logger.name):
        assert tss.flag_neighbours_safe(db_session, sid) == 0
    (record,) = [r for r in caplog.records if r.getMessage().startswith("settlement.neighbour_flag_failed")]
    assert record.getMessage() == "settlement.neighbour_flag_failed: ValueError" and record.exc_info is None
    assert "12305" not in caplog.text


def test_a_feed_in_another_currency_leaves_every_line_pending_and_books_nothing(seed, db_session, run, portfolio_stub,
                                                                                policy_on, monkeypatch, caplog):
    """PR #234 review finding 1: a feed answering in a currency other than the account's is treated as an
    unavailable source — every settlement line pending, code `currency_mismatch`, logged by code only."""
    import logging
    from sqlalchemy import func
    from app.services import policy_service
    policy_on()
    bank, _ = settlement_bank(seed, db_session)
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",), {"currency": "USD"}))
    monkeypatch.setattr(tss.logger, "disabled", False)  # alembic's fileConfig disables loggers created before it
    with caplog.at_level(logging.INFO, logger=tss.logger.name):
        sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")])
        policy_service.run_policy(db_session, sid)
        result = _reconcile(db_session, sid)
    assert result.settlement_source_unavailable is True
    assert (result.settlements["unavailable_code"], result.settlements["pending"], result.settlements["booked"]) == (
        "currency_mismatch", 1, 0)
    (case,) = _cases(db_session, sid, "open")
    assert (case.kind, case.context["hint"], case.context["nearest"]) == ("line_unmatched", "settlement_pending", None)
    assert db_session.scalar(select(func.count(SettlementBooking.id))) == 0
    assert db_session.scalar(select(func.count(LedgerEntry.id)).where(LedgerEntry.account_id == bank.id)) == 0
    records = [r for r in caplog.records if r.getMessage() == "settlement.currency_mismatch"]
    assert records and {r.code for r in records} == {"currency_mismatch"} and "12305" not in caplog.text


def test_drift_is_not_read_from_a_feed_in_another_currency(seed, db_session, run, portfolio_stub):
    """The re-read behind drift and `book_settlement` (`facts_now`) refuses a row in another currency."""
    bank, _ = settlement_bank(seed, db_session)
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",), {"currency": "USD"}))
    with pytest.raises(tss.PortfolioUnavailable) as info:
        tss.facts_now(db_session, bank, fingerprint=portfolio_stub.rows[0]["fingerprint"], trade_date=date(2026, 9, 10),
                      settlement_date=date(2026, 9, 14))
    assert info.value.code == "currency_mismatch"
