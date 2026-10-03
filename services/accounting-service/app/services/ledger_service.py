"""Read-only ledger queries (design D10, D14, D15, D17).

Balances count entries whose posted_date is not after the reference day (default today);
listings use the canonical order (entry_date, entry_time NULLS FIRST, seq) or its exact reverse.
"""

from collections import defaultdict
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session, aliased

from ..models import (
    MOZE_SOURCES,
    Account,
    AccountGroup,
    Category,
    Counterparty,
    EntryGroup,
    EntryRewardRule,
    FxRate,
    LedgerEntry,
    Preference,
    Project,
    RewardRule,
)
from .moze_import_service import import_locked

LEDGER_TZ = ZoneInfo("Asia/Taipei")  # ledger dates are naive Taipei dates (D8)
DEFAULT_MAIN_CURRENCY = "TWD"
AMOUNT_QUANTUM = Decimal("0.0001")
EXPENSE_KINDS = ("expense", "fee")
INCOME_KINDS = ("income", "reward", "interest", "discount")
PERIOD_INCOME_KINDS = ("income", "interest", "discount")  # period_summary reports rewards separately

CANONICAL_ORDER = (LedgerEntry.entry_date, LedgerEntry.entry_time.asc().nulls_first(), LedgerEntry.seq)
NEWEST_FIRST = (LedgerEntry.entry_date.desc(), LedgerEntry.entry_time.desc().nulls_last(), LedgerEntry.seq.desc())

REWARD_RULE_FIELDS = (
    "id", "account_id", "name", "method", "rate", "fixed_amount", "window", "posting", "delay_days",
    "post_month_offset", "post_day", "txn_rounding", "total_rounding", "total_cap", "shared_cap_id", "is_basic",
    "reward_account_id", "reward_project_id", "starts_on", "ends_on", "is_enabled", "description", "sort_order",
    "moze_id",
)
ACCOUNT_FIELDS = (
    "id", "name", "currency", "opening_balance", "group_id", "icon", "color", "is_archived", "include_in_total",
    "is_credit", "closing_day", "due_rule", "due_value", "credit_limit", "combined_account_id", "credit_sharing_id",
    "auto_pay_account_id", "fx_fee_pct", "fx_fee_rounding", "fx_fee_refundable", "sort_order",
    "settings_locally_edited", "moze_id",
)


def _today() -> date:
    """Today in the ledger time zone (Asia/Taipei), not the server's local date."""
    return datetime.now(LEDGER_TZ).date()


def _plain(value: Decimal) -> str:
    """Decimal without trailing zeros or exponent: 3.0000 → '3', 0.5000 → '0.5'."""
    return format(value.normalize(), "f")


def main_currency(db: Session) -> str:
    """The preference's main currency, without creating the preference row."""
    return db.scalar(select(Preference.main_currency).where(Preference.id == 1)) or DEFAULT_MAIN_CURRENCY


def latest_rates(db: Session, bases: Iterable[str], quote: str) -> dict[str, Decimal]:
    """Latest cached rate base→quote for each base (1 for quote itself); falls back to 1 / (quote→base)."""
    bases = set(bases)
    rates: dict[str, Decimal] = {}
    wanted = {base for base in bases if base != quote}
    if quote in bases:
        rates[quote] = Decimal(1)
    if not wanted:
        return rates
    direct = db.execute(
        select(FxRate.base, FxRate.rate)
        .where(FxRate.quote == quote, FxRate.base.in_(wanted))
        .distinct(FxRate.base)
        .order_by(FxRate.base, FxRate.date.desc())
    )
    rates.update({base: rate for base, rate in direct})
    missing = wanted - set(rates)
    if missing:
        inverse = db.execute(
            select(FxRate.quote, FxRate.rate)
            .where(FxRate.base == quote, FxRate.quote.in_(missing))
            .distinct(FxRate.quote)
            .order_by(FxRate.quote, FxRate.date.desc())
        )
        rates.update({base: Decimal(1) / rate for base, rate in inverse})
    return rates


def _convert(amount: Decimal, rate: Decimal | None) -> Decimal | None:
    if rate is None:
        return None
    return (amount * rate).quantize(AMOUNT_QUANTUM, rounding=ROUND_HALF_UP)


