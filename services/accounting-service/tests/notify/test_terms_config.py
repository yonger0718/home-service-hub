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


# --- dismiss list (#287) ---

def _dismiss(tmp_path, *entries, card=CARD):
    body = TERMS + card + "".join(f'\n[[terms.cards."101".dismiss]]\n{e}\n' for e in entries)
    return _load(tmp_path, body).terms.cards[0].dismiss


def _dismiss_err(tmp_path, *entries, card=CARD):
    with pytest.raises(ConfigError) as exc:
        _dismiss(tmp_path, *entries, card=card)
    return str(exc.value)


def test_card_without_dismiss_has_none(tmp_path):
    assert _load(tmp_path, TERMS + CARD).terms.cards[0].dismiss == ()


def test_dismiss_four_shapes_load(tmp_path):
    from datetime import date

    from notify.terms_config import Dismissal

    got = _dismiss(
        tmp_path,
        'field = "starts_on"\nrule = 162\nvalue = "2026-07-01"\nnote = "current period only"',
        'field = "description"\nrule = 163',
        'field = "missing_rule"\ncontains = ["LINE Pay", "10%"]\nuntil = "2026-12-31"',
        'field = "next_terms"\nvalue = "2027-03-31"',
    )
    assert got == (
        Dismissal("starts_on", rule=162, value="2026-07-01", note="current period only"),
        Dismissal("description", rule=163),
        Dismissal("missing_rule", contains=("LINE Pay", "10%"), until=date(2026, 12, 31)),
        Dismissal("next_terms", value="2027-03-31"),
    )


@pytest.mark.parametrize("field, raw, stored", [
    ("rate", "9.0", "9"), ("rate", '"9.0000"', "9"), ("rate", "9", "9"), ("total_cap", '"60000.50"', "60000.5"),
    ("fixed_amount", "100", "100"), ("total_cap", '"none"', "none"), ("ends_on", '"none"', "none"),
    ("is_basic", "true", "true"), ("is_basic", '"false"', "false"), ("window", '"calendar_month"', "calendar_month"),
    ("method", '"per_unit"', "per_unit"), ("ends_on", '"2027-03-31"', "2027-03-31"),
])
def test_dismiss_pin_is_normalised(tmp_path, field, raw, stored):
    (d,) = _dismiss(tmp_path, f'field = "{field}"\nrule = 1\nvalue = {raw}')
    assert d.value == stored


@pytest.mark.parametrize("entry, key", [
    ('field = "posting"\nrule = 1', "field"),
    ('field = "id"\nrule = 1', "field"),
    ('rule = 1', "field"),
    ('field = "rate"', "rule"),
    ('field = "rate"\nrule = 0', "rule"),
    ('field = "rate"\nrule = true', "rule"),
    ('field = "rate"\nrule = "1"', "rule"),
    ('field = "missing_rule"\nrule = 1\ncontains = ["ab"]\nuntil = "2026-12-31"', "rule"),
    ('field = "next_terms"\nrule = 1\nvalue = "2027-03-31"', "rule"),
    ('field = "name"\nrule = 1\nvalue = "x"', "value"),
    ('field = "description"\nrule = 1\nvalue = "x"', "value"),
    ('field = "missing_rule"\nvalue = "x"\ncontains = ["ab"]\nuntil = "2026-12-31"', "value"),
    ('field = "starts_on"\nrule = 1\nvalue = "2026-13-01"', "value"),
    ('field = "starts_on"\nrule = 1\nvalue = "2026/07/01"', "value"),
    ('field = "starts_on"\nrule = 1\nvalue = 20260701', "value"),
    ('field = "rate"\nrule = 1\nvalue = "nine"', "value"),
    ('field = "rate"\nrule = 1\nvalue = true', "value"),
    ('field = "rate"\nrule = 1\nvalue = "1e3"', "value"),
    ('field = "is_basic"\nrule = 1\nvalue = "yes"', "value"),
    ('field = "is_basic"\nrule = 1\nvalue = "none"', "value"),
    ('field = "window"\nrule = 1\nvalue = "Calendar Month"', "value"),
    ('field = "method"\nrule = 1\nvalue = "none"', "value"),
    ('field = "next_terms"', "value"),
    ('field = "next_terms"\nvalue = "none"', "value"),
    ('field = "next_terms"\nvalue = "soon"', "value"),
    ('field = "missing_rule"\nuntil = "2026-12-31"', "contains"),
    ('field = "missing_rule"\ncontains = []\nuntil = "2026-12-31"', "contains"),
    ('field = "missing_rule"\ncontains = ["a"]\nuntil = "2026-12-31"', "contains"),
    ('field = "missing_rule"\ncontains = ["ab", " "]\nuntil = "2026-12-31"', "contains"),
    ('field = "missing_rule"\ncontains = "LINE Pay"\nuntil = "2026-12-31"', "contains"),
    ('field = "rate"\nrule = 1\ncontains = ["ab"]', "contains"),
    ('field = "next_terms"\nvalue = "2027-03-31"\ncontains = ["ab"]', "contains"),
    ('field = "missing_rule"\ncontains = ["ab"]', "until"),
    ('field = "rate"\nrule = 1\nuntil = "tomorrow"', "until"),
    ('field = "rate"\nrule = 1\nuntil = 20261231', "until"),
    ('field = "rate"\nrule = 1\nnote = 5', "note"),
    ('field = "rate"\nrule = 1\nnote = "' + "x" * 201 + '"', "note"),
    ('field = "rate"\nrule = 1\nreason = "x"', "reason"),
])
def test_dismiss_refusals_name_the_key(tmp_path, entry, key):
    assert _dismiss_err(tmp_path, entry) == f"terms.cards.101.dismiss[0].{key}"


