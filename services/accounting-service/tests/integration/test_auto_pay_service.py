"""auto_pay_service: candidates, gates (in order), twins, booking (auto-pay spec §4.1–§4.4). Fictional amounts only."""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.models import (AccountStatement, LedgerEntry, ReconciliationAudit, ReconciliationCase, StatementAutoPay,
                        StatementCoverage)
from app.schemas.writes import TransferIn
from app.services import auto_pay_service as aps
from app.services import entry_write_service as ews
from app.services import reconciliation_service as rec
from app.services import transfer_service
from app.services.errors import CodedConflictError
from app.services.settings_service import AUTO_PAY_DEFAULTS
from tests.integration.statement_fixtures import OCT, SEP, _card, _line, _submit, _transfer, run  # noqa: F401

DAY = date(2026, 9, 3)
DUE = date(2026, 10, 24)  # Sat; Mon 10-26 is the 光復節 make-up holiday
PAY = date(2026, 10, 27)
TODAY = PAY
NOV = (date(2026, 11, 1), date(2026, 11, 30))


def _settings(**over):
    return {**AUTO_PAY_DEFAULTS, "enabled": True, "enabled_from": "2026-09-01", "version": 1, **over}


@pytest.fixture
def world(seed, db_session, run):
    bank = seed.account("薪轉")
    card = _card(seed, db_session, "卡", auto_pay_account_id=bank.id)
    stmt, _, _ = _submit(db_session, run, card, [_line(1, DAY, "全聯", 580)], period=SEP, due_date=DUE)
    return bank, card, stmt


def _cand(db, stmt_id, today=TODAY, settings=None):
    found = [c for c in aps.candidates(db, today, settings or _settings()) if c.statement_id == stmt_id]
    assert len(found) == 1, found
    return found[0]


def _dry(db, stmt_id, today=TODAY, **over):
    """The decision a dry run reports for the statement (nothing is written)."""
    settings = _settings(**over)
    return aps.decide(db, _cand(db, stmt_id, today, settings), today, settings, dry_run=True)


def _reason(db, stmt_id, today=TODAY, **over):
    return _dry(db, stmt_id, today, **over)["reason"]


def _events(db, statement_id):
    return [a.event for a in db.query(ReconciliationAudit).filter_by(statement_id=statement_id)
            .order_by(ReconciliationAudit.id)]


def _booked_row(db, stmt, card, bank, pay_date, amount="580"):
    row = StatementAutoPay(statement_id=stmt.id, card_account_id=card.id, pay_account_id=bank.id,
                           amount=Decimal(amount), due_date=pay_date, pay_date=pay_date, status="booked",
                           settings_version=1)
    db.add(row)
    db.flush()
    return row


def test_books_the_pair_with_two_posting_dates_and_the_row(db_session, world):
    db, (bank, card, stmt) = db_session, world
    cand = _cand(db, stmt.id)
    assert (cand.card_account_id, cand.pay_account_id, cand.amount, cand.due_date, cand.pay_date, cand.period_end,
            cand.card_name, cand.currency, cand.existing_reason) == (
        card.id, bank.id, Decimal("580"), DUE, PAY, SEP[1], "卡", "TWD", None)
    out = aps.decide(db, cand, TODAY, _settings())
    db.commit()
    assert out == {"statement_id": stmt.id, "card_account_id": card.id, "pay_account_id": bank.id,
                   "amount": str(cand.amount), "pay_date": "2026-10-27", "decision": "booked", "reason": None}
    row = db.query(StatementAutoPay).one()
    out_leg, in_leg = transfer_service.transfer_legs(db, row.transfer_group_id)
    assert (out_leg.account_id, in_leg.account_id, out_leg.amount, in_leg.amount) == (
        bank.id, card.id, Decimal("-580"), Decimal("580"))
    assert (out_leg.entry_date, out_leg.posted_date, in_leg.entry_date, in_leg.posted_date) == (
        PAY, PAY, PAY, date(2026, 10, 28))
    assert out_leg.source == in_leg.source == "statement" and out_leg.name == in_leg.name == "自動扣繳"
    assert out_leg.description == "卡 2026-09-30 帳單 自動扣繳"
    assert not out_leg.needs_review and not in_leg.needs_review
    assert (row.status, row.amount, row.due_date, row.pay_date, row.settings_version, row.reason) == (
        "booked", Decimal("580"), DUE, PAY, 1, None)
    assert (row.card_account_id, row.pay_account_id, row.out_entry_id, row.in_entry_id) == (
        card.id, bank.id, out_leg.id, in_leg.id)
    audit = db.query(ReconciliationAudit).filter_by(statement_id=stmt.id).order_by(ReconciliationAudit.id).all()[-1]
    assert (audit.event, audit.actor) == ("auto_pay_booked", "auto_pay")
    assert audit.detail == {"card_account_id": card.id, "pay_account_id": bank.id, "amount": str(cand.amount),
                            "pay_date": "2026-10-27", "out_entry_id": out_leg.id, "in_entry_id": in_leg.id}
    assert aps.candidates(db, TODAY, _settings()) == []  # booked: never a candidate again
    # the row's own legs are twins of themselves; excluded, nothing else is
    common = dict(card_account_id=card.id, pay_account_id=bank.id, amount=Decimal("580"), pay_date=PAY, window_days=7)
    assert aps.find_twins(db, **common) == sorted([out_leg.id, in_leg.id])
    assert aps.find_twins(db, **common, exclude=[out_leg.id, in_leg.id]) == []


