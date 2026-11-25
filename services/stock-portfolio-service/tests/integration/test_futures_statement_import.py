"""永豐期貨 statement import (spec §6.2), the snapshot refresh (§6.5) and the archive validation batch (§7)."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from pydantic import TypeAdapter
from sqlalchemy.orm.session import Session, SessionTransaction

from app.models.portfolio_snapshot import PortfolioSnapshot
from app.models.sinopac_futures_statement import (
    DAY_AMOUNTS,
    MONTH_AMOUNTS,
    FuturesStatementDay,
    FuturesStatementMonth,
)
from app.schemas.sinopac_futures_statement import FuturesPayload
from app.services import portfolio_snapshot_service
from app.services import sinopac_futures_statement_service as svc
from app.services.sinopac_futures_statement_service import import_statement, validate_archive

_ADAPTER = TypeAdapter(FuturesPayload)
TODAY = date(2026, 4, 30)


def daily(d: str, *, prev="0", balance="0", deposits=None, realized="0", fees="0", premium="0",
          sha="a", file_id="drive-day-1"):
    """A daily payload whose identities hold; ``deposits`` defaults to whatever closes the balance identity."""
    prev, balance, realized, fees, premium = map(Decimal, (prev, balance, realized, fees, premium))
    deposits = Decimal(deposits) if deposits is not None else balance - prev + fees - realized - premium
    assert balance == prev + deposits - fees + realized + premium
    equity = {k: "0" for k in DAY_AMOUNTS}
    equity.update(
        prev_balance=str(prev), deposits_net=str(deposits), fees_and_tax=str(fees), futures_realized=str(realized),
        premium_net=str(premium), balance=str(balance), equity=str(balance), total_equity=str(balance),
    )
    return _ADAPTER.validate_python({
        "kind": "daily",
        "source": {"drive_file_id": file_id, "sha256": sha * 64, "file_name": f"{d}.pdf"},
        "statement_date": d, "equity": equity, "deposits": [], "fills": [], "fills_total": None,
        "positions": [], "position_lines": {}, "warnings": [],
    })


def monthly(month: str, *, a="0", b="0", g="0", h="0", k="0", activity=(), sha="e", open_positions=0):
    year, mon = int(month[:4]), int(month[5:])
    month_end = (date(year + mon // 12, mon % 12 + 1, 1) - date.resolution).isoformat()
    a, b, g, h, k = map(Decimal, (a, b, g, h, k))
    m = a + b + g - h + k
    lines = {key.upper() if len(key) == 1 else key: "0" for key in MONTH_AMOUNTS}
    lines.update(A=str(a), B=str(b), G=str(g), H=str(h), K=str(k), M=str(m), Q=str(m), total_equity=str(m))
    return _ADAPTER.validate_python({
        "kind": "monthly",
        "source": {"drive_file_id": f"drive-{month}", "sha256": sha * 64, "file_name": f"{month}.pdf"},
        "month": month, "month_end": month_end, "lines": lines, "activity_dates": list(activity),
        "open_positions": open_positions, "warnings": [],
    })


def snap(db, d: date, futures="0"):
    db.add(PortfolioSnapshot(
        date=d, total_market_value=Decimal("1000"), total_cost=Decimal("900"), total_unrealized_pnl=Decimal("100"),
        total_dividends=Decimal("0"), total_realized_pnl=Decimal("0"), total_cash_twd=Decimal("50"),
        futures_equity_twd=Decimal(futures), portfolio_xirr=None,
    ))
    db.commit()


def snapshots(db) -> dict[date, Decimal]:
    db.expire_all()
    return {r.date: r.futures_equity_twd for r in db.query(PortfolioSnapshot).order_by(PortfolioSnapshot.date)}


def day_row(db, d: date) -> FuturesStatementDay | None:
    db.expire_all()
    return db.get(FuturesStatementDay, d)


def month_row(db, m: date) -> FuturesStatementMonth | None:
    db.expire_all()
    return db.get(FuturesStatementMonth, m)


def accepted_columns(row) -> dict:
    skip = {"last_sha256", "last_status", "last_detail", "last_at", "since"}
    return {c.key: getattr(row, c.key) for c in row.__table__.columns if c.key not in skip}


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    monkeypatch.setattr(portfolio_snapshot_service, "_today_tw", lambda: TODAY)


# --- §6.2 cases -------------------------------------------------------------


def test_daily_imported(db_session):
    result = import_statement(db_session, daily("2026-03-09", prev="100", balance="150"), dry_run=False)

    assert (result.outcome, result.key, result.findings, result.errors) == ("imported", "2026-03-09", [], [])
    row = day_row(db_session, date(2026, 3, 9))
    assert row.accepted_sha256 == "a" * 64 and row.accepted_at is not None
    assert row.drive_file_id == "drive-day-1"
    assert (row.prev_balance, row.balance, row.total_equity) == (Decimal("100"), Decimal("150"), Decimal("150"))
    assert (row.last_status, row.last_sha256) == ("imported", "a" * 64)
    assert row.since is not None and row.last_at is not None
    assert row.fills == [] and row.fills_total is None and row.open_positions == 0
    assert row.findings == [] and row.findings_sha is None and row.findings_since is None


def test_same_sha_duplicate_writes_nothing(db_session):
    import_statement(db_session, daily("2026-03-09", balance="150"), dry_run=False)
    before = accepted_columns(day_row(db_session, date(2026, 3, 9)))
    attempt = day_row(db_session, date(2026, 3, 9))
    attempt = (attempt.last_status, attempt.last_at, attempt.since)

    result = import_statement(db_session, daily("2026-03-09", balance="150", file_id="drive-other"),
                              dry_run=False)

    assert (result.outcome, result.key, result.errors) == ("duplicate", "2026-03-09", [])
    row = day_row(db_session, date(2026, 3, 9))
    assert accepted_columns(row) == before
    assert (row.last_status, row.last_at, row.since) == attempt


def test_other_sha_conflict_keeps_row(db_session):
    import_statement(db_session, daily("2026-03-09", balance="150"), dry_run=False)
    before = accepted_columns(day_row(db_session, date(2026, 3, 9)))

    result = import_statement(db_session, daily("2026-03-09", balance="999", sha="b", file_id="drive-day-2"),
                              dry_run=False)

    assert (result.outcome, result.key, result.errors) == ("conflict", "2026-03-09", [])
    row = day_row(db_session, date(2026, 3, 9))
    assert accepted_columns(row) == before
    assert (row.last_status, row.last_sha256) == ("conflict", "b" * 64)
    assert row.last_detail["accepted_sha256"] == "a" * 64
    assert row.last_detail["statement_sha256"] == "b" * 64
    assert row.last_detail["drive_file_id"] == "drive-day-2"


def test_conflict_since_kept_on_repeat(db_session, monkeypatch):
    clock = iter(datetime(2026, 3, 10, h, tzinfo=timezone.utc) for h in range(1, 10))
    monkeypatch.setattr(svc, "_now", lambda: next(clock))
    import_statement(db_session, daily("2026-03-09", balance="150"), dry_run=False)
    import_statement(db_session, daily("2026-03-09", balance="9", sha="b"), dry_run=False)
    first = day_row(db_session, date(2026, 3, 9)).since

    import_statement(db_session, daily("2026-03-09", balance="9", sha="b"), dry_run=False)
    row = day_row(db_session, date(2026, 3, 9))
    assert row.since == first and row.last_at > first

    import_statement(db_session, daily("2026-03-09", balance="8", sha="c"), dry_run=False)
    row = day_row(db_session, date(2026, 3, 9))
    assert row.since > first and row.last_sha256 == "c" * 64


def test_monthly_imported(db_session):
    payload = monthly("2026-03", a="100", b="50", k="10", activity=["2026-03-02"], open_positions=2)

    result = import_statement(db_session, payload, dry_run=False)

    assert (result.outcome, result.key, result.errors) == ("imported", "2026-03", [])
    row = month_row(db_session, date(2026, 3, 1))
    assert row.month_end == date(2026, 3, 31)
    assert (row.a, row.b, row.k, row.m, row.total_equity) == tuple(map(Decimal, ("100", "50", "10", "160", "160")))
    assert row.activity_dates == ["2026-03-02"] and row.open_positions == 2
    assert row.last_status == "imported" and row.accepted_sha256 == "e" * 64
    assert result.findings == [{"code": "gap", "dates": ["2026-03-02"], "letters": []}]


def test_monthly_duplicate_and_conflict(db_session):
    import_statement(db_session, monthly("2026-03", a="100"), dry_run=False)
    before = accepted_columns(month_row(db_session, date(2026, 3, 1)))

    assert import_statement(db_session, monthly("2026-03", a="100"), dry_run=False).outcome == "duplicate"
    assert import_statement(db_session, monthly("2026-03", a="7", sha="f"), dry_run=False).outcome == "conflict"
    row = month_row(db_session, date(2026, 3, 1))
    assert accepted_columns(row) == before
    assert (row.last_status, row.last_sha256) == ("conflict", "f" * 64)


def test_dry_run_writes_nothing(db_session):
    snap(db_session, date(2026, 3, 10))
    import_statement(db_session, daily("2026-03-02", balance="100"), dry_run=False)
    before = snapshots(db_session)

    result = import_statement(db_session, daily("2026-03-09", prev="90", balance="90"), dry_run=True)

    assert result.outcome == "imported"
    assert result.findings == [{"code": "prev", "dates": ["2026-03-02"], "letters": []}]
    assert day_row(db_session, date(2026, 3, 9)) is None
    assert snapshots(db_session) == before
    assert import_statement(db_session, daily("2026-03-02", balance="100"), dry_run=True).outcome == "duplicate"
    dry_conflict = import_statement(db_session, daily("2026-03-02", balance="1", sha="d"), dry_run=True)
    assert dry_conflict.outcome == "conflict"
    assert day_row(db_session, date(2026, 3, 2)).last_status == "imported"


def test_failure_after_refresh_rolls_back(db_session, monkeypatch):
    snap(db_session, date(2026, 3, 10))
    real = portfolio_snapshot_service.refresh_snapshot_futures_range

    def refresh_then_fail(db, start, end):
        real(db, start, end)
        db.flush()
        raise ValueError("refresh failed")

    monkeypatch.setattr(portfolio_snapshot_service, "refresh_snapshot_futures_range", refresh_then_fail)

    result = import_statement(db_session, daily("2026-03-09", balance="150"), dry_run=False)

    assert result.outcome == "imported" and result.errors == ["refresh failed"]
    assert day_row(db_session, date(2026, 3, 9)) is None
    assert snapshots(db_session) == {date(2026, 3, 10): Decimal("0")}


def test_refresh_updates_existing_rows_only(db_session):
    snap(db_session, date(2026, 3, 5))
    snap(db_session, date(2026, 3, 7))

    import_statement(db_session, daily("2026-03-06", balance="150"), dry_run=False)

    assert snapshots(db_session) == {date(2026, 3, 5): Decimal("0"), date(2026, 3, 7): Decimal("150")}


def test_refresh_stops_before_next_source(db_session):
    import_statement(db_session, daily("2026-03-10", prev="150", balance="200"), dry_run=False)
    for d in (date(2026, 3, 7), date(2026, 3, 9), date(2026, 3, 10)):
        snap(db_session, d, futures="999")

    import_statement(db_session, daily("2026-03-06", balance="150", sha="b"), dry_run=False)

    assert snapshots(db_session) == {
        date(2026, 3, 7): Decimal("150"), date(2026, 3, 9): Decimal("150"), date(2026, 3, 10): Decimal("999"),
    }


def test_monthly_refresh_starts_at_month_end(db_session):
    for d in (date(2026, 3, 30), date(2026, 3, 31), date(2026, 4, 2)):
        snap(db_session, d)

    import_statement(db_session, monthly("2026-03", a="100"), dry_run=False)

    assert snapshots(db_session) == {
        date(2026, 3, 30): Decimal("0"), date(2026, 3, 31): Decimal("100"), date(2026, 4, 2): Decimal("100"),
    }


def test_refresh_function_returns_rows_touched(db_session):
    snap(db_session, date(2026, 3, 5))
    snap(db_session, date(2026, 3, 7))
    import_statement(db_session, daily("2026-03-06", balance="150"), dry_run=False)

    touched = portfolio_snapshot_service.refresh_snapshot_futures_range(
        db_session, date(2026, 3, 1), date(2026, 3, 31)
    )

    assert touched == 2
    assert db_session.query(PortfolioSnapshot).count() == 2


def test_refresh_does_not_commit(db_session, monkeypatch):
    """``with db.begin()`` commits through ``SessionTransaction.commit``, which ``Session.commit`` also goes through,
    so counting it catches the import's one commit and any commit from inside the refresh."""
    snap(db_session, date(2026, 3, 7))
    tx_commits: list[int] = []
    session_commits: list[int] = []
    real_tx_commit, real_session_commit = SessionTransaction.commit, Session.commit

    def tx_commit(self, *args, **kwargs):
        if self.parent is None:
            tx_commits.append(1)
        return real_tx_commit(self, *args, **kwargs)

    def session_commit(self):
        session_commits.append(1)
        return real_session_commit(self)

    monkeypatch.setattr(SessionTransaction, "commit", tx_commit)
    monkeypatch.setattr(Session, "commit", session_commit)

    result = import_statement(db_session, daily("2026-03-06", balance="150"), dry_run=False)

    assert result.outcome == "imported"
    assert tx_commits == [1]
    assert session_commits == []
    assert snapshots(db_session)[date(2026, 3, 7)] == Decimal("150")


