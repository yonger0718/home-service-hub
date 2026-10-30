"""Notifier configuration, read from a TOML file (not env vars)."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

DEFAULT_STATE_FILE = "~/.local/state/homehub-notify/state.json"
_REQUIRED = ("api_base", "token_file", "hub_base_url", "discord_target")


class ConfigError(Exception):
    """Missing or invalid configuration; the message names the key, never a secret value."""


@dataclass(frozen=True)
class NotifyConfig:
    api_base: str
    token_file: Path
    hub_base_url: str
    discord_target: str
    lead_business_days: int = 2
    lead_overrides: dict[int, int] = field(default_factory=dict)
    weekly_open_case_limit: int = 10
    expected_statement_grace_days: int = 7
    min_buffer: Decimal = Decimal("0")
    state_file: Path = Path(DEFAULT_STATE_FILE).expanduser()
    portfolio_base: str | None = None
    cathay_run_time: time = time(6, 40)
    cathay_run_grace_minutes: int = 15


def _int(raw: dict, key: str, default: int) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(key)
    return value


def _hhmm(raw: dict, key: str, default: time) -> time:
    value = raw.get(key)
    if value is None:
        return default
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        raise ConfigError(key)
    try:
        return time(int(value[:2]), int(value[3:]))
    except ValueError:
        raise ConfigError(key) from None


def _optional_str(raw: dict, key: str) -> str | None:
    if key not in raw:
        return None
    value = raw[key]
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(key)
    return value.strip().rstrip("/")


def load_config(path: Path) -> NotifyConfig:
    try:
        with open(path, "rb") as fh:
            raw = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"config file {path}: {type(exc).__name__}") from None
    for key in _REQUIRED:
        if not isinstance(raw.get(key), str) or not raw[key].strip():
            raise ConfigError(key)
    overrides_raw = raw.get("lead_overrides", {})
    if not isinstance(overrides_raw, dict):
        raise ConfigError("lead_overrides")
    try:
        overrides = {int(k): int(v) for k, v in overrides_raw.items()}
    except (TypeError, ValueError):
        raise ConfigError("lead_overrides") from None
    try:
        min_buffer = Decimal(str(raw.get("min_buffer", "0")))
    except InvalidOperation:
        raise ConfigError("min_buffer") from None
    return NotifyConfig(
        api_base=raw["api_base"].strip().rstrip("/"),
        token_file=Path(raw["token_file"]).expanduser(),
        hub_base_url=raw["hub_base_url"].strip().rstrip("/"),
        discord_target=raw["discord_target"].strip(),
        lead_business_days=_int(raw, "lead_business_days", 2),
        lead_overrides=overrides,
        weekly_open_case_limit=_int(raw, "weekly_open_case_limit", 10),
        expected_statement_grace_days=_int(raw, "expected_statement_grace_days", 7),
        min_buffer=min_buffer,
        state_file=Path(raw.get("state_file", DEFAULT_STATE_FILE)).expanduser(),
        portfolio_base=_optional_str(raw, "portfolio_base"),
        cathay_run_time=_hhmm(raw, "cathay_run_time", time(6, 40)),
        cathay_run_grace_minutes=_int(raw, "cathay_run_grace_minutes", 15),
    )


def read_token(path: Path) -> str:
    try:
        token = Path(path).read_text().strip()
    except (OSError, UnicodeDecodeError):
        raise ConfigError("token") from None
    if not token:
        raise ConfigError("token")
    return token
