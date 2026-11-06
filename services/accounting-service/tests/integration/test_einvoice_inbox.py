"""E-invoice inbox section, bell count, target list and dismissed list (spec §8, §9, §14). Synthetic data only."""
from datetime import date

import pytest
from sqlalchemy import select

from app import auth
from app.models import Einvoice, EinvoiceSellerMute
from tests.integration.einvoice_fixtures import enable, import_, invoice


@pytest.fixture(autouse=True)
def _reconciliation_on(monkeypatch):
    monkeypatch.setenv(auth.FEATURE_ENV, "true")


D = date(2026, 10, 5)


def _row(db, number) -> Einvoice:
    db.expire_all()
    return db.execute(select(Einvoice).where(Einvoice.number == number)).scalars().one()


def _inbox(client, **params) -> dict:
    res = client.get("/reconciliation/inbox", params=params)
    assert res.status_code == 200, res.text
    return res.json()


def _seed_three(client, db_session, seed):
    """AB…1 ambiguous (two -120 entries), AB…2 near (-100 entry, total 118), AB…3 unmatched (no entry near)."""
    enable(client)
    a = seed.account("A")
    cat = seed.category("餐飲")
    sub = seed.category("早餐", parent=cat)
    seed.entry(a, "-120", day=D, name="午餐", category_id=sub.id)
    seed.entry(a, "-120", day=date(2026, 10, 6), name="晚餐")
    seed.entry(a, "-100", day=date(2026, 10, 20), name="近似", merchant="近似店")
    db_session.commit()
    import_(client,
            invoice(number="AB00000001", issue_date="2026-10-05"),
            invoice(number="AB00000002", issue_date="2026-10-21", total="104", seller_tax_id="00000002",
                    seller_name="近似店有限公司", items=(("近似品", "104"),)),
            invoice(number="AB00000003", issue_date="2026-09-01", total="77", seller_tax_id="00000003",
                    items=(("孤兒", "77"),)))


def test_section_lists_cards_with_candidates(client, db_session, seed):
    _seed_three(client, db_session, seed)
    cards = _inbox(client)["einvoices"]
    assert [(c["number"], c["state"]) for c in cards] == [
        ("AB00000001", "ambiguous"), ("AB00000002", "near"), ("AB00000003", "unmatched")]
    amb = cards[0]
    assert len(amb["candidates"]) == 2
    first = amb["candidates"][0]
    assert first["kind"] == "entry" and first["account_name"] == "A" and first["amount"] == "-120.0000"
    assert first["date"] == "2026-10-05" and first["name"] == "午餐" and first["category"] == "餐飲/早餐"
    assert first["currency"] == "TWD"
    assert amb["items"] == [{"name": "測試品項", "subtotal": "120.0000"}]
    assert len(cards[1]["candidates"]) == 1 and cards[2]["candidates"] == []
    assert cards[2]["seller_tax_id"] == "00000003" and cards[2]["status_raw"] == "開立"


def test_bell_count_with_limit_one(client, db_session, seed):
    _seed_three(client, db_session, seed)
    full, small = _inbox(client), _inbox(client, limit=1)
    assert small["counts"]["einvoice_ask"] == 2 and len(small["einvoices"]) == 1
    assert small["counts"]["ask"] == full["counts"]["ask"] == 0


def test_unmatched_reminders_not_counted(client, db_session, seed):
    enable(client)
    import_(client, *(invoice(number=f"AB0000000{i}", seller_tax_id=f"0000000{i}") for i in (1, 2, 3)))
    body = _inbox(client)
    assert body["counts"]["einvoice_ask"] == 0 and len(body["einvoices"]) == 3


def test_remind_switch_hides_reminders_only(client, db_session, seed):
    _seed_three(client, db_session, seed)
    assert client.patch("/einvoices/preferences", json={"remind_unmatched": False}).status_code == 200
    body = _inbox(client)
    assert [c["state"] for c in body["einvoices"]] == ["ambiguous", "near"]
    assert body["counts"]["einvoice_ask"] == 2


def test_muted_seller_has_no_reminder_but_keeps_questions(client, db_session, seed):
    enable(client)
    seed.entry(seed.account("A"), "-100", day=date(2026, 10, 6))
    db_session.commit()
    import_(client, invoice(number="AB00000001", total="104", items=(("品", "104"),)),
            invoice(number="AB00000002", total="55", issue_date="2026-09-01", items=(("品", "55"),)))
    db_session.add(EinvoiceSellerMute(seller_tax_id="00000001"))
    row = _row(db_session, "AB00000002")
    row.state, row.dismiss_reason = "dismissed", "seller_muted"
    db_session.commit()
    assert _row(db_session, "AB00000001").state == "near"
    cards = _inbox(client)["einvoices"]
    assert [(c["number"], c["state"]) for c in cards] == [("AB00000001", "near")]


