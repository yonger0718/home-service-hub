"""E-invoice owner actions, seller mutes, preferences and the runbook dismiss (spec §5, §7.5-§7.7, §8, §14).
Synthetic data only."""
import queue
import threading
from datetime import date

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app import auth
from app.models import Einvoice, EinvoiceLink, EinvoiceSellerMute, LedgerEntry
from app.services import einvoice_service
from app.services.entry_write_service import delete_entries_cascade, dissolve_group
from app.services.errors import CodedConflictError
from tests.helpers import _import, wait_until_blocked
from tests.integration.einvoice_fixtures import enable, import_, invoice


@pytest.fixture(autouse=True)
def _reconciliation_on(monkeypatch):
    monkeypatch.setenv(auth.FEATURE_ENV, "true")


D = date(2026, 10, 5)


def _row(db, number="AB00000001") -> Einvoice:
    db.expire_all()
    return db.execute(select(Einvoice).where(Einvoice.number == number)).scalars().one()


def _state(db, number="AB00000001") -> tuple[str, str | None]:
    row = _row(db, number)
    return row.state, row.dismiss_reason


def _link(db, number="AB00000001") -> EinvoiceLink | None:
    return db.get(EinvoiceLink, _row(db, number).id)


def _links(db) -> int:
    db.expire_all()
    return db.scalar(select(func.count()).select_from(EinvoiceLink))


def _stamp(db, entry_id) -> str | None:
    db.expire_all()
    return db.get(LedgerEntry, entry_id).invoice_number


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


def _post(client, number, action, body=None, *, status=200, db=None):
    invoice_id = _row(db, number).id
    res = client.post(f"/einvoices/{invoice_id}/{action}", json=body)
    assert res.status_code == status, res.text
    return res.json()


def _conflict(res, code):
    assert res.status_code == 409, res.text
    assert res.json()["message"] == code, res.text


def _auto_linked(client, db, seed, day=D) -> LedgerEntry:
    """Invoice AB00000001 (2026-10-05, 120) auto-linked to a fresh -120 entry dated `day`."""
    entry = seed.entry(seed.account("A"), "-120", day=day)
    db.commit()
    enable(client)
    assert import_(client, invoice())["match"]["linked"] == 1
    assert _link(db).entry_id == entry.id and _stamp(db, entry.id) == "AB00000001"
    return entry


# --- link -----------------------------------------------------------------------------------------------------

def test_owner_link_any_state_but_void(client, db_session, seed):
    import_(client, invoice(), invoice(number="AB00000002"), invoice(number="AB00000003", status="作廢"))
    a = seed.account("A")
    e1 = seed.entry(a, "-999", day=date(2026, 9, 20))
    e2 = seed.entry(a, "-888", day=date(2026, 9, 21))
    e3 = seed.entry(a, "-777", day=date(2026, 9, 22))
    db_session.commit()
    _set_state(db_session, "AB00000002", "dismissed", "owner")

    out = _post(client, "AB00000001", "link", {"entry_id": e1.id}, db=db_session)
    assert out == {"id": _row(db_session).id, "state": "linked", "dismiss_reason": None, "linked_by": "owner",
                   "entry_id": e1.id, "group_id": None}
    assert _stamp(db_session, e1.id) == "AB00000001"

    out = _post(client, "AB00000002", "link", {"entry_id": e2.id}, db=db_session)
    assert (out["state"], out["dismiss_reason"], out["linked_by"]) == ("linked", None, "owner")

    assert _row(db_session, "AB00000003").state == "void"
    res = client.post(f"/einvoices/{_row(db_session, 'AB00000003').id}/link", json={"entry_id": e3.id})
    _conflict(res, "einvoice_void")
    assert _stamp(db_session, e3.id) is None and _links(db_session) == 2


def test_link_body_needs_exactly_one_target(client, db_session, seed):
    import_(client, invoice())
    invoice_id = _row(db_session).id
    for body in ({}, {"entry_id": 1, "group_id": 1}):
        res = client.post(f"/einvoices/{invoice_id}/link", json=body)
        assert res.status_code == 422 and "target" in res.text, res.text
    assert client.post("/einvoices/999999/link", json={"entry_id": 1}).status_code == 404


