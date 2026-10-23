"""Attach a loan to an existing installment schedule (spec §4.1). Synthetic data only."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import LedgerEntry, ScheduleDefinition, ScheduleInstance
from app.services import ledger_service
from app.services.settlement_service import open_amount

# 36 monthly periods on the 8th from 2025-09-08: seq 1 = 2025-09-08 … seq 14 = 2026-10-08, seq 15 = 2026-11-08 …
DUE = [date(2025 + (8 + i) // 12, (8 + i) % 12 + 1, 8) for i in range(36)]
assert DUE[0] == date(2025, 9, 8) and DUE[13] == date(2026, 10, 8) and DUE[14] == date(2026, 11, 8) and DUE[35] == date(2028, 8, 8)


@pytest.fixture()
def plain(seed, db_session, today):
    """A 36-period installment with 14 posted periods and NO loan, like the imported 信貸 definitions; per-period
    overrides like MOZE's amortisation (principal 8000+seq, interest 1000-seq)."""
    today(date(2026, 10, 9))
    bank = seed.account("薪轉", opening="50000")
    lender = seed.counterparty("範例銀行")
    definition = seed.definition(
        [seed.line("repayment", bank, "8333"), seed.line("interest", bank, "620")],
        kind="installment", name="信貸", anchor=date(2025, 9, 8), times=36, total_amount=Decimal("300000"),
    )
    for seq in range(1, 37):
        posted = seq <= 14  # a posted period carries its entries (ck_schedule_instance_posted_entries)
        entries = [seed.entry(bank, f"-{8000 + seq}", day=DUE[seq - 1], source="schedule")] if posted else []
        seed.instance(definition, seq, DUE[seq - 1], status="posted" if posted else "pending", entries=entries,
                      amount_override=[str(8000 + seq), str(1000 - seq)])
    db_session.commit()
    pending_principal = sum(Decimal(8000 + seq) for seq in range(15, 37))  # 22 periods
    return SimpleNamespace(bank=bank, lender=lender, definition=definition, due_total=pending_principal)


def _attach(client, definition_id, bank, lender, amount, entry_date="2026-10-09", **extra):
    body = {"account_id": bank.id, "counterparty_id": lender.id, "amount": str(amount), "entry_date": entry_date, "name": "信貸"}
    body.update(extra)
    return client.post(f"/schedules/definitions/{definition_id}/loan", json=body)


def test_detail_exposes_pending_principal(client, plain):
    body = client.get(f"/schedules/definitions/{plain.definition.id}").json()
    assert Decimal(body["pending_principal"]) == plain.due_total and body["loan_remaining"] is None


def test_attach_loan_links_and_is_balance_neutral(client, db_session, plain):
    before = ledger_service.account_balance(db_session, plain.bank.id)
    res = _attach(client, plain.definition.id, plain.bank, plain.lender, plain.due_total)
    assert res.status_code == 201, res.text
    body = res.json()
    db_session.expire_all()
    assert ledger_service.account_balance(db_session, plain.bank.id) == before
    payable = db_session.scalar(select(LedgerEntry).where(LedgerEntry.kind == "payable"))
    adjust = db_session.scalar(select(LedgerEntry).where(LedgerEntry.kind == "balance_adjustment"))
    assert payable.amount == plain.due_total and adjust.amount == -plain.due_total and adjust.account_id == plain.bank.id
    assert adjust.entry_date == adjust.posted_date == payable.entry_date == payable.posted_date == date(2026, 10, 9)
    assert adjust.name == "掛帳調整：信貸"
    line0, line1 = body["template"]["lines"]
    assert line0["loan_entry_id"] == payable.id == body["loan_entry_id"] and line0["loan_from_seq"] == 15
    assert line1["loan_entry_id"] is None and line1.get("loan_from_seq") is None
    assert Decimal(body["loan_remaining"]) == plain.due_total
    d = db_session.get(ScheduleDefinition, plain.definition.id)
    assert (d.times, d.total_amount, d.first_seq, d.template_owner_edited) == (36, Decimal("300000"), 1, True)
    overrides = db_session.scalars(select(ScheduleInstance.amount_override).where(ScheduleInstance.definition_id == d.id)).all()
    assert all(o is not None for o in overrides) and len(overrides) == 36   # the per-period split survived
    detail = client.get(f"/entries/{payable.id}").json()
    assert Decimal(detail["loan_schedule"]["remaining"]) == plain.due_total and Decimal(detail["loan_schedule"]["repaid"]) == 0


def test_attach_then_posting_settles_with_the_period_principal(client, db_session, plain, today):
    _attach(client, plain.definition.id, plain.bank, plain.lender, plain.due_total)
    today(date(2026, 11, 8))
    nxt = db_session.scalar(select(ScheduleInstance.id).where(ScheduleInstance.definition_id == plain.definition.id, ScheduleInstance.seq == 15))
    r = client.post(f"/schedules/instances/{nxt}/post")
    assert r.status_code in (200, 201), r.text
    payable = db_session.scalar(select(LedgerEntry).where(LedgerEntry.kind == "payable"))
    assert open_amount(db_session, payable) == plain.due_total - Decimal(8015)
    assert Decimal(client.get(f"/entries/{payable.id}").json()["loan_schedule"]["repaid"]) == Decimal(8015)


