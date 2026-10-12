"""Agent proposals (R1c Task 9; rulings 21, 27, 31): create, list with supersession on read, apply through the
engine, reject, the locking races and `agent` coverage surviving reconcile. Synthetic data only."""
import queue
import threading
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.orm import sessionmaker

from app import auth
from app.models import (
    AccountStatement, ReconciliationAction, ReconciliationAudit, ReconciliationCase, ReconciliationProposal,
)
from app.schemas.reconciliation import ProposalIn
from app.services import proposal_service
from app.services import reconciliation_service as rec
from app.services.errors import CodedConflictError, CodedValidationError
from app.services.reconciliation_actions import engine
from app.services.reconciliation_actions.coverage_actions import MatchAction
from tests.helpers import race
from tests.integration.statement_fixtures import (  # noqa: F401  (run is a fixture)
    _active, _card, _cases, _code, _line, _reconcile, _submit, run,
)

TOKENS = "spa:spa-token,hermes:hermes-token,hermes2:hermes2-token"
SCOPES = "spa=legacy; hermes=read,propose; hermes2=read,propose"
S = {"Authorization": "Bearer spa-token"}
H = {"Authorization": "Bearer hermes-token"}
H2 = {"Authorization": "Bearer hermes2-token"}
DAY = date(2026, 9, 3)


@pytest.fixture(autouse=True)
def recon_env(monkeypatch):
    monkeypatch.setenv(auth.TOKENS_ENV, TOKENS)
    monkeypatch.setenv(auth.SCOPES_ENV, SCOPES)
    monkeypatch.setenv(auth.FEATURE_ENV, "true")


@pytest.fixture
def unmatched(seed, db_session, run):
    """An open line_unmatched case whose candidates name a booked entry (the matcher found none; the entry is
    booked afterwards and listed by hand)."""
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)])
    (case,) = _cases(db_session, stmt.id, "open")
    seed.entry(card, "-1", day=DAY, merchant="墊高編號")  # keeps entry ids apart from the line id
    entry = seed.entry(card, "-580", day=DAY, merchant="全聯")
    stray = seed.entry(card, "-999", day=DAY, merchant="別的")
    db_session.commit()
    case.candidates = [{"entry_id": entry.id, "reason": "test"}]
    db_session.commit()
    return SimpleNamespace(card=card, stmt=stmt, line=line, case=case, entry=entry, stray=stray)


def _url(case):
    return f"/reconciliation/cases/{case.id}/proposals"


def _body(s, **extra):
    return {"action": "match", "params": {"line_id": s.line.id, "entry_id": s.entry.id},
            "rationale": "same amount and day", "confidence": "0.9", **extra}


def _reload(db, model, pk):
    db.expire_all()
    return db.get(model, pk)


def _audit(db, event):
    db.expire_all()
    return db.execute(select(ReconciliationAudit).where(ReconciliationAudit.event == event)
                      .order_by(ReconciliationAudit.id)).scalars().all()


def _propose(client, s, headers=H, **extra):
    response = client.post(_url(s.case), headers=headers, json=_body(s, **extra))
    assert response.status_code == 201, response.text
    return response.json()


# --- API -----------------------------------------------------------------------------------------------------------

def test_hermes_proposes_and_the_case_becomes_proposed(client, db_session, unmatched):
    s = unmatched
    body = _propose(client, s)
    assert body["status"] == "pending" and body["author"] == "hermes" and body["case_id"] == s.case.id
    assert body["case_version"] == s.case.version and body["event_id"] == s.case.event_id
    assert body["action"] == "match" and body["params"]["entry_id"] == s.entry.id
    assert _reload(db_session, ReconciliationCase, s.case.id).status == "proposed"
    detail = client.get(f"/reconciliation/cases/{s.case.id}", headers=S).json()
    assert [p["id"] for p in detail["pending_proposals"]] == [body["id"]]
    assert len(_audit(db_session, "proposal_created")) == 1


def test_hermes_cannot_apply_or_reject(client, unmatched):
    pid = _propose(client, unmatched)["id"]
    assert client.post(f"/reconciliation/proposals/{pid}/apply", headers=H).status_code == 403
    assert client.post(f"/reconciliation/proposals/{pid}/reject", headers=H, json={}).status_code == 403