# --- archive validation (§7 --validate-history) -----------------------------


def test_validate_archive_empty_db_multi_file(db_session):
    for d in (1, 2, 9, 31):
        snap(db_session, date(2026, 3, d))
    before = snapshots(db_session)
    payloads = [
        daily("2026-03-02", balance="100"),
        daily("2026-03-09", prev="90", balance="90"),
        monthly("2026-03", a="0", b="90", activity=["2026-03-02", "2026-03-05", "2026-03-09"]),
        daily("2026-03-02", balance="100"),
        daily("2026-03-09", prev="90", balance="91", sha="b"),
    ]

    result = validate_archive(db_session, payloads)

    assert result["items"] == [
        {"file_name": "2026-03-02.pdf", "key": "2026-03-02", "outcome": "imported"},
        {"file_name": "2026-03-09.pdf", "key": "2026-03-09", "outcome": "imported"},
        {"file_name": "2026-03.pdf", "key": "2026-03", "outcome": "imported"},
        {"file_name": "2026-03-02.pdf", "key": "2026-03-02", "outcome": "duplicate"},
        {"file_name": "2026-03-09.pdf", "key": "2026-03-09", "outcome": "conflict"},
    ]
    assert result["findings"] == {
        "2026-03-02": [],
        "2026-03-09": [{"code": "prev", "dates": ["2026-03-02"], "letters": []}],
        "2026-03": [{"code": "gap", "dates": ["2026-03-05"], "letters": []}],
    }
    db_session.expire_all()
    assert db_session.query(FuturesStatementDay).count() == 0
    assert db_session.query(FuturesStatementMonth).count() == 0
    assert snapshots(db_session) == before


