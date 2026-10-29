"""Daily debit items (spec §5.1–§5.5): pure functions over a `Snapshot`, no I/O.

The report carries only renderable items — in the lead window, overdue, or 未扣款 — after the drop rules (paid,
twin, nothing to pay, past debit day more than 30 days after the due date, an undated statement once the next
cycle has closed). Projections are computed over every auto item first, so a debit day <= D outside a shorter
per-card window still counts (§5.3). Dedupe (Task 5) decides which blocks are sent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Literal

from .api import READ_DAYS, TZ, Snapshot, created_at_of
from .config import NotifyConfig
from .cycles import (
    Period,
    business_days_between,
    due_date,
    last_closed_period,
    next_business_day,
    shift_period,
    statement_period,
)

REASON_TEXT: dict[str, str] = {
    "no_link": "卡片沒有設定扣款帳戶",
    "link_ineligible": "扣款帳戶不可用",
    "over_cap": "超過單筆上限",
    "monthly_budget": "超過當月總額",
    "twin": "疑似已手動繳過",
    "duplicate_statement": "重複帳單",
    "header_disputed": "帳單有爭議",
    "expired": "超過重試期限",
}

NOTE_MISSING = "帳單未到"
NOTE_NO_DUE = "到期日未辨識"
NOTE_CURRENCY = "幣別不同，請自行確認"
MATCH_DAYS = 3        # a statement matches a cycle when its period_end is within ±3 days of the cycle end
OVERDUE_DAYS = 30     # manual / rule-only items stay overdue while 0 < (today − due).days <= 30
SKIPPED = "auto_pay_skipped"
CUTOFF = "cutoff"
NOT_ACTIVITY = frozenset({"balance_adjustment"})  # a ledger correction, never a line on the bank's statement


@dataclass(frozen=True)
class DebitItem:
    statement_id: int | None
    card_id: int
    card_name: str
    currency: str
    amount: Decimal | None
    paid: Decimal
    due: date | None
    debit_day: date | None
    from_rule: bool
    cls: Literal["auto", "manual"]
    source_id: int | None
    open_cases: int
    overdue_days: int
    unpaid_reason: str | None
    in_window: bool
    note: str | None

    @property
    def key(self) -> int:
        """State key (spec §6.3): the statement id, or `-card_id` for a rule-only item."""
        return self.statement_id if self.statement_id is not None else -self.card_id


@dataclass(frozen=True)
class SourceBlock:
    account_id: int
    name: str
    currency: str
    balance: Decimal
    projected: Decimal
    shortfall: Decimal | None
    items: list[DebitItem]


@dataclass(frozen=True)
class Skip:
    event_id: int
    card_name: str
    amount: Decimal
    reason: str  # the audit reason code; REASON_TEXT has the words
    card_id: int | None = None  # for the settings link; None when the audit detail carries no card


@dataclass(frozen=True)
class DailyReport:
    today: date
    blocks: list[SourceBlock]
    manual: list[DebitItem]
    skips: list[Skip]
    cleared: list[SourceBlock] = field(default_factory=list)  # filled by dedupe (Task 5) only


def _day(value) -> date | None:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(value)


class _Run:
    """One build: the snapshot, the config and the lookups every rule needs."""

    def __init__(self, snap: Snapshot, cfg: NotifyConfig):
        self.snap, self.cfg, self.today, self.cal = snap, cfg, snap.today, snap.calendar
        self.accounts = {a["id"]: a for a in snap.accounts}
        auto_pay = snap.settings["auto_pay"]
        self.auto_enabled = bool(auto_pay["enabled"])
        self.enabled_from = _day(auto_pay["enabled_from"])  # null → never auto
        self.twin_days = int(auto_pay["twin_window_days"])

    # --- classification (§5.2) ---------------------------------------------------------------------------------

    def link_target(self, card: dict) -> dict | None:
        return self.accounts.get(card["auto_pay_account_id"]) if card.get("auto_pay_account_id") else None

    def link_eligible(self, card: dict) -> bool:
        """The job's `_link_ineligible` predicate, evaluated now from `/accounts`."""
        target = self.link_target(card)
        return (target is not None and not target.get("is_archived") and not target.get("is_credit")
                and target["currency"] == card["currency"])

    def currency_mismatch(self, card: dict) -> bool:
        target = self.link_target(card)
        return target is not None and target["currency"] != card["currency"]

    def is_auto(self, card: dict, debit_day: date) -> bool:
        return (self.auto_enabled and self.enabled_from is not None and debit_day >= self.enabled_from
                and self.link_eligible(card))

    def in_window(self, card: dict, debit_day: date | None) -> bool:
        if debit_day is None or debit_day < self.today:
            return False
        lead = self.cfg.lead_overrides.get(card["id"], self.cfg.lead_business_days)
        return business_days_between(self.today, debit_day, self.cal) <= lead

    # --- payments and twins (§5.1) -----------------------------------------------------------------------------

    def transfers_in(self, card: dict) -> list[dict]:
        """Top-level `transfer_in` entries on the card and its 副卡 (`snap.payments` is keyed by the master)."""
        return [e for e in self.snap.payments.get(card["id"], []) if e["kind"] == "transfer_in" and _top_level(e)]

    def paid(self, card: dict, period_end: date, debit_day: date) -> Decimal:
        """Σ transfer_in in [period_end, min(today, max(debit_day, next_closing − 1))]."""
        next_closing = statement_period(card["closing_day"], period_end + timedelta(days=1))[1]
        end = min(self.today, max(debit_day, next_closing - timedelta(days=1)))
        return sum((Decimal(e["amount"]) for e in self.transfers_in(card)
                    if period_end <= _day(e["entry_date"]) <= end), Decimal(0))

    def has_twin(self, card: dict, amount: Decimal, debit_day: date) -> bool:
        """The debit already exists as the job's `find_twins` would see it (shapes a, b, c)."""
        def near(e: dict) -> bool:
            return abs((_day(e["entry_date"]) - debit_day).days) <= self.twin_days

        ins = self.transfers_in(card)
        if any(near(e) and Decimal(e["amount"]) == amount for e in ins):
            return True
        groups = {str(e["transfer_group_id"]) for e in ins if e.get("transfer_group_id")}
        for e in self.snap.source_entries.get(card["auto_pay_account_id"], []):
            if not _top_level(e) or not near(e) or Decimal(e["amount"]) != -amount:
                continue
            gid = e.get("transfer_group_id")
            if e["kind"] == "transfer_out" and (gid is None or str(gid) in groups):
                return True
            if e["kind"] == "expense" and e.get("source") == "statement":
                return True
        return False

    def unpaid_reason(self, statement_id: int) -> str:
        rows = [r for r in self.snap.audit if r.get("event") == SKIPPED and r.get("statement_id") == statement_id
                and (r.get("detail") or {}).get("reason") not in (None, CUTOFF)]
        if not rows:
            return "unknown"
        return max(rows, key=lambda r: (created_at_of(r), r["id"]))["detail"]["reason"]

    # --- items ---------------------------------------------------------------------------------------------------

    def _item(self, card: dict, **fields) -> DebitItem:
        base = dict(card_id=card["id"], card_name=card["name"], currency=card["currency"],
                    source_id=card.get("auto_pay_account_id"), paid=Decimal(0), overdue_days=0, unpaid_reason=None,
                    open_cases=0, note=None)
        return DebitItem(**{**base, **fields})

    def overdue_days(self, due: date, debit_day: date) -> int | None:
        """0 when the debit day has not passed; the overdue days while within 30; None → drop."""
        if debit_day >= self.today:
            return 0
        days = (self.today - due).days
        return days if 0 < days <= OVERDUE_DAYS else None

    def rule_item(self, card: dict, cycle: Period) -> DebitItem | None:
        due = due_date(cycle[1], card["due_rule"], card["due_value"])
        debit_day = next_business_day(due, self.cal)
        overdue = self.overdue_days(due, debit_day)
        if overdue is None:
            return None
        return self._item(card, statement_id=None, amount=None, due=due, debit_day=debit_day, from_rule=True,
                          cls="manual", overdue_days=overdue, in_window=self.in_window(card, debit_day),
                          note=NOTE_MISSING)

    def statement_item(self, card: dict, stmt: dict) -> DebitItem | None:
        amount = Decimal(stmt["statement_total"])
        if amount <= 0:
            return None
        common = dict(statement_id=stmt["id"], amount=amount, from_rule=False,
                      open_cases=int(stmt.get("open_case_count") or 0))
        due = _day(stmt.get("due_date"))
        if due is None:  # never booked by the job, never projected, never overdue: a manual item (Ruling 18)
            period_end = _day(stmt["period_end"])
            paid = self.paid(card, period_end, period_end)  # window [period_end, min(today, next_closing − 1)]
            if paid >= amount:
                return None
            return self._item(card, **common, paid=paid, due=None, debit_day=None, cls="manual", in_window=False,
                              note=NOTE_NO_DUE)
        debit_day = next_business_day(due, self.cal)
        paid = self.paid(card, _day(stmt["period_end"]), debit_day)
        past = debit_day < self.today
        overdue, unpaid_reason = 0, None
        if self.is_auto(card, debit_day):
            cls = "auto"
            if (self.has_twin(card, amount, debit_day) or (past and paid >= amount)
                    or self.overdue_days(due, debit_day) is None):
                return None
            if past:
                unpaid_reason = self.unpaid_reason(stmt["id"])
        else:
            cls = "manual"
            overdue = self.overdue_days(due, debit_day)
            if paid >= amount or overdue is None:
                return None
        return self._item(card, **common, paid=paid, due=due, debit_day=debit_day, cls=cls, overdue_days=overdue,
                          unpaid_reason=unpaid_reason, in_window=self.in_window(card, debit_day),
                          note=NOTE_CURRENCY if self.currency_mismatch(card) else None)

    def card_items(self, card: dict) -> list[DebitItem]:
        """The last closed cycle and the one before (for overdue), plus every other billable live statement."""
        closing = card["closing_day"]
        last = last_closed_period(closing, self.today)
        cycles = [shift_period(closing, last, -1), last]
        older_end = shift_period(closing, last, -2)[1]
        live = [s for s in self.snap.statements.get(card["id"], []) if s.get("mode") == "live" and not s.get("retired")]
        matched = {cycle: _match(live, cycle[1]) for cycle in cycles}
        # A dated statement ages out through the 30-day overdue horizon; an undated one has none, so it stays only
        # while newer than the older cycle's match zone (C−1 end + 3, the analogue of the C−2 end + 3 bound below):
        # it is dropped once the next cycle has closed (Ruling 18). The weekly 設定缺漏 still lists it.
        no_due_horizon = cycles[0][1] + timedelta(days=MATCH_DAYS)
        matched_ids = {s["id"] for s in matched.values() if s is not None}
        # Other billable statements count only when newer than the cycle-before-last's match zone: older history
        # is outside the payment fetch (today − 120) and the 30-day overdue horizon.
        horizon = older_end + timedelta(days=MATCH_DAYS)
        others = [s for s in live if s["id"] not in matched_ids and s.get("kind") == "card"
                  and Decimal(s["statement_total"]) > 0 and s.get("due_date") and _day(s["period_end"]) > horizon]
        surviving = [(s, i) for s, i in ((s, self.statement_item(card, s)) for s in others) if i is not None]
        other_items = [i for _, i in surviving]
        items: list[DebitItem | None] = []
        for cycle in cycles:
            stmt = matched[cycle]
            if stmt is not None:
                if stmt.get("due_date") is None and _day(stmt["period_end"]) <= no_due_horizon:
                    continue
                items.append(self.statement_item(card, stmt))
            else:
                # A surviving off-cycle bill newer than the previous cycle's match zone (its end + 3) is this
                # cycle's bill: no 帳單未到 next to it. One that only covers an older cycle does not clear this one.
                bound = shift_period(closing, cycle, -1)[1] + timedelta(days=MATCH_DAYS)
                if not any(_day(s["period_end"]) > bound for s, _ in surviving) and not no_bill_expected(
                        live, activity_of(self.snap, card), cycle[1], self.today):
                    items.append(self.rule_item(card, cycle))
        return [i for i in items if i is not None] + other_items

    # --- blocks (§5.3) and skips (§5.5) ------------------------------------------------------------------------

    def block(self, source_id: int, items: list[DebitItem]) -> SourceBlock:
        """Projection over every auto item of the source; `items` keeps the renderable ones only."""
        source = self.accounts[source_id]
        items = sorted(items, key=lambda i: (i.debit_day, i.card_name, i.key))
        horizon = max((i.debit_day for i in items if i.in_window), default=self.today)
        projected = (Decimal(source["balance"])
                     - sum((i.amount for i in items if i.debit_day <= horizon), Decimal(0))
                     - self.pending_outflow(source_id, horizon))
        gap = self.cfg.min_buffer - projected
        return SourceBlock(account_id=source_id, name=source["name"], currency=source["currency"],
                           balance=Decimal(source["balance"]), projected=projected,
                           shortfall=gap if gap > 0 else None, items=[i for i in items if _renderable(i)])

    def pending_outflow(self, source_id: int, horizon: date) -> Decimal:
        total = Decimal(0)
        for inst in self.snap.pending:
            if _day(inst["due_date"]) > horizon:
                continue
            for ln in inst.get("lines", []):
                amount = Decimal(ln["amount"])
                if ln["account_id"] == source_id and amount < 0:
                    total -= amount
        return total

    def skips(self) -> list[Skip]:
        since = datetime.combine(self.today - timedelta(days=1), time(6), TZ)
        out = []
        for row in self.snap.audit:
            detail = row.get("detail") or {}
            if row.get("event") != SKIPPED or detail.get("reason") in (None, CUTOFF) or created_at_of(row) < since:
                continue
            card = self.accounts.get(detail.get("card_account_id")) or {}
            out.append(Skip(event_id=row["id"], card_name=card.get("name") or f"#{detail.get('card_account_id')}",
                            amount=Decimal(str(detail.get("amount", "0"))), reason=detail["reason"],
                            card_id=detail.get("card_account_id")))
        return sorted(out, key=lambda s: s.event_id)


