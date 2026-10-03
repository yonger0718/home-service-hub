"""MOZE backup import: converter subprocess, settings upsert, entries and links, full replace (design D12-D19).

CLI: python -m app.services.moze_backup_import_service <zip> [--dry-run] [--rename OLD=NEW ...]
     [--allow-fx-outliers] [--no-strict] [--keep-json PATH]
"""

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Callable, Mapping, Sequence

from sqlalchemy import delete, exists, func, or_, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from ..models import (
    MOZE_SOURCES,
    Account,
    AccountGroup,
    Category,
    Counterparty,
    EntryGroup,
    EntryRewardRule,
    ImportRun,
    LedgerEntry,
    MozeSchedule,
    Preference,
    Project,
    RewardRule,
)
from . import fx_rate_service, ledger_service
from .moze_backup_json import (
    ARCHIVE_GROUP,
    category_name,
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
    load_backup_json,
    record_kind,
    split_tags,
    tag_delimiter,
)
from .moze_csv import MozeImportError
from .moze_import_service import (
    SYSTEM_CATEGORY_NAMES,
    ImportLockedError,
    ImportRefusedError,
    _apply_renames,
    _archive_disappeared_accounts,
    _money,
    _now,
    _parse_rename,
    assert_currency_change_allowed,
    delete_moze_entries,
    delete_unused_rows,
    failure_summary,
    import_lock,
    import_locked,
    mark_interrupted_runs,
    run_report,
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
    unmapped_category_keys: set[str] = field(default_factory=set)
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
    holds a needed name but whose MOZE id is gone from the backup is renamed `<name> (舊 <id>)`. Whether it is
    archived is left to `_archive_disappeared_accounts` (after the settings step), so a locally edited row or one
    that still has entries keeps its state and opening balance.
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
        holder.name = stale_name
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
        name = SYSTEM_CATEGORY_NAMES.get(kind) or category_name(record["name"], result.unmapped_category_keys)
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
        name = category_name(record["name"], result.unmapped_category_keys)
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
            # MOZE stores a fraction (0.01 = 1 %); reward_rule.rate is a percent, NUMERIC(8,4)
            "rate": (record["rewardPercentage"] * 100).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
            if method == "percent" else None,
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
BALANCE_ADJUSTMENT_TYPE = 7  # price is the account balance after the adjustment; total is the delta


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
    # (account name, reason) per main-currency record whose MOZE conversion row gives no usable rate
    fx_backup_rate_missing: list[tuple[str, str]] = field(default_factory=list)
    refund_direction: str | None = None
    tag_delimiter: str | None = None
    package_members: dict[str, list[LedgerEntry]] = field(default_factory=dict)  # AHPackage id -> grouped entries
    reward_source_from_package: int = 0  # rewards whose rewardRecordID names an imported package


def _account_currency(data: BackupData, main_currency: str) -> dict[str, str]:
    return {account["identifier"]: account["mainCurrency"] or main_currency for account in data.accounts}


def _is_future(record: dict, cutoff: date) -> bool:
    return record["date"].date() > cutoff


def _backup_rate(conversion: dict | None, account_currency: str, record_currency: str) -> Decimal | None:
    """Account-currency units per 1 record-currency unit from an AHCurrencyConversion row, or None if unusable.

    MOZE stores `exchangeRate` as units of `baseCurrencyCode` per 1 unit of `targetCurrencyCode`: base = account
    currency and target = record currency is used as is; the reverse pair is inverted; a rate not > 0 or any
    other pair gives None (the record then uses the cached fx_api rate).
    """
    if conversion is None or not conversion["exchangeRate"] > 0:
        return None
    rate = conversion["exchangeRate"]
    pair = (conversion["baseCurrencyCode"], conversion["targetCurrencyCode"])
    if pair == (account_currency, record_currency):
        return rate
    if pair == (record_currency, account_currency):
        return (Decimal(1) / rate).quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)
    return None


