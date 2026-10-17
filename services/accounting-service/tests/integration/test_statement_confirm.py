"""Statement confirm and conflict resolution (R1c Task 11; spec §7.1, rulings 11, 23, 28, 29, 30): the confirm checks
and `force`, incoming deferral resolution and its races with the linkers, and `resolve_conflict` (`keep=new` promotes
the stored revision through `promote_revision`; `keep=current` rejects it). Synthetic data only."""
import json
import threading
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

from app import auth
from app.models import (
    AccountStatement, LineLineage, ReconciliationAction, ReconciliationAudit, ReconciliationCase,
    ReconciliationProposal, StatementCoverage, StatementEvent, StatementLine, StatementRevision,
)
from app.services import confirm_service, coverage_service, reconciliation_actions
from app.services import reconciliation_service as rec
from app.services import statement_ingest_service as ing
from app.services.errors import CodedConflictError, CodedValidationError, ValidationError
from app.services.reconciliation_actions import engine
from tests.helpers import wait_until_blocked
from tests.integration.statement_fixtures import (  # noqa: F401  (run is a fixture)
    _card, _cases, _line, _submit, run,
)

DAY = date(2026, 9, 3)
OCT30 = (date(2026, 10, 1), date(2026, 10, 30))  # the card's next period after September (closing day 30)
CHECKS = {"current_revision", "guardrail_ok", "conflict_open", "needs_recheck", "open_cases", "pending_proposals",
          "conserved"}


def _confirm(db, stmt, *, force=False, note=None):
    result = confirm_service.confirm_statement(db, account_id=stmt.account_id, statement_id=stmt.id, force=force,
                                               note=note, label="owner-test", request_id="req-1")
    db.commit()
    return result


def _refused(db, stmt) -> dict:
    """The refusal's checks (409 `confirm_refused`, JSON in the detail); the transaction is rolled back."""
    with pytest.raises(CodedConflictError) as info:
        confirm_service.confirm_statement(db, account_id=stmt.account_id, statement_id=stmt.id, force=False,
                                          note=None, label="owner-test", request_id=None)
    db.rollback()
    assert info.value.code == "confirm_refused"
    return json.loads(str(info.value).split(": ", 1)[1])


def _fresh(db, model, pk):
    db.expire_all()
    return db.get(model, pk)


def _audit(db, event):
    db.expire_all()
    return db.execute(select(ReconciliationAudit).where(ReconciliationAudit.event == event)
                      .order_by(ReconciliationAudit.id)).scalars().all()


def _pending(db, statement_id) -> int:
    db.expire_all()
    return ing.get_statement(db, statement_id)["incoming_deferrals_pending"]


@pytest.fixture
def card(seed, db_session):
    return _card(seed, db_session)


@pytest.fixture
def clean(seed, db_session, run, card):
    """A live September card statement whose one line is claimed: nothing open, every check passes."""
    seed.entry(card, "-580", day=DAY, merchant="全聯")
    db_session.commit()
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)])
    assert (stmt.open_case_count, stmt.matched_count, stmt.status) == (0, 1, "open")
    return stmt, line


# --- checks and force ---------------------------------------------------------------------------------------------

def test_confirm_reconciles_a_clean_statement_and_audits_it(db_session, clean):
    stmt, _ = clean
    result = _confirm(db_session, stmt)
    assert (result.status, result.reopened_cases, result.forced) == ("reconciled", [], False)
    assert result.checks == {name: True for name in CHECKS}
    stmt = _fresh(db_session, AccountStatement, stmt.id)
    assert (stmt.status, stmt.open_case_count, stmt.matched_count) == ("reconciled", 0, 1)
    (row,) = _audit(db_session, "statement_confirmed")
    assert (row.statement_id, row.actor, row.request_id) == (stmt.id, "owner-test", "req-1")
    assert row.detail["checks"] == result.checks


def _no_current(db, stmt, line):
    db.execute(text("UPDATE account_statement SET current_revision_id = NULL WHERE id = :id"), {"id": stmt.id})


def _conflict_flag(db, stmt, line):
    db.execute(text("UPDATE account_statement SET conflict_open = true WHERE id = :id"), {"id": stmt.id})


def _dirty(db, stmt, line):
    db.execute(text("UPDATE account_statement SET needs_recheck = true WHERE id = :id"), {"id": stmt.id})


def _open_case(db, stmt, line):
    db.add(ReconciliationCase(statement_id=stmt.id, revision_id=stmt.current_revision_id, kind="recheck",
                              event_id=line.event_id, line_id=line.id))


