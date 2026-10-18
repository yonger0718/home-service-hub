"""Composite bank statement split (國泰世華 綜合月對帳單): one PDF, one section per deposit account, built from the
word geometry of the text layer — no model call. Spec: docs/superpowers/specs/2026-10-18-composite-statement-split-design.md §4.

Every SplitError names a section key and a row index at most; never amounts, descriptions or account numbers.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from worker.pdf import Line, Word

PERIOD = re.compile(r"綜合月對帳單 (\d{4}/\d{2}/\d{2}) ~ (\d{4}/\d{2}/\d{2})")
HOLDING = re.compile(r"^(活期存款|子帳戶|外匯綜合活期存款) (\S+) (\S+分行) (?:([A-Z]{3}) )?(-?[\d,]+(?:\.\d+)?)$")
SECTION = re.compile(r"^(?:活期存款|外匯綜合活期存款) 帳號 (\S+)$")
DATE = re.compile(r"^\d{4}/\d{2}/\d{2}$")
CURRENCY = re.compile(r"^[A-Z]{3}$")
AMOUNT = re.compile(r"^-?\d{1,3}(?:,\d{3})*(?:\.\d+)?$")
PAGE_NO = re.compile(r"^\d{1,2}$")
HEADER = ("日期", "交易說明", "幣別", "提出", "存入", "餘額")
FOOTER = re.compile(r"^(註：|台幣存款 餘額|外幣存款 餘額折合台幣)")
AFTER_PAGE_NO = ("持有明細", "交易明細", "活期存款", "外匯綜合活期存款", "日期")


class SplitError(RuntimeError):
    pass


@dataclass(frozen=True)
class Row:
    txn_date: date
    description: str
    currency: str
    withdrawal: Decimal | None
    deposit: Decimal | None
    balance: Decimal


@dataclass(frozen=True)
class Section:
    key: str
    currency: str
    period_start: date
    period_end: date
    closing_balance: Decimal
    opening_balance: Decimal
    rows: tuple[Row, ...]


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
            words[1].text in AFTER_PAGE_NO or DATE.match(words[1].text) or "綜合月對帳單" in words[1].text):
        return words[1:]
    return words


def _date(text: str) -> date:
    y, m, d = (int(p) for p in text.split("/"))
    return date(y, m, d)


def _amount(text: str, key: str, index: int) -> Decimal:
    if not AMOUNT.match(text):
        raise SplitError(f"{key}: row {index} has a non-numeric column")
    value = Decimal(text.replace(",", ""))
    if value < 0:
        raise SplitError(f"{key}: row {index} prints a negative amount")
    return value


def _suffix(number: str) -> str:
    tail = number.rsplit("*", 1)[-1]
    return tail if tail else number


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
            out = value if out is None else _dup(key, index)
        elif w.x1 <= geometry.x_bal:
            inn = value if inn is None else _dup(key, index)
        else:
            bal = value if bal is None else _dup(key, index)
    if bal is None or (out is None) == (inn is None):
        raise SplitError(f"{key}: row {index} does not have one amount column and one balance")
    return Row(_date(words[0].text), description, words[cur].text, out, inn, bal)


def _dup(key: str, index: int):
    raise SplitError(f"{key}: row {index} fills a column twice")


def split(lines: list[Line], declared: dict[str, bool]) -> tuple[list[Section], SplitReport]:
    period: tuple[date, date] | None = None
    holdings: dict[str, Decimal] = {}
    in_holdings = in_area = False
    geometry: Geometry | None = None
    open_suffix: str | None = None
    rows: dict[str, list[Row]] = {}  # suffix -> rows, in document order of first appearance
    for line in lines:
        words = _words(line)
        if not words:
            continue
        text = " ".join(w.text for w in words)
        if period is None:
            match = PERIOD.search(text)
            if match:
                period = (_date(match.group(1)), _date(match.group(2)))
            continue
        if text == "持有明細":
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
                holdings[key] = _amount(match.group(5), key, 0) if not match.group(5).startswith("-") else Decimal(
                    match.group(5).replace(",", ""))
            continue
        if not in_area:
            continue
        if tuple(w.text for w in words) == HEADER:
            geometry = _geometry(words)
            continue
        match = SECTION.match(text)
        if match:
            open_suffix = _suffix(match.group(1))
            rows.setdefault(open_suffix, [])
            continue
        if DATE.match(words[0].text):
            if open_suffix is None:
                raise SplitError("dated row outside any section")
            if geometry is None:
                raise SplitError(f"{open_suffix}/?: dated row before any column header (no geometry)")
            same = [k for k in holdings if k.startswith(f"{open_suffix}/")]
            rows[open_suffix].append(
                _row(words, geometry, open_suffix, len(rows[open_suffix]), same[0] if len(same) == 1 else None))
            continue
        if open_suffix is not None and FOOTER.match(text):
            continue
        open_suffix = None  # any other line closes the section
    if period is None:
        raise SplitError("no statement period line")
    report = SplitReport()
    sections: list[Section] = []
    for suffix, section_rows in rows.items():
        if not section_rows:
            raise SplitError(f"{suffix}/?: section header without rows")
        currencies = {r.currency for r in section_rows}
        if len(currencies) != 1:
            raise SplitError(f"{suffix}/?: rows of mixed currency")
        key = f"{suffix}/{section_rows[0].currency}"
        if key not in holdings:
            raise SplitError(f"{key}: no holdings row")
        if key not in declared:
            raise SplitError(f"{key}: undeclared section with rows")
        if declared[key]:
            raise SplitError(f"{key}: rows under an anchor_only key")
        sections.append(_section(key, section_rows, holdings[key], period))
        report.rows_per_key[key] = len(section_rows)
    for key, anchor_only in declared.items():
        if key not in holdings:
            raise SplitError(f"{key}: declared key has no holdings row")
        if not anchor_only and key not in report.rows_per_key:
            raise SplitError(f"{key}: declared section has no section")
        if anchor_only:
            sections.append(Section(key, key.split("/")[1], period[0], period[1], holdings[key], holdings[key], ()))
            report.anchor += 1
    report.skipped_keys = [key for key in holdings if key not in declared]
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