def test_owner_applies_the_proposal(client, db_session, unmatched):
    s = unmatched
    pid = _propose(client, s)["id"]
    response = client.post(f"/reconciliation/proposals/{pid}/apply", headers={**S, "x-request-id": "rq-1"})
    assert response.status_code == 201, response.text
    action = response.json()
    assert action["proposal_id"] == pid and action["actor"] == "owner" and action["action"] == "match"
    proposal = _reload(db_session, ReconciliationProposal, pid)
    assert proposal.status == "applied" and proposal.applied_action_id == action["id"]
    assert proposal.decided_at is not None
    assert _reload(db_session, ReconciliationCase, s.case.id).status == "resolved"
    assert [r.match_kind for r in _active(db_session, s.stmt.id)] == ["manual"]  # the owner decided
    (applied,) = _audit(db_session, "proposal_applied")
    assert applied.action_id == action["id"] and applied.request_id == "rq-1"
    # a second apply is refused: no longer pending
    again = client.post(f"/reconciliation/proposals/{pid}/apply", headers=S)
    assert again.status_code == 409 and again.json()["message"].startswith("proposal_not_pending")


def test_apply_unknown_proposal_is_404(client):
    assert client.post("/reconciliation/proposals/9999/apply", headers=S).status_code == 404
    assert client.post("/reconciliation/proposals/9999/reject", headers=S, json={}).status_code == 404


def test_an_entry_outside_the_candidates_is_id_out_of_scope(client, unmatched):
    s = unmatched
    response = client.post(_url(s.case), headers=H, json={
        **_body(s), "params": {"line_id": s.line.id, "entry_id": s.stray.id}})
    assert response.status_code == 422 and "id_out_of_scope" in response.text
    response = client.post(_url(s.case), headers=H, json={
        **_body(s), "params": {"line_id": s.line.id + 100, "entry_id": s.entry.id}})
    assert response.status_code == 422 and "id_out_of_scope" in response.text


def test_sibling_candidates_never_widen_the_scope(client, db_session, unmatched):
    s = unmatched
    case = _reload(db_session, ReconciliationCase, s.case.id)
    case.context = {**(case.context or {}), "sibling_candidates": [{"entry_id": s.stray.id, "account_id": 1}]}
    db_session.commit()
    response = client.post(_url(s.case), headers=H, json={
        **_body(s), "params": {"line_id": s.line.id, "entry_id": s.stray.id}})
    assert response.status_code == 422 and "id_out_of_scope" in response.text


def test_allow_sibling_is_never_proposable(client, unmatched):
    s = unmatched
    response = client.post(_url(s.case), headers=H, json={
        **_body(s), "params": {"line_id": s.line.id, "entry_id": s.entry.id, "allow_sibling": True}})
    assert response.status_code == 422 and "sibling_not_proposable" in response.text


def test_invalid_actions_and_params_are_422(client, unmatched):
    s = unmatched
    assert client.post(_url(s.case), headers=H, json={**_body(s), "action": "nope"}).status_code == 422
    assert client.post(_url(s.case), headers=H, json={**_body(s), "action": "revert"}).status_code == 422
    assert client.post(_url(s.case), headers=H, json={**_body(s), "params": {"line_id": s.line.id}}).status_code == 422
    assert client.post(_url(s.case), headers=H, json={**_body(s), "confidence": "1.5"}).status_code == 422
    assert client.post(_url(s.case), headers=H, json={**_body(s), "rationale": ""}).status_code == 422
    assert client.post("/reconciliation/cases/9999/proposals", headers=H, json=_body(s)).status_code == 404


def test_a_dismissed_case_takes_no_proposals(client, unmatched):
    s = unmatched
    assert client.post(f"/reconciliation/cases/{s.case.id}/dismiss", headers=S, json={"note": "x"}).status_code == 201
    response = client.post(_url(s.case), headers=H, json=_body(s))
    assert response.status_code == 409 and response.json()["message"].startswith("case_closed")


