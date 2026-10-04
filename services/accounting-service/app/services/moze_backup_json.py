"""Load and validate the JSON written by tools/moze-realm-export (no database access).

Values are normalised while validating: MOZE doubles become Decimal, date strings become naive
datetimes (Asia/Taipei wall clock), so the importer never sees floats or unparsed dates.
"""

import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from ..models import (
    Account,
    AccountGroup,
    Category,
    Counterparty,
    EntryGroup,
    LedgerEntry,
    ScheduleDefinition,
    Preference,
    Project,
    RewardRule,
)
from .moze_csv import MozeImportError

TAIPEI = ZoneInfo("Asia/Taipei")

RECORD_TYPE_TO_KIND: dict[int, str] = {
    0: "expense",
    1: "income",
    2: "transfer_out",  # transfer_in when isTransferIn; see record_kind()
    3: "receivable",
    4: "payable",
    5: "receivable",  # collection of a receivable
    6: "payable",  # repayment of a payable
    7: "balance_adjustment",
    12: "fee",
    13: "discount",
    14: "reward",
    15: "interest",
    16: "fee",  # automatic foreign-transaction fee
}

GROUP_NAMES: dict[str, str] = {
    "APP_GROUP_CASH": "現金",
    "APP_GROUP_BANK": "銀行",
    "APP_GROUP_CREDIT_CARD": "信用卡",
    "APP_GROUP_STORED_VALUE_CARD": "電子票證",
    "APP_GROUP_GIFT_VOUCHER": "禮券",
    "APP_GROUP_REWARD": "紅利點數",
    "APP_GROUP_INSURANCE_POLICY": "保單",
    "APP_GROUP_SECURITIES": "證券",
    "APP_GROUP_CRYPTO_CURRENCY": "加密貨幣",
    "ACCOUNT_OTHERS": "其他",
}
ARCHIVE_GROUP = "ARCHIVE"

