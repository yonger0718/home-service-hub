from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Account
from ..schemas.ledger import AccountOut, EntryKind, EntryPage
from ..services import ledger_service

router = APIRouter(prefix="/accounts", tags=["Accounts"])


@router.get("", response_model=list[AccountOut])
def list_accounts(db: Session = Depends(get_db)):
    return ledger_service.list_accounts(db)


@router.get("/{account_id}/entries", response_model=EntryPage)
def list_entries(
    account_id: int,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    kind: EntryKind | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
):
    if db.get(Account, account_id) is None:
        raise HTTPException(status_code=404, detail="account not found")
    total, items = ledger_service.list_entries(
        db, account_id, limit=limit, offset=offset, kind=kind, date_from=date_from, date_to=date_to
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}
