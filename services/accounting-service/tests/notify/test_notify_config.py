from pathlib import Path

import pytest

from notify.config import ConfigError, load_config, read_token

REPO = Path(__file__).resolve().parents[4]
EXAMPLE_FILE = REPO / "services/accounting-service/deploy/notify/homehub-notify.example.toml"
MINIMAL = (
    'api_base = "http://x"\n'
    'token_file = "~/tok"\n'
    'hub_base_url = "https://hub.example.invalid"\n'
    'discord_target = "discord:1"\n'
)


def test_load_config_example():
    cfg = load_config(EXAMPLE_FILE)
    assert cfg.lead_business_days == 2 and cfg.discord_target.startswith("discord:")
    assert cfg.lead_overrides == {}


def test_defaults_and_tilde(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text(MINIMAL)
    cfg = load_config(p)
    assert "~" not in str(cfg.token_file) and "~" not in str(cfg.state_file)
    assert cfg.weekly_open_case_limit == 10 and cfg.expected_statement_grace_days == 7
    assert str(cfg.min_buffer) == "0"


def test_lead_overrides_keys_are_ints(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text(MINIMAL + '\n[lead_overrides]\n"123" = 4\n')
    assert load_config(p).lead_overrides == {123: 4}


def test_min_buffer_string_to_decimal(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('min_buffer = "150.50"\n' + MINIMAL)
    assert str(load_config(p).min_buffer) == "150.50"


def test_missing_required_key(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('api_base = "http://x"\n')
    with pytest.raises(ConfigError) as e:
        load_config(p)
    assert "token_file" in str(e.value) or "discord_target" in str(e.value)


def test_invalid_toml_is_config_error(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("= =")
    with pytest.raises(ConfigError):
        load_config(p)


def test_read_token(tmp_path):
    p = tmp_path / "t"
    p.write_text("  secret-value\n")
    assert read_token(p) == "secret-value"
    p.write_text(" \n")
    with pytest.raises(ConfigError) as e:
        read_token(p)
    assert str(e.value) == "token"
    with pytest.raises(ConfigError) as e:
        read_token(tmp_path / "missing")
    assert "secret" not in str(e.value)


def test_read_token_non_utf8(tmp_path):
    p = tmp_path / "t"
    p.write_bytes(b"\xff\xfesecret")
    with pytest.raises(ConfigError) as e:
        read_token(p)
    assert str(e.value) == "token"


def test_cathay_defaults(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text(MINIMAL)
    cfg = load_config(p)
    assert cfg.portfolio_base is None
    assert (cfg.cathay_run_time.hour, cfg.cathay_run_time.minute) == (6, 40)
    assert cfg.cathay_run_grace_minutes == 15


def test_cathay_values(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text(
        'portfolio_base = "http://127.0.0.1:8001/"\ncathay_run_time = "07:05"\n'
        "cathay_run_grace_minutes = 20\n" + MINIMAL
    )
    cfg = load_config(p)
    assert cfg.portfolio_base == "http://127.0.0.1:8001"
    assert (cfg.cathay_run_time.hour, cfg.cathay_run_time.minute) == (7, 5)
    assert cfg.cathay_run_grace_minutes == 20


@pytest.mark.parametrize(
    "line,key",
    [
        ('portfolio_base = ""', "portfolio_base"),
        ("portfolio_base = 5", "portfolio_base"),
        ('cathay_run_time = "6:40"', "cathay_run_time"),
        ('cathay_run_time = "25:00"', "cathay_run_time"),
        ("cathay_run_time = 640", "cathay_run_time"),
        ('cathay_run_grace_minutes = "15"', "cathay_run_grace_minutes"),
        ("cathay_run_grace_minutes = true", "cathay_run_grace_minutes"),
    ],
)
def test_cathay_invalid(tmp_path, line, key):
    p = tmp_path / "c.toml"
    p.write_text(line + "\n" + MINIMAL)
    with pytest.raises(ConfigError) as e:
        load_config(p)
    assert str(e.value) == key
