"""國泰證券日對帳單 parser (spec 2026-10-29-cathay-daily-statement §4): pypdfium2 text -> payload dict.

Pure: no network, no Drive. Anything outside the §4.1 grammar raises StatementError with a short code; the message never
carries statement content.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from io import BytesIO

SUPPORTED = ("集買", "集賣", "沖買", "沖賣")
BUY = ("集買", "沖買")
POINTS_PREFIX = "(小樹點數) "

_HEADER_RE = re.compile(r"^證券日對帳單 (\d+)年 (\d{1,2})月 (\d{1,2})日$")
_COLUMNS_RE = re.compile(r"^成交日期 交割日期( \S+)*$")
_DATE_LINE_RE = re.compile(r"^(\d{4})/(\d{2})/(\d{2}) (\d{4})/(\d{2})/(\d{2}) (\S+) (\S+) (\S+)$")
_ITEM_COLUMNS = "商品名稱 類別 成交股數 單價 成交金額 手續費 交易稅"
_HOLDINGS_COLUMNS = "代碼 股票名稱 當日收盤價 集保股數 集保市值 融資股數 融券股數"
_CATEGORY_RE = re.compile(r"^[一-鿿]{2}$")
_UINT_RE = re.compile(r"^(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)$")
_PRICE_RE = re.compile(r"^(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?$")
_ORDER_RE = re.compile(r"^[A-Za-z0-9]{7}$")
_CODE_RE = re.compile(r"^[0-9A-Z]{4,6}$")
_FILENAME_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


class StatementError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ParsedStatement:
    trade_date: date
    settlement_date: date
    net_amount: Decimal
    lines: list[dict] = field(repr=False)
    totals: dict = field(repr=False)
    holdings: list[dict] = field(repr=False)
    unsupported: bool = False


def extract_text(data: bytes, password: str | None) -> str:
    import pypdfium2 as pdfium  # noqa: PLC0415

    doc = pdfium.PdfDocument(BytesIO(data), password=password)
    try:
        pages = []
        for index in range(len(doc)):
            page = doc[index]
            textpage = page.get_textpage()
            try:
                pages.append(textpage.get_text_range())
            finally:
                textpage.close()
                page.close()
        return "\n".join(pages)
    finally:
        doc.close()


def _uint(token: str, code: str) -> int:
    if not _UINT_RE.match(token):
        raise StatementError(code)
    return int(token.replace(",", ""))


def _signed(token: str, code: str) -> int:
    if token.startswith("-"):
        return -_uint(token[1:], code)
    return _uint(token, code)


def _parse_dates(lines: list[str]) -> tuple[date, date, int, int]:
    """Returns (trade, settlement, net, index of the first line after the data line)."""
    for i, line in enumerate(lines):
        m = _HEADER_RE.match(line)
        if m:
            break
    else:
        raise StatementError("header")
    if i + 2 >= len(lines) or not _COLUMNS_RE.match(lines[i + 1]):
        raise StatementError("header")
    d = _DATE_LINE_RE.match(lines[i + 2])
    if not d:
        raise StatementError("date_line")
    try:
        header = date(int(m.group(1)) + 1911, int(m.group(2)), int(m.group(3)))
        trade = date(int(d.group(1)), int(d.group(2)), int(d.group(3)))
        settle = date(int(d.group(4)), int(d.group(5)), int(d.group(6)))
    except ValueError:
        raise StatementError("date_line") from None
    if header != trade:
        raise StatementError("date_line")
    _uint(d.group(7), "date_line")
    _uint(d.group(8), "date_line")
    return trade, settle, _signed(d.group(9), "date_line"), i + 3


def _is_item_start(line: str) -> bool:
    tokens = line.split(" ")
    return len(tokens) >= 7 and bool(_CATEGORY_RE.match(tokens[-6]))


def _parse_item(lines: list[str], i: int) -> dict:
    tokens = lines[i].split(" ")
    name, category = " ".join(tokens[:-6]), tokens[-6]
    qty_t, price_t, amount_t, fee_t, tax_t = tokens[-5:]
    quantity = _uint(qty_t, "item_grammar")
    if quantity <= 0 or not _PRICE_RE.match(price_t):
        raise StatementError("item_grammar")
    price = Decimal(price_t.replace(",", ""))
    if price <= 0:
        raise StatementError("item_grammar")
    amount, fee, tax = (_uint(t, "item_grammar") for t in (amount_t, fee_t, tax_t))
    if i + 3 >= len(lines):
        raise StatementError("item_grammar")
    net_t, line3, line4 = lines[i + 1], lines[i + 2], lines[i + 3]
    net = _signed(net_t, "item_grammar")
    points = line3.startswith(POINTS_PREFIX)
    if points:
        line3 = line3[len(POINTS_PREFIX):]
    parts = line3.split(" ")
    if len(parts) != 5 or parts[:4] != ["0", "0", "0", "0"]:
        raise StatementError("line3")
    if not _ORDER_RE.match(parts[4]):
        raise StatementError("line3")
    if line4 != "0":
        raise StatementError("line4")
    if abs(quantity * price - amount) >= 1:
        raise StatementError("amount")
    if category in BUY:
        if tax != 0:
            raise StatementError("buy_tax")
        expected = -(amount + fee)
    else:
        expected = amount - fee - tax
    if net != expected:
        raise StatementError("net_identity")
    return {"name": name, "category": category, "quantity": quantity, "price": price, "amount": amount, "fee": fee,
            "tax": tax, "net": net, "order_no": parts[4], "points": points}


def _parse_footer(lines: list[str]) -> tuple[dict, int, int]:
    """lines start at the 總合計 line. Returns (totals with net, 淨收付金額, index of the holdings column header)."""
    tokens = lines[0].split(" ")
    if len(tokens) != 4 or tokens[0] != "總合計":
        raise StatementError("totals")
    amount, fee, tax = (_uint(t, "totals") for t in tokens[1:])
    try:
        end = lines.index(_HOLDINGS_COLUMNS)
    except ValueError:
        raise StatementError("holdings") from None
    rest = lines[1:end]
    # `<totals net>` then `淨收付金額 <net>`; the label's number may sit on the next line.
    if len(rest) == 3 and rest[1] == "淨收付金額":
        rest = [rest[0], f"淨收付金額 {rest[2]}"]
    if len(rest) != 2:
        raise StatementError("totals")
    label = rest[1].split(" ")
    if len(label) != 2 or label[0] != "淨收付金額":
        raise StatementError("totals")
    net = _signed(rest[0], "totals")
    return {"amount": amount, "fee": fee, "tax": tax, "net": net}, _signed(label[1], "totals"), end


def _parse_holdings(rows: list[str]) -> list[dict]:
    out = []
    for row in rows:
        t = row.split(" ")
        if len(t) < 7 or not _CODE_RE.match(t[0]):
            raise StatementError("holdings")
        if not _PRICE_RE.match(t[-5]):
            raise StatementError("holdings")
        shares = _uint(t[-4], "holdings")
        for token in t[-3:]:
            _uint(token, "holdings")
        out.append({"code": t[0], "name": " ".join(t[1:-5]), "shares": shares})
    return out


def parse_statement(text: str) -> ParsedStatement:
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    trade, settle, header_net, i = _parse_dates(lines)
    if i < len(lines) and lines[i] == _ITEM_COLUMNS:
        i += 1
    try:
        total_at = next(k for k in range(i, len(lines)) if lines[k].startswith("總合計"))
    except StopIteration:
        raise StatementError("totals") from None
    items: list[dict] = []
    unsupported = False
    while i < total_at:
        if not _is_item_start(lines[i]):
            raise StatementError("stray_line")
        category = lines[i].split(" ")[-6]
        if category not in SUPPORTED:
            unsupported = True  # margin lines carry columns §4.1 leaves open: the rest of the section is not read
            break
        items.append(_parse_item(lines, i))
        i += 4
    if i > total_at:
        raise StatementError("stray_line")
    totals, footer_net, holdings_at = _parse_footer(lines[total_at:])
    holdings = _parse_holdings(lines[total_at + holdings_at + 1:])
    if unsupported:
        items = []
    else:
        for key in ("amount", "fee", "tax"):
            if sum(item[key] for item in items) != totals[key]:
                raise StatementError("totals")
        if not (sum(item["net"] for item in items) == header_net == footer_net == totals["net"]):
            raise StatementError("net_total")
    if unsupported and not (header_net == footer_net == totals["net"]):
        raise StatementError("net_total")
    return ParsedStatement(trade, settle, Decimal(header_net), items, {k: Decimal(v) for k, v in totals.items()},
                           holdings, unsupported)


def to_payload(st: ParsedStatement, *, drive_file_id: str, sha256: str, file_name: str) -> dict:
    warnings: list[str] = []
    m = _FILENAME_DATE_RE.match(file_name)
    if m and m.group(1) != st.trade_date.isoformat():
        warnings.append("filename_date_differs")
    if st.unsupported:
        warnings.append("unsupported_category")
    lines = [{"name": ln["name"], "category": ln["category"], "quantity": ln["quantity"], "price": str(ln["price"]),
              "amount": str(ln["amount"]), "fee": str(ln["fee"]), "tax": str(ln["tax"]), "net": str(ln["net"]),
              "order_no": ln["order_no"], "points": ln["points"]} for ln in st.lines]
    return {"source": {"drive_file_id": drive_file_id, "sha256": sha256, "file_name": file_name},
            "trade_date": st.trade_date.isoformat(), "settlement_date": st.settlement_date.isoformat(),
            "net_amount": str(st.net_amount), "lines": lines,
            "totals": {k: str(v) for k, v in st.totals.items()},
            "holdings": [dict(h) for h in st.holdings], "warnings": warnings}
