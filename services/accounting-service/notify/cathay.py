"""國泰證券 daily-statement import status (spec §7.2): read from the portfolio API, rendered as notice lines.

The step is isolated: its own client, a 64 KiB body cap, strict validation. Any failure inside it collapses to the
single unreadable line, so the debit and weekly items are never affected.
"""
from __future__ import annotations

import json
import re
import threading
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import httpx

from .api import TZ
from .config import NotifyConfig

PENDING_PATH = "/api/portfolio/imports/cathay-statement/pending"
LATEST_PATH = "/api/portfolio/imports/cathay-statement/runs/latest"
MAX_BODY = 65536
REQUEST_DEADLINE = 10.0  # wall-clock seconds for one whole request: connect, headers, body and EOF
MAX_ITEMS = 10
MAX_REASON = 40
MAX_LINE = 120
STATUSES = frozenset({"held", "mismatch", "conflict"})
RESULTS = frozenset({"ok", "latched", "failed"})
UNREADABLE = "國泰對帳單狀態讀不到"
ITEM_KEYS = {"trade_date", "status", "reason", "since", "last_at"}
RUN_KEYS = {"run_at", "result", "code", "files_seen", "posted"}
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$")


@dataclass(frozen=True)
class CathayLine:
    key: str
    text: str


def last_scheduled_run(now: datetime, run_time: time, grace_minutes: int) -> datetime:
    """The most recent `run_time` (Asia/Taipei) that is at least `grace_minutes` before `now`."""
    local = now.astimezone(TZ)
    candidate = datetime.combine(local.date(), run_time, TZ)
    if candidate > local - timedelta(minutes=grace_minutes):
        candidate -= timedelta(days=1)
    return candidate


def _is_int(value: object, minimum: int = 0) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not _TIMESTAMP.match(value):
        raise ValueError("timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timestamp offset")
    return parsed


def _get(client: httpx.Client, path: str) -> dict:
    with client.stream("GET", path, headers={"Accept-Encoding": "identity"}) as response:
        if response.status_code != 200:
            raise ValueError("status")
        if response.headers.get("content-encoding", "identity").strip().lower() not in ("", "identity"):
            raise ValueError("content-encoding")
        body = bytearray()
        # Raw bytes: the cap bounds memory and nothing is decompressed. A response already held in memory (a canned
        # reply from a fake transport) cannot be streamed again; its content is the raw body since encodings are rejected.
        chunks = [response.content] if response.is_stream_consumed else response.iter_raw()
        for chunk in chunks:
            body += chunk
            if len(body) > MAX_BODY:
                raise ValueError("oversize")
    data = json.loads(bytes(body))
    if not isinstance(data, dict):
        raise ValueError("shape")
    return data


class _Deadline(Exception):
    pass


def _get_bounded(client: httpx.Client, path: str) -> dict:
    """Run `_get` in a daemon thread and give up after REQUEST_DEADLINE; a stalled server cannot extend it."""
    box: dict = {}

    def work() -> None:
        try:
            box["data"] = _get(client, path)
        except BaseException as exc:  # handed to the caller below
            box["error"] = exc

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    thread.join(REQUEST_DEADLINE)
    if thread.is_alive():
        raise _Deadline(path)  # the thread is abandoned; it is a daemon and cannot block exit
    if "error" in box:
        raise box["error"]
    return box["data"]


