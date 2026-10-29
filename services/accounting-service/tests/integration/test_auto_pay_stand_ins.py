"""Auto-pay legs as stand-ins (auto-pay spec §4.5): both legs are stand-ins on their own side, the sweep retires a
pair whose owner twin has arrived before matching, a leg nothing covers is an `orphaned_stand_in` hint carrying
`auto_pay_id`. Fictional amounts only."""
import logging
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text

from app.models import (AccountStatement, LedgerEntry, ReconciliationAction, ReconciliationAudit, ReconciliationCase,
                        StatementAutoPay, StatementCoverage)
from app.services import auto_pay_service as aps
from app.services import policy_service, settings_service, stand_ins, transfer_service
from app.services import reconciliation_actions as registry
from app.services import reconciliation_service as rec
from app.services.errors import CodedConflictError
from tests.integration.statement_fixtures import (  # noqa: F401  (run is a fixture)
    OCT, SEP, _active, _card, _cases, _line, _submit, _transfer, run,
)
from tests.integration.test_reconciliation_policy import POLICY_EVENTS, BACKFILL_ON, _actions, _proposals, _set_policy

DAY = date(2026, 9, 3)
DUE = date(2026, 10, 24)  # Sat; Mon 10-26 is the 光復節 make-up holiday
PAY = date(2026, 10, 27)
CARD_POSTED = date(2026, 10, 28)  # the card leg's posting day (next business day)


def _world(seed, db, run):
    bank = seed.account("薪轉", statement_live_from=date(2026, 9, 1))
    card = _card(seed, db, "卡", auto_pay_account_id=bank.id)
    stmt, _, _ = _submit(db, run, card, [_line(1, DAY, "全聯", 580)], period=SEP, due_date=DUE)
    settings_service.update_reconciliation_settings(db, {"auto_pay": {"enabled": True, "enabled_from": "2026-09-01"}})
    db.commit()
    return bank, card, stmt


def _decide(db, stmt):
    settings = settings_service.read_auto_pay_settings(db)
    (cand,) = [c for c in aps.candidates(db, PAY, settings) if c.statement_id == stmt.id]
    out = aps.decide(db, cand, PAY, settings)
    db.commit()
    return out


def _booked(seed, db, run, today):
    """bank, card, the September card statement (due 10-24, pay 10-27) and its booked row via `decide`."""
    today(date(2026, 10, 27))
    bank, card, stmt = _world(seed, db, run)
    assert _decide(db, stmt)["decision"] == "booked"
    row = db.query(StatementAutoPay).filter_by(statement_id=stmt.id).one()
    out_leg, in_leg = transfer_service.transfer_legs(db, row.transfer_group_id)
    assert (out_leg.account_id, in_leg.account_id, in_leg.posted_date) == (bank.id, card.id, CARD_POSTED)
    return SimpleNamespace(bank=bank, card=card, stmt=stmt, row=row, row_id=row.id, out_id=out_leg.id,
                           in_id=in_leg.id)


def _row(db, w):
    db.expire_all()
    return db.get(StatementAutoPay, w.row_id)


def _assert_retired(db, w):
    row = _row(db, w)
    assert (row.status, row.reason) == ("retired", "twin")
    assert db.get(LedgerEntry, w.out_id) is None and db.get(LedgerEntry, w.in_id) is None


def _bank_statement(db, run, w):
    """The October bank statement printing the −580 debit; the hook's reconcile lets the out leg claim it."""
    stmt, (line,), _ = _submit(db, run, w.bank, [_line(1, PAY, "卡費", -580, "withdrawal")], kind="bank", period=OCT)
    (held,) = _active(db, stmt.id)
    assert (held.line_id, held.entry_id) == (line.id, w.out_id)
    return stmt, line


def _orphan_cases(db):
    db.expire_all()
    return [c for c in db.query(ReconciliationCase).filter(ReconciliationCase.status.in_(("open", "proposed")))
            if (c.context or {}).get("hint") == "orphaned_stand_in"]


def _retired_audits(db, w):
    db.expire_all()
    return db.query(ReconciliationAudit).filter_by(statement_id=w.stmt.id, event="auto_pay_retired").all()


