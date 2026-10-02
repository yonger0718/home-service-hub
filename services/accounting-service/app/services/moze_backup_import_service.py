"""MOZE backup import: settings upsert, entries and links, full replace (design D12-D19).

CLI: python -m app.services.moze_backup_import_service <zip> [--dry-run] [--rename OLD=NEW ...]
     [--allow-fx-outliers] [--no-strict] [--keep-json PATH]
"""

import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Mapping

from sqlalchemy import exists, or_, select, text
from sqlalchemy.orm import Session

from ..models import (
    Account,
    AccountGroup,
    Category,
    Counterparty,
    EntryGroup,
    EntryRewardRule,
    LedgerEntry,
    Preference,
    Project,
    RewardRule,
)
from .moze_backup_json import (
    ARCHIVE_GROUP,
    CATEGORY_NAMES,
    CATEGORY_TYPE_TO_KIND,
    COLOR_MAP,
    DUE_RULE_MAP,
    GROUP_NAMES,
    ICON_BY_GROUP,
    ICON_BY_IMAGE,
    KEYPAD_MAP,
    POSTING_MAP,
    PROJECT_NAMES,
    REWARD_METHOD_MAP,
    REWARD_WINDOW_MAP,
    ROUNDING_MAP,
    SKIPPED_PROJECT,
    BackupData,
    record_kind,
    split_tags,
    tag_delimiter,
)
from .moze_csv import MozeImportError
from .moze_import_service import (
    SYSTEM_CATEGORY_NAMES,
    _apply_renames,
    assert_currency_change_allowed,
    delete_moze_entries,
)

SOURCE = "moze_backup"
SYSTEM_ACCOUNT_TYPES = (3, 4)  # MOZE's 應收應付款項 and 分期帳款
AMOUNT_QUANTUM = Decimal("0.0001")
MOZE_UUID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://homehub.local/moze")
ACCOUNT_SETTINGS = (
    "group_id", "note", "sort_order", "include_in_total", "is_credit", "closing_day", "due_rule", "due_value",
    "credit_limit", "combined_account_id", "credit_sharing_id", "auto_pay_account_id", "fx_fee_pct",
    "fx_fee_rounding", "fx_fee_refundable",
)


def moze_uuid(value: str | None) -> uuid.UUID | None:
    """Deterministic UUID for a MOZE sharing id, so re-imports produce the same value."""
    return uuid.uuid5(MOZE_UUID_NAMESPACE, value) if value else None


def _amount(value: Decimal) -> Decimal:
    return Decimal(value).quantize(AMOUNT_QUANTUM, rounding=ROUND_HALF_UP)


def _text(value: str | None) -> str | None:
    return value if value and value.strip() else None


def _color(value: str) -> str | None:
    digits = value.strip().lstrip("#")
    if len(digits) in (6, 8) and all(c in "0123456789abcdefABCDEF" for c in digits):
        return "#" + digits.lower()
    return None


def _mapped(table: Mapping[int, str], value: int, what: str, where: str) -> str:
    if value not in table:
        raise MozeImportError(f"{where}: unsupported {what} {value}")
    return table[value]


@dataclass
class SettingsResult:
    """MOZE identifier -> HomeHub row maps, plus the report lists the settings step produces."""

    main_currency: str
    groups: dict[str, int] = field(default_factory=dict)
    accounts: dict[str, Account] = field(default_factory=dict)
    categories: dict[str, int] = field(default_factory=dict)  # AHCategory and AHClassification ids
    system_categories: dict[str, int] = field(default_factory=dict)  # kind -> category id
    projects: dict[str, int] = field(default_factory=dict)
    counterparties: dict[str, int] = field(default_factory=dict)
    rules: dict[str, int] = field(default_factory=dict)
    accounts_created: list[str] = field(default_factory=list)
    accounts_renamed: list[dict] = field(default_factory=list)
    settings_skipped: list[dict] = field(default_factory=list)
    unsupported_rules: list[dict] = field(default_factory=list)
    orphaned_rules: list[dict] = field(default_factory=list)
    kept_moze_ids: dict[str, set[str]] = field(default_factory=dict)  # table -> moze ids present in the backup


def _upsert_named(session: Session, model, moze_id: str, *key) -> tuple[object, bool]:
    """Find a row by moze_id, else by `key` (a WHERE clause); create it when neither matches.

    Returns (row, owned): owned is False when `key` matched a row that another MOZE id already claimed.
    """
    row = session.scalar(select(model).where(model.moze_id == moze_id))
    if row is None:
        row = session.scalar(select(model).where(*key))
        if row is not None and row.moze_id not in (None, moze_id):
            return row, False
    if row is None:
        row = model(moze_id=moze_id)
        session.add(row)
    row.moze_id = moze_id
    return row, True


