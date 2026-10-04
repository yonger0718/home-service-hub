"""Backup import of MOZE scheduled data (spec "Schedule definitions and instances from the backup")."""

from datetime import date, datetime, timezone

from sqlalchemy import select

from app.models import ScheduleDefinition, ScheduleInstance
from app.services import schedule_generation as generation
from app.services import schedule_rules as rules
from tests.helpers import _by_moze_id, _import_backup

WEEKLY = ("2026-09-21", "2026-09-28", "2026-10-05", "2026-10-12")


def _accounts(backup):
    return [backup.account("A-WALLET", "錢包"), backup.account("A-BANK", "銀行")]


def _rec(backup, identifier, day, *, type_=0, price=-100, account="A-WALLET", **fields):
    return backup.record(identifier, account, type_=type_, price=price, date=f"{day}T00:00:00", **fields)


def _weekly(backup, days=WEEKLY, *, identifier="PER-W", weekday=2, exported_at=None, extra=()):
    kwargs = {"exported_at": exported_at} if exported_at else {}
    return backup.data(
        accounts=_accounts(backup), periods=[backup.period(identifier, unit=1, days=weekday, start=f"{days[0]}T00:00:00")],
        records=[_rec(backup, f"R-{day}", day, eventID=identifier) for day in days] + list(extra), **kwargs,
    )


def _definition(db, moze_id) -> ScheduleDefinition:
    db.expire_all()
    return db.scalar(select(ScheduleDefinition).where(ScheduleDefinition.moze_id == moze_id))


def _instances(db, definition) -> list[ScheduleInstance]:
    return list(
        db.scalars(select(ScheduleInstance).where(ScheduleInstance.definition_id == definition.id).order_by(ScheduleInstance.seq))
    )


def _state(db) -> list[tuple]:
    db.expire_all()
    return sorted(
        (row.id, row.definition_id, row.seq, row.status, row.due_date, row.rule_date, row.moze_id, row.amount_override)
        for row in db.scalars(select(ScheduleInstance))
    )


def test_imported_definitions_start_active_and_automatic(db_session, backup, today):
    today(date(2026, 10, 3))
    dates = [f"{rules.add_months(date(2026, 10, 9), k).isoformat()}T00:00:00" for k in range(3)]
    data = backup.data(
        accounts=_accounts(backup),
        periods=[backup.period("PER-W", unit=1, days=2, start="2026-09-21T00:00:00")],
        installments=[backup.installment("INS-1", dates=dates, times=3, total=300)],
        records=[_rec(backup, "R-1", "2026-09-21", eventID="PER-W"), _rec(backup, "R-3", "2026-10-05", eventID="PER-W"),
                 _rec(backup, "R-2", "2026-11-09", eventID="INS-1")],
    )
    summary = _import_backup(db_session, data)
    for moze_id in ("PER-W", "INS-1"):
        definition = _definition(db_session, moze_id)
        assert (definition.created_locally, definition.status, definition.posting_mode, definition.auto_post_from) == (
            False, "active", "auto", date(2026, 10, 3),
        )
    assert summary["schedules"]["definitions"]["recurring"]["created"] == 1
    assert summary["schedules"]["definitions"]["installment"]["created"] == 1
    assert _definition(db_session, "PER-W").moze_payload["unit"] == 1


def test_weekly_period_past_records_post_and_future_ones_wait(db_session, backup, today):
    # Spec "Weekly period keyed by weekday".
    today(date(2026, 10, 3))
    summary = _import_backup(db_session, _weekly(backup))
    definition = _definition(db_session, "PER-W")
    rows = _instances(db_session, definition)
    assert [(row.seq, row.status, row.acted_by) for row in rows] == [
        (1, "posted", "import"), (2, "posted", "import"), (3, "pending", None), (4, "pending", None),
    ]
    assert rows[0].posted_entry_ids == [_by_moze_id(db_session, "R-2026-09-21").id]
    assert rows[0].acted_at is not None
    assert (definition.interval_unit, definition.anchor_date, definition.times) == ("week", date(2026, 9, 21), None)
    counts = summary["schedules"]["instances"]
    assert (counts["created"], counts["posted_from_past"]) == (4, 2)


def test_installment_lines_resolve_the_loan(db_session, backup, today):
    # Spec "Repayment and interest are one period"; the loan line points at the imported payable.
    today(date(2026, 10, 3))
    dates = [f"{rules.add_months(date(2026, 10, 9), k).isoformat()}T00:00:00" for k in range(3)]
    data = backup.data(
        accounts=_accounts(backup), targets=[backup.target("T-BANK", "範例銀行")],
        installments=[backup.installment("INS-1", dates=dates, times=3, total=25000, remainder=16667)],
        records=[
            _rec(backup, "R-LOAN", "2026-09-01", type_=4, price=25000, target="T-BANK"),
            _rec(backup, "R-REP", "2026-11-09", type_=6, price=-8333, eventID="INS-1", packageID="PK-9", relatedID="R-LOAN",
                 target="T-BANK"),
            _rec(backup, "R-INT", "2026-11-09", type_=15, price=-612, eventID="INS-1", packageID="PK-9"),
        ],
    )
    summary = _import_backup(db_session, data)
    definition = _definition(db_session, "INS-1")
    repayment, interest = definition.template["lines"]
    assert (repayment["kind"], repayment["loan_entry_id"], repayment["amount"]) == (
        "repayment", _by_moze_id(db_session, "R-LOAN").id, "8333",
    )
    assert interest["kind"] == "interest" and definition.review_reason is None
    [instance] = _instances(db_session, definition)
    assert (instance.seq, instance.amount_override, instance.moze_record_ids, instance.moze_id) == (
        2, ["8333", "612"], ["R-REP", "R-INT"], "R-REP",
    )
    assert summary["schedules"]["loan_remainder_check"] == [
        {"definition": definition.name, "moze_remainder": "16667", "open_amount": "25000", "difference": "8333"}
    ]


