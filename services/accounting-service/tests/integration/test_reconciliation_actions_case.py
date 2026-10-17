"""Case actions that write no ledger rows (R1c Task 5; spec §4.9, rulings 29, 31): `dismiss`, `defer_next_period`
and `link_installment_plan` through the action engine, end to end with the reconcile. Synthetic data only."""
import json
import queue
import threading
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

from app.models import (
    Account, AccountStatement, InstallmentPlanMap, ReconciliationAction, ReconciliationAudit, ReconciliationCase,
    ScheduleInstance, StatementCoverage,
)
from app.services import confirm_service, schedule_posting
from app.services import reconciliation_service as rec
from app.services.errors import CodedConflictError, ValidationError
from app.services.reconciliation_actions import engine
from app.services.reconciliation_actions.case_actions import LinkInstallmentPlanAction
from tests.conftest import Seed
from tests.helpers import wait_until_blocked
from tests.integration.statement_fixtures import (  # noqa: F401  (run is a fixture)
    OCT, _active, _card, _cases, _open_beside_gap, _code, _line, _reconcile, _submit, run,
)

DAY = date(2026, 9, 3)
PLAN_KEY = "手機|12|1000.0000"


def _apply(db, case, action, params, **kw):
    kw.setdefault("actor", "owner")
    kw.setdefault("label", "owner-test")
    row, replayed = engine.apply_action(db, case_id=case if isinstance(case, int) else case.id, action=action,
                                        params=params, **kw)
    db.commit()
    return row, replayed


def _revert(db, action_id):
    row = engine.revert_action(db, action_id=action_id, actor="owner", label="owner-test")
    db.commit()
    return row


def _refuse(db, fn):
    with pytest.raises((ValidationError, CodedConflictError)) as info:
        fn()
    db.rollback()
    return info.value


def _fresh(db, model, pk):
    db.expire_all()
    return db.get(model, pk)


@pytest.fixture
def unmatched(seed, db_session, run):
    """A live September card statement with one line nothing covers (an open line_unmatched case)."""
    card = _card(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, DAY, "神秘", 77)])
    (case,) = _open_beside_gap(db_session, stmt.id, "-77.0000")
    assert case.kind == "line_unmatched"
    return card, stmt, line, case


@pytest.fixture
def stray(seed, db_session, run):
    """A September card statement with one matched line and a ledger entry (posted on the 10th, far from the period
    end, so the automatic heuristic would not defer it) that no line covers: an open entry_unmatched case."""
    card = _card(seed, db_session)
    seed.entry(card, "-580", day=DAY, merchant="全聯")
    entry = seed.entry(card, "-77", day=date(2026, 9, 10), merchant="早餐")
    db_session.commit()
    stmt, _, _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)])
    (case,) = _open_beside_gap(db_session, stmt.id, "77.0000")
    assert (case.kind, case.entry_id) == ("entry_unmatched", entry.id)
    return card, stmt, entry, case


@pytest.fixture
def unmapped(seed, db_session, run):
    """An installment line whose plan is not confirmed yet: open line_unmatched / installment_unmapped, candidate
    definition = the one whose seq-2 instance posted the 1000 entry."""
    card = _card(seed, db_session)
    definition = seed.definition([seed.line("expense", card, "1000")], kind="installment", name="手機", times=12)
    entry = seed.entry(card, "-1000", day=date(2026, 9, 5), merchant="手機")
    seed.instance(definition, 2, date(2026, 9, 5), status="posted", entries=[entry])
    db_session.commit()
    stmt, (line,), _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 6), "手機", 1000, "installment",
                                                             installment_seq=2, installment_total=12)])
    (case,) = [c for c in _cases(db_session, stmt.id, "open") if c.kind == "line_unmatched"]
    assert case.context == {"hint": "installment_unmapped", "plan_key": PLAN_KEY}
    assert case.candidates == [{"definition_id": definition.id}]
    return card, stmt, line, case, definition, entry


# --- dismiss ------------------------------------------------------------------------------------------------------

def test_dismiss_resolves_with_the_note_and_counts_as_explained(db_session, unmatched):
    _, stmt, _, case = unmatched
    row, replayed = _apply(db_session, case, "dismiss", {"note": "銀行誤植"})
    assert replayed is False and row.effect_slot == f"c{case.id}v{case.version}"
    case = _fresh(db_session, ReconciliationCase, case.id)
    assert (case.status, case.explanation, case.note, case.resolved_by, case.resolved_action_id) == (
        "dismissed", "accepted_exception", "銀行誤植", "owner", row.id)
    stmt = _fresh(db_session, AccountStatement, stmt.id)
    assert (stmt.explained_count, stmt.open_case_count) == (1, 1)  # the card balance_gap of the 77 stays open
    assert _open_beside_gap(db_session, stmt.id, "-77.0000") == []
    events = [a.event for a in db_session.execute(select(ReconciliationAudit)).scalars()]
    assert "action_applied" in events


@pytest.mark.parametrize("note", ["", "x" * 1001])
def test_dismiss_note_is_one_to_a_thousand_characters(db_session, unmatched, note):
    _, _, _, case = unmatched
    _refuse(db_session, lambda: _apply(db_session, case, "dismiss", {"note": note}))
    assert _fresh(db_session, ReconciliationCase, case.id).status == "open"