def _live_proposal(db, stmt, line):
    """A pending proposal written at its case's current version (on a resolved case, so no case is open)."""
    case = ReconciliationCase(statement_id=stmt.id, revision_id=stmt.current_revision_id, kind="recheck",
                              event_id=line.event_id, line_id=line.id, status="resolved")
    db.add(case)
    db.flush()
    db.add(ReconciliationProposal(case_id=case.id, case_version=case.version or 1, event_id=line.event_id,
                                  action="dismiss", params={"note": "x"}, rationale="r", confidence=Decimal("0.5"),
                                  author="hermes"))


def _unconserved(db, stmt, line):
    db.execute(text("UPDATE statement_coverage SET snapshot = jsonb_set(snapshot, '{flow}', '\"-1.0000\"') "
                    "WHERE statement_id = :id"), {"id": stmt.id})


@pytest.mark.parametrize("setup,failing", [
    (_no_current, {"current_revision", "guardrail_ok"}),
    (_conflict_flag, {"conflict_open"}),
    (_dirty, {"needs_recheck"}),
    (_open_case, {"open_cases"}),
    (_live_proposal, {"pending_proposals"}),
    (_unconserved, {"conserved"}),
], ids=["current_revision", "conflict_open", "needs_recheck", "open_cases", "pending_proposals", "conserved"])
def test_each_failing_check_refuses_with_409_and_names_it(db_session, clean, setup, failing):
    stmt, line = clean
    setup(db_session, stmt, line)
    db_session.commit()
    checks = _refused(db_session, stmt)
    assert set(checks) == CHECKS
    assert {name for name, ok in checks.items() if ok is False} == failing
    assert _fresh(db_session, AccountStatement, stmt.id).status == "open"
    assert _audit(db_session, "statement_confirmed") == []


def test_a_failed_current_revision_refuses_on_guardrail_ok(db_session, run, card):
    stmt, _, _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)], total="999")
    checks = _refused(db_session, stmt)
    assert checks["current_revision"] is True
    assert checks["guardrail_ok"] is False and checks["open_cases"] is False  # its parse_review


def test_stale_proposals_are_superseded_before_the_check(db_session, clean):
    stmt, line = clean
    case = ReconciliationCase(statement_id=stmt.id, revision_id=stmt.current_revision_id, kind="recheck",
                              event_id=line.event_id, line_id=line.id, status="resolved", version=2)
    db_session.add(case)
    db_session.flush()
    proposal = ReconciliationProposal(case_id=case.id, case_version=1, event_id=line.event_id, action="dismiss",
                                      params={"note": "x"}, rationale="r", confidence=Decimal("0.5"), author="hermes")
    db_session.add(proposal)
    db_session.commit()
    result = _confirm(db_session, stmt)
    assert result.checks["pending_proposals"] is True and result.status == "reconciled"
    assert _fresh(db_session, ReconciliationProposal, proposal.id).status == "superseded"


@pytest.mark.parametrize("note", [None, "", "   "])
def test_force_without_a_note_is_note_required(db_session, clean, note):
    stmt, _ = clean
    with pytest.raises(CodedValidationError) as info:
        confirm_service.confirm_statement(db_session, account_id=stmt.account_id, statement_id=stmt.id, force=True,
                                          note=note, label="owner-test", request_id=None)
    db_session.rollback()
    assert (info.value.code, info.value.field) == ("note_required", "note")
    assert _fresh(db_session, AccountStatement, stmt.id).status == "open"


def test_forced_confirm_reconciles_despite_failing_checks_and_audits_the_note(db_session, clean):
    stmt, line = clean
    _open_case(db_session, stmt, line)
    db_session.commit()
    result = _confirm(db_session, stmt, force=True, note="銀行對帳單已人工核對")
    assert (result.status, result.forced) == ("reconciled", True)
    assert result.checks["open_cases"] is False
    stmt = _fresh(db_session, AccountStatement, stmt.id)
    assert (stmt.status, stmt.open_case_count) == ("reconciled", 1)
    (row,) = _audit(db_session, "statement_confirmed_forced")
    assert row.detail["note"] == "銀行對帳單已人工核對" and row.detail["checks"] == result.checks
    assert _audit(db_session, "statement_confirmed") == []


def test_confirm_of_another_accounts_statement_is_not_found(db_session, seed, clean):
    from app.services.errors import NotFoundError
    stmt, _ = clean
    other = _card(seed, db_session, "乙卡")
    with pytest.raises(NotFoundError):
        confirm_service.confirm_statement(db_session, account_id=other.id, statement_id=stmt.id, force=False,
                                          note=None, label="owner-test", request_id=None)
    db_session.rollback()


# --- incoming deferrals (ruling 11) -------------------------------------------------------------------------------

