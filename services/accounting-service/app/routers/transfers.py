from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas.writes import TransferIn, TransferOut
from ..services import transfer_service
from .errors import service_errors

router = APIRouter(prefix="/transfers", tags=["Transfers"])


def _out(db: Session, group_id: UUID) -> dict:
    with service_errors():
        out_leg, in_leg = transfer_service.transfer_legs(db, group_id)
    return {"transfer_group_id": group_id, "out_entry_id": out_leg.id, "in_entry_id": in_leg.id}


@router.post("", response_model=TransferOut, status_code=201)
def post_transfer(payload: TransferIn, db: Session = Depends(get_db)):
    with service_errors():
        group_id = transfer_service.create_transfer(db, payload)
    db.commit()
    return _out(db, group_id)


@router.put("/{group_id}", response_model=TransferOut)
def put_transfer(group_id: UUID, payload: TransferIn, db: Session = Depends(get_db)):
    with service_errors():
        transfer_service.update_transfer(db, group_id, payload)
    db.commit()
    return _out(db, group_id)