def _find_account(session: Session, record: dict) -> Account | None:
    """Match a backup account by moze_id, then by name among accounts without a moze_id (CSV or manual)."""
    account = session.scalar(select(Account).where(Account.moze_id == record["identifier"]))
    if account is None:
        account = session.scalar(
            select(Account).where(Account.name == record["name"], Account.moze_id.is_(None))
        )
    return account


def _plan_names(records: list[dict]) -> dict[str, str]:
    """Backup identifier -> final account name; MOZE allows two accounts with one name, the second gets
    ` (<first 8 chars of its identifier>)` appended. Two accounts resolving to one final name fail."""
    final: dict[str, str] = {}
    owner: dict[str, str] = {}
    for record in records:
        name = record["name"]
        if name in owner:
            name = f"{name} ({record['identifier'][:8]})"
        if name in owner:
            raise MozeImportError(
                f"accounts {owner[name]} and {record['identifier']} both resolve to the name '{name}'"
            )
        owner[name] = record["identifier"]
        final[record["identifier"]] = name
    return final


def _plan_accounts(
    session: Session, data: BackupData, result: SettingsResult
) -> tuple[dict[str, str], dict[str, Account | None]]:
    """Resolve every final name and every match before writing, and clear names the import needs.

    Matched accounts whose name changes first get a temporary unique name (so swaps work); a row that
    holds a needed name but whose MOZE id is gone from the backup is renamed `<name> (舊 <id>)` and archived.
    """
    final = _plan_names(data.accounts)
    matches: dict[str, Account | None] = {}
    for record in data.accounts:
        account = _find_account(session, record)
        matches[record["identifier"]] = None if account is not None and account in matches.values() else account
    matched = {id(account) for account in matches.values() if account is not None}

    for identifier, name in final.items():
        holder = session.scalar(select(Account).where(Account.name == name))
        if holder is None or holder is matches[identifier] or id(holder) in matched:
            continue
        if holder.moze_id is None:
            raise MozeImportError(
                f"account '{name}' already exists and is not in the MOZE backup under that name; "
                "pass --rename to resolve it"
            )
        stale_name = f"{name} (舊 {holder.moze_id[:8]})"
        result.accounts_renamed.append({"from": holder.name, "to": stale_name})
        holder.name, holder.is_archived = stale_name, True
    for identifier, account in matches.items():
        if account is not None and account.name != final[identifier]:
            result.accounts_renamed.append({"from": account.name, "to": final[identifier]})
            account.name = f"__tmp_{identifier}"
    session.flush()
    return final, matches


def _upsert_groups(session: Session, data: BackupData, result: SettingsResult) -> None:
    for group in data.groups:
        if group["name"] == ARCHIVE_GROUP:
            continue
        name = GROUP_NAMES.get(group["name"], group["name"])
        row, owned = _upsert_named(session, AccountGroup, group["identifier"], AccountGroup.name == name)
        if owned:
            row.name = name
            row.sort_order = group["sequence"]
        session.flush()
        result.groups[group["identifier"]] = row.id
    result.kept_moze_ids["account_group"] = set(result.groups)


def _account_settings(account: dict, result: SettingsResult) -> dict:
    """Backup values of the settings columns, except the two account links (second pass)."""
    where = f"AHAccount '{account['name']}'"
    credit = account["isCreditAccount"]
    system = account["type"] in SYSTEM_ACCOUNT_TYPES
    closing_day = account["startDay"] if 1 <= account["startDay"] <= 31 else None
    if not credit and closing_day == 1:
        closing_day = None
    fee_enabled = account["isCurrencyFeeEnabled"]
    return {
        "group_id": result.groups.get(account["group"]),
        "note": _text(account["desc"]),
        "sort_order": account["sequence"],
        "include_in_total": account["isBalanceIncluded"] and not system,
        "is_credit": credit,
        "closing_day": closing_day,
        "due_rule": _mapped(DUE_RULE_MAP, account["paymentDeadlineType"], "paymentDeadlineType", where) if credit else None,
        "due_value": account["paymentDeadline"] if credit else None,
        "credit_limit": _amount(account["creditLimit"]) if credit and account["creditLimit"] else None,
        "credit_sharing_id": moze_uuid(account["creditSharingID"]) if credit else None,
        "fx_fee_pct": account["feePercentage"].quantize(Decimal("0.001")) if fee_enabled else None,
        "fx_fee_rounding": _mapped(ROUNDING_MAP, account["feeCalculation"], "feeCalculation", where) if fee_enabled else None,
        "fx_fee_refundable": account["isRefundWithCurrencyFee"],
    }


