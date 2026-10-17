"""Agent proposals on reconciliation cases (R1c Task 9; rulings 21, 27, 31).

Lock order everywhere: statement FOR UPDATE → case FOR UPDATE → proposal rows FOR UPDATE (the engine takes the same
order, after the import key and the budget row). `create_proposal` and `reject_proposal` lock them here;
`apply_proposal` does only an unlocked pre-check and delegates to the engine, which re-validates the proposal under
its own locks. `supersede_stale` marks pending proposals written against an older case version; the routes commit it
before they select or answer, so "superseded on read" is durable even when the request later fails. Services never
commit."""
from __future__ import annotations

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.models import AccountStatement, ReconciliationCase, ReconciliationProposal
from app.services import reconciliation_service as rec
from app.services import stand_ins
from app.services.errors import CodedConflictError, CodedValidationError, NotFoundError, ValidationError
from app.services.reconciliation_actions import ACTIONS, OPEN_STATUSES, allowed, audit, engine
from app.services.reconciliation_actions.base import POLICY, ActionParams

# Free choices validated by the action itself (a category or project is not part of the case's evidence).
UNSCOPED_IDS = frozenset({"category_id", "project_id"})
SIBLING_KEY = "sibling_candidates"
# Which keys of the case's candidates/context name the ids a param of each type may take; the case's own
# `entry_id` / `line_id` columns are added in `_allowed_ids`. `account_id` is handled separately.
ID_KEYS = {
    "entry_id": ("entry_id",), "original_entry_id": ("entry_id",),
    "line_id": ("line_id", "old_line_id", "new_line_id"),
    "group_id": ("group_id", "split_group_id"),
    "definition_id": ("definition_id",),
}


def _ids_under(value, keys: tuple[str, ...]) -> set[int]:
    """Every integer stored under one of `keys`, at any depth."""
    out: set[int] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in keys and isinstance(item, int) and not isinstance(item, bool):
                out.add(item)
            else:
                out |= _ids_under(item, keys)
    elif isinstance(value, (list, tuple)):
        for item in value:
            out |= _ids_under(item, keys)
    return out


def _allowed_ids(db: Session, case: ReconciliationCase, name: str) -> set[int]:
    context = {k: v for k, v in (case.context or {}).items() if k != SIBLING_KEY}
    evidence = [list(case.candidates or []), context]
    if name == "account_id":  # a candidate's account, or the account of the statement a move hint names
        out = set().union(*(_ids_under(e, ("account_id",)) for e in evidence))
        hinted = context.get("statement_id")
        if isinstance(hinted, int) and not isinstance(hinted, bool):
            account = db.execute(select(AccountStatement.account_id).where(AccountStatement.id == hinted)
                                 ).scalar_one_or_none()
            if account is not None:
                out.add(account)
        return out
    keys = ID_KEYS.get(name)
    if keys is None:
        return set()
    out = set().union(*(_ids_under(e, keys) for e in evidence))
    own = case.line_id if name == "line_id" else case.entry_id if name in ("entry_id", "original_entry_id") else None
    return out | ({own} if own is not None else set())


def _validate(db: Session, case: ReconciliationCase, action: str, params: dict) -> ActionParams:
    if action not in ACTIONS or action not in allowed(case):
        raise ValidationError("action", "not allowed for this case")
    if params.get("allow_sibling") is True:
        raise CodedValidationError("params", "sibling_not_proposable", "allow_sibling is for the owner")
    parsed = engine._parse(ACTIONS[action], params)
    if getattr(parsed, "allow_sibling", False):
        raise CodedValidationError("params", "sibling_not_proposable", "allow_sibling is for the owner")
    for name in type(parsed).model_fields:
        value = getattr(parsed, name)
        if name.endswith("_id") and name not in UNSCOPED_IDS and value is not None \
                and value not in _allowed_ids(db, case, name):
            raise CodedValidationError("params", "id_out_of_scope", f"{name} {value}")
    return parsed


def _lock_case_chain(db: Session, case_id: int) -> ReconciliationCase:
    """Statement → case, both FOR UPDATE; the case row is re-read under its lock (`populate_existing`), so the
    caller sees its current status and version."""
    probe = db.get(ReconciliationCase, case_id)
    if probe is None:
        raise NotFoundError(f"case {case_id} not found")
    rec._lock_statement(db, probe.statement_id)
    case = db.execute(select(ReconciliationCase).where(ReconciliationCase.id == case_id).with_for_update()
                      .execution_options(populate_existing=True)).scalar_one()
    return case