def test_both_legs_are_stand_ins_on_their_own_side(seed, db_session, run, today):
    db = db_session
    w = _booked(seed, db, run, today)
    assert stand_ins.auto_pay_legs(db, [w.card.id]) == {w.in_id: w.row_id}
    assert stand_ins.auto_pay_legs(db, [w.bank.id]) == {w.out_id: w.row_id}
    assert stand_ins.auto_pay_legs(db, [w.bank.id, w.card.id]) == {w.out_id: w.row_id, w.in_id: w.row_id}
    assert stand_ins.stand_in_entry_ids(db, [w.card.id]) == {w.in_id}
    assert stand_ins.stand_in_entry_ids(db, [w.bank.id]) == {w.out_id}
    # only booked rows: a retired row's legs are no stand-ins (they are deleted anyway; here the status alone flips)
    w.row.status = "owner_edited"
    db.commit()
    assert stand_ins.auto_pay_legs(db, [w.bank.id, w.card.id]) == {}
    assert stand_ins.stand_in_entry_ids(db, [w.card.id]) == set()


@pytest.mark.parametrize("line_day, owner_day", [(date(2026, 10, 28), date(2026, 10, 24)),
                                                 (date(2026, 10, 28), date(2026, 10, 23)),
                                                 (date(2026, 11, 1), date(2026, 10, 20))])
def test_owner_entry_beside_the_leg_wins_in_the_sweep(line_day, owner_day, seed, db_session, run, today):
    db = db_session
    w = _booked(seed, db, run, today)
    owner_out, owner_in = _transfer(seed, w.bank, w.card, 580, owner_day)
    db.commit()
    owner_in_id = owner_in.id
    card_stmt, (line,), _ = _submit(db, run, w.card, [_line(1, line_day, "繳款", -580, "payment")], period=OCT,
                                    opening=580)
    rec.reconcile(db, card_stmt.id)
    db.commit()
    _assert_retired(db, w)
    (case,) = [c for c in _cases(db, card_stmt.id, "open") if c.line_id == line.id]
    assert case.kind in ("amount_delta", "transfer_delta")
    assert owner_in_id in {c.get("entry_id") for c in case.candidates}
    legs = {w.out_id, w.in_id}
    db.expire_all()
    for cov in db.query(StatementCoverage).all():
        assert cov.entry_id not in legs and (cov.snapshot or {}).get("id") not in legs
    for c in db.query(ReconciliationCase).all():
        assert c.entry_id not in legs
        assert not legs & {x.get("entry_id") for x in c.candidates or [] if isinstance(x, dict)}


def test_owner_first_then_job_skips_twin(seed, db_session, run, today):
    db = db_session
    today(date(2026, 10, 27))
    bank, card, stmt = _world(seed, db, run)
    _transfer(seed, bank, card, 580, PAY)
    db.commit()
    out = _decide(db, stmt)
    assert (out["decision"], out["reason"]) == ("skipped", "twin")
    row = db.query(StatementAutoPay).filter_by(statement_id=stmt.id).one()
    assert (row.status, row.out_entry_id, row.in_entry_id) == ("skipped", None, None)
    assert db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.source == "statement")) == 0


def test_owner_after_the_bank_leg_holds_a_line(seed, db_session, run, today):
    db = db_session
    w = _booked(seed, db, run, today)
    bank_stmt, line = _bank_statement(db, run, w)
    owner_out, _ = _transfer(seed, w.bank, w.card, 580, PAY)
    db.commit()
    owner_out_id = owner_out.id
    rec.reconcile(db, bank_stmt.id)
    db.commit()
    _assert_retired(db, w)
    (claim,) = _active(db, bank_stmt.id)
    assert (claim.line_id, claim.entry_id) == (line.id, owner_out_id)
    assert _cases(db, bank_stmt.id, "open") == []
    assert len(_retired_audits(db, w)) == 1


def test_orphan_only_when_covered_period_exists_without_line_or_twin(seed, db_session, run, today):
    db = db_session
    w = _booked(seed, db, run, today)
    rec.reconcile(db, w.stmt.id)  # the September statement's period does not contain the pay date
    db.commit()
    assert _orphan_cases(db) == []
    card_stmt, _, _ = _submit(db, run, w.card, [_line(1, date(2026, 10, 5), "全聯", 300)], period=OCT, opening=580)
    rec.reconcile(db, card_stmt.id)
    db.commit()
    (orphan,) = _orphan_cases(db)
    assert (orphan.statement_id, orphan.kind, orphan.entry_id, orphan.event_id, orphan.line_id) == (
        card_stmt.id, "entry_unmatched", w.in_id, None, None)
    assert orphan.context == {"hint": "orphaned_stand_in", "backfill_action_id": None, "claimant_entry_id": None,
                              "auto_pay_id": w.row_id}
    # no plain entry_unmatched case for the leg beside the hinted one
    assert [c.id for c in _cases(db, card_stmt.id, "open") if c.entry_id == w.in_id] == [orphan.id]
    assert stand_ins.orphans(db, db.get(AccountStatement, card_stmt.id), [w.card.id]) == [
        {"entry_id": w.in_id, "event_id": None, "line_id": None, "backfill_action_id": None, "auto_pay_id": w.row_id,
         "claimant_entry_id": None}]
    # the owner's own payment arrives: the next reconcile retires the pair and the orphan case goes
    _transfer(seed, w.bank, w.card, 580, PAY)
    db.commit()
    rec.reconcile(db, card_stmt.id)
    db.commit()
    _assert_retired(db, w)
    assert _orphan_cases(db) == []


