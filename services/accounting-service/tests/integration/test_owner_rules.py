"""Owner-taught reconciliation rules (inbox spec §6). Synthetic data only."""
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import (
    AccountStatement, LedgerEntry, ReconciliationAction, ReconciliationAudit, ReconciliationCase, ReconciliationOwnerRule,
)
from app.services import owner_rule_service as ors
from app.services.reconciliation_actions import audit, engine
from tests.integration.statement_fixtures import OCT, _card, _cases, _line, _submit, run  # noqa: F401

DAY = date(2026, 9, 3)


def _rule(seed, db, account, **fields) -> ReconciliationOwnerRule:
    defaults = dict(line_kind="purchase", merchant_pattern="放款還款", flow_sign="-", amount=Decimal("3865"),
                    action="record", params={"category_id": 1})
    row = ReconciliationOwnerRule(account_id=account.id, **{**defaults, **fields})
    db.add(row)
    db.commit()
    return row


def test_rule_row_defaults(seed, db_session):
    account = seed.account("樂天")
    row = _rule(seed, db_session, account)
    db_session.expire_all()
    row = db_session.get(ReconciliationOwnerRule, row.id)
    assert row.enabled is True and row.applied_count == 0 and row.last_applied_at is None
    assert row.amount == Decimal("3865.0000") and row.params == {"category_id": 1}


def test_rule_key_is_unique_per_account(seed, db_session):
    account = seed.account("樂天")
    _rule(seed, db_session, account)
    with pytest.raises(IntegrityError):
        _rule(seed, db_session, account)
    db_session.rollback()


def test_rule_key_treats_null_amounts_as_equal(seed, db_session):
    account = seed.account("樂天")
    _rule(seed, db_session, account, amount=None)
    with pytest.raises(IntegrityError):
        _rule(seed, db_session, account, amount=None)
    db_session.rollback()


def test_new_audit_events_are_known():
    assert {"rule_applied", "rule_skipped", "inbox_answer"} <= audit.EVENTS


def test_rule_routes_list_patch_delete(client, seed, db_session, monkeypatch):
    from app import auth
    monkeypatch.setenv(auth.TOKENS_ENV, "spa:spa-token,hermes:hermes-token")
    monkeypatch.setenv(auth.SCOPES_ENV, "spa=legacy; hermes=read")
    monkeypatch.setenv(auth.FEATURE_ENV, "true")
    S, H = {"Authorization": "Bearer spa-token"}, {"Authorization": "Bearer hermes-token"}
    account = seed.account("樂天")
    row = _rule(seed, db_session, account)
    listed = client.get("/reconciliation/owner-rules", headers=H).json()
    assert [r["id"] for r in listed] == [row.id] and listed[0]["amount"] == "3865.0000"
    assert client.patch(f"/reconciliation/owner-rules/{row.id}", headers=H, json={"enabled": False}).status_code == 403
    assert client.patch(f"/reconciliation/owner-rules/{row.id}", headers=S, json={"enabled": False}).json()["enabled"] is False
    assert client.delete(f"/reconciliation/owner-rules/{row.id}", headers=S).status_code == 204
    assert client.delete(f"/reconciliation/owner-rules/{row.id}", headers=S).status_code == 404
    assert client.get("/reconciliation/owner-rules", headers=S).json() == []


# --- applying rules before the policy (spec §6.3) ---------------------------------------------------------------
# Bank withdrawals print negative amounts (derive.flow_amount refuses a positive BANK_DEBIT).

def _bank(seed, db, name="樂天"):
    account = seed.account(name, statement_live_from=date(2026, 9, 1))
    db.commit()
    return account


def _audits(db, event):
    db.expire_all()
    return db.execute(select(ReconciliationAudit).where(ReconciliationAudit.event == event)).scalars().all()


def _open_lines(db, statement_id):
    return [c for c in _cases(db, statement_id, "open") if c.kind in ("line_unmatched", "amount_delta")]