CATEGORY_NAMES: dict[str, str] = {
    "CATEGORY_FOOD": "飲食",
    "CATEGORY_TRANSPORT": "交通",
    "CATEGORY_ENTERTAINMENT": "娛樂",
    "CATEGORY_SHOPPING": "購物",
    "CATEGORY_PERSONAL": "個人",
    "CATEGORY_MEDICAL": "醫療",
    "CATEGORY_HOUSE": "家居",
    "CATEGORY_FAMILY": "家庭",
    "CATEGORY_LIVING": "生活",
    "CATEGORY_LEARNING": "學習",
    "CATEGORY_OTHERS": "其他",
    "CATEGORY_INCOME": "收入",
    "CATEGORY_TRANSFER": "轉帳",
    "CATEGORY_RECEIVABLE": "應收款項",
    "CATEGORY_PAYABLE": "應付款項",
    "CATEGORY_ADJUSTMENT": "餘額調整",
    "CATEGORY_SYSTEM_FEE": "手續費",
    "CATEGORY_SYSTEM_BONUS": "折扣",
    "CATEGORY_SYSTEM_BONUS_REWARD": "紅利回饋",
    "CATEGORY_SYSTEM_INTEREST": "利息",
    # 飲食
    "CATEGORY_BREAKFAST": "早餐",
    "CATEGORY_LUNCH": "午餐",
    "CATEGORY_DINNER": "晚餐",
    "CATEGORY_SNACKS": "點心",
    "CATEGORY_DRINKS": "飲料",
    "CATEGORY_ALCOHOL": "酒類",
    "CATEGORY_FRUITS": "水果",
    # 交通
    "CATEGORY_GAS": "加油",
    "CATEGORY_PARKING": "停車",
    "CATEGORY_TRAIN": "火車",
    "CATEGORY_SUBWAY": "捷運",
    "CATEGORY_CAR": "汽車",
    "CATEGORY_TAXI": "計程車",
    "CATEGORY_BUS": "公車",
    "CATEGORY_MOTO": "機車",
    "CATEGORY_BIKE": "腳踏車",
    "CATEGORY_AIRPLANE": "飛機",
    "CATEGORY_SHIP": "船",
    # 娛樂
    "CATEGORY_MOVIE": "電影",
    "CATEGORY_PLAYGROUND": "遊樂園",
    "CATEGORY_EXHIBITION": "展覽",
    "CATEGORY_VIDEO": "影片",
    "CATEGORY_MUSIC": "音樂",
    "CATEGORY_GAME": "遊戲",
    "CATEGORY_SPORT": "運動",
    "CATEGORY_GAMBLING": "博弈",
    "CATEGORY_RECREATION": "休閒",
    "CATEGORY_FITNESS": "健身",
    "CATEGORY_PUB": "酒吧",
    # 購物
    "CATEGORY_3C": "3C",
    "CATEGORY_SUPERMARKET": "超市",
    "CATEGORY_CLOTHING": "服飾",
    "CATEGORY_SHOES": "鞋子",
    "CATEGORY_ACCESSORY": "配件",
    "CATEGORY_BAG": "包包",
    "CATEGORY_COSMETICS": "美妝",
    "CATEGORY_BOUTIQUE": "精品",
    "CATEGORY_GIFT": "禮物",
    "CATEGORY_APP": "App",
    # 個人
    "CATEGORY_SOCIAL": "社交",
    "CATEGORY_MOBILE": "手機",
    "CATEGORY_LOAN": "貸款",
    "CATEGORY_INVESTMENT": "投資",
    "CATEGORY_TAX": "稅金",
    "CATEGORY_INSURANCE": "保險",
    "CATEGORY_DONATION": "捐款",
    "CATEGORY_PETS": "寵物",
    "CATEGORY_LOTTERY": "彩券",
    # 醫療
    "CATEGORY_HOSPITAL": "醫院",
    "CATEGORY_TOOTH_CARE": "牙齒保健",
    "CATEGORY_MEDICINE": "藥品",
    "CATEGORY_SUPPLIES": "用品",
    "CATEGORY_INJECTION": "針劑",
    "CATEGORY_WARD": "病房",
    "CATEGORY_SURGERY": "手術",
    "CATEGORY_EXAMINATION": "檢查",
    # 家居
    "CATEGORY_GROCERIES": "日用品",
    "CATEGORY_WATER": "水費",
    "CATEGORY_ELECTRICITY": "電費",
    "CATEGORY_FUEL": "瓦斯",
    "CATEGORY_PHONE": "電話",
    "CATEGORY_INTERNET": "網路",
    "CATEGORY_RENT": "房租",
    "CATEGORY_LAUNDRY": "洗衣",
    "CATEGORY_REPAIR": "維修",
    "CATEGORY_FURNITURE": "家具",
    "CATEGORY_SUBSCRIPTION": "訂閱",
    "CATEGORY_APPLIANCE": "家電",
    # 家庭
    "CATEGORY_ALLOWANCE": "零用錢",
    "CATEGORY_EDUCATION": "教育",
    "CATEGORY_NURSING": "照護",
    "CATEGORY_TOY": "玩具",
    "CATEGORY_TALENT": "才藝",
    # 生活
    "CATEGORY_SALON": "美髮",
    "CATEGORY_SPA": "SPA",
    "CATEGORY_MASSAGE": "按摩",
    "CATEGORY_ACCOMMODATION": "住宿",
    "CATEGORY_TRAVEL": "旅遊",
    "CATEGORY_PARTY": "聚會",
    # 學習
    "CATEGORY_BOOK": "書籍",
    "CATEGORY_COURSE": "課程",
    "CATEGORY_MATERIAL": "教材",
    "CATEGORY_CERTIFICATION": "證照",
    "CATEGORY_OUTDOORS": "戶外",
    "CATEGORY_STATIONERY": "文具",
    # 收入
    "CATEGORY_SALARY": "薪水",
    "CATEGORY_BONUS": "獎金",
    "CATEGORY_REPAYMENT": "收款",
    "CATEGORY_INTEREST": "利息",
    # 轉帳
    "CATEGORY_WITHDRAW": "提款",
    "CATEGORY_DEPOSIT": "存款",
    "CATEGORY_REFUND": "退款",
    "CATEGORY_EXCHANGE": "兌換",
    # 應收
    "CATEGORY_LEND": "借出",
    "CATEGORY_PAY_FOR": "代付",
    "CATEGORY_REIMBURSE": "報帳",
    # 應付
    "CATEGORY_BORROW": "借入",
    "CATEGORY_CREDIT": "信貸",
    "CATEGORY_CAR_LOAN": "車貸",
    "CATEGORY_MORTGAGE": "房貸",
}

_CATEGORY_KEY = re.compile(r"^CATEGORY_[A-Z0-9_]+$")


