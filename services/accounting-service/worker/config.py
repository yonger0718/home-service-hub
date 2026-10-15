"""Worker configuration from STATEMENT_* environment variables (defaults for the interim opc principal)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

TRUE = {"1", "true", "yes", "on"}


def _str(env: Mapping[str, str], key: str, default: str) -> str:
    """Value with surrounding whitespace removed; an empty or blank value means the default."""
    return env.get(key, "").strip() or default


def _path(env: Mapping[str, str], key: str, default: str) -> Path:
    return Path(_str(env, key, default)).expanduser()


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
    parser_allow_unsandboxed: bool
    parser_timeout_s: int
    parser_attempts: int
    verify_dir: Path
    verify_db_url: str | None = field(repr=False)  # may embed a password
    verify_parser_config_dir: Path
    account_map_file: Path | None
    ocr_enabled: bool = True          # R5: OCR child for files without a text layer (wired in R5c)
    ocr_threads: int = 2
    ocr_timeout_s: int = 120
    ocr_rlimit_mb: int = 2048
    ocr_child: str | None = None      # tests only: a command (shell words) replacing `python -B -m worker.ocr_child …`

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
        api_url=_str(env, "STATEMENT_API_URL", "http://127.0.0.1:8000").rstrip("/"),
        api_token_file=_path(env, "STATEMENT_API_TOKEN_FILE", "~/.config/homehub-statement-token"),
        state_dir=_path(env, "STATEMENT_STATE_DIR", "~/.local/state/home-hub-statements"),
        password_file=_path(env, "STATEMENT_PASSWORD_FILE", "~/.config/homehub-statement-passwords.env"),
        rclone_remote=_str(env, "STATEMENT_RCLONE_REMOTE", "gdrive:"),
        drive_root=_str(env, "STATEMENT_DRIVE_ROOT", "財務對帳單").strip("/"),
        minio_endpoint=_opt(env, "STATEMENT_MINIO_ENDPOINT"),
        minio_bucket=_str(env, "STATEMENT_MINIO_BUCKET", "homehub-statements"),
        minio_key_file=Path(key_file).expanduser() if key_file else None,
        parser_cli=_path(env, "STATEMENT_PARSER_CLI", "/home/opc/.local/bin/claude"),
        parser_cli_version=_str(env, "STATEMENT_PARSER_CLI_VERSION", "2.1.296"),
        parser_model=_str(env, "STATEMENT_PARSER_MODEL", "claude-sonnet-5-5"),
        parser_config_dir=_path(env, "STATEMENT_PARSER_CONFIG_DIR", "~/.local/state/home-hub-parser/claude"),
        parser_sandbox=_str(env, "STATEMENT_PARSER_SANDBOX", "true").lower() in TRUE,
        parser_allow_unsandboxed=_str(env, "STATEMENT_PARSER_ALLOW_UNSANDBOXED", "false").lower() in TRUE,
        parser_timeout_s=int(_str(env, "STATEMENT_PARSER_TIMEOUT", "120")),
        parser_attempts=int(_str(env, "STATEMENT_PARSER_ATTEMPTS", "3")),
        verify_dir=_path(env, "STATEMENT_VERIFY_DIR", "~/.local/state/home-hub-verify"),
        verify_db_url=_opt(env, "STATEMENT_VERIFY_DB_URL"),
        verify_parser_config_dir=_path(env, "STATEMENT_VERIFY_PARSER_CONFIG_DIR", "~/.local/state/home-hub-parser/claude-verify"),
        account_map_file=Path(map_file).expanduser() if map_file else None,
        ocr_enabled=_str(env, "STATEMENT_OCR", "true").lower() in TRUE,
        ocr_threads=int(_str(env, "STATEMENT_OCR_THREADS", "2")),
        ocr_timeout_s=int(_str(env, "STATEMENT_OCR_TIMEOUT", "120")),
        ocr_rlimit_mb=int(_str(env, "STATEMENT_OCR_RLIMIT_MB", "2048")),
        ocr_child=_opt(env, "STATEMENT_OCR_CHILD"),
    )
