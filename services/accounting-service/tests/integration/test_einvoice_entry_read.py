"""E-invoice on entry detail, the manual-link picker and linked-item search (spec §7.6, §10). Synthetic data only."""
from datetime import date

import pytest
from sqlalchemy import select

from app import auth
from app.models import Einvoice, EinvoiceLink
from app.services import report_rules
from app.services.report_rules import ReportFilters
from tests.integration.einvoice_fixtures import import_, invoice

D = date(2026, 10, 5)


@pytest.fixture(autouse=True)
def _reconciliation_on(monkeypatch):
    monkeypatch.setenv(auth.FEATURE_ENV, "true")


def _inv(db, number) -> Einvoice:
    db.expire_all()
    return db.execute(select(Einvoice).where(Einvoice.number == number)).scalars().one()


def _link(db, number, *, entry=None, group=None, by="auto"):
    inv = _inv(db, number)
    db.add(EinvoiceLink(invoice_id=inv.id, entry_id=entry.id if entry else None,
                        group_id=group.id if group else None, linked_by=by))
    inv.state = "linked"
    db.commit()


def _detail(client, entry_id) -> dict:
    res = client.get(f"/entries/{entry_id}")
    assert res.status_code == 200, res.text
    return res.json()


def _search(client, q, flag=True) -> dict:
    params = {"q": q, **({"include_invoice_items": "true"} if flag else {})}
    res = client.get("/entries", params=params)
    assert res.status_code == 200, res.text
    return res.json()


def _linked_entry(client, db, seed, items, number="AB00000001", day=D, amount="-120"):
    entry = seed.entry(seed.account("A"), amount, day=day, name="其他")
    db.commit()
    import_(client, invoice(number=number, items=items))
    _link(db, number, entry=entry)
    return entry


def test_detail_shows_own_invoice(client, db_session, seed):
    entry = _linked_entry(client, db_session, seed, (("測試品項", "120"),))
    out = _detail(client, entry.id)["einvoice"]
    assert out["number"] == "AB00000001" and out["seller_name"] == "測試商店股份有限公司"
    assert out["total_amount"] == "120.0000" and out["linked_by"] == "auto" and out["linked_to"] == "entry"
    assert out["items"] == [{"name": "測試品項", "subtotal": "120.0000"}]


def test_member_shows_group_invoice(client, db_session, seed):
    a = seed.account("A")
    g = seed.group()
    m1 = seed.entry(a, "-100", day=D, group_id=g.id)
    m2 = seed.entry(a, "-20", day=D, group_id=g.id)
    db_session.commit()
    import_(client, invoice())
    _link(db_session, "AB00000001", group=g, by="owner")
    for m in (m1, m2):
        out = _detail(client, m.id)["einvoice"]
        assert out["linked_to"] == "group" and out["linked_by"] == "owner" and out["number"] == "AB00000001"


def test_unlinked_entry_has_no_invoice(client, db_session, seed):
    entry = seed.entry(seed.account("A"), "-120", day=D)
    db_session.commit()
    assert _detail(client, entry.id)["einvoice"] is None


def test_search_matches_linked_item_names_with_flag(client, db_session, seed):
    entry = _linked_entry(client, db_session, seed, (("衛生紙", "120"),))
    hit = _search(client, "衛生紙")["items"]
    assert [r["id"] for r in hit] == [entry.id] and hit[0]["invoice_item_hits"] == ["衛生紙"]
    assert _search(client, "衛生紙", flag=False)["items"] == []


def test_search_matches_group_invoice_items(client, db_session, seed):
    a = seed.account("A")
    g = seed.group()
    m1 = seed.entry(a, "-100", day=D, group_id=g.id)
    m2 = seed.entry(a, "-20", day=D, group_id=g.id)
    db_session.commit()
    import_(client, invoice(items=(("衛生紙", "120"),)))
    _link(db_session, "AB00000001", group=g)
    rows = _search(client, "衛生紙")["items"]
    assert {r["id"] for r in rows} == {m1.id, m2.id}
    assert all(r["invoice_item_hits"] == ["衛生紙"] for r in rows)


