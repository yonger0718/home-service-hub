"""永豐期貨 findings (spec §6.4), pending items and acknowledgements (§8), run reports."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models.sinopac_futures_statement import DAY_AMOUNTS, FuturesStatementDay
from app.services import portfolio_snapshot_service
from app.services import sinopac_futures_statement_service as svc
from app.services.sinopac_futures_statement_service import (
    acknowledge,
    findings_sha,
    import_statement,
    latest_runs,
    pending,
    recompute_findings,
    record_run,
)
from tests.integration.test_futures_statement_import import TODAY, day_row, daily, month_row, monthly

MAR = date(2026, 3, 1)


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    monkeypatch.setattr(portfolio_snapshot_service, "_today_tw", lambda: TODAY)


def _imp(db, payload):
    result = import_statement(db, payload, dry_run=False)
    assert result.errors == []
    return result


def _prev(d):
    return {"code": "prev", "dates": [d], "letters": []}


def _gap(*dates):
    return {"code": "gap", "dates": list(dates), "letters": []}


# --- prev -----------------------------------------------------------------------


def test_prev_on_gap(db_session):
    _imp(db_session, daily("2026-03-02", balance="100"))

    result = _imp(db_session, daily("2026-03-09", prev="90", balance="90"))

    assert result.findings == [_prev("2026-03-02")]
    row = day_row(db_session, date(2026, 3, 9))
    assert row.findings == [_prev("2026-03-02")]
    assert row.findings_sha == findings_sha([_prev("2026-03-02")])
    assert row.findings_since is not None
    assert day_row(db_session, date(2026, 3, 2)).findings == []


def test_prev_rechecked_when_missing_day_arrives(db_session):
    _imp(db_session, daily("2026-03-02", balance="100"))
    _imp(db_session, daily("2026-03-09", prev="90", balance="90"))

    result = _imp(db_session, daily("2026-03-05", prev="100", balance="90"))

    assert result.findings == []
    row = day_row(db_session, date(2026, 3, 9))
    assert (row.findings, row.findings_sha, row.findings_since) == ([], None, None)


def test_prev_on_late_day_points_at_its_own_predecessor(db_session):
    _imp(db_session, daily("2026-03-02", balance="100"))
    _imp(db_session, daily("2026-03-09", prev="90", balance="90"))

    result = _imp(db_session, daily("2026-03-05", prev="95", balance="80"))

    assert result.findings == [_prev("2026-03-02")]
    assert day_row(db_session, date(2026, 3, 9)).findings == [_prev("2026-03-05")]


def test_first_statement_no_prev(db_session):
    assert _imp(db_session, daily("2026-03-09", prev="500", balance="500")).findings == []


def test_findings_sha():
    assert findings_sha([]) is None
    f = [_prev("2026-03-02")]
    expected = hashlib.sha256(
        json.dumps(f, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    assert findings_sha(f) == expected


# --- monthly: gap / diff / carry ----------------------------------------------------


def test_gap_lists_dates(db_session):
    result = _imp(db_session, monthly("2026-03", b="100", activity=["2026-03-09", "2026-03-02"]))

    assert result.findings == [_gap("2026-03-02", "2026-03-09")]


def test_gap_shrinks_then_clears(db_session):
    """Review Focus 2: the month imported before its dailies."""
    _imp(db_session, monthly("2026-03", b="100", h="5", k="20",
                             activity=["2026-03-02", "2026-03-05", "2026-03-09"]))
    assert month_row(db_session, MAR).findings == [_gap("2026-03-02", "2026-03-05", "2026-03-09")]

    _imp(db_session, daily("2026-03-05", prev="100", realized="20", balance="120"))
    assert month_row(db_session, MAR).findings == [_gap("2026-03-02", "2026-03-09")]
    assert [i["key"] for i in pending(db_session)["items"]] == ["2026-03"]

    _imp(db_session, daily("2026-03-02", prev="0", balance="100"))
    assert month_row(db_session, MAR).findings == [_gap("2026-03-09")]

    _imp(db_session, daily("2026-03-09", prev="120", fees="5", balance="115"))
    row = month_row(db_session, MAR)
    assert (row.findings, row.findings_sha, row.findings_since) == ([], None, None)
    assert pending(db_session) == {"items": [], "total": 0, "truncated": False}


def test_diff_only_without_gap(db_session):
    _imp(db_session, daily("2026-03-02", prev="10", balance="60"))  # deposits 50

    result = _imp(db_session, monthly("2026-03", a="0", b="60", activity=["2026-03-02"]))
    assert result.findings == [{"code": "diff", "dates": [], "letters": ["B"]}]

    with_gap = _imp(db_session, monthly("2026-04", a="60", b="10", activity=["2026-04-07"]))
    assert with_gap.findings == [_gap("2026-04-07")]


def test_diff_letters(db_session):
    _imp(db_session, daily("2026-03-02", prev="0", deposits="100", premium="-3", fees="2", realized="10",
                           balance="105"))
    result = _imp(db_session, monthly("2026-03", b="100", g="-3", h="2", k="11", activity=["2026-03-02"]))

    assert result.findings == [{"code": "diff", "dates": [], "letters": ["K", "M"]}]


def test_carry(db_session):
    _imp(db_session, monthly("2026-01", a="100"))

    assert _imp(db_session, monthly("2026-02", a="90")).findings == [{"code": "carry", "dates": [], "letters": []}]
    assert _imp(db_session, monthly("2026-03", a="90")).findings == []


def test_carry_out_of_order(db_session):
    assert _imp(db_session, monthly("2026-02", a="90")).findings == []

    assert _imp(db_session, monthly("2026-01", a="100")).findings == []

    assert month_row(db_session, date(2026, 2, 1)).findings == [{"code": "carry", "dates": [], "letters": []}]


def test_findings_sorted_by_code(db_session):
    _imp(db_session, monthly("2026-02", a="5"))
    result = _imp(db_session, monthly("2026-03", a="0", activity=["2026-03-02"]))

    assert [f["code"] for f in result.findings] == ["carry", "gap"]


def test_findings_identity_same_count_other_dates(db_session):
    _imp(db_session, monthly("2026-03", b="100", activity=["2026-03-02", "2026-03-03"]))
    _imp(db_session, daily("2026-03-03", balance="100"))
    assert month_row(db_session, MAR).findings == [_gap("2026-03-02")]
    assert acknowledge(db_session, "2026-03", "findings") is True
    assert pending(db_session)["items"] == []
    first_since = month_row(db_session, MAR).findings_since

    # the 03-03 row goes away (test-only edit) and 03-02 arrives: still one gap date, but a different one
    db_session.delete(day_row(db_session, date(2026, 3, 3)))
    db_session.commit()
    _imp(db_session, daily("2026-03-02", balance="100"))

    row = month_row(db_session, MAR)
    assert row.findings == [_gap("2026-03-03")]
    assert row.findings_since > first_since
    assert [(i["key"], i["status"]) for i in pending(db_session)["items"]] == [("2026-03", "findings")]


def test_recompute_findings_direct(db_session):
    _imp(db_session, daily("2026-03-02", balance="100"))
    _imp(db_session, daily("2026-03-09", prev="100", balance="100"))
    row = day_row(db_session, date(2026, 3, 9))
    row.prev_balance = Decimal("1")  # test-only edit
    db_session.commit()

    recompute_findings(db_session, daily=date(2026, 3, 2))
    db_session.commit()

    assert day_row(db_session, date(2026, 3, 9)).findings == [_prev("2026-03-02")]


# --- pending / ack --------------------------------------------------------------


def test_pending_conflict_and_findings_two_items(db_session):
    _imp(db_session, daily("2026-03-02", balance="100"))
    _imp(db_session, daily("2026-03-09", prev="90", balance="90"))
    _imp(db_session, daily("2026-03-09", prev="90", balance="91", sha="b"))

    items = pending(db_session)["items"]
    assert [(i["key"], i["kind"], i["status"], i["reason"]) for i in items] == [
        ("2026-03-09", "day", "conflict", None),
        ("2026-03-09", "day", "findings", "prev:03/02"),
    ]
    row = day_row(db_session, date(2026, 3, 9))
    assert items[0]["since"] == row.since.isoformat()
    assert items[1]["since"] == row.findings_since.isoformat()
    assert items[0]["last_at"] == row.last_at.isoformat()

    assert acknowledge(db_session, "2026-03-09", "conflict") is True
    assert [i["status"] for i in pending(db_session)["items"]] == ["findings"]
    assert acknowledge(db_session, "2026-03-09", "conflict") is False

    assert acknowledge(db_session, "2026-03-09", "findings") is True
    assert pending(db_session)["items"] == []
    row = day_row(db_session, date(2026, 3, 9))
    assert (row.ack_sha256, row.ack_status) == ("b" * 64, "conflict")
    assert row.ack_findings_sha == row.findings_sha


def test_new_conflicting_sha_after_ack_is_pending_again(db_session):
    _imp(db_session, daily("2026-03-09", balance="100"))
    _imp(db_session, daily("2026-03-09", balance="1", sha="b"))
    assert acknowledge(db_session, "2026-03-09", "conflict") is True
    _imp(db_session, daily("2026-03-09", balance="1", sha="b"))
    assert pending(db_session)["items"] == []

    _imp(db_session, daily("2026-03-09", balance="2", sha="c"))

    assert [i["status"] for i in pending(db_session)["items"]] == ["conflict"]


def test_ack_findings_then_identical_recompute_stays_acked(db_session):
    _imp(db_session, monthly("2026-03", b="100", activity=["2026-03-02"]))
    assert acknowledge(db_session, "2026-03", "findings") is True
    since = month_row(db_session, MAR).findings_since

    _imp(db_session, daily("2026-03-20", balance="7"))  # a day of the month that changes nothing
    svc.futures_lock(db_session)
    recompute_findings(db_session, month=MAR)
    db_session.commit()

    row = month_row(db_session, MAR)
    assert row.findings == [_gap("2026-03-02")]
    assert row.findings_since == since
    assert pending(db_session)["items"] == []


def test_ack_404_when_not_pending(db_session):
    assert acknowledge(db_session, "2026-03-09", "conflict") is False
    assert acknowledge(db_session, "2026-03", "findings") is False
    _imp(db_session, daily("2026-03-09", balance="100"))
    assert acknowledge(db_session, "2026-03-09", "conflict") is False
    assert acknowledge(db_session, "2026-03-09", "findings") is False
    _imp(db_session, monthly("2026-03", a="100"))
    assert acknowledge(db_session, "2026-03", "conflict") is False


def _raw_day(db, d: date, findings: list[dict]):
    row = FuturesStatementDay(
        statement_date=d, accepted_sha256="a" * 64, accepted_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
        last_sha256="a" * 64, last_status="imported", findings=findings, findings_sha=findings_sha(findings),
        findings_since=datetime(2026, 4, 1, tzinfo=timezone.utc),
        **{k: Decimal("0") for k in DAY_AMOUNTS},
    )
    db.add(row)


def test_pending_envelope_and_cap(db_session):
    start = date(2026, 1, 1)
    for i in range(51):
        _raw_day(db_session, start + timedelta(days=i), [_prev("2025-12-31")])
    db_session.commit()

    out = pending(db_session)

    assert out["total"] == 51 and out["truncated"] is True and len(out["items"]) == 50
    keys = [i["key"] for i in out["items"]]
    assert keys == sorted(keys) and keys[0] == "2026-01-01" and keys[-1] == "2026-02-19"
    assert set(out["items"][0]) == {"key", "kind", "status", "reason", "since", "last_at"}


def test_reason_codes_and_cut(db_session):
    _raw_day(db_session, date(2026, 3, 9), [_prev("2026-03-02")])
    _raw_day(db_session, date(2026, 3, 10), [
        {"code": "carry", "dates": [], "letters": []},
        {"code": "diff", "dates": [], "letters": ["B", "G", "H", "K", "M"]},
        {"code": "gap", "dates": ["2026-03-02", "2026-03-03"], "letters": []},
        _prev("2026-03-02"),
    ])
    _raw_day(db_session, date(2026, 3, 11), [
        {"code": "diff", "dates": [], "letters": list("BGHKM" * 10)},
    ])
    db_session.commit()

    reasons = [i["reason"] for i in pending(db_session)["items"]]

    assert reasons[0] == "prev:03/02"
    assert reasons[1] == "carry,diff:BGHKM,gap:2,prev:03/02"
    assert len(reasons[2]) == 40 and reasons[2].startswith("diff:BGHKM")


def test_run_report_latest_rows(db_session):
    assert latest_runs(db_session) == {"latest": None, "latest_ok": None}
    t1 = datetime(2026, 3, 10, 6, 45, tzinfo=timezone.utc)
    record_run(db_session, {"run_at": t1, "result": "ok", "code": None, "files_seen": 3, "posted": 2})
    record_run(db_session, {"run_at": t1 + timedelta(days=1), "result": "latched", "code": "grammar",
                            "files_seen": 1, "posted": 0})

    out = latest_runs(db_session)

    assert out["latest"]["result"] == "latched" and out["latest"]["code"] == "grammar"
    assert out["latest_ok"]["result"] == "ok" and out["latest_ok"]["posted"] == 2
    assert out["latest_ok"]["files_seen"] == 3
