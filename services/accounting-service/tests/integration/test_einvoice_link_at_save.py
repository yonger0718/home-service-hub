"""An entry saved with an open invoice's number links it in the same request (owner decision 2026-11-27).
Synthetic data only."""
from datetime import date

import pytest
from sqlalchemy import func, select, text

from app import auth
from app.models import Einvoice, EinvoiceLink, LedgerEntry
from app.services import einvoice_matcher
from app.services.einvoice_service import EINVOICE_LOCK_KEY
from tests.integration.einvoice_fixtures import enable, import_, invoice


@pytest.fixture(autouse=True)
def _reconciliation_on(monkeypatch):
    monkeypatch.setenv(auth.FEATURE_ENV, "true")


NUMBER = "AB00000001"


def _row(db, number=NUMBER) -> Einvoice:
    db.expire_all()
    return db.execute(select(Einvoice).where(Einvoice.number == number)).scalars().one()


def _links(db) -> int:
    db.expire_all()
    return db.scalar(select(func.count()).select_from(EinvoiceLink))


def _set_state(db, number, state, reason=None):
    row = _row(db, number)
    row.state, row.dismiss_reason = state, reason
    db.commit()


def _save(client, account_id, *, number=NUMBER, amount="120", day="2026-10-06") -> dict:
    body = {"account_id": account_id, "kind": "expense", "amount": amount, "entry_date": day}
    if number is not None:
        body["invoice_number"] = number
    res = client.post("/entries", json=body)
    assert res.status_code == 201, res.text
    return res.json()


def _setup(client, db_session, seed, *invoices):
    enable(client)
    account = seed.account("A")
    db_session.commit()
    import_(client, *(invoices or (invoice(),)))
    return account


@pytest.mark.parametrize("state,reason", [("unmatched", None), ("ambiguous", None), ("near", None),
                                          ("dismissed", "seller_muted")])
def test_entry_with_invoice_number_links_open_invoice(client, db_session, seed, state, reason):
    account = _setup(client, db_session, seed)
    _set_state(db_session, NUMBER, state, reason)
    out = _save(client, account.id)
    assert out["einvoice"]["number"] == NUMBER and out["einvoice"]["linked_by"] == "auto"
    row = _row(db_session)
    assert (row.state, row.dismiss_reason) == ("linked", None)
    link = db_session.get(EinvoiceLink, row.id)
    assert link.entry_id == out["id"] and link.linked_by == "auto"


def test_exact_number_wins_over_amount(client, db_session, seed):
    """The owner typed the number: the exact rule links even when the amount differs."""
    account = _setup(client, db_session, seed)
    out = _save(client, account.id, amount="99")
    assert out["einvoice"]["number"] == NUMBER
    assert _row(db_session).state == "linked"


@pytest.mark.parametrize("state,reason", [("dismissed", "owner"), ("dismissed", "runbook"), ("void", None)])
def test_closed_invoice_is_left_alone(client, db_session, seed, state, reason):
    account = _setup(client, db_session, seed)
    _set_state(db_session, NUMBER, state, reason)
    out = _save(client, account.id)
    assert out["einvoice"] is None
    row = _row(db_session)
    assert (row.state, row.dismiss_reason) == (state, reason)
    assert _links(db_session) == 0


def test_invoice_linked_elsewhere_is_left_alone(client, db_session, seed):
    enable(client)
    account = seed.account("A")
    first = seed.entry(account, "-120", day=date(2026, 10, 6))
    db_session.commit()
    import_(client, invoice())  # auto-links to `first`
    assert _row(db_session).state == "linked"
    out = _save(client, account.id)
    assert out["einvoice"] is None
    assert db_session.get(EinvoiceLink, _row(db_session).id).entry_id == first.id
    assert _links(db_session) == 1


def test_number_with_no_invoice_saves(client, db_session, seed):
    account = _setup(client, db_session, seed)
    out = _save(client, account.id, number="ZZ99999999")
    assert out["einvoice"] is None and out["invoice_number"] == "ZZ99999999"
    assert _row(db_session).state == "unmatched"


