"""Fictional notifier fixtures: API-shaped dicts, a real `api.Snapshot`, and report helpers for later tasks.

Default world (today Tue 2026-11-03, 2026-11-05 a holiday), names and amounts fictional (the spec §5.7 sample):
- 薪轉戶 A (76): TWD bank account, balance 52,300.
- 旅遊卡 (100): auto → 76, closing 15, fixed_day 5; statement 155 (10/15 cycle) 48,200 due 11/05 → debit 11/06.
  副卡 (104) is combined into it.
- 加油卡 (97): auto → 76, closing 20, fixed_day 7; 8,000 due 11/07 → debit 11/09 (outside the 2-day lead).
- 黑啤卡 (93): manual, closing 15, fixed_day 6; 4,378 due 11/06.
- 超市卡 (98): manual, closing 25, fixed_day 7; no statement for the 10/25 cycle (rule-only: 帳單未到, rule due 11/07,
  debit Mon 11/09).
Every card also has a 0-total statement for the cycle before (nothing to pay, dropped), so the default world has no
previous-cycle 帳單未到 lines. On Thu 11-05 this is the spec §5.7 sample (薪轉戶 A 52,300 → −3,900, 缺 3,900).
"""
from __future__ import annotations

import itertools
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from notify.api import Snapshot
from notify.config import NotifyConfig
from notify.cycles import Calendar
from notify.debits import DailyReport, DebitItem, Skip, SourceBlock

TODAY = date(2026, 11, 3)
HOLIDAYS = frozenset({date(2026, 11, 5)})
SOURCE_ID = 76
_UNSET = object()
_ids = itertools.count(1000)


def cfg(**overrides) -> NotifyConfig:
    base = dict(api_base="http://api.invalid", token_file=Path("/nonexistent/notify-token"),
                hub_base_url="https://example.invalid/hub", discord_target="discord:0")
    return NotifyConfig(**{**base, **overrides})


def account(*, id: int, name: str, currency: str = "TWD", balance: str = "0", is_credit: bool = True,
            is_archived: bool = False, closing_day: int | None = 15, due_rule: str | None = "fixed_day",
            due_value: int | None = 5, combined_account_id: int | None = None,
            auto_pay_account_id: int | None = None) -> dict:
    return {"id": id, "name": name, "currency": currency, "balance": balance, "is_credit": is_credit,
            "is_archived": is_archived, "closing_day": closing_day, "due_rule": due_rule, "due_value": due_value,
            "combined_account_id": combined_account_id, "auto_pay_account_id": auto_pay_account_id}


def statement(*, account_id: int | None = None, total: str = "1000", period_end: date, due_date=_UNSET,
              id: int | None = None, currency: str = "TWD", mode: str = "live", retired: bool = False,
              kind: str = "card", open_case_count: int = 0, status: str = "open") -> dict:
    """StatementOut shape; `due_date` defaults to period_end + 20 days (pass None for an unrecognised due date)."""
    due = period_end + timedelta(days=20) if due_date is _UNSET else due_date
    return {"id": next(_ids) if id is None else id, "account_id": account_id, "kind": kind, "currency": currency,
            "period_start": (period_end - timedelta(days=30)).isoformat(), "period_end": period_end.isoformat(),
            "closing_date": period_end.isoformat(), "due_date": due.isoformat() if due else None,
            "statement_total": total, "mode": mode, "status": status, "retired": retired,
            "open_case_count": open_case_count}


_DEFAULT_STATEMENTS = {
    # name: (id, account_id, total, period_end, due_date)
    "旅遊卡": (155, 100, "48200", date(2026, 10, 15), date(2026, 11, 5)),
    "加油卡": (156, 97, "8000", date(2026, 10, 20), date(2026, 11, 7)),
    "黑啤卡": (157, 93, "4378", date(2026, 10, 15), date(2026, 11, 6)),
    "超市卡": (158, 98, "2350", date(2026, 10, 25), date(2026, 11, 7)),
}
_PREVIOUS_ZERO = {  # the cycle before, 0 total: nothing to pay
    "旅遊卡": (145, 100, date(2026, 9, 15), date(2026, 10, 5)),
    "加油卡": (146, 97, date(2026, 9, 20), date(2026, 10, 7)),
    "黑啤卡": (147, 93, date(2026, 9, 15), date(2026, 10, 6)),
    "超市卡": (148, 98, date(2026, 9, 25), date(2026, 10, 7)),
}


