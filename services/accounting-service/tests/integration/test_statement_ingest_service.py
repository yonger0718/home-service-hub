from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models import (
    AccountStatement,
    CoverageDirty,
    IngestRun,
    LineLineage,
    ReconciliationCase,
    StatementCoverage,
    StatementEvent,
    StatementLine,
    StatementFile,
    StatementSource,
)
from app.schemas.statements import FileRegisterIn, LineIn, RevisionIn, SourceRegisterIn
from app.services import statement_ingest_service as svc
from app.services import statement_revision_service as revs
from app.services.errors import ConflictError, ValidationError

# These tests isolate their service: submit_revision runs without the reconcile pass (conftest.matcher_off).
pytestmark = pytest.mark.usefixtures("matcher_off")

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
    assert [s["id"] for s in svc.list_statements(db_session, card.id)] == [res.statement.id]
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


# --- same file -> same statement (fix 2026-10-17: a re-parse with another period must not create a second statement) ---
FOLDER = "mail/信用卡/國泰世華"
OCT = (date(2026, 10, 1), date(2026, 10, 31))


def _filed(db_session, r, token, sha=SHA, md5=MD5):
    f = _file(db_session, r, token, sha=sha)
    _source(db_session, r, token, f.id, md5=md5)
    return f


def _statements(db_session, account):
    db_session.expire_all()
    return db_session.query(AccountStatement).filter_by(account_id=account.id).order_by(AccountStatement.id).all()


def _event_ids(db_session, revision_id):
    return [l.event_id for l in db_session.query(StatementLine).filter_by(revision_id=revision_id).order_by(StatementLine.seq)]


def test_same_file_with_another_period_is_a_revision_of_its_statement(db_session, card, run):
    r, token = run
    f = _filed(db_session, r, token)
    amap = {FOLDER: card.id}
    lines = [_line(1, date(2026, 9, 3), "580"), _line(2, date(2026, 9, 9), "120")]
    first = svc.submit_revision(db_session, r, _rev(r, token, card, lines, total="700", file_id=f.id), account_map=amap)
    # the same file parsed again, its due date read as the closing date: another period, the same lines
    second = svc.submit_revision(db_session, r, _rev(r, token, card, lines, total="700", file_id=f.id,
                                                     period_start=OCT[0], period_end=OCT[1]), account_map=amap)
    assert [s.id for s in _statements(db_session, card)] == [first.statement.id]
    stmt = _statements(db_session, card)[0]
    assert (second.statement.id, second.revision.revision) == (stmt.id, 2)
    # the header change is surfaced the existing way: not current, one parse_review naming the changed fields
    assert (stmt.current_revision_id, stmt.period_start, stmt.period_end) == (first.revision.id, date(2026, 9, 1), date(2026, 9, 30))
    changes = {"period_start": ["2026-09-01", "2026-10-01"], "period_end": ["2026-09-30", "2026-10-31"]}
    assert second.revision.guardrail["header_changes"] == changes
    (case,) = db_session.query(ReconciliationCase).filter_by(statement_id=stmt.id).all()
    assert (case.kind, case.status, case.revision_id, case.context["header_changes"]) == (
        "parse_review", "open", second.revision.id, changes)
    assert second.case_ids == [case.id]
    # the lines ride their events: no event was created for the second revision
    assert _event_ids(db_session, second.revision.id) == _event_ids(db_session, first.revision.id)
    assert second.lineage_counts == {"identical": 2, "normalised": 0, "changed": 0, "unpaired_old": 0, "new": 0}
    assert db_session.query(StatementEvent).filter_by(statement_id=stmt.id).count() == 2
    # another file for that period is another statement (no regression)
    other = _filed(db_session, r, token, sha="ef" * 32, md5="01" * 16)
    third = svc.submit_revision(db_session, r, _rev(r, token, card, lines, total="700", file_id=other.id,
                                                    period_start=OCT[0], period_end=OCT[1]), account_map=amap)
    assert [s.id for s in _statements(db_session, card)] == [stmt.id, third.statement.id]
    assert (third.revision.revision, third.statement.period_end) == (1, OCT[1])