def test_a_dismissed_case_is_not_recreated_by_the_next_reconcile(db_session, unmatched):
    _, stmt, _, case = unmatched
    _apply(db_session, case, "dismiss", {"note": "略過"})
    _reconcile(db_session, stmt.id)
    assert _open_beside_gap(db_session, stmt.id, "-77.0000") == []
    assert [(c.id, c.status) for c in _cases(db_session, stmt.id) if c.kind != "balance_gap"] == [(case.id, "dismissed")]
    assert _fresh(db_session, AccountStatement, stmt.id).explained_count == 1


def test_reverting_a_dismiss_reopens_the_case_and_keeps_the_note_in_context(db_session, unmatched):
    _, stmt, _, case = unmatched
    row, _ = _apply(db_session, case, "dismiss", {"note": "略過"})
    _revert(db_session, row.id)
    case = _fresh(db_session, ReconciliationCase, case.id)
    assert (case.status, case.explanation, case.note, case.resolved_by) == ("open", None, None, None)
    assert case.context["dismiss_note"] == "略過" and case.context["reverted_action_id"] == row.id
    stmt = _fresh(db_session, AccountStatement, stmt.id)
    assert (stmt.explained_count, stmt.open_case_count) == (0, 2)  # the case and the card balance_gap
    again, _ = _apply(db_session, case, "dismiss", {"note": "再略過"})  # a new case version: acted on again
    assert again.id != row.id and again.effect_slot == f"c{case.id}v{case.version}"


# --- defer_next_period --------------------------------------------------------------------------------------------

def test_defer_is_refused_on_other_kinds(db_session, unmatched):
    _, _, _, case = unmatched
    error = _refuse(db_session, lambda: _apply(db_session, case, "defer_next_period", {"entry_id": 1}))
    assert isinstance(error, ValidationError)
    assert _fresh(db_session, ReconciliationCase, case.id).status == "open"


def test_defer_is_refused_on_a_foreign_entry(db_session, stray):
    _, _, entry, case = stray
    _refuse(db_session, lambda: _apply(db_session, case, "defer_next_period", {"entry_id": entry.id + 1}))
    assert _fresh(db_session, ReconciliationCase, case.id).status == "open"


def test_defer_on_a_card_uses_the_closing_day(db_session, stray):
    _, stmt, entry, case = stray
    row, _ = _apply(db_session, case, "defer_next_period", {"entry_id": entry.id})
    case = _fresh(db_session, ReconciliationCase, case.id)
    assert (case.status, case.explanation, case.resolved_by, case.resolved_action_id) == (
        "resolved", "deferred_next_period", "owner", row.id)
    assert (case.deferred_to_period_end, case.deferred_to_statement_id) == (date(2026, 10, 30), None)
    assert _fresh(db_session, AccountStatement, stmt.id).explained_count == 1


def test_defer_on_a_card_without_a_closing_day_uses_the_matcher_period_end(seed, db_session, run):
    """Final review I1: a card with no closing_day defers to the period end the matcher's own deferrals use (the
    statement's period_end day), so the next statement links it instead of it being lost."""
    card = _card(seed, db_session, closing_day=None)
    seed.entry(card, "-580", day=DAY, merchant="全聯")
    entry = seed.entry(card, "-77", day=date(2026, 9, 10), merchant="早餐")
    db_session.commit()
    stmt, _, _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)])
    (case,) = _open_beside_gap(db_session, stmt.id, "77.0000")
    _apply(db_session, case, "defer_next_period", {"entry_id": entry.id})
    statement, account = _fresh(db_session, AccountStatement, stmt.id), card
    assert _fresh(db_session, ReconciliationCase, case.id).deferred_to_period_end == date(2026, 10, 30)
    assert rec.deferral_period_end(statement, account) == date(2026, 10, 30)
    later, _, _ = _submit(db_session, run, card, [_line(1, date(2026, 10, 12), "早餐", 77)],
                          period=(date(2026, 10, 1), date(2026, 10, 30)))
    assert _fresh(db_session, ReconciliationCase, case.id).deferred_to_statement_id == later.id


def test_defer_on_a_bank_account_uses_the_month_end(seed, db_session, run):
    bank = seed.account("銀行", statement_live_from=date(2026, 9, 1))
    seed.entry(bank, "-77", day=date(2026, 9, 10), merchant="早餐")
    db_session.commit()
    stmt, _, _ = _submit(db_session, run, bank, [_line(1, DAY, "存入", 500, "deposit")], kind="bank")
    cases = _cases(db_session, stmt.id, "open")
    case = next(c for c in cases if c.kind == "entry_unmatched")
    _apply(db_session, case, "defer_next_period", {"entry_id": case.entry_id})
    assert _fresh(db_session, ReconciliationCase, case.id).deferred_to_period_end == date(2026, 10, 31)


def test_reverting_a_defer_clears_the_deferral_and_reopens(db_session, stray):
    _, _, entry, case = stray
    row, _ = _apply(db_session, case, "defer_next_period", {"entry_id": entry.id})
    _revert(db_session, row.id)
    case = _fresh(db_session, ReconciliationCase, case.id)
    assert (case.status, case.explanation, case.deferred_to_period_end, case.deferred_to_statement_id) == (
        "open", None, None, None)