def _pending(db: Session, case_id: int, *, author: str | None = None) -> list[ReconciliationProposal]:
    q = select(ReconciliationProposal).where(ReconciliationProposal.case_id == case_id,
                                             ReconciliationProposal.status == "pending")
    if author is not None:
        q = q.where(ReconciliationProposal.author == author)
    return list(db.execute(q.order_by(ReconciliationProposal.id).with_for_update()
                           .execution_options(populate_existing=True)).scalars())


def create_proposal(db: Session, *, case_id: int, payload, author: str) -> ReconciliationProposal:
    case = _lock_case_chain(db, case_id)
    if case.status not in OPEN_STATUSES:
        raise CodedConflictError("case_closed", f"case {case.id} is {case.status}")
    parsed = _validate(db, case, payload.action, payload.params)
    for previous in _pending(db, case.id, author=author):
        previous.status, previous.decided_at = "superseded", func.now()
        audit.record(db, event="proposal_superseded", actor=author, statement_id=case.statement_id, case_id=case.id,
                     detail={"proposal_id": previous.id, "by": "new_proposal"})
    row = ReconciliationProposal(
        case_id=case.id, case_version=case.version, event_id=case.event_id, action=payload.action,
        params=parsed.model_dump(mode="json"), rationale=payload.rationale, confidence=payload.confidence,
        author=author, status="pending")
    db.add(row)
    case.status = "proposed"
    db.flush()
    audit.record(db, event="proposal_created", actor=author, statement_id=case.statement_id, case_id=case.id,
                 detail={"proposal_id": row.id, "action": payload.action, "confidence": str(payload.confidence)})
    return row


def reopen_idle(db: Session, case_ids) -> None:
    """A `proposed` case with no pending proposal left goes back to `open`."""
    if not case_ids:
        return
    live = select(ReconciliationProposal.id).where(ReconciliationProposal.case_id == ReconciliationCase.id,
                                                   ReconciliationProposal.status == "pending").exists()
    db.execute(update(ReconciliationCase).where(ReconciliationCase.id.in_(sorted(set(case_ids))),
                                                ReconciliationCase.status == "proposed", ~live)
               .values(status="open").execution_options(synchronize_session="fetch"))


def supersede_stale(db: Session, *, case_id: int | None = None) -> int:
    """Pending proposals whose case moved on (`case_version < case.version`) → superseded, audited, and a `proposed`
    case left without a live proposal reopens. `case_id` limits it to one case. Returns how many.
    R1d item 2: the statements of those cases are locked first (ascending), then the cases (ascending), then the
    proposals are updated — `FOR UPDATE SKIP LOCKED` on statements and cases in the global form (a held row is
    left to its holder's own path or the next call) and `FOR UPDATE` when scoped. The statement lock is required:
    the audit row's FK takes FOR KEY SHARE on the statement, which conflicts with a FOR UPDATE held elsewhere;
    taking it first makes this helper's order a prefix of the engine's and of `create_proposal`'s."""
    stale_case = (select(ReconciliationProposal.id)
                  .where(ReconciliationProposal.case_id == ReconciliationCase.id,
                         ReconciliationProposal.status == "pending",
                         ReconciliationProposal.case_version < ReconciliationCase.version).exists())
    scope = [ReconciliationCase.id == case_id] if case_id is not None else []
    skip = case_id is None
    statements = list(db.execute(
        select(AccountStatement.id)
        .where(AccountStatement.id.in_(select(ReconciliationCase.statement_id).where(stale_case, *scope)))
        .order_by(AccountStatement.id).with_for_update(skip_locked=skip)).scalars())
    if not statements:
        return 0
    cases = list(db.execute(
        select(ReconciliationCase.id)
        .where(stale_case, ReconciliationCase.statement_id.in_(statements), *scope)
        .order_by(ReconciliationCase.id).with_for_update(skip_locked=skip)).scalars())
    if not cases:
        return 0
    stale = (select(ReconciliationCase.id)
             .where(ReconciliationCase.id == ReconciliationProposal.case_id,
                    ReconciliationProposal.case_version < ReconciliationCase.version).exists())
    rows = db.execute(update(ReconciliationProposal)
                      .where(ReconciliationProposal.status == "pending", ReconciliationProposal.case_id.in_(cases), stale)
                      .values(status="superseded", decided_at=func.now())
                      .returning(ReconciliationProposal.id, ReconciliationProposal.case_id)
                      .execution_options(synchronize_session=False)).all()
    for proposal_id, proposal_case_id in sorted(rows):
        statement_id = db.execute(select(ReconciliationCase.statement_id)
                                  .where(ReconciliationCase.id == proposal_case_id)).scalar_one()
        audit.record(db, event="proposal_superseded", actor="system", statement_id=statement_id,
                     case_id=proposal_case_id, detail={"proposal_id": proposal_id, "by": "case_version"})
    reopen_idle(db, cases)
    db.expire_all()
    return len(rows)


