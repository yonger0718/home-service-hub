from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import (
    Account,
    AccountGroup,
    Category,
    Counterparty,
    EntryRewardRule,
    LedgerEntry,
    Preference,
    Project,
    RewardRule,
)
from app.services import ledger_service
from app.services.moze_backup_import_service import moze_uuid
from app.services.moze_csv import MozeImportError
from tests.helpers import _import_backup


def _account(session, name: str) -> Account:
    return session.scalar(select(Account).where(Account.name == name))


def _card(backup, **fields) -> dict:
    return backup.account(
        "A-CARD", "華航卡", isCreditAccount=True, startDay=15, paymentDeadlineType=1, paymentDeadline=20,
        creditLimit=300000, combinedAccount="A-MAIN", isCurrencyFeeEnabled=True, feePercentage=1.5,
        feeCalculation=1, group="G-CARD", creditSharingID="CS-1", autoPaidAccount="A-BANK", **fields,
    )


def _settings_backup(backup, **overrides):
    accounts = overrides.pop("accounts", None) or [
        backup.account("A-BANK", "台新", group="G-BANK"),
        backup.account("A-MAIN", "主卡", isCreditAccount=True, startDay=15, group="G-CARD"),
        _card(backup),
    ]
    return backup.data(
        groups=[
            backup.group("G-BANK", "APP_GROUP_BANK"),
            backup.group("G-CARD", "APP_GROUP_CREDIT_CARD", sequence=2),
            backup.group("G-ARCH", "ARCHIVE", sequence=99),
            backup.group("G-MINE", "我的群組", sequence=3),
        ],
        accounts=accounts,
        **overrides,
    )


def test_groups_are_named_by_the_map_and_archive_is_not_a_group(db_session, backup):
    _import_backup(db_session, _settings_backup(backup))
    groups = {g.name: (g.sort_order, g.moze_id) for g in db_session.scalars(select(AccountGroup))}
    assert groups == {"銀行": (1, "G-BANK"), "信用卡": (2, "G-CARD"), "我的群組": (3, "G-MINE")}


def test_credit_card_settings_seeded(db_session, backup):
    summary = _import_backup(db_session, _settings_backup(backup))

    card, main, bank = (_account(db_session, n) for n in ("華航卡", "主卡", "台新"))
    # MOZE startDay 15 is the first day of the period, so the statement closes on the 14th
    assert (card.is_credit, card.closing_day, card.due_rule, card.due_value) == (True, 14, "days_after_closing", 20)
    assert card.credit_limit == Decimal("300000.0000")
    assert (card.combined_account_id, card.auto_pay_account_id) == (main.id, bank.id)
    assert card.credit_sharing_id == moze_uuid("CS-1")
    assert (card.fx_fee_pct, card.fx_fee_rounding) == (Decimal("1.500"), "round")
    assert (card.icon, card.moze_id, card.currency) == ("💳", "A-CARD", "TWD")
    assert db_session.get(AccountGroup, card.group_id).name == "信用卡"
    assert (bank.closing_day, bank.due_rule, bank.credit_limit, bank.icon) == (None, None, None, "🏦")
    assert summary["accounts_created"] == ["台新", "主卡", "華航卡"]


@pytest.mark.parametrize("credit", [True, False])
@pytest.mark.parametrize(("start_day", "closing_day"), [(7, 6), (31, 30), (2, 1), (1, None)])
def test_closing_day_is_the_day_before_moze_start_day(db_session, backup, credit, start_day, closing_day):
    # startDay is the first day of the statement period; startDay 1 is the calendar month (closing on the last day)
    _import_backup(db_session, backup.data(accounts=[backup.account("A-1", "帳戶", isCreditAccount=credit, startDay=start_day)]))
    assert _account(db_session, "帳戶").closing_day == closing_day


def test_system_accounts_are_archived_and_excluded_from_totals(db_session, backup):
    data = backup.data(accounts=[backup.account("A-AR", "應收應付款項", type=3), backup.account("A-INST", "分期帳款", type=4)])
    _import_backup(db_session, data)
    for name in ("應收應付款項", "分期帳款"):
        account = _account(db_session, name)
        assert (account.is_archived, account.include_in_total) == (True, False)