def test_not_a_candidate_before_pay_date_and_without_due_date(seed, db_session, run, world):
    db, (bank, card, stmt) = db_session, world
    assert aps.candidates(db, date(2026, 10, 26), _settings()) == []  # due Sat, pay Tue: not yet
    no_due, _, _ = _submit(db, run, card, [_line(1, date(2026, 10, 3), "全聯", 580)], period=OCT)
    unlinked = _card(seed, db, "無連結")
    unlinked_no_due, _, _ = _submit(db, run, unlinked, [_line(1, DAY, "全聯", 580)], period=SEP)
    assert no_due.due_date is None and no_due.mode == "live"
    assert [c.statement_id for c in aps.candidates(db, TODAY, _settings())] == [stmt.id]
    assert aps.no_due_date(db) == [no_due.id]  # the unlinked card's statement is not listed
    assert unlinked_no_due.id not in aps.no_due_date(db)


def test_gate_order_cutoff_duplicate_header_link(seed, db_session, run, world):
    db, (bank, card, stmt) = db_session, world
    # cutoff (terminal); skipped entirely while enabled_from is None
    assert _reason(db, stmt.id, enabled_from="2026-11-01") == "cutoff"
    assert _dry(db, stmt.id, enabled_from=None)["decision"] == "would_book"
    # duplicate_statement: a misread statement B of the same card already booked at 10-20 (7 days away)
    b, _, _ = _submit(db, run, card, [_line(1, date(2026, 10, 3), "全聯", 580)], period=OCT, due_date=date(2026, 10, 21))
    b_row = _booked_row(db, b, card, bank, PAY - timedelta(days=20))
    assert _reason(db, stmt.id) is None  # exactly 20 days apart: two bills
    b_row.pay_date = PAY - timedelta(days=19)
    db.flush()
    assert _reason(db, stmt.id) == "duplicate_statement"
    b_row.pay_date = date(2026, 10, 20)
    db.commit()
    out = aps.decide(db, _cand(db, stmt.id), TODAY, _settings())
    db.commit()
    assert (out["decision"], out["reason"]) == ("skipped", "duplicate_statement")
    row = db.query(StatementAutoPay).filter_by(statement_id=stmt.id).one()
    assert (row.status, row.reason, row.pay_account_id) == ("skipped", "duplicate_statement", bank.id)
    assert aps.candidates(db, TODAY, _settings()) == []  # terminal
    # the same card's next statement (due 11-24, 35 days after B's pay date) books while B's row is booked
    c, _, _ = _submit(db, run, card, [_line(1, date(2026, 11, 3), "全聯", 580)], period=NOV,
                      due_date=date(2026, 11, 24))
    later = date(2026, 11, 24)
    out = aps.decide(db, _cand(db, c.id, later), later, _settings())
    db.commit()
    assert out["decision"] == "booked"
    assert db.query(StatementAutoPay).filter_by(status="booked").count() == 2


def test_gate_header_disputed(db_session, world):
    db, (bank, card, stmt) = db_session, world
    case = ReconciliationCase(statement_id=stmt.id, revision_id=stmt.current_revision_id, kind="parse_review",
                              status="open")
    db.add(case)
    db.flush()
    assert _reason(db, stmt.id) == "header_disputed"
    case.status = "proposed"
    db.flush()
    assert _reason(db, stmt.id) == "header_disputed"
    case.status = "dismissed"  # the owner kept the current header
    db.flush()
    assert _dry(db, stmt.id)["decision"] == "would_book"
    db.add(ReconciliationCase(statement_id=stmt.id, revision_id=stmt.current_revision_id, kind="statement_conflict",
                              status="open"))
    db.flush()
    assert _reason(db, stmt.id) == "header_disputed"
    # header_disputed comes before no_link
    card.auto_pay_account_id = None
    db.flush()
    assert _reason(db, stmt.id) == "header_disputed"


