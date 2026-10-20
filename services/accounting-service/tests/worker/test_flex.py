import json
import logging
from pathlib import Path

import httpx
import pytest

from worker import config, flex

TOKEN = "abcdefghijklmnopqrstuv0123"


def _creds(tmp_path: Path, token: str = TOKEN, query_id: str = "1234567") -> Path:
    p = tmp_path / "flex.env"
    p.write_text(f"# ib\nIB_FLEX_TOKEN={token}\nIB_FLEX_QUERY_ID={query_id}\n", encoding="utf-8")
    return p


def test_load_credentials_reads_key_value_lines(tmp_path):
    creds = flex.load_credentials(_creds(tmp_path))
    assert (creds.token, creds.query_id) == (TOKEN, "1234567")


def test_credentials_repr_hides_the_token(tmp_path):
    creds = flex.load_credentials(_creds(tmp_path))
    assert TOKEN not in repr(creds) and TOKEN not in str(creds) and "1234567" in repr(creds)


@pytest.mark.parametrize("token, query_id, key", [
    ("", "1234567", "IB_FLEX_TOKEN"), ("short!", "1234567", "IB_FLEX_TOKEN"), (TOKEN, "homehub-daily", "IB_FLEX_QUERY_ID"), (TOKEN, "", "IB_FLEX_QUERY_ID"),
])
def test_load_credentials_names_only_the_key_on_a_bad_value(tmp_path, token, query_id, key):
    with pytest.raises(ValueError) as exc:
        flex.load_credentials(_creds(tmp_path, token, query_id))
    assert str(exc.value) == f"{key} missing or malformed"
    for secret in (token, query_id):
        assert not secret or secret not in str(exc.value)  # an empty value is a substring of anything: skip it
