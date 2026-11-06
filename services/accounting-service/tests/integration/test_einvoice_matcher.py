"""E-invoice matcher (spec §7). Synthetic data only."""
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app import auth
from app.models import Einvoice, EinvoiceLink, EinvoiceSellerMute, LedgerEntry
from app.services import coverage_service
from tests.integration.einvoice_fixtures import enable, import_, invoice


@pytest.fixture(autouse=True)
def _reconciliation_on(monkeypatch):
    monkeypatch.setenv(auth.FEATURE_ENV, "true")


D = date(2026, 10, 5)


def _row(db, number="AB00000001") -> Einvoice:
    db.expire_all()
    return db.execute(select(Einvoice).where(Einvoice.number == number)).scalars().one()


def _link(db, number="AB00000001") -> EinvoiceLink | None:
    return db.get(EinvoiceLink, _row(db, number).id)


def _links(db) -> int:
    db.expire_all()
    return db.scalar(select(func.count()).select_from(EinvoiceLink))


def _set_state(db, number, state, reason=None, **extra):
    row = _row(db, number)
    row.state, row.dismiss_reason = state, reason
    for k, v in extra.items():
        setattr(row, k, v)
    db.commit()
    return row


def _match(client) -> dict:
    res = client.post("/einvoices/match")
    assert res.status_code == 200, res.text
    return res.json()


def test_unique_twd_entry_auto_links(client, db_session, seed):
    enable(client)
    entry = seed.entry(seed.account("A"), "-120", day=date(2026, 10, 6))
    db_session.commit()
    out = import_(client, invoice())
    assert out["match"]["ran"] is True and out["match"]["linked"] == 1
    assert _row(db_session).state == "linked"
    link = _link(db_session)
    assert link.entry_id == entry.id and link.group_id is None and link.linked_by == "auto"
    assert db_session.get(LedgerEntry, entry.id).invoice_number == "AB00000001"


def test_fee_and_discount_children_count_in_net(client, db_session, seed):
    enable(client)
    a = seed.account("A")
    parent = seed.entry(a, "-130", day=D)
    seed.entry(a, "-10", kind="fee", day=D, parent_entry_id=parent.id)
    seed.entry(a, "20", kind="discount", day=D, parent_entry_id=parent.id)
    db_session.commit()
    import_(client, invoice())
    assert _link(db_session).entry_id == parent.id


def test_outside_window_or_wrong_kind_never_links(client, db_session, seed):
    enable(client)
    a = seed.account("A")
    seed.entry(a, "-120", day=date(2026, 10, 9))
    seed.entry(a, "120", kind="income", day=D)
    parent = seed.entry(a, "-999", day=D)
    seed.entry(a, "-120", day=D, parent_entry_id=parent.id)
    db_session.commit()
    import_(client, invoice())
    assert _row(db_session).state == "unmatched" and _links(db_session) == 0


def test_foreign_decimal_with_original_amount_links(client, db_session, seed):
    enable(client)
    entry = seed.entry(seed.account("A"), "-660", day=D, original_currency="USD", original_amount=Decimal("-20.99"))
    db_session.commit()
    import_(client, invoice(total="20.99", seller_name="Example SaaS Inc.", items=(("Plan", "20.99"),)))
    assert _row(db_session).state == "linked" and _link(db_session).entry_id == entry.id


def test_foreign_decimal_without_original_amount_is_near_by_token(client, db_session, seed):
    enable(client)
    seed.entry(seed.account("A"), "-660", day=D, merchant="EXAMPLE SAAS")
    db_session.commit()
    out = import_(client, invoice(total="20.99", seller_name="Example SaaS Inc.", items=(("Plan", "20.99"),)))
    assert out["match"]["near"] == 1
    assert _row(db_session).state == "near" and _links(db_session) == 0


def test_two_invoices_one_entry_both_ambiguous(client, db_session, seed):
    enable(client)
    seed.entry(seed.account("A"), "-120", day=D)
    db_session.commit()
    out = import_(client, invoice(), invoice(number="AB00000002"))
    assert out["match"]["ambiguous"] == 2
    assert {_row(db_session).state, _row(db_session, "AB00000002").state} == {"ambiguous"}
    assert _links(db_session) == 0