def test_gate_no_link_and_link_ineligible(seed, db_session, run, world):
    db, (bank, card, stmt) = db_session, world
    # no_link: a CHILD card carrying the link does not count; the skip row stores the card in pay_account_id
    master = _card(seed, db, "主卡")
    _card(seed, db, "子卡", combined_account_id=master.id, auto_pay_account_id=bank.id)
    m_stmt, _, _ = _submit(db, run, master, [_line(1, DAY, "全聯", 580)], period=SEP, due_date=DUE)
    cand = _cand(db, m_stmt.id)
    assert cand.pay_account_id is None
    out = aps.decide(db, cand, TODAY, _settings())
    db.commit()
    assert (out["decision"], out["reason"], out["pay_account_id"]) == ("skipped", "no_link", None)
    row = db.query(StatementAutoPay).filter_by(statement_id=m_stmt.id).one()
    assert (row.status, row.reason, row.pay_account_id, row.card_account_id) == ("skipped", "no_link", master.id,
                                                                                 master.id)
    assert _events(db, m_stmt.id)[-1] == "auto_pay_skipped"
    assert _cand(db, m_stmt.id).existing_reason == "no_link"  # transient: still a candidate
    # link_ineligible: a credit card, an archived account, another currency
    other_card = _card(seed, db, "別卡")
    archived = seed.account("舊戶", is_archived=True)
    usd = seed.account("美金", currency="USD")
    db.commit()
    for target in (other_card, archived, usd):
        card.auto_pay_account_id = target.id
        db.flush()
        assert _reason(db, stmt.id) == "link_ineligible", target.name
    card.auto_pay_account_id = bank.id
    db.flush()
    assert _dry(db, stmt.id)["decision"] == "would_book"


def test_twin_shapes(seed, db_session, world):
    db, (bank, card, stmt) = db_session, world
    child = _card(seed, db, "子卡", combined_account_id=card.id)
    other = seed.account("別戶")
    db.commit()

    def twin_after(make) -> str | None:
        make()
        db.flush()
        reason = _reason(db, stmt.id)
        db.rollback()
        return reason

    pair = uuid.uuid4()
    shapes = {
        "owner transfer bank→card": lambda: _transfer(seed, bank, card, 580, PAY),
        "owner transfer bank→child card": lambda: _transfer(seed, bank, child, 580, PAY),
        "transfer_in on the child card from another account": lambda: _transfer(seed, other, child, 580, PAY),
        "transfer_out on the bank without a peer leg, 3 days later":
            lambda: seed.entry(bank, "-580", kind="transfer_out", day=date(2026, 10, 30)),
        "statement-sourced expense on the bank": lambda: seed.entry(bank, "-580", day=PAY, source="statement"),
        "pair_transfer shape (source=statement)": lambda: (
            seed.entry(bank, "-580", kind="transfer_out", day=PAY, source="statement", transfer_group_id=pair),
            seed.entry(card, "580", kind="transfer_in", day=PAY, source="statement", transfer_group_id=pair)),
        "transfer_in on the card alone, 7 days before": lambda: seed.entry(card, "580", kind="transfer_in",
                                                                           day=PAY - timedelta(days=7)),
    }
    for label, make in shapes.items():
        assert twin_after(make) == "twin", label
    not_twins = {
        "manual expense on the bank": lambda: seed.entry(bank, "-580", day=PAY),
        "owner transfer 9 days later": lambda: _transfer(seed, bank, card, 580, date(2026, 11, 5)),
        "owner transfer of another amount": lambda: _transfer(seed, bank, card, 581, PAY),
        "transfer_in on the bank": lambda: seed.entry(bank, "580", kind="transfer_in", day=PAY),
        "transfer_out on the bank to another account, 3 days later":
            lambda: _transfer(seed, bank, other, 580, date(2026, 10, 30)),
    }
    for label, make in not_twins.items():
        assert twin_after(make) is None, label
    # a twin is terminal: the real decision writes a skipped row and the statement is never a candidate again
    _transfer(seed, bank, card, 580, PAY)
    db.flush()
    out = aps.decide(db, _cand(db, stmt.id), TODAY, _settings())
    db.commit()
    assert (out["decision"], out["reason"]) == ("skipped", "twin")
    assert aps.candidates(db, TODAY, _settings()) == []


