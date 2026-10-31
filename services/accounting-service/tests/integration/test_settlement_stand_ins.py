"""R7 Phase 1 daily settlement stand-ins (portfolio integration spec §4.5, §4.7, §9; owner decision Q7): the job books
a stand-in pair from the 國泰 daily statement on each settlement day, once per (account, settlement date), skips
ineligible and gated days, replaces or retires its own unclaimed rows when the feed changes, and leaves claimed,
owner-deleted and owner-edited pairs alone. Fictional amounts; the fixture's own accounts only."""
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Account, LedgerEntry, PolicyBudget, ReconciliationAudit, SettlementBooking
from app.services import entry_write_service as ews
from app.services import policy_service, settings_service
from app.services import trade_settlement_service as tss
from app.services.moze_import_service import IMPORT_LOCK_KEY
from app.services.schedule_locks import AUTO_PAY_JOB_LOCK_KEY, SETTLEMENT_JOB_LOCK_KEY
from tests.integration.settlement_fixtures import (  # noqa: F401  (portfolio_stub, policy_on, stand_ins_on: fixtures)
    feed, ingest_fixture, policy_on, portfolio_stub, seed_stand_in, settle_line, settlement_bank, stand_ins_on, submit_bank,
)
from tests.integration.statement_fixtures import _active, _reconcile, _transfer, run  # noqa: F401
from tests.integration.test_reconciliation_actions_ledger import _apply, _fresh, _revert
from tests.integration.test_schedule_job import Held
from tests.integration.test_settlement_reconcile import _cases

S = {"Authorization": "Bearer spa-token"}


def _settings(db):
    return settings_service.read_settlement_settings(db)


def _hk(**counts):
    return {**dict.fromkeys(tss.HOUSEKEEPING_KEYS, 0), **counts}


def test_job_books_once_per_settlement_day(pg_engine, seed, db_session, portfolio_stub, stand_ins_on):
    bank, target = settlement_bank(seed, db_session); stand_ins_on(bank, target, enabled_from="2026-09-01")
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)))
    first = tss.run_stand_ins(pg_engine, today=date(2026, 9, 14))
    again = tss.run_stand_ins(pg_engine, today=date(2026, 9, 15))
    (row,) = db_session.scalars(select(SettlementBooking)).all()
    assert (len(first["booked"]), again["booked"], row.origin, row.settlement_date) == (1, [], "stand_in", date(2026, 9, 14))
    out = db_session.get(LedgerEntry, row.out_entry_id)
    assert (out.account_id, out.amount, out.entry_date, out.source) == (bank.id, Decimal("-12305"), date(2026, 9, 14), "statement")


def test_job_skips_future_margin_csv_only_and_pre_go_live_days(pg_engine, seed, db_session, portfolio_stub, stand_ins_on):
    bank, target = settlement_bank(seed, db_session); stand_ins_on(bank, target, enabled_from="2026-09-10")
    portfolio_stub.rows = feed(("2026-09-04", "2026-09-08", "-1000", ("ct:1",)),                          # before go-live
                               ("2026-09-11", "2026-09-15", "-2000", ("ct:2",), {"bookable": False, "reason": "margin_day"}),
                               ("2026-09-11", None, "-3000", ("ct:3",), {"date_source": "legs"}),       # CSV-only
                               ("2026-09-14", "2026-09-16", "-4000", ("ct:4",)))                        # settles tomorrow
    assert tss.run_stand_ins(pg_engine, today=date(2026, 9, 15))["booked"] == []


def test_zero_net_statement_day_gets_no_stand_in(pg_engine, seed, db_session, portfolio_stub, stand_ins_on):
    bank, target = settlement_bank(seed, db_session); stand_ins_on(bank, target, enabled_from="2026-09-01")
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "0", ("ct:5",)))                    # a defensive row the feed should never send
    report = tss.run_stand_ins(pg_engine, today=date(2026, 9, 14))
    assert report["booked"] == [] and db_session.scalar(select(func.count(LedgerEntry.id))) == 0


def _booked(pg_engine, seed, db_session, portfolio_stub, stand_ins_on, **row):
    bank, target = settlement_bank(seed, db_session); stand_ins_on(bank, target, enabled_from="2026-09-01")
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)), **row)
    tss.run_stand_ins(pg_engine, today=date(2026, 9, 14))
    return bank, target, db_session.scalar(select(SettlementBooking))


