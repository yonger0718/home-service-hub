import json
import logging
from pathlib import Path

import httpx
import pytest

from worker import config, flex

TOKEN = "abcdefghijklmnopqrstuv0123"


def _creds(tmp_path: Path, token: str = TOKEN, query_id: str = "1234567") -> Path:
    p = tmp_path / "flex.env"
    p.write_text(f"# ib\nIB_FLEX_TOKEN={token}\nIB_FLEX_QUERY_ID={query_id}\n", encoding="utf-8")
    return p


def test_load_credentials_reads_key_value_lines(tmp_path):
    creds = flex.load_credentials(_creds(tmp_path))
    assert (creds.token, creds.query_id) == (TOKEN, "1234567")


def test_credentials_repr_hides_the_token(tmp_path):
    creds = flex.load_credentials(_creds(tmp_path))
    assert TOKEN not in repr(creds) and TOKEN not in str(creds) and "1234567" in repr(creds)


@pytest.mark.parametrize("token, query_id, key", [
    ("", "1234567", "IB_FLEX_TOKEN"), ("short!", "1234567", "IB_FLEX_TOKEN"), (TOKEN, "homehub-daily", "IB_FLEX_QUERY_ID"), (TOKEN, "", "IB_FLEX_QUERY_ID"),
])
def test_load_credentials_names_only_the_key_on_a_bad_value(tmp_path, token, query_id, key):
    with pytest.raises(ValueError) as exc:
        flex.load_credentials(_creds(tmp_path, token, query_id))
    assert str(exc.value) == f"{key} missing or malformed"
    for secret in (token, query_id):
        assert not secret or secret not in str(exc.value)  # an empty value is a substring of anything: skip it


FLEX = "https://flex.test/AccountManagement/FlexWebService"
SEND_OK = b"<FlexStatementResponse><Status>Success</Status><ReferenceCode>555</ReferenceCode><Url>x</Url></FlexStatementResponse>"
SEND_FAIL = b"<FlexStatementResponse><Status>Fail</Status><ErrorCode>1020</ErrorCode><ErrorMessage>Invalid request</ErrorMessage></FlexStatementResponse>"
PENDING = b"<FlexStatementResponse><Status>Warn</Status><ErrorCode>1019</ErrorCode><ErrorMessage>in progress</ErrorMessage></FlexStatementResponse>"
STATEMENT = b'<FlexQueryResponse queryName="q" type="AF"><FlexStatements count="1"><FlexStatement accountId="U1"/></FlexStatements></FlexQueryResponse>'


class _Flex:
    """A scripted Flex Web Service: `script` is the list of (status, body) answers for GetStatement; `raise_on_send`
    is an exception instance, `raise_factory` a callable taking the request (for exceptions that carry the URL)."""
    def __init__(self, send=SEND_OK, script=((200, STATEMENT),), raise_on_send=None, raise_factory=None):
        self.send, self.script, self.raise_on_send, self.raise_factory, self.urls = send, list(script), raise_on_send, raise_factory, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(str(request.url))
        if request.url.path.endswith("/SendRequest"):
            if self.raise_factory:
                raise self.raise_factory(request)
            if self.raise_on_send:
                raise self.raise_on_send
            return httpx.Response(200, content=self.send)
        status, body = self.script.pop(0)
        return httpx.Response(status, content=body)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _fetch(handler, creds, attempts=3):
    return flex.fetch(_client(handler), creds, base_url=FLEX, attempts=attempts, interval_s=0, sleep=lambda s: None)


def test_fetch_returns_the_statement_after_polling_past_pending(tmp_path):
    svc = _Flex(script=[(200, PENDING), (200, PENDING), (200, STATEMENT)])
    creds = flex.load_credentials(_creds(tmp_path))
    assert _fetch(svc, creds) == STATEMENT
    assert [u.split("?")[0].rsplit("/", 1)[1] for u in svc.urls] == ["SendRequest", "GetStatement", "GetStatement", "GetStatement"]
    assert all(f"t={TOKEN}" in u for u in svc.urls) and "q=1234567" in svc.urls[0] and "q=555" in svc.urls[1]


