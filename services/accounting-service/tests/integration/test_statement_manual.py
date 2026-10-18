"""Manual statements (R1c Task 11; spec §8): `POST /accounts/{account_id}/statements` builds a `RevisionIn` from
`ManualStatementIn` and submits it through the real ingest path on an owner_cli run of its own, with
`origin='manual'`; the hook reconciles and, for a live statement, the policy runs after the commit. Synthetic data
only."""
from datetime import date

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select

from app import auth
from app.models import AccountStatement, IngestRun, ReconciliationAudit, StatementCoverage, StatementRevision
from app.routers import statements as statements_router
from app.schemas.statements import ManualStatementIn
from app.services import statement_ingest_service as ing
from app.services import statement_revision_service as revs
from tests.integration.statement_fixtures import _card, _line, run  # noqa: F401  (run is a fixture)

TOKENS = "spa:spa-token,hermes:hermes-token,worker:worker-token"
SCOPES = "spa=legacy; hermes=read,propose,enqueue; worker=ingest"
S = {"Authorization": "Bearer spa-token"}
H = {"Authorization": "Bearer hermes-token"}
W = {"Authorization": "Bearer worker-token"}
DAY = date(2026, 9, 3)


@pytest.fixture(autouse=True)
def recon_env(monkeypatch):
    monkeypatch.setenv(auth.TOKENS_ENV, TOKENS)
    monkeypatch.setenv(auth.SCOPES_ENV, SCOPES)
    monkeypatch.setenv(auth.FEATURE_ENV, "true")


@pytest.fixture
def policy_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(statements_router.policy_service, "run_policy_safe",
                        lambda db, statement_id, run_id=None: calls.append((statement_id, run_id)))
    return calls


def _body(lines, total, **extra):
    return {"kind": "card", "currency": "TWD", "period_start": "2026-09-01", "period_end": "2026-09-30",
            "statement_total": total, "lines": lines, **extra}


LINE = {"seq": 1, "posted_date": "2026-09-03", "merchant_raw": "全聯", "printed_amount": "580", "line_kind": "purchase"}


def test_manual_statement_route_creates_a_live_manual_statement_reconciles_and_runs_the_policy(
        client, seed, db_session, policy_calls):
    card = _card(seed, db_session)
    entry = seed.entry(card, "-580", day=DAY, merchant="全聯")
    db_session.commit()
    response = client.post(f"/accounts/{card.id}/statements", headers=S,
                           json=_body([LINE], "580", due_date="2026-10-15"))
    assert response.status_code == 201, response.text
    out = response.json()
    assert (out["mode"], out["guardrail_ok"], out["conflict"], out["revision"]) == ("live", True, False, 1)
    db_session.expire_all()
    stmt = db_session.get(AccountStatement, out["statement_id"])
    assert (stmt.origin, stmt.mode, stmt.account_id, stmt.due_date) == ("manual", "live", card.id, date(2026, 10, 15))
    revision = db_session.get(StatementRevision, out["revision_id"])
    assert (revision.file_id, revision.parser, revision.parser_version, revision.raw) == (None, "manual", "1", {})
    run_row = db_session.get(IngestRun, revision.run_id)
    assert (run_row.trigger, run_row.mode, run_row.status, run_row.initiator_hint, run_row.claimed_by) == (
        "owner_cli", "live", "done", "spa", "spa")
    assert run_row.summary == {"manual": True}
    claim = db_session.execute(select(StatementCoverage).where(StatementCoverage.statement_id == stmt.id)).scalar_one()
    assert (claim.entry_id, claim.status) == (entry.id, "active")  # the hook reconciled
    assert stmt.matched_count == 1
    assert policy_calls == [(stmt.id, run_row.id)]
    audit = db_session.execute(select(ReconciliationAudit).where(
        ReconciliationAudit.event == "manual_statement_created")).scalar_one()
    assert (audit.statement_id, audit.actor, audit.run_id) == (stmt.id, "spa", run_row.id)
    assert audit.detail["revision_id"] == revision.id


