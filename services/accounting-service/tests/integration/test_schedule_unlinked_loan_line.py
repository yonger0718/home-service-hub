"""A repayment line without an applicable loan posts a plain expense (spec §4.0). Synthetic data only."""
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.models import LedgerEntry, ScheduleInstance
from app.services import ledger_service
from app.services.settlement_service import open_amount


def _post_next(client, definition_id):
    """Post the earliest pending instance through the public route the sheet uses (POST /schedules/instances/{id}/post)
    and return the response."""
    body = client.get(f"/schedules/definitions/{definition_id}").json()
    nxt = min((i for i in body["instances"] if i["status"] == "pending"), key=lambda i: i["seq"])
    return client.post(f"/schedules/instances/{nxt['id']}/post")


def _instance_id(db, definition_id, seq):
    db.expire_all()
    return db.scalar(
        select(ScheduleInstance.id).where(ScheduleInstance.definition_id == definition_id, ScheduleInstance.seq == seq)
    )


def test_unlinked_repayment_posts_as_expense(client, db_session, seed, today):
    today(date(2026, 10, 9))
    bank = seed.account("薪轉", opening="50000")
    lender = seed.counterparty("範例銀行")
    d = seed.definition(
        [seed.line("repayment", bank, "8333", counterparty_id=lender.id), seed.line("interest", bank, "620")],
        kind="installment", name="信貸", anchor=date(2026, 10, 8), times=36, total_amount=Decimal("300000"),
    )
    seed.instance(d, 1, date(2026, 10, 8))
    db_session.commit()
    before = ledger_service.account_balance(db_session, bank.id)
    r = _post_next(client, d.id)
    assert r.status_code in (200, 201), r.text
    db_session.expire_all()
    rows = db_session.scalars(select(LedgerEntry).where(LedgerEntry.account_id == bank.id).order_by(LedgerEntry.id)).all()
    kinds = sorted(e.kind for e in rows)
    assert kinds == ["expense", "interest"]
    exp = next(e for e in rows if e.kind == "expense")
    interest = next(e for e in rows if e.kind == "interest")
    assert interest.counterparty_id == lender.id  # no loan: the interest names the loan line's counterparty
    assert (exp.amount, exp.counterparty_id, exp.is_settlement, exp.settles_entry_id, exp.category_id, exp.source) == (
        Decimal("-8333"), lender.id, False, None, None, "schedule",
    )
    assert exp.group_id is not None and exp.group_id == next(e for e in rows if e.kind == "interest").group_id
    assert ledger_service.account_balance(db_session, bank.id) == before - Decimal("8333") - Decimal("620")


def _period_entries(db, definition_id, seq):
    db.expire_all()
    ids = db.scalar(
        select(ScheduleInstance.posted_entry_ids).where(
            ScheduleInstance.definition_id == definition_id, ScheduleInstance.seq == seq
        )
    )
    return sorted(db.scalars(select(LedgerEntry).where(LedgerEntry.id.in_(ids))).all(), key=lambda e: e.kind)


