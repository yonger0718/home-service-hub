"""GET /reports/* aggregation (design D1) on top of the shared inclusion rules in `report_rules`.

Every figure is an SQL sum grouped by (dimension keys…, currency), converted to the main currency per group with the
latest cached rates (`ledger_service.latest_rates`); currencies without a rate are left out and listed in
`missing_rates`. Three queries, never one: totals by currency only (so `totals.expense == −month_summary.expense`
exactly), rows by (group key, currency), splits by (group key, split key, currency). `unnest(tags)` appears only in a
query whose keys include `tag`.
"""

from dataclasses import replace
from statistics import median
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import Date, DateTime, Integer, Text, and_, cast, extract, func, literal, select, true, union_all
from sqlalchemy.orm import Session, aliased

from ..models import Account, Category, Counterparty, LedgerEntry, Project
from . import ledger_service
from .report_rules import (
    AMOUNT_QUANTUM,
    EXPENSE_KINDS,
    Compare,
    Dimension,
    Granularity,
    Metric,
    ReportFilters,
    compare_window,
    convert,
    counted_filter,
    entry_filters,
    income_filter,
    spend_filter,
    week_start,
)

MAX_ROWS = 200
MAX_STAT_GROUPS = 50
OTHER_KEY = "__other__"
TIME_DIMENSIONS = frozenset({"month", "week", "day", "weekday"})
_ROW_FILTERS = {"expense": spend_filter, "income": income_filter, "net": counted_filter, "count": counted_filter}


def _pure_spend_filter():
    """The spend rows without refunds: the rows `metric=expense` statistics are computed over."""
    return and_(LedgerEntry.kind.in_(EXPENSE_KINDS), LedgerEntry.amount < 0)


# Row statistics: expense over pure spend rows (a refund is no purchase), income over income rows, net signed over
# every counted row.
_STAT_FILTERS = {"expense": _pure_spend_filter, "income": income_filter, "net": counted_filter}

# A converted group: (spend ≤ 0 incl. refunds, refund ≥ 0, income ≥ 0, count), all in the main currency.
Sums = tuple[Decimal, Decimal, Decimal, int]
ZERO_SUMS: Sums = (Decimal(0), Decimal(0), Decimal(0), 0)


def _key_column(dimension: Dimension, root_parent, tag_column):
    """The SQL grouping expression of a dimension. `category` is the root (the parent when there is one, else the
    category itself — `_category_expense`'s outer-join trick); `tag_column` is the unnested tag or None."""
    entry = LedgerEntry
    if dimension == "category":
        return func.coalesce(root_parent.id, Category.id)
    if dimension == "tag":
        return tag_column
    if dimension == "month":
        return func.to_char(entry.entry_date, "YYYY-MM")
    if dimension == "week":
        return cast(func.date_trunc("week", cast(entry.entry_date, DateTime)), Date)
    if dimension == "weekday":
        return cast(extract("isodow", entry.entry_date), Integer)
    return {
        "subcategory": entry.category_id, "account": entry.account_id, "project": entry.project_id,
        "counterparty": entry.counterparty_id, "merchant": entry.merchant, "day": entry.entry_date,
    }[dimension]


def _grouped_select(where: list, dimensions: tuple[Dimension, ...], *, untagged: bool = False,
                    per_row: bool = False):
    """One grouped select of (k0…, currency, spend, refund, income, count). With a `tag` dimension it unnests the
    tags, or — `untagged` — takes the rows without tags (`cardinality(tags) = 0`) with a NULL tag key. `per_row`
    selects (k0…, currency, amount, entry_date) of every row instead of the grouped sums (row statistics)."""
    parent = aliased(Category)
    tags = None
    if "tag" in dimensions and not untagged:
        tags = func.unnest(LedgerEntry.tags).table_valued("tag").render_derived(name="entry_tag")
    keys, group_keys = [], []
    for index, dimension in enumerate(dimensions):
        if dimension == "tag" and untagged:
            keys.append(literal(None, Text).label(f"k{index}"))
            continue
        column = _key_column(dimension, parent, tags.c.tag if tags is not None else None)
        keys.append(column.label(f"k{index}"))
        group_keys.append(column)
    if per_row:
        stmt = select(*keys, LedgerEntry.currency, LedgerEntry.amount, LedgerEntry.entry_date)
    else:
        stmt = select(
            *keys,
            LedgerEntry.currency,
            func.coalesce(func.sum(LedgerEntry.amount).filter(spend_filter()), 0),
            func.coalesce(func.sum(LedgerEntry.amount).filter(LedgerEntry.kind == "refund"), 0),
            func.coalesce(func.sum(LedgerEntry.amount).filter(income_filter()), 0),
            func.count(LedgerEntry.id),
        )
    stmt = stmt.select_from(LedgerEntry)
    if "category" in dimensions:
        stmt = stmt.outerjoin(Category, Category.id == LedgerEntry.category_id).outerjoin(
            parent, parent.id == Category.parent_id
        )
    if tags is not None:
        stmt = stmt.join(tags, true())
    if untagged:
        where = [*where, func.cardinality(LedgerEntry.tags) == 0]
    stmt = stmt.where(*where)
    return stmt if per_row else stmt.group_by(*group_keys, LedgerEntry.currency)