def test_disabled_future_period_is_skipped(db_session, backup, today):
    # Spec "Disabled future period".
    today(date(2026, 10, 3))
    data = backup.data(
        accounts=_accounts(backup), periods=[backup.period("PER-D", unit=2, days=3, start="2026-11-03T00:00:00")],
        records=[_rec(backup, "R-NOV", "2026-11-03", eventID="PER-D"),
                 _rec(backup, "R-DEC", "2026-12-03", eventID="PER-D", isEnabled=False)],
    )
    summary = _import_backup(db_session, data)
    rows = _instances(db_session, _definition(db_session, "PER-D"))
    assert [(row.due_date, row.status, row.acted_by) for row in rows] == [
        (date(2026, 11, 3), "pending", None), (date(2026, 12, 3), "skipped", "import"),
    ]
    assert (summary["skipped_future"], summary["disabled_skipped"]) == ({"0": 1}, {"0": 1})
    assert summary["schedules"]["instances"]["skipped_disabled"] == 1


def test_single_record_becomes_its_own_definition(db_session, backup, today):
    # Spec "Single record keyed apart from definitions".
    today(date(2026, 10, 3))
    summary = _import_backup(db_session, backup.data(accounts=_accounts(backup), records=[_rec(backup, "X1", "2026-11-20")]))
    definition = _definition(db_session, "record:X1")
    assert (definition.kind, definition.times) == ("recurring", 1)
    assert [(row.status, row.due_date) for row in _instances(db_session, definition)] == [("pending", date(2026, 11, 20))]
    assert summary["schedules"]["definitions"]["single"]["created"] == 1


def test_reimport_is_idempotent_for_schedules(db_session, backup, today):
    # Spec "Re-import is idempotent" (schedule half).
    today(date(2026, 10, 3))
    data = _weekly(backup)
    _import_backup(db_session, data)
    before = _state(db_session)
    definition_ids = sorted(db_session.scalars(select(ScheduleDefinition.id)))

    summary = _import_backup(db_session, data)

    assert _state(db_session) == before
    assert sorted(db_session.scalars(select(ScheduleDefinition.id))) == definition_ids
    block = summary["schedules"]
    assert all(bucket["created"] == 0 and bucket["deleted"] == 0 for bucket in block["definitions"].values())
    assert (block["instances"]["created"], block["instances"]["deleted"], block["instances"]["adopted"]) == (0, 0, 0)


def test_homehub_generated_period_adopted(db_session, backup, today):
    # Spec "HomeHub-generated period adopted".
    today(date(2026, 10, 3))
    _import_backup(db_session, _weekly(backup))
    definition = _definition(db_session, "PER-W")
    generation.generate(db_session, definition, date(2026, 10, 3))
    db_session.commit()
    generated = next(row for row in _instances(db_session, definition) if row.due_date == date(2027, 10, 4))
    assert generated.moze_id is None

    summary = _import_backup(db_session, _weekly(backup, extra=[_rec(backup, "R-NEW", "2027-10-04", eventID="PER-W")]))

    rows = [row for row in _instances(db_session, _definition(db_session, "PER-W")) if row.due_date == date(2027, 10, 4)]
    assert [(row.id, row.moze_id) for row in rows] == [(generated.id, "R-NEW")]
    assert summary["schedules"]["instances"]["adopted"] == 1


def test_moved_period_adopted_by_its_rule_date(db_session, backup, today):
    # Spec "Moved period adopted by its rule date".
    today(date(2026, 10, 3))
    _import_backup(db_session, _weekly(backup))
    definition = _definition(db_session, "PER-W")
    generation.generate(db_session, definition, date(2026, 10, 3))
    moved = next(row for row in _instances(db_session, definition) if row.rule_date == date(2027, 10, 4))
    moved.due_date = date(2027, 10, 6)
    db_session.commit()

    _import_backup(db_session, _weekly(backup, extra=[_rec(backup, "R-NEW", "2027-10-04", eventID="PER-W")]))

    db_session.expire_all()
    row = db_session.get(ScheduleInstance, moved.id)
    assert (row.moze_id, row.due_date, row.rule_date) == ("R-NEW", date(2027, 10, 6), date(2027, 10, 4))


