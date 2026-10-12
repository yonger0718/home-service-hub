"""Agent proposals on reconciliation cases (R1c Task 9; rulings 21, 27, 31).

Lock order everywhere: statement FOR UPDATE → case FOR UPDATE → proposal rows FOR UPDATE (the engine takes the same
order, after the import key and the budget row). `create_proposal` and `reject_proposal` lock them here;
`apply_proposal` does only an unlocked pre-check and delegates to the engine, which re-validates the proposal under
its own locks. `supersede_stale` marks pending proposals written against an older case version; the routes commit it
before they select or answer, so "superseded on read" is durable even when the request later fails. Services never
commit."""
from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import ReconciliationCase, ReconciliationProposal
from app.services import reconciliation_service as rec
from app.services.errors import CodedConflictError, CodedValidationError, NotFoundError, ValidationError
from app.services.reconciliation_actions import ACTIONS, OPEN_STATUSES, allowed, audit, engine
from app.services.reconciliation_actions.base import ActionParams

# Free choices validated by the action itself (a category or project is not part of the case's evidence).
UNSCOPED_IDS = frozenset({"category_id", "project_id"})
SIBLING_KEY = "sibling_candidates"


def _ids_in(value) -> set[int]:
    """Every integer under a key ending in `_id`, at any depth."""
    out: set[int] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and key.endswith("_id") and isinstance(item, int) and not isinstance(item, bool):
                out.add(item)
            else:
                out |= _ids_in(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            out |= _ids_in(item)
    return out


def _scope(case: ReconciliationCase) -> set[int]:
    context = {k: v for k, v in (case.context or {}).items() if k != SIBLING_KEY}
    ids = _ids_in(list(case.candidates or [])) | _ids_in(context)
    return ids | {i for i in (case.line_id, case.entry_id) if i is not None}


def _validate(case: ReconciliationCase, action: str, params: dict) -> ActionParams:
    if action not in ACTIONS or action not in allowed(case):
        raise ValidationError("action", "not allowed for this case")
    if params.get("allow_sibling") is True:
        raise CodedValidationError("params", "sibling_not_proposable", "allow_sibling is for the owner")
    parsed = engine._parse(ACTIONS[action], params)
    if getattr(parsed, "allow_sibling", False):
        raise CodedValidationError("params", "sibling_not_proposable", "allow_sibling is for the owner")
    scope = _scope(case)
    for name in type(parsed).model_fields:
        value = getattr(parsed, name)
        if name.endswith("_id") and name not in UNSCOPED_IDS and value is not None and value not in scope:
            raise CodedValidationError("params", "id_out_of_scope", f"{name} {value}")
    return parsed


def _lock_case_chain(db: Session, case_id: int) -> ReconciliationCase:
    """Statement → case, both FOR UPDATE (the statement id read without a lock, re-checked under it)."""
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
    parsed = _validate(case, payload.action, payload.params)
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


def supersede_stale(db: Session) -> int:
    """Pending proposals whose case moved on (`case_version < case.version`) → superseded, audited. Returns how many."""
    stale = (select(ReconciliationCase.id).where(ReconciliationCase.id == ReconciliationProposal.case_id,
                                                 ReconciliationProposal.case_version < ReconciliationCase.version)
             .exists())
    rows = db.execute(update(ReconciliationProposal)
                      .where(ReconciliationProposal.status == "pending", stale)
                      .values(status="superseded", decided_at=func.now())
                      .returning(ReconciliationProposal.id, ReconciliationProposal.case_id)
                      .execution_options(synchronize_session=False)).all()
    for proposal_id, case_id in sorted(rows):
        statement_id = db.execute(select(ReconciliationCase.statement_id)
                                  .where(ReconciliationCase.id == case_id)).scalar_one()
        audit.record(db, event="proposal_superseded", actor="system", statement_id=statement_id, case_id=case_id,
                     detail={"proposal_id": proposal_id, "by": "case_version"})
    if rows:
        db.expire_all()
    return len(rows)


def list_proposals(db: Session, *, status: str | None = None, case_id: int | None = None,
                   limit: int = 200) -> list[ReconciliationProposal]:
    supersede_stale(db)
    q = select(ReconciliationProposal)
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
    """Unlocked pre-check, then the engine (which locks and re-validates the proposal row). Returns (action, replayed)."""
    proposal = _get(db, proposal_id)
    if proposal.status != "pending":
        raise CodedConflictError("proposal_not_pending", f"proposal {proposal.id} is {proposal.status}")
    case = db.get(ReconciliationCase, proposal.case_id)
    if proposal.case_version != case.version:
        raise CodedConflictError("proposal_superseded", f"proposal {proposal.id} was made at version "
                                                       f"{proposal.case_version}, the case is at {case.version}")
    return engine.apply_action(db, case_id=case.id, action=proposal.action, params=proposal.params, actor="owner",
                               label=label, proposal_id=proposal.id, request_id=request_id)


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
    if case.status == "proposed" and not [p for p in _pending(db, case.id) if p.id != proposal.id]:
        case.status = "open"
    audit.record(db, event="proposal_rejected", actor=label, statement_id=case.statement_id, case_id=case.id,
                 request_id=request_id, detail={"proposal_id": proposal.id, "note": note})
    return proposal
