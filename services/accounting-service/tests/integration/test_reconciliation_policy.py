"""The policy engine (R1c Task 10; spec §7.3, rulings 6, 10, 22, 31; Review Focus 4): shipped off; bank-only system
entries and matching FX fees applied by policy under per-action, per-statement and monthly caps, the budget row
reserved before the statement lock and charged only after the precheck under the lock, over-cap candidates turned
into policy proposals, agent `match` proposals accepted only with a single admissible representation, and the
reconcile / revision / sweep routes running it after their commit. Synthetic data only."""
import queue
import threading
from datetime import date
from functools import partial
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.orm import sessionmaker

from app import auth
from app.models import (
    DIRTY_BARRIER_KEY, AccountStatement, EntryGroup, LedgerEntry, PolicyBudget, ReconciliationAction, ReconciliationAudit,
    ReconciliationCase, ReconciliationProposal,
)
from app.schemas.reconciliation import ProposalIn
from app.services import entry_write_service as ews
from app.services.errors import CodedConflictError
from app.services import coverage_service, policy_service, proposal_service, settings_service
from app.services import reconciliation_service as rec
from app.services import reconciliation_actions as registry
from app.services.policy_service import PolicyResult
from app.services.reconciliation_actions import audit, engine
from tests.helpers import make_entry, race, wait_until_blocked
from tests.integration.statement_fixtures import (  # noqa: F401  (run is a fixture)
    _active, _card, _cases, _open_beside_gap, _line, _submit, run,
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
ANCHOR_ON = {"enabled": True, "actions": {"anchor_opening": {"enabled": True, "max_amount": "200000"}}}
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


def test_policy_coverage_survives_a_reconcile_and_reverts(seed, db_session, run, dirty_on):
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


def test_policy_on_books_a_bank_only_fee_as_agent_and_charges_the_budget(seed, db_session, run, dirty_on):
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


def test_policy_books_an_interest_line_without_a_pattern(seed, db_session, run, dirty_on):
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


def test_per_statement_count_cap_turns_the_second_candidate_into_a_proposal(seed, db_session, run, dirty_on):
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
def test_monthly_cap_race_applies_one_and_proposes_the_other(seed, db_session, run, pg_engine, row_exists, dirty_on):
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


def test_a_reservation_holds_the_budget_row_while_its_run_waits_for_the_statement(seed, db_session, run, pg_engine,
                                                                                   dirty_on):
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


def test_same_statement_cap_race_is_caught_by_the_precheck_under_the_lock(seed, db_session, run, pg_engine, dirty_on):
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

def test_a_failing_apply_is_skipped_and_the_loop_goes_on(seed, db_session, run, dirty_on):
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


def test_preview_labels_an_agent_proposal_over_its_cap_as_a_skip(ambiguous, db_session):
    # R6b.7: the skip:<code> label through a real preview, and run_policy agrees (the agent's proposal stays pending)
    s = ambiguous
    _set_policy(db_session, {**MATCH_ON, "actions": {"match": {"enabled": True, "max_amount": "100"}}})
    s.second.amount = Decimal("-581")
    db_session.commit()
    statement = db_session.get(AccountStatement, s.stmt.id)
    decisions = policy_service.preview(db_session, statement, settings_service.reconciliation_policy(db_session))
    assert [(c.action, c.proposal_id, d) for c, d in decisions] == [("match", s.proposal.id, "skip:cap_exceeded")]
    result = policy_service.run_policy(db_session, s.stmt.id)
    assert (result.applied, result.proposed, result.skipped) == ([], [], {"cap_exceeded": 1})
    assert _reload(db_session, ReconciliationProposal, s.proposal.id).status == "pending"


# --- routes -------------------------------------------------------------------------------------------------------

def test_reconcile_route_reports_policy_counts(client, seed, db_session, run, dirty_on):
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


def test_revision_route_runs_policy_for_a_live_statement(client, seed, db_session, dirty_on):
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


def test_sweep_route_runs_policy_per_statement(client, seed, db_session, run, dirty_on):
    s = _fee_statement(seed, db_session, run)
    _set_policy(db_session, SYSTEM_ON)
    s.stmt.needs_recheck = True  # pending for the sweep; the reconcile clears it before policy runs
    db_session.commit()
    worker_run, token = run

    response = client.post("/reconciliation/sweep", headers=W, json={"run_id": worker_run.id, "lease_token": token})

    assert response.status_code == 200, response.text
    (row,) = _actions(db_session)
    assert (row.actor, row.run_id) == ("policy", worker_run.id)


def test_sweep_route_picks_up_a_clean_statement_with_a_creation_deleted_before_the_watermark(
        client, seed, db_session, run, dirty_on, monkeypatch):
    # PR #76 review S1: a pre-R6b deletion already swept (watermark past the delete event) and reconciled
    # (needs_recheck cleared) has no pending event; the pending selection must still list the statement so the first
    # daily sweep runs the catch-up retirement (spec §6 I2 / R6b.0)
    s = _fee_statement(seed, db_session, run)
    row, _ = engine.apply_action(db_session, case_id=s.cases[0].id, action="create_system_entry", params={"kind": "fee"},
                                 actor="owner", label="t")
    db_session.commit()
    rec.reconcile(db_session, s.stmt.id)
    db_session.commit()
    assert rec.pending_statement_ids(db_session) == []  # an applied creation whose entry exists is not stranded
    (entry_id,) = row.created_entry_ids
    db_session.delete(db_session.get(LedgerEntry, entry_id))
    db_session.commit()
    with monkeypatch.context() as m:  # the pre-R6b sweep: the line is released, nothing is retired
        m.setattr(coverage_service, "_retire_creations", lambda *args: None)
        m.setattr(coverage_service, "_retire_stranded_creations", lambda *args: None)
        rec.reconcile(db_session, s.stmt.id)
        db_session.commit()
    db_session.expire_all()
    statement = db_session.get(AccountStatement, s.stmt.id)
    last_event = db_session.execute(text("SELECT max(id) FROM coverage_dirty")).scalar_one()
    assert statement.needs_recheck is False and statement.swept_through_event_id == last_event
    assert db_session.get(ReconciliationAction, row.id).status == "applied"
    assert coverage_service.sweep_pending(db_session) == [s.stmt.id]
    assert rec.pending_statement_ids(db_session) == [s.stmt.id]
    worker_run, token = run

    response = client.post("/reconciliation/sweep", headers=W, json={"run_id": worker_run.id, "lease_token": token})

    assert response.status_code == 200, response.text
    assert response.json()["statements"] == 1 and response.json()["errors"] == []
    db_session.expire_all()
    retired = db_session.get(ReconciliationAction, row.id)
    assert retired.status == "reverted" and retired.inverse["deleted_before_watermark"] is True
    assert coverage_service.sweep_pending(db_session) == [] and rec.pending_statement_ids(db_session) == []


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
    (case,) = _open_beside_gap(db, stmt.id, "-580.0000")
    return SimpleNamespace(card=account, stmt=stmt, line=line, case=case)


def test_policy_backfills_after_the_grace_and_not_before(seed, db_session, run, today, dirty_on):
    s = _purchase_statement(seed, db_session, run)
    _set_policy(db_session, {**BACKFILL_ON, "backfill_grace_days": 14})
    assert policy_service.run_policy(db_session, s.stmt.id).applied == [] and _booked(db_session) == []
    before_grace = policy_service.run_policy(db_session, s.stmt.id)  # inside the grace the line is not a candidate
    assert (before_grace.applied, before_grace.proposed, before_grace.skipped) == ([], [], {})
    assert _booked(db_session) == [] and _actions(db_session) == [] and _proposals(db_session) == []
    _set_policy(db_session, {**BACKFILL_ON, "backfill_grace_days": 10})
    result = policy_service.run_policy(db_session, s.stmt.id)
    (row,) = _actions(db_session)
    assert result.applied == [row.id] and row.action == "backfill_entry" and row.params == {"kind": "expense"}
    (entry,) = _booked(db_session)
    assert entry.kind == "expense" and entry.needs_review is True


def test_policy_backfill_respects_the_account_list_and_caps(seed, db_session, run, dirty_on):
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


def _sweep(db, stmt):
    db.expire_all()
    stmt = db.get(AccountStatement, stmt.id)
    with db.begin_nested():
        rec._lock_statement(db, stmt.id)
        coverage_service.sweep(db, stmt, run_id=None, barrier_wait=False)
    db.commit()


def test_policy_never_backfills_unknown_needs_peer_or_owner_deleted_lines(seed, db_session, run, dirty_on):
    account = _card(seed, db_session)
    stmt, lines, _ = _submit(db_session, run, account, [_line(1, DAY, "??", 10, "unknown"), _line(2, DAY, "繳款", -20000, "payment"),
                                                        _line(3, DAY, "全聯", 580)])
    _set_policy(db_session, {**BACKFILL_ON, "backfill_grace_days": 0})
    result = policy_service.run_policy(db_session, stmt.id)
    (row,) = _actions(db_session)
    assert result.applied == [row.id] and row.event_id == lines[2].event_id
    (case,) = [c for c in _cases(db_session, stmt.id, "resolved")]
    db_session.delete(db_session.get(LedgerEntry, row.created_entry_ids[0]))
    db_session.commit()
    _sweep(db_session, stmt)
    rec.reconcile(db_session, stmt.id)
    db_session.commit()
    assert policy_service.run_policy(db_session, stmt.id).applied == []


def test_policy_skips_an_owner_deleted_fee_line_but_the_owner_may_rebook_it(seed, db_session, run, dirty_on):
    s = _fee_statement(seed, db_session, run)
    _set_policy(db_session, SYSTEM_ON)
    (row_id,) = policy_service.run_policy(db_session, s.stmt.id).applied
    row = db_session.get(ReconciliationAction, row_id)
    db_session.delete(db_session.get(LedgerEntry, row.created_entry_ids[0]))
    db_session.commit()
    _sweep(db_session, s.stmt)
    rec.reconcile(db_session, s.stmt.id)
    db_session.commit()
    (case,) = _open_beside_gap(db_session, s.stmt.id, "-1200.0000")
    assert case.context["hint"] == "bank_only" and case.context["owner_deleted"] is True
    for _ in range(2):
        assert policy_service.run_policy(db_session, s.stmt.id).applied == []
        rec.reconcile(db_session, s.stmt.id)
        db_session.commit()
    (case,) = _open_beside_gap(db_session, s.stmt.id, "-1200.0000")
    assert "create_system_entry" in registry.allowed(case)
    engine.apply_action(db_session, case_id=case.id, action="create_system_entry", params={"kind": "fee"}, actor="owner", label="t")
    db_session.commit()
    assert len(_booked(db_session)) == 1


def test_policy_refuses_a_candidate_whose_creation_the_owner_made_and_deleted_since_the_collect(seed, db_session, run, dirty_on):
    s = _fee_statement(seed, db_session, run)
    _set_policy(db_session, SYSTEM_ON)
    (case,) = _open_beside_gap(db_session, s.stmt.id, "-1200.0000")
    cand = policy_service._system_entry_candidate(db_session, case, s.stmt)
    assert cand is not None
    engine.apply_action(db_session, case_id=case.id, action="create_system_entry", params=cand.params, actor="owner", label="t")
    db_session.commit()
    (row,) = _actions(db_session)
    db_session.delete(db_session.get(LedgerEntry, row.created_entry_ids[0]))
    db_session.commit()
    _sweep(db_session, s.stmt)  # not reconcile: the reopened case keeps its old context
    (reopened,) = _open_beside_gap(db_session, s.stmt.id, "-1200.0000")
    assert "owner_deleted" not in (reopened.context or {})
    with pytest.raises(CodedConflictError) as info:
        engine.apply_action(db_session, case_id=reopened.id, action="create_system_entry", params=cand.params, actor="policy",
                            label="policy", budget=(MONTH, "TWD", cand.amount), precheck=partial(policy_service._policy_precheck, cand))
    db_session.rollback()
    assert info.value.code == "candidate_changed"
    assert _booked(db_session) == []


@pytest.mark.parametrize("action, lines, params", [
    ("create_system_entry", [_line(1, DAY, "年費", 1200, "fee")], {"kind": "fee"}),
    ("backfill_entry", [_line(1, DAY, "全聯", 580)], {"kind": "expense"})])
def test_policy_never_proposes_rebooking_an_owner_deleted_line(seed, db_session, run, dirty_on, action, lines, params):
    # final review I3: the sweep reopened the case with its old context (no owner_deleted key yet); over max_amount
    # the candidate would have become a policy proposal. collect_candidates drops it: no apply, no proposal, no preview.
    card = _card(seed, db_session)
    stmt, _, _ = _submit(db_session, run, card, lines)
    (case,) = _open_beside_gap(db_session, stmt.id, f"{-sum(l.printed_amount for l in lines):.4f}")
    row, _ = engine.apply_action(db_session, case_id=case.id, action=action, params=params, actor="owner", label="t")
    db_session.commit()
    db_session.delete(db_session.get(LedgerEntry, row.created_entry_ids[0]))
    db_session.commit()
    _sweep(db_session, stmt)
    (reopened,) = _open_beside_gap(db_session, stmt.id, f"{-sum(l.printed_amount for l in lines):.4f}")
    assert "owner_deleted" not in (reopened.context or {})
    _set_policy(db_session, {"enabled": True, "backfill_grace_days": 0,
                             "actions": {action: {"enabled": True, "max_amount": "100"}}})
    live = settings_service.reconciliation_policy(db_session)
    statement = db_session.get(AccountStatement, stmt.id)
    assert policy_service.collect_candidates(db_session, statement, live) == []
    assert policy_service.preview(db_session, statement, live) == []
    result = policy_service.run_policy(db_session, stmt.id)
    assert (result.applied, result.proposed) == ([], []) and _proposals(db_session) == []
    assert _booked(db_session) == []


# --- anchor_opening (R6b.2) ---------------------------------------------------------------------------------------

def test_policy_anchors_once_per_account_and_proposes_over_the_cap(seed, db_session, run, dirty_on):
    bank = seed.account("銀行", opening="1000", statement_live_from=date(2026, 9, 1))
    db_session.commit()
    stmt, _, _ = _submit(db_session, run, bank, [_line(1, DAY, "存入", 100, "deposit")], kind="bank", opening=Decimal("1600"))
    _set_policy(db_session, ANCHOR_ON)
    result = policy_service.run_policy(db_session, stmt.id)
    (row,) = _actions(db_session)
    assert result.applied == [row.id] and row.action == "anchor_opening" and row.after["policy_budget"]["amount"] == "600"
    rec.reconcile(db_session, stmt.id)
    db_session.commit()
    assert policy_service.run_policy(db_session, stmt.id).applied == []  # already anchored → not a candidate
    bank2 = seed.account("銀行2", opening="0", statement_live_from=date(2026, 9, 1))
    db_session.commit()
    stmt2, _, _ = _submit(db_session, run, bank2, [_line(1, DAY, "存入", 100, "deposit")], kind="bank", opening=Decimal("300000"))
    result = policy_service.run_policy(db_session, stmt2.id)
    (p,) = _proposals(db_session, status="pending")
    assert result.proposed == [p.id] and p.action == "anchor_opening"


def _adjustments(db):
    return [e for e in _booked(db) if e.kind == "balance_adjustment"]


AUG = (date(2026, 8, 1), date(2026, 8, 31))


def _case_dump():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("r6_case_dump", Path(__file__).parents[2] / "scripts" / "r6_case_dump.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_anchor_is_idempotent_on_rerun_and_after_an_earlier_statement_is_added(seed, db_session, run, dirty_on):
    # spec R6b.8 / final review C1: September arrives first and is anchored by the policy (it starts within the
    # account's first live month: live from 8/15); August arrives later with a deposit and a fee the ledger lacks.
    # The anchor (dated 8/31) already absorbed both, so the policy neither re-anchors nor books the August lines.
    from app.services.reconciliation_actions import structural_actions as sa
    bank = seed.account("銀行", opening="1000", statement_live_from=date(2026, 8, 15))
    db_session.commit()
    sep, _, _ = _submit(db_session, run, bank, [_line(1, DAY, "存入", 100, "deposit")], kind="bank", opening=Decimal("1590"))
    _set_policy(db_session, {"enabled": True, "backfill_grace_days": 0,
                             "actions": {"anchor_opening": {"enabled": True, "max_amount": "200000"},
                                         "backfill_entry": {"enabled": True, "max_amount": "5000"},
                                         "create_system_entry": {"enabled": True}}})
    for _ in range(2):  # run 1 anchors (and flags the statement), run 2 backfills the September deposit
        policy_service.run_policy(db_session, sep.id)
        rec.reconcile(db_session, sep.id)
        db_session.commit()
    (anchor,) = [a for a in _actions(db_session) if a.action == "anchor_opening"]
    assert anchor.inverse["as_of"] == "2026-08-31" and anchor.after["policy_budget"]["amount"] == "590"
    assert sa.anchor_as_of(db_session, bank.id) == date(2026, 8, 31)
    assert sorted(a.action for a in _actions(db_session)) == ["anchor_opening", "backfill_entry"]
    assert [c for c in _cases(db_session, sep.id, "open") if c.kind == "balance_gap"] == []
    assert policy_service.run_policy(db_session, sep.id).applied == []  # rerun: idempotent
    booked = [e.id for e in _booked(db_session)]
    assert _case_dump()._policy_section(db_session, [db_session.get(AccountStatement, sep.id)])["anchor_above_earlier_statement"] == []

    aug, (deposit, fee), _ = _submit(db_session, run, bank, [_line(1, date(2026, 8, 5), "存入", 100, "deposit"),
                                                             _line(2, date(2026, 8, 20), "手續費", -10, "fee")],
                                     kind="bank", period=AUG, opening=Decimal("1500"))
    assert sa.anchor_target(db_session, bank.id).id == aug.id  # August is now the earliest opening balance
    aug_cases = {c.line_id: c for c in _cases(db_session, aug.id, "open") if c.kind == "line_unmatched"}
    assert set(aug_cases) == {deposit.id, fee.id} and aug_cases[fee.id].context["hint"] == "bank_only"
    statement = db_session.get(AccountStatement, aug.id)
    assert policy_service.collect_candidates(db_session, statement, settings_service.reconciliation_policy(db_session)) == []
    result = policy_service.run_policy(db_session, aug.id)
    assert (result.applied, result.proposed) == ([], [])
    for sid in (aug.id, sep.id):
        rec.reconcile(db_session, sid)
        db_session.commit()
    assert [e.id for e in _booked(db_session)] == booked  # no re-anchor, no backfill, no system entry below as_of
    assert len([a for a in _actions(db_session) if a.action == "anchor_opening"]) == 1
    assert {c.line_id for c in _cases(db_session, aug.id, "open") if c.kind == "line_unmatched"} == {deposit.id, fee.id}
    # the anchor dated 8/31 carries August's own movement: both months balance at their end, so no gap is opened
    assert [c for c in _cases(db_session, aug.id, "open") if c.kind == "balance_gap"] == []
    assert [c for c in _cases(db_session, sep.id, "open") if c.kind == "balance_gap"] == []
    statements = [db_session.get(AccountStatement, sid) for sid in (aug.id, sep.id)]
    section = _case_dump()._policy_section(db_session, statements)
    assert section["anchor_above_earlier_statement"] == [bank.id]
    # the owner may still book the absorbed deposit; the absorbed amount then shows as the August gap
    engine.apply_action(db_session, case_id=aug_cases[deposit.id].id, action="backfill_entry", params={"kind": "income"},
                        actor="owner", label="t")
    db_session.commit()
    rec.reconcile(db_session, aug.id)
    db_session.commit()
    (gap,) = [c for c in _cases(db_session, aug.id, "open") if c.kind == "balance_gap"]
    assert Decimal(gap.context["gap"]) == Decimal("-100")


def test_a_backfill_collected_before_an_anchor_is_refused_under_the_lock(seed, db_session, run, dirty_on):
    # August has no opening balance, so September is the anchor target (as_of 8/31); the policy collected August's
    # deposit before the owner anchored: under the lock the candidate is now absorbed by the anchor (C1)
    bank = seed.account("銀行", opening="1000", statement_live_from=date(2026, 8, 1))
    db_session.commit()
    aug, (deposit,), _ = _submit(db_session, run, bank, [_line(1, date(2026, 8, 5), "存入", 100, "deposit")], kind="bank",
                                 period=AUG, opening=Decimal("1500"))
    db_session.get(AccountStatement, aug.id).opening_balance = None
    db_session.commit()
    sep, _, _ = _submit(db_session, run, bank, [_line(1, DAY, "存入", 100, "deposit")], kind="bank", opening=Decimal("1600"))
    _set_policy(db_session, {**BACKFILL_ON, "backfill_grace_days": 0})
    (case,) = [c for c in _cases(db_session, aug.id, "open") if c.line_id == deposit.id]
    cand = policy_service._backfill_candidate(db_session, case, db_session.get(AccountStatement, aug.id))
    assert cand is not None and cand.action == "backfill_entry"
    (gap,) = [c for c in _cases(db_session, sep.id, "open") if c.kind == "balance_gap"]
    engine.apply_action(db_session, case_id=gap.id, action="anchor_opening", params={}, actor="owner", label="t")
    db_session.commit()
    rec.reconcile(db_session, aug.id)  # discharges the anchor's needs_recheck flag; the line case is kept as is
    db_session.commit()
    (case,) = [c for c in _cases(db_session, aug.id, "open") if c.line_id == deposit.id]
    assert case.id == cand.case_id
    assert policy_service._backfill_candidate(db_session, case, db_session.get(AccountStatement, aug.id)) is None
    with pytest.raises(CodedConflictError) as info:
        engine.apply_action(db_session, case_id=case.id, action="backfill_entry", params=cand.params, actor="policy",
                            label="policy", budget=(MONTH, "TWD", cand.amount), precheck=partial(policy_service._policy_precheck, cand))
    db_session.rollback()
    assert info.value.code == "candidate_changed"
    assert [e.kind for e in _booked(db_session)] == ["balance_adjustment"]


def test_the_policy_never_acts_on_a_card_balance_gap(seed, db_session, run, dirty_on):
    card = _card(seed, db_session)
    seed.entry(card, "-580", day=DAY, merchant="全聯")
    db_session.commit()
    stmt, _, _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580), _line(2, DAY, "神秘", 200)])
    (case,) = [c for c in _cases(db_session, stmt.id, "open") if c.kind == "balance_gap"]
    assert case.context == {"gap": "-200.0000", "card": True}
    _set_policy(db_session, {"enabled": True, "actions": {name: {"enabled": True, "max_amount": "200000"}
                                                          for name in policy_service.POLICY_ACTIONS}})
    statement = db_session.get(AccountStatement, stmt.id)
    assert policy_service._anchor_candidate(db_session, case, statement) is None
    assert [c for c in policy_service.collect_candidates(db_session, statement, {}) if c.case_id == case.id] == []
    policy_service.run_policy(db_session, stmt.id)
    db_session.commit()
    assert [a for a in _actions(db_session) if a.case_id == case.id] == [] and _adjustments(db_session) == []
    assert case.id in [c.id for c in _cases(db_session, stmt.id, "open")]