def _apply_account_settings(account: Account, values: dict, skipped: list[str]) -> None:
    for column, value in values.items():
        current = getattr(account, column)
        if account.settings_locally_edited and account.id is not None:
            if current != value:
                skipped.append(f"{column}: {value} → kept {current}")
            continue
        setattr(account, column, value)


def _upsert_accounts(session: Session, data: BackupData, result: SettingsResult) -> None:
    group_keys = {group["identifier"]: group["name"] for group in data.groups}
    skipped: dict[str, list[str]] = {}
    final, matches = _plan_accounts(session, data, result)
    for record in data.accounts:
        currency = record["mainCurrency"] or result.main_currency
        name = final[record["identifier"]]
        account = matches[record["identifier"]]
        if account is None:
            account = Account(name=name, currency=currency, moze_id=record["identifier"])
            session.add(account)
            result.accounts_created.append(name)
        else:
            account.name = name
            if account.currency != currency:
                assert_currency_change_allowed(session, account, currency)
                account.currency = currency
            account.moze_id = record["identifier"]
        account.opening_balance = _amount(record["originalAmount"])
        account.is_archived = record["isArchived"] or record["type"] in SYSTEM_ACCOUNT_TYPES
        differences = skipped.setdefault(record["identifier"], [])
        _apply_account_settings(account, _account_settings(record, result), differences)
        if account.icon is None and not account.settings_locally_edited:
            account.icon = ICON_BY_GROUP.get(group_keys.get(record["group"], ""))
        result.accounts[record["identifier"]] = account
        session.flush()

    for record in data.accounts:
        account = result.accounts[record["identifier"]]
        credit = record["isCreditAccount"]
        links = {}
        for column, key in (("combined_account_id", "combinedAccount"), ("auto_pay_account_id", "autoPaidAccount")):
            target = result.accounts.get(record[key]) if credit and record[key] else None
            links[column] = target.id if target is not None and target.id != account.id else None
        _apply_account_settings(account, links, skipped[record["identifier"]])
        if skipped[record["identifier"]]:
            result.settings_skipped.append({"name": account.name, "differences": skipped[record["identifier"]]})
    session.flush()


def _system_category(session: Session, result: SettingsResult, kind: str) -> int:
    if kind not in result.system_categories:
        name = SYSTEM_CATEGORY_NAMES[kind]
        row = session.scalar(
            select(Category).where(Category.kind == kind, Category.parent_id.is_(None), Category.name == name)
        )
        if row is None:
            row = Category(kind=kind, parent_id=None, name=name)
            session.add(row)
            session.flush()
        result.system_categories[kind] = row.id
    return result.system_categories[kind]


def _upsert_categories(session: Session, data: BackupData, result: SettingsResult) -> None:
    main_kind: dict[str, str] = {}
    main_name: dict[str, str] = {}
    for record in data.categories:
        kind = CATEGORY_TYPE_TO_KIND.get(record["type"])
        if kind is None:
            continue
        name = SYSTEM_CATEGORY_NAMES.get(kind) or CATEGORY_NAMES.get(record["name"], record["name"])
        row, owned = _upsert_named(
            session, Category, record["identifier"],
            Category.kind == kind, Category.parent_id.is_(None), Category.name == name,
        )
        if owned:
            row.kind, row.parent_id, row.name = kind, None, name
            row.icon = ICON_BY_IMAGE.get(record["imageName"], row.icon)
            row.color = _color(record["colorHex"]) or row.color
            row.is_hidden = record["isHidden"]
            row.sort_order = record["sequence"]
        session.flush()
        result.categories[record["identifier"]] = row.id
        main_kind[record["identifier"]], main_name[record["identifier"]] = kind, name
        if kind in SYSTEM_CATEGORY_NAMES:
            result.system_categories.setdefault(kind, row.id)

    for record in data.classifications:
        parent = record["category"]
        if parent not in main_kind:
            continue
        kind, parent_id = main_kind[parent], result.categories[parent]
        name = CATEGORY_NAMES.get(record["name"], record["name"])
        if kind in SYSTEM_CATEGORY_NAMES or name == main_name[parent]:
            result.categories[record["identifier"]] = parent_id
            continue
        row, owned = _upsert_named(
            session, Category, record["identifier"],
            Category.kind == kind, Category.parent_id == parent_id, Category.name == name,
        )
        if owned:
            row.kind, row.parent_id, row.name = kind, parent_id, name
            row.is_hidden = record["isHidden"]
            row.sort_order = record["sequence"]
            default_account = result.accounts.get(record["defaultAccount"])
            row.default_account_id = default_account.id if default_account is not None else None
            row.default_project_id = result.projects.get(record["defaultProject"])
        session.flush()
        result.categories[record["identifier"]] = row.id
    result.kept_moze_ids["category"] = set(result.categories)


