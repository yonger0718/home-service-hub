from datetime import date, time
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import Account, Category, Counterparty, EntryGroup, EntryRewardRule, LedgerEntry, Project, RewardRule
from app.services import ledger_service
from app.services.moze_csv import MozeImportError
from tests.helpers import _by_moze_id, _entries, _import_backup

JPY_DAY = (date(2026, 9, 1), "JPY", "TWD")


def _wallet(backup, **fields):
    return backup.account("A-WALLET", "錢包", **fields)


def _balance(session, name: str) -> Decimal:
    account = session.scalar(select(Account).where(Account.name == name))
    return ledger_service.account_balance(session, account.id, date(2026, 10, 1))


def test_expense_with_fee_and_discount_children(db_session, backup):
    data = backup.data(
        accounts=[_wallet(backup, originalAmount=1000)],
        records=[backup.record("R-1", price=-266, fee=-3, bonus=10, feeName="", bonusName="店家折扣")],
    )
    summary = _import_backup(db_session, data)

    expense, fee, discount = _entries(db_session, "錢包")
    assert (expense.kind, expense.amount, expense.moze_id, expense.source) == ("expense", Decimal("-266.0000"), "R-1", "moze_backup")
    assert (fee.kind, fee.amount, fee.name, fee.parent_entry_id) == ("fee", Decimal("-3.0000"), "手續費", expense.id)
    assert (discount.kind, discount.amount, discount.name) == ("discount", Decimal("10.0000"), "店家折扣")
    assert (fee.entry_date, fee.entry_time, fee.posted_date) == (expense.entry_date, expense.entry_time, expense.posted_date)
    assert [expense.seq + 1, expense.seq + 2] == [fee.seq, discount.seq]
    assert db_session.get(Category, fee.category_id).name == "手續費"
    assert _balance(db_session, "錢包") == Decimal("741.0000")
    assert summary["kind_counts"] == {"discount": 1, "expense": 1, "fee": 1}


def test_record_fields_map_to_entry_columns(db_session, backup):
    data = backup.data(
        accounts=[_wallet(backup)],
        categories=[backup.category()], classifications=[backup.classification()],
        projects=[backup.project("P-1", "日常")], targets=[backup.target("T-1", "Alan")],
        records=[backup.record(
            "R-1", price=-120, date="2026-09-01T12:30:00", chargeDate="2026-09-03T00:00:00", name="",
            store="麥當勞", desc="和同事", tags="#午餐 #公司", invoiceNumber="AB12345678", classification="K-LUNCH",
            project="P-1", target="T-1",
        )],
    )
    _import_backup(db_session, data)

    entry = _by_moze_id(db_session, "R-1")
    assert (entry.entry_date, entry.entry_time, entry.posted_date) == (date(2026, 9, 1), time(12, 30), date(2026, 9, 3))
    assert (entry.name, entry.merchant, entry.description, entry.tags) == (None, "麥當勞", "和同事", ["午餐", "公司"])
    assert entry.invoice_number == "AB12345678"
    assert db_session.get(Category, entry.category_id).name == "午餐"
    assert db_session.get(Project, entry.project_id).name == "日常"
    assert db_session.get(Counterparty, entry.counterparty_id).name == "Alan"


@pytest.mark.parametrize(
    ("record_type", "price", "fields", "kind"),
    [
        (1, 500, {}, "income"), (3, -420, {}, "receivable"), (5, 200, {}, "receivable"), (4, 1000, {}, "payable"),
        (6, -300, {}, "payable"), (7, -5, {}, "balance_adjustment"), (12, -100, {}, "fee"), (16, -15, {}, "fee"),
        (13, 20, {}, "discount"), (14, 8, {}, "reward"), (15, -30, {}, "interest"), (0, 350, {"isRefund": True}, "refund"),
    ],
)
def test_record_types_become_kinds(db_session, backup, record_type, price, fields, kind):
    _import_backup(db_session, backup.data(accounts=[_wallet(backup)], records=[backup.record("R-1", type_=record_type, price=price, **fields)]))
    entry = _by_moze_id(db_session, "R-1")
    assert (entry.kind, entry.amount) == (kind, Decimal(price).quantize(Decimal("0.0001")))