def test_a_second_proposal_by_the_same_author_supersedes_the_first(client, db_session, unmatched):
    s = unmatched
    first = _propose(client, s)
    other = _propose(client, s, headers=H2)
    second = _propose(client, s)
    assert _reload(db_session, ReconciliationProposal, first["id"]).status == "superseded"
    assert _reload(db_session, ReconciliationProposal, other["id"]).status == "pending"
    assert _reload(db_session, ReconciliationProposal, second["id"]).status == "pending"
    assert len(_audit(db_session, "proposal_superseded")) == 1
    listed = client.get("/reconciliation/proposals", params={"status": "pending", "case_id": s.case.id}, headers=H)
    assert sorted(p["id"] for p in listed.json()) == sorted([other["id"], second["id"]])
    assert client.get("/reconciliation/proposals", params={"status": "bogus"}, headers=H).status_code == 422


def test_a_case_version_bump_supersedes_on_read_and_apply_is_409(client, db_session, unmatched):
    s = unmatched
    pid = _propose(client, s)["id"]
    db_session.execute(update(ReconciliationCase).where(ReconciliationCase.id == s.case.id)
                       .values(version=ReconciliationCase.version + 1))
    db_session.commit()
    listed = client.get("/reconciliation/proposals", headers=H).json()
    assert [(p["id"], p["status"]) for p in listed] == [(pid, "superseded")]
    assert _reload(db_session, ReconciliationProposal, pid).status == "superseded"  # durable
    assert len(_audit(db_session, "proposal_superseded")) == 1
    response = client.post(f"/reconciliation/proposals/{pid}/apply", headers=S)
    assert response.status_code == 409 and response.json()["message"].startswith("proposal_not_pending")


def test_apply_of_a_stale_pending_proposal_is_superseded_and_durable(client, db_session, unmatched):
    s = unmatched
    pid = _propose(client, s)["id"]
    db_session.execute(update(ReconciliationCase).where(ReconciliationCase.id == s.case.id)
                       .values(version=ReconciliationCase.version + 1))
    db_session.commit()
    # skip the route's read-time sweep: call the service pre-check directly, then the route-level handling
    with pytest.raises(CodedConflictError) as info:
        proposal_service.apply_proposal(db_session, proposal_id=pid, label="t", request_id=None)
    db_session.rollback()
    assert info.value.code == "proposal_superseded"
    assert client.post(f"/reconciliation/proposals/{pid}/apply", headers=S).status_code == 409
    assert _reload(db_session, ReconciliationProposal, pid).status == "superseded"
    assert _actions(db_session) == []


def test_reject_reopens_the_case_when_it_was_the_only_pending(client, db_session, unmatched):
    s = unmatched
    first = _propose(client, s)
    second = _propose(client, s, headers=H2)
    done = client.post(f"/reconciliation/proposals/{first['id']}/reject", headers=S, json={"note": "no"})
    assert done.status_code == 200 and done.json()["status"] == "rejected" and done.json()["decided_at"]
    assert _reload(db_session, ReconciliationCase, s.case.id).status == "proposed"  # another is pending
    assert client.post(f"/reconciliation/proposals/{second['id']}/reject", headers=S).status_code == 200
    assert _reload(db_session, ReconciliationCase, s.case.id).status == "open"
    assert len(_audit(db_session, "proposal_rejected")) == 2
    again = client.post(f"/reconciliation/proposals/{second['id']}/reject", headers=S, json={})
    assert again.status_code == 409 and again.json()["message"].startswith("proposal_not_pending")


def test_applying_supersedes_the_other_pending_proposals(client, db_session, unmatched):
    s = unmatched
    mine = _propose(client, s)
    theirs = _propose(client, s, headers=H2)
    assert client.post(f"/reconciliation/proposals/{mine['id']}/apply", headers=S).status_code == 201
    assert _reload(db_session, ReconciliationProposal, theirs["id"]).status == "superseded"


# --- agent coverage (ruling 31, ruling 16) ---------------------------------------------------------------------------

