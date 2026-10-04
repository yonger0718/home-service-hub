"""Schedule-sourced writes (spec "Schedule-sourced entries"): source = 'schedule', no category defaults, D19 bypass."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import LedgerEntry
from app.schemas.writes import ChildIn, EntryIn, SettleIn, TransferIn
from app.services import entry_write_service as ews
from app.services import settlement_service, transfer_service
from app.services.edit_lock import EditLockedError


def test_insert_prepared_with_schedule_source_does_not_remember_defaults(db_session, seed):
    card = seed.account("範例卡")
    streaming = seed.category("串流")
    prepared = ews.prepare_entry(
        db_session,
        EntryIn(
            account_id=card.id, kind="expense", amount=Decimal("390"), entry_date=date(2026, 10, 22),
            category_id=streaming.id, fee=ChildIn(amount=Decimal("5")),
        ),
    )
    entry_id = ews.insert_prepared(db_session, prepared, remember=False, source="schedule")
    entry = db_session.get(LedgerEntry, entry_id)
    child = db_session.scalar(select(LedgerEntry).where(LedgerEntry.parent_entry_id == entry_id))
    assert (entry.source, child.source) == ("schedule", "schedule")
    db_session.refresh(streaming)
    assert streaming.default_account_id is None


def test_create_transfer_with_schedule_source(db_session, seed):
    pay, broker = seed.account("薪轉"), seed.account("交割")
    invest = seed.category("投資", kind="transfer_out")
    group = transfer_service.create_transfer(
        db_session,
        TransferIn(
            from_account_id=pay.id, to_account_id=broker.id, out_amount=Decimal("15000"), entry_date=date(2026, 10, 5),
            category_id=invest.id,
        ),
        source="schedule",
        remember=False,
    )
    out_leg, in_leg = transfer_service.transfer_legs(db_session, group)
    assert (out_leg.source, in_leg.source, out_leg.amount, in_leg.amount) == (
        "schedule", "schedule", Decimal("-15000"), Decimal("15000"),
    )
    db_session.refresh(invest)
    assert invest.default_account_id is None


def test_settle_bypasses_the_cutover_lock_only_when_asked(db_session, seed):
    bank = seed.account("薪轉")
    lender = seed.counterparty("範例銀行")
    loan = seed.entry(bank, "300000", kind="payable", counterparty_id=lender.id, source="moze_backup", moze_id="R-LOAN")
    body = SettleIn(account_id=bank.id, amount=Decimal("8333"), entry_date=date(2026, 11, 9))
    with pytest.raises(EditLockedError):
        settlement_service.settle(db_session, loan.id, body)
    entry_id = settlement_service.settle(
        db_session, loan.id, body, source="schedule", check_cutover_lock=False, name="信貸 每月還款"
    )
    entry = db_session.get(LedgerEntry, entry_id)
    assert (entry.source, entry.name, entry.amount, entry.settles_entry_id, entry.counterparty_id) == (
        "schedule", "信貸 每月還款", Decimal("-8333"), loan.id, lender.id,
    )


def test_schedule_entry_editable_before_cutover_and_keeps_its_source(client, db_session, seed):
    # Spec "Schedule entry editable before cutover".
    card = seed.account("範例卡")
    entry = seed.entry(card, "-390", source="schedule", day=date(2026, 10, 22))
    db_session.commit()
    response = client.put(
        f"/entries/{entry.id}",
        json={"account_id": card.id, "kind": "expense", "amount": "390", "entry_date": "2026-10-22", "name": "Netflix 家庭"},
    )
    assert response.status_code == 200
    assert (response.json()["name"], response.json()["source"]) == ("Netflix 家庭", "schedule")
    db_session.expire_all()
    assert db_session.get(LedgerEntry, entry.id).source == "schedule"


def test_scheduled_transfer_update_keeps_its_source(client, db_session, seed):
    pay, broker = seed.account("薪轉"), seed.account("交割")
    group = transfer_service.create_transfer(
        db_session,
        TransferIn(from_account_id=pay.id, to_account_id=broker.id, out_amount=Decimal("15000"), entry_date=date(2026, 10, 5)),
        source="schedule",
        remember=False,
    )
    db_session.commit()
    response = client.put(
        f"/transfers/{group}",
        json={"from_account_id": pay.id, "to_account_id": broker.id, "out_amount": "16000", "entry_date": "2026-10-05"},
    )
    assert response.status_code == 200
    db_session.expire_all()
    legs = transfer_service.transfer_legs(db_session, group)
    assert [leg.source for leg in legs] == ["schedule", "schedule"]
    assert legs[0].amount == Decimal("-16000")
