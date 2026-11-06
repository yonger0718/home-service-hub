"""E-invoice import schemas (spec §6). Strict: a field the importer does not know (carrier name/number) is a 422 and
nothing is written (privacy)."""
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

from .statements import Strict


class EinvoiceItemIn(BaseModel):
    model_config = Strict
    seq: int = Field(ge=1, le=1000)
    name: str = Field(min_length=1, max_length=256)
    subtotal: Decimal


class EinvoiceIn(BaseModel):
    model_config = Strict
    number: str = Field(pattern=r"^[A-Z]{2}[0-9]{8}$")
    issue_date: date
    seller_tax_id: str = Field(pattern=r"^[0-9A-Za-z]{0,16}$")
    seller_name: str = Field(min_length=1, max_length=128)
    total_amount: Decimal
    status: str = Field(min_length=1, max_length=16)
    source_drive_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    items: list[EinvoiceItemIn] = Field(max_length=500)


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


class EinvoiceImportOut(BaseModel):
    model_config = Strict
    inserted: int
    updated: int
    unchanged: int
    voided: int
    unknown_status: int
    match: EinvoiceMatchOut
