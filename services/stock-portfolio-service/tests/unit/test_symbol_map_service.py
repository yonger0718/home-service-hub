"""Symbol-map cache + transaction backfill."""

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.models import portfolio as models
from app.models.symbol_map import SymbolMap
from app.services import symbol_map_service as svc


def _stub_codes(**entries):
    """Build a fake twstock.codes dict whose values have .name and .market attributes."""
    return {
        code: SimpleNamespace(name=name, market=market)
        for code, (name, market) in entries.items()
    }


@pytest.fixture
def fake_twstock():
    """Patch twstock.codes and twstock.__update_codes for the duration of the test."""
    fake = SimpleNamespace(
        codes=_stub_codes(),
        __update_codes=lambda: None,
    )
    with patch.dict("sys.modules", {"twstock": fake}):
        yield fake


def test_refresh_upserts_known_names(db_session, fake_twstock):
    fake_twstock.codes = _stub_codes(
        **{
            "2317": ("鴻海", "TWSE"),
            "2330": ("台積電", "TWSE"),
            "0050": ("元大台灣50", "TWSE"),
        }
    )
    result = svc.refresh_all_from_twstock(db_session)
    assert result["refreshed_count"] == 3
    rows = db_session.query(SymbolMap).order_by(SymbolMap.symbol).all()
    assert [(r.name, r.symbol) for r in rows] == [
        ("元大台灣50", "0050"),
        ("鴻海", "2317"),
        ("台積電", "2330"),
    ]


def test_refresh_is_idempotent(db_session, fake_twstock):
    fake_twstock.codes = _stub_codes(**{"2317": ("鴻海", "TWSE")})
    svc.refresh_all_from_twstock(db_session)
    svc.refresh_all_from_twstock(db_session)
    assert db_session.query(SymbolMap).count() == 1


def test_refresh_skips_blank_names(db_session, fake_twstock):
    fake_twstock.codes = _stub_codes(
        **{"2317": ("鴻海", "TWSE"), "GOOD": ("", "TWSE")}
    )
    svc.refresh_all_from_twstock(db_session)
    assert db_session.query(SymbolMap).count() == 1


def test_resolve_name_returns_ticker(db_session):
    db_session.add(SymbolMap(name="鴻海", symbol="2317", market="TWSE"))
    db_session.commit()
    assert svc.resolve_name(db_session, "鴻海") == "2317"


def test_resolve_name_returns_none_for_unknown(db_session):
    assert svc.resolve_name(db_session, "不存在") is None


def _make_tx(symbol, name="dummy", qty=1000, price="50.00", fp=None):
    return models.Transaction(
        symbol=symbol,
        name=name,
        type=models.TransactionType.BUY,
        quantity=qty,
        price=Decimal(price),
        fee=Decimal("0"),
        tax=Decimal("0"),
        trade_date=datetime(2026, 5, 1, 13, 30, tzinfo=timezone.utc),
        import_fingerprint=fp,
    )


def test_backfill_rewrites_resolvable_chinese(db_session):
    db_session.add(SymbolMap(name="鴻海", symbol="2317", market="TWSE"))
    tx = _make_tx("鴻海", name="鴻海", fp="old-fp")
    db_session.add(tx)
    db_session.commit()

    result = svc.backfill_transactions(db_session, dry_run=False)

    assert result["updated"] == 1
    assert result["unresolved"] == []
    assert result["collisions"] == []
    db_session.refresh(tx)
    assert tx.symbol == "2317"
    # Fingerprint is intentionally NOT recomputed so future re-imports of the
    # original Chinese-named CSV still dedupe against the rewritten row.
    assert tx.import_fingerprint == "old-fp"


def test_backfill_skips_ticker_symbol(db_session):
    tx = _make_tx("2330", name="台積電")
    db_session.add(tx)
    db_session.commit()
    result = svc.backfill_transactions(db_session, dry_run=False)
    assert result["updated"] == 0
    assert result["unresolved"] == []


