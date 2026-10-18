import unicodedata

import pytest

from worker import mask, passwords, pdf

IDENT = passwords.Identity(id_number="A123456789", birth_date="19900101", holder_names=["王小明", "WANG XIAO MING"])


def test_text_canary_masks_every_class():
    m = mask.Masker(IDENT)
    s = ("卡號 4321-5678-9012-3456 戶名 王小明 WANG XIAO MING 身分證 A123456789 生日 1990/01/01 01/01/90 "
         "帳號 012 345 678 901 mail a.b@example.com 電話 0912-345-678 (02)2345-6789 small 12345")
    out = m.text(s)
    for secret in ("4321-5678-9012", "王小明", "WANG XIAO MING", "A123456789", "1990/01/01", "01/01/90", "012 345 678",
                   "a.b@example.com", "0912-345-678", "2345-6789"):
        assert secret not in out, secret
    assert "3456" in out and "[ID]" in out and "[NAME]" in out and "[EMAIL]" in out and "12345" in out


def test_cjk_adjacent_secrets_are_masked():
    m = mask.Masker(IDENT)
    out = m.text("帳號123456789012結餘 身分證B223456789末 戶名王小明先生 卡號4321-5678-9012-3456元")
    assert "123456789012" not in out and "B223456789" not in out and "王小明" not in out and "5678-9012" not in out
    assert out.startswith("帳號[NUM…9012]結餘") and "[ID]末" in out and "戶名[NAME]先生" in out and "3456]元" in out


def test_tables_are_masked_cell_by_cell():
    m = mask.Masker(IDENT)
    out = m.tables([[["王小明", "4321567890123456"], ["x", "ok"]]])
    assert out == [[["[NAME]", "[NUM…3456]"], ["x", "ok"]]]


def test_render_joins_text_and_tables():
    m = mask.Masker(IDENT)
    extracted = pdf.Extracted(text="hello A123456789", tables=[[["a", "b"]]], pages=1, text_chars_page1=200)
    rendered = m.render(extracted)
    assert rendered.startswith("hello [ID]") and "[tables]" in rendered and "a | b" in rendered


def test_masker_from_password_file_uses_the_shared_parser(tmp_path):
    p = tmp_path / "pw.env"
    p.write_text("  STATEMENT_ID_NUMBER=A123456789\nSTATEMENT_BIRTH_DATE=19900101\n"
                 "STATEMENT_HOLDER_NAMES=王小明,WANG\\, XIAO\n信用卡/x=$ID\n", encoding="utf-8")
    m = mask.Masker(passwords.identity(p))
    assert m.text("A123456789 王小明 WANG, XIAO 1990/01/01") == "[ID] [NAME] [NAME] [BIRTH]"


CANARY = ("卡號 4321-5678-9012-3456 戶名 王小明 WANG XIAO MING 身分證 A123456789 生日 1990/01/01 01/01/90 "
          "帳號 012 345 678 901 mail a.b@example.com 電話 0912-345-678 (02)2345-6789 small 12345")


def test_text_canary_replacement_tokens_exact():
    out = mask.Masker(IDENT).text(CANARY)
    assert out == ("卡號 [NUM…3456] 戶名 [NAME] [NAME] 身分證 [ID] 生日 [BIRTH] [BIRTH] 帳號 [NUM…8901] "
                   "mail [EMAIL] 電話 [PHONE] [PHONE] small 12345")


@pytest.mark.parametrize("s,token", [
    ("4321 5678 9012 3456", "[NUM…3456]"),
    ("4321-5678-9012-3456", "[NUM…3456]"),
    ("012 345 678 901", "[NUM…8901]"),
    ("123-456-7890", "[NUM…7890]"),
    ("12345678", "[NUM…5678]"),
    ("１２３４５６７８９０１２", "[NUM…９０１２]"),
])
def test_digit_runs_that_are_account_numbers_are_masked(s, token):
    assert mask.Masker(IDENT).text(s) == token


@pytest.mark.parametrize("s", [
    "30 2026", "000 2026", "2026/09/30", "2026-10-02", "115/09/30", "1,234,567", "12,345,678.00", "12345",
    "Page 1 of 12 2026 06", "2026/09/30 2026/10/01", "NT$ 100,000 2026/10/02",
])
def test_dates_amounts_and_short_numbers_stay_intact(s):
    assert mask.Masker(IDENT).text(s) == s


def test_zero_decimal_then_seven_digits_is_not_a_phone():
    out = mask.Masker(IDENT).text("0.00 1000000")
    assert "[PHONE]" not in out and out == "0.00 [NUM…0000]"


@pytest.mark.parametrize("s", ["0912-345-678", "0912345678", "+886 912 345 678", "(02) 2345-6789", "02-2345-6789"])
def test_phone_forms_are_masked(s):
    assert mask.Masker(IDENT).text(s) == "[PHONE]"


def test_holder_names_shorter_than_two_characters_are_ignored():
    m = mask.Masker(passwords.Identity(None, None, ["A", "王小明"]))
    assert m.text("A 王小明") == "A [NAME]"


@pytest.mark.parametrize("s,expected", [
    ("20260930", "20260930"),
    ("12345678", "[NUM…5678]"),
    ("20261399", "[NUM…1399]"),
    ("TXN 20260930 12345678", "TXN 20260930 [NUM…5678]"),
])
def test_compact_yyyymmdd_dates_are_kept_others_masked(s, expected):
    assert mask.Masker(IDENT).text(s) == expected


