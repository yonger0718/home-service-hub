"""Pure mapping of MOZE scheduled data (spec "Schedule definitions and instances from the backup", D37)."""

from datetime import date, datetime
from decimal import Decimal

import pytest

from app.services import schedule_rules as rules
from app.services.moze_backup_json import parse_backup_doc
from app.services.moze_csv import MozeImportError
from app.services.schedule_import_map import map_schedules, moze_weekday


def _accounts(backup):
    return [backup.account("A-WALLET", "錢包"), backup.account("A-BANK", "銀行")]


def _rec(backup, identifier, day, *, type_=0, price=-100, account="A-WALLET", **fields):
    return backup.record(identifier, account, type_=type_, price=price, date=f"{day}T00:00:00", **fields)


def test_moze_weekday_numbering():
    assert [moze_weekday(date(2026, 9, 20 + offset)) for offset in range(7)] == [1, 2, 3, 4, 5, 6, 7]  # Sun … Sat


def test_weekly_period_keyed_by_weekday(backup):
    # Spec "Weekly period keyed by weekday" (exported 2026-10-01).
    days = ("2026-09-21", "2026-09-28", "2026-10-05", "2026-10-12")
    data = backup.data(
        accounts=_accounts(backup),
        periods=[backup.period("PER-W", unit=1, days=2, times=0, start="2026-10-05T00:00:00")],
        records=[_rec(backup, f"R-{day}", day, eventID="PER-W") for day in days],
    )
    result = map_schedules(data)
    [definition] = result.definitions
    assert (
        definition.kind, definition.source, definition.interval_unit, definition.interval_n, definition.anchor_date,
        definition.times, definition.day_of_month, definition.review_reason,
    ) == ("recurring", "period", "week", 1, date(2026, 9, 21), None, None, None)
    assert [(item.seq, item.day, item.past, item.enabled) for item in definition.instances] == [
        (1, date(2026, 9, 21), True, True), (2, date(2026, 9, 28), True, True),
        (3, date(2026, 10, 5), False, True), (4, date(2026, 10, 12), False, True),
    ]
    assert result.records_mapped == 2


def test_monthly_transfer_period_keyed_by_day_of_month(backup):
    # Spec "Monthly period keyed by day of month".
    days = [rules.add_months(date(2026, 8, 21), k) for k in range(12)]
    records, transfers = [], []
    for k, day in enumerate(days):
        records += [
            _rec(backup, f"O-{k}", day.isoformat(), type_=2, price=-15000, eventID="PER-M"),
            _rec(backup, f"I-{k}", day.isoformat(), type_=2, price=15000, account="A-BANK", eventID="PER-M"),
        ]
        transfers.append(backup.transfer(f"X-{k}", f"O-{k}", f"I-{k}"))
    data = backup.data(
        accounts=_accounts(backup), records=records, transfers=transfers,
        periods=[backup.period("PER-M", unit=2, days=21, times=12, type_=1, start="2026-10-21T00:00:00")],
    )
    result = map_schedules(data)
    [definition] = result.definitions
    assert (definition.interval_unit, definition.day_of_month, definition.anchor_date, definition.times) == (
        "month", 21, date(2026, 8, 21), 12,
    )
    [line] = definition.lines
    assert (line.kind, line.account, line.to_account, line.amount, line.to_amount) == (
        "transfer", "A-WALLET", "A-BANK", "15000", "15000",
    )
    assert len(definition.instances) == 12
    assert definition.instances[0].record_ids == ["O-0", "I-0"] and definition.instances[0].amounts == ["15000"]
    assert result.records_mapped == 20


def test_day_of_month_clamped(backup):
    # Spec "Day of month clamped".
    data = backup.data(
        accounts=_accounts(backup),
        periods=[backup.period("PER-31", unit=2, days=31, start="2027-01-31T00:00:00")],
        records=[_rec(backup, "R-JAN", "2027-01-31", eventID="PER-31"), _rec(backup, "R-FEB", "2027-02-28", eventID="PER-31")],
    )
    [definition] = map_schedules(data).definitions
    assert (definition.day_of_month, definition.review_reason) == (31, None)
    assert [item.seq for item in definition.instances] == [1, 2]


