"""An entry saved with an open invoice's number links it in the same request (owner decision 2026-11-27).
Synthetic data only."""
from datetime import date

import pytest
from sqlalchemy import func, select

from app import auth
from app.models import Einvoice, EinvoiceLink, LedgerEntry
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


@pytest.mark.parametrize("state", ["unmatched", "ambiguous", "near"])
def test_entry_with_invoice_number_links_open_invoice(client, db_session, seed, state):
    enable(client)
    account = seed.account("A")
    db_session.commit()
    import_(client, invoice())  # no entry yet: unmatched
    if state != "unmatched":
        _set_state(db_session, NUMBER, state)
    out = _save(client, account.id)
    assert out["einvoice"]["number"] == NUMBER and out["einvoice"]["linked_by"] == "auto"
    assert _row(db_session).state == "linked"
    link = db_session.get(EinvoiceLink, _row(db_session).id)
    assert link.entry_id == out["id"] and link.linked_by == "auto"


def test_exact_number_wins_over_amount(client, db_session, seed):
    """The owner typed the number: the exact rule links even when the amount differs."""
    enable(client)
    account = seed.account("A")
    db_session.commit()
    import_(client, invoice())
    out = _save(client, account.id, amount="99")
    assert out["einvoice"]["number"] == NUMBER
    assert _row(db_session).state == "linked"


@pytest.mark.parametrize("state,reason", [("dismissed", "owner"), ("void", None)])
def test_closed_invoice_is_left_alone(client, db_session, seed, state, reason):
    enable(client)
    account = seed.account("A")
    db_session.commit()
    import_(client, invoice())
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


def test_entry_outside_the_invoice_period_does_not_link(client, db_session, seed):
    enable(client)
    account = seed.account("A")
    db_session.commit()
    import_(client, invoice())  # issue date 2026-10-05: period Sep–Oct
    out = _save(client, account.id, day="2026-11-02")
    assert out["einvoice"] is None
    assert _row(db_session).state == "unmatched"


def test_only_that_number_is_matched(client, db_session, seed):
    """Another open invoice that now has a candidate waits for the daily pass."""
    enable(client)
    account = seed.account("A")
    db_session.commit()
    import_(client, invoice(), invoice(number="AB00000002", total="300", items=(("測試品項二", "300"),)))
    seed.entry(account, "-300", day=date(2026, 10, 5))
    db_session.commit()
    _save(client, account.id)
    assert _row(db_session).state == "linked"
    assert _row(db_session, "AB00000002").state == "unmatched"


def test_entry_without_number_matches_nothing(client, db_session, seed):
    enable(client)
    account = seed.account("A")
    db_session.commit()
    import_(client, invoice())
    out = _save(client, account.id, number=None)
    assert out["einvoice"] is None
    assert _row(db_session).state == "unmatched"


def test_einvoices_disabled_no_link(client, db_session, seed):
    enable(client)
    account = seed.account("A")
    db_session.commit()
    import_(client, invoice())
    enable(client, enabled=False)
    out = _save(client, account.id)
    assert out["einvoice"] is None
    assert _row(db_session).state == "unmatched"
    assert db_session.get(LedgerEntry, out["id"]).invoice_number == NUMBER


def test_lock_conflict_never_fails_the_save(client, db_session, seed, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from app.services import einvoice_matcher

    def boom(db, *, numbers=None):
        raise OperationalError("SELECT 1", {}, Exception("deadlock detected"))

    enable(client)
    account = seed.account("A")
    db_session.commit()
    import_(client, invoice())
    monkeypatch.setattr(einvoice_matcher, "run_matcher", boom)
    out = _save(client, account.id)
    assert out["einvoice"] is None and out["invoice_number"] == NUMBER
    assert _row(db_session).state == "unmatched"


def test_edit_adding_the_number_does_not_link(client, db_session, seed):
    enable(client)
    account = seed.account("A")
    db_session.commit()
    import_(client, invoice())
    out = _save(client, account.id, number=None)
    body = {"account_id": account.id, "kind": "expense", "amount": "120", "entry_date": "2026-10-06",
            "invoice_number": NUMBER}
    res = client.put(f"/entries/{out['id']}", json=body)
    assert res.status_code == 200, res.text
    assert res.json()["einvoice"] is None
    assert _row(db_session).state == "unmatched"