def test_dismiss_error_names_the_entry_index(tmp_path):
    assert _dismiss_err(tmp_path, 'field = "rate"\nrule = 1', 'field = "rate"') == "terms.cards.101.dismiss[1].rule"


def test_dismiss_must_be_a_list_of_tables(tmp_path):
    with pytest.raises(ConfigError) as exc:
        _load(tmp_path, TERMS + CARD + 'dismiss = "all"\n')
    assert str(exc.value) == "terms.cards.101.dismiss"
    with pytest.raises(ConfigError) as exc:
        _load(tmp_path, TERMS + CARD + 'dismiss = ["x"]\n')
    assert str(exc.value) == "terms.cards.101.dismiss[0]"


def test_dismiss_on_a_signal_card_is_refused(tmp_path):
    signal = CARD.replace('tier = "precise"', 'tier = "signal"')
    assert _dismiss_err(tmp_path, 'field = "rate"\nrule = 1', card=signal) == "terms.cards.101.dismiss"


def test_note_at_the_limit_loads(tmp_path):
    (d,) = _dismiss(tmp_path, 'field = "rate"\nrule = 1\nnote = "' + "x" * 200 + '"')
    assert len(d.note) == 200


def test_toml_date_literals_are_accepted(tmp_path):
    from datetime import date

    (d,) = _dismiss(tmp_path, 'field = "starts_on"\nrule = 1\nvalue = 2026-07-01\nuntil = 2026-12-31')
    assert (d.value, d.until) == ("2026-07-01", date(2026, 12, 31))
    assert _dismiss_err(tmp_path, 'field = "rate"\nrule = 1\nuntil = 2026-12-31T00:00:00') == (
        "terms.cards.101.dismiss[0].until")


def test_example_toml_documents_the_four_dismiss_shapes():
    cards = {c.account_id: c for c in load_config(EXAMPLE_FILE, with_terms=True).terms.cards}
    got = [(d.field, d.rule, d.value, d.contains, d.until is not None) for d in cards[101].dismiss]
    assert got == [("starts_on", 162, "2026-07-01", (), False), ("description", 163, None, (), False),
                   ("missing_rule", None, None, ("LINE Pay", "10%"), True), ("next_terms", None, "2027-03-31", (), False)]
    assert cards[101].dismiss[3].rates == (("0.88", None), ("1", "200"), ("1", "200"), ("3", "888"))
    assert cards[102].dismiss == () and cards[301].dismiss == ()


# --- next_terms: pin on the announced rates and caps ---

def test_next_terms_rates_pin_is_normalised_and_sorted(tmp_path):
    (d,) = _dismiss(tmp_path, 'field = "next_terms"\nvalue = "2027-03-31"\n'
                              'rates = ["3.0/888", "0.88/none", "1/200.00", "1/200", "none/none"]')
    assert d.rates == ((None, None), ("0.88", None), ("1", "200"), ("1", "200"), ("3", "888"))


def test_next_terms_without_rates_has_no_rates_pin(tmp_path):
    (d,) = _dismiss(tmp_path, 'field = "next_terms"\nvalue = "2027-03-31"')
    assert d.rates is None


@pytest.mark.parametrize("entry", [
    'field = "next_terms"\nvalue = "2027-03-31"\nrates = "3/888"',
    'field = "next_terms"\nvalue = "2027-03-31"\nrates = []',
    'field = "next_terms"\nvalue = "2027-03-31"\nrates = ["3"]',
    'field = "next_terms"\nvalue = "2027-03-31"\nrates = ["3/888/1"]',
    'field = "next_terms"\nvalue = "2027-03-31"\nrates = ["three/888"]',
    'field = "next_terms"\nvalue = "2027-03-31"\nrates = [3]',
    'field = "rate"\nrule = 1\nrates = ["3/888"]',
    'field = "missing_rule"\ncontains = ["ab"]\nuntil = "2026-12-31"\nrates = ["3/888"]',
])
def test_rates_refusals(tmp_path, entry):
    assert _dismiss_err(tmp_path, entry) == "terms.cards.101.dismiss[0].rates"