def test_balance_adjustment_imports_the_delta_not_the_resulting_balance(db_session, backup):
    # MOZE type 7 stores price = the balance after the adjustment and total = the delta
    data = backup.data(
        accounts=[_wallet(backup, originalAmount=100)],
        records=[
            backup.record("R-1", price=-30, date="2026-09-01T12:00:00"),
            backup.record("R-ADJ", type_=7, price=90, total=20, date="2026-09-02T12:00:00"),
        ],
    )
    _import_backup(db_session, data)
    adjustment = _by_moze_id(db_session, "R-ADJ")
    assert (adjustment.kind, adjustment.amount) == ("balance_adjustment", Decimal("20.0000"))
    assert _balance(db_session, "錢包") == Decimal("90.0000")


def test_unknown_record_type_fails_naming_type_and_identifier(db_session, backup):
    with pytest.raises(MozeImportError, match="AHRecord 'R-8': unknown record type 8"):
        _import_backup(db_session, backup.data(accounts=[_wallet(backup)], records=[backup.record("R-8", type_=8)]))


def test_future_rows_are_skipped_and_counted_per_type(db_session, backup):
    data = backup.data(
        accounts=[_wallet(backup)],
        records=[
            backup.record("R-NOW", date="2026-10-01T23:59:00"),
            backup.record("R-LOAN", type_=6, price=-3000, date="2026-11-09T00:00:00"),
            backup.record("R-INT", type_=15, price=-12, date="2026-11-09T00:00:00"),
        ],
    )
    summary = _import_backup(db_session, data)
    assert [e.moze_id for e in _entries(db_session, "錢包")] == ["R-NOW"]
    assert summary["skipped_future"] == {"6": 1, "15": 1}


def test_disabled_record_is_skipped_and_counted_per_type(db_session, backup):
    data = backup.data(
        accounts=[_wallet(backup, originalAmount=100)],
        records=[backup.record("R-1", price=-30, isEnabled=False), backup.record("R-2", price=-10)],
    )
    summary = _import_backup(db_session, data)
    assert [e.moze_id for e in _entries(db_session, "錢包")] == ["R-2"]
    assert summary["disabled_skipped"] == {"0": 1}
    assert summary["needs_review"] == {"count": 0, "reasons": {}}
    assert _balance(db_session, "錢包") == Decimal("90.0000")


def test_disabled_transfer_leg_skips_the_pair(db_session, backup):
    data = backup.data(
        accounts=[_wallet(backup), backup.account("A-BANK", "銀行")],
        records=[
            backup.record("R-OUT", "A-WALLET", type_=2, price=-500, isEnabled=False),
            backup.record("R-IN", "A-BANK", type_=2, price=500),
        ],
        transfers=[backup.transfer("X-1", "R-OUT", "R-IN")],
    )
    summary = _import_backup(db_session, data)
    assert _entries(db_session, "錢包") == [] and _entries(db_session, "銀行") == []
    assert summary["disabled_skipped"] == {"2": 2}
    assert summary["transfers"] == 0
    assert summary["needs_review"]["reasons"] == {}


def test_children_of_a_disabled_record_are_skipped_with_it(db_session, backup):
    data = backup.data(
        accounts=[_wallet(backup)],
        records=[
            backup.record("R-1", price=-100, fee=-3, feeID="R-FEE", isEnabled=False),
            backup.record("R-FEE", type_=12, price=-5),
            backup.record("R-RW", type_=14, price=2, rewardRecordID="R-1"),
        ],
    )
    summary = _import_backup(db_session, data)
    assert _entries(db_session, "錢包") == []
    assert summary["disabled_skipped"] == {"0": 1}
    assert summary["kind_counts"] == {}