def test_rule_books_a_matching_line_at_ingest_as_owner(seed, db_session, run):
    account = _bank(seed, db_session)
    food = seed.category("飲食")
    db_session.commit()
    _rule(seed, db_session, account, line_kind="withdrawal", merchant_pattern="放款還款", amount=Decimal("-3865"),
          params={"category_id": food.id})
    stmt, (line,), _ = _submit(db_session, run, account, [_line(1, DAY, "放款還款 0903", -3865, "withdrawal")], kind="bank")
    result = ors.run_rules_safe(db_session, stmt.id)
    assert len(result.applied) == 1 and result.skipped == []
    (case,) = _cases(db_session, stmt.id, "resolved")
    action = db_session.get(ReconciliationAction, result.applied[0])
    assert action.actor == "owner" and action.action == "create_entry" and case.resolved_by == "owner"
    (booked,) = db_session.execute(select(LedgerEntry).where(LedgerEntry.account_id == account.id)).scalars().all()
    assert booked.category_id == food.id and booked.amount == Decimal("-3865.0000")
    (ev,) = _audits(db_session, "rule_applied")
    assert ev.actor.startswith("rule:") and ev.detail["case_id"] == case.id and ev.detail["action"] == "create_entry"
    db_session.expire_all()
    rule = db_session.execute(select(ReconciliationOwnerRule)).scalar_one()
    assert rule.applied_count == 1 and rule.last_applied_at is not None


def test_rule_amount_mismatch_and_historical_and_reverted_are_skipped(seed, db_session, run):
    account = _bank(seed, db_session)
    food = seed.category("飲食")
    db_session.commit()
    rule = _rule(seed, db_session, account, line_kind="withdrawal", merchant_pattern="放款還款", amount=Decimal("-3865"),
                 params={"category_id": food.id})
    stmt, _, _ = _submit(db_session, run, account, [_line(1, DAY, "放款還款", -3870, "withdrawal")], kind="bank")
    assert ors.run_rules_safe(db_session, stmt.id).applied == []  # amount differs → no match, no audit
    assert _audits(db_session, "rule_skipped") == []
    rule.amount = None
    db_session.commit()
    result = ors.run_rules_safe(db_session, stmt.id)
    assert len(result.applied) == 1
    engine.revert_action(db_session, action_id=result.applied[0], actor="owner", label="spa")
    db_session.commit()
    assert ors.run_rules_safe(db_session, stmt.id).applied == []  # the same rule never redoes what 撤銷 undid
    other = _rule(seed, db_session, account, line_kind="withdrawal", merchant_pattern="款還款", amount=None,
                  params={"category_id": food.id})  # a different rule (shorter pattern, higher id) still may
    assert len(ors.run_rules_safe(db_session, stmt.id).applied) == 1
    (ev,) = [e for e in _audits(db_session, "rule_applied") if e.detail["rule_id"] == other.id]
    hist = seed.account("舊戶")
    db_session.commit()
    _rule(seed, db_session, hist, line_kind="withdrawal", merchant_pattern="店", amount=None, params={"category_id": food.id})
    h, _, _ = _submit(db_session, run, hist, [_line(1, DAY, "店", -10, "withdrawal")], kind="bank")
    assert db_session.get(AccountStatement, h.id).mode == "historical"
    assert ors.run_rules_safe(db_session, h.id).applied == []


def test_rule_skips_owner_recorded_lines_and_audits(seed, db_session, run, monkeypatch):
    """The engine precheck is `policy_service.refuse_owner_recorded`; its own detection is tested in
    test_reconciliation_policy.py — here the wiring: a refusal is audited and the case stays open."""
    from app.services import policy_service
    from app.services.errors import CodedConflictError

    def refuse(ctx):
        raise CodedConflictError("owner_may_record", f"case {ctx.case.id}")

    monkeypatch.setattr(policy_service, "refuse_owner_recorded", refuse)
    account = _bank(seed, db_session)
    food = seed.category("飲食")
    db_session.commit()
    _rule(seed, db_session, account, line_kind="withdrawal", merchant_pattern="放款還款", amount=None, params={"category_id": food.id})
    stmt, _, _ = _submit(db_session, run, account, [_line(1, DAY, "放款還款", -3865, "withdrawal")], kind="bank")
    result = ors.run_rules_safe(db_session, stmt.id)
    assert result.applied == [] and [code for _, code in result.skipped] == ["owner_may_record"]
    (ev,) = _audits(db_session, "rule_skipped")
    assert ev.detail["code"] == "owner_may_record" and len(_open_lines(db_session, stmt.id)) == 1