def test_group_and_single_entry_same_amount_ambiguous(client, db_session, seed):
    enable(client)
    a = seed.account("A")
    g = seed.group()
    seed.entry(a, "-150", day=D, group_id=g.id)
    seed.entry(a, "-50", day=D, group_id=g.id)
    seed.entry(a, "-200", day=D)
    db_session.commit()
    import_(client, invoice(total="200", items=(("品項", "200"),)))
    assert _row(db_session).state == "ambiguous" and _links(db_session) == 0


def test_group_members_same_amount_ambiguous(client, db_session, seed):
    enable(client)
    a = seed.account("A")
    g = seed.group()
    seed.entry(a, "-100", day=D, group_id=g.id)
    seed.entry(a, "-100", day=D, group_id=g.id)
    db_session.commit()
    import_(client, invoice(total="100", items=(("品項", "100"),)))
    assert _row(db_session).state == "ambiguous" and _links(db_session) == 0


def test_group_and_own_member_same_amount_ambiguous(client, db_session, seed):
    enable(client)
    a = seed.account("A")
    g = seed.group()
    seed.entry(a, "-200", day=D, group_id=g.id)
    seed.entry(a, "50", kind="income", day=D, group_id=g.id)
    db_session.commit()
    import_(client, invoice(total="200", items=(("品項", "200"),)))
    assert _row(db_session).state == "ambiguous" and _links(db_session) == 0


def test_two_members_of_one_group_both_link(client, db_session, seed):
    enable(client)
    a = seed.account("A")
    g = seed.group()
    m1 = seed.entry(a, "-150", day=D, group_id=g.id)
    m2 = seed.entry(a, "-70", day=D, group_id=g.id)
    db_session.commit()
    out = import_(client, invoice(total="150", items=(("品項", "150"),)),
                  invoice(number="AB00000002", total="70", items=(("品項", "70"),)))
    assert out["match"]["linked"] == 2
    assert _link(db_session).entry_id == m1.id
    assert _link(db_session, "AB00000002").entry_id == m2.id


def test_matcher_integrity_error_is_retry_later(client, db_session, seed, monkeypatch):
    from app.services import einvoice_matcher

    import_(client, invoice(number="AB00000099", total="5", items=(("品項", "5"),)))
    competitor = _set_state(db_session, "AB00000099", "dismissed", "owner")
    competitor_id = competitor.id
    a = seed.account("A")
    taken = seed.entry(a, "-120", day=D)
    other = seed.entry(a, "-300", day=D)
    db_session.commit()
    enable(client)

    original = einvoice_matcher._insert_link

    def racing(db, inv, key):
        if inv.number == "AB00000001":  # a concurrent ledger write takes the target first
            db.add(EinvoiceLink(invoice_id=competitor_id, entry_id=key[1], linked_by="owner"))
            db.flush()
        return original(db, inv, key)

    monkeypatch.setattr(einvoice_matcher, "_insert_link", racing)
    out = import_(client, invoice(), invoice(number="AB00000002", total="300", items=(("品項", "300"),)))
    assert out["inserted"] == 2
    assert out["match"]["retry_later"] == 1 and out["match"]["linked"] == 1
    assert _row(db_session).state == "unmatched"
    assert _link(db_session) is None
    assert _link(db_session, "AB00000002").entry_id == other.id
    assert db_session.get(LedgerEntry, taken.id).invoice_number is None
    assert _links(db_session) == 1  # the competitor row went with the savepoint


def test_family_conflict_both_ambiguous(client, db_session, seed):
    enable(client)
    a = seed.account("A")
    g = seed.group()
    seed.entry(a, "-150", day=D, group_id=g.id)
    seed.entry(a, "-50", day=D, group_id=g.id)
    db_session.commit()
    import_(client, invoice(total="200", items=(("品項", "200"),)),
            invoice(number="AB00000002", total="150", items=(("品項", "150"),)))
    assert _row(db_session).state == "ambiguous"
    assert _row(db_session, "AB00000002").state == "ambiguous"
    assert _links(db_session) == 0