def test_a_policy_applied_proposal_writes_agent_coverage_that_reconcile_keeps(client, db_session, unmatched):
    s = unmatched
    pid = _propose(client, s)["id"]
    row, _ = engine.apply_action(db_session, case_id=s.case.id, action="match",
                                 params={"line_id": s.line.id, "entry_id": s.entry.id}, actor="policy",
                                 label="policy", proposal_id=pid, budget=(date(2026, 10, 1), "TWD", Decimal("580")))
    db_session.commit()
    (claim,) = _active(db_session, s.stmt.id)
    assert claim.match_kind == "agent" and row.proposal_id == pid
    assert _reload(db_session, ReconciliationProposal, pid).status == "applied"
    _reconcile(db_session, s.stmt.id)
    (kept,) = _active(db_session, s.stmt.id)
    assert kept.id == claim.id and kept.match_kind == "agent"


# --- races ---------------------------------------------------------------------------------------------------------

def _actions(db):
    db.expire_all()
    return db.execute(select(ReconciliationAction)).scalars().all()


def _pending(db, case_id):
    db.expire_all()
    return db.execute(select(ReconciliationProposal).where(
        ReconciliationProposal.case_id == case_id, ReconciliationProposal.status == "pending")).scalars().all()


def _payload(s):
    return ProposalIn(action="match", params={"line_id": s.line.id, "entry_id": s.entry.id}, rationale="r",
                      confidence="0.5")


def test_two_creates_by_one_author_leave_one_pending(unmatched, db_session, pg_engine):
    s = unmatched
    case_id, payload = s.case.id, _payload(s)
    outcome = race(pg_engine,
                   lambda db: proposal_service.create_proposal(db, case_id=case_id, payload=payload, author="a"),
                   lambda db: proposal_service.create_proposal(db, case_id=case_id, payload=payload, author="a"))
    assert outcome == "committed"
    assert len(_pending(db_session, case_id)) == 1
    assert len(db_session.execute(select(ReconciliationProposal)).scalars().all()) == 2


def _make(db_session, s):
    row = proposal_service.create_proposal(db_session, case_id=s.case.id, payload=_payload(s), author="a")
    db_session.commit()
    return row.id


def test_apply_vs_reject_the_loser_is_not_pending(unmatched, db_session, pg_engine):
    s = unmatched
    pid, case_id = _make(db_session, s), s.case.id
    apply = lambda db: proposal_service.apply_proposal(db, proposal_id=pid, label="t", request_id=None)  # noqa: E731
    reject = lambda db: proposal_service.reject_proposal(db, proposal_id=pid, label="t", note=None)  # noqa: E731
    outcome = race(pg_engine, apply, reject)
    assert isinstance(outcome, CodedConflictError) and outcome.code == "proposal_not_pending"
    assert _reload(db_session, ReconciliationProposal, pid).status == "applied"
    assert len(_actions(db_session)) == 1


def test_reject_vs_apply_the_loser_is_not_pending(unmatched, db_session, pg_engine):
    s = unmatched
    pid = _make(db_session, s)
    outcome = race(pg_engine,
                   lambda db: proposal_service.reject_proposal(db, proposal_id=pid, label="t", note=None),
                   lambda db: proposal_service.apply_proposal(db, proposal_id=pid, label="t", request_id=None))
    assert isinstance(outcome, CodedConflictError) and outcome.code == "proposal_not_pending"
    assert _reload(db_session, ReconciliationProposal, pid).status == "rejected"
    assert _actions(db_session) == []


def test_a_reject_committing_while_apply_is_in_prepare_wins(unmatched, db_session, pg_engine, monkeypatch):
    s = unmatched
    pid = _make(db_session, s)
    real = MatchAction.prepare

    def prepare_then_reject(self, ctx, params):
        prepared = real(self, ctx, params)
        other = sessionmaker(bind=pg_engine, autoflush=False)()
        try:
            proposal_service.reject_proposal(other, proposal_id=pid, label="t", note=None)
            other.commit()
        finally:
            other.close()
        return prepared

    monkeypatch.setattr(MatchAction, "prepare", prepare_then_reject)
    with pytest.raises(CodedConflictError) as info:
        proposal_service.apply_proposal(db_session, proposal_id=pid, label="t", request_id=None)
    db_session.rollback()
    assert info.value.code == "proposal_not_pending"
    assert _actions(db_session) == [] and _active(db_session, s.stmt.id) == []
    assert _reload(db_session, ReconciliationProposal, pid).status == "rejected"


