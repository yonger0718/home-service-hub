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
    return load_config(p)


def _card_err(tmp_path, old, new):
    body = TERMS + CARD.replace(old, new) if old else TERMS + CARD + new + "\n"
    with pytest.raises(ConfigError) as exc:
        _load(tmp_path, body)
    return str(exc.value)


def test_example_toml_loads_three_cards():
    terms = load_config(EXAMPLE_FILE).terms
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
