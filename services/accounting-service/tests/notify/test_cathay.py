import gzip
import threading
import time as clock
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from notify.cathay import CathayLine, cathay_lines, last_scheduled_run
from notify.config import NotifyConfig
from tests.notify.notify_fakes import make_client

TZ = timezone(timedelta(hours=8))
PENDING = "/api/portfolio/imports/cathay-statement/pending"
LATEST = "/api/portfolio/imports/cathay-statement/runs/latest"
UNREADABLE = [CathayLine("cathay:unreadable", "國泰對帳單狀態讀不到")]
MON_RUN = "2026-10-26T06:41:00.123456+08:00"
TUE_0700 = datetime(2026, 10, 27, 7, 0, tzinfo=TZ)
TUE_0650 = datetime(2026, 10, 27, 6, 50, tzinfo=TZ)


def cfg(base="http://portfolio"):
    return NotifyConfig(
        api_base="http://api", token_file=Path("t"), hub_base_url="https://hub.example.invalid",
        discord_target="discord:1", portfolio_base=base,
    )


def item(day="2026-10-27", status="held", reason="unsupported_category"):
    return {"trade_date": day, "status": status, "reason": reason,
            "since": "2026-10-27T06:41:00+08:00", "last_at": "2026-10-28T06:41:00+08:00"}


def pending(items=(), total=None):
    items = list(items)
    n = len(items) if total is None else total
    return {"items": items, "total": n, "truncated": n > len(items)}


def run(result="ok", at=MON_RUN, code=None):
    return {"run_at": at, "result": result, "code": code, "files_seen": 1, "posted": 1}


def latest(row=None, ok=None):
    return {"latest": row, "latest_ok": ok}


def lines(routes, now=TUE_0650):
    client, log = make_client(routes)
    out = cathay_lines(cfg(), now, lambda **kw: client)
    return out, log


def healthy(items=(), total=None, row=None):
    row = run() if row is None else row
    return {PENDING: pending(items, total), LATEST: latest(row, row)}


def test_off_without_portfolio_base():
    assert cathay_lines(cfg(None), TUE_0700, lambda **kw: pytest.fail("no client")) is None


def test_lines_per_status():
    out, log = lines(healthy([
        item("2026-10-27", "held", "unresolved_symbol:範例電子"),
        item("2026-10-28", "mismatch", None),
        item("2026-10-29", "conflict", None),
        item("2026-10-30", "held", "unsupported_category"),
        item("2026-10-31", "held", "odd_code"),
    ]))
    assert [l.text for l in out] == [
        "10/27 保留：名稱對不到代號 範例電子",
        "10/28 對不上 CSV",
        "10/29 收到不同版本的對帳單",
        "10/30 保留：含融資融券交易",
        "10/31 保留：odd_code",
    ]
    assert [l.key for l in out][:3] == [
        "cathay:2026-10-27:held", "cathay:2026-10-28:mismatch", "cathay:2026-10-29:conflict"]
    assert len(log) == 2


def test_mismatch_item_with_null_reason_is_readable():
    out, _ = lines(healthy([item("2026-10-28", "mismatch", None)]))
    assert out == [CathayLine("cathay:2026-10-28:mismatch", "10/28 對不上 CSV")]


def test_cap_and_more():
    items = [item(f"2026-10-{d:02d}") for d in range(1, 13)]
    out, _ = lines(healthy(items, total=51))
    assert len(out) == 11
    assert out[-1] == CathayLine("cathay:more", "另有 41 天")
    assert out[9].text.startswith("10/10")


def test_pending_lists_any_date():
    out, _ = lines(healthy([item("2026-09-03", "mismatch", None)]))
    assert out[0].text == "09/03 對不上 CSV"


def test_stale_timeline():
    routes = {PENDING: pending(), LATEST: latest(run(), run())}
    out, _ = lines(routes, TUE_0700)
    assert [l.key for l in out] == ["cathay:stale:2026-10-27T06:40:00+08:00"]
    assert out[0].text == "國泰對帳單今天沒有執行"
    out, _ = lines(routes, TUE_0650)
    assert out == []


