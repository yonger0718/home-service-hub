"""Mirror-period reconciliation in the full replace (spec "Record mapping", "Transactional full replace and report",
"Schedule definitions and instances from the backup"; Review Focus 3)."""

import threading
import time
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.models import Account, Category, LedgerEntry, ScheduleDefinition, ScheduleInstance
from app.services import schedule_generation as generation
from app.services import schedule_job, schedule_service, settlement_service
from app.services import schedule_posting as posting
from app.services import schedule_rules as rules
from app.schemas.schedules import InstanceUpdateIn
from app.services import moze_backup_import_service
from app.services.moze_backup_import_service import run_backup_import
from app.services.moze_import_service import IMPORT_LOCK_KEY, ImportAlreadyRunningError, import_lock
from app.services.schedule_locks import import_key_free
from tests.helpers import _by_moze_id, _import_backup


def _rec(backup, identifier, day, *, type_=0, price=-100, account="A-WALLET", **fields):
    return backup.record(identifier, account, type_=type_, price=price, date=f"{day}T00:00:00", **fields)


def _loan_data(backup, *, exported_at="2026-10-01T17:00:37", first_total=-8333, first_interest=-620, with_loan=True):
    dates = [f"{rules.add_months(date(2026, 10, 9), k).isoformat()}T00:00:00" for k in range(3)]
    records = [
        _rec(backup, "R-REP1", "2026-10-09", type_=6, price=first_total, eventID="INS-1", packageID="PK-1",
             relatedID="R-LOAN", target="T-BANK"),
        _rec(backup, "R-INT1", "2026-10-09", type_=15, price=first_interest, eventID="INS-1", packageID="PK-1"),
        _rec(backup, "R-REP2", "2026-11-09", type_=6, price=-8333, eventID="INS-1", packageID="PK-2",
             relatedID="R-LOAN", target="T-BANK"),
        _rec(backup, "R-INT2", "2026-11-09", type_=15, price=-620, eventID="INS-1", packageID="PK-2"),
    ]
    if with_loan:
        records.append(_rec(backup, "R-LOAN", "2026-09-01", type_=4, price=25000, target="T-BANK"))
    return backup.data(
        exported_at=exported_at, accounts=[backup.account("A-WALLET", "錢包", cacheDate="2026-10-12T00:00:00")],
        targets=[backup.target("T-BANK", "範例銀行")],
        installments=[backup.installment("INS-1", dates=dates, times=3, total=25000, remainder=16667)],
        records=records,
    )


def _post_first_loan_period(db, backup, today) -> ScheduleInstance:
    today(date(2026, 10, 3))
    _import_backup(db, _loan_data(backup))
    instance = db.scalar(select(ScheduleInstance).where(ScheduleInstance.moze_id == "R-REP1"))
    today(date(2026, 10, 9))
    assert posting.post_instance(db, instance.id, actor="auto", job=True, today=date(2026, 10, 9)).outcome == "posted"
    db.commit()
    return instance


def _schedule_entries(db) -> list[LedgerEntry]:
    db.expire_all()
    return list(db.scalars(select(LedgerEntry).where(LedgerEntry.source == "schedule").order_by(LedgerEntry.id)))


def test_reimport_after_local_post_keeps_schedule_entries_and_skips_the_record(db_session, backup, today):
    # Review Focus 3, spec "Period already posted by HomeHub" for a loan period.
    instance = _post_first_loan_period(db_session, backup, today)
    posted_ids = _schedule_entries(db_session)
    today(date(2026, 10, 12))

    summary = _import_backup(db_session, _loan_data(backup, exported_at="2026-10-12T17:00:00"))

    assert _by_moze_id(db_session, "R-REP1") is None and _by_moze_id(db_session, "R-INT1") is None
    assert [entry.id for entry in _schedule_entries(db_session)] == [entry.id for entry in posted_ids]
    loan = _by_moze_id(db_session, "R-LOAN")
    assert settlement_service.open_amount(db_session, loan) == Decimal("16667")
    db_session.expire_all()
    row = db_session.get(ScheduleInstance, instance.id)
    assert (row.status, row.acted_by) == ("posted", "auto")
    block = summary["schedules"]
    assert (block["past_records_already_posted"], block["past_records_amount_differs"]) == (
        2, {"count": 0, "instance_ids": [], "lines": []},
    )
    assert Decimal(summary["accounts"][0]["moze_part"]) == Decimal("25000") - Decimal("8953")
    # loan_check runs after restore_links: HomeHub's repayment counts, so MOZE's remainder matches (no false alarm).
    assert [(row["moze_remainder"], row["open_amount"], row["difference"]) for row in block["loan_remainder_check"]] == [
        ("16667", "16667", "0"),
    ]


