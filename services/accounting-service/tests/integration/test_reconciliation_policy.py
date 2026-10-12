"""The policy engine (R1c Task 10; spec §7.3, rulings 6, 10, 22, 31; Review Focus 4): shipped off; bank-only system
entries and matching FX fees applied by policy under per-action, per-statement and monthly caps, the budget row
reserved before the statement lock and charged only after the precheck under the lock, over-cap candidates turned
into policy proposals, agent `match` proposals accepted only with a single admissible representation, and the
reconcile / revision / sweep routes running it after their commit. Synthetic data only."""
import threading
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from app import auth
from app.models import (
    AccountStatement, LedgerEntry, PolicyBudget, ReconciliationAction, ReconciliationAudit, ReconciliationCase,
    ReconciliationProposal,
)
from app.schemas.reconciliation import ProposalIn
from app.services import entry_write_service as ews
from app.services import policy_service, proposal_service, settings_service
from app.services.policy_service import PolicyResult
from app.services.reconciliation_actions import engine
from tests.helpers import race, wait_until_blocked
from tests.integration.statement_fixtures import (  # noqa: F401  (run is a fixture)
    _active, _card, _cases, _line, _submit, run,
)

TOKENS = "spa:spa-token,worker:worker-token,hermes:hermes-token"
SCOPES = "spa=legacy; worker=ingest; hermes=read,propose,enqueue"
S = {"Authorization": "Bearer spa-token"}
W = {"Authorization": "Bearer worker-token"}
H = {"Authorization": "Bearer hermes-token"}
DAY = date(2026, 9, 3)
TODAY = date(2026, 10, 12)
MONTH = date(2026, 10, 1)
SYSTEM_ON = {"enabled": True, "actions": {"create_system_entry": {"enabled": True}}}
POLICY_EVENTS = ("policy_applied", "policy_proposed", "policy_skipped")


@pytest.fixture(autouse=True)
def recon_env(monkeypatch, today):
    monkeypatch.setenv(auth.TOKENS_ENV, TOKENS)
    monkeypatch.setenv(auth.SCOPES_ENV, SCOPES)
    monkeypatch.setenv(auth.FEATURE_ENV, "true")
    today(TODAY)


# --- helpers ------------------------------------------------------------------------------------------------------

def _set_policy(db, policy: dict) -> None:
    current = settings_service.get_reconciliation_settings(db)
    settings_service.update_reconciliation_settings(db, {"dirty_enabled": current["dirty_enabled"], "policy": policy})
    db.commit()


def _audit(db, *events):
    db.expire_all()
    return db.execute(select(ReconciliationAudit).where(ReconciliationAudit.event.in_(events))
                      .order_by(ReconciliationAudit.id)).scalars().all()


def _actions(db):
    db.expire_all()
    return db.execute(select(ReconciliationAction).order_by(ReconciliationAction.id)).scalars().all()


def _booked(db, account_id=None):
    db.expire_all()
    q = select(LedgerEntry).where(LedgerEntry.source == "statement")
    if account_id is not None:
        q = q.where(LedgerEntry.account_id == account_id)
    return db.execute(q.order_by(LedgerEntry.id)).scalars().all()


def _proposals(db, **where):
    db.expire_all()
    q = select(ReconciliationProposal).filter_by(**where)
    return db.execute(q.order_by(ReconciliationProposal.id)).scalars().all()


def _budget(db, currency="TWD"):
    db.expire_all()
    return db.get(PolicyBudget, (MONTH, currency))


def _reload(db, model, pk):
    db.expire_all()
    return db.get(model, pk)


def _fee_statement(seed, db, run, lines=None, *, name="卡", **card):
    """A live September card statement printing bank-only lines (default: one 1200 年費) nothing covers."""
    account = _card(seed, db, name, **card)
    stmt, printed, _ = _submit(db, run, account, lines or [_line(1, DAY, "年費", 1200, "fee")])
    cases = [c for c in _cases(db, stmt.id, "open") if c.kind == "line_unmatched"]
    assert cases and all((c.context or {}).get("hint") == "bank_only" for c in cases)
    return SimpleNamespace(card=account, stmt=stmt, lines=printed, cases=cases)


def _only_candidate(session, statement_id, case_id=None):
    statement = session.get(AccountStatement, statement_id)
    found = policy_service.collect_candidates(session, statement, settings_service.reconciliation_policy(session))
    (candidate,) = [c for c in found if case_id is None or c.case_id == case_id]
    return statement, candidate


