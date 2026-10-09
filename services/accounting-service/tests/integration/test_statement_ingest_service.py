from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models import (
    AccountStatement,
    CoverageDirty,
    IngestRun,
    LineLineage,
    ReconciliationCase,
    StatementEvent,
    StatementLine,
    StatementFile,
    StatementSource,
)
from app.schemas.statements import FileRegisterIn, LineIn, RevisionIn, SourceRegisterIn
from app.services import statement_ingest_service as svc
from app.services.errors import ConflictError, ValidationError

SHA = "ab" * 32
MD5 = "cd" * 16


def _line(seq, posted, printed, kind="purchase", merchant="全聯", **kw):
    return LineIn(seq=seq, posted_date=posted, merchant_raw=merchant, printed_amount=Decimal(printed), line_kind=kind, **kw)


def _rev(run, token, account, lines, total, opening=None, **kw):
    base = dict(run_id=run.id, lease_token=token, file_id=None, account_id=account.id, kind="card", parser="test",
                parser_version="1", currency="TWD", period_start=date(2026, 9, 1), period_end=date(2026, 9, 30),
                opening_balance=Decimal(opening) if opening is not None else None, statement_total=Decimal(total), lines=lines, raw={})
    base.update(kw)
    return RevisionIn(**base)


@pytest.fixture
def card(seed, db_session):
    account = seed.account("卡", is_credit=True, statement_live_from=date(2026, 9, 1))
    db_session.commit()
    return account


@pytest.fixture
def run(db_session):
    r = svc.create_worker_run(db_session, trigger="timer", initiator_hint=None, mode="live")
    r, token = svc.claim_run(db_session, r.id, label="worker")
    db_session.commit()
    return r, token


def _file(db_session, r, token, sha=SHA):
    return svc.register_file(db_session, r, FileRegisterIn(run_id=r.id, lease_token=token, sha256=sha, size=10, kind="card",
                                                           object_key=f"inbox/by-sha/{sha}.pdf"))


def _source(db_session, r, token, file_id, path="信用卡/國泰世華/2026-09.pdf", md5=MD5):
    return svc.register_source(db_session, r, SourceRegisterIn(run_id=r.id, lease_token=token, file_id=file_id, root="mail",
                                                               drive_file_id="drive-1", drive_path=path, drive_md5=md5, drive_size=10))


def test_enqueue_coalesces_non_terminal_runs(db_session):
    a = svc.enqueue_run(db_session, principal="hermes")
    b = svc.enqueue_run(db_session, principal="hermes")
    assert a.id == b.id and b.summary["coalesced"][0]["principal"] == "hermes"


def test_enqueue_coalesces_into_a_live_lease_but_never_a_dead_one(db_session):
    queued = svc.enqueue_run(db_session, principal="hermes")
    claimed, _ = svc.claim_run(db_session, queued.id, label="worker")
    assert svc.enqueue_run(db_session, principal="hermes").id == queued.id  # claimed, lease live
    claimed.lease_expires_at = svc._now() - timedelta(minutes=1)
    db_session.flush()
    fresh = svc.enqueue_run(db_session, principal="hermes")
    assert fresh.id != queued.id and fresh.status == "queued" and not fresh.summary.get("coalesced")
    fresh.status = "expired"
    db_session.flush()
    assert svc.enqueue_run(db_session, principal="hermes").id not in (queued.id, fresh.id)


def test_claim_is_atomic_and_fences_submissions(db_session, run):
    r, token = run
    with pytest.raises(ConflictError):
        svc.claim_run(db_session, r.id, label="worker")       # already claimed
    with pytest.raises(ConflictError):
        svc.require_lease(db_session, r.id, "wrong-token", label="worker")
    r.lease_expires_at = r.lease_expires_at - timedelta(hours=1)
    db_session.flush()
    with pytest.raises(ConflictError):
        svc.require_lease(db_session, r.id, token, label="worker")  # expired
    r2, token2 = svc.claim_run(db_session, r.id, label="worker")    # reclaim after expiry
    assert r2.attempt == 2 and token2 != token


def test_lease_moves_to_running_renews_and_finished_run_refuses(db_session, run):
    r, token = run
    assert svc.require_lease(db_session, r.id, token, label="worker").status == "running"
    before = r.lease_expires_at
    assert svc.renew_lease(db_session, r.id, token).lease_expires_at >= before
    svc.finish_run(db_session, r.id, token, status="done", summary={"files": 1})
    db_session.commit()
    assert db_session.get(IngestRun, r.id).summary == {"files": 1} and r.finished_at is not None
    with pytest.raises(ConflictError):
        svc.require_lease(db_session, r.id, token, label="worker")