def test_seq_conflict_reported_not_fatal(db_session, seed, backup, today):
    # Spec "Seq conflict reported, not fatal".
    today(date(2026, 10, 3))
    wallet = seed.account("錢包", moze_id="A-WALLET")
    definition = seed.definition(
        [seed.line("expense", wallet, "100")], interval_unit="week", anchor=date(2026, 12, 14), created_locally=False,
        moze_id="PER-S",
    )
    existing = seed.instance(definition, 5, date(2027, 1, 4))
    db_session.commit()

    summary = _import_backup(db_session, _weekly(backup, days=("2026-12-14", "2027-01-11"), identifier="PER-S"))

    db_session.expire_all()
    assert db_session.get(ScheduleInstance, existing.id).seq == 5
    assert {"moze_id": "R-2027-01-11", "reason": "seq_conflict"} in summary["schedules"]["review"]
    assert [row.seq for row in _instances(db_session, definition)] == [1, 5]


def test_new_review_reason_pauses_and_a_resolved_one_clears(db_session, seed, backup, today):
    # Spec "New review reason pauses, resolved one clears".
    today(date(2026, 10, 3))
    wallet = seed.account("錢包", moze_id="A-WALLET")
    left_active = seed.definition([seed.line("expense", wallet, "100")], name="A", interval_unit="week",
                                  anchor=date(2026, 9, 21), created_locally=False, moze_id="PER-A")
    resumed = seed.definition([seed.line("expense", wallet, "100")], name="B", interval_unit="week",
                              anchor=date(2026, 9, 21), created_locally=False, moze_id="PER-B",
                              review_reason="interval_mismatch")
    db_session.commit()
    data = backup.data(
        accounts=_accounts(backup),
        periods=[backup.period("PER-A", unit=1, days=3, start="2026-09-21T00:00:00"),
                 backup.period("PER-B", unit=1, days=2, start="2026-09-21T00:00:00")],
        records=[_rec(backup, "A-1", "2026-09-21", eventID="PER-A"), _rec(backup, "B-1", "2026-09-21", eventID="PER-B"),
                 # one enabled future record each: both periods are live (a period without one imports ended)
                 _rec(backup, "A-2", "2026-10-05", eventID="PER-A"), _rec(backup, "B-2", "2026-10-05", eventID="PER-B")],
    )

    _import_backup(db_session, data)

    first, second = _definition(db_session, "PER-A"), _definition(db_session, "PER-B")
    assert (first.status, first.review_reason) == ("paused", "interval_mismatch")
    assert (second.status, second.review_reason) == ("active", None)
    assert (left_active.id, resumed.id) == (first.id, second.id)


def test_owner_edited_pending_period_kept(db_session, backup, today):
    # Spec "Owner-edited pending period kept".
    today(date(2026, 10, 3))
    _import_backup(db_session, _weekly(backup))
    pending = _instances(db_session, _definition(db_session, "PER-W"))[2]
    pending.amount_override, pending.edited_by_owner = ["9000"], True
    db_session.commit()

    summary = _import_backup(db_session, _weekly(backup))

    db_session.expire_all()
    row = db_session.get(ScheduleInstance, pending.id)
    assert (row.amount_override, row.edited_by_owner, row.status) == (["9000"], True, "pending")
    assert summary["schedules"]["instances"]["kept_owner_edited"] == 1


def test_reimport_keeps_owner_decisions_and_local_definitions(db_session, seed, backup, today):
    # Spec "Re-import keeps owner decisions and local definitions".
    today(date(2026, 10, 3))
    _import_backup(db_session, _weekly(backup))
    definition = _definition(db_session, "PER-W")
    skipped = _instances(db_session, definition)[2]
    skipped.status, skipped.acted_at, skipped.acted_by = "skipped", datetime(2026, 10, 3, tzinfo=timezone.utc), "owner"
    definition.posting_mode = "confirm"
    card = seed.account("範例卡")
    local = seed.definition([seed.line("expense", card, "390")], name="本地")
    for seq in (1, 2, 3):
        entry = seed.entry(card, "-390", day=date(2026, 6 + seq, 22), source="schedule")
        seed.instance(local, seq, date(2026, 6 + seq, 22), status="posted", entries=[entry])
    db_session.commit()
    local_before = [(row.id, row.status, row.posted_entry_ids) for row in _instances(db_session, local)]

    _import_backup(db_session, _weekly(backup))

    db_session.expire_all()
    assert db_session.get(ScheduleInstance, skipped.id).status == "skipped"
    assert _definition(db_session, "PER-W").posting_mode == "confirm"
    assert [(row.id, row.status, row.posted_entry_ids) for row in _instances(db_session, local)] == local_before