def _posted_sum(as_of: date):
    return func.coalesce(func.sum(LedgerEntry.amount).filter(LedgerEntry.posted_date <= as_of), 0)


def account_balance(db: Session, account_id: int, as_of: date | None = None) -> Decimal:
    """opening_balance + Σ amount over entries posted on or before `as_of` (default today)."""
    as_of = as_of or _today()
    balance = db.scalar(
        select(Account.opening_balance + _posted_sum(as_of))
        .outerjoin(LedgerEntry, LedgerEntry.account_id == Account.id)
        .where(Account.id == account_id)
        .group_by(Account.id)
    )
    if balance is None:
        raise LookupError(f"account {account_id} not found")
    return balance


def _rule_summary(rule: RewardRule) -> str:
    if rule.method == "percent" and rule.rate is not None:
        return f"{rule.name} {_plain(rule.rate)}%"
    if rule.method == "fixed" and rule.fixed_amount is not None:
        return f"{rule.name} {_plain(rule.fixed_amount)}"
    return rule.name


def _rules_by_account(db: Session, account_ids: Iterable[int]) -> dict[int, list[RewardRule]]:
    rules: dict[int, list[RewardRule]] = defaultdict(list)
    for rule in db.scalars(
        select(RewardRule)
        .where(RewardRule.account_id.in_(list(account_ids)))
        .order_by(RewardRule.sort_order, RewardRule.id)
    ):
        rules[rule.account_id].append(rule)
    return rules


def rule_out(rule: RewardRule) -> dict:
    return {field: getattr(rule, field) for field in REWARD_RULE_FIELDS}


def _account_rows(
    db: Session, *, include_archived: bool, account_id: int | None = None, as_of: date | None = None
) -> list[dict]:
    """AccountOut dicts. Balances (posted on or before `as_of`, default today), available credit and
    conversions are computed over every account; entry_count counts every entry regardless of `as_of`."""
    as_of = as_of or _today()
    entry_count = func.count(LedgerEntry.id).label("entry_count")
    rows = db.execute(
        select(
            Account,
            AccountGroup.name.label("group_name"),
            (Account.opening_balance + _posted_sum(as_of)).label("balance"),
            entry_count,
        )
        .outerjoin(AccountGroup, AccountGroup.id == Account.group_id)
        .outerjoin(LedgerEntry, LedgerEntry.account_id == Account.id)
        .group_by(Account.id, AccountGroup.id)
        .order_by(
            AccountGroup.sort_order.asc().nulls_last(),
            AccountGroup.id.asc().nulls_last(),
            Account.sort_order,
            Account.name,
        )
    ).all()
    balances = {account.id: balance for account, _, balance, _ in rows}
    shared_balances: dict = defaultdict(Decimal)
    for account, _, balance, _ in rows:
        if account.credit_sharing_id is not None:
            shared_balances[account.credit_sharing_id] += balance

    main = main_currency(db)
    rates = latest_rates(db, {account.currency for account, _, _, _ in rows}, main)
    visible = [
        row for row in rows
        if (include_archived or not row[0].is_archived) and (account_id is None or row[0].id == account_id)
    ]
    rules = _rules_by_account(db, [account.id for account, _, _, _ in visible])

    result = []
    for account, group_name, balance, count in visible:
        available = None
        if account.is_credit and account.credit_limit is not None:
            used = shared_balances[account.credit_sharing_id] if account.credit_sharing_id else balances[account.id]
            available = account.credit_limit + used
        item = {field: getattr(account, field) for field in ACCOUNT_FIELDS}
        item.update(
            {
                "group_name": group_name,
                "balance": balance,
                "balance_main": balance if account.currency == main else _convert(balance, rates.get(account.currency)),
                "entry_count": count,
                "available_credit": available,
                "rule_summaries": [_rule_summary(rule) for rule in rules[account.id] if rule.is_enabled],
                "note": account.note,
                "reward_rules": [rule_out(rule) for rule in rules[account.id]],
            }
        )
        result.append(item)
    return result


def list_accounts(db: Session, *, include_archived: bool = False, as_of: date | None = None) -> list[dict]:
    return _account_rows(db, include_archived=include_archived, as_of=as_of)


