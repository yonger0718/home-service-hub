"""Fictional 永豐期貨 statements as positioned words (`pdf.Line` / `pdf.Word`), for the futures parser tests.

Label positions follow the archived layout (spec §2, rev 1.7 census); every name, product, order number and amount is
fictional. The defaults build exactly the statements of the portfolio fixtures `futures_daily_payload.json` and
`futures_monthly_payload.json`. A statement is a list of rows `(page, words)`; tests edit the rows, then `lines()`
turns them into `pdf.Line`s.
"""
from __future__ import annotations

from worker.pdf import Line, Word

Row = tuple[int, tuple[Word, ...]]

TEXT_W = 4.0  # fictional glyph width of a text word
NUM_W = 4.5  # fictional glyph width of a numeric word


def t(text: str, x0: float) -> Word:
    """A text word starting at x0."""
    return Word(text, x0, x0 + TEXT_W * len(text))


def n(text: str, x1: float) -> Word:
    """A numeric word right-aligned at x1."""
    return Word(text, x1 - NUM_W * len(text), x1)


def nc(text: str, centre: float) -> Word:
    """A numeric word centred at `centre`."""
    half = NUM_W * len(text) / 2
    return Word(text, centre - half, centre + half)


def row(page: int, *words: Word) -> Row:
    return page, tuple(sorted(words, key=lambda w: w.x0))


def text(r: Row) -> str:
    return " ".join(w.text for w in r[1])


def lines(rows: list[Row]) -> list[Line]:
    out: list[Line] = []
    tops: dict[int, float] = {}
    for page, words in rows:
        tops[page] = tops.get(page, 40.0) + 12.0
        out.append(Line(page, tops[page], words))
    return out


def find(rows: list[Row], prefix: str, start: int = 0) -> int:
    """Index of the first row at or after `start` whose text starts with `prefix`."""
    return next(i for i in range(start, len(rows)) if text(rows[i]).startswith(prefix))


def labels_row(page: int, labels: tuple[tuple[str, float, float], ...]) -> Row:
    return row(page, *(Word(name, x0, x1) for name, x0, x1 in labels))


def edge(labels: tuple[tuple[str, float, float], ...], name: str) -> float:
    return next(x1 for label, _, x1 in labels if label == name)


def centre(labels: tuple[tuple[str, float, float], ...], name: str) -> float:
    return next((x0 + x1) / 2 for label, x0, x1 in labels if label == name)


# ---------------------------------------------------------------- daily layout (census positions)

D_DEP = (("異動日期", 15.6, 43.6), ("異動序號", 67.4, 95.4), ("類別", 118.6, 132.6), ("異動類別", 185.7, 213.7),
         ("匯出入銀行", 242.4, 277.4), ("作業說明", 327.4, 355.4), ("手續費用", 446.8, 474.8), ("匯出入金額", 544.7, 579.7))
D_TRADE = (("成交日期", 15.6, 43.6), ("委託書號", 67.4, 95.4), ("交易所", 118.6, 139.6), ("商品代號", 152.1, 180.1),
           ("交割月份", 186.1, 214.1), ("履約價格", 239.9, 267.9), ("權別", 276.5, 290.5), ("交易", 304.8, 318.8),
           ("口數", 338.9, 352.9), ("成交價格", 379.3, 407.3), ("期交稅", 439.6, 460.6), ("手續費", 492.8, 513.8),
           ("權利金/平倉損益", 527.8, 579.7))
D_POS = (("成交日期", 15.6, 43.6), ("商品代號", 67.4, 95.4), ("交割月份", 118.6, 146.6), ("履約價格", 211.5, 239.5),
         ("權別", 248.1, 262.1), ("買賣", 276.5, 290.5), ("進場價格", 324.9, 352.9), ("行情價位", 379.3, 407.3),
         ("部位", 421.1, 435.1), ("原始保證金", 439.8, 474.8), ("權利金市值", 478.8, 513.8), ("未沖銷浮動損益", 530.7, 579.7))
