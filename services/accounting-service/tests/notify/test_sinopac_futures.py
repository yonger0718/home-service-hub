"""永豐期貨對帳單 notifier section (spec §8): the 國泰 section's shape for the futures pending / runs endpoints."""
import gzip
import threading
import time as clock
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from notify import debits, render
from notify.cathay import CathayLine
from notify.cathay import LATEST_PATH as CATHAY_LATEST, PENDING_PATH as CATHAY_PENDING
from notify.config import NotifyConfig
from notify.render import render_daily, render_weekly
from notify.sinopac_futures import FuturesLine, futures_lines
from notify.state import load_state
from tests.notify.notify_fakes import make_client
from tests.notify.test_cathay import _CloseFails, _blocking_stream, _timeout_client
from tests.notify.test_cli import _sent_text, cli_env, cli_env_quiet, main  # noqa: F401 (fixtures)
from tests.notify.test_render import _cathay_weekly, sample_report

TZ = timezone(timedelta(hours=8))
PENDING = "/api/portfolio/imports/sinopac-futures-statement/pending"
LATEST = "/api/portfolio/imports/sinopac-futures-statement/runs/latest"
UNREADABLE_TEXT = "永豐期貨對帳單狀態讀不到"
UNREADABLE = [FuturesLine("futures:unreadable", UNREADABLE_TEXT)]
MON_RUN = "2026-10-26T06:46:00.123456+08:00"
TUE_0700 = datetime(2026, 10, 27, 7, 0, tzinfo=TZ)
TUE_0650 = datetime(2026, 10, 27, 6, 50, tzinfo=TZ)


def cfg(base="http://portfolio", token_file=None):
    return NotifyConfig(
        api_base="http://api", token_file=Path("t"), hub_base_url="https://hub.example.invalid",
        discord_target="discord:1", portfolio_base=base, portfolio_token_file=token_file,
    )


def item(key="2026-11-03", kind="day", status="conflict", reason=None):
    return {"key": key, "kind": kind, "status": status, "reason": reason,
            "since": "2026-11-03T06:46:00+08:00", "last_at": "2026-11-04T06:46:00+08:00"}


def finding(key, reason):
    return item(key, "month" if len(key) == 7 else "day", "findings", reason)


def pending(items=(), total=None):
    items = list(items)
    n = len(items) if total is None else total
    return {"items": items, "total": n, "truncated": n > len(items)}


def run(result="ok", at=MON_RUN, code=None):
    return {"run_at": at, "result": result, "code": code, "files_seen": 2, "posted": 1}


def latest(row=None, ok=None):
    return {"latest": row, "latest_ok": ok}


def healthy(items=(), total=None, row=None):
    row = run() if row is None else row
    return {PENDING: pending(items, total), LATEST: latest(row, row)}


def lines(routes, now=TUE_0650):
    client, log = make_client(routes)
    return futures_lines(cfg(), now, lambda **kw: client), log


def texts(routes, now=TUE_0650):
    out, _ = lines(routes, now)
    return [l.text for l in out]


# --- section off / statuses / codes ------------------------------------------------------------------------------


def test_off_without_portfolio_base():
    assert futures_lines(cfg(None), TUE_0700, lambda **kw: pytest.fail("no client")) is None


def test_conflict_day_and_month():
    out, log = lines(healthy([item("2026-03", "month"), item("2026-11-03")]))
    assert out == [
        FuturesLine("futures:2026-03:conflict", "2026-03 收到不同版本的月對帳單"),
        FuturesLine("futures:2026-11-03:conflict", "11/03 收到不同版本的日對帳單"),
    ]
    assert [r.url.path for r in log] == [PENDING, LATEST]


def test_findings_prev():
    out, _ = lines(healthy([finding("2026-11-05", "prev:10/28")]))
    assert out == [FuturesLine("futures:2026-11-05:findings", "11/05 前日餘額接不上 10/28（中間有一天沒有日對帳單）")]


@pytest.mark.parametrize("key,reason,text", [
    ("2026-03", "gap:1", "2026-03 月對帳單有 1 天沒有日對帳單"),
    ("2026-03", "gap:12", "2026-03 月對帳單有 12 天沒有日對帳單"),
    ("2026-04", "carry", "2026-04 月初餘額接不上 3 月月底"),
    ("2026-01", "carry", "2026-01 月初餘額接不上 12 月月底"),
    ("2026-11", "carry", "2026-11 月初餘額接不上 10 月月底"),
    ("2026-03", "diff:BK", "2026-03 月對帳單對不上：B K"),
    ("2026-03", "diff:BGHKM", "2026-03 月對帳單對不上：B G H K M"),
])
def test_findings_month_codes(key, reason, text):
    assert texts(healthy([finding(key, reason)])) == [text]