def test_an_owner_deferral_survives_a_reconcile_and_the_later_statement_links_it(db_session, stray, run):
    card, stmt, entry, case = stray
    _apply(db_session, case, "defer_next_period", {"entry_id": entry.id})
    _reconcile(db_session, stmt.id)
    _reconcile(db_session, stmt.id)
    cases = [c for c in _cases(db_session, stmt.id) if c.kind == "entry_unmatched"]
    assert [(c.id, c.status, c.explanation, c.resolved_by) for c in cases] == [
        (case.id, "resolved", "deferred_next_period", "owner")]
    # the owner's deferral is excluded from September's balance: no balance_gap either
    assert _fresh(db_session, AccountStatement, stmt.id).open_case_count == 0
    later, _, _ = _submit(db_session, run, card, [_line(1, date(2026, 10, 12), "早餐", 77)],
                          period=(date(2026, 10, 1), date(2026, 10, 30)))
    case = _fresh(db_session, ReconciliationCase, case.id)  # the later statement's reconcile linked it (R1b)
    assert (case.deferred_to_period_end, case.deferred_to_statement_id) == (date(2026, 10, 30), later.id)
    _reconcile(db_session, stmt.id)  # and a re-reconcile of the earlier one keeps the link
    assert _fresh(db_session, ReconciliationCase, case.id).deferred_to_statement_id == later.id



def test_a_defer_is_not_revertable_after_a_later_action_on_its_case(seed, db_session, stray, run):
    """Multica AGENT-107: a `defer_next_period` touches no rows, so the row-overlap rule alone let it be reverted
    after the next statement's confirm reopened the case and the owner dismissed it, clearing the dismissal while the
    dismiss row stayed applied. A later applied action on the same case blocks the revert; the latest still reverts."""
    card, _, entry, case = stray
    deferred, _ = _apply(db_session, case, "defer_next_period", {"entry_id": entry.id})
    seed.entry(card, "-120", day=date(2026, 10, 5), merchant="超商")
    db_session.commit()
    # October prints what it carries over (card balance check): September's 580 and the deferred 77 it never prints
    later, _, _ = _submit(db_session, run, card, [_line(1, date(2026, 10, 5), "超商", 120)],
                          period=(date(2026, 10, 1), date(2026, 10, 30)), opening=Decimal("657"))
    assert later.open_case_count == 0
    result = confirm_service.confirm_statement(db_session, account_id=card.id, statement_id=later.id, force=False,
                                               note=None, label="owner-test", request_id=None)
    db_session.commit()
    assert result.reopened_cases == [case.id]  # the deferred entry is unclaimed there: the case reopens
    reopened = _fresh(db_session, ReconciliationCase, case.id)
    assert (reopened.status, reopened.version) == ("open", 2)

    dismissed, _ = _apply(db_session, case, "dismiss", {"note": "不追了"})

    error = _refuse(db_session, lambda: _revert(db_session, deferred.id))
    assert _code(error) == "not_revertable" and f"later action {dismissed.id}" in str(error)
    assert _fresh(db_session, ReconciliationAction, dismissed.id).status == "applied"
    assert _fresh(db_session, ReconciliationAction, deferred.id).status == "applied"
    case = _fresh(db_session, ReconciliationCase, case.id)
    assert (case.status, case.explanation, case.note, case.resolved_action_id) == (
        "dismissed", "accepted_exception", "不追了", dismissed.id)

    _revert(db_session, dismissed.id)  # the latest action on the case still reverts
    assert _fresh(db_session, ReconciliationAction, dismissed.id).status == "reverted"
    assert _fresh(db_session, ReconciliationCase, case.id).status == "open"

# --- link_installment_plan ----------------------------------------------------------------------------------------

def test_link_with_a_definition_outside_the_candidates_is_id_out_of_scope(seed, db_session, unmapped):
    card, stmt, _, case, definition, _ = unmapped
    other = seed.definition([seed.line("expense", card, "500")], kind="installment", name="冰箱", times=6)
    db_session.commit()
    error = _refuse(db_session, lambda: _apply(db_session, case, "link_installment_plan",
                                               {"definition_id": other.id}))
    assert _code(error) == "id_out_of_scope"
    assert db_session.execute(select(InstallmentPlanMap)).scalars().all() == []


def test_link_needs_an_active_installment_definition(db_session, unmapped):
    _, _, _, case, definition, _ = unmapped
    definition.status = "paused"
    db_session.commit()
    error = _refuse(db_session, lambda: _apply(db_session, case, "link_installment_plan",
                                               {"definition_id": definition.id}))
    assert isinstance(error, ValidationError)
    assert db_session.execute(select(InstallmentPlanMap)).scalars().all() == []


def test_link_maps_the_plan_and_claims_the_line_in_the_same_call(db_session, unmapped):
    card, stmt, line, case, definition, entry = unmapped
    row, _ = _apply(db_session, case, "link_installment_plan", {"definition_id": definition.id})
    (plan,) = db_session.execute(select(InstallmentPlanMap)).scalars().all()
    assert (plan.account_id, plan.plan_key, plan.definition_id, plan.confirmed_by) == (
        card.id, PLAN_KEY, definition.id, "owner")
    assert row.inverse == {"plan_map_id": plan.id}
    (claim,) = _active(db_session, stmt.id)
    assert (claim.line_id, claim.entry_id, claim.match_rule) == (line.id, entry.id, "installment")
    assert row.touched_entry_ids == [entry.id]
    case = _fresh(db_session, ReconciliationCase, case.id)
    assert (case.status, case.resolved_by, case.resolved_action_id, case.version) == ("resolved", "owner", row.id, 1)
    # the instance entry's own entry_unmatched case went with the claim; no open case is left
    assert [(c.kind, c.status) for c in _cases(db_session, stmt.id)] == [
        ("line_unmatched", "resolved"), ("entry_unmatched", "superseded")]
    stmt = _fresh(db_session, AccountStatement, stmt.id)
    assert (stmt.matched_count, stmt.open_case_count) == (1, 0)


