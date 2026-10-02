"""Manual entry writes: signs, children, FX, rules, category defaults, update, delete and the cutover lock."""

import threading
import uuid
from datetime import date, time
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.models import Account, Category, EntryRewardRule, LedgerEntry
from app.schemas.writes import EntryIn, EntryUpdateIn
from app.services import edit_lock, ledger_service, moze_import_service
from app.services import entry_write_service as ews
from app.services.edit_lock import EditLockedError, assert_editable
from app.services.errors import ConflictError, NotFoundError, ValidationError
from tests.helpers import make_entry, race

DAY = date(2026, 9, 1)


def _payload(account, **fields) -> dict:
    body = {"account_id": account.id, "kind": "expense", "amount": "100", "entry_date": DAY.isoformat()}
    body.update(fields)
    return body


def _entry_in(account, **fields) -> EntryIn:
    return EntryIn(**_payload(account, **fields))


def _children(db, parent_id) -> dict[str, LedgerEntry]:
    return {c.kind: c for c in db.scalars(select(LedgerEntry).where(LedgerEntry.parent_entry_id == parent_id))}


def _balance(db, account_id) -> Decimal:
    db.expire_all()
    return ledger_service.account_balance(db, account_id)


def _error_fields(response) -> set[str]:
    return {str(error["loc"][-1]) for error in response.json()["detail"]}


def test_expense_with_fee_child(client, db_session, seed):
    card = seed.account("A", opening="1000")
    db_session.commit()

    response = client.post(
        "/entries", json=_payload(card, amount="790", entry_time="12:30", fee={"amount": "15", "name": "運費"})
    )

    assert response.status_code == 201
    body = response.json()
    assert (body["kind"], body["amount"], body["currency"]) == ("expense", "-790.0000", "TWD")
    db_session.expire_all()
    entry = db_session.get(LedgerEntry, body["id"])
    fee = _children(db_session, entry.id)["fee"]
    assert (fee.amount, fee.name, fee.parent_entry_id) == (Decimal("-15"), "運費", entry.id)
    assert (fee.account_id, fee.entry_date, fee.entry_time, fee.posted_date) == (card.id, DAY, time(12, 30), DAY)
    assert entry.source == fee.source == "manual"
    assert _balance(db_session, card.id) == Decimal("195")


def test_children_default_names_signs_category_and_posting_date(db_session, seed):
    wallet = seed.account()
    entry_id = ews.create_entry(
        db_session, _entry_in(wallet, posted_date="2026-09-05", fee={"amount": "3"}, discount={"amount": "20"})
    )
    db_session.flush()

    entry = db_session.get(LedgerEntry, entry_id)
    children = _children(db_session, entry_id)
    fee, discount = children["fee"], children["discount"]
    assert (fee.amount, fee.name, discount.amount, discount.name) == (Decimal("-3"), "手續費", Decimal("20"), "折扣")
    assert fee.posted_date == discount.posted_date == entry.posted_date == date(2026, 9, 5)
    fee_category = db_session.scalar(select(Category.id).where(Category.kind == "fee", Category.name == "手續費"))
    assert fee.category_id == fee_category


def test_posted_date_defaults_to_entry_date(db_session, seed):
    entry_id = ews.create_entry(db_session, _entry_in(seed.account()))
    assert db_session.get(LedgerEntry, entry_id).posted_date == DAY


@pytest.mark.parametrize("kind, sign", [("expense", -1), ("receivable", -1), ("income", 1), ("payable", 1)])
def test_kind_sets_the_sign(db_session, seed, kind, sign):
    alan = seed.counterparty("Alan")
    extra = {"counterparty_id": alan.id} if kind in ("receivable", "payable") else {}
    entry_id = ews.create_entry(db_session, _entry_in(seed.account(), kind=kind, amount="42.5", **extra))
    entry = db_session.get(LedgerEntry, entry_id)
    # A manual receivable / payable is never a settlement, so it always carries the sign of its kind.
    assert (entry.amount, entry.is_settlement) == (Decimal("42.5") * sign, False)


def test_more_than_four_decimals_rejected(client, db_session, seed):
    wallet = seed.account()
    db_session.commit()

    too_precise = client.post("/entries", json=_payload(wallet, amount="1.23456"))
    child_too_precise = client.post("/entries", json=_payload(wallet, fee={"amount": "0.00001"}))

    assert too_precise.status_code == 422 and "amount" in _error_fields(too_precise)
    assert child_too_precise.status_code == 422
    assert client.post("/entries", json=_payload(wallet, amount="1.2345")).status_code == 201