def test_policy_never_backfills_a_line_an_auto_pay_leg_holds(seed, db_session, run, today, dirty_on):
    db = db_session
    w = _booked(seed, db, run, today)
    bank_stmt, line = _bank_statement(db, run, w)
    _set_policy(db, {**BACKFILL_ON, "backfill_grace_days": 0})
    policy_service.run_policy(db, bank_stmt.id)
    db.expire_all()
    assert db.query(ReconciliationAction).filter_by(event_id=line.event_id).all() == []
    assert db.query(LedgerEntry).filter_by(account_id=w.bank.id, kind="expense").all() == []
    (held,) = _active(db, bank_stmt.id)
    assert (held.line_id, held.entry_id) == (line.id, w.out_id)


def test_sweep_retire_skips_a_pair_locked_elsewhere(pg_engine, seed, db_session, run, today, caplog, monkeypatch):
    db = db_session
    monkeypatch.setattr(aps.logger, "disabled", False)  # alembic's fileConfig disables loggers created before it
    w = _booked(seed, db, run, today)
    bank_stmt, line = _bank_statement(db, run, w)
    _transfer(seed, w.bank, w.card, 580, PAY)
    db.commit()
    holder = pg_engine.connect()
    try:
        holder.begin()
        holder.execute(text("SELECT id FROM ledger_entry WHERE id = :id FOR UPDATE"), {"id": w.in_id})
        with caplog.at_level(logging.INFO, logger=aps.__name__):
            assert aps.retire_twins_for_statement(db, db.get(AccountStatement, bank_stmt.id), [w.bank.id]) == []
        busy = [r for r in caplog.records if r.getMessage() == "auto_pay.sweep_retire_busy"]
        assert [(r.auto_pay_id, r.statement_id) for r in busy] == [(w.row_id, bank_stmt.id)]
        assert _row(db, w).status == "booked"
        # the sweep yields on the busy pair; the reconcile then meets the same held leg among its candidates and
        # yields as it always does (`reconcile_busy`), never with the pair's own `auto_pay_busy`
        with pytest.raises(CodedConflictError) as caught:
            rec.reconcile(db, bank_stmt.id)
        assert caught.value.code == rec.RECONCILE_BUSY
        db.rollback()
    finally:
        holder.commit()
        holder.close()
    assert _row(db, w).status == "booked"
    assert db.get(LedgerEntry, w.out_id) is not None and db.get(LedgerEntry, w.in_id) is not None
    rec.reconcile(db, bank_stmt.id)
    db.commit()
    _assert_retired(db, w)
    assert len(_retired_audits(db, w)) == 1


# --- fix round 1 ----------------------------------------------------------------------------------------------------


def _card_orphan(seed, db, run, today):
    """The booked pair and the October card statement (no payment line) holding its leg's orphaned_stand_in case."""
    w = _booked(seed, db, run, today)
    card_stmt, _, _ = _submit(db, run, w.card, [_line(1, date(2026, 10, 5), "全聯", 300)], period=OCT, opening=580)
    (orphan,) = _orphan_cases(db)
    return w, card_stmt, orphan


@pytest.mark.parametrize("hint", ["shadowed_by_stand_in", "orphaned_stand_in"])
def test_an_auto_pay_hint_never_offers_or_builds_a_replacement(hint):
    case = ReconciliationCase(id=1, statement_id=1, kind="entry_unmatched", status="open", entry_id=2,
                              context={"hint": hint, "backfill_action_id": None, "auto_pay_id": 3})
    assert "replace_stand_in" not in registry.allowed(case)
    assert policy_service._replace_candidate(None, case, None) is None
    case.context = {**case.context, "backfill_action_id": 4}  # a backfilled stand-in's hint keeps both
    assert "replace_stand_in" in registry.allowed(case)
    backfilled = SimpleNamespace(get=lambda model, ident: SimpleNamespace(action="backfill_entry"))
    assert policy_service._replace_candidate(backfilled, case, None).action == "replace_stand_in"


