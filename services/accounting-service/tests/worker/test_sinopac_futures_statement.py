"""永豐期貨 statement parser (spec 2026-11-25-sinopac-futures-statement §5): fictional positioned-word fixtures only."""
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from tests.worker import futures_fixtures as fx
from tests.worker.futures_fixtures import Word, n, row, t
from worker.sinopac_futures_statement import StatementError, parse_daily, parse_monthly, to_payload

PORTFOLIO = Path(__file__).resolve().parents[3] / "stock-portfolio-service"


def daily(rows=None):
    return parse_daily(fx.lines(rows if rows is not None else fx.daily_rows()))


def monthly(rows=None):
    return parse_monthly(fx.lines(rows if rows is not None else fx.monthly_rows()))


def daily_code(rows) -> str:
    with pytest.raises(StatementError) as err:
        parse_daily(fx.lines(rows))
    return err.value.code


def monthly_code(rows) -> str:
    with pytest.raises(StatementError) as err:
        parse_monthly(fx.lines(rows))
    return err.value.code


def payload(st, file_name="fixture.pdf", sha="a" * 64, file_id="fixture-daily"):
    return to_payload(st, drive_file_id=file_id, sha256=sha, file_name=file_name)


# ---------------------------------------------------------------- daily shapes

def test_daily_fills_and_close_link():
    st = daily()
    assert st.statement_date == date(2026, 3, 9)
    closing, opening = st.fills
    assert (closing["action"], closing["qty"], closing["price"], closing["pnl"]) == (
        "平賣", 1, Decimal("21000"), Decimal("1500"))
    assert closing["closes"] == [
        {"date": date(2026, 3, 2), "order_no": "sA0Z9-01", "qty": 1, "price": Decimal("20850")}]
    assert (opening["strike"], opening["right"], opening["tax"], opening["closes"]) == (Decimal("21000"), "Call", 0, [])
    assert st.fills_total == {"tax": Decimal("4"), "fee": Decimal("30"), "pnl": Decimal("1200")}


def test_closing_fill_links_must_cover_its_quantity():
    rows = fx.daily_rows()
    i = fx.find(rows, "平倉->")
    rows[i] = fx.link("115/03/02", "sA0Z9-01", {"口數": "2", "成交價格": "20,850.00"})
    assert daily_code(rows) == "sequence"
    rows = fx.daily_rows()
    del rows[fx.find(rows, "平倉->")]
    assert daily_code(rows) == "sequence"


def test_link_after_an_opening_fill_rejected():
    rows = fx.daily_rows()
    i = fx.find(rows, "115/03/09 sA1B3-01")
    rows.insert(i + 1, fx.link("115/03/02", "sA0Z9-02", {"口數": "1", "成交價格": "5.00"}))
    assert daily_code(rows) == "sequence"


def no_trade_rows(**kw):
    eq_b = {"本日餘額": "100,000.00", "本日權益數": "100,000.00", "權益總值": "100,000.00", "超額保證金": "100,000.00"}
    eq_a = {"前日餘額": "100,000.00", "可動用(出金)保證金": "100,000.00"}
    base = dict(deposits=[fx.dep_none()], trades=[fx.trade_none()], positions=[row(1, t("無留倉", 15.6))],
                eq_b=eq_b, eq_a=eq_a)
    base.update(kw)
    return fx.daily_rows(**base)


def test_daily_no_trade_day():
    st = daily(no_trade_rows())
    out = payload(st)
    assert (out["fills"], out["fills_total"], out["positions"], out["position_lines"], out["deposits"]) == (
        [], None, [], {}, [])
    assert out["equity"]["balance"] == out["equity"]["total_equity"] == "100000"
    assert out["equity"]["fees_and_tax"] == out["equity"]["margin_call"] == "0"


