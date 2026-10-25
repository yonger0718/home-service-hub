"""R6c `adjust_transfer` (spec §5.1, §5.2): the owner rewrites a transfer to the statement figures across both
statements — both legs' amounts (and dates from the lines), a claim on this line and on the complementary peer line,
the peer line's case resolved by the same action row, four cases closed per transfer; revert restores both legs and
reopens both lines. Synthetic data only."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import update

from app.models import (
    LedgerEntry, ReconciliationAction, ReconciliationCase, ReconciliationProposal, StatementCoverage,
)
from app.schemas.reconciliation import ProposalIn
from app.services import coverage_service, proposal_service, stand_ins
from app.services import reconciliation_actions
from tests.integration.statement_fixtures import (  # noqa: F401  (run is a fixture)
    _active, _card, _cases, _line, _reconcile, _submit, _transfer, run,
)
from tests.integration.test_reconciliation_actions_ledger import (
    _ambiguous_case, _apply, _code, _delta_case, _fresh, _hand_case, _refuse, _revert,
)
from tests.integration.test_reconciliation_policy import _audit


def _bank(seed, db, name="銀行", **extra):
    bank = seed.account(name, statement_live_from=date(2026, 9, 1), **extra)
    db.commit()
    return bank


BANK_LINE = _line(1, date(2026, 9, 5), "信用卡繳款", "-20000", kind="payment")
CARD_LINE = _line(1, date(2026, 9, 5), "繳款", "-20000", kind="payment")


def _make_pair(seed, run, db, *, bank_lines=(BANK_LINE,), card_lines=(CARD_LINE,)):
    bank = _bank(seed, db)
    card = _card(seed, db, name="卡")
    out_leg, in_leg = _transfer(seed, bank, card, "19500", date(2026, 9, 4))
    db.commit()
    bstmt, blines, _ = _submit(db, run, bank, list(bank_lines), kind="bank")
    cstmt, clines, _ = _submit(db, run, card, list(card_lines))
    return SimpleNamespace(bank=bank, card=card, out_leg=out_leg, in_leg=in_leg, bstmt=bstmt, cstmt=cstmt,
                           bline=blines[0], cline=clines[0], blines=blines, clines=clines)


@pytest.fixture
def pair(dirty_on, seed, run, db_session):
    """Bank (lower id) and card, one live September statement each; the owner's transfer 19500 on 9/4 vs lines 20000
    on 9/5 (bank payment −20000, card payment +20000)."""
    return _make_pair(seed, run, db_session)


def _delta(db, stmt):
    """The transfer_delta case of a statement (None when it has none open)."""
    return next((c for c in _cases(db, stmt.id, "open")
                 if c.kind == "amount_delta" and (c.context or {}).get("hint") == "transfer_delta"), None)


def _adjust(pair) -> dict:
    return {"entry_id": pair.in_leg.id, "line_id": pair.cline.id}


def _transfer_case(db, stmt, line, leg) -> ReconciliationCase:
    """An amount_delta transfer_delta case on `line` naming `leg` (what the matcher writes)."""
    residual = str(Decimal(line.flow_amount) - Decimal(leg.amount))
    return _hand_case(db, stmt, line, "amount_delta", entry_id=leg.id,
                      candidates=[{"entry_id": leg.id, "reason": "transfer", "residual": residual, "date_distance": 1}],
                      context={"hint": "transfer_delta", "entry_id": leg.id, "residual": residual,
                               "date_distance": 1, "fee_matches": False})


def test_fixture_opens_transfer_delta_on_both_statements_and_entry_unmatched_on_both_legs(pair, db_session):
    assert _delta(db_session, pair.bstmt).entry_id == pair.out_leg.id
    assert _delta(db_session, pair.cstmt).entry_id == pair.in_leg.id
    assert {c.entry_id for c in _cases(db_session, pair.bstmt.id, "open") if c.kind == "entry_unmatched"} == {pair.out_leg.id}
    allowed = reconciliation_actions.allowed(_delta(db_session, pair.cstmt))
    # allowed() ignores protection; adjust_amount refuses `protected` at apply (test below); the inbox hides its
    # button via adjustable()
    assert "adjust_transfer" in allowed and "adjust_amount" in allowed


def test_adjust_transfer_rewrites_both_legs_claims_both_lines_and_closes_four_cases(pair, db_session):
    case = _delta(db_session, pair.cstmt)
    row = _apply(db_session, case, "adjust_transfer", _adjust(pair))
    out_leg, in_leg = _fresh(db_session, LedgerEntry, pair.out_leg.id), _fresh(db_session, LedgerEntry, pair.in_leg.id)
    assert (out_leg.amount, in_leg.amount) == (Decimal("-20000"), Decimal("20000"))
    assert out_leg.posted_date == in_leg.posted_date == date(2026, 9, 5)
    assert out_leg.source == in_leg.source == "manual"  # seed default, untouched by the action
    assert "對帳調整" in out_leg.description and "對帳調整" in in_leg.description
    assert {r.line_id for r in _active(db_session, pair.cstmt.id)} == {pair.cline.id}
    assert {r.line_id for r in _active(db_session, pair.bstmt.id)} == {pair.bline.id}
    assert all(r.match_rule == "adjust_transfer" and r.match_kind == "manual"
               for r in _active(db_session, pair.cstmt.id) + _active(db_session, pair.bstmt.id))
    peer = _fresh(db_session, ReconciliationCase, row.inverse["peer_case_id"])
    assert (peer.status, peer.resolved_action_id, peer.note) == ("resolved", row.id, f"adjust_transfer from case {case.id}")
    assert peer.explanation is None
    assert _cases(db_session, pair.bstmt.id, "open") == [] and _cases(db_session, pair.cstmt.id, "open") == []
    assert sorted(row.inverse) == ["peer_case_id", "peer_event_id", "peer_line_id", "restore", "statement_ids"]
    assert row.inverse["statement_ids"] == [pair.bstmt.id]
    assert row.inverse["peer_line_id"] == pair.bline.id and row.inverse["peer_event_id"] == pair.bline.event_id
    assert row.inverse["restore"][str(pair.out_leg.id)] == {"amount": "-19500.0000", "entry_date": "2026-09-04",
                                                           "posted_date": "2026-09-04", "description": None}
    for stmt in (pair.bstmt, pair.cstmt):  # a later reconcile (its sweep sees the legs' dirty events) keeps it all
        _reconcile(db_session, stmt.id)
    assert {r.line_id for r in _active(db_session, pair.bstmt.id)} == {pair.bline.id}
    assert _cases(db_session, pair.bstmt.id, "open") == [] and _cases(db_session, pair.cstmt.id, "open") == []


def test_adjust_transfer_revert_restores_both_legs_and_reopens_both_lines(pair, db_session):
    case = _delta(db_session, pair.cstmt)
    row = _apply(db_session, case, "adjust_transfer", _adjust(pair))
    _revert(db_session, row.id)
    out_leg, in_leg = _fresh(db_session, LedgerEntry, pair.out_leg.id), _fresh(db_session, LedgerEntry, pair.in_leg.id)
    assert (out_leg.amount, out_leg.posted_date, out_leg.description) == (Decimal("-19500"), date(2026, 9, 4), None)
    assert (in_leg.amount, in_leg.posted_date, in_leg.description) == (Decimal("19500"), date(2026, 9, 4), None)
    assert _active(db_session, pair.cstmt.id) == [] and _active(db_session, pair.bstmt.id) == []
    assert _fresh(db_session, ReconciliationCase, case.id).status == "open"
    assert _delta(db_session, pair.bstmt) is not None  # the peer line has an open case again


def test_adjust_transfer_supersedes_a_pending_peer_proposal(pair, db_session):
    peer = _delta(db_session, pair.bstmt)
    prop = proposal_service.create_proposal(
        db_session, case_id=peer.id,
        payload=ProposalIn(action="match", params={"line_id": pair.bline.id, "entry_id": pair.out_leg.id},
                           rationale="the bank line pays the card", confidence=Decimal("0.5")),
        author="hermes")
    db_session.commit()
    _apply(db_session, _delta(db_session, pair.cstmt), "adjust_transfer", _adjust(pair))
    assert _fresh(db_session, ReconciliationProposal, prop.id).status == "superseded"
    assert any(a.detail == {"proposal_id": prop.id, "by": "peer_resolved"} for a in _audit(db_session, "proposal_superseded"))


def test_adjust_transfer_without_a_peer_line_rewrites_amounts_and_keeps_the_peer_dates(dirty_on, seed, run, db_session):
    bank = _bank(seed, db_session)  # bank not live: no peer statement
    card = _card(seed, db_session, name="卡")
    bank.statement_live_from = None
    db_session.commit()
    out_leg, in_leg = _transfer(seed, bank, card, "19500", date(2026, 9, 4))
    db_session.commit()
    cstmt, clines, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 5), "繳款", "-20000", kind="payment")])
    row = _apply(db_session, _delta(db_session, cstmt), "adjust_transfer", {"entry_id": in_leg.id, "line_id": clines[0].id})
    out_leg = _fresh(db_session, LedgerEntry, out_leg.id)
    assert out_leg.amount == Decimal("-20000") and out_leg.posted_date == date(2026, 9, 4)
    assert _fresh(db_session, LedgerEntry, in_leg.id).posted_date == date(2026, 9, 5)
    assert row.inverse["peer_line_id"] is None and row.inverse["statement_ids"] == [] and len(_active(db_session, cstmt.id)) == 1
    assert row.inverse["peer_case_id"] is None and row.inverse["peer_event_id"] is None


def test_adjust_transfer_finds_a_complementary_line_on_the_next_live_period(dirty_on, seed, run, db_session):
    # card line 9/28 (September statement); the owner's transfer 9/27; the bank prints the payment on 10/2 — its
    # October statement, 4 days away (< drift_window_days 15)
    bank = _bank(seed, db_session)
    card = _card(seed, db_session, name="卡")
    out_leg, in_leg = _transfer(seed, bank, card, "19500", date(2026, 9, 27))
    db_session.commit()
    bsep, _, _ = _submit(db_session, run, bank, [], kind="bank")  # September: no complementary line
    boct, blines, _ = _submit(db_session, run, bank, [_line(1, date(2026, 10, 2), "信用卡繳款", "-20000", kind="payment")],
                              kind="bank", period=(date(2026, 10, 1), date(2026, 10, 31)))
    cstmt, clines, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 28), "繳款", "-20000", kind="payment")])
    row = _apply(db_session, _delta(db_session, cstmt), "adjust_transfer", {"entry_id": in_leg.id, "line_id": clines[0].id})
    # the leg-date statement and the line statement
    assert row.inverse["peer_line_id"] == blines[0].id and sorted(row.inverse["statement_ids"]) == sorted({bsep.id, boct.id})
    assert _fresh(db_session, LedgerEntry, out_leg.id).posted_date == date(2026, 10, 2)
    assert {r.line_id for r in _active(db_session, boct.id)} == {blines[0].id}


def test_adjust_transfer_finds_a_bank_withdrawal_peer_line(dirty_on, seed, run, db_session):
    # the bank prints the card payment as a withdrawal (not a payment line)
    bank = _bank(seed, db_session)
    card = _card(seed, db_session, name="卡")
    out_leg, in_leg = _transfer(seed, bank, card, "19500", date(2026, 9, 4))
    db_session.commit()
    bstmt, blines, _ = _submit(db_session, run, bank, [_line(1, date(2026, 9, 5), "提款", "-20000", kind="withdrawal")],
                               kind="bank")
    cstmt, clines, _ = _submit(db_session, run, card, [_line(1, date(2026, 9, 5), "繳款", "-20000", kind="payment")])
    bank_case = next(c for c in _cases(db_session, bstmt.id, "open") if c.line_id == blines[0].id)
    row = _apply(db_session, _delta(db_session, cstmt), "adjust_transfer", {"entry_id": in_leg.id, "line_id": clines[0].id})
    assert {r.line_id for r in _active(db_session, bstmt.id)} == {blines[0].id}
    assert {r.line_id for r in _active(db_session, cstmt.id)} == {clines[0].id}
    resolved = _fresh(db_session, ReconciliationCase, bank_case.id)
    assert (resolved.status, resolved.resolved_action_id, row.inverse["peer_case_id"]) == ("resolved", row.id, bank_case.id)


def _refusal_setup(variant, pair, seed, run, db):
    """Per variant: the case and params to apply (default: the card's transfer_delta case on the in leg)."""
    case, params = None, None
    if variant == "peer_claimed":  # the bank printed the ledger figure too: its exact line claims the out leg
        assert pair.out_leg.id in coverage_service.claimed_entry_ids(db)
    elif variant == "child":
        seed.entry(pair.card, "-5", kind="fee", parent_entry_id=pair.in_leg.id)
        db.commit()
    elif variant == "cross_currency":  # the peer leg in another currency (the in leg stays in the statement's)
        db.execute(update(LedgerEntry).where(LedgerEntry.id == pair.out_leg.id).values(currency="JPY"))
        db.commit()
    elif variant == "card_peer":  # the payment line's peer leg sits on a card
        pair.bank.is_credit = True
        db.commit()
    elif variant == "wrong_sign":  # a card-side transfer_out leg named on the card's +20000 payment line
        card_out, _ = _transfer(seed, pair.card, pair.bank, "20000", date(2026, 9, 5))
        db.commit()
        case = _transfer_case(db, pair.cstmt, pair.cline, card_out)
        params = {"entry_id": card_out.id, "line_id": pair.cline.id}
    return case or _delta(db, pair.cstmt), params or _adjust(pair)


REFUSAL_BANK_LINES = {
    "peer_claimed": (BANK_LINE, _line(2, date(2026, 9, 4), "轉帳", "-19500", kind="payment")),
    "two_peer_lines": (BANK_LINE, _line(2, date(2026, 9, 6), "信用卡繳款", "-20000", kind="payment")),  # same revision
}


@pytest.mark.parametrize("variant,code", [("peer_claimed", "peer_leg_claimed"), ("two_peer_lines", "peer_ambiguous"),
                                          ("child", "not_standalone"), ("cross_currency", "cross_currency"),
                                          ("card_peer", "peer_is_card"), ("wrong_sign", "kind_sign")])
def test_adjust_transfer_refusals(variant, code, dirty_on, seed, run, db_session):
    pair = _make_pair(seed, run, db_session, bank_lines=REFUSAL_BANK_LINES.get(variant, (BANK_LINE,)))
    case, params = _refusal_setup(variant, pair, seed, run, db_session)
    exc = _refuse(db_session, lambda: _apply(db_session, case, "adjust_transfer", params))
    assert _code(exc) == code
    assert _fresh(db_session, LedgerEntry, pair.in_leg.id).amount == Decimal("19500")
    assert db_session.query(ReconciliationAction).filter_by(action="adjust_transfer").count() == 0


def test_adjust_transfer_candidate_changed_when_the_peer_line_is_claimed_between_discovery_and_the_locks(
        pair, db_session, monkeypatch):
    impl = reconciliation_actions.ACTIONS["adjust_transfer"]
    original = impl.lock_targets
    claimed = []

    def lock_then_claim(ctx, params):
        targets = original(ctx, params)
        if not claimed:  # a hand claim on the peer line (amount conservation is irrelevant to the lock test)
            leg = ctx.db.get(LedgerEntry, pair.out_leg.id)
            ctx.db.add(StatementCoverage(statement_id=pair.bstmt.id, event_id=pair.bline.event_id, line_id=pair.bline.id,
                                         entry_id=leg.id, role="principal", snapshot=coverage_service.snapshot_entry(leg),
                                         match_kind="manual", match_rule="match", status="active"))
            ctx.db.flush()
            claimed.append(True)
        return targets

    monkeypatch.setattr(impl, "lock_targets", lock_then_claim)
    exc = _refuse(db_session, lambda: _apply(db_session, _delta(db_session, pair.cstmt), "adjust_transfer", _adjust(pair)))
    assert _code(exc) == "candidate_changed" and claimed


def test_second_line_on_the_same_leg_is_refused_entry_claimed(dirty_on, seed, run, db_session):
    pair = _make_pair(seed, run, db_session,
                      card_lines=(CARD_LINE, _line(2, date(2026, 9, 6), "繳款", "-20100", kind="payment")))
    cstmt, clines = pair.cstmt, pair.clines
    deltas = [c for c in _cases(db_session, cstmt.id, "open") if (c.context or {}).get("hint") == "transfer_delta"]
    assert sorted(c.entry_id for c in deltas) == [pair.in_leg.id, pair.in_leg.id]
    first = next(c for c in deltas if c.line_id == clines[0].id)
    _apply(db_session, first, "adjust_transfer", {"entry_id": pair.in_leg.id, "line_id": clines[0].id})
    # the apply superseded the second line's matcher case (it named the now-claimed leg): ask again on that line
    second = _transfer_case(db_session, cstmt, clines[1], _fresh(db_session, LedgerEntry, pair.in_leg.id))
    exc = _refuse(db_session, lambda: _apply(db_session, second, "adjust_transfer",
                                             {"entry_id": pair.in_leg.id, "line_id": clines[1].id}))
    assert _code(exc) == "entry_claimed"


def test_adjust_amount_still_refuses_a_leg_and_the_inbox_hides_accept(pair, db_session):
    exc = _refuse(db_session, lambda: _apply(db_session, _delta(db_session, pair.cstmt), "adjust_amount", _adjust(pair)))
    assert _code(exc) == "protected"


def test_allowed_lists_adjust_transfer_on_near_and_ambiguous_leg_cases_only(pair, seed, db_session):
    near = _delta_case(db_session, pair.cstmt, pair.cline, pair.in_leg, fee_matches=False)  # hand case without the hint
    assert "adjust_transfer" in reconciliation_actions.allowed(near)
    other = seed.entry(pair.card, "20050", kind="income", day=date(2026, 9, 5))
    db_session.commit()
    amb = _ambiguous_case(db_session, pair.cstmt, pair.cline, [pair.in_leg, other])
    assert "adjust_transfer" in reconciliation_actions.allowed(amb)
    plain = _delta_case(db_session, pair.cstmt, pair.cline, other, fee_matches=False)
    assert "adjust_transfer" not in reconciliation_actions.allowed(plain)
    assert "adjust_transfer" not in reconciliation_actions.allowed(_ambiguous_case(db_session, pair.cstmt, pair.cline, [other]))


def test_pair_transfer_block_reads_applied_and_owner_deleted_pairings(pair, db_session):
    case, peer_event = _delta(db_session, pair.cstmt), pair.bline.event_id
    assert not stand_ins.pair_transfer_block(db_session, [case.event_id, peer_event])
    row = ReconciliationAction(idempotency_key="t-pair-1", event_id=case.event_id, effect_slot="create", case_id=case.id,
                               action="pair_transfer", params={}, actor="owner", status="applied",
                               inverse={"peer_event_id": peer_event})
    db_session.add(row)
    db_session.commit()
    assert stand_ins.pair_transfer_block(db_session, [peer_event]) and stand_ins.pair_transfer_block(db_session, [case.event_id])
    row.status = "reverted"  # an ordinary engine revert: no block
    db_session.commit()
    assert not stand_ins.pair_transfer_block(db_session, [case.event_id, peer_event])
    row.inverse = {"peer_event_id": peer_event, "deleted_by_event": 7}
    db_session.commit()
    assert stand_ins.pair_transfer_block(db_session, [peer_event])
    db_session.add(ReconciliationAction(idempotency_key="t-pair-2", event_id=case.event_id, effect_slot="create",
                                        case_id=case.id, action="backfill_entry", params={}, actor="owner",
                                        status="applied"))
    db_session.commit()
    assert not stand_ins.pair_transfer_block(db_session, [case.event_id])  # a later creation on that event
    assert stand_ins.pair_transfer_block(db_session, [peer_event])  # none on the peer event