def test_fetch_gives_up_after_the_poll_budget_without_latching(tmp_path):
    svc = _Flex(script=[(200, PENDING)] * 3)
    with pytest.raises(flex.FlexError) as exc:
        _fetch(svc, flex.load_credentials(_creds(tmp_path)), attempts=3)
    assert str(exc.value) == "get:pending" and exc.value.transient is True


def _status(code: bytes) -> bytes:
    return b"<FlexStatementResponse><Status>Fail</Status><ErrorCode>" + code + b"</ErrorCode><ErrorMessage>m</ErrorMessage></FlexStatementResponse>"


def _request_url_error(request: httpx.Request) -> Exception:
    return httpx.ReadTimeout("timed out", request=request)  # the exception that carries the URL (and the token)


@pytest.mark.parametrize("svc, code, transient", [
    (lambda: _Flex(send=SEND_FAIL), "send:1020", False),
    (lambda: _Flex(send=_status(b"1012")), "send:1012", False),      # token expired: latch
    (lambda: _Flex(send=_status(b"1001")), "send:1001", True),
    (lambda: _Flex(send=_status(b"1018")), "send:1018", True),       # too many requests: retry tomorrow
    (lambda: _Flex(script=[(200, _status(b"1003"))]), "get:1003", True),
    (lambda: _Flex(script=[(200, _status(b"1017"))]), "get:1017", False),
    (lambda: _Flex(script=[(200, _status(b"9999"))]), "get:9999", False),  # unknown code: fail closed
    (lambda: _Flex(send=_status(b"DROP TABLE")), "xml", False),
    (lambda: _Flex(send="<FlexStatementResponse/>".encode("utf-16")), "xml", False),   # not UTF-8
    (lambda: _Flex(send=b"<!DOCTYPE x><FlexStatementResponse><Status>Success</Status></FlexStatementResponse>"), "xml", False),
    (lambda: _Flex(script=[(200, b"<FlexQueryResponse><!ENTITY a 'b'></FlexQueryResponse>")]), "xml", False),
    (lambda: _Flex(script=[(302, b"")]), "http:302", False),
    (lambda: _Flex(script=[(403, b"forbidden")]), "http:403", False),
    (lambda: _Flex(script=[(429, b"slow down")]), "http:429", True),
    (lambda: _Flex(script=[(408, b"timeout")]), "http:408", True),
    (lambda: _Flex(script=[(503, b"later")]), "http:503", True),
    (lambda: _Flex(raise_on_send=httpx.ConnectError("boom")), "http:ConnectError", True),
    (lambda: _Flex(raise_factory=_request_url_error), "http:ReadTimeout", True),
    (lambda: _Flex(send=b"<nope"), "xml", False),
])
def test_fetch_maps_failures_to_codes_without_the_token(tmp_path, caplog, svc, code, transient):
    import traceback
    with caplog.at_level(logging.DEBUG), pytest.raises(flex.FlexError) as exc:
        _fetch(svc(), flex.load_credentials(_creds(tmp_path)))
    assert (str(exc.value), exc.value.transient) == (code, transient)
    assert TOKEN not in str(exc.value) and TOKEN not in repr(exc.value) and exc.value.__cause__ is None
    assert exc.value.__context__ is None or exc.value.__suppress_context__
    if code.startswith("http:") and not code[5:].isdigit():
        assert exc.value.__context__ is None  # a wrapped httpx error is raised outside the except block
    assert TOKEN not in "".join(traceback.format_exception(exc.value))  # the standard traceback, context suppressed
    assert TOKEN not in caplog.text