def test_counterparty_required_for_receivable_and_forbidden_otherwise(client, db_session, seed):
    wallet = seed.account()
    alan = seed.counterparty("Alan")
    db_session.commit()

    missing = client.post("/entries", json=_payload(wallet, kind="receivable"))
    forbidden = client.post("/entries", json=_payload(wallet, counterparty_id=alan.id))
    payable = client.post("/entries", json=_payload(wallet, kind="payable", counterparty_id=alan.id))

    assert missing.status_code == 422
    assert missing.json()["detail"] == [
        {"loc": ["counterparty_id"], "msg": "required for receivable and payable entries", "type": "value_error"}
    ]
    assert forbidden.status_code == 422 and _error_fields(forbidden) == {"counterparty_id"}
    assert payable.status_code == 201 and payable.json()["amount"] == "100.0000"


def test_category_must_match_the_kind(db_session, seed):
    salary = seed.category("薪水", kind="income")
    with pytest.raises(ValidationError) as exc:
        ews.create_entry(db_session, _entry_in(seed.account(), category_id=salary.id))
    assert exc.value.field == "category_id"


def test_reward_rules_must_belong_to_the_account_and_be_enabled_on_the_date(db_session, seed):
    card, other = seed.account("卡"), seed.account("別張卡")
    good = seed.rule(card, "一般回饋")
    refused = [
        seed.rule(other, "別人的"),
        seed.rule(card, "停用", enabled=False),
        seed.rule(card, "過期", ends_on=date(2026, 8, 31)),
        seed.rule(card, "未開始", starts_on=date(2026, 9, 2)),
    ]
    for rule in refused:
        with pytest.raises(ValidationError) as exc:
            ews.create_entry(db_session, _entry_in(card, reward_rule_ids=[rule.id]))
        assert exc.value.field == "reward_rule_ids"

    entry_id = ews.create_entry(db_session, _entry_in(card, reward_rule_ids=[good.id, good.id]))
    db_session.flush()

    linked = db_session.scalars(select(EntryRewardRule.rule_id).where(EntryRewardRule.entry_id == entry_id)).all()
    assert linked == [good.id]


def test_amount_required_without_an_original_amount(db_session, seed):
    with pytest.raises(ValidationError) as exc:
        ews.create_entry(db_session, _entry_in(seed.account(), amount=None))
    assert exc.value.field == "amount"


def test_fx_request_rate_is_manual(db_session, seed):
    card = seed.account("華航卡")
    entry_id = ews.create_entry(
        db_session, _entry_in(card, amount=None, original_amount="1800", original_currency="JPY", fx_rate="0.2")
    )
    entry = db_session.get(LedgerEntry, entry_id)
    assert (entry.amount, entry.currency, entry.original_amount, entry.original_currency) == (
        Decimal("-360"), "TWD", Decimal("-1800"), "JPY",
    )
    assert (entry.fx_rate, entry.fx_source) == (Decimal("0.2"), "manual")


def test_fx_explicit_amount_derives_the_rate(client, db_session, seed):
    # Spec: "Converted amount overridden from the card statement".
    card = seed.account("TWD 卡")
    db_session.commit()

    response = client.post(
        "/entries", json=_payload(card, amount="1166", original_amount="5390", original_currency="JPY")
    )

    assert response.status_code == 201
    entry = db_session.get(LedgerEntry, response.json()["id"])
    assert (entry.amount, entry.original_amount, entry.original_currency, entry.fx_source) == (
        Decimal("-1166"), Decimal("-5390"), "JPY", "manual",
    )
    assert abs(entry.fx_rate - Decimal("0.2163")) <= Decimal("0.0001")


def test_fx_cached_daily_rate_is_fx_api(db_session, seed):
    card = seed.account("華航卡")
    seed.fx(DAY, "JPY", "TWD", "0.2163")

    entry_id = ews.create_entry(
        db_session, _entry_in(card, amount=None, original_amount="1800", original_currency="JPY")
    )

    entry = db_session.get(LedgerEntry, entry_id)
    assert (entry.amount, entry.original_amount, entry.fx_rate, entry.fx_source) == (
        Decimal("-389.34"), Decimal("-1800"), Decimal("0.2163"), "fx_api",
    )