def test_link_to_an_already_mapped_plan_is_plan_already_mapped(db_session, unmapped):
    card, _, _, case, definition, _ = unmapped
    db_session.add(InstallmentPlanMap(account_id=card.id, plan_key=PLAN_KEY, definition_id=definition.id,
                                      confirmed_by="owner"))
    db_session.commit()
    error = _refuse(db_session, lambda: _apply(db_session, case, "link_installment_plan",
                                               {"definition_id": definition.id}))
    assert _code(error) == "plan_already_mapped"
    assert _active(db_session, case.statement_id) == []


def test_reverting_a_link_removes_the_map_and_the_claim(db_session, unmapped):
    _, stmt, line, case, definition, _ = unmapped
    row, _ = _apply(db_session, case, "link_installment_plan", {"definition_id": definition.id})
    _revert(db_session, row.id)
    assert db_session.execute(select(InstallmentPlanMap)).scalars().all() == []
    assert _active(db_session, stmt.id) == []
    opened = [c for c in _cases(db_session, stmt.id, "open") if c.kind == "line_unmatched"]
    assert [(c.id, c.context["hint"]) for c in opened] == [(case.id, "installment_unmapped")]
    assert [c.kind for c in _cases(db_session, stmt.id, "open")] == ["line_unmatched", "entry_unmatched"]
    assert _fresh(db_session, ReconciliationAction, row.id).status == "reverted"


def test_a_link_is_not_revertable_after_a_later_action_touched_its_entries(db_session, unmapped):
    _, stmt, line, case, definition, entry = unmapped
    row, _ = _apply(db_session, case, "link_installment_plan", {"definition_id": definition.id})
    recheck = ReconciliationCase(statement_id=stmt.id, revision_id=stmt.current_revision_id, kind="recheck",
                                 event_id=line.event_id, line_id=line.id)
    db_session.add(recheck)
    db_session.commit()
    _apply(db_session, recheck, "unmatch", {"line_id": line.id})
    error = _refuse(db_session, lambda: _revert(db_session, row.id))
    assert _code(error) == "not_revertable"
    assert db_session.execute(select(InstallmentPlanMap)).scalars().all() != []


def test_reverting_a_link_keeps_the_claim_its_reconcile_made_for_another_line(seed, db_session, run):
    """Final review C1: the link's in-action reconcile also claims a second line whose entry was booked after the
    last reconcile; the link records (and its revert releases) only coverage on its own case's line."""
    card = _card(seed, db_session)
    definition = seed.definition([seed.line("expense", card, "1000")], kind="installment", name="手機", times=12)
    entry = seed.entry(card, "-1000", day=date(2026, 9, 5), merchant="手機")
    seed.instance(definition, 2, date(2026, 9, 5), status="posted", entries=[entry])
    db_session.commit()
    stmt, (line, other), _ = _submit(db_session, run, card, [
        _line(1, date(2026, 9, 6), "手機", 1000, "installment", installment_seq=2, installment_total=12),
        _line(2, date(2026, 9, 7), "早餐", 77)])
    (case,) = [c for c in _cases(db_session, stmt.id, "open") if c.line_id == line.id]
    assert [c.kind for c in _cases(db_session, stmt.id, "open") if c.line_id == other.id] == ["line_unmatched"]
    late = seed.entry(card, "-77", day=date(2026, 9, 7), merchant="早餐")  # booked after the reconcile
    db_session.commit()

    row, _ = _apply(db_session, case, "link_installment_plan", {"definition_id": definition.id})
    claims = {c.line_id: c for c in _active(db_session, stmt.id)}
    assert {(c.line_id, c.entry_id) for c in claims.values()} == {(line.id, entry.id), (other.id, late.id)}
    assert row.after["coverage_ids"] == [claims[line.id].id]
    assert row.touched_entry_ids == [entry.id]

    _revert(db_session, row.id)
    assert [(c.line_id, c.entry_id) for c in _active(db_session, stmt.id)] == [(other.id, late.id)]
    assert [c.line_id for c in _cases(db_session, stmt.id, "open") if c.kind == "line_unmatched"] == [line.id]


# --- link_installment_plan revert across statements (R1d item 3) ----------------------------------------------------