def test_several_codes_on_one_key_make_one_line():
    out, _ = lines(healthy([finding("2026-04", "carry,gap:2"), finding("2026-05", "carry,diff:M")]))
    assert out == [
        FuturesLine("futures:2026-04:findings", "2026-04 月初餘額接不上 3 月月底；月對帳單有 2 天沒有日對帳單"),
        FuturesLine("futures:2026-05:findings", "2026-05 月初餘額接不上 4 月月底；月對帳單對不上：M"),
    ]


def test_conflict_and_findings_on_one_key_are_two_lines():
    out, _ = lines(healthy([item("2026-11-05"), finding("2026-11-05", "prev:10/28")]))
    assert [l.key for l in out] == ["futures:2026-11-05:conflict", "futures:2026-11-05:findings"]


def test_items_follow_key_then_status_order():
    out, _ = lines(healthy([finding("2026-11-05", "prev:10/28"), item("2026-03", "month"), item("2026-03-31")]))
    assert [l.key for l in out] == [
        "futures:2026-03:conflict", "futures:2026-03-31:conflict", "futures:2026-11-05:findings"]


# A reason is cut to 40 characters, so a reason of exactly 40 may end mid-code ("gap:3" cut from "gap:31"): its
# last code is not trusted. The codes before it are rendered, and the line ends with the 「另有差異」 pointer.
FALLBACK = "另有差異請到匯入頁查看"
FALLBACK_ONLY = "有差異請到匯入頁查看"


def test_reason_at_the_cap_drops_the_possibly_cut_last_code():
    reason = "carry,diff:BGHKM,gap:12,diff:BGHKM,gap:3"
    assert len(reason) == 40
    assert texts(healthy([finding("2026-04", reason)])) == [
        "2026-04 月初餘額接不上 3 月月底；月對帳單對不上：B G H K M；月對帳單有 12 天沒有日對帳單；"
        f"月對帳單對不上：B G H K M；{FALLBACK}"]


def test_reason_cut_mid_code_renders_what_parses():
    reason = "carry,diff:BGHKM,gap:12,diff:BGHKM,diff:"
    assert len(reason) == 40
    assert texts(healthy([finding("2026-04", reason)])) == [
        "2026-04 月初餘額接不上 3 月月底；月對帳單對不上：B G H K M；月對帳單有 12 天沒有日對帳單；"
        f"月對帳單對不上：B G H K M；{FALLBACK}"]


def test_reason_at_the_cap_with_one_code_is_the_pointer_only():
    reason = "gap:" + "1" * 36
    assert texts(healthy([finding("2026-04", reason)])) == [f"2026-04 {FALLBACK_ONLY}"]


def test_reason_of_39_keeps_its_last_code():
    reason = "carry,diff:BGHKM,gap:2,diff:BGHKM,gap:3"
    assert len(reason) == 39
    assert texts(healthy([finding("2026-04", reason)]))[0].endswith("月對帳單有 3 天沒有日對帳單")


def test_short_reason_keeps_its_last_code():
    assert texts(healthy([finding("2026-03", "carry,gap:1")])) == [
        "2026-03 月初餘額接不上 2 月月底；月對帳單有 1 天沒有日對帳單"]


@pytest.mark.parametrize("key,reason,text", [
    ("2026-03", "weird", f"2026-03 {FALLBACK_ONLY}"),
    ("2026-03", "carry,weird", f"2026-03 月初餘額接不上 2 月月底；{FALLBACK}"),
    ("2026-03", "weird,other", f"2026-03 {FALLBACK_ONLY}"),
    ("2026-03", "carry,weird,gap:1", f"2026-03 月初餘額接不上 2 月月底；月對帳單有 1 天沒有日對帳單；{FALLBACK}"),
    ("2026-03", "carry,,", f"2026-03 月初餘額接不上 2 月月底；{FALLBACK}"),
    ("2026-03", "prev:02/27", f"2026-03 {FALLBACK_ONLY}"),  # prev belongs to a day
    ("2026-11-05", "gap:1", f"11/05 {FALLBACK_ONLY}"),  # gap belongs to a month
    ("2026-11-05", "prev:13/40", f"11/05 {FALLBACK_ONLY}"),
    ("2026-03", "gap:0", f"2026-03 {FALLBACK_ONLY}"),
    ("2026-03", "diff:BX", f"2026-03 {FALLBACK_ONLY}"),
    ("2026-03", "diff:", f"2026-03 {FALLBACK_ONLY}"),
    ("2026-03", "<@123>", f"2026-03 {FALLBACK_ONLY}"),
])
def test_unknown_codes_never_print_the_raw_reason(key, reason, text):
    assert texts(healthy([finding(key, reason)])) == [text]