def _key_text(value) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, date) else str(value)


def _group_rows(db: Session, where: list, dimensions: tuple[Dimension, ...]) -> list[tuple]:
    """Raw (key texts, currency, spend, refund, income, count) per group and currency."""
    stmt = _grouped_select(where, dimensions)
    if "tag" in dimensions:
        stmt = union_all(stmt, _grouped_select(where, dimensions, untagged=True))
    width = len(dimensions)
    return [
        (tuple(_key_text(value) for value in row[:width]), *row[width:])
        for row in db.execute(stmt).all()
    ]


def _convert_groups(raw: list[tuple], rates: dict[str, Decimal]) -> dict[tuple, Sums]:
    """Σ of each group's per-currency sums, each converted on its own; unrated currencies left out."""
    groups: dict[tuple, Sums] = {}
    for keys, currency, spend, refund, income, count in raw:
        rate = rates.get(currency)
        if rate is None:
            continue
        old = groups.get(keys, ZERO_SUMS)
        groups[keys] = (
            old[0] + convert(spend, rate), old[1] + convert(refund, rate), old[2] + convert(income, rate),
            old[3] + count,
        )
    return groups


def _amount(sums: Sums, metric: Metric) -> Decimal:
    spend, _, income, count = sums
    if metric == "expense":
        return Decimal(0) - spend  # positive spending; a refund-only group stays negative (never −0)
    if metric == "income":
        return income
    if metric == "net":
        return income + spend
    return Decimal(count)


def _avg(amount: Decimal, count: int, metric: Metric) -> Decimal:
    if metric == "count":
        return Decimal(1)
    return (amount / count).quantize(AMOUNT_QUANTUM, rounding=ROUND_HALF_UP)


def row_statistics(amounts_dates: list[tuple[Decimal, date]]) -> dict:
    """Median / min / max / first / last date / average days between entries of one group's converted magnitudes.
    `avg_interval_days = (last − first).days / (n − 1)` over n entries (0.0 for one), 1 dp; all None when empty."""
    if not amounts_dates:
        return {"median": None, "min": None, "max": None, "first_date": None, "last_date": None,
                "avg_interval_days": None}
    amounts = [amount for amount, _ in amounts_dates]
    dates = [entry_date for _, entry_date in amounts_dates]
    first, last = min(dates), max(dates)
    count = len(amounts_dates)
    return {
        "median": Decimal(median(amounts)).quantize(AMOUNT_QUANTUM, rounding=ROUND_HALF_UP),
        "min": min(amounts).quantize(AMOUNT_QUANTUM), "max": max(amounts).quantize(AMOUNT_QUANTUM),
        "first_date": first, "last_date": last,
        "avg_interval_days": 0.0 if count == 1 else round((last - first).days / (count - 1), 1),
    }


def _stat_rows(db: Session, where: list, dimension: Dimension) -> list[tuple]:
    """(key text, currency, signed amount, entry_date) per row `where` selects, built on the same select as the groups;
    `tag` unnests, the empty-tags rows form the null-key bucket."""
    stmt = _grouped_select(where, (dimension,), per_row=True)
    if dimension == "tag":
        stmt = union_all(stmt, _grouped_select(where, (dimension,), untagged=True, per_row=True))
    return [(_key_text(row[0]), *row[1:]) for row in db.execute(stmt).all()]


def _statistics(db: Session, where: list, dimension: Dimension, metric: Metric,
                rates: dict[str, Decimal]) -> dict[str | None, dict]:
    buckets: dict[str | None, list[tuple[Decimal, date]]] = {}
    for key, currency, amount, entry_date in _stat_rows(db, where, dimension):
        rate = rates.get(currency)
        if rate is None:
            continue
        value = convert(amount, rate)
        # expense rows (pure spend, no refunds) are positive spending; income and net keep the ledger sign
        buckets.setdefault(key, []).append((Decimal(0) - value if metric == "expense" else value, entry_date))
    return {key: row_statistics(items) for key, items in buckets.items()}


