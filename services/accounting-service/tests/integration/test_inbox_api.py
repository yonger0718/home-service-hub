"""Inbox read model and batch apply (spec 2026-10-21 §4–§5). Synthetic data only."""
from datetime import date
from decimal import Decimal

import pytest

from app import auth
from app.models import ReconciliationCase
from tests.integration.statement_fixtures import (  # noqa: F401
    _card, _cases, _line, _submit, run,
)

TOKENS = "spa:spa-token,hermes:hermes-token"
SCOPES = "spa=legacy; hermes=read,propose,enqueue"
S = {"Authorization": "Bearer spa-token"}
H = {"Authorization": "Bearer hermes-token"}
DAY = date(2026, 9, 3)


@pytest.fixture(autouse=True)
def recon_env(monkeypatch):
    monkeypatch.setenv(auth.TOKENS_ENV, TOKENS)
    monkeypatch.setenv(auth.SCOPES_ENV, SCOPES)
    monkeypatch.setenv(auth.FEATURE_ENV, "true")


def _bank(seed, db, name="樂天", **extra):
    account = seed.account(name, statement_live_from=date(2026, 9, 1), **extra)
    db.commit()
    return account


def _inbox(client, **params):
    res = client.get("/reconciliation/inbox", headers=S, params=params)
    assert res.status_code == 200, res.text
    return res.json()


def _items(body, question=None):
    items = [item for account in body["accounts"] for item in account["items"]]
    return items if question is None else [i for i in items if i["question"] == question]


def _open_lines(db, statement_id):
    """The open line_unmatched / amount_delta cases of a statement (a bank statement may also open a balance_gap)."""
    return [c for c in _cases(db, statement_id, "open") if c.kind in ("line_unmatched", "amount_delta")]


def test_bank_only_card_with_buttons_and_rule_hint(client, seed, db_session, run):
    account = _bank(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, account, [_line(1, DAY, "放款還款 0903", -3865, "withdrawal")], kind="bank")
    body = _inbox(client)
    (item,) = _items(body, "bank_only")
    assert body["accounts"][0]["name"] == "樂天" and body["accounts"][0]["is_credit"] is False
    assert item["question"] == "bank_only" and item["folded"] is False
    assert item["actions"] == ["record", "match_candidate", "dismiss"]
    assert item["line"] == {"id": line.id, "date": "2026-09-03", "merchant": line.merchant_norm,
                            "amount": "-3865.0000", "line_kind": "withdrawal"}
    assert item["rule_hint"] == {"merchant_pattern": "放款還款", "line_kind": "withdrawal", "flow_sign": "-",
                                 "amount": "-3865.0000", "default_category_id": None}
    assert item["group_key"] == f"{account.id}|withdrawal|放款還款|-3865.0000"
    assert item["statement"] == {"id": stmt.id, "period_end": "2026-09-30"}
    assert body["counts"]["ask"] == 1 and body["counts"]["ask_items"] == 1


def test_two_same_lines_count_one_card(client, seed, db_session, run):
    account = _bank(seed, db_session)
    _submit(db_session, run, account, [_line(1, DAY, "放款還款 0903", -3865, "withdrawal"),
                                       _line(2, date(2026, 9, 25), "放款還款 0925", -3865, "withdrawal")], kind="bank")
    body = _inbox(client)
    assert (body["counts"]["ask"], body["counts"]["ask_items"]) == (1, 2)
    keys = {item["group_key"] for item in _items(body, "bank_only")}
    assert len(keys) == 1


def test_transfer_and_settlement_and_installment(client, seed, db_session, run):
    account = _bank(seed, db_session)
    _submit(db_session, run, account, [
        _line(1, DAY, "他行轉入", 500, "transfer_in"),
        _line(2, DAY, "交割股款 1234", -9000, "withdrawal"),
    ], kind="bank")
    card = _card(seed, db_session)
    _submit(db_session, run, card, [_line(1, DAY, "分期 1/3", 300, "installment", installment_seq=1, installment_total=3)])
    body = _inbox(client)
    by_q = {item["question"]: item for item in _items(body)}  # one item per question in this fixture
    assert by_q["transfer"]["actions"] == ["match_candidate", "dismiss"] and by_q["transfer"]["rule_hint"] is None
    assert by_q["settlement"]["actions"] == [] and by_q["settlement"]["folded"] is True
    assert by_q["other"]["folded"] is True and by_q["other"]["actions"] == ["dismiss"]
    assert body["counts"]["settlement"] == 1 and body["counts"]["other"] >= 1