def test_imported_definition_absent_from_the_backup_ends_or_is_deleted(db_session, backup, today):
    today(date(2026, 10, 3))
    data = backup.data(
        accounts=_accounts(backup),
        periods=[backup.period("PER-H", unit=1, days=2, start="2026-09-21T00:00:00"),
                 backup.period("PER-F", unit=1, days=2, start="2026-10-05T00:00:00")],
        records=[_rec(backup, "H-1", "2026-09-21", eventID="PER-H"), _rec(backup, "H-2", "2026-10-05", eventID="PER-H"),
                 _rec(backup, "F-1", "2026-10-05", eventID="PER-F")],
    )
    _import_backup(db_session, data)

    summary = _import_backup(db_session, backup.data(accounts=_accounts(backup)))

    with_history = _definition(db_session, "PER-H")
    assert with_history.status == "ended"
    # H-1 was posted by the import; its record left the backup with the period, so it is recomputed: skipped.
    assert [(row.status, row.acted_by, row.posted_entry_ids) for row in _instances(db_session, with_history)] == [
        ("skipped", "import", []),
    ]
    assert _definition(db_session, "PER-F") is None
    assert summary["schedules"]["definitions"]["recurring"]["ended"] == 1
    assert summary["schedules"]["definitions"]["recurring"]["deleted"] == 1


def test_import_row_whose_record_left_the_backup_becomes_skipped(db_session, backup, today):
    # Spec: acted_by = import instances are recomputed from the newly imported entries; none left → skipped / import.
    today(date(2026, 10, 3))
    _import_backup(db_session, _weekly(backup))
    first = _instances(db_session, _definition(db_session, "PER-W"))[0]
    assert (first.status, first.acted_by) == ("posted", "import")

    _import_backup(db_session, _weekly(backup, days=WEEKLY[1:]))

    db_session.expire_all()
    row = db_session.get(ScheduleInstance, first.id)
    assert (row.status, row.acted_by, row.posted_entry_ids) == ("skipped", "import", [])
    assert _by_moze_id(db_session, "R-2026-09-21") is None


def test_template_change_realigns_kept_overrides(db_session, backup, today):
    # Global constraint: overrides stay aligned with the template's lines — an owner-edited row included.
    today(date(2026, 10, 3))
    dates = [f"{rules.add_months(date(2026, 10, 9), k).isoformat()}T00:00:00" for k in range(3)]

    def data(with_interest):
        records = [
            _rec(backup, "R-LOAN", "2026-09-01", type_=4, price=25000, target="T-BANK"),
            _rec(backup, "R-REP1", "2026-11-09", type_=6, price=-8333, eventID="INS-1", packageID="PK-1",
                 relatedID="R-LOAN", target="T-BANK"),
            _rec(backup, "R-REP2", "2026-12-09", type_=6, price=-8333, eventID="INS-1", packageID="PK-2",
                 relatedID="R-LOAN", target="T-BANK"),
        ]
        if with_interest:
            records.append(_rec(backup, "R-INT1", "2026-11-09", type_=15, price=-612, eventID="INS-1", packageID="PK-1"))
        return backup.data(
            accounts=_accounts(backup), targets=[backup.target("T-BANK", "範例銀行")],
            installments=[backup.installment("INS-1", dates=dates, times=3, total=25000)], records=records,
        )

    _import_backup(db_session, data(False))
    edited = _instances(db_session, _definition(db_session, "INS-1"))[1]
    edited.amount_override, edited.edited_by_owner = ["9000"], True
    db_session.commit()

    _import_backup(db_session, data(True))

    db_session.expire_all()
    assert [line["kind"] for line in _definition(db_session, "INS-1").template["lines"]] == ["repayment", "interest"]
    row = db_session.get(ScheduleInstance, edited.id)
    assert (row.amount_override, row.edited_by_owner, row.status) == (["9000", "612"], True, "pending")


def test_pending_imported_row_follows_moze_and_clears_failure_marks(db_session, backup, today):
    # Spec: pending imported instances are refreshed unless owner-edited — MOZE's new date included; a period MOZE
    # now marks disabled is skipped and no longer shows the failure it had while pending.
    today(date(2026, 10, 3))
    _import_backup(db_session, _weekly(backup))
    rows = _instances(db_session, _definition(db_session, "PER-W"))
    waiting, failing = rows[2], rows[3]
    failing.last_error, failing.last_error_at = "lines[0].account_id: 帳戶已封存", datetime(2026, 10, 3, tzinfo=timezone.utc)
    db_session.commit()
    extra = [
        _rec(backup, "R-2026-10-05", "2026-10-06", eventID="PER-W"),  # MOZE moved this period by a day
        _rec(backup, "R-2026-10-12", "2026-10-12", eventID="PER-W", isEnabled=False),
    ]

    _import_backup(db_session, _weekly(backup, days=WEEKLY[:2], extra=extra))

    db_session.expire_all()
    moved = db_session.get(ScheduleInstance, waiting.id)
    assert (moved.rule_date, moved.due_date, moved.status) == (date(2026, 10, 6), date(2026, 10, 6), "pending")
    dropped = db_session.get(ScheduleInstance, failing.id)
    assert (dropped.status, dropped.acted_by, dropped.last_error, dropped.last_error_at) == ("skipped", "import", None, None)


