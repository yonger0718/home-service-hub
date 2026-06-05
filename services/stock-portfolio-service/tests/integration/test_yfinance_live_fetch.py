from __future__ import annotations

import pytest

from app.services.quotes import yfinance_fetcher


@pytest.mark.live
def test_live_yfinance_fetch_shape() -> None:
    rows, errors = yfinance_fetcher.fetch([("AAPL", "US"), ("VOD", "LSE"), ("MSFT", "US")])

    by_key = {(row.symbol, row.market): row for row in rows}
    assert ("AAPL", "US") in by_key
    assert ("MSFT", "US") in by_key
    assert errors == [] or all(isinstance(error, str) for error in errors)
    for row in rows:
        assert row.close > 0
        assert row.currency