@pytest.fixture
def deferred(seed, db_session, run, card):
    """September defers an entry posted on the 29th (the matcher's deferral heuristic) to the period ending on
    October 30; a 120 entry on October 5 belongs to October."""
    seed.entry(card, "-580", day=DAY, merchant="全聯")
    late = seed.entry(card, "-77", day=date(2026, 9, 29), merchant="早餐")
    seed.entry(card, "-120", day=date(2026, 10, 5), merchant="超商")
    db_session.commit()
    sep, _, _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)])
    (case,) = [c for c in _cases(db_session, sep.id) if c.kind == "entry_unmatched"]
    assert (case.status, case.explanation, case.entry_id, case.deferred_to_period_end) == (
        "resolved", "deferred_next_period", late.id, date(2026, 10, 30))
    return sep, late, case


def _october(db, run, card, *, claimed: bool):
    lines = [_line(1, date(2026, 10, 5), "超商", 120)]
    if claimed:
        lines.append(_line(2, date(2026, 10, 1), "早餐", 77))
    # October prints what it carries over (card balance check): September's 580 owed and, unclaimed, the 77 the
    # ledger booked on 9-29 that no statement prints, so October itself balances and only the deferral is tested
    octo, _, _ = _submit(db, run, card, lines, period=OCT30, opening=Decimal("580") if claimed else Decimal("657"))
    assert octo.open_case_count == 0
    return octo


def test_confirm_keeps_a_deferral_claimed_here_resolved(db_session, run, card, deferred):
    sep, late, case = deferred
    octo = _october(db_session, run, card, claimed=True)
    assert _fresh(db_session, ReconciliationCase, case.id).deferred_to_statement_id == octo.id
    assert _pending(db_session, octo.id) == 1
    assert late.id in {r.entry_id for r in db_session.execute(select(StatementCoverage).where(
        StatementCoverage.statement_id == octo.id, StatementCoverage.status == "active")).scalars()}
    result = _confirm(db_session, octo)
    assert (result.status, result.reopened_cases) == ("reconciled", [])
    case = _fresh(db_session, ReconciliationCase, case.id)
    assert (case.status, case.explanation, case.version) == ("resolved", "deferred_next_period", 1)
    assert case.context["resolved_in_statement_id"] == octo.id
    assert _pending(db_session, octo.id) == 0
    assert _audit(db_session, "deferral_reopened") == []


def test_confirm_reopens_an_unclaimed_deferral_on_the_earlier_statement(db_session, run, card, deferred):
    sep, late, case = deferred
    octo = _october(db_session, run, card, claimed=False)
    assert _pending(db_session, octo.id) == 1
    result = _confirm(db_session, octo)
    assert (result.status, result.reopened_cases) == ("reconciled", [case.id])
    assert _fresh(db_session, AccountStatement, octo.id).status == "reconciled"
    case = _fresh(db_session, ReconciliationCase, case.id)
    assert (case.status, case.explanation, case.version, case.resolved_by, case.resolved_at) == (
        "open", None, 2, None, None)
    assert case.context["reopened_by_confirm_of"] == octo.id
    sep = _fresh(db_session, AccountStatement, sep.id)
    # the reopened deferral and September's card balance_gap: the 9-29 entry is in the ledger before September's
    # close but on no statement
    assert (sep.open_case_count, sep.status) == (2, "open")
    assert [c.context for c in _cases(db_session, sep.id, "open") if c.kind == "balance_gap"] == [
        {"gap": "77.0000", "card": True}]
    (row,) = _audit(db_session, "deferral_reopened")
    assert (row.statement_id, row.case_id, row.actor) == (sep.id, case.id, "owner-test")
    assert _pending(db_session, octo.id) == 0


def test_a_dirty_flag_refuses_even_though_the_incoming_deferral_was_processed(db_session, run, card, deferred,
                                                                             monkeypatch):
    sep, _, case = deferred
    octo = _october(db_session, run, card, claimed=False)
    _dirty(db_session, octo, None)
    db_session.commit()
    seen = {}
    original = confirm_service._resolve_incoming

    def spy(db, *args, **kwargs):
        reopened = original(db, *args, **kwargs)
        # in the confirm's own transaction, before the gate: the earlier case is already reopened
        seen["reopened"] = reopened
        seen["status"] = db.execute(select(ReconciliationCase.status)
                                    .where(ReconciliationCase.id == case.id)).scalar_one()
        return reopened

    monkeypatch.setattr(confirm_service, "_resolve_incoming", spy)
    checks = _refused(db_session, octo)
    assert checks["needs_recheck"] is False
    assert {name for name, ok in checks.items() if not ok} == {"needs_recheck"}
    assert seen == {"reopened": [case.id], "status": "open"}
    # the refusal rolled the deferral processing back with everything else
    case = _fresh(db_session, ReconciliationCase, case.id)
    assert (case.status, case.deferred_to_statement_id, case.version) == ("resolved", octo.id, 1)
    assert "reopened_by_confirm_of" not in case.context
    octo = _fresh(db_session, AccountStatement, octo.id)
    assert (octo.needs_recheck, octo.status) == (True, "open")
    assert _pending(db_session, octo.id) == 1