D_EQ_B = (("本日餘額", 36.6, 64.6), ("本日權益數", 80.8, 115.8), ("權益總值", 154.8, 182.8), ("原始保證金", 232.9, 267.9),
          ("維持保證金", 317.9, 352.9), ("有價證券抵繳總額", 379.1, 435.1), ("超額保證金", 478.8, 513.8),
          ("追繳保證金", 544.7, 579.7))
D_EQ_A = (("前日餘額", 36.6, 64.6), ("存提淨額", 87.8, 115.8), ("期交稅手續費", 140.8, 182.8),
          ("本日期貨平倉損益淨額", 197.9, 267.9), ("未沖銷期貨浮動損益", 289.9, 352.9), ("權利金收支與轉現價款", 365.1, 435.1),
          ("加收保證金", 478.8, 513.8), ("可動用(出金)保證金", 519.2, 579.7))

DATE = "115/03/09"  # 2026-03-09

EQ_B = {"本日餘額": "151,166.00", "本日權益數": "150,966.00", "權益總值": "151,266.00", "原始保證金": "20,000.00",
        "維持保證金": "15,000.00", "超額保證金": "131,266.00"}
EQ_A = {"前日餘額": "100,000.00", "存提淨額": "50,000.00", "期交稅手續費": "34.00", "本日期貨平倉損益淨額": "1,500.00",
        "未沖銷期貨浮動損益": "-200.00", "權利金收支與轉現價款": "-300.00", "可動用(出金)保證金": "130,966.00"}
TOTAL = {"口數": "2", "期交稅": "4.00", "手續費": "30.00", "權利金/平倉損益": "1,200.00"}


def marker(name: str, *, currency: str = "新台幣(NTT)", prefix: str = "交易幣別：", page: int = 1) -> Row:
    return row(page, t(f"{prefix}{currency}", 15.6), t(f"＊{name}＊", 200.0))


def framing(title: str, page: int, account_words: int) -> list[Row]:
    """Title, addressee line ending 啟 and account line (fictional; the parser never reads the last two)."""
    account = [t("期貨帳號：範例", 15.6), n("0000000", 140.0), t("證券帳號：範例", 160.0), n("0000000", 290.0)]
    if account_words == 2:
        account = [t("期貨帳號：範例", 15.6), t("證券帳號：範例", 160.0)]
    return [row(page, t(title, 250.0)), row(page, t("敬致", 15.6), t("範例客戶先生", 40.0), t("啟", 100.0)),
            row(page, *account)]


def footer(page: int, pages: int) -> Row:
    return row(page, t(f"第{page}頁/共{pages}頁", 280.0))


def values(page: int, labels, vals: dict[str, str]) -> Row:
    return row(page, *(n(v, edge(labels, k)) for k, v in vals.items()))


def cols(labels, vals: dict[str, str]) -> list[Word]:
    return [n(v, edge(labels, k)) for k, v in vals.items()]


def dep_none(date: str = DATE) -> Row:
    return row(1, t(date, 15.6), t("無存提", 67.4))


def dep_in(seq: str, amount: str, date: str = DATE) -> Row:
    return row(1, t(date, 15.6), nc(seq, centre(D_DEP, "異動序號")), t("當日入金", 118.6), t("入", 185.7), t("金", 193.4),
               t("範例銀行", 242.4), n(amount, edge(D_DEP, "匯出入金額")))


def dep_out(seq: str, amount: str, date: str = DATE) -> Row:
    return row(1, t(date, 15.6), nc(seq, centre(D_DEP, "異動序號")), t("當日出金", 118.6), t("網路出金", 185.7),
               t("範例銀行", 242.4), t("範例說明", 327.4), n(amount, edge(D_DEP, "匯出入金額")))


