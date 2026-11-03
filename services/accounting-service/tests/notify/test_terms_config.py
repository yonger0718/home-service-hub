from pathlib import Path

import pytest

from notify.config import ConfigError, load_config

REPO = Path(__file__).resolve().parents[4]
EXAMPLE_FILE = REPO / "services/accounting-service/deploy/notify/homehub-notify.example.toml"

MINIMAL = (
    'api_base = "http://x"\n'
    'token_file = "~/tok"\n'
    'hub_base_url = "https://hub.example.invalid"\n'
    'discord_target = "discord:1"\n'
)
TERMS = '\n[terms]\nreport_dir = "~/r"\nhermes_model = "m"\n'
CARD = (
    '\n[terms.cards."101"]\nofficial = "A"\nbank_domains = ["a.com"]\ntier = "precise"\n'
)


def _load(tmp_path, body):
    p = tmp_path / "c.toml"
    p.write_text(MINIMAL + body)
    return load_config(p, with_terms=True)


def _card_err(tmp_path, old, new):
    body = TERMS + CARD.replace(old, new) if old else TERMS + CARD + new + "\n"
    with pytest.raises(ConfigError) as exc:
        _load(tmp_path, body)
    return str(exc.value)


def test_example_toml_loads_three_cards():
    terms = load_config(EXAMPLE_FILE, with_terms=True).terms
    cards = {c.account_id: c for c in terms.cards}
    assert list(cards) == [101, 102, 301]
    assert cards[301].year_end_window is False and cards[301].currency == "USD"
    assert cards[101].year_end_window is True


def test_terms_absent_is_none(tmp_path):
    assert _load(tmp_path, "").terms is None


def test_bad_tier_names_key(tmp_path):
    assert _card_err(tmp_path, 'tier = "precise"', 'tier = "x"') == "terms.cards.101.tier"


def test_country_must_be_two_upper(tmp_path):
    assert _card_err(tmp_path, "", 'country = "tw"') == "terms.cards.101.country"


def test_language_must_be_bcp47(tmp_path):
    assert _card_err(tmp_path, "", 'language = "english"') == "terms.cards.101.language"
    cfg = _load(tmp_path, TERMS + CARD + 'language = "zh-Hant-TW"\n')
    assert cfg.terms.cards[0].language == "zh-Hant-TW"


def test_currency_three_upper(tmp_path):
    assert _card_err(tmp_path, "", 'currency = "usd"') == "terms.cards.101.currency"


@pytest.mark.parametrize("bad", ['"x"', "[1]"])
def test_aliases_list_of_strings(tmp_path, bad):
    assert _card_err(tmp_path, "", f"aliases = {bad}") == "terms.cards.101.aliases"


def test_toolsets_other_than_web_rejected_at_load(tmp_path):
    with pytest.raises(ConfigError) as exc:
        _load(tmp_path, TERMS + 'hermes_toolsets = "web,browser"\n' + CARD)
    assert str(exc.value) == "terms.hermes_toolsets"


def test_hermes_model_required(tmp_path):
    with pytest.raises(ConfigError) as exc:
        _load(tmp_path, '\n[terms]\nreport_dir = "~/r"\n')
    assert str(exc.value) == "terms.hermes_model"


def test_domains_normalised(tmp_path):
    body = TERMS + CARD.replace('["a.com"]', '["HSBC.com.tw."]')
    assert _load(tmp_path, body).terms.cards[0].bank_domains == ("hsbc.com.tw",)


def test_ignore_accounts_ints(tmp_path):
    cfg = _load(tmp_path, TERMS.replace("[terms]\n", "[terms]\nignore_accounts = [5, 7]\n") + CARD)
    assert cfg.terms.ignore_accounts == frozenset({5, 7})
    with pytest.raises(ConfigError) as exc:
        _load(tmp_path, TERMS.replace("[terms]\n", '[terms]\nignore_accounts = ["a"]\n'))
    assert str(exc.value) == "terms.ignore_accounts"