def test_historical_statement_cases_are_absent(client, seed, db_session, run):
    card = _card(seed, db_session, live=False)
    _submit(db_session, run, card, [_line(1, DAY, "神秘", 77)])
    assert _items(_inbox(client)) == []


def test_amount_differs_hides_accept_on_a_transfer_entry(client, seed, db_session, run):
    from tests.integration.statement_fixtures import _transfer
    account = _bank(seed, db_session)
    other = seed.account("錢包")
    _transfer(seed, account, other, "879", DAY)  # the bank shows 888: a near delta on a protected transfer leg
    db_session.commit()
    _submit(db_session, run, account, [_line(1, DAY, "卡費", -888, "withdrawal")], kind="bank")
    items = [i for i in _items(_inbox(client)) if i["question"] == "amount_differs"]
    assert items and "accept_bank_amount" not in items[0]["actions"]


def test_inbox_counts_ignore_limit(client, seed, db_session, run):
    account = _bank(seed, db_session)
    _submit(db_session, run, account, [_line(n, DAY, f"店{n}", -10 * n, "withdrawal") for n in range(1, 5)], kind="bank")
    body = _inbox(client, limit=2)
    assert len(_items(body)) == 2 and body["counts"]["ask_items"] == 4


def test_amount_differs_hides_accept_on_a_group_member_and_a_scheduled_entry(client, seed, db_session, run):
    """Review Should 2: `adjustable` mirrors ledger_actions — a split member (group_id) or a scheduled entry gets no button.
    Adapted fixture: the matcher never names a group member as a near candidate (`_standalone`), so the entry is
    standalone when the statement reconciles and joins a split group (then a posted schedule instance) afterwards;
    the dirty triggers are off in tests, so the case keeps naming it."""
    account = _bank(seed, db_session)
    entry = seed.entry(account, "-879", day=DAY, merchant="卡費")
    db_session.commit()
    _submit(db_session, run, account, [_line(1, DAY, "卡費", -888, "withdrawal")], kind="bank")

    def differs():
        return [i for i in _items(_inbox(client)) if i["question"] == "amount_differs"]

    assert "accept_bank_amount" in differs()[0]["actions"]  # control: the standalone entry is adjustable
    group = seed.group("split")
    entry.group_id = group.id
    db_session.commit()
    items = differs()
    assert items and "accept_bank_amount" not in items[0]["actions"]
    entry.group_id = None
    seed.instance(seed.definition([seed.line("expense", account, "-879")]), 1, DAY, status="posted", entries=[entry])
    db_session.commit()
    items = differs()
    assert items and "accept_bank_amount" not in items[0]["actions"]


def test_default_category_comes_from_a_rule_or_a_past_entry(client, seed, db_session, run):
    from app.models import ReconciliationOwnerRule
    account = _bank(seed, db_session)
    food = seed.category("飲食")
    seed.entry(account, "-100", day=date(2026, 8, 1), merchant="放款還款", category_id=food.id)
    db_session.commit()
    _submit(db_session, run, account, [_line(1, DAY, "放款還款 0903", -3865, "withdrawal")], kind="bank")
    (item,) = _items(_inbox(client), "bank_only")
    assert item["rule_hint"]["default_category_id"] == food.id
    loan = seed.category("信貸")
    db_session.add(ReconciliationOwnerRule(account_id=account.id, line_kind="withdrawal", merchant_pattern="放款還款",
                                           flow_sign="-", amount=None, action="record", params={"category_id": loan.id},
                                           enabled=False))
    db_session.commit()
    (item,) = _items(_inbox(client), "bank_only")
    assert item["rule_hint"]["default_category_id"] == loan.id  # a rule (even disabled) beats a past entry