def fill(order: str, product: str, month: str, action: str, vals: dict[str, str], *, right: str | None = None,
         date: str = DATE, exchange: tuple[str, ...] = ("Taiwan", "F")) -> Row:
    words = [t(date, 15.6), t(order, 67.4)]
    if exchange:
        words.append(t(exchange[0], 118.6))
        if len(exchange) > 1:
            words.append(t(exchange[1], 118.6 + TEXT_W * len(exchange[0]) + 1.7))
    words += [t(product, 152.1), t(month, 186.1)]
    if right:
        words.append(t(right, 276.5))
    words.append(t(action, 304.8))
    return row(1, *words, *cols(D_TRADE, vals))


def link(date: str, order: str, vals: dict[str, str]) -> Row:
    return row(1, t("平倉->", 15.6), t(date, 40.0), t(order, 90.0), *cols(D_TRADE, vals))


def trade_sub(vals: dict[str, str]) -> Row:
    return row(1, t("商品小計", 15.6), *cols(D_TRADE, vals))


def trade_total(vals: dict[str, str]) -> Row:
    return row(1, t("總計", 15.6), *cols(D_TRADE, vals))


def trade_none(date: str = DATE) -> Row:
    return row(1, t(date, 15.6), t("無交易", 67.4))


def default_trades(total: dict[str, str] = TOTAL) -> list[Row]:
    return [
        fill("sA1B2-01", "範例期貨", "2026/03", "平賣",
             {"口數": "1", "成交價格": "21,000.00", "期交稅": "4.00", "手續費": "20.00", "權利金/平倉損益": "1,500.00"}),
        link("115/03/02", "sA0Z9-01", {"口數": "1", "成交價格": "20,850.00", "權利金/平倉損益": "1,500.00"}),
        trade_sub({"口數": "1", "成交價格": "21,000.00", "期交稅": "4.00", "手續費": "20.00", "權利金/平倉損益": "1,500.00"}),
        fill("sA1B3-01", "範例選", "2026/03", "新買",
             {"履約價格": "21,000", "口數": "1", "成交價格": "6.00", "手續費": "10.00", "權利金/平倉損益": "-300.00"},
             right="Call"),
        trade_sub({"口數": "1", "成交價格": "6.00", "手續費": "10.00", "權利金/平倉損益": "-300.00"}),
        trade_total(total),
    ]


def pos(product: str, month: str, side: str, vals: dict[str, str], *, right: str | None = None,
        date: str = DATE) -> Row:
    words = [t(date, 15.6), t(product, 67.4), t(month, 118.6)]
    if right:
        words.append(t(right, 248.1))
    words.append(t(side, 276.5))
    return row(1, *words, *cols(D_POS, vals))


def pos_sub(product: str, side: str, vals: dict[str, str], *, right: str | None = None) -> Row:
    words = [t(product, 15.6), t("小計", 67.4)]
    if right:
        words.append(t(right, 248.1))
    words.append(t(side, 276.5))
    return row(1, *words, *cols(D_POS, vals))


def labelled(label: str, amount: str | None = None) -> Row:
    """未沖銷選擇權浮動損益 / 未沖銷選擇權市場價值淨額 / 省組合保證金 / 調整後原始保證金(A - C): the amount under 原始保證金."""
    words = [t(w, 15.6 + 50 * i) for i, w in enumerate(label.split(" "))]
    if amount is not None:
        words.append(n(amount, edge(D_POS, "原始保證金")))
    return row(1, *words)


def margin_a(qty: str, amount: str | None) -> Row:
    glued = f"{amount}[A]" if amount else "[A]"
    return row(1, t("調整前原始保證金", 15.6), n(qty, edge(D_POS, "部位")), Word(glued, 487.0 - NUM_W * len(glued), 487.0))


def margin_d() -> Row:
    return row(1, t("有價證券抵繳總額", 15.6), t("[D]", 90.0))


def margin_net(qty: str, amount: str | None) -> Row:
    words = [t("原始保證金(A", 15.6), t("-", 70.0), t("C", 76.0), t("-", 82.0), t("D)", 88.0),
             n(qty, edge(D_POS, "部位"))]
    if amount is not None:
        words.append(n(amount, edge(D_POS, "原始保證金")))
    return row(1, *words)