def _entries(db):
    return db.scalar(select(func.count(LedgerEntry.id)))


def test_owner_twin_first_skips_the_stand_in(pg_engine, seed, db_session, portfolio_stub, stand_ins_on):
    bank, target = settlement_bank(seed, db_session); stand_ins_on(bank, target, enabled_from="2026-09-01")
    _transfer(seed, bank, target, Decimal("12305"), date(2026, 9, 13))                          # the owner recorded it a day early
    db_session.commit()                                                                         # the job reads on its own sessions
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)))
    tss.run_stand_ins(pg_engine, today=date(2026, 9, 14)); tss.run_stand_ins(pg_engine, today=date(2026, 9, 15))
    rows = db_session.scalars(select(SettlementBooking)).all()
    assert [(r.status, r.reason) for r in rows] == [("skipped", "twin")] and _entries(db_session) == 2       # only the owner's legs


def test_reposted_statement_replaces_only_an_unclaimed_stand_in(pg_engine, seed, db_session, run, portfolio_stub, stand_ins_on):
    bank, target, old = _booked(pg_engine, seed, db_session, portfolio_stub, stand_ins_on)
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12310", ("ct:5", "ct:9")))          # a held day imported later: new net
    tss.run_stand_ins(pg_engine, today=date(2026, 9, 15))
    db_session.expire_all()
    rows = {r.status: r for r in db_session.scalars(select(SettlementBooking))}
    assert rows["replaced"].id == old.id and rows["booked"].net_amount == Decimal("-12310") and _entries(db_session) == 2
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12310")])     # the bank line claims the new one
    assert [c.match_rule for c in _active(db_session, sid)] == ["settlement_stand_in"]
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12310", ("ct:5", "ct:9")), amounts=("-12300", "-10"))  # offsetting edit after the claim
    report = tss.run_stand_ins(pg_engine, today=date(2026, 9, 16))
    assert report["housekeeping"] == _hk(checked=1) and _entries(db_session) == 2               # claimed: the job leaves it
    _reconcile(db_session, sid)
    assert {(c.kind, c.context.get("hint")) for c in _cases(db_session, sid, "open")} == {("settlement", "source_drift")}


def test_feed_row_no_longer_bookable_retires_an_unclaimed_stand_in(pg_engine, seed, db_session, portfolio_stub, stand_ins_on):
    bank, target, row = _booked(pg_engine, seed, db_session, portfolio_stub, stand_ins_on)
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",), {"bookable": False, "reason": "legs_disagree"}))
    tss.run_stand_ins(pg_engine, today=date(2026, 9, 15))
    db_session.refresh(row)
    assert (row.status, row.reason, row.out_entry_id, _entries(db_session)) == ("retired", "legs_disagree", None, 0)


def test_owner_deleted_stand_in_is_never_rebooked(pg_engine, seed, db_session, run, portfolio_stub, stand_ins_on, policy_on):
    policy_on()
    bank, target, row = _booked(pg_engine, seed, db_session, portfolio_stub, stand_ins_on)
    ews.delete_entry(db_session, row.out_entry_id); db_session.commit()                         # the owner reverts the stand-in (both legs go)
    tss.run_stand_ins(pg_engine, today=date(2026, 9, 15)); tss.run_stand_ins(pg_engine, today=date(2026, 9, 16))
    db_session.expire_all()
    assert [r.status for r in db_session.scalars(select(SettlementBooking))] == ["owner_deleted"] and _entries(db_session) == 0
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")])     # the monthly statement arrives
    policy_service.run_policy(db_session, sid)
    (case,) = _cases(db_session, sid, "open", "proposed")
    assert (case.kind, case.status, case.context["owner_deleted"]) == ("settlement", "proposed", True) and _entries(db_session) == 0