def test_weekday_mismatch_flagged(backup):
    # Spec "Weekday mismatch flagged".
    data = backup.data(
        accounts=_accounts(backup),
        periods=[backup.period("PER-T", unit=1, days=3, start="2026-10-05T00:00:00")],
        records=[_rec(backup, "R-1", "2026-09-21", eventID="PER-T"), _rec(backup, "R-2", "2026-09-28", eventID="PER-T")],
    )
    result = map_schedules(data)
    assert result.definitions[0].review_reason == "interval_mismatch"
    assert {"moze_id": "PER-T", "reason": "interval_mismatch"} in result.review
    assert [item.seq for item in result.definitions[0].instances] == [1, 2]


def test_installment_anchored_on_its_first_period(backup):
    # Spec "Installment anchored on its first period".
    dates = [f"{rules.add_months(date(2026, 2, 9), k).isoformat()}T00:00:00" for k in range(36)]
    data = backup.data(
        accounts=_accounts(backup),
        installments=[backup.installment("INS-1", day_of_month=9, dates=dates, times=36, total=300000, remainder=150000)],
        records=[_rec(backup, "R-NOV", "2026-11-09", type_=6, price=-8333, eventID="INS-1", relatedID="R-LOAN")],
    )
    [definition] = map_schedules(data).definitions
    assert (definition.kind, definition.anchor_date, definition.day_of_month, definition.times) == (
        "installment", date(2026, 2, 9), 9, 36,
    )
    assert (definition.total_amount, definition.remainder) == (Decimal("300000"), Decimal("150000"))
    assert [(line.kind, line.related) for line in definition.lines] == [("repayment", "R-LOAN")]
    assert [(item.seq, item.day) for item in definition.instances] == [(10, date(2026, 11, 9))]


def test_repayment_and_interest_are_one_period(backup):
    # Spec "Repayment and interest are one period".
    dates = [f"{rules.add_months(date(2026, 10, 9), k).isoformat()}T00:00:00" for k in range(3)]
    data = backup.data(
        accounts=_accounts(backup),
        installments=[backup.installment("INS-1", dates=dates, times=3, total=25000)],
        records=[
            _rec(backup, "R-REP", "2026-11-09", type_=6, price=-8333, eventID="INS-1", packageID="PK-9", relatedID="R-LOAN"),
            _rec(backup, "R-INT", "2026-11-09", type_=15, price=-612, eventID="INS-1", packageID="PK-9"),
        ],
    )
    [definition] = map_schedules(data).definitions
    [instance] = definition.instances
    assert [line.kind for line in definition.lines] == ["repayment", "interest"]
    assert (instance.moze_id, instance.record_ids, instance.amounts, instance.seq) == (
        "R-REP", ["R-REP", "R-INT"], ["8333", "612"], 2,
    )


def test_disabled_future_period(backup):
    # Spec "Disabled future period" (mapping side: the instance is not enabled; Task 17 makes it skipped).
    data = backup.data(
        accounts=_accounts(backup),
        periods=[backup.period("PER-D", unit=2, days=3, start="2026-11-03T00:00:00")],
        records=[
            _rec(backup, "R-NOV", "2026-11-03", eventID="PER-D"),
            _rec(backup, "R-DEC", "2026-12-03", eventID="PER-D", isEnabled=False),
        ],
    )
    result = map_schedules(data)
    assert [(item.day, item.enabled) for item in result.definitions[0].instances] == [
        (date(2026, 11, 3), True), (date(2026, 12, 3), False),
    ]
    assert result.records_mapped == 1


def test_single_record_keyed_apart_from_definitions(backup):
    # Spec "Single record keyed apart from definitions".
    data = backup.data(accounts=_accounts(backup), records=[_rec(backup, "X1", "2026-11-20", name="年費")])
    [definition] = map_schedules(data).definitions
    assert (definition.moze_id, definition.kind, definition.source, definition.times, definition.anchor_date, definition.name) == (
        "record:X1", "recurring", "single", 1, date(2026, 11, 20), "年費",
    )
    assert [(item.seq, item.moze_id) for item in definition.instances] == [(1, "X1")]


