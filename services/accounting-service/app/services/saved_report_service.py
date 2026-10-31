"""Saved reports (design D4): named, versioned report configs. Ids in the saved filters are checked on write; on read
the ones that no longer exist are dropped from the returned copy and listed as stale (the stored JSON is untouched)."""

import copy

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Account, Category, Counterparty, Project, SavedReport
from ..schemas.reports import SavedReportIn
from .errors import ConflictError, NotFoundError, ValidationError

# filter key -> table whose ids it holds; list-valued unless in _SCALAR
_TABLES = {"account_id": Account, "category_id": Category, "project_id": Project, "counterparty_id": Counterparty}
_SCALAR = {"counterparty_id"}


def _ids(filters: dict, key: str) -> list[int]:
    value = filters.get(key)
    if value is None:
        return []
    return [value] if key in _SCALAR else [*value]


def _existing(db: Session, key: str, ids: list[int]) -> set[int]:
    table = _TABLES[key]
    return set(db.scalars(select(table.id).where(table.id.in_(ids)))) if ids else set()


def validate_refs(db: Session, config: dict) -> None:
    filters = config.get("filters", {})
    for key in _TABLES:
        ids = _ids(filters, key)
        missing = sorted(set(ids) - _existing(db, key, ids))
        if missing:
            raise ValidationError(f"config.filters.{key}", f"unknown {key} {missing[0]}")


def resolve_stale(db: Session, config: dict) -> tuple[dict, list[str]]:
    """A copy of `config` without the filter ids that no longer exist, and `"<filter>:<id>"` for each (sorted)."""
    resolved = copy.deepcopy(config)
    filters = resolved.get("filters", {})
    stale: list[str] = []
    for key in sorted(_TABLES):
        ids = _ids(filters, key)
        gone = set(ids) - _existing(db, key, ids)
        if not gone:
            continue
        stale += [f"{key}:{i}" for i in sorted(gone)]
        if key in _SCALAR:
            filters[key] = None
        else:
            filters[key] = [i for i in ids if i not in gone]
    return resolved, stale


def _out(db: Session, row: SavedReport) -> dict:
    config, stale = resolve_stale(db, row.config)
    return {"id": row.id, "name": row.name, "config": config, "chart": row.chart, "pinned": row.pinned,
            "sort_order": row.sort_order, "stale_refs": stale, "created_at": row.created_at,
            "updated_at": row.updated_at}


def _row(db: Session, report_id: int) -> SavedReport:
    row = db.get(SavedReport, report_id)
    if row is None:
        raise NotFoundError("saved report not found")
    return row


def _check_name(db: Session, name: str, own_id: int | None) -> None:
    query = select(SavedReport.id).where(SavedReport.name == name)
    if own_id is not None:
        query = query.where(SavedReport.id != own_id)
    if db.scalar(query) is not None:
        raise ConflictError("a saved report with this name already exists")


def list_all(db: Session) -> list[dict]:
    rows = db.scalars(select(SavedReport).order_by(SavedReport.sort_order, SavedReport.id))
    return [_out(db, row) for row in rows]


list = list_all  # the service's public name; defined after every use of the builtin above


def get(db: Session, report_id: int) -> dict:
    return _out(db, _row(db, report_id))


def create(db: Session, payload: SavedReportIn) -> int:
    config = payload.config.stored()
    validate_refs(db, config)
    _check_name(db, payload.name, None)
    sort_order = payload.sort_order
    if sort_order is None:
        top = db.scalar(select(func.max(SavedReport.sort_order)))
        sort_order = 0 if top is None else top + 1
    row = SavedReport(name=payload.name, config=config, chart=payload.chart, pinned=payload.pinned,
                      sort_order=sort_order)
    db.add(row)
    db.flush()
    return row.id


def update(db: Session, report_id: int, payload: SavedReportIn) -> None:
    row = _row(db, report_id)
    config = payload.config.stored()
    validate_refs(db, config)
    _check_name(db, payload.name, report_id)
    row.name, row.config, row.chart, row.pinned = payload.name, config, payload.chart, payload.pinned
    if payload.sort_order is not None:
        row.sort_order = payload.sort_order
    db.flush()


def delete(db: Session, report_id: int) -> None:
    db.delete(_row(db, report_id))
    db.flush()