def required_backup_rates(data: BackupData) -> set[tuple[date, str, str]]:
    """(day, record currency, quote) for every foreign record: the MOZE-rate sanity check or the fx_api conversion."""
    main = data.preference["mainCurrency"] or "TWD"
    currencies = _account_currency(data, main)
    conversions = {conversion["recordID"]: conversion for conversion in data.conversions}
    cutoff = data.exported_at.date()
    needed = set()
    for record in data.records:
        account_currency = currencies.get(record["account"])
        currency = record["currency"] or account_currency
        if account_currency is None or currency == account_currency or _is_future(record, cutoff):
            continue
        uses_moze_rate = account_currency == main and _backup_rate(
            conversions.get(record["currencyConversion"]), account_currency, currency
        ) is not None
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
    rate = _backup_rate(conversion, account.currency, currency)
    if account.currency == main and conversion is not None and rate is None:
        # no usable MOZE rate: convert with the cached daily rate as if no conversion row existed
        reason = "zero_rate" if not conversion["exchangeRate"] > 0 else "pair"
        result.fx_backup_rate_missing.append((account.name, reason))
    if account.currency == main and rate is not None:
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


def _record_amount(record) -> Decimal:
    """The entry amount: MOZE's price, except a balance adjustment posts its delta (total), not the new balance."""
    return record["total"] if record["type"] == BALANCE_ADJUSTMENT_TYPE else record["price"]