def test_jpy_charge_on_a_twd_card_uses_moze_amount(db_session, backup):
    data = backup.data(
        accounts=[backup.account("A-CARD", "華航卡")],
        records=[backup.record("R-1", "A-CARD", price=-5390, fee=-81, currency="JPY", currencyConversion="R-1")],
        conversions=[backup.conversion("R-1", 0.2163)],
    )
    _import_backup(db_session, data, rates={JPY_DAY: Decimal("0.2150")})

    expense, fee = _entries(db_session, "華航卡")
    assert (expense.amount, expense.original_amount, expense.original_currency) == (Decimal("-1165.8570"), Decimal("-5390.0000"), "JPY")
    assert (expense.fx_rate, expense.fx_source, expense.currency) == (Decimal("0.2163000000"), "moze_backup", "TWD")
    assert (fee.amount, fee.original_amount, fee.fx_rate, fee.fx_source) == (Decimal("-17.5203"), Decimal("-81.0000"), Decimal("0.2163000000"), "moze_backup")


def test_jpy_record_on_a_jpy_account_is_not_converted(db_session, backup):
    data = backup.data(
        accounts=[backup.account("A-YEN", "日幣", "JPY")],
        records=[backup.record("R-1", "A-YEN", price=-1500, currency="JPY", currencyConversion="R-1")],
        conversions=[backup.conversion("R-1", 0.2163)],
    )
    _import_backup(db_session, data)
    entry = _by_moze_id(db_session, "R-1")
    assert (entry.amount, entry.original_amount, entry.fx_rate, entry.fx_source) == (Decimal("-1500.0000"), None, None, None)


def test_record_without_moze_rate_uses_the_fx_cache(db_session, backup):
    data = backup.data(
        accounts=[backup.account("A-YEN", "日幣", "JPY")],
        records=[backup.record("R-1", "A-YEN", price=-10, currency="USD")],
    )
    _import_backup(db_session, data, rates={(date(2026, 9, 1), "USD", "JPY"): Decimal("147.5")})
    entry = _by_moze_id(db_session, "R-1")
    assert (entry.amount, entry.original_amount, entry.original_currency, entry.fx_source) == (
        Decimal("-1475.0000"), Decimal("-10.0000"), "USD", "fx_api")


def test_zero_backup_rate_falls_back_to_the_cached_rate(db_session, backup):
    data = backup.data(
        accounts=[backup.account("A-CARD", "華航卡")],
        records=[backup.record("R-ZERO", "A-CARD", price=-1000, currency="JPY", currencyConversion="R-ZERO")],
        conversions=[backup.conversion("R-ZERO", 0)],
    )
    summary = _import_backup(db_session, data, rates={JPY_DAY: Decimal("0.2163")}, strict=True)
    entry = _by_moze_id(db_session, "R-ZERO")
    assert (entry.amount, entry.original_amount, entry.original_currency, entry.fx_rate, entry.fx_source) == (
        Decimal("-216.3000"), Decimal("-1000.0000"), "JPY", Decimal("0.2163000000"), "fx_api")
    assert summary["fx_outliers"] == []
    assert summary["fx_backup_rate_missing"] == {"count": 1, "accounts": ["華航卡"], "reasons": {"zero_rate": 1}}


def test_inverted_backup_conversion_is_normalised(db_session, backup):
    data = backup.data(
        accounts=[backup.account("A-CARD", "華航卡")],
        records=[backup.record("R-INV", "A-CARD", price=-1000, currency="JPY", currencyConversion="R-INV")],
        conversions=[backup.conversion("R-INV", 5.0, base="JPY", target="TWD")],
    )
    summary = _import_backup(db_session, data, rates={JPY_DAY: Decimal("0.2")}, strict=True)
    entry = _by_moze_id(db_session, "R-INV")
    assert (entry.amount, entry.original_amount, entry.fx_rate, entry.fx_source) == (
        Decimal("-200.0000"), Decimal("-1000.0000"), Decimal("0.2"), "moze_backup")
    assert summary["fx_outliers"] == []
    assert summary["fx_backup_rate_missing"] == {"count": 0, "accounts": [], "reasons": {}}


