"""永豐期貨日 / 月對帳單 parser (spec 2026-11-25-sinopac-futures-statement §5): positioned words -> payload dict.

Pure: no network, no Drive, no PDF library; the input is `pdf.extract_lines` output. Numeric words are read by
column (right edge within 3 pt of their label's, the monthly 買入 / 賣出 quantities by centre); text words by their
fixed sequence. Anything outside the §5.3 grammar, and any broken §2 identity, raises StatementError with a short
code; the message never carries statement content.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from worker.pdf import Line, Word

ALIGN_PT = 3.0
A_SUFFIX_PT = 12.2  # width of the `[A]` glued to the 調整前原始保證金 amount (census: every glued word, rev 1.7)

DAILY_TITLE = "國內期貨日對帳單"
MONTHLY_TITLE = "國內期貨月對帳單"
CURRENCY = "新台幣(NTT)"
DAILY_SECTIONS = ("國內市場存提", "國內市場交易紀錄", "國內市場留倉彙總", "國內市場權益帳")
MONTHLY_SECTIONS = ("國內市場存提", "國內市場交易明細", "國內市場沖銷明細", "國內市場留倉明細", "月底帳戶餘額明細")

_DATE_RE = re.compile(r"^([0-9]{3})/([0-9]{2})/([0-9]{2})$")  # ROC
_MONTH_RE = re.compile(r"^[0-9]{4}/[0-9]{2}$")
_ORDER_RE = re.compile(r"^[A-Za-z0-9]{5}-[0-9]{2}$")
_NUM_RE = re.compile(r"^-?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?$")
_INT_RE = re.compile(r"^[0-9]+$")
_MARKER_LIKE = re.compile(r"＊.+＊")
_MARKER_RE = re.compile(r"^(交易幣別：)?(\S+) ＊([^＊\s]+)＊$")
_FOOTER_RE = re.compile(r"^第([0-9]+)頁/共([0-9]+)頁$")
_GLUED_A_RE = re.compile(r"^((?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?)?\[A\]$")
_LEG_CODE_RE = re.compile(r"^[A-Z0-9]+/[A-Z0-9]+$")
_LEG_RATIO_RE = re.compile(r"^[0-9]+[A-Z]:[0-9]+[A-Z]$")
_LEG_KIND_RE = re.compile(r"^\[[^\]\s]+$")
_FILE_DAY_RE = re.compile(r"^([0-9]{4}-[0-9]{2}-[0-9]{2})_")
_FILE_MONTH_RE = re.compile(r"^([0-9]{4}-[0-9]{2})_")

RIGHTS = ("Call", "Put")
ACTIONS = ("新買", "新賣", "平買", "平賣")
CLOSING = ("平買", "平賣")
SIDES = ("買", "賣")
_KEYWORDS = frozenset(("Taiwan", "F", "小計", "商品小計", "總計", *RIGHTS, *ACTIONS, *SIDES))

# header rows, exactly (spec §2, rev 1.7)
D_DEP = ("異動日期", "異動序號", "類別", "異動類別", "匯出入銀行", "作業說明", "手續費用", "匯出入金額")
D_TRADE = ("成交日期", "委託書號", "交易所", "商品代號", "交割月份", "履約價格", "權別", "交易", "口數", "成交價格", "期交稅",
           "手續費", "權利金/平倉損益")
D_POS = ("成交日期", "商品代號", "交割月份", "履約價格", "權別", "買賣", "進場價格", "行情價位", "部位", "原始保證金",
         "權利金市值", "未沖銷浮動損益")
D_EQ_B = ("本日餘額", "本日權益數", "權益總值", "原始保證金", "維持保證金", "有價證券抵繳總額", "超額保證金", "追繳保證金")
D_EQ_A = ("前日餘額", "存提淨額", "期交稅手續費", "本日期貨平倉損益淨額", "未沖銷期貨浮動損益", "權利金收支與轉現價款",
          "加收保證金", "可動用(出金)保證金")
_M_HEAD = ("交易日", "買入", "賣出", "委託書號", "單別", "商品代號", "交割月份", "履約價格", "權別")
M_DEP = ("日期", "出入金類別", "出入金內容", "出入金銀行帳號", "出金金額", "入金金額")
M_TRADE = _M_HEAD + ("倉別", "成交價格", "交易稅", "手續費", "權利金收付")
M_CLOSE = _M_HEAD + ("倉別", "成交價格", "價差損益-支出金額", "價差損益-存入金額", "參考損益")
M_OPEN = _M_HEAD + ("買賣別", "成交價格", "價差損益(支出)", "價差損益(存入)")
_QTY = ("買入", "賣出")  # monthly quantity columns: centred under their labels
_ALL_HEADERS = (D_DEP, D_TRADE, D_POS, D_EQ_B, D_EQ_A, M_DEP, M_TRADE, M_CLOSE, M_OPEN)
_LABEL_WORDS = frozenset(label for header in _ALL_HEADERS for label in header)

EQUITY_FIELDS = {
    "本日餘額": "balance", "本日權益數": "equity", "權益總值": "total_equity", "原始保證金": "initial_margin",
    "維持保證金": "maintenance_margin", "有價證券抵繳總額": "securities_collateral", "超額保證金": "excess_margin",
    "追繳保證金": "margin_call", "前日餘額": "prev_balance", "存提淨額": "deposits_net", "期交稅手續費": "fees_and_tax",
    "本日期貨平倉損益淨額": "futures_realized", "未沖銷期貨浮動損益": "futures_unrealized",
    "權利金收支與轉現價款": "premium_net", "加收保證金": "additional_margin", "可動用(出金)保證金": "withdrawable",
}
POSITION_LINE_KEYS = {"未沖銷選擇權浮動損益": "option_unrealized", "未沖銷選擇權市場價值淨額": "option_value_net",
                      "省組合保證金": "combo_margin_saving", "調整後原始保證金(A - C)": "margin_after_combo"}

# month-end block: left label, right label (None: the line has none), payload keys
MONTH_END = (
    ("A.前月餘額", None, "A", None), ("B.存提淨額", "U.原始保證金", "B", "U"),
    ("G.權利金收支與轉現價款", "V.維持保證金", "G", "V"), ("H.手續費，期交稅合計", "有價證券抵繳總額", "H", "securities_collateral"),
    ("K.本月期貨平倉損益淨額", "W.超額／追繳保證金", "K", "W"), ("M.本月餘額", "加收保證金", "M", "additional_margin"),
    ("N.未沖銷期貨浮動損益", "可動用(出金)保證金", "N", "withdrawable"), ("Q.本月權益數", "P.未沖銷選擇權浮動損益", "Q", "P"),
    ("未沖銷買方權利金市值", None, "long_premium_value", None), ("未沖銷賣方權利金市值", None, "short_premium_value", None),
    ("權益總值", None, "total_equity", None),
)


class StatementError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class DailyStatement:
    statement_date: date
    equity: dict = field(repr=False)  # payload key -> Decimal
    deposits: list = field(repr=False)
    fills: list = field(repr=False)
    fills_total: dict | None = field(repr=False)
    positions: list = field(repr=False)
    position_lines: dict = field(repr=False)


@dataclass(frozen=True)
class MonthlyStatement:
    month: str  # YYYY-MM
    month_end: date
    lines: dict = field(repr=False)  # payload key -> Decimal
    activity_dates: tuple = field(repr=False)
    open_positions: int


# ---------------------------------------------------------------- words and columns

@dataclass(frozen=True)
class _Label:
    text: str
    x0: float
    x1: float
    by_centre: bool


def _texts(line: Line) -> list[str]:
    return [w.text for w in line.words]


def _is_num(token: str) -> bool:
    return bool(_NUM_RE.match(token))


def _dec(token: str) -> Decimal:
    return Decimal(token.replace(",", ""))


def _roc(token: str) -> date:
    m = _DATE_RE.match(token)
    if not m:
        raise StatementError("date")
    try:
        return date(int(m.group(1)) + 1911, int(m.group(2)), int(m.group(3)))
    except ValueError:
        raise StatementError("date") from None


def _split(words) -> tuple[list[str], list[Word]]:
    """(text words in reading order, numeric words)."""
    return [w.text for w in words if not _is_num(w.text)], [w for w in words if _is_num(w.text)]


def _labels(line: Line, centre: tuple[str, ...] = ()) -> list[_Label]:
    return [_Label(w.text, w.x0, w.x1, w.text in centre) for w in line.words]


def _columns(words: list[Word], labels: list[_Label], allowed: tuple[str, ...]) -> dict[str, str]:
    """Each numeric word to exactly one label of `allowed`, at most one word per label."""
    out: dict[str, str] = {}
    for w in words:
        mid = (w.x0 + w.x1) / 2
        hits = [lb for lb in labels
                if (abs((lb.x0 + lb.x1) / 2 - mid) if lb.by_centre else abs(lb.x1 - w.x1)) <= ALIGN_PT]
        if len(hits) != 1 or hits[0].text not in allowed or hits[0].text in out:
            raise StatementError("column")
        out[hits[0].text] = w.text
    return out


def _amount(cols: dict[str, str], label: str) -> Decimal:
    return _dec(cols[label]) if label in cols else Decimal(0)


def _required(cols: dict[str, str], label: str) -> Decimal:
    if label not in cols:
        raise StatementError("column")
    return _dec(cols[label])


def _qty(token: str | None) -> int:
    if token is None or not _INT_RE.match(token) or int(token) < 1:
        raise StatementError("column")
    return int(token)


def _contract(texts: list[str], at: int) -> tuple[str, str]:
    """(product, contract month) at texts[at], texts[at + 1]."""
    if at + 1 >= len(texts):
        raise StatementError("sequence")
    product, month = texts[at], texts[at + 1]
    if (product in _KEYWORDS or _is_num(product) or _DATE_RE.match(product) or _MONTH_RE.match(product)
            or not _MONTH_RE.match(month)):
        raise StatementError("sequence")
    return product, month


def _strike_right(texts: list[str], at: int, cols: dict[str, str]) -> tuple[Decimal | None, str | None, int]:
    """The optional right at texts[at]; an option has both a strike and a right, a futures line neither."""
    right = texts[at] if at < len(texts) and texts[at] in RIGHTS else None
    strike = _dec(cols["履約價格"]) if "履約價格" in cols else None
    if (strike is None) != (right is None):
        raise StatementError("sequence")
    return strike, right, at + (1 if right else 0)


def _kinds(codes: str, pattern: str, code: str = "sequence") -> None:
    if not re.fullmatch(pattern, codes):
        raise StatementError(code)


# ---------------------------------------------------------------- page framing and sections

def _footers(lines: list[Line]) -> dict[int, list[Line]]:
    pages: dict[int, list[Line]] = {}
    for ln in lines:
        pages.setdefault(ln.page, []).append(ln)
    count = len(pages)
    for number, page in pages.items():
        m = _FOOTER_RE.match(page[-1].text)
        if not m or int(m.group(1)) != number or int(m.group(2)) != count:
            raise StatementError("line")
        page.pop()
    return pages


def _sections(lines: list[Line], *, daily: bool, expected: tuple[str, ...]):
    """(lines before the first marker, [(section name, lines)]), markers checked for currency, names and order."""
    pre: list = []
    out: list[tuple[str, list]] = []
    for item in lines:
        ln = item[0] if isinstance(item, tuple) else item
        if _MARKER_LIKE.search(ln.text):
            if "國外" in ln.text or "海外" in ln.text:
                raise StatementError("currency")
            m = _MARKER_RE.match(ln.text)
            if not m or bool(m.group(1)) != daily:
                raise StatementError("section")
            if m.group(2) != CURRENCY:
                raise StatementError("currency")
            out.append((m.group(3), []))
            continue
        (out[-1][1] if out else pre).append(item)
    if tuple(name for name, _ in out) != expected:
        raise StatementError("section")
    return pre, out


def _check_pre(pre: list[Line], title: str) -> None:
    if not pre or pre[0].text != title:
        raise StatementError("title")
    if len(pre) != 3 or pre[1].words[-1].text != "啟":
        raise StatementError("header")


def _header(lines: list, expected: tuple[str, ...], centre: tuple[str, ...] = ()):
    """The section's header row (first line, exact) -> (labels, the remaining lines)."""
    def plain(item):
        return item[0] if isinstance(item, tuple) else item
    if not lines or tuple(_texts(plain(lines[0]))) != expected:
        raise StatementError("header")
    rest = []
    for item in lines[1:]:
        ln = plain(item)
        if tuple(_texts(ln)) == expected:
            if isinstance(item, tuple) and item[1]:
                continue  # repeated once at the top of a monthly continuation page
            raise StatementError("header")
        rest.append(ln)
    return _labels(plain(lines[0]), centre), rest


