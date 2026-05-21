from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from decimal import Decimal

import pytest

from app.models import portfolio as models
from app.services import broker_cathay_service, import_service
from app.services.per_date_verify import OverrideValidation

CATHAY_HEADER = (
    "股名,日期,成交股數,淨收付金額,買賣別,成交價,成本,手續費,交易稅,"
    "融資金額/券擔保品,資自備款/券保證金,利息,稅款,券手續費/標借費,委託書號\n"
)


def _cathay_csv(*rows: str) -> bytes:
    return (
        "根據您篩選的結果，總計有2筆資料\n" + CATHAY_HEADER + "\n".join(rows) + "\n"
    ).encode("utf-8")


def _row(
    *,
    name: str = "晶宏",
    side: str = "現買",
    quantity: str = "1,000",
    price: str = "56.3",
    fee: str = "22",
    tax: str = "0",
    order_id: str = "aT532",
) -> str:
    return (
        f'{name},2026/05/08,"{quantity}","-56,322",{side},"{price}","56,300",'
        f"{fee},{tax},0,0,0,0,0,{order_id}"
    )


@pytest.fixture(autouse=True)
def _stable_name_map(monkeypatch):
    monkeypatch.setitem(broker_cathay_service.NAME_TO_SYMBOL, "晶宏", ["3141"])


def _legacy_fp() -> str:
    return import_service._transaction_fingerprint(
        "3141",
        "BUY",
        1000,
        Decimal("56.3"),
        datetime(2026, 5, 8, tzinfo=timezone.utc),
        Decimal("22"),
        Decimal("0"),
    )


def _new_fp(order_id: str) -> str:
    return import_service._transaction_fingerprint(
        "3141",
        "BUY",
        1000,
        Decimal("56.3"),
        datetime(2026, 5, 8, tzinfo=timezone.utc),
        Decimal("22"),
        Decimal("0"),
        order_id=order_id,
    )


def _seed_legacy_transaction(db_session) -> models.Transaction:
    tx = models.Transaction(
        symbol="3141",
        name="晶宏",
        type=models.TransactionType.BUY,
        quantity=1000,
        price=Decimal("56.3"),
        trade_date=datetime(2026, 5, 8, tzinfo=timezone.utc),
        fee=Decimal("22"),
        tax=Decimal("0"),
        import_fingerprint=_legacy_fp(),
    )
    db_session.add(tx)
    db_session.commit()
    db_session.refresh(tx)
    return tx


def test_detect_csv_format_cathay_preamble():
    assert import_service.detect_csv_format(_cathay_csv(_row())) == "cathay"


def test_detect_csv_format_generic_english_header():
    raw = b"symbol,type,quantity\n2330,BUY,1\n"
    assert import_service.detect_csv_format(raw) == "generic"


def test_detect_csv_format_blank_leading_lines():
    raw = "\n\n根據您篩選的結果，總計有1筆資料\n".encode("utf-8")
    assert import_service.detect_csv_format(raw) == "cathay"


def test_resolve_symbol_unique():
    assert broker_cathay_service.resolve_symbol("晶宏") == "3141"


def test_resolve_symbol_unknown_raises_with_message():
    with pytest.raises(ValueError, match="cannot resolve symbol for 股名='不存在'"):
        broker_cathay_service.resolve_symbol("不存在")


def test_resolve_symbol_ambiguous_raises_with_candidates(monkeypatch):
    monkeypatch.setitem(broker_cathay_service.NAME_TO_SYMBOL, "元大23購15", ["A", "B"])
    with pytest.raises(
        ValueError,
        match=r"ambiguous symbol for 股名='元大23購15': \['A', 'B'\]",
    ):
        broker_cathay_service.resolve_symbol("元大23購15")


@pytest.mark.parametrize(
    ("side", "type_", "subtype"),
    [
        ("現買", "BUY", "現"),
        ("資買", "BUY", "資"),
        ("券買", "BUY", "券"),
        ("沖買", "BUY", "沖"),
        ("現賣", "SELL", "現"),
        ("資賣", "SELL", "資"),
        ("券賣", "SELL", "券"),
        ("沖賣", "SELL", "沖"),
    ],
)
def test_cathay_type_collapse_all_eight_variants(side, type_, subtype):
    parsed = broker_cathay_service.parse_cathay_rows(_cathay_csv(_row(side=side)))
    assert parsed.errors == []
    assert parsed.rows[0].payload["type"] == type_
    assert parsed.rows[0].payload["broker_subtype"] == subtype