def _match(live: list[dict], cycle_end: date) -> dict | None:
    """The live statement whose period_end is within ±3 days of `cycle_end`: closest first, then highest id."""
    near = [s for s in live if abs((_day(s["period_end"]) - cycle_end).days) <= MATCH_DAYS]
    return min(near, key=lambda s: (abs((_day(s["period_end"]) - cycle_end).days), -s["id"]), default=None)


def activity_of(snap: Snapshot, account: dict) -> list[dict] | None:
    """The card group's entries (`card_activity` is keyed by the master); None when the snapshot did not read them,
    for the whole map or for this group (an unread group is never quiet)."""
    if snap.card_activity is None:
        return None
    return snap.card_activity.get(account.get("combined_account_id") or account["id"])


def no_bill_expected(statements: list[dict], activity: list[dict] | None, cycle_end: date, today: date) -> bool:
    """§5.1 quiet cycle (owner ruling 2026-10-29): the bank sends no statement for a cycle that opens with nothing
    owed and has nothing on it. True when the last live statement ending before the cycle (period_end more than 3
    days before `cycle_end`) has `statement_total <= 0`, ends inside the entry read window, and no top-level entry
    of the group other than a balance adjustment falls after its period_end through `cycle_end` (by the later of
    entry and posted date). Unknown activity, no such statement, or a statement older than the window → False."""
    if activity is None:
        return False
    before = [s for s in statements if s.get("mode") == "live" and not s.get("retired")
              and _day(s["period_end"]) < cycle_end - timedelta(days=MATCH_DAYS)]
    if not before:
        return False
    last = max(before, key=lambda s: (_day(s["period_end"]), s["id"]))
    last_end = _day(last["period_end"])
    if Decimal(str(last["statement_total"])) > 0 or last_end < today - timedelta(days=READ_DAYS):
        return False
    return not any(e["kind"] not in NOT_ACTIVITY and _top_level(e) and last_end < _booked(e) <= cycle_end
                   for e in activity)