def _guard_notices(lines: list[Line], code: str) -> None:
    """Notices are not read, but nothing that belongs to a section may hide in them: a marker, a header or label
    row (also a partial one: two or more words, all labels), a dated line, an all-numeric line, a total."""
    for ln in lines:
        texts = _texts(ln)
        partial_header = len(texts) >= 2 and all(x in _LABEL_WORDS for x in texts)
        if (_MARKER_LIKE.search(ln.text) or partial_header or any(_DATE_RE.match(x) for x in texts)
                or all(_is_num(x) for x in texts) or any("總計" in x or "小計" in x for x in texts)):
            raise StatementError(code)


# ---------------------------------------------------------------- daily

def parse_daily(lines: list[Line]) -> DailyStatement:
    if sorted({ln.page for ln in lines}) != [1, 2]:
        raise StatementError("page2")
    pages = _footers(lines)
    _guard_notices(pages[2], "page2")
    pre, sections = _sections(pages[1], daily=True, expected=DAILY_SECTIONS)
    _check_pre(pre, DAILY_TITLE)
    body = dict(sections)
    dates, deposits = _daily_deposits(body["國內市場存提"])
    trade_dates, fills, fills_total = _daily_trades(body["國內市場交易紀錄"])
    positions, position_lines, checks = _daily_positions(body["國內市場留倉彙總"])
    equity = _daily_equity(body["國內市場權益帳"])
    dates |= trade_dates
    if len(dates) != 1:
        raise StatementError("date")
    _daily_identities(equity, deposits, fills, fills_total, positions, checks)
    return DailyStatement(dates.pop(), equity, deposits, fills, fills_total, positions, position_lines)