def test_counts_add_up_to_the_enabled_future_records(backup):
    data = backup.data(
        accounts=_accounts(backup),
        records=[
            _rec(backup, "R-RW", "2026-11-01", type_=14, price=30),
            _rec(backup, "R-ADJ", "2026-11-01", type_=7, price=100),
            _rec(backup, "R-OFF", "2026-11-01", isEnabled=False),
            _rec(backup, "R-GONE", "2026-11-02", eventID="GONE"),
            _rec(backup, "R-PAST", "2026-09-01"),
        ],
    )
    result = map_schedules(data)
    assert (result.records_mapped, result.rewards_ignored, dict(result.unsupported_types)) == (1, 1, {"7": 1})
    assert [definition.moze_id for definition in result.definitions] == ["record:R-GONE"]
    assert {"moze_id": "R-GONE", "reason": "event_missing"} in result.review
    # The past record stays an ordinary entry, but is offered as a past single (the importer keeps it only when an
    # earlier import made it a record:R-PAST definition); the disabled future one is neither.
    assert [(definition.moze_id, definition.instances[0].past) for definition in result.past_singles] == [
        ("record:R-PAST", True),
    ]


def test_a_later_package_kind_joins_the_template(backup):
    # The first package lacks its interest record: the template still gets an interest line, so the later
    # interest amounts are posted, and the first period's interest is "0".
    dates = [f"{rules.add_months(date(2026, 10, 9), k).isoformat()}T00:00:00" for k in range(3)]
    data = backup.data(
        accounts=_accounts(backup),
        installments=[backup.installment("INS-1", dates=dates, times=3, total=25000)],
        records=[
            _rec(backup, "R-REP1", "2026-11-09", type_=6, price=-8333, eventID="INS-1", packageID="PK-1", relatedID="R-LOAN"),
            _rec(backup, "R-REP2", "2026-12-09", type_=6, price=-8333, eventID="INS-1", packageID="PK-2", relatedID="R-LOAN"),
            _rec(backup, "R-INT2", "2026-12-09", type_=15, price=-612, eventID="INS-1", packageID="PK-2"),
        ],
    )
    [definition] = map_schedules(data).definitions
    assert [line.kind for line in definition.lines] == ["repayment", "interest"]
    assert [item.amounts for item in definition.instances] == [["8333", "0"], ["8333", "612"]]


def test_period_type_must_match_its_records(backup):
    # Spec: an AHPeriod of type 1 generates transfers, type 0 ordinary records; a disagreement is reviewed.
    data = backup.data(
        accounts=_accounts(backup),
        periods=[backup.period("PER-X", unit=2, days=3, type_=1, start="2026-11-03T00:00:00")],
        records=[_rec(backup, "R-NOV", "2026-11-03", eventID="PER-X")],
    )
    result = map_schedules(data)
    assert result.definitions[0].review_reason == "interval_mismatch"
    assert {"moze_id": "PER-X", "reason": "interval_mismatch"} in result.review


def test_finite_period_never_runs_past_its_last_record(backup):
    # MOZE deleted the first three records of a 12-time series: the 9 left are seqs 1–9, so times becomes 9 and
    # HomeHub never generates the three phantom periods after MOZE's real end.
    days = [rules.add_months(date(2026, 11, 3), k) for k in range(9)]
    data = backup.data(
        accounts=_accounts(backup),
        periods=[backup.period("PER-F", unit=2, days=3, times=12, start="2026-11-03T00:00:00")],
        records=[_rec(backup, f"R-{k}", day.isoformat(), eventID="PER-F") for k, day in enumerate(days)],
    )
    [definition] = map_schedules(data).definitions
    assert (definition.times, [item.seq for item in definition.instances][-1]) == (9, 9)


def test_same_date_records_are_reviewed(backup):
    data = backup.data(
        accounts=_accounts(backup),
        periods=[backup.period("PER-2", unit=2, days=5, start="2026-10-05T00:00:00")],
        records=[_rec(backup, "R-A", "2026-10-05", eventID="PER-2"), _rec(backup, "R-B", "2026-10-05", eventID="PER-2")],
    )
    result = map_schedules(data)
    assert len(result.definitions[0].instances) == 1
    assert {"moze_id": "R-B", "reason": "same_date"} in result.review


