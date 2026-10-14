"""R5b: the OCR masking entry point. Cases from the AGENT-118 reviews (rounds 1–3) and the author's probes; the text-layer
path (`Masker.text`) is untouched and several tests show it leaking on the same input, so these tests discriminate."""
import pytest

from worker import mask, passwords

IDENT = passwords.Identity(id_number="A123456789", birth_date="19900101", holder_names=["王小明", "WANG XIAO MING"])
M = mask.Masker(IDENT)


def compact(s: str) -> str:
    return "".join(s.split())


def leaked(out: str, *secrets: str) -> list[str]:
    c = compact(out)
    return [s for s in secrets if compact(s) in c]


# ---- identifiers reaching the sink on the OCR path (rounds 1–3) ---------------------------------------------------

@pytest.mark.parametrize("raw,secrets", [
    ("卡號 4111    2222　　3333    4444", ["4111", "2222", "3333"]),                 # wide gaps, U+3000
    ("Ａ１２ ３４５ ６７８９", ["A123456789", "123456789"]),                                  # full-width, split ID
    ("41 11 22 22 33 33 44 44", ["4111", "41 11", "2222"]),                                   # 2-digit groups
    ("帳號 012   345   678   901 餘額", ["012345678901", "345"]),                               # account, 3-digit groups
    ("身分證 B12 345 6789 末", ["B123456789", "B12"]),                                         # an ID that is not the configured one
    ("持卡人 王 小 明", ["王小明", "小明"]),                                                   # split CJK name
    ("wang xiao ming paid", ["wang", "ming"]),                                                # lowercase English name
    ("WANG XIAO  MING here", ["WANG", "MING"]),                                               # double space
    ("生日 1990 01 01", ["19900101", "1990"]),                                                # split birth date
    ("4lll 2222 3333 444O", ["4111", "4lll", "444O"]),                                        # confusables in a digit run
])
def test_identifiers_are_masked_on_the_ocr_path(raw, secrets):
    out = M.mask_ocr(raw)
    assert leaked(out, *secrets) == [], out


def test_numeric_password_split_across_boxes():
    out = M.mask_ocr("pw 01 15 here", extra_literals=["0115"])
    assert out == "pw [PW] here"


def test_the_text_layer_masker_leaks_on_the_same_input():
    """The control: without the OCR entry point these identifiers survive, so the tests above discriminate."""
    for raw, secret in (("卡號 4111    2222　　3333    4444", "4111"), ("持卡人 王 小 明", "王小明"),
                        ("Ａ１２ ３４５ ６７８９", "A123456789"), ("WANG XIAO  MING here", "WANGXIAOMING")):
        import unicodedata
        assert leaked(unicodedata.normalize("NFKC", M.text(raw)), secret) == [secret]  # NFKC only to compare: the leak is there


# ---- rows survive: spans only, never whole lines (round-3 Must 2) -------------------------------------------------

def test_name_in_a_transaction_row_leaves_the_transaction():
    assert M.mask_ocr("WANG XIAO  MING 2026/09/01 COFFEE 120") == "[NAME] 2026/09/01 COFFEE 120"
    assert M.mask_ocr("持卡人 王 小 明 先生") == "持卡人 [NAME] 先生"


def test_dates_and_amounts_survive_a_year_candidate():
    text = "2026/09/01 COFFEE 120\n20260901    1234\n2026-09-03 TAXI 2026 EUR"
    out = M.mask_ocr(text, extra_literals=["2026"])
    lines = out.split("\n")
    assert lines[0] == "2026/09/01 COFFEE 120" and lines[1] == "20260901    1234" and lines[2].startswith("2026-09-03 TAXI")


def test_standalone_numeric_password_equal_to_an_amount_is_masked_like_the_text_layer():
    """Documented limitation (ruling 2026-10-14): privacy over completeness; the guardrail catches a broken balance."""
    assert M.mask_ocr("2026/09/02 BOOKS 12026", extra_literals=["12026"]) == "2026/09/02 BOOKS [PW]"
    assert M.text("2026/09/02 BOOKS 12026", extra_literals=["12026"]) == "2026/09/02 BOOKS [PW]"


def test_merchant_text_and_cjk_runs_are_never_rewritten():
    assert M.mask_ocr("全聯 台北 350") == "全聯 台北 350"
    assert M.mask_ocr("一 二 三 四 五") == "一 二 三 四 五"
    assert M.mask_ocr("OllO12 merchant") == "OllO12 merchant"       # 2 of 6 digits: not a digit run
    assert M.mask_ocr("Ollie lol 10") == "Ollie lol 10"
    assert M.mask_ocr("2026/09/01   COFFEE   1,234") == "2026/09/01   COFFEE   1,234"


def test_amount_columns_mask_like_the_text_layer():
    assert M.mask_ocr("1200 3500 150000") == M.text("1200 3500 150000") == "[NUM…0000]"


def test_confusables_require_a_mostly_digit_run():
    assert M.mask_ocr("I0O1 2222 3333 4444") == "[NUM…4444]"
    assert M.mask_ocr("4lll") == "4lll"                             # alone: 1 digit of 4 is not mostly digits


def test_multiline_and_tags_keep_last_four():
    out = M.mask_ocr("第一頁\n卡號 4111 2222 3333 4444\n\n合計 1,234")
    assert out == "第一頁\n卡號 [NUM…4444]\n\n合計 1,234"


def test_email_and_phone_still_handled_by_the_shared_rules():
    out = M.mask_ocr("mail a.b@example.com 電話 0912-345-678")
    assert "[EMAIL]" in out and "[PHONE]" in out and "example.com" not in out