# --- off / on -----------------------------------------------------------------------------------------------------

def test_policy_off_does_nothing_and_audits_nothing(seed, db_session, run):
    s = _fee_statement(seed, db_session, run)
    assert settings_service.reconciliation_policy(db_session)["enabled"] is False

    result = policy_service.run_policy(db_session, s.stmt.id)

    assert result == PolicyResult(applied=[], proposed=[], skipped={})
    assert _actions(db_session) == [] and _booked(db_session) == [] and _proposals(db_session) == []
    assert _audit(db_session, *POLICY_EVENTS) == [] and _budget(db_session) is None
    assert _reload(db_session, ReconciliationCase, s.cases[0].id).status == "open"


def test_policy_on_books_a_bank_only_fee_as_auto_and_charges_the_budget(seed, db_session, run):
    s = _fee_statement(seed, db_session, run)
    _set_policy(db_session, SYSTEM_ON)

    result = policy_service.run_policy(db_session, s.stmt.id)

    (row,) = _actions(db_session)
    assert result.applied == [row.id] and result.proposed == []
    assert (row.action, row.actor, row.status, row.params, row.proposal_id) == (
        "create_system_entry", "policy", "applied", {"kind": "fee"}, None)
    assert row.after["policy_budget"] == {"month": "2026-10-01", "currency": "TWD", "amount": "1200"}
    (entry,) = _booked(db_session)
    assert (entry.kind, entry.amount, entry.source, entry.account_id) == ("fee", Decimal("-1200"), "statement",
                                                                         s.card.id)
    (claim,) = _active(db_session, s.stmt.id)
    assert (claim.entry_id, claim.match_kind) == (entry.id, "auto")
    budget = _budget(db_session)
    assert (budget.used_count, budget.used_amount) == (1, Decimal("1200"))
    assert _reload(db_session, ReconciliationCase, s.cases[0].id).status == "resolved"
    (applied,) = _audit(db_session, *POLICY_EVENTS)
    assert (applied.event, applied.actor, applied.action_id, applied.case_id) == (
        "policy_applied", "policy", row.id, s.cases[0].id)

    again = policy_service.run_policy(db_session, s.stmt.id)  # nothing left to do: no second charge
    assert again.applied == [] and _budget(db_session).used_count == 1


def test_an_excluded_statement_is_left_alone(seed, db_session, run):
    s = _fee_statement(seed, db_session, run)
    _set_policy(db_session, SYSTEM_ON)
    s.stmt.needs_recheck = True
    db_session.commit()

    assert policy_service.run_policy(db_session, s.stmt.id) == PolicyResult(applied=[], proposed=[], skipped={})
    assert _actions(db_session) == [] and _audit(db_session, *POLICY_EVENTS) == []


def test_a_disabled_action_is_not_tried(seed, db_session, run):
    s = _fee_statement(seed, db_session, run)
    _set_policy(db_session, {"enabled": True})  # create_system_entry stays off

    result = policy_service.run_policy(db_session, s.stmt.id)

    assert result.applied == [] and result.proposed == []
    assert _actions(db_session) == [] and _proposals(db_session) == []


# --- caps ---------------------------------------------------------------------------------------------------------

def test_over_max_amount_becomes_a_policy_proposal(seed, db_session, run):
    s = _fee_statement(seed, db_session, run)
    _set_policy(db_session, {"enabled": True,
                             "actions": {"create_system_entry": {"enabled": True, "max_amount": "1000"}}})

    result = policy_service.run_policy(db_session, s.stmt.id)

    (proposal,) = _proposals(db_session)
    assert result.applied == [] and result.proposed == [proposal.id]
    assert (proposal.author, proposal.action, proposal.params, proposal.rationale, proposal.confidence,
            proposal.status) == ("policy", "create_system_entry", {"kind": "fee"}, "policy: max_amount",
                                 Decimal("1"), "pending")
    assert _reload(db_session, ReconciliationCase, s.cases[0].id).status == "proposed"
    assert _actions(db_session) == [] and _booked(db_session) == []
    assert _budget(db_session) is None
    (proposed,) = _audit(db_session, "policy_proposed")
    assert proposed.detail["proposal_id"] == proposal.id and proposed.detail["reason"] == "max_amount"

    assert policy_service.run_policy(db_session, s.stmt.id).proposed == []  # a proposed case is not retried