def _daily_deposits(lines: list[Line]) -> tuple[set[date], list[dict]]:
    labels, rest = _header(lines, D_DEP)
    dates: set[date] = set()
    deposits: list[dict] = []
    codes = ""
    for ln in rest:
        words = list(ln.words)
        if not words or not _DATE_RE.match(words[0].text):
            raise StatementError("line")
        dates.add(_roc(words[0].text))
        if _texts(ln)[1:] == ["無存提"]:
            codes += "N"
            continue
        codes += "D"
        if len(words) < 2 or not _INT_RE.match(words[1].text):
            raise StatementError("sequence")
        texts, nums = _split(words[2:])
        if texts[:3] == ["當日入金", "入", "金"]:
            direction, free = "in", texts[3:]
        elif texts[:2] == ["當日出金", "網路出金"]:
            direction, free = "out", texts[2:]
        else:
            raise StatementError("sequence")
        if len(free) != (1 if direction == "in" else 2):  # bank; bank and note (census rev 1.7)
            raise StatementError("sequence")
        cols = _columns(nums, labels, ("匯出入金額",))
        deposits.append({"seq": words[1].text, "direction": direction, "amount": _required(cols, "匯出入金額")})
    _kinds(codes, r"N|D+")
    return dates, deposits


def _daily_trades(lines: list[Line]) -> tuple[set[date], list[dict], dict | None]:
    labels, rest = _header(lines, D_TRADE)
    dates: set[date] = set()
    fills: list[dict] = []
    total = None
    codes = ""
    for ln in rest:
        texts, nums = _split(ln.words)
        head = texts[0] if texts else ""
        if _DATE_RE.match(head) and texts[1:] == ["無交易"] and not nums:
            dates.add(_roc(head))
            codes += "N"
        elif _DATE_RE.match(head) and len(texts) > 1 and _ORDER_RE.match(texts[1]):
            fill = _fill(texts, nums, labels)
            dates.add(fill.pop("_date"))
            fills.append(fill)
            codes += "X" if fill["action"] in CLOSING else "F"
        elif head == "平倉->":
            if not fills or fills[-1]["action"] not in CLOSING or codes[-1] not in "XL":
                raise StatementError("sequence")
            fills[-1]["closes"].append(_link(texts, nums, labels))
            codes += "L"
        elif head == "商品小計":
            if texts != ["商品小計"]:
                raise StatementError("sequence")
            _columns(nums, labels, ("口數", "成交價格", "期交稅", "手續費", "權利金/平倉損益"))
            codes += "S"
        elif head == "總計":
            if texts != ["總計"]:
                raise StatementError("sequence")
            cols = _columns(nums, labels, ("口數", "期交稅", "手續費", "權利金/平倉損益"))
            total = {"tax": _amount(cols, "期交稅"), "fee": _amount(cols, "手續費"), "pnl": _amount(cols, "權利金/平倉損益")}
            codes += "T"
        else:
            raise StatementError("line")
    _kinds(codes, r"N|((F|XL+)+S)+T")
    for fill in fills:
        if fill["action"] in CLOSING and sum(c["qty"] for c in fill["closes"]) != fill["qty"]:
            raise StatementError("sequence")
    return dates, fills, total


