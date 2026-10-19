"""Composite bank statement split (國泰世華 綜合月對帳單): one PDF, one section per deposit account, built from the
word geometry of the text layer — no model call. Spec: docs/superpowers/specs/2026-10-18-composite-statement-split-design.md §4.

Every SplitError names a section key and a row index at most; never amounts, descriptions or account numbers.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import NoReturn

from worker.pdf import Line, Word

PERIOD = re.compile(r"綜合月對帳單 (\d{4}/\d{2}/\d{2}) ~ (\d{4}/\d{2}/\d{2})")
HOLDING = re.compile(r"^(活期存款|子帳戶|外匯綜合活期存款) (\S+) (\S+分行) (?:([A-Z]{3}) )?(-?[\d,]+(?:\.\d+)?)$")
SECTION = re.compile(r"^(?:外匯)?(?:綜合)?活期存款 帳號 (\S+)$")
DATE = re.compile(r"^\d{4}/\d{2}/\d{2}$")
CURRENCY = re.compile(r"^[A-Z]{3}$")
AMOUNT = re.compile(r"^-?\d{1,3}(?:,\d{3})*(?:\.\d+)?$")
PAGE_NO = re.compile(r"^\d{1,2}$")
HEADER = ("日期", "交易說明", "幣別", "提出", "存入", "餘額")
FOOTER = re.compile(r"^(註：|台幣存款 餘額|外幣存款 餘額折合台幣)")
AFTER_PAGE_NO = ("持有明細", "交易明細", "日期")
SECTION_KINDS = ("活期存款", "外匯活期存款", "外匯綜合活期存款")


class SplitError(RuntimeError):
    pass


@dataclass(frozen=True)
class Row:
    txn_date: date = field(repr=False)
    description: str = field(repr=False)
    currency: str
    withdrawal: Decimal | None = field(repr=False)
    deposit: Decimal | None = field(repr=False)
    balance: Decimal = field(repr=False)


@dataclass(frozen=True)
class Section:
    key: str
    currency: str
    period_start: date = field(repr=False)
    period_end: date = field(repr=False)
    closing_balance: Decimal = field(repr=False)
    opening_balance: Decimal = field(repr=False)
    rows: tuple[Row, ...] = field(repr=False)


@dataclass
class SplitReport:
    sections: int = 0
    anchor: int = 0
    skipped_keys: list[str] = field(default_factory=list)
    rows_per_key: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Geometry:
    x_in: float   # x0 of 存入
    x_bal: float  # x0 of 餘額


def _words(line: Line) -> tuple[Word, ...]:
    words = line.words
    if len(words) >= 2 and PAGE_NO.match(words[0].text) and (
            words[1].text in AFTER_PAGE_NO or DATE.match(words[1].text)
            or (words[1].text in SECTION_KINDS and len(words) > 2 and words[2].text == "帳號")):
        return words[1:]
    return words


def _date(text: str, where: str) -> date:
    try:
        y, m, d = (int(p) for p in text.split("/"))
        return date(y, m, d)
    except ValueError:
        raise SplitError(f"{where} has an impossible date") from None


def _amount(text: str, key: str, index: int) -> Decimal:
    if not AMOUNT.match(text):
        raise SplitError(f"{key}: row {index} has a non-numeric column")
    value = Decimal(text.replace(",", ""))
    if value < 0:
        raise SplitError(f"{key}: row {index} prints a negative amount")
    return value


def _holding_amount(text: str, key: str) -> Decimal:
    if not AMOUNT.match(text):
        raise SplitError(f"{key}: unreadable holdings amount")
    return Decimal(text.replace(",", ""))


def _suffix(number: str) -> str:
    tail = number.rsplit("*", 1)[-1]
    if "*" in number:
        return tail if re.fullmatch(r"\d{2,4}", tail) else "?"
    return number if re.fullmatch(r"\d{2,4}", number) else "?"  # a full unmasked number is never echoed


def _geometry(words: tuple[Word, ...]) -> Geometry:
    by_text = {w.text: w for w in words}
    return Geometry(x_in=by_text["存入"].x0, x_bal=by_text["餘額"].x0)


def _row(words: tuple[Word, ...], geometry: Geometry, suffix: str, index: int, hint: str | None = None) -> Row:
    key = hint or f"{suffix}/?"
    cur = next((i for i, w in enumerate(words) if i > 0 and CURRENCY.match(w.text)), None)
    if cur is None:
        raise SplitError(f"{key}: row {index} has no currency word")
    key = f"{suffix}/{words[cur].text}"
    description = " ".join(w.text for w in words[1:cur])
    out = inn = bal = None
    for w in words[cur + 1:]:
        value = _amount(w.text, key, index)
        if w.x1 <= geometry.x_in:
            if out is not None:
                _dup(key, index)
            out = value
        elif w.x1 <= geometry.x_bal:
            if inn is not None:
                _dup(key, index)
            inn = value
        else:
            if bal is not None:
                _dup(key, index)
            bal = value
    if bal is None or (out is None) == (inn is None):
        raise SplitError(f"{key}: row {index} does not have one amount column and one balance")
    return Row(_date(words[0].text, f"{key}: row {index}"), description, words[cur].text, out, inn, bal)


def _dup(key: str, index: int) -> NoReturn:
    raise SplitError(f"{key}: row {index} fills a column twice")


def split(lines: list[Line], declared: dict[str, bool]) -> tuple[list[Section], SplitReport]:
    period: tuple[date, date] | None = None
    holdings: dict[str, Decimal] = {}
    in_holdings = in_area = False
    geometry: Geometry | None = None
    open_suffix: str | None = None
    rows: dict[str, list[Row]] = {}  # "suffix/CUR" -> rows, in document order of first appearance
    headers: list[str] = []  # suffixes whose section header was seen, in order
    seen: dict[str, int] = {}  # suffix -> dated rows read under it (the row index in messages)
    dated_before_period = False  # a dated row ahead of the period line is still a row outside any section
    for line in lines:
        words = _words(line)
        if not words:
            continue
        if len(words) == 1 and PAGE_NO.match(words[0].text):
            continue  # a standalone page number (page bottom): neither closes a section nor adds a row
        text = " ".join(w.text for w in words)
        if period is None:
            match = PERIOD.search(text)
            if match:
                period = (_date(match.group(1), "statement period"), _date(match.group(2), "statement period"))
            elif DATE.match(words[0].text):
                dated_before_period = True  # reported after the missing-period check below
            continue
        if open_suffix is None and DATE.match(words[0].text):
            raise SplitError("dated row outside any section")  # never dropped, whatever block we are in
        if text == "持有明細":
            if in_area:
                raise SplitError("holdings block inside the transaction area")
            in_holdings = True
            continue
        if text == "交易明細":
            in_holdings, in_area = False, True
            continue
        if in_holdings:
            match = HOLDING.match(text)
            if match:
                key = f"{_suffix(match.group(2))}/{match.group(4) or 'TWD'}"
                if key in holdings:
                    raise SplitError(f"duplicate holdings row for {key}")
                holdings[key] = _holding_amount(match.group(5), key)
            continue
        if not in_area:
            continue
        if tuple(w.text for w in words) == HEADER:
            geometry = _geometry(words)
            continue
        match = SECTION.match(text)
        if match:
            open_suffix = _suffix(match.group(1))
            if open_suffix not in headers:
                headers.append(open_suffix)
            continue
        if DATE.match(words[0].text):
            if geometry is None:
                raise SplitError(f"{open_suffix}/?: dated row before any column header (no geometry)")
            same = [k for k in holdings if k.startswith(f"{open_suffix}/")]
            r = _row(words, geometry, open_suffix, seen.get(open_suffix, 0), same[0] if len(same) == 1 else None)
            seen[open_suffix] = seen.get(open_suffix, 0) + 1
            rows.setdefault(f"{open_suffix}/{r.currency}", []).append(r)  # one key per currency (rev 9)
            continue
        if open_suffix is not None and FOOTER.match(text):
            continue
        open_suffix = None  # any other line closes the section
    if period is None:
        raise SplitError("no statement period line")
    if dated_before_period:
        raise SplitError("dated row outside any section")
    report = SplitReport()
    sections: list[Section] = []
    for suffix in headers:
        if not any(k.startswith(f"{suffix}/") for k in rows):
            raise SplitError(f"{suffix}/?: section header without rows")
    skipped: list[str] = []
    for key in rows:  # every key is checked before any chain is read: the first error names the structural fault
        suffix = key.split("/")[0]
        if key not in holdings:
            raise SplitError(f"{key}: no holdings row")
        if key not in declared:
            if any(k.startswith(f"{suffix}/") for k in declared):
                # rev 9: a currency of a declared account the map leaves out (`50/USD` beside `50/JPY`) is skipped
                # with its rows, like its holdings row; an undeclared account's rows still fail the file
                skipped.append(key)
                continue
            raise SplitError(f"{key}: undeclared section with rows")
    for key, section_rows in rows.items():
        if key in skipped:
            continue
        sections.append(_section(key, section_rows, holdings[key], period))
        report.rows_per_key[key] = len(section_rows)
    for key, anchor_only in declared.items():
        if key not in holdings:
            raise SplitError(f"{key}: declared key has no holdings row")
        if not anchor_only and key not in report.rows_per_key:
            raise SplitError(f"{key}: declared key has no section")
        if anchor_only and key not in report.rows_per_key:  # anchor_only: a section is optional
            sections.append(Section(key, key.split("/")[1], period[0], period[1], holdings[key], holdings[key], ()))
            report.anchor += 1
    report.skipped_keys = list(dict.fromkeys(skipped + [key for key in holdings if key not in declared]))
    report.sections = len(sections)
    return sections, report


def _section(key: str, rows: list[Row], closing: Decimal, period: tuple[date, date]) -> Section:
    previous: Decimal | None = None
    for index, r in enumerate(rows):
        delta = (r.deposit or Decimal(0)) - (r.withdrawal or Decimal(0))
        if previous is not None and r.balance != previous + delta:
            raise SplitError(f"{key}: running balance breaks at row {index}")
        previous = r.balance
    if previous != closing:
        raise SplitError(f"{key}: last balance differs from the holdings closing balance")
    first = rows[0]
    opening = first.balance + (first.withdrawal or Decimal(0)) - (first.deposit or Decimal(0))
    return Section(key, first.currency, period[0], period[1], closing, opening, tuple(rows))


PARSER_ROWS = "composite-rows"
PARSER_ANCHOR = "composite-anchor"
# direction-specific, exact match on the classification view (spec §4): never a substring, never a direction change
KIND_TABLE = {"deposit": {"存款利息": "interest", "利息": "interest"}, "withdrawal": {"手續費": "fee", "轉帳手續費": "fee"}}


def _view(description: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", description).split())


def line_kind(row: Row) -> str:
    direction = "deposit" if row.deposit is not None else "withdrawal"
    amount = row.deposit if row.deposit is not None else row.withdrawal
    if amount == 0:
        return "unknown"
    return KIND_TABLE[direction].get(_view(row.description), direction)


def _money(value: Decimal) -> str:
    return format(value.normalize(), "f") if value != 0 else "0"


def _header(section: Section, *, file_id: int, account_id: int, parser: str, parser_version: str) -> dict:
    return {"file_id": file_id, "account_id": account_id, "kind": "bank", "parser": parser,
            "parser_version": parser_version, "currency": section.currency,
            "period_start": section.period_start.isoformat(), "period_end": section.period_end.isoformat(),
            "closing_date": section.period_end.isoformat(), "due_date": None,
            "opening_balance": _money(section.opening_balance), "statement_total": _money(section.closing_balance),
            "minimum_payment": None}


def bodies(sections: list[Section], *, file_id: int, targets: dict[str, dict], parser_version: str, masker,
           candidates: list[str]) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for section in sections:
        target = targets.get(section.key)
        if target is None:
            continue
        account_id = int(target["account_id"])
        if not section.rows:
            out.append((section.key, {**_header(section, file_id=file_id, account_id=account_id, parser=PARSER_ANCHOR,
                                                parser_version=parser_version),
                                      "lines": [], "raw": {"section": section.key, "anchor": True, "rows": 0}}))
            continue
        lines = []
        total = Decimal(0)
        for index, row in enumerate(section.rows):
            printed = (row.deposit if row.deposit is not None else -row.withdrawal)
            if printed != (row.deposit or Decimal(0)) - (row.withdrawal or Decimal(0)):
                raise SplitError(f"{section.key}: row {index} sign assertion failed")
            total += printed
            lines.append({"seq": index + 1, "txn_date": row.txn_date.isoformat(), "posted_date": row.txn_date.isoformat(),
                          "merchant_raw": masker.text(row.description, candidates)[:256],
                          "printed_amount": _money(printed), "foreign_amount": None, "foreign_currency": None,
                          "line_kind": line_kind(row), "installment_seq": None, "installment_total": None,
                          "is_subtotal": False})
        if total != section.closing_balance - section.opening_balance:
            raise SplitError(f"{section.key}: section sum assertion failed")
        out.append((section.key, {**_header(section, file_id=file_id, account_id=account_id, parser=PARSER_ROWS,
                                            parser_version=parser_version),
                                  "lines": lines, "raw": {"section": section.key, "anchor": False, "rows": len(lines)}}))
    return out