def test_fetch_keeps_httpx_and_httpcore_debug_lines_out_of_the_log(tmp_path, caplog):
    for name in ("httpx", "httpcore", "httpcore.http11", "httpcore.connection"):
        logging.getLogger(name).setLevel(logging.DEBUG)  # a stray DEBUG level on a child must not leak the URL
    with caplog.at_level(logging.DEBUG):
        _fetch(_Flex(), flex.load_credentials(_creds(tmp_path)))
    assert TOKEN not in caplog.text
    assert "HTTP Request" not in caplog.text


def test_fetch_redacts_the_token_if_anything_logs_it_anyway(tmp_path, caplog):
    class _Loud:
        def __init__(self): self.urls = []
        def __call__(self, request):
            log = logging.getLogger("worker.someone")
            log.warning("url %s", request.url)                     # a future bug that logs the request
            try:
                raise httpx.ReadTimeout("x", request=request)
            except httpx.ReadTimeout:
                log.exception("failed %s", request.url)             # and one that logs the exception with exc_info
                raise
    with caplog.at_level(logging.DEBUG), pytest.raises(flex.FlexError):
        _fetch(_Loud(), flex.load_credentials(_creds(tmp_path)))
    assert TOKEN not in caplog.text and caplog.text.count("***") >= 2 and "ReadTimeout" in caplog.text


def test_fetch_a_timeout_that_carries_the_request_does_not_expose_it(tmp_path):
    with pytest.raises(flex.FlexError) as exc:
        _fetch(_Flex(raise_factory=_request_url_error), flex.load_credentials(_creds(tmp_path)))
    assert not hasattr(exc.value, "request") and TOKEN not in repr(vars(exc.value))


PORTFOLIO_OK = {"parsed": 11, "created": 11, "skipped_duplicates": 0, "legacy_duplicates": 0, "candidates": 11,
                "legacy_pairs": [], "error_counts": {"parse": 2, "ambiguous": 0, "commit": 1, "stale_fx": 0},
                "errors": [{"row_index": 9, "message": "unsupported section Transfers", "reason": "unsupported section Transfers"},
                           {"row_index": 4, "message": "missing FX rate for 2026-06-05 USD", "reason": "missing FX rate for 2026-06-05 USD"},
                           {"row_index": 5, "message": f"something odd {TOKEN}", "reason": f"something odd {TOKEN}"}],  # a reason must never be copied
                "recalc_scheduled": True}


class _Portfolio:
    def __init__(self, status=200, body=PORTFOLIO_OK, raise_exc: Exception | None = None):
        self.status, self.body, self.raise_exc, self.requests = status, body, raise_exc, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((str(request.url), dict(request.headers), request.read()))
        if self.raise_exc:
            raise self.raise_exc
        return httpx.Response(self.status, json=self.body)


def _cfg(tmp_path) -> config.WorkerConfig:
    return config.load({"STATEMENT_STATE_DIR": str(tmp_path / "state"), "STATEMENT_FLEX_CREDENTIALS_FILE": str(_creds(tmp_path)),
                        "STATEMENT_FLEX_BASE_URL": FLEX, "STATEMENT_FLEX_POLL_INTERVAL": "0", "STATEMENT_PORTFOLIO_API_URL": "http://portfolio.test"})


def _run(cfg, svc=None, portfolio=None, **kw):
    svc, portfolio = svc or _Flex(), portfolio or _Portfolio()
    with _client(svc) as fh, httpx.Client(base_url="http://portfolio.test", transport=httpx.MockTransport(portfolio)) as ph:
        return flex.run(cfg, flex_http=fh, portfolio_http=ph, sleep=lambda s: None, **kw), svc, portfolio