def _fill(texts: list[str], nums: list[Word], labels: list[_Label]) -> dict:
    if len(texts) not in (7, 8) or texts[2:4] != ["Taiwan", "F"]:
        raise StatementError("sequence")
    product, month = _contract(texts, 4)
    cols = _columns(nums, labels, ("履約價格", "口數", "成交價格", "期交稅", "手續費", "權利金/平倉損益"))
    strike, right, at = _strike_right(texts, 6, cols)
    if at != len(texts) - 1 or texts[at] not in ACTIONS:
        raise StatementError("sequence")
    return {"_date": _roc(texts[0]), "order_no": texts[1], "product": product, "month": month, "strike": strike,
            "right": right, "action": texts[at], "qty": _qty(cols.get("口數")), "price": _required(cols, "成交價格"),
            "tax": _amount(cols, "期交稅"), "fee": _amount(cols, "手續費"), "pnl": _amount(cols, "權利金/平倉損益"),
            "closes": []}


def _link(texts: list[str], nums: list[Word], labels: list[_Label]) -> dict:
    if len(texts) != 3 or not _ORDER_RE.match(texts[2]):
        raise StatementError("sequence")
    cols = _columns(nums, labels, ("口數", "成交價格", "權利金/平倉損益"))  # the link's 損益 is a reference, not posted
    return {"date": _roc(texts[1]), "order_no": texts[2], "qty": _qty(cols.get("口數")),
            "price": _required(cols, "成交價格")}


_POS_COLUMNS = ("履約價格", "進場價格", "行情價位", "部位", "原始保證金", "權利金市值", "未沖銷浮動損益")