def test_rule_with_missing_category_is_skipped_not_fatal(seed, db_session, run):
    account = _bank(seed, db_session)
    _rule(seed, db_session, account, line_kind="withdrawal", merchant_pattern="甲", amount=None, params={"category_id": 999999})
    good = seed.category("飲食")
    db_session.commit()
    _rule(seed, db_session, account, line_kind="withdrawal", merchant_pattern="乙", amount=None, params={"category_id": good.id})
    stmt, _, _ = _submit(db_session, run, account, [_line(1, DAY, "甲", -10, "withdrawal"), _line(2, DAY, "乙", -20, "withdrawal")], kind="bank")
    result = ors.run_rules_safe(db_session, stmt.id)
    assert len(result.applied) == 1 and len(result.skipped) == 1
    assert len(_open_lines(db_session, stmt.id)) == 1


def test_rule_accept_bank_amount_rewrites_within_near(seed, db_session, run):
    account = _bank(seed, db_session)
    entry = seed.entry(account, "-879", day=DAY, merchant="卡費")
    db_session.commit()
    _rule(seed, db_session, account, line_kind="withdrawal", merchant_pattern="卡費", amount=None, action="accept_bank_amount", params={})
    stmt, _, _ = _submit(db_session, run, account, [_line(1, DAY, "卡費", -888, "withdrawal")], kind="bank")
    result = ors.run_rules_safe(db_session, stmt.id)
    assert len(result.applied) == 1
    db_session.expire_all()
    assert db_session.get(LedgerEntry, entry.id).amount == Decimal("-888.0000")


def test_rules_skip_settlement_proposed_and_other_accounts(seed, db_session, run):
    from app.models import ReconciliationProposal
    account = _bank(seed, db_session)
    other = _bank(seed, db_session, "別戶")
    food = seed.category("飲食")
    db_session.commit()
    _rule(seed, db_session, account, line_kind="withdrawal", merchant_pattern="交割股款", amount=None, params={"category_id": food.id})
    _rule(seed, db_session, account, line_kind="withdrawal", merchant_pattern="店", amount=None, params={"category_id": food.id})
    stmt, lines, _ = _submit(db_session, run, account, [_line(1, DAY, "交割股款 1234", -9000, "withdrawal"),
                                                        _line(2, DAY, "店", -10, "withdrawal")], kind="bank")
    shop = next(c for c in _open_lines(db_session, stmt.id) if c.line_id == lines[1].id)
    db_session.add(ReconciliationProposal(case_id=shop.id, case_version=shop.version, event_id=shop.event_id,
                                          action="dismiss", params={"note": "x"}, rationale="r", confidence=Decimal("0.5"),
                                          author="hermes"))
    shop.status = "proposed"
    db_session.commit()
    assert ors.run_rules_safe(db_session, stmt.id).applied == []  # settlement line + proposed case: untouched
    o, _, _ = _submit(db_session, run, other, [_line(1, DAY, "店", -10, "withdrawal")], kind="bank")
    assert ors.run_rules_safe(db_session, o.id).applied == []  # the rule belongs to another account


def test_rule_accept_respects_the_cutover_lock(seed, db_session, run):
    account = _bank(seed, db_session)
    seed.entry(account, "-879", day=DAY, merchant="卡費", source="moze_backup")  # ACCOUNTING_IMPORT_LOCKED unset (conftest)
    db_session.commit()
    _rule(seed, db_session, account, line_kind="withdrawal", merchant_pattern="卡費", amount=None, action="accept_bank_amount", params={})
    stmt, _, _ = _submit(db_session, run, account, [_line(1, DAY, "卡費", -888, "withdrawal")], kind="bank")
    result = ors.run_rules_safe(db_session, stmt.id)
    assert result.applied == [] and [code for _, code in result.skipped] == ["locked_until_cutover"]