def test_existing_csv_account_is_matched_by_name_and_claimed(db_session, backup):
    db_session.add(Account(name="台新", currency="TWD", opening_balance=Decimal("5")))
    db_session.commit()
    _import_backup(db_session, _settings_backup(backup))
    assert db_session.scalars(select(Account.name).order_by(Account.id)).all()[0] == "台新"
    assert _account(db_session, "台新").moze_id == "A-BANK"


def test_name_change_in_moze_renames_the_matched_account(db_session, backup):
    _import_backup(db_session, backup.data(accounts=[backup.account("A-1", "舊名")]))
    summary = _import_backup(db_session, backup.data(accounts=[backup.account("A-1", "新名")]))
    assert db_session.scalars(select(Account.name)).all() == ["新名"]
    assert summary["accounts_renamed"] == [{"from": "舊名", "to": "新名"}]


def test_locally_edited_settings_survive_a_reimport(db_session, backup):
    _import_backup(db_session, _settings_backup(backup))
    card = _account(db_session, "華航卡")
    card.closing_day, card.settings_locally_edited = 20, True
    db_session.commit()

    data = _settings_backup(
        backup,
        accounts=[backup.account("A-BANK", "台新"), backup.account("A-MAIN", "主卡"), _card(backup, originalAmount=-500)],
    )
    summary = _import_backup(db_session, data)

    db_session.refresh(card)
    assert card.closing_day == 20
    assert card.opening_balance == Decimal("-500.0000")
    [skipped] = summary["settings_skipped"]
    assert skipped["name"] == "華航卡"
    assert "closing_day: 14 → kept 20" in skipped["differences"]


def test_currency_change_refused_while_a_hermes_entry_remains(db_session, backup):
    _import_backup(db_session, backup.data(accounts=[backup.account("A-1", "錢包", "TWD")]))
    account = _account(db_session, "錢包")
    db_session.add(LedgerEntry(account_id=account.id, kind="expense", amount=Decimal("-1"), currency="TWD",
                               entry_date=date(2026, 9, 1), posted_date=date(2026, 9, 1), source="hermes"))
    db_session.commit()

    with pytest.raises(MozeImportError, match="account '錢包': currency TWD → JPY refused"):
        _import_backup(db_session, backup.data(accounts=[backup.account("A-1", "錢包", "JPY")]))
    db_session.rollback()
    assert _account(db_session, "錢包").currency == "TWD"


def test_currency_follows_the_backup_when_no_entry_remains(db_session, backup):
    _import_backup(db_session, backup.data(accounts=[backup.account("A-1", "錢包", "TWD")]))
    _import_backup(db_session, backup.data(accounts=[backup.account("A-1", "錢包", "JPY")]))
    assert _account(db_session, "錢包").currency == "JPY"


def test_categories_icons_colours_and_classification_defaults(db_session, backup):
    data = backup.data(
        accounts=[backup.account("A-1", "錢包")],
        projects=[backup.project("P-TRIP", "PROJECT_TRAVEL"), backup.project("P-NONE", "NO_PROJECT")],
        categories=[
            backup.category("C-FOOD", "CATEGORY_FOOD", 1, colorHex="F0CD92", sequence=2),
            backup.category("C-FEE", "CATEGORY_SYSTEM_FEE", 9, imageName="Account_19"),
            backup.category("C-PAY", "CATEGORY_CREDIT_CARD_PAYMENT", 7),
        ],
        classifications=[
            backup.classification("K-LUNCH", "午餐", "C-FOOD", defaultAccount="A-1", defaultProject="P-TRIP"),
            backup.classification("K-FOOD", "CATEGORY_FOOD", "C-FOOD"),
            backup.classification("K-FEE", "手續費", "C-FEE"),
        ],
    )
    _import_backup(db_session, data)

    food = db_session.scalar(select(Category).where(Category.moze_id == "C-FOOD"))
    assert (food.kind, food.name, food.icon, food.color, food.sort_order) == ("expense", "飲食", "🍜", "#f0cd92", 2)
    lunch = db_session.scalar(select(Category).where(Category.moze_id == "K-LUNCH"))
    assert (lunch.parent_id, lunch.default_account_id) == (food.id, _account(db_session, "錢包").id)
    assert db_session.get(Project, lunch.default_project_id).name == "旅遊"
    fee = db_session.scalar(select(Category).where(Category.kind == "fee"))
    assert (fee.name, fee.parent_id, fee.icon) == ("手續費", None, "🧾")
    names = sorted(c.name for c in db_session.scalars(select(Category)))
    assert names == ["午餐", "手續費", "飲食"]
    assert db_session.scalars(select(Project.name)).all() == ["旅遊"]


