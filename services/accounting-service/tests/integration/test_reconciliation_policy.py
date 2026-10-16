"""The policy engine (R1c Task 10; spec §7.3, rulings 6, 10, 22, 31; Review Focus 4): shipped off; bank-only system
entries and matching FX fees applied by policy under per-action, per-statement and monthly caps, the budget row
reserved before the statement lock and charged only after the precheck under the lock, over-cap candidates turned
into policy proposals, agent `match` proposals accepted only with a single admissible representation, and the
reconcile / revision / sweep routes running it after their commit. Synthetic data only."""
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
    AccountStatement, LedgerEntry, PolicyBudget, ReconciliationAction, ReconciliationAudit, ReconciliationCase,
    ReconciliationProposal,
)
from app.schemas.reconciliation import ProposalIn
from app.services import entry_write_service as ews
from app.services.errors import CodedConflictError
from app.services import policy_service, proposal_service, settings_service
from app.services import reconciliation_service as rec
from app.services.policy_service import PolicyResult
from app.services.reconciliation_actions import audit, engine
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


def _fx_fee_statement(seed, db, run, *, existing_child=False):
    """A live September card statement whose 1015 AMAZON line meets a −1000 USD entry: one open amount_delta case
    with fee_matches (expected FX fee 1.5 % = 15). `existing_child=True` books the fee child beforehand (fee_exists)."""
    card = _card(seed, db, fx_fee_pct=Decimal("1.5"))
    entry = seed.entry(card, "-1000", day=date(2026, 9, 15), merchant="AMAZON", original_amount=Decimal("-30"),
                       original_currency="USD")
    if existing_child:
        seed.entry(card, "-5", kind="fee", day=date(2026, 9, 15), parent_entry_id=entry.id, name=ews.FX_FEE_CHILD_NAME)
    db.commit()
    stmt, lines, _ = _submit(db, run, card, [
        _line(1, date(2026, 9, 15), "AMAZON", 1015, foreign_amount=Decimal("-30"), foreign_currency="USD")])
    (case,) = [c for c in _cases(db, stmt.id, "open") if c.kind == "amount_delta"]
    assert case.context["fee_matches"] is True
    return SimpleNamespace(card=card, stmt=stmt, case=case, entry=entry, fee=Decimal("15"))


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


def test_policy_coverage_survives_a_reconcile_and_reverts(seed, db_session, run):
    s = _fee_statement(seed, db_session, run)
    _set_policy(db_session, SYSTEM_ON)
    policy_service.run_policy(db_session, s.stmt.id)
    (row,) = _actions(db_session)
    (claim,) = _active(db_session, s.stmt.id)
    assert claim.match_kind == "agent" and claim.id in row.after["coverage_ids"]

    rec.reconcile(db_session, s.stmt.id)  # the owner's reconcile must not rewrite the policy's claim
    db_session.commit()

    (same,) = _active(db_session, s.stmt.id)
    assert (same.id, same.match_kind, same.match_rule) == (claim.id, "agent", "create_system_entry")

    engine.revert_action(db_session, action_id=row.id, actor="owner", label="owner-test")
    db_session.commit()
    assert _booked(db_session) == [] and _active(db_session, s.stmt.id) == []
    assert _reload(db_session, ReconciliationAction, row.id).status == "reverted"
    assert _reload(db_session, ReconciliationCase, s.cases[0].id).status == "open"


def test_policy_on_books_a_bank_only_fee_as_agent_and_charges_the_budget(seed, db_session, run):
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
    assert (claim.entry_id, claim.match_kind) == (entry.id, "agent")
    budget = _budget(db_session)
    assert (budget.used_count, budget.used_amount) == (1, Decimal("1200"))
    assert _reload(db_session, ReconciliationCase, s.cases[0].id).status == "resolved"
    (applied,) = _audit(db_session, *POLICY_EVENTS)
    assert (applied.event, applied.actor, applied.action_id, applied.case_id) == (
        "policy_applied", "policy", row.id, s.cases[0].id)

    again = policy_service.run_policy(db_session, s.stmt.id)  # nothing left to do: no second charge
    assert again.applied == [] and _budget(db_session).used_count == 1