def test_same_file_correction_moves_a_failed_statement_to_the_parsed_period(db_session, card, run):
    r, token = run
    f = _filed(db_session, r, token)
    amap = {FOLDER: card.id}
    lines = [_line(1, date(2026, 9, 3), "580")]
    wrong = svc.submit_revision(db_session, r, _rev(r, token, card, lines, total="999", file_id=f.id,
                                                    period_start=OCT[0], period_end=OCT[1]), account_map=amap)
    assert wrong.revision.guardrail_ok is False and wrong.statement.period_end == OCT[1]
    fixed = svc.submit_revision(db_session, r, _rev(r, token, card, lines, total="580", file_id=f.id), account_map=amap)
    (stmt,) = _statements(db_session, card)
    assert (fixed.statement.id, fixed.revision.revision, stmt.current_revision_id) == (stmt.id, 2, fixed.revision.id)
    assert (stmt.period_start, stmt.period_end, stmt.statement_total) == (date(2026, 9, 1), date(2026, 9, 30), Decimal("580"))
    assert fixed.revision.guardrail["header_changes"]["period_end"] == ["2026-10-31", "2026-09-30"]
    assert [(c.kind, c.status) for c in db_session.query(ReconciliationCase).all()] == [("parse_review", "superseded")]
    assert _event_ids(db_session, fixed.revision.id) == _event_ids(db_session, wrong.revision.id)
    (event,) = db_session.query(StatementEvent).all()
    assert (event.status, event.current_line_id) == ("live", db_session.query(StatementLine).filter_by(
        revision_id=fixed.revision.id).one().id)


def _stale_then_fixed(db, run, account, *, closing_day, wrong_end, fixed_end):
    """A statement parsed before the account had a closing day (the misread period passed every check of its day),
    then the closing day set and the same file re-parsed with the corrected period."""
    r, token = run
    f = _filed(db, r, token)
    amap = {FOLDER: account.id}
    lines = [_line(1, date(2026, 9, 3), "580")]
    start = date(2026, 8, 7)  # on the cycle (the day after the 6th): only `wrong_end` decides staleness here
    wrong = svc.submit_revision(db, r, _rev(r, token, account, lines, total="580", file_id=f.id,
                                            period_start=start, period_end=wrong_end), account_map=amap)
    db.commit()
    assert wrong.revision.guardrail_ok is True and wrong.statement.period_end == wrong_end
    account.closing_day = closing_day
    db.commit()
    fixed = svc.submit_revision(db, r, _rev(r, token, account, lines, total="580", file_id=f.id,
                                            period_start=start, period_end=fixed_end), account_map=amap)
    db.commit()
    return wrong, fixed, db.get(AccountStatement, wrong.statement.id)


def test_a_current_revision_failing_todays_closing_day_check_is_replaced_by_the_corrected_period(db_session, seed, run):
    """Fix 2026-10-17 (stale current): the historical 2025 HSBC statements were parsed with the payment due date as
    the closing date before the closing-day check existed; the hint now parses them right, but a header change on
    a historical statement opens nothing and the old revision passed its guardrails. A current revision that fails
    today's closing-day check is treated as failed, so the passing revision is a correction."""
    old_card = seed.account("舊卡", is_credit=True)  # no statement_live_from: historical
    db_session.commit()
    wrong, fixed, stmt = _stale_then_fixed(db_session, run, old_card, closing_day=6, wrong_end=date(2026, 9, 24),
                                           fixed_end=date(2026, 9, 6))
    assert stmt.mode == "historical"
    assert (fixed.statement.id, stmt.current_revision_id, stmt.period_end) == (stmt.id, fixed.revision.id, date(2026, 9, 6))
    assert fixed.revision.guardrail["header_changes"]["period_end"] == ["2026-09-24", "2026-09-06"]
    assert fixed.revision.guardrail["stale_current"] is True
    assert db_session.query(ReconciliationCase).count() == 0  # historical: still no case, and none needed


def test_stale_neighbours_do_not_block_each_others_corrected_periods(db_session, seed, run):
    """Two adjacent months both read with the due date as the closing date overlap each other; once the closing
    day is set, correcting either one must not be blocked by the other's stale period (review AGENT-145 focus 3),
    or the chain never resolves. The identity clause (same period_end) still blocks."""
    r, token = run
    old_card = seed.account("舊卡", is_credit=True)
    db_session.commit()
    fa, fb, la, lb, a, b = _two_misread_months(db_session, seed, run, old_card, b_period=(date(2026, 9, 5), date(2026, 10, 24)))
    fixed_a = _correct_a(db_session, run, old_card, fa, la)
    sa = db_session.get(AccountStatement, a.statement.id)
    assert (sa.current_revision_id, sa.period_start, sa.period_end) == (fixed_a.revision.id, date(2026, 8, 7), date(2026, 9, 6))
    assert "period_blocked_by" not in fixed_a.revision.guardrail and fixed_a.revision.guardrail["stale_current"] is True
    fixed_b = svc.submit_revision(db_session, r, _rev(r, token, old_card, lb, total="50", file_id=fb.id,
                                                      period_start=date(2026, 9, 7), period_end=date(2026, 10, 6)), account_map={FOLDER: old_card.id})
    db_session.commit()
    sb = db_session.get(AccountStatement, b.statement.id)
    assert (sb.current_revision_id, sb.period_end) == (fixed_b.revision.id, date(2026, 10, 6))
    # the identity clause is never bypassed: a period_end another statement holds still blocks
    assert revs.period_blocker(db_session, sa, date(2026, 9, 7), date(2026, 10, 6), account=old_card) == sb.id


