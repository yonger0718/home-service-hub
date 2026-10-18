"""A synthetic 綜合月對帳單 as pdf.Line objects: the layout of the real statement, fictional content (owner rule)."""
from __future__ import annotations

from worker.pdf import Line, Word

# right edges measured on the real file: withdrawals end under 提出, deposits under 存入, balances under 餘額
X_DATE, X_DESC, X_CUR, X_OUT, X_IN, X_BAL = 38, 90, 229, 356, 446, 555
HEADER_WORDS = (("日期", 51, 67), ("交易說明", 132, 164), ("幣別", 229, 245), ("提出", 304, 319), ("存入", 393, 409),
                ("餘額", 498, 514))


def W(text: str, x0: float, x1: float | None = None) -> Word:
    return Word(text, x0, x1 if x1 is not None else x0 + 6 * len(text))


def L(page: int, top: float, *words: Word) -> Line:
    return Line(page, top, tuple(sorted(words, key=lambda w: w.x0)))


def header(page: int, top: float) -> Line:
    return L(page, top, *(W(t, a, b) for t, a, b in HEADER_WORDS))


def amount(text: str, right: float) -> Word:
    return Word(text, right - 6 * len(text), right)


def row(page: int, top: float, day: str, desc: str, out: str | None, inn: str | None, bal: str, cur="TWD",
        extra: tuple[Word, ...] = ()) -> Line:
    words = [W(day, X_DATE), *(W(part, X_DESC + 30 * i) for i, part in enumerate(desc.split(" "))), W(cur, X_CUR)]
    if out is not None:
        words.append(amount(out, X_OUT))
    if inn is not None:
        words.append(amount(inn, X_IN))
    words.append(amount(bal, X_BAL))
    return L(page, top, *words, *extra)


def holding(page: int, top: float, kind: str, number: str, branch: str, bal: str, cur: str | None = None) -> Line:
    words = [W(kind, 40), W(number, 120), W(branch, 240)]
    if cur:
        words.append(W(cur, 330))
    words.append(W(bal, 400))
    return L(page, top, *words)


def section_header(page: int, top: float, number: str, kind="活期存款") -> Line:
    return L(page, top, W(kind, 40), W("帳號", 100), W(number, 130))


def page_no(n: int, top: float, page: int) -> Word:
    return W(str(n), 20, 26)


# 91: 16 rows whose chain closes from opening 30,000 to closing 34,431; 59: 12 rows from 50,000 to 52,752
ROWS_91 = [("2026/09/01", "交割股款", "2,000", None, "28,000"), ("2026/09/02", "交割股款", None, "1,500", "29,500"),
           ("2026/09/03", "網銀轉帳", "500", None, "29,000"), ("2026/09/04", "交割股款", None, "3,000", "32,000"),
           ("2026/09/05", "交割股款", "1,000", None, "31,000"), ("2026/09/08", "網銀轉帳", None, "2,000", "33,000"),
           ("2026/09/09", "交割股款", "2,500", None, "30,500"), ("2026/09/10", "薪資獎金", None, "4,000", "34,500"),
           ("2026/09/11", "交割股款", "1,200", None, "33,300"), ("2026/09/12", "網銀轉帳", None, "700", "34,000"),
           ("2026/09/15", "交割股款", "3,000", None, "31,000"), ("2026/09/16", "交割股款", None, "2,600", "33,600"),
           ("2026/09/17", "存款利息", None, "31", "33,631"), ("2026/09/18", "交割股款", "1,000", None, "32,631"),
           ("2026/09/22", "網銀轉帳", None, "2,000", "34,631"), ("2026/09/23", "轉帳手續費", "200", None, "34,431")]
