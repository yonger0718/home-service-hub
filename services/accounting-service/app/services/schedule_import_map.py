"""Pure mapping of MOZE's scheduled data to schedule definitions and instances (design D37).

No database here: MOZE identifiers stay as they are; schedule_import resolves them to HomeHub rows inside the
importer's ledger transaction.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from . import schedule_rules as rules
from .moze_backup_json import BackupData, category_name

MOZE_LINE_KINDS: dict[int, str] = {
    0: "expense", 1: "income", 2: "transfer", 3: "receivable", 4: "payable", 5: "collection", 6: "repayment",
    15: "interest",
}
REWARD_TYPE = 14
TRANSFER_TYPE = 2
INTEREST_TYPE = 15
SETTLING_TYPES = (5, 6)
INSTALLMENT_PRIMARY_TYPES = (6, 0)


def moze_weekday(day: date) -> int:
    """MOZE's (Apple Calendar's) weekday numbering: Sunday 1 … Saturday 7. Confirmed by Task 28 on the real backup."""
    return day.isoweekday() % 7 + 1


@dataclass
class MappedLine:
    kind: str
    account: str | None
    amount: str
    to_account: str | None = None
    to_amount: str | None = None
    target: str | None = None
    classification: str | None = None
    project: str | None = None
    related: str | None = None
    name: str | None = None
    store: str | None = None


@dataclass
class MappedInstance:
    moze_id: str
    record_ids: list[str]
    day: date
    seq: int | None
    amounts: list[str]
    past: bool
    enabled: bool
    records: list[dict]


@dataclass
class MappedDefinition:
    moze_id: str
    kind: str
    source: str  # period | installment | single
    name: str
    interval_unit: str
    interval_n: int
    anchor_date: date
    day_of_month: int | None
    times: int | None
    total_amount: Decimal | None
    remainder: Decimal | None
    lines: list[MappedLine]
    review_reason: str | None
    payload: dict | None
    instances: list[MappedInstance] = field(default_factory=list)


@dataclass
class MapResult:
    definitions: list[MappedDefinition] = field(default_factory=list)
    records_mapped: int = 0
    rewards_ignored: int = 0
    unsupported_types: Counter = field(default_factory=Counter)
    review: list[dict] = field(default_factory=list)
    # Past records without a definition, as `record:<id>` singles. Ordinary entries — unless HomeHub already holds
    # that record:<id> definition (a single future record of an earlier import that has turned past); the importer
    # keeps only those (schedule_import.merge_known_past_singles).
    past_singles: list[MappedDefinition] = field(default_factory=list)


def _abs(value) -> str:
    return rules.plain(abs(Decimal(str(value))))


def _text(value: str | None) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _day(record: dict) -> date:
    return record["date"].date()


def record_groups(records: list[dict], data: BackupData) -> list[list[dict]]:
    """Records of one definition merged into periods, earliest first: a transfer's in-leg joins its out-leg; a
    type-15 record joins the type-5/6 record of the same package on the same date. The first record is the primary."""
    by_id = {record["identifier"]: record for record in records}
    in_of_out = {
        transfer["outRecord"]: transfer["inRecord"]
        for transfer in data.transfers
        if transfer["outRecord"] in by_id and transfer["inRecord"] in by_id
    }
    merged_ins = set(in_of_out.values())
    settling = {}
    for record in records:
        if record["type"] in SETTLING_TYPES and record["packageID"]:
            settling.setdefault((record["packageID"], _day(record)), record["identifier"])

    def attached_interest(record: dict) -> str | None:
        if record["type"] != INTEREST_TYPE or not record["packageID"]:
            return None
        return settling.get((record["packageID"], _day(record)))

    groups: dict[str, list[dict]] = {}
    for record in sorted(records, key=lambda item: (item["date"], item["identifier"])):
        identifier = record["identifier"]
        if identifier in merged_ins or attached_interest(record) is not None:
            continue
        groups[identifier] = [record]
        if identifier in in_of_out:
            groups[identifier].append(by_id[in_of_out[identifier]])
    for record in sorted(records, key=lambda item: item["identifier"]):
        primary = attached_interest(record)
        if primary is not None and primary in groups:
            groups[primary].append(record)
    return list(groups.values())


def _lines(group: list[dict]) -> list[MappedLine]:
    primary = group[0]
    if primary["type"] == TRANSFER_TYPE:
        in_leg = group[1] if len(group) > 1 else None
        return [
            MappedLine(
                kind="transfer", account=primary["account"], amount=_abs(primary["total"]),
                to_account=in_leg["account"] if in_leg else None, to_amount=_abs(in_leg["total"]) if in_leg else None,
                classification=primary["classification"], project=primary["project"], name=_text(primary["name"]),
                store=_text(primary["store"]),
            )
        ]
    return [
        MappedLine(
            kind=MOZE_LINE_KINDS[record["type"]], account=record["account"], amount=_abs(record["total"]),
            target=record["target"], classification=record["classification"], project=record["project"],
            related=record["relatedID"] if record["type"] in SETTLING_TYPES else None, name=_text(record["name"]),
            store=_text(record["store"]),
        )
        for record in group
    ]


def _union_lines(groups: list[list[dict]]) -> list[MappedLine]:
    """Template lines of the first group, plus a line for each record kind a later group carries more often than the
    template (a first package without its interest record): no later amount is dropped by _amounts. A first transfer
    without its in-leg takes the in-leg account of a later group."""
    lines = _lines(groups[0])
    for group in groups[1:]:
        candidates = _lines(group)
        for candidate in candidates:
            if candidate.kind == "transfer":
                first = next(line for line in lines if line.kind == "transfer")
                if first.to_account is None and candidate.to_account is not None:
                    first.to_account, first.to_amount = candidate.to_account, candidate.to_amount
                continue
            have = sum(1 for line in lines if line.kind == candidate.kind)
            need = sum(1 for line in candidates if line.kind == candidate.kind)
            if have < need:
                lines.append(candidate)
    return lines


def _amounts(lines: list[MappedLine], group: list[dict]) -> list[str]:
    """|total| per template line, matched by kind; "0" for a line whose package member is missing (D37)."""
    used: set[str] = set()
    amounts = []
    for line in lines:
        match = None
        for record in group:
            kind = "transfer" if record["type"] == TRANSFER_TYPE else MOZE_LINE_KINDS.get(record["type"])
            if record["identifier"] not in used and kind == line.kind:
                match = record
                break
        if match is None:
            amounts.append("0")
        else:
            used.add(match["identifier"])
            amounts.append(_abs(match["total"]))
            if line.kind == "transfer":
                used.update(record["identifier"] for record in group)  # the in-leg belongs to this line
    return amounts


def _name(group: list[dict], data: BackupData, default: str) -> str:
    primary = group[0]
    if _text(primary["name"]):
        return _text(primary["name"])[:128]
    names = {row["identifier"]: row["name"] for row in data.classifications}
    names.update({row["identifier"]: category_name(row["name"]) for row in data.categories})
    return (names.get(primary["classification"]) or default)[:128]


def _instance(group: list[dict], lines: list[MappedLine], seq: int | None, cutoff: date) -> MappedInstance:
    primary = group[0]
    return MappedInstance(
        moze_id=primary["identifier"], record_ids=[record["identifier"] for record in group], day=_day(primary),
        seq=seq, amounts=_amounts(lines, group), past=_day(primary) <= cutoff,
        enabled=all(record["isEnabled"] for record in group), records=group,
    )


def _add_instances(definition: MappedDefinition, groups, seq_of, cutoff: date, result: MapResult) -> None:
    seen: set[date] = set()
    for group in groups:
        day = _day(group[0])
        seq = seq_of(day)
        if seq is None and definition.source == "installment":
            result.review.append({"moze_id": group[0]["identifier"], "reason": "interval_mismatch"})
            continue
        if seq is None:
            definition.review_reason = "interval_mismatch"
        if day in seen:
            result.review.append({"moze_id": group[0]["identifier"], "reason": "same_date"})
            continue
        seen.add(day)
        definition.instances.append(_instance(group, definition.lines, seq, cutoff))
    if definition.review_reason == "interval_mismatch" and definition.source == "period":
        for position, item in enumerate(definition.instances, start=1):
            item.seq = position  # off-rule dates: keep every period, numbered by position (the definition is paused)


def _map_period(period: dict, records: list[dict], data: BackupData, cutoff: date, result: MapResult) -> MappedDefinition | None:
    groups = record_groups(records, data)
    if not groups:
        result.review.append({"moze_id": period["identifier"], "reason": "no_records"})
        return None
    anchor = _day(groups[0][0])
    review, unit, day_of_month = None, {1: "week", 2: "month"}.get(period["unit"]), None
    if unit is None:
        unit, review = "month", "interval_mismatch"
    elif unit == "week":
        if period["days"] != moze_weekday(anchor):
            review = "interval_mismatch"
    else:
        day_of_month = period["days"] if 1 <= period["days"] <= 31 else None
        if day_of_month is None or anchor.day != min(day_of_month, rules.days_in_month(anchor.year, anchor.month)):
            review = "interval_mismatch"
    start = period["startDate"].date() if period["startDate"] else None
    if start is not None and rules.occurrence_index(anchor, unit, 1, day_of_month, start) is None:
        review = "interval_mismatch"
    primary_is_transfer = groups[0][0]["type"] == TRANSFER_TYPE
    if period["type"] in (0, 1) and (period["type"] == 1) != primary_is_transfer:
        review = "interval_mismatch"  # spec: type 1 periods generate transfers, type 0 ordinary records
    lines = _union_lines(groups)
    definition = MappedDefinition(
        moze_id=period["identifier"], kind="recurring", source="period", name=_name(groups[0], data, "週期"),
        interval_unit=unit, interval_n=1, anchor_date=anchor, day_of_month=day_of_month,
        times=None if period["times"] == 0 else period["times"], total_amount=None, remainder=None, lines=lines,
        review_reason=review, payload=period,
    )

    def seq_of(day: date) -> int | None:
        index = rules.occurrence_index(anchor, unit, 1, day_of_month, day)
        return None if index is None else index + 1

    _add_instances(definition, groups, seq_of, cutoff, result)
    if definition.times is not None and definition.instances and definition.review_reason is None:
        # MOZE pre-generates a finite series. Seqs count from the earliest record still in the backup, so when MOZE
        # deleted early records the rebased seqs end below `times`: never let HomeHub generate past MOZE's last
        # record (Task 28.5 checks every finite period's record count against its `times`).
        definition.times = min(definition.times, max(item.seq for item in definition.instances))
    if definition.review_reason is not None:
        result.review.append({"moze_id": definition.moze_id, "reason": definition.review_reason})
    return definition


def _map_installment(
    installment: dict, records: list[dict], data: BackupData, cutoff: date, result: MapResult
) -> MappedDefinition | None:
    groups = record_groups(records, data)
    dates = [value.date() for value in installment["dateInfo"]]
    if not groups or not dates:
        result.review.append({"moze_id": installment["identifier"], "reason": "no_records"})
        return None
    anchor = dates[0]
    day_of_month = installment["dayOfMonth"] if 1 <= installment["dayOfMonth"] <= 31 else None
    review = None
    for k, day in enumerate(dates):
        if day != rules.add_months(anchor, k, day_of_month):
            review = "interval_mismatch"
    first = next((group for group in groups if group[0]["type"] in INSTALLMENT_PRIMARY_TYPES), groups[0])
    total = abs(Decimal(str(installment["total"])))
    definition = MappedDefinition(
        moze_id=installment["identifier"], kind="installment" if installment["times"] >= 2 else "recurring",
        source="installment", name=_name(first, data, "分期"), interval_unit="month", interval_n=1,
        anchor_date=anchor, day_of_month=day_of_month, times=installment["times"],
        total_amount=total if total > 0 else None, remainder=abs(Decimal(str(installment["remainder"]))),
        lines=_union_lines([first, *(group for group in groups if group is not first)]), review_reason=review,
        payload=installment,
    )
    positions = {day: index + 1 for index, day in reversed(list(enumerate(dates)))}
    _add_instances(definition, groups, positions.get, cutoff, result)
    if review is not None:
        result.review.append({"moze_id": definition.moze_id, "reason": review})
    return definition


def _map_single(group: list[dict], data: BackupData, cutoff: date) -> MappedDefinition:
    primary = group[0]
    lines = _lines(group)
    definition = MappedDefinition(
        moze_id=f"record:{primary['identifier']}", kind="recurring", source="single", name=_name(group, data, "單筆"),
        interval_unit="month", interval_n=1, anchor_date=_day(primary), day_of_month=None, times=1,
        total_amount=None, remainder=None, lines=lines, review_reason=None, payload=None,
    )
    definition.instances.append(_instance(group, lines, 1, cutoff))
    return definition


def map_schedules(data: BackupData) -> MapResult:
    cutoff = data.exported_at.date()
    result = MapResult()
    periods = {row["identifier"]: row for row in data.periods}
    installments = {row["identifier"]: row for row in data.installments}
    by_event: dict[str, list[dict]] = defaultdict(list)
    singles: list[dict] = []
    past_singles: list[dict] = []
    for record in data.records:
        enabled_future = _day(record) > cutoff and record["isEnabled"]
        if record["type"] == REWARD_TYPE:
            result.rewards_ignored += enabled_future
            continue
        if record["type"] not in MOZE_LINE_KINDS:
            if enabled_future:
                result.unsupported_types[str(record["type"])] += 1
            continue
        event = record["eventID"]
        if event and (event in periods or event in installments):
            by_event[event].append(record)
            result.records_mapped += enabled_future
            continue
        if not enabled_future:
            if _day(record) <= cutoff:
                past_singles.append(record)  # an ordinary entry, unless HomeHub holds its record:<id> definition
            continue
        if event:
            result.review.append({"moze_id": record["identifier"], "reason": "event_missing"})
        singles.append(record)
        result.records_mapped += 1
    for identifier, period in periods.items():
        mapped = _map_period(period, by_event.get(identifier, []), data, cutoff, result)
        if mapped is not None:
            result.definitions.append(mapped)
    for identifier, installment in installments.items():
        mapped = _map_installment(installment, by_event.get(identifier, []), data, cutoff, result)
        if mapped is not None:
            result.definitions.append(mapped)
    for group in record_groups(singles, data):
        result.definitions.append(_map_single(group, data, cutoff))
    result.past_singles = [_map_single(group, data, cutoff) for group in record_groups(past_singles, data)]
    return result