def test_fx_unavailable_rate_names_fx_rate(db_session, seed, fake_http):
    card = seed.account("華航卡")
    with pytest.raises(ValidationError) as exc:
        ews.create_entry(
            db_session,
            _entry_in(card, amount=None, original_amount="1800", original_currency="JPY"),
            http_get=fake_http({}),
        )
    assert exc.value.field == "fx_rate"
    assert db_session.scalars(select(LedgerEntry)).all() == []


def test_response_proposes_the_fx_fee_but_never_adds_it(client, db_session, seed):
    card = seed.account("海外卡", fx_fee_pct=Decimal("1.5"), fx_fee_rounding="round")
    plain = seed.account("一般卡")
    db_session.commit()
    foreign = _payload(card, amount="1170", original_amount="5400", original_currency="JPY")

    proposed = client.post("/entries", json=foreign).json()
    with_fee = client.post("/entries", json={**foreign, "fee": {"amount": "18", "name": "國外交易手續費"}}).json()
    domestic = client.post("/entries", json=_payload(card, amount="1170")).json()
    no_pct = client.post(
        "/entries", json=_payload(plain, amount="1170", original_amount="5400", original_currency="JPY")
    ).json()

    assert proposed["proposed_fee"] == "18"
    assert _children(db_session, proposed["id"]) == {}
    assert with_fee["proposed_fee"] is None
    assert domestic["proposed_fee"] is None
    assert no_pct["proposed_fee"] is None


def test_category_remembers_the_last_account_and_project(client, db_session, seed):
    food = seed.category("飲食")
    lunch = seed.category("午餐", parent=food)
    uni = seed.account("玉山 UNI")
    life = seed.project("生活")
    db_session.commit()

    assert client.post("/entries", json=_payload(uni, category_id=lunch.id, project_id=life.id)).status_code == 201

    db_session.expire_all()
    remembered = db_session.get(Category, lunch.id)
    assert (remembered.default_account_id, remembered.default_project_id) == (uni.id, life.id)


def test_update_replaces_fields_and_children(client, db_session, seed):
    wallet, card = seed.account("錢包"), seed.account("卡")
    entry_id = ews.create_entry(db_session, _entry_in(wallet, fee={"amount": "5"}, discount={"amount": "10"}))
    db_session.commit()
    wallet_id, card_id = wallet.id, card.id

    response = client.put(
        f"/entries/{entry_id}",
        json=_payload(card, amount="250", entry_date="2026-09-03", name="晚餐", fee={"amount": "7", "name": "運費"}),
    )

    assert response.status_code == 200
    assert (response.json()["amount"], response.json()["name"]) == ("-250.0000", "晚餐")
    db_session.expire_all()
    children = _children(db_session, entry_id)
    assert list(children) == ["fee"]
    fee = children["fee"]
    assert (fee.amount, fee.name, fee.account_id, fee.entry_date) == (Decimal("-7"), "運費", card_id, date(2026, 9, 3))
    assert _balance(db_session, wallet_id) == 0
    assert _balance(db_session, card_id) == Decimal("-257")


def test_transfer_leg_cannot_change_kind_or_account(client, db_session, seed):
    a, b = seed.account("A"), seed.account("B")
    group = uuid.uuid4()
    out_leg = seed.entry(a, "-100", kind="transfer_out", transfer_group_id=group)
    seed.entry(b, "100", kind="transfer_in", transfer_group_id=group)
    db_session.commit()

    response = client.put(f"/entries/{out_leg.id}", json=_payload(a))

    assert response.status_code == 422 and _error_fields(response) == {"kind"}


def _loan_with_collection(seed):
    """A −420 receivable to Alan with a +200 collection settling it (open amount 220)."""
    wallet = seed.account()
    alan = seed.counterparty("Alan")
    loan = seed.entry(wallet, "-420", kind="receivable", counterparty_id=alan.id, name="借款")
    collection = seed.entry(
        wallet, "200", kind="receivable", counterparty_id=alan.id, settles_entry_id=loan.id, is_settlement=True
    )
    return wallet, alan, loan, collection


