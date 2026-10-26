"""Coverage-only actions (R1c; spec §4.9 action table, rulings 4, 8, 14, 16, 25, 29): `match` claims a line with an
entry (optionally with its children; a child alone as the line's principal, issue #183) or a complete group as owner
coverage (`match_kind='manual'`, or `'agent'` whenever actor='policy' applies it; `match_rule='owner'`, kept by
reconcile); `unmatch` releases a line's coverage and flags the statement for matching again. Neither writes a ledger
row, so neither emits a dirty event."""
from __future__ import annotations

from decimal import Decimal

from pydantic import model_validator
from sqlalchemy import exists, select

from app.models import EntryGroup, LedgerEntry, ReconciliationAction, StatementCoverage, StatementLine
from app.schemas.writes import Int32
from app.services import coverage_service, settings_service
from app.services.errors import CodedConflictError, CodedValidationError
from app.services.statements import matching

from . import register
from .base import (
    OWNER, POLICY, ActionBase, ActionParams, Context, Effect, LockSet, NotRevertable, Prepared, lockset_for,
)

MANUAL, OWNER_RULE = "manual", "owner"


def _refuse(field: str, code: str, detail: str | None = None) -> CodedValidationError:
    return CodedValidationError(field, code, detail)


def case_line(ctx: Context, line_id: int) -> StatementLine:
    """A line of the current revision that belongs to the case: the case's line or another line of its event."""
    line = ctx.db.get(StatementLine, line_id)
    if (line is None or line.revision_id != ctx.statement.current_revision_id
            or (line.id != ctx.case.line_id and (ctx.case.event_id is None or line.event_id != ctx.case.event_id))):
        raise _refuse("line_id", "line_not_in_case", f"line {line_id}")
    return line


def _line_claimed(ctx: Context, line_id: int) -> bool:
    return ctx.db.execute(select(exists().where(StatementCoverage.line_id == line_id,
                                                StatementCoverage.status == "active"))).scalar_one()


def _locked_group(ctx: Context, entry: LedgerEntry) -> None:
    """Under the locks: the entry's group must be one the engine locked. `lock_targets` read the group without locks,
    so a regroup committed before the expected snapshot (into another group, or into one from none) would pass the
    snapshot check while the new group is neither locked nor in `before` (2026-10-24 spec §5.1; precedent
    `Prepared.lock_set`)."""
    if entry.group_id is not None and entry.group_id not in (ctx.lock_set.group_ids if ctx.lock_set else ()):
        raise CodedConflictError("snapshot_conflict", f"entry {entry.id} regrouped to {entry.group_id}")


class MatchParams(ActionParams):
    line_id: Int32
    entry_id: Int32 | None = None
    group_id: Int32 | None = None
    with_children: bool = False
    allow_sibling: bool = False

    @model_validator(mode="after")
    def _one_target(self):
        if (self.entry_id is None) == (self.group_id is None):
            raise ValueError("exactly one of entry_id and group_id")
        if self.with_children and self.entry_id is None:
            raise ValueError("with_children applies to entry_id")
        return self


