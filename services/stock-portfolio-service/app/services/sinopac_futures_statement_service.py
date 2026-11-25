"""永豐期貨對帳單: write-once import (spec §6.2), findings (§6.4), snapshot refresh (§6.5), archive validation (§7),
pending items, acknowledgements and run reports (§8).

Every row in ``futures_statement_day`` / ``futures_statement_month`` is accepted; a duplicate writes nothing and a
conflict only updates the attempt columns of the accepted row.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..models.sinopac_futures_statement import (
    DAY_AMOUNTS,
    MONTH_AMOUNTS,
    FuturesStatementDay,
    FuturesStatementMonth,
    FuturesStatementRun,
)
from ..schemas.sinopac_futures_statement import DailyPayload, MonthlyPayload
from . import portfolio_snapshot_service
from .sinopac_futures_series import futures_lock

Outcome = Literal["imported", "duplicate", "conflict"]
Row = FuturesStatementDay | FuturesStatementMonth

PENDING_LIMIT = 50
PENDING_REASON_CHARS = 40
DIFF_LETTERS = ("b", "g", "h", "k")  # month line → summed daily equity field
_DAILY_SUM = {"b": "deposits_net", "g": "premium_net", "h": "fees_and_tax", "k": "futures_realized"}


@dataclass
class FuturesOutcome:
    """Result of one statement post. ``errors`` is non-empty only when the commit failed and was rolled back."""

    outcome: Outcome
    key: str
    findings: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- keys --------------------------------------------------------------------------


def _first_of(month: str) -> date:
    return date(int(month[:4]), int(month[5:7]), 1)


def _month_key(month: date) -> str:
    return f"{month.year:04d}-{month.month:02d}"


def _next_month(month: date) -> date:
    return date(month.year + month.month // 12, month.month % 12 + 1, 1)


def _prev_month(month: date) -> date:
    return date(month.year - 1, 12, 1) if month.month == 1 else date(month.year, month.month - 1, 1)


def _payload_key(payload: DailyPayload | MonthlyPayload) -> str:
    if isinstance(payload, DailyPayload):
        return payload.statement_date.isoformat()
    return payload.month


def _row_key(row: Row) -> str:
    if isinstance(row, FuturesStatementDay):
        return row.statement_date.isoformat()
    return _month_key(row.month)


def _get_row(db: Session, payload: DailyPayload | MonthlyPayload, *, fresh: bool) -> Row | None:
    if isinstance(payload, DailyPayload):
        return db.get(FuturesStatementDay, payload.statement_date, populate_existing=fresh)
    return db.get(FuturesStatementMonth, _first_of(payload.month), populate_existing=fresh)


# --- findings (§6.4) -----------------------------------------------------------------


def findings_sha(findings: list[dict]) -> str | None:
    if not findings:
        return None
    canonical = json.dumps(findings, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _finding(code: str, dates=(), letters=()) -> dict:
    return {"code": code, "dates": list(dates), "letters": list(letters)}


def _day_findings(db: Session, row: FuturesStatementDay) -> list[dict]:
    """``prev``: the latest accepted daily row before this one ends on another balance."""
    pred = db.scalars(
        select(FuturesStatementDay)
        .where(FuturesStatementDay.statement_date < row.statement_date)
        .order_by(FuturesStatementDay.statement_date.desc())
        .limit(1)
    ).first()
    if pred is not None and Decimal(row.prev_balance) != Decimal(pred.balance):
        return [_finding("prev", [pred.statement_date.isoformat()])]
    return []


def _month_findings(db: Session, row: FuturesStatementMonth) -> list[dict]:
    days = db.scalars(
        select(FuturesStatementDay)
        .where(FuturesStatementDay.statement_date >= row.month, FuturesStatementDay.statement_date <= row.month_end)
        .order_by(FuturesStatementDay.statement_date)
    ).all()
    have = {d.statement_date.isoformat() for d in days}
    findings: list[dict] = []

    previous = db.get(FuturesStatementMonth, _prev_month(row.month))
    if previous is not None and Decimal(row.a) != Decimal(previous.m):
        findings.append(_finding("carry"))

    gap = sorted({str(d) for d in row.activity_dates or []} - have)
    if gap:
        findings.append(_finding("gap", gap))
    else:
        letters = [
            letter.upper()
            for letter in DIFF_LETTERS
            if Decimal(getattr(row, letter)) != sum((Decimal(getattr(d, _DAILY_SUM[letter])) for d in days), Decimal(0))
        ]
        # M against the month's last daily balance; a month without a daily row has nothing to compare it with.
        if days and Decimal(row.m) != Decimal(days[-1].balance):
            letters.append("M")
        if letters:
            findings.append(_finding("diff", letters=letters))
    return sorted(findings, key=lambda f: f["code"])


def _store_findings(row: Row, findings: list[dict], now: datetime) -> None:
    sha = findings_sha(findings)
    if sha != row.findings_sha:
        row.findings_sha = sha
        row.findings_since = now if sha is not None else None
    if row.findings != findings:
        row.findings = findings


def recompute_findings(db: Session, *, daily: date | None = None, month: date | None = None) -> None:
    """The §6.4 recompute set, inside the caller's transaction under the exclusive futures lock.

    ``daily`` = D: D, the next accepted daily row after D (its ``prev``), and D's month. ``month`` (first of month)
    = M: M and the next month (its ``carry``). Flushes; never commits.
    """
    now = _now()
    if daily is not None:
        day = db.get(FuturesStatementDay, daily)
        if day is not None:
            _store_findings(day, _day_findings(db, day), now)
        following = db.scalars(
            select(FuturesStatementDay)
            .where(FuturesStatementDay.statement_date > daily)
            .order_by(FuturesStatementDay.statement_date)
            .limit(1)
        ).first()
        if following is not None:
            _store_findings(following, _day_findings(db, following), now)
        own_month = db.get(FuturesStatementMonth, daily.replace(day=1))
        if own_month is not None:
            _store_findings(own_month, _month_findings(db, own_month), now)
    if month is not None:
        for m in (month, _next_month(month)):
            row = db.get(FuturesStatementMonth, m)
            if row is not None:
                _store_findings(row, _month_findings(db, row), now)
    db.flush()


def _findings_for(db: Session, row: Row) -> list[dict]:
    if isinstance(row, FuturesStatementDay):
        return _day_findings(db, row)
    return _month_findings(db, row)


# --- rows ----------------------------------------------------------------------------


def _new_row(payload: DailyPayload | MonthlyPayload, now: datetime) -> Row:
    common = dict(
        accepted_sha256=payload.source.sha256, accepted_at=now, drive_file_id=payload.source.drive_file_id,
        last_sha256=payload.source.sha256, last_status="imported", last_detail=None, last_at=now, since=now,
        findings=[], findings_sha=None, findings_since=None,
    )
    if isinstance(payload, DailyPayload):
        dumped = payload.model_dump(mode="json")
        return FuturesStatementDay(
            statement_date=payload.statement_date,
            deposits=dumped["deposits"], fills=dumped["fills"], fills_total=dumped["fills_total"],
            positions=dumped["positions"], position_lines=dumped["position_lines"],
            open_positions=len(payload.positions),
            **{name: getattr(payload.equity, name) for name in DAY_AMOUNTS},
            **common,
        )
    lines = payload.lines
    return FuturesStatementMonth(
        month=_first_of(payload.month), month_end=payload.month_end,
        activity_dates=sorted(d.isoformat() for d in payload.activity_dates),
        open_positions=payload.open_positions,
        **{name: getattr(lines, name.upper() if len(name) == 1 else name) for name in MONTH_AMOUNTS},
        **common,
    )


def _decide(row: Row | None, payload: DailyPayload | MonthlyPayload) -> Outcome:
    if row is None:
        return "imported"
    return "duplicate" if row.accepted_sha256 == payload.source.sha256 else "conflict"


def _record_conflict(row: Row, payload: DailyPayload | MonthlyPayload, now: datetime) -> None:
    """Only the attempt columns change; ``since`` is kept while the same conflicting sha repeats."""
    sha = payload.source.sha256
    if row.last_status != "conflict" or row.last_sha256 != sha:
        row.since = now
    row.last_sha256 = sha
    row.last_status = "conflict"
    row.last_detail = {
        "accepted_sha256": row.accepted_sha256,
        "statement_sha256": sha,
        "drive_file_id": payload.source.drive_file_id,
        "file_name": payload.source.file_name,
    }
    row.last_at = now


def _source_date(row: Row) -> date:
    return row.statement_date if isinstance(row, FuturesStatementDay) else row.month_end


def _next_source_date(db: Session, after: date) -> date | None:
    day = db.scalar(
        select(FuturesStatementDay.statement_date)
        .where(FuturesStatementDay.statement_date > after)
        .order_by(FuturesStatementDay.statement_date)
        .limit(1)
    )
    month_end = db.scalar(
        select(FuturesStatementMonth.month_end)
        .where(FuturesStatementMonth.month_end > after)
        .order_by(FuturesStatementMonth.month_end)
        .limit(1)
    )
    found = [d for d in (day, month_end) if d is not None]
    return min(found) if found else None


def _apply(db: Session, payload: DailyPayload | MonthlyPayload) -> FuturesOutcome:
    """All §6.2 work for one statement inside the caller's transaction, with the futures lock already held.

    Never begins, commits or rolls back; flushes so later reads in the same transaction see the writes.
    """
    key = _payload_key(payload)
    row = _get_row(db, payload, fresh=True)
    outcome = _decide(row, payload)
    now = _now()
    if outcome == "conflict":
        _record_conflict(row, payload, now)
        db.flush()
    elif outcome == "imported":
        row = _new_row(payload, now)
        db.add(row)
        db.flush()
        if isinstance(row, FuturesStatementDay):
            recompute_findings(db, daily=row.statement_date)
        else:
            recompute_findings(db, month=row.month)
        start = _source_date(row)
        following = _next_source_date(db, start)
        end = following - timedelta(days=1) if following is not None else portfolio_snapshot_service._today_tw()
        portfolio_snapshot_service.refresh_snapshot_futures_range(db, start, end)
        db.flush()
    return FuturesOutcome(outcome=outcome, key=key, findings=list(row.findings or []))


# --- import / validation ---------------------------------------------------------------


def import_statement(db: Session, payload: DailyPayload | MonthlyPayload, *, dry_run: bool) -> FuturesOutcome:
    """Apply one statement (§6.2). A dry run takes no lock and writes nothing; its findings are those the statement
    would get against the current rows (the stored ones for a duplicate or conflict)."""
    key = _payload_key(payload)
    if dry_run:
        row = _get_row(db, payload, fresh=False)
        outcome = _decide(row, payload)
        if outcome == "imported":
            return FuturesOutcome(outcome, key, _findings_for(db, _new_row(payload, _now())))
        return FuturesOutcome(outcome, key, list(row.findings or []))

    if db.in_transaction():
        db.rollback()
    outcome: Outcome = "imported"
    try:
        with db.begin():
            futures_lock(db)
            row = _get_row(db, payload, fresh=True)
            outcome = _decide(row, payload)
            result = _apply(db, payload)
    except (ValueError, SQLAlchemyError) as exc:
        db.rollback()
        return FuturesOutcome(outcome, key, [], [str(exc)])
    return result


def _batch_order(item: tuple[int, DailyPayload | MonthlyPayload]) -> tuple:
    _, payload = item
    return (0, payload.statement_date.isoformat()) if isinstance(payload, DailyPayload) else (1, payload.month)


def validate_archive(db: Session, payloads: list[DailyPayload | MonthlyPayload]) -> dict:
    """Import every payload in one transaction under the futures lock (dailies by date, then monthlies), collect the
    final findings of every row, then roll back: nothing persists (§7 ``--validate-history``).

    ``items`` keep the posted order; ``findings`` cover every row the batch ends with, existing rows included.
    """
    if db.in_transaction():
        db.rollback()
    outcomes: dict[int, str] = {}
    db.begin()
    try:
        futures_lock(db)
        for index, payload in sorted(enumerate(payloads), key=_batch_order):
            outcomes[index] = _apply(db, payload).outcome
        rows: list[Row] = [
            *db.scalars(select(FuturesStatementDay).order_by(FuturesStatementDay.statement_date)),
            *db.scalars(select(FuturesStatementMonth).order_by(FuturesStatementMonth.month)),
        ]
        result = {
            "items": [
                {"file_name": p.source.file_name, "key": _payload_key(p), "outcome": outcomes[i]}
                for i, p in enumerate(payloads)
            ],
            "findings": {_row_key(r): [dict(f) for f in (r.findings or [])] for r in rows},
        }
    finally:
        db.rollback()
    return result


# --- pending / ack (§8) -----------------------------------------------------------------


def _conflict_pending(row: Row) -> bool:
    return row.last_status == "conflict" and not (
        row.ack_status == "conflict" and row.ack_sha256 == row.last_sha256
    )


def _findings_pending(row: Row) -> bool:
    return row.findings_sha is not None and row.ack_findings_sha != row.findings_sha


def findings_reason(findings: list[dict]) -> str:
    parts = []
    for f in findings:
        code = f["code"]
        if code == "prev":
            parts.append(f"prev:{f['dates'][0][5:7]}/{f['dates'][0][8:10]}")
        elif code == "gap":
            parts.append(f"gap:{len(f['dates'])}")
        elif code == "diff":
            parts.append(f"diff:{''.join(f['letters'])}")
        else:
            parts.append(code)
    return ",".join(parts)[:PENDING_REASON_CHARS]


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def pending(db: Session) -> dict:
    """One item per unacknowledged incident: a conflict, and / or a findings set (§8)."""
    candidates = or_(
        FuturesStatementDay.last_status == "conflict", FuturesStatementDay.findings_sha.is_not(None)
    )
    month_candidates = or_(
        FuturesStatementMonth.last_status == "conflict", FuturesStatementMonth.findings_sha.is_not(None)
    )
    rows: list[tuple[Row, str]] = [
        *((r, "day") for r in db.scalars(select(FuturesStatementDay).where(candidates))),
        *((r, "month") for r in db.scalars(select(FuturesStatementMonth).where(month_candidates))),
    ]
    items = []
    for row, kind in rows:
        key = _row_key(row)
        if _conflict_pending(row):
            items.append({"key": key, "kind": kind, "status": "conflict", "reason": None,
                          "since": _iso(row.since), "last_at": _iso(row.last_at)})
        if _findings_pending(row):
            items.append({"key": key, "kind": kind, "status": "findings", "reason": findings_reason(row.findings),
                          "since": _iso(row.findings_since), "last_at": _iso(row.last_at)})
    items.sort(key=lambda i: (i["key"], i["status"]))
    return {"items": items[:PENDING_LIMIT], "total": len(items), "truncated": len(items) > PENDING_LIMIT}


def _row_for_key(db: Session, key: str) -> Row | None:
    if len(key) == 10:
        return db.get(FuturesStatementDay, date.fromisoformat(key), populate_existing=True)
    if len(key) == 7:
        return db.get(FuturesStatementMonth, _first_of(key), populate_existing=True)
    raise ValueError(f"bad key: {key!r}")


def acknowledge(db: Session, key: str, status: Literal["conflict", "findings"]) -> bool:
    """Acknowledge one pending incident of ``key`` (a date or ``YYYY-MM``); False when it is not pending."""
    if status not in ("conflict", "findings"):
        raise ValueError(f"bad status: {status!r}")
    if db.in_transaction():
        db.rollback()
    with db.begin():
        futures_lock(db)
        row = _row_for_key(db, key)
        if row is None:
            return False
        if status == "conflict":
            if not _conflict_pending(row):
                return False
            row.ack_sha256 = row.last_sha256
            row.ack_status = "conflict"
        else:
            if not _findings_pending(row):
                return False
            row.ack_findings_sha = row.findings_sha
        row.ack_at = _now()
    return True


# --- run reports ---------------------------------------------------------------------------


def _upsert_run(db: Session, kind: str, report: dict) -> None:
    row = db.scalars(select(FuturesStatementRun).where(FuturesStatementRun.kind == kind)).one_or_none()
    if row is None:
        row = FuturesStatementRun(kind=kind)
        db.add(row)
    for name, value in report.items():
        setattr(row, name, value)


def record_run(db: Session, report: dict) -> None:
    """Upsert the ``latest`` run, and the ``ok`` run when the run succeeded."""
    _upsert_run(db, "latest", report)
    if report["result"] == "ok":
        _upsert_run(db, "ok", report)
    db.commit()


def latest_runs(db: Session) -> dict:
    rows = {r.kind: r for r in db.scalars(select(FuturesStatementRun))}

    def view(row: FuturesStatementRun | None) -> dict | None:
        if row is None:
            return None
        return {
            "run_at": row.run_at.isoformat(), "result": row.result, "code": row.code,
            "files_seen": row.files_seen, "posted": row.posted,
        }

    return {"latest": view(rows.get("latest")), "latest_ok": view(rows.get("ok"))}