def test_link_refuses_ineligible_target(client, db_session, seed):
    import_(client, invoice(), invoice(number="AB00000091", total="5", items=(("品項", "5"),)),
            invoice(number="AB00000092", total="6", items=(("品項", "6"),)))
    a = seed.account("A")
    income = seed.entry(a, "120", kind="income", day=D)
    parent = seed.entry(a, "-999", day=D)
    child = seed.entry(a, "-120", day=D, parent_entry_id=parent.id)
    stamped = seed.entry(a, "-120", day=D, invoice_number="AB00000009")
    taken = seed.entry(a, "-5", day=D)
    g = seed.group()
    member = seed.entry(a, "-100", day=D, group_id=g.id)
    seed.entry(a, "-20", day=D, group_id=g.id)
    db_session.add(EinvoiceLink(invoice_id=_row(db_session, "AB00000091").id, entry_id=taken.id, linked_by="owner"))
    db_session.add(EinvoiceLink(invoice_id=_row(db_session, "AB00000092").id, group_id=g.id, linked_by="owner"))
    db_session.commit()
    _set_state(db_session, "AB00000091", "linked")
    _set_state(db_session, "AB00000092", "linked")
    invoice_id = _row(db_session).id
    for target in (income, child, stamped, taken, member):
        _conflict(client.post(f"/einvoices/{invoice_id}/link", json={"entry_id": target.id}), "target_ineligible")
    assert _state(db_session) == ("unmatched", None) and _links(db_session) == 2


def _link_tuples(db) -> tuple[int, int, int]:
    """(inserted, updated, deleted) rows of einvoice_link as this backend counts them, pending stats included."""
    row = db.execute(text("SELECT n_tup_ins, n_tup_upd, n_tup_del FROM pg_stat_xact_user_tables "
                          "WHERE relname = 'einvoice_link'")).one()
    return tuple(row)


def test_relink_in_place_keeps_linked_and_next_run_leaves_it(client, db_session, seed):
    a_entry = _auto_linked(client, db_session, seed)
    b_entry = seed.entry(seed.account("B"), "-500", day=date(2026, 9, 2))
    db_session.commit()
    linked_at = _link(db_session).linked_at

    invoice_id = _row(db_session).id
    # The service runs in this test's transaction; a backend flushes pending table stats only between transactions,
    # so the delta of the einvoice_link counters inside one transaction is exactly what link() did.
    before, out = _link_tuples(db_session), einvoice_service.link(db_session, invoice_id, entry_id=b_entry.id,
                                                                  group_id=None)
    after = _link_tuples(db_session)
    assert tuple(a - b for a, b in zip(after, before)) == (0, 1, 0)  # one UPDATE in place, never delete + insert
    db_session.commit()
    assert (out["state"], out["linked_by"], out["entry_id"], out["group_id"]) == ("linked", "owner", b_entry.id, None)
    link = _link(db_session)
    assert link.linked_at >= linked_at  # a move to another target sets linked_at = now()
    assert (link.entry_id, link.linked_by) == (b_entry.id, "owner")
    assert _links(db_session) == 1 and _state(db_session) == ("linked", None)
    assert _stamp(db_session, a_entry.id) is None and _stamp(db_session, b_entry.id) == "AB00000001"

    out = _match(client)
    assert out["processed"] == 0
    assert _state(db_session) == ("linked", None)
    link = _link(db_session)
    assert (link.entry_id, link.linked_by) == (b_entry.id, "owner")


def test_relink_onto_own_target_is_accepted(client, db_session, seed):
    entry = _auto_linked(client, db_session, seed)
    linked_at = _link(db_session).linked_at
    out = _post(client, "AB00000001", "link", {"entry_id": entry.id}, db=db_session)
    assert (out["state"], out["linked_by"], out["entry_id"]) == ("linked", "owner", entry.id)
    assert _link(db_session).linked_at == linked_at  # same target: only linked_by changes
    assert _stamp(db_session, entry.id) == "AB00000001"


def _move_out_of_window(db, entry_id):
    """A's date moves to 2026-10-25: inside period 11509, outside the 3-day window of 2026-10-05 (an edit of a
    linked entry never unlinks it)."""
    db.execute(update(LedgerEntry).where(LedgerEntry.id == entry_id).values(entry_date=date(2026, 10, 25)))
    db.commit()


