"""The frozen per-run input for the reward-terms check: cards, live rules, unregistered accounts."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from notify.api import ReadClient
from notify.terms_config import CardEntry, TermsConfig

RULE_FIELDS = ("id", "name", "method", "rate", "fixed_amount", "is_basic", "total_cap",
               "window", "starts_on", "ends_on", "description")


@dataclass(frozen=True)
class CardInput:
    card: CardEntry
    rules: tuple[dict, ...]


@dataclass(frozen=True)
class TermsInput:
    today: date
    cause: str
    cards: tuple[CardInput, ...]
    unregistered: tuple[tuple[int, str], ...]


def select_cards(cfg: TermsConfig, cause: str) -> tuple[CardEntry, ...]:
    if cause == "year-end":
        return tuple(c for c in cfg.cards if c.year_end_window)
    return tuple(cfg.cards)


def _is_live(rule: dict, today: date) -> bool:
    if not rule.get("is_enabled"):
        return False
    ends_on = rule.get("ends_on")
    return ends_on is None or date.fromisoformat(ends_on) >= today


def build_input(client: ReadClient, cfg: TermsConfig, today: date, cause: str) -> TermsInput:
    active = client.reward_rules_active()
    cards = tuple(
        CardInput(card, tuple({k: r.get(k) for k in RULE_FIELDS}
                              for r in client.account_reward_rules(card.account_id)
                              if _is_live(r, today)))
        for card in select_cards(cfg, cause))
    known = {c.account_id for c in cfg.cards} | set(cfg.ignore_accounts)
    unregistered = tuple(sorted(
        (a["account_id"], a["account_name"]) for a in active.get("accounts", [])
        if a.get("rules") and a["account_id"] not in known))
    return TermsInput(today, cause, cards, unregistered)


def to_json(inp: TermsInput) -> dict:
    return {
        "today": inp.today.isoformat(),
        "cause": inp.cause,
        "cards": [{
            "account_id": c.card.account_id, "official": c.card.official,
            "bank_domains": list(c.card.bank_domains), "tier": c.card.tier,
            "country": c.card.country, "language": c.card.language,
            "currency": c.card.currency, "aliases": list(c.card.aliases),
            "year_end_window": c.card.year_end_window,
            "rules": [dict(r) for r in c.rules],
        } for c in inp.cards],
        "unregistered": [[i, n] for i, n in inp.unregistered],
    }