def test_a_historical_manual_statement_skips_the_policy(client, seed, db_session, policy_calls):
    card = _card(seed, db_session, live=False)
    response = client.post(f"/accounts/{card.id}/statements", headers=S, json=_body([LINE], "580"))
    assert response.status_code == 201, response.text
    assert response.json()["mode"] == "historical"
    assert policy_calls == []


def test_a_statement_without_lines_is_allowed(client, seed, db_session, policy_calls):
    card = _card(seed, db_session)
    response = client.post(f"/accounts/{card.id}/statements", headers=S, json=_body([], "0"))
    assert response.status_code == 201, response.text
    assert response.json()["lineage"]["new"] == 0


def test_manual_statement_route_scopes_and_validation(client, seed, db_session, policy_calls):
    card = _card(seed, db_session)
    url = f"/accounts/{card.id}/statements"
    for who in (H, W):
        assert client.post(url, headers=who, json=_body([LINE], "580")).status_code == 403
    too_many = [{**LINE, "seq": i} for i in range(1, 2002)]
    assert client.post(url, headers=S, json=_body(too_many, "580")).status_code == 422
    assert client.post(url, headers=S, json=_body([LINE, LINE], "1160")).status_code == 422  # duplicate seq
    assert client.post(url, headers=S, json=_body([LINE], "580", file_id=1)).status_code == 422  # extra field
    wrong_kind = client.post(url, headers=S, json={**_body([LINE], "580"), "kind": "bank"})
    assert wrong_kind.status_code == 422 and wrong_kind.json()["detail"][0]["loc"][-1] == "kind"
    assert client.post("/accounts/999999/statements", headers=S, json=_body([LINE], "580")).status_code == 404
    assert db_session.execute(select(AccountStatement)).scalars().all() == []
    assert policy_calls == []


def test_manual_statement_schema_caps_lines_at_2000():
    lines = [{**LINE, "seq": i} for i in range(1, 2001)]
    assert len(ManualStatementIn(**_body(lines, "0")).lines) == 2000
    with pytest.raises(PydanticValidationError):
        ManualStatementIn(**_body([*lines, {**LINE, "seq": 2001}], "0"))


def test_create_manual_statement_service_returns_the_submit_result(seed, db_session):
    card = _card(seed, db_session)
    payload = ManualStatementIn(**_body([LINE], "580"))
    result = ing.create_manual_statement(db_session, account_id=card.id, payload=payload, label="owner-test")
    db_session.commit()
    assert isinstance(result, revs.SubmitResult)
    assert (result.statement.origin, result.revision.revision, result.lineage_counts["new"]) == ("manual", 1, 1)
    second = ing.create_manual_statement(db_session, account_id=card.id, payload=payload, label="owner-test")
    db_session.commit()
    assert (second.statement.id, second.revision.revision, second.replayed) == (result.statement.id, 1, True)  # identical content replays (spec §5)


def test_submit_revision_origin_overrides_the_file_rule(seed, db_session, run):
    from decimal import Decimal

    from app.schemas.statements import RevisionIn

    card = _card(seed, db_session)
    r, token = run
    db_session.commit()  # the run outlives the rollbacks below
    payload = RevisionIn(run_id=r.id, lease_token=token, file_id=None, account_id=card.id, kind="card", parser="t",
                         parser_version="1", currency="TWD", period_start=date(2026, 9, 1),
                         period_end=date(2026, 9, 30), statement_total=Decimal("580"),
                         lines=[_line(1, DAY, "全聯", 580)], raw={})
    result = revs.submit_revision(db_session, r, payload, account_map={}, origin="import")
    assert result.statement.origin == "import"
    db_session.rollback()
    result = revs.submit_revision(db_session, r, payload, account_map={})
    assert result.statement.origin == "manual"  # R1a: no file → manual
    db_session.rollback()
