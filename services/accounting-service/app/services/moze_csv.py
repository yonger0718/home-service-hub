"""Parse a MOZE 16-column CSV export into validated rows (no database access)."""

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation

MOZE_COLUMNS = (
    "帳戶", "幣種", "記錄類型", "主類別", "子類別", "金額", "手續費", "折扣",
    "名稱", "商家", "日期", "時間", "專案", "描述", "標籤", "對象",
)

OPENING_RECORD_TYPE = "初始金額"

RECORD_TYPE_TO_KIND = {
    "支出": "expense",
    "收入": "income",
    "轉出": "transfer_out",
    "轉入": "transfer_in",
    "應收款項": "receivable",
    "應付款項": "payable",
    "餘額調整": "balance_adjustment",
    "手續費": "fee",
    "折扣": "discount",
    "紅利回饋": "reward",
    "利息": "interest",
    "退款": "refund",
}


class MozeImportError(Exception):
    """The file cannot be imported; the message names the offending column, row or account."""


@dataclass(frozen=True)
class MozeRow:
    row_no: int  # spreadsheet row number: header is row 1, first record is row 2
    account: str
    currency: str  # the row's own 幣種; may differ from the account currency
    kind: str
    main_category: str
    sub_category: str
    amount: Decimal
    fee: Decimal
    discount: Decimal
    name: str | None
    merchant: str | None
    entry_date: date
    entry_time: time | None
    project: str | None
    description: str | None
    tags: tuple[str, ...]
    counterparty: str | None


@dataclass(frozen=True)
class AccountSpec:
    name: str
    currency: str
    opening_balance: Decimal


@dataclass(frozen=True)
class ParsedFile:
    rows: tuple[MozeRow, ...]  # every non-opening record, in file order
    accounts: dict[str, AccountSpec]  # every account named in the file, in first-appearance order
    row_count: int  # number of data records, including 初始金額 rows


def _check_header(header: list[str]) -> None:
    missing = [c for c in MOZE_COLUMNS if c not in header]
    unexpected = [c for c in header if c not in MOZE_COLUMNS]
    if missing or unexpected:
        parts = []
        if missing:
            parts.append("missing columns: " + ", ".join(missing))
        if unexpected:
            parts.append("unexpected columns: " + ", ".join(unexpected))
        raise MozeImportError("unsupported MOZE CSV header; " + "; ".join(parts))
    if tuple(header) != MOZE_COLUMNS:
        raise MozeImportError("unsupported MOZE CSV header; columns are out of order")


def _decimal(value: str, column: str, row_no: int) -> Decimal:
    if value.strip() == "":
        return Decimal("0")
    try:
        result = Decimal(value.strip())
    except InvalidOperation:
        raise MozeImportError(f"row {row_no}: invalid {column} '{value}'") from None
    if not result.is_finite():
        raise MozeImportError(f"row {row_no}: invalid {column} '{value}'")
    return result


def _date(value: str, row_no: int) -> date:
    try:
        return datetime.strptime(value.strip(), "%Y/%m/%d").date()
    except ValueError:
        raise MozeImportError(f"row {row_no}: invalid 日期 '{value}' (expected YYYY/MM/DD)") from None


def _time(value: str, row_no: int) -> time | None:
    if value.strip() == "":
        return None
    try:
        return datetime.strptime(value.strip(), "%H:%M").time()
    except ValueError:
        raise MozeImportError(f"row {row_no}: invalid 時間 '{value}' (expected HH:MM)") from None


def _tags(value: str) -> tuple[str, ...]:
    tags = (part.strip().lstrip("#").strip() for part in value.split(";"))
    return tuple(tag for tag in tags if tag)


def _text(value: str) -> str | None:
    return value if value.strip() else None


def parse_moze_csv(data: bytes) -> ParsedFile:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise MozeImportError("file is not UTF-8 encoded") from None
    reader = csv.reader(io.StringIO(text, newline=""))
    header = next(reader, None)
    if header is None:
        raise MozeImportError("file is empty")
    _check_header(header)

    rows: list[MozeRow] = []
    openings: dict[str, list[tuple[int, str, Decimal]]] = {}
    first_seen: dict[str, int] = {}
    row_count = 0

    for row_no, record in enumerate(reader, start=2):
        if not record:
            continue
        row_count += 1
        if len(record) != len(MOZE_COLUMNS):
            raise MozeImportError(f"row {row_no}: expected {len(MOZE_COLUMNS)} fields, got {len(record)}")
        f = dict(zip(MOZE_COLUMNS, record))
        account, currency, record_type = f["帳戶"], f["幣種"], f["記錄類型"]

        first_seen.setdefault(account, row_no)

        if record_type == OPENING_RECORD_TYPE:
            openings.setdefault(account, []).append((row_no, currency, _decimal(f["金額"], "金額", row_no)))
            continue

        kind = RECORD_TYPE_TO_KIND.get(record_type)
        if kind is None:
            raise MozeImportError(f"row {row_no}: unknown 記錄類型 '{record_type}'")

        rows.append(
            MozeRow(
                row_no=row_no,
                account=account,
                currency=currency,
                kind=kind,
                main_category=f["主類別"].strip(),
                sub_category=f["子類別"].strip(),
                amount=_decimal(f["金額"], "金額", row_no),
                fee=_decimal(f["手續費"], "手續費", row_no),
                discount=_decimal(f["折扣"], "折扣", row_no),
                name=_text(f["名稱"]),
                merchant=_text(f["商家"]),
                entry_date=_date(f["日期"], row_no),
                entry_time=_time(f["時間"], row_no),
                project=_text(f["專案"]),
                description=_text(f["描述"]),
                tags=_tags(f["標籤"]),
                counterparty=_text(f["對象"]),
            )
        )

    problems = []
    for account in first_seen:
        found = openings.get(account, [])
        if not found:
            problems.append(f"account '{account}' has no 初始金額 row (first used on row {first_seen[account]})")
        elif len(found) > 1:
            row_list = ", ".join(str(row_no) for row_no, _, _ in found)
            problems.append(f"account '{account}' has {len(found)} 初始金額 rows: {row_list}")
    if problems:
        raise MozeImportError("; ".join(problems))

    # The account currency is the 幣種 of its single 初始金額 row; other rows may differ (converted later).
    accounts = {
        account: AccountSpec(account, openings[account][0][1], openings[account][0][2])
        for account in first_seen
    }
    return ParsedFile(rows=tuple(rows), accounts=accounts, row_count=row_count)
