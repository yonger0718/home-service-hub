"""The reconciliation audit trail (spec §4.10): one row per engine, proposal, policy and confirm event. Never commits;
callers write it in the transaction it describes."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import ReconciliationAudit

EVENTS = frozenset({
    "action_applied", "action_replayed", "action_reverted", "case_dismissed", "proposal_created", "proposal_applied",
    "proposal_rejected", "proposal_superseded", "policy_applied", "policy_proposed", "policy_skipped",
    "statement_confirmed", "statement_confirmed_forced", "deferral_reopened", "manual_statement_created",
    "conflict_superseded", "stand_in_retired", "anchor_applied", "stand_in_replaced",
    "statement_retired",  # also the retired-statement marker (app.services.statements.retired)
    "action_retired",  # the sweep retired an applied non-creation action a ledger delete made unrevertable
    "rule_applied", "rule_skipped", "inbox_answer",  # inbox (spec 2026-10-21 §5, §6.3)
    "auto_pay_booked", "auto_pay_skipped", "auto_pay_retired", "auto_pay_owner_deleted", "auto_pay_owner_edited",
    "auto_pay_header_changed", "auto_pay_statement_retired", "auto_pay_calendar_stale",  # card statement auto-pay
    "settlement_booked", "settlement_skipped", "settlement_retired", "settlement_replaced",
    "settlement_owner_deleted", "settlement_owner_edited", "settlement_claimed",  # brokerage settlement stand-ins
    "dividend_retired",  # a reverted book_dividend (portfolio integration dividends R4)
})


def record(db: Session, *, event: str, actor: str, statement_id: int | None = None, case_id: int | None = None,
           action_id: int | None = None, run_id: int | None = None, request_id: str | None = None,
           detail: dict | None = None) -> ReconciliationAudit:
    """Insert (and flush) one audit row; `actor` is who acted (the token label or "policy"), `detail` JSON-ready."""
    if event not in EVENTS:
        raise ValueError(f"unknown audit event {event!r}")
    row = ReconciliationAudit(event=event, actor=actor[:64], statement_id=statement_id, case_id=case_id,
                              action_id=action_id, run_id=run_id, request_id=request_id, detail=detail or {})
    db.add(row)
    db.flush()
    return row