def test_search_candidates_excludes_claimed_and_listed_entries(client, seed, db_session, run):
    """Claimed = an active StatementCoverage row (written by hand, like test_reconciliation_actions_case does for
    coverage fixtures); listed = the case's own near candidate; both must be absent from the search."""
    from app.models import StatementCoverage
    account = _bank(seed, db_session)
    near = seed.entry(account, "-3000", day=date(2026, 9, 10), merchant="近")
    far = seed.entry(account, "-3000", day=date(2026, 10, 30), merchant="遠")
    plus = seed.entry(account, "3000", day=DAY, merchant="收入")
    claimed = seed.entry(account, "-3000", day=date(2026, 9, 5), merchant="已對")
    listed = seed.entry(account, "-3860", day=DAY, merchant="近似")  # delta 5 → the case's near candidate
    db_session.commit()
    stmt, (line,), _ = _submit(db_session, run, account, [_line(1, DAY, "店", -3865, "withdrawal")], kind="bank")
    (case,) = _open_lines(db_session, stmt.id)
    assert case.kind == "amount_delta" and [c["entry_id"] for c in case.candidates] == [listed.id]
    other, (other_line,), _ = _submit(db_session, run, _bank(seed, db_session, "別戶"),
                                      [_line(1, date(2026, 9, 5), "已對", -3000, "withdrawal")], kind="bank")
    # adapted: match_kind is one of auto/manual/agent (the brief's "exact" is a match_rule value)
    db_session.add(StatementCoverage(statement_id=other.id, line_id=other_line.id, event_id=other_line.event_id,
                                     entry_id=claimed.id, role="principal", match_rule="owner", match_kind="manual",
                                     status="active", snapshot={"flow": "-3000.0000"}))
    db_session.commit()
    res = client.get(f"/reconciliation/inbox/cases/{case.id}/search", headers=S)
    assert res.status_code == 200, res.text
    ids = [c["entry_id"] for c in res.json()]
    assert near.id in ids
    assert far.id not in ids and plus.id not in ids and claimed.id not in ids and listed.id not in ids
    assert all(c["reason"] == "search" for c in res.json())
    assert client.get("/reconciliation/inbox/cases/999999/search", headers=S).status_code == 404