def get_account(db: Session, account_id: int, *, as_of: date | None = None) -> dict | None:
    rows = _account_rows(db, include_archived=True, account_id=account_id, as_of=as_of)
    if not rows:
        return None
    account = rows[0]
    sharing_id = account["credit_sharing_id"]
    account["credit_sharing_members"] = (
        []
        if sharing_id is None
        else list(
            db.scalars(
                select(Account.id)
                .where(Account.credit_sharing_id == sharing_id, Account.id != account_id)
                .order_by(Account.id)
            )
        )
    )
    return account


def list_reward_rules(db: Session, account_id: int) -> list[dict]:
    return [rule_out(rule) for rule in _rules_by_account(db, [account_id])[account_id]]


def _running_subquery(account_ids: list[int] | None):
    """running_balance per entry: opening + Σ posted amounts up to and including it, in canonical order."""
    today = _today()
    posted_amount = case((LedgerEntry.posted_date <= today, LedgerEntry.amount), else_=0)
    stmt = (
        select(
            LedgerEntry.id.label("entry_id"),
            (
                Account.opening_balance
                + func.sum(posted_amount).over(
                    partition_by=LedgerEntry.account_id, order_by=CANONICAL_ORDER, rows=(None, 0)
                )
            ).label("running_balance"),
        )
        .join(Account, Account.id == LedgerEntry.account_id)
    )
    if account_ids is not None:
        stmt = stmt.where(LedgerEntry.account_id.in_(account_ids))
    return stmt.subquery()


def _text_filter(q: str):
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
    return or_(
        LedgerEntry.name.ilike(pattern, escape="\\"),
        LedgerEntry.merchant.ilike(pattern, escape="\\"),
        LedgerEntry.description.ilike(pattern, escape="\\"),
    )


def _filters(kind, date_from, date_to, q, account_ids, hide_rewards) -> list:
    filters = []
    if account_ids is not None:
        filters.append(LedgerEntry.account_id.in_(account_ids))
    if kind is not None:
        filters.append(LedgerEntry.kind == kind)
    if date_from is not None:
        filters.append(LedgerEntry.entry_date >= date_from)
    if date_to is not None:
        filters.append(LedgerEntry.entry_date <= date_to)
    if q:
        filters.append(_text_filter(q))
    if hide_rewards:
        filters.append(LedgerEntry.kind != "reward")
    return filters


def is_locked(source: str, moze_id: str | None) -> bool:
    """Imported rows are read-only until cutover (D19)."""
    return not import_locked() and (source in MOZE_SOURCES or moze_id is not None)


def _group_summaries(db: Session, group_ids: set[int]) -> dict[int, dict]:
    if not group_ids:
        return {}
    groups = {group.id: group for group in db.scalars(select(EntryGroup).where(EntryGroup.id.in_(group_ids)))}
    members: dict[int, list[tuple[Decimal, str]]] = defaultdict(list)
    for group_id, amount, currency in db.execute(
        select(LedgerEntry.group_id, LedgerEntry.amount, LedgerEntry.currency)
        .where(LedgerEntry.group_id.in_(group_ids))
        .order_by(LedgerEntry.group_id, *CANONICAL_ORDER)
    ):
        members[group_id].append((amount, currency))
    summaries = {}
    for group_id, group in groups.items():
        rows = members[group_id]
        currency = rows[0][1]
        rates = latest_rates(db, {c for _, c in rows}, currency)
        converted = [_convert(amount, rates.get(c)) if c != currency else amount for amount, c in rows]
        total = None if any(value is None for value in converted) else sum(converted, Decimal(0))
        summaries[group_id] = {
            "id": group.id, "kind": group.kind, "name": group.name, "merchant": group.merchant,
            "description": group.description, "count": len(rows), "total": total, "currency": currency,
        }
    return summaries