def test_unrelated_conversion_pair_falls_back(db_session, backup):
    data = backup.data(
        accounts=[backup.account("A-CARD", "華航卡")],
        records=[backup.record("R-PAIR", "A-CARD", price=-1000, currency="JPY", currencyConversion="R-PAIR")],
        conversions=[backup.conversion("R-PAIR", 1.08, base="USD", target="EUR")],
    )
    summary = _import_backup(db_session, data, rates={JPY_DAY: Decimal("0.2163")}, strict=True)
    entry = _by_moze_id(db_session, "R-PAIR")
    assert (entry.amount, entry.fx_rate, entry.fx_source) == (Decimal("-216.3000"), Decimal("0.2163000000"), "fx_api")
    assert summary["fx_backup_rate_missing"] == {"count": 1, "accounts": ["華航卡"], "reasons": {"pair": 1}}


def _outlier_data(backup):
    return backup.data(
        accounts=[backup.account("A-CARD", "華航卡")],
        records=[backup.record("R-ODD", "A-CARD", price=-1000, currency="JPY", currencyConversion="R-ODD")],
        conversions=[backup.conversion("R-ODD", 2.163)],
    )


def test_moze_rate_far_from_the_daily_rate_fails_naming_the_record(db_session, backup):
    with pytest.raises(MozeImportError, match="AHRecord 'R-ODD': MOZE rate 2.163 differs from the cached JPY→TWD rate"):
        _import_backup(db_session, _outlier_data(backup), rates={JPY_DAY: Decimal("0.2163")})


def test_fx_outliers_can_be_allowed_and_are_reported(db_session, backup):
    summary = _import_backup(db_session, _outlier_data(backup), rates={JPY_DAY: Decimal("0.2163")}, allow_fx_outliers=True)
    assert summary["fx_outliers"] == [{"moze_id": "R-ODD", "date": "2026-09-01", "currency": "JPY", "moze_rate": "2.163", "cached_rate": "0.2163"}]
    assert _by_moze_id(db_session, "R-ODD").amount == Decimal("-2163.0000")


def _transfer_data(backup, *, exchange_rate=4.62, with_in=True):
    records = [backup.record("R-OUT", "A-TWD", 2, -10000, transferID="X-1")]
    if with_in:
        records.append(backup.record("R-IN", "A-JPY", 2, 46200, currency="JPY", transferID="X-1"))
    return backup.data(
        accounts=[backup.account("A-TWD", "台幣"), backup.account("A-JPY", "日幣", "JPY")],
        records=records,
        transfers=[backup.transfer("X-1", "R-OUT", "R-IN", exchange_rate)],
    )


def test_transfer_pair_from_the_backup(db_session, backup):
    summary = _import_backup(db_session, _transfer_data(backup))
    out_leg, in_leg = _by_moze_id(db_session, "R-OUT"), _by_moze_id(db_session, "R-IN")
    assert out_leg.transfer_group_id is not None and out_leg.transfer_group_id == in_leg.transfer_group_id
    assert (out_leg.kind, in_leg.kind, out_leg.needs_review, in_leg.needs_review) == ("transfer_out", "transfer_in", False, False)
    assert (in_leg.original_amount, in_leg.original_currency, in_leg.fx_rate) == (Decimal("10000.0000"), "TWD", Decimal("4.6200000000"))
    assert (out_leg.original_amount, out_leg.original_currency, out_leg.fx_rate) == (Decimal("-46200.0000"), "JPY", Decimal("0.2164502165"))
    assert (out_leg.fx_source, in_leg.fx_source) == ("moze_backup", "moze_backup")
    assert (summary["transfers"], summary["transfer_rate_mismatches"]) == (1, 0)


def test_transfer_rate_disagreement_is_only_reported(db_session, backup):
    summary = _import_backup(db_session, _transfer_data(backup, exchange_rate=4.0))
    assert summary["transfer_rate_mismatches"] == 1
    assert _by_moze_id(db_session, "R-IN").amount == Decimal("46200.0000")


def test_unpaired_transfer_leg_needs_review(db_session, backup):
    data = backup.data(accounts=[_wallet(backup)], records=[backup.record("R-LONE", type_=2, price=-500)])
    summary = _import_backup(db_session, data)
    entry = _by_moze_id(db_session, "R-LONE")
    assert (entry.transfer_group_id, entry.needs_review) == (None, True)
    assert summary["needs_review"]["reasons"] == {"unpaired_transfer": 1}