def test_validate_archive_orders_dailies_then_monthlies(db_session):
    """Posted month first and dailies out of order: the batch still imports 03-02, 03-09, then the month, so the
    month sees every daily row and the 03-09 row sees its predecessor."""
    payloads = [
        monthly("2026-03", b="100", activity=["2026-03-02", "2026-03-09"]),
        daily("2026-03-09", prev="100", balance="100"),
        daily("2026-03-02", balance="100"),
    ]

    result = validate_archive(db_session, payloads)

    assert [i["outcome"] for i in result["items"]] == ["imported"] * 3
    assert result["findings"] == {"2026-03-02": [], "2026-03-09": [], "2026-03": []}


def test_validate_archive_carry(db_session):
    payloads = [monthly("2026-02", a="0"), monthly("2026-01", a="80")]

    result = validate_archive(db_session, payloads)

    assert result["findings"] == {
        "2026-01": [],
        "2026-02": [{"code": "carry", "dates": [], "letters": []}],
    }


def test_validate_archive_sees_existing_rows_and_leaves_them(db_session):
    import_statement(db_session, daily("2026-03-02", balance="100"), dry_run=False)
    import_statement(db_session, monthly("2026-03", b="100", activity=["2026-03-02", "2026-03-09"]), dry_run=False)
    snap(db_session, date(2026, 3, 9), futures="5")
    day_before = accepted_columns(day_row(db_session, date(2026, 3, 2)))
    month_before = month_row(db_session, date(2026, 3, 1))
    month_before = (accepted_columns(month_before), month_before.last_status, month_before.last_at)
    assert month_before[0]["findings"] == [{"code": "gap", "dates": ["2026-03-09"], "letters": []}]

    result = validate_archive(db_session, [
        daily("2026-03-09", prev="90", balance="90"),
        daily("2026-03-02", balance="100"),
        monthly("2026-03", b="100", sha="f"),
    ])

    assert [i["outcome"] for i in result["items"]] == ["imported", "duplicate", "conflict"]
    assert result["findings"] == {
        "2026-03-02": [],
        "2026-03-09": [{"code": "prev", "dates": ["2026-03-02"], "letters": []}],
        "2026-03": [{"code": "diff", "dates": [], "letters": ["M"]}],
    }
    assert accepted_columns(day_row(db_session, date(2026, 3, 2))) == day_before
    month_after = month_row(db_session, date(2026, 3, 1))
    assert (accepted_columns(month_after), month_after.last_status, month_after.last_at) == month_before
    assert day_row(db_session, date(2026, 3, 9)) is None
    assert snapshots(db_session) == {date(2026, 3, 9): Decimal("5")}