def test_daily_deposit_and_withdrawal():
    rows = no_trade_rows(deposits=[fx.dep_in("10000001", "50,000.00"), fx.dep_out("10000002", "20,000.00")],
                         eq_b={"本日餘額": "130,000.00", "本日權益數": "130,000.00", "權益總值": "130,000.00",
                               "超額保證金": "130,000.00"},
                         eq_a={"前日餘額": "100,000.00", "存提淨額": "30,000.00", "可動用(出金)保證金": "130,000.00"})
    out = payload(daily(rows))
    assert out["deposits"] == [{"seq": "10000001", "direction": "in", "amount": "50000"},
                               {"seq": "10000002", "direction": "out", "amount": "20000"}]
    assert out["equity"]["deposits_net"] == "30000"


def test_daily_option_and_futures_positions():
    """A sold option's 權利金市值 counts negative: 權益總值 = 本日權益數 + Σ signed premium value."""
    positions = [
        fx.pos("範例期貨", "2026/04", "買", {"進場價格": "21,020.00", "行情價位": "21,000.00", "部位": "1",
                                          "原始保證金": "20,000.00", "未沖銷浮動損益": "-200.00"}, date="115/03/05"),
        fx.pos_sub("範例期貨", "買", {"進場價格": "21,020.00", "行情價位": "21,000.00", "部位": "1",
                                    "原始保證金": "20,000.00", "未沖銷浮動損益": "-200.00"}),
        fx.pos("範例選", "2026/03", "賣", {"履約價格": "22,000", "進場價格": "8.00", "行情價位": "6.00", "部位": "1",
                                        "原始保證金": "5,000.00", "權利金市值": "300.00", "未沖銷浮動損益": "100.00"},
               right="Call", date="115/03/04"),
        fx.pos_sub("範例選", "賣", {"履約價格": "22,000", "進場價格": "8.00", "行情價位": "6.00", "部位": "1",
                                  "原始保證金": "5,000.00", "權利金市值": "300.00", "未沖銷浮動損益": "100.00"},
                   right="Call"),
        fx.pos("範例選", "2026/03", "買", {"履約價格": "20,000", "進場價格": "2.00", "行情價位": "2.00", "部位": "1",
                                        "權利金市值": "100.00"}, right="Put", date="115/03/04"),
        fx.pos_sub("範例選", "買", {"履約價格": "20,000", "進場價格": "2.00", "行情價位": "2.00", "部位": "1",
                                  "權利金市值": "100.00"}, right="Put"),
        *fx.position_tail(qty="3", option_unrealized="100.00", option_value="-200.00", margin_amount="25,000.00"),
    ]
    rows = no_trade_rows(positions=positions,
                         eq_b={"本日餘額": "100,000.00", "本日權益數": "99,800.00", "權益總值": "99,600.00",
                               "原始保證金": "25,000.00", "維持保證金": "19,000.00", "超額保證金": "74,600.00"},
                         eq_a={"前日餘額": "100,000.00", "未沖銷期貨浮動損益": "-200.00",
                               "可動用(出金)保證金": "74,600.00"})
    out = payload(daily(rows))
    sold = out["positions"][1]
    assert (sold["side"], sold["right"], sold["strike"], sold["premium_value"], sold["initial_margin"]) == (
        "賣", "Call", "22000", "300", "5000")
    assert out["positions"][0]["strike"] is None and out["positions"][0]["opened"] == "2026-03-05"
    assert out["equity"]["total_equity"] == "99600"
    assert out["position_lines"] == {"option_unrealized": "100", "option_value_net": "-200"}


def test_daily_combo_margin_lines():
    combo = [fx.combo_leg("ABC21000/21100D6", "1B:1S", "買", "1", "買權價差"), row(1, t("組合合計[B]", 15.6)),
             fx.labelled("省組合保證金", "5,000.00"), fx.labelled("調整後原始保證金(A - C)", "15,000.00")]
    positions = fx.default_positions()
    tail = fx.find(positions, "未沖銷選擇權浮動損益")
    positions[tail:] = fx.position_tail(qty="2", option_unrealized=None, option_value="300.00",
                                        margin_amount="20,000.00", combo=combo, net_amount="15,000.00")
    out = payload(daily(fx.daily_rows(positions=positions)))
    assert out["position_lines"] == {"option_value_net": "300", "combo_margin_saving": "5000",
                                     "margin_after_combo": "15000"}


