"""Weekly digest (spec §6.1): report types and the pure builder over a weekly `api.Snapshot`."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from .api import TZ, Snapshot, created_at_of
from .config import NotifyConfig
from .cycles import shift_period
from .debits import CUTOFF, SKIPPED, Skip

QUESTION_TEXT: dict[str, str] = {
    "bank_only": "只有銀行有", "ledger_only": "只有帳本有", "transfer": "疑似轉帳", "amount_differs": "金額不同",
    "date_differs": "日期不同", "ambiguous": "多個候選", "balance_gap": "餘額差", "settlement": "結算",
    "proposed": "已提議", "other": "其他",
}


@dataclass(frozen=True)
class CaseLine:
    date: date | None
    amount: Decimal | None
    text: str
    question_zh: str
    statement_id: int


@dataclass(frozen=True)
class AccountCases:
    account_id: int
    name: str
    count: int
    oldest_days: int
    items: list[CaseLine]
    truncated: int


@dataclass(frozen=True)
class MissingStatement:
    account_id: int
    name: str
    last_period_end: date
    expected: date
    days_late: int


@dataclass(frozen=True)
class Gaps:
    unlinked_cards: list[str] = field(default_factory=list)
    cards_without_rule: list[str] = field(default_factory=list)
    statements_without_due: list[tuple[str, int]] = field(default_factory=list)


@dataclass(frozen=True)
class WeeklyReport:
    today: date
    open_cases: list[AccountCases]
    missing: list[MissingStatement]
    skips: list[Skip]
    gaps: Gaps
    last_daily_run: datetime | None


# --- builder ----------------------------------------------------------------------------------------------------


def _case_line(item: dict) -> CaseLine:
    src = item.get("line") or item.get("entry")
    question = item.get("question", "other")
    return CaseLine(
        date=date.fromisoformat(str(src["date"])) if src else None,
        amount=Decimal(str(src["amount"])) if src else None,
        text=(src.get("merchant") or src.get("title") or "") if src else "",
        question_zh=QUESTION_TEXT.get(question, QUESTION_TEXT["other"]),
        statement_id=item["statement"]["id"])


def _open_cases(snap: Snapshot, limit: int) -> list[AccountCases]:
    out = []
    for acct in (snap.inbox or {}).get("accounts", []):
        items = acct.get("items") or []
        if not items:
            continue
        created = [created_at_of(c).date() for c in snap.open_cases.get(acct["id"], [])]
        oldest = (snap.today - min(created)).days if created else 0
        lines = [_case_line(i) for i in items[:limit]]
        out.append(AccountCases(account_id=acct["id"], name=acct["name"], count=len(items), oldest_days=oldest,
                                items=lines, truncated=len(items) - len(lines)))
    return out


def _live(rows: list[dict]) -> list[dict]:
    return sorted((r for r in rows if r.get("mode") == "live" and not r.get("retired")),
                  key=lambda r: r["period_end"])


def _expected(closing_day: int | None, ends: list[date]) -> date:
    last = ends[-1]
    if closing_day is not None:
        return shift_period(closing_day, (last - timedelta(days=30), last), 1)[1]
    recent = ends[-3:]
    gaps = [(b - a).days for a, b in zip(recent, recent[1:])]
    if not gaps:
        return shift_period(None, (last.replace(day=1), last), 1)[1]
    return last + timedelta(days=sorted(gaps)[(len(gaps) - 1) // 2])


def _missing(snap: Snapshot, grace: int) -> list[MissingStatement]:
    out = []
    for acct in snap.accounts:
        live = _live(snap.statements.get(acct["id"], []))
        if not live:
            continue
        ends = [date.fromisoformat(r["period_end"]) for r in live]
        expected = _expected(acct.get("closing_day"), ends)
        if expected + timedelta(days=grace) < snap.today:
            out.append(MissingStatement(acct["id"], acct["name"], ends[-1], expected, (snap.today - expected).days))
    return out


def _skips(snap: Snapshot) -> list[Skip]:
    since = datetime.combine(snap.today - timedelta(days=7), time(0), TZ)
    names = {a["id"]: a["name"] for a in snap.accounts}
    latest: dict = {}
    for row in snap.audit:
        detail = row.get("detail") or {}
        if row.get("event") != SKIPPED or detail.get("reason") in (None, CUTOFF) or created_at_of(row) < since:
            continue
        key = row.get("statement_id") or ("event", row["id"])
        if key not in latest or row["id"] > latest[key]["id"]:
            latest[key] = row
    out = []
    for row in latest.values():
        detail = row["detail"]
        card_id = detail.get("card_account_id")
        out.append(Skip(event_id=row["id"], card_name=names.get(card_id) or f"#{card_id}",
                        amount=Decimal(str(detail.get("amount", "0"))), reason=detail["reason"], card_id=card_id))
    return sorted(out, key=lambda s: s.event_id)


def _gaps(snap: Snapshot) -> Gaps:
    unlinked, no_rule, no_due = [], [], []
    for acct in snap.accounts:
        if not acct.get("is_credit"):
            continue
        live = _live(snap.statements.get(acct["id"], []))
        if not live:
            continue
        if not acct.get("auto_pay_account_id"):
            unlinked.append(acct["name"])
        if any(acct.get(k) is None for k in ("closing_day", "due_rule", "due_value")):
            no_rule.append(acct["name"])
        no_due += [(acct["name"], r["id"]) for r in live
                   if Decimal(str(r["statement_total"])) > 0 and r.get("due_date") is None]
    return Gaps(unlinked, no_rule, no_due)


def build_weekly(snap: Snapshot, cfg: NotifyConfig, last_daily_run: datetime | None) -> WeeklyReport:
    return WeeklyReport(today=snap.today, open_cases=_open_cases(snap, cfg.weekly_open_case_limit),
                        missing=_missing(snap, cfg.expected_statement_grace_days), skips=_skips(snap),
                        gaps=_gaps(snap), last_daily_run=last_daily_run)