def test_loan_from_seq_boundary_repost_and_reopen(client, db_session, seed, today):
    """Periods before the boundary never touch the loan, even on repost / reopen→post; periods at/after settle it."""
    today(date(2026, 10, 9))
    bank = seed.account("薪轉", opening="500000")
    lender = seed.counterparty("範例銀行")
    payable = seed.entry(bank, "183000", kind="payable", counterparty_id=lender.id, name="信貸", day=date(2026, 10, 9))
    d = seed.definition(
        [
            seed.line("repayment", bank, "8333", loan_entry_id=payable.id, loan_from_seq=15),
            seed.line("interest", bank, "620"),
        ],
        kind="installment", name="信貸", anchor=date(2025, 9, 8), times=36, total_amount=Decimal("300000"),
    )
    seed.instance(d, 14, date(2026, 10, 8), status="pending")  # a period BEFORE the boundary, still unposted
    seed.instance(d, 15, date(2026, 11, 8), status="pending")
    db_session.commit()
    # seq 14 → plain expense, loan untouched
    r = client.post(f"/schedules/instances/{_instance_id(db_session, d.id, 14)}/post")
    assert r.status_code in (200, 201), r.text
    assert open_amount(db_session, payable) == Decimal("183000")
    # repost 14 with a changed amount → still no settlement
    r = client.post(f"/schedules/instances/{_instance_id(db_session, d.id, 14)}/repost", json={"amounts": ["8000", "620"]})
    assert r.status_code in (200, 201), r.text
    assert open_amount(db_session, payable) == Decimal("183000")
    expense, interest = _period_entries(db_session, d.id, 14)
    assert [(e.kind, e.amount, e.is_settlement) for e in (expense, interest)] == [
        ("expense", Decimal("-8000"), False), ("interest", Decimal("-620"), False),
    ]
    # reopen 14 then post again → still a plain expense, loan untouched
    r = client.post(f"/schedules/instances/{_instance_id(db_session, d.id, 14)}/reopen")
    assert r.status_code in (200, 201), r.text
    r = client.post(f"/schedules/instances/{_instance_id(db_session, d.id, 14)}/post")
    assert r.status_code in (200, 201), r.text
    assert open_amount(db_session, payable) == Decimal("183000")
    expense, interest = _period_entries(db_session, d.id, 14)
    # reopen keeps the repost's override (schedule_templates.aligned_override), so the re-post writes 8000 again
    assert [(e.kind, e.amount, e.is_settlement) for e in (expense, interest)] == [
        ("expense", Decimal("-8000"), False), ("interest", Decimal("-620"), False),
    ]
    # seq 15 → settles
    today(date(2026, 11, 8))
    r = client.post(f"/schedules/instances/{_instance_id(db_session, d.id, 15)}/post")
    assert r.status_code in (200, 201), r.text
    assert open_amount(db_session, payable) == Decimal("183000") - Decimal("8333")


def test_unlinked_collection_posts_as_income_and_payable_category_is_not_copied(client, db_session, seed, today):
    today(date(2026, 10, 9))
    bank = seed.account("薪轉")
    friend = seed.counterparty("朋友")
    cat = seed.category("借出", kind="receivable")
    d = seed.definition(
        [seed.line("collection", bank, "500", counterparty_id=friend.id, category_id=cat.id)],
        kind="installment", name="收回", anchor=date(2026, 10, 8), times=3,
    )
    seed.instance(d, 1, date(2026, 10, 8))
    db_session.commit()
    r = _post_next(client, d.id)
    assert r.status_code in (200, 201), r.text
    inc = db_session.scalar(select(LedgerEntry).where(LedgerEntry.account_id == bank.id))
    assert (inc.kind, inc.amount, inc.counterparty_id, inc.category_id) == ("income", Decimal("500"), friend.id, None)


def test_template_rules_for_loan_from_seq(client, db_session, seed):
    bank = seed.account("薪轉")
    db_session.commit()
    base = {"kind": "installment", "name": "x", "interval_unit": "month", "anchor_date": "2026-11-08", "times": 12}
    bad = client.post("/schedules/definitions", json={**base, "template": {"lines": [{"kind": "expense", "account_id": bank.id, "amount": "10", "currency": bank.currency, "loan_from_seq": 3}]}})
    assert bad.status_code == 422 and "起算期" in bad.text
    ok = client.post("/schedules/definitions", json={**base, "template": {"lines": [{"kind": "repayment", "account_id": bank.id, "amount": "10", "currency": bank.currency}]}})
    assert ok.status_code == 201, ok.text  # a repayment line without a loan is valid now
    assert ok.json()["template"]["lines"][0]["loan_entry_id"] is None


def test_loan_from_seq_round_trips_through_the_schemas(client, db_session, seed):
    bank = seed.account("薪轉")
    lender = seed.counterparty("範例銀行")
    payable = seed.entry(bank, "120000", kind="payable", counterparty_id=lender.id, name="信貸")
    db_session.commit()
    base = {"kind": "installment", "name": "x", "interval_unit": "month", "anchor_date": "2026-11-08", "times": 12}
    line = {"kind": "repayment", "account_id": bank.id, "amount": "10000", "currency": bank.currency,
            "counterparty_id": lender.id, "loan_entry_id": payable.id, "loan_from_seq": 5}
    created = client.post("/schedules/definitions", json={**base, "template": {"lines": [line]}})
    assert created.status_code == 201, created.text
    assert created.json()["template"]["lines"][0]["loan_from_seq"] == 5
    detail = client.get(f"/schedules/definitions/{created.json()['id']}")
    assert detail.json()["template"]["lines"][0]["loan_from_seq"] == 5
    zero = client.post("/schedules/definitions", json={**base, "template": {"lines": [{**line, "loan_from_seq": 0}]}})
    assert zero.status_code == 422 and "loan_from_seq" in zero.text