def test_whole_number_against_usd_original_never_auto_links(client, db_session, seed):
    enable(client)
    seed.entry(seed.account("A"), "-950", day=D, original_currency="USD", original_amount=Decimal("-30"))
    db_session.commit()
    import_(client, invoice(total="30", items=(("品項", "30"),)))
    assert _row(db_session).state in ("near", "unmatched") and _links(db_session) == 0


def test_twd_invoice_against_usd_account_never_auto_links(client, db_session, seed):
    enable(client)
    seed.entry(seed.account("美元帳戶", currency="USD"), "-120", day=D)
    db_session.commit()
    import_(client, invoice())
    assert _row(db_session).state != "linked" and _links(db_session) == 0


def test_exact_number_beats_amount(client, db_session, seed):
    enable(client)
    a = seed.account("A")
    stamped = seed.entry(a, "-999", day=date(2026, 10, 20), invoice_number="AB00000001")
    seed.entry(a, "-120", day=D)
    db_session.commit()
    import_(client, invoice())
    assert _link(db_session).entry_id == stamped.id


def test_exact_number_respects_period(client, db_session, seed):
    enable(client)
    stamped = seed.entry(seed.account("A"), "-120", day=date(2026, 10, 20), invoice_number="AB00000001")
    db_session.commit()
    import_(client, invoice(issue_date="2026-12-03"))
    row = _row(db_session)
    assert row.period == "11511" and row.state != "linked"
    assert db_session.scalar(select(func.count()).select_from(EinvoiceLink)
                             .where(EinvoiceLink.entry_id == stamped.id)) == 0


def test_period_edge(client, db_session, seed):
    enable(client)
    a = seed.account("A")
    inside = seed.entry(a, "-999", day=date(2026, 11, 1), invoice_number="AB00000001")
    seed.entry(a, "-999", day=date(2027, 1, 2), invoice_number="AB00000002")
    by_amount = seed.entry(a, "-120", day=date(2026, 12, 30))
    db_session.commit()
    import_(client, invoice(issue_date="2026-12-31"), invoice(number="AB00000002", issue_date="2026-12-31"))
    assert _link(db_session).entry_id == inside.id
    assert _link(db_session, "AB00000002").entry_id == by_amount.id


def test_stamped_with_other_number_is_ineligible(client, db_session, seed):
    enable(client)
    seed.entry(seed.account("A"), "-120", day=D, invoice_number="AB00000009")
    db_session.commit()
    import_(client, invoice())
    assert _row(db_session).state == "unmatched" and _links(db_session) == 0


def test_linked_group_member_and_grouped_link_ineligible(client, db_session, seed):
    import_(client, invoice(number="AB00000091", total="5", items=(("品項", "5"),)),
            invoice(number="AB00000092", total="6", items=(("品項", "6"),)))
    a = seed.account("A")
    g1 = seed.group()
    m1 = seed.entry(a, "-50", day=D, group_id=g1.id)
    seed.entry(a, "-70", day=D, group_id=g1.id)
    g2 = seed.group()
    seed.entry(a, "-300", day=D, group_id=g2.id)
    seed.entry(a, "-30", day=D, group_id=g2.id)
    db_session.add(EinvoiceLink(invoice_id=_row(db_session, "AB00000091").id, entry_id=m1.id, linked_by="owner"))
    db_session.add(EinvoiceLink(invoice_id=_row(db_session, "AB00000092").id, group_id=g2.id, linked_by="owner"))
    db_session.commit()
    _set_state(db_session, "AB00000091", "linked")
    _set_state(db_session, "AB00000092", "linked")
    enable(client)
    import_(client, invoice(total="120", items=(("品項", "120"),)),  # = g1's net: g1 has a linked member
            invoice(number="AB00000002", total="300", items=(("品項", "300"),)))  # = a member of linked g2
    assert _row(db_session).state != "linked" and _row(db_session, "AB00000002").state != "linked"
    assert _links(db_session) == 2


