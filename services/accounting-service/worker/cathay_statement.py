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
_COLUMNS = "成交日期 交割日期 預收款 留置款 客戶淨收(+)/付(-)金額"
_POINTS_COLUMNS = "客戶淨收(+)/付(-)點數"
_DATES = r"(\d{4})/(\d{2})/(\d{2}) (\d{4})/(\d{2})/(\d{2})"
_DATE_LINE_RE = re.compile(rf"^{_DATES} (\S+) (\S+)(?: (\S+))?$")
_ITEM_COLUMNS = [
    "商品名稱 類別 成交股數 單價 成交金額 手續費 交易稅", "客戶應收付額", "預收款/留置款", "融資自備款/", "融券擔保品",
    "融資金額/", "融券保證金", "融券手續費 債息利息 委託書號",
]
_HOLDINGS_COLUMNS = "代碼 股票名稱 當日收盤價 集保股數 集保市值 融資股數 融券股數 理財資訊"
_HOLDINGS_SUFFIX = " 停損/停利通知 個股分析 我要下單"
_FOOTNOTE = "*表示交易稅為千分之一"
_NUM = r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)"
_SNUM = rf"[+-]?{_NUM}"
_TRADE_TOTALS_RE = re.compile(rf"^買進總計： ({_NUM}) 賣出總計： ({_NUM}) 本期買\+賣合計： ({_NUM}) 當沖損益： ({_SNUM})$")
_CASH_TOTALS_RE = re.compile(rf"^預收款： ({_NUM}) 留置款： ({_NUM}) 應收付金額： ({_SNUM}) 現沖損益： ({_SNUM})$")
_NET_LINE_RE = re.compile(rf"^淨收付金額： ({_SNUM})$")
_POINTS_LINE_RE = re.compile(rf"^淨收付點數： ({_SNUM})$")
_HOLDINGS_TOTAL_RE = re.compile(rf"^集保市值總計： {_NUM}(?:\.[0-9]+)?$")
_HOLDING_PRICE_RE = re.compile(rf"^[▲▼]?{_NUM}(?:\.[0-9]+)?$")
_VALUE_RE = re.compile(rf"^{_NUM}(?:\.[0-9]+)?$")
_CATEGORY_RE = re.compile(r"^[\u4e00-\u9fff]{2}$")
_UINT_RE = re.compile(rf"^{_NUM}$")
_SINT_RE = re.compile(rf"^{_SNUM}$")
_PRICE_RE = re.compile(rf"^{_NUM}(?:\.[0-9]+)?$")
_ORDER_RE = re.compile(r"^[A-Za-z0-9]{7}$")
_CODE_RE = re.compile(r"^[0-9A-Z]{4,6}$")
_FILENAME_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
_LINK_PREFIX = "智慧選股 "


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
    if not _SINT_RE.match(token):
        raise StatementError(code)
    return int(token.replace(",", ""))


def _parse_header(lines: list[str]) -> tuple[date, date, int, int | None, int]:
    """Returns (trade, settlement, cash net, points net or None, index of the first line after the header)."""
    m = _HEADER_RE.match(lines[0]) if lines else None
    if not m or len(lines) < 6:
        raise StatementError("header")
    greeting, intro, fee = lines[1:4]
    if not (greeting.startswith("親愛的 ") and greeting.endswith(" 您好:")
            and intro.startswith("以下是您") and intro.endswith(" 的客戶日對帳單。")
            and fee.startswith("您的") and fee.endswith("元起。") and lines[4] == _COLUMNS):
        raise StatementError("header")
    points = lines[5] == _POINTS_COLUMNS
    at = 6 if points else 5
    d = _DATE_LINE_RE.match(lines[at])
    if not d or (d.group(9) is None) != points or d.group(7) != "0" or d.group(8) != "0":
        raise StatementError("date_line")
    try:
        header = date(int(m.group(1)) + 1911, int(m.group(2)), int(m.group(3)))
        trade = date(int(d.group(1)), int(d.group(2)), int(d.group(3)))
        settle = date(int(d.group(4)), int(d.group(5)), int(d.group(6)))
    except ValueError:
        raise StatementError("date_line") from None
    if header != trade:
        raise StatementError("date_line")
    if points:
        if at + 2 >= len(lines):
            raise StatementError("date_line")
        return trade, settle, _signed(lines[at + 1], "date_line"), _signed(lines[at + 2], "date_line"), at + 3
    return trade, settle, _signed(d.group(9), "date_line"), None, at + 1


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


@dataclass
class _Footer:
    totals: dict
    line_net: int
    buy_total: int
    sell_total: int
    both_total: int
    cash_net: int
    net: int
    points: int | None
    margin_ok: bool
    next_index: int


