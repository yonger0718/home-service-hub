"""Template lines (spec "Template lines") and the schedule lock helpers (design D32)."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.services import schedule_locks as locks
from app.services import schedule_templates as templates
from app.services.errors import NotFoundError, ValidationError
from app.services.moze_import_service import IMPORT_LOCK_KEY


def _template(*lines) -> dict:
    return {"lines": list(lines), "description": None, "tags": []}


def _field(exc_info) -> str:
    return exc_info.value.field


@pytest.fixture()
def loan(seed):
    bank = seed.account("薪轉")
    lender = seed.counterparty("範例銀行")
    payable = seed.entry(bank, "300000", kind="payable", counterparty_id=lender.id, name="信貸")
    return bank, lender, payable


def test_loan_repayment_template_is_accepted(db_session, seed, loan):
    # Spec "Loan repayment template".
    bank, _, payable = loan
    template = templates.normalize_template(
        _template(
            {"kind": "repayment", "account_id": bank.id, "amount": Decimal("8333"), "currency": "TWD", "loan_entry_id": payable.id},
            {"kind": "interest", "account_id": bank.id, "amount": Decimal("620"), "currency": "TWD"},
        )
    )
    templates.validate_template(db_session, template)
    assert templates.loan_line(template) == (0, template["lines"][0])
    assert templates.template_amounts(template) == ["8333", "620"]


def test_currency_mismatch_names_the_line(db_session, seed):
    # Spec "Currency mismatch refused".
    yen = seed.account("日幣現金", currency="JPY")
    with pytest.raises(ValidationError) as exc:
        templates.validate_template(db_session, _template(seed.line("expense", yen, "1000", currency="TWD")))
    assert _field(exc) == "lines[0].currency"


def test_missing_or_archived_account_refused(db_session, seed):
    old = seed.account("舊卡", is_archived=True)
    wallet = seed.account()
    for line in (seed.line("expense", wallet, "1", account_id=9999), seed.line("expense", old, "1")):
        with pytest.raises(ValidationError) as exc:
            templates.validate_template(db_session, _template(seed.line("income", wallet, "5"), line))
        assert _field(exc) == "lines[1].account_id"


def test_transfer_lines(db_session, seed):
    pay, broker, yen = seed.account("薪轉"), seed.account("交割"), seed.account("日幣", currency="JPY")
    cases = [
        (seed.line("transfer", pay, "15000"), "lines[0].to_account_id"),
        (seed.line("transfer", pay, "15000", to_account_id=pay.id), "lines[0].to_account_id"),
        (seed.line("transfer", pay, "15000", to_account_id=yen.id), "lines[0].to_amount"),
        (seed.line("transfer", pay, "15000", to_account_id=broker.id, to_amount="15000"), "lines[0].to_amount"),
        (seed.line("expense", pay, "15000", to_account_id=broker.id), "lines[0].to_account_id"),
    ]
    for line, field in cases:
        with pytest.raises(ValidationError) as exc:
            templates.validate_template(db_session, _template(line))
        assert _field(exc) == field
    templates.validate_template(db_session, _template(seed.line("transfer", pay, "15000", to_account_id=broker.id)))
    templates.validate_template(db_session, _template(seed.line("transfer", pay, "15000", to_account_id=yen.id, to_amount="70000")))


def test_receivable_and_payable_lines_need_a_counterparty(db_session, seed):
    wallet = seed.account()
    with pytest.raises(ValidationError) as exc:
        templates.validate_template(db_session, _template(seed.line("receivable", wallet, "100")))
    assert _field(exc) == "lines[0].counterparty_id"
    alan = seed.counterparty("Alan")
    with pytest.raises(ValidationError) as exc:
        templates.validate_template(db_session, _template(seed.line("expense", wallet, "100", counterparty_id=alan.id)))
    assert _field(exc) == "lines[0].counterparty_id"
    templates.validate_template(db_session, _template(seed.line("receivable", wallet, "100", counterparty_id=alan.id)))


def test_repayment_needs_a_payable_original_in_its_currency(db_session, seed, loan):
    bank, lender, payable = loan
    receivable = seed.entry(bank, "-500", kind="receivable", counterparty_id=lender.id)
    repayment = seed.entry(bank, "-100", kind="payable", counterparty_id=lender.id, settles_entry_id=payable.id, is_settlement=True)
    yen = seed.account("日幣", currency="JPY")
    for line in (
        seed.line("repayment", bank, "8333"),
        seed.line("repayment", bank, "8333", loan_entry_id=receivable.id),
        seed.line("repayment", bank, "8333", loan_entry_id=repayment.id),
        seed.line("repayment", yen, "8333", loan_entry_id=payable.id),
        seed.line("collection", bank, "8333", loan_entry_id=payable.id),
    ):
        with pytest.raises(ValidationError) as exc:
            templates.validate_template(db_session, _template(line))
        assert _field(exc) == "lines[0].loan_entry_id"
    with pytest.raises(ValidationError) as exc:
        templates.validate_template(
            db_session,
            _template(
                seed.line("repayment", bank, "1", loan_entry_id=payable.id),
                seed.line("repayment", bank, "1", loan_entry_id=payable.id),
            ),
        )
    assert _field(exc) == "lines[1].kind"


def test_category_must_belong_to_the_line_kind(db_session, seed, loan):
    bank, _, payable = loan
    food = seed.category("飲食")
    loans = seed.category("貸款", kind="payable")
    with pytest.raises(ValidationError) as exc:
        templates.validate_template(
            db_session, _template(seed.line("repayment", bank, "8333", loan_entry_id=payable.id, category_id=food.id))
        )
    assert _field(exc) == "lines[0].category_id"
    templates.validate_template(
        db_session, _template(seed.line("repayment", bank, "8333", loan_entry_id=payable.id, category_id=loans.id))
    )


def test_line_count_and_amount_format(db_session, seed):
    wallet = seed.account()
    with pytest.raises(ValidationError) as exc:
        templates.validate_template(db_session, _template())
    assert _field(exc) == "lines"
    with pytest.raises(ValidationError) as exc:
        templates.validate_template(db_session, _template(*[seed.line("expense", wallet, "1")] * 11))
    assert _field(exc) == "lines"
    for amount in ("0", "-5", "1.23456", "abc"):
        with pytest.raises(ValidationError) as exc:
            templates.validate_template(db_session, _template(seed.line("expense", wallet, amount)))
        assert _field(exc) == "lines[0].amount"


def test_normalize_fills_every_key_with_plain_amounts():
    template = templates.normalize_template(
        {"lines": [{"kind": "expense", "account_id": 1, "amount": Decimal("390.0000"), "currency": "TWD"}], "tags": ["訂閱"]}
    )
    assert set(template["lines"][0]) == set(templates.LINE_KEYS)
    assert template["lines"][0]["amount"] == "390"
    assert (template["description"], template["tags"]) == (None, ["訂閱"])


def test_check_amounts_aligns_with_the_lines():
    # Spec "Override length checked".
    template = {"lines": [{"kind": "repayment"}, {"kind": "interest"}], "description": None, "tags": []}
    for amounts in (["8333"], ["8333", "-1"], ["0", "0"], ["8333", "1.00001"]):
        with pytest.raises(ValidationError) as exc:
            templates.check_amounts(template, amounts)
        assert _field(exc) == "amounts"
    assert templates.check_amounts(template, [Decimal("8333.0000"), "0"]) == ["8333", "0"]
    assert templates.check_amounts(template, None) is None


def test_realign_keeps_same_kind_at_the_same_index():
    old = {"lines": [{"kind": "repayment", "amount": "8333"}, {"kind": "interest", "amount": "620"}]}
    same = {"lines": [{"kind": "repayment", "amount": "9000"}, {"kind": "interest", "amount": "600"}]}
    swapped = {"lines": [{"kind": "expense", "amount": "100"}, {"kind": "interest", "amount": "600"}]}
    single = {"lines": [{"kind": "expense", "amount": "100"}]}
    assert templates.realign_override(old, same, ["8345", "598"]) == ["8345", "598"]
    assert templates.realign_override(old, swapped, ["8345", "598"]) == ["100", "598"]
    assert templates.realign_override(old, single, ["8345", "598"]) is None
    assert templates.realign_override(old, same, None) is None


def test_import_key_is_refused_while_an_import_holds_it(db_session, pg_engine):
    assert locks.import_key_free(pg_engine) is True
    with pg_engine.connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(:key)"), {"key": IMPORT_LOCK_KEY})
        try:
            with pytest.raises(locks.ImportRunningError) as exc:
                locks.take_import_key_shared(db_session)
            assert str(exc.value) == "import_running"
            assert locks.import_key_free(pg_engine) is False
        finally:
            holder.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": IMPORT_LOCK_KEY})
            holder.commit()
    db_session.rollback()
    locks.take_import_key_shared(db_session)
    assert locks.import_key_free(pg_engine) is True


def test_unknown_rows_are_not_found(db_session):
    with pytest.raises(NotFoundError):
        locks.lock_definition(db_session, 424242)
    with pytest.raises(NotFoundError):
        locks.get_instance(db_session, 424242)
    assert locks.lock_instance(db_session, 424242) is None
