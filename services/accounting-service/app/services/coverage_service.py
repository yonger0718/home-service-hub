"""Statement coverage (spec §4.6): claims, release, conservation. Never commits; callers own the transaction."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AccountStatement, LedgerEntry, StatementCoverage, StatementLine
from app.services.errors import CodedConflictError, ConflictError
from app.services.statements import matching

_Q = Decimal("0.0001")


def _s(value) -> str | None:
    return None if value is None else str(value)


def snapshot_entry(entry: LedgerEntry) -> dict:
    return {
        "amount": _s(entry.amount), "flow": _s(entry.amount), "currency": entry.currency,
        "original_amount": _s(entry.original_amount), "original_currency": entry.original_currency,
        "entry_date": _s(entry.entry_date), "posted_date": _s(entry.posted_date),
        "account_id": entry.account_id, "kind": _s(entry.kind), "group_id": entry.group_id,
        "parent_entry_id": entry.parent_entry_id, "transfer_group_id": _s(entry.transfer_group_id),
        "refunds_entry_id": entry.refunds_entry_id, "settles_entry_id": entry.settles_entry_id,
        "is_settlement": entry.is_settlement, "name": entry.name, "merchant": entry.merchant,
    }


def write_claim(db: Session, statement: AccountStatement, line: StatementLine, rows: list[matching.Row], *,
                entries_by_id: dict[int, LedgerEntry], match_kind: str, match_rule: str,
                run_id: int | None) -> list[StatementCoverage]:
    if not matching.conserved(tuple(rows), line.flow_amount):
        raise ConflictError("coverage not conserved")
    out: list[StatementCoverage] = []
    for row in rows:
        snap = snapshot_entry(entries_by_id[row.entry_id])
        if Decimal(row.flow).quantize(_Q) != Decimal(snap["amount"]).quantize(_Q):
            raise ConflictError("coverage snapshot drift")
        out.append(StatementCoverage(
            statement_id=statement.id, event_id=line.event_id, line_id=line.id, entry_id=row.entry_id,
            group_id=row.group_id, role=row.role, snapshot=snap, match_kind=match_kind,
            match_rule=match_rule, status="active", run_id=run_id))
    try:
        with db.begin_nested():
            db.add_all(out)
            db.flush()
    except IntegrityError as exc:
        if "ux_statement_coverage_active_entry" not in str(exc.orig):
            raise
        claimed = claimed_entry_ids(db)
        dup = next((r.entry_id for r in rows if r.entry_id in claimed), rows[0].entry_id)
        raise CodedConflictError("duplicate_claim", f"entry {dup}") from exc
    return out


def _release(db: Session, conds: list, reason: str) -> int:
    rows = db.execute(select(StatementCoverage).where(StatementCoverage.status == "active", *conds)).scalars().all()
    for r in rows:
        r.status = "stale"
        r.stale_reason = reason[:64]
    db.flush()
    return len(rows)


def release_line(db: Session, statement: AccountStatement, line_id: int, *, reason: str) -> int:
    return _release(db, [StatementCoverage.statement_id == statement.id, StatementCoverage.line_id == line_id], reason)


def release_statement(db: Session, statement: AccountStatement, *, reason: str) -> int:
    return _release(db, [StatementCoverage.statement_id == statement.id], reason)


def active_rows(db: Session, statement_id: int) -> dict[int, list[StatementCoverage]]:
    out: dict[int, list[StatementCoverage]] = defaultdict(list)
    q = select(StatementCoverage).where(StatementCoverage.statement_id == statement_id, StatementCoverage.status == "active")
    for r in db.execute(q.order_by(StatementCoverage.id)).scalars():
        out[r.line_id].append(r)
    return dict(out)


def claimed_entry_ids(db: Session, *, exclude_statement_id: int | None = None) -> set[int]:
    q = select(StatementCoverage.entry_id).where(StatementCoverage.status == "active", StatementCoverage.entry_id.is_not(None))
    if exclude_statement_id is not None:
        q = q.where(StatementCoverage.statement_id != exclude_statement_id)
    return set(db.execute(q).scalars())


def assert_conserved(db: Session, statement: AccountStatement) -> None:
    for line_id, rows in active_rows(db, statement.id).items():
        line = db.get(StatementLine, line_id)
        total = sum((Decimal(r.snapshot["flow"]) for r in rows), Decimal(0))
        if total.quantize(_Q) != line.flow_amount.quantize(_Q):
            raise ConflictError(f"coverage not conserved for line {line_id}")