def test_policy_books_an_interest_line_without_a_pattern(seed, db_session, run):
    env = _fee_statement(seed, db_session, run, lines=[_line(1, DAY, "INTEREST CREDIT", 12, "interest")])
    assert env.cases[0].context == {"hint": "bank_only", "pattern": None}
    _set_policy(db_session, SYSTEM_ON)

    result = policy_service.run_policy(db_session, env.stmt.id)

    booked = _booked(db_session, env.card.id)
    (row,) = _actions(db_session)
    assert len(booked) == 1 and booked[0].kind == "interest" and result.applied == [row.id]


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

def _ambiguous_case(seed, db_session, run, *, author="hermes"):
    """Two identical 580 entries for one 580 line → an ambiguous case; `author` proposes the first."""
    card = _card(seed, db_session)
    seed.entry(card, "-1", day=date(2026, 8, 1), merchant="墊高編號")  # keeps entry ids apart from the line id
    first = seed.entry(card, "-580", day=DAY, merchant="全聯")
    second = seed.entry(card, "-580", day=DAY, merchant="全聯")
    db_session.commit()
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)])
    (case,) = [c for c in _cases(db_session, stmt.id, "open") if c.kind == "ambiguous"]
    proposal = proposal_service.create_proposal(
        db_session, case_id=case.id, author=author,
        payload=ProposalIn(action="match", params={"line_id": line.id, "entry_id": first.id},
                           rationale="same day", confidence=Decimal("0.7")))
    db_session.commit()
    return SimpleNamespace(card=card, stmt=stmt, line=line, case=case, first=first, second=second,
                           proposal=proposal)


@pytest.fixture
def ambiguous(seed, db_session, run):
    return _ambiguous_case(seed, db_session, run)


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


@pytest.mark.parametrize("author", ["spa", "worker", "nobody"])
def test_only_propose_only_tokens_are_agents(seed, db_session, run, author):
    s = _ambiguous_case(seed, db_session, run, author=author)
    _set_policy(db_session, MATCH_ON)
    s.second.amount = Decimal("-581")
    db_session.commit()

    result = policy_service.run_policy(db_session, s.stmt.id)

    assert result.applied == [] and _actions(db_session) == []
    assert _reload(db_session, ReconciliationProposal, s.proposal.id).status == "pending"


def test_agent_authors_without_scopes_are_every_non_policy_author(monkeypatch):
    monkeypatch.delenv(auth.SCOPES_ENV, raising=False)
    assert policy_service._agent_authors(["hermes", "spa", "policy"]) == {"hermes", "spa"}


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


def test_attach_fee_candidate_is_recomputed_under_the_lock(seed, db_session, run):
    s = _fx_fee_statement(seed, db_session, run)
    _set_policy(db_session, {"enabled": True, "actions": {"attach_fee": {"enabled": True}}})
    statement, candidate = _only_candidate(db_session, s.stmt.id, s.case.id)
    assert (candidate.action, candidate.amount) == ("attach_fee", s.fee)

    # the entry is edited after the reconcile (dirty tracking off: no sweep release, context.residual is stale)
    entry = db_session.get(LedgerEntry, s.entry.id)
    entry.amount = Decimal(entry.amount) - Decimal("1")
    db_session.commit()

    with pytest.raises(CodedConflictError) as info:
        policy_service.apply_candidate(db_session, statement, candidate, month=MONTH)
    db_session.rollback()
    assert info.value.code == "candidate_changed"
    assert _budget(db_session) is None or _budget(db_session).used_count == 0
    assert _actions(db_session) == []