def test_status_filter_and_scope(client, seed, db_session, run):
    assert client.get("/reconciliation/inbox", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/reconciliation/inbox", headers=S, params={"status": "weird"}).status_code == 422
    assert client.get("/reconciliation/inbox", headers=H).status_code == 200


# --- POST /reconciliation/inbox/apply (spec §5) ---------------------------------------------------------------------

def _apply(client, cases, action, params=None, answer="答", learn=None, headers=S):
    body = {"cases": [{"case_id": c.id, "case_version": c.version} for c in cases], "action": action,
            "params": params or {}, "answer": answer}
    if learn is not None:
        body["learn"] = learn
    return client.post("/reconciliation/inbox/apply", headers=headers, json=body)


def test_apply_record_books_and_audits_the_answer(client, seed, db_session, run):
    from app.models import LedgerEntry, ReconciliationAudit
    from sqlalchemy import select
    account = _bank(seed, db_session)
    food = seed.category("飲食")
    db_session.commit()
    stmt, (line,), _ = _submit(db_session, run, account, [_line(1, DAY, "放款還款", -3865, "withdrawal")], kind="bank")
    (case,) = _open_lines(db_session, stmt.id)
    res = _apply(client, [case], "record", {"category_id": food.id}, answer="記為支出 飲食")
    assert res.status_code == 200, res.text
    (result,) = res.json()["results"]
    assert result["status"] == "applied" and result["action_id"] and res.json()["rule_id"] is None
    booked = db_session.execute(select(LedgerEntry).where(LedgerEntry.account_id == account.id)).scalars().all()
    assert [(e.kind, e.amount, e.category_id) for e in booked] == [("expense", Decimal("-3865.0000"), food.id)]
    note = db_session.execute(select(ReconciliationAudit).where(ReconciliationAudit.event == "inbox_answer")).scalar_one()
    assert note.case_id == case.id and note.action_id == result["action_id"] and note.actor == "spa"
    assert note.detail == {"answer": "記為支出 飲食", "button": "record"}


def test_apply_refuses_a_stale_version_and_keeps_the_others(client, seed, db_session, run):
    account = _bank(seed, db_session)
    stmt, lines, _ = _submit(db_session, run, account, [_line(1, DAY, "甲", -10, "withdrawal"),
                                                        _line(2, DAY, "乙", -20, "withdrawal")], kind="bank")
    a, b = sorted(_open_lines(db_session, stmt.id), key=lambda c: c.line_id)
    stale = type("Ref", (), {"id": a.id, "version": a.version + 1})
    res = _apply(client, [stale, b], "dismiss", answer="忽略")
    assert res.status_code == 200
    results = {r["case_id"]: r for r in res.json()["results"]}
    assert results[a.id]["status"] == "refused" and results[a.id]["code"] == "stale_case"
    assert results[b.id]["status"] == "applied"
    db_session.expire_all()
    assert db_session.get(ReconciliationCase, a.id).status == "open"
    assert db_session.get(ReconciliationCase, b.id).status == "dismissed"


def test_apply_batch_two_statements(client, seed, db_session, run):
    from tests.integration.statement_fixtures import OCT
    account = _bank(seed, db_session)
    s1, _, _ = _submit(db_session, run, account, [_line(1, DAY, "放款還款", -3865, "withdrawal")], kind="bank")
    s2, _, _ = _submit(db_session, run, account, [_line(1, date(2026, 10, 3), "放款還款", -3865, "withdrawal")], kind="bank", period=OCT)
    cases = _open_lines(db_session, s1.id) + _open_lines(db_session, s2.id)
    res = _apply(client, cases, "dismiss", answer="忽略")
    assert [r["status"] for r in res.json()["results"]] == ["applied", "applied"]


def test_apply_rejects_mixed_questions_and_unoffered_buttons(client, seed, db_session, run):
    account = _bank(seed, db_session)
    stmt, _, _ = _submit(db_session, run, account, [_line(1, DAY, "他行轉入", 500, "transfer_in"),
                                                    _line(2, DAY, "店", -20, "withdrawal")], kind="bank")
    cases = _open_lines(db_session, stmt.id)
    mixed = _apply(client, cases, "dismiss")
    assert mixed.status_code == 422 and "mixed questions" in mixed.text
    transfer = next(c for c in cases if (c.context or {}).get("hint") == "needs_peer")
    unoffered = _apply(client, [transfer], "record", {"category_id": 1})
    assert unoffered.status_code == 422 and "not offered for transfer" in unoffered.text


def test_apply_resend_replays_every_member(client, seed, db_session, run):
    from sqlalchemy import func, select
    from app.models import LedgerEntry, ReconciliationAudit, ReconciliationOwnerRule
    account = _bank(seed, db_session)
    food = seed.category("飲食")
    db_session.commit()
    stmt, _, _ = _submit(db_session, run, account, [_line(1, DAY, "店", -10, "withdrawal"),
                                                    _line(2, DAY, "店", -10, "withdrawal")], kind="bank")
    cases = _open_lines(db_session, stmt.id)
    first = _apply(client, cases, "record", {"category_id": food.id}, answer="記為支出 飲食", learn={"amount_exact": True})
    again = _apply(client, cases, "record", {"category_id": food.id}, answer="記為支出 飲食", learn={"amount_exact": True})
    assert first.status_code == again.status_code == 200
    assert [r["status"] for r in again.json()["results"]] == ["replayed", "replayed"]
    assert [r["action_id"] for r in again.json()["results"]] == [r["action_id"] for r in first.json()["results"]]
    assert first.json()["rule_id"] is not None
    assert again.json()["rule_id"] is None and again.json()["rule_error"] is None  # an all-replayed resend learns nothing
    assert db_session.execute(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.account_id == account.id)).scalar_one() == 2
    assert db_session.execute(select(func.count()).select_from(ReconciliationOwnerRule)).scalar_one() == 1

    def events(name):
        return db_session.execute(select(func.count()).select_from(ReconciliationAudit)
                                  .where(ReconciliationAudit.event == name)).scalar_one()

    assert events("inbox_answer") == 2 and events("action_replayed") == 2