def _two_misread_months(db, seed, run, account, *, b_period, b_closing_date=None, b_line_day=date(2026, 9, 20)):
    """A (08-19..09-24) and B (`b_period`, its line on `b_line_day`) on two files, parsed before the closing day (6)
    was set; both pass the guardrails of their day."""
    r, token = run
    amap = {FOLDER: account.id}
    fa, fb = _filed(db, r, token), _filed(db, r, token, sha="bb" * 32, md5="bb" * 16)
    la, lb = [_line(1, date(2026, 8, 20), "580")], [_line(1, b_line_day, "50")]
    a = svc.submit_revision(db, r, _rev(r, token, account, la, total="580", file_id=fa.id,
                                        period_start=date(2026, 8, 19), period_end=date(2026, 9, 24)), account_map=amap)
    b = svc.submit_revision(db, r, _rev(r, token, account, lb, total="50", file_id=fb.id, period_start=b_period[0],
                                        period_end=b_period[1], closing_date=b_closing_date), account_map=amap)
    db.commit()
    account.closing_day = 6
    db.commit()
    return fa, fb, la, lb, a, b


def _correct_a(db, run, account, fa, la):
    r, token = run
    fixed = svc.submit_revision(db, r, _rev(r, token, account, la, total="580", file_id=fa.id,
                                            period_start=date(2026, 8, 7), period_end=date(2026, 9, 6)), account_map={FOLDER: account.id})
    db.commit()
    return fixed


def test_a_neighbour_stale_only_by_its_closing_date_still_blocks(db_session, seed, run):
    """Review AGENT-145 M2: B's period 08-10..09-09 passes today's check (+3); only its closing_date is off. B's
    period will not move, so A's corrected period (overlapping 08-10..09-06) must stay blocked."""
    old_card = seed.account("舊卡", is_credit=True)
    db_session.commit()
    fa, fb, la, lb, a, b = _two_misread_months(db_session, seed, run, old_card, b_period=(date(2026, 8, 10), date(2026, 9, 9)),
                                               b_closing_date=date(2026, 9, 24), b_line_day=date(2026, 9, 1))
    assert b.revision.guardrail_ok is True  # B passed its day's checks; only its closing_date is off today
    fixed = _correct_a(db_session, run, old_card, fa, la)
    sa = db_session.get(AccountStatement, a.statement.id)
    assert fixed.revision.guardrail_ok is True and fixed.revision.guardrail["period_blocked_by"] == b.statement.id
    assert (sa.current_revision_id, sa.period_end) == (a.revision.id, date(2026, 9, 24))


def test_a_reconciled_stale_neighbour_still_blocks(db_session, seed, run):
    """A reconciled neighbour's revisions go through the conflict gate and never move on their own."""
    old_card = seed.account("舊卡", is_credit=True)
    db_session.commit()
    fa, fb, la, lb, a, b = _two_misread_months(db_session, seed, run, old_card, b_period=(date(2026, 9, 5), date(2026, 10, 24)))
    db_session.get(AccountStatement, b.statement.id).status = "reconciled"
    db_session.commit()
    fixed = _correct_a(db_session, run, old_card, fa, la)
    sa = db_session.get(AccountStatement, a.statement.id)
    assert fixed.revision.guardrail["period_blocked_by"] == b.statement.id
    assert (sa.current_revision_id, sa.period_end) == (a.revision.id, date(2026, 9, 24))