def category_name(raw: str, unmapped: set[str] | None = None) -> str:
    """Display name for a MOZE category/classification name; unknown CATEGORY_* keys are title-cased and recorded."""
    if raw in CATEGORY_NAMES:
        return CATEGORY_NAMES[raw]
    if _CATEGORY_KEY.match(raw):
        if unmapped is not None:
            unmapped.add(raw)
        return raw[len("CATEGORY_"):].replace("_", " ").title()
    return raw

# AHCategory.type -> category kind; types 0, 7, 8, 13 and 102 are not imported.
CATEGORY_TYPE_TO_KIND: dict[int, str] = {
    1: "expense",
    2: "income",
    3: "transfer_out",
    4: "receivable",
    5: "payable",
    6: "balance_adjustment",
    9: "fee",
    10: "discount",
    11: "reward",
    12: "interest",
}

PROJECT_NAMES: dict[str, str] = {"PROJECT_TRAVEL": "旅遊", "PROJECT_LIVING": "生活", "PROJECT_INVEST": "投資"}
SKIPPED_PROJECT = "NO_PROJECT"

# AHCategory.imageName -> emoji (design D25). System categories share 🧾.
ICON_BY_IMAGE: dict[str, str] = {
    "Food": "🍜",
    "Transport": "🚌",
    "Entertainment": "🎮",
    "Shopping": "🛍️",
    "Personal": "💇",
    "Medical": "🩺",
    "House": "🏠",
    "Family": "👨‍👩‍👧",
    "Living": "🧾",
    "Learning": "📚",
    "Others": "📦",
    "Income": "💰",
    "Transfer": "⇄",
    "Receivable": "🤝",
    "Payable": "🏦",
    "Adjustment": "⚖️",
    "Account_19": "🧾",  # fee
    "Account_11": "🧾",  # discount
    "Account_09": "🧾",  # reward
    "Account_13": "🧾",  # interest
}

# AHAccountGroup.name key -> account emoji (design D25).
ICON_BY_GROUP: dict[str, str] = {
    "APP_GROUP_CASH": "👛",
    "APP_GROUP_BANK": "🏦",
    "APP_GROUP_CREDIT_CARD": "💳",
    "APP_GROUP_STORED_VALUE_CARD": "🚌",
    "APP_GROUP_GIFT_VOUCHER": "🎟️",
    "APP_GROUP_REWARD": "⭐",
    "APP_GROUP_INSURANCE_POLICY": "🛡️",
    "APP_GROUP_SECURITIES": "📈",
    "APP_GROUP_CRYPTO_CURRENCY": "🪙",
    "ACCOUNT_OTHERS": "📁",
}

# MOZE UI order: 保留小數 / 四捨五入 / 無條件捨去 / 無條件進位; 4 is the 2-decimal variant, treated as round.
# Only 1 → round is evidence-backed (Task 11); 2, 3 and 4 stay provisional until 2b computes rewards.
ROUNDING_MAP: dict[int, str] = {0: "keep", 1: "round", 2: "floor", 3: "ceil", 4: "round"}
POSTING_MAP: dict[int, str] = {0: "after_transaction", 2: "after_window", 3: "manual"}
DUE_RULE_MAP: dict[int, str] = {0: "fixed_day", 1: "days_after_closing"}
REWARD_METHOD_MAP: dict[int, str] = {0: "percent", 1: "fixed"}
REWARD_WINDOW_MAP: dict[int, str] = {0: "statement_cycle"}
COLOR_MAP: dict[int, str] = {0: "red_green", 1: "green_red"}
KEYPAD_MAP: dict[int, str] = {1: "calculator", 0: "phone"}

# Field types: str, str? (nullable), num (Decimal), int, bool, dt (datetime), dt?, list, dict, dates
S, SN, NUM, INT, BOOL, DT, DTN, LIST, DICT = "str", "str?", "num", "int", "bool", "dt", "dt?", "list", "dict"
DATES = "dates"  # AHInstallment.dateInfo: a list, or an object keyed "0", "1", … — normalised to a list of datetimes

