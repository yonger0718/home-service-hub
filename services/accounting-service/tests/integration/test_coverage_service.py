from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models import StatementCoverage, StatementLine
from app.schemas.statements import LineIn, RevisionIn
from app.services import coverage_service as cov
from app.services import statement_ingest_service as ing
from app.services.errors import CodedConflictError, ConflictError
from app.services.statements import matching


@pytest.fixture
def ctx(seed, db_session):
    card = seed.account("卡", is_credit=True, statement_live_from=date(2026, 9, 1))
    db_session.commit()
    r = ing.create_worker_run(db_session, trigger="timer", initiator_hint=None, mode="live")
    r, token = ing.claim_run(db_session, r.id, label="worker")
    lines = [LineIn(seq=i, posted_date=date(2026, 9, 3), merchant_raw="全聯", printed_amount=Decimal("580"), line_kind="purchase") for i in (1, 2)]
    rev = RevisionIn(run_id=r.id, lease_token=token, file_id=None, account_id=card.id, kind="card", parser="t", parser_version="1",
                     currency="TWD", period_start=date(2026, 9, 1), period_end=date(2026, 9, 30), opening_balance=None,
                     statement_total=Decimal("1160"), lines=lines, raw={})
    from app.services import statement_revision_service as revs
    res = revs.submit_revision(db_session, r, rev, account_map={})
    db_session.flush()
    ls = db_session.query(StatementLine).filter_by(revision_id=res.revision.id).order_by(StatementLine.seq).all()
    return res.statement, ls, card, r


def _rows(entries, role="principal"):
    return [matching.Row(e.id, role, Decimal(e.amount), e.group_id) for e in entries]


def _claim(db, stmt, line, entries, run=None, **kw):
    return cov.write_claim(db, stmt, line, _rows(entries), entries_by_id={e.id: e for e in entries},
                           match_kind="auto", match_rule="exact", run_id=run.id if run else None, **kw)


def test_conserved_claim_stores_rows_snapshots_roles(ctx, seed, db_session):
    stmt, (l1, _), card, run = ctx
    g = seed.group()
    a = seed.entry(card, "-300", group_id=g.id)
    b = seed.entry(card, "-280", group_id=g.id)
    db_session.flush(); db_session.refresh(a); db_session.refresh(b)
    out = cov.write_claim(db_session, stmt, l1, [matching.Row(a.id, "principal", Decimal("-300"), g.id), matching.Row(b.id, "member", Decimal("-280"), g.id)],
                          entries_by_id={a.id: a, b.id: b}, match_kind="auto", match_rule="group", run_id=run.id)
    assert [c.role for c in out] == ["principal", "member"]
    assert all(c.event_id == l1.event_id and c.group_id == g.id and c.status == "active" for c in out)
    s = out[0].snapshot
    assert s["amount"] == "-300.0000" and s["flow"] == s["amount"] and s["currency"] == "TWD"
    assert s["id"] == a.id and s["account_id"] == card.id and s["group_id"] == g.id and s["parent_entry_id"] is None
    assert isinstance(s["entry_date"], str) and isinstance(s["posted_date"], str)
    assert set(s) >= {"original_amount", "original_currency", "kind", "transfer_group_id", "refunds_entry_id", "settles_entry_id", "is_settlement", "name", "merchant"}


def test_non_conserved_claim_raises(ctx, seed, db_session):
    stmt, (l1, _), card, _ = ctx
    a = seed.entry(card, "-500")
    db_session.flush()
    with pytest.raises(ConflictError, match="not conserved"):
        _claim(db_session, stmt, l1, [a])
    assert db_session.query(StatementCoverage).count() == 0


def test_snapshot_drift_raises(ctx, seed, db_session):
    stmt, (l1, _), card, _ = ctx
    a = seed.entry(card, "-580")
    db_session.flush()
    with pytest.raises(ConflictError, match="drift"):
        cov.write_claim(db_session, stmt, l1, [matching.Row(a.id, "principal", Decimal("-580.5"), None), matching.Row(a.id, "child", Decimal("0.5"), None)],
                        entries_by_id={a.id: a}, match_kind="auto", match_rule="x", run_id=None)


def test_duplicate_claim_raises_coded_and_session_stays_usable(ctx, seed, db_session):
    stmt, (l1, l2), card, _ = ctx
    a = seed.entry(card, "-580")
    db_session.flush()
    _claim(db_session, stmt, l1, [a])
    with pytest.raises(CodedConflictError) as ei:
        _claim(db_session, stmt, l2, [a])
    assert str(ei.value).startswith("duplicate_claim") and ei.value.code == "duplicate_claim"
    other = seed.entry(card, "-580", day=date(2026, 9, 4))
    db_session.flush()
    _claim(db_session, stmt, l2, [other])
    db_session.flush()
    assert db_session.query(StatementCoverage).count() == 2