def combo_leg(code: str, ratio: str, side: str, qty: str, kind: str) -> Row:
    return row(1, t(code, 70.0), t(ratio, 140.0), t(side, 276.5), n(qty, edge(D_POS, "部位")), t(f"[{kind}", 480.0),
               t("]", 520.0))


def default_positions(*, option_unrealized: str | None = None, option_value: str | None = "300.00",
                      margin_amount: str | None = "20,000.00") -> list[Row]:
    return [
        pos("範例期貨", "2026/04", "買", {"進場價格": "21,020.00", "行情價位": "21,000.00", "部位": "1",
                                       "原始保證金": "20,000.00", "未沖銷浮動損益": "-200.00"}, date="115/03/05"),
        pos_sub("範例期貨", "買", {"進場價格": "21,020.00", "行情價位": "21,000.00", "部位": "1", "原始保證金": "20,000.00",
                                 "未沖銷浮動損益": "-200.00"}),
        pos("範例選", "2026/03", "買", {"履約價格": "21,000", "進場價格": "6.00", "行情價位": "6.00", "部位": "1",
                                     "權利金市值": "300.00"}, right="Call"),
        pos_sub("範例選", "買", {"履約價格": "21,000", "進場價格": "6.00", "行情價位": "6.00", "部位": "1",
                               "權利金市值": "300.00"}, right="Call"),
        *position_tail(qty="2", option_unrealized=option_unrealized, option_value=option_value,
                       margin_amount=margin_amount),
    ]


def position_tail(*, qty: str, option_unrealized: str | None, option_value: str | None, margin_amount: str | None,
                  combo: list[Row] | None = None, net_amount: str | None = "use-margin") -> list[Row]:
    net = margin_amount if net_amount == "use-margin" else net_amount
    return [labelled("未沖銷選擇權浮動損益", option_unrealized), labelled("未沖銷選擇權市場價值淨額", option_value),
            margin_a(qty, margin_amount), *(combo or []), margin_d(), margin_net(qty, net)]


def daily_rows(*, deposits: list[Row] | None = None, trades: list[Row] | None = None,
               positions: list[Row] | None = None, eq_b: dict[str, str] = EQ_B, eq_a: dict[str, str] = EQ_A,
               currency: str = "新台幣(NTT)") -> list[Row]:
    rows = framing("國內期貨日對帳單", 1, 4)
    rows.append(marker("國內市場存提", currency=currency))
    rows.append(labels_row(1, D_DEP))
    rows += deposits if deposits is not None else [dep_in("1", "50,000.00")]
    rows.append(marker("國內市場交易紀錄", currency=currency))
    rows.append(labels_row(1, D_TRADE))
    rows += trades if trades is not None else default_trades()
    rows.append(marker("國內市場留倉彙總", currency=currency))
    rows.append(labels_row(1, D_POS))
    rows += positions if positions is not None else default_positions()
    rows.append(marker("國內市場權益帳", currency=currency))
    rows += [row(1, t("匯率：", 15.6)), labels_row(1, D_EQ_B), values(1, D_EQ_B, eq_b), labels_row(1, D_EQ_A),
             values(1, D_EQ_A, eq_a), row(1, t("選擇權浮動損益，僅為參考值不計入浮動損益權益數中", 15.6)),
             row(1, t("一、範例注意事項：交易人應留意帳戶風險。", 15.6), n("1", 300.0), t("範例", 320.0)), footer(1, 2)]
    rows += [row(2, t("國內期貨日對帳單", 250.0)), row(2, t("範例告知事項，敬請詳閱。", 15.6)), footer(2, 2)]
    return rows


# ---------------------------------------------------------------- monthly layout (census positions)

_M_HEAD = (("交易日", 17.0, 38.0), ("買入", 61.6, 75.6), ("賣出", 82.9, 96.9), ("委託書號", 103.6, 131.6),
           ("單別", 138.8, 152.8), ("商品代號", 169.3, 197.3), ("交割月份", 212.5, 240.5), ("履約價格", 268.2, 296.2),
           ("權別", 304.7, 318.7))
