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


def _write(tmp_path, text: str, *, bom: bool = False, crlf: bool = False):
    p = tmp_path / "pw.env"
    body = text.replace("\n", "\r\n") if crlf else text
    p.write_bytes(("﻿" if bom else "").encode("utf-8") + body.encode("utf-8"))
    return p


IDENT = "STATEMENT_ID_NUMBER=A123456789\nSTATEMENT_BIRTH_DATE=19900101\n"


def test_slices_and_birth_variants(tmp_path):
    p = _write(tmp_path, IDENT + "k/s1=$ID[2:5]\nk/s1.alt2=$ID[3]\nk/b6=$BIRTH6\nk/b4=$BIRTH4\n")
    cands, _ = passwords.load(p)
    assert cands["k/s1"] == ["234", "3"]
    assert cands["k/b6"] == ["900101"]
    assert cands["k/b4"] == ["0101"]


def test_bare_hash_is_kept_in_value(tmp_path):
    p = _write(tmp_path, "k/h=a#b\n")
    assert passwords.load(p)[0]["k/h"] == ["a#b"]


def test_crlf_file_matches_lf(tmp_path):
    p = _write(tmp_path, IDENT + "k/x=$ID\n", crlf=True)
    assert passwords.load(p)[0]["k/x"] == ["A123456789"]
    assert passwords.identity(p).birth_date == "19900101"


def test_bom_file_identity_present_and_no_bogus_key(tmp_path):
    p = _write(tmp_path, IDENT + "k/x=$ID\n", bom=True)
    ident = passwords.identity(p)
    assert ident.id_number == "A123456789" and ident.birth_date == "19900101"
    cands, version = passwords.load(p)
    assert cands == {"k/x": ["A123456789"]}
    assert version == hashlib.sha256(p.read_bytes()).hexdigest()


def test_missing_identity_yields_no_candidate(tmp_path):
    p = _write(tmp_path, "k/m=$ID\nk/n=$BIRTH8\nk/o=$ID+suffix\n")
    cands, _ = passwords.load(p)
    assert cands["k/m"] == [] and cands["k/n"] == [] and cands["k/o"] == []


def test_alias_to_missing_folder_has_no_candidates(tmp_path):
    p = _write(tmp_path, IDENT + "手動下載/x=@信用卡/none\n")
    assert passwords.load(p)[0]["手動下載/x"] == []


def test_alias_chain_resolves(tmp_path):
    p = _write(tmp_path, "x/a=@x/b\nx/b=@x/c\nx/c=zz\n")
    assert passwords.load(p)[0]["x/a"] == ["zz"]


def test_alias_cycle_raises_naming_key_only(tmp_path):
    p = _write(tmp_path, IDENT + "x/a=@x/b\nx/b=@x/a\n")
    with pytest.raises(ValueError, match="alias cycle at x/"):
        passwords.load(p)


def test_self_alias_raises(tmp_path):
    p = _write(tmp_path, "x/s=@x/s\n")
    with pytest.raises(ValueError, match="alias cycle at x/s"):
        passwords.load(p)


@pytest.mark.parametrize("rule", ["$ID[]", "$ID[10]", "$ID[-]"])
def test_bad_slice_names_key_only(tmp_path, rule):
    p = _write(tmp_path, IDENT + f"x/bad={rule}\n")
    with pytest.raises(ValueError) as err:
        passwords.load(p)
    assert str(err.value) == "bad rule in x/bad"


def test_identity_repr_hides_values(tmp_path):
    p = _write(tmp_path, "STATEMENT_ID_NUMBER=A123456789\nSTATEMENT_BIRTH_DATE=19900101\n"
                         "STATEMENT_HOLDER_NAMES=王小明\n")
    text = repr(passwords.identity(p))
    assert "A123456789" not in text and "19900101" not in text and "王小明" not in text