def test_the_second_statements_sweep_never_waits_at_the_barrier(pg_engine, db_session, run, card, deferred,
                                                                monkeypatch):
    """Final review I3: with 2+ statements locked, only the first sweep may wait at the writer barrier; a later one
    may already hold rows the first sweep wrote (a ledger writer waiting on them while holding the shared barrier
    would close a cycle), so it try-locks: a busy barrier is 409 `sweep_barrier_busy` at once, nothing changed."""
    sep, _, case = deferred
    octo = _october(db_session, run, card, claimed=False)
    assert sep.id < octo.id and _pending(db_session, octo.id) == 1
    monkeypatch.setattr(coverage_service, "BARRIER_LOCK_TIMEOUT", "30s")  # a waiting sweep would outlast the holder
    original, swept = coverage_service.sweep, []
    holder = pg_engine.connect()
    release = threading.Timer(2.0, holder.rollback)

    def sweep_then_a_writer_arrives(db, statement, **kwargs):
        result = original(db, statement, **kwargs)
        swept.append((statement.id, kwargs.get("barrier_wait")))
        if len(swept) == 1:  # a ledger writer mid-transaction: holds the shared barrier for two seconds
            holder.execute(text("SELECT pg_advisory_xact_lock_shared(:key)"),
                           {"key": coverage_service.DIRTY_BARRIER_KEY})
            release.start()
        return result

    monkeypatch.setattr(coverage_service, "sweep", sweep_then_a_writer_arrives)
    try:
        with pytest.raises(CodedConflictError) as info:
            confirm_service.confirm_statement(db_session, account_id=octo.account_id, statement_id=octo.id,
                                              force=False, note=None, label="owner-test", request_id=None)
        db_session.rollback()
    finally:
        release.cancel()
        release.join(5)
        holder.rollback()
        holder.close()
    assert info.value.code == "sweep_barrier_busy"
    assert swept == [(sep.id, True)]  # the second sweep never got the barrier
    case = _fresh(db_session, ReconciliationCase, case.id)
    assert (case.status, case.version, case.deferred_to_statement_id) == ("resolved", 1, octo.id)
    assert _fresh(db_session, AccountStatement, octo.id).status == "open"
    assert _pending(db_session, octo.id) == 1 and _audit(db_session, "deferral_reopened") == []


@pytest.fixture(params=[True, False], ids=["claimed", "unclaimed"])
def unlinked(request, seed, db_session, run, card):
    """October exists first, then September defers the 29th's entry to it: an UNLINKED deferral (only October's next
    reconcile or `fill_deferral_links` links it). Claimed: October already claims that entry (the deferral is
    written directly, as an owner deferral made before the claim would be)."""
    claimed = request.param
    seed.entry(card, "-580", day=DAY, merchant="全聯")
    late = seed.entry(card, "-77", day=date(2026, 9, 29), merchant="早餐")
    seed.entry(card, "-120", day=date(2026, 10, 5), merchant="超商")
    db_session.commit()
    octo = _october(db_session, run, card, claimed=claimed)
    sep, _, _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)])
    if claimed:
        db_session.add(ReconciliationCase(
            statement_id=sep.id, revision_id=sep.current_revision_id, kind="entry_unmatched", entry_id=late.id,
            status="resolved", explanation="deferred_next_period", resolved_by="owner",
            deferred_to_period_end=OCT30[1], resolved_at=func.now(), context={}))
        db_session.commit()
    (case,) = [c for c in _cases(db_session, sep.id) if c.kind == "entry_unmatched"]
    assert (case.status, case.deferred_to_period_end, case.deferred_to_statement_id) == (
        "resolved", OCT30[1], None)
    return claimed, sep, octo, case


def test_a_link_appearing_between_discovery_and_the_locks_is_confirm_retry(pg_engine, db_session, unlinked,
                                                                           monkeypatch):
    claimed, sep, octo, case = unlinked
    original = confirm_service._discover

    def discover_then_link(db, statement_id):
        found = original(db, statement_id)
        with sessionmaker(bind=pg_engine)() as other:  # a linker commits while the confirm holds no lock yet
            other.execute(text("UPDATE reconciliation_case SET deferred_to_statement_id = :sid WHERE id = :cid"),
                          {"sid": statement_id, "cid": case.id})
            other.commit()
        return found

    monkeypatch.setattr(confirm_service, "_discover", discover_then_link)
    with pytest.raises(CodedConflictError) as info:
        confirm_service.confirm_statement(db_session, account_id=octo.account_id, statement_id=octo.id, force=False,
                                          note=None, label="owner-test", request_id=None)
    db_session.rollback()
    assert info.value.code == "confirm_retry"
    assert _fresh(db_session, AccountStatement, octo.id).status == "open"
    monkeypatch.setattr(confirm_service, "_discover", original)
    result = _confirm(db_session, octo)  # the retry discovers September, locks it and processes the link
    assert result.status == "reconciled"
    assert result.reopened_cases == ([] if claimed else [case.id])


