"""The parser's only output contract: StatementParse (decimal strings, caps) and the fixed instruction."""
from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MONEY = r"^-?\d{1,15}(\.\d{1,4})?$"
LINE_KINDS = ("purchase", "refund", "payment", "fee", "interest", "reward", "installment", "balance_adjustment",
              "deposit", "withdrawal", "transfer_in", "transfer_out", "unknown")


class ParsedLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seq: int = Field(ge=1, le=100000)
    txn_date: date | None = None
    posted_date: date
    merchant_raw: str = Field(max_length=256)
    printed_amount: str = Field(pattern=MONEY)
    foreign_amount: str | None = Field(default=None, pattern=MONEY)
    foreign_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    line_kind: Literal[LINE_KINDS]  # type: ignore[valid-type]
    installment_seq: int | None = Field(default=None, ge=1, le=120)
    installment_total: int | None = Field(default=None, ge=1, le=120)
    is_subtotal: bool = False


class StatementParse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["card", "bank"]
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    period_start: date = Field(description=(
        "First day of the statement period. Card: the day after the previous closing date (not the 1st of a calendar "
        "month unless the statement says so)."))
    period_end: date = Field(description="Last day of the statement period; on a card statement it equals closing_date.")
    closing_date: date | None = Field(default=None, description=(
        "The statement/closing date (結帳日 / 帳單結帳日); never the payment due date."))
    due_date: date | None = Field(default=None, description="The payment due date (繳款截止日); never the closing date.")
    opening_balance: str | None = Field(default=None, pattern=MONEY)
    statement_total: str = Field(pattern=MONEY)
    minimum_payment: str | None = Field(default=None, pattern=MONEY)
    lines: list[ParsedLine] = Field(max_length=2000)
    notes: str | None = Field(default=None, max_length=500)


INSTRUCTION = (
    "You are given, on stdin, the masked text (and extracted tables) of ONE Taiwanese bank or credit-card statement. "
    "Fill the StatementParse schema and nothing else. Rules: kind is card for a credit-card statement, bank for a "
    "bank account statement. Dates are ISO (YYYY-MM-DD); convert ROC years (民國, e.g. 115/09/30) by adding 1911. "
    "Amounts are decimal strings exactly as printed, without thousands separators or currency symbols. On a card "
    "statement a charge (purchase, fee, interest, installment, balance_adjustment) is a positive number and a credit "
    "(payment, refund, reward) is a negative number; on a bank statement a deposit/transfer_in/interest/refund/reward is "
    "positive and a withdrawal/transfer_out/fee/purchase/payment is negative. A printed row whose amount is 0 must use "
    "line_kind unknown (never purchase/fee/etc.). For a bank statement opening_balance is REQUIRED: the balance before "
    "the first transaction (上期餘額 / 期初餘額); statement_total is the closing balance (本期餘額 / 期末餘額). For a "
    "card statement statement_total is 本期應繳總額 and opening_balance is the previous statement's amount due (上期應繳總額) "
    "when printed, and a payment received (本期已繳金額/繳款) is a payment line. Header dates: closing_date is the "
    "statement/closing date (結帳日 / 帳單結帳日) and period_end equals closing_date; due_date is the payment due date "
    "(繳款截止日); never use the due date as the closing date. A card statement's period runs from the day after the "
    "previous closing date to the closing date, not a calendar month unless the statement says so. One line per printed "
    "transaction row in print order with seq starting at 1; mark subtotal or total rows with is_subtotal true. Keep "
    "merchant_raw as printed (masked tokens such as [NUM…1234] stay as they are). For foreign-currency rows set "
    "foreign_amount and foreign_currency (ISO code). For installment rows set installment_seq and installment_total "
    "from the printed 分期 n/N. The input may begin with a [hint] line from the operator stating the account's closing "
    "day; when it does, the printed header date whose day of month is that day (within 3 days) is closing_date and "
    "period_end, and a later header date is the payment due date, whatever the layout suggests. If no printed date "
    "falls within 3 days of that day, do not invent or shift one: report the dates as printed. Use line_kind unknown when a row "
    "cannot be classified. Every printed transaction row "
    "with a non-zero amount becomes a line, even when it has no description (header amounts such as 最低應繳金額 are "
    "not transaction rows). A transaction row holding only a transaction date, a posting date and a small positive "
    "amount, printed in a separate block beside the foreign-currency rows and sharing the dates of one of them, is "
    "that purchase's foreign transaction service fee (國外交易服務費): emit it as a fee line with merchant_raw "
    "國外交易服務費 and posted_date the second date; when that reading is uncertain keep the row as line_kind unknown. "
    "Never drop such a row, and never add or change a row to make the amounts balance. A transaction row may end with "
    "the card's last four digits, a four-digit authorisation code, a country code and a currency code (and, for a "
    "foreign row, the foreign amount and its date): the amount is the first amount after the whole merchant description "
    "(digits inside the merchant, such as 7-11, stay in merchant_raw; a thousands separator stays inside the amount) — "
    "those trailing digit groups are never the amount, never a reason to output 0 and never a reason to drop the row; "
    "the card and authorisation digits may appear as one masked token such as [NUM…1814]. "
    "Do not invent rows; if the "
    "text is not a statement, return zero lines and explain in notes. On a card statement every negative row is a "
    "credit: a payment received is payment, a cash-back or points credit is reward, and any other reversal (退款, 取消, "
    "分期調整 / 轉分期 reversing a purchase) is refund; never label a negative card row balance_adjustment."
)


def hint(closing_day: int | None) -> str:
    """The operator's per-file preamble for the parser (fix 2026-10-17: 2025 HSBC layouts print the due date above
    the closing date without labels and the parser kept picking the due date): the account's closing day, or the
    empty string. Not part of the parser version — the instruction's reference to it is. The hint chooses among
    PRINTED dates only; when the card's cycle moved and no printed date is near the configured day, the model
    reports the printed dates and the closing-day guardrail refuses the revision (the owner sees the mismatch)."""
    if closing_day is None:
        return ""
    return (f"[hint] This card statement closes on day {closing_day} of the month: the printed header date whose day "
            f"of month is {closing_day} (within 3 days) is the closing date (結帳日) and period_end; a later header date "
            "is the payment due date (繳款截止日). If no printed date is within 3 days of that day, report the dates as "
            "printed — never invent or shift one.\n\n")


def json_schema() -> dict:
    return StatementParse.model_json_schema()


VERSION_MAX = 64  # RevisionIn.parser_version / statement_file.parser_version are String(64)


def version(cli_version: str, model: str, ocr_fingerprint: str, mask_fingerprint: str) -> str:
    """One bounded version for every file (R5.4): the full CLI version, model id, instruction, schema, OCR fingerprint
    and mask fingerprint (mask.FINGERPRINT: the rules deciding what the parser receives) fold into a 12-hex digest
    behind a display prefix cut to 12+24 characters (max 61 in all), so no configuration can exceed the 64-character
    bound and truncated prefixes cannot collide."""
    digest = hashlib.sha256("\n".join([cli_version, model, INSTRUCTION, json.dumps(json_schema(), sort_keys=True),
                                       ocr_fingerprint, mask_fingerprint]).encode()).hexdigest()[:12]
    out = f"claude-cli-{cli_version[:12]}-{model[:24]}-{digest}"
    assert len(out) <= VERSION_MAX
    return out