def test_relink_then_delete_b_returns_unmatched_and_a_is_clear(client, db_session, seed):
    a_entry = _auto_linked(client, db_session, seed)
    _move_out_of_window(db_session, a_entry.id)
    b_entry = seed.entry(seed.account("B"), "-500", day=date(2026, 9, 2))
    db_session.commit()
    _post(client, "AB00000001", "link", {"entry_id": b_entry.id}, db=db_session)
    assert _stamp(db_session, a_entry.id) is None

    delete_entries_cascade(db_session, [b_entry.id])
    db_session.commit()
    assert _state(db_session) == ("unmatched", None) and _links(db_session) == 0
    _match(client)
    assert _row(db_session).state in ("unmatched", "near") and _links(db_session) == 0
    assert _stamp(db_session, a_entry.id) is None


def test_relink_to_group_then_dissolve_returns_unmatched_and_a_is_clear(client, db_session, seed):
    a_entry = _auto_linked(client, db_session, seed)
    _move_out_of_window(db_session, a_entry.id)
    b = seed.account("B")
    g = seed.group()
    m1 = seed.entry(b, "-300", day=date(2026, 9, 2), group_id=g.id)
    m2 = seed.entry(b, "-200", day=date(2026, 9, 2), group_id=g.id)
    db_session.commit()
    out = _post(client, "AB00000001", "link", {"group_id": g.id}, db=db_session)
    assert (out["state"], out["linked_by"], out["entry_id"], out["group_id"]) == ("linked", "owner", None, g.id)
    assert _stamp(db_session, a_entry.id) is None
    assert _stamp(db_session, m1.id) is None and _stamp(db_session, m2.id) is None  # a group is never stamped

    db_session.expire_all()
    dissolve_group(db_session, g.id, db_session.get(LedgerEntry, m1.id), name=None, merchant=None, description=None)
    db_session.commit()
    assert _state(db_session) == ("unmatched", None) and _links(db_session) == 0
    _match(client)
    assert _row(db_session).state in ("unmatched", "near") and _links(db_session) == 0


def test_link_race_is_409_not_500(db_session, seed, pg_engine):
    import_rows = [Einvoice(number=n, issue_date=D, period="11509", seller_name="測試商店", total_amount=120,
                            status_raw="開立", state="unmatched", source_drive_id="drive-fake-1")
                   for n in ("AB00000001", "AB00000002")]
    db_session.add_all(import_rows)
    entry = seed.entry(seed.account("A"), "-120", day=D)
    db_session.commit()
    x_id, y_id, entry_id = import_rows[0].id, import_rows[1].id, entry.id

    factory = sessionmaker(bind=pg_engine, autoflush=False)
    first, second = factory(), factory()
    pids: queue.Queue = queue.Queue()
    results: queue.Queue = queue.Queue()

    def racer():
        try:
            pids.put(second.execute(text("SELECT pg_backend_pid()")).scalar_one())
            results.put(einvoice_service.link(second, y_id, entry_id=entry_id, group_id=None))
        except BaseException as exc:
            results.put(exc)

    thread = None
    try:
        einvoice_service.link(first, x_id, entry_id=entry_id, group_id=None)  # holds the lock, not committed
        first_pid = first.execute(text("SELECT pg_backend_pid()")).scalar_one()
        thread = threading.Thread(target=racer)
        thread.start()
        wait_until_blocked(pg_engine, pids.get(timeout=5.0), blocker_pid=first_pid)
        first.commit()
        thread.join(5.0)
        outcome = results.get(timeout=5.0)
    finally:
        if thread is not None:
            thread.join(5.0)
        second.rollback()
        second.close()
        first.close()
    assert not isinstance(outcome, IntegrityError), outcome
    assert isinstance(outcome, CodedConflictError) and outcome.code == "target_ineligible", outcome
    assert _link(db_session).entry_id == entry_id and _link(db_session, "AB00000002") is None
    assert _state(db_session, "AB00000002") == ("unmatched", None)


# --- unlink, dismiss, undismiss -------------------------------------------------------------------------------

def test_unlink_dismisses_and_next_run_does_not_relink(client, db_session, seed):
    entry = _auto_linked(client, db_session, seed)
    out = _post(client, "AB00000001", "unlink", db=db_session)
    assert out == {"id": _row(db_session).id, "state": "dismissed", "dismiss_reason": "owner", "linked_by": None,
                   "entry_id": None, "group_id": None}
    assert _link(db_session) is None and _stamp(db_session, entry.id) is None
    _match(client)
    assert _state(db_session) == ("dismissed", "owner") and _links(db_session) == 0
    _conflict(client.post(f"/einvoices/{_row(db_session).id}/unlink"), "not_linked")


