from datetime import date
from decimal import Decimal

import pytest

from app.services.moze_csv import MozeImportError, parse_moze_csv
from app.services.moze_import_service import required_rates
from tests.helpers import _entries, _import

DAY = date(2026, 7, 10)


def _card(moze, *rows: str) -> bytes:
    return moze.csv(moze.opening("華航卡", "TWD", "0"), *rows)


def test_required_rates_lists_only_foreign_rows(moze):
    parsed = parse_moze_csv(
        _card(
            moze,
            moze.row("華航卡", "JPY", "支出", "-1800", main="飲食", date="2026/07/10"),
            moze.row("華航卡", "TWD", "支出", "-100", main="飲食"),
        )
    )
    assert required_rates(parsed) == {(DAY, "JPY", "TWD")}


def test_jpy_expense_on_twd_card_is_converted_keeping_the_original(db_session, moze):
    summary = _import(
        db_session,
        _card(moze, moze.row("華航卡", "JPY", "支出", "-1800", main="飲食", date="2026/07/10")),
        rates={(DAY, "JPY", "TWD"): Decimal("0.2")},
    )

    [entry] = _entries(db_session, "華航卡")
    assert (entry.amount, entry.currency) == (Decimal("-360.0000"), "TWD")
    assert (entry.original_amount, entry.original_currency) == (Decimal("-1800.0000"), "JPY")
    assert (entry.fx_rate, entry.fx_source) == (Decimal("0.2000000000"), "fx_api")
    [account] = summary["accounts"]
    assert (account["balance"], account["converted_entry_count"], account["converted_amount"]) == (
        "-360.0000", 1, "-360.0000",
    )


def test_conversion_rounds_half_up_to_four_decimals(db_session, moze):
    _import(
        db_session,
        _card(moze, moze.row("華航卡", "USD", "支出", "-21.35", main="訂閱", date="2026/07/10")),
        rates={(DAY, "USD", "TWD"): Decimal("32.1234567891")},
    )
    [entry] = _entries(db_session, "華航卡")
    # -21.35 × 32.1234567891 = -685.835802447... → -685.8358
    assert entry.amount == Decimal("-685.8358")


def test_fee_child_uses_the_same_rate_and_keeps_its_own_original(db_session, moze):
    _import(
        db_session,
        _card(moze, moze.row("華航卡", "JPY", "支出", "-1800", fee="-27", main="飲食", date="2026/07/10")),
        rates={(DAY, "JPY", "TWD"): Decimal("0.2")},
    )
    expense, fee = _entries(db_session, "華航卡")
    assert (fee.kind, fee.parent_entry_id) == ("fee", expense.id)
    assert (fee.amount, fee.original_amount, fee.original_currency) == (Decimal("-5.4000"), Decimal("-27.0000"), "JPY")
    assert (fee.fx_rate, fee.fx_source) == (Decimal("0.2000000000"), "fx_api")


def test_transfer_pairing_uses_the_rows_own_currency_and_amount(db_session, moze):
    summary = _import(
        db_session,
        moze.csv(
            moze.opening("台幣", "TWD", "0"),
            moze.opening("日圓", "JPY", "0"),
            moze.row("台幣", "TWD", "轉出", "-10000", main="轉帳", date="2026/07/10"),
            moze.row("日圓", "TWD", "轉入", "10000", main="轉帳", date="2026/07/10"),
        ),
        rates={(DAY, "TWD", "JPY"): Decimal("4.6")},
    )
    [out_leg] = _entries(db_session, "台幣")
    [in_leg] = _entries(db_session, "日圓")
    assert summary["pairing"] == {"pass1": 1, "pass2": 0, "pass3": 0}
    assert out_leg.transfer_group_id == in_leg.transfer_group_id
    assert (in_leg.amount, in_leg.currency, in_leg.original_amount) == (Decimal("46000.0000"), "JPY", Decimal("10000.0000"))


def test_missing_rate_fails_naming_row_pair_and_date(db_session, moze):
    with pytest.raises(MozeImportError, match="row 3: no FX rate for JPY→TWD on 2026-07-10"):
        _import(db_session, _card(moze, moze.row("華航卡", "JPY", "支出", "-1", main="飲食", date="2026/07/10")), rates={})