def _upsert_projects(session: Session, data: BackupData, result: SettingsResult) -> None:
    for record in data.projects:
        if record["name"] == SKIPPED_PROJECT:
            continue
        name = PROJECT_NAMES.get(record["name"], record["name"])
        row, owned = _upsert_named(session, Project, record["identifier"], Project.name == name)
        if owned:
            row.name = name
            row.is_archived = record["isArchived"]
            row.sort_order = record["sequence"]
        session.flush()
        result.projects[record["identifier"]] = row.id
    result.kept_moze_ids["project"] = set(result.projects)


def _upsert_counterparties(session: Session, data: BackupData, result: SettingsResult) -> None:
    for record in data.targets:
        row, owned = _upsert_named(session, Counterparty, record["identifier"], Counterparty.name == record["name"])
        if owned:
            row.name = record["name"]
        session.flush()
        result.counterparties[record["identifier"]] = row.id
    result.kept_moze_ids["counterparty"] = set(result.counterparties)


UNSUPPORTED_RULE_FIELDS = ("rewardLimit", "spendThreshold", "totalSpendThreshold", "minCountThreshold")


def _upsert_rules(session: Session, data: BackupData, result: SettingsResult) -> None:
    for record in data.rules:
        where = f"AHBonusReward '{record['name']}'"
        account = result.accounts.get(record["accountID"])
        if account is None:
            result.unsupported_rules.append({"name": record["name"], "reasons": ["account not in backup"]})
            continue
        method = _mapped(REWARD_METHOD_MAP, record["type"], "type", where)
        unsupported = [name for name in UNSUPPORTED_RULE_FIELDS if record[name]]
        if unsupported:
            result.unsupported_rules.append({"name": record["name"], "account": account.name, "reasons": unsupported})
        reward_account = result.accounts.get(record["rewardAccountID"])
        posting = _mapped(POSTING_MAP, record["rewardTimeType"], "rewardTimeType", where)
        post_month_offset, post_day = 0, 1  # unused unless the reward posts after the window
        if posting == "after_window":
            post_month_offset, post_day = record["rewardMonth"], record["rewardDay"]
            if not (0 <= post_month_offset <= 2 and 1 <= post_day <= 31):
                raise MozeImportError(
                    f"{where}: rewardMonth/rewardDay out of range "
                    f"(rewardMonth {post_month_offset}, expected 0-2; rewardDay {post_day}, expected 1-31)"
                )
        values = {
            "account_id": account.id,
            "name": record["name"],
            "method": method,
            "rate": record["rewardPercentage"] if method == "percent" else None,
            "fixed_amount": _amount(record["rewardAmount"]) if method == "fixed" else None,
            "window": _mapped(REWARD_WINDOW_MAP, record["rewardPeriodType"], "rewardPeriodType", where),
            "posting": posting,
            "delay_days": record["rewardDelayDays"],
            "post_month_offset": post_month_offset,
            "post_day": post_day,
            "txn_rounding": _mapped(ROUNDING_MAP, record["rewardCalculation"], "rewardCalculation", where),
            "total_rounding": _mapped(ROUNDING_MAP, record["totalRewardCalculation"], "totalRewardCalculation", where),
            "total_cap": _amount(record["totalRewardLimit"]) if record["totalRewardLimit"] else None,
            "shared_cap_id": moze_uuid(record["rewardSharingID"]),
            "is_basic": record["isBasic"],
            "reward_account_id": reward_account.id if reward_account is not None else account.id,
            "reward_project_id": result.projects.get(record["rewardProjectID"]),
            "starts_on": record["startDate"].date(),
            "ends_on": record["dueDate"].date(),
            "is_enabled": record["isEnabled"] and not unsupported,
            "description": _text(record["desc"]),
            "sort_order": record["sequence"],
        }
        rule = session.scalar(select(RewardRule).where(RewardRule.moze_id == record["identifier"]))
        if rule is None:
            rule = RewardRule(moze_id=record["identifier"])
            session.add(rule)
        for column, value in values.items():
            setattr(rule, column, value)
        session.flush()
        result.rules[record["identifier"]] = rule.id

    gone = session.scalars(
        select(RewardRule).where(RewardRule.moze_id.is_not(None), RewardRule.moze_id.not_in(list(result.rules) or [""]))
    ).all()
    for rule in gone:
        referenced = session.scalar(
            select(
                or_(
                    exists().where(EntryRewardRule.rule_id == rule.id),
                    exists().where(LedgerEntry.reward_rule_id == rule.id),
                )
            )
        )
        if referenced:
            rule.is_enabled = False
            result.orphaned_rules.append({"name": rule.name, "account": session.get(Account, rule.account_id).name})
        else:
            session.delete(rule)
    session.flush()