def test_single_record_homehub_posted_is_not_imported_once_it_is_past(db_session, backup, today):
    # Spec "Record mapping": a record in the moze_record_ids of an instance HomeHub posted is not imported — also
    # for a single future record (record:<id>) that the next backup holds as a past record without an eventID.
    today(date(2026, 10, 3))

    def data(exported_at):
        return backup.data(
            exported_at=exported_at, accounts=[backup.account("A-WALLET", "錢包")],
            records=[_rec(backup, "X1", "2026-10-09", name="年費")],
        )

    _import_backup(db_session, data("2026-10-01T17:00:37"))
    instance = db_session.scalar(select(ScheduleInstance).where(ScheduleInstance.moze_id == "X1"))
    assert posting.post_instance(db_session, instance.id, actor="auto", job=True, today=date(2026, 10, 9)).outcome == "posted"
    db_session.commit()
    today(date(2026, 10, 12))

    summary = _import_backup(db_session, data("2026-10-12T17:00:00"))

    assert _by_moze_id(db_session, "X1") is None
    assert len(_schedule_entries(db_session)) == 1
    assert summary["schedules"]["past_records_already_posted"] == 1
    definition = db_session.scalar(select(ScheduleDefinition).where(ScheduleDefinition.moze_id == "record:X1"))
    db_session.expire_all()
    assert (definition is not None, db_session.get(ScheduleInstance, instance.id).status) == (True, "posted")
    assert summary["schedules"]["definitions"]["single"]["ended"] == 0


def test_different_amount_reported(db_session, backup, today):
    # Spec "Different amount reported".
    instance = _post_first_loan_period(db_session, backup, today)
    today(date(2026, 10, 12))
    summary = _import_backup(db_session, _loan_data(backup, exported_at="2026-10-12T17:00:00", first_total=-8400))
    block = summary["schedules"]["past_records_amount_differs"]
    assert (block["count"], block["instance_ids"]) == (1, [instance.id])
    assert [(item["line"], item["kind"], item["amount"], item["moze_amount"]) for item in block["lines"]] == [
        (0, "repayment", "8333", "8400"),
    ]
    assert _by_moze_id(db_session, "R-REP1") is None


def test_equal_totals_with_a_different_allocation_are_reported_per_line(db_session, backup, today):
    # Multica R-F5: HomeHub posted 8333 + 620; MOZE's record says 8400 + 553 — the same 8953, a different split.
    instance = _post_first_loan_period(db_session, backup, today)
    today(date(2026, 10, 12))
    summary = _import_backup(
        db_session, _loan_data(backup, exported_at="2026-10-12T17:00:00", first_total=-8400, first_interest=-553)
    )
    definition = db_session.scalar(select(ScheduleDefinition).where(ScheduleDefinition.moze_id == "INS-1"))
    block = summary["schedules"]["past_records_amount_differs"]
    assert (block["count"], block["instance_ids"]) == (1, [instance.id])
    assert block["lines"] == [
        {"definition_id": definition.id, "name": definition.name, "seq": 1, "date": "2026-10-09", "line": 0,
         "kind": "repayment", "amount": "8333", "moze_amount": "8400"},
        {"definition_id": definition.id, "name": definition.name, "seq": 1, "date": "2026-10-09", "line": 1,
         "kind": "interest", "amount": "620", "moze_amount": "553"},
    ]
    # identical amounts → nothing reported (test_reimport_after_local_post_keeps_schedule_entries_and_skips_the_record)


def _owner_period_data(backup, exported_at):
    return backup.data(
        exported_at=exported_at,
        accounts=[backup.account("A-WALLET", "錢包", cacheDate=f"{exported_at[:10]}T00:00:00")],
        periods=[backup.period("PER-M", unit=2, days=9, start="2026-10-09T00:00:00")],
        records=[_rec(backup, "R-SEP", "2026-09-09", eventID="PER-M"), _rec(backup, "R", "2026-10-09", eventID="PER-M")],
    )


