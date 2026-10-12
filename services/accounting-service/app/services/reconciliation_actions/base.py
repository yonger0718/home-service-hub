"""The action protocol (R1c; spec §4.9, §7.2, §7.4): what every reconciliation action declares — a params model, the
case kinds and actors it serves, its effect slot (ruling 7), the rows it locks, an unlocked `prepare` returning the
C6 expected snapshot, `apply` under the engine's locks, and `invert` for revert. The engine (`engine.py`) owns the
transaction; an action only validates and writes its own effect."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Iterable, Protocol

from pydantic import BaseModel, ConfigDict
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import (
    Account, AccountStatement, ReconciliationAction, ReconciliationCase, ScheduleInstance, StatementLine,
)
from app.services.errors import CodedValidationError
from app.services.statements import matching

from . import snapshots

OWNER = "owner"
POLICY = "policy"


class ActionParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass
class Context:
    db: Session
    case: ReconciliationCase  # re-read FOR UPDATE once the statement lock is held (unlocked in `prepare`)
    statement: AccountStatement
    account: Account
    line: StatementLine | None  # the case's line in the current revision (by event), or None
    actor: str  # "owner" | "policy"
    label: str  # the token label, or "policy"
    run_id: int | None
    request_id: str | None
    rules: matching.Rules
    proposal_id: int | None = None  # the agent proposal being applied (ruling 31: policy + proposal = `agent` coverage)


@dataclass
class Effect:
    created_entry_ids: list[int] = field(default_factory=list)
    deleted_entry_ids: list[int] = field(default_factory=list)
    touched_entry_ids: list[int] = field(default_factory=list)
    touched_group_ids: list[int] = field(default_factory=list)
    before: dict = field(default_factory=dict)  # {"entries": {id: snap}, "groups": {id: snap}, "case": {...}}
    after: dict = field(default_factory=dict)
    inverse: dict = field(default_factory=dict)  # operation-specific, see each action
    coverage_ids: list[int] = field(default_factory=list)
    resolves_case: bool = True
    case_status: str = "resolved"  # or "dismissed"
    explanation: str | None = None
    case_context: dict = field(default_factory=dict)  # merged into case.context when resolves_case is False


@dataclass(frozen=True)
class LockSet:
    entry_ids: tuple[int, ...] = ()
    group_ids: tuple[int, ...] = ()
    statement_ids: tuple[int, ...] = ()  # extra statements (move_account target)
    definition_ids: tuple[int, ...] = ()  # schedule definitions/instances of touched entries (D32), locked after
    instance_ids: tuple[int, ...] = ()  # the statements, before the groups
    account_lock: bool = False  # balance_adjustment_asof only
    barrier: bool = False  # balance_adjustment_asof only (ruling 15)


@dataclass
class Prepared:
    """`prepare`'s result: `expected` = `snapshots.snapshot_set` of every row the action will touch, read without
    locks (C6); the engine re-reads exactly those ids under the locks and refuses on any difference. `data` carries
    whatever else the action computed outside the locks (FX quotes, reference checks). `lock_set` is what the
    engine locked for this attempt (its own unlocked `lock_targets` read, set by the engine after `prepare`), so
    `validate_before` can re-derive a target under the locks and refuse when it differs (move_account)."""
    expected: dict
    data: dict = field(default_factory=dict)
    lock_set: LockSet = field(default_factory=LockSet)


class NotRevertable(CodedValidationError):
    """422 `not_revertable` on `action_id` (an action's `invert` raises it; the engine too)."""

    def __init__(self, detail: str | None = None):
        super().__init__("action_id", "not_revertable", detail)


class Action(Protocol):
    name: str
    Params: type[ActionParams]
    case_kinds: frozenset[str]  # kinds the action is allowed on (the registry table narrows further)
    actors: frozenset[str]  # policy-eligible actions add "policy"

    def effect_slot(self, ctx: Context, params) -> str: ...

    def check_before_locks(self, ctx: Context, params) -> None: ...

    def validate_before(self, ctx: Context, params, prepared: Prepared, before: dict) -> None: ...

    def lock_targets(self, ctx: Context, params) -> LockSet: ...

    def prepare(self, ctx: Context, params) -> Prepared: ...

    def apply(self, ctx: Context, params, prepared: Prepared, effect: Effect) -> None: ...

    def invert(self, ctx: Context, action: ReconciliationAction) -> Effect: ...

    def revert_lock_targets(self, ctx: Context, action: ReconciliationAction) -> LockSet: ...


class ActionBase:
    """Defaults: the effect slot (below), no extra validation, nothing locked, `prepare` = the expected snapshot of the
    lock targets' rows (`snapshots.expected_set`), no inverse, and revert locks taken from the action row."""
    name: ClassVar[str]
    Params: ClassVar[type[ActionParams]]
    case_kinds: ClassVar[frozenset[str]] = frozenset()
    actors: ClassVar[frozenset[str]] = frozenset({OWNER})
    slot: ClassVar[str | None] = None  # ledger-effect actions set their fixed slot (ruling 7)

    def effect_slot(self, ctx: Context, params) -> str:
        """Ruling 31: an action that writes no ledger rows (match, unmatch, dismiss, defer_next_period) is one effect
        per case VERSION, `v<version>` (the version the idempotency key was built from, under the case lock), so a
        reopened case can be acted on again; ledger-effect actions keep their fixed slot (`create`, `fee`, `amount`,
        `move`, `split`, `balance`, `plan`) and stay one-shot per event while applied."""
        return self.slot if self.slot is not None else f"v{ctx.case.version}"

    def check_before_locks(self, ctx: Context, params) -> None:
        """Under the statement and case locks, after the case checks and the precheck, before the account row, the
        writer barrier and any row lock (a refusal here takes none of them)."""
        return None

    def validate_before(self, ctx: Context, params, prepared: Prepared, before: dict) -> None:
        return None

    def lock_targets(self, ctx: Context, params) -> LockSet:
        return LockSet()

    def prepare(self, ctx: Context, params) -> Prepared:
        targets = self.lock_targets(ctx, params)
        return Prepared(expected=snapshots.expected_set(ctx.db, targets.entry_ids, targets.group_ids))

    def apply(self, ctx: Context, params, prepared: Prepared, effect: Effect) -> None:
        raise NotImplementedError(self.name)

    def invert(self, ctx: Context, action: ReconciliationAction) -> Effect:
        raise NotRevertable(f"{self.name} has no inverse")

    def revert_lock_targets(self, ctx: Context, action: ReconciliationAction) -> LockSet:
        """The rows the action recorded: its `after` snapshot ids plus touched/created ids and groups."""
        entries, groups = snapshots.ids_of(action.after)
        return lockset_for(ctx.db, entry_ids={*entries, *(action.touched_entry_ids or ()),
                                              *(action.created_entry_ids or ())},
                           group_ids={*groups, *(action.touched_group_ids or ())})


def schedule_rows(db: Session, entry_ids: Iterable[int]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """(definition ids, instance ids) of the posted schedule instances listing any of the entries (unlocked)."""
    ids = sorted(set(entry_ids))
    if not ids:
        return (), ()
    rows = db.execute(select(ScheduleInstance.id, ScheduleInstance.definition_id)
                      .where(or_(*(ScheduleInstance.posted_entry_ids.contains([i]) for i in ids)))).all()
    return tuple(sorted({d for _, d in rows})), tuple(sorted({i for i, _ in rows}))


def lockset_for(db: Session, *, entry_ids: Iterable[int] = (), group_ids: Iterable[int] = (),
                statement_ids: Iterable[int] = (), account_lock: bool = False, barrier: bool = False) -> LockSet:
    """A LockSet over the closure of the rows (members, children) with the schedule rows behind them (D32)."""
    entries, groups = snapshots.closure(db, entry_ids, group_ids)
    definitions, instances = schedule_rows(db, entries)
    return LockSet(entry_ids=tuple(entries), group_ids=tuple(groups), statement_ids=tuple(sorted(set(statement_ids))),
                   definition_ids=definitions, instance_ids=instances, account_lock=account_lock, barrier=barrier)


def current_line(db: Session, case: ReconciliationCase, statement: AccountStatement) -> StatementLine | None:
    """The case's line in the statement's current revision: `case.line_id` itself, else the current line of the
    case's event; None for entry/statement cases or a line no longer printed."""
    if case.line_id is None:
        return None
    line = db.get(StatementLine, case.line_id)
    if line is not None and line.revision_id == statement.current_revision_id:
        return line
    if case.event_id is None:
        return None
    return db.execute(select(StatementLine).where(StatementLine.revision_id == statement.current_revision_id,
                                                  StatementLine.event_id == case.event_id)).scalar_one_or_none()
