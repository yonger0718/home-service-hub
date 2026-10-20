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
    (lambda: _Flex(send=_status(b"1018")), "send:1018", True),       # too many requests: retry tomorrow
    (lambda: _Flex(script=[(200, _status(b"1003"))]), "get:1003", True),
    (lambda: _Flex(script=[(200, _status(b"1017"))]), "get:1017", False),
    (lambda: _Flex(script=[(200, _status(b"9999"))]), "get:9999", False),  # unknown code: fail closed
    (lambda: _Flex(send=_status(b"DROP TABLE")), "xml", False),
    (lambda: _Flex(send="<FlexStatementResponse/>".encode("utf-16")), "xml", False),   # not UTF-8
    (lambda: _Flex(send=b"<!DOCTYPE x><FlexStatementResponse><Status>Success</Status></FlexStatementResponse>"), "xml", False),
    (lambda: _Flex(script=[(200, b"<FlexQueryResponse><!ENTITY a 'b'></FlexQueryResponse>")]), "xml", False),
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
    assert exc.value.__context__ is None or exc.value.__suppress_context__  # a wrapped httpx error is raised `from None`
    assert TOKEN not in "".join(traceback.format_exception(exc.value))  # the standard traceback, context suppressed
    assert TOKEN not in caplog.text


def test_fetch_keeps_httpx_and_httpcore_debug_lines_out_of_the_log(tmp_path, caplog):
    for name in ("httpx", "httpcore", "httpcore.http11", "httpcore.connection"):
        logging.getLogger(name).setLevel(logging.DEBUG)  # a stray DEBUG level on a child must not leak the URL
    with caplog.at_level(logging.DEBUG):
        _fetch(_Flex(), flex.load_credentials(_creds(tmp_path)))
    assert TOKEN not in caplog.text


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