def test_claim_wins_over_a_concurrent_replacement(pg_engine, seed, db_session, run, portfolio_stub, stand_ins_on, monkeypatch):
    bank, target, row = _booked(pg_engine, seed, db_session, portfolio_stub, stand_ins_on)
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12310", ("ct:5", "ct:9")))          # the job will want to replace
    feed_map = {r.trade_date: r for r in tss.fetch_by_settlement_window(db_session, bank, date(2026, 9, 1), date(2026, 9, 16)).rows}
    stale = tss.unclaimed_stand_ins(db_session, bank.id)                                        # the job's unlocked read, taken BEFORE the claim
    assert stale == {row.id}
    monkeypatch.setattr(tss, "unclaimed_stand_ins", lambda db, account_id: stale)               # freeze it: the race window, made deterministic
    with Session(pg_engine) as other:                                                           # session B: the monthly statement claims and commits inside the window
        other_run = ingest_fixture(other)
        portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)))
        sid = submit_bank(other, other_run, other.get(Account, bank.id), [settle_line(1, date(2026, 9, 14), "-12305")]); other.commit()
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12310", ("ct:5", "ct:9")))
    assert tss.check_row(db_session, db_session.get(SettlementBooking, row.id), feed_map, _settings(db_session), date(2026, 9, 16)) is None   # only the locked re-read in retire_pair can see the claim now
    db_session.commit(); db_session.refresh(row)
    assert row.status == "booked" and _entries(db_session) == 2 and [c.match_rule for c in _active(db_session, sid)] == ["settlement_stand_in"]
    _reconcile(db_session, sid)
    assert {c.context.get("hint") for c in _cases(db_session, sid, "open")} == {"source_drift"}   # the change is reconcile's to show


def test_job_skips_a_pair_whose_leg_another_session_holds(pg_engine, seed, db_session, portfolio_stub, stand_ins_on):
    bank, target, row = _booked(pg_engine, seed, db_session, portfolio_stub, stand_ins_on)
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",), {"bookable": False, "reason": "legs_disagree"}))
    with Session(pg_engine) as other:
        other.execute(select(LedgerEntry).where(LedgerEntry.id == row.out_entry_id).with_for_update())   # a reconcile holding the leg
        report = tss.run_stand_ins(pg_engine, today=date(2026, 9, 15))
        other.rollback()
    assert report["housekeeping"] == _hk(checked=1, settlement_busy=1) and _fresh(db_session, SettlementBooking, row.id).status == "booked"
    assert tss.run_stand_ins(pg_engine, today=date(2026, 9, 15))["housekeeping"] == _hk(checked=1, retired=1)      # next run: retired


def test_replacement_to_a_future_date_waits(pg_engine, seed, db_session, portfolio_stub, stand_ins_on):
    bank, target, row = _booked(pg_engine, seed, db_session, portfolio_stub, stand_ins_on)
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-17", "-12305", ("ct:5",)))                # the printed date moved to the future
    report = tss.run_stand_ins(pg_engine, today=date(2026, 9, 15))
    db_session.expire_all()
    assert report["housekeeping"] == _hk(checked=1, replaced_waiting=1) and _entries(db_session) == 0
    assert sorted(r.status for r in db_session.scalars(select(SettlementBooking))) == ["replaced", "skipped"]
    assert tss.run_stand_ins(pg_engine, today=date(2026, 9, 17))["booked"][0]["settlement_date"] == "2026-09-17"   # booked on its day


def test_replacement_to_a_dismissed_date_is_final(pg_engine, seed, db_session, run, portfolio_stub, stand_ins_on):
    bank, target = settlement_bank(seed, db_session); stand_ins_on(bank, target, enabled_from="2026-09-01")
    portfolio_stub.rows = feed(("2026-09-11", "2026-09-15", "-7000", ("ct:6",)))
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 15), "-7000")])
    (case,) = _cases(db_session, sid, "open"); _apply(db_session, case.id, "dismiss", {"note": "owner says no"})
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)))
    tss.run_stand_ins(pg_engine, today=date(2026, 9, 16))                                       # books 09-14
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-15", "-12305", ("ct:5",)))                # re-posted onto the dismissed day
    report = tss.run_stand_ins(pg_engine, today=date(2026, 9, 16))
    assert report["housekeeping"] == _hk(checked=1, replaced_waiting=1) and report["skipped"][0]["reason"] == "dismissed" and _entries(db_session) == 0


