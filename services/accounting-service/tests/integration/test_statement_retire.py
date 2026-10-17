"""Admin retire of a duplicate statement (fix 2026-10-17): `POST /accounts/{a}/statements/{s}/retire` releases its
coverage, retires its events, supersedes its open cases and pending proposals, makes it historical with the note and
audits `statement_retired`; it then drops out of the pending selection, the reconcile pass and the policy, and no
revision lands on it again. Refused (409 `actions_applied`, nothing changed) while an action on its cases is applied.
A second call is a 200 no-op (`already_retired`). Synthetic data only."""
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select, update

from app import auth
from app.models import (
    AccountStatement, CoverageDirty, ReconciliationAction, ReconciliationAudit, ReconciliationCase,
    ReconciliationProposal, StatementCoverage, StatementEvent, StatementRevision,
)
from app.schemas.statements import FileRegisterIn, RevisionIn, SourceRegisterIn
from app.services import policy_service, settings_service
from app.services import reconciliation_service as rec
from app.services import statement_ingest_service as ing
from app.services import statement_retire_service as retire_svc
from app.services import statement_revision_service as revs
from app.services.errors import CodedConflictError, ValidationError
from app.services.reconciliation_actions import engine
from tests.integration.statement_fixtures import (  # noqa: F401  (run is a fixture)
    SEP, _active, _card, _cases, _line, _submit, run,
)

DAY = date(2026, 9, 3)
WRONG = (date(2026, 9, 1), date(2026, 10, 5))  # the due date read as the closing date
NOTE = "重複對帳單：同一 PDF 錯讀期間"
SYSTEM_ON = {"enabled": True, "actions": {"create_system_entry": {"enabled": True}}}
TOKENS = "spa:spa-token,owner:owner-token,boss:boss-token"
SCOPES = "spa=legacy; owner=read,write; boss=admin"
OWNER = {"Authorization": "Bearer owner-token"}
ADMIN = {"Authorization": "Bearer boss-token"}


def _retire(db, stmt, note=NOTE):
    result = retire_svc.retire_statement(db, account_id=stmt.account_id, statement_id=stmt.id, note=note,
                                         label="owner-test", request_id=None)
    db.commit()
    return result


def _retired_audit(db, stmt):
    db.expire_all()
    return db.execute(select(ReconciliationAudit).where(ReconciliationAudit.statement_id == stmt.id,
                                                        ReconciliationAudit.event == "statement_retired")).scalars().all()


def _events(db, stmt):
    db.expire_all()
    return [e.status for e in db.execute(select(StatementEvent).where(StatementEvent.statement_id == stmt.id)
                                         .order_by(StatementEvent.id)).scalars()]


def _set_policy(db, policy):
    current = settings_service.get_reconciliation_settings(db)
    settings_service.update_reconciliation_settings(db, {"dirty_enabled": current["dirty_enabled"], "policy": policy})
    db.commit()


def _duplicate(seed, db, run):
    """The production shape: a live card statement whose revision failed its equation guardrail (one open
    parse_review, no claims)."""
    card = _card(seed, db)
    stmt, lines, _ = _submit(db, run, card, [_line(1, DAY, "全聯", 580), _line(2, DAY, "家樂福", 120)],
                             period=WRONG, total=999)
    assert [(c.kind, c.status) for c in _cases(db, stmt.id)] == [("parse_review", "open")]
    return card, stmt, lines