# --- cap ---------------------------------------------------------------------------------------------------------


def test_cap_and_more():
    items = [item(f"2026-10-{d:02d}") for d in range(1, 13)]
    out, _ = lines(healthy(items, total=51))
    assert len(out) == 11
    assert out[9].text == "10/10 收到不同版本的日對帳單"
    assert out[-1] == FuturesLine("futures:more", "另有 41 項")


def test_no_more_line_when_everything_is_shown():
    out, _ = lines(healthy([item(f"2026-10-{d:02d}") for d in range(1, 11)]))
    assert len(out) == 10 and out[-1].key != "futures:more"


# --- run timeline (06:45 + 15) ----------------------------------------------------------------------------------


def test_stale_timeline():
    routes = healthy()
    out, _ = lines(routes, TUE_0700)
    assert out == [FuturesLine("futures:stale:2026-10-27T06:45:00+08:00", "永豐期貨對帳單今天沒有執行")]
    assert lines(routes, TUE_0650)[0] == []
    assert lines(routes, datetime(2026, 10, 27, 6, 59, 59, tzinfo=TZ))[0] == []


def test_run_at_the_deadline_boundary():
    at_0645 = run(at="2026-10-27T06:45:00+08:00")
    assert lines(healthy(row=at_0645), TUE_0700)[0] == []
    before = run(at="2026-10-27T06:44:59+08:00")
    assert [l.key for l in lines(healthy(row=before), TUE_0700)[0]] == ["futures:stale:2026-10-27T06:45:00+08:00"]
    utc = run(at="2026-10-26T22:46:00Z")  # 06:46 Taipei
    assert lines(healthy(row=utc), TUE_0700)[0] == []


def test_empty_run_history_is_stale():
    assert texts({PENDING: pending(), LATEST: latest()}, TUE_0700) == ["永豐期貨對帳單今天沒有執行"]


def test_empty_run_history_before_the_first_deadline_is_stale_too():
    assert texts({PENDING: pending(), LATEST: latest()}, TUE_0650) == ["永豐期貨對帳單今天沒有執行"]


@pytest.mark.parametrize("code", ["column", "identity:balance", "parse:ValueError", "decrypt", "portfolio:422",
                                  "drive:HttpError", None])
def test_latched_and_failed_lines(code):
    at = "2026-10-27T06:46:30+08:00"
    out, _ = lines({PENDING: pending(), LATEST: latest(run("latched", at, code), None)}, TUE_0700)
    assert out == [FuturesLine("futures:run:latched", "永豐期貨對帳單匯入已停用")]
    out, _ = lines({PENDING: pending(), LATEST: latest(run("failed", at, code), run())}, TUE_0700)
    assert out == [FuturesLine("futures:run:failed", "永豐期貨對帳單匯入失敗")]


def test_an_old_latched_run_stays_latched_not_stale():
    old = run("latched", "2026-10-20T06:46:00+08:00", "column")
    assert texts({PENDING: pending(), LATEST: latest(old, None)}, TUE_0700) == ["永豐期貨對帳單匯入已停用"]


def test_items_then_run_line():
    out, _ = lines({PENDING: pending([item()]), LATEST: latest()}, TUE_0700)
    assert [l.key for l in out] == ["futures:2026-11-03:conflict", "futures:stale:2026-10-27T06:45:00+08:00"]


# --- unreadable -------------------------------------------------------------------------------------------------


def _bad(path, body):
    routes = healthy()
    routes[path] = body
    return routes