def _check_pending(data: dict) -> list[dict]:
    if set(data) != {"items", "total", "truncated"}:
        raise ValueError("pending keys")
    items = data["items"]
    if not isinstance(items, list) or not isinstance(data["truncated"], bool):
        raise ValueError("pending shape")
    if not _is_int(data["total"], len(items)):
        raise ValueError("total")
    if data["truncated"] != (data["total"] > len(items)):
        raise ValueError("truncated")
    for item in items:
        if not isinstance(item, dict) or set(item) != ITEM_KEYS:
            raise ValueError("item keys")
        if not isinstance(item["trade_date"], str):
            raise ValueError("trade_date")
        if date.fromisoformat(item["trade_date"]).isoformat() != item["trade_date"]:
            raise ValueError("trade_date")
        if item["status"] not in STATUSES:
            raise ValueError("item status")
        reason = item["reason"]
        if reason is not None and (not isinstance(reason, str) or len(reason) > MAX_REASON):
            raise ValueError("reason")
        if reason is not None and any(unicodedata.category(c) == "Cc" for c in reason):
            raise ValueError("reason")
        for key in ("since", "last_at"):
            if item[key] is not None:
                _timestamp(item[key])
    return items


def _check_run(row: object) -> dict | None:
    if row is None:
        return None
    if not isinstance(row, dict) or set(row) != RUN_KEYS:
        raise ValueError("run keys")
    _timestamp(row["run_at"])
    if row["result"] not in RESULTS:
        raise ValueError("run result")
    if row["code"] is not None and not isinstance(row["code"], str):
        raise ValueError("run code")
    if not _is_int(row["files_seen"]) or not _is_int(row["posted"]):
        raise ValueError("run counts")
    return row


def _check_latest(data: dict) -> dict | None:
    if set(data) != {"latest", "latest_ok"}:
        raise ValueError("latest keys")
    _check_run(data["latest_ok"])
    return _check_run(data["latest"])


def _held_text(reason: str | None) -> str:
    if reason is None:
        return "保留"
    if reason.startswith("unresolved_symbol:"):
        return f"保留：名稱對不到代號 {reason.split(':', 1)[1]}"
    if reason == "unsupported_category":
        return "保留：含融資融券交易"
    return f"保留：{reason}"


def _item_text(item: dict) -> str:
    stamp = date.fromisoformat(item["trade_date"]).strftime("%m/%d")
    if item["status"] == "held":
        return f"{stamp} {_held_text(item['reason'])}"
    if item["status"] == "mismatch":
        return f"{stamp} 對不上 CSV"
    return f"{stamp} 收到不同版本的對帳單"


def cathay_lines(
    cfg: NotifyConfig,
    now: datetime,
    client_factory: Callable[..., httpx.Client] | None = None,
) -> list[CathayLine] | None:
    if cfg.portfolio_base is None:
        return None
    factory = client_factory if client_factory is not None else httpx.Client
    client = None
    abandoned = False
    try:
        client = factory(base_url=cfg.portfolio_base, timeout=10)
        try:
            pending = _get_bounded(client, PENDING_PATH)
            items = _check_pending(pending)
            latest = _check_latest(_get_bounded(client, LATEST_PATH))
        except _Deadline:
            abandoned = True  # a blocked thread may still hold the client: do not close it under that thread
            raise
        lines = [
            CathayLine(f"cathay:{i['trade_date']}:{i['status']}", _item_text(i))
            for i in sorted(items, key=lambda i: i["trade_date"])[:MAX_ITEMS]
        ]
        shown = len(lines)
        if pending["total"] > shown:
            lines.append(CathayLine("cathay:more", f"另有 {pending['total'] - shown} 天"))
        scheduled = last_scheduled_run(now, cfg.cathay_run_time, cfg.cathay_run_grace_minutes)
        if latest is not None and latest["result"] == "latched":
            lines.append(CathayLine("cathay:run:latched", "國泰對帳單匯入已停用"))
        elif latest is not None and latest["result"] == "failed":
            lines.append(CathayLine("cathay:run:failed", "國泰對帳單匯入失敗"))
        elif latest is None or _timestamp(latest["run_at"]) < scheduled:
            lines.append(CathayLine(f"cathay:stale:{scheduled.isoformat()}", "國泰對帳單今天沒有執行"))
        if any(len(line.text) > MAX_LINE for line in lines):
            raise ValueError("line too long")
        return lines
    except Exception:
        return [CathayLine("cathay:unreadable", UNREADABLE)]
    finally:
        if client is not None and not abandoned:
            client.close()