def test_a_deterministic_skip_is_audited_once_per_case_version(seed, db_session, run):
    s = _fx_fee_statement(seed, db_session, run, existing_child=True)
    _set_policy(db_session, {"enabled": True, "actions": {"attach_fee": {"enabled": True}}})

    first = policy_service.run_policy(db_session, s.stmt.id)
    second = policy_service.run_policy(db_session, s.stmt.id)

    assert first.skipped == second.skipped == {"fee_exists": 1}
    (skipped,) = _audit(db_session, "policy_skipped")
    assert (skipped.case_id, skipped.detail["code"], skipped.detail["case_version"]) == (
        s.case.id, "fee_exists", _reload(db_session, ReconciliationCase, s.case.id).version)

    first_version = skipped.detail["case_version"]
    db_session.execute(update(ReconciliationCase).where(ReconciliationCase.id == s.case.id)
                       .values(version=ReconciliationCase.version + 1))
    db_session.commit()
    policy_service.run_policy(db_session, s.stmt.id)
    rows = _audit(db_session, "policy_skipped")
    assert sorted(r.detail["case_version"] for r in rows) == [first_version, first_version + 1]


def test_concurrent_skips_audit_once(seed, db_session, run, pg_engine, monkeypatch):
    """Two real `_skip` calls for the same candidate: A takes statement -> case, inserts and (deferred here) commits;
    B must acquire the statement only after A's commit, find the row and insert nothing. The hooks record the
    order in which the locks were ACQUIRED and the inserts happened."""
    s = _fx_fee_statement(seed, db_session, run, existing_child=True)
    _set_policy(db_session, {"enabled": True, "actions": {"attach_fee": {"enabled": True}}})
    statement, candidate = _only_candidate(db_session, s.stmt.id, s.case.id)
    db_session.commit()
    order: list[tuple[str, str]] = []
    real_lock, real_record = rec._lock_statement, audit.record

    def lock(db, statement_id):
        row = real_lock(db, statement_id)
        order.append(("locked", db.info["name"]))
        return row

    def record(db, **kw):
        order.append(("insert", db.info["name"]))
        return real_record(db, **kw)

    monkeypatch.setattr(rec, "_lock_statement", lock)
    monkeypatch.setattr(audit, "record", record)
    factory = sessionmaker(bind=pg_engine, autoflush=False)
    a, b = factory(), factory()
    a.info["name"], b.info["name"] = "A", "B"
    real_commit = a.commit
    monkeypatch.setattr(a, "commit", lambda: None)  # A's _skip returns still holding its locks
    outcome: queue.Queue = queue.Queue()
    thread = None
    try:
        policy_service._skip(a, s.stmt.id, candidate, "fee_exists", PolicyResult(), run_id=None)
        thread = threading.Thread(target=lambda: outcome.put(
            policy_service._skip(b, s.stmt.id, candidate, "fee_exists", PolicyResult(), run_id=None)), daemon=True)
        thread.start()
        thread.join(0.5)
        assert thread.is_alive(), "B must wait for A's statement lock"
        order.append(("commit", "A"))
        real_commit()
    finally:
        a.close()
        if thread is not None:
            thread.join(5)  # reclaimed after A is released; B rolls back / closes its own session below
            assert not thread.is_alive(), "B is still blocked after A committed"
        b.rollback()
        b.close()
    outcome.get_nowait()
    assert order == [("locked", "A"), ("insert", "A"), ("commit", "A"), ("locked", "B")]
    (skipped,) = _audit(db_session, "policy_skipped")
    assert skipped.detail["case_version"] == _reload(db_session, ReconciliationCase, s.case.id).version


def test_a_superseded_agent_proposal_is_marked_and_skipped_once(ambiguous, db_session, monkeypatch):
    """The case version moves on between the pre-filter and the engine's locks: the engine raises
    `proposal_superseded`; the policy persists the mark, commits, then audits the skip once."""
    s = ambiguous
    _set_policy(db_session, MATCH_ON)
    s.second.amount = Decimal("-581")
    db_session.commit()
    statement, candidate = _only_candidate(db_session, s.stmt.id, s.case.id)
    real_prefilter = policy_service._prefilter

    def prefilter(db, cand, policy):
        verdict = real_prefilter(db, cand, policy)
        db.execute(update(ReconciliationCase).where(ReconciliationCase.id == s.case.id)
                   .values(version=ReconciliationCase.version + 1))
        db.commit()
        return verdict

    monkeypatch.setattr(policy_service, "_prefilter", prefilter)
    real_skip, entered_in_transaction = policy_service._skip, []

    def skip(db, *args, **kwargs):  # the supersede mark must be committed before the skip takes its locks
        entered_in_transaction.append(db.in_transaction())
        return real_skip(db, *args, **kwargs)

    monkeypatch.setattr(policy_service, "_skip", skip)
    result = PolicyResult()

    policy_service._run_candidate(db_session, s.stmt.id, candidate, result, month=MONTH, run_id=None)

    assert entered_in_transaction == [False]
    assert result.skipped == {"proposal_superseded": 1}
    assert _reload(db_session, ReconciliationProposal, s.proposal.id).status == "superseded"
    (skipped,) = _audit(db_session, "policy_skipped")
    assert skipped.detail["code"] == "proposal_superseded"