def test_caps_and_expiry(seed, db_session, run, world):
    db, (bank, card, stmt) = db_session, world
    assert _reason(db, stmt.id, max_amount="500") == "over_cap"
    assert _reason(db, stmt.id, max_amount="580") is None  # strictly greater only
    # monthly_budget: 580 booked in October on another card + 580 > 1000
    card2 = _card(seed, db, "卡二", auto_pay_account_id=bank.id)
    s2, _, _ = _submit(db, run, card2, [_line(1, DAY, "全聯", 580)], period=SEP, due_date=date(2026, 10, 30))
    row2 = _booked_row(db, s2, card2, bank, date(2026, 10, 2))
    db.commit()
    assert _reason(db, stmt.id, monthly_total="1000") == "monthly_budget"
    assert _reason(db, stmt.id, monthly_total="1160") is None
    row2.pay_date = date(2026, 9, 30)  # another month does not count
    db.flush()
    assert _reason(db, stmt.id, monthly_total="1000") is None
    db.rollback()
    # a transient skip is rewritten in place and audited only when the reason changes
    capped = _settings(max_amount="500")
    for _ in range(2):
        out = aps.decide(db, _cand(db, stmt.id, settings=capped), TODAY, capped)
        db.commit()
        assert (out["decision"], out["reason"]) == ("skipped", "over_cap")
    row = db.query(StatementAutoPay).filter_by(statement_id=stmt.id).one()
    assert _events(db, stmt.id).count("auto_pay_skipped") == 1
    last_day = PAY + timedelta(days=30)
    assert _reason(db, stmt.id, last_day, max_amount="500") == "over_cap"  # retry_days not yet passed
    expired_day = PAY + timedelta(days=31)
    out = aps.decide(db, _cand(db, stmt.id, expired_day, capped), expired_day, capped)
    db.commit()
    assert (out["decision"], out["reason"]) == ("skipped", "expired")
    assert db.query(StatementAutoPay).filter_by(statement_id=stmt.id).one().id == row.id  # same row
    db.refresh(row)
    assert (row.status, row.reason) == ("skipped", "expired")
    assert _events(db, stmt.id).count("auto_pay_skipped") == 2
    skipped = db.query(ReconciliationAudit).filter_by(statement_id=stmt.id, event="auto_pay_skipped") \
        .order_by(ReconciliationAudit.id).all()
    assert [a.detail["reason"] for a in skipped] == ["over_cap", "expired"]
    assert aps.candidates(db, expired_day, capped) == []  # terminal: no longer a candidate


def test_transient_skip_clears_and_books_the_same_row(db_session, world):
    db, (bank, card, stmt) = db_session, world
    capped = _settings(max_amount="500")
    aps.decide(db, _cand(db, stmt.id, settings=capped), TODAY, capped)
    db.commit()
    skip_id = db.query(StatementAutoPay).one().id
    out = aps.decide(db, _cand(db, stmt.id, settings=_settings(version=2)), TODAY, _settings(version=2))
    db.commit()
    row = db.query(StatementAutoPay).one()
    assert (out["decision"], row.id, row.status, row.reason, row.settings_version) == (
        "booked", skip_id, "booked", None, 2)
    assert row.out_entry_id is not None and row.in_entry_id is not None


def test_dry_run_writes_nothing(db_session, world):
    db, (bank, card, stmt) = db_session, world
    entries = db.scalar(select(func.count()).select_from(LedgerEntry))
    audits = db.scalar(select(func.count()).select_from(ReconciliationAudit))
    out = _dry(db, stmt.id)
    assert (out["decision"], out["reason"]) == ("would_book", None)
    assert _dry(db, stmt.id, max_amount="500")["decision"] == "would_skip"
    db.commit()
    assert db.query(StatementAutoPay).count() == 0
    assert db.scalar(select(func.count()).select_from(LedgerEntry)) == entries
    assert db.scalar(select(func.count()).select_from(ReconciliationAudit)) == audits


def _over_cap_skip(db, stmt_id):
    capped = _settings(max_amount="500")
    aps.decide(db, _cand(db, stmt_id, settings=capped), TODAY, capped)
    db.commit()