def test_a_linker_waiting_on_the_confirm_links_after_it_and_the_next_confirm_processes_the_link(
        pg_engine, db_session, unlinked):
    claimed, sep, octo, case = unlinked
    factory = sessionmaker(bind=pg_engine, autoflush=False)
    confirmer, linker = factory(), factory()
    out: dict = {}
    try:
        confirm_pid = confirmer.execute(text("SELECT pg_backend_pid()")).scalar_one()
        linker_pid = linker.execute(text("SELECT pg_backend_pid()")).scalar_one()
        first = confirm_service.confirm_statement(confirmer, account_id=octo.account_id, statement_id=octo.id,
                                                  force=False, note=None, label="owner-test", request_id=None)
        assert (first.status, first.reopened_cases) == ("reconciled", [])  # its final recheck passed, uncommitted

        def link():
            try:
                out["linked"] = rec.fill_deferral_links(linker)
                linker.commit()
            except Exception as exc:  # noqa: BLE001  (asserted below)
                linker.rollback()
                out["error"] = exc

        thread = threading.Thread(target=link, daemon=True)
        thread.start()
        wait_until_blocked(pg_engine, linker_pid, blocker_pid=confirm_pid)  # on October's statement row
        confirmer.commit()
        thread.join(10)
        assert not thread.is_alive() and out == {"linked": 1}
    finally:
        confirmer.close()
        linker.close()
    octo = _fresh(db_session, AccountStatement, octo.id)
    assert (octo.status, octo.needs_recheck) == ("reconciled", False)  # the link set no flag
    assert _fresh(db_session, ReconciliationCase, case.id).deferred_to_statement_id == octo.id
    assert _pending(db_session, octo.id) == 1
    again = _confirm(db_session, octo)
    assert again.status == "reconciled" and all(again.checks.values())
    case = _fresh(db_session, ReconciliationCase, case.id)
    if claimed:
        assert again.reopened_cases == []
        assert (case.status, case.context["resolved_in_statement_id"]) == ("resolved", octo.id)
    else:
        assert again.reopened_cases == [case.id]
        assert (case.status, case.context["reopened_by_confirm_of"]) == ("open", octo.id)
    assert _pending(db_session, octo.id) == 0


def test_fill_deferral_links_links_to_a_reconciled_target_without_a_flag(db_session, unlinked):
    claimed, sep, octo, case = unlinked
    _confirm(db_session, octo)
    assert rec.fill_deferral_links(db_session) == 1
    db_session.commit()
    octo = _fresh(db_session, AccountStatement, octo.id)
    assert (octo.status, octo.needs_recheck) == ("reconciled", False)
    assert _pending(db_session, octo.id) == 1
    assert rec.fill_deferral_links(db_session) == 0


# --- statement_conflict and resolve_conflict ----------------------------------------------------------------------

def _apply(db, case, params):
    row, _ = engine.apply_action(db, case_id=case.id, action="resolve_conflict", params=params, actor="owner",
                                 label="owner-test")
    db.commit()
    return row


def _conflict_case(db, statement_id, revision: int) -> ReconciliationCase:
    (case,) = [c for c in _cases(db, statement_id, "open") if c.kind == "statement_conflict"
               and c.context["revision"] == revision]
    return case


def _count(db, model, *conds) -> int:
    return db.execute(select(func.count()).select_from(model).where(*conds)).scalar_one()


@pytest.fixture
def reconciled(seed, db_session, run, card):
    """A confirmed September statement: 全聯 580 (identical in the next revision), PAYPAL *Spotify 300 (changed),
    超商 120 on the 7th (printed on the 8th next time: unpaired), each claimed."""
    entries = [seed.entry(card, "-580", day=DAY, merchant="全聯"),
               seed.entry(card, "-300", day=date(2026, 9, 5), merchant="Spotify"),
               seed.entry(card, "-120", day=date(2026, 9, 7), merchant="超商")]
    db_session.commit()
    stmt, lines, first = _submit(db_session, run, card, [
        _line(1, DAY, "全聯", 580), _line(2, date(2026, 9, 5), "PAYPAL *Spotify", 300),
        _line(3, date(2026, 9, 7), "超商", 120)], due_date=date(2026, 10, 15))
    assert stmt.matched_count == 3 and stmt.open_case_count == 0
    _confirm(db_session, stmt)
    return stmt, lines, entries, first.revision