def test_dismissed_case_or_reverted_bank_booking_is_final_for_the_job(pg_engine, seed, db_session, run, portfolio_stub, stand_ins_on):
    bank, target = settlement_bank(seed, db_session); stand_ins_on(bank, target, enabled_from="2026-09-01")
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)), ("2026-09-11", "2026-09-15", "-7000", ("ct:6",)))
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305"), settle_line(2, date(2026, 9, 15), "-7000")])
    first, second = _cases(db_session, sid, "open")
    _apply(db_session, first.id, "dismiss", {"note": "owner says no"})                          # a dismissed settlement case
    row = _apply(db_session, second.id, "book_settlement", {"fingerprint": second.context["fingerprint"]})
    _revert(db_session, row.id)                                                                 # a reverted bank booking
    report = tss.run_stand_ins(pg_engine, today=date(2026, 9, 16))
    assert report["booked"] == [] and sorted(s["reason"] for s in report["skipped"]) == ["dismissed", "final"]
    assert db_session.scalar(select(func.count(SettlementBooking.id)).where(SettlementBooking.status == "booked")) == 0


def test_owner_edited_leg_is_left_alone(pg_engine, seed, db_session, portfolio_stub, stand_ins_on, client):
    bank, target, row = _booked(pg_engine, seed, db_session, portfolio_stub, stand_ins_on)
    res = client.put(f"/transfers/{row.transfer_group_id}", headers=S, json={                   # PUT /transfers/{group_id} (routers/transfers.py:29)
        "from_account_id": bank.id, "to_account_id": target.id, "out_amount": "12305", "entry_date": "2026-09-14", "description": "我改過了"})
    assert res.status_code == 200                                                               # an owner edit: the legs' source leaves `statement`
    tss.run_stand_ins(pg_engine, today=date(2026, 9, 15))
    db_session.refresh(row)
    assert (row.status, _entries(db_session)) == ("owner_edited", 2)


def test_job_with_portfolio_down_changes_nothing(pg_engine, seed, db_session, portfolio_stub, stand_ins_on):
    bank, target, row = _booked(pg_engine, seed, db_session, portfolio_stub, stand_ins_on)
    portfolio_stub.down = True
    report = tss.run_stand_ins(pg_engine, today=date(2026, 9, 15))
    db_session.refresh(row)
    assert (report["status"], row.status, _entries(db_session)) == ("source_unavailable", "booked", 2)


def test_dry_run_writes_nothing(pg_engine, seed, db_session, portfolio_stub, stand_ins_on):
    bank, target = settlement_bank(seed, db_session); stand_ins_on(bank, target, enabled_from="2026-09-01")
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)))
    report = tss.run_stand_ins(pg_engine, today=date(2026, 9, 14), dry_run=True)
    assert (report["status"], len(report["booked"])) == ("dry_run", 1)
    assert db_session.scalar(select(func.count(SettlementBooking.id))) == 0 and _entries(db_session) == 0


def test_stand_ins_are_outside_the_policy_caps(pg_engine, seed, db_session, run, portfolio_stub, stand_ins_on, policy_on):
    policy_on(monthly_total="0", per_statement_count=0)
    assert settings_service.reconciliation_policy(db_session)["actions"]["book_settlement"]["max_amount"] == "0"   # the caps really are zero
    bank, target = settlement_bank(seed, db_session); stand_ins_on(bank, target, enabled_from="2026-09-01")
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)))
    report = tss.run_stand_ins(pg_engine, today=date(2026, 9, 14))
    assert (len(report["booked"]), report["skipped"]) == (1, []) and db_session.scalar(select(func.count(PolicyBudget.month))) == 0   # C3: a cap-aware job would have skipped
    sid = submit_bank(db_session, run, bank, [settle_line(1, date(2026, 9, 14), "-12305")])
    assert [c.match_rule for c in _active(db_session, sid)] == ["settlement_stand_in"]          # and the claim needs no budget either


# --- the job shell: lock, import key, disabled, the report's shape, skipped rows re-evaluated ----------------------


def test_busy_when_the_job_lock_is_held(pg_engine, seed, db_session, portfolio_stub, stand_ins_on):
    assert SETTLEMENT_JOB_LOCK_KEY != AUTO_PAY_JOB_LOCK_KEY
    bank, target = settlement_bank(seed, db_session); stand_ins_on(bank, target, enabled_from="2026-09-01")
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)))
    with Held(pg_engine, SETTLEMENT_JOB_LOCK_KEY):
        report = tss.run_stand_ins(pg_engine, today=date(2026, 9, 14))
    assert (report["status"], report["booked"], portfolio_stub.calls) == ("busy", [], [])
    with Held(pg_engine, AUTO_PAY_JOB_LOCK_KEY):                                                # the auto-pay job does not block it
        assert tss.run_stand_ins(pg_engine, today=date(2026, 9, 14))["status"] == "completed"


