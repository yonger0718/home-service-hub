"""Parse the owner's monthly e-invoice CSV export (stdlib only).

Privacy: the carrier name/number columns are discarded as each row is split and exist on no type. ParseError
messages are the code only, never row content. Nothing is logged.
"""
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re

_NUMBER = re.compile(r"^[A-Z]{2}[0-9]{8}$")


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
    try:
        value = Decimal(text.strip())
    except InvalidOperation:
        raise ParseError("amount") from None
    if not value.is_finite():
        raise ParseError("amount")
    return value


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
            cur[1].append(ParsedItem(len(cur[1]) + 1, "|".join(f[3:]), _amount(f[2])))
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