def test_register_file_is_idempotent_by_sha256(db_session, run):
    r, token = run
    first = _file(db_session, r, token)
    second = _file(db_session, r, token, sha=SHA.upper())
    db_session.commit()
    assert first.id == second.id and first.run_id == r.id and first.status == "new"


def test_register_source_path_change_appends_history(db_session, run):
    r, token = run
    f = _file(db_session, r, token)
    s1 = _source(db_session, r, token, f.id)
    s2 = _source(db_session, r, token, f.id, path="信用卡/國泰世華/renamed.pdf")
    db_session.commit()
    row = db_session.get(StatementSource, s1.id)
    assert s1.id == s2.id and row.drive_path == "信用卡/國泰世華/renamed.pdf"
    assert [h["path"] for h in row.path_history] == ["信用卡/國泰世華/2026-09.pdf"] and "until" in row.path_history[0]
    assert svc.mark_removed_sources(db_session, r, {"drive-1"}) == 0
    assert svc.mark_removed_sources(db_session, r, set()) == 1 and row.removed_at is not None


def test_first_revision_creates_statement_events_and_lines(db_session, card, run):
    r, token = run
    result = svc.submit_revision(db_session, r, _rev(r, token, card, [_line(1, date(2026, 9, 3), "580"), _line(2, date(2026, 9, 10), "-500", kind="payment")], total="80"), account_map={})
    stmt = result.statement
    assert (stmt.mode, stmt.current_revision_id, result.revision.guardrail_ok) == ("live", result.revision.id, True)
    lines = db_session.query(StatementLine).filter_by(revision_id=result.revision.id).order_by(StatementLine.seq).all()
    assert [str(l.flow_amount) for l in lines] == ["-580.0000", "500.0000"]
    assert db_session.query(StatementEvent).filter_by(statement_id=stmt.id).count() == 2
    assert result.lineage_counts == {"identical": 0, "normalised": 0, "changed": 0, "unpaired_old": 0, "new": 2}


def test_reparse_identical_keeps_events_and_current_moves(db_session, card, run):
    r, token = run
    first = svc.submit_revision(db_session, r, _rev(r, token, card, [_line(1, date(2026, 9, 3), "580")], total="580"), account_map={})
    second = svc.submit_revision(db_session, r, _rev(r, token, card, [_line(1, date(2026, 9, 3), "580", merchant="全聯 ")], total="580"), account_map={})
    assert second.lineage_counts["identical"] == 1
    events = db_session.query(StatementEvent).filter_by(statement_id=first.statement.id).all()
    assert len(events) == 1 and events[0].current_line_id != events[0].first_line_id
    assert first.statement.current_revision_id == second.revision.id


def test_merchant_change_pairs_changed_retires_old_event_and_creates_new(db_session, card, run):
    r, token = run
    first = svc.submit_revision(db_session, r, _rev(r, token, card, [_line(1, date(2026, 9, 3), "580", merchant="PAYPAL *Spotify")], total="580"), account_map={})
    old_line = db_session.query(StatementLine).filter_by(revision_id=first.revision.id).one()
    second = svc.submit_revision(db_session, r, _rev(r, token, card, [_line(1, date(2026, 9, 3), "580", merchant="PAYPAL *Netflix")], total="580"), account_map={})
    assert second.lineage_counts == {"identical": 0, "normalised": 0, "changed": 1, "unpaired_old": 0, "new": 0}
    db_session.expire_all()
    new_line = db_session.query(StatementLine).filter_by(revision_id=second.revision.id).one()
    old_event, new_event = db_session.query(StatementEvent).order_by(StatementEvent.id).all()
    assert (old_event.status, old_event.current_line_id) == ("retired", None) and old_line.event_id == old_event.id
    assert (new_event.status, new_event.first_line_id, new_event.current_line_id) == ("live", new_line.id, new_line.id)
    assert new_line.event_id == new_event.id and new_event.first_revision_id == second.revision.id
    row = db_session.query(LineLineage).one()
    assert (row.old_line_id, row.new_line_id, row.equivalence, row.transferred) == (old_line.id, new_line.id, "changed", False)


