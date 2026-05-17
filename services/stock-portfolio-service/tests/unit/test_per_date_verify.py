from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from app.services import per_date_verify


class FakeResponse:
    def __init__(self, payload: Any, *, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def json(self) -> Any:
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


@pytest.fixture(autouse=True)
def _clear_cache_and_tls(monkeypatch) -> None:
    per_date_verify._NAME_CACHE.clear()
    per_date_verify._ERROR_CACHE.clear()
    # Pin TWSE_TLS_MODE so the tests don't pick up whatever .env happens to
    # configure (the dev box runs with `insecure` to work around the OL-ARM
    # cert issue, which would make these tests assert `verify=False`).
    monkeypatch.setenv("TWSE_TLS_MODE", "fallback")
    monkeypatch.delenv("TWSE_SSL_VERIFY", raising=False)


def test_fetch_twse_returns_name_for_4digit_code(monkeypatch) -> None:
    def fake_get(url: str, *, timeout: int, verify: bool) -> FakeResponse:
        assert "twse.com.tw" in url
        assert "date=20210730" in url
        assert "stockNo=2888" in url
        assert timeout == 10
        assert verify is True
        return FakeResponse(
            {
                "stat": "OK",
                "title": "110年07月 2888 新光金           各日成交資訊",
                "data": [["110/07/30", "1"]],
            }
        )

    monkeypatch.setattr(per_date_verify.requests, "get", fake_get)

    assert per_date_verify.fetch_name_for_date("2888", date(2021, 7, 30)) == (
        "新光金",
        "ok",
    )


def test_fetch_tpex_returns_name_for_6char_warrant_code(monkeypatch) -> None:
    # Dispatch now tries TWSE first for 5+ char codes (warrants can live on either
    # exchange — e.g. 076038 is on TWSE). TWSE returns a stat=='' miss, then TPEx
    # responds with the real data.
    def fake_get(url: str, *, timeout: int, verify: bool) -> FakeResponse:
        assert timeout == 10
        assert verify is True
        if "twse.com.tw" in url:
            return FakeResponse({"stat": "很抱歉，沒有符合條件的資料!", "total": 0})
        assert "tpex.org.tw" in url
        assert "date=2022/05/03" in url
        assert "code=70490P" in url
        return FakeResponse(
            {
                "tables": [
                    {
                        "title": "個股日成交資訊",
                        "subtitle": "70490P 元太元大18售19 111年05月",
                        "data": [["111/05/03", "1"]],
                    }
                ]
            }
        )

    monkeypatch.setattr(per_date_verify.requests, "get", fake_get)

    assert per_date_verify.fetch_name_for_date("70490P", date(2022, 5, 3)) == (
        "元太元大18售19",
        "ok",
    )


def test_fetch_cache_hits_no_second_call(monkeypatch) -> None:
    calls: list[str] = []

    def fake_get(url: str, *, timeout: int, verify: bool) -> FakeResponse:
        calls.append(url)
        return FakeResponse(
            {
                "stat": "OK",
                "title": "110年07月 2888 新光金           各日成交資訊",
                "data": [["110/07/30", "1"]],
            }
        )

    monkeypatch.setattr(per_date_verify.requests, "get", fake_get)

    assert per_date_verify.fetch_name_for_date("2888", date(2021, 7, 30)) == (
        "新光金",
        "ok",
    )
    assert per_date_verify.fetch_name_for_date("2888", date(2021, 7, 30)) == (
        "新光金",
        "ok",
    )
    assert len(calls) == 1


def test_fetch_returns_none_on_http_error(monkeypatch) -> None:
    def fake_get(url: str, *, timeout: int, verify: bool) -> FakeResponse:
        return FakeResponse(ValueError("not json"), status_code=500)

    monkeypatch.setattr(per_date_verify.requests, "get", fake_get)

    assert per_date_verify.fetch_name_for_date("2888", date(2021, 7, 30)) == (
        None,
        "error",
    )


def test_verify_marks_matching_name_as_verified(monkeypatch) -> None:
    def fake_get(url: str, *, timeout: int, verify: bool) -> FakeResponse:
        return FakeResponse(
            {
                "stat": "OK",
                "title": "110年07月 2888 新光金           各日成交資訊",
                "data": [["110/07/30", "1"]],
            }
        )

    monkeypatch.setattr(per_date_verify.requests, "get", fake_get)

    validations = per_date_verify.verify_overrides(
        name_to_code={"新光金": "2888"},
        name_to_earliest_date={"新光金": date(2021, 7, 30)},
        confirmed=set(),
    )

    assert validations == [
        per_date_verify.OverrideValidation(
            name="新光金",
            code="2888",
            status="verified",
            expected_name="新光金",
            fetched_name="新光金",
        )
    ]


def test_verify_marks_different_name_as_name_mismatch(monkeypatch) -> None:
    def fake_get(url: str, *, timeout: int, verify: bool) -> FakeResponse:
        return FakeResponse(
            {
                "stat": "OK",
                "title": "110年07月 2887 台新新光金           各日成交資訊",
                "data": [["110/07/30", "1"]],
            }
        )

    monkeypatch.setattr(per_date_verify.requests, "get", fake_get)

    validations = per_date_verify.verify_overrides(
        name_to_code={"新光金": "2887"},
        name_to_earliest_date={"新光金": date(2021, 7, 30)},
        confirmed=set(),
    )

    assert validations == [
        per_date_verify.OverrideValidation(
            name="新光金",
            code="2887",
            status="name_mismatch",
            expected_name="新光金",
            fetched_name="台新新光金",
        )
    ]


def test_verify_marks_empty_response_as_not_traded_on_date(monkeypatch) -> None:
    """4-digit codes now fall through TWSE → TPEx (per PR #6 review), so both
    sides must return empty for the final status to be `not_traded_on_date`."""

    def fake_get(url: str, *, timeout: int, verify: bool) -> FakeResponse:
        if "twse.com.tw" in url:
            return FakeResponse(
                {
                    "stat": "OK",
                    "title": "110年07月 2888 新光金           各日成交資訊",
                    "data": [],
                }
            )
        # TPEx fall-through also returns no rows.
        return FakeResponse(
            {
                "tables": [
                    {"title": "個股日成交資訊", "subtitle": "2888 新光金 110年07月", "data": []}
                ]
            }
        )

    monkeypatch.setattr(per_date_verify.requests, "get", fake_get)

    validations = per_date_verify.verify_overrides(
        name_to_code={"新光金": "2888"},
        name_to_earliest_date={"新光金": date(2021, 7, 30)},
        confirmed=set(),
    )

    assert validations == [
        per_date_verify.OverrideValidation(
            name="新光金",
            code="2888",
            status="not_traded_on_date",
            expected_name="新光金",
            fetched_name=None,
        )
    ]


def test_verify_respects_confirmed_set_overrides_to_user_overridden(monkeypatch) -> None:
    def fail_get(url: str, *, timeout: int, verify: bool) -> FakeResponse:
        raise AssertionError("confirmed overrides should not fetch")

    monkeypatch.setattr(per_date_verify.requests, "get", fail_get)

    validations = per_date_verify.verify_overrides(
        name_to_code={"新光金": "2887"},
        name_to_earliest_date={"新光金": date(2021, 7, 30)},
        confirmed={"新光金"},
    )

    assert validations == [
        per_date_verify.OverrideValidation(
            name="新光金",
            code="2887",
            status="user_overridden",
            expected_name="新光金",
            fetched_name=None,
        )
    ]