def test_run_fetches_saves_and_posts_the_statement(tmp_path, caplog):
    cfg = _cfg(tmp_path)
    with caplog.at_level(logging.INFO):
        summary, svc, portfolio = _run(cfg)
    saved = list((cfg.state_dir / "flex").glob("flex-*.xml"))
    assert len(saved) == 1 and saved[0].read_bytes() == STATEMENT and oct(saved[0].stat().st_mode)[-3:] == "600" and oct((cfg.state_dir / "flex").stat().st_mode)[-3:] == "700"
    assert summary == {"dry_run": False, "source": "fetch", "bytes": len(STATEMENT), "saved": saved[0].name, "parsed": 11, "created": 11,
                       "skipped_duplicates": 0, "legacy_duplicates": 0, "candidates": 11, "errors": 3,
                       "error_codes": {"unsupported_section": 1, "missing_fx": 1, "other": 1}, "recalc_scheduled": True, "stale_fx": 0, "codes": []}
    assert "2026-06-05" not in json.dumps(summary)  # a date from a reason is text, not a code
    url, headers, body = portfolio.requests[0]
    assert url == "http://portfolio.test/api/portfolio/imports/ib-flex?dry_run=false" and STATEMENT in body
    assert TOKEN not in url and TOKEN not in body.decode("latin-1") and TOKEN not in json.dumps(headers) and TOKEN not in json.dumps(summary) and TOKEN not in caplog.text
    assert not (cfg.state_dir / flex.LATCH).exists()


def test_run_dry_run_query_id_and_legacy_until_reach_both_services(tmp_path):
    from datetime import date
    summary, svc, portfolio = _run(_cfg(tmp_path), dry_run=True, query_id="7654321", legacy_until=date(2026, 6, 30))
    assert "q=7654321" in svc.urls[0] and summary["dry_run"] is True
    assert portfolio.requests[0][0] == "http://portfolio.test/api/portfolio/imports/ib-flex?dry_run=true&legacy_until=2026-06-30"


def test_run_from_file_posts_without_calling_ib(tmp_path):
    saved = tmp_path / "old.xml"; saved.write_bytes(STATEMENT)
    summary, svc, portfolio = _run(_cfg(tmp_path), from_file=saved)
    assert svc.urls == [] and summary["source"] == "file" and summary["saved"] is None and len(portfolio.requests) == 1


def test_run_latches_on_a_failed_send_request_and_only_a_plain_unlatch_run_clears_it(tmp_path):
    cfg = _cfg(tmp_path)
    summary, _, portfolio = _run(cfg, svc=_Flex(send=SEND_FAIL))
    assert summary["codes"] == ["flex_disabled", "send:1020"] and portfolio.requests == []
    latch = json.loads((cfg.state_dir / flex.LATCH).read_text(encoding="utf-8"))
    assert latch["reason"] == "send:1020" and TOKEN not in json.dumps(latch) and oct((cfg.state_dir / flex.LATCH).stat().st_mode)[-3:] == "600"
    with pytest.raises(flex.FlexDisabled):
        _run(cfg)
    saved = tmp_path / "old.xml"; saved.write_bytes(STATEMENT)
    for kw in ({"from_file": saved}, {"dry_run": True}, {"query_id": "7654321"}):
        summary, _, _ = _run(cfg, unlatch=True, **kw)   # these prove nothing about the daily credentials
        assert summary["codes"] == [] and (cfg.state_dir / flex.LATCH).exists()
    summary, _, _ = _run(cfg, unlatch=True)
    assert summary["codes"] == [] and not (cfg.state_dir / flex.LATCH).exists()


def test_run_non_plain_failures_report_failed_without_latching(tmp_path):
    cfg = _cfg(tmp_path)
    summary, _, _ = _run(cfg, svc=_Flex(send=SEND_FAIL), query_id="7654321")
    assert summary["codes"] == ["failed", "send:1020"] and not (cfg.state_dir / flex.LATCH).exists()


def test_run_stale_fx_is_a_code_without_a_latch(tmp_path):
    cfg = _cfg(tmp_path)
    body = {**PORTFOLIO_OK, "error_counts": {**PORTFOLIO_OK["error_counts"], "stale_fx": 1}}
    summary = _run(cfg, portfolio=_Portfolio(body=body))[0]
    assert summary["codes"] == ["stale_fx"] and summary["stale_fx"] == 1 and not (cfg.state_dir / flex.LATCH).exists()