BAD_CASES = {
    "500": _bad(PENDING, httpx.Response(500, json={})),
    "404": _bad(PENDING, httpx.Response(404, json={})),
    "invalid_json": _bad(PENDING, httpx.Response(200, content=b"{nope")),
    "not_object": _bad(PENDING, httpx.Response(200, json=[])),
    "missing_total": _bad(PENDING, {"items": [], "truncated": False}),
    "extra_key": _bad(PENDING, {**pending(), "extra": 1}),
    "truncated_false_but_more": _bad(PENDING, {"items": [], "total": 1, "truncated": False}),
    "truncated_true_but_complete": _bad(PENDING, {"items": [], "total": 0, "truncated": True}),
    "total_below_items": _bad(PENDING, {"items": [item()], "total": 0, "truncated": False}),
    "total_bool": _bad(PENDING, {"items": [], "total": False, "truncated": False}),
    "items_over_50": _bad(PENDING, pending([item(f"2026-10-{d % 28 + 1:02d}") for d in range(51)], total=60)),
    "item_missing_kind": _bad(PENDING, pending([{k: v for k, v in item().items() if k != "kind"}])),
    "item_extra_key": _bad(PENDING, pending([{**item(), "amount": 1}])),
    "item_trade_date_key": _bad(PENDING, pending([{**{k: v for k, v in item().items() if k != "key"},
                                                    "trade_date": "2026-11-03"}])),
    "unknown_status": _bad(PENDING, pending([item(status="held")])),
    "unknown_status_mismatch": _bad(PENDING, pending([item(status="mismatch")])),
    "unknown_kind": _bad(PENDING, pending([item(kind="week")])),
    "day_kind_month_key": _bad(PENDING, pending([item("2026-03", "day")])),
    "month_kind_day_key": _bad(PENDING, pending([item("2026-03-01", "month")])),
    "bad_date": _bad(PENDING, pending([item("2026-02-30")])),
    "basic_date": _bad(PENDING, pending([item("20261103")])),
    "bad_month": _bad(PENDING, pending([item("2026-13", "month")])),
    "short_month": _bad(PENDING, pending([item("2026-3", "month")])),
    "key_not_str": _bad(PENDING, pending([item(20261103)])),
    "bad_timestamp": _bad(PENDING, pending([{**item(), "since": "yesterday"}])),
    "naive_timestamp": _bad(PENDING, pending([{**item(), "last_at": "2026-11-03T06:46:00"}])),
    "space_timestamp": _bad(PENDING, pending([{**item(), "last_at": "2026-11-03 06:46:00+08:00"}])),
    "conflict_with_reason": _bad(PENDING, pending([item(reason="prev:10/28")])),
    "findings_null_reason": _bad(PENDING, pending([finding("2026-03", None)])),
    "findings_empty_reason": _bad(PENDING, pending([finding("2026-03", "")])),
    "findings_reason_not_str": _bad(PENDING, pending([finding("2026-03", 7)])),
    "reason_41": _bad(PENDING, pending([finding("2026-03", "carry," * 6 + "gap:1")])),
    "reason_newline": _bad(PENDING, pending([finding("2026-03", "carry\ngap:1")])),
    "gzip_encoded": _bad(PENDING, httpx.Response(
        200, headers={"content-encoding": "gzip"}, content=gzip.compress(b'{"items": []}'))),
    "body_70000": _bad(PENDING, httpx.Response(200, content=b" " * 70000)),
    "latest_500": _bad(LATEST, httpx.Response(500, json={})),
    "latest_invalid_json": _bad(LATEST, httpx.Response(200, content=b"{")),
    "latest_missing_key": _bad(LATEST, {"latest": None}),
    "latest_bad_result": _bad(LATEST, latest(run("skipped"), None)),
    "latest_bad_run_at": _bad(LATEST, latest(run(at="2026-10-27 06:46"), None)),
    "latest_naive_run_at": _bad(LATEST, latest(run(at="2026-10-27T06:46:00"), None)),
    "latest_code_not_str": _bad(LATEST, latest(run("failed", code=3), None)),
    "latest_negative_count": _bad(LATEST, latest({**run(), "posted": -1}, None)),
    "latest_row_extra_key": _bad(LATEST, latest({**run(), "x": 1}, None)),
    "latest_ok_bad": _bad(LATEST, latest(run(), {**run(), "files_seen": "2"})),
    "latest_body_70000": _bad(LATEST, httpx.Response(200, content=b" " * 70000)),
}
FAULT_PATH = {name: LATEST if name.startswith("latest_") else PENDING for name in BAD_CASES}


