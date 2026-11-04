"""Read-only oracle: scores the reward engine against historical MOZE reward rows (spec 9.4 / 10.3).

Usage: PYTHONPATH=<tree>/services/accounting-service python scripts/reward_oracle.py  (no -I/-E: they drop PYTHONPATH)
Opens one SET TRANSACTION READ ONLY transaction and rolls it back. Writes ~/reports/home-hub-reward-oracle-<UTC ts>/report.md
(directory 700, file 600; counts and keys only, no names).
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from app.services.cycles import statement_period
from app.services.moze_backup_json import POSTING_MAP, ROUNDING_MAP
from app.services.reward_math import Slot, allot, estimate
from app.services.reward_windows import post_on_for

_ZERO = Decimal("0")


@dataclass(frozen=True)
class Pair:
    """One MOZE reward row joined to its purchase and rule. `key` is the MOZE reward ledger entry id."""
    key: int
    rule_id: int
    account_id: int
    purchase_amount: Decimal
    purchase_date: date
    method: str
    rate: Decimal | None
    fixed_amount: Decimal | None
    unit_spend: Decimal | None
    txn_rounding: str
    total_rounding: str
    total_cap: Decimal | None
    shared_cap_id: str | None
    closing_day: int | None
    window_start: date
    window_end: date
    posting: str
    delay_days: int | None
    post_month_offset: int | None
    post_day: int | None
    moze_amount: Decimal
    moze_date: date
    refunded: bool = False
    flagged: bool = False


@dataclass
class Report:
    maps: str = ""
    amount_ok: int = 0
    amount_total: int = 0
    excluded: int = 0
    largest: list = field(default_factory=list)          # (key, engine, moze), worst first
    window_totals: list = field(default_factory=list)    # (rule_id, window_start, engine, moze, ok)
    posting_exact: int = 0
    posting_same_period: int = 0
    posting_total: int = 0
    posting_mismatch_by_card: dict = field(default_factory=dict)  # account_id -> count
    placeholders_excluded: int = 0
    other_methods: int = 0      # uncapped non-placeholder fixed / per_unit pairs (not in the amount bar)
    posting_manual: int = 0


def _maps_header() -> str:
    return f"ROUNDING_MAP={dict(ROUNDING_MAP)}\nPOSTING_MAP={dict(POSTING_MAP)}"


def _is_placeholder(p: Pair) -> bool:
    return p.method == "fixed" and not (p.fixed_amount or _ZERO)


def _one(p: Pair) -> Decimal:
    return estimate(method=p.method, rate=p.rate, fixed_amount=p.fixed_amount, unit_spend=p.unit_spend,
                    txn_rounding=p.txn_rounding, amount=p.purchase_amount)


def score(pairs: Sequence[Pair], *, tolerance: Decimal = Decimal("1")) -> Report:
    rep = Report(maps=_maps_header())
    diffs: list[tuple[Decimal, int, Decimal, Decimal]] = []
    capped: dict[tuple, list[Pair]] = defaultdict(list)
    for p in pairs:
        if _is_placeholder(p):
            rep.placeholders_excluded += 1
            continue
        if p.refunded or p.flagged:
            rep.excluded += 1
            continue
        window = (p.window_start, p.window_end)
        engine_on = post_on_for(p.posting, p.delay_days, p.post_month_offset, p.post_day, window, p.purchase_date)
        if engine_on is None:
            rep.posting_manual += 1
        else:
            rep.posting_total += 1
            if engine_on == p.moze_date:
                rep.posting_exact += 1
            else:
                if statement_period(p.closing_day, engine_on) == statement_period(p.closing_day, p.moze_date):
                    rep.posting_same_period += 1
                else:
                    rep.posting_mismatch_by_card[p.account_id] = rep.posting_mismatch_by_card.get(p.account_id, 0) + 1
        if p.total_cap is not None or p.shared_cap_id is not None:
            capped[(('s', p.shared_cap_id) if p.shared_cap_id else ('r', p.rule_id), p.window_start)].append(p)
            continue
        if p.method != 'percent':
            rep.other_methods += 1
            continue
        eng = _one(p)
        rep.amount_total += 1
        if abs(eng - p.moze_amount) <= tolerance:
            rep.amount_ok += 1
        else:
            diffs.append((abs(eng - p.moze_amount), p.key, eng, p.moze_amount))
    for (_, start), group in sorted(capped.items(), key=lambda kv: (kv[0][0][0], str(kv[0][0][1]), kv[0][1])):
        group.sort(key=lambda p: (p.purchase_date, p.key))
        first = min(group, key=lambda p: p.rule_id)  # lowest rule id leads, as the engine does
        out = allot([Slot(p.key, _one(p), None) for p in group], cap=first.total_cap, external_used=_ZERO,
                    total_rounding=first.total_rounding)
        eng = sum(out.values(), _ZERO)
        moze = sum((p.moze_amount for p in group), _ZERO)
        rep.window_totals.append((first.rule_id, start, eng, moze, abs(eng - moze) <= tolerance))
    diffs.sort(key=lambda d: (-d[0], d[1]))
    rep.largest = [(k, e, m) for _, k, e, m in diffs[:10]]
    return rep


def render(rep: Report) -> str:
    pct = lambda a, b: f"{(100 * a / b):.1f}%" if b else "n/a"  # noqa: E731
    lines = ["# Reward oracle", "", "```", rep.maps, "```", "",
             f"amount (uncapped percent): {rep.amount_ok}/{rep.amount_total} ok ({pct(rep.amount_ok, rep.amount_total)})",
             f"excluded (flagged/refunded): {rep.excluded}",
             f"placeholders excluded: {rep.placeholders_excluded}",
             f"other methods (uncapped fixed/per_unit, not in the bar): {rep.other_methods}", "", "## Largest differences (key, engine, moze)"]
    lines += [f"- {k}: {e} vs {m}" for k, e, m in rep.largest] or ["- none"]
    lines += ["", "## Capped / shared window totals (rule, window_start, engine, moze, ok)"]
    lines += [f"- {r}, {s}, {e}, {m}, {'ok' if ok else 'DIFF'}" for r, s, e, m, ok in rep.window_totals] or ["- none"]
    lines += ["", "## Posting date",
              f"exact: {rep.posting_exact}/{rep.posting_total} ({pct(rep.posting_exact, rep.posting_total)})",
              f"same statement period, different day: {rep.posting_same_period}", f"manual posting (no engine date): {rep.posting_manual}", "mismatch by card (account id: count):"]
    lines += [f"- {a}: {n}" for a, n in sorted(rep.posting_mismatch_by_card.items())] or ["- none"]
    return "\n".join(lines) + "\n"


def load_pairs(db) -> list[Pair]:
    """MOZE reward rows with both source entry and rule set, joined to purchase and rule (read-only)."""
    from sqlalchemy import select
    from sqlalchemy.orm import aliased

    from app.models import Account, LedgerEntry, RewardRule
    from app.services.reward_windows import window_for

    src = aliased(LedgerEntry)
    q = (select(LedgerEntry, src, RewardRule)
         .join(src, src.id == LedgerEntry.reward_source_entry_id)
         .join(RewardRule, RewardRule.id == LedgerEntry.reward_rule_id)
         .where(LedgerEntry.kind == "reward", LedgerEntry.moze_id.is_not(None)))
    rows = db.execute(q).all()
    refunded_ids = set(db.scalars(select(LedgerEntry.refunds_entry_id).where(
        LedgerEntry.refunds_entry_id.in_({purchase.id for _, purchase, _ in rows}))))
    out: list[Pair] = []
    closing: dict[int, int | None] = {}
    for reward, purchase, rule in rows:
        if rule.account_id not in closing:
            closing[rule.account_id] = db.get(Account, rule.account_id).closing_day
        w = window_for(rule.window, closing[rule.account_id], purchase.entry_date)
        refunded = purchase.id in refunded_ids
        out.append(Pair(
            key=reward.id, rule_id=rule.id, account_id=rule.account_id, purchase_amount=Decimal(purchase.amount),
            purchase_date=purchase.entry_date, method=rule.method, rate=rule.rate, fixed_amount=rule.fixed_amount,
            unit_spend=rule.unit_spend, txn_rounding=rule.txn_rounding, total_rounding=rule.total_rounding,
            total_cap=rule.total_cap, shared_cap_id=str(rule.shared_cap_id) if rule.shared_cap_id else None,
            closing_day=closing[rule.account_id], window_start=w[0], window_end=w[1], posting=rule.posting, delay_days=rule.delay_days,
            post_month_offset=rule.post_month_offset, post_day=rule.post_day,
            moze_amount=Decimal(reward.amount), moze_date=reward.entry_date,
            refunded=refunded, flagged=bool(reward.needs_review or purchase.needs_review)))
    return out


def main(argv: Sequence[str] | None = None) -> int:
    from sqlalchemy import text

    from app.database import SessionLocal

    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        pairs = load_pairs(db)
        db.rollback()
    rep = score(pairs)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    d = Path.home() / "reports" / f"home-hub-reward-oracle-{ts}"
    d.parent.mkdir(mode=0o700, exist_ok=True)
    d.mkdir(mode=0o700)
    fd = os.open(d / "report.md", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(render(rep))
    print(f"scored {len(pairs)} pairs: amount {rep.amount_ok}/{rep.amount_total}, posting exact "
          f"{rep.posting_exact}/{rep.posting_total}; report {d / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