def test_cleared_transient_skip_past_retry_days_expires_instead_of_booking(db_session, world):
    db, (bank, card, stmt) = db_session, world
    _over_cap_skip(db, stmt.id)
    entries = db.scalar(select(func.count()).select_from(LedgerEntry))
    raised = _settings()  # the cap no longer fails
    late = PAY + timedelta(days=31)
    assert _dry(db, stmt.id, late)["reason"] == "expired"
    out = aps.decide(db, _cand(db, stmt.id, late, raised), late, raised)
    db.commit()
    assert (out["decision"], out["reason"]) == ("skipped", "expired")
    row = db.query(StatementAutoPay).filter_by(statement_id=stmt.id).one()
    assert (row.status, row.reason) == ("skipped", "expired")
    assert db.scalar(select(func.count()).select_from(LedgerEntry)) == entries  # nothing booked
    assert aps.candidates(db, late, raised) == []


def test_cleared_transient_skip_on_the_last_retry_day_still_books(db_session, world):
    db, (bank, card, stmt) = db_session, world
    _over_cap_skip(db, stmt.id)
    raised = _settings()
    last = PAY + timedelta(days=30)
    out = aps.decide(db, _cand(db, stmt.id, last, raised), last, raised)
    db.commit()
    assert out["decision"] == "booked"
    assert db.query(StatementAutoPay).filter_by(statement_id=stmt.id).one().status == "booked"


def test_gate_precedence_pairs(seed, db_session, run, world):
    db, (bank, card, stmt) = db_session, world
    other = seed.account("別戶")
    db.commit()
    # twin before over_cap
    _transfer(seed, bank, card, 580, PAY)
    db.flush()
    assert _reason(db, stmt.id, max_amount="500") == "twin"
    db.rollback()
    # cutoff before duplicate_statement
    b, _, _ = _submit(db, run, card, [_line(1, date(2026, 10, 3), "全聯", 580)], period=OCT, due_date=date(2026, 10, 21))
    _booked_row(db, b, card, bank, date(2026, 10, 20))
    db.flush()
    assert _reason(db, stmt.id) == "duplicate_statement"
    assert _reason(db, stmt.id, enabled_from="2026-11-01") == "cutoff"
    db.rollback()
    # link_ineligible before twin
    archived = seed.account("舊戶", is_archived=True)
    db.commit()
    _transfer(seed, other, card, 580, PAY)  # a transfer_in on the card: a twin for any pay account
    db.flush()
    assert _reason(db, stmt.id) == "twin"
    card.auto_pay_account_id = archived.id
    db.flush()
    assert _reason(db, stmt.id) == "link_ineligible"
    db.rollback()
    # over_cap before monthly_budget
    card2 = _card(seed, db, "卡二", auto_pay_account_id=bank.id)
    s2, _, _ = _submit(db, run, card2, [_line(1, DAY, "全聯", 580)], period=SEP, due_date=date(2026, 10, 30))
    _booked_row(db, s2, card2, bank, date(2026, 10, 2))
    db.flush()
    assert _reason(db, stmt.id, monthly_total="1000") == "monthly_budget"
    assert _reason(db, stmt.id, monthly_total="1000", max_amount="500") == "over_cap"


def test_twin_window_upper_edge(seed, db_session, world):
    db, (bank, card, stmt) = db_session, world
    _transfer(seed, bank, card, 580, PAY + timedelta(days=8))
    db.flush()
    assert _reason(db, stmt.id) is None  # +8 is outside the 7-day window
    _transfer(seed, bank, card, 580, PAY + timedelta(days=7))
    db.flush()
    assert _reason(db, stmt.id) == "twin"  # +7 is inside (inclusive)


# --- housekeeping (Task 5) ------------------------------------------------------------------------------------------


def _book(db, stmt):
    """Book the statement through `decide` and commit; returns (row, out_leg, in_leg)."""
    assert aps.decide(db, _cand(db, stmt.id), TODAY, _settings())["decision"] == "booked"
    db.commit()
    row = db.query(StatementAutoPay).filter_by(statement_id=stmt.id).one()
    out_leg, in_leg = transfer_service.transfer_legs(db, row.transfer_group_id)
    return row, out_leg, in_leg


def _audit_tail(db, statement_id):
    return db.query(ReconciliationAudit).filter_by(statement_id=statement_id).order_by(ReconciliationAudit.id) \
        .all()[-1]


