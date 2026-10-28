import pytest

from tests.worker.pdfgen import encrypt, make_pdf, make_pdf_cid, make_pdf_glyphs, make_pdf_words, statement_lines
from worker import pdf
from worker.pdf import extract_lines


def test_unlock_tries_candidates_in_order_and_records_index():
    data = encrypt(make_pdf([statement_lines()]), "second")
    unlocked = pdf.unlock(data, ["first", "second"])
    assert unlocked.password_index == 1


def test_wrong_passwords_fail_with_the_word_password_only():
    data = encrypt(make_pdf([statement_lines()]), "right")
    with pytest.raises(pdf.PasswordError) as err:
        pdf.unlock(data, ["wrong-1", "wrong-2"])
    assert str(err.value) == "password" and "wrong" not in str(err.value)


def test_unencrypted_pdf_has_no_index():
    assert pdf.unlock(make_pdf([statement_lines()]), ["x"]).password_index is None


def test_owner_password_only_pdf_opens_without_a_candidate():
    # AMEX-style: encrypted with an owner password but an empty user password → no credential needed
    data = encrypt(make_pdf([statement_lines()]), "", owner_password="owner-secret")
    assert pdf.unlock(data, []).password_index is None
    assert pdf.unlock(data, ["unrelated"]).password_index is None  # the empty password wins before any candidate
    assert "MERCHANT 003" in pdf.extract(data, None).text  # the text path needs no password either


def test_user_password_still_required_when_set():
    data = encrypt(make_pdf([statement_lines()]), "user-pw", owner_password="owner-secret")
    with pytest.raises(pdf.PasswordError):
        pdf.unlock(data, [])
    assert pdf.unlock(data, ["user-pw"]).password_index == 0


def test_extract_text_and_page_count():
    data = make_pdf([statement_lines(), ["PAGE TWO"]])
    out = pdf.extract(data, None)
    assert out.pages == 2 and "MERCHANT 003" in out.text and "\f" in out.text
    assert out.text_chars_page1 >= 200


def test_no_text_layer_fails_closed():
    with pytest.raises(pdf.NoTextLayer):
        pdf.extract(make_pdf([["short"]]), None)


def test_bounds():
    with pytest.raises(pdf.TooLarge):
        pdf.extract(make_pdf([statement_lines()]), None, max_bytes=10)
    with pytest.raises(pdf.TooLarge):
        pdf.extract(make_pdf([statement_lines()] * 3), None, max_pages=2)


def test_encrypted_extract_uses_password():
    data = encrypt(make_pdf([statement_lines()]), "pw")
    assert "MERCHANT 001" in pdf.extract(data, "pw").text


def test_malformed_pdf_is_bounded():
    with pytest.raises(pdf.Malformed):
        pdf.extract(b"%PDF-1.4 garbage", None)


def test_wrong_password_in_extract_is_password_error():
    data = encrypt(make_pdf([statement_lines()]), "pw")
    with pytest.raises(pdf.PasswordError):
        pdf.extract(data, "bad")


def test_unlock_malformed_and_too_large():
    with pytest.raises(pdf.Malformed):
        pdf.unlock(b"%PDF-1.4 garbage", ["x"])
    with pytest.raises(pdf.TooLarge):
        pdf.unlock(make_pdf([statement_lines()]), ["x"], max_bytes=10)


def test_extract_timeout_fires(monkeypatch):
    import time

    import pdfplumber.page

    def slow(self, *args, **kwargs):
        time.sleep(5)
        return ""

    monkeypatch.setattr(pdfplumber.page.Page, "extract_text", slow)
    with pytest.raises(pdf.ExtractTimeout):
        pdf.extract(make_pdf([statement_lines()]), None, timeout_s=1)


def test_none_table_cells_become_empty_strings(monkeypatch):
    import pdfplumber.page

    monkeypatch.setattr(pdfplumber.page.Page, "extract_tables", lambda self, *a, **k: [[[None, "x"]]])
    out = pdf.extract(make_pdf([statement_lines()]), None)
    assert out.tables == [[["", "x"]]]


def test_alarm_restores_previous_handler():
    import signal

    from worker.pdf import _Alarm

    def custom(signum, frame):
        pass

    previous = signal.signal(signal.SIGALRM, custom)
    try:
        with _Alarm(5):
            pass
        assert signal.getsignal(signal.SIGALRM) is custom
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def test_library_logging_never_carries_pdf_content(caplog):
    import logging
    from worker import cli
    from worker import pdf as pdf_mod
    from tests.worker.pdfgen import make_pdf
    raw = make_pdf([["A123456789 WANG"] + ["filler line"] * 5]).replace(b"Tj", b"g ")  # invalid operator operands
    cli.quiet_libraries()
    with caplog.at_level(logging.INFO):
        try:
            pdf_mod.extract(raw, None)
        except Exception:  # noqa: BLE001 — the outcome does not matter, only what was logged
            pass
    assert "A123456789" not in caplog.text
    assert all("A123456789" not in r.getMessage() for r in caplog.records)


def _glyphs(cid: int, mapped: int) -> list[dict]:
    return [{"text": f"(cid:{i})"} for i in range(cid)] + [{"text": "x"} for _ in range(mapped)]