def test_a_stale_neighbour_still_blocks_the_same_period_end(db_session, seed, run):
    """Identity is never bypassed: a stale neighbour holding the corrected period_end blocks (DB regression)."""
    old_card = seed.account("舊卡", is_credit=True)
    db_session.commit()
    # B is stale (period_end 10-24 is 18 days off the 6th); A's "correction" lands exactly on B's period_end
    fa, fb, la, lb, a, b = _two_misread_months(db_session, seed, run, old_card, b_period=(date(2026, 9, 5), date(2026, 10, 24)))
    assert b.statement.id != a.statement.id
    r, token = run
    fixed = svc.submit_revision(db_session, r, _rev(r, token, old_card, [_line(1, date(2026, 10, 10), "580")], total="580", file_id=fa.id,
                                                    period_start=date(2026, 9, 25), period_end=date(2026, 10, 24)), account_map={FOLDER: old_card.id})
    db_session.commit()
    assert fixed.revision.guardrail["period_blocked_by"] == b.statement.id
    assert db_session.get(AccountStatement, a.statement.id).current_revision_id == a.revision.id


def test_skipping_a_stale_neighbour_still_hits_a_non_stale_one(db_session, seed, run):
    """The scan continues past a stale neighbour: a second, non-stale overlapping neighbour blocks (DB regression)."""
    old_card = seed.account("舊卡", is_credit=True)
    db_session.commit()
    fa, fb, la, lb, a, b = _two_misread_months(db_session, seed, run, old_card, b_period=(date(2026, 9, 5), date(2026, 10, 24)))
    r, token = run
    fc = _filed(db_session, r, token, sha="cc" * 32, md5="cc" * 16)
    c = svc.submit_revision(db_session, r, _rev(r, token, old_card, [_line(1, date(2026, 8, 20), "70")], total="70", file_id=fc.id,
                                                period_start=date(2026, 7, 7), period_end=date(2026, 8, 8)), account_map={FOLDER: old_card.id})
    db_session.commit()  # C ends 08-08: +2 off the 6th, not stale; it overlaps A's corrected 08-07..09-06 on 08-07/08
    fixed = _correct_a(db_session, run, old_card, fa, la)
    assert fixed.revision.guardrail["period_blocked_by"] == c.statement.id  # B (stale) skipped, C blocks
    assert db_session.get(AccountStatement, a.statement.id).current_revision_id == a.revision.id


def test_stale_neighbours_resolve_on_a_live_card_too(db_session, card, run):
    fa, fb, la, lb, a, b = _two_misread_months(db_session, None, run, card, b_period=(date(2026, 9, 5), date(2026, 10, 24)))
    assert db_session.get(AccountStatement, a.statement.id).mode == "live"
    fixed = _correct_a(db_session, run, card, fa, la)
    sa = db_session.get(AccountStatement, a.statement.id)
    assert (sa.current_revision_id, sa.period_end) == (fixed.revision.id, date(2026, 9, 6))
    assert _parse_reviews(db_session, sa.id) == []  # a correction, not a header-change review


@pytest.mark.parametrize("kind, closing_day, period_end, closing_date, stale", [
    ("card", 6, date(2026, 9, 9), None, False),          # +3: within tolerance
    ("card", 6, date(2026, 9, 3), None, False),          # -3
    ("card", 6, date(2026, 9, 10), None, True),          # +4
    ("card", 6, date(2026, 9, 2), None, True),           # -4
    ("card", 6, date(2026, 9, 6), date(2026, 9, 12), True),   # closing_date alone is off
    ("card", 31, date(2026, 9, 30), None, False),        # day 31 clamps to the month's last day
    ("bank", 6, date(2026, 9, 24), None, False),         # banks never
    ("card", None, date(2026, 9, 24), None, False),      # no closing day: never
])
def test_stale_revision_boundaries(kind, closing_day, period_end, closing_date, stale):
    account = SimpleNamespace(closing_day=closing_day)
    start = date(2026, 8, 7) if closing_day == 6 else date(2026, 9, 1)  # on the cycle: the day after a closing date
    revision = SimpleNamespace(period_start=start, period_end=period_end, closing_date=closing_date)
    db = SimpleNamespace(get=lambda model, pk: revision)
    assert revs._stale_revision(db, kind, account, 1) is stale
    assert revs._stale_revision(db, kind, account, None) is False


@pytest.mark.parametrize("period_end, closing_date, status, stale", [
    (date(2026, 9, 24), None, "open", True),             # period itself off: will move
    (date(2026, 9, 9), date(2026, 9, 24), "open", False),  # closing_date alone off: period stays (M2)
    (date(2026, 9, 24), None, "reconciled", False),      # reconciled: never moves on its own
])
def test_stale_period_boundaries(period_end, closing_date, status, stale):
    account = SimpleNamespace(closing_day=6)
    revision = SimpleNamespace(period_start=date(2026, 8, 7), period_end=period_end, closing_date=closing_date)
    db = SimpleNamespace(get=lambda model, pk: revision)
    assert revs._stale_period(db, "card", account, 1, status) is stale