def test_apply_closed_member_is_refused_while_the_open_one_applies(client, seed, db_session, run):
    account = _bank(seed, db_session)
    stmt, _, _ = _submit(db_session, run, account, [_line(1, DAY, "甲", -10, "withdrawal"),
                                                    _line(2, DAY, "乙", -20, "withdrawal")], kind="bank")
    a, b = sorted(_open_lines(db_session, stmt.id), key=lambda c: c.line_id)
    closed = client.post(f"/reconciliation/cases/{a.id}/dismiss", headers=S, json={"note": "elsewhere"})
    assert closed.status_code == 201
    res = _apply(client, [a, b], "dismiss", answer="忽略")  # a's version as rendered before the other session closed it
    assert res.status_code == 200
    results = {r["case_id"]: r for r in res.json()["results"]}
    assert results[a.id]["status"] == "refused" and results[a.id]["code"] == "case_closed"  # step 3b, before the engine
    assert results[b.id]["status"] == "applied"


def test_apply_needs_write_scope(client, seed, db_session, run):
    account = _bank(seed, db_session)
    stmt, _, _ = _submit(db_session, run, account, [_line(1, DAY, "店", -20, "withdrawal")], kind="bank")
    (case,) = _open_lines(db_session, stmt.id)
    assert _apply(client, [case], "dismiss", headers=H).status_code == 403


# --- learning rules (spec §6.2) -------------------------------------------------------------------------------------

def test_learn_creates_a_rule_with_the_exact_amount(client, seed, db_session, run):
    from app.models import ReconciliationOwnerRule
    from sqlalchemy import select
    account = _bank(seed, db_session)
    food = seed.category("飲食")
    db_session.commit()
    stmt, _, _ = _submit(db_session, run, account, [_line(1, DAY, "放款還款 0903", -3865, "withdrawal"),
                                                    _line(2, date(2026, 9, 25), "放款還款 0925", -3865, "withdrawal")], kind="bank")
    cases = _open_lines(db_session, stmt.id)
    res = _apply(client, cases, "record", {"category_id": food.id, "name": "信貸"}, learn={"amount_exact": True})
    assert res.status_code == 200, res.text
    rule = db_session.get(ReconciliationOwnerRule, res.json()["rule_id"])
    assert (rule.account_id, rule.line_kind, rule.merchant_pattern, rule.flow_sign) == (account.id, "withdrawal", "放款還款", "-")
    assert rule.amount == Decimal("-3865.0000") and rule.action == "record"
    assert rule.params == {"category_id": food.id, "name": "信貸"} and rule.enabled is True
    assert [r["status"] for r in res.json()["results"]] == ["applied", "applied"]
    assert client.get("/reconciliation/owner-rules", headers=S).json()[0]["id"] == rule.id


def test_learn_without_exact_amount_and_duplicate_updates(client, seed, db_session, run):
    from app.models import ReconciliationOwnerRule
    from sqlalchemy import func, select
    account = _bank(seed, db_session)
    a = seed.category("甲")
    b = seed.category("乙")
    db_session.commit()
    s1, _, _ = _submit(db_session, run, account, [_line(1, DAY, "店", -10, "withdrawal")], kind="bank")
    (c1,) = _open_lines(db_session, s1.id)
    r1 = _apply(client, [c1], "record", {"category_id": a.id}, learn={"amount_exact": False}).json()["rule_id"]
    from tests.integration.statement_fixtures import OCT
    s2, _, _ = _submit(db_session, run, account, [_line(1, date(2026, 10, 3), "店", -99, "withdrawal")], kind="bank", period=OCT)
    (c2,) = _open_lines(db_session, s2.id)
    r2 = _apply(client, [c2], "record", {"category_id": b.id}, learn={"amount_exact": False}).json()["rule_id"]
    assert r1 == r2
    assert db_session.execute(select(func.count()).select_from(ReconciliationOwnerRule)).scalar_one() == 1
    db_session.expire_all()
    assert db_session.get(ReconciliationOwnerRule, r1).params == {"category_id": b.id}
    assert db_session.get(ReconciliationOwnerRule, r1).amount is None


