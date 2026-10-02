"""Settings reads and writes. Task 5: preference; Task 16 adds accounts, groups, categories, projects, counterparties."""

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..models import Preference
from ..schemas.writes import PreferenceIn

PREFERENCE_FIELDS = (
    "expense_income_colors", "keypad_layout", "week_start", "main_currency", "hide_rewards_on_timeline",
    "abbreviate_totals",
)


class ConflictError(Exception):
    """The write conflicts with existing data; routers map it to HTTP 409."""


def _preference_row(db: Session) -> Preference:
    """The single preference row; inserted with the column defaults (D27) when absent. Not committed."""
    db.execute(pg_insert(Preference).values(id=1).on_conflict_do_nothing(index_elements=["id"]))
    return db.get(Preference, 1, populate_existing=True)


def get_preference(db: Session) -> dict:
    preference = _preference_row(db)
    return {field: getattr(preference, field) for field in PREFERENCE_FIELDS}


def update_preference(db: Session, payload: PreferenceIn) -> dict:
    preference = _preference_row(db)
    for field in PREFERENCE_FIELDS:
        setattr(preference, field, getattr(payload, field))
    db.flush()
    return {field: getattr(preference, field) for field in PREFERENCE_FIELDS}