def _entry_for(record, kind, account, fx, settings, session, import_run_id) -> LedgerEntry:
    if kind in SYSTEM_CATEGORY_NAMES:
        category_id = _system_category(session, settings, kind)
    else:
        category_id = settings.categories.get(record["classification"])
    return LedgerEntry(
        account_id=account.id,
        kind=kind,
        currency=account.currency,
        **_money_columns(_record_amount(record), fx),
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
        # required by ck_ledger_entry_settlement_sign for +receivable / -payable; a fee child or refund never settles
        is_settlement=record["type"] in SETTLING_TYPES and kind in ("receivable", "payable"),
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
            if source is None and record["rewardRecordID"] in result.package_members:
                source = _primary_member(result.package_members[record["rewardRecordID"]])
                if source is not None:
                    result.reward_source_from_package += 1
            entry.reward_source_entry_id = source.id if source is not None else None
            if entry.reward_rule_id is None:
                _review(entry, "reward_rule_missing", result)
            if source is None:
                _review(entry, "reward_source_missing", result)
        for rule_id in dict.fromkeys(settings.rules[key] for key in record["bonusRewards"] if key in settings.rules):
            attachments.append(EntryRewardRule(entry_id=entry.id, rule_id=rule_id))
    session.add_all(attachments)
    result.attachments = len(attachments)


def _primary_member(members: list[LedgerEntry]) -> LedgerEntry | None:
    """The split's first member in canonical order (entry_date, entry_time NULLS FIRST, seq), fee/discount excluded."""
    candidates = [member for member in members if member.kind not in ("fee", "discount")]
    return min(
        candidates,
        key=lambda m: (m.entry_date, m.entry_time is not None, m.entry_time or datetime.min.time(), m.seq),
        default=None,
    )


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
        result.package_members[package["identifier"]] = members
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
    _link_groups(session, data, result)  # before rewards: a reward may name a package as its source
    _link_rewards_and_attachments(session, data, settings, result)
    session.flush()

    for _, entry, children in ordered:
        result.kind_counts[entry.kind] += 1
        for child in children:
            result.kind_counts[child.kind] += 1
    result.tag_delimiter = tag_delimiter(record["tags"] for record in live)
    return result


MAX_BACKUP_BYTES = 200 * 1024 * 1024
CONVERTER_TIMEOUT_SEC = 600
REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_EXPORTER_SCRIPT = REPO_ROOT / "tools" / "moze-realm-export" / "index.js"

# Rule that names the balanceInfo key holding the balance MOZE showed at export time, or None while
# no rule is confirmed (design: balanceInfo is not comparable by default). Signature: (AHAccount row) -> key | None.
balance_info_key: Callable[[dict], str | None] | None = None


class ConverterError(MozeImportError):
    """The Realm converter could not produce the JSON document; nothing was locked or written."""


def exporter_command() -> list[str]:
    """The converter command from `MOZE_REALM_EXPORTER`, default `node <repo>/tools/moze-realm-export/index.js`.

    A value naming one existing file keeps the original behaviour: a `.js` script runs with `node` from PATH,
    anything else is executed directly. Any other value is split like a shell command line (`shlex.split`, so
    quotes work) and used as the command, e.g. `/usr/bin/node /path/to/index.js`; its last argument ending in
    `.js` must exist.
    """
    configured = os.getenv("MOZE_REALM_EXPORTER", "").strip()
    single = Path(configured).expanduser() if configured else DEFAULT_EXPORTER_SCRIPT
    if single.is_file():
        return [shutil.which("node") or "node", str(single)] if single.suffix == ".js" else [str(single)]
    try:
        words = shlex.split(configured)
    except ValueError as exc:
        raise ConverterError(f"MOZE_REALM_EXPORTER cannot be parsed: {exc}") from exc
    if len(words) < 2:
        raise ConverterError(f"MOZE realm exporter not found: {single}")
    scripts = [word for word in words[1:] if word.endswith(".js")]
    if scripts and not Path(scripts[-1]).expanduser().is_file():
        raise ConverterError(f"MOZE realm exporter script not found: {scripts[-1]}")
    return words


def convert_backup(zip_path: Path, work_dir: Path, exporter: Sequence[str] | None = None, timeout: int = CONVERTER_TIMEOUT_SEC) -> Path:
    """Run the converter on `zip_path`; returns the JSON path inside `work_dir`. Raises ConverterError."""
    command = list(exporter) if exporter is not None else exporter_command()
    out = Path(work_dir) / "backup.json"
    args = [*command, str(zip_path), "--out", str(out), "--work", str(Path(work_dir) / "realm")]
    try:
        completed = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise ConverterError(f"MOZE realm exporter not found: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ConverterError(f"MOZE realm exporter timed out after {timeout} s") from exc
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout).strip()[-500:]
        raise ConverterError(f"MOZE realm exporter failed (exit {completed.returncode}): {message}")
    if not out.is_file():
        raise ConverterError("MOZE realm exporter wrote no JSON document")
    return out


def _jsonable(row: dict) -> dict:
    def default(value):
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        raise TypeError(type(value).__name__)

    return json.loads(json.dumps(row, default=default, ensure_ascii=False))


def _replace_schedule(session: Session, data: BackupData, skipped: list[dict], import_run_id: int | None) -> dict:
    session.execute(delete(MozeSchedule))
    rows = [
        *(("period", row) for row in data.periods),
        *(("installment", row) for row in data.installments),
        *(("skipped_record", row) for row in skipped),
    ]
    session.add_all(
        MozeSchedule(kind=kind, moze_id=row.get("identifier"), payload=_jsonable(row), import_run_id=import_run_id)
        for kind, row in rows
    )
    session.flush()
    return dict(Counter(kind for kind, _ in rows))


def _cache_date(account: dict, data: BackupData) -> date:
    return (account["cacheDate"] or data.exported_at.replace(tzinfo=None)).date()


def _moze_part(session: Session, account: Account, cutoff: date, *, sources: Sequence[str], any_moze_id: bool) -> Decimal:
    condition = LedgerEntry.source.in_(sources)
    if any_moze_id:
        condition = or_(condition, LedgerEntry.moze_id.is_not(None))
    total = session.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(
            LedgerEntry.account_id == account.id, LedgerEntry.posted_date <= cutoff, condition
        )
    )
    return _amount(account.opening_balance + total)


def _previous_moze_parts(session: Session, data: BackupData) -> dict[str, Decimal]:
    """Per backup account: the moze_part of the ledger as it is before this import (any MOZE source)."""
    previous = {}
    for record in data.accounts:
        account = _find_account(session, record)
        if account is not None:
            previous[record["identifier"]] = _moze_part(
                session, account, _cache_date(record, data), sources=MOZE_SOURCES, any_moze_id=True
            )
    return previous