@pytest.mark.parametrize("name", sorted(BAD_CASES))
def test_unreadable_on(name):
    out, log = lines(BAD_CASES[name], TUE_0700)
    assert out == UNREADABLE
    assert FAULT_PATH[name] in [r.url.path for r in log], "the failing path must have been requested"


def test_cathay_statuses_and_shapes_are_not_accepted():
    cathay_item = {"trade_date": "2026-10-27", "status": "conflict", "reason": None,
                   "since": "2026-10-27T06:41:00+08:00", "last_at": "2026-10-28T06:41:00+08:00"}
    assert lines(_bad(PENDING, pending([cathay_item])), TUE_0700)[0] == UNREADABLE


def test_unreadable_on_timeout():
    client, log = _timeout_client()
    assert futures_lines(cfg(), TUE_0700, lambda **kw: client) == UNREADABLE
    assert log


def test_oversize_stream_stops_reading():
    yielded = []

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(128):
                yielded.append(1)
                yield b"x" * 8192

    routes = healthy()
    routes[PENDING] = httpx.Response(200, stream=Stream())
    assert lines(routes, TUE_0700)[0] == UNREADABLE
    assert len(yielded) <= 9


@pytest.mark.parametrize("first", [None, b"{}"], ids=["before_first_chunk", "before_eof"])
def test_stalled_request_is_unreadable_within_the_futures_deadline(monkeypatch, first):
    monkeypatch.setattr("notify.sinopac_futures.REQUEST_DEADLINE", 0.3)
    release = threading.Event()
    routes = healthy()
    routes[PENDING] = httpx.Response(200, stream=_blocking_stream(first, release))
    started = clock.monotonic()
    try:
        out, _ = lines(routes, TUE_0700)
        elapsed = clock.monotonic() - started
    finally:
        release.set()
    assert out == UNREADABLE
    assert elapsed < 0.3 + 1


def test_cleanup_failure_is_unreadable_not_an_exception():
    client, _ = make_client(healthy())
    client = httpx.Client(transport=_CloseFails(client._transport), base_url="http://api")
    assert futures_lines(cfg(), TUE_0650, lambda **kw: client) == UNREADABLE


def test_client_factory_receives_base_timeout_and_token(tmp_path):
    token = tmp_path / "portfolio-token"
    token.write_text("notify-test-token\n")
    seen = []

    def factory(**kw):
        seen.append(kw)
        return make_client(healthy())[0]

    assert futures_lines(cfg(), TUE_0650, factory) == []
    assert futures_lines(cfg(token_file=token), TUE_0650, factory) == []
    assert seen == [{"base_url": "http://portfolio", "timeout": 10},
                    {"base_url": "http://portfolio", "timeout": 10,
                     "headers": {"Authorization": "Bearer notify-test-token"}}]


def test_unreadable_portfolio_token_file_is_the_unreadable_line(tmp_path):
    client, log = make_client(healthy())
    assert futures_lines(cfg(token_file=tmp_path / "missing"), TUE_0650, lambda **kw: client) == UNREADABLE
    assert not log


def test_default_factory_resolved_at_call_time(monkeypatch):
    client, log = make_client(healthy())
    monkeypatch.setattr("notify.sinopac_futures.httpx.Client", lambda **kw: client)
    assert futures_lines(cfg(), TUE_0650) == []
    assert log


def test_run_time_comes_from_config():
    late = replace(cfg(), sinopac_futures_run_time=time(7, 30), sinopac_futures_run_grace_minutes=20)

    def at(now, run_at):
        return futures_lines(late, now, lambda **kw: make_client(healthy(row=run(at=run_at)))[0])

    assert at(datetime(2026, 10, 27, 7, 50, tzinfo=TZ), "2026-10-27T07:31:00+08:00") == []
    assert at(datetime(2026, 10, 27, 7, 49, tzinfo=TZ), "2026-10-26T07:31:00+08:00") == []  # within the grace
    out = at(datetime(2026, 10, 27, 7, 50, tzinfo=TZ), "2026-10-27T07:29:00+08:00")
    assert [l.key for l in out] == ["futures:stale:2026-10-27T07:30:00+08:00"]


# --- render --------------------------------------------------------------------------------------------------------


