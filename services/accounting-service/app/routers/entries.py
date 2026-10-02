from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas.ledger import EntryDetailOut, EntryKind, EntryPage, MonthSummaryOut
from ..services import ledger_service

router = APIRouter(prefix="/entries", tags=["Entries"])


@router.get("", response_model=EntryPage)
def list_entries(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    kind: EntryKind | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    q: str | None = Query(default=None, max_length=100),
    account_id: list[int] | None = Query(default=None),
    hide_rewards: bool = False,
    db: Session = Depends(get_db),
):
    total, items = ledger_service.list_all_entries(
        db, limit=limit, offset=offset, kind=kind, date_from=date_from, date_to=date_to, q=q,
        account_ids=account_id or None, hide_rewards=hide_rewards,
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


# Declared before /{entry_id} so "summary" is never parsed as an entry id.
@router.get("/summary", response_model=MonthSummaryOut)
def month_summary(month: str = Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$"), db: Session = Depends(get_db)):
    return ledger_service.month_summary(db, month)


@router.get("/{entry_id}", response_model=EntryDetailOut)
def get_entry(entry_id: int, db: Session = Depends(get_db)):
    detail = ledger_service.get_entry_detail(db, entry_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="entry not found")
    return detail


# write endpoints are added in Task 12