def test_attach_amount_rules(client, db_session, plain):
    d, bank, lender = plain.definition, plain.bank, plain.lender
    last = Decimal(8036)
    too_much = _attach(client, d.id, bank, lender, plain.due_total + 1)
    assert too_much.status_code == 422 and "超過之後各期本金合計" in too_much.text
    too_little = _attach(client, d.id, bank, lender, plain.due_total - last)
    assert too_little.status_code == 422 and "剩餘期數乘以每期金額已達剩餘本金" in too_little.text
    early = _attach(client, d.id, bank, lender, plain.due_total - last + 1)   # ends in the last period with a smaller final payment
    assert early.status_code == 201, early.text


def test_series_counts_periods_beyond_the_horizon(client, db_session, seed, today):
    """Only 3 of 24 periods are generated; the rule still counts all 24."""
    today(date(2026, 10, 9))
    bank = seed.account("薪轉")
    lender = seed.counterparty("範例銀行")
    d = seed.definition([seed.line("repayment", bank, "1000")], kind="installment", name="短", anchor=date(2026, 11, 8), times=24, total_amount=Decimal("24000"))
    for seq in range(1, 4):
        seed.instance(d, seq, date(2026, 10 + seq, 8) if seq <= 2 else date(2027, 1, 8), status="pending")
    db_session.commit()
    assert Decimal(client.get(f"/schedules/definitions/{d.id}").json()["pending_principal"]) == Decimal(24000)
    assert _attach(client, d.id, bank, lender, 24000).status_code == 201


def test_attach_refusals(client, db_session, plain, seed):
    bank, lender, d = plain.bank, plain.lender, plain.definition
    other = seed.account("別戶")
    coll = seed.definition([seed.line("collection", bank, "100")], kind="installment", name="收", anchor=date(2026, 11, 8), times=3)
    rec = seed.definition([seed.line("expense", bank, "100")], kind="recurring", name="月費", anchor=date(2026, 11, 1))
    db_session.commit()
    assert _attach(client, d.id, other, lender, plain.due_total).status_code == 422                 # 貸款要掛在還款帳戶
    r = _attach(client, coll.id, bank, lender, 300); assert r.status_code == 422 and "排程沒有還款那一行" in r.text
    r = _attach(client, rec.id, bank, lender, 100); assert r.status_code == 422 and "只有分期可以掛貸款" in r.text
    assert _attach(client, d.id, bank, lender, plain.due_total).status_code == 201
    again = _attach(client, d.id, bank, lender, plain.due_total)
    assert again.status_code == 409 and "loan_already_attached" in again.text


def test_catch_up_then_attach_end_to_end(client, db_session, plain, today):
    today(date(2026, 12, 20))                                   # seq 15 (11-08) and 16 (12-08) are overdue
    r = _attach(client, plain.definition.id, plain.bank, plain.lender, plain.due_total, entry_date="2026-12-20")
    assert r.status_code == 409 and "catch_up_first" in r.text
    for seq in (15, 16):                                        # 補記: plain expenses (Task 1)
        iid = db_session.scalar(select(ScheduleInstance.id).where(ScheduleInstance.definition_id == plain.definition.id, ScheduleInstance.seq == seq))
        assert client.post(f"/schedules/instances/{iid}/post").status_code in (200, 201)
    remaining = plain.due_total - Decimal(8015) - Decimal(8016)
    assert Decimal(client.get(f"/schedules/definitions/{plain.definition.id}").json()["pending_principal"]) == remaining
    r = _attach(client, plain.definition.id, plain.bank, plain.lender, remaining, entry_date="2026-12-20")
    assert r.status_code == 201, r.text
    assert r.json()["template"]["lines"][0]["loan_from_seq"] == 17
    assert db_session.scalar(select(LedgerEntry).where(LedgerEntry.kind == "payable")) is not None
    assert db_session.scalars(select(LedgerEntry).where(LedgerEntry.is_settlement.is_(True))).all() == []


def test_attach_refuses_ended_and_nothing_pending(client, db_session, plain, seed, today):
    client.post(f"/schedules/definitions/{plain.definition.id}/end")
    r = _attach(client, plain.definition.id, plain.bank, plain.lender, plain.due_total)
    assert r.status_code == 409 and "definition_ended" in r.text
    bank, lender = plain.bank, plain.lender
    d = seed.definition([seed.line("repayment", bank, "100")], kind="installment", name="無待入帳", anchor=date(2026, 1, 8), times=2)
    for seq, day in ((1, date(2026, 1, 8)), (2, date(2026, 2, 8))):
        seed.instance(d, seq, day, status="posted", entries=[seed.entry(bank, "-100", day=day, source="schedule")])
    db_session.commit()
    r = _attach(client, d.id, bank, lender, 100)
    assert r.status_code == 409 and "nothing_pending" in r.text