def test_daily_futures_block_after_cathay(fx):
    rep = replace(sample_report(fx), cathay=[CathayLine("k", "10/28 對不上 CSV")],
                  futures=[FuturesLine("f", "11/03 收到不同版本的日對帳單")])
    out = "\n\n".join(render_daily(rep, fx.cfg()))
    cathay = out.index("**國泰對帳單**\n• 10/28 對不上 CSV")
    futures = out.index("**永豐期貨對帳單**\n• 11/03 收到不同版本的日對帳單")
    assert cathay < futures


def test_daily_futures_only_counts_as_a_block(fx):
    rep = debits.DailyReport(today=date(2026, 11, 3), blocks=[], manual=[], skips=[],
                             futures=[FuturesLine("f", UNREADABLE_TEXT)])
    (out,) = render_daily(rep, fx.cfg())
    assert out.endswith(f"**永豐期貨對帳單**\n• {UNREADABLE_TEXT}")


def test_weekly_futures_block_between_cathay_and_gaps(fx):
    from notify.digest import Gaps
    rep = replace(_cathay_weekly([("cathay:2026-10-27:held", "10/27 對不上 CSV")],
                                 skips=[fx.skip(7, card_id=100)], gaps=Gaps(["聯名卡"], [], [])),
                  futures=[FuturesLine("f", "2026-03 月對帳單對不上：B K")])
    (out,) = render_weekly(rep, fx.cfg())
    order = [out.index(s) for s in ("**自動扣繳本週跳過**", "**國泰對帳單待處理**",
                                    "**永豐期貨對帳單待處理**\n• 2026-03 月對帳單對不上：B K", "**設定缺漏**")]
    assert order == sorted(order) and "本週沒有待處理" not in out


def test_weekly_futures_only_is_not_the_empty_digest(fx):
    rep = replace(_cathay_weekly([]), futures=[FuturesLine("f", UNREADABLE_TEXT)])
    out = "\n\n".join(render_weekly(rep, fx.cfg()))
    assert f"**永豐期貨對帳單待處理**\n• {UNREADABLE_TEXT}" in out and "本週沒有待處理" not in out


# --- CLI -----------------------------------------------------------------------------------------------------------

CONFLICT = pending([item("2026-11-03")])
FUTURES_BLOCK = "**永豐期貨對帳單**\n• 11/03 收到不同版本的日對帳單"


def test_daily_only_futures_section_sends(cli_env_quiet):
    cli_env_quiet.portfolio[PENDING] = CONFLICT
    assert main(["daily", "--as-of", "2026-11-20", "--send", "--config", cli_env_quiet.cfg]) == 0
    assert len(cli_env_quiet.sent) == 1 and FUTURES_BLOCK in cli_env_quiet.sent[0][1]
    assert "**國泰對帳單**" not in cli_env_quiet.sent[0][1]
    assert {PENDING, LATEST} <= set(cli_env_quiet.portfolio_paths)


def test_daily_quiet_sends_nothing_with_both_sections_healthy(cli_env_quiet):
    assert main(["daily", "--as-of", "2026-11-20", "--send", "--config", cli_env_quiet.cfg]) == 0
    assert cli_env_quiet.sent == []
    assert {PENDING, LATEST, CATHAY_PENDING, CATHAY_LATEST} <= set(cli_env_quiet.portfolio_paths)


def test_daily_dedupe_includes_futures_keys(cli_env_quiet):
    cli_env_quiet.portfolio[PENDING] = CONFLICT
    args = ["daily", "--as-of", "2026-11-20", "--send", "--config", cli_env_quiet.cfg]
    assert main(args) == 0 and len(cli_env_quiet.sent) == 1
    assert "futures:2026-11-03:conflict" in load_state(cli_env_quiet.state_path).last_send.triggers
    assert main(args) == 0 and len(cli_env_quiet.sent) == 1  # same day, same lines
    cli_env_quiet.portfolio[PENDING] = pending([item("2026-11-03"), finding("2026-11-05", "prev:10/28")])
    assert main(args) == 0 and len(cli_env_quiet.sent) == 2  # a new line sends again


def test_daily_stale_futures_run_is_listed(cli_env_quiet):
    cli_env_quiet.portfolio[LATEST] = latest()
    assert main(["daily", "--as-of", "2026-11-20", "--send", "--config", cli_env_quiet.cfg]) == 0
    assert "**永豐期貨對帳單**\n• 永豐期貨對帳單今天沒有執行" in cli_env_quiet.sent[0][1]


