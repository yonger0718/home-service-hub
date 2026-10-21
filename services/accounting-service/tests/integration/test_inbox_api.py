"""Inbox read model and batch apply (spec 2026-10-21 §4–§5). Synthetic data only."""
from datetime import date

import pytest

from app import auth
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