def test_csv_created_categories_are_matched_by_kind_parent_and_name(db_session, backup):
    main = Category(kind="expense", name="飲食")
    db_session.add(main)
    db_session.flush()
    sub = Category(kind="expense", parent_id=main.id, name="午餐")
    db_session.add(sub)
    db_session.commit()

    _import_backup(db_session, backup.data(categories=[backup.category()], classifications=[backup.classification()]))

    assert db_session.scalars(select(Category.id).order_by(Category.id)).all() == [main.id, sub.id]
    assert (db_session.get(Category, sub.id).moze_id, db_session.get(Category, main.id).moze_id) == ("K-LUNCH", "C-FOOD")


def test_counterparties_from_targets(db_session, backup):
    _import_backup(db_session, backup.data(targets=[backup.target("T-1", "Alan"), backup.target("T-2", "Alan")]))
    assert [(c.name, c.moze_id) for c in db_session.scalars(select(Counterparty))] == [("Alan", "T-1")]


def _rules_backup(backup, *rules):
    return backup.data(
        accounts=[backup.account("A-CARD", "華航卡", isCreditAccount=True), backup.account("A-PTS", "點數")],
        projects=[backup.project("P-REWARD", "回饋專案")],
        rules=list(rules),
    )


def test_percent_rule_with_cap(db_session, backup):
    _import_backup(db_session, _rules_backup(backup, backup.rule(
        "B-1", rewardPercentage=0.02, rewardTimeType=2, rewardMonth=1, rewardDay=15, totalRewardLimit=150,
        rewardAccountID="A-PTS", rewardProjectID="P-REWARD", rewardCalculation=4, totalRewardCalculation=2,
    )))
    rule = db_session.scalar(select(RewardRule))
    assert (rule.method, rule.rate, rule.posting, rule.post_month_offset, rule.post_day) == (
        "percent", Decimal("2.0000"), "after_window", 1, 15)
    assert (rule.total_cap, rule.shared_cap_id, rule.window) == (Decimal("150.0000"), None, "statement_cycle")
    assert (rule.txn_rounding, rule.total_rounding) == ("round", "floor")
    assert rule.reward_account_id == _account(db_session, "點數").id
    assert db_session.get(Project, rule.reward_project_id).name == "回饋專案"
    assert (rule.starts_on, rule.ends_on, rule.is_enabled) == (date(2026, 1, 1), date(2026, 12, 31), True)
    card = next(a for a in ledger_service.list_accounts(db_session) if a["name"] == "華航卡")
    assert card["rule_summaries"] == ["回饋 2%"]


def test_fixed_rule_with_shared_cap(db_session, backup):
    _import_backup(db_session, _rules_backup(backup, backup.rule(
        "B-2", type=1, rewardAmount=50, rewardTimeType=0, rewardDelayDays=2, rewardSharingID="S-1")))
    rule = db_session.scalar(select(RewardRule))
    assert (rule.method, rule.fixed_amount, rule.rate) == ("fixed", Decimal("50.0000"), None)
    assert (rule.posting, rule.delay_days, rule.shared_cap_id) == ("after_transaction", 2, moze_uuid("S-1"))