def test_unpaired_old_event_is_retired_and_new_line_gets_event(db_session, card, run):
    r, token = run
    first = svc.submit_revision(db_session, r, _rev(r, token, card, [_line(1, date(2026, 9, 3), "580")], total="580"), account_map={})
    old_event = db_session.query(StatementEvent).one()
    second = svc.submit_revision(db_session, r, _rev(r, token, card, [_line(1, date(2026, 9, 4), "580")], total="580"), account_map={})
    assert second.lineage_counts == {"identical": 0, "normalised": 0, "changed": 0, "unpaired_old": 1, "new": 1}
    db_session.refresh(old_event)
    assert old_event.status == "retired" and old_event.current_line_id is None
    assert first.statement.current_revision_id == second.revision.id
    new_line = db_session.query(StatementLine).filter_by(revision_id=second.revision.id).one()
    new_event = db_session.get(StatementEvent, new_line.event_id)
    assert new_event.id != old_event.id and new_event.first_line_id == new_event.current_line_id == new_line.id
    assert sorted(l.equivalence for l in db_session.query(LineLineage).all()) == ["unpaired", "unpaired"]


def test_guardrail_failure_stores_revision_opens_parse_review_and_keeps_current(db_session, card, run):
    r, token = run
    first = svc.submit_revision(db_session, r, _rev(r, token, card, [_line(1, date(2026, 9, 3), "580")], total="580"), account_map={})
    bad = svc.submit_revision(db_session, r, _rev(r, token, card, [_line(1, date(2026, 9, 3), "580")], total="581"), account_map={})
    assert bad.revision.guardrail_ok is False and first.statement.current_revision_id == first.revision.id
    cases = db_session.query(ReconciliationCase).filter_by(statement_id=first.statement.id).all()
    assert [c.kind for c in cases] == ["parse_review"] and bad.case_ids == [cases[0].id]
    assert first.statement.open_case_count == 1


def test_first_revision_with_failed_guardrail_becomes_current(db_session, card, run):
    r, token = run
    res = svc.submit_revision(db_session, r, _rev(r, token, card, [_line(1, date(2026, 9, 3), "580")], total="999"), account_map={})
    assert res.statement.current_revision_id == res.revision.id and res.revision.guardrail_ok is False
    assert [c.kind for c in db_session.query(ReconciliationCase).all()] == ["parse_review"]


def test_historical_mode_never_opens_cases(db_session, seed, run):
    r, token = run
    old_card = seed.account("舊卡", is_credit=True)  # statement_live_from = None -> historical
    db_session.commit()
    res = svc.submit_revision(db_session, r, _rev(r, token, old_card, [_line(1, date(2026, 9, 3), "580")], total="999"), account_map={})
    assert res.statement.mode == "historical" and res.revision.guardrail_ok is False and res.case_ids == []


def test_reconciled_statement_gets_conflict_not_new_current(db_session, card, run):
    r, token = run
    first = svc.submit_revision(db_session, r, _rev(r, token, card, [_line(1, date(2026, 9, 3), "580")], total="580"), account_map={})
    first.statement.status = "reconciled"
    db_session.flush()
    second = svc.submit_revision(db_session, r, _rev(r, token, card, [_line(1, date(2026, 9, 3), "581")], total="581"), account_map={})
    assert second.revision.conflict and first.statement.conflict_open and first.statement.current_revision_id == first.revision.id
    assert [c.kind for c in db_session.query(ReconciliationCase).all()] == ["statement_conflict"]
    # A revision that does not become current moves no event: the old one stays live on its line, the new line's
    # event has no current line.
    old_event = db_session.query(StatementEvent).filter_by(first_revision_id=first.revision.id).one()
    new_event = db_session.query(StatementEvent).filter_by(first_revision_id=second.revision.id).one()
    assert (old_event.status, old_event.current_line_id) == ("live", old_event.first_line_id)
    assert new_event.current_line_id is None and new_event.first_line_id is not None
    # ...and that orphan event is born retired, so no live event lacks a current line.
    assert new_event.status == "retired"


