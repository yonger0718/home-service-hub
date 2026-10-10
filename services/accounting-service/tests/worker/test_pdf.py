import pytest

from tests.worker.pdfgen import encrypt, make_pdf, statement_lines
from worker import pdf


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