def test_fee_record_referenced_by_fee_id_becomes_a_child(db_session, backup):
    data = backup.data(
        accounts=[_wallet(backup)],
        records=[
            backup.record("R-FX-FEE", type_=16, price=-15, date="2026-09-01T12:00:00"),
            backup.record("R-BUY", price=-1000, feeID="R-FX-FEE", date="2026-09-01T12:00:00"),
        ],
    )
    _import_backup(db_session, data)
    parent, child = _entries(db_session, "錢包")
    assert (parent.moze_id, child.moze_id, child.kind, child.parent_entry_id) == ("R-BUY", "R-FX-FEE", "fee", parent.id)


@pytest.mark.parametrize("side", ["refund", "original"])
def test_refund_links_to_the_original_from_either_side(db_session, backup, side):
    original = backup.record("R-BUY", price=-350, refundID="R-BACK" if side == "original" else None)
    refund = backup.record("R-BACK", price=350, isRefund=True, refundID="R-BUY" if side == "refund" else None,
                           date="2026-09-05T10:00:00")
    summary = _import_backup(db_session, backup.data(accounts=[_wallet(backup)], records=[original, refund]))
    entry = _by_moze_id(db_session, "R-BACK")
    assert (entry.kind, entry.refunds_entry_id) == ("refund", _by_moze_id(db_session, "R-BUY").id)
    expected = "refund_record_points_to_original" if side == "refund" else "original_points_to_refund"
    assert summary["confirmed_maps"]["refund_direction"] == expected


def test_collection_settles_the_receivable_it_names(db_session, backup):
    data = backup.data(
        accounts=[_wallet(backup)], targets=[backup.target("T-1", "Alan")],
        records=[
            backup.record("R-LEND", type_=3, price=-420, target="T-1"),
            backup.record("R-BACK", type_=5, price=200, target="T-1", relatedID="R-LEND"),
            backup.record("R-OTHER", type_=5, price=100, target="T-1"),
        ],
    )
    _import_backup(db_session, data)
    assert _by_moze_id(db_session, "R-BACK").settles_entry_id == _by_moze_id(db_session, "R-LEND").id
    assert _by_moze_id(db_session, "R-OTHER").settles_entry_id is None


def test_collections_and_repayments_are_flagged_as_settlements(db_session, backup):
    data = backup.data(
        accounts=[_wallet(backup)], targets=[backup.target("T-1", "Alan")],
        records=[
            backup.record("R-LEND", type_=3, price=-420, target="T-1"),
            backup.record("R-BACK", type_=5, price=150, target="T-1"),
        ],
    )
    _import_backup(db_session, data)
    collection = _by_moze_id(db_session, "R-BACK")
    assert (collection.kind, collection.amount) == ("receivable", Decimal("150.0000"))
    assert collection.is_settlement is True
    assert collection.settles_entry_id is None
    assert _by_moze_id(db_session, "R-LEND").is_settlement is False


def test_refund_of_a_settling_type_is_not_a_settlement(db_session, backup):
    data = backup.data(
        accounts=[_wallet(backup)], targets=[backup.target("T-1", "Alan")],
        records=[backup.record("R-BACK", type_=5, price=150, target="T-1", isRefund=True)],
    )
    _import_backup(db_session, data)
    refund = _by_moze_id(db_session, "R-BACK")
    assert (refund.kind, refund.is_settlement) == ("refund", False)


def test_packages_become_groups(db_session, backup):
    data = backup.data(
        accounts=[_wallet(backup)],
        records=[backup.record(f"R-{n}", price=-n) for n in range(1, 6)],
        packages=[
            backup.package("PK-SPLIT", ["R-1", "R-2"], name="聚餐", store="餐廳"),
            backup.package("PK-CLAIM", ["R-3"], type_=4),
            backup.package("PK-INST", ["R-4"], event_type=2),
            backup.package("PK-EMPTY", ["R-GONE"]),
        ],
    )
    summary = _import_backup(db_session, data)
    groups = {g.moze_id: g for g in db_session.scalars(select(EntryGroup))}
    assert {k: g.kind for k, g in groups.items()} == {"PK-SPLIT": "split", "PK-CLAIM": "reward_claim", "PK-INST": "installment"}
    assert (groups["PK-SPLIT"].name, groups["PK-SPLIT"].merchant) == ("聚餐", "餐廳")
    assert {_by_moze_id(db_session, k).group_id for k in ("R-1", "R-2")} == {groups["PK-SPLIT"].id}
    assert _by_moze_id(db_session, "R-5").group_id is None
    assert summary["groups"] == 3


