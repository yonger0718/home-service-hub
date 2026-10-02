from datetime import datetime
from decimal import Decimal

import pytest

from app.services.moze_backup_json import (
    CATEGORY_TYPE_TO_KIND,
    DUE_RULE_MAP,
    POSTING_MAP,
    RECORD_TYPE_TO_KIND,
    ROUNDING_MAP,
    TAIPEI,
    load_backup_json,
    parse_backup_doc,
    record_kind,
    split_tags,
    tag_delimiter,
)
from app.services.moze_csv import MozeImportError


def test_loads_converter_json_into_typed_rows(backup, tmp_path):
    doc = backup.doc(
        accounts=[backup.account(originalAmount=2000.5)],
        records=[backup.record(price=-5390, currency="JPY", currencyConversion="R-1")],
        conversions=[backup.conversion(rate=0.2163)],
    )
    data = load_backup_json(backup.write(tmp_path, doc))

    assert data.exported_at == datetime(2026, 10, 1, 17, 0, 37, tzinfo=TAIPEI)
    [account] = data.accounts
    assert account["originalAmount"] == Decimal("2000.5")
    assert account["cacheDate"] == datetime(2026, 10, 1)
    [record] = data.records
    assert (record["price"], record["date"]) == (Decimal("-5390"), datetime(2026, 9, 1, 12, 0))
    assert data.conversions[0]["exchangeRate"] == Decimal("0.2163")
    assert data.preference["mainCurrency"] == "TWD"
    assert data.app_config["timeZoneName"] == "Asia/Taipei"
    assert data.periods == [] and data.installments == []


def test_float_text_is_kept_exactly(backup, tmp_path):
    path = tmp_path / "b.json"
    text = backup.write(tmp_path, backup.doc(records=[backup.record()])).read_text(encoding="utf-8")
    path.write_text(text.replace('"price": -100', '"price": -0.1'), encoding="utf-8")
    assert load_backup_json(path).records[0]["price"] == Decimal("-0.1")


def test_optional_classes_may_be_absent(backup):
    doc = backup.doc()
    for name in ("AHPeriod", "AHInstallment", "AHBonusRewardSharing", "AHCreditSharing", "AHAppConfig"):
        del doc["classes"][name]
    data = parse_backup_doc(doc)
    assert (data.periods, data.sharings, data.app_config) == ([], [], {})


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d.pop("classes"), "has no 'classes' object"),
        (lambda d: d.update(exported_at="yesterday"), "no valid 'exported_at'"),
        (lambda d: d["classes"].pop("AHRecord"), "missing classes: AHRecord"),
        (lambda d: d["classes"]["AHRecord"][0].pop("price"), "AHRecord 'R-1': missing field 'price'"),
        (lambda d: d["classes"]["AHRecord"][0].update(price="12"), "AHRecord 'R-1': field 'price' must be a number"),
        (lambda d: d["classes"]["AHRecord"][0].update(type=True), "field 'type' must be an integer"),
        (lambda d: d["classes"]["AHRecord"][0].update(account=7), "field 'account' must be a string or null"),
        (lambda d: d["classes"]["AHRecord"][0].update(date="2026/09/01"), "field 'date' must be an ISO date-time"),
        (lambda d: d["classes"]["AHRecord"].append(dict(d["classes"]["AHRecord"][0])), "AHRecord 'R-1': duplicate identifier"),
        (lambda d: d["classes"]["AHPreference"].clear(), "exactly 1 AHPreference row, found 0"),
    ],
)
def test_shape_problems_name_class_row_and_field(backup, mutate, message):
    doc = backup.doc(accounts=[backup.account()], records=[backup.record()])
    mutate(doc)
    with pytest.raises(MozeImportError, match=message):
        parse_backup_doc(doc)


def test_unreadable_file_is_an_import_error(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(MozeImportError, match="cannot read backup JSON bad.json"):
        load_backup_json(bad)
    with pytest.raises(MozeImportError, match="cannot read backup JSON missing.json"):
        load_backup_json(tmp_path / "missing.json")


@pytest.mark.parametrize(
    ("record_type", "fields", "kind"),
    [
        (0, {}, "expense"), (1, {}, "income"), (2, {"isTransferIn": False}, "transfer_out"),
        (2, {"isTransferIn": True}, "transfer_in"), (3, {}, "receivable"), (5, {}, "receivable"),
        (4, {}, "payable"), (6, {}, "payable"), (7, {}, "balance_adjustment"), (12, {}, "fee"), (16, {}, "fee"),
        (13, {}, "discount"), (14, {}, "reward"), (15, {}, "interest"), (0, {"isRefund": True}, "refund"),
    ],
)
def test_record_kind_decodes_every_known_type(backup, record_type, fields, kind):
    assert record_kind(backup.record(type_=record_type, **fields)) == kind


def test_unknown_record_type_names_type_and_identifier(backup):
    with pytest.raises(MozeImportError, match="AHRecord 'R-9': unknown record type 8"):
        record_kind(backup.record(identifier="R-9", type_=8))


def test_maps_match_the_spec():
    assert sorted(RECORD_TYPE_TO_KIND) == [0, 1, 2, 3, 4, 5, 6, 7, 12, 13, 14, 15, 16]
    assert sorted(CATEGORY_TYPE_TO_KIND) == [1, 2, 3, 4, 5, 6, 9, 10, 11, 12]
    assert ROUNDING_MAP == {0: "keep", 1: "floor", 2: "ceil", 4: "round"}
    assert POSTING_MAP == {0: "after_transaction", 2: "after_window", 3: "manual"}
    assert DUE_RULE_MAP == {0: "fixed_day", 1: "days_after_closing"}


@pytest.mark.parametrize(
    ("raw", "tags"),
    [
        ("", []),
        ("午餐", ["午餐"]),
        ("午餐,公司", ["午餐", "公司"]),
        ("午餐、公司、午餐", ["午餐", "公司"]),
        ("#午餐 #公司", ["午餐", "公司"]),
        ("#New York #旅行", ["New York", "旅行"]),
        (" 午餐 ; 公司 ", ["午餐", "公司"]),
        ("午餐，公司", ["午餐", "公司"]),
    ],
)
def test_split_tags(raw, tags):
    assert split_tags(raw) == tags


def test_tag_delimiter_reports_the_most_common_separator():
    assert tag_delimiter(["a,b", "c,d", "#e #f", "single", ""]) == ","
    assert tag_delimiter(["#e #f", "#g #h", "a、b"]) == " #"
    assert tag_delimiter(["single", ""]) is None
