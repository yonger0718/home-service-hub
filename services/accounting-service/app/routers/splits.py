from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas.writes import SplitIn, SplitOut
from ..services import split_service
from .errors import service_errors

router = APIRouter(prefix="/splits", tags=["Splits"])


@router.post("", response_model=SplitOut, status_code=201)
def post_split(payload: SplitIn, db: Session = Depends(get_db)):
    with service_errors():
        result = split_service.create_split_result(db, payload)
    db.commit()
    return result.out()


@router.put("/{group_id}", response_model=SplitOut)
def put_split(group_id: int, payload: SplitIn, db: Session = Depends(get_db)):
    with service_errors():
        result = split_service.update_split(db, group_id, payload)
    db.commit()
    return result.out()


@router.delete("/{group_id}", status_code=204)
def remove_split(group_id: int, db: Session = Depends(get_db)):
    with service_errors():
        split_service.delete_split(db, group_id)
    db.commit()
    return Response(status_code=204)
