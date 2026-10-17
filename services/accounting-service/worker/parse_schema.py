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
    "from the printed 分期 n/N. Use line_kind unknown when a row cannot be classified. Do not invent rows; if the "
    "text is not a statement, return zero lines and explain in notes. On a card statement every negative row is a "
    "credit: a payment received is payment, a cash-back or points credit is reward, and any other reversal (退款, 取消, "
    "分期調整 / 轉分期 reversing a purchase) is refund; never label a negative card row balance_adjustment."
)


def json_schema() -> dict:
    return StatementParse.model_json_schema()


VERSION_MAX = 64  # RevisionIn.parser_version / statement_file.parser_version are String(64)


def version(cli_version: str, model: str, ocr_fingerprint: str) -> str:
    """One bounded version for every file (R5.4): the full CLI version, model id, instruction, schema and OCR
    fingerprint fold into a 12-hex digest behind a display prefix cut to 12+24 characters (max 61 in all), so no
    configuration can exceed the 64-character bound and truncated prefixes cannot collide."""
    digest = hashlib.sha256("\n".join([cli_version, model, INSTRUCTION, json.dumps(json_schema(), sort_keys=True),
                                       ocr_fingerprint]).encode()).hexdigest()[:12]
    out = f"claude-cli-{cli_version[:12]}-{model[:24]}-{digest}"
    assert len(out) <= VERSION_MAX
    return out


