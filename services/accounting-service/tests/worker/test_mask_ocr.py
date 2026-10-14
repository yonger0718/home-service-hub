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


# ---- review round (internal reviewer, 2026-10-14): exact outputs ---------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("STRAßE CAFE 卡號 A123456789", "STRAßE CAFE 卡號 [ID]"),                     # casefold changes length (ß → ss)
    ("İstanbul A12 345 6789", "İstanbul [ID]"),                                     # İ folds to two code points
    ("ID NO B12 345 6789 TWD", "ID NO [ID] TWD"),                                   # letters on both sides of a split ID
    ("B12 345 6789 1500", "[ID] 1500"),                                              # digits after a split ID
    ("b123456789", "[ID]"),                                                          # lower-case ID letter
    ("20260901 1234  5678  9012", "20260901 [NUM…9012]"),                            # a date next to a double-spaced account number
    ("2026 | 350 | 1200", "2026 | 350 | 1200"),                                      # table borders are not digits
    ("4111 - 2222 - 3333 - 4444", "[NUM…4444]"),                                    # dashed groups
    ("4111 – 2222 – 3333 – 4444", "[NUM…4444]"),                      # en dashes
    ("4111·2222·3333·4444", "[NUM…4444]"),                            # middle dots
    ("2026-09-01 2026-09-03", "2026-09-01 2026-09-03"),                              # two dates are not a card number
    ("WANG, XIAO-MING paid", "[NAME] paid"),                                         # punctuation inside a name
    ("WANG/XIAO MING", "[NAME]"),
    ("1990 01 01 1234", "[BIRTH] 1234"),                                             # birth date then an amount
    ("19900101 1234", "[BIRTH] 1234"),
    ("持卡人 王小明 身分證 A123456789", "持卡人 [NAME] 身分證 [ID]"),                  # two identifiers on one line
    ("4111​2222 3333 4444", "[NUM…4444]"),                                      # zero-width space inside a group
    ("A12345678O", "[ID]"),                                                          # confusable inside a mixed token
    ("AI23456789", "[ID]"),
])
def test_review_cases_exact(raw, expected):
    assert M.mask_ocr(raw) == expected


def test_literals_are_nfkc_normalised_before_matching():
    m = mask.Masker(passwords.Identity(id_number="Ａ１２３４５６７８９", birth_date="19900101", holder_names=["ＷＡＮＧ ＸＩＡＯ"]))
    assert m.mask_ocr("A123456789 wang xiao") == "[ID] [NAME]"


def test_short_latin_name_needs_word_edges():
    m = mask.Masker(passwords.Identity(id_number=None, birth_date=None, holder_names=["LI"]))
    assert m.mask_ocr("Linda at Alibaba") == "Linda at Alibaba"
    assert m.mask_ocr("holder LI here") == "holder [NAME] here"


def test_overlapping_names_merge():
    m = mask.Masker(passwords.Identity(id_number=None, birth_date=None, holder_names=["王小明", "小明華"]))
    assert m.mask_ocr("戶名 王小明華 先生") == "戶名 [NAME] 先生"


def test_empty_or_blank_literals_are_ignored():
    m = mask.Masker(passwords.Identity(id_number=None, birth_date=None, holder_names=["   "]))
    assert m.mask_ocr("nothing to mask", extra_literals=["    "]) == "nothing to mask"


def test_cross_line_split_is_a_documented_limitation():
    """Known limitation (ruling 2026-10-14, Should 1): no reassembly across lines; the external parser receives both."""
    assert M.mask_ocr("WANG XIAO\nMING") == "WANG XIAO\nMING"
    assert M.mask_ocr("4111 2222 3333\n4444") == "[NUM…3333]\n4444"


