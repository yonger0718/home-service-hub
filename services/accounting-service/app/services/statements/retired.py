"""A retired statement (fix 2026-10-17): one the owner retired as a duplicate (`statement_retire_service`). Its
`statement_retired` audit row is the marker — written once, never removed, so a retire is final without a schema
change. No import beyond the models: coverage_service and the revision service use it."""
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.models import ReconciliationAudit

EVENT = "statement_retired"


def clause(statement_id):
    """EXISTS: the statement (an id column or value) is retired."""
    return exists().where(ReconciliationAudit.statement_id == statement_id, ReconciliationAudit.event == EVENT)


def is_retired(db: Session, statement_id: int) -> bool:
    return db.execute(select(clause(statement_id))).scalar_one()