@pytest.mark.parametrize(
    ("side", "marker"),
    [
        ("沖買", "沖買"),
        ("沖賣", "沖賣"),
        ("現買", None),
        ("現賣", None),
        ("資買", None),
        ("資賣", None),
        ("券買", None),
        ("券賣", None),
    ],
)
def test_cathay_day_trade_marker_emitted_for_intraday_sides(side, marker):
    parsed = broker_cathay_service.parse_cathay_rows(_cathay_csv(_row(side=side)))
    assert parsed.errors == []
    assert parsed.rows[0].payload["broker_day_trade_marker"] == marker


def test_cathay_subtype_does_not_affect_fingerprint():
    parsed = broker_cathay_service.parse_cathay_rows(
        _cathay_csv(_row(side="現買"), _row(side="資買"))
    )
    assert parsed.errors == []
    assert parsed.rows[0].fingerprint == parsed.rows[1].fingerprint


def test_cathay_parser_skips_preamble_and_reads_header():
    parsed = broker_cathay_service.parse_cathay_rows(_cathay_csv(_row()))
    assert parsed.errors == []
    assert len(parsed.rows) == 1
    assert parsed.rows[0].payload["symbol"] == "3141"


def test_cathay_thousands_separator_in_quantity_and_price():
    parsed = broker_cathay_service.parse_cathay_rows(
        _cathay_csv(_row(quantity="1,000", price="56,322"))
    )
    assert parsed.errors == []
    assert parsed.rows[0].payload["quantity"] == 1000
    assert parsed.rows[0].payload["price"] == Decimal("56322")


def test_rehash_existing_legacy_row_updates_in_place_no_insert(db_session):
    tx = _seed_legacy_transaction(db_session)
    result = broker_cathay_service.parse_cathay_transactions_csv(
        _cathay_csv(_row()), dry_run=False, db=db_session
    )

    assert result.rehashed == 1
    assert result.created == 0
    assert result.skipped_duplicates == 0
    assert db_session.query(models.Transaction).count() == 1
    db_session.refresh(tx)
    assert tx.import_fingerprint == _new_fp("aT532")


def test_rehash_propagates_broker_day_trade_marker(db_session):
    tx = models.Transaction(
        symbol="3141",
        name="晶宏",
        type=models.TransactionType.BUY,
        position_side=models.PositionSide.LONG,
        quantity=1000,
        price=Decimal("56.3"),
        trade_date=datetime(2026, 5, 8, 13, 30, tzinfo=timezone.utc),
        fee=Decimal("22"),
        tax=Decimal("0"),
        import_fingerprint=None,
    )
    db_session.add(tx)
    db_session.commit()

    result = broker_cathay_service.parse_cathay_transactions_csv(
        _cathay_csv(_row(side="沖買")), dry_run=False, db=db_session
    )

    assert result.rehashed == 1
    assert result.created == 0
    db_session.refresh(tx)
    assert tx.broker_day_trade_marker == "沖買"


def test_rehash_recomputes_is_day_trade_after_marker_write(db_session):
    tx = models.Transaction(
        symbol="3141",
        name="晶宏",
        type=models.TransactionType.BUY,
        position_side=models.PositionSide.LONG,
        quantity=1000,
        price=Decimal("56.3"),
        trade_date=datetime(2026, 5, 8, 13, 30, tzinfo=timezone.utc),
        fee=Decimal("22"),
        tax=Decimal("0"),
        is_day_trade=False,
        import_fingerprint=None,
    )
    db_session.add(tx)
    db_session.commit()

    result = broker_cathay_service.parse_cathay_transactions_csv(
        _cathay_csv(_row(side="沖買")), dry_run=False, db=db_session
    )

    assert result.rehashed == 1
    db_session.refresh(tx)
    assert tx.broker_day_trade_marker == "沖買"
    assert tx.is_day_trade is True


def test_rehash_recovers_same_day_collision_twin(db_session):
    _seed_legacy_transaction(db_session)
    result = broker_cathay_service.parse_cathay_transactions_csv(
        _cathay_csv(_row(order_id="aT532"), _row(order_id="aT699")),
        dry_run=False,
        db=db_session,
    )

    assert result.rehashed == 1
    assert result.created == 1
    assert db_session.query(models.Transaction).count() == 2
    assert {
        fp for (fp,) in db_session.query(models.Transaction.import_fingerprint).all()
    } == {_new_fp("aT532"), _new_fp("aT699")}