def test_release_line_marks_stale_and_frees_entry(ctx, seed, db_session):
    stmt, (l1, l2), card, _ = ctx
    a = seed.entry(card, "-580")
    db_session.flush()
    _claim(db_session, stmt, l1, [a])
    assert cov.release_line(db_session, stmt, l1.id, reason="x" * 100) == 1
    row = db_session.query(StatementCoverage).one()
    assert row.status == "stale" and row.stale_reason == "x" * 64
    assert cov.release_line(db_session, stmt, l1.id, reason="again") == 0
    _claim(db_session, stmt, l2, [a])


def test_release_statement_and_active_rows(ctx, seed, db_session):
    stmt, (l1, l2), card, _ = ctx
    a, b = seed.entry(card, "-580"), seed.entry(card, "-580", day=date(2026, 9, 4))
    db_session.flush()
    _claim(db_session, stmt, l1, [a])
    _claim(db_session, stmt, l2, [b])
    act = cov.active_rows(db_session, stmt.id)
    assert set(act) == {l1.id, l2.id} and len(act[l1.id]) == 1
    assert cov.release_statement(db_session, stmt, reason="reparse") == 2
    assert cov.active_rows(db_session, stmt.id) == {}


def test_claimed_entry_ids_with_and_without_exclusion(ctx, seed, db_session):
    stmt, (l1, _), card, _ = ctx
    a = seed.entry(card, "-580")
    db_session.flush()
    _claim(db_session, stmt, l1, [a])
    assert cov.claimed_entry_ids(db_session) == {a.id}
    assert cov.claimed_entry_ids(db_session, exclude_statement_id=stmt.id) == set()
    assert cov.claimed_entry_ids(db_session, exclude_statement_id=stmt.id + 999) == {a.id}


def test_assert_conserved_passes_then_fails_on_tamper(ctx, seed, db_session):
    stmt, (l1, _), card, _ = ctx
    a = seed.entry(card, "-580")
    db_session.flush()
    _claim(db_session, stmt, l1, [a])
    cov.assert_conserved(db_session, stmt)
    db_session.execute(text("update statement_coverage set snapshot = jsonb_set(snapshot, '{flow}', '\"-1\"')"))
    db_session.expire_all()
    with pytest.raises(ConflictError, match=f"line {l1.id}"):
        cov.assert_conserved(db_session, stmt)


def test_assert_conserved_is_null_safe(ctx, seed, db_session):
    stmt, (l1, _), card, _ = ctx
    a = seed.entry(card, "-580")
    db_session.flush()
    _claim(db_session, stmt, l1, [a])
    db_session.execute(text("update statement_coverage set snapshot = jsonb_set(snapshot, '{flow}', 'null')"))
    db_session.expire_all()
    with pytest.raises(ConflictError, match=f"line {l1.id}"):
        cov.assert_conserved(db_session, stmt)


def test_snapshot_amounts_are_quantised_to_four_places(ctx, seed, db_session):
    stmt, (l1, _), card, _ = ctx
    a = seed.entry(card, "-580", original_amount=Decimal("-20.5"), original_currency="USD")
    db_session.flush()  # not refreshed: the Python-side Decimals are unquantised
    snap = _claim(db_session, stmt, l1, [a])[0].snapshot
    assert (snap["amount"], snap["flow"], snap["original_amount"]) == ("-580.0000", "-580.0000", "-20.5000")


def test_duplicate_claim_across_statements_raises_coded(ctx, seed, db_session):
    stmt, (l1, _), card, _ = ctx
    a = seed.entry(card, "-580")
    db_session.flush()
    _claim(db_session, stmt, l1, [a])
    r = ing.create_worker_run(db_session, trigger="timer", initiator_hint=None, mode="live")
    r, token = ing.claim_run(db_session, r.id, label="worker")
    from app.services import statement_revision_service as revs
    rev = RevisionIn(run_id=r.id, lease_token=token, file_id=None, account_id=card.id, kind="card", parser="t",
                     parser_version="1", currency="TWD", period_start=date(2026, 10, 1), period_end=date(2026, 10, 31),
                     opening_balance=None, statement_total=Decimal("580"), raw={},
                     lines=[LineIn(seq=1, posted_date=date(2026, 10, 3), merchant_raw="全聯",
                                   printed_amount=Decimal("580"), line_kind="purchase")])
    res = revs.submit_revision(db_session, r, rev, account_map={})
    db_session.flush()
    other_line = db_session.query(StatementLine).filter_by(revision_id=res.revision.id).one()
    assert res.statement.id != stmt.id
    with pytest.raises(CodedConflictError) as ei:
        _claim(db_session, res.statement, other_line, [a])
    assert ei.value.code == "duplicate_claim" and f"entry {a.id}" in str(ei.value)
    assert cov.claimed_entry_ids(db_session) == {a.id}


def test_other_integrity_errors_pass_through(ctx, seed, db_session):
    stmt, (l1, _), card, _ = ctx
    a = seed.entry(card, "-580")
    db_session.flush()
    with pytest.raises(IntegrityError) as ei:
        cov.write_claim(db_session, stmt, l1, _rows([a]), entries_by_id={a.id: a}, match_kind="auto",
                        match_rule="exact", run_id=987654)
    assert "ux_statement_coverage_active_entry" not in str(ei.value.orig)
    assert db_session.query(StatementCoverage).count() == 0  # the savepoint rolled back; the session is usable