def test_period_and_installment_fields_are_validated(backup):
    doc = backup.doc(accounts=_accounts(backup), periods=[{"identifier": "PER-X", "unit": 2}])
    with pytest.raises(MozeImportError, match="AHPeriod 'PER-X': missing field 'days'"):
        parse_backup_doc(doc)
    as_list = backup.installment("INS-L", dates=())
    as_list["dateInfo"] = ["2026-11-09T00:00:00", "2026-12-09T00:00:00"]
    as_dict = backup.installment("INS-D", dates=())
    as_dict["dateInfo"] = {str(k): f"2027-{k + 1:02d}-09T00:00:00" for k in range(11)}
    data = parse_backup_doc(backup.doc(accounts=_accounts(backup), installments=[as_list, as_dict]))
    assert data.installments[0]["dateInfo"] == [datetime(2026, 11, 9), datetime(2026, 12, 9)]
    assert data.installments[1]["dateInfo"][9:] == [datetime(2027, 10, 9), datetime(2027, 11, 9)]


def test_later_transfer_group_does_not_crash_and_flags_the_period(backup):
    # An expense first, a transfer pair later under one period: mapped without an exception, flagged for review.
    data = backup.data(
        accounts=_accounts(backup),
        periods=[backup.period("PER-MIX", unit=2, days=3, start="2026-11-03T00:00:00")],
        records=[
            _rec(backup, "R-1", "2026-11-03", eventID="PER-MIX"),
            _rec(backup, "O-2", "2026-12-03", type_=2, price=-50, eventID="PER-MIX"),
            _rec(backup, "I-2", "2026-12-03", type_=2, price=50, account="A-BANK", eventID="PER-MIX"),
        ],
        transfers=[backup.transfer("X-2", "O-2", "I-2")],
    )
    result = map_schedules(data)
    [definition] = result.definitions
    assert definition.review_reason == "interval_mismatch"
    assert {"moze_id": "PER-MIX", "reason": "interval_mismatch"} in result.review
    assert [line.kind for line in definition.lines] == ["expense", "transfer"]
    assert [item.amounts for item in definition.instances] == [["100", "0"], ["0", "50"]]


def test_in_leg_only_transfer_group_is_flagged(backup):
    # The backup holds a transfer's in-leg without its out-leg: never mapped as an out-leg, flagged for review.
    data = backup.data(
        accounts=_accounts(backup),
        periods=[backup.period("PER-IN", unit=2, days=3, type_=1, start="2026-11-03T00:00:00")],
        records=[_rec(backup, "I-1", "2026-11-03", type_=2, price=50, account="A-BANK", eventID="PER-IN")],
    )
    result = map_schedules(data)
    [definition] = result.definitions
    assert definition.review_reason == "interval_mismatch"
    assert {"moze_id": "PER-IN", "reason": "interval_mismatch"} in result.review


# --- Task 28 shape fix (rulings R1–R4): synthetic shapes of the real backup, never real data -------------------------


def test_rule_fields_accept_digit_strings(backup):
    # R1: MOZE stores AHPeriod.days as a Realm string; times, count, startIndex and dayOfMonth may be strings too.
    period = backup.period("PER-S", unit=2, days="21", times="12", count="1", startIndex="1")
    installment = backup.installment("INS-S", day_of_month="9", times="3", count="3", startIndex="1")
    data = parse_backup_doc(backup.doc(accounts=_accounts(backup), periods=[period], installments=[installment]))
    assert [data.periods[0][key] for key in ("days", "times", "count", "startIndex")] == [21, 12, 1, 1]
    assert [data.installments[0][key] for key in ("dayOfMonth", "times", "count", "startIndex")] == [9, 3, 3, 1]
    for field, value in (("days", "2a"), ("times", ""), ("startIndex", "-1"), ("count", True)):
        bad = backup.period("PER-X", **{field: value}) if field != "days" else backup.period("PER-X", days=value)
        with pytest.raises(MozeImportError, match=f"AHPeriod 'PER-X': field '{field}' must be an integer"):
            parse_backup_doc(backup.doc(accounts=_accounts(backup), periods=[bad]))
    with pytest.raises(MozeImportError, match="AHInstallment 'INS-X': field 'dayOfMonth' must be an integer"):
        parse_backup_doc(backup.doc(accounts=_accounts(backup), installments=[backup.installment("INS-X", day_of_month="9th")]))


def test_count_and_start_index_are_optional(backup):
    period = backup.period("PER-O")
    del period["count"], period["startIndex"]
    data = parse_backup_doc(backup.doc(accounts=_accounts(backup), periods=[period]))
    assert "startIndex" not in data.periods[0]