def list_proposals(db: Session, *, status: str | None = None, case_id: int | None = None,
                   limit: int = 200) -> list[ReconciliationProposal]:
    """After the global supersede: a pending proposal whose case moved on but whose rows were held (SKIP LOCKED)
    is not listed — it is superseded durably by its holder's own path or the next call (ruling 21)."""
    supersede_stale(db)
    current = or_(ReconciliationProposal.status != "pending",
                  ReconciliationProposal.case_version == ReconciliationCase.version)
    q = (select(ReconciliationProposal)
         .join(ReconciliationCase, ReconciliationCase.id == ReconciliationProposal.case_id).where(current))
    if status is not None:
        q = q.where(ReconciliationProposal.status == status)
    if case_id is not None:
        q = q.where(ReconciliationProposal.case_id == case_id)
    return list(db.execute(q.order_by(ReconciliationProposal.id.desc()).limit(limit)
                           .execution_options(populate_existing=True)).scalars())


def pending_for_case(db: Session, case_id: int) -> list[ReconciliationProposal]:
    return list(db.execute(select(ReconciliationProposal).where(
        ReconciliationProposal.case_id == case_id, ReconciliationProposal.status == "pending")
        .order_by(ReconciliationProposal.id)).scalars())


def _get(db: Session, proposal_id: int) -> ReconciliationProposal:
    proposal = db.get(ReconciliationProposal, proposal_id)
    if proposal is None:
        raise NotFoundError(f"proposal {proposal_id} not found")
    return proposal


def apply_proposal(db: Session, *, proposal_id: int, label: str, request_id: str | None):
    """Unlocked pre-check, then the engine (which locks and re-validates the proposal row). Returns (action, replayed).
    A policy-authored `backfill_entry` / `create_system_entry` proposal is refused under the locks (409
    `owner_may_record`) when an owner entry may already record its line (`policy_service.refuse_owner_recorded`; the
    action re-checks under the writer barrier)."""
    proposal = _get(db, proposal_id)
    if proposal.status != "pending":
        raise CodedConflictError("proposal_not_pending", f"proposal {proposal.id} is {proposal.status}")
    case = db.get(ReconciliationCase, proposal.case_id)
    if proposal.case_version != case.version:
        raise CodedConflictError("proposal_superseded", f"proposal {proposal.id} was made at version "
                                                       f"{proposal.case_version}, the case is at {case.version}")
    precheck = None
    if proposal.author == POLICY and proposal.action in stand_ins.OWNER_TWIN_GUARDED:
        from app.services import policy_service  # local: policy_service imports this module
        precheck = policy_service.refuse_owner_recorded  # 409 owner_may_record (an owner twin or a replaced event)
    return engine.apply_action(db, case_id=case.id, action=proposal.action, params=proposal.params, actor="owner",
                               label=label, proposal_id=proposal.id, request_id=request_id, precheck=precheck)


def reject_proposal(db: Session, *, proposal_id: int, label: str, note: str | None,
                    request_id: str | None = None) -> ReconciliationProposal:
    proposal = _get(db, proposal_id)
    case = _lock_case_chain(db, proposal.case_id)
    proposal = db.execute(select(ReconciliationProposal).where(ReconciliationProposal.id == proposal_id)
                          .with_for_update().execution_options(populate_existing=True)).scalar_one()
    if proposal.status != "pending":
        raise CodedConflictError("proposal_not_pending", f"proposal {proposal.id} is {proposal.status}")
    proposal.status, proposal.decided_at = "rejected", func.now()
    db.flush()
    live = [p for p in _pending(db, case.id) if p.id != proposal.id and p.case_version == case.version]
    if case.status == "proposed" and not live:
        case.status = "open"
    audit.record(db, event="proposal_rejected", actor=label, statement_id=case.statement_id, case_id=case.id,
                 request_id=request_id, detail={"proposal_id": proposal.id, "note": note})
    return proposal