def test_update_refuses_settlement_entry(client, db_session, seed):
    # Plan review P1: re-applying signed() would turn the +200 collection into −200 (open amount 620).
    wallet, alan, loan, collection = _loan_with_collection(seed)
    db_session.commit()
    loan_id, collection_id = loan.id, collection.id

    response = client.put(
        f"/entries/{collection_id}",
        json=_payload(wallet, kind="receivable", amount="200", counterparty_id=alan.id, name="改名"),
    )

    assert response.status_code == 422
    assert response.json()["detail"] == [
        {"loc": ["kind"], "msg": "settlement entries are edited by deleting and re-settling", "type": "value_error"}
    ]
    db_session.expire_all()
    assert db_session.get(LedgerEntry, collection_id).amount == Decimal("200")
    assert client.get(f"/entries/{loan_id}").json()["open_amount"] == "220.0000"


def test_update_refuses_unlinked_settlement(client, db_session, seed):
    # Plan review round 2 P1: deleting the original clears settles_entry_id; the collection is still a settlement
    # (is_settlement) and must not be re-signed to −200.
    wallet, alan, loan, collection = _loan_with_collection(seed)
    db_session.commit()
    loan_id, collection_id = loan.id, collection.id
    assert client.delete(f"/entries/{loan_id}").status_code == 204
    db_session.expire_all()
    assert db_session.get(LedgerEntry, collection_id).settles_entry_id is None

    response = client.put(
        f"/entries/{collection_id}",
        json=_payload(wallet, kind="receivable", amount="200", counterparty_id=alan.id, name="改名"),
    )

    assert response.status_code == 422
    assert response.json()["detail"] == [
        {"loc": ["kind"], "msg": "settlement entries are edited by deleting and re-settling", "type": "value_error"}
    ]
    db_session.expire_all()
    assert db_session.get(LedgerEntry, collection_id).amount == Decimal("200")


def test_update_refuses_imported_collection_without_link(client, db_session, seed, monkeypatch):
    # An imported MOZE collection (AHRecord type 5) may have no link at all; after cutover it is editable in
    # general, but still not through the sign-applying PUT.
    monkeypatch.setenv("ACCOUNTING_IMPORT_LOCKED", "true")
    wallet = seed.account()
    alan = seed.counterparty("Alan")
    collection = seed.entry(
        wallet, "200", kind="receivable", counterparty_id=alan.id, is_settlement=True, source="moze_backup"
    )
    db_session.commit()
    collection_id = collection.id

    response = client.put(
        f"/entries/{collection_id}", json=_payload(wallet, kind="receivable", amount="200", counterparty_id=alan.id)
    )

    assert response.status_code == 422 and _error_fields(response) == {"kind"}
    db_session.expire_all()
    assert db_session.get(LedgerEntry, collection_id).amount == Decimal("200")


def test_update_refuses_counterparty_change_on_settled_original(client, db_session, seed):
    wallet, alan, loan, _ = _loan_with_collection(seed)
    bob = seed.counterparty("Bob")
    db_session.commit()
    loan_id, alan_id = loan.id, alan.id

    response = client.put(
        f"/entries/{loan_id}", json=_payload(wallet, kind="receivable", amount="420", counterparty_id=bob.id)
    )

    assert response.status_code == 422 and _error_fields(response) == {"counterparty_id"}
    db_session.expire_all()
    assert db_session.get(LedgerEntry, loan_id).counterparty_id == alan_id


def test_update_blocks_until_concurrent_settlement_commits(pg_engine, db_session, seed):
    # Plan review round 2 P1: without the row lock the PUT read "no settlements" and re-wrote the original to −100
    # while a +200 collection committed against it. With it, the PUT waits and then sees the collection.
    wallet = seed.account()
    alan = seed.counterparty("Alan")
    loan = seed.entry(wallet, "-420", kind="receivable", counterparty_id=alan.id)
    db_session.commit()
    wallet_id, alan_id, loan_id = wallet.id, alan.id, loan.id

    def settle_200(db):
        # What settlement_service.settle (Task 15) does: lock the original, then insert the linked collection.
        ews.locked_entry(db, loan_id)
        make_entry(
            db, db.get(Account, wallet_id), "200", kind="receivable", entry_date=DAY,
            counterparty_id=alan_id, settles_entry_id=loan_id, is_settlement=True,
        )

    def put_amount_100(db):
        ews.update_entry(
            db, loan_id,
            EntryUpdateIn(account_id=wallet_id, kind="receivable", amount="100", counterparty_id=alan_id, entry_date=DAY),
        )

    result = race(pg_engine, settle_200, put_amount_100)

    assert isinstance(result, ValidationError) and result.field == "amount"
    db_session.expire_all()
    assert db_session.get(LedgerEntry, loan_id).amount == Decimal("-420")
    settlements = db_session.scalars(select(LedgerEntry).where(LedgerEntry.settles_entry_id == loan_id)).all()
    assert [s.amount for s in settlements] == [Decimal("200")]


