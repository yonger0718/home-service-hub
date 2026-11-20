"""Debt close / reopen events (#401): the list that waits for the owner, and the owner's confirmation."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..auth import require
from ..database import get_db
from ..schemas.ledger import DebtReviewPage
from ..services import debt_events
from .errors import service_errors

router = APIRouter(prefix="/debt-events", tags=["Debt events"])


@router.get("/to-review", response_model=DebtReviewPage, dependencies=[Depends(require("read"))])
def to_review(limit: int = Query(default=50, ge=1, le=200), db: Session = Depends(get_db)):
    """Debts whose latest event is a close with a remainder that the owner has not confirmed (newest first)."""
    return debt_events.to_review(db, limit)


@router.post("/{event_id}/review", status_code=204, dependencies=[Depends(require("admin"))])
def review(event_id: int, db: Session = Depends(get_db)):
    """The owner's confirmation of a close: `admin` (or `legacy`) only, so a caller holding only `write` cannot take
    its own close off the list. Not a ledger write."""
    with service_errors():
        debt_events.review(db, event_id)
    db.commit()