def _reward_data(backup, *, reward_id="B-1", source_id="R-BUY"):
    return backup.data(
        accounts=[backup.account("A-CARD", "華航卡", isCreditAccount=True)],
        rules=[backup.rule("B-1", "A-CARD"), backup.rule("B-2", "A-CARD")],
        records=[
            backup.record("R-BUY", "A-CARD", price=-1000, bonusRewards=["B-1", "B-2", "B-1", "B-GONE"]),
            backup.record("R-REWARD", "A-CARD", type_=14, price=10, rewardID=reward_id, rewardRecordID=source_id,
                          chargeDate="2026-09-15T00:00:00"),
        ],
    )


def test_reward_links_rule_and_source_and_attachments_are_rows(db_session, backup):
    summary = _import_backup(db_session, _reward_data(backup))
    reward, source = _by_moze_id(db_session, "R-REWARD"), _by_moze_id(db_session, "R-BUY")
    rule_ids = {r.moze_id: r.id for r in db_session.scalars(select(RewardRule))}
    assert (reward.reward_rule_id, reward.reward_source_entry_id, reward.needs_review) == (rule_ids["B-1"], source.id, False)
    attached = sorted(a.rule_id for a in db_session.scalars(select(EntryRewardRule).where(EntryRewardRule.entry_id == source.id)))
    assert attached == sorted([rule_ids["B-1"], rule_ids["B-2"]])
    assert summary["attachments"] == 2


def test_reward_with_missing_rule_or_source_needs_review(db_session, backup):
    summary = _import_backup(db_session, _reward_data(backup, reward_id="B-NONE", source_id="R-NONE"))
    reward = _by_moze_id(db_session, "R-REWARD")
    assert (reward.reward_rule_id, reward.reward_source_entry_id, reward.needs_review) == (None, None, True)
    assert summary["needs_review"]["reasons"] == {"reward_rule_missing": 1, "reward_source_missing": 1}
    assert summary["reward_source_from_package"] == 0


def test_reward_on_a_split_purchase_links_to_the_primary_member(db_session, backup):
    data = backup.data(
        accounts=[backup.account("A-CARD", "華航卡", isCreditAccount=True)],
        rules=[backup.rule("B-1", "A-CARD")],
        records=[
            backup.record("R-LATE", "A-CARD", price=-300, date="2026-09-02T09:00:00"),
            backup.record("R-EARLY", "A-CARD", price=-700, date="2026-09-01T18:00:00"),
            backup.record("R-REWARD", "A-CARD", type_=14, price=10, rewardID="B-1", rewardRecordID="PK-SPLIT",
                          date="2026-09-03T00:00:00"),
        ],
        packages=[backup.package("PK-SPLIT", ["R-LATE", "R-EARLY"])],
    )
    summary = _import_backup(db_session, data, strict=True)
    reward, primary = _by_moze_id(db_session, "R-REWARD"), _by_moze_id(db_session, "R-EARLY")
    assert (reward.reward_source_entry_id, reward.needs_review) == (primary.id, False)
    assert summary["reward_source_from_package"] == 1
    assert summary["needs_review"]["count"] == 0


def test_record_on_an_unknown_account_fails(db_session, backup):
    with pytest.raises(MozeImportError, match="AHRecord 'R-1': account 'A-NONE' is not in the backup"):
        _import_backup(db_session, backup.data(accounts=[_wallet(backup)], records=[backup.record("R-1", "A-NONE")]))


def test_tag_delimiter_is_reported(db_session, backup):
    data = backup.data(accounts=[_wallet(backup)], records=[backup.record("R-1", tags="a,b"), backup.record("R-2", tags="c,d")])
    assert _import_backup(db_session, data)["confirmed_maps"]["tag_delimiter"] == ","
    assert db_session.scalar(select(func.count()).select_from(LedgerEntry)) == 2