def test_combo_block_out_of_order_rejected():
    combo = [row(1, t("組合合計[B]", 15.6)), fx.combo_leg("ABC21000/21100D6", "1B:1S", "買", "1", "買權價差"),
             fx.labelled("省組合保證金", "5,000.00"), fx.labelled("調整後原始保證金(A - C)", "15,000.00")]
    positions = fx.default_positions()
    tail = fx.find(positions, "未沖銷選擇權浮動損益")
    positions[tail:] = fx.position_tail(qty="2", option_unrealized=None, option_value="300.00",
                                        margin_amount="20,000.00", combo=combo)
    assert daily_code(fx.daily_rows(positions=positions)) == "sequence"


def test_equity_row_with_blank_columns():
    eq_a = {k: v for k, v in fx.EQ_A.items() if k != "前日餘額"}
    eq_a["存提淨額"] = "150,000.00"  # first statement: no 前日餘額 printed, the balance identity still holds
    out = payload(daily(fx.daily_rows(deposits=[fx.dep_in("1", "150,000.00")], eq_a=eq_a)))
    assert out["equity"]["prev_balance"] == "0"
    eq = out["equity"]
    assert eq["securities_collateral"] == eq["margin_call"] == eq["additional_margin"] == "0"
    assert out["equity"]["deposits_net"] == "150000"


def test_payload_amounts_drop_separators_and_trailing_zeros():
    rows = fx.daily_rows()
    i = fx.find(rows, "115/03/09 sA1B3-01")
    rows[i] = fx.fill("sA1B3-01", "範例選", "2026/03", "新買",
                      {"履約價格": "21,000", "口數": "1", "成交價格": "6.50", "手續費": "10.00",
                       "權利金/平倉損益": "-300.00"}, right="Call")
    assert payload(daily(rows))["fills"][1]["price"] == "6.5"


# ---------------------------------------------------------------- monthly shapes

def test_monthly_month_end_block():
    st = monthly()
    out = payload(st, sha="b" * 64, file_id="fixture-monthly")
    assert (out["month"], out["month_end"], out["open_positions"]) == ("2026-03", "2026-03-31", 1)
    assert out["activity_dates"] == ["2026-03-02", "2026-03-09"]
    assert out["lines"]["W"] == "80880" and out["lines"]["P"] == "0" and out["lines"]["securities_collateral"] == "0"
    assert out["lines"]["long_premium_value"] == "300" and out["lines"]["short_premium_value"] == "0"


def test_monthly_month_from_last_trading_day():
    rows = [(p, tuple(Word("115/03/27", w.x0, w.x1) if w.text == fx.MONTH_END_DATE else w for w in ws))
            for p, ws in fx.monthly_rows()]
    out = payload(monthly(rows), file_name="2026-03_永豐期貨_月對帳單_x.pdf")
    assert (out["month"], out["month_end"], out["warnings"]) == ("2026-03", "2026-03-31", [])


def test_monthly_page_break_without_header_repeat():
    assert monthly(fx.monthly_rows(page2_header=False)).open_positions == 1


def test_monthly_activity_date_outside_month_rejected():
    rows = fx.monthly_rows()
    i = fx.find(rows, "115/03/02 當日入金")
    rows[i] = (rows[i][0], tuple(Word("115/02/27", w.x0, w.x1) if w.text == "115/03/02" else w for w in rows[i][1]))
    assert monthly_code(rows) == "date"


# ---------------------------------------------------------------- column mapping (Review Focus 1)

