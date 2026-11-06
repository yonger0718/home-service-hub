from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models import Einvoice, EinvoiceItem, EinvoiceLink, EinvoiceSellerMute


def _invoice(number="AB00000001", period="11510", **extra) -> Einvoice:
    fields = dict(
        number=number, issue_date=date(2026, 10, 5), period=period, seller_tax_id="00000001",
        seller_name="測試商店股份有限公司", total_amount=Decimal("120"), status_raw="開立",
        source_drive_id="drive-fake-1",
    )
    fields.update(extra)
    return Einvoice(**fields)


def _add(db_session, *rows):
    db_session.add_all(rows)
    db_session.flush()
    return rows[0] if len(rows) == 1 else rows


def _state(db_session, invoice_id):
    db_session.expire_all()
    return db_session.get(Einvoice, invoice_id)


def test_invoice_round_trip(db_session):
    invoice = _add(db_session, _invoice())
    _add(
        db_session,
        EinvoiceItem(invoice_id=invoice.id, seq=1, name="飲料", subtotal=Decimal("50")),
        EinvoiceItem(invoice_id=invoice.id, seq=2, name="餅乾", subtotal=Decimal("70")),
    )
    db_session.commit()
    db_session.expire_all()
    row = db_session.get(Einvoice, invoice.id)
    assert row.state == "unmatched"
    assert row.is_void is False
    assert row.dismiss_reason is None
    assert row.period == "11510"
    assert row.first_seen_at is not None and row.updated_at is not None
    items = db_session.query(EinvoiceItem).filter_by(invoice_id=row.id).order_by(EinvoiceItem.seq).all()
    assert [(i.name, i.subtotal) for i in items] == [("飲料", Decimal("50")), ("餅乾", Decimal("70"))]


def test_number_unique_per_period(db_session):
    _add(db_session, _invoice())
    with pytest.raises(IntegrityError):
        _add(db_session, _invoice())
    db_session.rollback()
    _add(db_session, _invoice(period="11511"), _invoice(period="11601"))


def test_link_needs_exactly_one_target(db_session, seed):
    account = seed.account()
    entry = seed.entry(account, "-120")
    group = seed.group()
    first, second = _add(db_session, _invoice(), _invoice(number="AB00000002"))
    db_session.commit()  # the rollback between the two attempts must keep the rows they reference
    with pytest.raises(IntegrityError, match="ck_einvoice_link_one_target"):
        _add(db_session, EinvoiceLink(invoice_id=first.id, entry_id=entry.id, group_id=group.id, linked_by="owner"))
    db_session.rollback()
    with pytest.raises(IntegrityError, match="ck_einvoice_link_one_target"):
        _add(db_session, EinvoiceLink(invoice_id=second.id, linked_by="owner"))


def test_one_invoice_per_entry_and_per_group(db_session, seed):
    account = seed.account()
    entry = seed.entry(account, "-120")
    group = seed.group()
    first, second = _add(db_session, _invoice(), _invoice(number="AB00000002"))
    db_session.commit()  # a rollback below must not discard the rows
    _add(db_session, EinvoiceLink(invoice_id=first.id, entry_id=entry.id, linked_by="auto"))
    with pytest.raises(IntegrityError, match="ux_einvoice_link_entry"):
        _add(db_session, EinvoiceLink(invoice_id=second.id, entry_id=entry.id, linked_by="auto"))
    db_session.rollback()

    first, second = _add(db_session, _invoice(number="AB00000003"), _invoice(number="AB00000004"))
    _add(db_session, EinvoiceLink(invoice_id=first.id, group_id=group.id, linked_by="auto"))
    with pytest.raises(IntegrityError, match="ux_einvoice_link_group"):
        _add(db_session, EinvoiceLink(invoice_id=second.id, group_id=group.id, linked_by="auto"))


def test_dismiss_reason_only_with_dismissed(db_session):
    with pytest.raises(IntegrityError):
        _add(db_session, _invoice(state="unmatched", dismiss_reason="owner"))
    db_session.rollback()
    with pytest.raises(IntegrityError):
        _add(db_session, _invoice(state="dismissed", dismiss_reason=None))


def test_void_flag_needs_void_state(db_session):
    with pytest.raises(IntegrityError):
        _add(db_session, _invoice(is_void=True, state="unmatched"))


def _linked(db_session, target):
    invoice = _add(db_session, _invoice(state="linked"))
    kwargs = {"entry_id": target.id} if hasattr(target, "amount") else {"group_id": target.id}
    _add(db_session, EinvoiceLink(invoice_id=invoice.id, linked_by="auto", **kwargs))
    db_session.commit()
    return invoice.id


def _link_count(db_session):
    return db_session.execute(text("SELECT count(*) FROM einvoice_link")).scalar_one()


def test_entry_delete_cascades_and_trigger_unmatches(db_session, seed):
    entry = seed.entry(seed.account(), "-120")
    invoice_id = _linked(db_session, entry)
    db_session.execute(text("DELETE FROM ledger_entry"))
    db_session.commit()
    assert _link_count(db_session) == 0
    assert _state(db_session, invoice_id).state == "unmatched"


def test_group_delete_cascades_and_trigger_unmatches(db_session, seed):
    group = seed.group()
    invoice_id = _linked(db_session, group)
    db_session.execute(text("DELETE FROM entry_group"))
    db_session.commit()
    assert _link_count(db_session) == 0
    assert _state(db_session, invoice_id).state == "unmatched"


@pytest.mark.parametrize("kwargs", [{"is_void": True, "state": "void"}, {"state": "dismissed", "dismiss_reason": "owner"}])
def test_trigger_leaves_non_linked_states(db_session, seed, kwargs):
    entry = seed.entry(seed.account(), "-120")
    invoice_id = _linked(db_session, entry)
    db_session.expire_all()
    invoice = db_session.get(Einvoice, invoice_id)
    for key, value in kwargs.items():
        setattr(invoice, key, value)
    db_session.commit()
    db_session.execute(text("DELETE FROM einvoice_link"))
    db_session.commit()
    row = _state(db_session, invoice_id)
    assert (row.state, row.dismiss_reason) == (kwargs["state"], kwargs.get("dismiss_reason"))


def test_mute_tax_id_not_empty(db_session):
    with pytest.raises(IntegrityError):
        _add(db_session, EinvoiceSellerMute(seller_tax_id=""))