ROWS_59 = [("2026/09/01", "網銀轉帳", "1,000", None, "49,000"), ("2026/09/02", "電子預約", "2,000", None, "47,000"),
           ("2026/09/05", "薪資獎金", None, "8,000", "55,000"), ("2026/09/08", "無卡提款", "3,000", None, "52,000"),
           ("2026/09/10", "信用卡款", "4,000", None, "48,000"), ("2026/09/12", "網銀轉帳", None, "6,000", "54,000"),
           ("2026/09/15", "轉帳支取", "1,000", None, "53,000"), ("2026/09/18", "無卡提款", "2,000", None, "51,000"),
           ("2026/09/19", "網銀轉帳", None, "3,000", "54,000"), ("2026/09/22", "電子預約", "1,248", None, "52,752"),
           ("2026/09/25", "網銀轉帳", None, "1,000", "53,752"), ("2026/09/30", "無卡提款", "1,000", None, "52,752")]
HOLDINGS = {"91/TWD": "34,431", "59/TWD": "52,752", "68/TWD": "0", "50/JPY": "150,000", "50/USD": "2.47"}
DECLARED = {"91/TWD": False, "59/TWD": False, "68/TWD": True, "50/JPY": True}


def page_number_line(page: int, top: float, n: int) -> Line:
    return L(page, top, W(str(n), 300, 306))


def statement(rows_91=ROWS_91, rows_59=ROWS_59, holdings=HOLDINGS, *, split_at=10,
              standalone_page_numbers: bool = False) -> list[Line]:
    """Pages: 1 title; 2 holdings (page-number word before 持有明細); 3 交易明細, header, 91 rows up to `split_at`;
    4 page number, repeated header, the rest of 91, 59 header, header, 59 rows, a trailing note line.
    With `standalone_page_numbers` the page numbers are their own lines at the page bottom (top 818) and page 4 has
    no repeated header: the layout of the real file."""
    glued = not standalone_page_numbers
    lines = [L(1, 50, W("國泰世華銀行綜合月對帳單", 40), W("2026/09/01", 300), W("~", 370), W("2026/09/30", 380)),
             L(1, 70, W("親愛的客戶您好", 40)),
             *([page_number_line(1, 818, 1)] if not glued else []),
             L(2, 50, *([page_no(1, 50, 2)] if glued else []), W("持有明細", 40)),
             L(2, 70, W("台幣存款", 40), W("餘額", 100), W("87,183", 140), W("相較上月", 200), W("+1.00%", 260)),
             holding(2, 90, "活期存款", "123456****91", "屏東分行", holdings["91/TWD"]),
             holding(2, 100, "子帳戶", "123456****68", "屏東分行", holdings["68/TWD"]),
             holding(2, 110, "活期存款", "123456****59", "四維分行", holdings["59/TWD"]),
             L(2, 130, W("外幣存款", 40), W("餘額折合台幣", 100), W("30,000", 180), W("相較上月", 240), W("+0.10%", 300)),
             holding(2, 150, "外匯綜合活期存款", "123456****50", "屏東分行", holdings["50/JPY"], "JPY"),
             holding(2, 160, "外匯綜合活期存款", "123456****50", "屏東分行", holdings["50/USD"], "USD"),
             *([page_number_line(2, 818, 2)] if not glued else []),
             L(3, 50, W("交易明細", 40)),
             section_header(3, 70, "123456****91"), header(3, 90)]
    top = 110
    for r in rows_91[:split_at]:
        lines.append(row(3, top, *r))
        top += 12
    lines.append(L(3, top, W("註：上述帳戶如已動用透支額度", 40)))
    if glued:
        lines.append(L(4, 50, page_no(2, 50, 4), header(4, 50).words[0], *header(4, 50).words[1:]))
    else:
        lines.append(page_number_line(3, 818, 3))
    top = 70
    for r in rows_91[split_at:]:
        lines.append(row(4, top, *r))
        top += 12
    lines.append(section_header(4, top, "123456****59"))
    lines.append(header(4, top + 12))
    top += 24
    for r in rows_59:
        lines.append(row(4, top, *r))
        top += 12
    lines.append(L(4, top, W("國泰綜合證券股份有限公司下單之相關交易", 40)))
    lines.append(L(4, top + 12, W("本行重要訊息", 40)))
    if not glued:
        lines.append(page_number_line(4, 818, 4))
    return lines
