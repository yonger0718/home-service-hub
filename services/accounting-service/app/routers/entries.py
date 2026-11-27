from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from ..auth import holds_admin, method_scope, require_admin_to_confirm
from ..database import get_db
from ..schemas.ledger import DailySummaryOut, EntryDeleteOut, EntryDetailOut, EntryKind, EntryPage, MonthSummaryOut
from ..schemas.writes import (
    EntryIn, EntryUpdateIn, EntryWriteOut, RefundIn, RewardRepriceIn, RewardRepriceOut, RewardRulesIn, SettleIn, SplitIn,
    SplitOut,
)
from ..services import einvoice_matcher, entry_write_service, ledger_service, reward_reprice, settlement_service, split_service
from .errors import service_errors

router = APIRouter(prefix="/entries", tags=["Entries"], dependencies=[Depends(method_scope())])


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
    counterparty_id: int | None = None,
    open: bool = Query(default=False, description="Only unsettled receivables/payables (settlements excluded)"),
    tag: list[str] | None = Query(default=None, description="Rows carrying ANY of these tags"),
    category_id: list[int] | None = Query(default=None, description="Rows in any of these categories or their sub-categories"),
    project_id: int | None = None,
    no_project: bool = Query(default=False, description="Only rows without a project (#261); exclusive with project_id"),
    merchant: str | None = Query(default=None, max_length=100, description="Case-insensitive substring of the merchant"),
    amount_min: Decimal | None = Query(default=None, description="Signed lower bound (inclusive) on the entry's amount in its ACCOUNT's currency; no conversion"),
    amount_max: Decimal | None = Query(default=None, description="Signed upper bound (inclusive) on the entry's amount in its ACCOUNT's currency; no conversion"),
    include_invoice_items: bool = Query(default=False, description="Also match `q` against the item names of the invoice linked to the entry or its group"),
    db: Session = Depends(get_db),
):
    if amount_min is not None and amount_max is not None and amount_min > amount_max:
        raise HTTPException(status_code=422, detail="amount_min must not exceed amount_max")
    if no_project and project_id is not None:
        raise HTTPException(status_code=422, detail="no_project and project_id are mutually exclusive")
    total, items = ledger_service.list_all_entries(
        db, limit=limit, offset=offset, kind=kind, date_from=date_from, date_to=date_to, q=q,
        account_ids=account_id or None, hide_rewards=hide_rewards,
        counterparty_id=counterparty_id, open_only=open,
        tags=tag or None, category_ids=category_id or None, project_id=project_id, no_project=no_project, merchant=merchant,
        amount_min=amount_min, amount_max=amount_max, include_invoice_items=include_invoice_items,
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
        if payload.invoice_number:
            einvoice_matcher.link_saved_entry(db, entry_id, payload.invoice_number)
    db.commit()
    return _written(db, entry_id)


@router.put("/{entry_id}", response_model=EntryWriteOut)
def put_entry(entry_id: int, payload: EntryUpdateIn, request: Request, drop_children: bool = False,
              db: Session = Depends(get_db)):
    """A stored fee / discount child the payload does not carry is dropped only with `?drop_children=true`
    (409 `children_unconfirmed`, #396); the parameter needs the admin scope."""
    require_admin_to_confirm(request, drop_children)
    with service_errors():
        entry_write_service.update_entry(db, entry_id, payload, drop_children=drop_children)
    db.commit()
    return _written(db, entry_id)


@router.delete("/{entry_id}", response_model=EntryDeleteOut)
def remove_entry(entry_id: int, request: Request, cascade: bool = False, db: Session = Depends(get_db)):
    """Delete the entry with its fee / discount children and, for a transfer, the other leg; the answer lists every
    row removed. A child on another account than its parent needs `?cascade=true` (409 `cascade_unconfirmed`, #390);
    the parameter needs the admin scope (#396)."""
    require_admin_to_confirm(request, cascade)
    with service_errors():
        deleted = entry_write_service.delete_entry(db, entry_id, cascade=cascade)
    db.commit()
    return EntryDeleteOut(deleted=[vars(row) for row in deleted])


@router.put("/{entry_id}/reward-rules", response_model=EntryDetailOut)
def put_entry_reward_rules(entry_id: int, payload: RewardRulesIn, db: Session = Depends(get_db)):
    """Replace only the entry's reward rule links: the row, its source and its fee / discount children (and their
    statement coverage) stay untouched (issue #293)."""
    with service_errors():
        entry_write_service.update_reward_rules(db, entry_id, payload.reward_rule_ids)
    db.commit()
    return ledger_service.get_entry_detail(db, entry_id)


def _reprice_out(plan: reward_reprice.Plan) -> RewardRepriceOut:
    return RewardRepriceOut(entry_id=plan.entry_id, flagged=plan.flagged, applied=plan.applied,
                            items=[vars(item) for item in plan.items])


@router.get("/{entry_id}/reward-reprice", response_model=RewardRepriceOut)
def get_reward_reprice(entry_id: int, db: Session = Depends(get_db)):
    """The purchase's posted rewards: booked amount vs. what they would be on its current figures (issue #306)."""
    with service_errors():
        return _reprice_out(reward_reprice.preview(db, entry_id))


@router.post("/{entry_id}/reward-reprice", response_model=RewardRepriceOut)
def post_reward_reprice(entry_id: int, payload: RewardRepriceIn, db: Session = Depends(get_db)):
    """Reprice the purchase's posted rewards on its current figures, or keep them (`keep`); clears `reward_posted`."""
    with service_errors():
        plan = reward_reprice.apply(db, entry_id, keep=payload.keep)
    db.commit()
    return _reprice_out(plan)


@router.put("/{entry_id}/split", response_model=SplitOut)
def put_entry_split(entry_id: int, payload: SplitIn, request: Request, drop_children: bool = False,
                    db: Session = Depends(get_db)):
    """Convert a single entry into a split; the entry keeps its id (split rework §1.6). `drop_children`: as on
    PUT /entries/{entry_id} (#396)."""
    require_admin_to_confirm(request, drop_children)
    with service_errors():
        result = split_service.convert_to_split(db, entry_id, payload, drop_children=drop_children)
    db.commit()
    return result.out()


@router.post("/{entry_id}/settle", response_model=EntryDetailOut, status_code=201)
def post_settlement(entry_id: int, payload: SettleIn, db: Session = Depends(get_db)):
    with service_errors():
        new_id = settlement_service.settle(db, entry_id, payload)
    db.commit()
    return ledger_service.get_entry_detail(db, new_id)


def _set_closed(request: Request, db: Session, entry_id: int, closed: bool) -> dict:
    """The change is recorded with the caller's label; a close by a caller without the owner's scope waits for the
    owner's confirmation when it leaves a remainder (#401)."""
    with service_errors():
        settlement_service.set_closed(
            db, entry_id, closed, actor=getattr(request.state, "client_label", None), by_owner=holds_admin(request))
    db.commit()
    return ledger_service.get_entry_detail(db, entry_id)


@router.post("/{entry_id}/reopen", response_model=EntryDetailOut)
def reopen_debt(entry_id: int, request: Request, db: Session = Depends(get_db)):
    """Clear `is_closed` on a receivable / payable original: its remainder follows from its settlements again (#391)."""
    return _set_closed(request, db, entry_id, False)


@router.post("/{entry_id}/close", response_model=EntryDetailOut)
def close_debt(entry_id: int, request: Request, db: Session = Depends(get_db)):
    """Set `is_closed` on a receivable / payable original: no remainder, whatever its settlements sum to (#391)."""
    return _set_closed(request, db, entry_id, True)


@router.post("/{entry_id}/refund", response_model=EntryDetailOut, status_code=201)
def post_refund(entry_id: int, payload: RefundIn, db: Session = Depends(get_db)):
    with service_errors():
        new_id = settlement_service.refund(db, entry_id, payload)
    db.commit()
    return ledger_service.get_entry_detail(db, new_id)