def _upsert_preference(session: Session, data: BackupData, result: SettingsResult) -> None:
    record = data.preference
    preference = session.get(Preference, 1)
    if preference is None:
        preference = Preference(id=1)
        session.add(preference)
    preference.expense_income_colors = _mapped(COLOR_MAP, record["expenseIncomeColor"], "expenseIncomeColor", "AHPreference")
    preference.keypad_layout = _mapped(KEYPAD_MAP, record["numberPadType"], "numberPadType", "AHPreference")
    preference.week_start = (record["firstWeekday"] - 1) % 7
    preference.main_currency = result.main_currency
    preference.hide_rewards_on_timeline = record["hideRewardsOnHome"]
    preference.abbreviate_totals = record["isTotalBalanceAbbreviate"]
    session.flush()


def upsert_settings(session: Session, data: BackupData) -> SettingsResult:
    """Upsert groups, accounts, projects, categories, counterparties, rules and the preference row.

    Runs after the old MOZE entries are deleted, so the currency rule sees only entries that will remain.
    """
    result = SettingsResult(main_currency=data.preference["mainCurrency"] or "TWD")
    _upsert_groups(session, data, result)
    _upsert_accounts(session, data, result)
    _upsert_projects(session, data, result)
    _upsert_categories(session, data, result)
    _upsert_counterparties(session, data, result)
    _upsert_rules(session, data, result)
    _upsert_preference(session, data, result)
    return result


RATE_QUANTUM = Decimal("0.0000000001")  # fx_rate is NUMERIC(20,10)
FX_OUTLIER_TOLERANCE = Decimal("0.25")
TRANSFER_RATE_TOLERANCE = Decimal("0.01")
SETTLED_TYPES = (3, 4)
SETTLING_TYPES = (5, 6)


@dataclass
class EntryResult:
    entries: dict[str, LedgerEntry] = field(default_factory=dict)  # AHRecord identifier -> entry
    kind_counts: Counter = field(default_factory=Counter)
    skipped_future: Counter = field(default_factory=Counter)  # record type -> count
    skipped_records: list[dict] = field(default_factory=list)
    needs_review: Counter = field(default_factory=Counter)  # reason -> count
    groups: int = 0
    transfers: int = 0
    attachments: int = 0
    transfer_rate_mismatches: int = 0
    fx_outliers: list[dict] = field(default_factory=list)
    refund_direction: str | None = None
    tag_delimiter: str | None = None


def _account_currency(data: BackupData, main_currency: str) -> dict[str, str]:
    return {account["identifier"]: account["mainCurrency"] or main_currency for account in data.accounts}


def _is_future(record: dict, cutoff: date) -> bool:
    return record["date"].date() > cutoff


def required_backup_rates(data: BackupData) -> set[tuple[date, str, str]]:
    """(day, record currency, quote) for every foreign record: the MOZE-rate sanity check or the fx_api conversion."""
    main = data.preference["mainCurrency"] or "TWD"
    currencies = _account_currency(data, main)
    conversions = {conversion["recordID"] for conversion in data.conversions}
    cutoff = data.exported_at.date()
    needed = set()
    for record in data.records:
        account_currency = currencies.get(record["account"])
        currency = record["currency"] or account_currency
        if account_currency is None or currency == account_currency or _is_future(record, cutoff):
            continue
        uses_moze_rate = account_currency == main and record["currencyConversion"] in conversions
        needed.add((record["date"].date(), currency, main if uses_moze_rate else account_currency))
    return needed


@dataclass(frozen=True)
class _Fx:
    rate: Decimal
    currency: str
    source: str

    def columns(self, amount: Decimal) -> dict:
        return {"original_amount": amount, "original_currency": self.currency, "fx_rate": self.rate, "fx_source": self.source}