def test_interval_mismatch_definition_never_generates(db_session, backup, today):
    # An off-rule import is paused with seqs by position; generation must not roll that invented rule forward.
    today(date(2026, 10, 3))
    _import_backup(db_session, _weekly(backup, weekday=3))  # MOZE says Tuesday, the records fall on Mondays
    definition = _definition(db_session, "PER-W")
    assert (definition.status, definition.review_reason) == ("paused", "interval_mismatch")
    assert generation.generate(db_session, definition, date(2026, 10, 3)) == 0
    assert len(_instances(db_session, definition)) == 4


def test_period_without_future_records_imports_ended(db_session, backup, today):
    # Spec maps each live AHPeriod: one with no enabled future record (cancelled or finished in MOZE) ends.
    today(date(2026, 10, 3))
    summary = _import_backup(db_session, _weekly(backup, days=WEEKLY[:2]))
    definition = _definition(db_session, "PER-W")
    assert (definition.status, definition.review_reason) == ("ended", "not_live")
    assert [row.status for row in _instances(db_session, definition)] == ["posted", "posted"]
    assert generation.generate(db_session, definition, date(2026, 10, 3)) == 0
    assert summary["schedules"]["definitions"]["recurring"]["ended"] == 1
    assert {"moze_id": "PER-W", "reason": "not_live"} not in summary["schedules"]["review"]


def test_not_live_end_keeps_owner_and_homehub_rows_and_reverses_when_moze_resumes(db_session, backup, today):
    # D37: an import never deletes owner-edited or HomeHub-generated pending rows. A backup without an enabled future
    # record (e.g. mid-mirror, before MOZE pre-generated the next period) ends the period with review_reason
    # not_live; the next backup that has one sets it back to active and creates the pending period.
    today(date(2026, 10, 3))
    _import_backup(db_session, _weekly(backup))
    definition = _definition(db_session, "PER-W")
    generation.generate(db_session, definition, date(2026, 10, 3))
    edited = _instances(db_session, definition)[2]  # R-2026-10-05, pending
    edited.amount_override, edited.edited_by_owner = ["9000"], True
    generated = next(row for row in _instances(db_session, definition) if row.due_date == date(2027, 10, 4))
    assert generated.moze_id is None
    db_session.commit()

    summary = _import_backup(db_session, _weekly(backup, days=WEEKLY[:2]))

    definition = _definition(db_session, "PER-W")
    assert (definition.status, definition.review_reason) == ("ended", "not_live")
    assert summary["schedules"]["definitions"]["recurring"]["ended"] == 1
    rows = {row.id: row for row in _instances(db_session, definition)}
    assert (rows[edited.id].status, rows[edited.id].amount_override) == ("pending", ["9000"])  # owner-edited: kept
    assert (rows[generated.id].status, rows[generated.id].moze_id) == ("pending", None)  # HomeHub-generated: kept
    assert all(row.moze_id != "R-2026-10-12" for row in rows.values())  # MOZE-sourced, record gone: deleted
    assert generation.generate(db_session, definition, date(2026, 10, 3)) == 0

    again = _import_backup(db_session, _weekly(backup, days=WEEKLY[:2]))  # still not live: nothing changes
    assert (_definition(db_session, "PER-W").status, again["schedules"]["definitions"]["recurring"]["ended"]) == ("ended", 0)

    summary = _import_backup(db_session, _weekly(backup))

    definition = _definition(db_session, "PER-W")
    assert (definition.status, definition.review_reason) == ("active", None)
    assert summary["schedules"]["definitions"]["recurring"]["updated"] == 1
    rows = _instances(db_session, definition)
    revived = next(row for row in rows if row.moze_id == "R-2026-10-12")
    assert (revived.status, revived.due_date) == ("pending", date(2026, 10, 12))
    assert db_session.get(ScheduleInstance, edited.id).amount_override == ["9000"]
    assert db_session.get(ScheduleInstance, generated.id).status == "pending"


def test_account_missing_definition_realigns_moze_amounts_to_the_kept_template(db_session, backup, today):
    # Global constraint (overrides aligned with the template on every write): a re-import whose lines cannot resolve
    # keeps the stored template, so MOZE's per-period amounts are realigned to its lines, not written as mapped.
    today(date(2026, 10, 3))
    dates = [f"{rules.add_months(date(2026, 10, 9), k).isoformat()}T00:00:00" for k in range(3)]
    loan = _rec(backup, "R-LOAN", "2026-09-01", type_=4, price=25000, target="T-BANK")

    def data(records):
        return backup.data(
            accounts=_accounts(backup), targets=[backup.target("T-BANK", "範例銀行")],
            installments=[backup.installment("INS-1", dates=dates, times=3, total=25000)], records=[loan, *records],
        )

    _import_backup(db_session, data([
        _rec(backup, "R-REP2", "2026-12-09", type_=6, price=-8333, eventID="INS-1", packageID="PK-2", relatedID="R-LOAN",
             target="T-BANK"),
        _rec(backup, "R-INT2", "2026-12-09", type_=15, price=-612, eventID="INS-1", packageID="PK-2"),
    ]))
    definition = _definition(db_session, "INS-1")
    assert [line["kind"] for line in definition.template["lines"]] == ["repayment", "interest"]

    # MOZE moved the repayment to an account the backup no longer lists and dropped the interest record.
    summary = _import_backup(db_session, data([
        _rec(backup, "R-REP2", "2026-12-09", type_=6, price=-8000, eventID="INS-1", packageID="PK-2", relatedID="R-LOAN",
             target="T-BANK", account="A-GONE"),
    ]))

    definition = _definition(db_session, "INS-1")
    assert [line["kind"] for line in definition.template["lines"]] == ["repayment", "interest"]  # template kept
    assert {"moze_id": "INS-1", "reason": "account_missing"} in summary["schedules"]["review"]
    [row] = _instances(db_session, definition)
    assert (row.status, row.amount_override) == ("pending", ["8000", "612"])  # one amount per template line