def test_owner_deleted(db_session, world):
    db, (bank, card, stmt) = db_session, world
    row, out_leg, in_leg = _book(db, stmt)
    assert aps.booked_rows(db) == [row]
    ews.delete_entry(db, out_leg.id)  # both legs go
    db.commit()
    assert aps.check_row(db, row, TODAY, _settings()) == "owner_deleted"
    db.commit()
    assert (row.status, row.out_entry_id, row.in_entry_id) == ("owner_deleted", None, None)
    tail = _audit_tail(db, stmt.id)
    assert (tail.event, tail.actor) == ("auto_pay_owner_deleted", "auto_pay")
    assert tail.detail == {"card_account_id": card.id, "pay_account_id": bank.id, "amount": "580.0000",
                           "pay_date": "2026-10-27"}
    assert aps.booked_rows(db) == []
    assert aps.check_row(db, row, TODAY, _settings()) is None
    db.commit()
    assert _events(db, stmt.id).count("auto_pay_owner_deleted") == 1


def test_owner_edited_via_put_transfers(db_session, world):
    db, (bank, card, stmt) = db_session, world
    row, out_leg, in_leg = _book(db, stmt)
    out_id, in_id = out_leg.id, in_leg.id
    transfer_service.update_transfer(db, row.transfer_group_id, TransferIn(
        from_account_id=bank.id, to_account_id=card.id, out_amount=Decimal("580"), entry_date=PAY, posted_date=PAY,
        name="自動扣繳", description="我付的"))
    db.commit()
    assert {db.get(LedgerEntry, out_id).source, db.get(LedgerEntry, in_id).source} == {"manual"}
    assert aps.check_row(db, row, TODAY, _settings()) == "owner_edited"
    db.commit()
    assert (row.status, row.amount, row.pay_date, row.out_entry_id, row.in_entry_id) == (
        "owner_edited", Decimal("580"), PAY, out_id, in_id)  # figures untouched
    assert db.get(LedgerEntry, out_id) is not None and db.get(LedgerEntry, in_id) is not None
    tail = _audit_tail(db, stmt.id)
    assert tail.event == "auto_pay_owner_edited"
    assert (tail.detail["out_entry_id"], tail.detail["in_entry_id"]) == (out_id, in_id)
    assert aps.check_row(db, row, TODAY, _settings()) is None
    assert _events(db, stmt.id).count("auto_pay_owner_edited") == 1


def test_description_only_edit_keeps_booked(db_session, world):
    db, (bank, card, stmt) = db_session, world
    row, out_leg, in_leg = _book(db, stmt)
    out_leg.description = "x"
    in_leg.name = "改名"
    db.commit()
    assert aps.check_row(db, row, TODAY, _settings()) is None
    db.commit()
    assert row.status == "booked" and row.reason is None
    assert _events(db, stmt.id)[-1] == "auto_pay_booked"


def test_action_style_redate_updates_figures(db_session, world):
    db, (bank, card, stmt) = db_session, world
    row, out_leg, in_leg = _book(db, stmt)
    out_leg.entry_date = in_leg.entry_date = PAY + timedelta(days=1)  # source stays statement
    db.commit()
    assert aps.check_row(db, row, TODAY, _settings()) == "updated"
    db.commit()
    assert (row.status, row.pay_date, row.amount, row.pay_account_id, row.card_account_id, row.due_date) == (
        "booked", PAY + timedelta(days=1), Decimal("580"), bank.id, card.id, DUE)
    assert _events(db, stmt.id)[-1] == "auto_pay_booked"  # no audit
    assert aps.check_row(db, row, TODAY, _settings()) is None  # settled: not "updated" on every run


def test_twin_after_booking_retires_the_pair_and_releases_claims(seed, db_session, run, world):
    db, (bank, card, stmt) = db_session, world
    bank.statement_live_from = date(2026, 9, 1)
    db.commit()
    row, out_leg, in_leg = _book(db, stmt)
    out_id, in_id = out_leg.id, in_leg.id
    bank_stmt, (line,), _ = _submit(db, run, bank, [_line(1, PAY, "卡費", -580, "withdrawal")], kind="bank",
                                    period=OCT)
    (held,) = db.query(StatementCoverage).filter_by(statement_id=bank_stmt.id, status="active").all()
    assert (held.line_id, held.entry_id, held.match_rule) == (line.id, out_id, "exact")
    bank_stmt.needs_recheck = False
    db.commit()
    twin_out, twin_in = _transfer(seed, bank, card, 580, PAY)
    db.commit()
    twin_out_id = twin_out.id
    assert aps.check_row(db, row, TODAY, _settings()) == "retired"
    db.commit()
    assert db.get(LedgerEntry, out_id) is None and db.get(LedgerEntry, in_id) is None
    db.refresh(held)
    assert (held.status, held.stale_reason) == ("stale", "auto_pay:twin")
    assert db.get(AccountStatement, bank_stmt.id).needs_recheck is True
    assert (row.status, row.reason) == ("retired", "twin")
    tail = _audit_tail(db, stmt.id)
    assert (tail.event, tail.actor) == ("auto_pay_retired", "auto_pay")
    assert tail.detail == {"reason": "twin", "out_entry_id": out_id, "in_entry_id": in_id,
                           "released_statement_ids": [bank_stmt.id]}
    rec.reconcile(db, bank_stmt.id)
    db.commit()
    (claim,) = db.query(StatementCoverage).filter_by(statement_id=bank_stmt.id, status="active").all()
    assert (claim.line_id, claim.entry_id) == (line.id, twin_out_id)