M_DEP = (("日期", 17.0, 31.0), ("出入金類別", 101.6, 136.6), ("出入金內容", 177.7, 212.7), ("出入金銀行帳號", 270.8, 319.8),
         ("出金金額", 465.7, 493.7), ("入金金額", 550.3, 578.3))
M_TRADE = _M_HEAD + (("倉別", 332.9, 346.9), ("成交價格", 381.0, 409.0), ("交易稅", 444.4, 465.4),
                     ("手續費", 500.9, 521.9), ("權利金收付", 543.3, 578.3))
M_CLOSE = _M_HEAD + (("倉別", 332.9, 346.9), ("成交價格", 381.0, 409.0), ("價差損益-支出金額", 414.9, 465.4),
                     ("價差損益-存入金額", 471.3, 521.9), ("參考損益", 550.3, 578.3))
M_OPEN = _M_HEAD + (("買賣別", 329.4, 350.4), ("成交價格", 381.0, 409.0), ("價差損益(支出)", 447.2, 493.7),
                    ("價差損益(存入)", 531.8, 578.3))

ME_LEFT_X1 = 242.0
ME_RIGHT_X1 = 437.0
MONTH_END_DATE = "115/03/31"

# left label, right label, left value, right value (None = blank)
MONTH_END = [
    ("A.前月餘額", None, "90,000.00", None),
    ("B.存提淨額", "U.原始保證金", "10,000.00", "20,000.00"),
    ("G.權利金收支與轉現價款", "V.維持保證金", "-300.00", "15,000.00"),
    ("H.手續費，期交稅合計", "有價證券抵繳總額", "120.00", None),
    ("K.本月期貨平倉損益淨額", "W.超額／追繳保證金", "1,500.00", "80,880.00"),
    ("M.本月餘額", "加收保證金", "101,080.00", None),
    ("N.未沖銷期貨浮動損益", "可動用(出金)保證金", "-200.00", "80,580.00"),
    ("Q.本月權益數", "P.未沖銷選擇權浮動損益", "100,880.00", None),
    ("未沖銷買方權利金市值", None, "300.00", None),
    ("未沖銷賣方權利金市值", None, None, None),
    ("權益總值", None, "101,180.00", None),
]


def qty_word(labels, side: str, qty: str) -> Word:
    return nc(qty, centre(labels, side))


def month_end_row(page: int, left: str, right: str | None, lv: str | None, rv: str | None,
                  date: str = MONTH_END_DATE) -> Row:
    words = [t(date, 17.0), t(left, 59.0 if "." in left else 66.0)]
    if lv is not None:
        words.append(n(lv, ME_LEFT_X1))
    if right is not None:
        words.append(t(right, 271.0 if "." in right else 278.0))
        if rv is not None:
            words.append(n(rv, ME_RIGHT_X1))
    return row(page, *words)


def m_trade(date: str, side: str, qty: str, order: str, product: str, month: str, action: str,
            vals: dict[str, str], *, right: str | None = None, desk: str = "網") -> Row:
    words = [t(date, 17.0), qty_word(M_TRADE, side, qty), t(order, 103.6), t(desk, 138.8), t(product, 169.3),
             t(month, 212.5)]
    if right:
        words.append(t(right, 304.7))
    words.append(t(action, 332.9))
    return row(1, *words, *cols(M_TRADE, vals))