def test_import_running_stops_the_run_and_writes_nothing(pg_engine, seed, db_session, portfolio_stub, stand_ins_on):
    bank, target = settlement_bank(seed, db_session); stand_ins_on(bank, target, enabled_from="2026-09-01")
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)))
    with Held(pg_engine, IMPORT_LOCK_KEY):
        report = tss.run_stand_ins(pg_engine, today=date(2026, 9, 14))
    assert (report["status"], report["booked"]) == ("import_running", [])
    assert db_session.scalar(select(func.count(SettlementBooking.id))) == 0 and _entries(db_session) == 0
    with Held(pg_engine, IMPORT_LOCK_KEY):                                                      # a dry run takes no import key
        assert tss.run_stand_ins(pg_engine, today=date(2026, 9, 14), dry_run=True)["status"] == "dry_run"


def test_disabled_books_nothing_but_keeps_housekeeping(pg_engine, seed, db_session, portfolio_stub, stand_ins_on):
    bank, target, row = _booked(pg_engine, seed, db_session, portfolio_stub, stand_ins_on)
    block = {k: v for k, v in _settings(db_session).items() if k in settings_service.SETTLEMENT_DEFAULTS}
    settings_service.update_reconciliation_settings(db_session, {"settlement": {**block, "stand_in_enabled": False}})
    db_session.commit()
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",), {"bookable": False, "reason": "legs_disagree"}),
                               ("2026-09-11", "2026-09-15", "-7000", ("ct:6",)))
    report = tss.run_stand_ins(pg_engine, today=date(2026, 9, 15))
    assert (report["status"], report["booked"], report["skipped"]) == ("disabled", [], [])
    assert report["housekeeping"] == _hk(checked=1, retired=1) and _fresh(db_session, SettlementBooking, row.id).status == "retired"
    preview = tss.run_stand_ins(pg_engine, today=date(2026, 9, 15), dry_run=True)               # the dry run previews whatever enabled says
    assert [(b["account_id"], b["settlement_date"]) for b in preview["booked"]] == [(bank.id, "2026-09-15")]


def test_report_has_every_key_ids_and_dates_and_never_amounts(pg_engine, seed, db_session, portfolio_stub, stand_ins_on):
    bank, target, row = _booked(pg_engine, seed, db_session, portfolio_stub, stand_ins_on)
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-15", "-12305", ("ct:5",)))                # re-posted onto the next day
    report = tss.run_stand_ins(pg_engine, today=date(2026, 9, 15))
    assert set(report) == {"status", "booked", "skipped", "housekeeping", "touched_dates"}
    assert report["housekeeping"] == _hk(checked=1, replaced=1)
    (booked,) = report["booked"]
    assert set(booked) == {"account_id", "settlement_date", "booking_id"} and booked["settlement_date"] == "2026-09-15"
    assert report["touched_dates"] == {bank.id: ["2026-09-14", "2026-09-15"]}                   # the old and the new date
    assert "12305" not in repr(report)
    db_session.expire_all()
    (audit,) = db_session.scalars(select(ReconciliationAudit).where(ReconciliationAudit.event == "settlement_replaced"))
    assert (audit.detail["old_fingerprint"], audit.detail["new_booking_id"]) == ("cathay:2026-09-10:ct:5", booked["booking_id"])


def test_a_skipped_twin_books_once_the_twin_is_gone(pg_engine, seed, db_session, portfolio_stub, stand_ins_on):
    bank, target = settlement_bank(seed, db_session); stand_ins_on(bank, target, enabled_from="2026-09-01")
    out_leg, _ = _transfer(seed, bank, target, Decimal("12305"), date(2026, 9, 13))
    db_session.commit()
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)))
    assert [s["reason"] for s in tss.run_stand_ins(pg_engine, today=date(2026, 9, 14))["skipped"]] == ["twin"]
    assert [s["reason"] for s in tss.run_stand_ins(pg_engine, today=date(2026, 9, 15))["skipped"]] == ["twin"]
    skips = db_session.scalars(select(ReconciliationAudit).where(ReconciliationAudit.event == "settlement_skipped")).all()
    assert len(skips) == 1                                                                      # audited on change only
    ews.delete_entry(db_session, out_leg.id); db_session.commit()                               # the owner deletes the twin
    report = tss.run_stand_ins(pg_engine, today=date(2026, 9, 16))
    assert [b["settlement_date"] for b in report["booked"]] == ["2026-09-14"]                   # a skipped row is never final (M7)