def test_rules_run_before_the_policy_at_every_hook_site(client, seed, db_session, dirty_on, monkeypatch):
    """Review Should 1: `run_rules_safe` runs right before `run_policy_safe` at the submit, manual, reconcile and
    sweep routes, for the same statement id and run id. Both services are replaced by recorders. One worker run (the
    route-claimed lease) serves the submit and the sweep: a second claimed run would make `_claim` refuse."""
    from app import auth
    from app.services import policy_service
    from tests.integration.test_reconciliation_policy import FEE_LINE, _claim, _revision
    calls: list[tuple[str, int, int | None]] = []
    monkeypatch.setattr(ors, "run_rules_safe", lambda db, sid, run_id=None: calls.append(("rules", sid, run_id)) or ors.RuleRunResult())
    monkeypatch.setattr(policy_service, "run_policy_safe", lambda db, sid, run_id=None: calls.append(("policy", sid, run_id)) or policy_service.PolicyResult())
    monkeypatch.setenv(auth.TOKENS_ENV, "spa:spa-token,worker:worker-token,hermes:hermes-token")
    monkeypatch.setenv(auth.SCOPES_ENV, "spa=legacy; worker=ingest; hermes=read,propose,enqueue")
    monkeypatch.setenv(auth.FEATURE_ENV, "true")
    S, W = {"Authorization": "Bearer spa-token"}, {"Authorization": "Bearer worker-token"}

    def last_pair():
        assert len(calls) >= 2 and calls[-2][0] == "rules" and calls[-1][0] == "policy", calls[-2:]
        assert calls[-2][1:] == calls[-1][1:]  # same statement id and run id
        return calls[-1][1]

    # 1. submit route (worker lease; a live card statement → mode live, hooks run)
    card = _card(seed, db_session)
    lease = _claim(client)
    res = client.post("/statements/revisions", headers=W, json=_revision(lease, card, [FEE_LINE], "1200"))
    assert res.status_code == 201, res.text
    assert last_pair() == res.json()["statement_id"] and calls[-1][2] == lease["run_id"]
    # 2. manual statement route (owner): same fields as ManualStatementIn (`schemas/statements.py:227-241`)
    account = _bank(seed, db_session)
    body = {"kind": "bank", "currency": "TWD", "period_start": "2026-09-01", "period_end": "2026-09-30",
            "opening_balance": "0", "statement_total": "-10.0000",
            "lines": [{"seq": 1, "posted_date": "2026-09-03", "merchant_raw": "店", "printed_amount": "-10", "line_kind": "withdrawal"}]}
    res = client.post(f"/accounts/{account.id}/statements", headers=S, json=body)
    assert res.status_code == 201, res.text
    manual_id = res.json()["statement_id"]
    assert last_pair() == manual_id
    # 3. reconcile route
    assert client.post(f"/accounts/{account.id}/statements/{manual_id}/reconcile", headers=S).status_code == 200
    assert last_pair() == manual_id
    # 4. sweep route: mark the statement pending the way test_sweep_route_runs_policy_per_statement does
    stmt = db_session.get(AccountStatement, manual_id)
    stmt.needs_recheck = True
    db_session.commit()
    res = client.post("/reconciliation/sweep", headers=W, json={"run_id": lease["run_id"], "lease_token": lease["lease_token"]})
    assert res.status_code == 200, res.text
    assert last_pair() == manual_id and calls[-1][2] == lease["run_id"]


def test_reteach_then_the_replacement_rule_answers_the_next_line(seed, db_session, run):
    """Re-check Should: after dismiss → record re-teach the next matching line is booked, and after record → dismiss it is dismissed."""
    account = _bank(seed, db_session)
    food = seed.category("飲食")
    db_session.commit()
    rule = _rule(seed, db_session, account, line_kind="withdrawal", merchant_pattern="店", amount=None, action="dismiss", params={})
    rule.action, rule.params = "record", {"category_id": food.id}  # what Task 5's upsert does on re-teach
    db_session.commit()
    s1, _, _ = _submit(db_session, run, account, [_line(1, DAY, "店", -10, "withdrawal")], kind="bank")
    ors.run_rules_safe(db_session, s1.id)
    (c1,) = _cases(db_session, s1.id, "resolved")
    assert c1.kind == "line_unmatched" and db_session.get(ReconciliationAction, c1.resolved_action_id).action == "create_entry"
    rule.action, rule.params = "dismiss", {}
    db_session.commit()
    s2, _, _ = _submit(db_session, run, account, [_line(1, date(2026, 10, 3), "店", -10, "withdrawal")], kind="bank", period=OCT)
    ors.run_rules_safe(db_session, s2.id)
    assert len(_cases(db_session, s2.id, "dismissed")) == 1


def test_rules_really_book_at_the_reconcile_route(client, seed, db_session, run, monkeypatch):
    """No recorders: the real runner + real policy (off) through the route."""
    from app import auth
    monkeypatch.setenv(auth.TOKENS_ENV, "spa:spa-token")
    monkeypatch.setenv(auth.SCOPES_ENV, "spa=legacy")
    monkeypatch.setenv(auth.FEATURE_ENV, "true")
    account = _bank(seed, db_session)
    food = seed.category("飲食")
    db_session.commit()
    stmt, _, _ = _submit(db_session, run, account, [_line(1, DAY, "放款還款", -3865, "withdrawal")], kind="bank")
    _rule(seed, db_session, account, line_kind="withdrawal", merchant_pattern="放款還款", amount=None, params={"category_id": food.id})
    res = client.post(f"/accounts/{account.id}/statements/{stmt.id}/reconcile", headers={"Authorization": "Bearer spa-token"})
    assert res.status_code == 200, res.text
    assert _open_lines(db_session, stmt.id) == [] and len(_audits(db_session, "rule_applied")) == 1