def _parse_footer(lines: list[str], at: int) -> _Footer:
    """lines[at] is the 總合計 line."""
    if at + 8 > len(lines):
        raise StatementError("totals")
    tokens = lines[at].split(" ")
    if len(tokens) != 4 or tokens[0] != "總合計":
        raise StatementError("totals")
    amount, fee, tax = (_uint(t, "totals") for t in tokens[1:])
    line_net = _signed(lines[at + 1], "totals")
    margin_ok = lines[at + 2] == "0 0 0 0" and lines[at + 3] == "0"
    trade = _TRADE_TOTALS_RE.match(lines[at + 4])
    cash = _CASH_TOTALS_RE.match(lines[at + 5])
    net = _NET_LINE_RE.match(lines[at + 6])
    if not (trade and cash and net):
        raise StatementError("totals")
    i = at + 7
    points = None
    pm = _POINTS_LINE_RE.match(lines[i])
    if pm:
        points = int(pm.group(1).replace(",", ""))
        i += 1
    if i >= len(lines) or lines[i] != _FOOTNOTE:
        raise StatementError("totals")
    to_int = lambda t: int(t.replace(",", ""))  # noqa: E731
    return _Footer({"amount": amount, "fee": fee, "tax": tax, "net": line_net}, line_net, to_int(trade.group(1)),
                   to_int(trade.group(2)), to_int(trade.group(3)), to_int(cash.group(3)), to_int(net.group(1)),
                   points, margin_ok, i + 1)


def _parse_holdings(lines: list[str], at: int) -> list[dict]:
    """lines[at:] is the optional holdings table then the link line."""
    if len(lines) - at == 1 and lines[at].startswith(_LINK_PREFIX):
        return []
    if lines[at] != _HOLDINGS_COLUMNS or not lines[-1].startswith(_LINK_PREFIX) or len(lines) - at < 3:
        raise StatementError("holdings")
    if not _HOLDINGS_TOTAL_RE.match(lines[-2]):
        raise StatementError("holdings")
    out = []
    for row in lines[at + 1:-2]:
        if row.endswith(_HOLDINGS_SUFFIX):
            row = row[: -len(_HOLDINGS_SUFFIX)]
        t = row.split(" ")
        if len(t) != 7 or not _CODE_RE.match(t[0]) or not _HOLDING_PRICE_RE.match(t[2]):
            raise StatementError("holdings")
        shares = _uint(t[3], "holdings")
        if not _VALUE_RE.match(t[4]):
            raise StatementError("holdings")
        _uint(t[5], "holdings")
        _uint(t[6], "holdings")
        out.append({"code": t[0], "name": t[1], "shares": shares})
    return out


def parse_statement(text: str) -> ParsedStatement:
    lines = [ln.strip() for ln in text.split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    if any(not ln for ln in lines):
        raise StatementError("stray_line")
    trade, settle, header_net, header_points, i = _parse_header(lines)
    if lines[i:i + len(_ITEM_COLUMNS)] != _ITEM_COLUMNS:
        raise StatementError("header")
    i += len(_ITEM_COLUMNS)
    try:
        total_at = next(k for k in range(i, len(lines)) if lines[k].startswith("總合計 "))
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
    foot = _parse_footer(lines, total_at)
    holdings = _parse_holdings(lines, foot.next_index)
    if (foot.points is None) != (header_points is None) or foot.points != header_points:
        raise StatementError("net_total")
    # 應收付金額 is the signed sum of every line; 淨收付金額 is the cash part, without the lines paid with points
    if foot.line_net != foot.cash_net or header_net != foot.net:
        raise StatementError("net_total")
    if unsupported:
        items = []
        if foot.net != foot.cash_net:
            raise StatementError("net_total")
    else:
        points_net = sum(item["net"] for item in items if item["points"])
        if header_points is None and any(item["points"] for item in items):
            raise StatementError("net_total")
        if foot.net != foot.cash_net - points_net or sum(item["net"] for item in items) != foot.cash_net:
            raise StatementError("net_total")
        for key in ("amount", "fee", "tax"):
            if sum(item[key] for item in items) != foot.totals[key]:
                raise StatementError("totals")
        buy = sum(it["amount"] for it in items if it["category"] in BUY)
        sell = sum(it["amount"] for it in items if it["category"] not in BUY)
        if not (foot.buy_total == buy and foot.sell_total == sell and foot.both_total == buy + sell and foot.margin_ok):
            raise StatementError("totals")
    return ParsedStatement(trade, settle, Decimal(foot.line_net), items, {k: Decimal(v) for k, v in foot.totals.items()},
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