def _purchase_installment(backup, *, times=3, dates=None, origin_total=-3000, origin_enabled=False):
    """A non-loan installment as the real backup shapes it: dateInfo empty, a disabled purchase record for the whole
    total on the first period's date, then one record per period."""
    dates = dates or [rules.add_months(date(2026, 8, 30), k, 30) for k in range(times)]
    records = [_rec(backup, "R-BUY", dates[0].isoformat(), price=origin_total, eventID="INS-P", isEnabled=origin_enabled)]
    records += [_rec(backup, f"R-{k}", day.isoformat(), price=-1000, eventID="INS-P") for k, day in enumerate(dates)]
    installment = backup.installment("INS-P", day_of_month=30, times=times, total=3000, remainder=1000, installment=1000)
    installment["dateInfo"] = {}
    return backup.data(accounts=_accounts(backup), installments=[installment], records=records)


def test_installment_dates_come_from_its_records_when_date_info_is_empty(backup):
    # R2: the purchase record is the origin, not a period; the rest are the periods, anchored on the first one.
    result = map_schedules(_purchase_installment(backup))
    [definition] = result.definitions
    assert (definition.kind, definition.anchor_date, definition.day_of_month, definition.times, definition.review_reason) == (
        "installment", date(2026, 8, 30), 30, 3, None,
    )
    assert [(item.seq, item.day, item.moze_id) for item in definition.instances] == [
        (1, date(2026, 8, 30), "R-0"), (2, date(2026, 9, 30), "R-1"), (3, date(2026, 10, 30), "R-2"),
    ]
    assert all("R-BUY" not in item.record_ids for item in definition.instances)
    assert [line.amount for line in definition.lines] == ["1000"]
    assert result.review == []


def test_derived_installment_dates_off_the_monthly_rule_are_flagged(backup):
    # R2: every derived date must be add_months(anchor, k, dayOfMonth); MOZE rolled 02-29 over to 03-01 once.
    dates = [date(2027, 1, 29), date(2027, 3, 1), date(2027, 3, 29)]
    result = map_schedules(_purchase_installment(backup, dates=dates))
    [definition] = result.definitions
    assert definition.review_reason == "interval_mismatch"
    assert {"moze_id": "INS-P", "reason": "interval_mismatch"} in result.review


def test_future_enabled_origin_stays_one_entry(backup):
    # An origin MOZE has not booked yet is not dropped: it maps as a single record:<id> definition.
    dates = [rules.add_months(date(2026, 11, 30), k, 30) for k in range(3)]
    result = map_schedules(_purchase_installment(backup, dates=dates, origin_enabled=True))
    assert sorted((item.source, item.moze_id) for item in result.definitions) == [
        ("installment", "INS-P"), ("single", "record:R-BUY"),
    ]
    [installment] = [item for item in result.definitions if item.source == "installment"]
    assert [item.moze_id for item in installment.instances] == ["R-0", "R-1", "R-2"]


def test_loan_without_date_info_sets_its_payables_aside(backup):
    # R2: a loan's two type-4 records (the loan) are the origin; the repayment + interest packages are the periods.
    # MOZE's repayments name the disabled copy by relatedID; the line takes the enabled payable the importer books.
    days = [rules.add_months(date(2026, 9, 9), k) for k in range(3)]
    records = [
        _rec(backup, "R-LOAN", "2026-08-20", type_=4, price=30000, eventID="INS-L", isEnabled=False),
        _rec(backup, "R-LOAN2", "2026-08-20", type_=4, price=30000, eventID="INS-L"),
    ]
    for k, day in enumerate(days):
        records += [
            _rec(backup, f"R-REP{k}", day.isoformat(), type_=6, price=-10000, eventID="INS-L", packageID=f"PK-{k}", relatedID="R-LOAN"),
            _rec(backup, f"R-INT{k}", day.isoformat(), type_=15, price=-100, eventID="INS-L", packageID=f"PK-{k}"),
        ]
    installment = backup.installment("INS-L", day_of_month=9, times=3, total=30000, remainder=20000)
    installment["dateInfo"] = {}
    result = map_schedules(backup.data(accounts=_accounts(backup), installments=[installment], records=records))
    [definition] = result.definitions
    assert (definition.anchor_date, definition.times, definition.review_reason) == (date(2026, 9, 9), 3, None)
    assert [(line.kind, line.related) for line in definition.lines] == [("repayment", "R-LOAN2"), ("interest", None)]
    assert [(item.seq, item.record_ids) for item in definition.instances] == [
        (k + 1, [f"R-REP{k}", f"R-INT{k}"]) for k in range(3)
    ]


