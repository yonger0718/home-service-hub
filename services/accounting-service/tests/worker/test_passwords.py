import hashlib

import pytest

from worker import passwords

FILE = """# comment
STATEMENT_ID_NUMBER=A123456789
STATEMENT_BIRTH_DATE=19900101
STATEMENT_HOLDER_NAMES=王小明,WANG XIAO MING
信用卡/國泰世華=$ID
信用卡/台新=$BIRTH8
信用卡/台新.alt=$BIRTH{DDMMYY}+$ID[:6]
信用卡/滙豐 Live+=$ID[-4:]  # last four
銀行帳戶/星展=literal-pw
手動下載/國泰世華=@信用卡/國泰世華
"""


@pytest.fixture
def pwfile(tmp_path):
    p = tmp_path / "pw.env"
    p.write_text(FILE, encoding="utf-8")
    return p


def test_candidates_expand_tokens_in_order(pwfile):
    cands, version = passwords.load(pwfile)
    assert cands["信用卡/國泰世華"] == ["A123456789"]
    assert cands["信用卡/台新"] == ["19900101", "010190A12345"]
    assert cands["信用卡/滙豐 Live+"] == ["6789"]
    assert cands["銀行帳戶/星展"] == ["literal-pw"]
    assert cands["手動下載/國泰世華"] == ["A123456789"]  # alias
    assert version == hashlib.sha256(pwfile.read_bytes()).hexdigest()


def test_password_key_by_root():
    assert passwords.password_key("mail", "信用卡/國泰世華") == "信用卡/國泰世華"
    assert passwords.password_key("manual", "國泰世華") == "手動下載/國泰世華"


def test_unknown_folder_has_no_candidates(pwfile):
    cands, _ = passwords.load(pwfile)
    assert cands.get("信用卡/不存在", []) == []


def test_identity_shares_the_parser(tmp_path):
    p = tmp_path / "pw.env"
    p.write_text("  STATEMENT_ID_NUMBER=A123456789  # id\n\tSTATEMENT_BIRTH_DATE=19900101\n"
                 "STATEMENT_HOLDER_NAMES=王小明,WANG\\, XIAO\n信用卡/x=$ID\n", encoding="utf-8")
    ident = passwords.identity(p)
    assert ident.id_number == "A123456789" and ident.birth_date == "19900101"
    assert ident.holder_names == ["王小明", "WANG, XIAO"]
    assert passwords.load(p)[0]["信用卡/x"] == ["A123456789"]


def test_repr_never_shows_values(pwfile):
    cands, _ = passwords.load(pwfile)
    assert "A123456789" not in passwords.describe(cands)
    assert "信用卡/國泰世華=1" in passwords.describe(cands)