def test_moze_date_refresh_never_lands_on_another_pending_period(db_session, backup, today):
    # The refresh of a pending row to MOZE's new date must not collide with another pending (or posted) period of
    # the definition: both would post on one day and the second would hit ux_schedule_instance_posted_day.
    today(date(2026, 10, 3))
    _import_backup(db_session, _weekly(backup))
    rows = _instances(db_session, _definition(db_session, "PER-W"))
    waiting, moved_by_owner = rows[2], rows[3]
    moved_by_owner.due_date, moved_by_owner.edited_by_owner = date(2026, 10, 7), True
    db_session.commit()
    extra = [
        _rec(backup, "R-2026-10-05", "2026-10-07", eventID="PER-W"),  # MOZE moved this period onto the owner's day
        _rec(backup, "R-2026-10-12", "2026-10-12", eventID="PER-W"),
    ]

    _import_backup(db_session, _weekly(backup, days=WEEKLY[:2], extra=extra))

    db_session.expire_all()
    kept = db_session.get(ScheduleInstance, waiting.id)
    assert (kept.rule_date, kept.due_date, kept.status) == (date(2026, 10, 5), date(2026, 10, 5), "pending")
    owner = db_session.get(ScheduleInstance, moved_by_owner.id)
    assert (owner.due_date, owner.status) == (date(2026, 10, 7), "pending")


def test_row_matched_by_a_record_id_takes_the_new_primary_id(db_session, backup, today):
    # MOZE changed a package's primary record (the repayment joined an interest-only package): the row matched by the
    # shared record id takes the new moze_id and is refreshed — the final sweep must not delete it and lose the period.
    today(date(2026, 10, 3))
    dates = [f"{rules.add_months(date(2026, 10, 9), k).isoformat()}T00:00:00" for k in range(3)]

    def data(with_repayment):
        records = [
            _rec(backup, "R-LOAN", "2026-09-01", type_=4, price=25000, target="T-BANK"),
            _rec(backup, "R-REP1", "2026-11-09", type_=6, price=-8333, eventID="INS-1", packageID="PK-1",
                 relatedID="R-LOAN", target="T-BANK"),
            _rec(backup, "R-INT1", "2026-11-09", type_=15, price=-612, eventID="INS-1", packageID="PK-1"),
            _rec(backup, "R-INT2", "2026-12-09", type_=15, price=-600, eventID="INS-1", packageID="PK-2"),
        ]
        if with_repayment:
            records.append(_rec(backup, "R-REP2", "2026-12-09", type_=6, price=-8333, eventID="INS-1", packageID="PK-2",
                                relatedID="R-LOAN", target="T-BANK"))
        return backup.data(
            accounts=_accounts(backup), targets=[backup.target("T-BANK", "範例銀行")],
            installments=[backup.installment("INS-1", dates=dates, times=3, total=25000)], records=records,
        )

    _import_backup(db_session, data(False))
    second = next(row for row in _instances(db_session, _definition(db_session, "INS-1")) if row.due_date == date(2026, 12, 9))
    assert (second.moze_id, second.amount_override) == ("R-INT2", ["0", "600"])

    summary = _import_backup(db_session, data(True))

    db_session.expire_all()
    row = db_session.get(ScheduleInstance, second.id)
    assert (row.moze_id, row.moze_record_ids, row.amount_override, row.status) == (
        "R-REP2", ["R-REP2", "R-INT2"], ["8333", "600"], "pending",
    )
    assert summary["schedules"]["instances"]["deleted"] == 0


def test_owner_set_template_amounts_survive_a_reimport(db_session, backup, today):
    # Spec "Owner-set price survives a re-import" (proposal decision 24): after 全部週期 the owner's price stands on
    # refreshed and new pending periods; MOZE's differing amounts are only reported.
    today(date(2026, 10, 3))
    _import_backup(db_session, _weekly(backup))
    definition = _definition(db_session, "PER-W")
    line = definition.template["lines"][0]
    definition.template = {**definition.template, "lines": [{**line, "amount": "120"}]}
    definition.template_owner_edited = True
    for row in _instances(db_session, definition):
        if row.status == "pending":
            row.amount_override = None  # what Task 12's scope = all leaves behind
    db_session.commit()

    summary = _import_backup(db_session, _weekly(backup, extra=[_rec(backup, "R-2026-10-19", "2026-10-19", eventID="PER-W")]))

    definition = _definition(db_session, "PER-W")
    assert (definition.template["lines"][0]["amount"], definition.template_owner_edited) == ("120", True)
    pending = [(row.seq, row.amount_override) for row in _instances(db_session, definition) if row.status == "pending"]
    assert pending == [(3, None), (4, None), (5, None)]
    assert summary["schedules"]["amount_differs"] == [
        {"definition_id": definition.id, "name": definition.name, "seq": seq, "date": day, "line": 0, "kind": "expense",
         "amount": "120", "moze_amount": "100"}
        for seq, day in ((3, "2026-10-05"), (4, "2026-10-12"), (5, "2026-10-19"))
    ]