def test_policy_leaves_an_auto_pay_orphan_alone(seed, db_session, run, today, dirty_on):
    db = db_session
    w, card_stmt, orphan = _card_orphan(seed, db, run, today)
    assert "replace_stand_in" not in registry.allowed(orphan)
    _set_policy(db, {"enabled": True, "actions": {"replace_stand_in": {"enabled": True}}})
    result = policy_service.run_policy(db, card_stmt.id)
    assert (result.applied, result.proposed, result.skipped) == ([], [], {})
    db.expire_all()
    assert db.query(ReconciliationAudit).filter(ReconciliationAudit.statement_id == card_stmt.id,
                                                ReconciliationAudit.event.in_(POLICY_EVENTS)).all() == []
    assert _actions(db) == [] and _proposals(db) == []
    assert db.get(LedgerEntry, w.in_id) is not None and _orphan_cases(db)[0].id == orphan.id


def test_a_card_leg_posted_after_the_period_end_is_not_an_orphan(seed, db_session, run, today):
    db = db_session
    w = _booked(seed, db, run, today)  # the card leg: entry_date 10-27 (the pay date), posted 10-28
    card_stmt, _, _ = _submit(db, run, w.card, [_line(1, date(2026, 10, 5), "全聯", 300)],
                              period=(date(2026, 10, 1), PAY), opening=580)
    rec.reconcile(db, card_stmt.id)
    db.commit()
    assert _orphan_cases(db) == []
    assert [c for c in _cases(db, card_stmt.id, "open") if c.entry_id == w.in_id] == []


def test_a_deferred_bank_leg_is_not_an_orphan(seed, db_session, run, today):
    db = db_session
    w = _booked(seed, db, run, today)  # the bank leg posted 10-27, within the last 2 days of a period ending 10-28
    bank_stmt, _, _ = _submit(db, run, w.bank, [_line(1, date(2026, 10, 5), "存入", 100, "deposit")], kind="bank",
                              period=(date(2026, 10, 1), date(2026, 10, 28)))
    rec.reconcile(db, bank_stmt.id)
    db.commit()
    assert _orphan_cases(db) == []
    assert [c for c in _cases(db, bank_stmt.id, "open") if c.entry_id == w.out_id] == []



# --- Task 8: the next statements claim the legs (matcher rule auto_pay) ---------------------------------------------

LATE = (date(2026, 10, 10), date(2026, 11, 30))  # holds every line day below; ends on the card's closing day


def _claim_rules(db, stmt_id):
    return [(c.line_id, c.entry_id, c.match_rule) for c in _active(db, stmt_id)]


@pytest.mark.parametrize("line_day, rule", [(date(2026, 10, 28), "payment"),   # distance 0: the exact path
                                            (date(2026, 11, 1), "auto_pay"),   # 4: beyond the exact window
                                            (date(2026, 11, 8), "auto_pay")])  # 11
def test_next_statements_claim_the_legs(line_day, rule, seed, db_session, run, today):
    db = db_session
    w = _booked(seed, db, run, today)
    card_stmt, (line,), _ = _submit(db, run, w.card, [_line(1, line_day, "繳款", -580, "payment")], period=LATE,
                                    opening=580)
    rec.reconcile(db, card_stmt.id)
    db.commit()
    assert _claim_rules(db, card_stmt.id) == [(line.id, w.in_id, rule)]
    assert [c for c in _cases(db, card_stmt.id, "open") if c.line_id == line.id or c.entry_id == w.in_id] == []
    assert _row(db, w).status == "booked"


@pytest.mark.parametrize("line_kind, line_day, rule", [("withdrawal", PAY, "exact"),
                                                       ("payment", PAY, "payment"),
                                                       ("transfer_out", PAY, "transfer"),
                                                       ("withdrawal", date(2026, 11, 2), "auto_pay")])  # distance 6
def test_bank_statement_lines_claim_the_out_leg(line_kind, line_day, rule, seed, db_session, run, today):
    db = db_session
    w = _booked(seed, db, run, today)
    bank_stmt, (line,), _ = _submit(db, run, w.bank, [_line(1, line_day, "卡費", -580, line_kind)], kind="bank",
                                    period=LATE)
    rec.reconcile(db, bank_stmt.id)
    db.commit()
    assert _claim_rules(db, bank_stmt.id) == [(line.id, w.out_id, rule)]
    assert [c for c in _cases(db, bank_stmt.id, "open") if c.line_id == line.id or c.entry_id == w.out_id] == []