def test_kind_must_match_account_and_folder_map(db_session, seed, run, card):
    r, token = run
    bank = seed.account("活存")
    db_session.commit()
    with pytest.raises(ValidationError) as exc:
        svc.submit_revision(db_session, r, _rev(r, token, bank, [_line(1, date(2026, 9, 3), "580")], total="580"), account_map={})
    assert exc.value.field == "kind"
    f = _file(db_session, r, token)
    lines = [_line(1, date(2026, 9, 3), "580")]
    with pytest.raises(ValidationError) as exc:
        svc.submit_revision(db_session, r, _rev(r, token, card, lines, total="580", file_id=f.id), account_map={})
    assert (exc.value.field, exc.value.message) == ("file_id", "file has no source")
    _source(db_session, r, token, f.id)
    with pytest.raises(ValidationError) as exc:
        svc.submit_revision(db_session, r, _rev(r, token, card, lines, total="580", file_id=f.id),
                            account_map={"mail/信用卡/國泰世華": card.id + 1})
    assert (exc.value.field, exc.value.message) == ("account_id", "folder maps elsewhere")
    res = svc.submit_revision(db_session, r, _rev(r, token, card, lines, total="580", file_id=f.id),
                              account_map={"mail/信用卡/國泰世華": card.id})
    assert res.statement.origin == "import" and res.revision.file_id == f.id
    db_session.expire_all()
    assert db_session.get(StatementFile, f.id).account_id == card.id


def test_sign_inconsistent_line_is_422(db_session, card, run):
    r, token = run
    with pytest.raises(ValidationError) as exc:
        svc.submit_revision(db_session, r, _rev(r, token, card, [_line(1, date(2026, 9, 3), "-580")], total="580"), account_map={})
    assert exc.value.field == "lines"


def test_get_and_list_statements(db_session, card, run):
    r, token = run
    res = svc.submit_revision(db_session, r, _rev(r, token, card, [_line(1, date(2026, 9, 3), "580")], total="999"), account_map={})
    db_session.commit()
    detail = svc.get_statement(db_session, res.statement.id)
    assert detail["statement_total"] == Decimal("999") and detail["stale_events_pending"] is False
    assert [(l["seq"], l["flow_amount"], l["merchant_norm"]) for l in detail["lines"]] == [(1, Decimal("-580"), "全聯")]
    assert detail["lines"][0]["event_id"] == db_session.query(StatementEvent).one().id
    assert [c["kind"] for c in detail["cases"]] == ["parse_review"]
    db_session.add(CoverageDirty(kind="entry", row_id=1, op="insert", new_account_id=card.id))
    db_session.flush()
    assert svc.get_statement(db_session, res.statement.id)["stale_events_pending"] is True
    assert [s.id for s in svc.list_statements(db_session, card.id)] == [res.statement.id]
    assert db_session.query(AccountStatement).count() == 1


def test_first_revision_with_identical_twins_opens_no_case(db_session, card, run):
    r, token = run
    twins = [_line(1, date(2026, 9, 3), "100"), _line(2, date(2026, 9, 3), "100")]
    res = svc.submit_revision(db_session, r, _rev(r, token, card, twins, total="200"), account_map={})
    assert res.case_ids == [] and res.revision.guardrail["twins_changed"] is False
    assert db_session.query(ReconciliationCase).count() == 0 and res.statement.current_revision_id == res.revision.id


def test_corrected_revision_replaces_failed_current_and_supersedes_parse_review(db_session, card, run):
    r, token = run
    bad = svc.submit_revision(db_session, r, _rev(r, token, card, [_line(1, date(2026, 9, 3), "580")], total="999"), account_map={})
    fixed = svc.submit_revision(db_session, r, _rev(r, token, card, [_line(1, date(2026, 9, 3), "580")], total="580"), account_map={})
    assert fixed.revision.guardrail_ok and fixed.case_ids == []
    assert bad.statement.current_revision_id == fixed.revision.id and bad.statement.statement_total == Decimal("580")
    cases = db_session.query(ReconciliationCase).all()
    assert [(c.kind, c.status, c.version) for c in cases] == [("parse_review", "superseded", 2)]
    assert bad.statement.open_case_count == 0
    event = db_session.query(StatementEvent).one()
    assert event.current_line_id != event.first_line_id


def test_claim_does_not_expire_a_live_lease(db_session, run):
    r, token = run
    with pytest.raises(ConflictError):
        svc.claim_run(db_session, r.id, label="other")
    db_session.expire_all()
    row = db_session.get(IngestRun, r.id)
    assert (row.status, row.attempt, row.lease_token) == ("claimed", 1, token)