def test_per_statement_count_cap_turns_the_second_candidate_into_a_proposal(seed, db_session, run):
    s = _fee_statement(seed, db_session, run, [_line(1, DAY, "年費", 1200, "fee"),
                                               _line(2, DAY, "循環利息", 300, "interest")])
    _set_policy(db_session, {**SYSTEM_ON, "per_statement_count": 1})

    result = policy_service.run_policy(db_session, s.stmt.id)

    (row,) = _actions(db_session)
    (proposal,) = _proposals(db_session)
    assert result.applied == [row.id] and result.proposed == [proposal.id]
    assert row.case_id == s.cases[0].id and proposal.case_id == s.cases[1].id
    assert (proposal.author, proposal.action, proposal.params, proposal.rationale) == (
        "policy", "create_system_entry", {"kind": "interest"}, "policy: per_statement_count")
    assert _budget(db_session).used_count == 1


@pytest.mark.parametrize("row_exists", [False, True], ids=["new_month", "existing_row"])
def test_monthly_cap_race_applies_one_and_proposes_the_other(seed, db_session, run, pg_engine, row_exists):
    """Review Focus 4: two statements at a 20,000 monthly cap, 15,000 each, in parallel: the second waits on the
    budget row the first reserved (on its insert for a new month, on its FOR UPDATE otherwise), then sees it charged
    and proposes."""
    first_stmt = _fee_statement(seed, db_session, run, [_line(1, DAY, "年費", 15000, "fee")], name="卡一")
    second_stmt = _fee_statement(seed, db_session, run, [_line(1, DAY, "年費", 15000, "fee")], name="卡二")
    _set_policy(db_session, {"enabled": True, "monthly_total": "20000", "per_statement_total": "50000",
                             "actions": {"create_system_entry": {"enabled": True, "max_amount": "20000"}}})
    if row_exists:
        db_session.add(PolicyBudget(month=MONTH, currency="TWD", used_amount=Decimal("0"), used_count=0))
        db_session.commit()
    applied, results = [], []

    def first(session):
        statement, candidate = _only_candidate(session, first_stmt.stmt.id)
        applied.append(policy_service.apply_candidate(session, statement, candidate, month=MONTH).id)

    def second(session):
        results.append(policy_service.run_policy(session, second_stmt.stmt.id))

    assert race(pg_engine, first, second) == "committed"

    (result,) = results
    (proposal,) = _proposals(db_session)
    assert result.applied == [] and result.proposed == [proposal.id]
    assert proposal.case_id == second_stmt.cases[0].id and proposal.rationale == "policy: monthly_total"
    assert [a.id for a in _actions(db_session)] == applied
    assert [e.account_id for e in _booked(db_session)] == [first_stmt.card.id]
    budget = _budget(db_session)
    assert (budget.used_amount, budget.used_count) == (Decimal("15000"), 1)


def test_a_reservation_holds_the_budget_row_while_its_run_waits_for_the_statement(seed, db_session, run, pg_engine):
    """The window between reservation and charge: run A has reserved the budget row and waits for its statement
    (held by an owner transaction); run B on another statement must wait for A's budget row, not read the stale
    total — else both would spend 15,000 of a 20,000 month."""
    stmt_a = _fee_statement(seed, db_session, run, [_line(1, DAY, "年費", 15000, "fee")], name="卡一")
    stmt_b = _fee_statement(seed, db_session, run, [_line(1, DAY, "年費", 15000, "fee")], name="卡二")
    _set_policy(db_session, {"enabled": True, "monthly_total": "20000", "per_statement_total": "50000",
                             "actions": {"create_system_entry": {"enabled": True, "max_amount": "20000"}}})
    db_session.add(PolicyBudget(month=MONTH, currency="TWD", used_amount=Decimal("0"), used_count=0))
    db_session.commit()
    factory = sessionmaker(bind=pg_engine, autoflush=False)
    owner, session_a, session_b = factory(), factory(), factory()
    pids = {name: s.execute(text("SELECT pg_backend_pid()")).scalar_one()
            for name, s in (("owner", owner), ("a", session_a), ("b", session_b))}
    results: dict = {}

    def policy_run(name, session, statement_id):
        try:
            results[name] = policy_service.run_policy(session, statement_id)
        except Exception as exc:  # noqa: BLE001  (asserted below)
            session.rollback()
            results[name] = exc

    owner.execute(select(AccountStatement).where(AccountStatement.id == stmt_a.stmt.id).with_for_update())
    threads = {"a": threading.Thread(target=policy_run, args=("a", session_a, stmt_a.stmt.id), daemon=True),
               "b": threading.Thread(target=policy_run, args=("b", session_b, stmt_b.stmt.id), daemon=True)}
    try:
        threads["a"].start()
        wait_until_blocked(pg_engine, pids["a"], blocker_pid=pids["owner"])  # reserved, waiting for its statement
        threads["b"].start()
        wait_until_blocked(pg_engine, pids["b"], blocker_pid=pids["a"])  # on the budget row A reserved
    finally:
        owner.commit()
        for thread in threads.values():
            thread.join(15)
        for session in (owner, session_a, session_b):
            session.close()
    assert not any(thread.is_alive() for thread in threads.values()), results

    assert len(results["a"].applied) == 1 and results["a"].proposed == []
    assert results["b"].applied == [] and len(results["b"].proposed) == 1
    budget = _budget(db_session)
    assert (budget.used_amount, budget.used_count) == (Decimal("15000"), 1)