def test_installment_without_records_or_dates_is_still_no_records(backup):
    installment = backup.installment("INS-E", times=3)
    installment["dateInfo"] = {}
    result = map_schedules(backup.data(accounts=_accounts(backup), installments=[installment]))
    assert (result.definitions, result.review) == ([], [{"moze_id": "INS-E", "reason": "no_records"}])


def test_finite_period_total_is_start_index_plus_times(backup):
    # R3: MOZE's `times` counts the repeats after the first record; the series has startIndex + times periods.
    days = [rules.add_months(date(2026, 10, 3), k) for k in range(4)]
    data = backup.data(
        accounts=_accounts(backup),
        periods=[backup.period("PER-F", unit=2, days=3, times=3, startIndex=1, start="2026-10-03T00:00:00")],
        records=[_rec(backup, f"R-{k}", day.isoformat(), eventID="PER-F") for k, day in enumerate(days)],
    )
    [definition] = map_schedules(data).definitions
    assert (definition.times, [item.seq for item in definition.instances], definition.review_reason) == (
        4, [1, 2, 3, 4], None,
    )


def test_record_beyond_the_series_end_flags_the_period(backup):
    # Task 28: a disabled record far after a finite series' last period. Kept, numbered by position, paused.
    days = [rules.add_months(date(2026, 1, 3), k) for k in range(3)] + [date(2028, 6, 3)]
    data = backup.data(
        accounts=_accounts(backup),
        periods=[backup.period("PER-G", unit=2, days=3, times=3, startIndex=1, start="2026-01-03T00:00:00")],
        records=[
            _rec(backup, f"R-{k}", day.isoformat(), eventID="PER-G", isEnabled=k < 3) for k, day in enumerate(days)
        ],
    )
    result = map_schedules(data)
    [definition] = result.definitions
    assert (definition.review_reason, definition.times) == ("interval_mismatch", 4)
    assert [(item.seq, item.enabled) for item in definition.instances] == [(1, True), (2, True), (3, True), (4, False)]
    assert {"moze_id": "PER-G", "reason": "interval_mismatch"} in result.review


@pytest.mark.parametrize(("start", "review"), [
    ("2023-04-21T00:00:00", None),  # 42 months before the first record, on the rule's day of month (R4)
    ("2027-02-21T00:00:00", None),  # after the first record, on phase
    ("2023-04-20T00:00:00", "interval_mismatch"),  # off phase
])
def test_start_date_only_checked_for_phase(backup, start, review):
    data = backup.data(
        accounts=_accounts(backup),
        periods=[backup.period("PER-P", unit=2, days=21, start=start)],
        records=[_rec(backup, "R-1", "2026-10-21", eventID="PER-P"), _rec(backup, "R-2", "2026-11-21", eventID="PER-P")],
    )
    assert map_schedules(data).definitions[0].review_reason == review


def test_weekly_start_date_before_the_anchor_on_its_weekday(backup):
    data = backup.data(
        accounts=_accounts(backup),
        periods=[backup.period("PER-W", unit=1, days="2", start="2025-01-06T00:00:00")],  # a Monday
        records=[_rec(backup, "R-1", "2026-09-21", eventID="PER-W"), _rec(backup, "R-2", "2026-09-28", eventID="PER-W")],
    )
    assert map_schedules(data).definitions[0].review_reason is None


def test_same_date_keeps_the_enabled_group(backup):
    # Task 28: MOZE keeps a disabled copy beside a live series' last period; the enabled record is the period.
    data = backup.data(
        accounts=_accounts(backup),
        periods=[backup.period("PER-2", unit=2, days=5, start="2026-10-05T00:00:00")],
        records=[
            _rec(backup, "R-A", "2026-11-05", eventID="PER-2", isEnabled=False),
            _rec(backup, "R-B", "2026-11-05", eventID="PER-2"),
        ],
    )
    result = map_schedules(data)
    assert [(item.moze_id, item.enabled) for item in result.definitions[0].instances] == [("R-B", True)]
    assert {"moze_id": "R-A", "reason": "same_date"} in result.review