def _moze_balance(record: dict) -> Decimal | None:
    if balance_info_key is None or record["type"] in SYSTEM_ACCOUNT_TYPES:
        return None
    key = balance_info_key(record)
    if key is None or key not in record["balanceInfo"]:
        return None
    return _amount(Decimal(str(record["balanceInfo"][key])))


def _account_reports(session, data, settings, previous) -> list[dict]:
    reports = []
    for record in data.accounts:
        account = settings.accounts[record["identifier"]]
        moze_part = _moze_part(session, account, _cache_date(record, data), sources=(SOURCE,), any_moze_id=False)
        moze_balance = _moze_balance(record)
        reports.append(
            {
                "name": account.name,
                "currency": account.currency,
                "balance": _money(ledger_service.account_balance(session, account.id)),
                "moze_part": _money(moze_part),
                "previous_moze_part": _money(previous[record["identifier"]]) if record["identifier"] in previous else None,
                "moze_balance": _money(moze_balance) if moze_balance is not None else None,
                "difference": _money(moze_part - moze_balance) if moze_balance is not None else None,
                "compared": moze_balance is not None,
            }
        )
    return reports


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
    """Full replace in the caller's transaction; returns the report summary. Raises MozeImportError."""
    renamed = _apply_renames(session, renames or {})
    previous = _previous_moze_parts(session, data)
    delete_moze_entries(session)
    settings = upsert_settings(session, data)
    archived = _archive_disappeared_accounts(session, {account.name for account in settings.accounts.values()})
    entries = insert_entries(session, data, settings, import_run_id, rates or {}, allow_fx_outliers=allow_fx_outliers)
    delete_unused_rows(session, keep=settings.kept_moze_ids)
    schedules = _replace_schedule(session, data, entries.skipped_records, import_run_id)
    session.flush()

    accounts = _account_reports(session, data, settings, previous)
    compared = [report for report in accounts if report["compared"]]
    mismatched = [report for report in compared if Decimal(report["difference"]) != 0]
    if strict and mismatched:
        listed = ", ".join(f"{report['name']} ({report['difference']})" for report in mismatched)
        raise MozeImportError(f"strict mode: {len(mismatched)} compared account(s) differ from MOZE: {listed}")
    return {
        "kind": "moze_backup",
        "exported_at": data.exported_at.isoformat(),
        "kind_counts": dict(sorted(entries.kind_counts.items())),
        "skipped_future": {str(key): value for key, value in sorted(entries.skipped_future.items())},
        "schedules": schedules,
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
        "unmapped_category_keys": sorted(settings.unmapped_category_keys),
        "settings_skipped": settings.settings_skipped,
        "accounts": accounts,
        "compared_accounts": {"compared": len(compared), "total": len(accounts)},
        "not_compared": [report["name"] for report in accounts if not report["compared"]],
        "fx_outliers": entries.fx_outliers,
        "reward_source_from_package": entries.reward_source_from_package,
        "fx_backup_rate_missing": {
            "count": len(entries.fx_backup_rate_missing),
            "accounts": sorted({name for name, _ in entries.fx_backup_rate_missing}),
            "reasons": dict(sorted(Counter(reason for _, reason in entries.fx_backup_rate_missing).items())),
        },
        "confirmed_maps": {
            "due_rule": {str(key): value for key, value in DUE_RULE_MAP.items()},
            "rounding": {str(key): value for key, value in ROUNDING_MAP.items()},
            "refund_direction": entries.refund_direction,
            "tag_delimiter": entries.tag_delimiter,
            "balance_info_key": getattr(balance_info_key, "__name__", None),
        },
        "accounts_created": settings.accounts_created,
        "accounts_archived": archived,
        "accounts_renamed": renamed + settings.accounts_renamed,
    }