def test_owner_edited_period_whose_record_turned_past_is_booked_once(db_session, backup, today):
    # Multica R-F1: the owner edits an imported future period; the next backup holds its record as past. The owner's
    # pending choice wins: no entry is imported, the period stays pending with its override, the report lists it, and
    # a later post writes it once (a further re-import then counts it as already posted).
    today(date(2026, 10, 3))
    _import_backup(db_session, _owner_period_data(backup, "2026-10-01T17:00:37"))
    instance = db_session.scalar(select(ScheduleInstance).where(ScheduleInstance.moze_id == "R"))
    schedule_service.update_instance(db_session, instance.id, InstanceUpdateIn(amounts=["120"]))
    db_session.commit()
    today(date(2026, 10, 12))

    summary = _import_backup(db_session, _owner_period_data(backup, "2026-10-12T17:00:00"))

    assert _by_moze_id(db_session, "R") is None and _schedule_entries(db_session) == []
    db_session.expire_all()
    row = db_session.get(ScheduleInstance, instance.id)
    assert (row.status, row.amount_override, row.edited_by_owner, row.posted_entry_ids) == ("pending", ["120"], True, [])
    block = summary["schedules"]
    assert block["past_records_owner_pending"] == [{"definition_id": row.definition_id, "seq": row.seq, "date": "2026-10-09"}]
    assert block["past_records_already_posted"] == 0
    assert [(item["amount"], item["moze_amount"]) for item in block["amount_differs"]] == [("120", "100")]  # R-A2
    assert Decimal(summary["accounts"][0]["moze_part"]) == Decimal("-200")  # R-SEP and the suppressed R, as MOZE

    assert posting.post_instance(db_session, instance.id, actor="owner").outcome == "posted"
    db_session.commit()
    again = _import_backup(db_session, _owner_period_data(backup, "2026-10-13T17:00:00"))
    assert [entry.amount for entry in _schedule_entries(db_session)] == [Decimal("-120")]
    assert _by_moze_id(db_session, "R") is None
    assert (again["schedules"]["past_records_already_posted"], again["schedules"]["past_records_owner_pending"]) == (1, [])


def test_loan_link_survives_the_full_replace(db_session, backup, today):
    # Spec "Loan link survives the full replace".
    _post_first_loan_period(db_session, backup, today)
    old_loan_id = _by_moze_id(db_session, "R-LOAN").id
    summary = _import_backup(db_session, _loan_data(backup))
    new_loan = _by_moze_id(db_session, "R-LOAN")
    assert new_loan.id != old_loan_id
    repayment = next(entry for entry in _schedule_entries(db_session) if entry.is_settlement)
    assert repayment.settles_entry_id == new_loan.id
    definition = db_session.scalar(select(ScheduleDefinition).where(ScheduleDefinition.moze_id == "INS-1"))
    assert definition.template["lines"][0]["loan_entry_id"] == new_loan.id
    assert summary["schedules"]["relinked"]["settlements"] == 1


def test_period_already_posted_by_homehub(db_session, backup, today):
    # Spec "Period already posted by HomeHub".
    today(date(2026, 10, 3))
    period = backup.period("PER-M", unit=2, days=9, start="2026-10-09T00:00:00")

    def data(exported_at):
        return backup.data(
            exported_at=exported_at, accounts=[backup.account("A-WALLET", "錢包")], periods=[period],
            records=[_rec(backup, "R-SEP", "2026-09-09", eventID="PER-M"), _rec(backup, "R", "2026-10-09", eventID="PER-M")],
        )

    _import_backup(db_session, data("2026-10-01T17:00:37"))
    instance = db_session.scalar(select(ScheduleInstance).where(ScheduleInstance.moze_id == "R"))
    posting.post_instance(db_session, instance.id, actor="auto", job=True, today=date(2026, 10, 9))
    db_session.commit()
    today(date(2026, 10, 12))

    summary = _import_backup(db_session, data("2026-10-12T17:00:00"))

    assert _by_moze_id(db_session, "R") is None
    assert len(_schedule_entries(db_session)) == 1
    assert summary["schedules"]["past_records_already_posted"] == 1