def test_a_version_bump_vs_apply_is_proposal_superseded(unmatched, db_session, pg_engine):
    s = unmatched
    pid, stmt_id, case_id = _make(db_session, s), s.stmt.id, s.case.id

    def bump(db):  # what a reconcile holding the statement lock does to the case
        db.execute(select(AccountStatement).where(AccountStatement.id == stmt_id).with_for_update())
        db.execute(update(ReconciliationCase).where(ReconciliationCase.id == case_id)
                   .values(version=ReconciliationCase.version + 1))

    outcome = race(pg_engine, bump,
                   lambda db: proposal_service.apply_proposal(db, proposal_id=pid, label="t", request_id=None))
    assert _code(outcome) == "proposal_superseded", outcome
    assert _actions(db_session) == [] and _active(db_session, stmt_id) == []
    assert proposal_service.supersede_stale(db_session) == 1
    db_session.commit()
    assert _reload(db_session, ReconciliationProposal, pid).status == "superseded"


def test_create_service_refuses_out_of_scope_ids(unmatched, db_session):
    s = unmatched
    payload = ProposalIn(action="match", params={"line_id": s.line.id, "entry_id": s.stray.id}, rationale="r",
                         confidence="0.5")
    with pytest.raises(CodedValidationError) as info:
        proposal_service.create_proposal(db_session, case_id=s.case.id, payload=payload, author="a")
    assert info.value.code == "id_out_of_scope"


def test_lineage_supersession_writes_an_audit_row(unmatched, db_session):
    from app.services import statement_revision_service as revs
    s = unmatched
    pid = _make(db_session, s)
    revs._supersede_pending_proposals(db_session, s.case.event_id)
    db_session.commit()
    assert _reload(db_session, ReconciliationProposal, pid).status == "superseded"
    (row,) = _audit(db_session, "proposal_superseded")
    assert row.case_id == s.case.id and row.detail["proposal_id"] == pid


# --- fix round 1: typed id scoping, proposal mismatch, check order, no dangling `proposed` -------------------------

def _scoped(client, s, **params):
    return client.post(_url(s.case), headers=H, json={**_body(s), "params": {"line_id": s.line.id, **params}})


def test_entry_id_equal_to_a_candidates_account_id_is_out_of_scope(client, db_session, unmatched):
    s = unmatched
    case = _reload(db_session, ReconciliationCase, s.case.id)
    case.candidates = [{"entry_id": s.entry.id, "account_id": s.stray.id}]
    db_session.commit()
    response = _scoped(client, s, entry_id=s.stray.id)
    assert response.status_code == 422 and "id_out_of_scope" in response.text
    assert _scoped(client, s, entry_id=s.entry.id).status_code == 201  # the real candidate still passes


def test_entry_id_equal_to_the_cases_line_id_is_out_of_scope(client, unmatched):
    s = unmatched
    response = _scoped(client, s, entry_id=s.line.id)
    assert response.status_code == 422 and "id_out_of_scope" in response.text


def test_entry_id_equal_to_a_context_statement_id_is_out_of_scope(client, db_session, unmatched):
    s = unmatched
    case = _reload(db_session, ReconciliationCase, s.case.id)
    case.context = {"statement_id": s.stray.id, "old_line_id": 424242}
    db_session.commit()
    assert "id_out_of_scope" in _scoped(client, s, entry_id=s.stray.id).text
    assert _scoped(client, s, entry_id=s.entry.id).status_code == 201


def test_line_id_scope_accepts_old_and_new_line_ids(client, db_session, unmatched):
    s = unmatched
    case = _reload(db_session, ReconciliationCase, s.case.id)
    case.context = {"old_line_id": 424242}
    db_session.commit()
    ok = client.post(_url(s.case), headers=H, json={
        **_body(s), "params": {"line_id": 424242, "entry_id": s.entry.id}})
    assert ok.status_code == 201, ok.text