def _shop_statement(seed, db, run):
    account = _bank(seed, db)
    food = seed.category("飲食")
    db.commit()
    _rule(seed, db, account, line_kind="withdrawal", merchant_pattern="店", amount=None, params={"category_id": food.id})
    stmt, _, _ = _submit(db, run, account, [_line(1, DAY, "店", -10, "withdrawal")], kind="bank")
    return stmt


def _rule_audits(db):
    return _audits(db, "rule_applied") + _audits(db, "rule_skipped")


def test_rules_respect_an_open_parse_review(seed, db_session, run):
    """Review Important 1: the policy's statement exclusions (bar currency) hold for rules too."""
    stmt = _shop_statement(seed, db_session, run)
    review = ReconciliationCase(statement_id=stmt.id, revision_id=stmt.current_revision_id, kind="parse_review",
                                context={})
    db_session.add(review)
    db_session.commit()
    result = ors.run_rules_safe(db_session, stmt.id)
    assert result.applied == [] and result.skipped == [] and _rule_audits(db_session) == []
    review.status = "dismissed"  # the exclusion lifted, the same rule answers the line
    db_session.commit()
    assert len(ors.run_rules_safe(db_session, stmt.id).applied) == 1


def test_rules_respect_needs_recheck(seed, db_session, run):
    stmt = _shop_statement(seed, db_session, run)
    db_session.get(AccountStatement, stmt.id).needs_recheck = True
    db_session.commit()
    result = ors.run_rules_safe(db_session, stmt.id)
    assert result.applied == [] and result.skipped == [] and _rule_audits(db_session) == []
    db_session.get(AccountStatement, stmt.id).needs_recheck = False
    db_session.commit()
    assert len(ors.run_rules_safe(db_session, stmt.id).applied) == 1


def test_a_repeated_refusal_is_audited_once(seed, db_session, run):
    """Review Minor 1: once per (case, version, rule, code), like policy_skipped."""
    account = _bank(seed, db_session)
    _rule(seed, db_session, account, line_kind="withdrawal", merchant_pattern="甲", amount=None, params={"category_id": 999999})
    stmt, _, _ = _submit(db_session, run, account, [_line(1, DAY, "甲", -10, "withdrawal")], kind="bank")
    first = ors.run_rules_safe(db_session, stmt.id)
    second = ors.run_rules_safe(db_session, stmt.id)
    assert len(first.skipped) == len(second.skipped) == 1  # the run still reports it each time
    (ev,) = _audits(db_session, "rule_skipped")
    (case,) = _open_lines(db_session, stmt.id)
    assert ev.detail["case_version"] == case.version and ev.detail["case_id"] == case.id


def test_an_open_case_with_a_pending_proposal_is_untouched(seed, db_session, run):
    """Review Minor 2: the case stays `open` (not `proposed`), so only `pending_for_case` keeps the rule off."""
    from app.models import ReconciliationProposal
    stmt = _shop_statement(seed, db_session, run)
    (shop,) = _open_lines(db_session, stmt.id)
    db_session.add(ReconciliationProposal(case_id=shop.id, case_version=shop.version, event_id=shop.event_id,
                                          action="dismiss", params={"note": "x"}, rationale="r", confidence=Decimal("0.5"),
                                          author="hermes"))
    db_session.commit()
    assert db_session.get(ReconciliationCase, shop.id).status == "open"
    result = ors.run_rules_safe(db_session, stmt.id)
    assert result.applied == [] and result.skipped == [] and _rule_audits(db_session) == []
    assert [c.id for c in _open_lines(db_session, stmt.id)] == [shop.id]


def test_reverted_rule_id_ignores_a_malformed_context_value(seed, db_session, run):
    """Review Minor 5: a malformed `reverted_action_id` returns None instead of aborting the run."""
    stmt = _shop_statement(seed, db_session, run)
    (shop,) = _open_lines(db_session, stmt.id)
    shop.context = {**(shop.context or {}), "reverted_action_id": "not-a-number"}
    db_session.commit()
    assert ors.reverted_rule_id(db_session, shop) is None
    assert len(ors.run_rules_safe(db_session, stmt.id).applied) == 1