CLASS_FIELDS: dict[str, dict[str, str]] = {
    "AHAccount": {
        "identifier": S, "name": S, "mainCurrency": SN, "group": SN, "originalAmount": NUM, "isArchived": BOOL,
        "sequence": INT, "desc": S, "isBalanceIncluded": BOOL, "isCreditAccount": BOOL, "startDay": INT,
        "paymentDeadlineType": INT, "paymentDeadline": INT, "creditLimit": NUM, "combinedAccount": SN,
        "creditSharingID": SN, "autoPaidAccount": SN, "isCurrencyFeeEnabled": BOOL, "feePercentage": NUM,
        "feeCalculation": INT, "isRefundWithCurrencyFee": BOOL, "type": INT, "imageName": S, "cacheDate": DTN,
        "balanceInfo": DICT,
    },
    "AHAccountGroup": {"identifier": S, "name": S, "sequence": INT, "type": INT},
    "AHCategory": {
        "identifier": S, "name": S, "type": INT, "imageName": S, "colorHex": S, "isHidden": BOOL, "sequence": INT,
    },
    "AHClassification": {
        "identifier": S, "name": S, "category": SN, "defaultAccount": SN, "defaultProject": SN, "isHidden": BOOL,
        "sequence": INT, "imageName": S,
    },
    "AHProject": {"identifier": S, "name": S, "isArchived": BOOL, "sequence": INT},
    "AHTarget": {"identifier": S, "name": S, "type": INT, "isSettle": BOOL},
    # total = price + fee + bonus, except type 7 (balance adjustment): price is the balance after the
    # adjustment and total is the delta, which the importer posts as the entry amount
    "AHRecord": {
        "identifier": S, "type": INT, "price": NUM, "fee": NUM, "bonus": NUM, "total": NUM, "currency": SN,
        "currencyConversion": SN, "account": SN, "project": SN, "classification": SN, "target": SN,
        "bonusRewards": LIST, "date": DT, "chargeDate": DT, "name": S, "desc": S, "tags": S, "store": S,
        "feeName": S, "bonusName": S, "transferID": SN, "refundID": SN, "rewardID": SN, "rewardRecordID": SN,
        "packageID": SN, "relatedID": SN, "eventID": SN, "feeID": SN, "isTransferIn": BOOL, "isRefund": BOOL,
        "isEnabled": BOOL, "invoiceNumber": SN,
    },
    "AHTransfer": {"identifier": S, "outRecord": SN, "inRecord": SN, "exchangeRate": NUM},
    "AHPackage": {
        "identifier": S, "type": INT, "eventType": INT, "records": LIST, "name": S, "store": S, "desc": S,
    },
    "AHBonusReward": {
        "identifier": S, "accountID": S, "name": S, "desc": S, "type": INT, "rewardPercentage": NUM,
        "rewardAmount": NUM, "rewardPeriodType": INT, "rewardTimeType": INT, "rewardDelayDays": INT,
        "rewardMonth": INT, "rewardDay": INT, "rewardCalculation": INT, "totalRewardCalculation": INT,
        "rewardLimit": NUM, "totalRewardLimit": NUM, "rewardSharingID": SN, "spendThreshold": NUM,
        "totalSpendThreshold": NUM, "minCountThreshold": INT, "isBasic": BOOL, "rewardAccountID": S,
        "rewardProjectID": SN, "startDate": DT, "dueDate": DT, "isEnabled": BOOL, "sequence": INT,
    },
    "AHBonusRewardSharing": {"identifier": S, "bonusRewards": LIST},
    "AHCreditSharing": {"identifier": S, "accounts": LIST},
    "AHCurrencyConversion": {"recordID": S, "exchangeRate": NUM, "baseCurrencyCode": S, "targetCurrencyCode": S},
    "AHPeriod": {"identifier": S, "unit": INT, "days": INT, "times": INT, "type": INT, "startDate": DTN},
    "AHInstallment": {
        "identifier": S, "dayOfMonth": INT, "dateInfo": DATES, "times": INT, "total": NUM, "remainder": NUM,
    },
    "AHPreference": {
        "expenseIncomeColor": INT, "numberPadType": INT, "firstWeekday": INT, "mainCurrency": SN,
        "hideRewardsOnHome": BOOL, "isTotalBalanceAbbreviate": BOOL,
    },
    "AHAppConfig": {"timeZoneName": SN},
}