def test_unsupported_threshold_disables_the_rule(db_session, backup):
    summary = _import_backup(db_session, _rules_backup(backup, backup.rule("B-3", totalSpendThreshold=5000)))
    assert db_session.scalar(select(RewardRule)).is_enabled is False
    assert summary["unsupported_rules"] == [{"name": "回饋", "account": "華航卡", "reasons": ["totalSpendThreshold"]}]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [("rewardPeriodType", 1, "unsupported rewardPeriodType 1"), ("rewardTimeType", 1, "unsupported rewardTimeType 1"),
     ("rewardCalculation", 5, "unsupported rewardCalculation 5")],
)
def test_unknown_rule_codes_fail_the_import(db_session, backup, field, value, message):
    with pytest.raises(MozeImportError, match=f"AHBonusReward '回饋': {message}"):
        _import_backup(db_session, _rules_backup(backup, backup.rule("B-1", **{field: value})))


def test_reimport_updates_rules_in_place_and_handles_orphans(db_session, backup):
    _import_backup(db_session, _rules_backup(backup, backup.rule("B-1"), backup.rule("B-2"), backup.rule("B-3")))
    ids = {r.moze_id: r.id for r in db_session.scalars(select(RewardRule))}
    card = _account(db_session, "華航卡")
    manual = LedgerEntry(account_id=card.id, kind="expense", amount=Decimal("-10"), currency="TWD",
                         entry_date=date(2026, 9, 1), posted_date=date(2026, 9, 1), source="manual")
    db_session.add(manual)
    db_session.flush()
    db_session.add(EntryRewardRule(entry_id=manual.id, rule_id=ids["B-2"]))
    db_session.commit()

    summary = _import_backup(db_session, _rules_backup(backup, backup.rule("B-1", rewardPercentage=0.03)))

    rules = {r.moze_id: r for r in db_session.scalars(select(RewardRule))}
    assert set(rules) == {"B-1", "B-2"}
    assert (rules["B-1"].id, rules["B-1"].rate) == (ids["B-1"], Decimal("3.0000"))
    assert (rules["B-2"].id, rules["B-2"].is_enabled) == (ids["B-2"], False)
    assert summary["orphaned_rules"] == [{"name": "回饋", "account": "華航卡"}]
    assert db_session.scalar(select(EntryRewardRule)).rule_id == ids["B-2"]


def test_preference_seeded(db_session, backup):
    _import_backup(db_session, backup.data(preference=backup.preference(
        expenseIncomeColor=1, numberPadType=0, firstWeekday=2, mainCurrency="TWD", hideRewardsOnHome=True,
        isTotalBalanceAbbreviate=False)))
    preference = db_session.get(Preference, 1)
    assert (preference.expense_income_colors, preference.keypad_layout, preference.week_start) == ("green_red", "phone", 1)
    assert (preference.main_currency, preference.hide_rewards_on_timeline, preference.abbreviate_totals) == ("TWD", True, False)


def test_posting_fields_are_normalised_when_the_rule_does_not_post_after_the_window(db_session, backup):
    _import_backup(db_session, _rules_backup(backup, backup.rule("B-1", rewardTimeType=0, rewardMonth=7, rewardDay=0)))
    rule = db_session.scalar(select(RewardRule))
    assert (rule.posting, rule.post_month_offset, rule.post_day) == ("after_transaction", 0, 1)


def test_out_of_range_posting_day_fails_naming_the_rule(db_session, backup):
    with pytest.raises(MozeImportError, match="AHBonusReward '回饋': rewardMonth/rewardDay out of range"):
        _import_backup(db_session, _rules_backup(backup, backup.rule("B-1", rewardTimeType=2, rewardDay=0)))


def test_account_names_swapped_within_one_backup(db_session, backup):
    _import_backup(db_session, backup.data(accounts=[backup.account("A-1", "甲"), backup.account("A-2", "乙")]))
    summary = _import_backup(db_session, backup.data(accounts=[backup.account("A-1", "乙"), backup.account("A-2", "甲")]))
    names = {a.moze_id: a.name for a in db_session.scalars(select(Account))}
    assert names == {"A-1": "乙", "A-2": "甲"}
    assert summary["accounts_renamed"] == [{"from": "甲", "to": "乙"}, {"from": "乙", "to": "甲"}]