def test_unlink_overwrites_seller_muted(client, db_session, seed):
    import_(client, invoice())
    entry = seed.entry(seed.account("A"), "-120", day=D)
    db_session.add(EinvoiceSellerMute(seller_tax_id="00000001"))
    db_session.commit()
    _set_state(db_session, "AB00000001", "dismissed", "seller_muted")
    enable(client)
    assert _match(client)["linked"] == 1 and _link(db_session).entry_id == entry.id
    out = _post(client, "AB00000001", "unlink", db=db_session)
    assert (out["state"], out["dismiss_reason"]) == ("dismissed", "owner")
    _match(client)
    assert _state(db_session) == ("dismissed", "owner") and _links(db_session) == 0


def test_dismiss_linked_removes_link_and_detail(client, db_session, seed):
    entry = _auto_linked(client, db_session, seed)
    out = _post(client, "AB00000001", "dismiss", db=db_session)
    assert (out["state"], out["dismiss_reason"], out["entry_id"]) == ("dismissed", "owner", None)
    assert _link(db_session) is None and _stamp(db_session, entry.id) is None
    assert client.get(f"/entries/{entry.id}").json()["einvoice"] is None


def test_dismiss_void_is_409(client, db_session):
    import_(client, invoice(status="作廢"))
    _conflict(client.post(f"/einvoices/{_row(db_session).id}/dismiss"), "einvoice_void")


def test_undismiss_returns_to_matching(client, db_session, seed):
    import_(client, invoice())
    _post(client, "AB00000001", "dismiss", db=db_session)
    entry = seed.entry(seed.account("A"), "-120", day=D)
    db_session.commit()
    enable(client)
    _match(client)
    assert _state(db_session) == ("dismissed", "owner")
    out = _post(client, "AB00000001", "undismiss", db=db_session)
    assert (out["state"], out["dismiss_reason"], out["linked_by"]) == ("unmatched", None, None)
    assert _match(client)["linked"] == 1 and _link(db_session).entry_id == entry.id
    _conflict(client.post(f"/einvoices/{_row(db_session).id}/undismiss"), "not_dismissed")


def test_undismiss_rejected_while_seller_muted(client, db_session):
    import_(client, invoice())
    db_session.add(EinvoiceSellerMute(seller_tax_id="00000001"))
    db_session.commit()
    _set_state(db_session, "AB00000001", "dismissed", "owner")
    _conflict(client.post(f"/einvoices/{_row(db_session).id}/undismiss"), "seller_muted")
    assert _state(db_session) == ("dismissed", "owner")
    _set_state(db_session, "AB00000001", "dismissed", "seller_muted")
    _conflict(client.post(f"/einvoices/{_row(db_session).id}/undismiss"), "not_dismissed")


# --- mutes ----------------------------------------------------------------------------------------------------

def _keeps_questions_and_links(db, linked_entry_id):
    """§8: a mute (or its removal) touches `unmatched` / `seller_muted` only; questions and links stay."""
    assert _state(db, "AB00000005") == ("ambiguous", None)
    assert _state(db, "AB00000006") == ("linked", None)
    assert _link(db, "AB00000006").entry_id == linked_entry_id and _stamp(db, linked_entry_id) == "AB00000006"


def test_mute_converts_unmatched_and_removal_restores(client, db_session, seed):
    import_(client, invoice(), invoice(number="AB00000002", issue_date="2026-10-08", seller_name="測試商店新名"),
            invoice(number="AB00000003"), invoice(number="AB00000004", seller_tax_id="00000002"),
            invoice(number="AB00000005", issue_date="2026-10-01"), invoice(number="AB00000006", issue_date="2026-10-01"))
    _set_state(db_session, "AB00000005", "ambiguous")
    linked = seed.entry(seed.account("A"), "-120", day=date(2026, 10, 1), invoice_number="AB00000006")
    db_session.add(EinvoiceLink(invoice_id=_row(db_session, "AB00000006").id, entry_id=linked.id, linked_by="auto"))
    db_session.commit()
    _set_state(db_session, "AB00000006", "linked")

    out = _post(client, "AB00000003", "dismiss", {"mute_seller": True}, db=db_session)
    assert (out["state"], out["dismiss_reason"]) == ("dismissed", "owner")
    assert _state(db_session, "AB00000001") == ("dismissed", "seller_muted")
    assert _state(db_session, "AB00000002") == ("dismissed", "seller_muted")
    assert _state(db_session, "AB00000004") == ("unmatched", None)
    _keeps_questions_and_links(db_session, linked.id)

    mutes = client.get("/einvoices/mutes")
    assert mutes.status_code == 200, mutes.text
    [mute] = mutes.json()
    assert (mute["seller_tax_id"], mute["seller_name"]) == ("00000001", "測試商店新名") and mute["created_at"]
    einvoice_service.create_mute(db_session, "00000001")  # idempotent: still one row
    db_session.commit()
    assert len(client.get("/einvoices/mutes").json()) == 1

    res = client.delete("/einvoices/mutes/00000001")
    assert res.status_code == 204, res.text
    assert _state(db_session, "AB00000001") == ("unmatched", None)
    assert _state(db_session, "AB00000002") == ("unmatched", None)
    assert _state(db_session, "AB00000003") == ("dismissed", "owner")
    _keeps_questions_and_links(db_session, linked.id)
    assert client.get("/einvoices/mutes").json() == []
    assert client.delete("/einvoices/mutes/00000001").status_code == 404


