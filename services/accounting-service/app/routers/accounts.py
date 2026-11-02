from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..auth import method_scope, require
from ..database import get_db
from ..models import Account, RewardRule
from ..schemas.ledger import (
    AccountDetailOut,
    AccountOut,
    AccountPeriodSummaryOut,
    DebtsSummaryOut,
    EntryKind,
    EntryPage,
    RewardRuleOut,
)
from ..schemas.writes import SOURCE_CURRENCIES, AccountIn, AccountSourcesPatch, OrderIn, RewardRuleIn
from ..services import account_logo_service, ledger_service, reward_rule_service, settings_service
from ..services.account_logo_service import MAX_LOGO_UPLOAD_BYTES, LogoError, logo_hash
from ..services.errors import CodedValidationError
from .errors import service_errors

router = APIRouter(prefix="/accounts", tags=["Accounts"], dependencies=[Depends(method_scope())])


def _require_account(db: Session, account_id: int) -> None:
    if db.get(Account, account_id) is None:
        raise HTTPException(status_code=404, detail="account not found")


@router.get("", response_model=list[AccountOut])
def list_accounts(include_archived: bool = False, as_of: date | None = None, db: Session = Depends(get_db)):
    """`as_of` (YYYY-MM-DD, default today): balances count entries posted on or before it."""
    return ledger_service.list_accounts(db, include_archived=include_archived, as_of=as_of)


@router.get("/debts", response_model=DebtsSummaryOut)
def debts_summary(db: Session = Depends(get_db)):
    """Open payables/receivables summed in the main currency; unrated currencies are listed as `unconverted`."""
    return ledger_service.debts_summary(db)


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


@router.post("/{account_id}/reward-rules", response_model=RewardRuleOut, status_code=201)
def create_reward_rule(account_id: int, payload: RewardRuleIn, db: Session = Depends(get_db)):
    with service_errors():
        rule_id = reward_rule_service.create(db, account_id, payload)
    db.commit()
    return ledger_service.rule_out(db.get(RewardRule, rule_id))


# Declared above the `{rule_id}` routes, or `order` binds to {rule_id}.
@router.put("/{account_id}/reward-rules/order", response_model=list[RewardRuleOut])
def reorder_reward_rules(account_id: int, payload: OrderIn, db: Session = Depends(get_db)):
    with service_errors():
        reward_rule_service.reorder(db, account_id, payload.ids)
    db.commit()
    return ledger_service.list_reward_rules(db, account_id)


@router.put("/{account_id}/reward-rules/{rule_id}", response_model=RewardRuleOut)
def update_reward_rule(account_id: int, rule_id: int, payload: RewardRuleIn, db: Session = Depends(get_db)):
    with service_errors():
        reward_rule_service.update(db, account_id, rule_id, payload)
    db.commit()
    return ledger_service.rule_out(db.get(RewardRule, rule_id))


@router.delete("/{account_id}/reward-rules/{rule_id}", status_code=204)
def delete_reward_rule(account_id: int, rule_id: int, db: Session = Depends(get_db)):
    with service_errors():
        reward_rule_service.delete(db, account_id, rule_id)
    db.commit()
    return Response(status_code=204)


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


@router.post("", response_model=AccountDetailOut, status_code=201)
def post_account(payload: AccountIn, db: Session = Depends(get_db)):
    with service_errors():
        account_id = settings_service.create_account(db, payload)
    db.commit()
    return ledger_service.get_account(db, account_id)


@router.put("/{account_id}", response_model=AccountDetailOut)
def put_account(account_id: int, payload: AccountIn, db: Session = Depends(get_db)):
    """Settings writes are allowed on imported accounts at any time (D19); they set settings_locally_edited."""
    with service_errors():
        settings_service.update_account(db, account_id, payload)
    db.commit()
    return ledger_service.get_account(db, account_id)


@router.patch("/{account_id}", response_model=AccountDetailOut, dependencies=[Depends(require("admin"))])
def patch_account_sources(account_id: int, payload: AccountSourcesPatch, db: Session = Depends(get_db)):
    """Admin-only: set the portfolio feed sources of an account (settlement / valuation); absent fields stay. A
    `settlement_source` is refused 422 `currency_mismatch` on an account outside the source's currency
    (`SOURCE_CURRENCIES`; PR #234 review finding 1)."""
    account = db.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="account not found")
    source = payload.settlement_source
    if "settlement_source" in payload.model_fields_set and source is not None \
            and account.currency != SOURCE_CURRENCIES[source]:
        with service_errors():
            raise CodedValidationError("settlement_source", "currency_mismatch",
                                       f"{source} answers in {SOURCE_CURRENCIES[source]} only")
    for field in payload.model_fields_set:
        setattr(account, field, getattr(payload, field))
    db.commit()
    return ledger_service.get_account(db, account_id)


@router.delete("/{account_id}", status_code=204)
def remove_account(account_id: int, db: Session = Depends(get_db)):
    with service_errors():
        settings_service.delete_account(db, account_id)
    db.commit()
    return Response(status_code=204)


async def _read_body_capped(request: Request, cap: int) -> bytes:
    """Read the request body, refusing the moment it exceeds `cap` (declared or streamed)."""
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            too_big = int(declared) > cap
        except ValueError:
            too_big = False
        if too_big:
            raise LogoError("logo_too_large")
    buf = bytearray()
    async for chunk in request.stream():
        buf.extend(chunk)
        if len(buf) > cap:
            raise LogoError("logo_too_large")
    return bytes(buf)


def _store_logo(db: Session, account_id: int, data: bytes, declared_type: str | None):
    """Sync worker run in the threadpool: decode/normalise, store, commit and read back on one thread."""
    try:
        logo = account_logo_service.normalise_logo(data, declared_type=declared_type)
    except LogoError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.code) from exc
    with service_errors():
        account_logo_service.put_logo(db, account_id, logo)
    db.commit()
    return ledger_service.get_account(db, account_id)


@router.put("/{account_id}/logo", response_model=AccountDetailOut)
async def put_account_logo(account_id: int, request: Request, db: Session = Depends(get_db)):
    try:
        data = await _read_body_capped(request, MAX_LOGO_UPLOAD_BYTES)
    except LogoError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.code) from exc
    return await run_in_threadpool(_store_logo, db, account_id, data, request.headers.get("content-type"))


@router.get("/{account_id}/logo.png")
def get_account_logo(account_id: int, request: Request, v: str | None = Query(default=None),
                     db: Session = Depends(get_db)):
    _require_account(db, account_id)
    row = account_logo_service.get_logo(db, account_id)
    if row is None:
        raise HTTPException(status_code=404, detail="logo not found")
    etag = f'"{row.sha256}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    cache = "private, max-age=31536000, immutable" if v == logo_hash(row.sha256) else "no-store"
    return Response(
        content=bytes(row.png),
        media_type="image/png",
        headers={
            "ETag": etag,
            "Cache-Control": cache,
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": 'inline; filename="logo.png"',
        },
    )


@router.delete("/{account_id}/logo", status_code=204)
def delete_account_logo(account_id: int, db: Session = Depends(get_db)):
    with service_errors():
        account_logo_service.delete_logo(db, account_id)
    db.commit()
    return Response(status_code=204)


@router.post("/{account_id}/reset-settings-flag", response_model=AccountDetailOut)
def post_reset_settings_flag(account_id: int, db: Session = Depends(get_db)):
    with service_errors():
        settings_service.reset_settings_flag(db, account_id)
    db.commit()
    return ledger_service.get_account(db, account_id)