def test_retire_a_duplicate_statement(seed, db_session, run):
    card, stmt, lines = _duplicate(seed, db_session, run)
    (case,) = _cases(db_session, stmt.id)
    db_session.add(ReconciliationProposal(case_id=case.id, case_version=case.version, action="dismiss",
                                          params={"note": "dup"}, rationale="r", confidence=Decimal("0.5"),
                                          author="hermes"))
    db_session.add(CoverageDirty(kind="entry", row_id=1, op="insert", new_account_id=card.id))
    db_session.commit()
    assert stmt.id in rec.pending_statement_ids(db_session)

    result = _retire(db_session, stmt)

    assert (result.already_retired, result.retired_events, result.superseded_cases, result.released_coverage) == (
        False, 2, 1, 0)
    db_session.expire_all()
    stmt = db_session.get(AccountStatement, stmt.id)
    assert (stmt.mode, stmt.note, stmt.open_case_count, stmt.needs_recheck, stmt.conflict_open) == (
        "historical", NOTE, 0, False, False)
    assert _events(db_session, stmt) == ["retired", "retired"]
    (case,) = _cases(db_session, stmt.id)
    assert (case.status, case.version) == ("superseded", 2)
    assert db_session.execute(select(ReconciliationProposal.status)).scalars().all() == ["superseded"]
    (row,) = _retired_audit(db_session, stmt)
    assert (row.actor, row.detail["note"], row.detail["previous_mode"], row.detail["superseded_cases"]) == (
        "owner-test", NOTE, "live", [case.id])
    # out of the pending selection, the reconcile pass and the policy
    assert stmt.id not in rec.pending_statement_ids(db_session)
    assert rec.reconcile(db_session, stmt.id).skipped == "retired"
    db_session.commit()
    _set_policy(db_session, SYSTEM_ON)
    assert policy_service.run_policy(db_session, stmt.id) == policy_service.PolicyResult()
    assert policy_service._statement_excluded(db_session, stmt, SYSTEM_ON) == "not_live"
    # a second call is a no-op
    again = _retire(db_session, stmt, note="another note")
    assert again.already_retired is True
    assert len(_retired_audit(db_session, stmt)) == 1 and db_session.get(AccountStatement, stmt.id).note == NOTE


def test_retire_releases_the_statement_claims(seed, db_session, run):
    card = _card(seed, db_session)
    seed.entry(card, "-580", day=DAY, merchant="全聯")
    db_session.commit()
    stmt, (claimed, unmatched), _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580), _line(2, DAY, "家樂福", 120)])
    (claim,) = _active(db_session, stmt.id)
    assert claim.line_id == claimed.id
    assert [c.kind for c in _cases(db_session, stmt.id, "open")] == ["line_unmatched"]

    result = _retire(db_session, stmt)

    assert (result.released_coverage, result.superseded_cases) == (1, 1)
    assert _active(db_session, stmt.id) == []
    released = db_session.get(StatementCoverage, claim.id)
    assert (released.status, released.stale_reason) == ("stale", "retired")
    assert _events(db_session, stmt) == ["retired", "retired"]
    assert [c.status for c in _cases(db_session, stmt.id)] == ["superseded"]
    stmt = db_session.get(AccountStatement, stmt.id)
    assert (stmt.matched_count, stmt.open_case_count, stmt.mode) == (0, 0, "historical")


def test_retire_refuses_a_statement_with_an_applied_action(seed, db_session, run):
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "年費", 1200, "fee")])
    (case,) = _cases(db_session, stmt.id, "open")
    engine.apply_action(db_session, case_id=case.id, action="create_system_entry", params={"kind": "fee"},
                        actor="owner", label="owner-test")
    db_session.commit()
    (claim,) = _active(db_session, stmt.id)

    with pytest.raises(CodedConflictError) as exc:
        retire_svc.retire_statement(db_session, account_id=card.id, statement_id=stmt.id, note=NOTE,
                                    label="owner-test", request_id=None)
    db_session.rollback()

    assert exc.value.code == "actions_applied"
    stmt = db_session.get(AccountStatement, stmt.id)
    assert (stmt.mode, stmt.note) == ("live", None)
    assert _events(db_session, stmt) == ["live"]
    assert [c.id for c in _active(db_session, stmt.id)] == [claim.id]
    assert db_session.execute(select(ReconciliationAction.status)).scalars().all() == ["applied"]
    assert [c.status for c in _cases(db_session, stmt.id)] == ["resolved"]
    assert _retired_audit(db_session, stmt) == []