def test_same_statement_cap_race_is_caught_by_the_precheck_under_the_lock(seed, db_session, run, pg_engine):
    """Two policy runs on two cases of one statement at per_statement_count=1: the second run's pre-filter passes
    (nothing committed yet), then the recount under the statement lock refuses it → a proposal."""
    s = _fee_statement(seed, db_session, run, [_line(1, DAY, "年費", 1200, "fee"),
                                               _line(2, DAY, "循環利息", 300, "interest")])
    _set_policy(db_session, {**SYSTEM_ON, "per_statement_count": 1})
    early, late = s.cases
    applied, results = [], []

    def first(session):  # the later case, so the racing run meets the earlier one first
        statement, candidate = _only_candidate(session, s.stmt.id, late.id)
        applied.append(policy_service.apply_candidate(session, statement, candidate, month=MONTH).id)

    def second(session):
        results.append(policy_service.run_policy(session, s.stmt.id))

    assert race(pg_engine, first, second) == "committed"

    (result,) = results
    (proposal,) = _proposals(db_session)
    assert result.applied == [] and result.proposed == [proposal.id]
    assert proposal.case_id == early.id and proposal.rationale == "policy: per_statement_count"
    assert [(a.id, a.case_id, a.actor) for a in _actions(db_session)] == [(applied[0], late.id, "policy")]
    assert _budget(db_session).used_count == 1


# --- failures -----------------------------------------------------------------------------------------------------

def test_a_failing_apply_is_skipped_and_the_loop_goes_on(seed, db_session, run):
    """attach_fee meets an existing FX fee child (`fee_exists`): rolled back with its reservation, audited, no
    `failed` row; the next candidate still applies and alone charges the budget."""
    card = _card(seed, db_session, fx_fee_pct=Decimal("1.5"))
    entry = seed.entry(card, "-1000", day=date(2026, 9, 15), merchant="AMAZON", original_amount=Decimal("-30"),
                       original_currency="USD")
    seed.entry(card, "-5", kind="fee", day=date(2026, 9, 15), parent_entry_id=entry.id,
               name=ews.FX_FEE_CHILD_NAME)
    db_session.commit()
    stmt, lines, _ = _submit(db_session, run, card, [
        _line(1, date(2026, 9, 15), "AMAZON", 1015, foreign_amount=Decimal("-30"), foreign_currency="USD"),
        _line(2, DAY, "年費", 1200, "fee")])
    (delta,) = [c for c in _cases(db_session, stmt.id, "open") if c.kind == "amount_delta"]
    (fee,) = [c for c in _cases(db_session, stmt.id, "open") if c.kind == "line_unmatched"]
    assert delta.context["fee_matches"] is True and delta.id < fee.id
    _set_policy(db_session, {"enabled": True, "actions": {"create_system_entry": {"enabled": True},
                                                          "attach_fee": {"enabled": True}}})

    result = policy_service.run_policy(db_session, stmt.id)

    (row,) = _actions(db_session)
    assert result.applied == [row.id] and result.proposed == [] and result.skipped == {"fee_exists": 1}
    assert (row.action, row.case_id, row.status) == ("create_system_entry", fee.id, "applied")
    assert {a.status for a in _actions(db_session)} == {"applied"}
    (skipped,) = _audit(db_session, "policy_skipped")
    assert (skipped.case_id, skipped.detail["code"], skipped.detail["action"]) == (delta.id, "fee_exists",
                                                                                   "attach_fee")
    budget = _budget(db_session)
    assert (budget.used_count, budget.used_amount) == (1, Decimal("1200"))
    assert _reload(db_session, ReconciliationCase, delta.id).status == "open"