@pytest.mark.parametrize("live_from", [date(2026, 8, 1), date(2026, 7, 1)])
def test_the_policy_anchors_only_the_first_live_month_but_the_owner_may_anchor_later_ones(seed, db_session, run, dirty_on, live_from):
    # the September statement is not the first live month (live from 8/1: August is; 9/1 is a calendar month later)
    bank = seed.account("銀行", opening="1000", statement_live_from=live_from)
    db_session.commit()
    stmt, _, _ = _submit(db_session, run, bank, [_line(1, DAY, "存入", 100, "deposit")], kind="bank", opening=Decimal("1600"))
    _set_policy(db_session, ANCHOR_ON)
    (case,) = [c for c in _cases(db_session, stmt.id, "open") if c.kind == "balance_gap"]
    statement = db_session.get(AccountStatement, stmt.id)
    assert policy_service._anchor_candidate(db_session, case, statement) is None
    result = policy_service.run_policy(db_session, stmt.id)
    assert (result.applied, result.proposed) == ([], []) and _actions(db_session) == []
    assert "anchor_opening" in registry.allowed(case)
    row, _ = engine.apply_action(db_session, case_id=case.id, action="anchor_opening", params={}, actor="owner", label="t")
    db_session.commit()
    assert row.status == "applied" and len(_adjustments(db_session)) == 1