def test_update_refuses_refund_entry(client, db_session, seed):
    wallet = seed.account()
    expense = seed.entry(wallet, "-1200")
    refund = seed.entry(wallet, "570", kind="refund", refunds_entry_id=expense.id)
    db_session.commit()
    refund_id = refund.id

    response = client.put(f"/entries/{refund_id}", json=_payload(wallet, amount="570"))

    assert response.status_code == 422
    assert response.json()["detail"] == [
        {"loc": ["kind"], "msg": "refund entries are edited by deleting and re-refunding", "type": "value_error"}
    ]
    db_session.expire_all()
    assert db_session.get(LedgerEntry, refund_id).amount == Decimal("570")


def test_update_refuses_amount_change_on_settled_original(client, db_session, seed):
    wallet, alan, loan, _ = _loan_with_collection(seed)
    other = seed.account("別的錢包")
    expense = seed.entry(wallet, "-1200")
    seed.entry(wallet, "570", kind="refund", refunds_entry_id=expense.id)
    db_session.commit()
    loan_id, expense_id = loan.id, expense.id

    amount = client.put(
        f"/entries/{loan_id}", json=_payload(wallet, kind="receivable", amount="500", counterparty_id=alan.id)
    )
    kind = client.put(f"/entries/{loan_id}", json=_payload(wallet, kind="expense", amount="420"))
    account = client.put(f"/entries/{expense_id}", json=_payload(other, amount="1200"))

    assert amount.status_code == 422 and _error_fields(amount) == {"amount"}
    assert kind.status_code == 422 and _error_fields(kind) == {"kind"}
    assert account.status_code == 422 and _error_fields(account) == {"account_id"}
    db_session.expire_all()
    assert (db_session.get(LedgerEntry, loan_id).amount, db_session.get(LedgerEntry, expense_id).account_id) == (
        Decimal("-420"), wallet.id,
    )


def test_open_amount_unchanged_after_editing_original_name(client, db_session, seed):
    wallet, alan, loan, _ = _loan_with_collection(seed)
    db_session.commit()
    loan_id = loan.id

    response = client.put(
        f"/entries/{loan_id}",
        json=_payload(wallet, kind="receivable", amount="420", counterparty_id=alan.id, name="午餐代墊", tags=["朋友"]),
    )

    assert response.status_code == 200
    assert (response.json()["name"], response.json()["amount"]) == ("午餐代墊", "-420.0000")
    detail = client.get(f"/entries/{loan_id}").json()
    assert (detail["open_amount"], detail["is_settled"]) == ("220.0000", False)


def test_delete_removes_children_and_the_other_transfer_leg(client, db_session, seed):
    # Spec: "Deleting a transfer leg deletes the pair".
    a, b = seed.account("A", opening="500"), seed.account("B")
    group = uuid.uuid4()
    out_leg = seed.entry(a, "-100", kind="transfer_out", transfer_group_id=group)
    seed.entry(a, "-15", kind="fee", parent_entry_id=out_leg.id)
    in_leg = seed.entry(b, "100", kind="transfer_in", transfer_group_id=group)
    db_session.commit()
    a_id, b_id, in_id = a.id, b.id, in_leg.id

    assert client.delete(f"/entries/{in_id}").status_code == 204

    db_session.expire_all()
    assert db_session.scalars(select(LedgerEntry)).all() == []
    assert (_balance(db_session, a_id), _balance(db_session, b_id)) == (Decimal("500"), Decimal("0"))


def test_reward_entries_cannot_be_deleted(client, db_session, seed):
    reward = seed.entry(seed.account(), "30", kind="reward")
    db_session.commit()

    response = client.delete(f"/entries/{reward.id}")

    assert response.status_code == 409
    assert "reward" in response.json()["message"]


