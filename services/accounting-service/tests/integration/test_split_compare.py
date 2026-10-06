"""split_compare: the canonical comparison that sorts a full member into unchanged / meta / financial (§1.4)."""

from datetime import date
from decimal import Decimal

import pytest

from app.models import LedgerEntry
from app.schemas.writes import EntryIn
from app.services import entry_write_service as ews
from app.services.split_compare import classify, payload_signature, stored_signature

DAY = date(2026, 9, 1)


def _body(account, **fields) -> dict:
    body = {
        "account_id": account.id, "kind": "expense", "amount": "100", "entry_date": DAY.isoformat(),
        "entry_time": "12:30:15", "name": "午餐", "tags": ["公司"], "fee": {"amount": "5"},
    }
    body.update(fields)
    return body


def _verdict(db, entry_id: int, payload: EntryIn) -> str:
    db.expire_all()
    entry = db.get(LedgerEntry, entry_id)
    return classify(stored_signature(db, entry), payload_signature(payload, entry.currency))


@pytest.fixture()
def stored(db_session, seed):
    card = seed.account("卡")
    rule = seed.rule(card)
    entry_id = ews.create_entry(db_session, EntryIn(**_body(card, reward_rule_ids=[rule.id])))
    db_session.commit()
    return card, rule, entry_id


def test_the_same_payload_is_unchanged(db_session, stored):
    card, rule, entry_id = stored

    assert _verdict(db_session, entry_id, EntryIn(**_body(card, reward_rule_ids=[rule.id]))) == "unchanged"
    # an omitted posted_date means entry_date, exactly as prepare_entry stores it
    same_posting = EntryIn(**_body(card, reward_rule_ids=[rule.id], posted_date=DAY.isoformat()))
    assert _verdict(db_session, entry_id, same_posting) == "unchanged"


@pytest.mark.parametrize(
    "change", [{"name": "晚餐"}, {"merchant": "全家"}, {"tags": []}, {"description": "備註"}],
    ids=["name", "merchant", "tags", "description"],
)
def test_metadata_fields_are_a_metadata_change(db_session, stored, change):
    card, rule, entry_id = stored

    assert _verdict(db_session, entry_id, EntryIn(**_body(card, reward_rule_ids=[rule.id], **change))) == "meta"


def test_a_project_is_metadata(db_session, seed, stored):
    card, rule, entry_id = stored
    trip = seed.project("日本行")
    db_session.commit()

    assert _verdict(db_session, entry_id, EntryIn(**_body(card, reward_rule_ids=[rule.id], project_id=trip.id))) == "meta"


@pytest.mark.parametrize(
    "change",
    [
        {"amount": "101"}, {"kind": "income"}, {"entry_date": "2026-09-02"}, {"entry_time": "12:30"},
        {"posted_date": "2026-09-05"}, {"fee": None}, {"fee": {"amount": "5", "name": "運費"}},
        {"discount": {"amount": "1"}}, {"reward_rule_ids": []}, {"invoice_number": "AB12345678"},
    ],
    ids=["amount", "kind", "date", "time-precision", "posted", "fee-removed", "fee-renamed", "discount", "rules",
         "invoice"],
)
def test_financial_fields_are_a_financial_change(db_session, stored, change):
    card, rule, entry_id = stored
    body = _body(card, reward_rule_ids=[rule.id])
    body.update(change)

    assert _verdict(db_session, entry_id, EntryIn(**body)) == "financial"


def test_account_and_category_are_financial(db_session, seed, stored):
    card, rule, entry_id = stored
    other = seed.account("別張卡")
    food = seed.category("餐飲")
    db_session.commit()

    assert _verdict(db_session, entry_id, EntryIn(**_body(other, reward_rule_ids=[rule.id]))) == "financial"
    assert _verdict(db_session, entry_id, EntryIn(**_body(card, reward_rule_ids=[rule.id], category_id=food.id))) == "financial"


def test_online_fx_echo_is_unchanged_only_against_an_online_row(db_session, seed):
    # Review Focus 5: amount=None / fx_rate=None (online FX) equals a stored fx_api row with the same original.
    card = seed.account("華航卡")
    fx = {"original_amount": Decimal("-1800"), "original_currency": "JPY", "fx_rate": Decimal("0.2163")}
    online = seed.entry(card, "-389.34", fx_source="fx_api", **fx)
    manual = seed.entry(card, "-389.34", fx_source="manual", **fx)
    db_session.commit()
    echo = {"account_id": card.id, "kind": "expense", "amount": None, "original_amount": "1800",
            "original_currency": "JPY", "entry_date": DAY.isoformat()}

    assert _verdict(db_session, online.id, EntryIn(**echo)) == "unchanged"
    assert _verdict(db_session, manual.id, EntryIn(**echo)) == "financial"
    assert _verdict(db_session, online.id, EntryIn(**{**echo, "original_amount": "1900"})) == "financial"
    assert _verdict(db_session, manual.id, EntryIn(**{**echo, "fx_rate": "0.2163"})) == "unchanged"
    assert _verdict(db_session, manual.id, EntryIn(**{**echo, "amount": "389.34"})) == "unchanged"