def test_attach_loan_on_paused_schedule_then_resume_settles(client, db_session, plain, today):
    client.post(f"/schedules/definitions/{plain.definition.id}/pause")
    assert _attach(client, plain.definition.id, plain.bank, plain.lender, plain.due_total).status_code == 201
    today(date(2026, 11, 9))
    r = client.post(f"/schedules/definitions/{plain.definition.id}/resume", json={"backlog": "post"})
    assert r.status_code == 200, r.text
    payable = db_session.scalar(select(LedgerEntry).where(LedgerEntry.kind == "payable"))
    assert open_amount(db_session, payable) == plain.due_total - Decimal(8015)


def test_attach_refuses_imported_definition_while_unlocked(client, db_session, plain, monkeypatch):
    d = db_session.get(ScheduleDefinition, plain.definition.id)
    d.moze_id = "moze-1"
    db_session.commit()
    monkeypatch.delenv("ACCOUNTING_IMPORT_LOCKED", raising=False)
    assert _attach(client, d.id, plain.bank, plain.lender, plain.due_total).status_code == 409
    monkeypatch.setenv("ACCOUNTING_IMPORT_LOCKED", "true")
    assert _attach(client, d.id, plain.bank, plain.lender, plain.due_total).status_code == 201


def test_update_after_attach_keeps_loan_link(client, db_session, plain):
    _attach(client, plain.definition.id, plain.bank, plain.lender, plain.due_total)
    body = client.get(f"/schedules/definitions/{plain.definition.id}").json()
    keep = ("kind", "account_id", "to_account_id", "to_amount", "counterparty_id", "category_id", "project_id", "amount", "currency", "loan_entry_id", "loan_from_seq", "name", "merchant")
    update = {k: body[k] for k in ("name", "template", "interval_unit", "interval_n", "anchor_date", "day_of_month", "times", "end_date", "total_amount")}
    update["template"] = {"lines": [{k: v for k, v in line.items() if k in keep} for line in body["template"]["lines"]], "description": body["template"].get("description"), "tags": body["template"].get("tags", [])}
    update["name"] = "信貸（改名）"
    r = client.put(f"/schedules/definitions/{plain.definition.id}", json=update)
    assert r.status_code == 200, r.text
    assert r.json()["loan_entry_id"] == body["loan_entry_id"] and r.json()["template"]["lines"][0]["loan_from_seq"] == 15
    # a client that does not know the key (sends the same loan without loan_from_seq) must not erase the boundary
    naked = {**update, "name": "信貸（再改）"}
    naked["template"] = {**update["template"], "lines": [{k: v for k, v in line.items() if k != "loan_from_seq"} for line in update["template"]["lines"]]}
    r = client.put(f"/schedules/definitions/{plain.definition.id}", json=naked)
    assert r.status_code == 200, r.text
    assert r.json()["template"]["lines"][0]["loan_from_seq"] == 15
    # existing behaviour, recorded not fixed here: the rename regenerates future pending instances and flattens their
    # per-period overrides (see spec §5) — assert what happens so the follow-up has a baseline:
    overrides = db_session.scalars(select(ScheduleInstance.amount_override).where(ScheduleInstance.definition_id == plain.definition.id, ScheduleInstance.status == "pending")).all()
    print("pending overrides after rename:", sum(o is not None for o in overrides), "of", len(overrides))


def test_update_refuses_a_changed_loan_from_seq(client, db_session, plain):
    """Ruling: the boundary is set by 掛上貸款 only; a PUT may keep it, never move it."""
    _attach(client, plain.definition.id, plain.bank, plain.lender, plain.due_total)
    body = client.get(f"/schedules/definitions/{plain.definition.id}").json()
    keep = ("kind", "account_id", "to_account_id", "to_amount", "counterparty_id", "category_id", "project_id", "amount", "currency", "loan_entry_id", "loan_from_seq", "name", "merchant")
    update = {k: body[k] for k in ("name", "interval_unit", "interval_n", "anchor_date", "day_of_month", "times", "end_date", "total_amount")}
    lines = [{k: v for k, v in line.items() if k in keep} for line in body["template"]["lines"]]
    lines[0]["loan_from_seq"] = 16
    update["template"] = {"lines": lines, "description": body["template"].get("description"), "tags": body["template"].get("tags", [])}
    r = client.put(f"/schedules/definitions/{plain.definition.id}", json=update)
    assert r.status_code == 422 and "起算期只能由掛上貸款設定" in r.text
    assert "template.lines[0].loan_from_seq" in r.text
    db_session.expire_all()
    assert db_session.get(ScheduleDefinition, plain.definition.id).template["lines"][0]["loan_from_seq"] == 15