def test_the_policy_does_not_anchor_past_an_earlier_live_statement_without_an_opening_balance(seed, db_session, run, dirty_on):
    # M1 (final re-review): live from 8/15 → August is live but carries no opening balance, so September is the
    # anchor target; the calendar bound alone admits it, the earlier-statement bound refuses it (the owner may)
    bank = seed.account("銀行", opening="1000", statement_live_from=date(2026, 8, 15))
    db_session.commit()
    aug, _, _ = _submit(db_session, run, bank, [_line(1, date(2026, 8, 20), "存入", 100, "deposit")], kind="bank",
                        period=(date(2026, 8, 1), date(2026, 8, 31)), opening=Decimal("1000"))
    db_session.get(AccountStatement, aug.id).opening_balance = None
    db_session.commit()
    stmt, _, _ = _submit(db_session, run, bank, [_line(1, DAY, "存入", 100, "deposit")], kind="bank", opening=Decimal("1600"))
    from app.services.reconciliation_actions import structural_actions as sa
    assert sa.anchor_target(db_session, bank.id).id == stmt.id
    assert policy_service._anchor_gate(db_session, bank.id) is False
    _set_policy(db_session, ANCHOR_ON)
    result = policy_service.run_policy(db_session, stmt.id)
    assert (result.applied, result.proposed) == ([], []) and _actions(db_session) == []
    (case,) = [c for c in _cases(db_session, stmt.id, "open") if c.kind == "balance_gap"]
    assert "anchor_opening" in registry.allowed(case)


