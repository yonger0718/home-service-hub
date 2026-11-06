"""E-invoice schemas: import (spec §6) and owner actions (§7.5, §8). Strict: a field the importer does not know
(carrier name/number) is a 422 and nothing is written (privacy)."""
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .ledger import EinvoiceItemOut
from .statements import Strict
from .writes import Int32

INVOICE_NUMBER = r"^[A-Z]{2}[0-9]{8}$"


class EinvoiceItemIn(BaseModel):
    model_config = Strict
    seq: int = Field(ge=1, le=1000)
    name: str = Field(min_length=1, max_length=256)
    subtotal: Decimal = Field(max_digits=20, decimal_places=4)  # the column's numeric(20, 4)


class EinvoiceIn(BaseModel):
    model_config = Strict
    number: str = Field(pattern=INVOICE_NUMBER)
    issue_date: date
    seller_tax_id: str = Field(pattern=r"^[0-9A-Za-z]{0,16}$")
    seller_name: str = Field(min_length=1, max_length=128)
    total_amount: Decimal = Field(max_digits=20, decimal_places=4)  # the column's numeric(20, 4)
    status: str = Field(min_length=1, max_length=16)
    source_drive_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    items: list[EinvoiceItemIn] = Field(max_length=500)

    @model_validator(mode="after")
    def _unique_seq(self) -> "EinvoiceIn":
        seqs = [i.seq for i in self.items]
        if len(seqs) != len(set(seqs)):
            raise ValueError("items: seq must be unique within an invoice")
        return self


class EinvoiceImportIn(BaseModel):
    model_config = Strict
    invoices: list[EinvoiceIn] = Field(max_length=5000)


class EinvoiceMatchOut(BaseModel):
    model_config = Strict
    ran: bool
    processed: int
    linked: int
    ambiguous: int
    near: int
    unmatched: int
    unchanged: int
    retry_later: int  # a link insert lost to a concurrent ledger write; state left as is (R6)


class EinvoiceImportOut(BaseModel):
    model_config = Strict
    inserted: int
    updated: int
    unchanged: int
    voided: int
    unknown_status: int
    match: EinvoiceMatchOut


# --- owner actions (spec §7.5, §8) ---------------------------------------------------------------------------

class EinvoiceLinkIn(BaseModel):
    model_config = Strict
    entry_id: Int32 | None = None
    group_id: Int32 | None = None

    @model_validator(mode="after")
    def _one_target(self) -> "EinvoiceLinkIn":
        if (self.entry_id is None) == (self.group_id is None):
            raise ValueError("target: give exactly one of entry_id or group_id")
        return self


class EinvoiceDismissIn(BaseModel):
    model_config = Strict
    mute_seller: bool = False


class EinvoiceStateOut(BaseModel):
    model_config = Strict
    id: int
    state: str
    dismiss_reason: str | None
    linked_by: str | None
    entry_id: int | None
    group_id: int | None


class EinvoiceMuteOut(BaseModel):
    model_config = Strict
    seller_tax_id: str
    seller_name: str | None  # the latest invoice's
    created_at: datetime


class EinvoicePreferencesIn(BaseModel):
    model_config = Strict
    remind_unmatched: bool


class EinvoicePreferencesOut(BaseModel):
    model_config = Strict
    enabled: bool
    remind_unmatched: bool
    version: int


class RunbookRefIn(BaseModel):
    model_config = Strict
    number: str = Field(pattern=INVOICE_NUMBER)
    issue_date: date


class RunbookDismissIn(BaseModel):
    model_config = Strict
    invoices: list[RunbookRefIn] = Field(min_length=1, max_length=500)


class RunbookDismissOut(BaseModel):
    model_config = Strict
    dismissed: int
    not_found: list[int]  # indexes into `invoices`: no such invoice, or void


# --- reads: inbox cards, target list, dismissed list (spec §8, §9) --------------------------------------------------

class EinvoiceSummaryOut(BaseModel):
    model_config = Strict
    id: int
    number: str
    issue_date: date
    seller_name: str
    seller_tax_id: str
    total_amount: Decimal
    status_raw: str
    state: str
    items: list[EinvoiceItemOut]


class EinvoiceDismissedOut(EinvoiceSummaryOut):
    dismiss_reason: Literal["owner", "runbook"]


class EinvoiceTargetOut(BaseModel):
    model_config = Strict
    kind: Literal["entry", "group"]
    id: int
    account_name: str
    date: date
    amount: Decimal  # the net (entry plus fee / discount children; a group's expense / receivable members)
    currency: str
    category: str | None
    name: str | None


class InboxEinvoice(EinvoiceSummaryOut):
    state: Literal["ambiguous", "near", "unmatched"]
    candidates: list[EinvoiceTargetOut]
    offer_mute: bool


class EinvoicePickerOut(BaseModel):
    model_config = Strict
    entry_eligible: bool
    group_id: int | None
    group_eligible: bool
    invoices: list[EinvoiceSummaryOut]