@pytest.mark.parametrize("closing_day, period_start, period_end, stale", [
    (6, date(2026, 8, 7), date(2026, 9, 6), False),    # the day after the 6th: on the cycle
    (6, date(2026, 8, 10), date(2026, 9, 6), False),   # +3
    (6, date(2026, 8, 4), date(2026, 9, 6), False),    # -3
    (6, date(2026, 8, 11), date(2026, 9, 6), True),    # +4
    (6, date(2026, 8, 3), date(2026, 9, 6), True),     # -4
    (6, date(2026, 8, 1), date(2026, 9, 6), True),     # a week early (production: 07-01 for a 07-07 start)
    (31, date(2026, 3, 1), date(2026, 3, 31), False),  # previous day 02-28: day 31 clamps to February's end
    (31, date(2028, 3, 1), date(2028, 3, 31), False),  # leap year: 02-29
    (31, date(2026, 5, 1), date(2026, 5, 31), False),  # 04-30
    (31, date(2026, 1, 1), date(2026, 1, 31), False),  # 12-31 of the previous year
    (31, date(2026, 3, 5), date(2026, 3, 31), True),   # 03-04 is +4 from 02-28
])
def test_stale_period_start_boundaries(closing_day, period_start, period_end, stale):
    """Fix 2026-10-17: a period_start whose previous day is off the cycle makes the period stale too — both for the
    current revision (correction) and for a neighbour (bypass); month ends and year ends clamp like `closing_offset`."""
    account = SimpleNamespace(closing_day=closing_day)
    revision = SimpleNamespace(period_start=period_start, period_end=period_end, closing_date=None)
    db = SimpleNamespace(get=lambda model, pk: revision)
    assert revs._stale_revision(db, "card", account, 1) is stale
    assert revs._stale_period(db, "card", account, 1, "open") is stale
    assert revs._stale_period(db, "card", account, 1, "reconciled") is False  # reconciled: never a bypass


STALE_FIRST = (date(2026, 9, 1), date(2026, 9, 30))   # parsed before the closing day (6) was set: 6 days off
CORRECTED = (date(2026, 8, 7), date(2026, 9, 6))       # the 09-03 line stays inside


def _stale_claimed(db, seed, run, card):
    """A live card statement (file-backed) whose one 580 line is claimed by an owner entry, parsed before the card's
    closing day was set; then closing_day = 6 makes the current revision stale. Returns (file, first, line, entry)."""
    from app.services import coverage_service as cov
    from app.services.statements import matching
    r, token = run
    f = _filed(db, r, token)
    first = svc.submit_revision(db, r, _rev(r, token, card, [_line(1, date(2026, 9, 3), "580")], total="580", file_id=f.id,
                                            period_start=STALE_FIRST[0], period_end=STALE_FIRST[1]), account_map={FOLDER: card.id})
    line = db.query(StatementLine).filter_by(revision_id=first.revision.id).one()
    entry = seed.entry(card, "-580", day=date(2026, 9, 3))
    db.flush()
    db.refresh(entry)
    cov.write_claim(db, first.statement, line, [matching.Row(entry.id, "principal", Decimal("-580"), None)],
                    entries_by_id={entry.id: entry}, match_kind="auto", match_rule="exact", run_id=r.id)
    db.commit()
    card.closing_day = 6
    db.commit()
    return f, first, line, entry


def _active_rows(db, statement_id):
    return db.query(StatementCoverage).filter_by(statement_id=statement_id).order_by(StatementCoverage.id).all()


def _parse_reviews(db, statement_id):
    return db.query(ReconciliationCase).filter_by(statement_id=statement_id, kind="parse_review").order_by(ReconciliationCase.id).all()


def test_stale_current_correction_transfers_coverage_on_an_identical_line(db_session, seed, card, run):
    """Stale-current path with financial state (review AGENT-145 S2): the identical line keeps its claim on the
    corrected revision; no parse_review."""
    f, first, old_line, entry = _stale_claimed(db_session, seed, run, card)
    r, token = run
    second = svc.submit_revision(db_session, r, _rev(r, token, card, [_line(1, date(2026, 9, 3), "580")], total="580", file_id=f.id,
                                                     period_start=CORRECTED[0], period_end=CORRECTED[1]), account_map={FOLDER: card.id})
    db_session.commit()
    stmt = db_session.get(AccountStatement, first.statement.id)
    assert (stmt.current_revision_id, stmt.period_start, stmt.period_end) == (second.revision.id, *CORRECTED)
    assert second.revision.guardrail["stale_current"] is True and second.lineage_counts["identical"] == 1
    new_line = db_session.query(StatementLine).filter_by(revision_id=second.revision.id).one()
    [row] = _active_rows(db_session, stmt.id)
    assert (row.status, row.line_id, row.entry_id) == ("active", new_line.id, entry.id)
    assert _parse_reviews(db_session, stmt.id) == []