def test_glyph_garbage_share_rule_at_the_bound():
    assert pdf.GARBAGE_GLYPH_SHARE == 0.45
    assert pdf.is_font_garbage(_glyphs(45, 55)) is True                 # exactly 45 %
    assert pdf.is_font_garbage(_glyphs(449, 551)) is False              # just below
    assert pdf.is_font_garbage(_glyphs(40, 60)) is False                # 國泰-shaped page: icons unmapped, text mapped
    assert pdf.is_font_garbage(_glyphs(53, 47)) is True                 # 遠東/渣打-shaped page
    assert pdf.is_font_garbage([]) is False and pdf.glyph_garbage_share([]) == 0.0
    assert pdf.is_font_garbage([{"text": "A(cid:1)"}]) is False         # only a glyph that IS an unmapped cid counts


def test_cid_garbage_page_is_no_text_layer():
    """A page 1 whose glyphs come 55 % from a Type3 font without a ToUnicode map (pdfminer: `(cid:N)`), 220 real characters."""
    with pytest.raises(pdf.NoTextLayer, match="font garbage"):
        pdf.extract(make_pdf_glyphs(["x" * 55] * 4, 270), None)


def test_glyph_rule_bounds_through_extract():
    assert pdf.extract(make_pdf_glyphs(["x" * 55] * 4, 179), None).pages == 1          # 179/399 = 44.9 %: text path
    with pytest.raises(pdf.NoTextLayer, match="font garbage"):
        pdf.extract(make_pdf_glyphs(["x" * 55] * 4, 180), None)                         # 180/400 = exactly 45 %
    out = pdf.extract(make_pdf_glyphs(statement_lines(), 500), None)                   # 500/1350 = 37 %, 國泰-like: text
    assert out.pages == 1 and "MERCHANT 003" in out.text and "(cid:" in out.text


def test_zero_glyph_page_falls_under_the_short_rule():
    with pytest.raises(pdf.NoTextLayer, match="characters"):
        pdf.extract(make_pdf_glyphs([], 0, extra_pages=[statement_lines()]), None)


def test_cid_tounicode_control_stays_on_the_text_path():
    """A 國泰-shaped page: a CID font with a ToUnicode map carries the text (CJK included) and 37–40 % of the glyphs are
    unmapped Type3 icons. The text layer is used; the CJK text is really mapped."""
    import io
    import pdfplumber
    text = "\n".join(statement_lines()) + "\n國泰世華 信用卡 帳單 金額 合計"
    for garbage in (500, 600):
        data = make_pdf_cid(text, garbage)
        with pdfplumber.open(io.BytesIO(data)) as doc:
            share = pdf.glyph_garbage_share(doc.pages[0].chars)
            fonts = {c.get("fontname") for c in doc.pages[0].chars}
        assert 0.36 <= share <= 0.41 and "SynthSans" in fonts
        out = pdf.extract(data, None)
        assert "MERCHANT 003" in out.text and "國泰世華" in out.text and "(cid:" in out.text


def test_garbage_rule_looks_at_page_one_only():
    """Page 1 is a clean text page; page 2 is 60 % unmapped Type3 glyphs (real unmapped glyphs, not literal text)."""
    import io
    import pdfplumber
    data = make_pdf_glyphs(statement_lines(), 0, extra_pages=[(["x" * 40] * 5, 300)])
    with pdfplumber.open(io.BytesIO(data)) as doc:
        page2 = pdf.glyph_garbage_share(doc.pages[1].chars)
    assert page2 >= pdf.GARBAGE_GLYPH_SHARE  # a rule over all pages would fire here …
    out = pdf.extract(data, None)             # … the page-1 rule does not: the text layer is used as today
    assert out.pages == 2 and "MERCHANT 003" in out.text and "(cid:" in out.text


def test_extract_lines_groups_words_by_top_and_sorts_by_x():
    page1 = [(40, 780, "2026/09/01"), (229, 780, "TWD"), (330, 780, "1,000"), (530, 780, "9,000"),
             (40, 760, "2026/09/02"), (229, 760, "TWD"), (420, 760, "500"), (530, 760, "9,500")]
    page2 = [(40, 780, "3"), (60, 780, "2026/09/03")]
    data = make_pdf_words([page1, page2])
    lines = extract_lines(data, None)
    assert [line.page for line in lines] == [1, 1, 2]
    first = lines[0]
    assert [w.text for w in first.words] == ["2026/09/01", "TWD", "1,000", "9,000"]
    assert first.text == "2026/09/01 TWD 1,000 9,000"
    assert first.words[2].x0 < first.words[3].x0 and first.words[2].x1 < first.words[3].x1
    assert lines[1].top > first.top and [w.text for w in lines[2].words] == ["3", "2026/09/03"]


def test_extract_lines_keeps_the_extract_bounds():
    data = make_pdf_words([[(40, 780, "x")]])
    with pytest.raises(pdf.TooLarge):
        extract_lines(data, None, max_bytes=10)
    with pytest.raises(pdf.TooLarge):
        extract_lines(make_pdf_words([[(40, 780, "a")], [(40, 780, "b")]]), None, max_pages=1)
    with pytest.raises(pdf.Malformed):
        extract_lines(b"%PDF-1.4 not a pdf", None)
    locked = encrypt(make_pdf([statement_lines()]), "pw")
    with pytest.raises(pdf.PasswordError):
        extract_lines(locked, "wrong")
    assert extract_lines(locked, "pw")[0].words[0].text.startswith("2026/09/")


def test_extract_lines_timeout(monkeypatch):
    import time
    import pdfplumber

    def slow(self, *a, **k):
        time.sleep(3)
        return []

    monkeypatch.setattr(pdfplumber.page.Page, "extract_words", slow)
    with pytest.raises(pdf.ExtractTimeout):
        extract_lines(make_pdf_words([[(40, 780, "x")]]), None, timeout_s=1)