class _Run:
    """Run fn() in a daemon thread and keep the handle: `result(timeout)` = the return value or the exception (cached: safe to call in the body and again in
    `finally`), failing explicitly when the thread is still alive after `timeout` (a NOWAIT path that waited).
    fn owns its own session and must roll it back / close it itself."""

    def __init__(self, fn):
        self.out: queue.Queue = queue.Queue()
        self.outcome = None  # cached by result(): it is called once in the body and again in `finally`
        self.thread = threading.Thread(target=self._go, args=(fn,), daemon=True)
        self.thread.start()

    def _go(self, fn):
        try:
            self.out.put(fn())
        except Exception as exc:  # noqa: BLE001  (asserted by the caller)
            self.out.put(exc)

    def result(self, timeout=5.0):
        """Idempotent: the first call joins and takes the outcome, later calls return the same outcome."""
        if self.outcome is None:
            self.thread.join(timeout)
            assert not self.thread.is_alive(), f"still running after {timeout} s: the call waited instead of refusing"
            self.outcome = (self.out.get_nowait(),)
        return self.outcome[0]


def _own_session(pg_engine, fn, *, commit=False):
    """A worker body that opens, uses and closes its own session (rolled back unless `commit`)."""
    def body():
        session = sessionmaker(bind=pg_engine, autoflush=False)()
        try:
            value = fn(session)
            if commit:
                session.commit()
            return value
        finally:
            session.rollback()
            session.close()
    return body


def _busy(outcome, code):
    assert isinstance(outcome, CodedConflictError) and outcome.code == code, outcome


UNTOUCHED = {"maps": 1, "action": "applied", "case": "resolved", "reverts": 0}


def _refused_revert(row, case):
    """A worker `fn` for `_own_session`: the revert, and when it is refused, `(exc, snapshot)` read in the SAME
    session before its rollback (anything the refused attempt left in the transaction shows up here, flushed
    first: the session does not autoflush). A revert that succeeds returns `(row, None)`."""
    def fn(db):
        try:
            return engine.revert_action(db, action_id=row.id, actor="owner", label="owner-test"), None
        except CodedConflictError as exc:
            db.flush()
            return exc, {
                "maps": db.execute(select(func.count()).select_from(InstallmentPlanMap)).scalar_one(),
                "action": db.execute(select(ReconciliationAction.status)
                                     .where(ReconciliationAction.id == row.id)).scalar_one(),
                "case": db.execute(select(ReconciliationCase.status)
                                   .where(ReconciliationCase.id == case.id)).scalar_one(),
                "reverts": db.execute(select(func.count()).select_from(ReconciliationAction)
                                      .where(ReconciliationAction.action == "revert")).scalar_one(),
            }
    return fn


@pytest.fixture
def unmapped_twice(seed, db_session, run):
    """The same unconfirmed plan printed on September (seq 2) and October (seq 3): two installment_unmapped cases."""
    card = _card(seed, db_session)
    definition = seed.definition([seed.line("expense", card, "1000")], kind="installment", name="手機", times=12)
    sep_entry = seed.entry(card, "-1000", day=date(2026, 9, 5), merchant="手機")
    oct_entry = seed.entry(card, "-1000", day=date(2026, 10, 5), merchant="手機")
    seed.instance(definition, 2, date(2026, 9, 5), status="posted", entries=[sep_entry])
    seed.instance(definition, 3, date(2026, 10, 5), status="posted", entries=[oct_entry])
    db_session.commit()
    sep, (sep_line,), _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 6), "手機", 1000, "installment",
                                                              installment_seq=2, installment_total=12)])
    # October prints September's unpaid 1000 as its opening (card balance check: the ledger holds both instalments)
    oct, (oct_line,), _ = _submit(db_session, run, card, [_line(1, date(2026, 10, 6), "手機", 1000, "installment",
                                                              installment_seq=3, installment_total=12)], period=OCT,
                                  opening="1000")
    (sep_case,) = [c for c in _cases(db_session, sep.id, "open") if c.kind == "line_unmatched"]
    (oct_case,) = [c for c in _cases(db_session, oct.id, "open") if c.kind == "line_unmatched"]
    assert sep_case.context["hint"] == oct_case.context["hint"] == "installment_unmapped"
    return SimpleNamespace(card=card, definition=definition, sep=sep, oct=oct, sep_case=sep_case, oct_case=oct_case,
                           sep_entry=sep_entry, oct_entry=oct_entry)


@pytest.fixture
def unmapped_then_pending(seed, db_session, run):
    """September posted (seq 2); October's instance (seq 3) still PENDING — no October entry exists yet."""
    card = _card(seed, db_session)
    definition = seed.definition([seed.line("expense", card, "1000")], kind="installment", name="手機", times=12)
    sep_entry = seed.entry(card, "-1000", day=date(2026, 9, 5), merchant="手機")
    seed.instance(definition, 2, date(2026, 9, 5), status="posted", entries=[sep_entry])
    oct_instance = seed.instance(definition, 3, date(2026, 10, 5), status="pending")
    db_session.commit()
    sep, _, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 6), "手機", 1000, "installment",
                                                    installment_seq=2, installment_total=12)])
    oct, _, _ = _submit(db_session, run, card, [_line(1, date(2026, 10, 6), "手機", 1000, "installment",
                                                    installment_seq=3, installment_total=12)], period=OCT)
    (sep_case,) = [c for c in _cases(db_session, sep.id, "open") if c.kind == "line_unmatched"]
    return SimpleNamespace(card=card, definition=definition, sep=sep, oct=oct, sep_case=sep_case,
                           sep_entry=sep_entry, oct_instance=oct_instance)