def test_daily_futures_run_at_0641_is_stale_at_0700(cli_env_quiet):
    # the 國泰 default row (06:41) is fresh for 國泰 and stale for the 06:45 futures job
    cli_env_quiet.portfolio[LATEST] = latest(run(at="2026-11-20T06:41:00+08:00"), None)
    assert main(["daily", "--as-of", "2026-11-20", "--send", "--config", cli_env_quiet.cfg]) == 0
    assert "永豐期貨對帳單今天沒有執行" in cli_env_quiet.sent[0][1]
    assert "國泰對帳單今天沒有執行" not in cli_env_quiet.sent[0][1]


def test_both_sections_render_independently(cli_env_quiet):
    cli_env_quiet.portfolio[CATHAY_PENDING] = httpx.Response(500, json={})
    cli_env_quiet.portfolio[PENDING] = pending([finding("2026-03", "diff:BK")])
    assert main(["daily", "--as-of", "2026-11-20", "--send", "--config", cli_env_quiet.cfg]) == 0
    (text,) = [t for _, t in cli_env_quiet.sent]
    assert "**國泰對帳單**\n• 國泰對帳單狀態讀不到" in text
    assert "**永豐期貨對帳單**\n• 2026-03 月對帳單對不上：B K" in text
    assert UNREADABLE_TEXT not in text
    # and the other way round
    cli_env_quiet.sent.clear()
    cli_env_quiet.portfolio.clear()
    cli_env_quiet.portfolio[PENDING] = httpx.Response(500, json={})
    assert main(["daily", "--as-of", "2026-11-21", "--send", "--config", cli_env_quiet.cfg]) == 0
    (text,) = [t for _, t in cli_env_quiet.sent]
    assert f"**永豐期貨對帳單**\n• {UNREADABLE_TEXT}" in text and "國泰對帳單" not in text


CLI_CASES = ["500", "invalid_json", "item_missing_kind", "bad_timestamp", "unknown_status", "unknown_kind",
             "body_70000", "reason_41", "latest_500", "latest_bad_run_at"]


@pytest.mark.parametrize("command,as_of", [("daily", "2026-11-03"), ("weekly", "2026-11-07")])
@pytest.mark.parametrize("name", CLI_CASES + ["timeout"])
def test_reader_failure_still_delivers_the_debit_messages(cli_env, command, as_of, name):
    faults = {PENDING: httpx.ConnectTimeout("slow")} if name == "timeout" else {
        path: body for path, body in BAD_CASES[name].items() if path == FAULT_PATH[name]}
    base = _sent_text(cli_env, [command, "--as-of", as_of, "--config", cli_env.config_without_portfolio()])
    cli_env.sent.clear()
    cli_env.portfolio.update(faults)
    assert main([command, "--as-of", as_of, "--send", "--config", cli_env.cfg]) == 0
    assert all(len(t) <= render.LIMIT for _, t in cli_env.sent)
    text = "\n\n".join(t for _, t in cli_env.sent)
    block = "**永豐期貨對帳單**" if command == "daily" else "**永豐期貨對帳單待處理**"
    assert text.replace(f"\n\n{block}\n• {UNREADABLE_TEXT}", "") == base
    assert UNREADABLE_TEXT in text


def test_stalled_futures_still_delivers_the_debit_message(cli_env, monkeypatch):
    monkeypatch.setattr("notify.sinopac_futures.REQUEST_DEADLINE", 0.3)
    base = _sent_text(cli_env, ["daily", "--as-of", "2026-11-03", "--config", cli_env.config_without_portfolio()])
    cli_env.sent.clear()
    release = threading.Event()
    cli_env.portfolio[PENDING] = httpx.Response(200, stream=_blocking_stream(None, release))
    try:
        assert main(["daily", "--as-of", "2026-11-03", "--send", "--config", cli_env.cfg]) == 0
    finally:
        release.set()
    text = "\n\n".join(t for _, t in cli_env.sent)
    assert text.replace(f"\n\n**永豐期貨對帳單**\n• {UNREADABLE_TEXT}", "") == base


def test_default_routes_add_nothing(cli_env):
    base = _sent_text(cli_env, ["daily", "--as-of", "2026-11-03", "--config", cli_env.config_without_portfolio()])
    cli_env.portfolio_paths.clear()
    assert _sent_text(cli_env, ["daily", "--as-of", "2026-11-03", "--config", cli_env.cfg]) == base
    assert {PENDING, LATEST} <= set(cli_env.portfolio_paths)
