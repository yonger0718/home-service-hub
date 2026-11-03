from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from notify.terms_config import CardEntry
from notify.terms_input import CardInput, TermsInput
from notify.terms_parse import CardResult, Finding, NextTerms, PeriodEnd, TermsResult, TruncatedSource
from notify.terms_render import render_terms
from notify.terms_state import ShowDecision

GOLDEN = Path(__file__).parent / "golden"
ARCHIVE = Path("/reports/20261103T000000Z")
ON = "https://www.example-bank.com/cards/terms"
OFF = "https://evil-example-bank.com/terms?x=1"
NO = ShowDecision(*([False] * 9))


def golden(name: str) -> str:
    return (GOLDEN / name).read_text(encoding="utf-8").rstrip("\n")


def entry(aid, official, tier="precise"):
    return CardEntry(account_id=aid, official=official, bank_domains=("example-bank.com",), tier=tier)


def card(aid, status="unchanged", **kw):
    base = dict(account_id=aid, status=status, current_period_end=None, findings=(), unverified=(),
                next_terms=None, next_terms_unverified_url=None, sources=(), notes="", truncated=())
    return CardResult(**{**base, **kw})


def show(**kw):
    return replace(NO, **kw)


def finding(**kw):
    base = dict(rule_id=7, field="ends_on", site="2027-03-31", system="2026-12-31", source=ON, quote="valid until")
    return Finding(**{**base, **kw})


def inp(entries, unregistered=(), rules=()):
    return TermsInput(date(2026, 11, 3), "weekly", tuple(CardInput(e, tuple(rules)) for e in entries), unregistered)


RULES = ({"id": 7, "name": "Overseas 3%", "ends_on": "2026-12-31", "rate": "3", "total_cap": None},)


def run(fx, entries, cards, decisions, unregistered=(), log=None, rules=RULES):
    res = TermsResult(tuple(cards), log or {"failed": [], "truncated": []})
    return render_terms(inp(entries, unregistered, rules), res, decisions, fx.cfg(), ARCHIVE)


def full_world(fx):
    entries = [entry(101, "範例 Live+"), entry(102, "範例 Points", "signal"), entry(301, "Example Global"),
               entry(401, "範例 Quiet"), entry(402, "範例 Calm")]
    cards = [
        card(101, "changed", current_period_end=PeriodEnd("2027-03-31", ON, "q"),
             findings=(finding(), finding(rule_id=None, field="missing_rule", site="dining +1% cap 200",
                                          system=None, quote="x" * 100)),
             unverified=(finding(source=OFF),), truncated=(TruncatedSource(ON, True, "first ~40k chars"),)),
        card(102, "signal", notes="points programme looks the same\nsecond line"),
        card(301, "announced", next_terms=NextTerms(True, ("2027-01-01", "2027-03-31"),
                                                    ({"name": "Overseas dining", "rate": "1.5", "cap": "3000",
                                                      "notes": None},), ON, "q")),
        card(401), card(402)]
    dec = {101: show(show_findings=True, show_period=True, show_unverified=True, show_truncated=True),
           102: show(show_signal=True), 301: show(show_announced=True), 401: NO, 402: NO}
    return entries, cards, dec


def test_terms_matches_golden(fx):
    entries, cards, dec = full_world(fx)
    out = run(fx, entries, cards, dec, unregistered=((555, "範例 Orphan"),))
    assert out == [golden("terms.txt")]


def test_quiet_golden(fx):
    out = run(fx, [entry(401, "A"), entry(402, "B")], [card(401), card(402)], {401: NO, 402: NO})
    assert out == [golden("terms_quiet.txt")]


def test_quiet_single_line_only_when_nothing_and_no_unregistered(fx):
    (msg,) = run(fx, [entry(401, "A")], [card(401)], {401: NO})
    assert msg == "📋 回饋條款查核 2026-11-03：1 張卡無變動"


def test_unregistered_disables_single_line(fx):
    (msg,) = run(fx, [entry(401, "A")], [card(401)], {401: NO}, unregistered=((9, "Foo"), (8, "Bar")))
    assert "（1 張卡，0 張有事項）" in msg and "未登錄：Foo（9）、Bar（8）" in msg and "無變動：A" in msg


def test_block_order_findings_announced_signal(fx):
    nt = NextTerms(True, ("2027-01-01", None), ({"name": "R", "rate": "2", "cap": None, "notes": "n"},), ON, "q")
    c = card(101, "changed", findings=(finding(),), next_terms=nt, notes="sig note")
    (msg,) = run(fx, [entry(101, "A")], [c], {101: show(show_findings=True, show_announced=True, show_signal=True)})
    assert msg.index("官網") < msg.index("下期條款已公告") < msg.index("· sig note")
    assert "　- R 2% 上限 無 n" in msg and "2027-01-01～?" in msg