def _rule_names(db: Session, entry_ids: list[int]) -> dict[int, list[str]]:
    names: dict[int, list[str]] = defaultdict(list)
    if not entry_ids:
        return names
    for entry_id, name in db.execute(
        select(EntryRewardRule.entry_id, RewardRule.name)
        .join(RewardRule, RewardRule.id == EntryRewardRule.rule_id)
        .where(EntryRewardRule.entry_id.in_(entry_ids))
        .order_by(EntryRewardRule.entry_id, RewardRule.sort_order, RewardRule.id)
    ):
        names[entry_id].append(name)
    return names


def _entry_rows(db: Session, filters: list, *, running_accounts: list[int] | None, limit=None, offset=0) -> list[dict]:
    """EntryOut dicts newest first for the entries matching `filters`."""
    running = _running_subquery(running_accounts)
    parent_category = aliased(Category)
    stmt = (
        select(
            LedgerEntry,
            running.c.running_balance,
            Account.name.label("account_name"),
            Category.name.label("category_name"),
            parent_category.name.label("parent_category_name"),
            func.coalesce(Category.icon, parent_category.icon).label("category_icon"),
            func.coalesce(Category.color, parent_category.color).label("category_color"),
            Project.name.label("project_name"),
            Counterparty.name.label("counterparty_name"),
        )
        .join(running, running.c.entry_id == LedgerEntry.id)
        .join(Account, Account.id == LedgerEntry.account_id)
        .outerjoin(Category, Category.id == LedgerEntry.category_id)
        .outerjoin(parent_category, parent_category.id == Category.parent_id)
        .outerjoin(Project, Project.id == LedgerEntry.project_id)
        .outerjoin(Counterparty, Counterparty.id == LedgerEntry.counterparty_id)
        .where(*filters)
        .order_by(*NEWEST_FIRST)
        .offset(offset)
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    rows = db.execute(stmt).all()
    groups = _group_summaries(db, {row.LedgerEntry.group_id for row in rows if row.LedgerEntry.group_id})
    rule_names = _rule_names(db, [row.LedgerEntry.id for row in rows])

    page = []
    for row in rows:
        entry = row.LedgerEntry
        if row.category_name is None:
            category = None
        elif row.parent_category_name is None:
            category = row.category_name
        else:
            category = f"{row.parent_category_name}/{row.category_name}"
        page.append(
            {
                "id": entry.id,
                "kind": entry.kind,
                "amount": entry.amount,
                "currency": entry.currency,
                "original_amount": entry.original_amount,
                "original_currency": entry.original_currency,
                "fx_rate": entry.fx_rate,
                "fx_source": entry.fx_source,
                "entry_date": entry.entry_date,
                "entry_time": entry.entry_time,
                "posted_date": entry.posted_date,
                "account_id": entry.account_id,
                "account_name": row.account_name,
                "category_id": entry.category_id,
                "category": category,
                "category_icon": row.category_icon,
                "category_color": row.category_color,
                "project_id": entry.project_id,
                "project": row.project_name,
                "name": entry.name,
                "merchant": entry.merchant,
                "counterparty_id": entry.counterparty_id,
                "counterparty": row.counterparty_name,
                "description": entry.description,
                "tags": entry.tags,
                "parent_entry_id": entry.parent_entry_id,
                "transfer_group_id": entry.transfer_group_id,
                "is_settlement": entry.is_settlement,
                "group": groups.get(entry.group_id),
                "rule_names": rule_names[entry.id],
                "invoice_number": entry.invoice_number,
                "invoice_random": entry.invoice_random,
                "needs_review": entry.needs_review,
                "source": entry.source,
                "moze_id": entry.moze_id,
                "locked": is_locked(entry.source, entry.moze_id),
                "running_balance": row.running_balance,
            }
        )
    return page


def list_entries(
    db: Session,
    account_id: int,
    *,
    limit: int,
    offset: int,
    kind: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    q: str | None = None,
) -> tuple[int, list[dict]]:
    """Return (total, page) newest first; running_balance is computed over all entries before filtering."""
    return list_all_entries(
        db, limit=limit, offset=offset, kind=kind, date_from=date_from, date_to=date_to, q=q, account_ids=[account_id]
    )


def list_all_entries(
    db: Session,
    *,
    limit: int,
    offset: int,
    kind: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    q: str | None = None,
    account_ids: list[int] | None = None,
    hide_rewards: bool = False,
) -> tuple[int, list[dict]]:
    filters = _filters(kind, date_from, date_to, q, account_ids, hide_rewards)
    total = db.scalar(select(func.count()).select_from(LedgerEntry).where(*filters))
    return total, _entry_rows(db, filters, running_accounts=account_ids, limit=limit, offset=offset)


def _spend_filter():
    """Rows that make up spending: negative expense / fee amounts and every refund (refunds are positive and
    reduce the spending of the period they are dated / posted in)."""
    return or_(
        and_(LedgerEntry.kind.in_(EXPENSE_KINDS), LedgerEntry.amount < 0),
        LedgerEntry.kind == "refund",
    )


def _month_bounds(month: str) -> tuple[date, date]:
    """[first day, first day of the next month) for a YYYY-MM month."""
    year, month_no = (int(part) for part in month.split("-"))
    return date(year, month_no, 1), date(year + (month_no == 12), month_no % 12 + 1, 1)


def _converted_totals(db: Session, month: str, *, by_day: bool, hide_rewards: bool) -> tuple[str, dict, list[str]]:
    """The month/calendar summary rules in one place: expense (Σ negative expense/fee amounts + Σ refunds), income
    (Σ positive income/reward/interest/discount amounts, rewards dropped when `hide_rewards`) and the count of those
    rows, for entries dated in `month`, converted to the main currency with the latest cached rate. Transfers,
    receivables/payables (settlements included) and balance adjustments are excluded. Returns (main currency,
    totals keyed by entry_date — or by None when not `by_day` — and the sorted currencies that have rows in the month
    but no rate; those rows are left out of the totals)."""
    start, end = _month_bounds(month)
    income_kinds = tuple(kind for kind in INCOME_KINDS if not (hide_rewards and kind == "reward"))
    spend_rows = _spend_filter()
    income_rows = and_(LedgerEntry.kind.in_(income_kinds), LedgerEntry.amount > 0)
    keys = (LedgerEntry.entry_date,) if by_day else ()
    rows = db.execute(
        select(
            *keys,
            LedgerEntry.currency,
            func.coalesce(func.sum(LedgerEntry.amount).filter(spend_rows), 0),
            func.coalesce(func.sum(LedgerEntry.amount).filter(income_rows), 0),
            func.count(LedgerEntry.id).filter(or_(spend_rows, income_rows)),
        )
        .where(LedgerEntry.entry_date >= start, LedgerEntry.entry_date < end)
        .group_by(*keys, LedgerEntry.currency)
    ).all()

    main = main_currency(db)
    rates = latest_rates(db, {row[-4] for row in rows}, main)
    totals: dict = defaultdict(lambda: {"expense": Decimal(0), "income": Decimal(0), "count": 0})
    missing = set()
    for row in rows:
        key = row[0] if by_day else None
        currency, expense, income, count = row[-4:]
        rate = rates.get(currency)
        if rate is None:
            missing.add(currency)
            continue
        if count:
            bucket = totals[key]
            bucket["expense"] += _convert(expense, rate)
            bucket["income"] += _convert(income, rate)
            bucket["count"] += count
    return main, totals, sorted(missing)


def month_summary(db: Session, month: str) -> dict:
    """Expense (refunds reduce it, as in MOZE's statistics), income and net (income + expense) for entries dated
    in `month` (YYYY-MM) in the main currency; see `_converted_totals` for the inclusion rules."""
    main, totals, missing = _converted_totals(db, month, by_day=False, hide_rewards=False)
    month_totals = totals.get(None, {"expense": Decimal(0), "income": Decimal(0)})
    return {
        "month": month,
        "currency": main,
        "expense": month_totals["expense"],
        "income": month_totals["income"],
        "net": month_totals["income"] + month_totals["expense"],
        "missing_rates": missing,
    }


def daily_summary(db: Session, month: str) -> dict:
    """Per-day expense, income and count for the calendar view: the month summary's rules bucketed by entry_date,
    rewards left out when the preference hides them on the timeline. Days without counted rows are omitted."""
    hide_rewards = bool(db.scalar(select(Preference.hide_rewards_on_timeline).where(Preference.id == 1)))
    main, totals, missing = _converted_totals(db, month, by_day=True, hide_rewards=hide_rewards)
    return {
        "month": month,
        "currency": main,
        "days": [{"date": day, **totals[day]} for day in sorted(totals)],
        "missing_rates": missing,
    }


def period_summary(db: Session, account_id: int, date_from: date, date_to: date) -> dict:
    """Spend (refunds included, as in month_summary), income, rewards, net and count for one account's entries
    posted within [date_from, date_to],
    plus the balance as of date_to. Computed over the whole period, never over a listing page."""
    account = db.get(Account, account_id)
    if account is None:
        raise LookupError(f"account {account_id} not found")
    if date_from > date_to:
        raise ValueError("date_from is after date_to")
    amount = LedgerEntry.amount
    spend, income, rewards, net, count = db.execute(
        select(
            func.coalesce(func.sum(amount).filter(_spend_filter()), 0),
            func.coalesce(func.sum(amount).filter(LedgerEntry.kind.in_(PERIOD_INCOME_KINDS), amount > 0), 0),
            func.coalesce(func.sum(amount).filter(LedgerEntry.kind == "reward"), 0),
            func.coalesce(func.sum(amount), 0),
            func.count(LedgerEntry.id),
        ).where(
            LedgerEntry.account_id == account_id,
            LedgerEntry.posted_date >= date_from,
            LedgerEntry.posted_date <= date_to,
        )
    ).one()
    return {
        "account_id": account.id,
        "currency": account.currency,
        "date_from": date_from,
        "date_to": date_to,
        "spend": spend,
        "income": income,
        "rewards": rewards,
        "net": net,
        "end_balance": account_balance(db, account_id, as_of=date_to),
        "count": count,
    }


def _open_amount(db: Session, entry: LedgerEntry) -> Decimal:
    settled = db.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(LedgerEntry.settles_entry_id == entry.id)
    )
    return abs(entry.amount + settled)