def test_delete_clears_settle_and_refund_links_on_dependants(db_session, seed):
    wallet = seed.account()
    alan = seed.counterparty("Alan")
    expense = seed.entry(wallet, "-1200")
    refund = seed.entry(wallet, "570", kind="refund", refunds_entry_id=expense.id)
    loan = seed.entry(wallet, "-420", kind="receivable", counterparty_id=alan.id)
    collection = seed.entry(
        wallet, "200", kind="receivable", counterparty_id=alan.id, settles_entry_id=loan.id, is_settlement=True
    )
    expense_id, refund_id, loan_id, collection_id = expense.id, refund.id, loan.id, collection.id

    ews.delete_entry(db_session, expense_id)
    ews.delete_entry(db_session, loan_id)
    db_session.flush()
    db_session.expire_all()

    assert db_session.get(LedgerEntry, expense_id) is None
    assert db_session.get(LedgerEntry, refund_id).refunds_entry_id is None
    assert db_session.get(LedgerEntry, collection_id).settles_entry_id is None


def test_missing_entry_is_404(client, db_session, seed):
    wallet = seed.account()
    db_session.commit()
    assert client.put("/entries/999999", json=_payload(wallet)).status_code == 404
    assert client.delete("/entries/999999").status_code == 404


@pytest.mark.parametrize(
    "marks",
    [{"source": "moze_backup", "moze_id": "rec-1"}, {"source": "moze_import"}, {"source": "manual", "moze_id": "rec-2"}],
    ids=["moze_backup", "moze_import", "moze_id"],
)
def test_imported_entry_locked_until_cutover(client, db_session, seed, monkeypatch, marks):
    wallet = seed.account()
    imported = seed.entry(wallet, "-100", **marks)
    db_session.commit()
    entry_id = imported.id

    put = client.put(f"/entries/{entry_id}", json=_payload(wallet, amount="80"))
    delete = client.delete(f"/entries/{entry_id}")

    assert (put.status_code, put.json()["message"]) == (409, "locked_until_cutover")
    assert (delete.status_code, delete.json()["message"]) == (409, "locked_until_cutover")

    monkeypatch.setenv("ACCOUNTING_IMPORT_LOCKED", "true")
    edited = client.put(f"/entries/{entry_id}", json=_payload(wallet, amount="80"))

    assert edited.status_code == 200 and edited.json()["amount"] == "-80.0000"
    db_session.expire_all()
    assert db_session.get(LedgerEntry, entry_id).source == "manual"
    assert client.delete(f"/entries/{entry_id}").status_code == 204


def test_assert_editable_checks_groups_through_their_members(db_session, seed, monkeypatch):
    wallet = seed.account()
    manual_group, member_imported, tagged = seed.group(), seed.group(), seed.group(moze_id="pkg-1")
    seed.entry(wallet, "-1", group_id=manual_group.id)
    seed.entry(wallet, "-2", group_id=member_imported.id)
    seed.entry(wallet, "-3", group_id=member_imported.id, source="moze_backup", moze_id="rec-9")

    assert_editable(manual_group)
    for group in (member_imported, tagged):
        with pytest.raises(EditLockedError, match="locked_until_cutover"):
            assert_editable(group)

    monkeypatch.setenv("ACCOUNTING_IMPORT_LOCKED", "true")
    assert_editable(member_imported)
    assert_editable(tagged)


def test_import_locked_is_shared_with_the_csv_importer():
    assert moze_import_service.import_locked is edit_lock.import_locked


def test_concurrent_deletes_of_both_transfer_legs_do_not_deadlock(pg_engine, db_session, seed):
    # Task 12 review: locking the target first and the other leg in a second statement deadlocked two concurrent
    # deletes of the two legs. Both now lock the legs in one statement by ascending id, so the second waits.
    a, b = seed.account("A", opening="500"), seed.account("B")
    group = uuid.uuid4()
    out_leg = seed.entry(a, "-100", kind="transfer_out", transfer_group_id=group)
    seed.entry(a, "-15", kind="fee", parent_entry_id=out_leg.id)
    in_leg = seed.entry(b, "100", kind="transfer_in", transfer_group_id=group)
    db_session.commit()
    a_id, b_id, out_id, in_id = a.id, b.id, out_leg.id, in_leg.id

    result = race(pg_engine, lambda db: ews.delete_entry(db, out_id), lambda db: ews.delete_entry(db, in_id))

    assert result == "committed" or isinstance(result, NotFoundError), result
    db_session.expire_all()
    assert db_session.scalars(select(LedgerEntry)).all() == []
    assert (_balance(db_session, a_id), _balance(db_session, b_id)) == (Decimal("500"), Decimal("0"))