def _totals(raw: list[tuple], rates: dict[str, Decimal]) -> dict:
    spend, refund, income, count = _convert_groups(raw, rates).get((), ZERO_SUMS)
    expense = Decimal(0) - spend
    return {"expense": expense, "income": income, "net": income - expense, "count": count, "refund": refund}


def _sort(items: list[dict], dimension: Dimension) -> list[dict]:
    """Time dimensions by key ascending; others by amount descending, then key (null last)."""
    if dimension in TIME_DIMENSIONS:
        return sorted(items, key=lambda item: item["key"])
    return sorted(items, key=lambda item: (-item["amount"], item["key"] is None, item["key"] or ""))


def _resolve_labels(db: Session, dimension: Dimension, keys: set[str | None]) -> dict[str | None, tuple[str, str | None]]:
    """(label, colour) per key, one query per id dimension; a null key is labelled "" (the client names it)."""
    labels: dict[str | None, tuple[str, str | None]] = {None: ("", None)}
    present = {key for key in keys if key is not None}
    models = {"category": Category, "subcategory": Category, "account": Account, "project": Project,
              "counterparty": Counterparty}
    if dimension not in models:
        labels.update({key: (key, None) for key in present})
        return labels
    model = models[dimension]
    ids = [int(key) for key in present if key != OTHER_KEY]
    coloured = hasattr(model, "color")
    columns = (model.id, model.name, model.color) if coloured else (model.id, model.name)
    for row in db.execute(select(*columns).where(model.id.in_(ids))) if ids else []:
        labels[str(row[0])] = (row[1], row[2] if coloured else None)
    return labels


def _fold(rows: list[dict], metric: Metric) -> list[dict]:
    """At most MAX_ROWS rows; the remainder becomes one `__other__` row, always last."""
    if len(rows) <= MAX_ROWS:
        return rows
    kept, rest = rows[:MAX_ROWS], rows[MAX_ROWS:]
    amount = sum((row["amount"] for row in rest), Decimal(0))
    count = sum(row["count"] for row in rest)
    other = {
        "key": OTHER_KEY, "label": OTHER_KEY, "color": None, "amount": amount,
        "refund": sum((row["refund"] for row in rest), Decimal(0)), "count": count, "avg": _avg(amount, count, metric),
    }
    compared = [row["compare_amount"] for row in rest if row.get("compare_amount") is not None]
    if compared:
        other["compare_amount"] = sum(compared, Decimal(0))
    if rest[0].get("splits") is not None:
        merged: dict[str | None, dict] = {}
        for row in rest:
            for split in row["splits"]:
                item = merged.setdefault(split["key"], {**split, "amount": Decimal(0), "count": 0})
                item["amount"] += split["amount"]
                item["count"] += split["count"]
        other["splits"] = list(merged.values())
    return [*kept, other]


def _compare(db: Session, f: ReportFilters, compare: Compare, metric: Metric, group_by: Dimension,
             rates_for) -> tuple[dict, dict[str | None, Decimal]]:
    """The comparison window: its five totals and each group key's amount there."""
    date_from, date_to = compare_window(f.date_from, f.date_to, compare)
    where = entry_filters(db, replace(f, date_from=date_from, date_to=date_to))
    totals_raw = _group_rows(db, [*where, counted_filter()], ())
    rows_raw = _group_rows(db, [*where, _ROW_FILTERS[metric]()], (group_by,))
    rates = rates_for(totals_raw + rows_raw)
    amounts = {keys[0]: _amount(sums, metric) for keys, sums in _convert_groups(rows_raw, rates).items()}
    return {"date_from": date_from, "date_to": date_to, "totals": _totals(totals_raw, rates)}, amounts


