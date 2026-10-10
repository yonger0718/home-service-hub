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