@pytest.mark.parametrize("live_from", [None, date(2026, 9, 1), date(2026, 9, 20), date(2026, 8, 2)])
def test_the_policy_anchors_a_target_in_the_first_live_month(live_from):
    target = SimpleNamespace(period_start=date(2026, 9, 1))
    assert policy_service._first_live_month(target, SimpleNamespace(statement_live_from=live_from)) is True


@pytest.mark.parametrize("live_from", [date(2026, 8, 1), date(2026, 7, 31), date(2026, 6, 1)])
def test_the_policy_does_not_anchor_a_target_after_the_first_live_month(live_from):
    target = SimpleNamespace(period_start=date(2026, 9, 1))
    assert policy_service._first_live_month(target, SimpleNamespace(statement_live_from=live_from)) is False


def test_policy_skips_statements_flagged_by_the_anchor_until_reconciled(seed, db_session, run, dirty_on):
    # run 1 (anchor only): the anchor applies and flags the statement needs_recheck. Run 2 (system entries now on)
    # is skipped (statement_excluded: needs_recheck) until a reconcile clears the flag; run 3 books the fee.
    bank = seed.account("銀行", opening="1000", statement_live_from=date(2026, 9, 1))
    db_session.commit()
    stmt, _, _ = _submit(db_session, run, bank, [_line(1, DAY, "年費", -50, "fee")], kind="bank", opening=Decimal("1600"))
    _set_policy(db_session, ANCHOR_ON)
    first = policy_service.run_policy(db_session, stmt.id)
    assert [a.action for a in _actions(db_session)] == ["anchor_opening"] and len(first.applied) == 1
    db_session.expire_all()
    assert db_session.get(AccountStatement, stmt.id).needs_recheck is True
    _set_policy(db_session, {**ANCHOR_ON, "actions": {**ANCHOR_ON["actions"], **SYSTEM_ON["actions"]}})
    # S1: while flagged, run_policy returns an EMPTY result (policy_service.py:457-458), and nothing changes
    second = policy_service.run_policy(db_session, stmt.id)
    assert (second.applied, second.proposed, second.skipped) == ([], [], {})
    assert len(_actions(db_session)) == 1 and len(_booked(db_session)) == 1 and len(_adjustments(db_session)) == 1
    rec.reconcile(db_session, stmt.id)  # only a completed reconcile discharges the flag
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(AccountStatement, stmt.id).needs_recheck is False
    third = policy_service.run_policy(db_session, stmt.id)
    assert len(third.applied) == 1 and _actions(db_session)[-1].action == "create_system_entry"
    assert len(_booked(db_session)) == 2 and len(_adjustments(db_session)) == 1