def test_empty_history_is_stale():
    out, _ = lines({PENDING: pending(), LATEST: latest()}, TUE_0700)
    assert [l.text for l in out] == ["國泰對帳單今天沒有執行"]


def test_stale_uses_latest_not_latest_ok():
    fresh_fail = run("failed", "2026-10-27T06:40:30+08:00", "boom")
    out, _ = lines({PENDING: pending(), LATEST: latest(fresh_fail, run())}, TUE_0700)
    assert [l.key for l in out] == ["cathay:run:failed"]


def test_latched_and_failed_lines():
    at = "2026-10-27T06:40:30+08:00"
    out, _ = lines({PENDING: pending(), LATEST: latest(run("latched", at), None)}, TUE_0700)
    assert out == [CathayLine("cathay:run:latched", "國泰對帳單匯入已停用")]
    out, _ = lines({PENDING: pending(), LATEST: latest(run("failed", at, "password_file"), None)}, TUE_0700)
    assert out == [CathayLine("cathay:run:failed", "國泰對帳單匯入失敗")]


def test_last_scheduled_run_grace_and_day_rollover():
    assert last_scheduled_run(TUE_0700, time(6, 40), 15) == datetime(2026, 10, 27, 6, 40, tzinfo=TZ)
    assert last_scheduled_run(TUE_0650, time(6, 40), 15) == datetime(2026, 10, 26, 6, 40, tzinfo=TZ)
    utc = datetime(2026, 10, 26, 23, 0, tzinfo=timezone.utc)
    assert last_scheduled_run(utc, time(6, 40), 15) == datetime(2026, 10, 27, 6, 40, tzinfo=TZ)


def _bad(path, body):
    routes = healthy()
    routes[path] = body
    return routes


def _timeout_client():
    log = []

    def handler(request):
        log.append(request)
        raise httpx.ConnectTimeout("slow", request=request)

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://api"), log


BAD_CASES = {
    "500": _bad(PENDING, httpx.Response(500, json={})),
    "invalid_json": _bad(PENDING, httpx.Response(200, content=b"{nope")),
    "not_object": _bad(PENDING, httpx.Response(200, json=[])),
    "missing_total": _bad(PENDING, {"items": [], "truncated": False}),
    "truncated_false_but_more": _bad(PENDING, {"items": [], "total": 1, "truncated": False}),
    "truncated_true_but_complete": _bad(PENDING, {"items": [], "total": 0, "truncated": True}),
    "extra_key": _bad(PENDING, {**pending(), "extra": 1}),
    "total_below_items": _bad(PENDING, {"items": [item()], "total": 0, "truncated": False}),
    "truncated_not_bool": _bad(PENDING, {"items": [], "total": 0, "truncated": 0}),
    "bad_date": _bad(PENDING, pending([item("2026-13-40")])),
    "bad_timestamp": _bad(PENDING, pending([{**item(), "since": "yesterday"}])),
    "naive_timestamp": _bad(PENDING, pending([{**item(), "last_at": "2026-10-27T06:41:00"}])),
    "basic_date": _bad(PENDING, pending([item("20261027")])),
    "iso_week_date": _bad(PENDING, pending([item("2026-W44-1")])),
    "basic_timestamp": _bad(PENDING, pending([{**item(), "since": "20261027T064100+0800"}])),
    "space_timestamp": _bad(PENDING, pending([{**item(), "last_at": "2026-10-27 06:41:00+08:00"}])),
    "reason_newline": _bad(PENDING, pending([item(reason="a\nb")])),
    "reason_tab": _bad(PENDING, pending([item(reason="a\tb")])),
    "reason_cr": _bad(PENDING, pending([item(reason="a\rb")])),
    "latest_basic_run_at": _bad(LATEST, latest(run(at="20261027T064000+0800"), None)),
    "gzip_encoded": _bad(PENDING, httpx.Response(
        200, headers={"content-encoding": "gzip"}, content=gzip.compress(b'{"items": []}'))),
    "unknown_status": _bad(PENDING, pending([item(status="weird")])),
    "reason_41": _bad(PENDING, pending([item(reason="x" * 41)])),
    "item_extra_key": _bad(PENDING, pending([{**item(), "amount": 1}])),
    "body_70000": _bad(PENDING, httpx.Response(200, content=b" " * 70000)),
    "latest_bad_result": _bad(LATEST, latest(run("weird"), None)),
    "latest_bad_run_at": _bad(LATEST, latest(run(at="2026-10-27 06:40"), None)),
    "latest_missing_key": _bad(LATEST, {"latest": None}),
    "latest_negative_count": _bad(LATEST, latest({**run(), "posted": -1}, None)),
    "latest_row_extra_key": _bad(LATEST, latest({**run(), "x": 1}, None)),
    "latest_500": _bad(LATEST, httpx.Response(500, json={})),
}