def _daily_positions(lines: list[Line]) -> tuple[list[dict], dict, dict]:
    labels, rest = _header(lines, D_POS)
    positions: list[dict] = []
    position_lines: dict[str, Decimal] = {}
    checks: dict[str, Decimal] = {}
    codes = ""
    for ln in rest:
        texts, nums = _split(ln.words)
        joined = " ".join(texts)
        head = texts[0] if texts else ""
        if texts == ["無留倉"] and not nums:
            codes += "N"
        elif _DATE_RE.match(head):
            positions.append(_position(texts, nums, labels))
            codes += "P"
        elif len(texts) > 1 and texts[1] == "小計":
            _contract_sub(texts, nums, labels)
            codes += "S"
        elif joined in POSITION_LINE_KEYS:
            cols = _columns(nums, labels, ("原始保證金",))
            if "原始保證金" in cols:
                position_lines[POSITION_LINE_KEYS[joined]] = _dec(cols["原始保證金"])
            checks[joined] = _amount(cols, "原始保證金")
            codes += {"未沖銷選擇權浮動損益": "u", "未沖銷選擇權市場價值淨額": "v", "省組合保證金": "c",
                      "調整後原始保證金(A - C)": "e"}[joined]
        elif head == "調整前原始保證金" and len(texts) == 2 and _GLUED_A_RE.match(texts[1]):
            cols = _columns(nums, labels, ("部位",))
            glued = _GLUED_A_RE.match(texts[1]).group(1)
            if glued:
                word = next(w for w in ln.words if w.text == texts[1])
                margin_x1 = next(lb.x1 for lb in labels if lb.text == "原始保證金")
                if abs(word.x1 - A_SUFFIX_PT - margin_x1) > ALIGN_PT:
                    raise StatementError("column")
            checks["A.qty"], checks["A"] = Decimal(_qty(cols.get("部位"))), _dec(glued) if glued else Decimal(0)
            codes += "a"
        elif joined == "組合合計[B]" and not nums:
            codes += "b"
        elif joined == "有價證券抵繳總額 [D]" and not nums:
            codes += "d"
        elif joined == "原始保證金(A - C - D)":
            cols = _columns(nums, labels, ("部位", "原始保證金"))
            checks["ACD.qty"] = Decimal(_qty(cols.get("部位")))
            checks["ACD"] = _amount(cols, "原始保證金")
            codes += "n"
        elif len(texts) == 5 and _LEG_CODE_RE.match(head):
            if not (_LEG_RATIO_RE.match(texts[1]) and texts[2] in SIDES and _LEG_KIND_RE.match(texts[3])
                    and texts[4] == "]"):
                raise StatementError("sequence")
            _qty(_columns(nums, labels, ("部位",)).get("部位"))
            codes += "l"
        else:
            raise StatementError("line")
    _kinds(codes, r"N|(P+S)+uva(l+bce)?dn")
    return positions, position_lines, checks


def _position(texts: list[str], nums: list[Word], labels: list[_Label]) -> dict:
    product, month = _contract(texts, 1)
    cols = _columns(nums, labels, _POS_COLUMNS)
    strike, right, at = _strike_right(texts, 3, cols)
    if at != len(texts) - 1 or texts[at] not in SIDES:
        raise StatementError("sequence")
    return {"opened": _roc(texts[0]), "product": product, "month": month, "strike": strike, "right": right,
            "side": texts[at], "entry_price": _required(cols, "進場價格"), "mark_price": _required(cols, "行情價位"),
            "qty": _qty(cols.get("部位")), "initial_margin": _amount(cols, "原始保證金"),
            "premium_value": _amount(cols, "權利金市值"), "unrealized": _amount(cols, "未沖銷浮動損益")}


def _contract_sub(texts: list[str], nums: list[Word], labels: list[_Label]) -> None:
    """`<商品> 小計 [<strike> <right>] <side>`: shape only."""
    if texts[0] in _KEYWORDS or _is_num(texts[0]):
        raise StatementError("sequence")
    cols = _columns(nums, labels, _POS_COLUMNS)
    _, _, at = _strike_right(texts, 2, cols)
    if at != len(texts) - 1 or texts[at] not in SIDES:
        raise StatementError("sequence")


def _daily_equity(lines: list[Line]) -> dict[str, Decimal]:
    if not lines or _texts(lines[0]) != ["匯率："]:
        raise StatementError("line")
    values: dict[str, Decimal] = {}
    at = 1
    for expected in (D_EQ_B, D_EQ_A):
        if at >= len(lines) or tuple(_texts(lines[at])) != expected:
            raise StatementError("line" if at < len(lines) and all(_is_num(x) for x in _texts(lines[at]))
                                 else "header")
        labels = _labels(lines[at])
        if at + 1 >= len(lines) or not all(_is_num(x) for x in _texts(lines[at + 1])):
            raise StatementError("line")
        cols = _columns(list(lines[at + 1].words), labels, expected)
        values.update({label: _amount(cols, label) for label in expected})
        at += 2
    _guard_notices(lines[at:], "line")
    return {EQUITY_FIELDS[label]: value for label, value in values.items()}