def test_learn_refuses_mismatched_members_before_applying(client, seed, db_session, run):
    account = _bank(seed, db_session)
    stmt, _, _ = _submit(db_session, run, account, [_line(1, DAY, "甲", -10, "withdrawal"),
                                                    _line(2, DAY, "乙", -10, "withdrawal")], kind="bank")
    cases = _open_lines(db_session, stmt.id)
    res = _apply(client, cases, "dismiss", learn={"amount_exact": True})
    assert res.status_code == 422 and "rule_pattern_mismatch" in res.text
    db_session.expire_all()
    assert all(db_session.get(ReconciliationCase, c.id).status == "open" for c in cases)


def test_learn_refuses_cross_account_and_cross_amount_members(client, seed, db_session, run):
    other = _bank(seed, db_session, "別戶")
    account = _bank(seed, db_session)
    s1, _, _ = _submit(db_session, run, account, [_line(1, DAY, "店", -10, "withdrawal")], kind="bank")
    s2, _, _ = _submit(db_session, run, other, [_line(1, DAY, "店", -10, "withdrawal")], kind="bank")
    from tests.integration.statement_fixtures import OCT
    (a,) = _open_lines(db_session, s1.id)
    (b,) = _open_lines(db_session, s2.id)

    def still_open(*cases):
        db_session.expire_all()
        return all(db_session.get(ReconciliationCase, c.id).status == "open" for c in cases)

    res = _apply(client, [a, b], "dismiss", learn={"amount_exact": True})
    assert res.status_code == 422 and "rule_pattern_mismatch" in res.text and still_open(a, b)
    s3, _, _ = _submit(db_session, run, account, [_line(1, date(2026, 10, 3), "店", -99, "withdrawal")], kind="bank", period=OCT)
    (c,) = _open_lines(db_session, s3.id)
    res = _apply(client, [a, c], "dismiss", learn={"amount_exact": True})
    assert res.status_code == 422 and "rule_pattern_mismatch" in res.text and still_open(a, c)
    assert _apply(client, [a, c], "dismiss", learn={"amount_exact": False}).status_code == 200  # same pattern, amounts may differ
    assert still_open(b)


def test_reteaching_replaces_the_action(client, seed, db_session, run):
    """Same key, new answer → the same row with the new action/params (both directions). Task 6 adds the test
    that the replacement rule actually books/dismisses the next line."""
    from app.models import ReconciliationOwnerRule
    from tests.integration.statement_fixtures import OCT
    account = _bank(seed, db_session)
    food = seed.category("飲食")
    db_session.commit()
    s1, _, _ = _submit(db_session, run, account, [_line(1, DAY, "店", -10, "withdrawal")], kind="bank")
    (c1,) = _open_lines(db_session, s1.id)
    rid = _apply(client, [c1], "dismiss", learn={"amount_exact": False}).json()["rule_id"]
    s2, _, _ = _submit(db_session, run, account, [_line(1, date(2026, 10, 3), "店", -10, "withdrawal")], kind="bank", period=OCT)
    (c2,) = _open_lines(db_session, s2.id)
    rid2 = _apply(client, [c2], "record", {"category_id": food.id}, learn={"amount_exact": False}).json()["rule_id"]
    assert rid2 == rid
    db_session.expire_all()
    rule = db_session.get(ReconciliationOwnerRule, rid)
    assert rule.action == "record" and rule.params == {"category_id": food.id}
    s3, _, _ = _submit(db_session, run, account, [_line(2, date(2026, 11, 4), "店", -10, "withdrawal")], kind="bank",
                       period=(date(2026, 11, 1), date(2026, 11, 30)))
    (c3,) = _open_lines(db_session, s3.id)
    assert _apply(client, [c3], "dismiss", learn={"amount_exact": False}).json()["rule_id"] == rid
    db_session.expire_all()
    assert db_session.get(ReconciliationOwnerRule, rid).action == "dismiss" and db_session.get(ReconciliationOwnerRule, rid).params == {}