def test_value_between_labels_rejected():
    rows = fx.daily_rows()
    i = fx.find(rows, "115/03/09 sA1B2-01")
    words = [w for w in rows[i][1] if w.text != "1"]
    rows[i] = row(1, *words, n("1", fx.edge(fx.D_TRADE, "口數") + 5))  # 5 pt off its column, far from the next
    assert daily_code(rows) == "column"
    # two labels 10 pt apart, the value 5 pt from each: it must not land in the nearer column
    rows = fx.daily_rows()
    h = fx.find(rows, "成交日期 委託書號")
    price_x1 = fx.edge(fx.D_TRADE, "成交價格")
    rows[h] = row(1, *(Word(w.text, price_x1 - 10 - 28, price_x1 - 10) if w.text == "口數" else w for w in rows[h][1]))
    i = fx.find(rows, "115/03/09 sA1B2-01")
    words = [w for w in rows[i][1] if w.text != "1"]
    rows[i] = row(1, *words, n("1", price_x1 - 5))
    assert daily_code(rows) == "column"


def test_value_matching_two_labels_rejected():
    rows = fx.daily_rows()
    h = fx.find(rows, "成交日期 委託書號")
    fee_x1 = fx.edge(fx.D_TRADE, "手續費")
    rows[h] = row(1, *(Word(w.text, fee_x1 - 4 - 21, fee_x1 - 4) if w.text == "期交稅" else w for w in rows[h][1]))
    i = fx.find(rows, "115/03/09 sA1B2-01")
    words = [w for w in rows[i][1] if w.text != "4.00"]
    rows[i] = row(1, *words, n("4.00", fee_x1 - 2))  # 2 pt from both 期交稅 and 手續費
    assert daily_code(rows) == "column"


def test_value_in_a_column_the_line_does_not_print_rejected():
    rows = fx.daily_rows()
    i = fx.find(rows, "平倉->")
    rows[i] = row(1, *rows[i][1], n("4.00", fx.edge(fx.D_TRADE, "期交稅")))
    assert daily_code(rows) == "column"


def test_unknown_line_rejected():
    rows = fx.daily_rows()
    rows.insert(fx.find(rows, "商品小計"), row(1, t("範例說明文字", 15.6)))
    assert daily_code(rows) == "line"


# ---------------------------------------------------------------- daily layout guards

def _drop(prefix):
    def f(rows):
        del rows[fx.find(rows, prefix)]
    return f


def _dup(prefix):
    def f(rows):
        i = fx.find(rows, prefix)
        rows.insert(i + 1, rows[i])
    return f


def _swap_markers(rows):
    a, b = fx.find(rows, "交易幣別：新台幣(NTT) ＊國內市場交易紀錄＊"), fx.find(rows, "交易幣別：新台幣(NTT) ＊國內市場留倉彙總＊")
    rows[a], rows[b] = rows[b], rows[a]


def _eq_label_missing(rows):
    i = fx.find(rows, "前日餘額 存提淨額")
    rows[i] = (1, tuple(w for w in rows[i][1] if w.text != "加收保證金"))


def _eq_label_repeated(rows):
    i = fx.find(rows, "本日餘額 本日權益數")
    rows[i] = row(1, *rows[i][1], Word("追繳保證金", 590.0, 625.0))


def _drop_eq_values(rows):
    i = fx.find(rows, "本日餘額 本日權益數")
    del rows[i + 1]


def _double_eq_values(rows):
    i = fx.find(rows, "前日餘額 存提淨額")
    rows.insert(i + 2, rows[i + 1])


def _one_page(rows):
    rows[:] = [r for r in rows if r[0] == 1]


def _three_pages(rows):
    rows.append(row(3, t("範例告知事項", 15.6)))


def _page2(new_row):
    def f(rows):
        rows.insert(len(rows) - 1, new_row)
    return f