def test_offer_mute_after_threshold(client, db_session, seed):
    enable(client)
    import_(client, *(invoice(number=f"AB0000001{i}", issue_date="2026-09-01") for i in (1, 2, 3)),
            invoice(number="AB00000020", issue_date="2026-09-02"),
            invoice(number="AB00000030", issue_date="2026-09-03", seller_tax_id=""))
    for i in (1, 2, 3):
        res = client.post(f"/einvoices/{_row(db_session, f'AB0000001{i}').id}/dismiss")
        assert res.status_code == 200, res.text
    cards = {c["number"]: c for c in _inbox(client)["einvoices"]}
    assert cards["AB00000020"]["offer_mute"] is True and cards["AB00000030"]["offer_mute"] is False
    client.put("/settings/reconciliation", json={**{k: v for k, v in client.get("/settings/reconciliation").json().items()
                                                    if k in ("account_map", "dirty_enabled")},
                                                "einvoice": {"enabled": True, "mute_prompt_after": 4}})
    assert {c["offer_mute"] for c in _inbox(client)["einvoices"]} == {False}


def test_disabled_hides_section(client, db_session, seed):
    _seed_three(client, db_session, seed)
    current = client.get("/settings/reconciliation").json()
    client.put("/settings/reconciliation", json={"account_map": current["account_map"],
                                                 "dirty_enabled": current["dirty_enabled"],
                                                 "einvoice": {"enabled": False}})
    body = _inbox(client)
    assert body["einvoices"] == [] and body["counts"]["einvoice_ask"] == 0


def test_account_filter_has_no_invoices(client, db_session, seed):
    _seed_three(client, db_session, seed)
    account_id = seed.account("B").id
    db_session.commit()
    body = _inbox(client, account_id=account_id)
    assert body["einvoices"] == [] and body["counts"]["einvoice_ask"] == 0


def test_dismissed_list_owner_and_runbook_only(client, db_session, seed):
    enable(client)
    import_(client, invoice(number="AB00000001"), invoice(number="AB00000002"), invoice(number="AB00000003"),
            invoice(number="AB00000004"))
    ids = {n: _row(db_session, n).id for n in ("AB00000001", "AB00000002", "AB00000003", "AB00000004")}
    assert client.post(f"/einvoices/{ids['AB00000001']}/dismiss").status_code == 200
    db_session.commit()
    for number, reason in (("AB00000002", "runbook"), ("AB00000003", "seller_muted")):
        row = _row(db_session, number)
        row.state, row.dismiss_reason = "dismissed", reason
        db_session.commit()
    db_session.commit()
    res = client.get("/einvoices/dismissed")
    assert res.status_code == 200, res.text
    body = res.json()
    assert [(r["number"], r["dismiss_reason"]) for r in body] == [("AB00000002", "runbook"), ("AB00000001", "owner")]
    assert body[0]["items"] == [{"name": "測試品項", "subtotal": "120.0000"}]
    assert client.post(f"/einvoices/{ids['AB00000001']}/undismiss").status_code == 200
    assert [r["number"] for r in client.get("/einvoices/dismissed").json()] == ["AB00000002"]


def test_targets_lists_eligible_in_days(client, db_session, seed):
    enable(client)
    a = seed.account("A")
    far = seed.entry(a, "-500", day=date(2026, 10, 10))
    near = seed.entry(a, "-118", day=date(2026, 10, 4))
    seed.entry(a, "300", kind="income", day=D)
    db_session.commit()
    import_(client, invoice(total="120"))
    inv_id = _row(db_session, "AB00000001").id
    res = client.get(f"/einvoices/{inv_id}/targets", params={"days": 14})
    assert res.status_code == 200, res.text
    assert [(t["kind"], t["id"]) for t in res.json()] == [("entry", near.id), ("entry", far.id)]
    assert [t["id"] for t in client.get(f"/einvoices/{inv_id}/targets", params={"days": 3}).json()] == [near.id]
    assert client.get(f"/einvoices/{inv_id}/targets", params={"days": 61}).status_code == 422
    assert client.get("/einvoices/99999/targets").status_code == 404


def test_targets_of_void_invoice_is_409(client, db_session, seed):
    enable(client)
    import_(client, invoice())
    row = _row(db_session, "AB00000001")
    row.state, row.is_void = "void", True
    db_session.commit()
    assert client.get(f"/einvoices/{row.id}/targets").status_code == 409


def test_section_runs_one_eligible_targets_query(client, db_session, seed, monkeypatch):
    from app.services import einvoice_read
    _seed_three(client, db_session, seed)
    calls = []
    real = einvoice_read.eligible_targets
    monkeypatch.setattr(einvoice_read, "eligible_targets", lambda *a, **k: calls.append(1) or real(*a, **k))
    assert len(_inbox(client)["einvoices"]) == 3
    assert len(calls) == 1