def test_learn_null_amount_key_is_unique(client, seed, db_session, run, pg_engine):
    """Review Must 2: two sessions learning the same NULL-amount key end with one row. s2's insert waits on s1's
    uncommitted row (ON CONFLICT), so it runs on a thread while the main thread commits s1."""
    import threading
    from sqlalchemy import func, select
    from sqlalchemy.orm import Session
    from app.models import ReconciliationOwnerRule
    from app.services import owner_rule_service as ors
    account = _bank(seed, db_session)
    stmt, (line,), _ = _submit(db_session, run, account, [_line(1, DAY, "店", -10, "withdrawal")], kind="bank")
    (case,) = _open_lines(db_session, stmt.id)
    key = ors.rule_key_for(db_session, [case], [line], amount_exact=False)
    assert key.amount is None
    db_session.commit()  # end the read transaction before the two writers start
    learned: dict = {}

    def second():
        try:
            learned["s2"] = ors.learn(s2, question="bank_only", button="dismiss", key=key, params={},
                                      created_from_case_id=case.id).id
        except Exception as exc:  # surfaced by the assertion below
            learned["error"] = exc

    with Session(pg_engine) as s1, Session(pg_engine) as s2:
        learned["s1"] = ors.learn(s1, question="bank_only", button="dismiss", key=key, params={},
                                  created_from_case_id=case.id).id
        worker = threading.Thread(target=second)
        worker.start()
        worker.join(0.5)
        assert worker.is_alive()  # ON CONFLICT: s2 waits for s1's uncommitted row
        s1.commit()
        worker.join(10)
        assert not worker.is_alive() and "error" not in learned, learned.get("error")
        s2.commit()  # then updates the same row
    assert learned["s1"] == learned["s2"]
    assert db_session.execute(select(func.count()).select_from(ReconciliationOwnerRule)).scalar_one() == 1


def test_learn_refuses_empty_pattern(client, seed, db_session, run):
    account = _bank(seed, db_session)
    stmt, _, _ = _submit(db_session, run, account, [_line(1, DAY, "20260903", -10, "withdrawal")], kind="bank")
    (case,) = _open_lines(db_session, stmt.id)
    res = _apply(client, [case], "dismiss", learn={"amount_exact": True})
    assert res.status_code == 422 and "rule_pattern_empty" in res.text


def test_learn_is_skipped_when_a_member_is_refused(client, seed, db_session, run):
    account = _bank(seed, db_session)
    stmt, _, _ = _submit(db_session, run, account, [_line(1, DAY, "店", -10, "withdrawal"),
                                                    _line(2, DAY, "店", -10, "withdrawal")], kind="bank")
    a, b = _open_lines(db_session, stmt.id)
    stale = type("Ref", (), {"id": a.id, "version": a.version + 5})
    res = _apply(client, [stale, b], "dismiss", learn={"amount_exact": True})
    assert res.status_code == 200 and res.json()["rule_id"] is None


def test_accept_bank_amount_rule_has_no_amount(client, seed, db_session, run):
    from app.models import ReconciliationOwnerRule
    account = _bank(seed, db_session)
    seed.entry(account, "-879", day=DAY, merchant="卡費")
    db_session.commit()
    stmt, _, _ = _submit(db_session, run, account, [_line(1, DAY, "卡費", -888, "withdrawal")], kind="bank")
    (case,) = [c for c in _cases(db_session, stmt.id, "open") if c.kind == "amount_delta"]
    res = _apply(client, [case], "accept_bank_amount", learn={"amount_exact": True})
    assert res.status_code == 200, res.text
    rule = db_session.get(ReconciliationOwnerRule, res.json()["rule_id"])
    assert rule.action == "accept_bank_amount" and rule.amount is None and rule.params == {}


def test_apply_checks_params_up_front_and_judges_newer_members_alone(client, seed, db_session, run):
    """Task 4 review carry-overs: a missing param is a 422 before anything applies; a member at a newer version is
    left out of the mixed-question check and refused on its own as stale_case."""
    account = _bank(seed, db_session)
    stmt, _, _ = _submit(db_session, run, account, [_line(1, DAY, "他行轉入", 500, "transfer_in"),
                                                    _line(2, DAY, "店", -20, "withdrawal")], kind="bank")
    cases = _open_lines(db_session, stmt.id)
    transfer = next(c for c in cases if (c.context or {}).get("hint") == "needs_peer")
    shop = next(c for c in cases if c.id != transfer.id)
    res = _apply(client, [shop], "record", {})
    assert res.status_code == 422 and "category_id required" in res.text
    res = _apply(client, [shop], "match_candidate", {"entry_ids": {}})
    assert res.status_code == 422 and "entry_id required" in res.text
    newer = type("Ref", (), {"id": transfer.id, "version": transfer.version + 1})
    res = _apply(client, [newer, shop], "dismiss")
    assert res.status_code == 200, res.text
    assert {r["case_id"]: (r["status"], r["code"]) for r in res.json()["results"]} == {
        transfer.id: ("refused", "stale_case"), shop.id: ("applied", None)}