def monthly_rows(*, month_end: list[tuple] | None = None, page2_header: bool = True) -> list[Row]:
    rows = framing("國內期貨月對帳單", 1, 2)
    rows.append(marker("國內市場存提", prefix=""))
    rows.append(labels_row(1, M_DEP))
    rows.append(row(1, t("115/03/02", 17.0), t("當日入金", 101.6), t("新倉入金", 177.7), t("範例銀行範例帳號", 270.8),
                    n("10,000.00", edge(M_DEP, "入金金額"))))
    rows.append(row(1, t("存提淨額(B)", 17.0), n("10,000.00", edge(M_DEP, "入金金額"))))
    rows.append(marker("國內市場交易明細", prefix=""))
    rows.append(labels_row(1, M_TRADE))
    rows.append(m_trade("115/03/09", "賣出", "1", "sA1B2-01", "範例期貨", "2026/03", "平賣",
                        {"成交價格": "21,000.00", "交易稅": "4.00", "手續費": "20.00"}))
    rows.append(m_trade("115/03/09", "買入", "1", "sA1B3-01", "範例選", "2026/03", "新買",
                        {"履約價格": "21,000", "成交價格": "6.00", "手續費": "10.00", "權利金收付": "-300.00"},
                        right="Call"))
    rows.append(row(1, qty_word(M_TRADE, "買入", "1"), qty_word(M_TRADE, "賣出", "1"), t("權利金收入與支出(G)", 110.0),
                    n("-300.00", edge(M_TRADE, "權利金收付"))))
    rows.append(row(1, t("手續費總計(H)", 110.0), n("116.00", edge(M_TRADE, "手續費"))))
    rows.append(row(1, t("期", 110.0), t("-", 116.0), t("期交稅總計(H)", 122.0), n("4.00", edge(M_TRADE, "手續費"))))
    rows.append(row(1, t("權", 110.0), t("-", 116.0), t("期交稅總計(H)", 122.0)))
    rows.append(marker("國內市場沖銷明細", prefix=""))
    rows.append(labels_row(1, M_CLOSE))
    rows.append(row(1, t("115/03/09", 17.0), qty_word(M_CLOSE, "賣出", "1"), t("sA1B2-01", 103.6), t("範例期貨", 169.3),
                    t("2026/03", 212.5), t("平倉價", 332.9), n("21,000.00", edge(M_CLOSE, "成交價格")),
                    n("1,500", edge(M_CLOSE, "價差損益-存入金額"))))
    rows.append(footer(1, 2))
    rows += framing("國內期貨月對帳單", 2, 2)
    if page2_header:
        rows.append(labels_row(2, M_CLOSE))
    rows.append(row(2, t("115/03/02", 17.0), qty_word(M_CLOSE, "買入", "1"), t("sA0Z9-01", 103.6), t("<被平倉>", 169.3),
                    t("原價格", 332.9), n("20,850.00", edge(M_CLOSE, "成交價格"))))
    rows.append(row(2, t("期貨平倉損益淨額(K)", 110.0), n("1,500", edge(M_CLOSE, "價差損益-存入金額"))))
    rows.append(row(2, t("選擇權參考損益合計", 110.0)))
    rows.append(marker("國內市場留倉明細", prefix="", page=2))
    rows.append(labels_row(2, M_OPEN))
    rows.append(row(2, t("115/03/05", 17.0), qty_word(M_OPEN, "買入", "1"), t("sA0Z8-01", 103.6), t("範例期貨", 169.3),
                    t("2026/04", 212.5), n("21,020.00", edge(M_OPEN, "成交價格")),
                    n("200.00", edge(M_OPEN, "價差損益(支出)"))))
    rows.append(row(2, t("小計", 110.0), t("範例期貨", 169.3), t("2026/04", 212.5),
                    n("200.00", edge(M_OPEN, "價差損益(支出)"))))
    rows.append(row(2, qty_word(M_OPEN, "買入", "1"), t("合計", 110.0), t("未沖銷期貨浮動損益(N)", 140.0),
                    n("200.00", edge(M_OPEN, "價差損益(支出)"))))
    rows.append(row(2, t("未沖銷選擇權浮動損益-參考(P)", 110.0)))
    rows.append(marker("月底帳戶餘額明細", prefix="", page=2))
    rows += [month_end_row(2, *spec) for spec in (month_end if month_end is not None else MONTH_END)]
    rows.append(row(2, t("選擇權浮動損益，僅為參考值不計入浮動損益權益數中", 17.0)))
    rows.append(row(2, t("範例告知事項", 17.0), n("10", 77.0), t("日前寄送。", 90.0)))
    rows.append(footer(2, 2))
    return rows
