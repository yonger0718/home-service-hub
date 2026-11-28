"""E-invoice schemas: import (spec §6) and owner actions (§7.5, §8). Strict: a field the importer does not know
(carrier name/number) is a 422 and nothing is written (privacy)."""
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, StrictBool, StrictInt, model_validator

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
    # 復原 of 解除連結: refuse with `einvoice_changed` once the invoice is linked again (AGENT-638).
    if_unlinked: StrictBool = False

    @model_validator(mode="after")
    def _one_target(self) -> "EinvoiceLinkIn":
        if (self.entry_id is None) == (self.group_id is None):
            raise ValueError("target: give exactly one of entry_id or group_id")
        return self


class EinvoiceReplaceIn(BaseModel):
    """`entry_id` / `group_id` (at most one) is a precondition for 復原: the link must still be on that target, else
    `einvoice_changed` and nothing changes (AGENT-638)."""
    model_config = Strict
    with_invoice_id: Int32
    entry_id: Int32 | None = None
    group_id: Int32 | None = None

    @model_validator(mode="after")
    def _at_most_one_target(self) -> "EinvoiceReplaceIn":
        if self.entry_id is not None and self.group_id is not None:
            raise ValueError("target: give at most one of entry_id or group_id")
        return self


class EinvoiceDismissIn(BaseModel):
    model_config = Strict
    mute_seller: bool = False
    # 復原 of 放回: refuse with `einvoice_changed` unless the invoice is still `unmatched` (AGENT-638).
    if_unmatched: StrictBool = False


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
    """The owner's e-invoice preferences (write scope): any of the three, merged into the stored block. `enabled` and
    the read-only status fields are not accepted (extra=forbid)."""
    model_config = Strict
    remind_unmatched: StrictBool | None = None
    date_window_days: StrictInt | None = Field(None, ge=0, le=7)
    mute_prompt_after: StrictInt | None = Field(None, ge=1, le=10)

    @model_validator(mode="after")
    def _some_field(self):
        if not self.model_fields_set:
            raise ValueError("send at least one of remind_unmatched, date_window_days, mute_prompt_after")
        nulls = [k for k in self.model_fields_set if getattr(self, k) is None]
        if nulls:
            raise ValueError(f"{nulls[0]} cannot be null")
        return self


class EinvoicePreferencesOut(BaseModel):
    model_config = Strict
    enabled: bool
    remind_unmatched: bool
    date_window_days: int
    mute_prompt_after: int
    #: Read only: the newest invoice's `first_seen_at` (null before the first import).
    last_import_at: datetime | None
    #: Read only: invoices the matcher linked (`linked_by = 'auto'`).
    auto_linked_count: int
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
    seller_dismissed: int  # the owner's 不用記帳 of this seller (tax id); 0 without one and on questions (D11)


class EinvoicePickerOut(BaseModel):
    model_config = Strict
    entry_eligible: bool
    group_id: int | None
    group_eligible: bool
    invoices: list[EinvoiceSummaryOut]


class EinvoiceReplaceOut(BaseModel):
    """換一張發票: the invoice the link left (`previous`) and the one it moved to (`current`)."""
    model_config = Strict
    previous: EinvoiceStateOut
    current: EinvoiceStateOut