def test_unlinked_invoice_items_never_match(client, db_session, seed):
    seed.entry(seed.account("A"), "-120", day=D, name="其他")
    db_session.commit()
    import_(client, invoice(items=(("衛生紙", "120"),)))
    assert _search(client, "衛生紙")["items"] == []


def test_search_literal_percent(client, db_session, seed):
    entry = _linked_entry(client, db_session, seed, (("100%純棉", "120"),))
    assert [r["id"] for r in _search(client, "100%")["items"]] == [entry.id]
    assert _search(client, "10_")["items"] == []


def test_item_hits_capped_at_three(client, db_session, seed):
    entry = _linked_entry(client, db_session, seed, tuple((f"衛生紙{i}", "24") for i in range(5)))
    [row] = _search(client, "衛生紙")["items"]
    assert row["id"] == entry.id and row["invoice_item_hits"] == ["衛生紙0", "衛生紙1", "衛生紙2"]


def test_no_hits_without_flag_or_match_on_other_field(client, db_session, seed):
    entry = _linked_entry(client, db_session, seed, (("衛生紙", "120"),))
    [row] = _search(client, "其他")["items"]
    assert row["id"] == entry.id and row["invoice_item_hits"] == []
    res = client.get("/entries")
    assert res.json()["items"][0]["invoice_item_hits"] == []


def test_reports_unchanged(client, db_session, seed, monkeypatch):
    entry = _linked_entry(client, db_session, seed, (("衛生紙", "120"),))
    from app.models import LedgerEntry
    f = ReportFilters(date_from=date(2026, 10, 1), date_to=date(2026, 10, 31), q="衛生紙")
    where = report_rules.entry_filters(db_session, f)
    assert db_session.scalars(select(LedgerEntry.id).where(*where)).all() == []
    assert entry.id  # the entry exists; only its linked item matched


def _picker(client, entry_id) -> dict:
    res = client.get("/einvoices/picker", params={"entry_id": entry_id})
    assert res.status_code == 200, res.text
    return res.json()


def test_picker_lists_unlinked_non_void_near_date(client, db_session, seed):
    entry = seed.entry(seed.account("A"), "-120", day=D)
    other = seed.entry(seed.account("B"), "-5", day=D)
    db_session.commit()
    import_(client,
            invoice(number="AB00000001", issue_date="2026-10-07", total="118"),
            invoice(number="AB00000002", issue_date="2026-10-01", total="300"),
            invoice(number="AB00000003", issue_date="2026-10-05", status="作廢"),
            invoice(number="AB00000004", issue_date="2026-10-05", total="120"),
            invoice(number="AB00000005", issue_date="2026-10-20", total="120"))
    _link(db_session, "AB00000004", entry=other)
    out = _picker(client, entry.id)
    assert out["entry_eligible"] is True and out["group_id"] is None and out["group_eligible"] is False
    assert [i["number"] for i in out["invoices"]] == ["AB00000001", "AB00000002"]
    assert out["invoices"][0]["state"] == "unmatched"
    assert out["invoices"][0]["items"] == [{"name": "測試品項", "subtotal": "120.0000"}]


def test_picker_missing_entry_is_404(client):
    assert client.get("/einvoices/picker", params={"entry_id": 999999}).status_code == 404


def test_picker_split_member(client, db_session, seed):
    a = seed.account("A")
    g = seed.group()
    m1 = seed.entry(a, "-100", day=D, group_id=g.id)
    seed.entry(a, "-20", day=D, group_id=g.id)
    db_session.commit()
    import_(client, invoice())
    out = _picker(client, m1.id)
    assert out["group_id"] == g.id and out["group_eligible"] is True and out["entry_eligible"] is True
    _link(db_session, "AB00000001", group=g)
    out = _picker(client, m1.id)
    assert out["group_id"] == g.id and out["group_eligible"] is False and out["entry_eligible"] is False