# Class -> BackupData attribute; the last five may be absent from a Realm and default to empty.
CLASS_ATTRS: dict[str, str] = {
    "AHAccount": "accounts",
    "AHAccountGroup": "groups",
    "AHCategory": "categories",
    "AHClassification": "classifications",
    "AHProject": "projects",
    "AHTarget": "targets",
    "AHRecord": "records",
    "AHTransfer": "transfers",
    "AHPackage": "packages",
    "AHBonusReward": "rules",
    "AHCurrencyConversion": "conversions",
    "AHBonusRewardSharing": "sharings",
    "AHCreditSharing": "credit_sharings",
    "AHPeriod": "periods",
    "AHInstallment": "installments",
}
OPTIONAL_CLASSES = ("AHBonusRewardSharing", "AHCreditSharing", "AHPeriod", "AHInstallment", "AHAppConfig")
PRIMARY_KEYS = {"AHCurrencyConversion": "recordID"}


@dataclass(frozen=True)
class BackupData:
    exported_at: datetime  # aware, Asia/Taipei
    accounts: list[dict]
    groups: list[dict]
    categories: list[dict]
    classifications: list[dict]
    projects: list[dict]
    targets: list[dict]
    records: list[dict]
    transfers: list[dict]
    packages: list[dict]
    rules: list[dict]
    sharings: list[dict]
    credit_sharings: list[dict]
    conversions: list[dict]
    periods: list[dict]
    installments: list[dict]
    preference: dict
    app_config: dict


# MOZE text field -> the String(n) column it is stored in; load_backup_json refuses longer text up front, so the
# import never reaches Postgres with it (a DataError would otherwise abort the import naming owner rows).
TEXT_COLUMNS: dict[str, dict[str, Any]] = {
    "AHAccount": {"identifier": Account.moze_id, "name": Account.name, "mainCurrency": Account.currency},
    "AHAccountGroup": {"identifier": AccountGroup.moze_id, "name": AccountGroup.name},
    "AHCategory": {"identifier": Category.moze_id, "name": Category.name},
    "AHClassification": {"identifier": Category.moze_id, "name": Category.name},
    "AHProject": {"identifier": Project.moze_id, "name": Project.name},
    "AHTarget": {"identifier": Counterparty.moze_id, "name": Counterparty.name},
    "AHRecord": {
        "identifier": LedgerEntry.moze_id, "currency": LedgerEntry.currency, "name": LedgerEntry.name,
        "store": LedgerEntry.merchant, "feeName": LedgerEntry.name, "bonusName": LedgerEntry.name,
        "invoiceNumber": LedgerEntry.invoice_number,
    },
    "AHPackage": {"identifier": EntryGroup.moze_id, "name": EntryGroup.name, "store": EntryGroup.merchant},
    "AHBonusReward": {"identifier": RewardRule.moze_id, "name": RewardRule.name},
    "AHPeriod": {"identifier": ScheduleDefinition.moze_id},
    "AHInstallment": {"identifier": ScheduleDefinition.moze_id},
    "AHPreference": {"mainCurrency": Preference.main_currency},
}


def _check_lengths(name: str, row: dict, where: str) -> None:
    """Refuse text longer than its column; the message names the row's identifier and field, never the text."""
    for field, column in TEXT_COLUMNS.get(name, {}).items():
        value = row.get(field)
        if isinstance(value, str) and value.strip() and len(value) > column.type.length:
            raise MozeImportError(f"{where}: field '{field}' is longer than {column.type.length} characters")


def _datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=None)


def _normalise(value: Any, kind: str, where: str, field: str) -> Any:
    nullable = kind.endswith("?")
    base = kind.rstrip("?")
    if value is None and nullable:
        return None
    if base == "str" and isinstance(value, str):
        return value
    if base == "int" and isinstance(value, int) and not isinstance(value, bool):
        return value
    if base == "bool" and isinstance(value, bool):
        return value
    if base == "num" and isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return value if isinstance(value, Decimal) else Decimal(str(value))
    if base == "dt" and (parsed := _datetime(value)) is not None:
        return parsed
    if base == "list" and isinstance(value, list):
        return value
    if base == "dict" and isinstance(value, dict):
        return value
    if base == "dates":
        items = value
        if isinstance(value, dict):
            try:
                items = [value[key] for key in sorted(value, key=int)]
            except ValueError:
                items = None
        if isinstance(items, list):
            parsed_items = [_datetime(item) for item in items]
            if all(item is not None for item in parsed_items):
                return parsed_items
    expected = {"str": "a string", "int": "an integer", "bool": "a boolean", "num": "a number",
                "dt": "an ISO date-time", "list": "a list", "dict": "an object",
                "dates": "a list of ISO date-times"}[base]
    raise MozeImportError(f"{where}: field '{field}' must be {expected}{' or null' if nullable else ''}")


