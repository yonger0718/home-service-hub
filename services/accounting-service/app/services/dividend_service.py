"""國泰 dividend receipts on the bank statement (portfolio integration spec §4.6; dividends plan rev 2.3, R1–R11): a
dividend-labelled deposit line (`matching.is_dividend_line`) of a settlement-source bank statement is matched against
the portfolio's confirmed receipts, and `book_dividend` (`reconciliation_actions.settlement_actions`) books it as an
income entry under one `settlement_booking` row of kind `dividend`.

- Match (R1): a receipt is a candidate of a line when it is unreserved (no `kind='dividend'` booking holds its
  fingerprint or item), of exactly the line's flow (the feed's `amount` is already net of 匯費 / 補充保費: never
  deducted again) and within `WINDOW_DAYS` of the line's posted date. Both sides unique within the statement → a
  `dividend` case; a line with several candidates, or whose receipt another line could also take → `ambiguous` +
  `dividend_ambiguous` (the owner may pick one, the policy never does); a line without candidates beside exactly one
  near receipt of another amount → its plain `line_unmatched` case gains `dividend_mismatch` with the nearest receipt
  and its signed delta (never a candidate, never a booking); anything else keeps its ordinary case.
- Feed (R3): `reconcile` makes the one window read (`fetch_for`, statement period ± `WINDOW_DAYS`) next to the
  settlement read, through the same cached client, only when `applies`; `book_dividend.prepare` re-reads the line's
  window (`read_window`). An unavailable, incomplete or other-currency answer leaves the lines in their ordinary case.
- Re-check (R11): `recheck_statement_ids` names every statement holding an open dividend-family or plain
  dividend-line case; `trade_settlement_service.recheck_targets` unions it in.

Nothing here commits; the reconciler and the engine own the transaction."""
from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.services.portfolio_client import Dividend
from app.services.statements import matching

KIND = "dividend"
AMBIGUOUS_HINT = "dividend_ambiguous"
MISMATCH_HINT = "dividend_mismatch"
FAMILY_HINTS = (AMBIGUOUS_HINT, MISMATCH_HINT)
WINDOW_DAYS = 3
ITEM_SOURCE = "dividend"
UNAVAILABLE_KEY = "dividend_unavailable_code"
COUNT_KEYS = ("dividend", "dividend_ambiguous", "dividend_mismatch")
_Q = Decimal("0.0001")

logger = logging.getLogger(__name__)


# --- facts ------------------------------------------------------------------------------------------------------------


def _money(value: Decimal) -> str:
    return f"{Decimal(value).quantize(_Q)}"


def facts_of(d: Dividend) -> dict:
    """The one JSON shape of a dividend candidate and of `settlement_booking.facts` (dates ISO, amount 4-dp)."""
    return {"fingerprint": d.fingerprint, "id": d.id, "symbol": d.symbol, "receipt_date": d.receipt_date.isoformat(),
            "amount": _money(d.amount), "currency": d.currency}


def same_facts(booked: dict, now: dict | None) -> bool:
    """The receipt as listed equals the receipt as re-read: fingerprint, amount, receipt date and currency."""
    return now is not None and (booked["fingerprint"], Decimal(booked["amount"]), booked["receipt_date"],
                                booked["currency"]) == (now["fingerprint"], Decimal(now["amount"]),
                                                        now["receipt_date"], now["currency"])


def nearest_of(line: matching.Line, d: Dividend) -> dict:
    """The `dividend_mismatch` hint's receipt: `delta = line.flow − amount`, signed, 4 dp."""
    return {"id": d.id, "symbol": d.symbol, "receipt_date": d.receipt_date.isoformat(), "amount": _money(d.amount),
            "delta": f"{(line.flow - d.amount).quantize(_Q):+.4f}"}


# --- the assignment ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Assignment:
    unique: dict[int, Dividend]  # line id → its one receipt (both sides unique within the statement)
    ambiguous: dict[int, tuple[Dividend, ...]]  # line id → its candidates, (receipt_date, id) order
    near: dict[int, Dividend] = field(default_factory=dict)  # line id → the one near receipt of another amount


def _within(line: matching.Line, d: Dividend) -> bool:
    return abs((d.receipt_date - line.posted_date).days) <= WINDOW_DAYS


def assign(lines: Sequence[matching.Line], rows: Sequence[Dividend], reserved: set[str],
           rules: matching.Rules) -> Assignment:
    """R1 over the dividend lines of `lines` (pure): a receipt is a candidate of a line when unreserved, of the line's
    exact flow and within `WINDOW_DAYS`; a line with one candidate that no other line could take is `unique`, any
    other line with candidates `ambiguous`; a line without candidates whose only near receipt (unreserved, within the
    window, of another amount, no line's candidate) is one gets `near`."""
    lines = [ln for ln in lines if matching.is_dividend_line(ln, rules)]
    free = [r for r in rows if r.fingerprint not in reserved]
    candidates = {ln.id: tuple(sorted((r for r in free if _within(ln, r) and r.amount.quantize(_Q) == ln.flow.quantize(_Q)),
                                      key=lambda r: (r.receipt_date, r.id))) for ln in lines}
    # both sides unique within the statement: a receipt that two lines could take decides neither
    takers = Counter(row.fingerprint for found in candidates.values() for row in found)
    unique = {lid: found[0] for lid, found in candidates.items() if len(found) == 1 and takers[found[0].fingerprint] == 1}
    ambiguous = {lid: found for lid, found in candidates.items() if found and lid not in unique}  # (receipt_date, id) order
    near = {}
    for ln in lines:
        if candidates[ln.id]:
            continue
        close = [r for r in free if _within(ln, r) and r.amount.quantize(_Q) != ln.flow.quantize(_Q)
                 and r.fingerprint not in takers]
        if len(close) == 1:
            near[ln.id] = close[0]
    return Assignment(unique=unique, ambiguous=ambiguous, near=near)
