"""永豐期貨 statement import status (spec §8): read from the portfolio API, rendered as notice lines.

Built as the 國泰 section (notify/cathay.py): its own client, the same 64 KiB body cap and request deadline, strict
validation of the futures pending / run shapes. Any failure inside it collapses to the single unreadable line, so the
debit, 國泰 and weekly items are never affected.

A findings item's `reason` is the portfolio's codes joined by `,` (`prev:<MM/DD>`, `gap:<n>`, `carry`,
`diff:<letters>`) cut to 40 characters. Only parsed codes are rendered, never the raw text. A reason of exactly 40
characters may end mid-code, so its last code is not trusted; that, or any code that does not parse for the item's
kind, adds one 「另有差異請到匯入頁查看」 segment at the end of the line; when no code parses at all the line is
`<key> 有差異請到匯入頁查看`. The run-row checks are 國泰's: both run endpoints share one server model.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime

import httpx

from .cathay import _check_latest, _Deadline, _get_bounded, _is_int, _timestamp, last_scheduled_run
from .config import NotifyConfig, read_token

PENDING_PATH = "/api/portfolio/imports/sinopac-futures-statement/pending"
LATEST_PATH = "/api/portfolio/imports/sinopac-futures-statement/runs/latest"
REQUEST_DEADLINE = 10.0  # wall-clock seconds for one whole request (see notify/cathay.py)
MAX_PENDING = 50
MAX_ITEMS = 10
MAX_REASON = 40
MAX_LINE = 120
STATUSES = frozenset({"conflict", "findings"})
KINDS = frozenset({"day", "month"})
UNREADABLE = "永豐期貨對帳單狀態讀不到"
FALLBACK = "另有差異請到匯入頁查看"  # after parsed codes
FALLBACK_ONLY = "有差異請到匯入頁查看"  # when no code parses
ITEM_KEYS = {"key", "kind", "status", "reason", "since", "last_at"}
_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_PREV = re.compile(r"^prev:(0[1-9]|1[0-2])/(0[1-9]|[12]\d|3[01])$")
_GAP = re.compile(r"^gap:([1-9]\d{0,2})$")
_DIFF = re.compile(r"^diff:([BGHKM]+)$")


@dataclass(frozen=True)
class FuturesLine:
    key: str
    text: str


def _check_key(key: object, kind: str) -> None:
    if not isinstance(key, str):
        raise ValueError("key")
    if kind == "day":
        if date.fromisoformat(key).isoformat() != key:  # rejects basic and week forms
            raise ValueError("key")
    elif not _MONTH.match(key):
        raise ValueError("key")


def _check_pending(data: dict) -> list[dict]:
    if set(data) != {"items", "total", "truncated"}:
        raise ValueError("pending keys")
    items = data["items"]
    if not isinstance(items, list) or len(items) > MAX_PENDING or not isinstance(data["truncated"], bool):
        raise ValueError("pending shape")
    if not _is_int(data["total"], len(items)):
        raise ValueError("total")
    if data["truncated"] != (data["total"] > len(items)):
        raise ValueError("truncated")
    for item in items:
        if not isinstance(item, dict) or set(item) != ITEM_KEYS:
            raise ValueError("item keys")
        if item["kind"] not in KINDS:
            raise ValueError("item kind")
        _check_key(item["key"], item["kind"])
        if item["status"] not in STATUSES:
            raise ValueError("item status")
        reason = item["reason"]
        if item["status"] == "conflict":
            if reason is not None:
                raise ValueError("reason")
        elif (not isinstance(reason, str) or not reason or len(reason) > MAX_REASON
              or any(unicodedata.category(c) == "Cc" for c in reason)):
            raise ValueError("reason")
        for key in ("since", "last_at"):
            if item[key] is not None:
                _timestamp(item[key])
    return items


def _previous_month(key: str) -> int:
    month = int(key[5:7])
    return 12 if month == 1 else month - 1


def _code_text(code: str, key: str, kind: str) -> str | None:
    """One reason code as text, or None when it does not parse (or does not belong to the item's kind)."""
    if kind == "day":
        m = _PREV.match(code)
        return f"前日餘額接不上 {m[1]}/{m[2]}（中間有一天沒有日對帳單）" if m else None
    if code == "carry":
        return f"月初餘額接不上 {_previous_month(key)} 月月底"
    if m := _GAP.match(code):
        return f"月對帳單有 {int(m[1])} 天沒有日對帳單"
    if m := _DIFF.match(code):
        return "月對帳單對不上：" + " ".join(m[1])
    return None


def _findings_text(reason: str, key: str, kind: str) -> list[str]:
    codes = reason.split(",")
    unsure = len(reason) >= MAX_REASON  # the reason may have been cut inside its last code
    if unsure:
        codes = codes[:-1]
    parts = [_code_text(code, key, kind) for code in codes]
    out = [p for p in parts if p is not None]
    if not out:
        return [FALLBACK_ONLY]
    if unsure or len(out) < len(parts):
        out.append(FALLBACK)
    return out


def _item_text(item: dict) -> str:
    key, kind = item["key"], item["kind"]
    stamp = date.fromisoformat(key).strftime("%m/%d") if kind == "day" else key
    if item["status"] == "conflict":
        return f"{stamp} 收到不同版本的{'日' if kind == 'day' else '月'}對帳單"
    return f"{stamp} " + "；".join(_findings_text(item["reason"], key, kind))


def futures_lines(
    cfg: NotifyConfig,
    now: datetime,
    client_factory: Callable[..., httpx.Client] | None = None,
) -> list[FuturesLine] | None:
    if cfg.portfolio_base is None:
        return None
    factory = client_factory if client_factory is not None else httpx.Client
    client = None
    abandoned = False
    try:
        options: dict = {"base_url": cfg.portfolio_base, "timeout": 10}
        if cfg.portfolio_token_file is not None:  # an unreadable file is the unreadable line, never a raise
            options["headers"] = {"Authorization": f"Bearer {read_token(cfg.portfolio_token_file)}"}
        client = factory(**options)
        try:
            pending = _get_bounded(client, PENDING_PATH, REQUEST_DEADLINE)
            items = _check_pending(pending)
            latest = _check_latest(_get_bounded(client, LATEST_PATH, REQUEST_DEADLINE))
        except _Deadline:
            abandoned = True  # a blocked thread may still hold the client: do not close it under that thread
            raise
        shown = sorted(items, key=lambda i: (i["key"], i["status"]))[:MAX_ITEMS]
        lines = [FuturesLine(f"futures:{i['key']}:{i['status']}", _item_text(i)) for i in shown]
        if pending["total"] > len(shown):
            lines.append(FuturesLine("futures:more", f"另有 {pending['total'] - len(shown)} 項"))
        scheduled = last_scheduled_run(now, cfg.sinopac_futures_run_time, cfg.sinopac_futures_run_grace_minutes)
        if latest is not None and latest["result"] == "latched":
            lines.append(FuturesLine("futures:run:latched", "永豐期貨對帳單匯入已停用"))
        elif latest is not None and latest["result"] == "failed":
            lines.append(FuturesLine("futures:run:failed", "永豐期貨對帳單匯入失敗"))
        elif latest is None or _timestamp(latest["run_at"]) < scheduled:
            lines.append(FuturesLine(f"futures:stale:{scheduled.isoformat()}", "永豐期貨對帳單今天沒有執行"))
        if any(len(line.text) > MAX_LINE for line in lines):
            raise ValueError("line too long")
        result = lines
    except Exception:
        result = [FuturesLine("futures:unreadable", UNREADABLE)]
    if client is not None and not abandoned:
        try:
            client.close()
        except Exception:  # cleanup failure is part of the isolated step: collapse, never raise
            result = [FuturesLine("futures:unreadable", UNREADABLE)]
    return result