def test_no_revision_lands_on_a_retired_statement(seed, db_session, run):
    """The pre-fix duplicate (two statements from one file) after its retire: the file's re-parse with the wrong period
    becomes a revision of the statement kept, never of the retired one; any other revision for the retired identity is
    refused (422, the worker's parse failure — a 409 would abort its run)."""
    r, token = run
    card = _card(seed, db_session)
    f = ing.register_file(db_session, r, FileRegisterIn(run_id=r.id, lease_token=token, sha256="ab" * 32, size=10,
                                                        kind="card", object_key="inbox/by-sha/x.pdf"))
    ing.register_source(db_session, r, SourceRegisterIn(run_id=r.id, lease_token=token, file_id=f.id, root="mail",
                                                        drive_file_id="drive-1", drive_path="信用卡/匯豐/2026-09.pdf",
                                                        drive_md5="cd" * 16, drive_size=10))
    amap = {"mail/信用卡/匯豐": card.id}
    lines = [_line(1, DAY, "全聯", 580)]

    def body(period, total, file_id):
        return RevisionIn(run_id=r.id, lease_token=token, file_id=file_id, account_id=card.id, kind="card", parser="t",
                          parser_version="1", currency="TWD", period_start=period[0], period_end=period[1],
                          statement_total=Decimal(total), lines=lines, raw={})

    kept = revs.submit_revision(db_session, r, body(SEP, "580", f.id), account_map=amap).statement
    dup = revs.submit_revision(db_session, r, body(WRONG, "999", None), account_map=amap).statement
    db_session.execute(update(StatementRevision).where(StatementRevision.statement_id == dup.id)
                       .values(file_id=f.id))  # the pre-fix data: both statements carry the file
    db_session.commit()
    _retire(db_session, dup)

    again = revs.submit_revision(db_session, r, body(WRONG, "580", f.id), account_map=amap)
    assert (again.statement.id, again.revision.revision) == (kept.id, 2)
    db_session.commit()
    with pytest.raises(ValidationError) as exc:
        revs.submit_revision(db_session, r, body(WRONG, "580", None), account_map=amap)
    db_session.rollback()
    assert (exc.value.field, exc.value.code) == ("period_end", "statement_retired")
    assert db_session.scalar(select(func.count(StatementRevision.id)).where(StatementRevision.statement_id == dup.id)) == 1


@pytest.fixture
def recon_env(monkeypatch):
    monkeypatch.setenv(auth.TOKENS_ENV, TOKENS)
    monkeypatch.setenv(auth.SCOPES_ENV, SCOPES)
    monkeypatch.setenv(auth.FEATURE_ENV, "true")


def test_retire_route(recon_env, client, seed, db_session, run):
    card, stmt, _ = _duplicate(seed, db_session, run)
    url = f"/accounts/{card.id}/statements/{stmt.id}/retire"
    assert client.post(url, headers=OWNER, json={"note": NOTE}).status_code == 403
    for bad in ({}, {"note": ""}, {"note": "   "}, {"note": "x" * 1001}, {"note": NOTE, "force": True}):
        assert client.post(url, headers=ADMIN, json=bad).status_code == 422, bad
    assert client.post(f"/accounts/{card.id + 1}/statements/{stmt.id}/retire", headers=ADMIN,
                       json={"note": NOTE}).status_code == 404
    done = client.post(url, headers=ADMIN, json={"note": NOTE})
    assert done.status_code == 200, done.text
    assert done.json() == {"statement_id": stmt.id, "mode": "historical", "already_retired": False,
                           "released_coverage": 0, "retired_events": 2, "superseded_cases": 1}
    again = client.post(url, headers=ADMIN, json={"note": NOTE})
    assert again.status_code == 200 and again.json()["already_retired"] is True
    assert len(_retired_audit(db_session, stmt)) == 1
