"""IB Flex Web Service client pieces: the credentials file reader (the token never leaves this module's values)."""
from __future__ import annotations

import contextlib
import fcntl
import logging
import os
import re
import time
import traceback
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from datetime import date, datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from worker import inbox, parser
from worker.config import WorkerConfig

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
_REFERENCE_RE = re.compile(r"^[0-9]+$")


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
        try:
            message = record.getMessage()
        except Exception:  # a malformed log call must not raise inside the filter
            message = f"{record.msg!s} {record.args!r}"
        record.msg = message.replace(self._secret, "***")
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
        if not any(isinstance(h, logging.NullHandler) for h in lg.handlers):
            lg.addHandler(logging.NullHandler())  # propagate=False must not route records to logging.lastResort


@contextlib.contextmanager
def _redacting(secret: str) -> Iterator[None]:
    root = logging.getLogger()
    redact = _Redact(secret)
    targets = [root, *root.handlers]
    if logging.lastResort is not None:
        targets.append(logging.lastResort)
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
    failure = None
    try:
        response = http.get(url, params=params)
    except httpx.HTTPError as exc:
        failure = type(exc).__name__
    if failure:  # raised outside the except block: the httpx error (and its request URL) is not even the context
        raise FlexError(f"http:{failure}", transient=True)
    status = response.status_code
    if status >= 400:
        raise FlexError(f"http:{status}", transient=status >= 500 or status in (408, 429))
    return response.content


def fetch(http: httpx.Client, creds: Credentials, *, base_url: str, attempts: int, interval_s: float,
          sleep: Callable[[float], None] = time.sleep) -> bytes:
    """SendRequest, then poll GetStatement. The token travels only as the `t` parameter; every other sink is token-free."""
    quiet_http_logging()
    with _redacting(creds.token):
        return _fetch(http, creds, base_url, attempts, interval_s, sleep)


def _fetch(http, creds, base_url, attempts, interval_s, sleep) -> bytes:
    sent = _parse_flex_xml(_get(http, f"{base_url}/SendRequest", {"t": creds.token, "q": creds.query_id, "v": "3"}))
    if sent.tag != "FlexStatementResponse":
        raise FlexError("xml", transient=False)
    if (sent.findtext("Status") or "").strip() != "Success":
        raise _status_error("send", sent)
    reference = (sent.findtext("ReferenceCode") or "").strip()
    if not _REFERENCE_RE.match(reference):
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


class FlexDisabled(RuntimeError):
    """A non-transient Flex failure latched the daily run; `python -m worker flex --unlatch` clears it."""


class AlreadyRunning(RuntimeError):
    """Another flex run holds the flex lock (the statement runs' lock is pipeline.AlreadyRunning)."""


LATCH = "flex-disabled.json"
LOCK = ".flex.lock"
ERROR_CODES = [
    (re.compile(r"^missing FX rate"), "missing_fx"),
    (re.compile(r"^unsupported assetCategory"), "unsupported_asset_category"),
    (re.compile(r"^unsupported cash transaction type"), "unsupported_cash_type"),
    (re.compile(r"^unsupported section"), "unsupported_section"),
    (re.compile(r"^ibCommissionCurrency"), "commission_currency"),
    (re.compile(r"^ambiguous legacy match"), "ambiguous_legacy"),
    (re.compile(r"without (tradeID|transactionID)"), "missing_id"),
]