def test_mute_needs_tax_id(client, db_session):
    import_(client, invoice(seller_tax_id=""))
    res = client.post(f"/einvoices/{_row(db_session).id}/dismiss", json={"mute_seller": True})
    assert res.status_code == 422, res.text
    assert res.json()["detail"][0]["loc"] == ["mute_seller"]
    assert _state(db_session) == ("unmatched", None)
    assert db_session.scalar(select(func.count()).select_from(EinvoiceSellerMute)) == 0


# --- preferences ----------------------------------------------------------------------------------------------

def test_preferences_patch_merges_and_bumps(client, db_session):
    enable(client, date_window_days=5)
    before = client.get("/settings/reconciliation").json()
    prefs = client.get("/einvoices/preferences")
    assert prefs.status_code == 200, prefs.text
    assert prefs.json() == {"enabled": True, "remind_unmatched": True, "version": before["version"]}

    res = client.patch("/einvoices/preferences", json={"remind_unmatched": False})
    assert res.status_code == 200, res.text
    assert res.json() == {"enabled": True, "remind_unmatched": False, "version": before["version"] + 1}

    after = client.get("/settings/reconciliation").json()
    assert after["einvoice"] == {**before["einvoice"], "remind_unmatched": False}
    assert after["einvoice"]["date_window_days"] == 5
    assert after["version"] == before["version"] + 1
    others = ("einvoice", "version")
    assert {k: v for k, v in after.items() if k not in others} == {k: v for k, v in before.items() if k not in others}
    assert client.patch("/einvoices/preferences", json={}).status_code == 422


# --- runbook --------------------------------------------------------------------------------------------------

def test_runbook_dismiss(client, db_session, seed):
    import_(client, invoice(), invoice(number="AB00000002", issue_date="2026-10-06"),
            invoice(number="AB00000003", status="作廢"))
    entry = seed.entry(seed.account("A"), "-120", day=D)
    db_session.commit()
    refs = [{"number": "AB00000001", "issue_date": "2026-10-05"}, {"number": "AB00000002", "issue_date": "2026-10-06"},
            {"number": "AB00000099", "issue_date": "2026-10-05"}, {"number": "AB00000003", "issue_date": "2026-10-05"}]
    res = client.post("/einvoices/runbook-dismiss", json={"invoices": refs})
    assert res.status_code == 200, res.text
    assert res.json() == {"dismissed": 2, "not_found": [2, 3]}
    assert _state(db_session, "AB00000001") == ("dismissed", "runbook")
    assert _state(db_session, "AB00000002") == ("dismissed", "runbook")
    assert _state(db_session, "AB00000003") == ("void", None)

    enable(client)
    assert _match(client)["processed"] == 0
    assert _state(db_session, "AB00000001") == ("dismissed", "runbook") and _links(db_session) == 0
    _post(client, "AB00000001", "undismiss", db=db_session)
    assert _match(client)["linked"] == 1 and _link(db_session).entry_id == entry.id
    assert _state(db_session, "AB00000002") == ("dismissed", "runbook")
    assert client.post("/einvoices/runbook-dismiss", json={"invoices": []}).status_code == 422


# --- scope ----------------------------------------------------------------------------------------------------

