"""/reward-accruals endpoints (reward engine spec 6): post a `manual` rule's accrual through the daily job's
per-accrual step. In-service paths: Caddy and the dev proxy strip /api/accounting."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..auth import method_scope
from ..database import get_db
from ..schemas.writes import RewardAccrualPostIn, RewardAccrualPostOut
from ..services import reward_posting
from .errors import service_errors

router = APIRouter(prefix="/reward-accruals", tags=["Reward accruals"], dependencies=[Depends(method_scope())])


@router.post("/{accrual_id}/post", response_model=RewardAccrualPostOut)
def post_accrual(accrual_id: int, payload: RewardAccrualPostIn | None = None, db: Session = Depends(get_db)):
    """404 missing; 409 `accrual_not_estimated` (already posted, confirmed or forfeited) or `rule_not_manual`."""
    with service_errors():
        out = reward_posting.post_manual(db, accrual_id, payload.on if payload is not None else None)
    db.commit()
    return out