def _booked(entry: dict) -> date:
    """The later of entry and posted date: a purchase the bank posts after the closing day is on the next bill."""
    posted = _day(entry.get("posted_date"))
    booked = _day(entry["entry_date"])
    return max(booked, posted) if posted else booked


def _top_level(entry: dict) -> bool:
    """Split children (`parent_entry_id` set) are ignored, as the job's `find_twins` ignores them."""
    return entry.get("parent_entry_id") is None


def _renderable(item: DebitItem) -> bool:
    """In window, overdue, 未扣款, or 到期日未辨識 (no debit day, so never in a window: always shown)."""
    return item.in_window or item.overdue_days > 0 or item.unpaid_reason is not None or item.debit_day is None


def _is_card(account: dict) -> bool:
    """Rule-bearing master cards; 副卡 (combined_account_id set) are folded into their master."""
    return (bool(account.get("is_credit")) and not account.get("is_archived")
            and not account.get("combined_account_id")
            and all(account.get(k) is not None for k in ("closing_day", "due_rule", "due_value")))


def build_daily(snap: Snapshot, cfg: NotifyConfig) -> DailyReport:
    run = _Run(snap, cfg)
    items = [item for card in snap.accounts if _is_card(card) for item in run.card_items(card)]
    by_source: dict[int, list[DebitItem]] = {}
    for item in items:
        if item.cls == "auto":
            by_source.setdefault(item.source_id, []).append(item)
    blocks = sorted((b for b in (run.block(source_id, group) for source_id, group in by_source.items()) if b.items),
                    key=lambda b: (b.items[0].debit_day, b.account_id))
    # statement-backed bills first (by debit day, undated last), then the 帳單未到 lines
    manual = sorted((i for i in items if i.cls == "manual" and _renderable(i)),
                    key=lambda i: (i.from_rule, i.debit_day or date.max, i.card_name, i.key))
    return DailyReport(today=snap.today, blocks=blocks, manual=manual, skips=run.skips())
