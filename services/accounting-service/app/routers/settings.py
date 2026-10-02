from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas.ledger import (
    AccountGroupOut,
    CategoryOut,
    CounterpartyOut,
    EntryKind,
    FxRateOut,
    PreferenceOut,
    ProjectOut,
)
from ..schemas.writes import AccountGroupIn, CategoryIn, CounterpartyIn, OrderIn, PreferenceIn, ProjectIn
from ..services import fx_rate_service, settings_service
from .errors import service_errors

router = APIRouter(tags=["Settings"])

CURRENCY_PATTERN = r"^[A-Za-z0-9]{3,8}$"


@router.get("/preference", response_model=PreferenceOut)
def get_preference(db: Session = Depends(get_db)):
    preference = settings_service.get_preference(db)
    db.commit()
    return preference


@router.put("/preference", response_model=PreferenceOut)
def update_preference(payload: PreferenceIn, db: Session = Depends(get_db)):
    preference = settings_service.update_preference(db, payload)
    db.commit()
    return preference


@router.get("/fx-rate", response_model=FxRateOut)
def get_fx_rate(
    day: date = Query(alias="date"),
    base: str = Query(pattern=CURRENCY_PATTERN),
    quote: str = Query(pattern=CURRENCY_PATTERN),
    db: Session = Depends(get_db),
):
    base, quote = base.upper(), quote.upper()
    try:
        found = fx_rate_service.get_rate(db, day, base, quote)
    except fx_rate_service.FxRateUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "date": day, "base": base, "quote": quote, "rate": found.rate, "source": found.source,
        "rate_date": found.rate_date,
    }


# Task 16 adds /account-groups, /categories, /projects and /counterparties here.


def _find(rows: list[dict], row_id: int) -> dict:
    return next(row for row in rows if row["id"] == row_id)


# --- account groups -------------------------------------------------------------


@router.get("/account-groups", response_model=list[AccountGroupOut])
def get_account_groups(db: Session = Depends(get_db)):
    return settings_service.list_groups(db)


@router.post("/account-groups", response_model=AccountGroupOut, status_code=201)
def post_account_group(payload: AccountGroupIn, db: Session = Depends(get_db)):
    with service_errors():
        group_id = settings_service.create_group(db, payload)
    db.commit()
    return _find(settings_service.list_groups(db), group_id)


@router.put("/account-groups/order", response_model=list[AccountGroupOut])
def put_account_group_order(payload: OrderIn, db: Session = Depends(get_db)):
    with service_errors():
        settings_service.reorder_groups(db, payload.ids)
    db.commit()
    return settings_service.list_groups(db)


@router.put("/account-groups/{group_id}", response_model=AccountGroupOut)
def put_account_group(group_id: int, payload: AccountGroupIn, db: Session = Depends(get_db)):
    with service_errors():
        settings_service.update_group(db, group_id, payload)
    db.commit()
    return _find(settings_service.list_groups(db), group_id)


@router.delete("/account-groups/{group_id}", status_code=204)
def remove_account_group(group_id: int, db: Session = Depends(get_db)):
    with service_errors():
        settings_service.delete_group(db, group_id)
    db.commit()
    return Response(status_code=204)


# --- categories -----------------------------------------------------------------


@router.get("/categories", response_model=list[CategoryOut])
def get_categories(kind: EntryKind = Query(...), db: Session = Depends(get_db)):
    return settings_service.list_categories(db, kind)


@router.post("/categories", response_model=CategoryOut, status_code=201)
def post_category(payload: CategoryIn, db: Session = Depends(get_db)):
    with service_errors():
        category_id = settings_service.create_category(db, payload)
    db.commit()
    return settings_service.get_category(db, category_id)


@router.put("/categories/order", response_model=list[CategoryOut])
def put_category_order(payload: OrderIn, db: Session = Depends(get_db)):
    with service_errors():
        settings_service.reorder_categories(db, payload.ids)
    db.commit()
    return [settings_service.get_category(db, category_id) for category_id in payload.ids]


@router.put("/categories/{category_id}", response_model=CategoryOut)
def put_category(category_id: int, payload: CategoryIn, db: Session = Depends(get_db)):
    with service_errors():
        settings_service.update_category(db, category_id, payload)
    db.commit()
    return settings_service.get_category(db, category_id)


@router.delete("/categories/{category_id}", status_code=204)
def remove_category(category_id: int, db: Session = Depends(get_db)):
    with service_errors():
        settings_service.delete_category(db, category_id)
    db.commit()
    return Response(status_code=204)


# --- projects -------------------------------------------------------------------


@router.get("/projects", response_model=list[ProjectOut])
def get_projects(db: Session = Depends(get_db)):
    return settings_service.list_projects(db)


@router.post("/projects", response_model=ProjectOut, status_code=201)
def post_project(payload: ProjectIn, db: Session = Depends(get_db)):
    with service_errors():
        project_id = settings_service.create_project(db, payload)
    db.commit()
    return _find(settings_service.list_projects(db), project_id)


@router.put("/projects/{project_id}", response_model=ProjectOut)
def put_project(project_id: int, payload: ProjectIn, db: Session = Depends(get_db)):
    with service_errors():
        settings_service.update_project(db, project_id, payload)
    db.commit()
    return _find(settings_service.list_projects(db), project_id)


@router.delete("/projects/{project_id}", status_code=204)
def remove_project(project_id: int, db: Session = Depends(get_db)):
    with service_errors():
        settings_service.delete_project(db, project_id)
    db.commit()
    return Response(status_code=204)


# --- counterparties -------------------------------------------------------------


@router.get("/counterparties", response_model=list[CounterpartyOut])
def get_counterparties(db: Session = Depends(get_db)):
    return settings_service.list_counterparties(db)


@router.post("/counterparties", response_model=CounterpartyOut, status_code=201)
def post_counterparty(payload: CounterpartyIn, db: Session = Depends(get_db)):
    with service_errors():
        counterparty_id = settings_service.create_counterparty(db, payload)
    db.commit()
    return _find(settings_service.list_counterparties(db), counterparty_id)


@router.put("/counterparties/{counterparty_id}", response_model=CounterpartyOut)
def put_counterparty(counterparty_id: int, payload: CounterpartyIn, db: Session = Depends(get_db)):
    with service_errors():
        settings_service.update_counterparty(db, counterparty_id, payload)
    db.commit()
    return _find(settings_service.list_counterparties(db), counterparty_id)


@router.delete("/counterparties/{counterparty_id}", status_code=204)
def remove_counterparty(counterparty_id: int, db: Session = Depends(get_db)):
    with service_errors():
        settings_service.delete_counterparty(db, counterparty_id)
    db.commit()
    return Response(status_code=204)
