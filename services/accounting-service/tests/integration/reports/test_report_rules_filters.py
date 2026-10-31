from datetime import date
from decimal import Decimal

from sqlalchemy import func, select

from app.models import LedgerEntry
from app.services.report_rules import ReportFilters, counted_filter, entry_filters


def test_entry_filters_with_every_option_executes(db_session):
    f = ReportFilters(
        date_from=date(2026, 10, 1), date_to=date(2026, 10, 31), account_ids=[1], category_ids=[1], project_ids=[1],
        tags=["a"], merchant="x_%", counterparty_id=1, amount_min=Decimal(-5), amount_max=Decimal(5), q="y",
        weekdays=[1, 7],
    )
    where = entry_filters(db_session, f)
    assert db_session.scalar(select(func.count()).select_from(LedgerEntry).where(*where, counted_filter(True))) == 0