class MatchAction(ActionBase):
    name = "match"
    Params = MatchParams
    case_kinds = frozenset({"line_unmatched", "ambiguous", "amount_delta", "recheck"})
    actors = frozenset({OWNER, POLICY})  # coverage only: effect slot c<case.id>v<version> (rulings 31, 36)

    def lock_targets(self, ctx: Context, params: MatchParams) -> LockSet:
        """The entry (or the group) named; a grouped entry's group too (split members, 2026-10-24 spec §5.1), so the
        snapshot closure covers every member and a concurrent regroup is a `snapshot_conflict`."""
        if params.entry_id is None:
            return lockset_for(ctx.db, group_ids=(params.group_id,))
        entry = ctx.db.get(LedgerEntry, params.entry_id)  # missing: `_claim` refuses `not_found` under the locks
        return lockset_for(ctx.db, entry_ids=(params.entry_id,),
                           group_ids=() if entry is None or entry.group_id is None else (entry.group_id,))

    def validate_before(self, ctx: Context, params: MatchParams, prepared: Prepared, before: dict) -> None:
        self._claim(ctx, params)

    def apply(self, ctx: Context, params: MatchParams, prepared: Prepared, effect: Effect) -> None:
        line, rows, by_id = self._claim(ctx, params)
        written = coverage_service.write_claim(ctx.db, ctx.statement, line, rows, entries_by_id=by_id,
                                               match_kind=("agent" if ctx.actor == POLICY else MANUAL),
                                               match_rule=OWNER_RULE, run_id=ctx.run_id)
        effect.coverage_ids = [c.id for c in written]
        effect.touched_entry_ids = [r.entry_id for r in rows]
        member_of = None if params.entry_id is None else by_id[params.entry_id].group_id  # a split member alone
        effect.touched_group_ids = ([params.group_id] if params.group_id is not None
                                    else [] if member_of is None else [member_of])
        effect.inverse = {"release_line": line.id}
        if params.entry_id is not None and self._leaves_a_child_unclaimed(ctx, params.entry_id, rows):
            # ruling 29: an action may raise the flag; a still-claimable child is a matching reason (fee-child spec,
            # carrier rows); only a completed reconcile clears it
            ctx.statement.needs_recheck = True
            ctx.db.flush()

    @staticmethod
    def _leaves_a_child_unclaimed(ctx: Context, entry_id: int, rows) -> bool:
        """The matched entry has a child this claim did not take, that no active coverage holds and that the loader
        would read for this statement (participating account, statement currency — `_claimable_children`'s filter;
        AGENT-275 finding 2: a child the next reconcile cannot load is no matching reason)."""
        taken = [r.entry_id for r in rows]
        accounts = coverage_service.participating_accounts(ctx.db, ctx.statement.account_id)
        return ctx.db.execute(select(exists().where(
            LedgerEntry.parent_entry_id == entry_id, LedgerEntry.id.not_in(taken),
            LedgerEntry.account_id.in_(accounts), LedgerEntry.currency == ctx.statement.currency,
            ~exists().where(StatementCoverage.entry_id == LedgerEntry.id, StatementCoverage.status == "active"),
        ))).scalar_one()

    def invert(self, ctx: Context, action: ReconciliationAction) -> Effect:
        coverage_service.release_line(ctx.db, ctx.statement, action.inverse["release_line"], reason="revert")
        return Effect(touched_entry_ids=list(action.touched_entry_ids or ()),
                      touched_group_ids=list(action.touched_group_ids or ()))

    # --- validation (under the locks; `apply` re-derives the rows from the locked state) ------------------------

    def _claim(self, ctx: Context, params: MatchParams):
        db, statement = ctx.db, ctx.statement
        line = case_line(ctx, params.line_id)
        if _line_claimed(ctx, line.id):
            raise _refuse("line_id", "line_claimed", f"line {line.id}")
        accounts = set(coverage_service.participating_accounts(db, statement.account_id))
        if params.allow_sibling:
            accounts |= set(settings_service.account_siblings(db, statement.account_id))
        claimed = coverage_service.claimed_entry_ids(db)

        def gate(entry: LedgerEntry, field: str) -> None:
            if entry.account_id not in accounts:
                raise _refuse(field, "wrong_account", f"entry {entry.id}")
            if entry.currency != statement.currency:
                raise _refuse(field, "wrong_currency", f"entry {entry.id}")
            if entry.id in claimed:
                raise _refuse(field, "entry_claimed", f"entry {entry.id}")

        if params.entry_id is not None:
            entry = db.get(LedgerEntry, params.entry_id)
            if entry is None:
                raise _refuse("entry_id", "not_found", f"entry {params.entry_id}")
            _locked_group(ctx, entry)
            gate(entry, "entry_id")
            if entry.parent_entry_id is not None and params.with_children:
                # issue #183: a child is claimed alone as the line's principal, like the matcher's child
                # representation (§6.3; its parent need not be covered); it has no children to take
                raise _refuse("entry_id", "with_children_on_child", f"entry {entry.id}")
            if entry.group_id is not None and db.get(EntryGroup, entry.group_id).kind != "split":
                # a split member is matched alone like a standalone entry (spec §5.1; its coverage row keeps
                # `group_id NULL`); installment and reward_claim members only go with their group
                raise _refuse("entry_id", "entry_in_group", f"match group {entry.group_id}")
            picked = [(entry, "principal", None)]
            if params.with_children:
                for child in db.execute(select(LedgerEntry).where(LedgerEntry.parent_entry_id == entry.id)
                                        .order_by(LedgerEntry.id)).scalars():
                    gate(child, "entry_id")
                    picked.append((child, "child", None))
        else:
            if db.get(EntryGroup, params.group_id) is None:
                raise _refuse("group_id", "not_found", f"group {params.group_id}")
            members = list(db.execute(select(LedgerEntry).where(LedgerEntry.group_id == params.group_id,
                                                                LedgerEntry.parent_entry_id.is_(None))
                                      .order_by(LedgerEntry.id)).scalars())
            if not members:
                raise _refuse("group_id", "empty_group", f"group {params.group_id}")
            for member in members:
                gate(member, "group_id")
            picked = [(member, "member", params.group_id) for member in members]
        rows = [matching.Row(e.id, role, Decimal(e.amount), group_id) for e, role, group_id in picked]
        if not matching.conserved(tuple(rows), Decimal(line.flow_amount)):
            total = sum((r.flow for r in rows), Decimal(0))
            raise _refuse("params", "not_conserved", f"rows {total} vs line {line.flow_amount}")
        return line, rows, {e.id: e for e, _, _ in picked}