def test_owner_edited_pending_period_on_an_owner_priced_definition_is_compared(db_session, backup, today):
    # Multica R-A2: template_owner_edited, template 150, MOZE 100, a retained owner override 120 → one amount_differs
    # item, 120 against MOZE's 100 (the period's effective posting amount is the owner's override).
    today(date(2026, 10, 3))
    _import_backup(db_session, _weekly(backup))
    definition = _definition(db_session, "PER-W")
    line = definition.template["lines"][0]
    definition.template = {**definition.template, "lines": [{**line, "amount": "150"}]}
    definition.template_owner_edited = True
    rows = [row for row in _instances(db_session, definition) if row.status == "pending"]
    for row in rows:
        row.amount_override = None
    rows[0].amount_override, rows[0].edited_by_owner = ["120"], True
    db_session.commit()

    summary = _import_backup(db_session, _weekly(backup, days=WEEKLY[:3]))  # only the owner-edited period stays pending

    db_session.expire_all()
    row = db_session.get(ScheduleInstance, rows[0].id)
    assert (row.status, row.amount_override, row.edited_by_owner) == ("pending", ["120"], True)
    assert summary["schedules"]["amount_differs"] == [
        {"definition_id": definition.id, "name": definition.name, "seq": row.seq, "date": row.due_date.isoformat(),
         "line": 0, "kind": "expense", "amount": "120", "moze_amount": "100"},
    ]


def test_import_booked_transfer_period_lists_both_legs(db_session, backup, today):
    # Task 13 follow-up: an instance the import marks posted lists both legs of a transfer in posted_entry_ids.
    today(date(2026, 10, 3))
    days = ("2026-09-21", "2026-10-21")
    records, transfers = [], []
    for k, day in enumerate(days):
        records += [
            _rec(backup, f"O-{k}", day, type_=2, price=-500, eventID="PER-T"),
            _rec(backup, f"I-{k}", day, type_=2, price=500, account="A-BANK", eventID="PER-T"),
        ]
        transfers.append(backup.transfer(f"X-{k}", f"O-{k}", f"I-{k}"))
    data = backup.data(
        accounts=_accounts(backup), records=records, transfers=transfers,
        periods=[backup.period("PER-T", unit=2, days=21, type_=1, start="2026-09-21T00:00:00")],
    )

    _import_backup(db_session, data)

    posted, pending = _instances(db_session, _definition(db_session, "PER-T"))
    assert (posted.status, posted.acted_by, pending.status) == ("posted", "import", "pending")
    assert posted.posted_entry_ids == [_by_moze_id(db_session, "O-0").id, _by_moze_id(db_session, "I-0").id]
    assert posted.moze_record_ids == ["O-0", "I-0"]


def test_template_change_leaves_posted_overrides_alone(db_session, backup, today):
    # Task 12: a posted row's amount_override holds the amounts it was posted with (metadata); a re-import whose
    # template changes realigns pending rows only and never clears or overwrites a posted row's override.
    today(date(2026, 10, 3))
    _import_backup(db_session, _weekly(backup))
    definition = _definition(db_session, "PER-W")
    posted = _instances(db_session, definition)[0]
    posted_override = list(posted.amount_override)
    assert posted.status == "posted"

    data = _weekly(backup)
    for record in data.records:
        record["type"] = 1  # MOZE now books the period as income: the template's line kind changes
        record["price"] = 100
    _import_backup(db_session, data)

    definition = _definition(db_session, "PER-W")
    assert [line["kind"] for line in definition.template["lines"]] == ["income"]
    rows = _instances(db_session, definition)
    assert (rows[0].id, rows[0].status, rows[0].amount_override) == (posted.id, "posted", posted_override)
    assert [row.amount_override for row in rows if row.status == "pending"] == [["100"], ["100"]]