def test_password_candidates_are_masked_before_other_rules():
    from worker import pdf
    masker = mask.Masker(passwords.Identity(None, None, []))
    extracted = pdf.Extracted("hello Secret99 world ab12", [[["Secret99xx", "ok"]]], 1, 300)
    out = masker.render(extracted, ["Secret99", "Secret99xx", "ab1"])  # longest first; "ab1" < 4 chars is ignored
    assert "Secret99" not in out and "[PW]" in out and "ab12" in out


def test_password_literals_respect_token_boundaries():
    from worker import pdf
    masker = mask.Masker(passwords.Identity(None, None, []))
    ex = lambda t: pdf.Extracted(t, [], 1, 300)  # noqa: E731
    assert "2026/09/30" in masker.render(ex("on 2026/09/30"), ["2026"])
    assert "20260930" in masker.render(ex("ref 20260930"), ["2026"])
    assert "20260115" in masker.render(ex("ref 20260115"), ["0115"])
    assert "[PW]" in masker.render(ex("pin 0115 end"), ["0115"])
    assert "Secret991" in masker.render(ex("a Secret991 b"), ["Secret99"])
    assert "[PW]" in masker.render(ex("a Secret99 b"), ["Secret99"])


@pytest.mark.parametrize("s,expected", [
    ("12/22 12/26 優步－大慶大車隊 174 9538 1814 TW TWD", "12/22 12/26 優步－大慶大車隊 174 [NUM…1814] TW TWD"),
    ("12/22 12/29 高鐵智慧型手機ＩＰｈｏ 1,480 9538 1814 TW TWD", "12/22 12/29 高鐵智慧型手機ＩＰｈｏ 1,480 [NUM…1814] TW TWD"),
    ("12/24 12/29 SUICA MOBILE PAYMENT 2,025 9538 5897 JP JPY 10,000.00 12/23",
     "12/24 12/29 SUICA MOBILE PAYMENT 2,025 [NUM…5897] JP JPY 10,000.00 12/23"),
    ("11/07 12/23 富邦ＭＯＭＯ－ＥＣ 02/12 1,224 9538 TW TWD", "11/07 12/23 富邦ＭＯＭＯ－ＥＣ 02/12 1,224 [NUM…9538] TW TWD"),  # card last four only
    ("12/22 12/26 連加＊7-11 174 9538 1814 TW TWD", "12/22 12/26 連加＊7-11 174 [NUM…1814] TW TWD"),  # merchant ending in a short number
    ("12/24 01/23 國外交易手續費 -SUICA 30 9538", "12/24 01/23 國外交易手續費 -SUICA 30 9538"),
    ("12/22 12/26 全聯福利中心 85 1234 3799", "12/22 12/26 全聯福利中心 85 [NUM…3799]"),  # no country/currency: generic rule, amount untouched anyway
])
def test_amount_in_a_card_transaction_row_survives(s, expected):
    """Cube rows end with the card's last four digits, an optional authorisation code, a country code and a currency
    code: the amount column before them stays printed and the code groups become one masked token (AGENT-147
    follow-up: 12 Cube statements lost every amount to the generic rule and failed the equation)."""
    assert mask.Masker(IDENT).text(s) == expected
    assert mask.Masker(IDENT).mask_ocr_line(s) == unicodedata.normalize("NFKC", expected)  # the OCR path normalises first


def test_only_whole_rows_are_rewritten_inside_a_page():
    page = "卡號 123 4567 8901 2345\n12/22 12/26 優步 174 9538 1814 TW TWD\n本期應繳總額 6,382"
    assert mask.Masker(IDENT).text(page) == "卡號 [NUM…2345]\n12/22 12/26 優步 174 [NUM…1814] TW TWD\n本期應繳總額 6,382"


@pytest.mark.parametrize("s,expected", [
    ("帳號 0123 4567890", "帳號 [NUM…7890]"),
    ("保單 123 45678", "保單 [NUM…5678]"),
    ("電話 0800 123456", "電話 [NUM…3456]"),
    ("卡號 123 4567 8901 2345", "卡號 [NUM…2345]"),
    ("帳號 013 21705159", "帳號 [NUM…5159]"),
    ("帳號 123 4567 8901 TW TWD", "帳號 [NUM…8901] TW TWD"),  # AGENT-148 round 2: the tail without the transaction dates
    ("電話 0800 123 4567 TW TWD", "電話 [NUM…4567] TW TWD"),  # AGENT-148 round 2: the tail inside a longer identifier
    ("12/22 12/26 電話 0800 123 4567 TW TWD", "12/22 12/26 電話 [NUM…4567] TW TWD"),  # same, on a dated row
    ("12/22 12/26 帳號 123 4567 8901 2345", "12/22 12/26 帳號 [NUM…2345]"),  # a dated row without the codes' anchor
    ("1480 9538 1814 TW TWD", "[NUM…1814] TW TWD"),  # a four-digit amount without a thousands separator: known limitation
])
def test_identifiers_with_a_short_leading_group_stay_masked(s, expected):
    """AGENT-148 Must (rounds 1 and 2): only a whole transaction row is rewritten and never from inside a longer
    digit run; every other shape keeps the generic rule, so no identifier prefix is exposed — on the text path and
    on the OCR path alike."""
    assert mask.Masker(IDENT).text(s) == expected
    assert mask.Masker(IDENT).mask_ocr_line(s) == expected


def test_mask_fingerprint_pins_the_amount_rule():
    assert mask.FINGERPRINT == "mask2-amount-before-codes"