def test_rehash_idempotent_second_run_all_skipped(db_session):
    raw = _cathay_csv(_row(order_id="aT532"), _row(order_id="aT699"))
    _seed_legacy_transaction(db_session)
    first = broker_cathay_service.parse_cathay_transactions_csv(
        raw, dry_run=False, db=db_session
    )
    second = broker_cathay_service.parse_cathay_transactions_csv(
        raw, dry_run=False, db=db_session
    )

    assert first.rehashed == 1
    assert first.created == 1
    assert second.skipped_duplicates == 2
    assert second.rehashed == 0
    assert second.created == 0


def test_rehash_unresolvable_name_skips_row_not_rollback(db_session):
    tx = _seed_legacy_transaction(db_session)
    result = broker_cathay_service.parse_cathay_transactions_csv(
        _cathay_csv(_row(order_id="aT532"), _row(name="不存在", order_id="bad")),
        dry_run=False,
        db=db_session,
    )

    assert result.rehashed == 1
    assert result.created == 0
    assert result.errors == []
    assert result.skipped_unresolved == 1
    assert len(result.unresolved_names) == 1
    assert result.unresolved_names[0].name == "不存在"
    assert db_session.query(models.Transaction).count() == 1
    db_session.refresh(tx)
    assert tx.import_fingerprint == _new_fp("aT532")


def test_dry_run_rehash_reports_counts_writes_nothing(db_session):
    tx = _seed_legacy_transaction(db_session)
    result = broker_cathay_service.parse_cathay_transactions_csv(
        _cathay_csv(_row(order_id="aT532"), _row(order_id="aT699")),
        dry_run=True,
        db=db_session,
    )

    assert result.would_rehash == 1
    assert result.would_insert == 1
    assert result.would_skip_duplicate == 0
    assert db_session.query(models.Transaction).count() == 1
    db_session.refresh(tx)
    assert tx.import_fingerprint == _legacy_fp()


# ---------- Manual name overrides + unresolved-name reporting ----------


def test_name_overrides_resolves_name_absent_from_static_map():
    parsed = broker_cathay_service.parse_cathay_rows(
        _cathay_csv(_row(name="不存在")),
        name_overrides={"不存在": "9999"},
    )

    assert parsed.errors == []
    assert parsed.unresolved_names == []
    assert parsed.rows[0].payload["symbol"] == "9999"


def test_name_overrides_wins_over_static_map():
    parsed = broker_cathay_service.parse_cathay_rows(
        _cathay_csv(_row()),
        name_overrides={"晶宏": "9999"},
    )

    assert parsed.errors == []
    assert parsed.rows[0].payload["symbol"] == "9999"


def test_unresolved_name_collected_not_raised():
    parsed = broker_cathay_service.parse_cathay_rows(_cathay_csv(_row(name="不存在")))

    assert parsed.rows == []
    assert parsed.errors == []
    assert len(parsed.unresolved_names) == 1
    assert parsed.unresolved_names[0].name == "不存在"
    assert parsed.unresolved_names[0].occurrences == 1
    assert parsed.unresolved_names[0].sample_dates == ["2026-05-08"]


def test_unresolved_does_not_rollback_resolved_rows(db_session):
    result = broker_cathay_service.parse_cathay_transactions_csv(
        _cathay_csv(
            _row(order_id="aT532"),
            _row(order_id="aT699"),
            _row(name="不存在", order_id="bad"),
        ),
        dry_run=False,
        db=db_session,
    )

    assert result.rehashed == 0
    assert result.created == 2
    assert result.skipped_unresolved == 1
    assert result.errors == []
    assert len(result.unresolved_names) == 1
    assert result.unresolved_names[0].name == "不存在"
    assert db_session.query(models.Transaction).count() == 2


def test_ambiguous_name_surfaces_to_unresolved_panel(db_session, monkeypatch):
    """Per PR #6 review: ambiguous names (multiple candidates in
    NAME_TO_SYMBOL) now flow into the same override UX as unknown names,
    instead of hard-failing the batch. The user picks the right ticker on the
    frontend; resolvable rows commit as usual."""
    monkeypatch.setitem(broker_cathay_service.NAME_TO_SYMBOL, "模擬曖昧", ["A", "B"])
    tx = _seed_legacy_transaction(db_session)
    result = broker_cathay_service.parse_cathay_transactions_csv(
        _cathay_csv(_row(order_id="aT532"), _row(name="模擬曖昧", order_id="bad")),
        dry_run=False,
        db=db_session,
    )

    # Resolvable row still rehashes; ambiguous one is collected for the
    # override panel rather than poisoning the whole batch.
    assert result.rehashed == 1
    assert result.created == 0
    assert result.errors == []
    assert [u.name for u in result.unresolved_names] == ["模擬曖昧"]
    assert result.skipped_unresolved == 1
    assert db_session.query(models.Transaction).count() == 1