# --- R6b.5 replace_stand_in ---------------------------------------------------------------------------------------

def test_policy_replaces_a_stand_in_with_zero_budget_and_counts_toward_the_statement_cap(seed, db_session, run, dirty_on):
    card = _card(seed, db_session)
    # two lines from the start, so the cap half has a second line/stand-in/owner triple on the same statement
    stmt, (line, second), _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580), _line(2, DAY, "家樂福", 320)])
    for case in _open_beside_gap(db_session, stmt.id, "-900.0000"):
        engine.apply_action(db_session, case_id=case.id, action="backfill_entry", params={"kind": "expense"}, actor="owner", label="t")
        db_session.commit()
    owner = seed.entry(card, "-580", day=DAY, merchant="全聯")
    db_session.commit()
    rec.reconcile(db_session, stmt.id)
    db_session.commit()
    _set_policy(db_session, {"enabled": True})  # replace_stand_in is enabled by default
    result = policy_service.run_policy(db_session, stmt.id)
    (row,) = [a for a in _actions(db_session) if a.action == "replace_stand_in"]
    assert result.applied == [row.id] and row.after["policy_budget"]["amount"] == "0.0000"
    assert _budget(db_session).used_amount == Decimal("0") and _budget(db_session).used_count == 1
    assert [c.entry_id for c in _active(db_session, stmt.id) if c.line_id == line.id] == [owner.id]
    _set_policy(db_session, {"enabled": True, "per_statement_count": 1})
    # a second replacement on the same statement would exceed the count cap → proposal
    owner2 = seed.entry(card, "-320", day=DAY, merchant="家樂福")
    db_session.commit()
    rec.reconcile(db_session, stmt.id)
    db_session.commit()
    result = policy_service.run_policy(db_session, stmt.id)
    (p,) = _proposals(db_session, status="pending")
    assert result.applied == [] and result.proposed == [p.id] and p.action == "replace_stand_in" and p.params == {}
    applied, _ = proposal_service.apply_proposal(db_session, proposal_id=p.id, label="owner-test", request_id=None)
    db_session.commit()
    assert applied.status == "applied" and applied.actor == "owner"
    assert len([a for a in _actions(db_session) if a.action == "replace_stand_in"]) == 2
    assert [c.line_id for c in _active(db_session, stmt.id) if c.entry_id in {e.id for e in _booked(db_session)}] == []
    assert [c.entry_id for c in _active(db_session, stmt.id) if c.line_id == second.id] == [owner2.id]
    assert sorted(c.entry_id for c in _active(db_session, stmt.id)) == sorted([owner.id, owner2.id])


@pytest.mark.parametrize("owner_day, outcome", [(date(2026, 11, 1), "applied"), (date(2026, 11, 2), "proposed"),
                                                (date(2026, 11, 5), "proposed")])
def test_the_policy_applies_only_exact_window_shadows_and_proposes_the_drifted_ones(seed, db_session, run, dirty_on, today,
                                                                                   owner_day, outcome):
    # final review C2: October's 2000 ATM line was backfilled after the grace; the owner then records an equal ATM
    # withdrawal in November. Within exact_window_days (3) of the line the policy replaces the stand-in; farther (up
    # to the drift window) it only proposes (date_drift) and the owner decides from the case.
    today(date(2026, 11, 20))
    bank = seed.account("銀行", opening="10000", statement_live_from=date(2026, 9, 1))
    db_session.commit()
    oct_, (atm,), _ = _submit(db_session, run, bank, [_line(1, date(2026, 10, 29), "ATM", -2000, "withdrawal")], kind="bank",
                              period=(date(2026, 10, 1), date(2026, 10, 31)), opening=Decimal("10000"))
    _set_policy(db_session, {**BACKFILL_ON, "backfill_grace_days": 14})
    (backfill_id,) = policy_service.run_policy(db_session, oct_.id).applied
    rec.reconcile(db_session, oct_.id)
    db_session.commit()
    stand_in_id = db_session.get(ReconciliationAction, backfill_id).created_entry_ids[0]
    assert [c for c in _cases(db_session, oct_.id, "open") if c.kind == "balance_gap"] == []
    owner = seed.entry(bank, "-2000", day=owner_day, merchant="ATM")
    db_session.commit()
    rec.reconcile(db_session, oct_.id)
    db_session.commit()
    (case,) = [c for c in _cases(db_session, oct_.id, "open") if c.kind == "entry_unmatched"]
    drift = (owner_day - atm.posted_date).days
    assert case.entry_id == owner.id and case.context["hint"] == "shadowed_by_stand_in" and case.context["drift_days"] == drift
    statement = db_session.get(AccountStatement, oct_.id)
    (decision,) = [d for c, d in policy_service.preview(db_session, statement, settings_service.reconciliation_policy(db_session))
                   if c.action == "replace_stand_in"]
    result = policy_service.run_policy(db_session, oct_.id)
    if outcome == "applied":
        assert decision == "apply"
        (row,) = [a for a in _actions(db_session) if a.action == "replace_stand_in"]
        assert result.applied == [row.id] and result.proposed == []
        assert db_session.get(LedgerEntry, stand_in_id) is None
        assert [c.entry_id for c in _active(db_session, oct_.id)] == [owner.id]
        return
    assert decision == "propose:date_drift"
    (p,) = _proposals(db_session, status="pending")
    assert result.applied == [] and result.proposed == [p.id] and p.action == "replace_stand_in"
    assert p.rationale == "policy: date_drift"
    assert db_session.get(LedgerEntry, stand_in_id) is not None and db_session.get(LedgerEntry, owner.id) is not None
    assert [c.entry_id for c in _active(db_session, oct_.id)] == [stand_in_id]
    rec.reconcile(db_session, oct_.id)
    db_session.commit()
    assert [c for c in _cases(db_session, oct_.id, "open") if c.kind == "balance_gap"] == []  # October unchanged
    assert policy_service.run_policy(db_session, oct_.id).applied == []  # the proposed case is not re-collected
    (case,) = [c for c in _cases(db_session, oct_.id, "open", "proposed") if c.kind == "entry_unmatched"]
    assert "replace_stand_in" in registry.allowed(case)
    row, _ = engine.apply_action(db_session, case_id=case.id, action="replace_stand_in", params={}, actor="owner", label="t")
    db_session.commit()
    assert row.status == "applied" and db_session.get(LedgerEntry, stand_in_id) is None
    assert [c.entry_id for c in _active(db_session, oct_.id)] == [owner.id]