def test_posted_homehub_period_adopted(db_session, backup, today):
    # Spec "Posted HomeHub period adopted".
    today(date(2026, 10, 3))
    weekly = ("2026-09-21", "2026-09-28", "2026-10-05", "2026-10-12")

    def data(exported_at, extra=()):
        return backup.data(
            exported_at=exported_at, accounts=[backup.account("A-WALLET", "錢包")],
            periods=[backup.period("PER-W", unit=1, days=2, start="2026-09-21T00:00:00")],
            records=[_rec(backup, f"R-{day}", day, eventID="PER-W") for day in weekly] + list(extra),
        )

    _import_backup(db_session, data("2026-10-01T17:00:37"))
    definition = db_session.scalar(select(ScheduleDefinition).where(ScheduleDefinition.moze_id == "PER-W"))
    generation.generate(db_session, definition, date(2026, 10, 3))
    generated = db_session.scalar(
        select(ScheduleInstance).where(ScheduleInstance.definition_id == definition.id, ScheduleInstance.due_date == date(2027, 10, 4))
    )
    today(date(2027, 10, 4))
    posting.post_instance(db_session, generated.id, actor="auto", job=True, today=date(2027, 10, 4))
    db_session.commit()
    today(date(2027, 10, 6))

    summary = _import_backup(db_session, data("2027-10-06T17:00:00", [_rec(backup, "R", "2027-10-04", eventID="PER-W")]))

    db_session.expire_all()
    row = db_session.get(ScheduleInstance, generated.id)
    assert (row.moze_id, row.status, row.acted_by) == ("R", "posted", "auto")
    assert _by_moze_id(db_session, "R") is None
    assert summary["schedules"]["past_records_already_posted"] == 1
    assert summary["schedules"]["instances"]["adopted"] == 1


def test_a_period_homehub_skipped_is_not_imported(db_session, backup, today):
    today(date(2026, 10, 3))
    period = backup.period("PER-W", unit=1, days=2, start="2026-09-21T00:00:00")
    days = ("2026-09-21", "2026-10-05")

    def data(exported_at):
        return backup.data(exported_at=exported_at, accounts=[backup.account("A-WALLET", "錢包")], periods=[period],
                           records=[_rec(backup, f"R-{day}", day, eventID="PER-W") for day in days])

    _import_backup(db_session, data("2026-10-01T17:00:37"))
    pending = db_session.scalar(select(ScheduleInstance).where(ScheduleInstance.moze_id == "R-2026-10-05"))
    schedule_service.skip_instance(db_session, pending.id)
    db_session.commit()

    summary = _import_backup(db_session, data("2026-10-12T17:00:00"))

    assert _by_moze_id(db_session, "R-2026-10-05") is None
    assert summary["schedules"]["past_records_already_skipped"] == 1


def test_re_pointing_finds_no_loan(db_session, seed, backup, today):
    # Spec "Re-pointing finds no loan".
    today(date(2026, 10, 3))
    _import_backup(db_session, _loan_data(backup))
    wallet = db_session.scalar(select(Account).where(Account.moze_id == "A-WALLET"))
    loan = _by_moze_id(db_session, "R-LOAN")
    local = seed.definition([seed.line("repayment", wallet, "1000", loan_entry_id=loan.id)], kind="installment",
                            name="本地貸款", times=12)
    db_session.commit()

    summary = _import_backup(db_session, backup.data(accounts=[backup.account("A-WALLET", "錢包")]))

    db_session.expire_all()
    row = db_session.get(ScheduleDefinition, local.id)
    assert row.template["lines"][0]["loan_entry_id"] is None
    assert (row.status, row.review_reason) == ("paused", "loan_missing")
    assert {"moze_id": f"definition:{local.id}", "reason": "loan_missing"} in summary["schedules"]["review"]