def _two_dates(rows):
    i = fx.find(rows, "115/03/09 sA1B3-01")
    rows[i] = (1, tuple(Word("115/03/10", w.x0, w.x1) if w.text == fx.DATE else w for w in rows[i][1]))


def _replace_fill(**kw):
    def f(rows):
        if kw.get("closing"):
            i = fx.find(rows, "115/03/09 sA1B2-01")
            rows[i] = fx.fill("sA1B2-01", "範例期貨", "2026/03", "平賣",
                              {"口數": "1", "成交價格": "21,000.00", "期交稅": "4.00", "手續費": "20.00",
                               "權利金/平倉損益": "1,500.00"}, **kw["closing"])
            return
        i = fx.find(rows, "115/03/09 sA1B3-01")
        vals = {"履約價格": "21,000", "口數": "1", "成交價格": "6.00", "手續費": "10.00", "權利金/平倉損益": "-300.00"}
        if kw.get("no_strike"):
            del vals["履約價格"]
        rows[i] = fx.fill("sA1B3-01", "範例選", "2026/03", "新買", vals, right=kw.get("right"))
    return f


def _position_side(rows):
    i = fx.find(rows, "115/03/05 範例期貨")
    rows[i] = (1, tuple(Word("多", w.x0, w.x1) if w.text == "買" else w for w in rows[i][1]))


DAILY_LAYOUT = [
    ("section_missing", _drop("交易幣別：新台幣(NTT) ＊國內市場留倉彙總＊"), "section"),
    ("section_repeated", _dup("交易幣別：新台幣(NTT) ＊國內市場存提＊"), "section"),
    ("section_out_of_order", _swap_markers, "section"),
    ("header_missing", _drop("成交日期 委託書號"), "header"),
    ("header_repeated", _dup("成交日期 委託書號"), "header"),
    ("equity_label_missing", _eq_label_missing, "header"),
    ("equity_label_repeated", _eq_label_repeated, "header"),
    ("equity_value_row_missing", _drop_eq_values, "line"),
    ("equity_value_row_doubled", _double_eq_values, "line"),
    ("one_page", _one_page, "page2"),
    ("three_pages", _three_pages, "page2"),
    ("page2_marker", _page2(fx.marker("國內市場存提", page=2)), "page2"),
    ("page2_header_row", _page2(fx.labels_row(2, fx.D_TRADE)), "page2"),
    ("page2_dated_line", _page2(row(2, t(fx.DATE, 15.6), t("範例", 67.4))), "page2"),
    ("page2_total", _page2(row(2, t("總計", 15.6), n("1", 352.9))), "page2"),
    ("page2_subtotal", _page2(row(2, t("範例期貨", 15.6), t("小計", 67.4))), "page2"),
    ("foreign_currency", lambda rows: rows.__setitem__(fx.find(rows, "交易幣別：新台幣(NTT) ＊國內市場交易紀錄＊"),
                                                       fx.marker("國內市場交易紀錄", currency="美元(USD)")), "currency"),
    ("foreign_market", lambda rows: rows.__setitem__(fx.find(rows, "交易幣別：新台幣(NTT) ＊國內市場交易紀錄＊"),
                                                     fx.marker("國外市場交易紀錄")), "currency"),
    ("two_dates", _two_dates, "date"),
    ("taiwan_without_f", _replace_fill(closing={"exchange": ("Taiwan",)}), "sequence"),
    ("option_strike_without_right", _replace_fill(right=None), "sequence"),
    ("option_right_without_strike", _replace_fill(right="Call", no_strike=True), "sequence"),
    ("futures_fill_with_right", _replace_fill(closing={"right": "Put"}), "sequence"),
    ("position_side_not_buy_sell", _position_side, "sequence"),
    ("notice_dated_line", lambda rows: rows.insert(fx.find(rows, "第1頁/共2頁"), row(1, t(fx.DATE, 15.6))), "line"),
    ("footer_missing", _drop("第1頁/共2頁"), "line"),
]