def test_stale_current_correction_quarantines_a_changed_claimed_line(db_session, seed, card, run):
    from app.services import coverage_service as cov
    f, first, old_line, entry = _stale_claimed(db_session, seed, run, card)
    r, token = run
    second = svc.submit_revision(db_session, r, _rev(r, token, card, [_line(1, date(2026, 9, 3), "580", merchant="PAYPAL *Netflix")],
                                                     total="580", file_id=f.id, period_start=CORRECTED[0], period_end=CORRECTED[1]),
                                 account_map={FOLDER: card.id})
    db_session.commit()
    stmt = db_session.get(AccountStatement, first.statement.id)
    assert stmt.current_revision_id == second.revision.id and stmt.period_end == CORRECTED[1]
    old_event = db_session.get(StatementEvent, old_line.event_id)
    assert (old_event.status, old_event.current_line_id) == ("quarantined", None)
    [row] = _active_rows(db_session, stmt.id)
    assert (row.status, row.stale_reason) == ("stale", "lineage:changed")
    assert entry.id not in cov.claimed_entry_ids(db_session)
    [case] = _parse_reviews(db_session, stmt.id)
    assert (case.status, case.event_id) == ("open", old_event.id)


def test_stale_current_keeps_the_twin_count_gate(db_session, seed, card, run):
    """Review AGENT-145 M1: a stale-but-passing current may hold coverage; a corrected revision that changes the
    twin count (1 → 2 identical lines) is not a correction: it stays non-current, the claim stays on the old line,
    and the live statement gets a parse_review."""
    f, first, old_line, entry = _stale_claimed(db_session, seed, run, card)
    r, token = run
    twins = [_line(1, date(2026, 9, 3), "580"), _line(2, date(2026, 9, 3), "580")]
    second = svc.submit_revision(db_session, r, _rev(r, token, card, twins, total="1160", file_id=f.id,
                                                     period_start=CORRECTED[0], period_end=CORRECTED[1]), account_map={FOLDER: card.id})
    db_session.commit()
    stmt = db_session.get(AccountStatement, first.statement.id)
    assert second.revision.guardrail_ok is True and second.revision.guardrail["twins_changed"] is True
    assert (stmt.current_revision_id, stmt.period_end) == (first.revision.id, STALE_FIRST[1])
    [row] = _active_rows(db_session, stmt.id)
    assert (row.status, row.line_id, row.entry_id) == ("active", old_line.id, entry.id)
    [case] = _parse_reviews(db_session, stmt.id)
    assert case.status == "open" and case.context["twins_changed"] is True


def test_a_period_start_a_week_early_is_corrected_and_no_longer_blocks_its_neighbour(db_session, seed, run):
    """Production shape after #84: B = 07-01..08-06 (end right, start a week early) blocked A's correction to
    06-07..07-06 and B's own start-only correction stayed parked. Now B is stale by period_start: its correction
    lands, and A's correction is not blocked by B's stale period either."""
    r, token = run
    old_card = seed.account("舊卡", is_credit=True)
    db_session.commit()
    amap = {FOLDER: old_card.id}
    fa, fb = _filed(db_session, r, token), _filed(db_session, r, token, sha="bb" * 32, md5="bb" * 16)
    la, lb = [_line(1, date(2026, 6, 20), "580")], [_line(1, date(2026, 7, 20), "50")]
    a = svc.submit_revision(db_session, r, _rev(r, token, old_card, la, total="580", file_id=fa.id,
                                                period_start=date(2026, 6, 11), period_end=date(2026, 7, 24)), account_map=amap)
    b = svc.submit_revision(db_session, r, _rev(r, token, old_card, lb, total="50", file_id=fb.id,
                                                period_start=date(2026, 7, 1), period_end=date(2026, 8, 6)), account_map=amap)
    db_session.commit()
    old_card.closing_day = 6
    db_session.commit()
    fixed_a = svc.submit_revision(db_session, r, _rev(r, token, old_card, la, total="580", file_id=fa.id,
                                                      period_start=date(2026, 6, 7), period_end=date(2026, 7, 6)), account_map=amap)
    db_session.commit()
    sa = db_session.get(AccountStatement, a.statement.id)
    assert "period_blocked_by" not in fixed_a.revision.guardrail  # B's start-only stale period does not block
    assert (sa.current_revision_id, sa.period_end) == (fixed_a.revision.id, date(2026, 7, 6))
    fixed_b = svc.submit_revision(db_session, r, _rev(r, token, old_card, lb, total="50", file_id=fb.id,
                                                      period_start=date(2026, 7, 7), period_end=date(2026, 8, 6)), account_map=amap)
    db_session.commit()
    sb = db_session.get(AccountStatement, b.statement.id)
    assert fixed_b.revision.guardrail["stale_current"] is True
    assert (sb.current_revision_id, sb.period_start) == (fixed_b.revision.id, date(2026, 7, 7))