def test_the_engine_refuses_an_action_that_is_not_the_proposals(unmatched, db_session):
    s = unmatched
    pid = _make(db_session, s)
    with pytest.raises(CodedConflictError) as info:
        engine.apply_action(db_session, case_id=s.case.id, action="match",
                            params={"line_id": s.line.id, "entry_id": s.entry.id, "with_children": True},
                            actor="policy", label="policy", proposal_id=pid,
                            budget=(date(2026, 10, 1), "TWD", Decimal("580")))
    db_session.rollback()
    assert info.value.code == "proposal_mismatch"
    with pytest.raises(CodedConflictError) as info:
        engine.apply_action(db_session, case_id=s.case.id, action="dismiss", params={"note": "x"},
                            actor="owner", label="policy", proposal_id=pid)
    db_session.rollback()
    assert info.value.code == "proposal_mismatch"
    assert _actions(db_session) == [] and _reload(db_session, ReconciliationProposal, pid).status == "pending"


def test_a_rejected_proposal_is_409_even_when_the_identical_action_already_exists(unmatched, db_session):
    s = unmatched
    pid = _make(db_session, s)
    params = {"line_id": s.line.id, "entry_id": s.entry.id}
    proposal_service.reject_proposal(db_session, proposal_id=pid, label="t", note=None)
    db_session.commit()
    engine.apply_action(db_session, case_id=s.case.id, action="match", params=params, actor="owner", label="o")
    db_session.commit()
    with pytest.raises(CodedConflictError) as info:
        engine.apply_action(db_session, case_id=s.case.id, action="match", params=params, actor="owner",
                            label="o", proposal_id=pid)
    db_session.rollback()
    assert info.value.code == "proposal_not_pending"


def test_supersede_stale_reopens_a_case_left_proposed(unmatched, db_session):
    s = unmatched
    _make(db_session, s)
    db_session.execute(update(ReconciliationCase).where(ReconciliationCase.id == s.case.id)
                       .values(version=ReconciliationCase.version + 1))
    db_session.commit()
    assert proposal_service.supersede_stale(db_session) == 1
    db_session.commit()
    assert _reload(db_session, ReconciliationCase, s.case.id).status == "open"


def test_lineage_supersession_reopens_a_proposed_case(unmatched, db_session):
    from app.services import statement_revision_service as revs
    s = unmatched
    _make(db_session, s)
    revs._supersede_pending_proposals(db_session, s.case.event_id)
    db_session.commit()
    assert _reload(db_session, ReconciliationCase, s.case.id).status == "open"


def test_reject_ignores_stale_pending_rows_when_reopening(unmatched, db_session):
    s = unmatched
    first = proposal_service.create_proposal(db_session, case_id=s.case.id, payload=_payload(s), author="a")
    second = proposal_service.create_proposal(db_session, case_id=s.case.id, payload=_payload(s), author="b")
    db_session.commit()
    first_id, second_id = first.id, second.id
    db_session.execute(update(ReconciliationCase).where(ReconciliationCase.id == s.case.id)
                       .values(version=ReconciliationCase.version + 1))
    db_session.commit()
    proposal_service.reject_proposal(db_session, proposal_id=first_id, label="t", note=None)
    db_session.commit()
    assert _reload(db_session, ReconciliationCase, s.case.id).status == "open"
    assert second_id  # the other row is stale, not a live proposal


def test_case_detail_supersedes_stale_proposals_first(client, db_session, unmatched):
    s = unmatched
    _make(db_session, s)
    db_session.execute(update(ReconciliationCase).where(ReconciliationCase.id == s.case.id)
                       .values(version=ReconciliationCase.version + 1))
    db_session.commit()
    detail = client.get(f"/reconciliation/cases/{s.case.id}", headers=S).json()
    assert detail["pending_proposals"] == [] and detail["status"] == "open"