def test_disabled_job_without_stand_ins_reads_nothing(pg_engine, seed, db_session, portfolio_stub, stand_ins_on):
    bank, target = settlement_bank(seed, db_session)                                            # target set, stand-ins off
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)))
    report = tss.run_stand_ins(pg_engine, today=date(2026, 9, 14))
    assert (report["status"], report["housekeeping"], portfolio_stub.calls) == ("disabled", _hk(), [])


# --- book_stand_in's unique-index backstop (a concurrent book_settlement won the day or a constituent) ----------


def _candidate(db, bank, target, row):
    (settlement,) = [s for s in tss.fetch_by_settlement_window(db, bank, date(2026, 9, 1), date(2026, 9, 16)).rows
                     if s.fingerprint == row["fingerprint"]]
    return tss.StandInCandidate(bank.id, target.id, settlement)


def test_a_lost_day_race_is_recorded_as_day_booked(seed, db_session, portfolio_stub, stand_ins_on):
    bank, target = settlement_bank(seed, db_session); stand_ins_on(bank, target, enabled_from="2026-09-01")
    seed_stand_in(db_session, bank, target, settlement_date=date(2026, 9, 14), net="-500",
                  fingerprint="cathay:2026-09-09:ct:1", ids=("ct:1",), origin="bank")                # the bank path got the day
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)))
    row = tss.book_stand_in(db_session, _candidate(db_session, bank, target, portfolio_stub.rows[0]), _settings(db_session))
    db_session.commit()
    assert (row.status, row.reason, row.items, _entries(db_session)) == ("skipped", "day_booked", [], 2)


def test_a_lost_constituent_race_is_recorded_as_constituent_reserved(seed, db_session, portfolio_stub, stand_ins_on):
    bank, target = settlement_bank(seed, db_session); stand_ins_on(bank, target, enabled_from="2026-09-01")
    seed_stand_in(db_session, bank, target, settlement_date=date(2026, 9, 11), net="-500",
                  fingerprint="cathay:2026-09-09:ct:5", ids=("ct:5",), origin="bank")                # holds ct:5
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)))
    cand = _candidate(db_session, bank, target, portfolio_stub.rows[0])
    assert tss.gate(db_session, cand, _settings(db_session)) == "constituent_reserved"          # the gate sees it too
    row = tss.book_stand_in(db_session, cand, _settings(db_session))
    db_session.commit()
    assert (row.status, row.reason, _entries(db_session)) == ("skipped", "constituent_reserved", 2)


# --- fix round 1: the dry run previews read only and says what it assumed; record_skip uses the run's settings ----


def test_dry_run_states_the_settings_it_previewed(pg_engine, seed, db_session, portfolio_stub):
    bank, target = settlement_bank(seed, db_session)                                            # stand-ins off, no go-live date
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)))
    preview = tss.run_stand_ins(pg_engine, today=date(2026, 9, 14), dry_run=True)
    assert preview["previewed_settings"] == {"stand_in_enabled": False, "enabled_from": None,
                                             "effective_enabled_from": "2026-09-14"}
    assert preview["booked"] == [{"account_id": bank.id, "settlement_date": "2026-09-14", "booking_id": None}]
    live = tss.run_stand_ins(pg_engine, today=date(2026, 9, 14))
    assert live["status"] == "disabled" and "previewed_settings" not in live