def test_a_current_revision_within_the_closing_day_tolerance_keeps_a_period_change_parked(db_session, seed, run):
    """The rule is narrow: a current revision that still passes today's checks is a trustworthy base, so a changed
    period stays a header change (non-current on a historical statement, as before)."""
    old_card = seed.account("舊卡", is_credit=True)
    db_session.commit()
    wrong, fixed, stmt = _stale_then_fixed(db_session, run, old_card, closing_day=6, wrong_end=date(2026, 9, 8),
                                           fixed_end=date(2026, 9, 6))
    assert fixed.revision.guardrail_ok is True
    assert (stmt.current_revision_id, stmt.period_end) == (wrong.revision.id, date(2026, 9, 8))
    assert "stale_current" not in fixed.revision.guardrail


def test_a_stale_current_on_a_live_statement_is_corrected_without_a_parse_review(db_session, card, run):
    wrong, fixed, stmt = _stale_then_fixed(db_session, run, card, closing_day=6, wrong_end=date(2026, 9, 24),
                                           fixed_end=date(2026, 9, 6))
    assert stmt.mode == "live"
    assert (stmt.current_revision_id, stmt.period_end) == (fixed.revision.id, date(2026, 9, 6))
    assert [(c.kind, c.status) for c in db_session.query(ReconciliationCase).filter_by(kind="parse_review").all()] == []
    assert _event_ids(db_session, fixed.revision.id) == _event_ids(db_session, wrong.revision.id)


@pytest.mark.parametrize("taken", [(date(2026, 9, 1), date(2026, 9, 30)), (date(2026, 9, 16), date(2026, 10, 15))],
                         ids=["same_period_end", "overlapping"])
def test_same_file_period_change_onto_another_statement_goes_to_review(db_session, card, run, taken):
    r, token = run
    f = _filed(db_session, r, token)
    amap = {FOLDER: card.id}
    lines = [_line(1, date(2026, 9, 3), "580")]
    wrong = svc.submit_revision(db_session, r, _rev(r, token, card, lines, total="999", file_id=f.id,
                                                    period_start=date(2026, 9, 1), period_end=date(2026, 10, 5)),
                                account_map=amap)
    other = svc.submit_revision(db_session, r, _rev(r, token, card, [_line(1, date(2026, 9, 20), "50")], total="50",
                                                    period_start=taken[0], period_end=taken[1]), account_map=amap)
    fixed = svc.submit_revision(db_session, r, _rev(r, token, card, lines, total="580", file_id=f.id), account_map=amap)
    assert [s.id for s in _statements(db_session, card)] == [wrong.statement.id, other.statement.id]
    stmt = db_session.get(AccountStatement, wrong.statement.id)
    assert fixed.statement.id == stmt.id and fixed.revision.guardrail_ok is True
    # the period cannot move onto another statement: the revision stays non-current and the owner reviews it
    assert (stmt.current_revision_id, stmt.period_end) == (wrong.revision.id, date(2026, 10, 5))
    cases = db_session.query(ReconciliationCase).filter_by(statement_id=stmt.id).order_by(ReconciliationCase.id).all()
    assert [(c.kind, c.status) for c in cases] == [("parse_review", "open"), ("parse_review", "open")]
    assert cases[1].revision_id == fixed.revision.id
    assert cases[1].context["header_changes"]["period_end"] == ["2026-10-05", "2026-09-30"]
    assert cases[1].context["period_blocked_by"] == other.statement.id
    assert fixed.case_ids == [cases[1].id]