def _second(db, run, card, *, due=date(2026, 10, 16), minimum=None):
    return _submit(db, run, card, [
        _line(1, DAY, "全聯", 580), _line(2, date(2026, 9, 5), "PAYPAL *Netflix", 300),
        _line(3, date(2026, 9, 8), "超商", 120)], due_date=due, minimum_payment=minimum)


def test_a_new_revision_of_a_confirmed_statement_opens_a_conflict_and_confirm_refuses(db_session, run, card,
                                                                                       reconciled):
    stmt, lines, _, first = reconciled
    _, _, second = _second(db_session, run, card)
    assert second.revision.conflict is True
    stmt = _fresh(db_session, AccountStatement, stmt.id)
    assert (stmt.status, stmt.conflict_open, stmt.current_revision_id) == ("reconciled", True, first.id)
    case = _conflict_case(db_session, stmt.id, 2)
    assert case.revision_id == second.revision.id
    # ruling 34: a conflict resolves only through resolve_conflict (a dismissed one would leave conflict_open set)
    assert reconciliation_actions.allowed(case) == ["resolve_conflict"]
    with pytest.raises(ValidationError):
        engine.apply_action(db_session, case_id=case.id, action="dismiss", params={"note": "x"}, actor="owner",
                            label="owner-test")
    db_session.rollback()
    checks = _refused(db_session, stmt)
    assert checks["conflict_open"] is False and checks["open_cases"] is False


def test_keep_new_promotes_the_stored_revision_without_new_lines(db_session, run, card, reconciled):
    stmt, (l1, l2, l3), (e1, e2, e3), first = reconciled
    before = {r.entry_id: r for r in db_session.execute(select(StatementCoverage).where(
        StatementCoverage.statement_id == stmt.id, StatementCoverage.status == "active")).scalars()}
    _, (n1, n2, n3), second = _second(db_session, run, card, minimum=Decimal("100"))
    rev2 = second.revision.id
    lines_before = _count(db_session, StatementLine, StatementLine.revision_id == rev2)
    lineage_before = _count(db_session, LineLineage)
    assert (lines_before, lineage_before) == (3, 4)  # identical, changed, unpaired new, unpaired old
    # stored, not current: the identical line rides the old event, the others got retired events of their own
    assert n1.event_id == l1.event_id
    assert {_fresh(db_session, StatementEvent, n.event_id).status for n in (n2, n3)} == {"retired"}
    case = _conflict_case(db_session, stmt.id, 2)
    row = _apply(db_session, case, {"keep": "new"})
    assert row.inverse == {"keep": "new", "previous_current_revision_id": first.id}
    db_session.expire_all()
    stmt = db_session.get(AccountStatement, stmt.id)
    assert (stmt.current_revision_id, stmt.conflict_open, stmt.status) == (rev2, False, "open")
    assert (stmt.due_date, stmt.minimum_payment) == (date(2026, 10, 16), Decimal("100"))
    # promotion inserts no statement_line and no duplicate lineage row
    assert _count(db_session, StatementLine, StatementLine.revision_id == rev2) == lines_before
    assert _count(db_session, LineLineage) == lineage_before
    lineage = {(r.old_line_id, r.new_line_id): (r.equivalence, r.transferred)
               for r in db_session.execute(select(LineLineage)).scalars()}
    assert lineage == {(l1.id, n1.id): ("identical", True), (l2.id, n2.id): ("changed", False),
                       (None, n3.id): ("unpaired", False), (l3.id, None): ("unpaired", False)}
    # identical: the same coverage row moved to the new line; changed / unpaired: released and quarantined
    moved = db_session.get(StatementCoverage, before[e1.id].id)
    assert (moved.status, moved.line_id, moved.event_id) == ("active", n1.id, l1.event_id)
    for entry, reason in ((e2, "lineage:changed"), (e3, "lineage:unpaired")):
        released = db_session.get(StatementCoverage, before[entry.id].id)
        assert (released.status, released.stale_reason) == ("stale", reason)
    assert (db_session.get(StatementEvent, l1.event_id).current_line_id,
            db_session.get(StatementEvent, l1.event_id).status) == (n1.id, "live")
    for old in (l2, l3):
        event = db_session.get(StatementEvent, old.event_id)
        assert (event.status, event.current_line_id) == ("quarantined", None)
    for new in (n2, n3):
        event = db_session.get(StatementEvent, new.event_id)
        assert (event.status, event.current_line_id) == ("live", new.id)
    reviews = [c for c in _cases(db_session, stmt.id, "open") if c.kind == "parse_review"]
    assert sorted(c.event_id for c in reviews) == sorted([l2.event_id, l3.event_id])
    case = _fresh(db_session, ReconciliationCase, case.id)
    assert (case.status, case.resolved_action_id) == ("resolved", row.id)
    with pytest.raises(CodedValidationError) as info:
        engine.revert_action(db_session, action_id=row.id, actor="owner", label="owner-test")
    db_session.rollback()
    assert info.value.code == "not_revertable"