# --- R6b.1 backfill_entry ---------------------------------------------------------------------------------------

BACKFILL_ON = {"enabled": True, "actions": {"backfill_entry": {"enabled": True, "max_amount": "5000"}}}


def _purchase_statement(seed, db, run, name="卡", **card):
    account = _card(seed, db, name, **card)
    stmt, (line,), _ = _submit(db, run, account, [_line(1, DAY, "全聯", 580)])
    (case,) = _cases(db, stmt.id, "open")
    return SimpleNamespace(card=account, stmt=stmt, line=line, case=case)


def test_policy_backfills_after_the_grace_and_not_before(seed, db_session, run, today):
    s = _purchase_statement(seed, db_session, run)
    _set_policy(db_session, {**BACKFILL_ON, "backfill_grace_days": 14})
    assert policy_service.run_policy(db_session, s.stmt.id).applied == [] and _booked(db_session) == []
    assert policy_service.run_policy(db_session, s.stmt.id).skipped.get("not_eligible", 0) >= 0
    _set_policy(db_session, {**BACKFILL_ON, "backfill_grace_days": 10})
    result = policy_service.run_policy(db_session, s.stmt.id)
    (row,) = _actions(db_session)
    assert result.applied == [row.id] and row.action == "backfill_entry" and row.params == {"kind": "expense"}
    (entry,) = _booked(db_session)
    assert entry.kind == "expense" and entry.needs_review is True


def test_policy_backfill_respects_the_account_list_and_caps(seed, db_session, run):
    s = _purchase_statement(seed, db_session, run)
    _set_policy(db_session, {**BACKFILL_ON, "backfill_grace_days": 0, "backfill_accounts": [s.card.id + 1000]})
    assert policy_service.run_policy(db_session, s.stmt.id).applied == []
    _set_policy(db_session, {**BACKFILL_ON, "backfill_grace_days": 0, "backfill_accounts": [s.card.id],
                             "actions": {"backfill_entry": {"enabled": True, "max_amount": "100"}}})
    result = policy_service.run_policy(db_session, s.stmt.id)
    (p,) = _proposals(db_session, status="pending")
    assert result.proposed == [p.id] and p.action == "backfill_entry" and p.params == {"kind": "expense"}
    row, _ = proposal_service.apply_proposal(db_session, proposal_id=p.id, label="owner-test", request_id=None)
    db_session.commit()
    assert row.status == "applied" and len(_booked(db_session)) == 1


def test_policy_never_backfills_unknown_needs_peer_or_owner_deleted_lines(seed, db_session, run):
    account = _card(seed, db_session)
    stmt, lines, _ = _submit(db_session, run, account, [_line(1, DAY, "??", 10, "unknown"), _line(2, DAY, "繳款", -20000, "payment"),
                                                        _line(3, DAY, "全聯", 580)])
    _set_policy(db_session, {**BACKFILL_ON, "backfill_grace_days": 0})
    result = policy_service.run_policy(db_session, stmt.id)
    (row,) = _actions(db_session)
    assert result.applied == [row.id] and row.event_id == lines[2].event_id
    (case,) = [c for c in _cases(db_session, stmt.id, "resolved")]
    # Task 6 replaces with the real sweep
    db_session.delete(db_session.get(LedgerEntry, row.created_entry_ids[0]))
    row.status, row.inverse = "reverted", {**(row.inverse or {}), "deleted_by_event": 1}
    db_session.commit()
    rec.reconcile(db_session, stmt.id)
    db_session.commit()
    assert policy_service.run_policy(db_session, stmt.id).applied == []