def _backup_summary_run(session, data, run_id, renames, http_get, *, strict, allow_fx_outliers, persist_rates) -> dict:
    """Resolve FX rates (ensure_rates commits what it persists, so it runs first), then replace (uncommitted)."""
    rates = fx_rate_service.ensure_rates(session, required_backup_rates(data), persist=persist_rates, http_get=http_get)
    return replace_ledger_from_backup(
        session, data, run_id, renames, rates, strict=strict, allow_fx_outliers=allow_fx_outliers
    )


def _run_backup_locked(conn, data, file_name, sha256, *, dry_run, renames, strict, allow_fx_outliers, http_get) -> dict:
    with Session(bind=conn, autoflush=False) as session:
        if dry_run:
            started = _now()
            try:
                summary = _backup_summary_run(
                    session, data, None, renames, http_get,
                    strict=strict, allow_fx_outliers=allow_fx_outliers, persist_rates=False,
                )
            finally:
                session.rollback()
            return {
                "id": None, "kind": "moze_backup", "status": "dry_run", "started_at": started.isoformat(),
                "finished_at": _now().isoformat(), "file_name": file_name, "file_sha256": sha256,
                "row_count": len(data.records), "exported_at": data.exported_at.isoformat(), "summary": summary,
            }

        mark_interrupted_runs(session)
        run = ImportRun(
            kind="moze_backup", started_at=_now(), file_name=file_name, file_sha256=sha256,
            status="running", exported_at=data.exported_at,
        )
        session.add(run)
        session.commit()
        run_id = run.id
        try:
            summary = _backup_summary_run(
                session, data, run_id, renames, http_get,
                strict=strict, allow_fx_outliers=allow_fx_outliers, persist_rates=True,
            )
            run = session.get(ImportRun, run_id)
            run.status, run.row_count, run.summary, run.finished_at = "succeeded", len(data.records), summary, _now()
            session.commit()
        except Exception as exc:
            session.rollback()
            run = session.get(ImportRun, run_id)
            run.status, run.summary, run.finished_at = "failed", failure_summary(exc, "moze_backup"), _now()
            session.commit()
            raise
        return run_report(session.get(ImportRun, run_id))


def _keep_private_copy(source: Path, destination: Path) -> None:
    """Copy the converter JSON (owner data) to `destination`, mode 0600 before any byte is written: created with
    0600, or an existing file fchmod-ed before it is truncated and rewritten. A symlinked destination is refused
    (O_NOFOLLOW), so the copy never lands where the link points."""
    if destination.is_symlink():
        raise MozeImportError(f"--keep-json destination is a symbolic link: {destination}")
    try:
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    except OSError as exc:
        raise MozeImportError(f"--keep-json destination cannot be written: {destination} ({exc.strerror})") from exc
    with os.fdopen(fd, "wb") as target, source.open("rb") as handle:
        os.fchmod(target.fileno(), 0o600)
        target.truncate()
        shutil.copyfileobj(handle, target)


def run_backup_import(
    engine: Engine,
    zip_path: Path,
    file_name: str,
    *,
    dry_run: bool = False,
    renames: Mapping[str, str] | None = None,
    strict: bool = True,
    allow_fx_outliers: bool = False,
    http_get: fx_rate_service.HttpGet | None = None,
    exporter: Sequence[str] | None = None,
    keep_json: Path | None = None,
) -> dict:
    """Convert, then import under the shared lock. Raises ImportRefusedError, ConverterError or MozeImportError."""
    if import_locked():
        raise ImportLockedError("MOZE import is locked (ACCOUNTING_IMPORT_LOCKED=true)")
    zip_path = Path(zip_path)
    if not zip_path.is_file():
        raise MozeImportError(f"backup archive not found: {zip_path}")
    if zip_path.stat().st_size > MAX_BACKUP_BYTES:
        raise MozeImportError("backup archive is larger than 200 MB")
    with zip_path.open("rb") as handle:
        sha256 = hashlib.file_digest(handle, "sha256").hexdigest()
    with tempfile.TemporaryDirectory(prefix="moze-backup-") as work:
        json_path = convert_backup(zip_path, Path(work), exporter)
        if keep_json is not None:
            _keep_private_copy(json_path, Path(keep_json))
        data = load_backup_json(json_path)
    with import_lock(engine) as conn:
        return _run_backup_locked(
            conn, data, file_name, sha256, dry_run=dry_run, renames=renames or {}, strict=strict,
            allow_fx_outliers=allow_fx_outliers, http_get=http_get,
        )