def test_keep_new_reconciles_the_promoted_revision_and_a_later_confirm_succeeds(seed, db_session, run, card):
    seed.entry(card, "-580", day=DAY, merchant="全聯")
    db_session.commit()
    stmt, _, _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)])
    _confirm(db_session, stmt)
    later = seed.entry(card, "-120", day=date(2026, 9, 7), merchant="超商")  # booked after the confirmation
    db_session.commit()
    _, (_, new_line), second = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580),
                                                               _line(2, date(2026, 9, 7), "超商", 120)])
    assert second.revision.conflict is True
    _apply(db_session, _conflict_case(db_session, stmt.id, 2), {"keep": "new"})
    db_session.expire_all()
    stmt = db_session.get(AccountStatement, stmt.id)
    assert (stmt.status, stmt.conflict_open, stmt.current_revision_id) == ("open", False, second.revision.id)
    assert stmt.statement_total == Decimal("700")
    # the in-action reconcile claimed the new line
    claim = db_session.execute(select(StatementCoverage).where(StatementCoverage.line_id == new_line.id,
                                                               StatementCoverage.status == "active")).scalar_one()
    assert claim.entry_id == later.id
    assert (stmt.matched_count, stmt.open_case_count, stmt.needs_recheck) == (2, 0, False)
    result = _confirm(db_session, stmt)
    assert result.status == "reconciled" and all(result.checks.values())


def test_keep_current_rejects_the_revision_and_the_statement_stays_reconciled(db_session, run, card, reconciled):
    stmt, (l1, _, _), _, first = reconciled
    _, _, second = _second(db_session, run, card)
    row = _apply(db_session, _conflict_case(db_session, stmt.id, 2), {"keep": "current"})
    assert row.inverse == {"keep": "current", "previous_current_revision_id": first.id}
    db_session.expire_all()
    stmt = db_session.get(AccountStatement, stmt.id)
    assert (stmt.status, stmt.conflict_open, stmt.current_revision_id, stmt.due_date) == (
        "reconciled", False, first.id, date(2026, 10, 15))
    assert db_session.get(StatementRevision, second.revision.id).rejected is True
    assert db_session.get(StatementEvent, l1.event_id).current_line_id == l1.id
    assert stmt.matched_count == 3


def test_resolving_an_older_conflict_revision_is_conflict_stale(db_session, run, card, reconciled):
    stmt, _, _, first = reconciled
    _second(db_session, run, card)
    _second(db_session, run, card, due=date(2026, 10, 17))
    older = _conflict_case(db_session, stmt.id, 2)
    with pytest.raises(CodedConflictError) as info:
        engine.apply_action(db_session, case_id=older.id, action="resolve_conflict", params={"keep": "new"},
                            actor="owner", label="owner-test")
    db_session.rollback()
    assert info.value.code == "conflict_stale"
    stmt = _fresh(db_session, AccountStatement, stmt.id)
    assert (stmt.current_revision_id, stmt.conflict_open) == (first.id, True)
    assert db_session.execute(select(ReconciliationAction)).scalars().all() == []
    _apply(db_session, _conflict_case(db_session, stmt.id, 3), {"keep": "new"})  # the newest one resolves
    assert _fresh(db_session, AccountStatement, stmt.id).due_date == date(2026, 10, 17)


def _same_lines(db, run, card, *, due, total=None):
    """The reconciled fixture's lines again with another due date (header-only change: no quarantine on promotion)."""
    return _submit(db, run, card, [
        _line(1, DAY, "全聯", 580), _line(2, date(2026, 9, 5), "PAYPAL *Spotify", 300),
        _line(3, date(2026, 9, 7), "超商", 120)], due_date=due, total=total)


