"""R6c `adjust_transfer` (spec §5.1, §5.2): the owner rewrites a transfer to the statement figures across both
statements — both legs' amounts (and dates from the lines), a claim on this line and on the complementary peer line,
the peer line's case resolved by the same action row, four cases closed per transfer; revert restores both legs and
reopens both lines. `pair_transfer` (spec §5.1, §5.3): two complementary needs_peer lines and no owner transfer — one
transfer booked from the statement figures, both lines claimed, both cases closed; the transfer-twin and occupancy
guards bind the policy and its proposals, never the owner's own action. Synthetic data only."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import update

from app.models import (
    LedgerEntry, ReconciliationAction, ReconciliationCase, ReconciliationProposal, StatementCoverage,
)
from app.schemas.reconciliation import ProposalIn
from app.services import coverage_service, proposal_service, settings_service, stand_ins
from app.services import entry_write_service as ews
from app.services import reconciliation_actions
from app.services import reconciliation_service as rec
from app.services.errors import CodedConflictError
from app.services.reconciliation_actions import transfer_actions
from app.services.reconciliation_actions.base import POLICY
from tests.helpers import set_dirty
from tests.integration.statement_fixtures import (  # noqa: F401  (run is a fixture)
    _active, _card, _cases, _line, _reconcile, _submit, _transfer, run,
)
from tests.integration.test_reconciliation_actions_ledger import (
    _ambiguous_case, _apply, _code, _delta_case, _fresh, _hand_case, _refuse, _revert,
)
from tests.integration.test_reconciliation_policy import _audit, _sweep


def _bank(seed, db, name="銀行", **extra):
    bank = seed.account(name, statement_live_from=date(2026, 9, 1), **extra)
    db.commit()
    return bank


BANK_LINE = _line(1, date(2026, 9, 5), "信用卡繳款", "-20000", kind="payment")
CARD_LINE = _line(1, date(2026, 9, 5), "繳款", "-20000", kind="payment")


def _make_pair(seed, run, db, *, bank_lines=(BANK_LINE,), card_lines=(CARD_LINE,), owner_transfer=True):
    bank = _bank(seed, db)
    card = _card(seed, db, name="卡")
    out_leg, in_leg = _transfer(seed, bank, card, "19500", date(2026, 9, 4)) if owner_transfer else (None, None)
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


def test_adjust_transfer_applies_the_discovery_validate_before_verified_under_the_locks(pair, db_session, monkeypatch):
    """A rediscovery after `validate_before` could reach an unlocked window statement: `apply` must not run one."""
    impl = reconciliation_actions.ACTIONS["adjust_transfer"]
    original, calls = impl._discover, []

    def discover(ctx, params):  # lock_targets, then validate_before; any later call sees a different world
        calls.append(True)
        found = original(ctx, params)
        return found if len(calls) <= 2 else {**found, "peer_line_id": None, "peer_line_ids": [], "peer_statement_ids": []}

    monkeypatch.setattr(impl, "_discover", discover)
    row = _apply(db_session, _delta(db_session, pair.cstmt), "adjust_transfer", _adjust(pair))
    assert len(calls) == 2
    assert row.inverse["peer_line_id"] == pair.bline.id and row.inverse["statement_ids"] == [pair.bstmt.id]
    assert {r.line_id for r in _active(db_session, pair.bstmt.id)} == {pair.bline.id}


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


# --- pair_transfer (§5.3) -------------------------------------------------------------------------------------------

POLICY_RUN = {"actor": "policy", "label": "policy", "budget": (date(2026, 10, 1), "TWD", Decimal("1"))}


@pytest.fixture
def orphan_pair(dirty_on, seed, run, db_session):
    """As `pair`, but the owner booked no transfer: the bank's −20000 and the card's +20000 payment lines are both
    `needs_peer`."""
    return _make_pair(seed, run, db_session, owner_transfer=False)


def _needs_peer(db, stmt):
    """The open/proposed needs_peer line case of a statement (None when it has none)."""
    return next((c for c in _cases(db, stmt.id, "open", "proposed")
                 if c.kind == "line_unmatched" and (c.context or {}).get("hint") == "needs_peer"), None)


def _pair_params(line) -> dict:
    return {"peer_line_id": line.id}


def _rules(db, stmt):
    return rec.rules_for(settings_service.reconciliation_rules(db), stmt.period_end)


def _policy_proposal(db, case, line, author=POLICY):
    prop = proposal_service.create_proposal(
        db, case_id=case.id, payload=ProposalIn(action="pair_transfer", params=_pair_params(line),
                                                rationale="complementary lines", confidence=Decimal("0.9")),
        author=author)
    db.commit()
    return prop


def _legs(db, row):
    db.expire_all()
    return [db.get(LedgerEntry, i) for i in sorted(row.created_entry_ids)]


def test_pair_transfer_books_one_transfer_claims_both_lines_and_closes_both_cases(orphan_pair, db_session):
    p = orphan_pair
    case = _needs_peer(db_session, p.cstmt)
    allowed = reconciliation_actions.allowed(case)
    assert "pair_transfer" in allowed and "backfill_entry" not in allowed  # _no_peer_hint still hides the creations
    cands = reconciliation_actions.ACTIONS["pair_transfer"].peer_candidates(db_session, case)
    assert [(s.id, line.id) for s, line in cands] == [(p.bstmt.id, p.bline.id)]
    row = _apply(db_session, case, "pair_transfer", _pair_params(p.bline))
    out_leg, in_leg = _legs(db_session, row)
    assert (out_leg.account_id, out_leg.kind, out_leg.amount, out_leg.posted_date) == (
        p.bank.id, "transfer_out", Decimal("-20000"), date(2026, 9, 5))
    assert (in_leg.account_id, in_leg.kind, in_leg.amount, in_leg.posted_date) == (
        p.card.id, "transfer_in", Decimal("20000"), date(2026, 9, 5))
    assert out_leg.transfer_group_id == in_leg.transfer_group_id and out_leg.source == in_leg.source == "statement"
    assert out_leg.needs_review and in_leg.needs_review and out_leg.name == in_leg.name == "對帳配對轉帳"
    assert "2026-09-30" in out_leg.description and out_leg.category_id is None
    assert row.effect_slot == "create" and sorted(row.inverse) == [
        "created_entry_ids", "peer_case_id", "peer_event_id", "peer_line_id", "statement_ids"]
    assert row.inverse["statement_ids"] == [p.bstmt.id] and row.inverse["peer_line_id"] == p.bline.id
    assert row.inverse["peer_event_id"] == p.bline.event_id
    peer = _fresh(db_session, ReconciliationCase, row.inverse["peer_case_id"])
    assert (peer.status, peer.resolved_action_id, peer.explanation, peer.note) == (
        "resolved", row.id, None, f"pair_transfer from case {case.id}")
    assert {(r.line_id, r.entry_id, r.match_rule) for r in _active(db_session, p.cstmt.id)} == {
        (p.cline.id, in_leg.id, "pair_transfer")}
    assert {(r.line_id, r.entry_id, r.match_rule) for r in _active(db_session, p.bstmt.id)} == {
        (p.bline.id, out_leg.id, "pair_transfer")}
    assert _cases(db_session, p.bstmt.id, "open") == [] and _cases(db_session, p.cstmt.id, "open") == []
    # option A: the case statement's leg is a stand-in, the peer leg an owner entry on its statement
    assert in_leg.id in stand_ins.stand_in_entry_ids(db_session, [p.card.id])
    assert out_leg.id not in stand_ins.stand_in_entry_ids(db_session, [p.bank.id])
    for stmt in (p.bstmt, p.cstmt):  # a later reconcile keeps it all
        _reconcile(db_session, stmt.id)
    assert _cases(db_session, p.bstmt.id, "open") == [] and _cases(db_session, p.cstmt.id, "open") == []


def test_pair_transfer_from_the_bank_side_books_the_same_transfer(orphan_pair, db_session):
    p = orphan_pair
    row = _apply(db_session, _needs_peer(db_session, p.bstmt), "pair_transfer", _pair_params(p.cline))
    out_leg, in_leg = _legs(db_session, row)
    assert (out_leg.account_id, out_leg.amount, in_leg.account_id, in_leg.amount) == (
        p.bank.id, Decimal("-20000"), p.card.id, Decimal("20000"))
    assert out_leg.id in stand_ins.stand_in_entry_ids(db_session, [p.bank.id])  # now the bank is the case statement
    assert _cases(db_session, p.bstmt.id, "open") == [] and _cases(db_session, p.cstmt.id, "open") == []


def test_pair_transfer_with_different_line_dates_keeps_each_legs_own_date(dirty_on, seed, run, db_session):
    p = _make_pair(seed, run, db_session, owner_transfer=False,
                   card_lines=(_line(1, date(2026, 9, 7), "繳款", "-20000", kind="payment"),))
    row = _apply(db_session, _needs_peer(db_session, p.cstmt), "pair_transfer", _pair_params(p.bline))
    out_leg, in_leg = _legs(db_session, row)
    assert (out_leg.entry_date, out_leg.posted_date) == (date(2026, 9, 5), date(2026, 9, 5))
    assert (in_leg.entry_date, in_leg.posted_date) == (date(2026, 9, 7), date(2026, 9, 7))


def test_pair_transfer_revert_deletes_both_legs_and_reopens_both_cases(orphan_pair, db_session):
    p = orphan_pair
    row = _apply(db_session, _needs_peer(db_session, p.cstmt), "pair_transfer", _pair_params(p.bline))
    _revert(db_session, row.id)
    db_session.expire_all()
    assert all(db_session.get(LedgerEntry, i) is None for i in row.created_entry_ids)
    assert _active(db_session, p.bstmt.id) == [] and _active(db_session, p.cstmt.id) == []
    assert _needs_peer(db_session, p.bstmt) is not None and _needs_peer(db_session, p.cstmt) is not None


@pytest.mark.parametrize("field,value", [("description", "改"), ("category_id", "SEED_CATEGORY"),
                                         ("amount", Decimal("-20001"))])
def test_pair_transfer_revert_refuses_snapshot_conflict_after_any_leg_edit(field, value, orphan_pair, seed, db_session):
    # the category arm sets a real id: create_transfer builds the legs with category_id None
    row = _apply(db_session, _needs_peer(db_session, orphan_pair.cstmt), "pair_transfer", _pair_params(orphan_pair.bline))
    leg = db_session.get(LedgerEntry, sorted(row.created_entry_ids)[0])
    setattr(leg, field, seed.category("對帳測試", kind="transfer_out").id if value == "SEED_CATEGORY" else value)
    db_session.commit()
    exc = _refuse(db_session, lambda: _revert(db_session, row.id))
    assert isinstance(exc, CodedConflictError) and exc.code == "snapshot_conflict"
    db_session.expire_all()
    assert all(db_session.get(LedgerEntry, i) is not None for i in row.created_entry_ids)
    assert _fresh(db_session, ReconciliationAction, row.id).status == "applied"


@pytest.mark.parametrize("variant,code", [("third_account", "peer_out_of_scope"), ("busy", "peer_statement_busy"),
                                          ("historical", "peer_not_live")])
def test_pair_transfer_refuses_a_peer_outside_the_candidates_and_a_busy_or_historical_peer(
        variant, code, orphan_pair, seed, run, db_session):
    p = orphan_pair
    peer = p.bline
    if variant == "third_account":  # a live statement of another account printing a non-complementary payment
        other = _bank(seed, db_session, name="另一銀行")
        _, (peer,), _ = _submit(db_session, run, other, [_line(1, date(2026, 9, 5), "轉帳", "-15000", kind="payment")],
                                kind="bank")
    elif variant == "busy":
        _fresh(db_session, type(p.bstmt), p.bstmt.id).needs_recheck = True
        db_session.commit()
    else:
        _fresh(db_session, type(p.bstmt), p.bstmt.id).mode = "historical"
        db_session.commit()
    exc = _refuse(db_session, lambda: _apply(db_session, _needs_peer(db_session, p.cstmt), "pair_transfer",
                                             _pair_params(peer)))
    assert _code(exc) == code
    assert db_session.query(ReconciliationAction).filter_by(action="pair_transfer").count() == 0


def test_pair_transfer_repeats_the_creation_family_check_for_the_peer_event(orphan_pair, seed, db_session):
    p = orphan_pair
    category = seed.category("繳卡費")
    db_session.commit()
    bank_case = _needs_peer(db_session, p.bstmt)
    # the bank event gets an applied creation first (a hand row: create_entry is not offered on a needs_peer case)
    db_session.add(ReconciliationAction(idempotency_key="t-peer-create", event_id=bank_case.event_id,
                                        effect_slot="create", case_id=bank_case.id, action="create_entry",
                                        params={"kind": "expense", "category_id": category.id}, actor="owner",
                                        status="applied"))
    db_session.commit()
    exc = _refuse(db_session, lambda: _apply(db_session, _needs_peer(db_session, p.cstmt), "pair_transfer",
                                             _pair_params(p.bline)))
    assert _code(exc) == "duplicate_effect"


def _add_twin(twin, p, seed, db):
    if twin == "unclaimed_transfer_any_amount":  # 15 % off: outside the transfer tolerance, inside the window
        _transfer(seed, p.bank, p.card, "17000", date(2026, 9, 1))
    elif twin == "near_amount_entry_any_kind":  # 5 % off, an expense on the bank
        seed.entry(p.bank, "-19000", kind="expense", day=date(2026, 9, 6))
    else:  # the R6b twin rule: an exact standalone transfer_in on the card
        seed.entry(p.card, "20000", kind="transfer_in", day=date(2026, 9, 5))
    db.commit()


@pytest.mark.parametrize("twin", ["unclaimed_transfer_any_amount", "near_amount_entry_any_kind", "r6b_twin"])
def test_pair_transfer_transfer_twin_guard_binds_the_policy_and_policy_proposals_not_the_owner(
        twin, orphan_pair, seed, db_session):
    p = orphan_pair
    case = _needs_peer(db_session, p.cstmt)
    prop = _policy_proposal(db_session, case, p.bline)  # 1. made while no twin exists (an over-cap policy run)
    _add_twin(twin, p, seed, db_session)  # 2. the twin arrives; the daily pass reconciles the peer statement
    _reconcile(db_session, p.bstmt.id)  # (its sweep would otherwise leave it needs_recheck: peer_statement_busy)
    assert _needs_peer(db_session, p.bstmt) is not None and _needs_peer(db_session, p.cstmt).id == case.id
    reason = transfer_actions.transfer_twin_reason(db_session, _rules(db_session, p.cstmt), (p.cstmt, p.cline),
                                                   (p.bstmt, p.bline))
    assert reason is not None
    # 3. the policy actor → candidate_changed; the owner applying the policy's proposal → owner_may_record
    exc = _refuse(db_session, lambda: _apply(db_session, _needs_peer(db_session, p.cstmt), "pair_transfer",
                                             _pair_params(p.bline), **POLICY_RUN))
    assert _code(exc) == "candidate_changed"
    exc = _refuse(db_session, lambda: proposal_service.apply_proposal(db_session, proposal_id=prop.id,
                                                                      label="owner-test", request_id=None))
    assert _code(exc) == "owner_may_record"
    # the plain owner action from the case is not checked
    row = _apply(db_session, _needs_peer(db_session, p.cstmt), "pair_transfer", _pair_params(p.bline))
    assert row.status == "applied"


def test_pair_transfer_owner_applying_an_agent_proposal_skips_the_twin_guard(orphan_pair, seed, db_session):
    p = orphan_pair
    prop = _policy_proposal(db_session, _needs_peer(db_session, p.cstmt), p.bline, author="hermes")
    _add_twin("r6b_twin", p, seed, db_session)
    row, _ = proposal_service.apply_proposal(db_session, proposal_id=prop.id, label="owner-test", request_id=None)
    db_session.commit()
    assert row.status == "applied" and row.proposal_id == prop.id


def test_pair_transfer_owner_may_re_pair_after_deleting_the_pair(orphan_pair, db_session):
    p = orphan_pair
    first = _apply(db_session, _needs_peer(db_session, p.cstmt), "pair_transfer", _pair_params(p.bline))
    ews.delete_entry(db_session, sorted(first.created_entry_ids)[0])  # either leg removes both
    db_session.commit()
    for stmt in (p.bstmt, p.cstmt):
        _reconcile(db_session, stmt.id)
    case = _needs_peer(db_session, p.cstmt)
    assert case.context.get("owner_deleted") is True and _needs_peer(db_session, p.bstmt).context.get("owner_deleted")
    # a policy-authored proposal on the case: the owner applying it is gated like the policy (occupancy guard)
    prop = _policy_proposal(db_session, case, p.bline)
    exc = _refuse(db_session, lambda: proposal_service.apply_proposal(db_session, proposal_id=prop.id,
                                                                      label="owner-test", request_id=None))
    assert _code(exc) == "owner_deleted"
    # the owner's own action re-pairs
    row = _apply(db_session, _needs_peer(db_session, p.cstmt), "pair_transfer", _pair_params(p.bline))
    assert row.status == "applied" and len(row.created_entry_ids) == 2
    assert not set(row.created_entry_ids) & set(first.created_entry_ids)
    assert all(leg is not None for leg in _legs(db_session, row))


def test_pair_transfer_twin_rule_a_ignores_a_transfer_whose_legs_are_both_covered(dirty_on, seed, run, db_session):
    small_bank = _line(2, date(2026, 9, 3), "信用卡繳款", "-5000", kind="payment")
    small_card = _line(2, date(2026, 9, 3), "繳款", "-5000", kind="payment")
    bank = _bank(seed, db_session)
    card = _card(seed, db_session, name="卡")
    _transfer(seed, bank, card, "5000", date(2026, 9, 3))  # another payment the same week, printed on both
    db_session.commit()
    bstmt, blines, _ = _submit(db_session, run, bank, [BANK_LINE, small_bank], kind="bank")
    cstmt, clines, _ = _submit(db_session, run, card, [CARD_LINE, small_card])
    assert len(_active(db_session, bstmt.id)) == 1 and len(_active(db_session, cstmt.id)) == 1
    assert transfer_actions.transfer_twin_reason(db_session, _rules(db_session, cstmt), (cstmt, clines[0]),
                                                 (bstmt, blines[0])) is None
    row = _apply(db_session, _needs_peer(db_session, cstmt), "pair_transfer", _pair_params(blines[0]), **POLICY_RUN)
    assert row.status == "applied" and row.actor == "policy"


def test_pair_transfer_dirty_disabled_binds_the_policy_not_the_owner(orphan_pair, db_session):
    p = orphan_pair
    set_dirty(db_session, False)
    db_session.commit()
    exc = _refuse(db_session, lambda: _apply(db_session, _needs_peer(db_session, p.cstmt), "pair_transfer",
                                             _pair_params(p.bline), **POLICY_RUN))
    assert _code(exc) == "dirty_disabled"
    assert _apply(db_session, _needs_peer(db_session, p.cstmt), "pair_transfer",
                  _pair_params(p.bline)).status == "applied"


@pytest.mark.parametrize("shape", ["shadow", "orphan"])
def test_replace_stand_in_refuses_a_transfer_stand_in(shape, orphan_pair, seed, db_session):
    p = orphan_pair
    row = _apply(db_session, _needs_peer(db_session, p.cstmt), "pair_transfer", _pair_params(p.bline))
    _, stand_in = _legs(db_session, row)  # the card (case statement) leg
    if shape == "orphan":  # the stand-in's claim released without an edit (as an unmatch would)
        coverage_service.release_line(db_session, _fresh(db_session, type(p.cstmt), p.cstmt.id), p.cline.id, reason="test")
        db_session.commit()
    _, owner_in = _transfer(seed, p.bank, p.card, "20000", date(2026, 9, 5))  # the owner records it later
    db_session.commit()
    _reconcile(db_session, p.cstmt.id)
    hinted = [c for c in _cases(db_session, p.cstmt.id, "open")
              if (c.context or {}).get("hint") in ("shadowed_by_stand_in", "orphaned_stand_in")]
    (case,) = hinted
    if shape == "orphan":  # the owner's in leg took the line (the stand-in's representation dropped)
        assert {r.entry_id for r in _active(db_session, p.cstmt.id)} == {owner_in.id}
        assert (case.context["hint"], case.entry_id) == ("orphaned_stand_in", stand_in.id)
    else:  # the stand-in keeps its claim; the owner's in leg shadows it
        assert {r.entry_id for r in _active(db_session, p.cstmt.id)} == {stand_in.id}
        assert (case.context["hint"], case.entry_id, case.context["stand_in_entry_id"]) == (
            "shadowed_by_stand_in", owner_in.id, stand_in.id)
    exc = _refuse(db_session, lambda: _apply(db_session, case, "replace_stand_in", {}))
    assert _code(exc) == "stand_in_changed"
    assert _fresh(db_session, LedgerEntry, stand_in.id) is not None


def test_owner_deleted_pair_blocks_a_second_pairing_on_both_statements(orphan_pair, db_session):
    p = orphan_pair
    row = _apply(db_session, _needs_peer(db_session, p.cstmt), "pair_transfer", _pair_params(p.bline))
    ews.delete_entry(db_session, sorted(row.created_entry_ids)[0])
    db_session.commit()
    peers = {p.bstmt.id: p.cline, p.cstmt.id: p.bline}

    def blocked(stmt):
        case = _needs_peer(db_session, stmt)
        assert case.context.get("owner_deleted") is True and case.context["hint"] == "needs_peer"
        exc = _refuse(db_session, lambda: _apply(db_session, case, "pair_transfer", _pair_params(peers[stmt.id]),
                                                 **POLICY_RUN))
        assert _code(exc) == "owner_deleted"

    # (1) the bank statement first: the card statement (the row's own) is not swept yet, so the row is still
    # applied — the window in which the peer statement's policy runs in the daily pass (the engine sweeps the locked
    # card statement inside the refused attempt, so the guard sees either arm; the rollback keeps the row applied)
    _reconcile(db_session, p.bstmt.id)
    assert _fresh(db_session, ReconciliationAction, row.id).status == "applied"
    blocked(p.bstmt)
    assert _fresh(db_session, ReconciliationAction, row.id).status == "applied"
    # (2) the originating sweep retires the row: the policy actor is still blocked on both statements
    _sweep(db_session, p.cstmt)
    retired = _fresh(db_session, ReconciliationAction, row.id)
    assert retired.status == "reverted" and "deleted_by_event" in retired.inverse
    for stmt in (p.bstmt, p.cstmt):
        _reconcile(db_session, stmt.id)
        blocked(stmt)


def test_engine_revert_of_a_pair_does_not_block_a_later_pairing(orphan_pair, db_session):
    p = orphan_pair
    row = _apply(db_session, _needs_peer(db_session, p.cstmt), "pair_transfer", _pair_params(p.bline))
    _revert(db_session, row.id)
    assert "owner_deleted" not in _needs_peer(db_session, p.cstmt).context
    again = _apply(db_session, _needs_peer(db_session, p.cstmt), "pair_transfer", _pair_params(p.bline), **POLICY_RUN)
    assert again.status == "applied"
