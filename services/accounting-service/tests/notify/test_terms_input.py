from datetime import date

import pytest

from notify.api import ApiError, ReadClient
from notify.terms_config import CardEntry, TermsConfig
from notify.terms_input import RULE_FIELDS, build_input, select_cards, to_json
from tests.notify.notify_fakes import make_client

TODAY = date(2026, 11, 3)


def _rule(rid, enabled=True, ends_on=None, **extra):
    r = {"id": rid, "name": f"r{rid}", "method": "percent", "rate": "0.03",
         "fixed_amount": None, "is_basic": False, "total_cap": None, "window": None,
         "starts_on": "2026-01-01", "ends_on": ends_on, "description": "d",
         "is_enabled": enabled, "extra_internal": "x"}
    r.update(extra)
    return r


def _card(account_id, **kw):
    return CardEntry(account_id=account_id, official="https://bank.example/c",
                     bank_domains=("bank.example",), tier="precise", **kw)


def _cfg(*cards, ignore=frozenset()):
    return TermsConfig(report_dir=None, hermes_model="m", cards=tuple(cards),
                       ignore_accounts=ignore)


def test_live_rule_filter_drops_disabled_and_ended():
    routes = {
        "/reward-rules": {"accounts": [], "counts": {}},
        "/accounts/101/reward-rules": [
            _rule(1), _rule(2, enabled=False), _rule(3, ends_on="2026-11-02"),
            _rule(4, ends_on="2026-11-03"), _rule(5, ends_on="2027-01-01")],
    }
    client, _ = make_client(routes)
    inp = build_input(ReadClient(client, "t"), _cfg(_card(101)), TODAY, "scheduled")
    assert [r["id"] for r in inp.cards[0].rules] == [1, 4, 5]


def test_zero_rule_card_still_included():
    client, _ = make_client({"/reward-rules": {"accounts": []},
                             "/accounts/101/reward-rules": {"rules": []}})
    inp = build_input(ReadClient(client, "t"), _cfg(_card(101)), TODAY, "scheduled")
    assert len(inp.cards) == 1 and inp.cards[0].rules == ()


def test_unregistered_excludes_ignore_accounts():
    active = {"accounts": [
        {"account_id": 205, "account_name": "Z", "rules": [_rule(9)]},
        {"account_id": 101, "account_name": "Reg", "rules": [_rule(1)]},
        {"account_id": 202, "account_name": "Ign", "rules": [_rule(2)]},
        {"account_id": 204, "account_name": "Empty", "rules": []},
        {"account_id": 203, "account_name": "A", "rules": [_rule(3)]}]}
    client, _ = make_client({"/reward-rules": active, "/accounts/101/reward-rules": []})
    inp = build_input(ReadClient(client, "t"),
                      _cfg(_card(101), ignore=frozenset({202})), TODAY, "scheduled")
    assert inp.unregistered == ((203, "A"), (205, "Z"))


def test_year_end_selects_window_cards_only():
    cfg = _cfg(_card(101), _card(102, year_end_window=False))
    assert [c.account_id for c in select_cards(cfg, "year-end")] == [101]
    assert [c.account_id for c in select_cards(cfg, "scheduled")] == [101, 102]


def test_build_input_year_end_omits_non_window_cards():
    cfg = _cfg(_card(101), _card(301, country="US", year_end_window=False))
    client, log = make_client({"/reward-rules": {"accounts": []},
                               "/accounts/101/reward-rules": [_rule(1)],
                               "/accounts/301/reward-rules": [_rule(2)]})
    inp = build_input(ReadClient(client, "t"), cfg, TODAY, "year-end")
    assert [c.card.account_id for c in inp.cards] == [101]
    assert "/accounts/301/reward-rules" not in [r.url.path for r in log]


def test_rule_fields_are_exactly_RULE_FIELDS():
    client, _ = make_client({"/reward-rules": {"accounts": []},
                             "/accounts/101/reward-rules": [_rule(1)]})
    inp = build_input(ReadClient(client, "t"), _cfg(_card(101)), TODAY, "scheduled")
    assert tuple(inp.cards[0].rules[0]) == RULE_FIELDS
    out = to_json(inp)
    assert out["today"] == "2026-11-03" and out["cause"] == "scheduled"
    card = out["cards"][0]
    assert card["bank_domains"] == ["bank.example"] and card["aliases"] == []
    assert set(card) == {"account_id", "official", "bank_domains", "tier", "country",
                         "language", "currency", "aliases", "year_end_window", "rules"}
    assert out["unregistered"] == []


def test_api_error_propagates():
    client, _ = make_client({"/reward-rules": {"accounts": []}})
    with pytest.raises(ApiError):
        build_input(ReadClient(client, "t"), _cfg(_card(101)), TODAY, "scheduled")
