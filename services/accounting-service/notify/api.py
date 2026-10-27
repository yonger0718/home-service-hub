"""Read-only HomeHub API client and the daily/weekly snapshots.

GET only: there is no method that can issue another verb, and no response body
or token ever reaches an exception message.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import httpx

from .cycles import Calendar

PAGE = 500
CASE_CAP = 1000
TZ = timezone(timedelta(hours=8))


class ApiError(Exception):
    def __init__(self, status: int | None, path: str, reason: str | None = None):
        super().__init__(f"{status} {path}" if status is not None else f"{path}: {reason}")
        self.status, self.path, self.reason = status, path, reason


class CappedRead(ApiError):
    """A capped list came back full and may hide rows the run needs."""


class ReadClient:
    def __init__(self, client: httpx.Client, token: str):
        self._client = client
        self._headers = {"Authorization": f"Bearer {token}"}

    def _get(self, path: str, params: Any = None) -> Any:
        try:
            response = self._client.get(path, params=params, headers=self._headers)
        except httpx.HTTPError as exc:
            raise ApiError(None, path, type(exc).__name__) from None
        if response.status_code >= 400:
            raise ApiError(response.status_code, path)
        try:
            return response.json()
        except ValueError:
            raise ApiError(response.status_code, path, "invalid JSON") from None

    def settings(self) -> dict:
        return self._get("/settings/reconciliation")

    def calendar(self, date_from: date, date_to: date) -> Calendar:
        data = self._get("/calendar/business-days", {"from": date_from.isoformat(), "to": date_to.isoformat()})
        return Calendar(
            holidays=frozenset(date.fromisoformat(d) for d in data["holidays"]),
            workdays=frozenset(date.fromisoformat(d) for d in data["workdays"]),
            last_known_day=date.fromisoformat(data["last_known_day"]),
        )

    def accounts(self, as_of: date) -> list[dict]:
        return self._get("/accounts", {"include_archived": "false", "as_of": as_of.isoformat()})

    def statements(self, account_id: int) -> list[dict]:
        return self._get(f"/accounts/{account_id}/statements")

    def entries(self, account_ids: list[int], kind: str, date_from: date, date_to: date) -> list[dict]:
        path, out, offset = "/entries", [], 0
        while True:
            params = [("account_id", str(i)) for i in account_ids] + [
                ("kind", kind), ("date_from", date_from.isoformat()), ("date_to", date_to.isoformat()),
                ("limit", str(PAGE)), ("offset", str(offset))]
            page = self._get(path, params)
            items = page["items"]
            out.extend(items)
            offset += len(items)
            if offset >= page["total"]:
                return out
            if not items:
                raise CappedRead(None, path, reason="entries page ended early")

    def pending_instances(self, until: date) -> list[dict]:
        return self._get("/schedules/instances", {"status": "pending", "until": until.isoformat()})

    def audit(self, limit: int = 1000) -> list[dict]:
        return self._get("/reconciliation/audit", {"limit": str(limit)})

    def open_cases(self, account_id: int) -> list[dict]:
        return self._get("/reconciliation/cases",
                         {"status": "open", "account_id": str(account_id), "limit": str(CASE_CAP)})

    def inbox(self) -> dict:
        return self._get("/reconciliation/inbox", {"limit": str(CASE_CAP)})


@dataclass(frozen=True)
class Snapshot:
    today: date
    settings: dict
    calendar: Calendar
    accounts: list[dict]
    statements: dict[int, list[dict]]
    payments: dict[int, list[dict]]
    source_entries: dict[int, list[dict]]
    pending: list[dict]
    audit: list[dict]
    open_cases: dict[int, list[dict]] = field(default_factory=dict)
    inbox: dict | None = None


def _created_at(row: dict) -> datetime:
    value = datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00"))
    return value if value.tzinfo else value.replace(tzinfo=TZ)


def _checked_audit(client: ReadClient, since: datetime) -> list[dict]:
    rows = client.audit(1000)
    if len(rows) >= 1000 and min(_created_at(r) for r in rows) >= since:
        raise CappedRead(None, "/reconciliation/audit", reason="audit page full inside the window")
    return rows


def _groups(accounts: list[dict]) -> dict[int, list[int]]:
    """Credit accounts grouped under their master (combined_account_id); the master's own id is the key."""
    groups: dict[int, list[int]] = {}
    for a in accounts:
        if a.get("is_credit") and not a.get("is_archived"):
            master = a.get("combined_account_id") or a["id"]
            groups.setdefault(master, [master])
            if a["id"] != master:
                groups[master].append(a["id"])
    return groups


def _fetch(client: ReadClient, today: date, *, weekly: bool) -> Snapshot:
    settings = client.settings()
    calendar = client.calendar(today - timedelta(days=40), today + timedelta(days=60))
    accounts = [a for a in client.accounts(today) if not a.get("is_archived")]
    stmt_accounts = accounts if weekly else [a for a in accounts if a.get("is_credit")]
    statements = {a["id"]: client.statements(a["id"]) for a in stmt_accounts}
    start = today - timedelta(days=120)
    payments = {m: client.entries(ids, "transfer_in", start, today) for m, ids in _groups(accounts).items()}
    sources = sorted({a["auto_pay_account_id"] for a in accounts if a.get("is_credit") and a.get("auto_pay_account_id")})
    source_entries = {x: client.entries([x], "transfer_out", start, today) + client.entries([x], "expense", start, today)
                      for x in sources}
    pending = client.pending_instances(today + timedelta(days=60))
    since = (datetime.combine(today - timedelta(days=7), time(0), TZ) if weekly
             else datetime.combine(today - timedelta(days=1), time(6), TZ))
    audit = _checked_audit(client, since)
    open_cases: dict[int, list[dict]] = {}
    inbox = None
    if weekly:
        inbox = client.inbox()
        if sum(len(a.get("items", [])) for a in inbox.get("accounts", [])) >= CASE_CAP:
            raise CappedRead(None, "/reconciliation/inbox", reason="inbox page full")
        for acct in inbox.get("accounts", []):
            if acct.get("items"):
                cases = client.open_cases(acct["id"])
                if len(cases) >= CASE_CAP:
                    raise CappedRead(None, "/reconciliation/cases", reason="cases page full")
                open_cases[acct["id"]] = cases
    return Snapshot(today, settings, calendar, accounts, statements, payments, source_entries, pending, audit,
                    open_cases, inbox)


def fetch_daily(client: ReadClient, today: date) -> Snapshot:
    return _fetch(client, today, weekly=False)


def fetch_weekly(client: ReadClient, today: date) -> Snapshot:
    return _fetch(client, today, weekly=True)