def test_concurrent_deletes_of_both_transfer_legs_reach_the_lock_together(pg_engine, db_session, seed, monkeypatch):
    """Both deletes are held at a barrier right before the leg-lock statement, so they lock at the same time.

    This deadlocked on the pre-fix ordering (each delete locked its own leg first, then the other leg in a second
    statement: DeadlockDetected in every run). With one statement ordered by id, one delete waits for the other,
    which then finds its leg gone.
    """
    a, b = seed.account("A", opening="500"), seed.account("B")
    group = uuid.uuid4()
    out_leg = seed.entry(a, "-100", kind="transfer_out", transfer_group_id=group)
    seed.entry(a, "-15", kind="fee", parent_entry_id=out_leg.id)
    in_leg = seed.entry(b, "100", kind="transfer_in", transfer_group_id=group)
    db_session.commit()
    a_id, b_id, out_id, in_id = a.id, b.id, out_leg.id, in_leg.id

    barrier = threading.Barrier(2)
    original = ews.locked_with_legs

    def lock_together(db, entry_id, transfer_group_id):
        barrier.wait(timeout=5)
        return original(db, entry_id, transfer_group_id)

    monkeypatch.setattr(ews, "locked_with_legs", lock_together)
    factory = sessionmaker(bind=pg_engine, autoflush=False)
    outcomes: dict[int, object] = {}

    def delete(entry_id):
        session = factory()
        try:
            ews.delete_entry(session, entry_id)
            session.commit()
            outcomes[entry_id] = "committed"
        except Exception as exc:  # noqa: BLE001  (asserted below)
            session.rollback()
            outcomes[entry_id] = exc
        finally:
            session.close()

    threads = [threading.Thread(target=delete, args=(entry_id,), daemon=True) for entry_id in (out_id, in_id)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert not any(thread.is_alive() for thread in threads), "a delete is still blocked after 5 s"

    assert set(outcomes) == {out_id, in_id}
    assert not any(isinstance(o, OperationalError) for o in outcomes.values()), outcomes
    assert all(o == "committed" or isinstance(o, (NotFoundError, ConflictError)) for o in outcomes.values()), outcomes
    assert "committed" in outcomes.values(), outcomes
    db_session.expire_all()
    assert db_session.scalars(select(LedgerEntry)).all() == []
    assert (_balance(db_session, a_id), _balance(db_session, b_id)) == (Decimal("500"), Decimal("0"))


def test_put_keeps_a_link_to_a_rule_disabled_after_it_was_attached(client, db_session, seed):
    card, other = seed.account("卡"), seed.account("別張卡")
    rule, expired, foreign = seed.rule(card, "一般回饋"), seed.rule(card, "已過期"), seed.rule(other, "別人的")
    entry_id = ews.create_entry(db_session, _entry_in(card, reward_rule_ids=[rule.id, expired.id]))
    rule.is_enabled = False  # an import disabled it
    expired.ends_on = date(2026, 8, 31)
    db_session.commit()
    rule_id, expired_id, foreign_id, card_id = rule.id, expired.id, foreign.id, card.id

    renamed = client.put(f"/entries/{entry_id}", json=_payload(card, name="改名", reward_rule_ids=[rule_id, expired_id]))
    assert renamed.status_code == 200, renamed.text
    db_session.expire_all()
    linked = db_session.scalars(select(EntryRewardRule.rule_id).where(EntryRewardRule.entry_id == entry_id)).all()
    assert sorted(linked) == sorted([rule_id, expired_id])

    disabled_new = seed.rule(db_session.get(Account, card_id), "新停用", enabled=False)
    db_session.commit()
    for refused_id in (disabled_new.id, foreign_id):  # a new disabled rule and a foreign one are still refused
        response = client.put(f"/entries/{entry_id}", json=_payload(card, reward_rule_ids=[rule_id, refused_id]))
        assert response.status_code == 422, refused_id
