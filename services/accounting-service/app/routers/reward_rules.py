from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..auth import method_scope
from ..database import get_db
from ..schemas.ledger import RewardRuleOverviewOut
from ..services import reward_rule_service

router = APIRouter(prefix="/reward-rules", tags=["Reward rules"], dependencies=[Depends(method_scope())])


@router.get("", response_model=RewardRuleOverviewOut)
def reward_rules_overview(
    account_id: list[int] | None = Query(default=None),
    state: Literal["active", "disabled", "expired", "upcoming", "all"] = "all",
    include_archived_accounts: bool = False,
    db: Session = Depends(get_db),
):
    return reward_rule_service.overview(
        db, account_ids=account_id, state=state, include_archived_accounts=include_archived_accounts)
