from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app import auth
from app.models import Einvoice, EinvoiceItem, EinvoiceLink, LedgerEntry
from tests.integration.einvoice_fixtures import import_, invoice


@pytest.fixture(autouse=True)
def _reconciliation_on(monkeypatch):
    monkeypatch.setenv(auth.FEATURE_ENV, "true")


def _row(db, number="AB00000001") -> Einvoice:
    db.expire_all()
    return db.execute(select(Einvoice).where(Einvoice.number == number)).scalars().first()


def _link(db, seed, row, number="AB00000001"):
    account = seed.account("A")
    db.flush()
    entry = LedgerEntry(account_id=account.id, kind="expense", amount=Decimal("120"), currency="TWD", source="manual", entry_date=row.issue_date,
                        invoice_number=number)
    db.add(entry)
    db.flush()
    db.add(EinvoiceLink(invoice_id=row.id, entry_id=entry.id, linked_by="auto"))
    row.state = "linked"
    db.commit()
    return entry.id


def test_new_invoice_is_unmatched(client, db_session):
    out = import_(client, invoice(items=(("甲", "70"), ("乙", "50"))))
    assert out["inserted"] == 1 and out["match"]["ran"] is False
    row = _row(db_session)
    assert row.state == "unmatched" and row.period == "11509"
    items = db_session.execute(select(EinvoiceItem).where(EinvoiceItem.invoice_id == row.id)
                               .order_by(EinvoiceItem.seq)).scalars().all()
    assert [i.name for i in items] == ["甲", "乙"]


def test_reimport_identical_changes_nothing(client, db_session):
    import_(client, invoice())
    first = _row(db_session)
    before = (first.updated_at, first.source_drive_id)
    out = import_(client, invoice())
    assert out["unchanged"] == 1 and out["updated"] == 0
    assert (_row(db_session).updated_at, _row(db_session).source_drive_id) == before


def test_conflict_updates_fields_and_replaces_items(client, db_session):
    import_(client, invoice())
    out = import_(client, invoice(seller_name="新名稱", total="80", items=(("新品項", "80"),), source="drive-fake-2"))
    assert out["updated"] == 1
    row = _row(db_session)
    assert row.seller_name == "新名稱" and row.source_drive_id == "drive-fake-2" and row.total_amount == 80
    assert db_session.scalar(select(func.count()).select_from(EinvoiceItem).where(EinvoiceItem.invoice_id == row.id)) == 1


def test_duplicate_in_one_payload_last_wins(client, db_session):
    import_(client, invoice(total="120"), invoice(total="90", items=(("測試品項", "90"),)))
    assert db_session.scalar(select(func.count()).select_from(Einvoice)) == 1
    row = _row(db_session)
    assert row.total_amount == 90
    assert db_session.scalar(select(func.count()).select_from(EinvoiceItem)) == 1


def test_number_reuse_across_periods_is_two_rows(client, db_session):
    import_(client, invoice(issue_date="2026-10-05"), invoice(issue_date="2026-12-05"))
    assert db_session.scalar(select(func.count()).select_from(Einvoice)) == 2


def test_void_sets_state_and_removes_link(client, db_session, seed):
    import_(client, invoice())
    entry_id = _link(db_session, seed, _row(db_session))
    out = import_(client, invoice(status="作廢"))
    assert out["voided"] == 1
    row = _row(db_session)
    assert row.state == "void" and row.is_void
    assert db_session.scalar(select(func.count()).select_from(EinvoiceLink)) == 0
    assert db_session.get(LedgerEntry, entry_id).invoice_number is None


def test_void_is_sticky(client, db_session):
    import_(client, invoice())
    import_(client, invoice(status="作廢"))
    import_(client, invoice(status="開立"))
    assert _row(db_session).state == "void"


def test_allowance_keeps_link(client, db_session, seed):
    import_(client, invoice())
    _link(db_session, seed, _row(db_session))
    out = import_(client, invoice(status="折讓", total="100", items=(("測試品項", "100"),)))
    assert out["updated"] == 1
    row = _row(db_session)
    assert row.state == "linked" and row.total_amount == 100
    assert db_session.scalar(select(func.count()).select_from(EinvoiceLink)) == 1


def test_unknown_status_counted(client, db_session):
    import_(client, invoice())
    out = import_(client, invoice(status="未知"))
    assert out["unknown_status"] == 1
    assert _row(db_session).status_raw == "未知" and _row(db_session).state == "unmatched"


def test_extra_carrier_fields_refused(client, db_session):
    bad = {**invoice(), "carrier_name": "測試載具", "carrier_number": "/FAKE+01"}
    res = client.post("/einvoices/import", json={"invoices": [bad]})
    assert res.status_code == 422
    assert db_session.scalar(text("SELECT count(*) FROM einvoice")) == 0


def test_no_carrier_values_in_rows(client, db_session):
    import_(client, invoice())
    dump = "".join(db_session.scalars(text("SELECT row_to_json(e)::text FROM einvoice e")).all()
                   + db_session.scalars(text("SELECT row_to_json(i)::text FROM einvoice_item i")).all())
    assert "測試載具" not in dump and "/FAKE+01" not in dump


def test_import_needs_ingest_scope(client, monkeypatch):
    from tests.integration.test_scope_enforcement import SCOPES, TOKENS
    monkeypatch.setenv(auth.TOKENS_ENV, TOKENS)
    monkeypatch.setenv(auth.SCOPES_ENV, SCOPES)
    res = client.post("/einvoices/import", headers={"Authorization": "Bearer hermes-token"}, json={"invoices": []})
    assert res.status_code == 403