def test_a_shadow_that_drifted_since_the_collect_is_refused_under_the_lock(seed, db_session, run, dirty_on):
    # the owner entry sits after the period (10/1, 2 days from the 9/29 line): moving it to 10/10 stays outside the
    # period, so no recheck is raised and the case keeps its collected drift_days; the lock re-measures the distance
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 29), "全聯", 580)])
    (case,) = _open_beside_gap(db_session, stmt.id, "-580.0000")
    engine.apply_action(db_session, case_id=case.id, action="backfill_entry", params={"kind": "expense"}, actor="owner", label="t")
    db_session.commit()
    owner = seed.entry(card, "-580", day=date(2026, 10, 1), merchant="全聯")
    db_session.commit()
    rec.reconcile(db_session, stmt.id)
    db_session.commit()
    _set_policy(db_session, {"enabled": True})
    statement, cand = _only_candidate(db_session, stmt.id)
    assert cand.action == "replace_stand_in" and cand.evidence["drift_days"] == 2
    owner.posted_date = owner.entry_date = date(2026, 10, 10)  # 11 days from the line now, still outside the period
    db_session.commit()
    with pytest.raises(CodedConflictError) as info:
        engine.apply_action(db_session, case_id=cand.case_id, action="replace_stand_in", params={}, actor="policy", label="policy",
                            budget=(MONTH, "TWD", cand.amount), precheck=partial(policy_service._policy_precheck, cand))
    db_session.rollback()
    assert info.value.code == "candidate_changed"
    assert [a.action for a in _actions(db_session)] == ["backfill_entry"]


# --- owner twins (final production finding 2026-10-17) ------------------------------------------------------------

def _split_owner(seed, db, card, wallet, *, other, amount="-580", day=DAY, kind="expense"):
    """A MOZE `split` group whose `kind` member of `amount` on the card records the line (default: the 580 purchase);
    its second member is a different amount on the card (`other="card"`: the group does not conserve) or an income
    on a non-participating account (`other="wallet"`: the group is partial). Returns the card member."""
    group = seed.group("split")
    member = seed.entry(card, amount, kind=kind, day=day, merchant="全聯", group_id=group.id)
    if other == "card":
        seed.entry(card, "-120", day=day, merchant="全聯", group_id=group.id)
    else:
        seed.entry(wallet, "200", kind="income", day=day, group_id=group.id)
    db.commit()
    return member


def _open_line_case(db, stmt_id, line_id):
    (case,) = [c for c in _cases(db, stmt_id, "open") if c.kind == "line_unmatched" and c.line_id == line_id]
    return case


def _backfills(db):
    return [a for a in _actions(db) if a.action == "backfill_entry"]


@pytest.mark.parametrize("other", ["card", "wallet"])
def test_policy_never_backfills_a_line_a_split_member_records(seed, db_session, run, other):
    card = _card(seed, db_session)
    wallet = seed.account("錢包")
    member = _split_owner(seed, db_session, card, wallet, other=other)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)])
    case = _open_line_case(db_session, stmt.id, line.id)
    assert not case.candidates and _active(db_session, stmt.id) == []  # the matcher never claims a split member alone
    assert "backfill_entry" in registry.allowed(case)  # the owner still decides
    statement = db_session.get(AccountStatement, stmt.id)
    for max_amount in ("5000", "100"):  # neither applied nor, over max_amount, proposed
        _set_policy(db_session, {**BACKFILL_ON, "backfill_grace_days": 0,
                                 "actions": {"backfill_entry": {"enabled": True, "max_amount": max_amount}}})
        live = settings_service.reconciliation_policy(db_session)
        assert [c for c in policy_service.collect_candidates(db_session, statement, live) if c.action == "backfill_entry"] == []
        assert [c for c, _ in policy_service.preview(db_session, statement, live) if c.action == "backfill_entry"] == []
        result = policy_service.run_policy(db_session, stmt.id)
        assert (result.applied, result.proposed) == ([], [])
    assert _backfills(db_session) == [] and _proposals(db_session) == [] and _booked(db_session) == []
    assert _open_line_case(db_session, stmt.id, line.id).id == case.id
    assert db_session.get(LedgerEntry, member.id) is not None


@pytest.mark.parametrize("owner_kind", ["plain", "split", "deleted"])
def test_policy_never_rebackfills_an_event_whose_stand_in_was_replaced(seed, db_session, run, dirty_on, owner_kind):
    # the policy backfilled the line; the owner's entry arrived and replace_stand_in removed the stand-in; a MOZE
    # re-import then deletes the owner entry and re-creates it with a new id ("deleted": the owner removed it).
    card = _card(seed, db_session)
    wallet = seed.account("錢包")
    db_session.commit()
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)])
    _set_policy(db_session, {**BACKFILL_ON, "backfill_grace_days": 0})
    (backfill_id,) = policy_service.run_policy(db_session, stmt.id).applied
    stand_in_id = db_session.get(ReconciliationAction, backfill_id).created_entry_ids[0]

    def owner_entry():
        if owner_kind == "split":
            return _split_owner(seed, db_session, card, wallet, other="wallet")
        entry = seed.entry(card, "-580", day=DAY, merchant="全聯")
        db_session.commit()
        return entry

    owner = owner_entry()
    rec.reconcile(db_session, stmt.id)
    db_session.commit()
    result = policy_service.run_policy(db_session, stmt.id)
    (replaced,) = [a for a in _actions(db_session) if a.action == "replace_stand_in"]
    assert result.applied == [replaced.id] and replaced.event_id == line.event_id
    assert db_session.get(LedgerEntry, stand_in_id) is None and _booked(db_session) == []
    assert [c.entry_id for c in _active(db_session, stmt.id) if c.line_id == line.id] == [owner.id]
    # the re-import: every entry of the owner's record deleted, an identical one inserted with a new id
    gone = [owner] if owner.group_id is None else db_session.execute(
        select(LedgerEntry).where(LedgerEntry.group_id == owner.group_id)).scalars().all()
    for entry in gone:
        db_session.delete(entry)
    db_session.commit()
    new = None if owner_kind == "deleted" else owner_entry()
    _sweep(db_session, stmt)
    for _ in range(2):
        rec.reconcile(db_session, stmt.id)
        db_session.commit()
        result = policy_service.run_policy(db_session, stmt.id)
        assert (result.applied, result.proposed) == ([], [])
    assert [a.id for a in _backfills(db_session)] == [backfill_id] and _proposals(db_session) == []
    assert _booked(db_session) == []
    if owner_kind == "plain":  # the matcher re-claims the re-created plain entry
        assert [c.entry_id for c in _active(db_session, stmt.id) if c.line_id == line.id] == [new.id]
    else:  # a split member is never claimed alone; a deleted record leaves the line to the owner
        assert not _open_line_case(db_session, stmt.id, line.id).candidates
        assert [c for c in _active(db_session, stmt.id) if c.line_id == line.id] == []