def test_a_candidate_resolved_by_the_owner_during_the_lock_wait_is_skipped(seed, db_session, run, pg_engine):
    s = _fee_statement(seed, db_session, run)
    _set_policy(db_session, SYSTEM_ON)
    case = s.cases[0]
    results = []

    def owner(session):
        engine.apply_action(session, case_id=case.id, action="dismiss", params={"note": "not ours"}, actor="owner",
                            label="owner-test")

    def policy(session):
        results.append(policy_service.run_policy(session, s.stmt.id))

    assert race(pg_engine, owner, policy) == "committed"

    (result,) = results
    assert result.applied == [] and result.proposed == [] and result.skipped == {"case_closed": 1}
    assert [(a.action, a.actor) for a in _actions(db_session)] == [("dismiss", "owner")]
    assert _booked(db_session) == [] and _proposals(db_session) == []
    budget = _budget(db_session)
    assert budget is None or (budget.used_count, budget.used_amount) == (0, Decimal("0"))
    (skipped,) = _audit(db_session, "policy_skipped")
    assert skipped.detail["code"] == "case_closed"


def test_a_foreign_currency_statement_is_skipped(seed, db_session, run):
    s = _fee_statement(seed, db_session, run, currency="JPY")
    _set_policy(db_session, SYSTEM_ON)

    result = policy_service.run_policy(db_session, s.stmt.id)

    assert result == PolicyResult(applied=[], proposed=[], skipped={"currency": 1})
    assert _actions(db_session) == [] and _proposals(db_session) == []
    (skipped,) = _audit(db_session, *POLICY_EVENTS)
    assert (skipped.event, skipped.statement_id, skipped.detail["code"]) == ("policy_skipped", s.stmt.id,
                                                                             "currency")


# --- agent proposals ----------------------------------------------------------------------------------------------

@pytest.fixture
def ambiguous(seed, db_session, run):
    """Two identical 580 entries for one 580 line → an ambiguous case; hermes proposes the first."""
    card = _card(seed, db_session)
    seed.entry(card, "-1", day=date(2026, 8, 1), merchant="墊高編號")  # keeps entry ids apart from the line id
    first = seed.entry(card, "-580", day=DAY, merchant="全聯")
    second = seed.entry(card, "-580", day=DAY, merchant="全聯")
    db_session.commit()
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)])
    (case,) = [c for c in _cases(db_session, stmt.id, "open") if c.kind == "ambiguous"]
    proposal = proposal_service.create_proposal(
        db_session, case_id=case.id, author="hermes",
        payload=ProposalIn(action="match", params={"line_id": line.id, "entry_id": first.id},
                           rationale="same day", confidence=Decimal("0.7")))
    db_session.commit()
    return SimpleNamespace(card=card, stmt=stmt, line=line, case=case, first=first, second=second,
                           proposal=proposal)


MATCH_ON = {"enabled": True, "accept_agent_proposals": True,
            "actions": {"match": {"enabled": True, "max_amount": "1000"}}}


def test_agent_proposals_stay_pending_while_accept_is_off(ambiguous, db_session):
    s = ambiguous
    _set_policy(db_session, {**MATCH_ON, "accept_agent_proposals": False})
    s.second.amount = Decimal("-581")  # a single admissible representation would exist
    db_session.commit()

    result = policy_service.run_policy(db_session, s.stmt.id)

    assert result.applied == [] and _actions(db_session) == []
    assert _reload(db_session, ReconciliationProposal, s.proposal.id).status == "pending"
    assert _reload(db_session, ReconciliationCase, s.case.id).status == "proposed"


def test_a_single_admissible_agent_match_is_applied_as_agent(ambiguous, db_session):
    s = ambiguous
    _set_policy(db_session, MATCH_ON)
    s.second.amount = Decimal("-581")  # the second candidate no longer conserves the line
    db_session.commit()

    result = policy_service.run_policy(db_session, s.stmt.id)

    (row,) = _actions(db_session)
    assert result.applied == [row.id]
    assert (row.action, row.actor, row.proposal_id) == ("match", "policy", s.proposal.id)
    proposal = _reload(db_session, ReconciliationProposal, s.proposal.id)
    assert (proposal.status, proposal.applied_action_id) == ("applied", row.id)
    (claim,) = _active(db_session, s.stmt.id)
    assert (claim.entry_id, claim.match_kind) == (s.first.id, "agent")
    assert _budget(db_session).used_count == 1


