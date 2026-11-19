from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..auth import method_scope, require_admin_to_confirm
from ..database import get_db
from ..schemas.writes import TransferIn, TransferOut
from ..services import transfer_service
from .errors import service_errors

router = APIRouter(prefix="/transfers", tags=["Transfers"], dependencies=[Depends(method_scope())])


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
def put_transfer(group_id: UUID, payload: TransferIn, request: Request, drop_children: bool = False,
                 db: Session = Depends(get_db)):
    """A leg's stored fee / discount child the payload does not carry is dropped only with `?drop_children=true`
    (409 `children_unconfirmed`, #396); the parameter needs the admin scope."""
    require_admin_to_confirm(request, drop_children)
    with service_errors():
        transfer_service.update_transfer(db, group_id, payload, drop_children=drop_children)
    db.commit()
    return _out(db, group_id)