def statement_default(name: str, **overrides) -> dict:
    sid, account_id, total, period_end, due = _DEFAULT_STATEMENTS[name]
    args = dict(id=sid, account_id=account_id, total=total, period_end=period_end, due_date=due)
    return statement(**{**args, **overrides})


def _default_statements() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {name: [statement_default(name)] for name in ("旅遊卡", "加油卡", "黑啤卡")}
    out["超市卡"] = []
    for name, (sid, account_id, period_end, due) in _PREVIOUS_ZERO.items():
        out[name].append(statement(id=sid, account_id=account_id, total="0", period_end=period_end, due_date=due))
    return out


def entry(*, account_id: int, kind: str, amount: str, entry_date: date, transfer_group_id: str | None = None,
          source: str = "manual", id: int | None = None, parent_entry_id: int | None = None,
          posted_date: date | None = None) -> dict:
    """EntryOut shape (the fields the notifier reads, plus a few for realism); posted_date defaults to entry_date."""
    return {"id": next(_ids) if id is None else id, "kind": kind, "amount": amount, "currency": "TWD",
            "entry_date": entry_date.isoformat(), "posted_date": (posted_date or entry_date).isoformat(),
            "account_id": account_id,
            "parent_entry_id": parent_entry_id, "transfer_group_id": transfer_group_id, "source": source}


def line(account_id: int, amount: str, kind: str = "expense") -> dict:
    return {"kind": kind, "account_id": account_id, "to_account_id": None, "amount": amount, "currency": "TWD"}


def instance(*, lines: list[dict], due_date: date, status: str = "pending", id: int | None = None) -> dict:
    return {"id": next(_ids) if id is None else id, "definition_id": 1, "definition_name": "固定支出",
            "due_date": due_date.isoformat(), "rule_date": due_date.isoformat(), "status": status, "lines": lines}


def audit_event(*, id: int, statement_id: int | None = None, reason: str, created_at: str,
                card_account_id: int = 100, amount: str = "48200", pay_date: str = "2026-11-06",
                event: str = "auto_pay_skipped") -> dict:
    """AuditOut shape; the reason lives only in `detail` (as `record_skip` writes it)."""
    return {"id": id, "statement_id": statement_id, "case_id": None, "action_id": None, "run_id": None,
            "event": event, "actor": "auto_pay", "request_id": None, "created_at": created_at,
            "detail": {"reason": reason, "card_account_id": card_account_id, "pay_account_id": SOURCE_ID,
                       "amount": amount, "pay_date": pay_date}}


def _default_accounts() -> list[dict]:
    return [
        account(id=SOURCE_ID, name="薪轉戶 A", balance="52300", is_credit=False, closing_day=None, due_rule=None,
                due_value=None),
        account(id=100, name="旅遊卡", auto_pay_account_id=SOURCE_ID, closing_day=15, due_value=5),
        account(id=104, name="副卡", combined_account_id=100, closing_day=15, due_value=5),
        account(id=97, name="加油卡", auto_pay_account_id=SOURCE_ID, closing_day=20, due_value=7),
        account(id=93, name="黑啤卡", closing_day=15, due_value=6),
        account(id=98, name="超市卡", closing_day=25, due_value=7),
    ]


def snapshot(*, today: date = TODAY, statements_for: dict[str, list[dict]] | None = None,
             payments: dict[int, list[dict]] | None = None, source_entries: dict[int, list[dict]] | None = None,
             pending: list[dict] | None = None, audit: list[dict] | None = None,
             enabled_from: str | None = "2026-10-01", auto_pay_enabled: bool = True,
             source_overrides: dict[int, dict] | None = None, extra_cards: list[dict] | None = None,
             open_cases: dict[int, list[dict]] | None = None, inbox: dict | None = None,
             card_activity: dict[int, list[dict]] | None = None) -> Snapshot:
    """`card_activity` left out = not read (no cycle is ever quiet); pass {card id: entries} to read it."""
    accounts = _default_accounts() + list(extra_cards or [])
    for acct in accounts:
        acct.update((source_overrides or {}).get(acct["id"], {}))
    by_name = {a["name"]: a["id"] for a in accounts}
    per_name = {**_default_statements(), **(statements_for or {})}
    statements: dict[int, list[dict]] = {a["id"]: [] for a in accounts if a["is_credit"]}
    for name, rows in per_name.items():
        for row in rows:
            if row["account_id"] is None:
                row = {**row, "account_id": by_name[name]}
            statements.setdefault(by_name[name], []).append(row)
    settings = {"version": 1, "auto_pay": {"enabled": auto_pay_enabled, "enabled_from": enabled_from,
                                           "max_amount": "200000", "monthly_total": "500000",
                                           "twin_window_days": 7, "retry_days": 30}}
    calendar = Calendar(holidays=HOLIDAYS, workdays=frozenset(), last_known_day=today + timedelta(days=60))
    return Snapshot(today=today, settings=settings, calendar=calendar, accounts=accounts, statements=statements,
                    payments=dict(payments or {}), source_entries=dict(source_entries or {}),
                    pending=list(pending or []), audit=list(audit or []), open_cases=dict(open_cases or {}),
                    inbox=inbox, card_activity=card_activity)