def test_learn_failure_after_the_members_applied_is_a_rule_error(client, seed, db_session, run, monkeypatch):
    from app.services import owner_rule_service
    from app.services.errors import CodedValidationError

    def broken(*args, **kwargs):
        raise CodedValidationError("learn", "not_learnable")

    monkeypatch.setattr(owner_rule_service, "learn", broken)
    account = _bank(seed, db_session)
    stmt, _, _ = _submit(db_session, run, account, [_line(1, DAY, "店", -10, "withdrawal")], kind="bank")
    (case,) = _open_lines(db_session, stmt.id)
    res = _apply(client, [case], "dismiss", learn={"amount_exact": True})
    assert res.status_code == 200, res.text
    assert res.json()["rule_id"] is None and res.json()["rule_error"] == "not_learnable"
    assert [r["status"] for r in res.json()["results"]] == ["applied"]
    db_session.expire_all()
    assert db_session.get(ReconciliationCase, case.id).status == "dismissed"


def test_resend_after_disabling_or_deleting_the_rule_learns_nothing(client, seed, db_session, run):
    """Task 5 review carry-over: a delayed retry whose every member replays must not re-enable a rule the owner
    disabled since, nor re-create one the owner deleted."""
    from sqlalchemy import func, select
    from app.models import ReconciliationOwnerRule
    account = _bank(seed, db_session)
    stmt, _, _ = _submit(db_session, run, account, [_line(1, DAY, "店", -10, "withdrawal")], kind="bank")
    cases = _open_lines(db_session, stmt.id)
    first = _apply(client, cases, "dismiss", answer="忽略", learn={"amount_exact": True})
    rule_id = first.json()["rule_id"]
    assert rule_id is not None
    assert client.patch(f"/reconciliation/owner-rules/{rule_id}", headers=S, json={"enabled": False}).status_code == 200
    again = _apply(client, cases, "dismiss", answer="忽略", learn={"amount_exact": True})
    assert [r["status"] for r in again.json()["results"]] == ["replayed"] and again.json()["rule_id"] is None
    db_session.expire_all()
    assert db_session.get(ReconciliationOwnerRule, rule_id).enabled is False
    assert client.delete(f"/reconciliation/owner-rules/{rule_id}", headers=S).status_code == 204
    _apply(client, cases, "dismiss", answer="忽略", learn={"amount_exact": True})
    assert db_session.execute(select(func.count()).select_from(ReconciliationOwnerRule)).scalar_one() == 0


def test_actions_list_carries_label_and_answer(client, seed, db_session, run):
    from sqlalchemy import select
    from app.models import ReconciliationOwnerRule
    from app.services import owner_rule_service as ors
    from tests.integration.statement_fixtures import OCT
    account = _bank(seed, db_session)
    food = seed.category("飲食")
    db_session.commit()
    s1, _, _ = _submit(db_session, run, account, [_line(1, DAY, "店", -10, "withdrawal")], kind="bank")
    (c1,) = _open_lines(db_session, s1.id)
    _apply(client, [c1], "record", {"category_id": food.id}, answer="記為支出 飲食", learn={"amount_exact": False})
    s2, _, _ = _submit(db_session, run, account, [_line(1, date(2026, 10, 3), "店", -10, "withdrawal")], kind="bank", period=OCT)
    ors.run_rules_safe(db_session, s2.id)
    rows = client.get("/reconciliation/actions", headers=S).json()
    by_case = {r["case_id"]: r for r in rows}
    mine = by_case[c1.id]
    assert mine["label"] == "spa" and mine["answer"] == "記為支出 飲食"
    (c2,) = _cases(db_session, s2.id, "resolved")
    rule_id = db_session.execute(select(ReconciliationOwnerRule.id)).scalar_one()
    assert by_case[c2.id]["label"] == f"rule:{rule_id}" and by_case[c2.id]["answer"] is None