def test_template_reference_keeps_a_category_and_an_account(db_session, seed, backup, today):
    # Spec "Template reference keeps a category" (and a referenced account is never archived as disappeared).
    today(date(2026, 10, 3))
    streaming = seed.category("串流", moze_id="K-STREAM")
    spare = seed.account("備用")
    seed.definition([seed.line("expense", spare, "390", category_id=streaming.id)], name="Netflix")
    db_session.commit()

    _import_backup(db_session, backup.data(accounts=[backup.account("A-WALLET", "錢包")]))

    db_session.expire_all()
    assert db_session.get(Category, streaming.id) is not None
    assert db_session.get(Account, spare.id).is_archived is False


def test_schedule_entry_survives_a_backup_import(db_session, seed, backup, today):
    # Ledger spec "Schedule entry survives a backup import".
    today(date(2026, 10, 30))
    card = seed.account("範例卡")
    entry = seed.entry(card, "-390", day=date(2026, 10, 22), source="schedule")
    db_session.commit()
    _import_backup(db_session, backup.data(accounts=[backup.account("A-WALLET", "錢包")]))
    db_session.expire_all()
    row = db_session.get(LedgerEntry, entry.id)
    assert (row.amount, row.source, row.entry_date) == (Decimal("-390"), "schedule", date(2026, 10, 22))


def test_skipped_future_adds_up(db_session, backup, today):
    # The report invariant behind spec "Real backup schedule counts".
    today(date(2026, 10, 3))
    data = backup.data(
        accounts=[backup.account("A-WALLET", "錢包")],
        periods=[backup.period("PER-W", unit=1, days=2, start="2026-10-05T00:00:00")],
        records=[
            _rec(backup, "W-1", "2026-10-05", eventID="PER-W"), _rec(backup, "W-2", "2026-10-12", eventID="PER-W"),
            _rec(backup, "RW", "2026-10-20", type_=14, price=30), _rec(backup, "ADJ", "2026-10-20", type_=7, price=50),
            _rec(backup, "ONE", "2026-11-20"), _rec(backup, "OFF", "2026-11-21", isEnabled=False),
        ],
    )
    summary = _import_backup(db_session, data)
    block = summary["schedules"]
    assert sum(summary["skipped_future"].values()) == block["records_mapped"] + block["rewards_ignored"] + sum(
        block["unsupported_types"].values()
    ) == 5
    assert (block["records_mapped"], block["rewards_ignored"], block["unsupported_types"]) == (3, 1, {"7": 1})


def test_import_waits_for_schedule_writers_then_runs(pg_engine, db_session):
    writer = pg_engine.connect()
    writer.execute(text("SELECT pg_advisory_xact_lock_shared(:key)"), {"key": IMPORT_LOCK_KEY})
    release = threading.Timer(0.5, lambda: (writer.commit(), writer.close()))
    release.start()
    started = time.monotonic()
    with import_lock(pg_engine):
        waited = time.monotonic() - started
    release.join()
    assert 0.3 <= waited < 5


def test_import_is_refused_at_once_while_another_import_runs(pg_engine, db_session):
    holder = pg_engine.connect()
    holder.execute(text("SELECT pg_advisory_lock(:key)"), {"key": IMPORT_LOCK_KEY})
    try:
        started = time.monotonic()
        with pytest.raises(ImportAlreadyRunningError):
            with import_lock(pg_engine):
                pass
        assert time.monotonic() - started < 2
    finally:
        holder.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": IMPORT_LOCK_KEY})
        holder.commit()
        holder.close()