def test_twin_window_closed_after_14_days(seed, db_session, world):
    db, (bank, card, stmt) = db_session, world
    row, out_leg, in_leg = _book(db, stmt)
    _transfer(seed, bank, card, 580, PAY)
    db.commit()
    assert aps.check_row(db, row, PAY + timedelta(days=15), _settings()) is None  # the job stopped looking
    db.commit()
    assert row.status == "booked"
    assert aps.check_row(db, row, PAY + timedelta(days=14), _settings()) == "retired"  # last day still looks
    db.commit()
    assert row.status == "retired"


def test_retire_pair_directly_ignores_the_window(seed, db_session, world):
    db, (bank, card, stmt) = db_session, world
    row, out_leg, in_leg = _book(db, stmt)
    out_id, in_id = out_leg.id, in_leg.id
    assert aps.retire_pair(db, row, reason="twin") == set()  # the sweep path (Task 7): no window, no claims held
    db.commit()
    assert (row.status, row.reason) == ("retired", "twin")
    assert db.get(LedgerEntry, out_id) is None and db.get(LedgerEntry, in_id) is None


def test_header_change_marks_needs_review_once(db_session, world):
    db, (bank, card, stmt) = db_session, world
    row, out_leg, in_leg = _book(db, stmt)
    out_id, in_id = out_leg.id, in_leg.id
    db.execute(text("UPDATE account_statement SET statement_total = 600 WHERE id = :id"), {"id": stmt.id})
    db.commit()
    assert aps.check_row(db, row, TODAY, _settings()) == "header_changed"
    db.commit()
    assert (row.status, row.reason, row.amount) == ("booked", "header_changed", Decimal("580"))
    assert db.get(LedgerEntry, out_id).needs_review and db.get(LedgerEntry, in_id).needs_review
    tail = _audit_tail(db, stmt.id)
    assert tail.event == "auto_pay_header_changed"
    assert (tail.detail["statement_total"], tail.detail["due_date"]) == ("600.0000", DUE.isoformat())
    assert aps.check_row(db, row, TODAY, _settings()) is None
    db.commit()
    assert _events(db, stmt.id).count("auto_pay_header_changed") == 1


def test_retire_pair_busy_when_a_leg_is_locked(pg_engine, db_session, world):
    db, (bank, card, stmt) = db_session, world
    row, out_leg, in_leg = _book(db, stmt)
    out_id, in_id = out_leg.id, in_leg.id
    holder = pg_engine.connect()
    try:
        holder.begin()
        holder.execute(text("SELECT id FROM ledger_entry WHERE id = :id FOR UPDATE"), {"id": in_id})
        with pytest.raises(CodedConflictError) as caught:
            aps.retire_pair(db, row, reason="twin")
        assert caught.value.code == aps.PAIR_BUSY == "auto_pay_busy"
        db.rollback()
    finally:
        holder.rollback()
        holder.close()
    db.refresh(row)
    assert (row.status, row.reason, row.out_entry_id, row.in_entry_id) == ("booked", None, out_id, in_id)
    assert db.get(LedgerEntry, out_id) is not None and db.get(LedgerEntry, in_id) is not None
    assert _events(db, stmt.id)[-1] == "auto_pay_booked"


# --- final fix wave: duplicate gate over final rows, transfer_out twin peers, retire under the row lock ---------------


def _entry_count(db):
    return db.scalar(select(func.count()).select_from(LedgerEntry))