@contextlib.contextmanager
def _flex_lock(cfg: WorkerConfig) -> Iterator[None]:
    inbox.private_dir(cfg.state_dir)
    fd = os.open(cfg.state_dir / LOCK, os.O_WRONLY | os.O_CREAT, 0o600)
    with os.fdopen(fd, "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise AlreadyRunning(LOCK) from None
        yield


def _save(cfg: WorkerConfig, data: bytes) -> str:
    """Keep the fetched XML (0600 in a 0700 dir); microseconds + pid make the name unique even within one second."""
    directory = inbox.private_dir(cfg.state_dir / "flex")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = directory / f"flex-{stamp}-{os.getpid()}.xml"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return path.name


def _post(cfg: WorkerConfig, http: httpx.Client, data: bytes, dry_run: bool, legacy_until: date | None) -> dict:
    params = {"dry_run": "true" if dry_run else "false"}
    if legacy_until:
        params["legacy_until"] = legacy_until.isoformat()
    failure = None
    try:
        response = http.post("/api/portfolio/imports/ib-flex", params=params,
                             files={"file": ("flex.xml", data, "application/xml")}, timeout=cfg.flex_timeout_s)
    except httpx.HTTPError as exc:
        failure = type(exc).__name__
    if failure:
        raise FlexError(f"portfolio:{failure}", transient=True)
    status = response.status_code
    if status >= 400:
        raise FlexError(f"portfolio:{status}", transient=status >= 500 or status in (408, 429))
    try:
        body = response.json()
    except ValueError:
        raise FlexError("portfolio:body", transient=True) from None
    if not isinstance(body, dict):
        raise FlexError("portfolio:body", transient=True)
    return body


def _error_code(reason: object) -> str:
    text = reason if isinstance(reason, str) else ""
    return next((code for rx, code in ERROR_CODES if rx.search(text)), "other")


def _summarise(body: dict, *, dry_run: bool, source: str, size: int, saved: str | None) -> dict:
    errors = body.get("errors")
    if not isinstance(errors, list):
        raise FlexError("portfolio:body", transient=True)
    counts = {}
    for key in ("parsed", "created", "skipped_duplicates", "legacy_duplicates", "candidates"):
        try:
            counts[key] = int(body[key])
        except (KeyError, TypeError, ValueError):
            raise FlexError("portfolio:body", transient=True) from None
    error_codes: dict[str, int] = {}
    for item in errors:
        code = _error_code(item.get("reason") if isinstance(item, dict) else None)
        error_codes[code] = error_codes.get(code, 0) + 1
    return {"dry_run": dry_run, "source": source, "bytes": size, "saved": saved, "parsed": counts["parsed"],
            "created": counts["created"], "skipped_duplicates": counts["skipped_duplicates"],
            "legacy_duplicates": counts["legacy_duplicates"], "candidates": counts["candidates"],
            "errors": len(errors), "error_codes": error_codes, "recalc_scheduled": bool(body.get("recalc_scheduled")),
            "codes": []}


def run(cfg: WorkerConfig, *, flex_http: httpx.Client, portfolio_http: httpx.Client, dry_run: bool = False,
        query_id: str | None = None, from_file: Path | None = None, legacy_until: date | None = None,
        unlatch: bool = False, sleep: Callable[[float], None] = time.sleep) -> dict:
    """Fetch (or read) the statement, keep it, post it to the portfolio API; failures become codes, never exceptions."""
    latch = cfg.state_dir / LATCH
    with _flex_lock(cfg):
        if latch.exists() and not unlatch:
            raise FlexDisabled("flex disabled until `python -m worker flex --unlatch` succeeds")
        summary: dict = {"dry_run": dry_run, "source": "file" if from_file else "fetch", "bytes": 0, "saved": None, "codes": []}
        try:
            if from_file:
                data, saved = from_file.read_bytes(), None
                summary["bytes"] = len(data)
            else:
                creds = load_credentials(cfg.flex_credentials_file)
                if query_id:
                    if not QUERY_RE.match(query_id):
                        raise ValueError("IB_FLEX_QUERY_ID malformed")
                    creds = Credentials(token=creds.token, query_id=query_id)
                data = fetch(flex_http, creds, base_url=cfg.flex_base_url, attempts=cfg.flex_poll_attempts,
                             interval_s=cfg.flex_poll_interval_s, sleep=sleep)
                saved = summary["saved"] = _save(cfg, data)
                summary["bytes"] = len(data)
            body = _post(cfg, portfolio_http, data, dry_run, legacy_until)
            summary = _summarise(body, dry_run=dry_run, source=summary["source"], size=len(data), saved=saved)
        except FlexError as exc:
            code = str(exc)
            log.info("flex: %s", code)
            if exc.transient:
                summary["codes"] = ["transient", code]
            else:
                parser.write_private_json(cfg.state_dir, latch, {"reason": code, "at": datetime.now(timezone.utc).isoformat()})
                summary["codes"] = ["flex_disabled", code]
            return summary
        if unlatch and not (from_file or query_id or dry_run):
            latch.unlink(missing_ok=True)
        return summary