def _signed_premium(positions: list[dict]) -> Decimal:
    return sum((-p["premium_value"] if p["side"] == "賣" else p["premium_value"] for p in positions), Decimal(0))


def _daily_identities(e: dict, deposits: list[dict], fills: list[dict], fills_total: dict | None,
                      positions: list[dict], checks: dict) -> None:
    def check(name: str, ok: bool) -> None:
        if not ok:
            raise StatementError(f"identity:{name}")

    zero = Decimal(0)
    futures = [f for f in fills if f["right"] is None]
    options = [f for f in fills if f["right"] is not None]
    check("deposits", e["deposits_net"] == sum((d["amount"] if d["direction"] == "in" else -d["amount"]
                                                for d in deposits), zero))
    check("realized", e["futures_realized"] == sum((f["pnl"] for f in futures), zero))
    check("premium", e["premium_net"] == sum((f["pnl"] for f in options), zero))
    check("unrealized", e["futures_unrealized"] == sum((p["unrealized"] for p in positions if p["right"] is None),
                                                       zero))
    if positions:
        check("option_unrealized", checks["未沖銷選擇權浮動損益"] == sum((p["unrealized"] for p in positions
                                                                 if p["right"] is not None), zero))
        check("option_value", checks["未沖銷選擇權市場價值淨額"] == _signed_premium(positions))
        qty = Decimal(sum(p["qty"] for p in positions))
        check("position_margin", checks["A"] == sum((p["initial_margin"] for p in positions), zero)
              and checks["A.qty"] == qty == checks["ACD.qty"])
        saving = checks.get("省組合保證金", zero)
        if "調整後原始保證金(A - C)" in checks:
            check("margin_after_combo", checks["調整後原始保證金(A - C)"] == checks["A"] - saving)
        check("margin_net", checks["ACD"] == checks["A"] - saving)  # D: the [D] line never prints an amount
    if fills_total is not None:
        check("total_tax", fills_total["tax"] == sum((f["tax"] for f in fills), zero))
        check("total_fee", fills_total["fee"] == sum((f["fee"] for f in fills), zero))
        check("total_pnl", fills_total["pnl"] == sum((f["pnl"] for f in fills), zero))
        check("fees", e["fees_and_tax"] == fills_total["tax"] + fills_total["fee"])
    check("balance", e["balance"] == e["prev_balance"] + e["deposits_net"] - e["fees_and_tax"] + e["futures_realized"]
          + e["premium_net"])
    check("equity", e["equity"] == e["balance"] + e["futures_unrealized"])
    check("total_equity", e["total_equity"] == e["equity"] + _signed_premium(positions))


# ---------------------------------------------------------------- monthly

def parse_monthly(lines: list[Line]) -> MonthlyStatement:
    if not lines:
        raise StatementError("title")
    pages = _footers(lines)
    stream: list[tuple[Line, bool]] = []  # (line, first line after a continuation page's top block)
    for number in sorted(pages):
        page = pages[number]
        if number > 1:
            if not page or page[0].text != MONTHLY_TITLE:
                raise StatementError("title")
            skip = 1
            if len(page) > 1 and page[1].words[-1].text == "啟":
                if len(page) < 3:
                    raise StatementError("header")
                skip = 3
            page = page[skip:]
        stream += [(ln, number > 1 and i == 0) for i, ln in enumerate(page)]
    pre, sections = _sections(stream, daily=False, expected=MONTHLY_SECTIONS)
    _check_pre([ln for ln, _ in pre], MONTHLY_TITLE)
    body = dict(sections)
    activity = _monthly_deposits(body["國內市場存提"]) | _monthly_trades(body["國內市場交易明細"])
    _monthly_closes(body["國內市場沖銷明細"])
    open_positions = _monthly_open(body["國內市場留倉明細"])
    when, values = _month_end([ln for ln, _ in body["月底帳戶餘額明細"]])
    if any((d.year, d.month) != (when.year, when.month) for d in activity):
        raise StatementError("date")
    v = values
    if v["M"] != v["A"] + v["B"] + v["G"] - v["H"] + v["K"]:
        raise StatementError("identity:month_balance")
    if v["Q"] != v["M"] + v["N"]:
        raise StatementError("identity:month_equity")
    if v["total_equity"] != v["Q"] + v["long_premium_value"] - v["short_premium_value"]:
        raise StatementError("identity:month_total_equity")
    last = date(when.year, when.month, calendar.monthrange(when.year, when.month)[1])
    return MonthlyStatement(f"{when.year:04d}-{when.month:02d}", last, values, tuple(sorted(activity)), open_positions)