def weekly_snapshot(*, inbox_items: dict[str, int] | None = None, oldest_created: date | None = None,
                    statements_for: dict[str, list[dict]] | None = None, **overrides) -> Snapshot:
    """The daily world plus the weekly-only reads.

    The rule-only 超市卡 gets its current-cycle statement (so a quiet world has nothing missing);
    `inbox_items={card name: n}` builds n bank_only inbox items and n open cases (oldest created `oldest_created`).
    """
    statements_for = dict(statements_for or {})
    statements_for.setdefault("超市卡", [statement_default("超市卡"), statement_default("超市卡", id=148, total="0",
                                                                                       period_end=date(2026, 9, 25))])
    ids = {a["name"]: a["id"] for a in _default_accounts()}
    inbox, open_cases = {"accounts": []}, {}
    for name, n in (inbox_items or {}).items():
        items = [{"case_id": 9000 + i, "question": "bank_only", "statement": {"id": 155, "period_end": "2026-10-15"},
                  "line": {"id": 7000 + i, "date": f"2026-10-{1 + i:02d}", "merchant": f"商店{i}",
                           "amount": f"{100 + i}", "line_kind": "purchase"}, "entry": None} for i in range(n)]
        inbox["accounts"].append({"id": ids[name], "name": name, "currency": "TWD", "is_credit": True,
                                  "items": items})
        base = oldest_created or date(2026, 11, 1)
        open_cases[ids[name]] = [{"id": 9000 + i, "created_at": f"{(base + timedelta(days=n - 1 - i)).isoformat()}T09:00:00+08:00"}
                                 for i in range(n)]
    overrides.setdefault("inbox", inbox)
    overrides.setdefault("open_cases", open_cases)
    return snapshot(statements_for=statements_for, **overrides)


# --- report helpers (for the dedupe / render tests) ---------------------------------------------------------------


def find(report: DailyReport, card_name: str) -> DebitItem | None:
    """First item of `card_name`, searching the source blocks first, then the manual block."""
    for it in [i for b in report.blocks for i in b.items] + report.manual:
        if it.card_name == card_name:
            return it
    return None


def block(report: DailyReport, source_name: str) -> SourceBlock:
    return next(b for b in report.blocks if b.name == source_name)


def item(statement_id: int | None, debit_day: date = date(2026, 11, 6), **overrides) -> DebitItem:
    base = DebitItem(statement_id=statement_id, card_id=100, card_name="旅遊卡", currency="TWD",
                     amount=Decimal("48200"), paid=Decimal(0), due=debit_day, debit_day=debit_day, from_rule=False,
                     cls="auto", source_id=SOURCE_ID, open_cases=0, overdue_days=0, unpaid_reason=None,
                     in_window=True, note=None)
    return replace(base, **overrides)


def skip(event_id: int, **overrides) -> Skip:
    return replace(Skip(event_id=event_id, card_name="旅遊卡", amount=Decimal("48200"), reason="over_cap"),
                   **overrides)


def report(items: list[DebitItem] | None = None, shortfall: Decimal | None = None, skips: list[Skip] | None = None,
           today: date = TODAY) -> DailyReport:
    """Auto items form one 薪轉戶 A block (balance 52,300); manual items go to the manual block."""
    items = list(items or [])
    auto = [i for i in items if i.cls == "auto"]
    balance = Decimal("52300")
    projected = -shortfall if shortfall else balance - sum((i.amount or Decimal(0) for i in auto), Decimal(0))
    blocks = [SourceBlock(account_id=SOURCE_ID, name="薪轉戶 A", currency="TWD", balance=balance,
                          projected=projected, shortfall=shortfall, items=auto)] if auto else []
    return DailyReport(today=today, blocks=blocks, manual=[i for i in items if i.cls == "manual"],
                       skips=list(skips or []))