def test_quote_truncated_at_80(fx):
    c = card(101, "changed", findings=(finding(quote="y" * 80), finding(field="rate", quote="z" * 81)))
    (msg,) = run(fx, [entry(101, "A")], [c], {101: show(show_findings=True)})
    assert "　「" + "y" * 80 + "」" in msg
    assert "　「" + "z" * 80 + "…」" in msg and "z" * 81 not in msg


def test_truncated_sources_rendered_every_run(fx):
    c = card(101, truncated=(TruncatedSource(ON, True, "first 40k"),))
    (msg,) = run(fx, [entry(101, "A")], [c], {101: show(show_truncated=True)})
    assert f"· 來源文件過長，僅讀到前段（<{ON}> (first 40k)）" in msg
    assert "無變動" not in msg and "（1 張卡，1 張有事項）" in msg


def test_changed_card_prints_period_line(fx):
    c = card(101, "changed", current_period_end=PeriodEnd("2027-03-31", ON, "q"), findings=(finding(),))
    (msg,) = run(fx, [entry(101, "A")], [c], {101: show(show_findings=True, show_period=True)})
    assert "· 條款期間至 2027-03-31" in msg
    c2 = card(101, current_period_end=PeriodEnd("2027-03-31", ON, "q"))
    (msg2,) = run(fx, [entry(101, "A")], [c2], {101: show(show_period_only=True)})
    assert "· 無差異，條款期間至 2027-03-31" in msg2


def test_unknown_field_falls_back_to_raw_name(fx):
    c = card(101, "changed", findings=(finding(field="weird_field"),))
    (msg,) = run(fx, [entry(101, "A")], [c], {101: show(show_findings=True)})
    assert "Overseas 3%：weird_field 官網 2027-03-31 — <" + ON + ">" in msg  # not a rule-row key: no 系統 part


def test_defang_model_text(fx):
    evil = "see https://evil.com @everyone [x](https://evil.com)"
    c = card(101, "changed", findings=(finding(quote=evil, site="a\n@here"),), notes="https://evil.com\n@everyone")
    (msg,) = run(fx, [entry(101, "A")], [c], {101: show(show_findings=True, show_signal=True)})
    for bad in ("https://evil.com", "@everyone", "@here", "](", "\n@"):
        assert bad not in msg


def test_unverified_prints_host_only(fx):
    c = card(101, "changed", unverified=(finding(source="http://User:pw@Evil.Example.com:99/secret/path?q=1"),),
             next_terms_unverified_url="https://sub.evil.example.org/p/a/t/h")
    (msg,) = run(fx, [entry(101, "A")], [c], {101: show(show_unverified=True)})
    assert "· 未驗證（來源不在銀行網域）：結束日 2027-03-31 — evil.example.com" in msg
    assert "· 下期條款：來源未驗證（sub.evil.example.org）" in msg
    body = "\n".join(ln for ln in msg.splitlines() if not ln.startswith("【"))  # drop the settings-link head
    for bad in ("http", "secret", "pw", "/p/a/t/h"):
        assert bad not in body
    c2 = card(101, "changed", unverified=(finding(source="not a url"),))
    (m2,) = run(fx, [entry(101, "A")], [c2], {101: show(show_unverified=True)})
    assert "— ?" in m2


def test_not_found_count_without_detail_when_digest_matches(fx):
    c = card(101, "not_found", unverified=(finding(source=OFF), finding(source=OFF, field="rate")))
    log = {"failed": [ON + " (403)"], "truncated": []}
    (msg,) = run(fx, [entry(101, "A")], [c], {101: show(show_not_found=True)}, log=log)
    assert "· 找不到官網來源（2 項未驗證）" in msg and f"　- <{ON}> (403)" in msg
    assert "未驗證（來源不在銀行網域）" not in msg
    (m2,) = run(fx, [entry(101, "A")], [card(101, "not_found")], {101: show(show_not_found=True)})
    assert "· 找不到官網來源\n" in m2 + "\n"
    (m3,) = run(fx, [entry(101, "A")], [c], {101: show(show_not_found=True, show_unverified=True)}, log=log)
    assert "未驗證（來源不在銀行網域）：結束日" in m3


def test_long_output_is_chunked(fx):
    entries = [entry(1000 + i, f"Card {i}") for i in range(80)]
    cards = [card(1000 + i, "changed", findings=(finding(quote="q" * 80),)) for i in range(80)]
    dec = {1000 + i: show(show_findings=True) for i in range(80)}
    out = run(fx, entries, cards, dec)
    assert len(out) > 1 and all(len(m) <= 2000 for m in out)