def _schedule_due(kind: str, payload: dict) -> date | None:
    def day(value) -> date | None:
        return date.fromisoformat(value[:10]) if isinstance(value, str) and len(value) >= 10 else None

    if kind == "skipped_record":
        return day(payload.get("date"))
    if kind == "installment":
        upcoming = sorted(d for d in (day(v) for v in (payload.get("dateInfo") or {}).values()) if d and d >= ledger_service._today())
        if upcoming:
            return upcoming[0]
    return day(payload.get("startDate"))


def list_schedules(db: Session, kind: str | None = None) -> list[dict]:
    """ScheduleItemOut rows: name, next_date, amount and currency are derived from the stored payload.

    currency: the payload's own `currency`, else the currency of the payload's `account` (by moze_id),
    else the preference's main currency. The payload itself stays in the table and is not returned.
    """
    query = select(MozeSchedule).order_by(MozeSchedule.id)
    if kind is not None:
        query = query.where(MozeSchedule.kind == kind)
    account_currency = dict(db.execute(select(Account.moze_id, Account.currency).where(Account.moze_id.is_not(None))).all())
    main = ledger_service.main_currency(db)
    items = []
    for row in db.scalars(query):
        payload = row.payload or {}
        amount = payload.get("total") if row.kind == "skipped_record" else payload.get("installment")
        currency = payload.get("currency")
        if not isinstance(currency, str) or not currency:
            currency = account_currency.get(payload.get("account")) or main
        items.append(
            {
                "id": row.id,
                "kind": row.kind,
                "moze_id": row.moze_id,
                "name": payload.get("name") or None,
                "next_date": _schedule_due(row.kind, payload),
                "amount": Decimal(str(amount)) if amount is not None else None,
                "currency": currency,
            }
        )
    return sorted(items, key=lambda item: (item["next_date"] is None, item["next_date"] or date.min, item["id"]))


def main(argv: Sequence[str] | None = None, *, engine: Engine | None = None, exporter: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.services.moze_backup_import_service")
    parser.add_argument("path", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--rename", action="append", default=[], type=_parse_rename, metavar="OLD=NEW")
    parser.add_argument("--allow-fx-outliers", action="store_true")
    parser.add_argument("--no-strict", action="store_true")
    parser.add_argument("--keep-json", type=Path, metavar="PATH")
    args = parser.parse_args(argv)

    if engine is None:
        from ..database import engine as default_engine

        engine = default_engine
    try:
        report = run_backup_import(
            engine, args.path, args.path.name, dry_run=args.dry_run, renames=dict(args.rename),
            strict=not args.no_strict, allow_fx_outliers=args.allow_fx_outliers, exporter=exporter,
            keep_json=args.keep_json,
        )
    except ImportRefusedError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except MozeImportError as exc:
        print(f"import failed: {exc}", file=sys.stderr)
        return 1
    compared = report["summary"]["compared_accounts"]
    print(f"compared_accounts: {compared['compared']} of {compared['total']}", file=sys.stderr)
    print(f"not_compared: {compared['total'] - compared['compared']}", file=sys.stderr)
    missing = report["summary"]["fx_backup_rate_missing"]
    print(f"reward_source_from_package: {report['summary']['reward_source_from_package']}", file=sys.stderr)
    print(f"fx_backup_rate_missing: {missing['count']} {missing['reasons']} ({', '.join(missing['accounts'])})", file=sys.stderr)
    if compared["compared"] == 0:
        print(
            f"WARNING: 0 of {compared['total']} accounts compared; check balances against MOZE by hand",
            file=sys.stderr,
        )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