def get_entry_detail(db: Session, entry_id: int) -> dict | None:
    entry = db.get(LedgerEntry, entry_id)
    if entry is None:
        return None
    [detail] = _entry_rows(db, [LedgerEntry.id == entry_id], running_accounts=[entry.account_id])

    def rows(*filters) -> list[dict]:
        return list(reversed(_entry_rows(db, list(filters), running_accounts=None)))  # canonical order

    def one(*filters) -> dict | None:
        found = rows(*filters)
        return found[0] if found else None

    detail["children"] = rows(LedgerEntry.parent_entry_id == entry.id)
    detail["group_members"] = rows(LedgerEntry.group_id == entry.group_id) if entry.group_id else []
    detail["transfer_counterpart"] = (
        one(LedgerEntry.transfer_group_id == entry.transfer_group_id, LedgerEntry.id != entry.id)
        if entry.transfer_group_id
        else None
    )
    detail["settles"] = one(LedgerEntry.id == entry.settles_entry_id) if entry.settles_entry_id else None
    # Only linked settlements; an unlinked one (is_settlement without settles_entry_id) belongs to no entry.
    detail["settled_by"] = rows(LedgerEntry.settles_entry_id == entry.id)
    detail["refunds"] = one(LedgerEntry.id == entry.refunds_entry_id) if entry.refunds_entry_id else None
    detail["refunded_by"] = rows(LedgerEntry.refunds_entry_id == entry.id)
    detail["rules"] = [
        rule_out(rule)
        for rule in db.scalars(
            select(RewardRule)
            .join(EntryRewardRule, EntryRewardRule.rule_id == RewardRule.id)
            .where(EntryRewardRule.entry_id == entry.id)
            .order_by(RewardRule.sort_order, RewardRule.id)
        )
    ]
    detail["rewards"] = rows(LedgerEntry.reward_source_entry_id == entry.id)
    if entry.kind in ("receivable", "payable") and not entry.is_settlement and entry.settles_entry_id is None:
        open_amount = _open_amount(db, entry)
        detail["open_amount"], detail["is_settled"] = open_amount, open_amount == 0
    else:
        detail["open_amount"], detail["is_settled"] = None, None
    detail["refunded_amount"] = sum((item["amount"] for item in detail["refunded_by"]), Decimal(0))
    return detail