# ---- fix round (dev-grok review 2026-10-14, AGENT-122): exact outputs ----------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    # Must 1: group separators never reach the ID, literal or ≥12-digit rules
    ("身分證 B12-345-6789 末", "身分證 [ID] 末"),
    ("B12·345·6789", "[ID]"),
    ("B12/345/6789", "[ID]"),
    ("A123-456-789", "[ID]"),                                                         # the configured ID, dashed
    ("4111/2222/3333/4444", "[NUM…4444]"),
    ("4111−2222−3333−4444", "[NUM…4444]"),                                            # U+2212 minus sign
    ("4111 | 2222 | 3333 | 4444", "[NUM…4444]"),                                      # pipes
    ("2026 | 350 | 1200", "2026 | 350 | 1200"),                                       # control: 11 digits
    # Must 2: a CJK neighbour is not a word edge
    ("持卡人WANG XIAO  MING先生", "持卡人[NAME]先生"),
    ("持卡人wang xiao ming先生", "持卡人[NAME]先生"),
    ("身分證A12 345 6789末", "身分證[ID]末"),
    # Must 3: confusable runs may hold digit-free groups; a run of 12+ is corrected whatever its digit share
    ("4lll 222O 333O 444O", "[NUM…4440]"),
    ("卡號 4lll 222O 333O 444O", "卡號 [NUM…4440]"),
    ("1111 OOOO 2222 3333", "[NUM…3333]"),
    ("OOOO 1111 2222 3333", "[NUM…3333]"),
    ("4lll llll 2222 3333", "[NUM…3333]"),
    ("OllO12 merchant", "OllO12 merchant"),                                           # controls: short runs keep the 70 % gate
    ("4lll", "4lll"),
    ("I0O1", "I0O1"),
    ("2026 | 350 | 1200 | ok", "2026 | 350 | 1200 | ok"),                              # a lone pipe is never part of a run
    # Must 4: a combining mark that did not compose is dropped
    ("A1234\u030156789", "[ID]"),
    ("4111\u03012222 3333 4444", "[NUM…4444]"),
    # Should 2 (promoted): a Cyrillic or Greek lookalike at the ID-letter position
    ("\u041012 345 6789", "[ID]"),                                                    # Cyrillic А
    ("\u039112 345 6789", "[ID]"),                                                    # Greek Α
    ("\u0410123456789", "[ID]"),
    ("Москва 350", "Москва 350"),                                                      # controls: non-ID Cyrillic/Greek text
    ("Αθήνα cafe 120", "Αθήνα cafe 120"),
    ("\u0410 12 345", "\u0410 12 345"),                                              # not an ID shape
])
def test_fix_round_exact(raw, expected):
    assert M.mask_ocr(raw) == expected


def test_fix_round_no_residual_digits():
    for raw in ("身分證 B12-345-6789 末", "B12·345·6789", "B12/345/6789", "A123-456-789"):
        out = M.mask_ocr(raw)
        assert not any(ch.isdigit() for ch in out) and "A" not in out.replace("[ID]", ""), out
    for raw in ("4lll 222O 333O 444O", "1111 OOOO 2222 3333", "OOOO 1111 2222 3333", "4lll llll 2222 3333"):
        out = M.mask_ocr(raw)
        assert out.startswith("[NUM…") and out.endswith("]") and out.count("[") == 1, out


def test_dashed_numeric_password_is_masked():
    assert M.mask_ocr("pw 01-15 here", extra_literals=["0115"]) == "pw [PW] here"
    assert M.mask_ocr("pw 01 15 here", extra_literals=["0115"]) == "pw [PW] here"


def test_date_fragments_still_survive_a_separated_candidate():
    """The separator strip must not let a candidate eat a date: 2026/01/15 with candidate 0115 or 2026."""
    assert M.mask_ocr("2026/01/15 COFFEE 120", extra_literals=["0115", "2026"]) == "2026/01/15 COFFEE 120"
    assert M.mask_ocr("2026-09-01 2026-09-03", extra_literals=["0901"]) == "2026-09-01 2026-09-03"


def test_short_latin_name_edges_are_ascii_only():
    m = mask.Masker(passwords.Identity(id_number=None, birth_date=None, holder_names=["LI"]))
    assert m.mask_ocr("Linda at Alibaba") == "Linda at Alibaba"       # control: an ASCII neighbour is an edge
    assert m.mask_ocr("戶名LI先生") == "戶名[NAME]先生"                   # a CJK neighbour is not


# ---- Should 4: the email rule is linear, with the same matches as before ------------------------------------------

_EMAIL_LEGACY = mask.re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def test_email_rule_matches_exactly_as_before():
    import random
    rnd = random.Random(20261014)
    alphabet = "ab.+-@_ 1X\u00e9\u738b"
    for _ in range(3000):
        s = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 24)))
        assert _EMAIL_LEGACY.sub("[EMAIL]", s) == mask._EMAIL.sub("[EMAIL]", s), s
    for s in ("a.b@example.com", "x..@@c.d", ".@c.d", "mail: first.last+tag@sub.example.co.uk ok", "@@", "a@b", "a@b.c@d.e"):
        assert _EMAIL_LEGACY.sub("[EMAIL]", s) == mask._EMAIL.sub("[EMAIL]", s), s


def test_long_dotted_line_without_an_at_sign_is_fast():
    import time
    line = "a.b.c." * 70_000  # ~420 KB, over the parser's STDIN_CAP, no "@"
    t = time.monotonic()
    out = M.mask_ocr(line)
    assert time.monotonic() - t < 10 and out == line
