"""TWTB8U parser, upsert idempotency, factor helper, endpoints."""

import json
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.models.corporate_action import CorporateAction
from app.services import corporate_action_service as svc


def _twtb8u_payload(*rows):
    return {"data": list(rows)}


def test_parse_roc_date_year_month_day():
    assert svc.parse_roc_date("115年5月14日") == date(2026, 5, 14)


def test_parse_roc_date_slash_format():
    assert svc.parse_roc_date("115/05/14") == date(2026, 5, 14)


def test_parse_roc_date_already_western():
    assert svc.parse_roc_date("2026/05/14") == date(2026, 5, 14)


def test_parse_roc_date_invalid_returns_none():
    assert svc.parse_roc_date("nope") is None
    assert svc.parse_roc_date("") is None


def test_parse_twtb8u_extracts_ratio_and_event_key():
    payload = _twtb8u_payload(
        ["115年5月14日", "2330", "台積電", "100", "50", "x", "y", "z"],
    )
    rows = svc.parse_twtb8u(payload, 2026)
    assert len(rows) == 1
    assert rows[0].symbol == "2330"
    assert rows[0].effective_date == date(2026, 5, 14)
    assert rows[0].ratio == Decimal("2")
    assert rows[0].source_event_key == "2330_2026-05-14"
    assert rows[0].action_type == "FACE_VALUE_CHANGE"
    assert rows[0].source == "TWSE"


def test_parse_twtb8u_skips_missing_pre_close():
    payload = _twtb8u_payload(
        ["115年5月14日", "2330", "x", "-", "50", "x", "y", "z"],
    )
    assert svc.parse_twtb8u(payload, 2026) == []


def test_parse_twtb8u_skips_zero_post_ref():
    payload = _twtb8u_payload(
        ["115年5月14日", "2330", "x", "100", "0", "x", "y", "z"],
    )
    assert svc.parse_twtb8u(payload, 2026) == []


def test_parse_twtb8u_accepts_bytes_payload():
    raw = json.dumps(_twtb8u_payload(
        ["115年5月14日", "2330", "x", "100", "50", "x", "y", "z"],
    )).encode("utf-8")
    rows = svc.parse_twtb8u(raw, 2026)
    assert len(rows) == 1


def test_upsert_rows_inserts(db_session):
    row = svc.CorporateActionRow(
        symbol="2330", effective_date=date(2026, 5, 14),
        action_type="FACE_VALUE_CHANGE", ratio=Decimal("10"),
        source="TWSE", source_event_key="2330_2026-05-14",
        raw_payload={},
    )
    svc.upsert_rows(db_session, [row])
    assert db_session.query(CorporateAction).count() == 1


def test_upsert_rows_is_idempotent(db_session):
    row = svc.CorporateActionRow(
        symbol="2330", effective_date=date(2026, 5, 14),
        action_type="FACE_VALUE_CHANGE", ratio=Decimal("10"),
        source="TWSE", source_event_key="2330_2026-05-14",
        raw_payload={},
    )
    svc.upsert_rows(db_session, [row])
    svc.upsert_rows(db_session, [row])
    assert db_session.query(CorporateAction).count() == 1


def test_get_split_factor_returns_one_when_no_actions(db_session):
    assert svc.get_split_factor(db_session, "2330", date(2026, 12, 31)) == Decimal(1)


def test_get_split_factor_single_action(db_session):
    db_session.add(CorporateAction(
        symbol="2330", effective_date=date(2026, 5, 14),
        action_type="FACE_VALUE_CHANGE", ratio=Decimal("2"),
        source="TWSE", source_event_key="2330_2026-05-14",
    ))
    db_session.commit()
    assert svc.get_split_factor(db_session, "2330", date(2026, 5, 14)) == Decimal("2")
    assert svc.get_split_factor(db_session, "2330", date(2026, 5, 13)) == Decimal("1")


def test_get_split_factor_compound(db_session):
    db_session.add_all([
        CorporateAction(symbol="X", effective_date=date(2026, 1, 1),
                        ratio=Decimal("2"), source="TWSE",
                        source_event_key="X_2026-01-01"),
        CorporateAction(symbol="X", effective_date=date(2026, 6, 1),
                        ratio=Decimal("5"), source="TWSE",
                        source_event_key="X_2026-06-01"),
    ])
    db_session.commit()
    assert svc.get_split_factor(db_session, "X", date(2026, 12, 31)) == Decimal("10")
    assert svc.get_split_factor(db_session, "X", date(2026, 3, 1)) == Decimal("2")


def test_list_actions_filters_by_symbol(db_session):
    db_session.add_all([
        CorporateAction(symbol="2330", effective_date=date(2026, 5, 14),
                        ratio=Decimal("2"), source="TWSE",
                        source_event_key="2330_2026-05-14"),
        CorporateAction(symbol="0050", effective_date=date(2026, 5, 14),
                        ratio=Decimal("3"), source="TWSE",
                        source_event_key="0050_2026-05-14"),
    ])
    db_session.commit()
    rows = svc.list_actions(db_session, symbol="2330")
    assert [r.symbol for r in rows] == ["2330"]


def test_list_actions_filters_by_date_range(db_session):
    for d in (date(2026, 1, 1), date(2026, 5, 14), date(2026, 12, 1)):
        db_session.add(CorporateAction(
            symbol="X", effective_date=d, ratio=Decimal("2"),
            source="TWSE", source_event_key=f"X_{d.isoformat()}",
        ))
    db_session.commit()
    rows = svc.list_actions(db_session, from_date=date(2026, 3, 1), to_date=date(2026, 6, 30))
    assert [r.effective_date for r in rows] == [date(2026, 5, 14)]


def test_backfill_year_calls_fetch_and_upserts(db_session):
    rows = [
        svc.CorporateActionRow(
            symbol="2330", effective_date=date(2026, 5, 14),
            action_type="FACE_VALUE_CHANGE", ratio=Decimal("2"),
            source="TWSE", source_event_key="2330_2026-05-14",
            raw_payload={},
        )
    ]
    with patch.object(svc, "fetch_year", return_value=rows) as fetch_mock:
        result = svc.backfill_year(db_session, 2026)
    fetch_mock.assert_called_once_with(2026)
    assert result == {"year": 2026, "rows": 1, "written": 1}
    assert db_session.query(CorporateAction).count() == 1


def test_corp_actions_endpoint_list(client, db_session):
    db_session.add(CorporateAction(
        symbol="2330", effective_date=date(2026, 5, 14),
        ratio=Decimal("2"), source="TWSE",
        source_event_key="2330_2026-05-14",
    ))
    db_session.commit()
    response = client.get("/api/portfolio/corporate-actions", params={"symbol": "2330"})
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["symbol"] == "2330"
    assert body[0]["ratio"].startswith("2")


def test_corp_actions_endpoint_backfill(client):
    rows = [
        svc.CorporateActionRow(
            symbol="2330", effective_date=date(2026, 5, 14),
            action_type="FACE_VALUE_CHANGE", ratio=Decimal("2"),
            source="TWSE", source_event_key="2330_2026-05-14",
            raw_payload={},
        )
    ]
    with patch.object(svc, "fetch_year", return_value=rows):
        response = client.post("/api/portfolio/corporate-actions/backfill", params={"year": 2026})
    assert response.status_code == 200
    assert response.json()["written"] == 1
