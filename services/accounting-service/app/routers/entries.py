from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas.ledger import EntryDetailOut, EntryKind, DailySummaryOut, EntryPage, MonthSummaryOut
from ..schemas.writes import EntryIn, EntryUpdateIn, EntryWriteOut, RefundIn, SettleIn
from ..services import entry_write_service, ledger_service, settlement_service
from .errors import service_errors

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


MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"


# Declared before /{entry_id} so "summary" is never parsed as an entry id.
@router.get("/summary", response_model=MonthSummaryOut)
def month_summary(month: str = Query(pattern=MONTH_PATTERN), db: Session = Depends(get_db)):
    return ledger_service.month_summary(db, month)


@router.get("/summary/daily", response_model=DailySummaryOut)
def daily_summary(month: str = Query(pattern=MONTH_PATTERN), db: Session = Depends(get_db)):
    return ledger_service.daily_summary(db, month)


@router.get("/{entry_id}", response_model=EntryDetailOut)
def get_entry(entry_id: int, db: Session = Depends(get_db)):
    detail = ledger_service.get_entry_detail(db, entry_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="entry not found")
    return detail


def _written(db: Session, entry_id: int) -> dict:
    detail = ledger_service.get_entry_detail(db, entry_id)
    return {**detail, "proposed_fee": entry_write_service.proposed_fee_for_entry(db, entry_id)}


@router.post("", response_model=EntryWriteOut, status_code=201)
def post_entry(payload: EntryIn, db: Session = Depends(get_db)):
    with service_errors():
        entry_id = entry_write_service.create_entry(db, payload)
    db.commit()
    return _written(db, entry_id)


@router.put("/{entry_id}", response_model=EntryWriteOut)
def put_entry(entry_id: int, payload: EntryUpdateIn, db: Session = Depends(get_db)):
    with service_errors():
        entry_write_service.update_entry(db, entry_id, payload)
    db.commit()
    return _written(db, entry_id)


@router.delete("/{entry_id}", status_code=204)
def remove_entry(entry_id: int, db: Session = Depends(get_db)):
    with service_errors():
        entry_write_service.delete_entry(db, entry_id)
    db.commit()
    return Response(status_code=204)


@router.post("/{entry_id}/settle", response_model=EntryDetailOut, status_code=201)
def post_settlement(entry_id: int, payload: SettleIn, db: Session = Depends(get_db)):
    with service_errors():
        new_id = settlement_service.settle(db, entry_id, payload)
    db.commit()
    return ledger_service.get_entry_detail(db, new_id)


@router.post("/{entry_id}/refund", response_model=EntryDetailOut, status_code=201)
def post_refund(entry_id: int, payload: RefundIn, db: Session = Depends(get_db)):
    with service_errors():
        new_id = settlement_service.refund(db, entry_id, payload)
    db.commit()
    return ledger_service.get_entry_detail(db, new_id)