def _record_fx(record, account, conversions, rates, main, result, allow_fx_outliers) -> _Fx | None:
    currency = record["currency"] or account.currency
    if currency == account.currency:
        return None
    day = record["date"].date()
    conversion = conversions.get(record["currencyConversion"])
    if account.currency == main and conversion is not None:
        rate = conversion["exchangeRate"]
        cached = rates.get((day, currency, main))
        if cached is None:
            raise MozeImportError(f"AHRecord '{record['identifier']}': no cached {currency}→{main} rate on {day.isoformat()}")
        if abs(rate - cached) / cached > FX_OUTLIER_TOLERANCE:
            outlier = {"moze_id": record["identifier"], "date": day.isoformat(), "currency": currency,
                       "moze_rate": str(rate), "cached_rate": str(cached)}
            if not allow_fx_outliers:
                raise MozeImportError(
                    f"AHRecord '{record['identifier']}': MOZE rate {rate} differs from the cached {currency}→{main} "
                    f"rate {cached} on {day.isoformat()} by more than 25% (pass --allow-fx-outliers to accept)"
                )
            result.fx_outliers.append(outlier)
        return _Fx(rate.quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP), currency, "moze_backup")
    key = (day, currency, account.currency)
    if key not in rates:
        raise MozeImportError(f"AHRecord '{record['identifier']}': no FX rate for {currency}→{account.currency} on {day.isoformat()}")
    return _Fx(rates[key], currency, "fx_api")


def _money_columns(amount: Decimal, fx: _Fx | None) -> dict:
    if fx is None:
        return {"amount": _amount(amount)}
    return {"amount": _amount(amount * fx.rate), **fx.columns(_amount(amount))}


def _entry_for(record, kind, account, fx, settings, session, import_run_id) -> LedgerEntry:
    if kind in SYSTEM_CATEGORY_NAMES:
        category_id = _system_category(session, settings, kind)
    else:
        category_id = settings.categories.get(record["classification"])
    return LedgerEntry(
        account_id=account.id,
        kind=kind,
        currency=account.currency,
        **_money_columns(record["price"], fx),
        entry_date=record["date"].date(),
        entry_time=record["date"].time(),
        posted_date=record["chargeDate"].date(),
        category_id=category_id,
        project_id=settings.projects.get(record["project"]),
        counterparty_id=settings.counterparties.get(record["target"]),
        name=_text(record["name"]),
        merchant=_text(record["store"]),
        description=_text(record["desc"]),
        tags=split_tags(record["tags"]),
        invoice_number=_text(record["invoiceNumber"]),
        is_settlement=record["type"] in SETTLING_TYPES,  # required by ck_ledger_entry_settlement_sign for +receivable / -payable
        needs_review=False,
        source=SOURCE,
        moze_id=record["identifier"],
        import_run_id=import_run_id,
    )


def _column_children(record, parent, fx, settings, session, import_run_id) -> list[LedgerEntry]:
    children = []
    for kind, column, name_column, default_name in (("fee", "fee", "feeName", "手續費"), ("discount", "bonus", "bonusName", "折扣")):
        if record[column] == 0:
            continue
        children.append(
            LedgerEntry(
                account_id=parent.account_id,
                kind=kind,
                currency=parent.currency,
                **_money_columns(record[column], fx),
                entry_date=parent.entry_date,
                entry_time=parent.entry_time,
                posted_date=parent.posted_date,
                category_id=_system_category(session, settings, kind),
                name=_text(record[name_column]) or default_name,
                tags=[],
                is_settlement=False,
                needs_review=False,
                source=SOURCE,
                import_run_id=import_run_id,
            )
        )
    return children


def _review(entry: LedgerEntry, reason: str, result: EntryResult) -> None:
    entry.needs_review = True
    result.needs_review[reason] += 1


def _link_transfers(data: BackupData, result: EntryResult) -> None:
    linked: set[str] = set()
    for transfer in data.transfers:
        out_leg, in_leg = result.entries.get(transfer["outRecord"]), result.entries.get(transfer["inRecord"])
        if out_leg is None or in_leg is None:
            continue
        group_id = uuid.uuid4()
        out_leg.transfer_group_id = in_leg.transfer_group_id = group_id
        linked.update((transfer["outRecord"], transfer["inRecord"]))
        result.transfers += 1
        if out_leg.amount == 0 or in_leg.amount == 0:
            continue
        derived = (abs(in_leg.amount) / abs(out_leg.amount))
        if transfer["exchangeRate"] and abs(transfer["exchangeRate"] - derived) / derived > TRANSFER_RATE_TOLERANCE:
            result.transfer_rate_mismatches += 1
        if out_leg.currency != in_leg.currency:
            for leg, other in ((out_leg, in_leg), (in_leg, out_leg)):
                sign = 1 if leg.amount > 0 else -1
                leg.original_amount = sign * abs(other.amount)
                leg.original_currency = other.currency
                leg.fx_rate = (abs(leg.amount) / abs(other.amount)).quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)
                leg.fx_source = "moze_backup"
    for record in data.records:
        entry = result.entries.get(record["identifier"])
        if entry is not None and record["type"] == 2 and record["identifier"] not in linked:
            _review(entry, "unpaired_transfer", result)


