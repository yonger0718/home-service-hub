"""Case actions that write no ledger rows (R1c Task 5; spec §4.9, rulings 29, 31): `dismiss`, `defer_next_period`
and `link_installment_plan` through the action engine, end to end with the reconcile. Synthetic data only."""
from datetime import date

import pytest
from sqlalchemy import select

from app.models import (
    AccountStatement, InstallmentPlanMap, ReconciliationAction, ReconciliationAudit, ReconciliationCase,
)
from app.services import reconciliation_service as rec
from app.services.errors import CodedConflictError, ValidationError
from app.services.reconciliation_actions import engine
from tests.integration.statement_fixtures import (  # noqa: F401  (run is a fixture)
    _active, _card, _cases, _code, _line, _reconcile, _submit, run,
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
    (case,) = _cases(db_session, stmt.id, "open")
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
    (case,) = _cases(db_session, stmt.id, "open")
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
    assert replayed is False and row.effect_slot == f"v{case.version}"
    case = _fresh(db_session, ReconciliationCase, case.id)
    assert (case.status, case.explanation, case.note, case.resolved_by, case.resolved_action_id) == (
        "dismissed", "accepted_exception", "銀行誤植", "owner", row.id)
    stmt = _fresh(db_session, AccountStatement, stmt.id)
    assert (stmt.explained_count, stmt.open_case_count) == (1, 0)
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
    assert _cases(db_session, stmt.id, "open") == []
    assert [(c.id, c.status) for c in _cases(db_session, stmt.id)] == [(case.id, "dismissed")]
    assert _fresh(db_session, AccountStatement, stmt.id).explained_count == 1


def test_reverting_a_dismiss_reopens_the_case_and_keeps_the_note_in_context(db_session, unmatched):
    _, stmt, _, case = unmatched
    row, _ = _apply(db_session, case, "dismiss", {"note": "略過"})
    _revert(db_session, row.id)
    case = _fresh(db_session, ReconciliationCase, case.id)
    assert (case.status, case.explanation, case.note, case.resolved_by) == ("open", None, None, None)
    assert case.context["dismiss_note"] == "略過" and case.context["reverted_action_id"] == row.id
    stmt = _fresh(db_session, AccountStatement, stmt.id)
    assert (stmt.explained_count, stmt.open_case_count) == (0, 1)
    again, _ = _apply(db_session, case, "dismiss", {"note": "再略過"})  # a new case version: acted on again
    assert again.id != row.id and again.effect_slot == f"v{case.version}"


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
    (case,) = _cases(db_session, stmt.id, "open")
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
    assert _fresh(db_session, AccountStatement, stmt.id).open_case_count == 0
    later, _, _ = _submit(db_session, run, card, [_line(1, date(2026, 10, 12), "早餐", 77)],
                          period=(date(2026, 10, 1), date(2026, 10, 30)))
    case = _fresh(db_session, ReconciliationCase, case.id)  # the later statement's reconcile linked it (R1b)
    assert (case.deferred_to_period_end, case.deferred_to_statement_id) == (date(2026, 10, 30), later.id)
    _reconcile(db_session, stmt.id)  # and a re-reconcile of the earlier one keeps the link
    assert _fresh(db_session, ReconciliationCase, case.id).deferred_to_statement_id == later.id


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