def test_outside_the_period_never_falls_back_to_amount(client, db_session, seed):
    """Issued 02-28 (period Jan–Feb), saved 03-01 with the same number and amount: within the date window, but the
    save links by the exact rule only; the daily pass decides the rest."""
    account = _setup(client, db_session, seed,
                     invoice(issue_date="2026-02-28", total="85", items=(("測試品項", "85"),)))
    out = _save(client, account.id, amount="85", day="2026-03-01")
    assert out["einvoice"] is None
    assert _row(db_session).state == "unmatched" and _links(db_session) == 0


def test_shared_candidate_leaves_the_other_invoice(client, db_session, seed):
    """Two $120 invoices share one unnumbered entry (both ambiguous); saving a new entry with the first number links
    the first and leaves the second as it was."""
    enable(client)
    account = seed.account("A")
    seed.entry(account, "-120", day=date(2026, 10, 5))
    db_session.commit()
    import_(client, invoice(), invoice(number="AB00000002", seller_tax_id="00000002"))
    assert {_row(db_session).state, _row(db_session, "AB00000002").state} == {"ambiguous"}
    out = _save(client, account.id)
    assert out["einvoice"]["number"] == NUMBER
    assert _row(db_session, "AB00000002").state == "ambiguous"
    assert _links(db_session) == 1


def test_two_entries_with_the_number_wait_for_the_daily_pass(client, db_session, seed):
    account = _setup(client, db_session, seed)
    seed.entry(account, "-120", day=date(2026, 10, 7), invoice_number=NUMBER)
    db_session.commit()
    out = _save(client, account.id)
    assert out["einvoice"] is None
    assert _row(db_session).state == "unmatched" and _links(db_session) == 0


def test_only_that_number_is_matched(client, db_session, seed):
    """Another open invoice that now has a candidate waits for the daily pass."""
    account = _setup(client, db_session, seed,
                     invoice(), invoice(number="AB00000002", total="300", items=(("測試品項二", "300"),)))
    seed.entry(account, "-300", day=date(2026, 10, 5))
    db_session.commit()
    _save(client, account.id)
    assert _row(db_session).state == "linked"
    assert _row(db_session, "AB00000002").state == "unmatched"


def test_entry_without_number_matches_nothing(client, db_session, seed):
    account = _setup(client, db_session, seed)
    out = _save(client, account.id, number=None)
    assert out["einvoice"] is None
    assert _row(db_session).state == "unmatched"


def test_einvoices_disabled_no_link(client, db_session, seed):
    account = _setup(client, db_session, seed)
    enable(client, enabled=False)
    out = _save(client, account.id)
    assert out["einvoice"] is None
    assert _row(db_session).state == "unmatched"
    assert db_session.get(LedgerEntry, out["id"]).invoice_number == NUMBER


def test_busy_matcher_lock_skips_without_waiting(client, db_session, seed):
    """An import holds the matcher lock: the save neither waits nor fails; the invoice waits for the daily pass."""
    account = _setup(client, db_session, seed)
    db_session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": EINVOICE_LOCK_KEY})
    try:
        out = _save(client, account.id)
    finally:
        db_session.rollback()
    assert out["einvoice"] is None and out["invoice_number"] == NUMBER
    assert _row(db_session).state == "unmatched" and _links(db_session) == 0


def test_failure_mid_link_rolls_back_the_link_and_keeps_the_entry(client, db_session, seed, monkeypatch):
    from sqlalchemy.exc import OperationalError

    real_insert = einvoice_matcher._insert_link

    def insert_then_fail(db, inv, key):
        real_insert(db, inv, key)  # the link row is written, then the pass fails
        raise OperationalError("SELECT 1", {}, Exception("deadlock detected"))

    account = _setup(client, db_session, seed)
    monkeypatch.setattr(einvoice_matcher, "_insert_link", insert_then_fail)
    out = _save(client, account.id)
    assert out["einvoice"] is None and out["invoice_number"] == NUMBER
    assert db_session.get(LedgerEntry, out["id"]) is not None
    assert _row(db_session).state == "unmatched" and _links(db_session) == 0


def test_edit_adding_the_number_does_not_link(client, db_session, seed):
    account = _setup(client, db_session, seed)
    out = _save(client, account.id, number=None)
    body = {"account_id": account.id, "kind": "expense", "amount": "120", "entry_date": "2026-10-06",
            "invoice_number": NUMBER}
    res = client.put(f"/entries/{out['id']}", json=body)
    assert res.status_code == 200, res.text
    assert res.json()["einvoice"] is None
    assert _row(db_session).state == "unmatched"