class UnmatchParams(ActionParams):
    line_id: Int32


class UnmatchAction(ActionBase):
    name = "unmatch"
    Params = UnmatchParams
    case_kinds = frozenset({"duplicate_claim", "recheck"})  # coverage only: slot c<case.id>v<version> (31, 36)

    @staticmethod
    def _rows(ctx: Context, line_id: int) -> list[StatementCoverage]:
        return coverage_service.active_rows(ctx.db, ctx.statement.id).get(line_id, [])

    def lock_targets(self, ctx: Context, params: UnmatchParams) -> LockSet:
        rows = self._rows(ctx, params.line_id)
        return lockset_for(ctx.db, entry_ids=[r.entry_id for r in rows if r.entry_id is not None],
                           group_ids=[r.group_id for r in rows if r.group_id is not None])

    def validate_before(self, ctx: Context, params: UnmatchParams, prepared: Prepared, before: dict) -> None:
        case_line(ctx, params.line_id)
        if not self._rows(ctx, params.line_id):
            raise _refuse("line_id", "not_claimed", f"line {params.line_id}")

    def apply(self, ctx: Context, params: UnmatchParams, prepared: Prepared, effect: Effect) -> None:
        rows = self._rows(ctx, params.line_id)
        effect.inverse = {"line_id": params.line_id,
                          "rows": [{"entry_id": r.entry_id, "role": r.role, "group_id": r.group_id} for r in rows],
                          "match_kind": rows[0].match_kind, "match_rule": rows[0].match_rule}
        effect.touched_entry_ids = [r.entry_id for r in rows if r.entry_id is not None]
        effect.touched_group_ids = sorted({r.group_id for r in rows if r.group_id is not None})
        coverage_service.release_line(ctx.db, ctx.statement, params.line_id, reason="owner")
        ctx.statement.needs_recheck = True  # matching must run again; only a completed reconcile clears it
        ctx.db.flush()

    def invert(self, ctx: Context, action: ReconciliationAction) -> Effect:
        """Re-claim the released rows as they were (kind, rule, roles) — the engine has already checked that the
        entries equal the action's `after`; they and the line must still be unclaimed."""
        db, inverse = ctx.db, action.inverse
        line = db.get(StatementLine, inverse["line_id"])
        if line is None or line.revision_id != ctx.statement.current_revision_id:
            raise NotRevertable(f"line {inverse['line_id']} is no longer printed")
        if _line_claimed(ctx, line.id):
            raise NotRevertable(f"line {line.id} is claimed again")
        claimed = coverage_service.claimed_entry_ids(db)
        entries: dict[int, LedgerEntry] = {}
        rows = []
        for item in inverse["rows"]:
            entry = None if item["entry_id"] is None else db.get(LedgerEntry, item["entry_id"])
            if entry is None:
                raise NotRevertable(f"entry {item['entry_id']} is gone")
            if entry.id in claimed:
                raise NotRevertable(f"entry {entry.id} is claimed again")
            entries[entry.id] = entry
            rows.append(matching.Row(entry.id, item["role"], Decimal(entry.amount), item["group_id"]))
        written = coverage_service.write_claim(db, ctx.statement, line, rows, entries_by_id=entries,
                                               match_kind=inverse["match_kind"], match_rule=inverse["match_rule"],
                                               run_id=ctx.run_id)
        return Effect(coverage_ids=[c.id for c in written], touched_entry_ids=list(entries),
                      touched_group_ids=sorted({r.group_id for r in rows if r.group_id is not None}))


register(MatchAction())
register(UnmatchAction())