def _monthly_deposits(lines: list) -> set[date]:
    labels, rest = _header(lines, M_DEP)
    dates: set[date] = set()
    codes = ""
    for ln in rest:
        texts, nums = _split(ln.words)
        if texts == ["無存提"] and not nums:
            codes += "N"
        elif texts == ["存提淨額(B)"]:
            _columns(nums, labels, ("出金金額", "入金金額"))
            codes += "B"
        elif texts and _DATE_RE.match(texts[0]):
            if texts[1:3] == ["當日入金", "新倉入金"]:
                column = "入金金額"
            elif texts[1:3] == ["當日出金", "網路出金"]:
                column = "出金金額"
            else:
                raise StatementError("sequence")
            if len(texts) != 4:  # one unread account word (census rev 1.7)
                raise StatementError("sequence")
            _required(_columns(nums, labels, (column,)), column)
            dates.add(_roc(texts[0]))
            codes += "D"
        else:
            raise StatementError("line")
    _kinds(codes, r"N|D+B")
    return dates


_M_TOTALS = {("手續費總計(H)",): "h", ("期", "-", "期交稅總計(H)"): "i", ("權", "-", "期交稅總計(H)"): "j"}


def _monthly_trades(lines: list) -> set[date]:
    labels, rest = _header(lines, M_TRADE, _QTY)
    dates: set[date] = set()
    codes = ""
    for ln in rest:
        texts, nums = _split(ln.words)
        if texts == ["無交易"] and not nums:
            codes += "N"
        elif texts == ["權利金收入與支出(G)"]:
            _columns(nums, labels, ("買入", "賣出", "權利金收付"))
            codes += "G"
        elif tuple(texts) in _M_TOTALS:
            _columns(nums, labels, ("手續費",))
            codes += _M_TOTALS[tuple(texts)]
        elif texts and _DATE_RE.match(texts[0]):
            if len(texts) < 6 or not _ORDER_RE.match(texts[1]) or texts[2] not in ("網", "複"):
                raise StatementError("sequence")
            _contract(texts, 3)
            cols = _columns(nums, labels, ("買入", "賣出", "履約價格", "成交價格", "交易稅", "手續費", "權利金收付"))
            _, _, at = _strike_right(texts, 5, cols)
            if at != len(texts) - 1 or texts[at] not in (*ACTIONS, "到期"):
                raise StatementError("sequence")
            _one_qty(cols)
            _required(cols, "成交價格")
            dates.add(_roc(texts[0]))
            codes += "T"
        else:
            raise StatementError("line")
    _kinds(codes, r"N|T+Ghij")
    return dates


def _one_qty(cols: dict[str, str]) -> None:
    present = [c for c in _QTY if c in cols]
    if len(present) != 1:
        raise StatementError("column")
    _qty(cols[present[0]])


def _monthly_closes(lines: list) -> None:
    labels, rest = _header(lines, M_CLOSE, _QTY)
    codes = ""
    for ln in rest:
        texts, nums = _split(ln.words)
        if texts == ["無沖銷"] and not nums:
            codes += "N"
        elif texts == ["期貨平倉損益淨額(K)"]:
            _columns(nums, labels, ("價差損益-支出金額", "價差損益-存入金額"))
            codes += "K"
        elif texts == ["選擇權參考損益合計"]:
            _columns(nums, labels, ("參考損益",))
            codes += "R"
        elif texts and _DATE_RE.match(texts[0]) and len(texts) > 1 and _ORDER_RE.match(texts[1]):
            _roc(texts[0])
            if "原價格" in texts:
                tail = texts[2:]
                if tail[:1] == ["複"]:
                    tail = tail[1:]
                if tail not in (["<被平倉>", "原價格"], ["<被履約>", "原價格"]):
                    raise StatementError("sequence")
                cols = _columns(nums, labels, ("買入", "賣出", "成交價格", "參考損益"))
                codes += "C"
            else:
                _contract(texts, 2)
                cols = _columns(nums, labels, ("買入", "賣出", "履約價格", "成交價格", "價差損益-支出金額",
                                               "價差損益-存入金額"))
                _, _, at = _strike_right(texts, 4, cols)
                if at != len(texts) - 1 or texts[at] not in ("平倉價", "結算價"):
                    raise StatementError("sequence")
                codes += "O"
            _one_qty(cols)
            _required(cols, "成交價格")
        else:
            raise StatementError("line")
    _kinds(codes, r"N|(OC+)+KR")


def _monthly_open(lines: list) -> int:
    labels, rest = _header(lines, M_OPEN, _QTY)
    codes = ""
    pnl = ("價差損益(支出)", "價差損益(存入)")
    for ln in rest:
        texts, nums = _split(ln.words)
        if texts == ["無留倉"] and not nums:
            codes += "N"
        elif texts == ["合計", "未沖銷期貨浮動損益(N)"]:
            _columns(nums, labels, ("買入", "賣出", *pnl))
            codes += "T"
        elif texts == ["未沖銷選擇權浮動損益-參考(P)"]:
            _columns(nums, labels, pnl)
            codes += "Q"
        elif texts[:1] == ["小計"]:
            _contract(texts, 1)
            _, _, at = _strike_right(texts, 3, _columns(nums, labels, ("履約價格", *pnl)))
            if at != len(texts):
                raise StatementError("sequence")
            codes += "S"
        elif texts and _DATE_RE.match(texts[0]):
            _roc(texts[0])
            if len(texts) < 4 or not _ORDER_RE.match(texts[1]):
                raise StatementError("sequence")
            at = 3 if texts[2] == "複" else 2
            _contract(texts, at)
            cols = _columns(nums, labels, ("買入", "賣出", "履約價格", "成交價格", *pnl))
            _, _, end = _strike_right(texts, at + 2, cols)
            if end != len(texts):
                raise StatementError("sequence")
            _one_qty(cols)
            _required(cols, "成交價格")
            codes += "P"
        else:
            raise StatementError("line")
    _kinds(codes, r"N|(P+S)+TQ")
    return codes.count("P")


