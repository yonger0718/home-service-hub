from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from hashlib import sha256

from app.services.statements.merchant import normalise

QUANTUM = Decimal("0.0001")
# Lines whose printed amount is positive on the issuer side (charges / debits) carry the ledger sign −.
CARD_DEBIT = {"purchase", "fee", "interest", "installment", "balance_adjustment"}
CARD_CREDIT = {"payment", "refund", "reward"}
BANK_CREDIT = {"deposit", "interest", "transfer_in", "refund", "reward"}
BANK_DEBIT = {"withdrawal", "fee", "transfer_out", "purchase", "payment"}
WINDOW = timedelta(days=5)
MAX_PERIOD = timedelta(days=62)
# The closing date may move off the account's closing day by a weekend/holiday shift; a misread period (the due
# date taken for the closing date, a calendar month on a mid-month card) lands farther away and goes to review.
CLOSING_TOLERANCE_DAYS = 3


class SignError(ValueError):
    pass


@dataclass(frozen=True)
class LineIn:
    seq: int
    txn_date: date | None
    posted_date: date
    merchant_raw: str
    printed_amount: Decimal
    foreign_amount: Decimal | None
    foreign_currency: str | None
    line_kind: str
    installment_seq: int | None
    installment_total: int | None
    is_subtotal: bool = False


@dataclass(frozen=True)
class DerivedLine:
    line: LineIn
    merchant_norm: str
    flow_amount: Decimal
    canonical_key: str
    logical_key: str

    @property
    def seq(self) -> int:
        return self.line.seq


@dataclass(frozen=True)
class Header:
    period_start: date
    period_end: date
    closing_date: date | None
    due_date: date | None
    opening_balance: Decimal | None
    statement_total: Decimal
    minimum_payment: Decimal | None
    currency: str


@dataclass
class GuardrailResult:
    ok: bool
    checks: dict[str, bool] = field(default_factory=dict)
    detail: dict = field(default_factory=dict)


def _q(value: Decimal) -> Decimal:
    return Decimal(value).quantize(QUANTUM, rounding=ROUND_HALF_UP)


def flow_amount(kind: str, line_kind: str, printed: Decimal) -> Decimal:
    printed = _q(printed)
    if printed == 0 and line_kind != "unknown":
        raise SignError(f"{line_kind}: zero amount")
    if kind == "card":
        if (line_kind in CARD_DEBIT and printed < 0) or (line_kind in CARD_CREDIT and printed > 0):
            raise SignError(f"card {line_kind}: sign {printed}")
        return _q(-printed)
    if (line_kind in BANK_CREDIT and printed < 0) or (line_kind in BANK_DEBIT and printed > 0):
        raise SignError(f"bank {line_kind}: sign {printed}")
    return printed


def _canonical(line: LineIn, norm: str, flow: Decimal) -> str:
    parts = (line.posted_date.isoformat(), line.txn_date.isoformat() if line.txn_date else "", str(flow),
             str(_q(line.foreign_amount)) if line.foreign_amount is not None else "", line.foreign_currency or "",
             line.line_kind, str(line.installment_seq or ""), str(line.installment_total or ""), norm)
    return sha256("|".join(parts).encode()).hexdigest()


def derive_lines(kind: str, lines: list[LineIn]) -> list[DerivedLine]:
    out: list[DerivedLine] = []
    occurrences: dict[tuple[date, Decimal], int] = {}
    for line in sorted(lines, key=lambda l: l.seq):
        if line.is_subtotal:
            continue
        flow = flow_amount(kind, line.line_kind, line.printed_amount)
        norm = normalise(line.merchant_raw)
        index = occurrences.get((line.posted_date, flow), 0)
        occurrences[(line.posted_date, flow)] = index + 1
        out.append(DerivedLine(line=line, merchant_norm=norm, flow_amount=flow,
                               canonical_key=_canonical(line, norm, flow),
                               logical_key=f"{line.posted_date.isoformat()}|{flow}|{index}"))
    return out


def closing_offset(closing: date, closing_day: int) -> int:
    """Signed days from the nearest scheduled closing date — `closing_day` in the previous, same or next month,
    clamped to that month's length (day 31 closes on the 30th of a 30-day month) — to `closing`."""
    offsets = []
    for shift in (-1, 0, 1):
        year, month0 = divmod(closing.year * 12 + closing.month - 1 + shift, 12)
        scheduled = date(year, month0 + 1, min(closing_day, monthrange(year, month0 + 1)[1]))
        offsets.append((closing - scheduled).days)
    return min(offsets, key=abs)


def guardrails(kind: str, header: Header, account_currency: str, lines: list[DerivedLine], *,
               closing_day: int | None) -> GuardrailResult:
    """`closing_day`: the account's closing day; when set, the closing date (`closing_date`, else `period_end`) must
    lie within CLOSING_TOLERANCE_DAYS of it (check `closing_day`); None (no cycle configured) skips the check."""
    result = GuardrailResult(ok=True)
    total = _q(header.statement_total)
    sum_flow = _q(sum((l.flow_amount for l in lines), Decimal(0)))
    opening = header.opening_balance
    if kind == "card":
        if opening is None:
            result.detail["opening_assumed_zero"] = True
            opening = Decimal(0)
        expected = _q(opening - sum_flow)
    else:
        if opening is None:
            result.checks["opening_present"] = False
            result.ok = False
            expected = None
        else:
            expected = _q(opening + sum_flow)
    result.checks["equation"] = expected is not None and expected == total
    result.detail.update(sum_flow=str(sum_flow), expected_total=str(expected) if expected is not None else None, statement_total=str(total))
    result.checks["currency"] = header.currency == account_currency
    result.checks["period_length"] = timedelta(0) <= header.period_end - header.period_start <= MAX_PERIOD
    lo, hi = header.period_start - WINDOW, header.period_end + WINDOW
    result.checks["dates_in_window"] = all(lo <= l.line.posted_date <= hi for l in lines)
    result.checks["installments"] = all(
        l.line.installment_seq is None or (l.line.installment_total is not None and 1 <= l.line.installment_seq <= l.line.installment_total)
        for l in lines)
    result.checks["line_count"] = len(lines) <= 2000
    if closing_day is not None:
        closing = header.closing_date or header.period_end
        offset = closing_offset(closing, closing_day)
        result.checks["closing_day"] = abs(offset) <= CLOSING_TOLERANCE_DAYS
        result.detail["closing_day"] = {"expected_day": closing_day, "closing_date": closing.isoformat(),
                                        "days_off": offset}
    result.ok = result.ok and all(result.checks.values())
    return result
