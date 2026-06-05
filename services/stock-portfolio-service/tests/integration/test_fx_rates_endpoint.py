from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.fx_rate import FXRate
from app.services.quotes import fx_rate_service


def test_phase2_fx_rate_lookup_dates_and_gbp_minor_unit(db_session) -> None:
    db_session.add_all(
        [
            FXRate(currency="USD", date=date(2026, 6, 3), rate_to_twd=Decimal("32"), source="test"),
            FXRate(currency="USD", date=date(2026, 6, 5), rate_to_twd=Decimal("33"), source="test"),
            FXRate(currency="GBP", date=date(2026, 6, 5), rate_to_twd=Decimal("40"), source="test"),
        ]
    )
    db_session.commit()

    assert fx_rate_service.get_rate(db_session, "USD", date(2026, 6, 4)) == Decimal("32.00000000")
    assert fx_rate_service.get_rate(db_session, "USD", date(2026, 6, 5)) == Decimal("33.00000000")
    assert fx_rate_service.get_rate(db_session, "GBp", date(2026, 6, 5)) == Decimal("0.40000000")
    assert fx_rate_service.get_rate(db_session, "USD", date(2026, 6, 2)) is None
