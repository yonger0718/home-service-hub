from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Account
from ..schemas.ledger import AccountDetailOut, AccountOut, AccountPeriodSummaryOut, EntryKind, EntryPage, RewardRuleOut
from ..services import ledger_service

router = APIRouter(prefix="/accounts", tags=["Accounts"])


def _require_account(db: Session, account_id: int) -> None:
    if db.get(Account, account_id) is None:
        raise HTTPException(status_code=404, detail="account not found")


@router.get("", response_model=list[AccountOut])
def list_accounts(include_archived: bool = False, as_of: date | None = None, db: Session = Depends(get_db)):
    """`as_of` (YYYY-MM-DD, default today): balances count entries posted on or before it."""
    return ledger_service.list_accounts(db, include_archived=include_archived, as_of=as_of)


@router.get("/{account_id}", response_model=AccountDetailOut)
def get_account(account_id: int, as_of: date | None = None, db: Session = Depends(get_db)):
    account = ledger_service.get_account(db, account_id, as_of=as_of)
    if account is None:
        raise HTTPException(status_code=404, detail="account not found")
    return account


@router.get("/{account_id}/reward-rules", response_model=list[RewardRuleOut])
def list_reward_rules(account_id: int, db: Session = Depends(get_db)):
    _require_account(db, account_id)
    return ledger_service.list_reward_rules(db, account_id)


@router.get("/{account_id}/summary", response_model=AccountPeriodSummaryOut)
def account_summary(account_id: int, date_from: date, date_to: date, db: Session = Depends(get_db)):
    """Period totals for the passbook header; independent of the entries page the client has loaded."""
    _require_account(db, account_id)
    if date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must not be after date_to")
    return ledger_service.period_summary(db, account_id, date_from, date_to)


@router.get("/{account_id}/entries", response_model=EntryPage)
def list_entries(
    account_id: int,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    kind: EntryKind | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    q: str | None = Query(default=None, max_length=100),
    db: Session = Depends(get_db),
):
    _require_account(db, account_id)
    total, items = ledger_service.list_entries(
        db, account_id, limit=limit, offset=offset, kind=kind, date_from=date_from, date_to=date_to, q=q
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}