FAULT_PATH = {name: LATEST if name.startswith("latest_") else PENDING for name in BAD_CASES}
TIMEOUT_CASE = {PENDING: httpx.ConnectTimeout("slow")}  # CLI-level fault: the mock raises it


@pytest.mark.parametrize("name", sorted(BAD_CASES))
def test_unreadable_on(name):
    out, log = lines(BAD_CASES[name], TUE_0700)
    assert out == UNREADABLE
    assert log, "the injected client must have been used"
    if name.startswith("latest_"):
        assert len(log) == 2, "both endpoints must have been requested"


def test_canonical_timestamp_forms_are_accepted():
    row = run(at="2026-10-27T06:41:00Z")
    out, _ = lines({PENDING: pending([{**item(), "since": "2026-10-27T06:41:00.5+08:00"}]), LATEST: latest(row, row)})
    assert out != UNREADABLE


def test_unreadable_on_timeout():
    client, log = _timeout_client()
    assert cathay_lines(cfg(), TUE_0700, lambda **kw: client) == UNREADABLE
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
    out, _ = lines(routes, TUE_0700)
    assert out == UNREADABLE
    assert len(yielded) <= 9


def test_compressed_body_is_rejected_before_any_chunk_is_read():
    yielded = []

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            blob = gzip.compress(b" " * (50 * 1024 * 1024))
            for i in range(0, len(blob), 1024):
                yielded.append(1)
                yield blob[i:i + 1024]

    routes = healthy()
    routes[PENDING] = httpx.Response(200, headers={"content-encoding": "gzip"}, stream=Stream())
    out, log = lines(routes, TUE_0700)
    assert out == UNREADABLE
    assert len(yielded) <= 1
    assert log[0].headers["accept-encoding"] == "identity"


def _blocking_stream(first: bytes | None, release: threading.Event):
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            if first is not None:
                yield first
            release.wait(30)  # blocked until the test ends

    return Stream()


@pytest.mark.parametrize("first", [None, b"{}"], ids=["before_first_chunk", "before_eof"])
def test_stalled_request_is_unreadable_within_the_deadline(monkeypatch, first):
    monkeypatch.setattr("notify.cathay.REQUEST_DEADLINE", 0.3)
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


def test_client_factory_receives_base_and_timeout():
    seen = {}
    client, _ = make_client(healthy())

    def factory(**kw):
        seen.update(kw)
        return client

    cathay_lines(cfg(), TUE_0650, factory)
    assert seen == {"base_url": "http://portfolio", "timeout": 10}


def test_default_factory_resolved_at_call_time(monkeypatch):
    client, log = make_client(healthy())
    monkeypatch.setattr("notify.cathay.httpx.Client", lambda **kw: client)
    assert cathay_lines(cfg(), TUE_0650) == []
    assert log