def test_same_file_for_another_account_follows_the_folder_map(db_session, seed, card, run):
    r, token = run
    f = _filed(db_session, r, token)
    lines = [_line(1, date(2026, 9, 3), "580")]
    first = svc.submit_revision(db_session, r, _rev(r, token, card, lines, total="580", file_id=f.id),
                                account_map={FOLDER: card.id})
    other = seed.account("卡二", is_credit=True, statement_live_from=date(2026, 9, 1))
    db_session.commit()
    with pytest.raises(ValidationError) as exc:  # the folder still maps to the first card
        svc.submit_revision(db_session, r, _rev(r, token, other, lines, total="580", file_id=f.id, period_end=date(2026, 10, 5)),
                            account_map={FOLDER: card.id})
    assert (exc.value.field, exc.value.message) == ("account_id", "folder maps elsewhere")
    # the owner remapped the folder: the file is now the other account's, a statement of its own
    moved = svc.submit_revision(db_session, r, _rev(r, token, other, lines, total="580", file_id=f.id, period_end=date(2026, 10, 5)),
                                account_map={FOLDER: other.id})
    assert moved.statement.id != first.statement.id and moved.statement.account_id == other.id
    assert [s.current_revision_id for s in _statements(db_session, card)] == [first.revision.id]


def test_same_file_on_a_reconciled_statement_is_a_conflict_whose_promotion_keeps_the_period_safe(db_session, card, run):
    from app.services import statement_revision_service as revs
    from app.services.errors import CodedConflictError

    r, token = run
    f = _filed(db_session, r, token)
    amap = {FOLDER: card.id}
    lines = [_line(1, date(2026, 9, 3), "580")]
    first = svc.submit_revision(db_session, r, _rev(r, token, card, lines, total="580", file_id=f.id), account_map=amap)
    first.statement.status = "reconciled"
    other = svc.submit_revision(db_session, r, _rev(r, token, card, [_line(1, date(2026, 10, 3), "50")], total="50",
                                                    period_start=OCT[0], period_end=OCT[1]), account_map=amap)
    again = svc.submit_revision(db_session, r, _rev(r, token, card, lines, total="580", file_id=f.id,
                                                    period_start=OCT[0], period_end=OCT[1]), account_map=amap)
    assert (again.statement.id, again.revision.conflict) == (first.statement.id, True)
    case = db_session.get(ReconciliationCase, again.case_ids[0])
    assert (case.kind, case.context["period_blocked_by"]) == ("statement_conflict", other.statement.id)
    events = {e.id: (e.status, e.current_line_id) for e in db_session.query(StatementEvent).all()}
    with pytest.raises(CodedConflictError) as exc:  # keep=new cannot move the period onto the other statement
        revs.promote_revision(db_session, again.statement, again.revision)
    assert exc.value.code == "period_conflict"
    # refused before anything moved: no event pointer or status was flipped in the session
    assert {e.id: (e.status, e.current_line_id) for e in db_session.query(StatementEvent).all()} == events
    assert again.statement.current_revision_id == first.revision.id


def test_two_sections_of_one_composite_file_become_two_independent_statements(db_session, seed, run):
    r, token = run
    live = seed.account("91 帳戶", is_credit=False, statement_live_from=date(2026, 9, 1))
    hist = seed.account("59 帳戶", is_credit=False)
    db_session.commit()
    f = _file(db_session, r, token, sha="c" * 64)
    svc.register_source(db_session, r, SourceRegisterIn(run_id=r.id, lease_token=token, file_id=f.id, root="manual",
                                                        drive_file_id="m1", drive_path="國泰世華/2609.pdf", drive_md5=MD5, drive_size=10))
    cmap = {"manual/國泰世華": {"91/TWD": {"account_id": live.id, "anchor_only": False},
                               "59/TWD": {"account_id": hist.id, "anchor_only": False}}}

    def body(account, section, **kw):
        return _rev(r, token, account, [], total="0", opening="0", kind="bank", file_id=f.id,
                    raw={"section": section, "anchor": True, "rows": 0}, **kw)

    a = svc.submit_revision(db_session, r, body(live, "91/TWD"), account_map={}, composite_map=cmap)
    b = svc.submit_revision(db_session, r, body(hist, "59/TWD", period_start=date(2026, 8, 1), period_end=date(2026, 8, 31)),
                            account_map={}, composite_map=cmap)
    assert a.statement.id != b.statement.id
    assert (a.statement.mode, b.statement.mode) == ("live", "historical")
    assert (a.statement.period_end, b.statement.period_end) == (date(2026, 9, 30), date(2026, 8, 31))
    assert db_session.get(StatementFile, f.id).account_id is None