def _month_end(lines: list[Line]) -> tuple[date, dict[str, Decimal]]:
    if len(lines) < len(MONTH_END):
        raise StatementError("line")
    values: dict[str, Decimal] = {}
    dates: set[date] = set()
    left_edges: list[float] = []
    right_edges: list[float] = []
    for ln, (left, right, left_key, right_key) in zip(lines, MONTH_END):
        words = list(ln.words)
        if len(words) < 2 or not _DATE_RE.match(words[0].text) or words[1].text != left:
            raise StatementError("line")
        dates.add(_roc(words[0].text))
        at = 2
        values[left_key] = Decimal(0)
        if at < len(words) and _is_num(words[at].text):
            values[left_key] = _dec(words[at].text)
            left_edges.append(words[at].x1)
            at += 1
        if right is not None:
            if at >= len(words) or words[at].text != right:
                raise StatementError("line")
            at += 1
            values[right_key] = Decimal(0)
            if at < len(words) and _is_num(words[at].text):
                values[right_key] = _dec(words[at].text)
                right_edges.append(words[at].x1)
                at += 1
        if at != len(words):
            raise StatementError("column" if any(_is_num(w.text) for w in words[at:]) else "line")
    for edges in (left_edges, right_edges):
        if edges and max(edges) - min(edges) > ALIGN_PT:
            raise StatementError("column")
    if len(dates) != 1:
        raise StatementError("date")
    _guard_notices(lines[len(MONTH_END):], "line")
    return dates.pop(), values


# ---------------------------------------------------------------- payload

def _s(value: Decimal) -> str:
    """Decimal string without group separators or trailing fractional zeros; zero is "0"."""
    if value == 0:
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _opt(value: Decimal | None) -> str | None:
    return None if value is None else _s(value)


def to_payload(st: DailyStatement | MonthlyStatement, *, drive_file_id: str, sha256: str, file_name: str) -> dict:
    source = {"drive_file_id": drive_file_id, "sha256": sha256, "file_name": file_name}
    warnings: list[str] = []
    if isinstance(st, MonthlyStatement):
        m = _FILE_MONTH_RE.match(file_name)
        if m and m.group(1) != st.month:
            warnings.append("filename_date_differs")
        return {"kind": "monthly", "source": source, "month": st.month, "month_end": st.month_end.isoformat(),
                "lines": {k: _s(v) for k, v in st.lines.items()},
                "activity_dates": [d.isoformat() for d in st.activity_dates], "open_positions": st.open_positions,
                "warnings": warnings}
    m = _FILE_DAY_RE.match(file_name)
    if m and m.group(1) != st.statement_date.isoformat():
        warnings.append("filename_date_differs")
    fills = [{"order_no": f["order_no"], "product": f["product"], "month": f["month"], "strike": _opt(f["strike"]),
              "right": f["right"], "action": f["action"], "qty": f["qty"], "price": _s(f["price"]), "tax": _s(f["tax"]),
              "fee": _s(f["fee"]), "pnl": _s(f["pnl"]),
              "closes": [{"date": c["date"].isoformat(), "order_no": c["order_no"], "qty": c["qty"],
                          "price": _s(c["price"])} for c in f["closes"]]} for f in st.fills]
    positions = [{"opened": p["opened"].isoformat(), "product": p["product"], "month": p["month"],
                  "strike": _opt(p["strike"]), "right": p["right"], "side": p["side"],
                  "entry_price": _s(p["entry_price"]), "mark_price": _s(p["mark_price"]), "qty": p["qty"],
                  "initial_margin": _s(p["initial_margin"]), "premium_value": _s(p["premium_value"]),
                  "unrealized": _s(p["unrealized"])} for p in st.positions]
    return {"kind": "daily", "source": source, "statement_date": st.statement_date.isoformat(),
            "equity": {k: _s(v) for k, v in st.equity.items()},
            "deposits": [{"seq": d["seq"], "direction": d["direction"], "amount": _s(d["amount"])}
                         for d in st.deposits],
            "fills": fills,
            "fills_total": None if st.fills_total is None else {k: _s(v) for k, v in st.fills_total.items()},
            "positions": positions, "position_lines": {k: _s(v) for k, v in st.position_lines.items()},
            "warnings": warnings}