def test_duplicate_gate_sees_an_owner_deleted_pair(seed, db_session, run, world):
    db, (bank, card, stmt) = db_session, world
    row, out_leg, in_leg = _book(db, stmt)
    ews.delete_entry(db, out_leg.id)  # the owner deletes the pair
    db.commit()
    assert aps.check_row(db, row, TODAY, _settings()) == "owner_deleted"
    db.commit()
    # a re-parsed duplicate of the same bill (period_end + 5 days, same total) must not book it again
    b, _, _ = _submit(db, run, card, [_line(1, date(2026, 9, 8), "全聯", 580)],
                      period=(date(2026, 9, 6), date(2026, 10, 5)), due_date=DUE)
    entries = _entry_count(db)
    out = aps.decide(db, _cand(db, b.id), TODAY, _settings())
    db.commit()
    assert (out["decision"], out["reason"]) == ("skipped", "duplicate_statement")
    assert _entry_count(db) == entries
    assert db.query(StatementAutoPay).filter_by(statement_id=b.id).one().status == "skipped"


def test_duplicate_gate_sees_a_retired_pair_when_the_twin_is_outside_the_duplicates_window(seed, db_session, run,
                                                                                          world):
    db, (bank, card, stmt) = db_session, world
    row, _, _ = _book(db, stmt)
    _transfer(seed, bank, card, 580, PAY - timedelta(days=5))  # the owner's own payment: a twin of A
    db.commit()
    assert aps.check_row(db, row, TODAY, _settings()) == "retired"
    db.commit()
    # the duplicate's due date is misread 3 days late: the owner's payment is 8 days from its pay date
    late_due = PAY + timedelta(days=3)  # Fri 10-30, a business day
    b, _, _ = _submit(db, run, card, [_line(1, date(2026, 9, 8), "全聯", 580)],
                      period=(date(2026, 9, 6), date(2026, 10, 5)), due_date=late_due)
    cand = _cand(db, b.id, late_due)
    assert cand.pay_date == late_due
    assert aps.find_twins(db, card_account_id=card.id, pay_account_id=bank.id, amount=cand.amount,
                          pay_date=cand.pay_date, window_days=7) == []
    entries = _entry_count(db)
    out = aps.decide(db, cand, late_due, _settings())
    db.commit()
    assert (out["decision"], out["reason"]) == ("skipped", "duplicate_statement")
    assert _entry_count(db) == entries


def test_two_cards_paid_from_one_bank_with_equal_totals_both_book(seed, db_session, run, world):
    db, (bank, card, stmt) = db_session, world
    card_b = _card(seed, db, "卡二", auto_pay_account_id=bank.id)
    due_b = date(2026, 10, 29)  # Thu, the same week
    stmt_b, _, _ = _submit(db, run, card_b, [_line(1, DAY, "全聯", 580)], period=SEP, due_date=due_b)
    assert aps.decide(db, _cand(db, stmt.id), TODAY, _settings())["decision"] == "booked"
    db.commit()
    # card A's out leg (bank → card A) is a transfer_out of 580 on the bank 2 days away, but its peer is not on B
    out = aps.decide(db, _cand(db, stmt_b.id, due_b), due_b, _settings())
    db.commit()
    assert (out["decision"], out["reason"]) == ("booked", None)
    rows = db.query(StatementAutoPay).filter_by(status="booked").order_by(StatementAutoPay.id).all()
    assert [(r.card_account_id, r.pay_account_id, r.amount) for r in rows] == [
        (card.id, bank.id, Decimal("580")), (card_b.id, bank.id, Decimal("580"))]
    # neither pair is the other's twin: housekeeping keeps both
    for row in rows:
        assert aps.check_row(db, row, due_b, _settings()) is None
    db.commit()
    assert db.query(StatementAutoPay).filter_by(status="booked").count() == 2


def test_owner_transfer_to_savings_is_not_a_twin(seed, db_session, world):
    db, (bank, card, stmt) = db_session, world
    savings = seed.account("儲蓄")
    db.commit()
    _transfer(seed, bank, savings, 580, PAY + timedelta(days=1))  # the owner moves the same amount to savings
    db.commit()
    assert _reason(db, stmt.id) is None  # the gate passes
    row, out_leg, in_leg = _book(db, stmt)
    out_id, in_id = out_leg.id, in_leg.id
    assert aps.check_row(db, row, TODAY, _settings()) is None  # and housekeeping does not retire the pair
    db.commit()
    assert row.status == "booked"
    assert db.get(LedgerEntry, out_id) is not None and db.get(LedgerEntry, in_id) is not None