def test_backfill_reports_unresolved(db_session):
    tx = _make_tx("世紀鋼富邦49購01", name="世紀鋼富邦49購01")
    db_session.add(tx)
    db_session.commit()
    result = svc.backfill_transactions(db_session, dry_run=False)
    assert result["updated"] == 0
    assert "世紀鋼富邦49購01" in result["unresolved"]


def test_backfill_leaves_fingerprint_untouched(db_session):
    """The original fingerprint must survive so the source CSV still dedupes."""
    db_session.add(SymbolMap(name="鴻海", symbol="2317", market="TWSE"))
    tx = _make_tx("鴻海", name="鴻海", fp="fp-from-original-csv")
    db_session.add(tx)
    db_session.commit()

    svc.backfill_transactions(db_session, dry_run=False)

    db_session.refresh(tx)
    assert tx.symbol == "2317"
    assert tx.import_fingerprint == "fp-from-original-csv"


def test_backfill_dry_run_makes_no_writes(db_session):
    db_session.add(SymbolMap(name="鴻海", symbol="2317", market="TWSE"))
    tx = _make_tx("鴻海", name="鴻海", fp="old-fp")
    db_session.add(tx)
    db_session.commit()

    result = svc.backfill_transactions(db_session, dry_run=True)

    assert result["updated"] == 1
    assert result["dry_run"] is True
    db_session.refresh(tx)
    assert tx.symbol == "鴻海"  # rolled back


def test_backfill_endpoint_dry_run(client, db_session):
    db_session.add(SymbolMap(name="鴻海", symbol="2317", market="TWSE"))
    db_session.add(_make_tx("鴻海", name="鴻海", fp="x"))
    db_session.commit()

    response = client.post("/api/portfolio/symbol-map/backfill", params={"dry_run": "true"})
    assert response.status_code == 200
    body = response.json()
    assert body["updated"] == 1
    assert body["dry_run"] is True


def test_names_endpoint_prefers_meaningful_transaction_name_over_placeholder(
    client, db_session
):
    """If the latest tx for a symbol has name==symbol (legacy placeholder),
    fall back to an older tx with a real name OR the symbol_map dictionary
    rather than returning the ticker as its own display name.
    """
    db_session.add(SymbolMap(name="富邦臺灣加權正2", symbol="00675L", market="TWSE"))
    # Newer tx has placeholder name == symbol; older tx has the real name.
    placeholder = models.Transaction(
        symbol="00675L",
        name="00675L",
        type=models.TransactionType.BUY,
        quantity=1000,
        price=Decimal("50"),
        fee=Decimal("0"),
        tax=Decimal("0"),
        trade_date=datetime(2026, 5, 10, 13, 30, tzinfo=timezone.utc),
    )
    real = models.Transaction(
        symbol="00675L",
        name="富邦臺灣加權正2",
        type=models.TransactionType.BUY,
        quantity=1000,
        price=Decimal("50"),
        fee=Decimal("0"),
        tax=Decimal("0"),
        trade_date=datetime(2026, 5, 1, 13, 30, tzinfo=timezone.utc),
    )
    db_session.add_all([placeholder, real])
    db_session.commit()

    response = client.get("/api/portfolio/symbol-map/names")
    assert response.status_code == 200
    body = response.json()
    assert body["00675L"] == "富邦臺灣加權正2"


def test_names_endpoint_falls_back_to_symbol_map_when_only_placeholder_tx(
    client, db_session
):
    """If every tx for a symbol uses the placeholder name, look it up in
    symbol_map instead of returning the ticker as its own display name."""
    db_session.add(SymbolMap(name="富邦臺灣加權正2", symbol="00675L", market="TWSE"))
    placeholder = models.Transaction(
        symbol="00675L",
        name="00675L",
        type=models.TransactionType.BUY,
        quantity=1000,
        price=Decimal("50"),
        fee=Decimal("0"),
        tax=Decimal("0"),
        trade_date=datetime(2026, 5, 10, 13, 30, tzinfo=timezone.utc),
    )
    db_session.add(placeholder)
    db_session.commit()

    response = client.get("/api/portfolio/symbol-map/names")
    assert response.status_code == 200
    body = response.json()
    assert body["00675L"] == "富邦臺灣加權正2"
