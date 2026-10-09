from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..auth import method_scope
from ..database import get_db
from ..schemas.ledger import EntryDetailOut
from ..schemas.writes import BalanceAdjustmentIn
from ..services import entry_write_service, ledger_service
from .errors import service_errors

router = APIRouter(prefix="/balance-adjustments", tags=["Entries"], dependencies=[Depends(method_scope())])


@router.post("", response_model=EntryDetailOut, status_code=201)
def post_balance_adjustment(payload: BalanceAdjustmentIn, db: Session = Depends(get_db)):
    with service_errors():
        entry_id = entry_write_service.create_balance_adjustment(db, payload)
    db.commit()
    return ledger_service.get_entry_detail(db, entry_id)