@pytest.fixture
def unmapped_two_accounts(seed, db_session, run):
    """Must (AGENT-111): one installment definition whose template posts to two cards (A 1000, B 500); one posted
    instance (seq 2) with an entry on each; a September statement per card printing its own seq-2 line, each an
    installment_unmapped case. Each card's map is its own (`installment_plan_map` is per account)."""
    a, b = _card(seed, db_session, "A卡"), _card(seed, db_session, "B卡")
    definition = seed.definition([seed.line("expense", a, "1000"), seed.line("expense", b, "500")],
                                 kind="installment", name="手機", times=12)
    entry_a = seed.entry(a, "-1000", day=date(2026, 9, 5), merchant="手機")
    entry_b = seed.entry(b, "-500", day=date(2026, 9, 5), merchant="手機")
    seed.instance(definition, 2, date(2026, 9, 5), status="posted", entries=[entry_a, entry_b])
    db_session.commit()
    stmt_a, _, _ = _submit(db_session, run, a, [_line(1, date(2026, 9, 6), "手機", 1000, "installment",
                                                      installment_seq=2, installment_total=12)])
    stmt_b, _, _ = _submit(db_session, run, b, [_line(1, date(2026, 9, 6), "手機", 500, "installment",
                                                      installment_seq=2, installment_total=12)])
    (case_a,) = [c for c in _cases(db_session, stmt_a.id, "open") if c.kind == "line_unmatched"]
    (case_b,) = [c for c in _cases(db_session, stmt_b.id, "open") if c.kind == "line_unmatched"]
    assert case_a.context["hint"] == case_b.context["hint"] == "installment_unmapped"
    return SimpleNamespace(a=a, b=b, definition=definition, stmt_a=stmt_a, stmt_b=stmt_b, case_a=case_a,
                           case_b=case_b, entry_a=entry_a, entry_b=entry_b)


def test_reverting_a_link_leaves_another_accounts_map_and_claims_alone(db_session, unmapped_two_accounts):
    """AGENT-111 Must: B linked first, then A; reverting A's link releases only A's claim and removes only A's map.
    B's claim, flags, counts and map stay, and B's own link stays revertable."""
    s = unmapped_two_accounts
    row_b, _ = _apply(db_session, s.case_b, "link_installment_plan", {"definition_id": s.definition.id})
    claims_b = [(c.id, c.entry_id) for c in _active(db_session, s.stmt_b.id)]
    assert [entry for _, entry in claims_b] == [s.entry_b.id]
    matched_b = _fresh(db_session, AccountStatement, s.stmt_b.id).matched_count
    row_a, _ = _apply(db_session, s.case_a, "link_installment_plan", {"definition_id": s.definition.id})
    (claim_a,) = _active(db_session, s.stmt_a.id)
    assert claim_a.entry_id == s.entry_a.id

    _revert(db_session, row_a.id)

    assert _active(db_session, s.stmt_a.id) == []
    assert _fresh(db_session, StatementCoverage, claim_a.id).status == "stale"
    assert [(c.id, c.entry_id) for c in _active(db_session, s.stmt_b.id)] == claims_b
    stmt_b = _fresh(db_session, AccountStatement, s.stmt_b.id)
    assert (stmt_b.needs_recheck, stmt_b.matched_count) == (False, matched_b)
    maps = db_session.execute(select(InstallmentPlanMap.account_id)).scalars().all()
    assert maps == [s.b.id]  # A's map gone, B's kept

    _revert(db_session, row_b.id)  # B's coverage is intact: its link is still revertable
    assert db_session.execute(select(InstallmentPlanMap)).scalars().all() == []
    assert _active(db_session, s.stmt_b.id) == []


def _link(db, s):
    row, _ = _apply(db, s.sep_case, "link_installment_plan", {"definition_id": s.definition.id})
    return row


def _post_october(pg_engine, s):
    """Another transaction posts October's pending instance (a new posted entry appears) and commits."""
    session = sessionmaker(bind=pg_engine, autoflush=False)()
    try:
        entry = Seed(session).entry(session.get(Account, s.card.id), "-1000", day=date(2026, 10, 5), merchant="手機")
        instance = session.get(ScheduleInstance, s.oct_instance.id)
        instance.status, instance.posted_entry_ids = "posted", [entry.id]
        instance.acted_at, instance.acted_by = func.now(), "auto"  # ck_schedule_instance_acted
        session.commit()
        return entry.id
    finally:
        session.close()


def test_reverting_a_link_releases_the_plans_claims_on_other_statements(db_session, unmapped_twice):
    s = unmapped_twice
    row = _link(db_session, s)
    _reconcile(db_session, s.oct.id)  # October now claims its line through the confirmed map
    (oct_claim,) = _active(db_session, s.oct.id)
    assert oct_claim.entry_id == s.oct_entry.id

    _revert(db_session, row.id)

    assert _active(db_session, s.sep.id) == [] and _active(db_session, s.oct.id) == []
    released = _fresh(db_session, StatementCoverage, oct_claim.id)
    assert (released.status, released.stale_reason) == ("stale", "plan_unmapped")
    oct = _fresh(db_session, AccountStatement, s.oct.id)
    assert (oct.needs_recheck, oct.matched_count, oct.open_case_count) == (True, 0, 0)
    assert _fresh(db_session, ReconciliationAction, row.id).status == "reverted"

    _reconcile(db_session, s.oct.id)  # the October case is raised again
    reopened = [c for c in _cases(db_session, s.oct.id, "open") if c.kind == "line_unmatched"]
    assert [c.context["hint"] for c in reopened] == ["installment_unmapped"]
    assert _fresh(db_session, AccountStatement, s.oct.id).needs_recheck is False