def test_keep_new_refuses_a_guardrail_failed_revision_and_keep_current_rejects_it(db_session, run, card,
                                                                                    reconciled):
    stmt, (l1, _, _), _, first = reconciled
    _, _, failed = _same_lines(db_session, run, card, due=date(2026, 10, 16), total="999")
    assert (failed.revision.conflict, failed.revision.guardrail_ok) == (True, False)
    case = _conflict_case(db_session, stmt.id, 2)
    with pytest.raises(CodedConflictError) as info:
        engine.apply_action(db_session, case_id=case.id, action="resolve_conflict", params={"keep": "new"},
                            actor="owner", label="owner-test")
    db_session.rollback()
    assert info.value.code == "conflict_guardrail_failed"
    stmt = _fresh(db_session, AccountStatement, stmt.id)  # untouched
    assert (stmt.status, stmt.conflict_open, stmt.current_revision_id, stmt.due_date) == (
        "reconciled", True, first.id, date(2026, 10, 15))
    assert _fresh(db_session, ReconciliationCase, case.id).status == "open"
    assert db_session.get(StatementEvent, l1.event_id).current_line_id == l1.id
    assert db_session.execute(select(ReconciliationAction)).scalars().all() == []
    _apply(db_session, case, {"keep": "current"})
    assert _fresh(db_session, StatementRevision, failed.revision.id).rejected is True
    stmt = _fresh(db_session, AccountStatement, stmt.id)
    assert (stmt.status, stmt.conflict_open, stmt.current_revision_id) == ("reconciled", False, first.id)


@pytest.mark.parametrize("keep", ["current", "new"])
def test_resolving_the_newest_conflict_supersedes_the_older_ones(db_session, run, card, reconciled, keep):
    """Ruling 34: conflict_open and the open statement_conflict cases never diverge."""
    stmt, _, _, first = reconciled
    _, _, r2 = _same_lines(db_session, run, card, due=date(2026, 10, 16))
    _, _, r3 = _same_lines(db_session, run, card, due=date(2026, 10, 17))
    older, newest = _conflict_case(db_session, stmt.id, 2), _conflict_case(db_session, stmt.id, 3)
    _apply(db_session, newest, {"keep": keep})
    older = _fresh(db_session, ReconciliationCase, older.id)
    assert (older.status, older.version) == ("superseded", 2)
    assert [c.id for c in _cases(db_session, stmt.id, "open", "proposed") if c.kind == "statement_conflict"] == []
    rejected = keep == "current"
    assert _fresh(db_session, StatementRevision, r2.revision.id).rejected is rejected
    assert _fresh(db_session, StatementRevision, r3.revision.id).rejected is rejected
    stmt = _fresh(db_session, AccountStatement, stmt.id)
    assert stmt.conflict_open is False
    assert stmt.current_revision_id == (first.id if rejected else r3.revision.id)
    (row,) = _audit(db_session, "conflict_superseded")
    assert (row.statement_id, row.case_id, row.actor) == (stmt.id, older.id, "owner-test")
    assert row.detail["revision_id"] == r2.revision.id and row.detail["by_case_id"] == newest.id
    result = _confirm(db_session, stmt)
    assert result.checks["open_cases"] is True and result.status == "reconciled"


# --- route --------------------------------------------------------------------------------------------------------

TOKENS = "spa:spa-token,hermes:hermes-token,worker:worker-token"
SCOPES = "spa=legacy; hermes=read,propose,enqueue; worker=ingest"
S = {"Authorization": "Bearer spa-token"}
H = {"Authorization": "Bearer hermes-token"}
W = {"Authorization": "Bearer worker-token"}


@pytest.fixture
def recon_env(monkeypatch):
    monkeypatch.setenv(auth.TOKENS_ENV, TOKENS)
    monkeypatch.setenv(auth.SCOPES_ENV, SCOPES)
    monkeypatch.setenv(auth.FEATURE_ENV, "true")


def _url(stmt) -> str:
    return f"/accounts/{stmt.account_id}/statements/{stmt.id}/confirm"


def test_confirm_route(recon_env, client, db_session, clean):
    stmt, line = clean
    for who in (H, W):
        assert client.post(_url(stmt), headers=who, json={}).status_code == 403
    assert client.post(_url(stmt), headers=S, json={"force": True}).status_code == 422
    assert client.post(_url(stmt), headers=S, json={"force": True, "note": "x" * 1001}).status_code == 422
    _open_case(db_session, stmt, line)
    db_session.commit()
    refused = client.post(_url(stmt), headers=S, json={})
    assert refused.status_code == 409
    code, detail = refused.json()["message"].split(": ", 1)
    assert code == "confirm_refused" and json.loads(detail)["open_cases"] is False
    forced = client.post(_url(stmt), headers=S, json={"force": True, "note": "已核對"})
    assert forced.status_code == 200, forced.text
    body = forced.json()
    assert (body["status"], body["forced"], body["reopened_cases"], body["checks"]["open_cases"]) == (
        "reconciled", True, [], False)
    assert client.post(f"/accounts/{stmt.account_id + 1}/statements/{stmt.id}/confirm", headers=S,
                       json={}).status_code == 404