def test_owner_routes_need_write(client, monkeypatch, db_session):
    from tests.integration.test_scope_enforcement import SCOPES, TOKENS
    monkeypatch.setenv(auth.TOKENS_ENV, TOKENS)
    monkeypatch.setenv(auth.SCOPES_ENV, SCOPES)
    hermes = {"Authorization": "Bearer hermes-token"}
    for method, path, body in (("POST", "/einvoices/1/link", {"entry_id": 1}),
                               ("PATCH", "/einvoices/preferences", {"remind_unmatched": False}),
                               ("GET", "/einvoices/mutes", None), ("GET", "/einvoices/preferences", None)):
        res = client.request(method, path, headers=hermes, json=body)
        assert res.status_code == 403, (method, path, res.text)
    assert client.get("/einvoices/mutes", headers={"Authorization": "Bearer spa-token"}).status_code == 200


def test_runbook_dismiss_needs_admin(client, monkeypatch, db_session):
    import_(client, invoice())
    monkeypatch.setenv(auth.TOKENS_ENV, "adm:adm-token,wr:wr-token")
    monkeypatch.setenv(auth.SCOPES_ENV, "adm=read,write,admin; wr=read,write")  # wr: write without admin
    body = {"invoices": [{"number": "AB00000001", "issue_date": "2026-10-05"}]}
    res = client.post("/einvoices/runbook-dismiss", headers={"Authorization": "Bearer wr-token"}, json=body)
    assert res.status_code == 403 and res.json()["message"] == "scope", res.text
    assert _state(db_session) == ("unmatched", None)
    res = client.post("/einvoices/runbook-dismiss", headers={"Authorization": "Bearer adm-token"}, json=body)
    assert res.status_code == 200 and res.json() == {"dismissed": 1, "not_found": []}, res.text


# --- cascades -------------------------------------------------------------------------------------------------

def test_moze_full_replace_unmatches(client, db_session, moze):
    data = moze.csv(moze.opening("錢包", "TWD", "0"),
                    moze.row("錢包", "TWD", "支出", "-120", main="飲食", date="2026/10/06"))
    _import(db_session, data)
    enable(client)
    assert import_(client, invoice())["match"]["linked"] == 1
    first = _link(db_session).entry_id

    _import(db_session, data)  # the full replace deletes every MOZE entry: the link cascades away
    assert _state(db_session) == ("unmatched", None) and _links(db_session) == 0
    assert _match(client)["linked"] == 1
    second = _link(db_session).entry_id
    assert second != first
    assert db_session.get(LedgerEntry, second).source == "moze_import"


def test_void_then_cascade_stays_void(client, db_session, seed):
    entry = _auto_linked(client, db_session, seed)
    out = import_(client, invoice(status="作廢"))
    assert out["voided"] == 1
    assert _state(db_session) == ("void", None) and _links(db_session) == 0
    assert _stamp(db_session, entry.id) is None
    delete_entries_cascade(db_session, [entry.id])
    db_session.commit()
    assert _state(db_session) == ("void", None)


# --- a deadlock is the 409 `retry` (final review A1) ------------------------------------------------------------

def _retry(res):
    assert res.status_code == 409, res.text
    assert res.json()["message"].split(":")[0] == "retry", res.text


def _deadlock(*_args, **_kwargs):
    from psycopg2.errors import DeadlockDetected
    from sqlalchemy.exc import OperationalError
    raise OperationalError("UPDATE einvoice ...", {}, DeadlockDetected("deadlock detected"))


def test_deadlock_in_unlink_is_409_retry(client, db_session, seed, monkeypatch):
    entry = _auto_linked(client, db_session, seed)
    monkeypatch.setattr(einvoice_service, "remove_link", _deadlock)
    _retry(client.post(f"/einvoices/{_row(db_session).id}/unlink"))
    monkeypatch.undo()
    assert _state(db_session) == ("linked", None) and _link(db_session).entry_id == entry.id


def test_deadlock_in_link_is_409_retry(client, db_session, seed, monkeypatch):
    import_(client, invoice())
    entry = seed.entry(seed.account("A"), "-999", day=D)
    db_session.commit()
    monkeypatch.setattr(einvoice_service, "stamp", _deadlock)
    _retry(client.post(f"/einvoices/{_row(db_session).id}/link", json={"entry_id": entry.id}))
    assert _links(db_session) == 0 and _state(db_session) == ("unmatched", None)


def test_deadlock_in_import_is_409_retry(client, db_session, monkeypatch):
    monkeypatch.setattr(einvoice_service, "void_invoice", _deadlock)
    _retry(client.post("/einvoices/import", json={"invoices": [invoice(status="作廢")]}))
    assert db_session.scalar(select(func.count()).select_from(Einvoice)) == 0