def breakdown(db: Session, f: ReportFilters, *, metric: Metric, group_by: Dimension, split_by: Dimension | None,
              compare: Compare | None) -> dict:
    """Totals, rows per `group_by` key (splits per `split_by` key) and the optional comparison, shaped as
    `BreakdownOut`; row statistics come from one extra query when there are ≤ 50 groups and metric != count."""
    main = ledger_service.main_currency(db)
    rate_cache: dict[str, Decimal | None] = {}

    def rates_for(raw: list[tuple]) -> dict[str, Decimal]:
        wanted = {row[1] for row in raw} - set(rate_cache)
        if wanted:
            found = ledger_service.latest_rates(db, wanted, main)
            rate_cache.update({currency: found.get(currency) for currency in wanted})
        return {currency: rate for currency, rate in rate_cache.items() if rate is not None}

    where = entry_filters(db, f)
    row_filter = _ROW_FILTERS[metric]()
    totals_raw = _group_rows(db, [*where, counted_filter()], ())
    rows_raw = _group_rows(db, [*where, row_filter], (group_by,))
    splits_raw = _group_rows(db, [*where, row_filter], (group_by, split_by)) if split_by else []
    current = totals_raw + rows_raw + splits_raw
    rates = rates_for(current)
    missing = sorted({row[1] for row in current} - set(rates))

    rows = []
    for (key,), sums in _convert_groups(rows_raw, rates).items():
        amount, count = _amount(sums, metric), sums[3]
        rows.append({
            "key": key, "amount": amount, "refund": sums[1] if metric == "expense" else Decimal(0), "count": count,
            "avg": _avg(amount, count, metric),
        })

    if split_by:
        splits: dict[str | None, list[dict]] = {}
        for (key, split_key), sums in _convert_groups(splits_raw, rates).items():
            splits.setdefault(key, []).append({"key": split_key, "amount": _amount(sums, metric), "count": sums[3]})
        for row in rows:
            row["splits"] = splits.get(row["key"], [])

    compare_out = None
    if compare:
        compare_out, compare_amounts = _compare(db, f, compare, metric, group_by, rates_for)
        for row in rows:
            row["compare_amount"] = compare_amounts.get(row["key"])

    if metric != "count" and len(rows) <= MAX_STAT_GROUPS:
        stats = _statistics(db, [*where, _STAT_FILTERS[metric]()], group_by, metric, rates)
        for row in rows:
            row.update(stats.get(row["key"]) or row_statistics([]))

    rows = _fold(_sort(rows, group_by), metric)
    labels = _resolve_labels(db, group_by, {row["key"] for row in rows})
    for row in rows:
        if row["key"] != OTHER_KEY:
            row["label"], row["color"] = labels[row["key"]]
    if split_by:
        split_labels = _resolve_labels(db, split_by, {s["key"] for row in rows for s in row["splits"]})
        for row in rows:
            row["splits"] = _sort(row["splits"], split_by)
            for split in row["splits"]:
                split["label"] = split_labels[split["key"]][0]

    return {
        "currency": main, "date_from": f.date_from, "date_to": f.date_to,
        "totals": _totals(totals_raw, rates), "compare": compare_out, "rows": rows,
        "overlapping": group_by == "tag" or split_by == "tag", "missing_rates": missing,
    }


TREND_ALL = "all"


def _bucket_starts(granularity: Granularity, date_from: date, date_to: date) -> list[date]:
    """Every bucket start from the bucket holding `date_from` through `date_to`."""
    if granularity == "month":
        current, step = date_from.replace(day=1), None
    else:
        current = week_start(date_from) if granularity == "week" else date_from
        step = timedelta(days=7 if granularity == "week" else 1)
    starts = []
    while current <= date_to:
        starts.append(current)
        current = current + step if step else (current.replace(day=28) + timedelta(days=4)).replace(day=1)
    return starts


def top_level_category_ids(db: Session, ids: list[int]) -> bool:
    """True when every id is an existing category without a parent."""
    found = set(db.scalars(select(Category.id).where(Category.id.in_(ids), Category.parent_id.is_(None))))
    return found == set(ids)


def trend(db: Session, f: ReportFilters, *, metric: Metric, granularity: Granularity,
          series: list[int] | None) -> dict:
    """One value per (bucket, series): a grouped query by (bucket, root category when `series`, currency), converted
    per group; every bucket from `date_from`'s through `date_to` is present, empty ones with 0."""
    main = ledger_service.main_currency(db)
    dimensions: tuple[Dimension, ...] = (granularity, "category") if series else (granularity,)
    raw = _group_rows(db, [*entry_filters(db, f), _ROW_FILTERS[metric]()], dimensions)
    rates = {c: r for c, r in ledger_service.latest_rates(db, {row[1] for row in raw}, main).items() if r is not None}
    keys = [str(i) for i in dict.fromkeys(series)] if series else [TREND_ALL]
    starts = _bucket_starts(granularity, f.date_from, f.date_to)
    values = {start: {key: Decimal(0) for key in keys} for start in starts}
    for key_texts, sums in _convert_groups(raw, rates).items():
        bucket_text = key_texts[0]
        start = date.fromisoformat(f"{bucket_text}-01" if granularity == "month" else bucket_text)
        series_key = key_texts[1] if series else TREND_ALL
        if start in values and series_key in values[start]:
            values[start][series_key] = _amount(sums, metric)
    average = {
        key: (sum((values[start][key] for start in starts), Decimal(0)) / len(starts)).quantize(
            AMOUNT_QUANTUM, rounding=ROUND_HALF_UP)
        for key in keys
    }
    return {
        "currency": main, "granularity": granularity,
        "buckets": [{"start": start, "values": values[start]} for start in starts], "average": average,
    }
