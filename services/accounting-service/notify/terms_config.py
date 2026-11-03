"""The notifier's optional ``[terms]`` section: Hermes settings and the card registry."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from notify.config import ConfigError

DEFAULT_TERMS_STATE_FILE = "~/.local/state/homehub-notify/terms-state.json"
_TIERS = ("precise", "signal")
_BCP47 = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$")
_CARD_KEY = re.compile(r"[1-9][0-9]*")
_LDH = r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
_HOSTNAME = re.compile(rf"{_LDH}(\.{_LDH})+")  # LDH labels, at least one dot
MAX_HERMES_TIMEOUT = 1840  # the units' TimeoutStartSec=1900 must outlast Hermes by 60 s


@dataclass(frozen=True)
class CardEntry:
    account_id: int
    official: str
    bank_domains: tuple[str, ...]
    tier: str  # "precise" | "signal"
    country: str = "TW"
    language: str = "zh-TW"
    currency: str = "TWD"
    aliases: tuple[str, ...] = ()
    year_end_window: bool = True


@dataclass(frozen=True)
class TermsConfig:
    report_dir: Path
    hermes_model: str
    hermes_toolsets: str = "web"
    max_output_bytes: int = 4194304
    hermes_timeout_seconds: int = 1800
    hermes_provider: str = "anthropic"
    ignore_accounts: frozenset[int] = frozenset()
    terms_state_file: Path = Path(DEFAULT_TERMS_STATE_FILE).expanduser()
    cards: tuple[CardEntry, ...] = ()


def _str(raw: dict, key: str, path: str, default: str | None = None) -> str:
    value = raw.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(path)
    return value.strip()


def _int(raw: dict, key: str, path: str, default: int) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(path)
    return value


def _str_list(raw: dict, key: str, path: str, *, non_empty: bool) -> list[str]:
    value = raw.get(key, [])
    if (not isinstance(value, list) or (non_empty and not value)
            or not all(isinstance(v, str) and v.strip() for v in value)):
        raise ConfigError(path)
    return [v.strip() for v in value]


def _pattern(raw: dict, key: str, path: str, default: str, pattern: str) -> str:
    value = raw.get(key, default)
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise ConfigError(path)
    return value


def _bool(raw: dict, key: str, path: str, default: bool) -> bool:
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(path)
    return value


def _card(key: str, raw: object) -> CardEntry:
    p = f"terms.cards.{key}"
    if not isinstance(key, str) or not _CARD_KEY.fullmatch(key):
        raise ConfigError(p)
    account_id = int(key)
    if not isinstance(raw, dict):
        raise ConfigError(p)
    tier = raw.get("tier")
    if tier not in _TIERS:
        raise ConfigError(f"{p}.tier")
    country = _pattern(raw, "country", f"{p}.country", "TW", r"[A-Z]{2}")
    language = raw.get("language", "zh-TW")
    if not isinstance(language, str) or not _BCP47.fullmatch(language):
        raise ConfigError(f"{p}.language")
    domains = [d.lower().rstrip(".") for d in _str_list(raw, "bank_domains", f"{p}.bank_domains", non_empty=True)]
    if not all(_HOSTNAME.fullmatch(d) for d in domains):
        raise ConfigError(f"{p}.bank_domains")
    return CardEntry(
        account_id=account_id,
        official=_str(raw, "official", f"{p}.official"),
        bank_domains=tuple(domains),
        tier=tier,
        country=country,
        language=language,
        currency=_pattern(raw, "currency", f"{p}.currency", "TWD", r"[A-Z]{3}"),
        aliases=tuple(_str_list(raw, "aliases", f"{p}.aliases", non_empty=False)),
        year_end_window=_bool(raw, "year_end_window", f"{p}.year_end_window", country == "TW"),
    )


def load_terms(raw: dict) -> TermsConfig | None:
    """Parse ``[terms]`` from the loaded TOML; None when the section is absent."""
    if "terms" not in raw:
        return None
    t = raw["terms"]
    if not isinstance(t, dict):
        raise ConfigError("terms")
    toolsets = _str(t, "hermes_toolsets", "terms.hermes_toolsets", "web")
    if {s.strip() for s in toolsets.split(",")} != {"web"}:
        raise ConfigError("terms.hermes_toolsets")
    ignore = t.get("ignore_accounts", [])
    if (not isinstance(ignore, list)
            or not all(isinstance(v, int) and not isinstance(v, bool) for v in ignore)):
        raise ConfigError("terms.ignore_accounts")
    cards_raw = t.get("cards", {})
    if not isinstance(cards_raw, dict):
        raise ConfigError("terms.cards")
    cards: list[CardEntry] = []
    for key, value in cards_raw.items():
        card = _card(key, value)
        if any(c.account_id == card.account_id for c in cards):
            raise ConfigError(f"terms.cards.{key}")
        cards.append(card)
    timeout = _int(t, "hermes_timeout_seconds", "terms.hermes_timeout_seconds", 1800)
    if timeout > MAX_HERMES_TIMEOUT:
        raise ConfigError("terms.hermes_timeout_seconds")
    return TermsConfig(
        report_dir=Path(_str(t, "report_dir", "terms.report_dir")).expanduser(),
        hermes_model=_str(t, "hermes_model", "terms.hermes_model"),
        hermes_toolsets="web",
        max_output_bytes=_int(t, "max_output_bytes", "terms.max_output_bytes", 4194304),
        hermes_timeout_seconds=timeout,
        hermes_provider=_str(t, "hermes_provider", "terms.hermes_provider", "anthropic"),
        ignore_accounts=frozenset(ignore),
        terms_state_file=Path(_str(t, "terms_state_file", "terms.terms_state_file",
                                   DEFAULT_TERMS_STATE_FILE)).expanduser(),
        cards=tuple(cards),
    )