@pytest.mark.parametrize("mutate,code", [c[1:] for c in DAILY_LAYOUT], ids=[c[0] for c in DAILY_LAYOUT])
def test_daily_layout_rejected(mutate, code):
    rows = fx.daily_rows()
    mutate(rows)
    assert daily_code(rows) == code


# ---------------------------------------------------------------- monthly layout guards

def _m_insert(before_prefix):
    def f(rows):
        rows.insert(fx.find(rows, before_prefix), row(rows[fx.find(rows, before_prefix)][0], t("範例說明文字", 17.0)))
    return f


def _m_shift_price(rows):
    i = next(k for k, r in enumerate(rows) if "sA1B3-01" in fx.text(r))
    rows[i] = (1, tuple(Word(w.text, w.x0 + 5, w.x1 + 5) if w.text == "6.00" else w for w in rows[i][1]))


def _m_drop_right_label(rows):
    i = fx.find(rows, f"{fx.MONTH_END_DATE} B.存提淨額")
    rows[i] = (rows[i][0], tuple(w for w in rows[i][1] if w.text != "U.原始保證金"))


def _m_misaligned_left_value(rows):
    i = fx.find(rows, f"{fx.MONTH_END_DATE} M.本月餘額")
    rows[i] = (rows[i][0], tuple(Word(w.text, w.x0 - 6, w.x1 - 6) if w.text == "101,080.00" else w for w in rows[i][1]))


MONTHLY_LAYOUT = [
    ("unknown_line_存提", _m_insert("存提淨額(B)"), "line"),
    ("unknown_line_交易明細", _m_insert("手續費總計(H)"), "line"),
    ("unknown_line_沖銷明細", _m_insert("期貨平倉損益淨額(K)"), "line"),
    ("unknown_line_留倉明細", _m_insert("未沖銷選擇權浮動損益-參考(P)"), "line"),
    ("unknown_line_月底帳戶餘額明細", _m_insert(f"{fx.MONTH_END_DATE} 權益總值"), "line"),
    ("section_missing", _drop("新台幣(NTT) ＊國內市場沖銷明細＊"), "section"),
    ("section_repeated", _dup("新台幣(NTT) ＊國內市場存提＊"), "section"),
    ("numeric_word_without_label", _m_shift_price, "column"),
    ("month_end_label_missing", _m_drop_right_label, "line"),
    ("month_end_value_misaligned", _m_misaligned_left_value, "column"),
    ("title", lambda rows: rows.__setitem__(0, row(1, t("國內期貨日對帳單", 250.0))), "title"),
    ("currency", lambda rows: rows.__setitem__(fx.find(rows, "新台幣(NTT) ＊國內市場交易明細＊"),
                                               fx.marker("國內市場交易明細", prefix="", currency="美元(USD)")), "currency"),
]


@pytest.mark.parametrize("mutate,code", [c[1:] for c in MONTHLY_LAYOUT], ids=[c[0] for c in MONTHLY_LAYOUT])
def test_monthly_layout_rejected(mutate, code):
    rows = fx.monthly_rows()
    mutate(rows)
    assert monthly_code(rows) == code


# ---------------------------------------------------------------- identities, each broken alone

def _eq(eq_b=None, eq_a=None):
    return {"eq_b": {**fx.EQ_B, **(eq_b or {})}, "eq_a": {**fx.EQ_A, **(eq_a or {})}}