def test_reverting_a_link_flags_a_confirmed_other_statement(db_session, unmapped_twice):
    """AGENT-109 S3: an already reconciled October keeps its status, loses the claim, refuses confirm until its
    next reconcile reopens the case."""
    s = unmapped_twice
    row = _link(db_session, s)
    _reconcile(db_session, s.oct.id)
    confirm_service.confirm_statement(db_session, account_id=s.card.id, statement_id=s.oct.id, force=False, note=None,
                                      label="owner-test", request_id=None)
    db_session.commit()
    assert _fresh(db_session, AccountStatement, s.oct.id).status == "reconciled"

    _revert(db_session, row.id)

    oct = _fresh(db_session, AccountStatement, s.oct.id)
    assert (oct.status, oct.needs_recheck, oct.matched_count, oct.open_case_count) == ("reconciled", True, 0, 0)
    exc = _refuse(db_session, lambda: confirm_service.confirm_statement(
        db_session, account_id=s.card.id, statement_id=s.oct.id, force=False, note=None, label="owner-test",
        request_id=None))
    checks = json.loads(str(exc).split(": ", 1)[1])  # the checks JSON is the message's detail part
    assert exc.code == "confirm_refused" and checks["needs_recheck"] is False  # the check failed

    _reconcile(db_session, s.oct.id)
    oct = _fresh(db_session, AccountStatement, s.oct.id)
    # back to October's pre-link state: its line's installment_unmapped case and the instance entry's entry_unmatched
    assert (oct.status, oct.needs_recheck, oct.open_case_count) == ("reconciled", False, 2)
    assert [(c.kind, c.context.get("hint") if c.kind == "line_unmatched" else c.entry_id)
            for c in _cases(db_session, s.oct.id, "open")] == [
        ("line_unmatched", "installment_unmapped"), ("entry_unmatched", s.oct_entry.id)]


def test_reverting_a_link_is_busy_while_another_statement_reconciles(db_session, unmapped_twice, pg_engine):
    """AGENT-109 Must 3: October's reconcile holds its candidate entry and an UNCOMMITTED claim through the map;
    the revert must be action_busy (map intact); after that commit the retry releases the claim."""
    s = unmapped_twice
    row = _link(db_session, s)
    holder = sessionmaker(bind=pg_engine, autoflush=False)()
    run = None
    try:
        rec.reconcile(holder, s.oct.id)  # holds October's statement + entry, claim written, not committed
        run = _Run(_own_session(pg_engine, _refused_revert(row, s.sep_case)))
        exc, snapshot = run.result()
        _busy(exc, "action_busy")
        assert snapshot == UNTOUCHED  # read in the refused revert's own transaction
        holder.commit()
    finally:
        holder.close()
        if run is not None:
            run.result()  # the worker thread is reclaimed (bounded) after the holder is released
    _revert(db_session, row.id)  # the retry sees the committed claim, locks October and releases it
    assert db_session.execute(select(InstallmentPlanMap)).scalars().all() == []
    assert _active(db_session, s.oct.id) == [] and _fresh(db_session, AccountStatement, s.oct.id).needs_recheck is True


def test_another_statements_reconcile_is_busy_during_a_link_revert(db_session, unmapped_twice, pg_engine):
    """The mirror: the revert holds the plan's entries; October's reconcile is reconcile_busy; after the revert
    commits it finds no map and writes no claim."""
    s = unmapped_twice
    row = _link(db_session, s)
    reverter = sessionmaker(bind=pg_engine, autoflush=False)()
    run = None
    try:
        engine.revert_action(reverter, action_id=row.id, actor="owner", label="owner-test")  # not committed yet
        run = _Run(_own_session(pg_engine, lambda db: rec.reconcile(db, s.oct.id)))
        _busy(run.result(), "reconcile_busy")
        reverter.commit()
    finally:
        reverter.close()
        if run is not None:
            run.result()
    _reconcile(db_session, s.oct.id)
    assert _active(db_session, s.oct.id) == []
    assert [c.context["hint"] for c in _cases(db_session, s.oct.id, "open") if c.kind == "line_unmatched"] == [
        "installment_unmapped"]