def test_an_owner_twin_recorded_since_the_collect_refuses_the_backfill_under_the_lock(seed, db_session, run):
    card = _card(seed, db_session)
    wallet = seed.account("錢包")
    db_session.commit()
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)])
    _set_policy(db_session, {**BACKFILL_ON, "backfill_grace_days": 0})
    statement, cand = _only_candidate(db_session, stmt.id)
    assert cand.action == "backfill_entry"
    _split_owner(seed, db_session, card, wallet, other="card")  # dirty off: no recheck, the case is kept as collected
    with pytest.raises(CodedConflictError) as info:
        engine.apply_action(db_session, case_id=cand.case_id, action="backfill_entry", params=cand.params, actor="policy",
                            label="policy", budget=(MONTH, "TWD", cand.amount), precheck=partial(policy_service._policy_precheck, cand))
    db_session.rollback()
    assert info.value.code == "candidate_changed"
    assert _backfills(db_session) == [] and _booked(db_session) == []


# the two automatic creations an owner twin guards: (printed line, params, the twin's amount and kind)
CREATIONS = {
    "backfill_entry": (_line(1, DAY, "全聯", 580), {"kind": "expense"}, "-580", "expense"),
    "create_system_entry": (_line(1, DAY, "年費", 1200, "fee"), {"kind": "fee"}, "-1200", "fee"),
}


def _creation_policy(action, max_amount="5000"):
    return {"enabled": True, "backfill_grace_days": 0, "actions": {action: {"enabled": True, "max_amount": max_amount}}}


def _creation_statement(seed, db, run, action):
    """A live card statement printing the action's line, nothing covering it: (card, wallet, stmt, line)."""
    card = _card(seed, db)
    wallet = seed.account("錢包")
    db.commit()
    stmt, (line,), _ = _submit(db, run, card, [CREATIONS[action][0]])
    return card, wallet, stmt, line


def _twin(seed, db, card, wallet, action, *, other):
    _, _, amount, kind = CREATIONS[action]
    return _split_owner(seed, db, card, wallet, other=other, amount=amount, kind=kind)


def _over_cap_proposal(seed, db, run, action="backfill_entry"):
    """The action's card line the policy (max_amount 100) proposes rather than applies: (card, wallet, stmt, line,
    pending policy proposal)."""
    card, wallet, stmt, line = _creation_statement(seed, db, run, action)
    _set_policy(db, _creation_policy(action, "100"))
    (pid,) = policy_service.run_policy(db, stmt.id).proposed
    proposal = _reload(db, ReconciliationProposal, pid)
    assert (proposal.author, proposal.action, proposal.status) == ("policy", action, "pending")
    return card, wallet, stmt, line, proposal


def _created(db, action):
    return [a for a in _actions(db) if a.action == action]


@pytest.mark.parametrize("action", sorted(CREATIONS))
def test_the_policy_supersedes_its_pending_creation_proposal_once_an_owner_twin_arrives(seed, db_session, run, dirty_on,
                                                                                         action):
    # review of aa8aaa1: the case keeps its version through reconcile, so supersede_stale never fires; the run does
    card, wallet, stmt, line, proposal = _over_cap_proposal(seed, db_session, run, action)
    _twin(seed, db_session, card, wallet, action, other="card")  # the MOZE import arrives later
    rec.reconcile(db_session, stmt.id)
    db_session.commit()
    result = policy_service.run_policy(db_session, stmt.id)
    assert (result.applied, result.proposed) == ([], [])
    assert _reload(db_session, ReconciliationProposal, proposal.id).status == "superseded"
    (superseded,) = _audit(db_session, "proposal_superseded")
    assert (superseded.actor, superseded.case_id, superseded.statement_id) == ("policy", proposal.case_id, stmt.id)
    assert superseded.detail == {"proposal_id": proposal.id, "by": "owner_may_record"}
    assert _reload(db_session, ReconciliationCase, proposal.case_id).status == "open"  # no live proposal left
    policy_service.run_policy(db_session, stmt.id)  # nothing pending left: no second audit row
    assert len(_audit(db_session, "proposal_superseded")) == 1
    with pytest.raises(CodedConflictError) as info:
        proposal_service.apply_proposal(db_session, proposal_id=proposal.id, label="owner-test", request_id=None)
    db_session.rollback()
    assert info.value.code == "proposal_not_pending"
    assert _created(db_session, action) == [] and _booked(db_session) == []


@pytest.mark.parametrize("action", sorted(CREATIONS))
def test_applying_a_policy_creation_proposal_an_owner_entry_may_record_is_refused(client, seed, db_session, run, dirty_on,
                                                                                   action):
    # defence in depth: the twin arrived and no reconcile or policy run happened since (the case keeps its version)
    card, wallet, stmt, line, proposal = _over_cap_proposal(seed, db_session, run, action)
    _twin(seed, db_session, card, wallet, action, other="wallet")
    response = client.post(f"/reconciliation/proposals/{proposal.id}/apply", headers=S)
    assert response.status_code == 409 and response.json()["message"].startswith("owner_may_record")
    assert _reload(db_session, ReconciliationProposal, proposal.id).status == "pending"
    assert _created(db_session, action) == [] and _booked(db_session) == []
    # the owner's own action from the case stays unrestricted
    row, _ = engine.apply_action(db_session, case_id=proposal.case_id, action=action, params=CREATIONS[action][1],
                                 actor="owner", label="t")
    db_session.commit()
    assert row.status == "applied" and len(_booked(db_session)) == 1


def test_a_replacement_applied_since_the_collect_refuses_the_backfill_under_the_lock(seed, db_session, run, dirty_on):
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)])
    _set_policy(db_session, {**BACKFILL_ON, "backfill_grace_days": 0})
    statement, cand = _only_candidate(db_session, stmt.id)
    assert cand.action == "backfill_entry"
    # meanwhile: the owner backfills, the owner's entry arrives and replaces the stand-in, then the owner deletes it
    engine.apply_action(db_session, case_id=cand.case_id, action="backfill_entry", params=cand.params, actor="owner", label="t")
    db_session.commit()
    owner = seed.entry(card, "-580", day=DAY, merchant="全聯")
    db_session.commit()
    rec.reconcile(db_session, stmt.id)
    db_session.commit()
    (shadow,) = [c for c in _cases(db_session, stmt.id, "open") if c.kind == "entry_unmatched"]
    engine.apply_action(db_session, case_id=shadow.id, action="replace_stand_in", params={}, actor="owner", label="t")
    db_session.commit()
    db_session.delete(db_session.get(LedgerEntry, owner.id))
    db_session.commit()
    _sweep(db_session, stmt)  # not reconcile: the reopened line case keeps its collected context
    (reopened,) = [c for c in _cases(db_session, stmt.id, "open") if c.kind == "line_unmatched"]
    assert (reopened.id, reopened.status) == (cand.case_id, cand.status) and not reopened.candidates
    assert policy_service._backfill_candidate(db_session, reopened, statement) is None
    with pytest.raises(CodedConflictError) as info:
        engine.apply_action(db_session, case_id=cand.case_id, action="backfill_entry", params=cand.params, actor="policy",
                            label="policy", budget=(MONTH, "TWD", cand.amount), precheck=partial(policy_service._policy_precheck, cand))
    db_session.rollback()
    assert info.value.code == "candidate_changed"
    assert _booked(db_session) == []


