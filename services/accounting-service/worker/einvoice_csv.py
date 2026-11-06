"""Parse the owner's monthly e-invoice CSV export (stdlib only).

Privacy: the carrier name/number columns are discarded as each row is split and exist on no type. ParseError
messages are the code only, never row content. Nothing is logged.

Fail closed: the worker sends one merged import per run, and the API rejects the whole batch (422) if any invoice
violates EinvoiceIn, so the parser mirrors those bounds and a bad file fails alone. Seller name (1..128), status
(1..16), tax id (^[0-9A-Za-z]{0,16}$), item name (1..256) and more than 500 items raise row_shape (a bad tax id is
row_shape too). Amounts must match ^-?[0-9]+(\.[0-9]+)?$ with at most 16 integer and 4 fraction digits (Numeric(20,4))
or raise amount.
"""
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import re

_NUMBER = re.compile(r"^[A-Z]{2}[0-9]{8}$")
_AMOUNT = re.compile(r"-?([0-9]+)(?:\.([0-9]+))?")
_TAX_ID = re.compile(r"[0-9A-Za-z]{0,16}")
_MAX_ITEMS = 500


class ParseError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ParsedItem:
    seq: int
    name: str
    subtotal: Decimal


@dataclass(frozen=True)
class ParsedInvoice:
    number: str
    issue_date: date
    seller_tax_id: str
    seller_name: str
    total_amount: Decimal
    status: str
    items: tuple[ParsedItem, ...]


def _fields(line: str) -> list[str]:
    f = line.split("|")
    if f and f[-1] == "":
        f.pop()
    return f


def _amount(text: str) -> Decimal:
    m = _AMOUNT.fullmatch(text.strip())
    if not m or len(m.group(1)) > 16 or len(m.group(2) or "") > 4:
        raise ParseError("amount")
    return Decimal(m.group(0))


def _date(text: str) -> date:
    if not re.fullmatch(r"[0-9]{8}", text):
        raise ParseError("date")
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError:
        raise ParseError("date") from None


def parse(data: bytes) -> list[ParsedInvoice]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ParseError("encoding") from None
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2 or not lines[0].startswith("表頭=M|") or not lines[1].startswith("明細=D|"):
        raise ParseError("header")
    # keyed by (number, date); dict order: a replaced invoice moves to the end
    out: dict[tuple[str, date], tuple[ParsedInvoice, list[ParsedItem]]] = {}
    cur: tuple[ParsedInvoice, list[ParsedItem]] | None = None
    cur_key: tuple[str, date] | None = None
    for line in lines[2:]:
        f = _fields(line)
        kind = f[0]
        if kind == "M":
            if len(f) != 9:
                raise ParseError("row_shape")
            _, _, _, d, tax, name, number, total, status = f  # f[1], f[2] (carrier) are dropped here
            if not _NUMBER.match(number):
                raise ParseError("number")
            inv = ParsedInvoice(number, _date(d), tax, name, _amount(total), status, ())
            if not 1 <= len(name) <= 128 or not 1 <= len(status) <= 16 or not _TAX_ID.fullmatch(tax):
                raise ParseError("row_shape")
            cur_key = (number, inv.issue_date)
            out.pop(cur_key, None)
            cur = (inv, [])
            out[cur_key] = cur
        elif kind == "D":
            if cur is None:
                raise ParseError("orphan_item")
            if len(f) < 4:
                raise ParseError("row_shape")
            if f[1] != cur[0].number:
                raise ParseError("item_number")
            item_name = "|".join(f[3:])
            if not 1 <= len(item_name) <= 256 or len(cur[1]) >= _MAX_ITEMS:
                raise ParseError("row_shape")
            cur[1].append(ParsedItem(len(cur[1]) + 1, item_name, _amount(f[2])))
        else:
            raise ParseError("row_shape")
    return [ParsedInvoice(i.number, i.issue_date, i.seller_tax_id, i.seller_name, i.total_amount, i.status,
                          tuple(items)) for i, items in out.values()]


def to_payload(invoice: ParsedInvoice, *, source_drive_id: str) -> dict:
    return {
        "number": invoice.number,
        "issue_date": invoice.issue_date.isoformat(),
        "seller_tax_id": invoice.seller_tax_id,
        "seller_name": invoice.seller_name,
        "total_amount": str(invoice.total_amount),
        "status": invoice.status,
        "source_drive_id": source_drive_id,
        "items": [{"seq": it.seq, "name": it.name, "subtotal": str(it.subtotal)} for it in invoice.items],
    }