def test_real_import_triggers_a_job_run_and_a_dry_run_does_not(pg_engine, db_session, backup, fake_exporter, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(schedule_job, "trigger_after_import", lambda engine: calls.append(engine))
    zip_path = tmp_path / "MOZE_4.0.zip"
    zip_path.write_bytes(b"PK")
    doc = backup.doc(accounts=[backup.account("A-WALLET", "錢包")])
    run_backup_import(pg_engine, zip_path, zip_path.name, dry_run=True, exporter=fake_exporter(doc))
    assert calls == []
    report = run_backup_import(pg_engine, zip_path, zip_path.name, exporter=fake_exporter(doc))
    assert report["status"] == "succeeded" and calls == [pg_engine]


@pytest.mark.parametrize(("switch", "posted"), [("true", 1), ("false", 0)], ids=["scheduler_on", "scheduler_off"])
def test_cli_import_runs_the_job_inline_after_releasing_the_lock(
    pg_engine, db_session, backup, fake_exporter, tmp_path, monkeypatch, capsys, today, switch, posted
):
    # Multica R-F4: the standalone importer CLI (no scheduler in its process) runs the job itself, after the import
    # lock is released: generation always, posting only with ACCOUNTING_SCHEDULER_ENABLED on, else a due count.
    today(date(2026, 10, 9))
    monkeypatch.setenv("ACCOUNTING_SCHEDULER_ENABLED", switch)
    triggered, seen_free = [], []
    monkeypatch.setattr(schedule_job, "trigger_after_import", lambda engine: triggered.append(engine))
    real_run = schedule_job.run

    def run_checking_the_lock(engine, trigger, **kwargs):
        seen_free.append(import_key_free(engine))  # the import's advisory lock is already released
        return real_run(engine, trigger, **kwargs)

    monkeypatch.setattr(schedule_job, "run", run_checking_the_lock)
    zip_path = tmp_path / "MOZE_4.0.zip"
    zip_path.write_bytes(b"PK")
    doc = backup.doc(
        exported_at="2026-10-08T03:00:00", accounts=[backup.account("A-WALLET", "錢包")],
        periods=[backup.period("PER-M", unit=2, days=9, start="2026-10-09T00:00:00")],
        records=[_rec(backup, "R-SEP", "2026-09-09", eventID="PER-M"), _rec(backup, "R-OCT", "2026-10-09", eventID="PER-M")],
    )

    assert moze_backup_import_service.main([str(zip_path)], engine=pg_engine, exporter=fake_exporter(doc)) == 0

    err = capsys.readouterr().err
    assert triggered == [] and seen_free == [True]
    instance = db_session.scalar(select(ScheduleInstance).where(ScheduleInstance.moze_id == "R-OCT"))
    db_session.expire_all()
    assert db_session.get(ScheduleInstance, instance.id).status == ("posted" if posted else "pending")
    generated = db_session.scalar(select(func.count()).select_from(ScheduleInstance).where(ScheduleInstance.moze_id.is_(None)))
    assert generated > 0  # the horizon was generated in both cases
    assert f"posted {posted}, failed 0, due_unposted {1 - posted}" in err


def _fee_period_data(backup, exported_at, moze_balance=None):
    # R (a PER-M period) carries a feeID fee child and a reward; MOZE books all three.
    fields = {"balanceInfo": {"1": moze_balance}} if moze_balance is not None else {}
    return backup.data(
        exported_at=exported_at,
        accounts=[backup.account("A-WALLET", "錢包", cacheDate=f"{exported_at[:10]}T00:00:00", **fields)],
        periods=[backup.period("PER-M", unit=2, days=9, start="2026-10-09T00:00:00")],
        records=[
            _rec(backup, "R-SEP", "2026-09-09", eventID="PER-M"),
            _rec(backup, "R", "2026-10-09", eventID="PER-M", feeID="R-FEE"),
            _rec(backup, "R-FEE", "2026-10-09", type_=16, price=-15),
            _rec(backup, "R-RW", "2026-10-09", type_=14, price=2, rewardRecordID="R"),
        ],
    )


def test_fee_child_of_a_posted_covered_record_is_reparented_onto_the_stand_in(db_session, backup, today, monkeypatch):
    # Review fix: a covered record's fee / reward dependants hang off the HomeHub entry standing in for it, so the
    # ledger holds them as MOZE does and a strict compared import succeeds.
    today(date(2026, 10, 3))
    _import_backup(db_session, _fee_period_data(backup, "2026-10-01T17:00:37"))
    instance = db_session.scalar(select(ScheduleInstance).where(ScheduleInstance.moze_id == "R"))
    assert posting.post_instance(db_session, instance.id, actor="auto", job=True, today=date(2026, 10, 9)).outcome == "posted"
    db_session.commit()
    today(date(2026, 10, 12))
    monkeypatch.setattr(moze_backup_import_service, "balance_info_key", lambda account: "1" if account["balanceInfo"] else None)

    summary = _import_backup(db_session, _fee_period_data(backup, "2026-10-12T17:00:00", moze_balance=-213), strict=True)

    (stand_in,) = _schedule_entries(db_session)
    assert _by_moze_id(db_session, "R") is None
    fee, reward = _by_moze_id(db_session, "R-FEE"), _by_moze_id(db_session, "R-RW")
    assert (fee.kind, fee.parent_entry_id, fee.amount) == ("fee", stand_in.id, Decimal("-15"))
    assert reward.reward_source_entry_id == stand_in.id
    wallet = summary["accounts"][0]
    assert (Decimal(wallet["moze_part"]), Decimal(wallet["difference"])) == (Decimal("-213"), Decimal("0"))
    assert summary["schedules"]["dependants_suppressed"] == {"count": 0, "records": []}


@pytest.mark.parametrize(("hold", "moze_part"), [("skip", "-113"), ("owner_edit", "-213")])
def test_dependants_of_a_skipped_covered_record_are_suppressed_and_compensated(db_session, backup, today, hold, moze_part):
    # Review fix: a skipped or owner-held period has no HomeHub entry to carry the dependants, so they are not
    # imported; their MOZE amounts join moze_part (the owner-held record itself too, R-F1) and the report lists them.
    today(date(2026, 10, 3))
    _import_backup(db_session, _fee_period_data(backup, "2026-10-01T17:00:37"))
    instance = db_session.scalar(select(ScheduleInstance).where(ScheduleInstance.moze_id == "R"))
    if hold == "skip":
        schedule_service.skip_instance(db_session, instance.id)
    else:
        schedule_service.update_instance(db_session, instance.id, InstanceUpdateIn(amounts=["120"]))
    db_session.commit()
    today(date(2026, 10, 12))

    summary = _import_backup(db_session, _fee_period_data(backup, "2026-10-12T17:00:00"))

    assert [_by_moze_id(db_session, key) for key in ("R", "R-FEE", "R-RW")] == [None, None, None]
    assert Decimal(summary["accounts"][0]["moze_part"]) == Decimal(moze_part)
    assert summary["schedules"]["dependants_suppressed"] == {
        "count": 2,
        "records": [
            {"moze_id": "R-FEE", "parent_moze_id": "R", "type": 16},
            {"moze_id": "R-RW", "parent_moze_id": "R", "type": 14},
        ],
    }


def test_dependants_follow_fee_and_reward_links_transitively():
    def record(identifier, *, type_=0, fee=None, reward_of=None):
        return {"identifier": identifier, "type": type_, "feeID": fee, "rewardRecordID": reward_of}

    records = [
        record("ROOT", fee="FEE-1"), record("FEE-1", type_=16, fee="FEE-2"), record("FEE-2", type_=12),
        record("RW", type_=14, reward_of="ROOT"), record("RW-FEE", type_=16), record("OTHER", fee="OTHER-FEE"),
        record("OTHER-FEE", type_=16), record("SELF", fee="SELF"),
    ]
    records[4]["feeID"] = None
    records[3]["feeID"] = "RW-FEE"
    assert moze_backup_import_service._dependants(records, {"ROOT", "SELF"}) == {"FEE-1", "FEE-2", "RW", "RW-FEE"}
    assert moze_backup_import_service._dependant_parents(records, {"ROOT"}) == {
        "FEE-1": "ROOT", "FEE-2": "FEE-1", "RW": "ROOT", "RW-FEE": "RW",
    }
    assert moze_backup_import_service._dependants(records, ()) == set()


def test_cli_job_crash_keeps_the_import_exit_code(pg_engine, db_session, backup, fake_exporter, tmp_path, monkeypatch, capsys):
    def crash(engine, **kwargs):
        raise RuntimeError("owner detail 錢包 must not be printed")

    monkeypatch.setattr(schedule_job, "run_after_cli_import", crash)
    zip_path = tmp_path / "MOZE_4.0.zip"
    zip_path.write_bytes(b"PK")
    doc = backup.doc(accounts=[backup.account("A-WALLET", "錢包")])

    assert moze_backup_import_service.main([str(zip_path)], engine=pg_engine, exporter=fake_exporter(doc)) == 0

    err = capsys.readouterr().err
    assert "schedule_job: status crashed error_class=RuntimeError" in err
    assert "owner detail" not in err


def test_the_old_schedule_listing_is_gone(client):
    # Spec REMOVED "Scheduled data preserved for phase 4".
    assert client.get("/imports/schedules").status_code == 404
