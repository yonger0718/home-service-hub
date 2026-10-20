"""IB Flex Web Service client pieces: the credentials file reader (the token never leaves this module's values)."""
from __future__ import annotations

import contextlib
import logging
import re
import time
import traceback
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import httpx

log = logging.getLogger("worker")

TOKEN_RE = re.compile(r"^[A-Za-z0-9]{8,128}$")
QUERY_RE = re.compile(r"^[0-9]{1,12}$")


@dataclass(frozen=True)
class Credentials:
    token: str = field(repr=False)
    query_id: str


def load_credentials(path: Path) -> Credentials:
    """Parse `key=value` lines (BOM, `#` comments and blank lines allowed); a bad value names its key only."""
    values: dict[str, str] = {}
    for line in path.read_bytes().decode("utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip()] = value.strip()
    token = values.get("IB_FLEX_TOKEN", "")
    if not TOKEN_RE.match(token):
        raise ValueError("IB_FLEX_TOKEN missing or malformed")
    query_id = values.get("IB_FLEX_QUERY_ID", "")
    if not QUERY_RE.match(query_id):
        raise ValueError("IB_FLEX_QUERY_ID missing or malformed")
    return Credentials(token=token, query_id=query_id)


IB_TRANSIENT_CODES = frozenset({"1003", "1004", "1005", "1006", "1007", "1008", "1009", "1018", "1019", "1021"})
_ERROR_CODE_RE = re.compile(r"^[0-9]{4}$")


class FlexError(RuntimeError):
    """A Flex failure, identified by a code only; it never stores the request, the response or the token."""

    def __init__(self, code: str, *, transient: bool):
        super().__init__(code)
        self.transient = transient


class _Redact(logging.Filter):
    """Replace the secret in the message, the exception and the stack of every record before a handler formats it."""

    def __init__(self, secret: str):
        super().__init__()
        self._secret = secret

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = record.getMessage().replace(self._secret, "***")
        record.args = ()
        if record.exc_info:
            record.exc_text = "".join(traceback.format_exception(*record.exc_info)).replace(self._secret, "***")
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = record.exc_text.replace(self._secret, "***")
        if record.stack_info:
            record.stack_info = record.stack_info.replace(self._secret, "***")
        return True


def quiet_http_logging() -> None:
    """httpx logs every request URL at INFO and httpcore its children at DEBUG; neither may reach a handler."""
    for name in ("httpx", "httpcore"):
        lg = logging.getLogger(name)
        lg.setLevel(logging.WARNING)
        lg.propagate = False


@contextlib.contextmanager
def _redacting(secret: str) -> Iterator[None]:
    root = logging.getLogger()
    redact = _Redact(secret)
    targets = [root, *root.handlers]
    for t in targets:
        t.addFilter(redact)
    try:
        yield
    finally:
        for t in targets:
            t.removeFilter(redact)


def _parse_flex_xml(body: bytes) -> ET.Element:
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise FlexError("xml", transient=False) from None
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        raise FlexError("xml", transient=False)
    try:
        return ET.fromstring(text)
    except ET.ParseError:
        raise FlexError("xml", transient=False) from None


def _status_error(stage: str, root: ET.Element) -> FlexError:
    code = (root.findtext("ErrorCode") or "").strip()
    if not _ERROR_CODE_RE.match(code):
        return FlexError("xml", transient=False)
    return FlexError(f"{stage}:{code}", transient=code in IB_TRANSIENT_CODES)


def _get(http: httpx.Client, url: str, params: dict[str, str]) -> bytes:
    try:
        response = http.get(url, params=params)
    except httpx.HTTPError as exc:
        raise FlexError(f"http:{type(exc).__name__}", transient=True) from None
    status = response.status_code
    if status >= 400:
        raise FlexError(f"http:{status}", transient=status >= 500 or status in (408, 429))
    return response.content


def fetch(http: httpx.Client, creds: Credentials, *, base_url: str, attempts: int, interval_s: float,
          sleep: Callable[[float], None] = time.sleep) -> bytes:
    """SendRequest, then poll GetStatement. The token travels only as the `t` parameter; every other sink is token-free."""
    quiet_http_logging()
    try:
        with _redacting(creds.token):
            return _fetch(http, creds, base_url, attempts, interval_s, sleep)
    except FlexError as exc:
        log.info("flex: %s", exc)
        raise


def _fetch(http, creds, base_url, attempts, interval_s, sleep) -> bytes:
    sent = _parse_flex_xml(_get(http, f"{base_url}/SendRequest", {"t": creds.token, "q": creds.query_id, "v": "3"}))
    if sent.tag != "FlexStatementResponse":
        raise FlexError("xml", transient=False)
    if (sent.findtext("Status") or "").strip() != "Success":
        raise _status_error("send", sent)
    reference = (sent.findtext("ReferenceCode") or "").strip()
    if not reference:
        raise FlexError("xml", transient=False)
    for attempt in range(attempts):
        body = _get(http, f"{base_url}/GetStatement", {"q": reference, "t": creds.token, "v": "3"})
        root = _parse_flex_xml(body)
        if root.tag == "FlexQueryResponse":
            return body
        if root.tag != "FlexStatementResponse":
            raise FlexError("xml", transient=False)
        error = _status_error("get", root)
        if str(error) != "get:1019":
            raise error
        if attempt < attempts - 1:
            sleep(interval_s)
    raise FlexError("get:pending", transient=True)
