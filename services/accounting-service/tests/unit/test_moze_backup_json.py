from datetime import datetime
from decimal import Decimal

import pytest

from app.services.moze_backup_json import (
    CATEGORY_NAMES,
    CATEGORY_TYPE_TO_KIND,
    category_name,
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
    assert ROUNDING_MAP == {0: "keep", 1: "round", 2: "floor", 3: "ceil", 4: "round"}  # MOZE UI order; 4 = 2-decimal round
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


@pytest.mark.parametrize(
    ("build", "field", "identifier", "limit"),
    [
        (lambda b, text: {"records": [b.record("R-LONG", name=text)]}, "name", "R-LONG", 128),
        (lambda b, text: {"records": [b.record("R-LONG", store=text)]}, "store", "R-LONG", 128),
        (lambda b, text: {"records": [b.record("R-LONG", invoiceNumber=text)]}, "invoiceNumber", "R-LONG", 16),
        (lambda b, text: {"accounts": [b.account("A-LONG", text)]}, "name", "A-LONG", 64),
        (lambda b, text: {"targets": [b.target("T-LONG", text)]}, "name", "T-LONG", 128),
        (lambda b, text: {"classifications": [b.classification("K-LONG", text)]}, "name", "K-LONG", 64),
    ],
)
def test_text_longer_than_its_column_is_refused_naming_the_identifier(backup, tmp_path, build, field, identifier, limit):
    secret = "機密" * limit  # longer than the column; must never appear in the error
    path = backup.write(tmp_path, backup.doc(**build(backup, secret)))

    with pytest.raises(MozeImportError) as exc:
        load_backup_json(path)

    assert f"'{identifier}'" in str(exc.value) and f"'{field}'" in str(exc.value) and str(limit) in str(exc.value)
    assert "機密" not in str(exc.value)


def test_text_at_the_column_limit_is_accepted(backup, tmp_path):
    doc = backup.doc(records=[backup.record("R-1", name="名" * 128, invoiceNumber="A" * 16)])
    assert load_backup_json(backup.write(tmp_path, doc)).records[0]["name"] == "名" * 128


def test_surrounding_whitespace_counts_toward_the_limit_because_the_raw_value_is_stored(backup, tmp_path):
    path = backup.write(tmp_path, backup.doc(records=[backup.record("R-PAD", name="名" * 128 + " ")]))

    with pytest.raises(MozeImportError) as exc:
        load_backup_json(path)

    assert "'R-PAD'" in str(exc.value) and "'name'" in str(exc.value) and "名" not in str(exc.value)


EXPECTED_SUBCATEGORY_NAMES = {
    # 飲食
    "CATEGORY_BREAKFAST": "早餐",
    "CATEGORY_LUNCH": "午餐",
    "CATEGORY_DINNER": "晚餐",
    "CATEGORY_SNACKS": "點心",
    "CATEGORY_DRINKS": "飲料",
    "CATEGORY_ALCOHOL": "酒類",
    "CATEGORY_FRUITS": "水果",
    # 交通
    "CATEGORY_GAS": "加油",
    "CATEGORY_PARKING": "停車",
    "CATEGORY_TRAIN": "火車",
    "CATEGORY_SUBWAY": "捷運",
    "CATEGORY_CAR": "汽車",
    "CATEGORY_TAXI": "計程車",
    "CATEGORY_BUS": "公車",
    "CATEGORY_MOTO": "機車",
    "CATEGORY_BIKE": "腳踏車",
    "CATEGORY_AIRPLANE": "飛機",
    "CATEGORY_SHIP": "船",
    # 娛樂
    "CATEGORY_MOVIE": "電影",
    "CATEGORY_PLAYGROUND": "遊樂園",
    "CATEGORY_EXHIBITION": "展覽",
    "CATEGORY_VIDEO": "影片",
    "CATEGORY_MUSIC": "音樂",
    "CATEGORY_GAME": "遊戲",
    "CATEGORY_SPORT": "運動",
    "CATEGORY_GAMBLING": "博弈",
    "CATEGORY_RECREATION": "休閒",
    "CATEGORY_FITNESS": "健身",
    "CATEGORY_PUB": "酒吧",
    # 購物
    "CATEGORY_SUPERMARKET": "超市",
    "CATEGORY_CLOTHING": "服飾",
    "CATEGORY_SHOES": "鞋子",
    "CATEGORY_ACCESSORY": "配件",
    "CATEGORY_BAG": "包包",
    "CATEGORY_COSMETICS": "美妝",
    "CATEGORY_BOUTIQUE": "精品",
    "CATEGORY_GIFT": "禮物",
    "CATEGORY_APP": "App",
    # 個人
    "CATEGORY_SOCIAL": "社交",
    "CATEGORY_MOBILE": "手機",
    "CATEGORY_LOAN": "貸款",
    "CATEGORY_INVESTMENT": "投資",
    "CATEGORY_TAX": "稅金",
    "CATEGORY_INSURANCE": "保險",
    "CATEGORY_DONATION": "捐款",
    "CATEGORY_PETS": "寵物",
    "CATEGORY_LOTTERY": "彩券",
    # 醫療
    "CATEGORY_HOSPITAL": "醫院",
    "CATEGORY_TOOTH_CARE": "牙齒保健",
    "CATEGORY_MEDICINE": "藥品",
    "CATEGORY_SUPPLIES": "用品",
    "CATEGORY_INJECTION": "針劑",
    "CATEGORY_WARD": "病房",
    "CATEGORY_SURGERY": "手術",
    "CATEGORY_EXAMINATION": "檢查",
    # 家居
    "CATEGORY_GROCERIES": "日用品",
    "CATEGORY_WATER": "水費",
    "CATEGORY_ELECTRICITY": "電費",
    "CATEGORY_FUEL": "瓦斯",
    "CATEGORY_PHONE": "電話",
    "CATEGORY_INTERNET": "網路",
    "CATEGORY_RENT": "房租",
    "CATEGORY_LAUNDRY": "洗衣",
    "CATEGORY_REPAIR": "維修",
    "CATEGORY_FURNITURE": "家具",
    "CATEGORY_SUBSCRIPTION": "訂閱",
    "CATEGORY_APPLIANCE": "家電",
    # 家庭
    "CATEGORY_ALLOWANCE": "零用錢",
    "CATEGORY_EDUCATION": "教育",
    "CATEGORY_NURSING": "照護",
    "CATEGORY_TOY": "玩具",
    "CATEGORY_TALENT": "才藝",
    # 生活
    "CATEGORY_SALON": "美髮",
    "CATEGORY_SPA": "SPA",
    "CATEGORY_MASSAGE": "按摩",
    "CATEGORY_ACCOMMODATION": "住宿",
    "CATEGORY_TRAVEL": "旅遊",
    "CATEGORY_PARTY": "聚會",
    # 學習
    "CATEGORY_BOOK": "書籍",
    "CATEGORY_COURSE": "課程",
    "CATEGORY_MATERIAL": "教材",
    "CATEGORY_CERTIFICATION": "證照",
    "CATEGORY_OUTDOORS": "戶外",
    "CATEGORY_STATIONERY": "文具",
    # 收入
    "CATEGORY_SALARY": "薪水",
    "CATEGORY_BONUS": "獎金",
    "CATEGORY_REPAYMENT": "收款",
    "CATEGORY_INTEREST": "利息",
    # 轉帳
    "CATEGORY_WITHDRAW": "提款",
    "CATEGORY_DEPOSIT": "存款",
    "CATEGORY_REFUND": "退款",
    "CATEGORY_EXCHANGE": "兌換",
    # 應收
    "CATEGORY_LEND": "借出",
    "CATEGORY_PAY_FOR": "代付",
    "CATEGORY_REIMBURSE": "報帳",
    # 應付
    "CATEGORY_BORROW": "借入",
    "CATEGORY_CREDIT": "信貸",
    "CATEGORY_CAR_LOAN": "車貸",
    "CATEGORY_MORTGAGE": "房貸",
}


def test_every_builtin_subcategory_key_maps_to_its_chinese_name():
    for key, value in EXPECTED_SUBCATEGORY_NAMES.items():
        assert CATEGORY_NAMES[key] == value, key
        assert category_name(key) == value


def test_unknown_category_key_is_title_cased_and_collected():
    unmapped: set[str] = set()
    assert category_name("CATEGORY_FOO_BAR", unmapped) == "Foo Bar"
    assert category_name("CATEGORY_LUNCH", unmapped) == "午餐"
    assert category_name("Category_foo", unmapped) == "Category_foo"
    assert category_name("午餐", unmapped) == "午餐"
    assert unmapped == {"CATEGORY_FOO_BAR"}
