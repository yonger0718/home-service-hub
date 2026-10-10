import pytest

from pathlib import Path

from worker import config


def test_defaults_expand_home():
    cfg = config.load({})
    assert cfg.api_url == "http://127.0.0.1:8000"
    assert cfg.state_dir == Path.home() / ".local/state/home-hub-statements"
    assert cfg.inbox_dir == cfg.state_dir / "inbox" / "by-sha"
    assert cfg.mail_root == "財務對帳單/銀行" and cfg.manual_root == "財務對帳單/手動下載"
    assert cfg.parser_sandbox is True and cfg.minio_endpoint is None


def test_env_overrides_and_booleans(tmp_path):
    cfg = config.load({"STATEMENT_STATE_DIR": str(tmp_path), "STATEMENT_PARSER_SANDBOX": "no",
                       "STATEMENT_PARSER_TIMEOUT": "7", "STATEMENT_MINIO_ENDPOINT": "127.0.0.1:9000"})
    assert cfg.state_dir == tmp_path and cfg.lock_path == tmp_path / ".lock"
    assert cfg.parser_sandbox is False and cfg.parser_timeout_s == 7
    assert cfg.minio_endpoint == "127.0.0.1:9000"


def test_empty_env_values_mean_default():
    cfg = config.load({"STATEMENT_STATE_DIR": "", "STATEMENT_API_URL": "  ",
                       "STATEMENT_PARSER_SANDBOX": "", "STATEMENT_DRIVE_ROOT": " ",
                       "STATEMENT_PARSER_TIMEOUT": "", "STATEMENT_PARSER_CLI": ""})
    assert cfg.state_dir == Path.home() / ".local/state/home-hub-statements"
    assert cfg.api_url == "http://127.0.0.1:8000"
    assert cfg.parser_sandbox is True and cfg.parser_timeout_s == 120
    assert cfg.drive_root == "財務對帳單"
    assert cfg.parser_cli == Path("/home/opc/.local/bin/claude")


@pytest.mark.parametrize("raw,expected", [("YES", True), ("1", True), ("on", True),
                                          ("0", False), ("no", False)])
def test_boolean_variants(raw, expected):
    assert config.load({"STATEMENT_PARSER_SANDBOX": raw}).parser_sandbox is expected


def test_verify_db_url_hidden_from_repr():
    cfg = config.load({"STATEMENT_VERIFY_DB_URL": "postgresql://ro:synthpw@localhost/db"})
    assert cfg.verify_db_url.endswith("synthpw@localhost/db")
    assert "synthpw" not in repr(cfg)