def test_two_admissible_representations_skip_the_agent_match(ambiguous, db_session):
    s = ambiguous
    _set_policy(db_session, MATCH_ON)

    result = policy_service.run_policy(db_session, s.stmt.id)

    assert result.applied == [] and result.proposed == [] and result.skipped == {"not_single_admissible": 1}
    assert _actions(db_session) == [] and _active(db_session, s.stmt.id) == []
    assert _reload(db_session, ReconciliationProposal, s.proposal.id).status == "pending"
    (skipped,) = _audit(db_session, "policy_skipped")
    assert (skipped.case_id, skipped.detail["code"]) == (s.case.id, "not_single_admissible")
    assert _budget(db_session) is None or _budget(db_session).used_count == 0


# --- routes -------------------------------------------------------------------------------------------------------

def test_reconcile_route_reports_policy_counts(client, seed, db_session, run):
    s = _fee_statement(seed, db_session, run, [_line(1, DAY, "年費", 1200, "fee"),
                                               _line(2, DAY, "循環利息", 300, "interest")])
    _set_policy(db_session, {**SYSTEM_ON, "per_statement_count": 1})

    response = client.post(f"/accounts/{s.card.id}/statements/{s.stmt.id}/reconcile", headers=S)

    assert response.status_code == 200, response.text
    assert response.json()["policy"] == {"applied": 1, "proposed": 1}


def test_reconcile_route_never_fails_on_a_policy_error(client, seed, db_session, run, monkeypatch):
    s = _fee_statement(seed, db_session, run)
    _set_policy(db_session, SYSTEM_ON)

    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(policy_service, "run_policy", broken)
    response = client.post(f"/accounts/{s.card.id}/statements/{s.stmt.id}/reconcile", headers=S)

    assert response.status_code == 200, response.text
    assert response.json()["policy"] == {"applied": 0, "proposed": 0}
    (skipped,) = _audit(db_session, "policy_skipped")
    assert (skipped.statement_id, skipped.detail["code"]) == (s.stmt.id, "RuntimeError")


def _claim(client):
    enqueued = client.post("/statements/ingest/run", headers=H).json()
    claimed = client.post(f"/statements/ingest-runs/{enqueued['run_id']}/claim", headers=W)
    assert claimed.status_code == 200, claimed.text
    return claimed.json()


def _revision(lease, account, lines, total):
    return {"run_id": lease["run_id"], "lease_token": lease["lease_token"], "account_id": account.id, "kind": "card",
            "parser": "test", "parser_version": "1", "currency": account.currency, "period_start": "2026-09-01",
            "period_end": "2026-09-30", "statement_total": total, "lines": lines, "raw": {}}


FEE_LINE = {"seq": 1, "posted_date": "2026-09-03", "merchant_raw": "年費", "printed_amount": "1200",
            "line_kind": "fee"}


def test_revision_route_runs_policy_for_a_live_statement(client, seed, db_session):
    card = _card(seed, db_session)
    _set_policy(db_session, SYSTEM_ON)
    lease = _claim(client)

    response = client.post("/statements/revisions", headers=W, json=_revision(lease, card, [FEE_LINE], "1200"))

    assert response.status_code == 201, response.text
    assert response.json()["mode"] == "live"
    (row,) = _actions(db_session)
    assert (row.actor, row.action, row.run_id) == ("policy", "create_system_entry", lease["run_id"])
    assert [e.account_id for e in _booked(db_session)] == [card.id]


def test_revision_route_skips_policy_for_a_historical_statement(client, seed, db_session, monkeypatch):
    card = _card(seed, db_session, live=False)
    _set_policy(db_session, SYSTEM_ON)
    calls = []
    monkeypatch.setattr(policy_service, "run_policy", lambda *a, **kw: calls.append((a, kw)))
    lease = _claim(client)

    response = client.post("/statements/revisions", headers=W, json=_revision(lease, card, [FEE_LINE], "1200"))

    assert response.status_code == 201, response.text
    assert response.json()["mode"] == "historical"
    assert calls == [] and _actions(db_session) == []


def test_sweep_route_runs_policy_per_statement(client, seed, db_session, run):
    s = _fee_statement(seed, db_session, run)
    _set_policy(db_session, SYSTEM_ON)
    s.stmt.needs_recheck = True  # pending for the sweep; the reconcile clears it before policy runs
    db_session.commit()
    worker_run, token = run

    response = client.post("/reconciliation/sweep", headers=W, json={"run_id": worker_run.id, "lease_token": token})

    assert response.status_code == 200, response.text
    (row,) = _actions(db_session)
    assert (row.actor, row.run_id) == ("policy", worker_run.id)
