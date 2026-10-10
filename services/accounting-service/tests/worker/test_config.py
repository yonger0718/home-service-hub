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
