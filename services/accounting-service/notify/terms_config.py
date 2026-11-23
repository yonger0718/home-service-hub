"""The notifier's optional ``[terms]`` section: Hermes settings and the card registry."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from notify.config import ConfigError

DEFAULT_TERMS_STATE_FILE = "~/.local/state/homehub-notify/terms-state.json"
_TIERS = ("precise", "signal")
_BCP47 = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$")
_CARD_KEY = re.compile(r"[1-9][0-9]*")
_LDH = r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
_HOSTNAME = re.compile(rf"{_LDH}(\.{_LDH})+")  # LDH labels, at least one dot
MAX_HERMES_TIMEOUT = 1840  # the units' TimeoutStartSec=1900 must outlast Hermes by 60 s

# Dismiss list (#287): which findings the owner accepted. A rule field can be pinned to the value the bank
# publishes when that value has a stable form; free text (name, description) can only be dismissed whole.
DATE_FIELDS = ("starts_on", "ends_on")
NUMBER_FIELDS = ("rate", "fixed_amount", "total_cap")
TOKEN_FIELDS = ("method", "window")
TEXT_FIELDS = ("name", "description")
DISMISS_RULE_FIELDS = DATE_FIELDS + NUMBER_FIELDS + TOKEN_FIELDS + TEXT_FIELDS + ("is_basic",)
NO_VALUE = "none"  # pin: the bank gives no value (date and number fields)
_DISMISS_KEYS = frozenset({"field", "rule", "value", "contains", "until", "note"})
_NUMBER = re.compile(r"[0-9]+(\.[0-9]+)?")
_TOKEN = re.compile(r"[a-z_]+")
_NOTE_MAX = 200


@dataclass(frozen=True)
class Dismissal:
    field: str  # a compared rule field, "missing_rule" or "next_terms"
    rule: int | None = None
    value: str | None = None  # normalised pin; NO_VALUE = the bank gives no value
    contains: tuple[str, ...] = ()
    until: date | None = None
    note: str = ""


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
    dismiss: tuple[Dismissal, ...] = ()


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


def _iso_date(value: object, path: str) -> date:
    """An ISO string or a TOML date literal; a datetime is refused."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            pass
    raise ConfigError(path)


def format_number(value: Decimal) -> str:
    """One spelling per number, so a pin and a site value compare as text: ``9.0000`` and ``9`` are both ``9``."""
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _pin(field: str, value: object, path: str) -> str:
    if field in DATE_FIELDS or field in NUMBER_FIELDS:
        if value == NO_VALUE:
            return NO_VALUE
    if field in DATE_FIELDS or field == "next_terms":
        return _iso_date(value, path).isoformat()
    if field in NUMBER_FIELDS:
        text = str(value).strip() if isinstance(value, (int, float, str)) and not isinstance(value, bool) else ""
        if not _NUMBER.fullmatch(text):
            raise ConfigError(path)
        return format_number(Decimal(text))
    if field == "is_basic":
        if isinstance(value, bool):
            return "true" if value else "false"
        if value in ("true", "false"):
            return value
        raise ConfigError(path)
    if field in TOKEN_FIELDS and isinstance(value, str) and _TOKEN.fullmatch(value) and value != NO_VALUE:
        return value
    raise ConfigError(path)  # name / description / missing_rule take no pin


def _dismissal(raw: object, p: str) -> Dismissal:
    if not isinstance(raw, dict):
        raise ConfigError(p)
    for key in raw:
        if key not in _DISMISS_KEYS:
            raise ConfigError(f"{p}.{key}")
    field = raw.get("field")
    is_rule_field = field in DISMISS_RULE_FIELDS
    if not is_rule_field and field not in ("missing_rule", "next_terms"):
        raise ConfigError(f"{p}.field")
    rule = raw.get("rule")
    if is_rule_field != ("rule" in raw) or (is_rule_field and (
            isinstance(rule, bool) or not isinstance(rule, int) or rule <= 0)):
        raise ConfigError(f"{p}.rule")
    value = None
    if "value" in raw or field == "next_terms":
        value = _pin(field, raw.get("value"), f"{p}.value")
    contains = raw.get("contains", [])
    if ((field == "missing_rule") != ("contains" in raw) or not isinstance(contains, list)
            or (field == "missing_rule" and not contains)
            or not all(isinstance(c, str) and len(c.strip()) >= 2 for c in contains)):
        raise ConfigError(f"{p}.contains")
    if "until" not in raw and field == "missing_rule":
        raise ConfigError(f"{p}.until")
    until = _iso_date(raw["until"], f"{p}.until") if "until" in raw else None
    note = raw.get("note", "")
    if not isinstance(note, str) or len(note) > _NOTE_MAX:
        raise ConfigError(f"{p}.note")
    return Dismissal(field, rule=rule if is_rule_field else None, value=value,
                     contains=tuple(c.strip() for c in contains), until=until, note=note)


def _dismissals(raw: dict, p: str, tier: str) -> tuple[Dismissal, ...]:
    entries = raw.get("dismiss", [])
    if not isinstance(entries, list) or (entries and tier != "precise"):
        raise ConfigError(p)  # a signal card has no findings to dismiss
    return tuple(_dismissal(e, f"{p}[{i}]") for i, e in enumerate(entries))


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
        dismiss=_dismissals(raw, f"{p}.dismiss", tier),
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