def _link_refunds(data: BackupData, result: EntryResult) -> None:
    by_refund_id: dict[str, list[str]] = {}
    for record in data.records:
        if record["refundID"] and not record["isRefund"]:
            by_refund_id.setdefault(record["refundID"], []).append(record["identifier"])
    directions: Counter = Counter()
    for record in data.records:
        entry = result.entries.get(record["identifier"])
        if entry is None or not record["isRefund"]:
            continue
        original, direction = None, None
        if record["refundID"] and record["refundID"] in result.entries and record["refundID"] != record["identifier"]:
            original, direction = result.entries[record["refundID"]], "refund_record_points_to_original"
        elif record["identifier"] in by_refund_id:
            original, direction = result.entries.get(by_refund_id[record["identifier"]][0]), "original_points_to_refund"
        elif record["refundID"] and record["refundID"] in by_refund_id:
            original, direction = result.entries.get(by_refund_id[record["refundID"]][0]), "shared_refund_id"
        if original is None:
            _review(entry, "refund_original_missing", result)
            continue
        entry.refunds_entry_id = original.id
        directions[direction] += 1
    result.refund_direction = directions.most_common(1)[0][0] if directions else None


def _link_settlements(data: BackupData, result: EntryResult) -> None:
    types = {record["identifier"]: record["type"] for record in data.records}
    for record in data.records:
        entry = result.entries.get(record["identifier"])
        related = record["relatedID"]
        if entry is None or record["type"] not in SETTLING_TYPES or not related:
            continue
        if types.get(related) in SETTLED_TYPES and related in result.entries:
            entry.settles_entry_id = result.entries[related].id


def _link_rewards_and_attachments(session: Session, data: BackupData, settings: SettingsResult, result: EntryResult) -> None:
    attachments = []
    for record in data.records:
        entry = result.entries.get(record["identifier"])
        if entry is None:
            continue
        if record["type"] == 14:
            entry.reward_rule_id = settings.rules.get(record["rewardID"])
            source = result.entries.get(record["rewardRecordID"])
            entry.reward_source_entry_id = source.id if source is not None else None
            if entry.reward_rule_id is None:
                _review(entry, "reward_rule_missing", result)
            if source is None:
                _review(entry, "reward_source_missing", result)
        for rule_id in dict.fromkeys(settings.rules[key] for key in record["bonusRewards"] if key in settings.rules):
            attachments.append(EntryRewardRule(entry_id=entry.id, rule_id=rule_id))
    session.add_all(attachments)
    result.attachments = len(attachments)


def _link_groups(session: Session, data: BackupData, result: EntryResult) -> None:
    groups = []
    for package in data.packages:
        members = [result.entries[key] for key in package["records"] if key in result.entries]
        if not members:
            continue
        if package["type"] == 4:
            kind = "reward_claim"
        elif package["eventType"] == 2:
            kind = "installment"
        else:
            kind = "split"
        group = EntryGroup(
            kind=kind, name=_text(package["name"]), merchant=_text(package["store"]),
            description=_text(package["desc"]), moze_id=package["identifier"],
        )
        groups.append((group, members))
    session.add_all([group for group, _ in groups])
    session.flush()
    for group, members in groups:
        for member in members:
            member.group_id = group.id
    result.groups = len(groups)