# --- PR #77 review M1/S1: the twin re-check under the writer barrier; system entries guarded too ---------------------

@pytest.mark.parametrize("other", ["card", "wallet"])
def test_policy_never_books_a_system_entry_a_split_member_records(seed, db_session, run, other):
    card = _card(seed, db_session)
    wallet = seed.account("錢包")
    _twin(seed, db_session, card, wallet, "create_system_entry", other=other)
    stmt, (line,), _ = _submit(db_session, run, card, [CREATIONS["create_system_entry"][0]])
    case = _open_line_case(db_session, stmt.id, line.id)
    assert (case.context or {}).get("hint") == "bank_only" and not case.candidates
    assert "create_system_entry" in registry.allowed(case)  # the owner still decides
    statement = db_session.get(AccountStatement, stmt.id)
    for max_amount in ("5000", "100"):  # neither applied nor, over max_amount, proposed
        _set_policy(db_session, _creation_policy("create_system_entry", max_amount))
        live = settings_service.reconciliation_policy(db_session)
        assert policy_service.collect_candidates(db_session, statement, live) == []
        result = policy_service.run_policy(db_session, stmt.id)
        assert (result.applied, result.proposed) == ([], [])
    assert _created(db_session, "create_system_entry") == [] and _proposals(db_session) == [] and _booked(db_session) == []


def _commit_twin_elsewhere(pg_engine, card_id: int, action: str) -> None:
    """An ordinary ledger writer on its own connection: a split group whose member records the action's line plus
    a −120 member on the same card, committed at once."""
    _, _, amount, kind = CREATIONS[action]
    writer = sessionmaker(bind=pg_engine, autoflush=False)()
    try:
        account = SimpleNamespace(id=card_id, currency="TWD")
        group = EntryGroup(kind="split")
        writer.add(group)
        writer.flush()
        make_entry(writer, account, amount, kind=kind, entry_date=DAY, posted_date=DAY, group_id=group.id)
        make_entry(writer, account, "-120", entry_date=DAY, posted_date=DAY, group_id=group.id)
        writer.commit()
    finally:
        writer.close()


@pytest.mark.parametrize("action", sorted(CREATIONS))
def test_an_owner_twin_committed_after_the_policy_precheck_is_caught_under_the_writer_barrier(
        seed, db_session, run, dirty_on, pg_engine, monkeypatch, action):
    card, _, stmt, _ = _creation_statement(seed, db_session, run, action)
    card_id = card.id
    _set_policy(db_session, _creation_policy(action))
    original = policy_service._policy_precheck
    fired = []

    def racing(candidate, ctx):
        original(candidate, ctx)  # no twin yet: the precheck passes
        _commit_twin_elsewhere(pg_engine, card_id, action)
        fired.append(candidate.action)

    monkeypatch.setattr(policy_service, "_policy_precheck", racing)
    result = policy_service.run_policy(db_session, stmt.id)
    assert fired == [action]
    assert (result.applied, result.proposed, result.skipped) == ([], [], {"candidate_changed": 1})
    assert _created(db_session, action) == [] and _booked(db_session) == []


@pytest.mark.parametrize("action", sorted(CREATIONS))
def test_an_owner_twin_committed_while_a_policy_proposal_is_applied_is_caught_under_the_writer_barrier(
        client, seed, db_session, run, dirty_on, pg_engine, monkeypatch, action):
    card, _, stmt, _, proposal = _over_cap_proposal(seed, db_session, run, action)
    card_id = card.id
    original = policy_service.refuse_owner_recorded
    fired = []

    def racing(ctx):
        original(ctx)  # no twin yet: the apply-time check passes
        _commit_twin_elsewhere(pg_engine, card_id, action)
        fired.append(ctx.case.id)

    monkeypatch.setattr(policy_service, "refuse_owner_recorded", racing)
    response = client.post(f"/reconciliation/proposals/{proposal.id}/apply", headers=S)
    assert fired == [proposal.case_id]
    assert response.status_code == 409 and response.json()["message"].startswith("owner_may_record")
    assert _reload(db_session, ReconciliationProposal, proposal.id).status == "pending"
    assert _created(db_session, action) == [] and _booked(db_session) == []


@pytest.mark.parametrize("action", sorted(CREATIONS))
def test_a_writer_holding_the_barrier_makes_the_policy_creation_yield(seed, db_session, run, dirty_on, pg_engine,
                                                                     monkeypatch, action):
    """A writer takes the shared barrier after the action's sweep and keeps it past the (shortened) lock timeout: the
    policy's creation is skipped `sweep_barrier_busy`, nothing booked (it retries on the next run)."""
    _, _, stmt, _ = _creation_statement(seed, db_session, run, action)
    _set_policy(db_session, _creation_policy(action))
    monkeypatch.setattr(coverage_service, "BARRIER_LOCK_TIMEOUT", "200ms")
    original = coverage_service.take_barrier_xact
    holder = pg_engine.connect()
    squeezed_at = []

    def squeezed(db):
        holder.execute(text("SELECT pg_advisory_xact_lock_shared(:k)"), {"k": DIRTY_BARRIER_KEY})
        squeezed_at.append(True)
        return original(db)

    monkeypatch.setattr(coverage_service, "take_barrier_xact", squeezed)
    try:
        result = policy_service.run_policy(db_session, stmt.id)
    finally:
        holder.rollback()
        holder.close()
    assert squeezed_at == [True]
    assert (result.applied, result.proposed, result.skipped) == ([], [], {"sweep_barrier_busy": 1})
    assert _created(db_session, action) == [] and _booked(db_session) == []


@pytest.mark.parametrize("action", sorted(CREATIONS))
def test_without_the_dirty_triggers_the_policy_creation_is_refused_but_the_owner_may_book(seed, db_session, run, action):
    # the writer barrier holds back ledger writers only while the dirty triggers take it: the policy (and a policy
    # proposal) is refused dirty_disabled before any lock; the owner's own action from the case stays available
    _, _, stmt, _ = _creation_statement(seed, db_session, run, action)
    _set_policy(db_session, _creation_policy(action))
    result = policy_service.run_policy(db_session, stmt.id)
    assert (result.applied, result.proposed, result.skipped) == ([], [], {"dirty_disabled": 1})
    assert _created(db_session, action) == [] and _booked(db_session) == []
    (case,) = [c for c in _cases(db_session, stmt.id, "open") if c.kind == "line_unmatched"]
    row, _ = engine.apply_action(db_session, case_id=case.id, action=action, params=CREATIONS[action][1], actor="owner",
                                 label="t")
    db_session.commit()
    assert row.status == "applied" and len(_booked(db_session)) == 1