def test_run_refuses_to_overlap_itself(tmp_path):
    import fcntl
    cfg = _cfg(tmp_path); cfg.state_dir.mkdir(parents=True, exist_ok=True)
    with open(cfg.state_dir / ".flex.lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(flex.AlreadyRunning):
            _run(cfg)


def test_run_file_names_are_unique_across_two_runs_in_one_second(tmp_path):
    cfg = _cfg(tmp_path)
    _run(cfg); _run(cfg)
    assert len(list((cfg.state_dir / "flex").glob("flex-*.xml"))) == 2


def test_run_keeps_the_saved_xml_when_the_portfolio_post_fails(tmp_path):
    cfg = _cfg(tmp_path)
    summary, _, _ = _run(cfg, portfolio=_Portfolio(raise_exc=httpx.ConnectError("down")))
    assert summary["saved"] == next((cfg.state_dir / "flex").glob("flex-*.xml")).name and summary["bytes"] == len(STATEMENT)
    assert summary["codes"] == ["transient", "portfolio:ConnectError"] and len(list((cfg.state_dir / "flex").glob("flex-*.xml"))) == 1
    assert not (cfg.state_dir / flex.LATCH).exists()
    busy = _run(cfg, portfolio=_Portfolio(status=429, body={"detail": "slow down"}))[0]
    assert busy["codes"] == ["transient", "portfolio:429"] and not (cfg.state_dir / flex.LATCH).exists()
    bad = _run(cfg, portfolio=_Portfolio(status=400, body={"detail": "flex: 1020"}))[0]
    assert bad["codes"] == ["flex_disabled", "portfolio:400"] and (cfg.state_dir / flex.LATCH).exists()


def test_run_transient_fetch_failure_does_not_latch_or_save(tmp_path):
    cfg = _cfg(tmp_path)
    summary, _, portfolio = _run(cfg, svc=_Flex(script=[(503, b"x")]))
    assert summary["codes"] == ["transient", "http:503"] and portfolio.requests == [] and not (cfg.state_dir / "flex").exists()


def test_cli_flex_prints_one_json_line_and_exits_by_codes(tmp_path, monkeypatch, capsys):
    from worker import cli
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(config, "load", lambda env=None: cfg)
    calls = []
    monkeypatch.setattr(flex, "run", lambda c, **kw: calls.append(kw) or {"codes": [], "created": 0})
    assert cli.main(["flex", "--dry-run", "--query-id", "7654321", "--legacy-until", "2026-06-30"]) == 0
    out, err = capsys.readouterr()
    assert json.loads(out.strip()) == {"codes": [], "created": 0} and err == ""
    assert calls[0]["dry_run"] is True and calls[0]["query_id"] == "7654321" and calls[0]["from_file"] is None and calls[0]["unlatch"] is False
    assert str(calls[0]["legacy_until"]) == "2026-06-30"
    monkeypatch.setattr(flex, "run", lambda c, **kw: {"codes": ["transient", "http:503"]})
    assert cli.main(["flex"]) == 1
    out, err = capsys.readouterr()                                        # drain: one JSON line, nothing on stderr
    assert json.loads(out.strip())["codes"] == ["transient", "http:503"] and err == ""
    monkeypatch.setattr(flex, "run", lambda c, **kw: (_ for _ in ()).throw(flex.AlreadyRunning()))
    assert cli.main(["flex"]) == 3
    out, err = capsys.readouterr()
    assert out.strip() == "already running" and err == ""
    monkeypatch.setattr(flex, "run", lambda c, **kw: (_ for _ in ()).throw(RuntimeError(f"boom {TOKEN}")))
    assert cli.main(["flex"]) == 1
    out, err = capsys.readouterr()
    assert out == "" and "flex failed: RuntimeError" in err and TOKEN not in err and "Traceback" not in err


def test_cli_flex_exit_codes_for_missing_credentials_and_latch(tmp_path, monkeypatch, capsys):
    from worker import cli
    import dataclasses
    cfg = dataclasses.replace(_cfg(tmp_path), flex_credentials_file=tmp_path / "absent.env")
    monkeypatch.setattr(config, "load", lambda env=None: cfg)
    assert cli.main(["flex"]) == 2 and "flex credentials file is missing" in capsys.readouterr().err
    cfg2 = dataclasses.replace(cfg, flex_credentials_file=_creds(tmp_path))  # config.load is patched: _cfg() would return cfg
    monkeypatch.setattr(config, "load", lambda env=None: cfg2)
    monkeypatch.setattr(flex, "run", lambda c, **kw: (_ for _ in ()).throw(flex.FlexDisabled("x")))
    assert cli.main(["flex"]) == 1 and "flex disabled" in capsys.readouterr().err


@pytest.mark.parametrize("reason, code", [
    ("missing FX rate for 2026-06-05 USD", "missing_fx"), ("unsupported assetCategory FUT", "unsupported_asset_category"),
    ("unsupported cash transaction type Foo", "unsupported_cash_type"), ("unsupported section Transfers", "unsupported_section"),
    ("ibCommissionCurrency EUR differs", "commission_currency"), ("ambiguous legacy match for trade", "ambiguous_legacy"),
    ("trade without tradeID", "missing_id"), ("unsupported buySell BUY (Ca.)", "unsupported_buy_sell"),
    ("quantity sign disagrees with buySell", "quantity_sign"), ("something else", "other"),
])
def test_error_reasons_map_to_codes(reason, code):
    assert flex._error_code(reason) == code


@pytest.mark.parametrize("body", [
    "not json", [1, 2], {**PORTFOLIO_OK, "errors": "oops"}, {**PORTFOLIO_OK, "created": "many"}, {k: v for k, v in PORTFOLIO_OK.items() if k != "parsed"},
])
def test_run_maps_a_malformed_portfolio_body_to_a_transient_code(tmp_path, body):
    class _Raw(_Portfolio):
        def __call__(self, request):
            if isinstance(self.body, str):
                return httpx.Response(200, content=self.body.encode())
            return super().__call__(request)
    cfg = _cfg(tmp_path)
    summary = _run(cfg, portfolio=_Raw(body=body))[0]
    assert summary["codes"] == ["transient", "portfolio:body"] and not (cfg.state_dir / flex.LATCH).exists()


def test_run_rejects_a_malformed_query_id_override(tmp_path, monkeypatch, capsys):
    from worker import cli
    cfg = _cfg(tmp_path)
    with pytest.raises(ValueError, match="IB_FLEX_QUERY_ID malformed"):
        _run(cfg, query_id="homehub-daily")
    monkeypatch.setattr(config, "load", lambda env=None: cfg)
    assert cli.main(["flex", "--query-id", "x"]) == 2
    assert "IB_FLEX_QUERY_ID malformed" in capsys.readouterr().err


def test_fetch_rejects_a_non_numeric_reference_code(tmp_path):
    send = b"<FlexStatementResponse><Status>Success</Status><ReferenceCode>5a5</ReferenceCode></FlexStatementResponse>"
    with pytest.raises(flex.FlexError) as exc:
        _fetch(_Flex(send=send), flex.load_credentials(_creds(tmp_path)))
    assert str(exc.value) == "xml"


def test_cli_flex_from_file_does_not_need_the_credentials_file(tmp_path, monkeypatch, capsys):
    import dataclasses
    from worker import cli
    cfg = dataclasses.replace(_cfg(tmp_path), flex_credentials_file=tmp_path / "absent.env")
    monkeypatch.setattr(config, "load", lambda env=None: cfg)
    monkeypatch.setattr(flex, "run", lambda c, **kw: {"codes": []})
    assert cli.main(["flex", "--from-file", str(tmp_path / "x.xml")]) == 0