def test_a_version_bump_during_prepare_is_persisted_as_superseded_by_the_route(client, db_session, pg_engine,
                                                                                unmatched, monkeypatch):
    s = unmatched
    pid = _propose(client, s)["id"]
    real = MatchAction.prepare

    def prepare_then_bump(self, ctx, params):
        prepared = real(self, ctx, params)
        other = sessionmaker(bind=pg_engine, autoflush=False)()
        try:
            other.execute(update(ReconciliationCase).where(ReconciliationCase.id == s.case.id)
                          .values(version=ReconciliationCase.version + 1))
            other.commit()
        finally:
            other.close()
        return prepared

    monkeypatch.setattr(MatchAction, "prepare", prepare_then_bump)
    response = client.post(f"/reconciliation/proposals/{pid}/apply", headers=S)
    assert response.status_code == 409 and response.json()["message"].startswith("proposal_superseded")
    assert _reload(db_session, ReconciliationProposal, pid).status == "superseded"
    assert _reload(db_session, ReconciliationCase, s.case.id).status == "open"
    assert _actions(db_session) == []


def _proposals(db, **where):
    db.expire_all()
    return db.execute(select(ReconciliationProposal).filter_by(**where)
                      .order_by(ReconciliationProposal.id)).scalars().all()


def _stale_pending(db, s):
    """A pending proposal on s.case made at the previous version (the case moved on); returns its id."""
    pid = _make(db, s)
    db.execute(update(ReconciliationCase).where(ReconciliationCase.id == s.case.id)
               .values(version=ReconciliationCase.version + 1))
    db.commit()
    return pid


def _hold_case(db, s):
    db.execute(select(ReconciliationCase).where(ReconciliationCase.id == s.case.id).with_for_update())


def _hold_statement(db, s):
    rec._lock_statement(db, s.stmt.id)


def _global_supersede_while(pg_engine, hold, s):
    """Run the global helper while `hold(session, s)` keeps a row locked; returns (its result, whether it waited)."""
    factory = sessionmaker(bind=pg_engine, autoflush=False)
    holder, other = factory(), factory()
    out: queue.Queue = queue.Queue()
    try:
        hold(holder, s)
        thread = threading.Thread(target=lambda: out.put(proposal_service.supersede_stale(other)), daemon=True)
        thread.start()
        thread.join(2)
        waited = thread.is_alive()
        holder.commit()
        thread.join(5)
        assert not thread.is_alive()
        result = out.get_nowait()
        other.commit()
        return result, waited
    finally:
        holder.close()
        other.close()


@pytest.mark.parametrize("hold", [_hold_case, _hold_statement], ids=["case", "statement"])
def test_global_supersede_skips_a_locked_row(unmatched, db_session, pg_engine, hold):
    s = unmatched
    _stale_pending(db_session, s)
    result, waited = _global_supersede_while(pg_engine, hold, s)
    assert (result, waited) == (0, False), "the global helper must neither wait nor supersede a held row"
    assert proposal_service.supersede_stale(db_session) == 1  # released: the next call does it durably
    db_session.commit()
    assert _reload(db_session, ReconciliationCase, s.case.id).status == "open"


@pytest.mark.parametrize("hold", [_hold_case, _hold_statement], ids=["case", "statement"])
def test_scoped_supersede_waits_for_the_lock(unmatched, db_session, pg_engine, hold):
    s = unmatched
    _stale_pending(db_session, s)
    outcome = race(pg_engine, first=lambda db: hold(db, s),
                   second=lambda db: proposal_service.supersede_stale(db, case_id=s.case.id))
    assert outcome == "committed"
    (proposal,) = _proposals(db_session, case_id=s.case.id)
    assert proposal.status == "superseded"
    assert _reload(db_session, ReconciliationCase, s.case.id).status == "open"


def test_listing_hides_a_stale_pending_proposal_it_could_not_supersede(unmatched, db_session, pg_engine):
    s = unmatched
    pid = _stale_pending(db_session, s)
    factory = sessionmaker(bind=pg_engine, autoflush=False)
    holder = factory()
    try:
        _hold_case(holder, s)
        listed = proposal_service.list_proposals(db_session)  # global supersede skips the held case
        db_session.commit()
        assert [p.id for p in listed] == []
        assert _proposals(db_session, case_id=s.case.id)[0].status == "pending"  # still pending in the table
        holder.commit()
    finally:
        holder.close()
    listed = proposal_service.list_proposals(db_session)
    db_session.commit()
    assert [(p.id, p.status) for p in listed] == [(pid, "superseded")]
