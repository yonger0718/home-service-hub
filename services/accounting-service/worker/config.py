"""Worker configuration from STATEMENT_* environment variables (defaults for the interim opc principal)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

TRUE = {"1", "true", "yes", "on"}


def _path(env: Mapping[str, str], key: str, default: str) -> Path:
    return Path(env.get(key, default)).expanduser()


def _opt(env: Mapping[str, str], key: str) -> str | None:
    value = env.get(key, "").strip()
    return value or None


@dataclass(frozen=True)
class WorkerConfig:
    api_url: str
    api_token_file: Path
    state_dir: Path
    password_file: Path
    rclone_remote: str
    drive_root: str
    minio_endpoint: str | None
    minio_bucket: str
    minio_key_file: Path | None
    parser_cli: Path
    parser_cli_version: str
    parser_model: str
    parser_config_dir: Path
    parser_sandbox: bool
    parser_timeout_s: int
    parser_attempts: int
    verify_dir: Path
    verify_db_url: str | None
    verify_parser_config_dir: Path
    account_map_file: Path | None

    @property
    def mail_root(self) -> str:
        return f"{self.drive_root}/銀行"

    @property
    def manual_root(self) -> str:
        return f"{self.drive_root}/手動下載"

    @property
    def inbox_dir(self) -> Path:
        return self.state_dir / "inbox" / "by-sha"

    @property
    def staging_dir(self) -> Path:
        return self.state_dir / "staging"

    @property
    def lock_path(self) -> Path:
        return self.state_dir / ".lock"


def load(env: Mapping[str, str] = os.environ) -> WorkerConfig:
    key_file = _opt(env, "STATEMENT_MINIO_KEY_FILE")
    map_file = _opt(env, "STATEMENT_ACCOUNT_MAP_FILE")
    return WorkerConfig(
        api_url=env.get("STATEMENT_API_URL", "http://127.0.0.1:8000").rstrip("/"),
        api_token_file=_path(env, "STATEMENT_API_TOKEN_FILE", "~/.config/homehub-statement-token"),
        state_dir=_path(env, "STATEMENT_STATE_DIR", "~/.local/state/home-hub-statements"),
        password_file=_path(env, "STATEMENT_PASSWORD_FILE", "~/.config/homehub-statement-passwords.env"),
        rclone_remote=env.get("STATEMENT_RCLONE_REMOTE", "gdrive:"),
        drive_root=env.get("STATEMENT_DRIVE_ROOT", "財務對帳單").strip("/"),
        minio_endpoint=_opt(env, "STATEMENT_MINIO_ENDPOINT"),
        minio_bucket=env.get("STATEMENT_MINIO_BUCKET", "homehub-statements"),
        minio_key_file=Path(key_file).expanduser() if key_file else None,
        parser_cli=_path(env, "STATEMENT_PARSER_CLI", "/home/opc/.local/bin/claude"),
        parser_cli_version=env.get("STATEMENT_PARSER_CLI_VERSION", "2.1.296"),
        parser_model=env.get("STATEMENT_PARSER_MODEL", "claude-sonnet-5-5"),
        parser_config_dir=_path(env, "STATEMENT_PARSER_CONFIG_DIR", "~/.local/state/home-hub-parser/claude"),
        parser_sandbox=env.get("STATEMENT_PARSER_SANDBOX", "true").strip().lower() in TRUE,
        parser_timeout_s=int(env.get("STATEMENT_PARSER_TIMEOUT", "120")),
        parser_attempts=int(env.get("STATEMENT_PARSER_ATTEMPTS", "3")),
        verify_dir=_path(env, "STATEMENT_VERIFY_DIR", "~/.local/state/home-hub-verify"),
        verify_db_url=_opt(env, "STATEMENT_VERIFY_DB_URL"),
        verify_parser_config_dir=_path(env, "STATEMENT_VERIFY_PARSER_CONFIG_DIR", "~/.local/state/home-hub-parser/claude-verify"),
        account_map_file=Path(map_file).expanduser() if map_file else None,
    )
