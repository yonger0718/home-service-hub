"""Daily dedupe (spec §5.6): keep only what passes a trigger, return the trigger-key set and the updated state."""
from __future__ import annotations

from dataclasses import replace
from datetime import date

from .debits import DailyReport, DebitItem
from .state import SourceState, State, StatementState


def _item_keys(items: list[DebitItem], state: State, today: date) -> set[str]:
    keys: set[str] = set()
    for it in items:
        known = state.statements.get(it.key)
        if known is None or known.first_notified in (None, today):  # today: stable across same-day re-runs
            keys.add(f"new:{it.key}")
        if it.debit_day == today:
            keys.add(f"today:{it.key}")
        if it.overdue_days > 0 or it.unpaid_reason or it.from_rule:
            keys.add(f"overdue:{it.key}")
    return keys


def apply(report: DailyReport, state: State, today: date) -> tuple[DailyReport, State, frozenset[str]]:
    """Pure: returns (filtered report, updated copy of state, trigger keys). A passing block keeps all its items."""
    new = replace(state, statements=dict(state.statements), sources=dict(state.sources),
                  skips_seen=set(state.skips_seen))
    keys: set[str] = set()
    blocks, cleared = [], []

    for blk in report.blocks:
        bkeys = _item_keys(blk.items, state, today)
        if blk.shortfall is not None and blk.shortfall > 0:
            bkeys.add(f"short:{blk.account_id}")
        prev = state.sources.get(blk.account_id)
        if prev is not None and prev.last_shortfall is not None and blk.shortfall is None:
            keys.add(f"cleared:{blk.account_id}")
            cleared.append(blk)
        if bkeys:
            blocks.append(blk)
            keys |= bkeys
        new.sources[blk.account_id] = SourceState(last_shortfall=blk.shortfall)

    shown = {b.account_id for b in report.blocks}
    for sid, src in state.sources.items():  # block gone entirely: forget the shortfall, nothing to render
        if sid not in shown and src.last_shortfall is not None:
            new.sources[sid] = SourceState(last_shortfall=None)

    mkeys = _item_keys(report.manual, state, today)
    manual = report.manual if mkeys else []
    keys |= mkeys

    skips = [s for s in report.skips if s.event_id not in state.skips_seen]
    keys |= {f"skip:{s.event_id}" for s in skips}
    new.skips_seen |= {s.event_id for s in skips}

    for it in [i for b in blocks for i in b.items] + manual:
        if it.key not in new.statements or new.statements[it.key].first_notified is None:
            new.statements[it.key] = StatementState(first_notified=today)

    out = replace(report, blocks=blocks, manual=manual, skips=skips, cleared=cleared)
    return out, new, frozenset(keys)