def insert_entries(
    session: Session,
    data: BackupData,
    settings: SettingsResult,
    import_run_id: int | None,
    rates: Mapping[tuple[date, str, str], Decimal],
    *,
    allow_fx_outliers: bool,
) -> EntryResult:
    """Insert one entry per live AHRecord dated up to the export date, with children, FX and links."""
    result = EntryResult()
    cutoff = data.exported_at.date()
    conversions = {conversion["recordID"]: conversion for conversion in data.conversions}
    kinds = {record["identifier"]: record_kind(record) for record in data.records}  # fails on unknown types first

    live = []
    for record in data.records:
        if _is_future(record, cutoff):
            result.skipped_future[record["type"]] += 1
            result.skipped_records.append(record)
        else:
            live.append(record)
    live_ids = {record["identifier"] for record in live}
    fee_parent = {
        record["feeID"]: record["identifier"]
        for record in live
        if record["feeID"] and record["feeID"] in live_ids and record["feeID"] != record["identifier"]
    }
    fee_records: dict[str, list[dict]] = {}
    for record in live:
        if record["identifier"] in fee_parent:
            fee_records.setdefault(fee_parent[record["identifier"]], []).append(record)

    ordered: list[tuple[dict, LedgerEntry, list[LedgerEntry]]] = []

    def build(record: dict) -> None:
        account = settings.accounts.get(record["account"])
        if account is None:
            raise MozeImportError(f"AHRecord '{record['identifier']}': account {record['account']!r} is not in the backup")
        fx = _record_fx(record, account, conversions, rates, settings.main_currency, result, allow_fx_outliers)
        kind = "fee" if record["identifier"] in fee_parent else kinds[record["identifier"]]
        entry = _entry_for(record, kind, account, fx, settings, session, import_run_id)
        if not record["isEnabled"]:
            _review(entry, "disabled_record", result)
        ordered.append((record, entry, _column_children(record, entry, fx, settings, session, import_run_id)))
        result.entries[record["identifier"]] = entry
        for child_record in fee_records.get(record["identifier"], []):
            build(child_record)

    for record in sorted(live, key=lambda r: r["date"]):
        if record["identifier"] not in fee_parent:
            build(record)
    unbuilt = sorted(live_ids - set(result.entries))
    if unbuilt:
        raise MozeImportError(f"AHRecord '{unbuilt[0]}': feeID links form a cycle")

    total = sum(1 + len(children) for _, _, children in ordered)
    seqs = iter(
        sorted(session.scalars(text("SELECT nextval('ledger_entry_seq_seq') FROM generate_series(1, :n)"), {"n": total}))
    ) if total else iter(())
    for _, entry, children in ordered:
        entry.seq = next(seqs)
        for child in children:
            child.seq = next(seqs)
    session.add_all([entry for _, entry, _ in ordered])
    session.flush()
    for record, entry, children in ordered:
        if record["identifier"] in fee_parent:
            entry.parent_entry_id = result.entries[fee_parent[record["identifier"]]].id
        for child in children:
            child.parent_entry_id = entry.id
        session.add_all(children)
    session.flush()

    _link_transfers(data, result)
    _link_refunds(data, result)
    _link_settlements(data, result)
    _link_rewards_and_attachments(session, data, settings, result)
    _link_groups(session, data, result)
    session.flush()

    for _, entry, children in ordered:
        result.kind_counts[entry.kind] += 1
        for child in children:
            result.kind_counts[child.kind] += 1
    result.tag_delimiter = tag_delimiter(record["tags"] for record in live)
    return result


def replace_ledger_from_backup(
    session: Session,
    data: BackupData,
    import_run_id: int | None,
    renames: Mapping[str, str],
    rates: Mapping[tuple[date, str, str], Decimal],
    *,
    strict: bool,
    allow_fx_outliers: bool,
) -> dict:
    """Full replace in the caller's transaction; returns the report summary (Task 9: settings and entries)."""
    renamed = _apply_renames(session, renames or {})
    delete_moze_entries(session)
    settings = upsert_settings(session, data)
    entries = insert_entries(session, data, settings, import_run_id, rates or {}, allow_fx_outliers=allow_fx_outliers)
    session.flush()
    return {
        "kind": "moze_backup",
        "exported_at": data.exported_at.isoformat(),
        "kind_counts": dict(sorted(entries.kind_counts.items())),
        "skipped_future": {str(key): value for key, value in sorted(entries.skipped_future.items())},
        "groups": entries.groups,
        "transfers": entries.transfers,
        "transfer_rate_mismatches": entries.transfer_rate_mismatches,
        "rules": len(settings.rules),
        "attachments": entries.attachments,
        "counterparties": len(set(settings.counterparties.values())),
        "needs_review": {
            "count": sum(1 for entry in entries.entries.values() if entry.needs_review),
            "reasons": dict(sorted(entries.needs_review.items())),
        },
        "unsupported_rules": settings.unsupported_rules,
        "orphaned_rules": settings.orphaned_rules,
        "settings_skipped": settings.settings_skipped,
        "fx_outliers": entries.fx_outliers,
        "confirmed_maps": {
            "due_rule": {str(key): value for key, value in DUE_RULE_MAP.items()},
            "rounding": {str(key): value for key, value in ROUNDING_MAP.items()},
            "refund_direction": entries.refund_direction,
            "tag_delimiter": entries.tag_delimiter,
            "balance_info_key": None,
        },
        "accounts_created": settings.accounts_created,
        "accounts_renamed": renamed + settings.accounts_renamed,
    }