def test_account_missing_realignment_that_keeps_nothing_does_not_crash(db_session, backup, today):
    # Review fix: the package lost its first member and the remaining record's account left the backup. Nothing of
    # MOZE's lines keeps its index and kind in the stored template, so the period follows the template's amounts.
    today(date(2026, 10, 3))
    dates = [f"{rules.add_months(date(2026, 10, 9), k).isoformat()}T00:00:00" for k in range(3)]
    loan = _rec(backup, "R-LOAN", "2026-09-01", type_=4, price=25000, target="T-BANK")

    def data(records):
        return backup.data(
            accounts=_accounts(backup), targets=[backup.target("T-BANK", "範例銀行")],
            installments=[backup.installment("INS-1", dates=dates, times=3, total=25000)], records=[loan, *records],
        )

    _import_backup(db_session, data([
        _rec(backup, "R-REP2", "2026-12-09", type_=6, price=-8333, eventID="INS-1", packageID="PK-2", relatedID="R-LOAN",
             target="T-BANK"),
        _rec(backup, "R-INT2", "2026-12-09", type_=15, price=-612, eventID="INS-1", packageID="PK-2"),
    ]))

    summary = _import_backup(db_session, data([
        _rec(backup, "R-INT2", "2026-12-09", type_=15, price=-600, eventID="INS-1", packageID="PK-2", account="A-GONE"),
    ]))

    definition = _definition(db_session, "INS-1")
    assert [line["kind"] for line in definition.template["lines"]] == ["repayment", "interest"]
    assert {"moze_id": "INS-1", "reason": "account_missing"} in summary["schedules"]["review"]
    [row] = _instances(db_session, definition)
    assert (row.status, row.moze_id, row.amount_override) == ("pending", "R-INT2", ["8333", "612"])


def test_early_payment_in_moze_moves_the_period_to_the_record_day(db_session, backup, today):
    # Review fix: the owner paid an upcoming period early in MOZE (its record moved to today). The period is booked by
    # the import on the record's day, so the period, its entry and a later repost share one date.
    today(date(2026, 10, 3))
    _import_backup(db_session, _weekly(backup))
    waiting = _instances(db_session, _definition(db_session, "PER-W"))[2]
    assert (waiting.moze_id, waiting.status, waiting.due_date) == ("R-2026-10-05", "pending", date(2026, 10, 5))

    early = _rec(backup, "R-2026-10-05", "2026-10-03", eventID="PER-W")
    _import_backup(db_session, _weekly(backup, days=WEEKLY[:2], exported_at="2026-10-03T03:00:00",
                                       extra=[early, _rec(backup, "R-2026-10-12", "2026-10-12", eventID="PER-W")]))

    db_session.expire_all()
    row = db_session.get(ScheduleInstance, waiting.id)
    entry = _by_moze_id(db_session, "R-2026-10-05")
    assert (row.status, row.acted_by, row.rule_date, row.due_date) == ("posted", "import", date(2026, 10, 3), date(2026, 10, 3))
    assert row.posted_entry_ids == [entry.id]
    assert entry.entry_date == row.due_date


def test_row_back_to_pending_follows_refresh_rules(db_session, backup, today):
    # Review fix: an import-posted period whose record MOZE moved into the future returns to pending on the record's
    # day; on an owner-priced definition (decision 24) it takes no MOZE override and the difference is reported.
    today(date(2026, 10, 3))
    _import_backup(db_session, _weekly(backup))
    definition = _definition(db_session, "PER-W")
    first = _instances(db_session, definition)[0]
    assert (first.status, first.acted_by) == ("posted", "import")
    line = definition.template["lines"][0]
    definition.template = {**definition.template, "lines": [{**line, "amount": "120"}]}
    definition.template_owner_edited = True
    db_session.commit()
    data = backup.data(
        accounts=_accounts(backup), periods=[backup.period("PER-W", unit=1, days=2, start="2026-09-21T00:00:00")],
        records=[_rec(backup, "R-2026-09-21", "2026-10-19", eventID="PER-W")]
        + [_rec(backup, f"R-{day}", day, eventID="PER-W") for day in WEEKLY[1:]],
    )

    summary = _import_backup(db_session, data)

    db_session.expire_all()
    row = db_session.get(ScheduleInstance, first.id)
    assert (row.status, row.acted_by, row.posted_entry_ids) == ("pending", None, [])
    assert (row.rule_date, row.due_date, row.amount_override) == (date(2026, 10, 19), date(2026, 10, 19), None)
    assert {"definition_id": definition.id, "name": definition.name, "seq": row.seq, "date": "2026-10-19", "line": 0,
            "kind": "expense", "amount": "120", "moze_amount": "100"} in summary["schedules"]["amount_differs"]


def test_new_period_on_a_taken_day_is_reported(db_session, backup, today):
    # Review fix: a new MOZE period on a day another posted or pending period of the definition holds is reported
    # (same_date) rather than created, which would collide on ux_schedule_instance_posted_day at posting.
    today(date(2026, 10, 3))
    _import_backup(db_session, _weekly(backup))
    definition = _definition(db_session, "PER-W")
    moved = _instances(db_session, definition)[3]
    moved.due_date, moved.edited_by_owner = date(2026, 10, 19), True
    db_session.commit()

    summary = _import_backup(db_session, _weekly(backup, extra=[_rec(backup, "R-NEW", "2026-10-19", eventID="PER-W")]))

    assert {"moze_id": "R-NEW", "reason": "same_date"} in summary["schedules"]["review"]
    rows = _instances(db_session, _definition(db_session, "PER-W"))
    assert len(rows) == 4 and all(row.moze_id != "R-NEW" for row in rows)