def test_rename_to_the_name_a_later_new_account_takes(db_session, backup):
    _import_backup(db_session, backup.data(accounts=[backup.account("A-1", "甲")]))
    summary = _import_backup(db_session, backup.data(accounts=[backup.account("A-1", "乙"), backup.account("A-2", "甲")]))
    names = {a.moze_id: a.name for a in db_session.scalars(select(Account))}
    assert names == {"A-1": "乙", "A-2": "甲"}
    assert summary["accounts_created"] == ["甲"]


def test_deleted_and_recreated_account_renames_and_archives_the_stale_row(db_session, backup):
    _import_backup(db_session, backup.data(accounts=[backup.account("A-OLD-123456", "錢包")]))
    summary = _import_backup(db_session, backup.data(accounts=[backup.account("A-NEW", "錢包")]))
    stale = db_session.scalar(select(Account).where(Account.moze_id == "A-OLD-123456"))
    assert (stale.name, stale.is_archived) == ("錢包 (舊 A-OLD-12)", True)
    assert _account(db_session, "錢包").moze_id == "A-NEW"
    assert {"from": "錢包", "to": "錢包 (舊 A-OLD-12)"} in summary["accounts_renamed"]
    assert "錢包 (舊 A-OLD-12)" in summary["accounts_archived"]


def test_stale_locally_edited_holder_is_renamed_but_not_archived(db_session, backup):
    _import_backup(db_session, backup.data(accounts=[backup.account("A-OLD-123456", "錢包")]))
    old = db_session.scalar(select(Account).where(Account.moze_id == "A-OLD-123456"))
    old.opening_balance, old.settings_locally_edited = Decimal("50"), True
    db_session.flush()
    summary = _import_backup(db_session, backup.data(accounts=[backup.account("A-NEW", "錢包")]))
    stale = db_session.scalar(select(Account).where(Account.moze_id == "A-OLD-123456"))
    assert (stale.name, stale.is_archived, stale.opening_balance) == ("錢包 (舊 A-OLD-12)", False, Decimal("50"))
    assert _account(db_session, "錢包").moze_id == "A-NEW"
    assert {"from": "錢包", "to": "錢包 (舊 A-OLD-12)"} in summary["accounts_renamed"]
    assert "錢包 (舊 A-OLD-12)" not in summary["accounts_archived"]


def test_two_backup_accounts_with_one_final_name_fail(db_session, backup):
    data = backup.data(accounts=[
        backup.account("A-3", "錢包 (A-2)"), backup.account("A-1", "錢包"), backup.account("A-2", "錢包"),
    ])
    with pytest.raises(MozeImportError, match="A-2.*A-3|A-3.*A-2"):
        _import_backup(db_session, data)


def test_builtin_subcategory_keys_import_with_chinese_names_and_unmapped_keys_are_reported(db_session, backup):
    data = backup.data(
        categories=[backup.category("C-FOOD", "CATEGORY_FOOD", 1)],
        classifications=[
            backup.classification("K-BF", "CATEGORY_BREAKFAST", "C-FOOD"),
            backup.classification("K-NEW", "CATEGORY_NEW_THING", "C-FOOD"),
            backup.classification("K-PLAIN", "自訂", "C-FOOD"),
        ],
    )
    summary = _import_backup(db_session, data)
    names = {c.moze_id: c.name for c in db_session.scalars(select(Category))}
    assert (names["K-BF"], names["K-NEW"], names["K-PLAIN"]) == ("早餐", "New Thing", "自訂")
    assert summary["unmapped_category_keys"] == ["CATEGORY_NEW_THING"]


def test_unmapped_category_keys_is_empty_list_when_all_mapped(db_session, backup):
    summary = _import_backup(db_session, backup.data(categories=[backup.category("C-FOOD", "CATEGORY_FOOD", 1)]))
    assert summary["unmapped_category_keys"] == []