def test_next_terms_unverified_only_card_gets_block(fx):
    c = card(101, next_terms_unverified_url="https://evil.example.org/x/y")
    (msg,) = run(fx, [entry(101, "A"), entry(401, "B")], [c, card(401)],
                 {101: show(show_unverified=True), 401: NO})
    assert "【A】" in msg and "· 下期條款：來源未驗證（evil.example.org）" in msg
    assert "無變動：B" in msg and "無變動：A" not in msg and "/x/y" not in msg


def test_failures_are_per_card(fx):
    other = CardEntry(account_id=102, official="O", bank_domains=("other-bank.com",), tier="precise")
    a, b = "https://www.example-bank.com/a", "https://www.other-bank.com/b"
    log = {"failed": [a + " (403)", b + " (500)", "[unverified source]"], "truncated": []}
    d = {101: show(show_not_found=True), 102: show(show_not_found=True)}
    (msg,) = run(fx, [entry(101, "A"), other], [card(101, "not_found"), card(102, "not_found")], d, log=log)
    first, second = msg.split("【O】")
    assert f"　- <{a}> (403)" in first and b not in first
    assert f"　- <{b}> (500)" in second and a not in second and "[unverified" not in msg


# --- C1: no smuggled link or mention on any print path (final fix A1) ------------------------------------------

SMUGGLE = ["#<https://evil.example/x>", "/<@&1>", "/@everyone", "/a`b`", "/a*b*", "/[x](y)"]
BAD_OUT = ("<https://evil", "evil.example", "<@&", "@everyone", "`", "*", "[x]", "](")


def _parsed_world(tail):
    from notify.terms_parse import parse_result
    from tests.notify import terms_fixtures as tfx
    src = ON + tail
    raw = {"account_id": 101, "status": "changed",
           "findings": [{"rule_id": 7, "field": "ends_on", "site": "2027-03-31", "system": "2026-12-31",
                         "source": src, "quote": "q"}]}
    log = {"failed": [src + " (403)"], "truncated": [{"account_id": 101, "url": src, "read": "r"}]}
    return parse_result(tfx.one_card(raw, fetch_log=log), (entry(101, "A"),))


@pytest.mark.parametrize("tail", SMUGGLE)
def test_smuggled_urls_never_print_on_any_path(fx, tail):
    res = _parsed_world(tail)
    d = {101: show(show_findings=True, show_not_found=True, show_truncated=True, show_unverified=True)}
    (msg,) = render_terms(inp([entry(101, "A")], (), RULES), res, d, fx.cfg(), ARCHIVE)
    body = msg.split("\n", 3)[3]  # drop the header, archive path and the card head
    for bad in BAD_OUT:
        assert bad not in body, (tail, bad)
    assert "· 來源文件過長，僅讀到前段（www.example-bank.com (r)）" in body
    assert "· 未驗證（來源不在銀行網域）：結束日 2027-03-31 — www.example-bank.com" in body
    assert "　- " not in body  # the failed entry is dropped


def test_verified_urls_print_in_angle_brackets(fx):
    from notify.terms_parse import TruncatedSource
    c = card(101, "not_found", findings=(finding(),), truncated=(TruncatedSource(ON, True, "r"),))
    log = {"failed": [ON + " (403)"], "truncated": []}
    (msg,) = run(fx, [entry(101, "A")], [c], {101: show(show_findings=True, show_not_found=True,
                                                           show_truncated=True)}, log=log)
    assert f"— <{ON}>" in msg and f"（<{ON}> (r)）" in msg and f"　- <{ON}> (403)" in msg
    assert "→ <https://example.invalid/hub/accounting/accounts/101/settings>" in msg
    assert f" {ON}" not in msg.replace(f"<{ON}>", "")


# --- I2: system side and labels come from HomeHub (final fix A3) -------------------------------------------------

def test_missing_rule_label_only_for_missing_rule_field(fx):
    c = card(101, "changed", findings=(finding(rule_id=8, field="rate"), finding(field="missing_rule", site="new")))
    (msg,) = run(fx, [entry(101, "A")], [c], {101: show(show_findings=True)})
    assert msg.count("缺規則") == 1 and "· 缺規則：new — <" in msg
    assert "· ?：回饋率 官網 2027-03-31" in msg


def test_system_part_follows_rule_row(fx):
    c = card(101, "changed", findings=(finding(field="total_cap", site="300", system=None),
                                       finding(field="window", site="m", system="x")))
    (msg,) = run(fx, [entry(101, "A")], [c], {101: show(show_findings=True)})
    assert "· Overseas 3%：上限 官網 300 / 系統 無 — <" in msg
    assert "· Overseas 3%：期間 官網 m — <" in msg


def test_unknown_rule_unverified_reason(fx):
    c = card(101, "changed", unverified=(finding(rule_id=99, reason="未知規則"),))
    (msg,) = run(fx, [entry(101, "A")], [c], {101: show(show_unverified=True)})
    assert "· 未驗證（未知規則）：結束日 2027-03-31 — www.example-bank.com" in msg