DAILY_IDENTITIES = [
    ("balance", _eq(eq_a={"前日餘額": "100,001.00"})),
    ("equity", _eq(eq_b={"本日權益數": "150,967.00", "權益總值": "151,267.00"})),
    ("total_equity", _eq(eq_b={"權益總值": "151,267.00"})),
    ("realized", _eq(eq_a={"本日期貨平倉損益淨額": "1,501.00", "前日餘額": "99,999.00"})),
    ("premium", _eq(eq_a={"權利金收支與轉現價款": "-301.00", "前日餘額": "100,001.00"})),
    ("unrealized", _eq(eq_a={"未沖銷期貨浮動損益": "-201.00"}, eq_b={"本日權益數": "150,965.00", "權益總值": "151,265.00"})),
    ("deposits", _eq(eq_a={"存提淨額": "50,001.00", "前日餘額": "99,999.00"})),
    ("total_tax", {**_eq(eq_a={"期交稅手續費": "35.00", "前日餘額": "100,001.00"}),
                   "trades": fx.default_trades({**fx.TOTAL, "期交稅": "5.00"})}),
    ("total_fee", {**_eq(eq_a={"期交稅手續費": "35.00", "前日餘額": "100,001.00"}),
                   "trades": fx.default_trades({**fx.TOTAL, "手續費": "31.00"})}),
    ("total_pnl", {"trades": fx.default_trades({**fx.TOTAL, "權利金/平倉損益": "1,201.00"})}),
    ("fees", _eq(eq_a={"期交稅手續費": "35.00", "前日餘額": "100,001.00"})),
    ("option_value", {"positions": fx.default_positions(option_value="301.00")}),
    ("option_unrealized", {"positions": fx.default_positions(option_unrealized="1.00")}),
    ("position_margin", {"positions": fx.default_positions(margin_amount="20,001.00")}),
]


@pytest.mark.parametrize("name,kw", DAILY_IDENTITIES, ids=[c[0] for c in DAILY_IDENTITIES])
def test_identity_broken_alone(name, kw):
    assert daily_code(fx.daily_rows(**kw)) == f"identity:{name}"


def _month_end(**changes):
    out = []
    for left, right, lv, rv in fx.MONTH_END:
        lv = changes.get(left, lv)
        out.append((left, right, lv, rv))
    return out


MONTHLY_IDENTITIES = [
    ("month_balance", _month_end(**{"A.前月餘額": "90,001.00"})),
    ("month_equity", _month_end(**{"Q.本月權益數": "100,881.00", "權益總值": "101,181.00"})),
    ("month_total_equity", _month_end(**{"權益總值": "101,181.00"})),
]


@pytest.mark.parametrize("name,month_end", MONTHLY_IDENTITIES, ids=[c[0] for c in MONTHLY_IDENTITIES])
def test_monthly_identity_broken_alone(name, month_end):
    assert monthly_code(fx.monthly_rows(month_end=month_end)) == f"identity:{name}"


# ---------------------------------------------------------------- payload

def test_filename_date_differs_warning():
    assert payload(daily(), file_name="2026-03-10_永豐期貨_日對帳單_x_01_2026年3月10日期貨日對帳單.pdf")["warnings"] == [
        "filename_date_differs"]
    assert payload(daily(), file_name="2026-03-09_永豐期貨_日對帳單_x_01_2026年3月9日期貨日對帳單.pdf")["warnings"] == []
    assert payload(monthly(), file_name="2026-04_永豐期貨_月對帳單_x_01_2026年04月期貨月對帳單.pdf")["warnings"] == [
        "filename_date_differs"]


def test_payload_matches_portfolio_fixture():
    fixtures = PORTFOLIO / "tests" / "fixtures"
    expected_daily = json.loads((fixtures / "futures_daily_payload.json").read_text(encoding="utf-8"))
    expected_monthly = json.loads((fixtures / "futures_monthly_payload.json").read_text(encoding="utf-8"))
    assert payload(daily()) == expected_daily
    assert payload(monthly(), sha="b" * 64, file_id="fixture-monthly") == expected_monthly


def test_error_never_carries_statement_content():
    rows = fx.daily_rows(eq_a={**fx.EQ_A, "前日餘額": "100,001.00"})
    with pytest.raises(StatementError) as err:
        parse_daily(fx.lines(rows))
    assert str(err.value) == "identity:balance" and "100" not in repr(err.value)