def _rows(name: str, raw: Any) -> list[dict]:
    if not isinstance(raw, list):
        raise MozeImportError(f"class {name} must be a list of rows")
    key = PRIMARY_KEYS.get(name, "identifier")
    fields = CLASS_FIELDS[name]
    rows, seen = [], set()
    for index, row in enumerate(raw):
        if not isinstance(row, dict):
            raise MozeImportError(f"{name} row {index}: not an object")
        where = f"{name} '{row[key]}'" if isinstance(row.get(key), str) else f"{name} row {index}"
        for field in fields:
            if field not in row:
                raise MozeImportError(f"{where}: missing field '{field}'")
        normalised = dict(row)
        for field, kind in fields.items():
            normalised[field] = _normalise(row[field], kind, where, field)
        _check_lengths(name, normalised, where)
        if key in normalised:
            if normalised[key] in seen:
                raise MozeImportError(f"{where}: duplicate {key}")
            seen.add(normalised[key])
        rows.append(normalised)
    return rows


def parse_backup_doc(doc: Any) -> BackupData:
    """Validate a converter document already parsed from JSON; raises MozeImportError naming the problem."""
    if not isinstance(doc, dict) or not isinstance(doc.get("classes"), dict):
        raise MozeImportError("backup JSON has no 'classes' object")
    exported = _datetime(doc.get("exported_at"))
    if exported is None:
        raise MozeImportError("backup JSON has no valid 'exported_at'")
    classes = doc["classes"]
    missing = [name for name in CLASS_FIELDS if name not in classes and name not in OPTIONAL_CLASSES]
    if missing:
        raise MozeImportError("backup JSON is missing classes: " + ", ".join(missing))
    parsed = {name: _rows(name, classes.get(name, [])) for name in CLASS_FIELDS}
    if len(parsed["AHPreference"]) != 1:
        raise MozeImportError(f"backup JSON must hold exactly 1 AHPreference row, found {len(parsed['AHPreference'])}")
    return BackupData(
        exported_at=exported.replace(tzinfo=TAIPEI),
        **{attr: parsed[name] for name, attr in CLASS_ATTRS.items()},
        preference=parsed["AHPreference"][0],
        app_config=(parsed["AHAppConfig"] or [{}])[0],
    )


def load_backup_json(path: Path) -> BackupData:
    """Read the converter output; floats are parsed as Decimal so no binary rounding creeps in."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"), parse_float=Decimal)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MozeImportError(f"cannot read backup JSON {Path(path).name}: {exc}") from exc
    return parse_backup_doc(doc)


def record_kind(record: dict) -> str:
    """Ledger kind of an AHRecord; raises MozeImportError for an unknown type."""
    record_type = record["type"]
    if record_type not in RECORD_TYPE_TO_KIND:
        raise MozeImportError(f"AHRecord '{record['identifier']}': unknown record type {record_type}")
    if record["isRefund"]:
        return "refund"
    if record_type == 2:
        return "transfer_in" if record["isTransferIn"] else "transfer_out"
    return RECORD_TYPE_TO_KIND[record_type]


_TAG_SPLIT = re.compile(r"[,、;，；]|\s+(?=#)")
TAG_DELIMITERS = {",": ",", "、": "、", ";": ";", "，": ",", "；": ";"}


def split_tags(value: str) -> list[str]:
    """Split MOZE's tag string on , 、 ; (half or full width) or on whitespace before '#'; strip '#'; dedupe."""
    tags: list[str] = []
    for part in _TAG_SPLIT.split(value or ""):
        tag = part.strip().lstrip("#").strip()
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def tag_delimiter(values: Iterable[str]) -> str | None:
    """The delimiter seen in most multi-tag strings (' #' for space-separated hashtags), or None."""
    seen: Counter[str] = Counter()
    for value in values:
        if len(split_tags(value)) < 2:
            continue
        found = {label for char, label in TAG_DELIMITERS.items() if char in value}
        if re.search(r"\s+#", value):
            found.add(" #")
        seen.update(found)
    return seen.most_common(1)[0][0] if seen else None