def test_dry_run_leaves_booked_stand_ins_alone(pg_engine, seed, db_session, portfolio_stub, stand_ins_on):
    bank, target, row = _booked(pg_engine, seed, db_session, portfolio_stub, stand_ins_on)
    legs = (row.out_entry_id, row.in_entry_id)
    for changed in (feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",), {"bookable": False, "reason": "legs_disagree"})),
                    feed(("2026-09-10", "2026-09-15", "-12310", ("ct:5", "ct:9")))):           # a live run would retire, then replace
        portfolio_stub.rows = changed
        with Session(pg_engine) as other:                                                       # a held leg would have made a live run busy
            other.execute(select(LedgerEntry).where(LedgerEntry.id == row.out_entry_id).with_for_update())
            report = tss.run_stand_ins(pg_engine, today=date(2026, 9, 15), dry_run=True)
            other.rollback()
        assert (report["status"], report["housekeeping"]) == ("dry_run", _hk())
        fresh = _fresh(db_session, SettlementBooking, row.id)
        assert (fresh.status, (fresh.out_entry_id, fresh.in_entry_id), _entries(db_session)) == ("booked", legs, 2)
        assert db_session.scalar(select(func.count(SettlementBooking.id))) == 1


def test_record_skip_takes_the_target_from_the_given_settings(seed, db_session, portfolio_stub, stand_ins_on):
    bank, target = settlement_bank(seed, db_session); stand_ins_on(bank, target, enabled_from="2026-09-01")
    other_target = seed.account("另一個市值"); db_session.commit()
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)))
    cand = _candidate(db_session, bank, target, portfolio_stub.rows[0])
    settings = {**_settings(db_session), "target_accounts": {str(bank.id): other_target.id}}   # the run's dict, not the store
    row = tss.record_skip(db_session, bank, cand.settlement, "twin", settings=settings)
    db_session.commit()
    assert (row.status, row.reason, row.target_account_id) == ("skipped", "twin", other_target.id)


def test_job_skips_a_feed_in_another_currency_and_books_nothing(pg_engine, seed, db_session, portfolio_stub, stand_ins_on):
    """PR #234 review finding 1: the account's candidates are skipped `currency_mismatch`, nothing is written."""
    bank, target = settlement_bank(seed, db_session); stand_ins_on(bank, target, enabled_from="2026-09-01")
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",), {"currency": "USD"}))
    preview = tss.run_stand_ins(pg_engine, today=date(2026, 9, 14), dry_run=True)
    report = tss.run_stand_ins(pg_engine, today=date(2026, 9, 14))
    skip = [{"account_id": bank.id, "settlement_date": "2026-09-14", "reason": "currency_mismatch"}]
    assert (preview["status"], preview["booked"], preview["skipped"]) == ("dry_run", [], skip)
    assert (report["status"], report["booked"], report["skipped"]) == ("completed", [], skip)
    assert report["housekeeping"] == _hk(currency_mismatch=1) and report["touched_dates"] == {}
    assert db_session.scalar(select(func.count(SettlementBooking.id))) == 0 and _entries(db_session) == 0


def test_job_leaves_its_stand_ins_alone_while_the_feed_answers_in_another_currency(pg_engine, seed, db_session,
                                                                                  portfolio_stub, stand_ins_on):
    """No retirement or replacement is decided from a feed in another currency: the booked stand-in stays."""
    bank, target, old = _booked(pg_engine, seed, db_session, portfolio_stub, stand_ins_on)
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12310", ("ct:5", "ct:9"), {"currency": "USD"}))
    report = tss.run_stand_ins(pg_engine, today=date(2026, 9, 15))
    db_session.expire_all()
    assert report["housekeeping"] == _hk(currency_mismatch=1) and report["booked"] == []
    assert [(r.id, r.status) for r in db_session.scalars(select(SettlementBooking))] == [(old.id, "booked")]
    assert _entries(db_session) == 2


def test_gate_refuses_a_target_in_another_currency(seed, db_session, portfolio_stub, stand_ins_on):
    """The settings refuse such a pair; the gate asserts it anyway (the run's dict, not the store)."""
    bank, target = settlement_bank(seed, db_session); stand_ins_on(bank, target, enabled_from="2026-09-01")
    usd = seed.account("美股市值", currency="USD"); db_session.commit()
    portfolio_stub.rows = feed(("2026-09-10", "2026-09-14", "-12305", ("ct:5",)))
    cand = _candidate(db_session, bank, usd, portfolio_stub.rows[0])
    settings = {**_settings(db_session), "target_accounts": {str(bank.id): usd.id}}
    assert tss.gate(db_session, cand, settings) == "currency_mismatch"