def test_installment_and_reward_claim_groups_ineligible(client, db_session, seed):
    enable(client)
    a = seed.account("A")
    inst = seed.group(kind="installment")
    seed.entry(a, "-100", day=D, group_id=inst.id)
    seed.entry(a, "-20", day=D, group_id=inst.id)
    claim = seed.group(kind="reward_claim")
    seed.entry(a, "-300", day=D, group_id=claim.id)
    seed.entry(a, "-100", day=D, group_id=claim.id)
    db_session.commit()
    import_(client, invoice(), invoice(number="AB00000002", total="400", items=(("品項", "400"),)))
    assert _row(db_session).state != "linked" and _row(db_session, "AB00000002").state != "linked"
    assert _links(db_session) == 0


def test_mixed_currency_group_ineligible(client, db_session, seed):
    enable(client)
    g = seed.group()
    seed.entry(seed.account("A"), "-100", day=D, group_id=g.id)
    seed.entry(seed.account("美元帳戶", currency="USD"), "-20", day=D, group_id=g.id)
    db_session.commit()
    import_(client, invoice())
    assert _row(db_session).state != "linked" and _links(db_session) == 0


def test_skips_linked_void_and_owner_dismissed(client, db_session, seed):
    numbers = ("AB00000001", "AB00000002", "AB00000003", "AB00000004")
    import_(client, *(invoice(number=n) for n in numbers))
    a = seed.account("A")
    unrelated = seed.entry(a, "-1", day=date(2026, 9, 1))
    seed.entry(a, "-120", day=D)
    db_session.add(EinvoiceLink(invoice_id=_row(db_session, numbers[0]).id, entry_id=unrelated.id, linked_by="owner"))
    db_session.commit()
    _set_state(db_session, numbers[0], "linked")
    _set_state(db_session, numbers[1], "void", is_void=True)
    _set_state(db_session, numbers[2], "dismissed", "owner")
    _set_state(db_session, numbers[3], "dismissed", "runbook")
    enable(client)
    out = _match(client)
    assert out["ran"] is True and out["processed"] == 0
    states = [(_row(db_session, n).state, _row(db_session, n).dismiss_reason) for n in numbers]
    assert states == [("linked", None), ("void", None), ("dismissed", "owner"), ("dismissed", "runbook")]
    assert _links(db_session) == 1


def test_muted_invoice_still_auto_links_or_stays(client, db_session, seed):
    import_(client, invoice(), invoice(number="AB00000002", total="777", items=(("品項", "777"),)))
    entry = seed.entry(seed.account("A"), "-120", day=D)
    db_session.add(EinvoiceSellerMute(seller_tax_id="00000001"))
    db_session.commit()
    _set_state(db_session, "AB00000001", "dismissed", "seller_muted")
    _set_state(db_session, "AB00000002", "dismissed", "seller_muted")
    enable(client)
    out = _match(client)
    assert out["processed"] == 2 and out["linked"] == 1 and out["unchanged"] == 1
    linked = _row(db_session)
    assert (linked.state, linked.dismiss_reason) == ("linked", None)
    assert _link(db_session).entry_id == entry.id
    stays = _row(db_session, "AB00000002")
    assert (stays.state, stays.dismiss_reason) == ("dismissed", "seller_muted")


def test_disabled_runs_nothing(client, db_session, seed):
    seed.entry(seed.account("A"), "-120", day=D)
    db_session.commit()
    out = import_(client, invoice())
    assert out["match"] == {"ran": False, "processed": 0, "linked": 0, "ambiguous": 0, "near": 0, "unmatched": 0,
                            "unchanged": 0, "retry_later": 0}
    assert _row(db_session).state == "unmatched" and _links(db_session) == 0


def test_match_endpoint_runs_and_is_idempotent(client, db_session, seed):
    import_(client, invoice(), invoice(number="AB00000002", total="777", items=(("品項", "777"),)))
    seed.entry(seed.account("A"), "-120", day=D)
    db_session.commit()
    enable(client)
    first = _match(client)
    assert (first["ran"], first["processed"], first["linked"], first["unchanged"]) == (True, 2, 1, 1)
    second = _match(client)
    assert (second["processed"], second["linked"], second["unchanged"]) == (1, 0, 1)
    assert _links(db_session) == 1


def test_invoice_number_is_not_structural():
    assert "invoice_number" not in coverage_service.STRUCTURAL_KEYS