def test_an_instance_posted_after_discovery_makes_the_revert_retry(db_session, unmapped_then_pending, pg_engine,
                                                                     monkeypatch):
    """AGENT-110 Must 1: between the revert's unlocked discovery and its locks, October's instance is posted and
    October's reconcile claims the new entry (uncommitted). The revert must notice the grown posted set under the
    exclusive definition lock and refuse with nothing mutated; the retry after the commit releases the claim."""
    s = unmapped_then_pending
    row = _link(db_session, s)
    factory = sessionmaker(bind=pg_engine, autoflush=False)
    holder = factory()
    real = LinkInstallmentPlanAction.revert_lock_targets
    fired = []

    def discovery_then_posting(self, ctx, action):
        targets = real(self, ctx, action)
        if not fired:
            fired.append(_post_october(pg_engine, s))  # P: October posted + committed after the discovery
            rec.reconcile(holder, s.oct.id)  # B: claims the new entry through the still-valid map, uncommitted
        return targets

    monkeypatch.setattr(LinkInstallmentPlanAction, "revert_lock_targets", discovery_then_posting)
    run = None
    try:
        run = _Run(_own_session(pg_engine, _refused_revert(row, s.sep_case)))
        exc, snapshot = run.result()
        _busy(exc, "action_busy")
        assert "was posted after discovery" in str(exc)  # _check_lock_set's posted-set branch, not a lock conflict
        # nothing mutated: map, action and case as before, no revert row (read in the revert's own transaction)
        assert snapshot == UNTOUCHED
        holder.commit()  # B's claim lands
    finally:
        holder.close()
        if run is not None:
            run.result()
    (claim,) = _active(db_session, s.oct.id)
    assert claim.entry_id == fired[0]
    _revert(db_session, row.id)  # the retry discovers the posted entry and October, locks and releases
    assert _active(db_session, s.oct.id) == [] and _fresh(db_session, AccountStatement, s.oct.id).needs_recheck is True
    assert db_session.execute(select(InstallmentPlanMap)).scalars().all() == []


def test_a_claim_committed_after_discovery_makes_the_revert_retry(db_session, unmapped_twice, pg_engine, monkeypatch):
    """`_check_lock_set`'s late-statement branch: between the revert's unlocked discovery (no other statement claims
    the plan yet) and its locks, October's reconcile commits a claim through the still-valid map. The posted set did
    not grow, so only the statement re-derivation can notice: action_busy with nothing mutated; the retry discovers
    October, locks it and releases the claim."""
    s = unmapped_twice
    row = _link(db_session, s)
    real = LinkInstallmentPlanAction.revert_lock_targets
    fired = []

    def discovery_then_claim(self, ctx, action):
        targets = real(self, ctx, action)
        if not fired:
            fired.append(True)
            _own_session(pg_engine, lambda db: rec.reconcile(db, s.oct.id), commit=True)()  # October claims, commits
        return targets

    monkeypatch.setattr(LinkInstallmentPlanAction, "revert_lock_targets", discovery_then_claim)
    run = None
    try:
        run = _Run(_own_session(pg_engine, _refused_revert(row, s.sep_case)))
        exc, snapshot = run.result()
        _busy(exc, "action_busy")
        assert "gained a plan claim" in str(exc)
        assert snapshot == UNTOUCHED  # read in the refused revert's own transaction
    finally:
        if run is not None:
            run.result()
    (claim,) = _active(db_session, s.oct.id)
    assert claim.entry_id == s.oct_entry.id
    _revert(db_session, row.id)  # the hook has fired: the retry discovers October, locks it and releases the claim
    assert _active(db_session, s.oct.id) == [] and _fresh(db_session, AccountStatement, s.oct.id).needs_recheck is True
    assert db_session.execute(select(InstallmentPlanMap)).scalars().all() == []


def test_a_posting_in_flight_makes_the_revert_busy(db_session, unmapped_then_pending, pg_engine):
    """AGENT-110 Should 1: the real posting lock (`lock_definition_for_post`, FOR SHARE) conflicts with the
    revert's FOR UPDATE NOWAIT on the definition: the revert is action_busy at once, nothing mutated."""
    s = unmapped_then_pending
    row = _link(db_session, s)
    poster = sessionmaker(bind=pg_engine, autoflush=False)()
    run = None
    try:
        schedule_posting.lock_definition_for_post(poster, s.definition.id)  # a posting holds the definition
        run = _Run(_own_session(pg_engine, _refused_revert(row, s.sep_case)))
        exc, snapshot = run.result()
        _busy(exc, "action_busy")
        assert snapshot == UNTOUCHED  # read in the refused revert's own transaction
        poster.commit()
    finally:
        poster.close()
        if run is not None:
            run.result()
    _revert(db_session, row.id)  # released: the retry succeeds
    assert db_session.execute(select(InstallmentPlanMap)).scalars().all() == []


def test_a_posting_waits_behind_an_open_link_revert(db_session, unmapped_then_pending, pg_engine):
    """The mirror: while the revert holds the definition FOR UPDATE, a posting's FOR SHARE cannot complete; it
    blocks on a lock the reverter's backend holds (pg_blocking_pids) and finishes only after the revert commits."""
    s = unmapped_then_pending
    row = _link(db_session, s)
    reverter = sessionmaker(bind=pg_engine, autoflush=False)()
    pids: queue.Queue = queue.Queue()

    def posting(db):
        pids.put(db.execute(text("SELECT pg_backend_pid()")).scalar_one())  # pins the worker's connection first
        return schedule_posting.lock_definition_for_post(db, s.definition.id).id

    run = None
    try:
        engine.revert_action(reverter, action_id=row.id, actor="owner", label="owner-test")  # holds the definition
        reverter_pid = reverter.execute(text("SELECT pg_backend_pid()")).scalar_one()  # same open transaction
        run = _Run(_own_session(pg_engine, posting, commit=True))
        wait_until_blocked(pg_engine, pids.get(timeout=5.0), blocker_pid=reverter_pid)
        reverter.commit()
        assert run.result(5.0) == s.definition.id  # released: the posting's lock is granted and it completes
    finally:
        reverter.close()
        if run is not None:
            run.result()
    assert db_session.execute(select(InstallmentPlanMap)).scalars().all() == []