def test_endpoint_accepts_name_overrides_form_field(client, monkeypatch):
    def fake_verify_overrides(**kwargs):
        return [
            OverrideValidation(
                name="新光金",
                code="2888",
                status="verified",
                expected_name="新光金",
                fetched_name="新光金",
            )
        ]

    monkeypatch.setattr(
        broker_cathay_service.per_date_verify,
        "verify_overrides",
        fake_verify_overrides,
    )

    response = client.post(
        "/api/portfolio/imports/transactions",
        files={"file": ("cathay.csv", BytesIO(_cathay_csv(_row(name="新光金"))), "text/csv")},
        data={"name_overrides": '{"新光金":"2888"}'},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 1
    assert body["rows"][0]["payload"]["symbol"] == "2888"


def test_generic_path_response_has_empty_unresolved_names_for_shape_consistency(client):
    raw = "symbol,type,quantity,price,trade_date,fee,tax,name\n2330,BUY,10,600,2026-05-15T01:30:00Z,28,0,台積電\n".encode("utf-8")
    response = client.post(
        "/api/portfolio/imports/transactions",
        files={"file": ("tx.csv", BytesIO(raw), "text/csv")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["skipped_unresolved"] == 0
    assert body["unresolved_names"] == []


def test_override_with_name_mismatch_skips_rows_keeps_other_rows_committable(
    db_session, monkeypatch
):
    def fake_verify_overrides(**kwargs):
        return [
            OverrideValidation(
                name="新光金",
                code="2887",
                status="name_mismatch",
                expected_name="新光金",
                fetched_name="台新新光金",
            )
        ]

    monkeypatch.setattr(
        broker_cathay_service.per_date_verify,
        "verify_overrides",
        fake_verify_overrides,
    )

    result = broker_cathay_service.parse_cathay_transactions_csv(
        _cathay_csv(_row(order_id="auto"), _row(name="新光金", order_id="override")),
        dry_run=False,
        db=db_session,
        name_overrides={"新光金": "2887"},
    )

    assert result.created == 1
    assert result.skipped_unverified == 1
    assert result.override_validations[0].status == "name_mismatch"
    assert db_session.query(models.Transaction).count() == 1


def test_confirmed_overrides_allow_mismatched_rows_to_commit(db_session, monkeypatch):
    def fake_verify_overrides(**kwargs):
        assert kwargs["confirmed"] == {"新光金"}
        return [
            OverrideValidation(
                name="新光金",
                code="2887",
                status="user_overridden",
                expected_name="新光金",
                fetched_name=None,
            )
        ]

    monkeypatch.setattr(
        broker_cathay_service.per_date_verify,
        "verify_overrides",
        fake_verify_overrides,
    )

    result = broker_cathay_service.parse_cathay_transactions_csv(
        _cathay_csv(_row(order_id="auto"), _row(name="新光金", order_id="override")),
        dry_run=False,
        db=db_session,
        name_overrides={"新光金": "2887"},
        confirmed_overrides={"新光金"},
    )

    assert result.created == 2
    assert result.skipped_unverified == 0
    assert result.override_validations[0].status == "user_overridden"
    assert db_session.query(models.Transaction).count() == 2


def test_auto_resolved_rows_not_sent_to_per_date_verify(db_session, monkeypatch):
    calls: list[dict] = []

    def fake_verify_overrides(**kwargs):
        calls.append(kwargs)
        return [
            OverrideValidation(
                name="新光金",
                code="2888",
                status="verified",
                expected_name="新光金",
                fetched_name="新光金",
            )
        ]

    monkeypatch.setattr(
        broker_cathay_service.per_date_verify,
        "verify_overrides",
        fake_verify_overrides,
    )

    result = broker_cathay_service.parse_cathay_transactions_csv(
        _cathay_csv(_row(order_id="auto"), _row(name="新光金", order_id="override")),
        dry_run=False,
        db=db_session,
        name_overrides={"新光金": "2888"},
    )

    assert result.created == 2
    assert calls == [
        {
            "name_to_code": {"新光金": "2888"},
            "name_to_earliest_date": {"晶宏": datetime(2026, 5, 8, tzinfo=timezone.utc).date(), "新光金": datetime(2026, 5, 8, tzinfo=timezone.utc).date()},
            "confirmed": set(),
        }
    ]