def test_card_key_must_be_int(tmp_path):
    with pytest.raises(ConfigError) as exc:
        _load(tmp_path, TERMS + CARD.replace('"101"', '"abc"'))
    assert str(exc.value) == "terms.cards.abc"


def test_broken_terms_block_loads_without_with_terms(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text(MINIMAL + '\n[terms]\nreport_dir = "~/r"\nhermes_model = "m"\nhermes_toolsets = "all"\n')
    assert load_config(p).terms is None
    with pytest.raises(ConfigError):
        load_config(p, with_terms=True)


# --- final fix A7 / A8 ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("key", ["1_01", "+101", " 101", "-5", "0", "0101", "101 ", "１０１"])
def test_card_key_must_be_canonical_digits(tmp_path, key):
    with pytest.raises(ConfigError) as exc:
        _load(tmp_path, TERMS + CARD.replace('"101"', f'"{key}"'))
    assert str(exc.value) == f"terms.cards.{key}"


def test_duplicate_account_id_rejected():
    from notify.terms_config import load_terms

    class Twice(dict):  # TOML keys are unique; this is the defensive duplicate check
        def items(self):
            card = {"official": "A", "bank_domains": ["a.com"], "tier": "precise"}
            return [("101", card), ("101", card)]
    with pytest.raises(ConfigError) as exc:
        load_terms({"terms": {"report_dir": "~/r", "hermes_model": "m", "cards": Twice(x=1)}})
    assert str(exc.value) == "terms.cards.101"


@pytest.mark.parametrize("bad", ["[]", '["com"]', '["a..com"]', '["-a.com"]', '["a_b.com"]', '["https://a.com"]',
                                 '["a.com/x"]', '["a .com"]', '[""]', '["a.com", 5]', '"a.com"'])
def test_bank_domains_must_be_ldh_hostnames(tmp_path, bad):
    assert _card_err(tmp_path, '["a.com"]', bad) == "terms.cards.101.bank_domains"


def test_bank_domains_accepts_hostnames(tmp_path):
    body = TERMS + CARD.replace('["a.com"]', '["Bank-1.example.com.tw", "x.co"]')
    assert _load(tmp_path, body).terms.cards[0].bank_domains == ("bank-1.example.com.tw", "x.co")


@pytest.mark.parametrize("bad", ['"yes"', "1"])
def test_year_end_window_must_be_bool(tmp_path, bad):
    assert _card_err(tmp_path, "", f"year_end_window = {bad}") == "terms.cards.101.year_end_window"


def test_ignore_accounts_string_rejected(tmp_path):
    with pytest.raises(ConfigError) as exc:
        _load(tmp_path, TERMS.replace("[terms]\n", '[terms]\nignore_accounts = "a"\n') + CARD)
    assert str(exc.value) == "terms.ignore_accounts"


@pytest.mark.parametrize("bad", ["0", "-1", "true", '"9"'])
def test_max_output_bytes_positive_int(tmp_path, bad):
    with pytest.raises(ConfigError) as exc:
        _load(tmp_path, TERMS.replace("[terms]\n", f"[terms]\nmax_output_bytes = {bad}\n") + CARD)
    assert str(exc.value) == "terms.max_output_bytes"


@pytest.mark.parametrize("bad", ["0", "-1", "1841", "true"])
def test_hermes_timeout_within_unit_timeout(tmp_path, bad):
    with pytest.raises(ConfigError) as exc:
        _load(tmp_path, TERMS.replace("[terms]\n", f"[terms]\nhermes_timeout_seconds = {bad}\n") + CARD)
    assert str(exc.value) == "terms.hermes_timeout_seconds"
    for ok in (1, 1840):
        cfg = _load(tmp_path, TERMS.replace("[terms]\n", f"[terms]\nhermes_timeout_seconds = {ok}\n") + CARD)
        assert cfg.terms.hermes_timeout_seconds == ok


def test_defaults_for_provider_and_state_file(tmp_path):
    t = _load(tmp_path, TERMS + CARD).terms
    assert t.hermes_provider == "anthropic" and t.terms_state_file.name == "terms-state.json"
    assert t.hermes_timeout_seconds == 1800 and t.max_output_bytes == 4194304
