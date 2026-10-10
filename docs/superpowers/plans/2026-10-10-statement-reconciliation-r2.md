# Statement reconciliation R2 — ingest worker — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The worker that turns the owner's Google Drive statement PDFs into statement revisions through the R1a/R1b API: Drive acquisition, unlock, text extraction and masking, the sandboxed model-as-a-function parser, run leases and submission, retry rules, the parser gate, and the two-step verify mode that reports match quality against the live ledger without writing anything.

**Architecture:** A separate Python package `services/accounting-service/worker/` (same venv, its own `requirements-worker.txt`) with one module per stage and a pipeline that drives a per-file state machine. The run path talks to the API only over HTTP (`httpx`) and never imports `app.database`; the verify path imports the pure `app.services.statements.*` modules and `reconciliation_service.load_candidates` under a read-only transaction. External programs (`rclone`, `bwrap`, `claude`) are called through one injectable `Runner` so every test runs with fakes and no network. Ops files (systemd user units, gate script) ship under `services/accounting-service/deploy/statements/`.

**Tech Stack:** Python 3.13, pydantic 2.14, httpx 0.28 (Starlette's `TestClient` is an `httpx.Client`, so tests inject it as the API client), `pypdf` 6 + `cryptography` 50 (unlock), `pdfplumber` 0.11 (text + tables), `minio` (optional object store), rclone 1.73 (`gdrive:` remote), bubblewrap, Claude Code CLI **2.1.296** (native aarch64 binary at `/home/opc/.local/bin/claude`).

**Spec:** `docs/superpowers/specs/2026-10-08-statement-reconciliation-design.md` v4 (§3, §4.1, §4.2, §5 incl. the "as v2"/"as v3" text of commits `eef3b31` and `6aabdc9`, §8, §9.1, §11, §13, §16 C7/C9/S1 acceptance items, §17 notes). Base: `main` a4a9207 (R1b merged). Revision 2 after the Multica plan review (AGENT-95, dev-astra): 11 Must + 5 Should folded in; see the rulings 9–13.

## Global Constraints

- **Secrets never leave the worker.** Password values, the ID number, birth date, holder names, decrypted bytes and unmasked text are never logged, never written to disk outside the private state dir, never sent to the API, MinIO or the parser. Errors about passwords say only `password`. Tests use synthetic identities (`A123456789`, `19900101`, `王小明`), never the owner's.
- **Masking before anything leaves the worker** (§5.4): card/account numbers keep the last 4 digits, ID numbers `[A-Z][12]\d{8}`, separator-tolerant digit runs ≥ 6, birth date (`YYYYMMDD`, `YYYY/MM/DD`, `YYYY-MM-DD`, `DDMMYY`), holder names, e-mails, phone numbers — applied to text and to every table cell.
- **Bounds** (§5.4/§5.5): PDF ≤ 20 MB, ≤ 40 pages, extraction ≤ 60 s, page 1 text ≥ 200 chars else `no_text_layer`, masked text ≤ 400 KB else `too_large`; parser stdin ≤ 400 KB, stdout and stderr each ≤ 2 MB while streaming, 120 s per attempt, 3 attempts, process group killed and reaped on expiry.
- **Parser sandbox** (§5.5): `bwrap --unshare-all --share-net --die-with-parent --new-session --cap-drop ALL`, read-only `/usr /lib /lib64 /etc/pki /etc/ssl /etc/resolv.conf /etc/hosts`, the CLI binary at `/opt/claude/claude`, config dir read-only at `/cfg` with only `/cfg/.credentials.json` writable, tmpfs `/tmp` and `/work`, `--proc /proc --dev /dev`, env exactly `HOME=/tmp CLAUDE_CONFIG_DIR=/cfg PATH=/opt/claude:/usr/bin`. CLI flags exactly: `-p --safe-mode --tools "" --disallowedTools "mcp__*" --strict-mcp-config --mcp-config '{"mcpServers":{}}' --permission-prompts none --no-session-persistence --max-turns 1 --output-format stream-json --verbose --json-schema <schema> --model <pinned> <instruction>`.
- **CLI pin 2.1.296** (ruling below); any other `claude --version` → parsing disabled (`failed/sandbox`), the run continues for acquisition only.
- **Run lease** (§4.1): every submission carries `run_id` + `lease_token`; the worker renews every 5 minutes; a 409 on renew or submit aborts the run (`failed`, reason `lease`).
- **Idempotency**: files by sha256, sources by `(drive_file_id, drive_md5)`, object key `by-sha/<sha256>.pdf`; a crash at any boundary is safe to re-run.
- **Singleton**: `flock` on `<state_dir>/.lock`; a second worker prints `already running` and exits 0 (timer units must not fail).
- **Private by construction**: the CLI sets `umask 0o077` first thing; every directory the worker creates is 0700 and every file 0600 (state, staging, inbox, verify dirs, reports, caches). A listed PDF larger than 20 MB is skipped before download (counted `too_large`, never registered).
- **Error text discipline**: run summaries, reports and API payloads carry bounded codes and counts only (`errors: ["listing", "download", "store", …]`), never exception text, paths, names or identities.
- **No owner financial data in tests**; synthetic PDFs built by the test helper. Commits carry `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`; the PR body ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- Services never commit; routers do (the one API addition in Task 6 follows this).

## Rulings (recorded in spec §17 by Task 9)

1. **CLI pin bumped 2.1.295 → 2.1.296** (installed version; .295 is gone). The gate must pass before the first real parse.
2. **Structured output is a tool call.** With `--json-schema`, the CLI exposes exactly one tool, `StructuredOutput`, and the model answers by calling it: the stream is `system/init` (tools `["StructuredOutput"]`) → one `assistant` message whose content is exactly one `tool_use` named `StructuredOutput` → one `user` message (its `tool_result`) → `result` (`subtype == "success"`, `num_turns == 2`, `structured_output` set). The §5.5 gate therefore reads: tools list equals `["StructuredOutput"]`, exactly one `tool_use` and it is `StructuredOutput`, no other tool name, no `hook`/`subagent`/permission event, `num_turns == 2`. Anything else → `failed/sandbox` and parsing disabled.
3. **Interim principal.** The dedicated users of §3 do not exist yet; R2 runs as `opc` with every path configurable (`STATEMENT_*` env, defaults below). The deploy notes carry the §3 checklist for the switch; the feature flag stays off in production until then, so the first real use is `export-masked` + `verify` (under ruling 11's read-only role).
4. **MinIO is optional in R2**: with `STATEMENT_MINIO_ENDPOINT` unset the put is skipped and `object_key` is still `by-sha/<sha>.pdf` (the local inbox holds the bytes). Setting the endpoint later re-uploads missing objects on the next run (the file row stays `transient` until the put succeeds).
5. **Account map reaches the worker through a new ingest-scope route** `GET /statements/account-map` (`{"account_map": …, "mapping_version": sha256-prefix}`), because the worker token holds `ingest` only and cannot read settings. `verify` reads the map from the database (read-only) or from `STATEMENT_ACCOUNT_MAP_FILE` (same key format) while the feature flag is off.
6. **Verify is in-process, read-only.** The verify runner opens one `REPEATABLE READ READ ONLY` transaction on the read-only role (ruling 11), builds candidates with `reconciliation_service.load_candidates` on a stand-in statement whose id is the existing statement's id when `(account, currency, period_end)` already exists (so its own claims are excluded like a re-reconcile) and `0` otherwise, skips matching when the guardrails fail (as `reconcile` does), and calls `matching.match`; it writes only its report. Parse results are cached by sha256 + parser version so re-runs do not re-parse.
7. **Regex parsers**: none in R2 (as spec v1); no registry either (ruling 13) — adding one later touches `pipeline.process_file` in one place.
8. **Pinned model** default `claude-sonnet-5-5` (`STATEMENT_PARSER_MODEL`); the parser version string is `claude-cli-<cli version>-<model>-<instruction sha256[:8]>-<schema sha256[:8]>`.
9. **The gate is mandatory and latched** (AGENT-95 Must 2). Every `run`, `poll`, `backfill` and `verify` calls `parser.Gate.ensure()` before the first parse: it passes when `<state_dir>/gate.json` holds a successful gate whose key (`cli_version`, `model`, `config dir mtime`, sha256 of the full argv template, sandbox flag) matches and is younger than 24 h; otherwise it runs the canary now. A violation anywhere (gate or real input) writes `<state_dir>/parser-disabled.json` (reason, time, key); while that latch exists no process parses, runs finish `failed` with `errors: ["parser_disabled"]`, and only a successful operator `python -m worker gate` removes it. `deploy/statements/gate.sh` adds the host-side evidence (mounts, processes, `/etc` and `$HOME` unreadable) and is the §16 C7 artefact.
10. **`SourceIndex` is a download cache, never the authority.** Every complete listing re-registers every listed source through `POST /statements/sources` (idempotent upsert; path history, `removed_at` reset) and re-checks the object store for every file that lacks a stored object (`store_pending` tracked locally), regardless of the index. The index only decides whether bytes must be fetched again.
11. **Verify reads through a dedicated read-only database role** (AGENT-95 Must 9/11, owner decision pending at plan time): `STATEMENT_VERIFY_DB_URL` is **required** for `verify` (no fallback to the application URL) and must name a role with `SELECT` only and `default_transaction_read_only = on` (ops step in Task 9's README: `CREATE ROLE accounting_ro LOGIN PASSWORD … ; GRANT CONNECT ON DATABASE accounting_db; GRANT USAGE ON SCHEMA public; GRANT SELECT ON ALL TABLES IN SCHEMA public; ALTER ROLE accounting_ro SET default_transaction_read_only = on`). The verify transaction is `REPEATABLE READ READ ONLY`. Verify's parser child binds the login directory read-only with **no** writable credentials file (`STATEMENT_VERIFY_PARSER_CONFIG_DIR`, a copy refreshed by the operator's `gate` run); an expired token fails verify with `auth`, it never refreshes. The Linux principal stays `opc` in the interim (ruling 3), recorded as an accepted deviation from §3, not as compliance; the flag stays off and no real-data `run` happens until the §3 users exist.
12. **Settings are read SELECT-only where a read-only transaction may hold them**: new `settings_service.read_reconciliation_settings(db)` (no insert, defaults applied) and `read_reconciliation_rules(db)` are used by `GET /settings/reconciliation`, `GET /statements/account-map` and verify; the inserting `_reconciliation_row` stays for writers.
13. **Narrowed contracts** (AGENT-95 Should): no regex-parser registry in R2 (ruling 7 is "none", the pipeline has a single parser); run provenance (`parser_version`, `credential_version`, `mapping_version`, `cli_version`, `model`) travels in the run's finish `summary.versions` (the `ingest_run` columns stay for R1c's API change); `backfill` prints no pre-execution live-period count (the worker cannot read accounts) — the CLI refuses without `--acknowledge-live-periods` and the summary reports `live_revisions` afterwards; the daily sweep (`POST /reconciliation/sweep`) runs at the end of every full run under its lease.

## Review Focus

1. A Drive file replaced between listing and download (md5 mismatch) must be re-listed once and then skipped as `transient`, never published under the wrong identity — Task 3.
2. A wrong password must mark the file `failed/password` with the winning index unrecorded, and a change of the password file (new `credential_version`) must retry it — Tasks 2, 4, 7.
3. The parser emitting a second tool call or a non-`StructuredOutput` tool must fail the run and disable parsing; a timeout must kill the whole process group — Task 5.
4. A lease lost while a parse is in flight must block the submission that follows it, and a crash after claim must resume the same run on restart — Tasks 6, 7.
5. `verify` with policies enabled and a live account must leave every table's row count unchanged — Task 8.

---

### Task 1: Branch and baseline

- [ ] **Step 1:** worktree `/home/opc/workspace/home-hub-reconcile` on `feat/reconciliation-r2` from `origin/main` (a4a9207); `services/accounting-service/.venv` and the root `.env` link exist.
- [ ] **Step 2:** `cd services/accounting-service && .venv/bin/pytest -q` → 1378 passed, 6 warnings.
- [ ] **Step 3:** create `services/accounting-service/requirements-worker.txt`:

```
-r requirements.txt
pypdf==6.19.0
cryptography==50.0.2
pdfplumber==0.11.10
minio==7.2.15
```

- [ ] **Step 4:** `.venv/bin/pip install -r requirements-worker.txt` (network needed once). Add `worker/` to `pytest.ini`'s `testpaths` only if one is set; otherwise tests under `tests/worker/` are collected by default.
- [ ] **Step 5:** commit `chore(accounting): worker requirements`.

---

### Task 2: Worker config and the password resolver

**Files:**
- Create: `services/accounting-service/worker/__init__.py` (empty), `worker/config.py`, `worker/passwords.py`
- Test: `tests/worker/__init__.py` (empty), `tests/worker/test_config.py`, `tests/worker/test_passwords.py`

**Interfaces:**
- Produces `config.WorkerConfig` (frozen dataclass) built by `config.load(env: Mapping[str, str] = os.environ) -> WorkerConfig` with fields and defaults:

| field | env | default |
|---|---|---|
| `api_url` | `STATEMENT_API_URL` | `http://127.0.0.1:8000` |
| `api_token_file` | `STATEMENT_API_TOKEN_FILE` | `~/.config/homehub-statement-token` |
| `state_dir` | `STATEMENT_STATE_DIR` | `~/.local/state/home-hub-statements` |
| `password_file` | `STATEMENT_PASSWORD_FILE` | `~/.config/homehub-statement-passwords.env` |
| `rclone_remote` | `STATEMENT_RCLONE_REMOTE` | `gdrive:` |
| `drive_root` | `STATEMENT_DRIVE_ROOT` | `財務對帳單` |
| `minio_endpoint` | `STATEMENT_MINIO_ENDPOINT` | `None` |
| `minio_bucket` | `STATEMENT_MINIO_BUCKET` | `homehub-statements` |
| `minio_key_file` | `STATEMENT_MINIO_KEY_FILE` | `None` (file: two lines, access key then secret key) |
| `parser_cli` | `STATEMENT_PARSER_CLI` | `/home/opc/.local/bin/claude` |
| `parser_cli_version` | `STATEMENT_PARSER_CLI_VERSION` | `2.1.296` |
| `parser_model` | `STATEMENT_PARSER_MODEL` | `claude-sonnet-5-5` |
| `parser_config_dir` | `STATEMENT_PARSER_CONFIG_DIR` | `~/.local/state/home-hub-parser/claude` |
| `parser_sandbox` | `STATEMENT_PARSER_SANDBOX` | `true` (`false` only in tests) |
| `parser_timeout_s` | `STATEMENT_PARSER_TIMEOUT` | `120` |
| `parser_attempts` | `STATEMENT_PARSER_ATTEMPTS` | `3` |
| `verify_dir` | `STATEMENT_VERIFY_DIR` | `~/.local/state/home-hub-verify` |
| `verify_db_url` | `STATEMENT_VERIFY_DB_URL` | `None` (**required** by `verify`; must be the read-only role, ruling 11) |
| `verify_parser_config_dir` | `STATEMENT_VERIFY_PARSER_CONFIG_DIR` | `~/.local/state/home-hub-parser/claude-verify` (bound read-only, no credentials write) |
| `account_map_file` | `STATEMENT_ACCOUNT_MAP_FILE` | `None` |

  `~` expands with `Path.expanduser()`. Derived properties: `mail_root = f"{drive_root}/銀行"`, `manual_root = f"{drive_root}/手動下載"`, `inbox_dir = state_dir / "inbox" / "by-sha"`, `staging_dir = state_dir / "staging"`, `lock_path = state_dir / ".lock"`. Booleans parse `1/true/yes` case-insensitively.
- Produces `passwords.Candidates = dict[str, list[str]]`, `passwords.load(path: Path) -> tuple[Candidates, str]` (candidates per folder key, and `credential_version` = sha256 hex of the file bytes), `passwords.identity(path: Path) -> passwords.Identity(id_number, birth_date, holder_names)` (the **same** parser as `load`, so leading whitespace or inline comments cannot make an identity line vanish from masking while it still feeds candidates — AGENT-95 Must 1), `passwords.password_key(root: str, folder: str) -> str` (`mail` → `folder`, e.g. `信用卡/國泰世華`; `manual` → `手動下載/` + folder).

- [ ] **Step 1: Write the failing tests** `tests/worker/test_config.py`:

```python
from pathlib import Path

from worker import config


def test_defaults_expand_home():
    cfg = config.load({})
    assert cfg.api_url == "http://127.0.0.1:8000"
    assert cfg.state_dir == Path.home() / ".local/state/home-hub-statements"
    assert cfg.inbox_dir == cfg.state_dir / "inbox" / "by-sha"
    assert cfg.mail_root == "財務對帳單/銀行" and cfg.manual_root == "財務對帳單/手動下載"
    assert cfg.parser_sandbox is True and cfg.minio_endpoint is None


def test_env_overrides_and_booleans(tmp_path):
    cfg = config.load({"STATEMENT_STATE_DIR": str(tmp_path), "STATEMENT_PARSER_SANDBOX": "no",
                       "STATEMENT_PARSER_TIMEOUT": "7", "STATEMENT_MINIO_ENDPOINT": "127.0.0.1:9000"})
    assert cfg.state_dir == tmp_path and cfg.lock_path == tmp_path / ".lock"
    assert cfg.parser_sandbox is False and cfg.parser_timeout_s == 7
    assert cfg.minio_endpoint == "127.0.0.1:9000"
```

`tests/worker/test_passwords.py` (synthetic identity only):

```python
import hashlib

import pytest

from worker import passwords

FILE = """# comment
STATEMENT_ID_NUMBER=A123456789
STATEMENT_BIRTH_DATE=19900101
STATEMENT_HOLDER_NAMES=王小明,WANG XIAO MING
信用卡/國泰世華=$ID
信用卡/台新=$BIRTH8
信用卡/台新.alt=$BIRTH{DDMMYY}+$ID[:6]
信用卡/滙豐 Live+=$ID[-4:]  # last four
銀行帳戶/星展=literal-pw
手動下載/國泰世華=@信用卡/國泰世華
"""


@pytest.fixture
def pwfile(tmp_path):
    p = tmp_path / "pw.env"
    p.write_text(FILE, encoding="utf-8")
    return p


def test_candidates_expand_tokens_in_order(pwfile):
    cands, version = passwords.load(pwfile)
    assert cands["信用卡/國泰世華"] == ["A123456789"]
    assert cands["信用卡/台新"] == ["19900101", "010190A12345"]
    assert cands["信用卡/滙豐 Live+"] == ["6789"]
    assert cands["銀行帳戶/星展"] == ["literal-pw"]
    assert cands["手動下載/國泰世華"] == ["A123456789"]  # alias
    assert version == hashlib.sha256(pwfile.read_bytes()).hexdigest()


def test_password_key_by_root():
    assert passwords.password_key("mail", "信用卡/國泰世華") == "信用卡/國泰世華"
    assert passwords.password_key("manual", "國泰世華") == "手動下載/國泰世華"


def test_unknown_folder_has_no_candidates(pwfile):
    cands, _ = passwords.load(pwfile)
    assert cands.get("信用卡/不存在", []) == []


def test_identity_shares_the_parser(tmp_path):
    p = tmp_path / "pw.env"
    p.write_text("  STATEMENT_ID_NUMBER=A123456789  # id\n\tSTATEMENT_BIRTH_DATE=19900101\n"
                 "STATEMENT_HOLDER_NAMES=王小明,WANG\\, XIAO\n信用卡/x=$ID\n", encoding="utf-8")
    ident = passwords.identity(p)
    assert ident.id_number == "A123456789" and ident.birth_date == "19900101"
    assert ident.holder_names == ["王小明", "WANG, XIAO"]
    assert passwords.load(p)[0]["信用卡/x"] == ["A123456789"]


def test_repr_never_shows_values(pwfile):
    cands, _ = passwords.load(pwfile)
    assert "A123456789" not in passwords.describe(cands)
    assert "信用卡/國泰世華=1" in passwords.describe(cands)
```

- [ ] **Step 2:** `.venv/bin/pytest tests/worker -q` → fails with `ModuleNotFoundError: worker`.
- [ ] **Step 3: Implement** `worker/config.py`:

```python
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
```

`worker/passwords.py` (the scratchpad prototype, hardened; the grammar is §5.3):

```python
"""Password candidates per Drive folder from the owner's password file. Values never appear in logs or errors."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

Candidates = dict[str, list[str]]
IDENTITY_KEYS = ("STATEMENT_ID_NUMBER", "STATEMENT_BIRTH_DATE", "STATEMENT_HOLDER_NAMES")
_TOKEN = re.compile(r"\$(ID|BIRTH8|BIRTH6|BIRTH4)(\[(-?\d*)(:)?(-?\d*)\])?$")
_BIRTH_FMT = re.compile(r"\$BIRTH\{([A-Z]+)\}$")


def _strip_comment(value: str) -> str:
    # an inline comment starts with two spaces and '#'; a bare '#' may be part of a password
    return value.split("  #", 1)[0].strip()


def _parse(text: str) -> tuple[dict[str, str], dict[str, list[str]]]:
    identity: dict[str, str] = {}
    folders: dict[str, list[str]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), _strip_comment(value)
        if key in IDENTITY_KEYS:
            identity[key] = value
        else:
            folders.setdefault(re.sub(r"\.alt\d*$", "", key), []).append(value)
    return identity, folders


def _birth(fmt: str, ymd: str) -> str:
    return fmt.replace("YYYY", ymd[0:4]).replace("YY", ymd[2:4]).replace("MM", ymd[4:6]).replace("DD", ymd[6:8])


def _expand(value: str, identity: dict[str, str]) -> str | None:
    if not value or value.startswith("@"):
        return None
    idn, bd = identity.get("STATEMENT_ID_NUMBER", ""), identity.get("STATEMENT_BIRTH_DATE", "")
    out: list[str] = []
    for part in value.split("+"):
        fmt = _BIRTH_FMT.fullmatch(part)
        if fmt:
            out.append(_birth(fmt.group(1), bd))
            continue
        token = _TOKEN.fullmatch(part)
        if token:
            base = {"ID": idn, "BIRTH8": bd, "BIRTH6": bd[2:], "BIRTH4": bd[4:]}[token.group(1)]
            if token.group(2):
                a, colon, b = token.group(3), token.group(4), token.group(5)
                if colon:
                    base = base[int(a) if a else None:int(b) if b else None]
                else:
                    n = int(a)
                    base = base[n:] if n < 0 else base[n]
            out.append(base)
            continue
        out.append(part)
    joined = "".join(out)
    return joined or None


def load(path: Path) -> tuple[Candidates, str]:
    """{folder key: [candidate passwords in order]} and credential_version (sha256 of the file bytes)."""
    data = path.read_bytes()
    identity, folders = _parse(data.decode("utf-8"))
    out: Candidates = {}
    for folder, values in folders.items():
        out[folder] = [c for c in (_expand(v, identity) for v in values) if c]
    for folder, values in folders.items():
        for v in values:
            if v.startswith("@"):
                out[folder] = out.get(folder, []) + out.get(v[1:].strip(), [])
    return out, hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Identity:
    id_number: str | None
    birth_date: str | None  # YYYYMMDD
    holder_names: list[str]


def identity(path: Path) -> Identity:
    """The three STATEMENT_* fields through the same parser as `load` (whitespace/comment handling identical)."""
    ident, _ = _parse(path.read_text(encoding="utf-8"))
    raw_names = ident.get("STATEMENT_HOLDER_NAMES", "").replace("\\,", "\x00")
    names = [n.replace("\x00", ",").strip() for n in raw_names.split(",") if n.strip()]
    return Identity(ident.get("STATEMENT_ID_NUMBER") or None, ident.get("STATEMENT_BIRTH_DATE") or None, names)


def password_key(root: str, folder: str) -> str:
    """Key in the password file for a source: mail files use their folder path under 銀行/, manual ones 手動下載/<folder>."""
    return folder if root == "mail" else f"手動下載/{folder}"


def describe(candidates: Candidates) -> str:
    """Loggable summary: folder=count, never a value."""
    return ", ".join(f"{folder}={len(values)}" for folder, values in sorted(candidates.items()))
```

- [ ] **Step 4:** `.venv/bin/pytest tests/worker -q` → 8 passed.
- [ ] **Step 5:** commit `feat(worker): config and password resolver`.

---

### Task 3: Drive acquisition and the inbox

**Files:**
- Create: `worker/runner.py`, `worker/drive.py`, `worker/inbox.py`
- Test: `tests/worker/test_drive.py`, `tests/worker/test_inbox.py`

**Interfaces:**
- `runner.Runner` protocol: `run(args: list[str], *, stdin: bytes | None = None, timeout: float | None = None, env: dict | None = None) -> runner.Result` with `Result(returncode: int, stdout: bytes, stderr: bytes)`; `runner.SubprocessRunner` implements it with `subprocess.run(..., capture_output=True, start_new_session=True)`; `runner.FakeRunner(handlers: dict[str, Callable[[list[str], bytes | None], Result]])` dispatches on `args[0]` (tests).
- `drive.Listed` (frozen dataclass): `drive_file_id, path (relative to the root listed), name, size, md5, mod_time, root ('mail'|'manual')`; `drive.folder_of(listed) -> str` = parent folders joined by `/` (e.g. `信用卡/國泰世華`, or `國泰世華` under manual).
- `drive.Drive(cfg, runner)`: `list_pdfs() -> list[Listed]` (both roots, `rclone lsjson --hash --recursive --files-only <remote><root>`, case-insensitive `.pdf`, raises `drive.ListingError` on any non-zero exit or JSON error — **nothing is marked removed after a ListingError**); `download(item: Listed, dest: Path) -> None` (`rclone copyto --drive-root-folder-id <id> <remote> <dest>` is NOT how Drive addresses a single file; use `rclone backend copyid <remote> <id> <dest>` which downloads by file id), raises `drive.DownloadError`.
- `inbox.Inbox(cfg)`: `publish(staged: Path, *, expected_md5: str, expected_size: int) -> inbox.Published(sha256: str, object_key: str, path: Path)`; verifies size and md5 of the staged bytes (mismatch → `inbox.VerifyError`), computes sha256, publishes atomically to `inbox_dir/<sha>.pdf` via `os.replace` from a temp name in the same directory; if the target exists with different bytes → `inbox.CollisionError` (hard failure); identical existing bytes → no-op; `object_key = f"by-sha/{sha}.pdf"`.
- `inbox.SourceIndex(path: Path)`: a **download cache only** (ruling 10): JSON map `"<drive_file_id>:<md5>" → sha256` at `state_dir/sources.json` (mode 600, written atomically); `get(drive_file_id, md5) -> str | None`, `put(drive_file_id, md5, sha256) -> None`. It decides only whether bytes are fetched again; registration, path history and store checks happen every run regardless. Wiping the state dir costs a re-download, nothing else.
- `inbox.private_dir(path) -> Path` (0700 mkdir), `inbox.MAX_PDF_BYTES = 20_000_000`; the staging file is created 0600 (`rclone copyto` writes with the process umask, which the CLI sets to `0o077`).
- `inbox.ObjectStore` protocol with `put(key: str, path: Path) -> None` and `exists(key) -> bool`; `inbox.MinioStore(endpoint, bucket, key_file)` using the `minio` client (`fput_object`, `stat_object`); `inbox.NullStore` when the endpoint is unset (`put` no-op, `exists` True).

- [ ] **Step 1: Write the failing tests** `tests/worker/test_drive.py`:

```python
import json

import pytest

from worker import config, drive
from worker.runner import FakeRunner, Result

LISTING = [
    {"Path": "信用卡/國泰世華/2026-09_國泰世華_信用卡.pdf", "Name": "2026-09_國泰世華_信用卡.pdf", "Size": 10,
     "ModTime": "2026-10-01T00:00:00Z", "Hashes": {"md5": "ab" * 16}, "ID": "id-1", "MimeType": "application/pdf"},
    {"Path": "信用卡/渣打銀行/2026-09.PDF", "Name": "2026-09.PDF", "Size": 11, "ModTime": "2026-10-01T00:00:00Z",
     "Hashes": {"md5": "cd" * 16}, "ID": "id-2", "MimeType": "application/pdf"},
    {"Path": "信用卡/星展/notes.txt", "Name": "notes.txt", "Size": 1, "ModTime": "2026-10-01T00:00:00Z",
     "Hashes": {"md5": "ef" * 16}, "ID": "id-3", "MimeType": "text/plain"},
]
MANUAL = [{"Path": "國泰世華/2510.pdf", "Name": "2510.pdf", "Size": 12, "ModTime": "2026-10-09T00:00:00Z",
           "Hashes": {"md5": "01" * 16}, "ID": "id-4", "MimeType": "application/pdf"}]


def _rclone(args, stdin):
    if args[1] == "lsjson":
        body = LISTING if args[-1].endswith("/銀行") else MANUAL
        return Result(0, json.dumps(body).encode(), b"")
    raise AssertionError(args)


def test_list_pdfs_both_roots_case_insensitive(tmp_path):
    cfg = config.load({"STATEMENT_STATE_DIR": str(tmp_path)})
    items = drive.Drive(cfg, FakeRunner({"rclone": _rclone})).list_pdfs()
    assert [(i.root, i.path, i.drive_file_id) for i in items] == [
        ("mail", "信用卡/國泰世華/2026-09_國泰世華_信用卡.pdf", "id-1"), ("mail", "信用卡/渣打銀行/2026-09.PDF", "id-2"),
        ("manual", "國泰世華/2510.pdf", "id-4")]
    assert drive.folder_of(items[0]) == "信用卡/國泰世華" and drive.folder_of(items[2]) == "國泰世華"
    assert items[0].md5 == "ab" * 16 and items[0].size == 10


def test_listing_error_raises(tmp_path):
    cfg = config.load({"STATEMENT_STATE_DIR": str(tmp_path)})
    bad = FakeRunner({"rclone": lambda a, s: Result(3, b"", b"directory not found")})
    with pytest.raises(drive.ListingError):
        drive.Drive(cfg, bad).list_pdfs()


def test_download_by_id(tmp_path):
    cfg = config.load({"STATEMENT_STATE_DIR": str(tmp_path)})
    seen = []

    def rclone(args, stdin):
        seen.append(args)
        (tmp_path / "out.pdf").write_bytes(b"%PDF-1.4 fake")
        return Result(0, b"", b"")

    item = drive.Listed("id-1", "信用卡/國泰世華/x.pdf", "x.pdf", 13, "ab" * 16, "2026-10-01T00:00:00Z", "mail")
    drive.Drive(cfg, FakeRunner({"rclone": rclone})).download(item, tmp_path / "out.pdf")
    assert seen[0][1:4] == ["backend", "copyid", "gdrive:"] and seen[0][4] == "id-1"
```

`tests/worker/test_inbox.py`:

```python
import hashlib

import pytest

from worker import config, inbox


@pytest.fixture
def cfg(tmp_path):
    return config.load({"STATEMENT_STATE_DIR": str(tmp_path)})


def _staged(tmp_path, data=b"%PDF-1.4 abc"):
    p = tmp_path / "staging" / "x.tmp"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p, hashlib.md5(data).hexdigest(), len(data)


def test_publish_is_atomic_and_idempotent(cfg, tmp_path):
    staged, md5, size = _staged(tmp_path)
    box = inbox.Inbox(cfg)
    first = box.publish(staged, expected_md5=md5, expected_size=size)
    assert first.path == cfg.inbox_dir / f"{first.sha256}.pdf" and first.path.read_bytes() == b"%PDF-1.4 abc"
    assert first.object_key == f"by-sha/{first.sha256}.pdf"
    staged2, _, _ = _staged(tmp_path)
    assert box.publish(staged2, expected_md5=md5, expected_size=size).sha256 == first.sha256
    assert not list(cfg.staging_dir.iterdir()) or all(p.suffix != ".tmp" for p in cfg.staging_dir.iterdir())


def test_md5_or_size_mismatch_refuses(cfg, tmp_path):
    staged, md5, size = _staged(tmp_path)
    with pytest.raises(inbox.VerifyError):
        inbox.Inbox(cfg).publish(staged, expected_md5="00" * 16, expected_size=size)
    staged, md5, size = _staged(tmp_path)
    with pytest.raises(inbox.VerifyError):
        inbox.Inbox(cfg).publish(staged, expected_md5=md5, expected_size=size + 1)
    assert not cfg.inbox_dir.exists() or not list(cfg.inbox_dir.iterdir())


def test_same_bytes_compares_bytes_not_digests(cfg, tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"x" * 10)
    b.write_bytes(b"y" * 10)
    monkeypatch.setattr(inbox, "_digests", lambda path: ("m", "same-digest", 10))  # a forced digest collision
    assert inbox._same_bytes(a, b) is False


def test_private_modes(cfg, tmp_path):
    staged, md5, size = _staged(tmp_path)
    published = inbox.Inbox(cfg).publish(staged, expected_md5=md5, expected_size=size)
    assert oct(cfg.inbox_dir.stat().st_mode)[-3:] == "700" and oct(published.path.stat().st_mode)[-3:] == "600"


def test_collision_with_different_bytes_is_hard(cfg, tmp_path, monkeypatch):
    staged, md5, size = _staged(tmp_path)
    box = inbox.Inbox(cfg)
    published = box.publish(staged, expected_md5=md5, expected_size=size)
    published.path.write_bytes(b"different")  # simulate a corrupted inbox object under the same name
    staged, md5, size = _staged(tmp_path)
    with pytest.raises(inbox.CollisionError):
        box.publish(staged, expected_md5=md5, expected_size=size)


def test_source_index_round_trip(cfg):
    index = inbox.SourceIndex(cfg.state_dir / "sources.json")
    assert index.get("id-1", "ab" * 16) is None
    index.put("id-1", "ab" * 16, "cd" * 32)
    assert inbox.SourceIndex(cfg.state_dir / "sources.json").get("id-1", "ab" * 16) == "cd" * 32
    assert oct((cfg.state_dir / "sources.json").stat().st_mode)[-3:] == "600"


def test_null_store_when_minio_unset(cfg):
    store = inbox.store_for(cfg)
    assert isinstance(store, inbox.NullStore) and store.exists("by-sha/x.pdf")
```

- [ ] **Step 2:** run → `ModuleNotFoundError`.
- [ ] **Step 3: Implement** `worker/runner.py`:

```python
"""One seam for every external program (rclone, bwrap, claude): tests inject a FakeRunner."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Callable, Protocol


@dataclass(frozen=True)
class Result:
    returncode: int
    stdout: bytes
    stderr: bytes


class Runner(Protocol):
    def run(self, args: list[str], *, stdin: bytes | None = None, timeout: float | None = None,
            env: dict | None = None) -> Result: ...


class SubprocessRunner:
    def run(self, args, *, stdin=None, timeout=None, env=None) -> Result:
        try:
            done = subprocess.run(args, input=stdin, capture_output=True, timeout=timeout, env=env,
                                  start_new_session=True, check=False)
        except subprocess.TimeoutExpired as exc:
            return Result(-1, exc.stdout or b"", (exc.stderr or b"") + b"\ntimeout")
        return Result(done.returncode, done.stdout, done.stderr)


class FakeRunner:
    def __init__(self, handlers: dict[str, Callable[[list[str], bytes | None], Result]]):
        self.handlers = handlers
        self.calls: list[list[str]] = []

    def run(self, args, *, stdin=None, timeout=None, env=None) -> Result:
        self.calls.append(list(args))
        return self.handlers[args[0]](list(args), stdin)
```

`worker/drive.py`:

```python
"""Google Drive acquisition through rclone: a complete listing, then downloads by file id (§5.1)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from worker.config import WorkerConfig
from worker.runner import Runner


class ListingError(RuntimeError):
    """The listing was not complete: nothing may be marked removed."""


class DownloadError(RuntimeError):
    pass


@dataclass(frozen=True)
class Listed:
    drive_file_id: str
    path: str  # relative to the root listed
    name: str
    size: int
    md5: str
    mod_time: str
    root: str  # 'mail' | 'manual'


def folder_of(item: Listed) -> str:
    parts = [p for p in item.path.split("/") if p]
    return "/".join(parts[:-1])


class Drive:
    def __init__(self, cfg: WorkerConfig, runner: Runner):
        self.cfg, self.runner = cfg, runner

    def _list_root(self, root: str, drive_path: str) -> list[Listed]:
        args = ["rclone", "lsjson", "--hash", "--recursive", "--files-only", f"{self.cfg.rclone_remote}{drive_path}"]
        result = self.runner.run(args, timeout=300)
        if result.returncode != 0:
            raise ListingError(f"rclone lsjson exit {result.returncode}")
        try:
            rows = json.loads(result.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ListingError("rclone lsjson output is not JSON") from exc
        out = []
        for row in rows:
            if not str(row.get("Path", "")).lower().endswith(".pdf"):
                continue
            md5 = (row.get("Hashes") or {}).get("md5")
            if not md5 or "ID" not in row:
                raise ListingError(f"listing row without md5 or ID: {row.get('Path')}")
            out.append(Listed(row["ID"], row["Path"], row.get("Name", ""), int(row.get("Size", 0)), md5.lower(),
                              row.get("ModTime", ""), root))
        return out

    def list_pdfs(self) -> list[Listed]:
        return self._list_root("mail", self.cfg.mail_root) + self._list_root("manual", self.cfg.manual_root)

    def download(self, item: Listed, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        args = ["rclone", "backend", "copyid", self.cfg.rclone_remote, item.drive_file_id, str(dest)]
        result = self.runner.run(args, timeout=600)
        if result.returncode != 0 or not dest.exists():
            raise DownloadError(f"rclone copyid exit {result.returncode}")
```

`worker/inbox.py`:

```python
"""The private inbox: verified bytes published atomically by sha256, optionally mirrored to MinIO (ruling 4)."""
from __future__ import annotations

import filecmp
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from worker.config import WorkerConfig

MAX_PDF_BYTES = 20_000_000


def private_dir(path: Path) -> Path:
    """mkdir -p with mode 0700 (and chmod when it already existed with a wider mode)."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    return path


class VerifyError(RuntimeError):
    pass


class CollisionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Published:
    sha256: str
    object_key: str
    path: Path


def _digests(path: Path) -> tuple[str, str, int]:
    md5, sha = hashlib.md5(), hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            md5.update(chunk)
            sha.update(chunk)
            size += len(chunk)
    return md5.hexdigest(), sha.hexdigest(), size


def _same_bytes(a: Path, b: Path) -> bool:
    return filecmp.cmp(a, b, shallow=False)  # byte comparison, not a second digest (AGENT-95 Should)


class Inbox:
    def __init__(self, cfg: WorkerConfig):
        self.cfg = cfg

    def publish(self, staged: Path, *, expected_md5: str, expected_size: int) -> Published:
        md5, sha, size = _digests(staged)
        if md5 != expected_md5.lower() or size != expected_size:
            staged.unlink(missing_ok=True)
            raise VerifyError("downloaded bytes do not match the listing")
        private_dir(self.cfg.inbox_dir)
        target = self.cfg.inbox_dir / f"{sha}.pdf"
        if target.exists():
            if not _same_bytes(target, staged):
                raise CollisionError(f"inbox object {sha} holds different bytes")
            staged.unlink(missing_ok=True)
        else:
            tmp = self.cfg.inbox_dir / f".{sha}.tmp"
            os.replace(staged, tmp)
            os.chmod(tmp, 0o600)
            os.replace(tmp, target)
        return Published(sha, f"by-sha/{sha}.pdf", target)


class SourceIndex:
    """(drive_file_id, md5) → sha256 of what this worker already published; JSON, private, written atomically."""

    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, str] = {}
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))

    def get(self, drive_file_id: str, md5: str) -> str | None:
        return self.data.get(f"{drive_file_id}:{md5.lower()}")

    def put(self, drive_file_id: str, md5: str, sha256: str) -> None:
        self.data[f"{drive_file_id}:{md5.lower()}"] = sha256
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh)
        os.replace(tmp, self.path)


class ObjectStore(Protocol):
    def put(self, key: str, path: Path) -> None: ...
    def exists(self, key: str) -> bool: ...


class NullStore:
    def put(self, key: str, path: Path) -> None:
        return None

    def exists(self, key: str) -> bool:
        return True


class MinioStore:
    def __init__(self, endpoint: str, bucket: str, key_file: Path):
        from minio import Minio  # imported here so the package is optional without MinIO

        access, secret = key_file.read_text(encoding="utf-8").splitlines()[:2]
        self.client, self.bucket = Minio(endpoint, access_key=access.strip(), secret_key=secret.strip(),
                                         secure=endpoint.startswith("https://")), bucket

    def put(self, key: str, path: Path) -> None:
        self.client.fput_object(self.bucket, key, str(path), content_type="application/pdf")

    def exists(self, key: str) -> bool:
        from minio.error import S3Error

        try:
            self.client.stat_object(self.bucket, key)
        except S3Error:
            return False
        return True


def store_for(cfg: WorkerConfig) -> ObjectStore:
    if cfg.minio_endpoint is None:
        return NullStore()
    if cfg.minio_key_file is None:
        raise RuntimeError("STATEMENT_MINIO_KEY_FILE is required with STATEMENT_MINIO_ENDPOINT")
    return MinioStore(cfg.minio_endpoint, cfg.minio_bucket, cfg.minio_key_file)
```

- [ ] **Step 4:** run → 10 passed (this task's tests).
- [ ] **Step 5:** commit `feat(worker): Drive listing, download by id, verified atomic inbox`.

---

### Task 4: Unlock, extract, mask

**Files:**
- Create: `worker/pdf.py`, `worker/mask.py`
- Test: `tests/worker/pdfgen.py` (helper), `tests/worker/test_pdf.py`, `tests/worker/test_mask.py`

**Interfaces:**
- `pdf.Unlocked(reader: pypdf.PdfReader, password_index: int | None)`; `pdf.unlock(data: bytes, candidates: list[str]) -> Unlocked` raises `pdf.PasswordError` (message exactly `password`) when every candidate fails; an unencrypted PDF returns index `None`.
- `pdf.Extracted(text: str, tables: list[list[list[str]]], pages: int, text_chars_page1: int)`; `pdf.extract(data: bytes, password: str | None, *, max_pages=40, max_bytes=20_000_000, timeout_s=60) -> Extracted` raises `pdf.TooLarge`, `pdf.NoTextLayer` (page 1 < 200 chars), `pdf.ExtractTimeout`. Text is the concatenation of `page.extract_text()` with `\f` between pages; tables via `page.extract_tables()`.
- `mask.Masker(identity: passwords.Identity)` (the identity comes from `passwords.identity(path)`, Task 2); `Masker.text(s: str) -> str`; `Masker.tables(tables) -> tables`; `Masker.render(extracted: pdf.Extracted) -> str` = masked text + a `\n\n[tables]\n` section with every table row as `cell | cell | …`. Replacement tokens: `[ID]`, `[BIRTH]`, `[NAME]`, `[EMAIL]`, `[PHONE]`, numbers → `[NUM…1234]` (last 4 kept). Boundaries are **digit/ASCII-letter boundaries, not `\w`** (AGENT-95 Must 1): `\w` matches CJK, so `帳號123456789012結餘` must still mask; order: configured ID literal → generic ID pattern → names (longest first) → e-mail → birth literals → phones → digit runs.

- [ ] **Step 1: Write the helper** `tests/worker/pdfgen.py` (a minimal one-page text PDF, optional encryption):

```python
"""Tiny synthetic PDFs for worker tests: ASCII text on one or more pages, optionally AES-encrypted."""
from io import BytesIO

from pypdf import PdfReader, PdfWriter


def _page_stream(lines: list[str]) -> bytes:
    body = ["BT", "/F1 11 Tf", "40 780 Td", "13 TL"]
    for line in lines:
        safe = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        body.append(f"({safe}) Tj T*")
    body.append("ET")
    return "\n".join(body).encode("latin-1")


def make_pdf(pages: list[list[str]]) -> bytes:
    objects: list[bytes] = []
    page_ids = []
    font_id = 3
    kids_start = 4
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(b"")  # pages, filled below
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i, lines in enumerate(pages):
        content = _page_stream(lines)
        page_num = kids_start + 2 * i
        page_ids.append(page_num)
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {page_num + 1} 0 R >>".encode())
        objects.append(b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream")
    kids = " ".join(f"{p} 0 R" for p in page_ids)
    objects[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode()
    out = BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for n, obj in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{n} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def encrypt(data: bytes, password: str) -> bytes:
    writer = PdfWriter(clone_from=PdfReader(BytesIO(data)))
    writer.encrypt(user_password=password, owner_password=password, algorithm="AES-256")
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def statement_lines(n: int = 25) -> list[str]:
    """Enough ASCII to pass the 200-character page-1 probe."""
    return [f"2026/09/{(i % 28) + 1:02d} MERCHANT {i:03d} NT$ {100 + i}.00" for i in range(n)]
```

- [ ] **Step 2: Write the failing tests** `tests/worker/test_pdf.py`:

```python
import pytest

from tests.worker.pdfgen import encrypt, make_pdf, statement_lines
from worker import pdf


def test_unlock_tries_candidates_in_order_and_records_index():
    data = encrypt(make_pdf([statement_lines()]), "second")
    unlocked = pdf.unlock(data, ["first", "second"])
    assert unlocked.password_index == 1


def test_wrong_passwords_fail_with_the_word_password_only():
    data = encrypt(make_pdf([statement_lines()]), "right")
    with pytest.raises(pdf.PasswordError) as err:
        pdf.unlock(data, ["wrong-1", "wrong-2"])
    assert str(err.value) == "password" and "wrong" not in str(err.value)


def test_unencrypted_pdf_has_no_index():
    assert pdf.unlock(make_pdf([statement_lines()]), ["x"]).password_index is None


def test_extract_text_and_page_count():
    data = make_pdf([statement_lines(), ["PAGE TWO"]])
    out = pdf.extract(data, None)
    assert out.pages == 2 and "MERCHANT 003" in out.text and "\f" in out.text
    assert out.text_chars_page1 >= 200


def test_no_text_layer_fails_closed():
    with pytest.raises(pdf.NoTextLayer):
        pdf.extract(make_pdf([["short"]]), None)


def test_bounds():
    with pytest.raises(pdf.TooLarge):
        pdf.extract(make_pdf([statement_lines()]), None, max_bytes=10)
    with pytest.raises(pdf.TooLarge):
        pdf.extract(make_pdf([statement_lines()] * 3), None, max_pages=2)


def test_encrypted_extract_uses_password():
    data = encrypt(make_pdf([statement_lines()]), "pw")
    assert "MERCHANT 001" in pdf.extract(data, "pw").text
```

`tests/worker/test_mask.py` (the canary of §5.4; synthetic identity):

```python
from worker import mask, passwords, pdf

IDENT = passwords.Identity(id_number="A123456789", birth_date="19900101", holder_names=["王小明", "WANG XIAO MING"])


def test_text_canary_masks_every_class():
    m = mask.Masker(IDENT)
    s = ("卡號 4321-5678-9012-3456 戶名 王小明 WANG XIAO MING 身分證 A123456789 生日 1990/01/01 01/01/90 "
         "帳號 012 345 678 901 mail a.b@example.com 電話 0912-345-678 (02)2345-6789 small 12345")
    out = m.text(s)
    for secret in ("4321-5678-9012", "王小明", "WANG XIAO MING", "A123456789", "1990/01/01", "01/01/90", "012 345 678",
                   "a.b@example.com", "0912-345-678", "2345-6789"):
        assert secret not in out, secret
    assert "3456" in out and "[ID]" in out and "[NAME]" in out and "[EMAIL]" in out and "12345" in out


def test_cjk_adjacent_secrets_are_masked():
    m = mask.Masker(IDENT)
    out = m.text("帳號123456789012結餘 身分證B223456789末 戶名王小明先生 卡號4321-5678-9012-3456元")
    assert "123456789012" not in out and "B223456789" not in out and "王小明" not in out and "5678-9012" not in out
    assert out.startswith("帳號[NUM…9012]結餘") and "[ID]末" in out and "戶名[NAME]先生" in out and "3456]元" in out


def test_tables_are_masked_cell_by_cell():
    m = mask.Masker(IDENT)
    out = m.tables([[["王小明", "4321567890123456"], ["x", "ok"]]])
    assert out == [[["[NAME]", "[NUM…3456]"], ["x", "ok"]]]


def test_render_joins_text_and_tables():
    m = mask.Masker(IDENT)
    extracted = pdf.Extracted(text="hello A123456789", tables=[[["a", "b"]]], pages=1, text_chars_page1=200)
    rendered = m.render(extracted)
    assert rendered.startswith("hello [ID]") and "[tables]" in rendered and "a | b" in rendered


def test_masker_from_password_file_uses_the_shared_parser(tmp_path):
    p = tmp_path / "pw.env"
    p.write_text("  STATEMENT_ID_NUMBER=A123456789\nSTATEMENT_BIRTH_DATE=19900101\n"
                 "STATEMENT_HOLDER_NAMES=王小明,WANG\\, XIAO\n信用卡/x=$ID\n", encoding="utf-8")
    m = mask.Masker(passwords.identity(p))
    assert m.text("A123456789 王小明 WANG, XIAO 1990/01/01") == "[ID] [NAME] [NAME] [BIRTH]"
```

- [ ] **Step 3:** run → import errors.
- [ ] **Step 4: Implement** `worker/pdf.py`:

```python
"""Unlock (pypdf) and extract (pdfplumber) within the §5.4 bounds. Decrypted bytes stay in memory."""
from __future__ import annotations

import signal
from dataclasses import dataclass
from io import BytesIO

import pdfplumber
from pypdf import PdfReader
from pypdf.errors import PdfReadError


class PasswordError(RuntimeError):
    pass


class NoTextLayer(RuntimeError):
    pass


class TooLarge(RuntimeError):
    pass


class ExtractTimeout(RuntimeError):
    pass


@dataclass
class Unlocked:
    reader: PdfReader
    password_index: int | None


@dataclass(frozen=True)
class Extracted:
    text: str
    tables: list[list[list[str]]]
    pages: int
    text_chars_page1: int


def unlock(data: bytes, candidates: list[str]) -> Unlocked:
    reader = PdfReader(BytesIO(data))
    if not reader.is_encrypted:
        return Unlocked(reader, None)
    for index, candidate in enumerate(candidates):
        try:
            if reader.decrypt(candidate) > 0:
                return Unlocked(reader, index)
        except (PdfReadError, NotImplementedError):
            continue
    raise PasswordError("password")


class _Alarm:
    def __init__(self, seconds: int):
        self.seconds = seconds

    def __enter__(self):
        signal.signal(signal.SIGALRM, self._fire)
        signal.alarm(self.seconds)

    def __exit__(self, *exc):
        signal.alarm(0)

    @staticmethod
    def _fire(signum, frame):
        raise ExtractTimeout("extraction timed out")


def extract(data: bytes, password: str | None, *, max_pages: int = 40, max_bytes: int = 20_000_000,
            timeout_s: int = 60) -> Extracted:
    if len(data) > max_bytes:
        raise TooLarge(f"{len(data)} bytes")
    with _Alarm(timeout_s), pdfplumber.open(BytesIO(data), password=password) as doc:
        if len(doc.pages) > max_pages:
            raise TooLarge(f"{len(doc.pages)} pages")
        texts, tables = [], []
        for page in doc.pages:
            texts.append(page.extract_text() or "")
            for table in page.extract_tables() or []:
                tables.append([[("" if cell is None else str(cell)) for cell in row] for row in table])
        page1 = len(texts[0].strip()) if texts else 0
        if page1 < 200:
            raise NoTextLayer(f"page 1 has {page1} characters")
        return Extracted("\f".join(texts), tables, len(doc.pages), page1)
```

`worker/mask.py`:

```python
"""Masking of personal data before text leaves the worker (§5.4). Applied to text and every table cell."""
from __future__ import annotations

import re

from worker import pdf
from worker.passwords import Identity

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# boundaries on ASCII letters/digits only: CJK neighbours must not protect a secret (AGENT-95 Must 1)
_ID = re.compile(r"(?<![A-Za-z0-9])[A-Z][12]\d{8}(?![A-Za-z0-9])")
_PHONE = re.compile(r"(?<!\d)(?:\(0\d{1,2}\)|0\d{1,2})[- ]?\d{3,4}[- ]?\d{3,4}(?!\d)")
_DIGIT_RUN = re.compile(r"(?<!\d)\d(?:[ \-]?\d){5,}(?!\d)")  # separator-tolerant, ≥ 6 digits


def _birth_patterns(ymd: str) -> list[str]:
    y, m, d = ymd[0:4], ymd[4:6], ymd[6:8]
    return [f"{y}{m}{d}", f"{y}/{m}/{d}", f"{y}-{m}-{d}", f"{y}.{m}.{d}", f"{d}{m}{y[2:]}", f"{d}/{m}/{y[2:]}",
            f"{m}/{d}/{y}", f"{y}年{int(m)}月{int(d)}日", f"{int(y) - 1911}/{m}/{d}", f"{int(y) - 1911}{m}{d}"]


class Masker:
    def __init__(self, identity: Identity):
        self.identity = identity
        self.literals: list[str] = []
        if identity.birth_date and len(identity.birth_date) == 8:
            self.literals += _birth_patterns(identity.birth_date)
        self.names = sorted((n for n in identity.holder_names if n), key=len, reverse=True)

    @staticmethod
    def _digits(match: re.Match) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        return f"[NUM…{digits[-4:]}]"

    def text(self, s: str) -> str:
        if self.identity.id_number:
            s = s.replace(self.identity.id_number, "[ID]")
        s = _ID.sub("[ID]", s)
        for name in self.names:
            s = s.replace(name, "[NAME]")
        s = _EMAIL.sub("[EMAIL]", s)
        for literal in self.literals:
            s = s.replace(literal, "[BIRTH]")
        s = _PHONE.sub("[PHONE]", s)
        s = _DIGIT_RUN.sub(self._digits, s)
        return s

    def tables(self, tables: list[list[list[str]]]) -> list[list[list[str]]]:
        return [[[self.text(cell) for cell in row] for row in table] for table in tables]

    def render(self, extracted: pdf.Extracted) -> str:
        parts = [self.text(extracted.text)]
        if extracted.tables:
            parts.append("\n\n[tables]\n")
            for table in self.tables(extracted.tables):
                parts.extend(" | ".join(row) + "\n" for row in table)
                parts.append("\n")
        return "".join(parts)
```

`pdf.unlock` and `pdf.extract` wrap every library exception (`PdfReadError`, `pdfplumber`/`pdfminer` errors, `ValueError`, `KeyError`) into `pdf.Malformed` (message `malformed`), so a corrupted PDF is a bounded outcome (`failed/parse` in the pipeline), never a traceback with file content. Add `test_malformed_pdf_is_bounded`: `pdf.extract(b"%PDF-1.4 garbage", None)` raises `pdf.Malformed`. The 20 MB check runs on the listing size before download (Task 3) and again on the bytes before `PdfReader` is constructed (`unlock` checks `len(data)` first).

- [ ] **Step 5:** run → 13 passed. If `pdfplumber` cannot read the synthetic PDF's Helvetica text, switch the helper's font to `/Courier` (still a standard 14 font) and re-run; do not weaken the 200-char probe.
- [ ] **Step 6:** commit `feat(worker): unlock, extract within bounds, masking canary`.

---

### Task 5: The parser child and its gate

**Files:**
- Create: `worker/parse_schema.py`, `worker/parser.py`
- Test: `tests/worker/test_parser.py`, `tests/worker/fake_claude.py` (helper that writes a fake CLI script)

**Interfaces:**
- `parse_schema.ParsedLine`, `parse_schema.StatementParse` (pydantic, `extra="forbid"`): fields as `RevisionIn`/`LineIn` of `app/schemas/statements.py` but amounts as decimal **strings** (`pattern ^-?\d{1,15}(\.\d{1,4})?$`), `kind: Literal["card","bank"]`, `currency: str (3 upper)`, `lines ≤ 2000`, `merchant_raw ≤ 256`, `notes: str | None ≤ 500`; `parse_schema.json_schema() -> dict` (`StatementParse.model_json_schema()`), `parse_schema.INSTRUCTION` (fixed text below), `parse_schema.version(cli_version, model) -> str` (ruling 8).
- `parser.Parser(cfg, runner, *, config_dir: Path | None = None, credentials_writable: bool = True)`: `cli_version() -> str` (`claude --version` first token), `parse(masked_text: str) -> StatementParse` raising `parser.ParseError(reason)` with reasons `cli_version`, `stdin_too_large`, `timeout`, `output_too_large`, `exit`, `schema`, `sandbox`, `auth`; `gate() -> parser.GateReport(ok: bool, reasons: list[str], evidence: dict)` running the canary; `parser.command(cfg, schema_json: str, *, config_dir, credentials_writable) -> list[str]` builds the full argv (bwrap prefix when `cfg.parser_sandbox`; with `credentials_writable=False` there is no `--bind` of `.credentials.json`, the whole dir is `--ro-bind` — verify's mode, ruling 11).
- Stream rules (ruling 2) implemented in `parser.consume(stream: Iterable[bytes]) -> parser.Envelope` which validates **while consuming** against the exact allowed sequence `system/init → assistant(one tool_use StructuredOutput, optional text blocks) → user(tool_result) → [rate_limit_event]* → result/success` and records every deviation in `envelope.violations` (unknown or malformed event, any `hook*`/`subagent*`/`permission*` type **or subtype**, a second init or result, an assistant block that is not `text` or `tool_use`, a `tool_use` with another name, more than one assistant message, a user message without a `tool_result`, `num_turns != 2`, `tools != ["StructuredOutput"]`). `parser.check_envelope(env) -> list[str]` returns those violations. **Precedence** (AGENT-95 Must 3): violations are checked before exit status, schema and everything else, so a forbidden stream that also exits non-zero is `sandbox`, never a retried `exit`.
- I/O (AGENT-95 Must 4): stdin is written by a helper thread (so a child that stops reading cannot block the deadline loop; a `BrokenPipeError` there is recorded, not raised), stdout/stderr are drained with `selectors` and `os.read(fd, 65536)` under one deadline, every read is bounded by the caps (`STDOUT_CAP` each), the final drain after exit is bounded too, and any failure path kills the process group (`killpg(SIGKILL)`) and reaps it.
- `parser.Gate(cfg)`: `key(parser) -> dict` (`cli_version`, `model`, `config_dir_mtime`, `argv_sha256`, `sandbox`), `ensure(parser) -> None` (ruling 9: raises `parser.ParserDisabled` when `<state_dir>/parser-disabled.json` exists; otherwise reads `<state_dir>/gate.json` and returns when it is ok, same key and younger than 24 h; otherwise runs `parser.gate()` now, writes `gate.json`, and on failure writes the latch and raises `ParserDisabled`), `latch(reason: str, key: dict) -> None`, `clear() -> None` (only `python -m worker gate` after a success).

- [ ] **Step 1: Write the fake CLI helper** `tests/worker/fake_claude.py`:

```python
"""Writes an executable fake `claude` that emits a stream-json transcript (default: a well-behaved parse)."""
import json
import os
import stat
from pathlib import Path

GOOD = {"kind": "card", "currency": "TWD", "period_start": "2026-09-01", "period_end": "2026-09-30",
        "closing_date": "2026-09-30", "due_date": "2026-10-15", "opening_balance": "0", "statement_total": "580",
        "minimum_payment": "100", "notes": None,
        "lines": [{"seq": 1, "txn_date": "2026-09-03", "posted_date": "2026-09-04", "merchant_raw": "COFFEE",
                   "printed_amount": "80", "foreign_amount": None, "foreign_currency": None, "line_kind": "purchase",
                   "installment_seq": None, "installment_total": None, "is_subtotal": False},
                  {"seq": 2, "txn_date": "2026-09-10", "posted_date": "2026-09-10", "merchant_raw": "BOOKS",
                   "printed_amount": "500", "foreign_amount": None, "foreign_currency": None, "line_kind": "purchase",
                   "installment_seq": None, "installment_total": None, "is_subtotal": False}]}


def transcript(output=GOOD, *, tools=("StructuredOutput",), tool_name="StructuredOutput", extra_tool=False,
               num_turns=2, subtype="success"):
    events = [{"type": "system", "subtype": "init", "tools": list(tools), "claude_code_version": "2.1.296"}]
    content = [{"type": "tool_use", "name": tool_name, "input": output}]
    if extra_tool:
        content.append({"type": "tool_use", "name": "Bash", "input": {"command": "id"}})
    events.append({"type": "assistant", "message": {"content": content}})
    events.append({"type": "user", "message": {"content": [{"type": "tool_result"}]}})
    events.append({"type": "result", "subtype": subtype, "num_turns": num_turns, "is_error": subtype != "success",
                   "structured_output": output if subtype == "success" else None})
    return "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n"


def write(path: Path, body: str, *, version="2.1.296 (Claude Code)", sleep=0, stdout_bytes=None) -> Path:
    """`claude --version` prints `version`; any other invocation sleeps `sleep` s then prints `body` (or stdout_bytes)."""
    payload = (path.parent / f"{path.name}.out")
    payload.write_bytes(stdout_bytes if stdout_bytes is not None else body.encode("utf-8"))
    path.write_text("#!/bin/sh\n"
                    f'if [ "$1" = "--version" ]; then echo "{version}"; exit 0; fi\n'
                    f"cat >/dev/null\nsleep {sleep}\ncat '{payload}'\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path
```

- [ ] **Step 2: Write the failing tests** `tests/worker/test_parser.py`:

```python
import json
import os
import time

import pytest

from tests.worker import fake_claude
from worker import config, parse_schema, parser
from worker.runner import SubprocessRunner


@pytest.fixture
def cfg(tmp_path):
    cli = fake_claude.write(tmp_path / "claude", fake_claude.transcript())
    return config.load({"STATEMENT_STATE_DIR": str(tmp_path / "state"), "STATEMENT_PARSER_CLI": str(cli),
                        "STATEMENT_PARSER_SANDBOX": "false", "STATEMENT_PARSER_TIMEOUT": "5",
                        "STATEMENT_PARSER_ATTEMPTS": "1"})


def test_schema_is_strict_and_amounts_are_decimal_strings():
    schema = parse_schema.json_schema()
    assert schema["additionalProperties"] is False
    with pytest.raises(ValueError):
        parse_schema.StatementParse.model_validate({**fake_claude.GOOD, "statement_total": "5,80"})
    with pytest.raises(ValueError):
        parse_schema.StatementParse.model_validate({**fake_claude.GOOD, "extra": 1})
    assert len(parse_schema.StatementParse.model_validate(fake_claude.GOOD).lines) == 2


def test_command_without_sandbox_has_the_pinned_flags(cfg):
    argv = parser.command(cfg, "{}")
    assert argv[0] == str(cfg.parser_cli) and argv[1:4] == ["-p", "--safe-mode", "--tools"]
    for flag in ("--disallowedTools", "--strict-mcp-config", "--permission-prompts", "--no-session-persistence",
                 "--max-turns", "--output-format", "--verbose", "--json-schema", "--model"):
        assert flag in argv
    assert argv[argv.index("--model") + 1] == "claude-sonnet-5-5" and argv[-1] == parse_schema.INSTRUCTION


def test_command_with_sandbox_binds_only_the_stated_surfaces(tmp_path):
    cli = tmp_path / "bin" / "claude"
    cfg = config.load({"STATEMENT_PARSER_SANDBOX": "true", "STATEMENT_PARSER_CONFIG_DIR": str(tmp_path / "cfg"),
                       "STATEMENT_PARSER_CLI": str(cli)})
    argv = parser.command(cfg, "{}")
    assert argv[0] == "bwrap" and "--unshare-all" in argv and "--share-net" in argv and "--cap-drop" in argv
    assert argv[argv.index("--chdir") + 1] == "/work"
    i = argv.index("/opt/claude/claude")
    assert argv[i - 2:i + 1] == ["--ro-bind", str(cli), "/opt/claude/claude"]
    assert argv[argv.index("--bind") + 1] == str(tmp_path / "cfg" / ".credentials.json")
    host_paths = [a for a in argv if a.startswith("/") and not a.startswith(str(tmp_path))]
    assert all(p.startswith(("/usr", "/lib", "/etc/pki", "/etc/ssl", "/etc/resolv.conf", "/etc/hosts", "/opt/claude",
                             "/cfg", "/tmp", "/work", "/proc", "/dev")) for p in host_paths), host_paths
    env_pairs = [(argv[i + 1], argv[i + 2]) for i, a in enumerate(argv) if a == "--setenv"]
    assert dict(env_pairs) == {"HOME": "/tmp", "CLAUDE_CONFIG_DIR": "/cfg", "PATH": "/opt/claude:/usr/bin"}
    ro = parser.command(cfg, "{}", config_dir=tmp_path / "ro", credentials_writable=False)
    assert "--bind" not in ro and ["--ro-bind", str(tmp_path / "ro"), "/cfg"] == ro[ro.index("/cfg") - 2:ro.index("/cfg") + 1]


def test_parse_happy_path(cfg):
    out = parser.Parser(cfg, SubprocessRunner()).parse("masked text")
    assert out.statement_total == "580" and out.lines[1].merchant_raw == "BOOKS"


def test_version_pin(cfg, tmp_path):
    fake_claude.write(tmp_path / "claude", fake_claude.transcript(), version="2.1.295 (Claude Code)")
    with pytest.raises(parser.ParseError) as err:
        parser.Parser(cfg, SubprocessRunner()).parse("x")
    assert err.value.reason == "cli_version"


@pytest.mark.parametrize("variant, reason", [
    ({"extra_tool": True}, "sandbox"), ({"tool_name": "Bash"}, "sandbox"), ({"tools": ("StructuredOutput", "Bash")}, "sandbox"),
    ({"num_turns": 3}, "sandbox"), ({"hook_event": True}, "sandbox"), ({"duplicate_result": True}, "sandbox"),
    ({"garbage_line": True}, "sandbox"), ({"extra_tool": True, "exit_code": 1}, "sandbox"),
    ({"subtype": "error_max_turns"}, "exit"), ({"exit_code": 1}, "exit"),
    ({"output": {**fake_claude.GOOD, "statement_total": "abc"}}, "schema")])
def test_envelope_violations_and_precedence(cfg, tmp_path, variant, reason):
    exit_code = variant.pop("exit_code", 0)
    fake_claude.write(tmp_path / "claude", fake_claude.transcript(**variant), exit_code=exit_code)
    with pytest.raises(parser.ParseError) as err:
        parser.Parser(cfg, SubprocessRunner()).parse("x")
    assert err.value.reason == reason


def test_timeout_kills_the_whole_process_group(cfg, tmp_path):
    marker = tmp_path / "grandchild.pid"
    fake_claude.write(tmp_path / "claude", fake_claude.transcript(), sleep=30, grandchild_pid_file=marker)
    cfg = config.load({"STATEMENT_PARSER_CLI": str(tmp_path / "claude"), "STATEMENT_PARSER_SANDBOX": "false",
                       "STATEMENT_PARSER_TIMEOUT": "1", "STATEMENT_PARSER_ATTEMPTS": "1",
                       "STATEMENT_STATE_DIR": str(tmp_path / "state")})
    with pytest.raises(parser.ParseError) as err:
        parser.Parser(cfg, SubprocessRunner()).parse("x")
    assert err.value.reason == "timeout"
    pid = int(marker.read_text())
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)  # the grandchild died with the group


def test_child_that_never_reads_stdin_still_times_out(cfg, tmp_path):
    fake_claude.write(tmp_path / "claude", fake_claude.transcript(), sleep=30, read_stdin=False)
    cfg = config.load({"STATEMENT_PARSER_CLI": str(tmp_path / "claude"), "STATEMENT_PARSER_SANDBOX": "false",
                       "STATEMENT_PARSER_TIMEOUT": "1", "STATEMENT_PARSER_ATTEMPTS": "1",
                       "STATEMENT_STATE_DIR": str(tmp_path / "state")})
    started = time.monotonic()
    with pytest.raises(parser.ParseError) as err:
        parser.Parser(cfg, SubprocessRunner()).parse("x" * 300_000)  # larger than a pipe buffer
    assert err.value.reason == "timeout" and time.monotonic() - started < 4


def test_stdin_and_output_caps(cfg, tmp_path):
    with pytest.raises(parser.ParseError) as err:
        parser.Parser(cfg, SubprocessRunner()).parse("x" * (parser.STDIN_CAP + 1))
    assert err.value.reason == "stdin_too_large"
    fake_claude.write(tmp_path / "claude", "", stdout_bytes=b"{" + b"x" * (parser.STDOUT_CAP + 1))
    with pytest.raises(parser.ParseError) as err:
        parser.Parser(cfg, SubprocessRunner()).parse("x")
    assert err.value.reason == "output_too_large"
    fake_claude.write(tmp_path / "claude", fake_claude.transcript(), stderr_bytes=b"e" * (parser.STDOUT_CAP + 1))
    with pytest.raises(parser.ParseError) as err:
        parser.Parser(cfg, SubprocessRunner()).parse("x")
    assert err.value.reason == "output_too_large"


def test_gate_passes_on_the_canary_and_fails_on_a_second_tool(cfg, tmp_path):
    report = parser.Parser(cfg, SubprocessRunner()).gate()
    assert report.ok and report.evidence["num_turns"] == 2 and report.evidence["tools"] == ["StructuredOutput"]
    fake_claude.write(tmp_path / "claude", fake_claude.transcript(extra_tool=True))
    report = parser.Parser(cfg, SubprocessRunner()).gate()
    assert not report.ok and any("tool" in r for r in report.reasons)


def test_gate_ensure_caches_and_latches(cfg, tmp_path):
    prs = parser.Parser(cfg, SubprocessRunner())
    gate = parser.Gate(cfg)
    gate.ensure(prs)  # runs the canary once
    assert (cfg.state_dir / "gate.json").exists()
    fake_claude.write(tmp_path / "claude", fake_claude.transcript(extra_tool=True))
    gate.ensure(prs)  # cached evidence, same key → no new canary, still allowed
    stale = json.loads((cfg.state_dir / "gate.json").read_text())
    stale["checked_at"] = "2000-01-01T00:00:00+00:00"
    (cfg.state_dir / "gate.json").write_text(json.dumps(stale))
    with pytest.raises(parser.ParserDisabled):
        gate.ensure(prs)  # stale → re-run → violation → latch
    assert (cfg.state_dir / "parser-disabled.json").exists()
    fake_claude.write(tmp_path / "claude", fake_claude.transcript())
    with pytest.raises(parser.ParserDisabled):
        gate.ensure(prs)  # latched until an operator gate clears it
    assert gate.run_operator_gate(prs).ok and not (cfg.state_dir / "parser-disabled.json").exists()


def test_parser_version_string():
    v = parse_schema.version("2.1.296", "claude-sonnet-5-5")
    assert v.startswith("claude-cli-2.1.296-claude-sonnet-5-5-") and len(v.split("-")[-1]) == 8
```

The fake CLI helper gains: `exit_code=` (exit status after printing), `stderr_bytes=` (written to stderr), `read_stdin=False` (skip the `cat >/dev/null`), `grandchild_pid_file=` (the script starts `sleep 60 &` and writes its pid there before sleeping), and `transcript(..., hook_event=True)` (inserts `{"type": "system", "subtype": "hook_started"}` after init), `duplicate_result=True` (a second `result` line), `garbage_line=True` (a non-JSON line before the result).

- [ ] **Step 3:** run → import errors.
- [ ] **Step 4: Implement** `worker/parse_schema.py`:

```python
"""The parser's only output contract: StatementParse (decimal strings, caps) and the fixed instruction."""
from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MONEY = r"^-?\d{1,15}(\.\d{1,4})?$"
LINE_KINDS = ("purchase", "refund", "payment", "fee", "interest", "reward", "installment", "balance_adjustment",
              "deposit", "withdrawal", "transfer_in", "transfer_out", "unknown")


class ParsedLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seq: int = Field(ge=1, le=100000)
    txn_date: date | None = None
    posted_date: date
    merchant_raw: str = Field(max_length=256)
    printed_amount: str = Field(pattern=MONEY)
    foreign_amount: str | None = Field(default=None, pattern=MONEY)
    foreign_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    line_kind: Literal[LINE_KINDS]  # type: ignore[valid-type]
    installment_seq: int | None = Field(default=None, ge=1, le=120)
    installment_total: int | None = Field(default=None, ge=1, le=120)
    is_subtotal: bool = False


class StatementParse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["card", "bank"]
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    period_start: date
    period_end: date
    closing_date: date | None = None
    due_date: date | None = None
    opening_balance: str | None = Field(default=None, pattern=MONEY)
    statement_total: str = Field(pattern=MONEY)
    minimum_payment: str | None = Field(default=None, pattern=MONEY)
    lines: list[ParsedLine] = Field(max_length=2000)
    notes: str | None = Field(default=None, max_length=500)


INSTRUCTION = (
    "You are given, on stdin, the masked text (and extracted tables) of ONE Taiwanese bank or credit-card statement. "
    "Fill the StatementParse schema and nothing else. Rules: kind is card for a credit-card statement, bank for a "
    "bank account statement. Dates are ISO (YYYY-MM-DD); convert ROC years (民國, e.g. 115/09/30) by adding 1911. "
    "Amounts are decimal strings exactly as printed, without thousands separators or currency symbols. On a card "
    "statement a charge (purchase, fee, interest, installment, balance_adjustment) is a positive number and a credit "
    "(payment, refund, reward) is a negative number; on a bank statement a deposit/transfer_in/interest/refund/reward is "
    "positive and a withdrawal/transfer_out/fee/purchase/payment is negative. statement_total is the printed amount due "
    "(card) or the closing balance (bank); opening_balance is the previous balance when printed. One line per printed "
    "transaction row in print order with seq starting at 1; mark subtotal or total rows with is_subtotal true. Keep "
    "merchant_raw as printed (masked tokens such as [NUM…1234] stay as they are). For foreign-currency rows set "
    "foreign_amount and foreign_currency (ISO code). For installment rows set installment_seq and installment_total "
    "from the printed 分期 n/N. Use line_kind unknown when a row cannot be classified. Do not invent rows; if the "
    "text is not a statement, return zero lines and explain in notes."
)


def json_schema() -> dict:
    return StatementParse.model_json_schema()


def version(cli_version: str, model: str) -> str:
    instr = hashlib.sha256(INSTRUCTION.encode()).hexdigest()[:8]
    schema = hashlib.sha256(json.dumps(json_schema(), sort_keys=True).encode()).hexdigest()[:8]
    return f"claude-cli-{cli_version}-{model}-{instr}-{schema}"
```

`worker/parser.py`:

```python
"""The sandboxed model-as-a-function parser (§5.5, rulings 1–2, 8–9): command, strict stream, bounded I/O, gate."""
from __future__ import annotations

import hashlib
import json
import os
import selectors
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from pydantic import ValidationError

from worker import parse_schema
from worker.config import WorkerConfig
from worker.runner import Runner

STDIN_CAP = 400 * 1024
STDOUT_CAP = 2 * 1024 * 1024
STRUCTURED_TOOL = "StructuredOutput"
CANARY = "MASKED STATEMENT CANARY\n" + "\n".join(f"2026/09/{i:02d} CANARY SHOP {i} 100" for i in range(1, 12))
GATE_MAX_AGE = timedelta(hours=24)
FORBIDDEN_PREFIXES = ("hook", "subagent", "permission")


class ParseError(RuntimeError):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason if not detail else f"{reason}: {detail}")
        self.reason = reason


class ParserDisabled(RuntimeError):
    pass


@dataclass
class Envelope:
    structured_output: dict | None = None
    num_turns: int | None = None
    tools: list[str] = field(default_factory=list)
    tool_names: list[str] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    result_subtype: str | None = None
    assistant_messages: int = 0
    violations: list[str] = field(default_factory=list)


@dataclass
class GateReport:
    ok: bool
    reasons: list[str]
    evidence: dict


def _sandbox_prefix(cfg: WorkerConfig, config_dir: Path, credentials_writable: bool) -> list[str]:
    binds: list[str] = []
    for path in ("/usr", "/lib", "/lib64", "/etc/pki", "/etc/ssl", "/etc/resolv.conf", "/etc/hosts"):
        if os.path.exists(path):
            binds += ["--ro-bind", path, path]
    cfg_binds = ["--ro-bind", str(config_dir), "/cfg"]
    if credentials_writable:
        cfg_binds += ["--bind", str(config_dir / ".credentials.json"), "/cfg/.credentials.json"]
    return ["bwrap", "--unshare-all", "--share-net", "--die-with-parent", "--new-session", "--cap-drop", "ALL",
            *binds, "--ro-bind", str(cfg.parser_cli), "/opt/claude/claude", *cfg_binds,
            "--tmpfs", "/tmp", "--tmpfs", "/work", "--proc", "/proc", "--dev", "/dev", "--chdir", "/work",
            "--setenv", "HOME", "/tmp", "--setenv", "CLAUDE_CONFIG_DIR", "/cfg",
            "--setenv", "PATH", "/opt/claude:/usr/bin", "--", "/opt/claude/claude"]


def command(cfg: WorkerConfig, schema_json: str, *, config_dir: Path | None = None,
            credentials_writable: bool = True) -> list[str]:
    config_dir = config_dir or cfg.parser_config_dir
    head = _sandbox_prefix(cfg, config_dir, credentials_writable) if cfg.parser_sandbox else [str(cfg.parser_cli)]
    return [*head, "-p", "--safe-mode", "--tools", "", "--disallowedTools", "mcp__*", "--strict-mcp-config",
            "--mcp-config", '{"mcpServers":{}}', "--permission-prompts", "none", "--no-session-persistence",
            "--max-turns", "1", "--output-format", "stream-json", "--verbose", "--json-schema", schema_json,
            "--model", cfg.parser_model, parse_schema.INSTRUCTION]


def consume(lines: Iterable[bytes]) -> Envelope:
    """Validate the exact allowed sequence while consuming (ruling 2; AGENT-95 Must 3)."""
    env = Envelope()
    expect = "init"
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            env.violations.append("non-json line")
            continue
        if not isinstance(event, dict):
            env.violations.append("non-object event")
            continue
        kind, sub = str(event.get("type", "")), str(event.get("subtype", "") or "")
        env.events.append(f"{kind}/{sub}")
        if kind.startswith(FORBIDDEN_PREFIXES) or sub.startswith(FORBIDDEN_PREFIXES):
            env.violations.append(f"forbidden event {kind}/{sub}")
            continue
        if kind == "system" and sub == "init":
            if expect != "init":
                env.violations.append("duplicate init")
            env.tools = list(event.get("tools") or [])
            expect = "assistant"
        elif kind == "assistant":
            env.assistant_messages += 1
            if expect != "assistant":
                env.violations.append("assistant out of order")
            blocks = (event.get("message") or {}).get("content") or []
            for block in blocks:
                btype = block.get("type") if isinstance(block, dict) else None
                if btype == "tool_use":
                    env.tool_names.append(str(block.get("name", "")))
                elif btype != "text":
                    env.violations.append(f"assistant block {btype}")
            expect = "user"
        elif kind == "user":
            blocks = (event.get("message") or {}).get("content") or []
            if expect != "user" or not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in blocks):
                env.violations.append("user message without tool_result or out of order")
            expect = "result"
        elif kind == "rate_limit_event":
            continue
        elif kind == "result":
            if env.result_subtype is not None:
                env.violations.append("duplicate result")
            if expect != "result":
                env.violations.append("result out of order")
            env.result_subtype = sub
            env.num_turns = event.get("num_turns")
            env.structured_output = event.get("structured_output")
            expect = "end"
        else:
            env.violations.append(f"unknown event {kind}/{sub}")
    if env.tools != [STRUCTURED_TOOL]:
        env.violations.append(f"tools exposed: {env.tools}")
    if env.tool_names != [STRUCTURED_TOOL]:
        env.violations.append(f"tool calls: {env.tool_names}")
    if env.assistant_messages != 1:
        env.violations.append(f"assistant messages: {env.assistant_messages}")
    if env.num_turns != 2:
        env.violations.append(f"num_turns: {env.num_turns}")
    return env


def check_envelope(env: Envelope) -> list[str]:
    return list(env.violations)


class _ChildIO:
    """Bounded, deadline-driven I/O with the child: stdin from a thread, stdout/stderr via selectors."""

    def __init__(self, proc: subprocess.Popen, stdin: bytes, deadline: float):
        self.proc, self.deadline = proc, deadline
        self.out, self.err = bytearray(), bytearray()
        self.stdin_error: str | None = None
        self._writer = threading.Thread(target=self._write, args=(stdin,), daemon=True)

    def _write(self, data: bytes) -> None:
        try:
            self.proc.stdin.write(data)
            self.proc.stdin.close()
        except (BrokenPipeError, OSError):
            self.stdin_error = "stdin closed by the child"

    def pump(self) -> None:
        self._writer.start()
        sel = selectors.DefaultSelector()
        sel.register(self.proc.stdout, selectors.EVENT_READ, self.out)
        sel.register(self.proc.stderr, selectors.EVENT_READ, self.err)
        open_pipes = 2
        while open_pipes:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise ParseError("timeout")
            for key, _ in sel.select(timeout=min(remaining, 0.25)):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    sel.unregister(key.fileobj)
                    open_pipes -= 1
                    continue
                key.data.extend(chunk)
                if len(key.data) > STDOUT_CAP:
                    raise ParseError("output_too_large")
        if self.proc.wait(timeout=max(0.0, self.deadline - time.monotonic())) is None:
            raise ParseError("timeout")


def _kill(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


class Parser:
    def __init__(self, cfg: WorkerConfig, runner: Runner, *, config_dir: Path | None = None,
                 credentials_writable: bool = True):
        self.cfg, self.runner = cfg, runner
        self.config_dir, self.credentials_writable = config_dir or cfg.parser_config_dir, credentials_writable
        self._schema = json.dumps(parse_schema.json_schema(), ensure_ascii=False, separators=(",", ":"))

    def argv(self) -> list[str]:
        return command(self.cfg, self._schema, config_dir=self.config_dir, credentials_writable=self.credentials_writable)

    def cli_version(self) -> str:
        result = self.runner.run([str(self.cfg.parser_cli), "--version"], timeout=30)
        return result.stdout.decode("utf-8", "replace").split()[0] if result.returncode == 0 and result.stdout else ""

    def _check_version(self) -> None:
        found = self.cli_version()
        if found != self.cfg.parser_cli_version:
            raise ParseError("cli_version", f"found {found or 'none'}, pinned {self.cfg.parser_cli_version}")

    def _run_once(self, stdin: bytes) -> Envelope:
        env = {"HOME": os.environ.get("HOME", "/tmp"), "PATH": os.environ.get("PATH", "/usr/bin")}
        proc = subprocess.Popen(self.argv(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=env, start_new_session=True)
        io = _ChildIO(proc, stdin, time.monotonic() + self.cfg.parser_timeout_s)
        try:
            io.pump()
        except BaseException:
            _kill(proc)
            raise
        finally:
            if proc.poll() is None:
                _kill(proc)
        envelope = consume(bytes(io.out).splitlines())
        if envelope.violations:  # sandbox precedence over exit/schema (AGENT-95 Must 3)
            raise ParseError("sandbox", "; ".join(envelope.violations)[:500])
        if proc.returncode != 0 or envelope.result_subtype != "success":
            text = bytes(io.err).decode("utf-8", "replace")
            if "auth" in text.lower() or "login" in text.lower():
                raise ParseError("auth")
            raise ParseError("exit", f"rc={proc.returncode} subtype={envelope.result_subtype}")
        return envelope

    def _validated(self, envelope: Envelope) -> parse_schema.StatementParse:
        try:
            return parse_schema.StatementParse.model_validate(envelope.structured_output or {})
        except ValidationError as exc:
            raise ParseError("schema", str(exc)[:500]) from exc

    def parse(self, masked_text: str) -> parse_schema.StatementParse:
        stdin = masked_text.encode("utf-8")
        if len(stdin) > STDIN_CAP:
            raise ParseError("stdin_too_large")
        self._check_version()
        last: ParseError | None = None
        for _ in range(max(1, self.cfg.parser_attempts)):
            try:
                return self._validated(self._run_once(stdin))
            except ParseError as exc:
                if exc.reason in ("sandbox", "cli_version", "stdin_too_large", "auth"):
                    raise
                last = exc
        assert last is not None
        raise last

    def gate(self) -> GateReport:
        reasons: list[str] = []
        evidence: dict = {"pinned": self.cfg.parser_cli_version, "model": self.cfg.parser_model,
                          "sandbox": self.cfg.parser_sandbox}
        try:
            self._check_version()
            envelope = self._run_once(CANARY.encode())
            evidence.update(num_turns=envelope.num_turns, tools=envelope.tools, tool_calls=envelope.tool_names,
                            events=envelope.events)
            parse_schema.StatementParse.model_validate(envelope.structured_output or {})
        except ParseError as exc:
            reasons.append(f"{exc.reason}: {exc}"[:300])
        except ValidationError as exc:
            reasons.append(f"schema: {str(exc)[:200]}")
        return GateReport(not reasons, reasons, evidence)


class Gate:
    """Ruling 9: mandatory, cached for 24 h per key, latched on any violation until an operator gate succeeds."""

    def __init__(self, cfg: WorkerConfig):
        self.cfg = cfg
        self.evidence_path = cfg.state_dir / "gate.json"
        self.latch_path = cfg.state_dir / "parser-disabled.json"

    def key(self, parser: Parser) -> dict:
        try:
            mtime = parser.config_dir.stat().st_mtime
        except FileNotFoundError:
            mtime = None
        return {"cli_version": parser.cli_version(), "model": self.cfg.parser_model, "config_dir_mtime": mtime,
                "argv_sha256": hashlib.sha256("\0".join(parser.argv()).encode()).hexdigest(),
                "sandbox": self.cfg.parser_sandbox}

    def _write(self, path: Path, data: dict) -> None:
        self.cfg.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, default=str)

    def latch(self, reason: str, key: dict) -> None:
        self._write(self.latch_path, {"reason": reason[:300], "at": datetime.now(timezone.utc).isoformat(), "key": key})

    def clear(self) -> None:
        self.latch_path.unlink(missing_ok=True)

    def ensure(self, parser: Parser) -> None:
        if self.latch_path.exists():
            raise ParserDisabled("parser disabled until an operator gate succeeds")
        key = self.key(parser)
        if self.evidence_path.exists():
            evidence = json.loads(self.evidence_path.read_text(encoding="utf-8"))
            checked = datetime.fromisoformat(evidence.get("checked_at", "2000-01-01T00:00:00+00:00"))
            if evidence.get("ok") and evidence.get("key") == key and datetime.now(timezone.utc) - checked < GATE_MAX_AGE:
                return
        report = parser.gate()
        self._write(self.evidence_path, {"ok": report.ok, "reasons": report.reasons, "evidence": report.evidence,
                                         "key": key, "checked_at": datetime.now(timezone.utc).isoformat()})
        if not report.ok:
            self.latch("; ".join(report.reasons), key)
            raise ParserDisabled("gate failed")

    def run_operator_gate(self, parser: Parser) -> GateReport:
        """`python -m worker gate`: the only path that clears the latch (on success)."""
        report = parser.gate()
        key = self.key(parser)
        self._write(self.evidence_path, {"ok": report.ok, "reasons": report.reasons, "evidence": report.evidence,
                                         "key": key, "checked_at": datetime.now(timezone.utc).isoformat()})
        if report.ok:
            self.clear()
        else:
            self.latch("; ".join(report.reasons), key)
        return report
```

- [ ] **Step 5:** run → 24 passed. The sandbox test only builds argv; a real `bwrap` run is the operator's gate (Task 9 script).
- [ ] **Step 6:** commit `feat(worker): StatementParse schema, sandboxed parser child, envelope gate`.

---

### Task 6: API client, lease keeper, and the account-map route

**Files:**
- Create: `worker/api.py`
- Modify: `app/routers/statements.py` (one route; `GET /settings/reconciliation` switches to the SELECT-only reader), `app/services/settings_service.py` (`mapping_version`, `read_reconciliation_settings`, `read_reconciliation_rules`), `app/schemas/statements.py` (`AccountMapOut`, three optional version fields on `FileOut`)
- Test: `tests/worker/test_api.py` (against the real API through `TestClient`), extend `tests/integration/test_statement_ingest_api.py`

**Interfaces:**
- `settings_service.mapping_version(account_map: dict) -> str` = `sha256(json.dumps(account_map, sort_keys=True, ensure_ascii=False, separators=(",", ":")))[:16]`.
- `settings_service.read_reconciliation_settings(db) -> dict` and `read_reconciliation_rules(db) -> dict` (ruling 12): `db.get(ReconciliationSettings, 1)` without the insert; when the row is absent they return the same defaults `_reconciliation_dict` would produce for an empty row (`account_map {}`, `dirty_enabled False`, `rules` = `RULE_DEFAULTS`, `version 0`). `GET /settings/reconciliation` and `GET /statements/account-map` use them; `PUT` keeps `_reconciliation_row(lock=True)`. Test: in a session that ran `SET TRANSACTION READ ONLY`, `read_reconciliation_settings` succeeds on an empty table and `get_reconciliation_settings` raises.
- `GET /statements/account-map` → `AccountMapOut(account_map: dict[str, int], mapping_version: str)`, scope `ingest`, behind the feature gate.
- `api.ApiClient(client: httpx.Client, token: str, *, guard: Callable[[], bool] | None = None)`: `guard` (the pipeline passes `lambda: keeper.lost`) is consulted **before every lease-route request**; when it returns True the client raises `LeaseLost` without sending (AGENT-95 Must 7: a lease lost during a parse blocks the submission that follows). Methods: `create_run(trigger, mode, initiator_hint) -> api.Lease(run_id, lease_token, lease_expires_at, attempt)` (`POST /statements/ingest-runs`), `queued_runs() -> list[dict]`, `claim(run_id) -> Lease`, `renew(lease) -> None`, `finish(lease, status, summary) -> None`, `account_map() -> tuple[dict[str, int], str]`, `register_file(lease, sha256, size, kind, object_key) -> dict`, `update_file(lease, file_id, **fields) -> dict`, `register_source(lease, file_id, root, drive_file_id, drive_path, drive_md5, drive_size) -> dict`, `mark_removed(lease, seen_ids: list[str], allow_empty=False) -> int`, `submit_revision(lease, body: dict) -> dict`. Every call raises `api.LeaseLost` on 409 from a lease route or submission, `api.ApiError(status, body)` otherwise. Requests carry `Authorization: Bearer <token>`.
- `api.LeaseKeeper(client: ApiClient, lease: Lease, interval_s=300)`: context manager running a daemon thread that renews; **any** exception from a renew (`ApiError`, `httpx.HTTPError`, `OSError`) sets `keeper.lost = True` and stops the thread; `__exit__` joins the thread (bounded) and never raises. `api.RunState(path)` persists `{run_id, lease_token, attempt, started_at}` at `<state_dir>/run.json` (0600) after a claim and deletes it after `finish`; on start the pipeline reads it and, when `renew` succeeds, **resumes** that run (same lease), otherwise discards the file (§4.1 restart recovery; AGENT-95 Must 7).

- [ ] **Step 1: Write the failing API test** (append to `tests/integration/test_statement_ingest_api.py`):

```python
def test_read_only_settings_reader_never_inserts(db_session):
    from sqlalchemy import text
    from app.services import settings_service
    db_session.execute(text("SET TRANSACTION READ ONLY"))
    data = settings_service.read_reconciliation_settings(db_session)
    assert data["account_map"] == {} and data["dirty_enabled"] is False and "rules" in data
    with pytest.raises(Exception):
        settings_service.get_reconciliation_settings(db_session)  # INSERT ... ON CONFLICT is refused read-only
    db_session.rollback()


def test_account_map_route_is_ingest_scoped(client):
    put = client.put("/settings/reconciliation", headers=S, json={"account_map": {"mail/信用卡/國泰世華": 1},
                                                                  "dirty_enabled": False, "rules": {}})
    assert put.status_code == 200
    assert client.get("/statements/account-map", headers=H).status_code == 403
    got = client.get("/statements/account-map", headers=W)
    assert got.status_code == 200
    assert got.json()["account_map"] == {"mail/信用卡/國泰世華": 1} and len(got.json()["mapping_version"]) == 16
```

and `tests/worker/test_api.py`:

```python
import threading
import time

import pytest

from app import auth
from worker import api
from tests.integration.test_statement_ingest_api import SCOPES, TOKENS  # the same token fixtures

W_TOKEN = "worker-token"


@pytest.fixture(autouse=True)
def recon_env(monkeypatch):
    monkeypatch.setenv(auth.TOKENS_ENV, TOKENS)
    monkeypatch.setenv(auth.SCOPES_ENV, SCOPES)
    monkeypatch.setenv(auth.FEATURE_ENV, "true")


@pytest.fixture
def worker_api(client):
    return api.ApiClient(client, W_TOKEN)


def test_run_lifecycle(worker_api):
    lease = worker_api.create_run("owner_cli", "live", "tester")
    assert lease.run_id > 0 and len(lease.lease_token) >= 16 and lease.attempt == 1
    worker_api.renew(lease)
    file = worker_api.register_file(lease, "ab" * 32, 10, "card", "by-sha/" + "ab" * 32 + ".pdf")
    assert worker_api.register_file(lease, "AB" * 32, 10, "card", "by-sha/x.pdf")["id"] == file["id"]
    src = worker_api.register_source(lease, file["id"], "mail", "id-1", "信用卡/國泰世華/2026-09.pdf", "cd" * 16, 10)
    assert src["drive_file_id"] == "id-1"
    assert worker_api.mark_removed(lease, ["id-1"]) == 0
    worker_api.update_file(lease, file["id"], status="failed", failure="password", credential_version="v1")
    worker_api.finish(lease, "done", {"files": 1})
    with pytest.raises(api.LeaseLost):
        worker_api.renew(lease)


def test_lease_lost_on_submission_with_a_stale_token(worker_api):
    lease = worker_api.create_run("owner_cli", "live", "tester")
    stale = api.Lease(lease.run_id, "t" * 32, lease.lease_expires_at, lease.attempt)
    with pytest.raises(api.LeaseLost):
        worker_api.register_file(stale, "cd" * 32, 1, "card", "by-sha/y.pdf")


def test_account_map(worker_api, client):
    client.put("/settings/reconciliation", headers={"Authorization": "Bearer spa-token"},
               json={"account_map": {"manual/國泰世華": 3}, "dirty_enabled": False, "rules": {}})
    mapping, version = worker_api.account_map()
    assert mapping == {"manual/國泰世華": 3} and len(version) == 16


def test_guard_blocks_lease_routes_before_sending(client):
    lost = {"v": False}
    guarded = api.ApiClient(client, W_TOKEN, guard=lambda: lost["v"])
    lease = guarded.create_run("owner_cli", "live", "tester")
    lost["v"] = True
    with pytest.raises(api.LeaseLost):
        guarded.register_file(lease, "ab" * 32, 1, "card", "by-sha/x.pdf")
    assert guarded.queued_runs() == []  # non-lease reads still work


def test_run_state_round_trip(tmp_path, worker_api):
    lease = worker_api.create_run("owner_cli", "live", "tester")
    state = api.RunState(tmp_path / "run.json")
    state.save(lease)
    assert oct((tmp_path / "run.json").stat().st_mode)[-3:] == "600"
    assert state.load() == lease
    state.clear()
    assert state.load() is None


def test_lease_keeper_flags_transport_errors(worker_api, monkeypatch):
    lease = worker_api.create_run("owner_cli", "live", "tester")

    def boom(l):
        raise ConnectionError("down")

    monkeypatch.setattr(worker_api, "renew", boom)
    with api.LeaseKeeper(worker_api, lease, interval_s=0.05) as keeper:
        time.sleep(0.2)
        assert keeper.lost


def test_lease_keeper_renews_and_flags_loss(worker_api, monkeypatch):
    lease = worker_api.create_run("owner_cli", "live", "tester")
    calls = []
    real = worker_api.renew

    def renew(l):
        calls.append(1)
        return real(l)

    monkeypatch.setattr(worker_api, "renew", renew)
    with api.LeaseKeeper(worker_api, lease, interval_s=0.05) as keeper:
        time.sleep(0.2)
        assert calls and not keeper.lost
        worker_api.finish(lease, "done", {})
        time.sleep(0.2)
        assert keeper.lost
    assert not any(t.name.startswith("lease-keeper") and t.is_alive() for t in threading.enumerate())
```

- [ ] **Step 2:** run → 404 on the route / import error.
- [ ] **Step 3: Implement.** In `app/services/settings_service.py` add:

```python
def mapping_version(account_map: dict) -> str:
    """Stable 16-hex digest of the account map (the worker's `mapping_version` retry trigger)."""
    canonical = json.dumps(account_map or {}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()[:16]
```

(`import json` and `from hashlib import sha256` at the top). In `app/schemas/statements.py`:

```python
class AccountMapOut(BaseModel):
    account_map: dict[str, int]
    mapping_version: str
```

Also in `settings_service.py`:

```python
def read_reconciliation_settings(db: Session) -> dict:
    """SELECT-only variant for read-only transactions (ruling 12): defaults when the row does not exist."""
    row = db.get(ReconciliationSettings, 1)
    return _reconciliation_dict(row) if row is not None else _reconciliation_dict(ReconciliationSettings(id=1, data={}, version=0))


def read_reconciliation_rules(db: Session) -> dict:
    return read_reconciliation_settings(db)["rules"]
```

(check `_reconciliation_dict` tolerates a transient, un-added `ReconciliationSettings` instance — it reads `row.data` and `row.version` only; adjust the constructor kwargs to the model's columns). Switch `GET /settings/reconciliation` to `read_reconciliation_settings`. In `app/routers/statements.py`, next to the other ingest-scope routes:

```python
@router.get("/statements/account-map", response_model=AccountMapOut, dependencies=INGEST)
def read_account_map(db: Session = Depends(get_db)):
    data = settings_service.read_reconciliation_settings(db)
    return AccountMapOut(account_map=data.get("account_map") or {},
                         mapping_version=settings_service.mapping_version(data.get("account_map") or {}))
```

(`INGEST` is the dependency list the file already uses on `POST /statements/files`; `AccountMapOut` joins the schema imports). `worker/api.py`:

```python
"""The worker's only way to application state: the ingest API over HTTP under a run lease (§4.1, §8)."""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

LEASE_ROUTES = ("/renew", "/finish", "/files", "/sources", "/revisions")


class ApiError(RuntimeError):
    def __init__(self, status: int, body):
        super().__init__(f"api {status}: {str(body)[:300]}")
        self.status, self.body = status, body


class LeaseLost(ApiError):
    pass


@dataclass(frozen=True)
class Lease:
    run_id: int
    lease_token: str
    lease_expires_at: str
    attempt: int

    def body(self, **extra) -> dict:
        return {"run_id": self.run_id, "lease_token": self.lease_token, **extra}


class ApiClient:
    def __init__(self, client: httpx.Client, token: str, *, guard: Callable[[], bool] | None = None):
        self.client, self.headers, self.guard = client, {"Authorization": f"Bearer {token}"}, guard

    def _call(self, method: str, path: str, *, json=None, params=None, lease_route: bool = False):
        if lease_route and self.guard is not None and self.guard():
            raise LeaseLost(409, "lease lost (local guard)")
        response = self.client.request(method, path, json=json, params=params, headers=self.headers)
        if response.status_code >= 400:
            body = response.json() if response.headers.get("content-type", "").startswith("application/json") else response.text
            if response.status_code == 409 and (lease_route or any(path.endswith(r) or r in path for r in LEASE_ROUTES)):
                raise LeaseLost(response.status_code, body)
            raise ApiError(response.status_code, body)
        return response.json() if response.content else None

    @staticmethod
    def _lease(data: dict) -> Lease:
        return Lease(data["run_id"], data["lease_token"], data["lease_expires_at"], data["attempt"])

    def create_run(self, trigger: str, mode: str, initiator_hint: str | None) -> Lease:
        return self._lease(self._call("POST", "/statements/ingest-runs",
                                      json={"trigger": trigger, "mode": mode, "initiator_hint": initiator_hint}))

    def queued_runs(self) -> list[dict]:
        return self._call("GET", "/statements/ingest-runs", params={"status": "queued"}) or []

    def claim(self, run_id: int) -> Lease:
        return self._lease(self._call("POST", f"/statements/ingest-runs/{run_id}/claim", lease_route=True))

    def renew(self, lease: Lease) -> None:
        self._call("POST", f"/statements/ingest-runs/{lease.run_id}/renew", json={"lease_token": lease.lease_token},
                   lease_route=True)

    def finish(self, lease: Lease, status: str, summary: dict) -> None:
        self._call("POST", f"/statements/ingest-runs/{lease.run_id}/finish",
                   json=lease.body(status=status, summary=summary), lease_route=True)

    def account_map(self) -> tuple[dict[str, int], str]:
        data = self._call("GET", "/statements/account-map")
        return data["account_map"], data["mapping_version"]

    def register_file(self, lease: Lease, sha256: str, size: int, kind: str, object_key: str) -> dict:
        return self._call("POST", "/statements/files", json=lease.body(sha256=sha256, size=size, kind=kind,
                                                                       object_key=object_key), lease_route=True)

    def update_file(self, lease: Lease, file_id: int, **fields) -> dict:
        return self._call("PATCH", f"/statements/files/{file_id}", json=lease.body(**fields), lease_route=True)

    def register_source(self, lease: Lease, file_id: int, root: str, drive_file_id: str, drive_path: str,
                        drive_md5: str, drive_size: int) -> dict:
        return self._call("POST", "/statements/sources", json=lease.body(
            file_id=file_id, root=root, drive_file_id=drive_file_id, drive_path=drive_path, drive_md5=drive_md5,
            drive_size=drive_size), lease_route=True)

    def mark_removed(self, lease: Lease, seen_ids: list[str], allow_empty: bool = False) -> int:
        return self._call("POST", "/statements/sources/mark-removed",
                          json=lease.body(seen_drive_file_ids=seen_ids, allow_empty=allow_empty),
                          lease_route=True)["removed"]

    def submit_revision(self, lease: Lease, body: dict) -> dict:
        return self._call("POST", "/statements/revisions", json=lease.body(**body), lease_route=True)


class LeaseKeeper:
    """Renews the lease every `interval_s` on a daemon thread; `lost` becomes True after a failed renew."""

    def __init__(self, client: ApiClient, lease: Lease, interval_s: float = 300):
        self.client, self.lease, self.interval_s = client, lease, interval_s
        self.lost = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name=f"lease-keeper-{lease.run_id}", daemon=True)

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            try:
                self.client.renew(self.lease)
            except Exception:  # noqa: BLE001 — ApiError, httpx transport errors, OSError: all mean "not renewed"
                self.lost = True
                return

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=5)
        return False


class RunState:
    """The claimed lease on disk (0600) so a restarted worker resumes its own run (§4.1)."""

    def __init__(self, path: Path):
        self.path = path

    def save(self, lease: Lease) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(lease.__dict__, fh)

    def load(self) -> Lease | None:
        if not self.path.exists():
            return None
        return Lease(**json.loads(self.path.read_text(encoding="utf-8")))

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
```

`FileRegisterIn.sha256` accepts 64 hex of any case; `FileUpdateIn` field names are as in the schema; the client passes them through unchanged. Add `credential_version: str | None = None`, `mapping_version: str | None = None`, `parser_version: str | None = None` to `FileOut` (read-only schema addition, no migration) and `def list_files(self) -> list[dict]: return self._call("GET", "/statements/files") or []` to `ApiClient`.

- [ ] **Step 4:** run `tests/worker/test_api.py tests/integration/test_statement_ingest_api.py` → all pass (the `TestClient` raises on 4xx only if `raise_server_exceptions`; our client inspects status codes).
- [ ] **Step 5:** commit `feat(accounting): account-map route for the worker; worker API client and lease keeper`.

---

### Task 7: The pipeline (per-file state machine, run orchestration, CLI)

**Files:**
- Create: `worker/pipeline.py`, `worker/cli.py`, `worker/__main__.py`
- Test: `tests/worker/test_pipeline.py` (end to end: fake rclone, fake claude, real API through `TestClient`, synthetic encrypted PDFs)

**Interfaces:**
- `pipeline.Services(cfg, api: ApiClient, drive: Drive, inbox: Inbox, index: SourceIndex, retries: RetryLog, store: ObjectStore, parser: Parser, gate: Gate, candidates: Candidates, credential_version: str, masker: Masker, runner)` assembled by `pipeline.build(cfg, client: httpx.Client, runner: Runner) -> Services` (reads the token file, password file, identity). `pipeline.RetryLog(path)` (`<state_dir>/retries.json`, 0600): per sha256 `{transient_attempts: int, store_pending: bool}` — the worker's own transient epoch (reset on any non-transient outcome; AGENT-95 Should) and the "object not yet stored" flag (ruling 10).
- `pipeline.run(services, *, trigger: str, mode: str = "live", initiator_hint: str | None = None, acknowledge_live_periods: bool = False) -> dict` (the summary): creates and claims the run, lists Drive, acquires new/changed sources, processes every file that is `new` or retry-eligible, marks removed sources, finishes the run. Returns the summary dict `{"listed", "new_files", "parsed", "needs_review", "failed", "ignored", "skipped", "unchanged", "transient", "cases_opened", "live_revisions", "errors": [...]}` with counts only (no file names). `unchanged` = files whose row is not retry-eligible this run (parsed, or failed with the same version); `skipped` = files not processed because the parser was disabled during this run. An item whose `(drive_file_id, md5)` is in the `SourceIndex` and whose file row exists in `GET /statements/files` is not downloaded; its bytes are read from the inbox only when it needs processing (missing inbox object → re-acquire).
- `pipeline.process_file(services, lease, file_row: dict, listed: Listed, data: bytes, account_map, mapping_version, summary) -> str` returns the final status and performs: mapping (`account_map[f"{root}/{folder[:2 levels]}"]` else `ignored/mapping`), unlock (`failed/password` on `PasswordError`, index not persisted; `failed/parse` on `pdf.Malformed`), extract (`needs_review/no_text_layer`, `needs_review/too_large`, `failed/transient` on timeout), mask + render, parse (`failed/parse` on schema/exit; `failed/transient` on timeout; `failed/sandbox` on `sandbox`/`cli_version`/`auth` → `services.gate.latch(...)`, every later file of this run is `skipped`, and the run finishes **`failed`** with `errors: ["parser_disabled"]` — ruling 9), submit (`parsed`, or `needs_review/guardrail` when `guardrail_ok` is false; the revision is still stored by the API). `LeaseLost` (from the API or the local guard) propagates and aborts the run.
- Retry eligibility (`pipeline.retry_due(file_row, retry: dict, now, credential_version, mapping_version, parser_version) -> bool`): `status in ('new','unlocked')` always; `failure == 'transient'` and (`next_retry_at` is None or ≤ now) and `retry['transient_attempts'] < 5` (the worker's own epoch, not the server's cumulative `attempts`); `failure == 'password'` and `credential_version` differs; `failure == 'mapping'` and `mapping_version` differs; `failure in ('parse','guardrail','no_text_layer','too_large')` and `parser_version` differs; `failure == 'sandbox'` only when the latch is clear **and** `parser_version` differs or the gate evidence is newer than the file's `parsed_at`/update (sandbox recovery is operator-driven, ruling 9). Backoff for `transient`: `next_retry_at = now + {1: 1h, 2: 6h, 3+: 24h}[transient_attempts]`; any non-transient outcome resets `transient_attempts` to 0. Tests pin the 1h/6h/24h steps and the max-5 boundary.
- Live-period acknowledgement (ruling 13): `backfill` requires `--acknowledge-live-periods`; without it the CLI exits 2 with `backfill may touch live periods; pass --acknowledge-live-periods` and no run is created. The live-period count is reported in the run summary afterwards (`live_revisions`).
- Full runs (`trigger in ('timer','owner_cli')`, i.e. `run`/`backfill`, not `poll`) end with the daily sweep: `POST /reconciliation/sweep` under the lease (`api.sweep(lease) -> dict`); its counts go to `summary.sweep` (`reconciled`, `busy`, `errors`), a 409 there is recorded as `sweep: busy`, not an abort (ruling 13, §4.6).
- Run summary `versions`: `{"parser_version", "cli_version", "model", "credential_version", "mapping_version"}` (ruling 13).
- Restart recovery: `run()` first consults `api.RunState(<state_dir>/run.json)`; a saved lease that still renews is resumed (`summary.resumed = True`, same run id, no new run), otherwise the file is discarded and a new run is created. The state file is written right after the claim and removed after `finish`.
- `pipeline.singleton(cfg) -> contextmanager` using `fcntl.flock(LOCK_EX | LOCK_NB)` on `cfg.lock_path` (state dir created 0700 first); raises `pipeline.AlreadyRunning`.
- Every run starts with `services.gate.ensure(services.parser)` (ruling 9); `ParserDisabled` there makes the run finish `failed` with `errors: ["parser_disabled"]` after the acquisition phase (files are still acquired and registered so nothing is lost; none is parsed).
- CLI (`python -m worker <cmd>`): sets `os.umask(0o077)` first; `run` (`--trigger timer|owner_cli`, default `owner_cli`), `poll` (claim queued runs: for each queued run → `claim` → the same processing under that lease, `trigger='enqueue'`), `backfill` (`--acknowledge-live-periods`), `gate` (runs `Gate.run_operator_gate`, prints the report as JSON, exit 1 when not ok — the only way to clear the latch), `export-masked` and `verify` (Task 8). Exit codes: 0 ok **and** `already running` (timers must not fail), 1 run failed, 2 usage/refused.

- [ ] **Step 1: Write the failing end-to-end test** `tests/worker/test_pipeline.py`:

```python
import hashlib
import json
from datetime import date

import pytest
from sqlalchemy import text

from app import auth
from tests.integration.test_statement_ingest_api import SCOPES, TOKENS
from tests.worker import fake_claude
from tests.worker.pdfgen import encrypt, make_pdf, statement_lines
from worker import config, pipeline
from worker.runner import FakeRunner, Result, SubprocessRunner

PW_FILE = "STATEMENT_ID_NUMBER=A123456789\nSTATEMENT_BIRTH_DATE=19900101\nSTATEMENT_HOLDER_NAMES=王小明\n信用卡/國泰世華=$ID\n"


@pytest.fixture(autouse=True)
def recon_env(monkeypatch):
    monkeypatch.setenv(auth.TOKENS_ENV, TOKENS)
    monkeypatch.setenv(auth.SCOPES_ENV, SCOPES)
    monkeypatch.setenv(auth.FEATURE_ENV, "true")


@pytest.fixture
def card(seed, db_session):
    account = seed.account("卡", is_credit=True, statement_live_from=date(2026, 9, 1))
    db_session.commit()
    return account


@pytest.fixture
def world(tmp_path, client, card):
    """Drive with one encrypted card PDF; the fake parser returns GOOD; the account map points at `card`."""
    pdf_bytes = encrypt(make_pdf([statement_lines()]), "A123456789")
    drive_files = {"id-1": ("信用卡/國泰世華/2026-09_國泰世華.pdf", pdf_bytes)}

    def rclone(args, stdin):
        if args[1] == "lsjson":
            if not args[-1].endswith("/銀行"):
                return Result(0, b"[]", b"")
            rows = [{"Path": p, "Name": p.split("/")[-1], "Size": len(b), "ModTime": "2026-10-01T00:00:00Z",
                     "Hashes": {"md5": hashlib.md5(b).hexdigest()}, "ID": fid, "MimeType": "application/pdf"}
                    for fid, (p, b) in drive_files.items()]
            return Result(0, json.dumps(rows).encode(), b"")
        if args[1:3] == ["backend", "copyid"]:
            from pathlib import Path
            Path(args[5]).write_bytes(drive_files[args[4]][1])
            return Result(0, b"", b"")
        raise AssertionError(args)

    pw = tmp_path / "pw.env"
    pw.write_text(PW_FILE, encoding="utf-8")
    token = tmp_path / "token"
    token.write_text("worker-token")
    cli = fake_claude.write(tmp_path / "claude", fake_claude.transcript())
    cfg = config.load({"STATEMENT_STATE_DIR": str(tmp_path / "state"), "STATEMENT_PASSWORD_FILE": str(pw),
                       "STATEMENT_API_TOKEN_FILE": str(token), "STATEMENT_PARSER_CLI": str(cli),
                       "STATEMENT_PARSER_SANDBOX": "false", "STATEMENT_PARSER_TIMEOUT": "5",
                       "STATEMENT_PARSER_ATTEMPTS": "1"})
    client.put("/settings/reconciliation", headers={"Authorization": "Bearer spa-token"},
               json={"account_map": {"mail/信用卡/國泰世華": card.id}, "dirty_enabled": False, "rules": {}})

    class Runner:  # rclone through the fake, claude through a real subprocess (the fake script)
        def __init__(self):
            self.fake, self.real = FakeRunner({"rclone": rclone}), SubprocessRunner()
            self.downloads = 0

        def run(self, args, **kw):
            if args[0] == "rclone" and args[1:3] == ["backend", "copyid"]:
                self.downloads += 1
            return self.fake.run(args, **kw) if args[0] == "rclone" else self.real.run(args, **kw)

    runner = Runner()
    services = pipeline.build(cfg, client, runner)
    type(services).drive_downloads = property(lambda self: runner.downloads)  # test-only accessor
    return services, drive_files, cfg


def _counts(db_session):
    return {t: db_session.execute(text(f"select count(*) from {t}")).scalar_one()
            for t in ("statement_file", "statement_source", "account_statement", "statement_revision", "ingest_run")}


def _file_rows(db_session):
    return db_session.execute(text("select status, failure from statement_file order by id")).all()


def test_end_to_end_parses_and_is_idempotent(world, db_session, client):
    services, _, cfg = world
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["listed"] == 1 and summary["new_files"] == 1 and summary["parsed"] == 1 and summary["errors"] == []
    assert _counts(db_session) == {"statement_file": 1, "statement_source": 1, "account_statement": 1,
                                   "statement_revision": 1, "ingest_run": 1}
    row = db_session.execute(text("select status, failure, has_text_layer, credential_version from statement_file")).one()
    assert row[0] == "parsed" and row[1] is None and row[2] is True and len(row[3]) == 64
    assert list(cfg.inbox_dir.iterdir())[0].suffix == ".pdf"
    second = pipeline.run(services, trigger="owner_cli")
    assert second["new_files"] == 0 and second["parsed"] == 0 and second["unchanged"] == 1 and second["skipped"] == 0
    assert services.drive_downloads == 1  # the second run did not download again (see Step 3: count in the test Runner)
    assert _counts(db_session)["statement_revision"] == 1  # unchanged, successful file is a no-op
    run_status = db_session.execute(text("select status, summary from ingest_run order by id")).all()
    assert [r[0] for r in run_status] == ["done", "done"]
    assert run_status[0][1]["versions"]["parser_version"].startswith("claude-cli-2.1.296-")
    assert "sweep" in run_status[0][1] and (cfg.state_dir / "gate.json").exists()
    assert not (cfg.state_dir / "run.json").exists()
    sources = db_session.execute(text("select last_seen_at from statement_source")).scalars().all()
    assert len(sources) == 1  # re-registered every listing (ruling 10): last_seen_at moved on the second run


def test_rename_in_drive_updates_path_history_without_a_download(world, db_session):
    services, drive_files, cfg = world
    pipeline.run(services, trigger="owner_cli")
    path, data = drive_files["id-1"]
    drive_files["id-1"] = (path.replace("2026-09_", "2026-09_renamed_"), data)
    second = pipeline.run(services, trigger="owner_cli")
    assert services.drive_downloads == 1
    hist = db_session.execute(text("select drive_path, path_history from statement_source")).one()
    assert "renamed" in hist[0] and hist[1] and hist[1][0]["path"] == path


def test_wrong_password_then_credential_change_retries(world, db_session, tmp_path):
    services, _, cfg = world
    cfg.password_file.write_text(PW_FILE.replace("$ID", "nope"), encoding="utf-8")
    services = pipeline.build(cfg, services.api.client, services.runner)
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["failed"] == 1
    row = db_session.execute(text("select status, failure, credential_version from statement_file")).one()
    assert row[:2] == ("failed", "password")
    assert pipeline.run(services, trigger="owner_cli")["unchanged"] == 1  # same credential version: not retried
    cfg.password_file.write_text(PW_FILE, encoding="utf-8")
    services = pipeline.build(cfg, services.api.client, services.runner)
    assert pipeline.run(services, trigger="owner_cli")["parsed"] == 1


def test_unmapped_folder_is_ignored_and_remapping_requeues(world, db_session, client, card):
    services, drive_files, cfg = world
    client.put("/settings/reconciliation", headers={"Authorization": "Bearer spa-token"},
               json={"account_map": {}, "dirty_enabled": False, "rules": {}})
    assert pipeline.run(services, trigger="owner_cli")["ignored"] == 1
    assert db_session.execute(text("select failure from statement_file")).scalar_one() == "mapping"
    client.put("/settings/reconciliation", headers={"Authorization": "Bearer spa-token"},
               json={"account_map": {"mail/信用卡/國泰世華": card.id}, "dirty_enabled": False, "rules": {}})
    assert pipeline.run(services, trigger="owner_cli")["parsed"] == 1


def test_sandbox_violation_latches_and_fails_the_run(world, db_session, tmp_path):
    services, drive_files, cfg = world
    services.gate.ensure(services.parser)  # a valid cached gate, so the violation happens on real input
    fake_claude.write(tmp_path / "claude", fake_claude.transcript(extra_tool=True))
    drive_files["id-2"] = ("信用卡/國泰世華/2026-08_國泰世華.pdf", encrypt(make_pdf([statement_lines(30)]), "A123456789"))
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["failed"] == 1 and summary["skipped"] == 1 and "parser_disabled" in summary["errors"]
    assert sorted(_file_rows(db_session)) == [("failed", "sandbox"), ("new", None)]
    assert db_session.execute(text("select status from ingest_run")).scalar_one() == "failed"
    assert (cfg.state_dir / "parser-disabled.json").exists()
    fake_claude.write(tmp_path / "claude", fake_claude.transcript())
    again = pipeline.run(services, trigger="owner_cli")  # still latched: nothing parsed, run failed
    assert again["parsed"] == 0 and "parser_disabled" in again["errors"]
    assert services.gate.run_operator_gate(services.parser).ok
    assert pipeline.run(services, trigger="owner_cli")["parsed"] >= 1


def test_gate_runs_before_the_first_parse(world, db_session, tmp_path):
    services, _, cfg = world
    fake_claude.write(tmp_path / "claude", fake_claude.transcript(extra_tool=True))
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["parsed"] == 0 and summary["skipped"] == 1 and "parser_disabled" in summary["errors"]
    assert _file_rows(db_session) == [("new", None)]  # acquired and registered, never parsed


def test_store_pending_is_retried_independently_of_parse(world, db_session, monkeypatch):
    services, _, cfg = world
    calls = {"put": 0}

    class FlakyStore:
        def exists(self, key):
            return calls["put"] > 0

        def put(self, key, path):
            calls["put"] += 1
            if calls["put"] == 1:
                raise RuntimeError("minio down")

    services.store = FlakyStore()
    first = pipeline.run(services, trigger="owner_cli")
    assert first["transient"] == 1 and _file_rows(db_session) == [("failed", "transient")]
    second = pipeline.run(services, trigger="owner_cli")
    assert calls["put"] == 2 and second["parsed"] == 1 and services.drive_downloads == 1


def test_transient_backoff_uses_the_worker_epoch(world, db_session, monkeypatch):
    services, _, cfg = world
    services.drive.download = lambda item, dest: (_ for _ in ()).throw(__import__("worker.drive", fromlist=["x"]).DownloadError("x"))
    for _ in range(2):
        pipeline.run(services, trigger="owner_cli")
    # pre-registration failures: no row, no durable backoff (documented); now a registered transient failure:
    services.drive.download = services.drive.__class__.download.__get__(services.drive)
    monkeypatch.setattr(services.store, "put", lambda key, path: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(services.store, "exists", lambda key: False)
    times = []
    for n in range(6):
        summary = pipeline.run(services, trigger="owner_cli")
        row = db_session.execute(text("select next_retry_at from statement_file")).scalar_one()
        times.append(row)
        monkeypatch.setattr(pipeline, "_now", lambda r=row: r + __import__("datetime").timedelta(seconds=1))
    retry = services.retries.get(db_session.execute(text("select sha256 from statement_file")).scalar_one())
    assert retry["transient_attempts"] == 5 and summary["unchanged"] == 0 and summary["deferred"] == 1
    deltas = [(times[1] - times[0]), (times[2] - times[1])]
    assert deltas[0] >= __import__("datetime").timedelta(hours=5, minutes=59) and deltas[1] >= __import__("datetime").timedelta(hours=23, minutes=59)


def test_md5_mismatch_is_transient(world, db_session, tmp_path):
    services, drive_files, cfg = world
    real = services.drive.download

    def corrupt(item, dest):
        real(item, dest)
        dest.write_bytes(b"%PDF-1.4 corrupted")

    services.drive.download = corrupt
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["failed"] == 0 and summary["errors"] and _counts(db_session)["statement_file"] == 0
    assert summary["transient"] == 1


def test_lease_lost_during_parse_blocks_the_submission(world, db_session, monkeypatch):
    services, _, cfg = world
    original = services.parser.parse
    sent = []

    def parse_then_lose(text_):
        out = original(text_)
        services.keeper.lost = True  # the renew thread reported a loss while the parse was in flight
        return out

    monkeypatch.setattr(services.parser, "parse", parse_then_lose)
    real_submit = services.api.submit_revision
    monkeypatch.setattr(services.api, "submit_revision", lambda lease, body: sent.append(1) or real_submit(lease, body))
    with pytest.raises(pipeline.RunAborted):
        pipeline.run(services, trigger="owner_cli")
    assert sent == [] and db_session.execute(text("select count(*) from statement_revision")).scalar_one() == 0
    assert db_session.execute(text("select status from statement_file")).scalar_one() != "parsed"


def test_crash_after_claim_resumes_the_same_run(world, db_session, monkeypatch):
    services, _, cfg = world
    monkeypatch.setattr(services.drive, "list_pdfs", lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        pipeline.run(services, trigger="owner_cli")
    assert (cfg.state_dir / "run.json").exists()
    monkeypatch.undo()
    summary = pipeline.run(services, trigger="owner_cli")
    assert summary["resumed"] is True and summary["parsed"] == 1
    assert db_session.execute(text("select count(*) from ingest_run")).scalar_one() == 1
    assert not (cfg.state_dir / "run.json").exists()


def test_backfill_needs_acknowledgement(world):
    services, _, _ = world
    with pytest.raises(pipeline.Refused):
        pipeline.run(services, trigger="owner_cli", mode="backfill")


def test_singleton(world, tmp_path):
    services, _, cfg = world
    with pipeline.singleton(cfg):
        with pytest.raises(pipeline.AlreadyRunning):
            with pipeline.singleton(cfg):
                pass
    assert oct(cfg.state_dir.stat().st_mode)[-3:] == "700"


def test_summary_carries_codes_not_text(world, monkeypatch):
    services, _, cfg = world
    from worker import drive as drive_mod
    monkeypatch.setattr(services.drive, "list_pdfs", lambda: (_ for _ in ()).throw(drive_mod.ListingError("rclone lsjson exit 3: /secret/path")))
    with pytest.raises(pipeline.RunAborted):
        pipeline.run(services, trigger="owner_cli")
    summary = services.api.client.get("/statements/ingest-runs", headers={"Authorization": "Bearer worker-token"}).json()[-1]["summary"]
    assert summary["errors"] == ["listing"] and "/secret" not in json.dumps(summary)
```

- [ ] **Step 2:** run → import errors.
- [ ] **Step 3: Implement** `worker/pipeline.py`:

```python
"""Run orchestration and the per-file state machine (§4.1, §4.2, §5.1–5.6, rulings 3–5, 9–10, 13)."""
from __future__ import annotations

import fcntl
import json
import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from worker import drive as drive_mod
from worker import inbox as inbox_mod
from worker import mask, parse_schema, parser as parser_mod, passwords, pdf
from worker.api import ApiClient, ApiError, Lease, LeaseKeeper, LeaseLost, RunState
from worker.config import WorkerConfig
from worker.runner import Runner

log = logging.getLogger("worker")
BACKOFF = {1: timedelta(hours=1), 2: timedelta(hours=6)}
MAX_TRANSIENT = 5
FULL_RUN_TRIGGERS = ("timer", "owner_cli")


class RunAborted(RuntimeError):
    pass


class Refused(RuntimeError):
    pass


class AlreadyRunning(RuntimeError):
    pass


class RetryLog:
    """Per-sha transient epoch and store_pending flag (0600 JSON)."""

    def __init__(self, path: Path):
        self.path, self.data = path, {}
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))

    def get(self, sha: str) -> dict:
        return dict(self.data.get(sha) or {"transient_attempts": 0, "store_pending": False})

    def set(self, sha: str, **fields) -> None:
        self.data[sha] = {**self.get(sha), **fields}
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh)
        os.replace(tmp, self.path)


@dataclass
class Services:
    cfg: WorkerConfig
    api: ApiClient
    drive: drive_mod.Drive
    inbox: inbox_mod.Inbox
    index: inbox_mod.SourceIndex
    retries: RetryLog
    store: inbox_mod.ObjectStore
    parser: parser_mod.Parser
    gate: parser_mod.Gate
    candidates: passwords.Candidates
    credential_version: str
    masker: mask.Masker
    runner: Runner
    keeper: LeaseKeeper | None = None
    parser_disabled: bool = False
    parser_version: str = ""
    cli_version: str = ""


def build(cfg: WorkerConfig, client: httpx.Client, runner: Runner) -> Services:
    inbox_mod.private_dir(cfg.state_dir)
    token = cfg.api_token_file.read_text(encoding="utf-8").strip()
    candidates, credential_version = passwords.load(cfg.password_file)
    services = Services(cfg, None, drive_mod.Drive(cfg, runner), inbox_mod.Inbox(cfg),
                        inbox_mod.SourceIndex(cfg.state_dir / "sources.json"), RetryLog(cfg.state_dir / "retries.json"),
                        inbox_mod.store_for(cfg), parser_mod.Parser(cfg, runner), parser_mod.Gate(cfg), candidates,
                        credential_version, mask.Masker(passwords.identity(cfg.password_file)), runner)
    services.api = ApiClient(client, token, guard=lambda: services.keeper is not None and services.keeper.lost)
    services.cli_version = services.parser.cli_version() or "unknown"
    services.parser_version = parse_schema.version(services.cli_version, cfg.parser_model)
    return services


@contextmanager
def singleton(cfg: WorkerConfig):
    inbox_mod.private_dir(cfg.state_dir)
    with cfg.lock_path.open("w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise AlreadyRunning(str(cfg.lock_path)) from exc
        yield


def _now() -> datetime:
    return datetime.now(timezone.utc)


def retry_due(row: dict, retry: dict, now: datetime, credential_version: str, mapping_version: str,
              parser_version: str, *, latched: bool) -> bool:
    status, failure = row["status"], row.get("failure")
    if status in ("new", "unlocked"):
        return True
    if status == "parsed":
        return False
    if failure == "transient":
        nxt = row.get("next_retry_at")
        due = nxt is None or datetime.fromisoformat(nxt) <= now
        return due and retry["transient_attempts"] < MAX_TRANSIENT
    if failure == "password":
        return row.get("credential_version") != credential_version
    if failure == "mapping":
        return row.get("mapping_version") != mapping_version
    if failure in ("parse", "guardrail", "no_text_layer", "too_large"):
        return row.get("parser_version") != parser_version
    if failure == "sandbox":
        return not latched and row.get("parser_version") != parser_version
    return False


def _folder_key(listed: drive_mod.Listed) -> str:
    parts = [p for p in drive_mod.folder_of(listed).split("/") if p][:2]
    return "/".join([listed.root, *parts])


@dataclass
class Summary:
    listed: int = 0
    new_files: int = 0
    parsed: int = 0
    needs_review: int = 0
    failed: int = 0
    ignored: int = 0
    skipped: int = 0
    unchanged: int = 0
    deferred: int = 0
    transient: int = 0
    too_large: int = 0
    cases_opened: int = 0
    live_revisions: int = 0
    resumed: bool = False
    errors: list[str] = field(default_factory=list)
    sweep: dict = field(default_factory=dict)
    versions: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _fail(services: Services, lease: Lease, file_row: dict, failure: str, summary: Summary, **versions) -> str:
    sha = file_row["sha256"]
    fields = {"status": "failed", "failure": failure, **versions}
    if failure == "transient":
        attempts = services.retries.get(sha)["transient_attempts"] + 1
        services.retries.set(sha, transient_attempts=attempts)
        fields["next_retry_at"] = (_now() + BACKOFF.get(attempts, timedelta(hours=24))).isoformat()
        summary.transient += 1
    else:
        services.retries.set(sha, transient_attempts=0)
        summary.failed += 1
    services.api.update_file(lease, file_row["id"], **fields)
    return "failed"


def _review(services: Services, lease: Lease, file_row: dict, failure: str, summary: Summary, **fields) -> str:
    services.retries.set(file_row["sha256"], transient_attempts=0)
    services.api.update_file(lease, file_row["id"], status="needs_review", failure=failure,
                             parser_version=services.parser_version, **fields)
    summary.needs_review += 1
    return "needs_review"


def process_file(services: Services, lease: Lease, file_row: dict, listed: drive_mod.Listed, data: bytes,
                 account_map: dict[str, int], mapping_version: str, summary: Summary) -> str:
    account_id = account_map.get(_folder_key(listed))
    if account_id is None:
        services.api.update_file(lease, file_row["id"], status="ignored", failure="mapping",
                                 mapping_version=mapping_version)
        summary.ignored += 1
        return "ignored"
    if services.parser_disabled:
        summary.skipped += 1
        return "skipped"
    candidates = services.candidates.get(passwords.password_key(listed.root, drive_mod.folder_of(listed)), [])
    try:
        unlocked = pdf.unlock(data, candidates)
        password = candidates[unlocked.password_index] if unlocked.password_index is not None else None
        extracted = pdf.extract(data, password)
    except pdf.PasswordError:
        return _fail(services, lease, file_row, "password", summary, credential_version=services.credential_version)
    except pdf.Malformed:
        return _fail(services, lease, file_row, "parse", summary, parser_version=services.parser_version)
    except pdf.NoTextLayer:
        return _review(services, lease, file_row, "no_text_layer", summary, has_text_layer=False)
    except pdf.TooLarge:
        return _review(services, lease, file_row, "too_large", summary)
    except pdf.ExtractTimeout:
        return _fail(services, lease, file_row, "transient", summary)
    services.api.update_file(lease, file_row["id"], status="unlocked", failure=None, has_text_layer=True,
                             text_chars=len(extracted.text), pages=extracted.pages,
                             credential_version=services.credential_version)
    masked = services.masker.render(extracted)
    if len(masked.encode("utf-8")) > parser_mod.STDIN_CAP:
        return _review(services, lease, file_row, "too_large", summary)
    try:
        parsed = services.parser.parse(masked)
    except parser_mod.ParseError as exc:
        if exc.reason in ("sandbox", "cli_version", "auth"):
            services.parser_disabled = True
            services.gate.latch(f"{exc.reason} on real input", services.gate.key(services.parser))
            if "parser_disabled" not in summary.errors:
                summary.errors.append("parser_disabled")
            return _fail(services, lease, file_row, "sandbox", summary, parser_version=services.parser_version)
        if exc.reason == "timeout":
            return _fail(services, lease, file_row, "transient", summary)
        return _fail(services, lease, file_row, "parse", summary, parser_version=services.parser_version)
    body = revision_body(parsed, file_id=file_row["id"], account_id=account_id, parser_version=services.parser_version)
    try:
        out = services.api.submit_revision(lease, body)
    except LeaseLost:
        raise
    except ApiError as exc:
        if exc.status == 422:
            return _fail(services, lease, file_row, "parse", summary, parser_version=services.parser_version)
        raise
    summary.cases_opened += len(out.get("case_ids") or [])
    summary.live_revisions += 1 if out.get("mode") == "live" else 0
    if not out.get("guardrail_ok"):
        return _review(services, lease, file_row, "guardrail", summary)
    services.retries.set(file_row["sha256"], transient_attempts=0)
    services.api.update_file(lease, file_row["id"], status="parsed", failure=None, parser_version=services.parser_version)
    summary.parsed += 1
    return "parsed"


def revision_body(parsed: parse_schema.StatementParse, *, file_id: int, account_id: int, parser_version: str) -> dict:
    lines = [line.model_dump(mode="json") for line in parsed.lines]
    return {"file_id": file_id, "account_id": account_id, "kind": parsed.kind, "parser": "claude-cli",
            "parser_version": parser_version, "currency": parsed.currency,
            "period_start": parsed.period_start.isoformat(), "period_end": parsed.period_end.isoformat(),
            "closing_date": parsed.closing_date.isoformat() if parsed.closing_date else None,
            "due_date": parsed.due_date.isoformat() if parsed.due_date else None,
            "opening_balance": parsed.opening_balance, "statement_total": parsed.statement_total,
            "minimum_payment": parsed.minimum_payment, "lines": lines, "raw": {"notes": parsed.notes}}


def _download_verified(services: Services, listed: drive_mod.Listed, summary: Summary):
    """Download by id, verify, publish; on a mismatch re-list once (the file may have been replaced).
    Returns (Published, Listed-as-verified) or None (transient, counted)."""
    staged = services.cfg.staging_dir / f"{listed.drive_file_id}.tmp"
    inbox_mod.private_dir(services.cfg.staging_dir)
    for attempt in (1, 2):
        try:
            services.drive.download(listed, staged)
            return services.inbox.publish(staged, expected_md5=listed.md5, expected_size=listed.size), listed
        except inbox_mod.VerifyError:
            if attempt == 2:
                break
            try:
                fresh = next((i for i in services.drive.list_pdfs() if i.drive_file_id == listed.drive_file_id), None)
            except drive_mod.ListingError:
                break
            if fresh is None or fresh.md5 == listed.md5:
                break
            listed = fresh
        except drive_mod.DownloadError:
            break
    summary.transient += 1
    if "download" not in summary.errors:
        summary.errors.append("download")
    return None


def _ensure_stored(services: Services, lease: Lease, file_row: dict, published_path: Path, summary: Summary) -> bool:
    key = f"by-sha/{file_row['sha256']}.pdf"
    if services.store.exists(key):
        services.retries.set(file_row["sha256"], store_pending=False)
        return True
    try:
        services.store.put(key, published_path)
    except Exception:  # noqa: BLE001 — MinIO failures are transient by the §5.1 matrix
        if "store" not in summary.errors:
            summary.errors.append("store")
        services.retries.set(file_row["sha256"], store_pending=True)
        _fail(services, lease, file_row, "transient", summary)
        return False
    services.retries.set(file_row["sha256"], store_pending=False)
    return True


def _acquire(services: Services, lease: Lease, listed: drive_mod.Listed, known: dict[str, dict], summary: Summary):
    """Ruling 10: fetch bytes only when the index does not know them; register the source every listing;
    ensure the object is stored. Returns (file_row, bytes-or-None, listed) or None when skipped this run."""
    if listed.size > inbox_mod.MAX_PDF_BYTES:
        summary.too_large += 1
        return None
    sha = services.index.get(listed.drive_file_id, listed.md5)
    path = services.cfg.inbox_dir / f"{sha}.pdf" if sha else None
    data = None
    if not (sha and sha in known and path.exists()):
        got = _download_verified(services, listed, summary)
        if got is None:
            return None
        published, listed = got
        sha, path = published.sha256, published.path
        data = path.read_bytes()
        services.index.put(listed.drive_file_id, listed.md5, sha)
    kind = "bank" if drive_mod.folder_of(listed).startswith("銀行帳戶") else "card"
    file_row = services.api.register_file(lease, sha, path.stat().st_size, kind, f"by-sha/{sha}.pdf")
    if sha not in known:
        summary.new_files += 1
        known[sha] = file_row
    services.api.register_source(lease, file_row["id"], listed.root, listed.drive_file_id, listed.path, listed.md5,
                                 listed.size)
    if not _ensure_stored(services, lease, file_row, path, summary):
        return None
    return file_row, data, listed


def _sweep(services: Services, lease: Lease, summary: Summary) -> None:
    try:
        summary.sweep = services.api.sweep(lease)
    except LeaseLost:
        raise
    except ApiError as exc:
        summary.sweep = {"error": "busy" if exc.status == 409 else "failed"}


def run(services: Services, *, trigger: str, mode: str = "live", initiator_hint: str | None = None,
        acknowledge_live_periods: bool = False, lease: Lease | None = None) -> dict:
    if mode == "backfill" and not acknowledge_live_periods:
        raise Refused("backfill may touch live periods; pass --acknowledge-live-periods")
    summary = Summary()
    state = RunState(services.cfg.state_dir / "run.json")
    if lease is None:
        saved = state.load()
        if saved is not None:
            try:
                services.api.renew(saved)
                lease, summary.resumed = saved, True
            except ApiError:
                state.clear()
        if lease is None:
            lease = services.api.create_run(trigger, mode, initiator_hint)
            state.save(lease)
    summary.versions = {"parser_version": services.parser_version, "cli_version": services.cli_version,
                        "model": services.cfg.parser_model, "credential_version": services.credential_version}
    status = "failed"
    try:
        with LeaseKeeper(services.api, lease) as keeper:
            services.keeper = keeper
            try:
                services.gate.ensure(services.parser)
            except parser_mod.ParserDisabled:
                services.parser_disabled = True
                summary.errors.append("parser_disabled")
            account_map, mapping_version = services.api.account_map()
            summary.versions["mapping_version"] = mapping_version
            try:
                listing = services.drive.list_pdfs()
            except drive_mod.ListingError as exc:
                summary.errors.append("listing")
                raise RunAborted("listing") from exc
            summary.listed = len(listing)
            known = {f["sha256"]: f for f in services.api.list_files()}
            latched = services.gate.latch_path.exists()
            for listed in listing:
                if keeper.lost:
                    raise RunAborted("lease lost")
                acquired = _acquire(services, lease, listed, known, summary)
                if acquired is None:
                    continue
                file_row, data, listed = acquired
                retry = services.retries.get(file_row["sha256"])
                if not retry_due(file_row, retry, _now(), services.credential_version, mapping_version,
                                 services.parser_version, latched=latched):
                    if file_row["status"] == "parsed":
                        summary.unchanged += 1
                    else:
                        summary.deferred += 1
                    continue
                if data is None:
                    data = (services.cfg.inbox_dir / f"{file_row['sha256']}.pdf").read_bytes()
                process_file(services, lease, file_row, listed, data, account_map, mapping_version, summary)
            services.api.mark_removed(lease, [i.drive_file_id for i in listing], allow_empty=not listing)
            if trigger in FULL_RUN_TRIGGERS:
                _sweep(services, lease, summary)
            if keeper.lost:
                raise RunAborted("lease lost")
            status = "failed" if "parser_disabled" in summary.errors else "done"
    except LeaseLost as exc:
        if "lease_lost" not in summary.errors:
            summary.errors.append("lease_lost")
        raise RunAborted("lease lost") from exc
    finally:
        services.keeper = None
        try:
            services.api.finish(lease, status, summary.as_dict())
            state.clear()
        except ApiError:
            pass
    return summary.as_dict()
```

`ApiClient.sweep(lease) -> dict` = `POST /reconciliation/sweep` with the lease body (route exists since R1b; `ingest` scope). The pipeline's `known` map and `retry_due` rely on `status`, `failure`, `next_retry_at`, `credential_version`, `mapping_version`, `parser_version` from `FileOut` (Task 6 added the three version fields). On `KeyboardInterrupt`/`SystemExit` inside `run`, the `finally` still calls `finish(...)` — change that: only `ApiError`-free normal completion or `RunAborted` finish the run; an interrupt (`BaseException` that is not `Exception`) leaves the run `claimed`/`running` with `run.json` intact so the next start resumes it (`test_crash_after_claim_resumes_the_same_run`). Implement by catching `BaseException` around the body: `except Exception` paths finish, `except BaseException: raise` without finishing.

`worker/cli.py`:

```python
"""python -m worker <command>: run | poll | backfill | gate | export-masked | verify."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys

import httpx

from worker import config, pipeline
from worker.runner import SubprocessRunner


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="worker")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run")
    p_run.add_argument("--trigger", choices=["timer", "owner_cli"], default="owner_cli")
    sub.add_parser("poll")
    p_bf = sub.add_parser("backfill")
    p_bf.add_argument("--acknowledge-live-periods", action="store_true")
    sub.add_parser("gate")
    p_em = sub.add_parser("export-masked")
    p_em.add_argument("--for-verify", action="store_true", required=True)
    p_em.add_argument("--folder", default=None)
    p_em.add_argument("--limit", type=int, default=None)
    p_v = sub.add_parser("verify")
    p_v.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = config.load()
    runner = SubprocessRunner()
    if args.cmd == "gate":
        from worker.parser import Gate, Parser

        report = Gate(cfg).run_operator_gate(Parser(cfg, runner))
        print(json.dumps(report.__dict__, ensure_ascii=False, indent=2))
        return 0 if report.ok else 1
    if args.cmd in ("export-masked", "verify"):
        from worker import verify

        try:
            with pipeline.singleton(cfg):
                if args.cmd == "export-masked":
                    print(json.dumps(verify.export_masked(cfg, runner, folder=args.folder, limit=args.limit)))
                else:
                    print(json.dumps(verify.run(cfg, runner, limit=args.limit), ensure_ascii=False))
        except pipeline.AlreadyRunning:
            print("already running")
            return 0
        except pipeline.Refused as exc:
            print(str(exc), file=sys.stderr)
            return 2
        return 0
    try:
        with pipeline.singleton(cfg), httpx.Client(base_url=cfg.api_url, timeout=60) as client:  # noqa: SIM117
            services = pipeline.build(cfg, client, runner)
            hint = os.environ.get("USER")
            if args.cmd == "run":
                summary = pipeline.run(services, trigger=args.trigger, initiator_hint=hint)
            elif args.cmd == "backfill":
                summary = pipeline.run(services, trigger="owner_cli", mode="backfill", initiator_hint=hint,
                                       acknowledge_live_periods=args.acknowledge_live_periods)
            else:
                summary = {"claimed": 0}
                for queued in services.api.queued_runs():
                    lease = services.api.claim(queued["id"])
                    summary = pipeline.run(services, trigger="enqueue", lease=lease, initiator_hint=hint)
                    summary["claimed"] = 1
            print(json.dumps(summary, ensure_ascii=False))
            return 1 if summary.get("errors") else 0
    except pipeline.AlreadyRunning:
        print("already running")
        return 0
    except pipeline.Refused as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except pipeline.RunAborted as exc:
        print(f"run aborted: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

`worker/__main__.py`: `from worker.cli import main; raise SystemExit(main())`.

- [ ] **Step 4:** run `tests/worker/test_pipeline.py -v`; `test_md5_mismatch_is_transient` expects no `statement_file` row (publish refused before registration). All 15 pass; then the whole suite.
- [ ] **Step 5:** commit `feat(worker): pipeline, retry rules, singleton, CLI`.

---

### Task 8: export-masked and the verify runner

**Files:**
- Create: `worker/verify.py`
- Test: `tests/worker/test_verify.py`

**Interfaces:**
- `verify.export_masked(cfg, runner, *, folder: str | None, limit: int | None) -> dict`: lists Drive, and for every listed PDF whose `(drive_file_id, md5)` is not in the verify `SourceIndex` (`verify_dir/sources.json`): downloads into a private staging dir, verifies md5/size (`inbox.Inbox` with `inbox_dir = verify_dir/pdf-cache` — the same verified acquisition as the run path, AGENT-95 Must 6), unlocks, extracts, masks, caps the masked text at `STDIN_CAP` (else counted `too_large`), and writes `verify_dir/masked/<sha256>.txt` (0600) **then** `verify_dir/masked/<sha256>.json` (`{"root", "folder", "name", "kind", "drive_file_id", "md5"}`) — the `.json` is written last so a crash never leaves a snapshot without its metadata; the index entry is written after both. Replacement bytes under an exported id get a new sha and a new snapshot. No inbox publish into the run state dir, no API, no MinIO. Returns counts `{"listed", "exported", "password", "no_text_layer", "too_large", "errors"}` (no names).
- `verify.run(cfg, runner, *, limit) -> dict`: requires `cfg.verify_db_url` (else `Refused("STATEMENT_VERIFY_DB_URL is required for verify")`) and opens one transaction with `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY` as its first statement (one snapshot for the whole report; the role is read-only too, ruling 11). It calls `Gate.ensure` on a verify `Parser` built with `config_dir=cfg.verify_parser_config_dir, credentials_writable=False`. For every masked snapshot: parse (cache `verify_dir/parsed/<sha>.<parser_version>.json`), map the folder to an account through `account_map_file` (JSON, same key format) or `settings_service.read_reconciliation_settings` (ruling 12), then `dry_run_match(session, account_id, parsed)`; write `verify_dir/reports/<timestamp>.json` with per-statement rows `{sha256, account_id, period_end, existing_statement_id, lines, claims, unmatched_lines, unmatched_entries, cases_by_kind, guardrail_ok, matched: bool}` and a totals block; returns the totals. An `auth` parse error stops the run with `errors: ["auth"]` (the operator refreshes the login through `gate`, never verify).
- `verify.dry_run_match(db, account_id: int, parsed: StatementParse) -> verify.DryRun(result: matching.MatchResult | None, guard: derive.GuardrailResult, existing_statement_id: int | None)`: derive lines with `derive.derive_lines(kind, [derive.LineIn(...)])`, guardrails with `derive.guardrails(kind, derive.Header(...), account.currency, derived)`; **when the guardrails fail, `result` is `None`** (the real `reconcile` never matches a failed revision — AGENT-95 Must 10). Otherwise `matching.Line` per derived line (`id = event_id = seq`), `existing = select(AccountStatement.id).where(account_id, currency, period_end)` (the statement identity), stand-in `SimpleNamespace(id=existing or 0, account_id, kind, currency, period_start, period_end, current_revision_id=None)` so an already-ingested statement's own claims are excluded exactly as on a re-reconcile, candidates via `reconciliation_service.load_candidates(db, stand_in, participating_accounts(db, account_id), window_days=rules.candidate_window_days, include_reward=<any reward line>)` (the shared loader keeps the account/currency gates), `rules = reconciliation_service.rules_for(settings_service.read_reconciliation_rules(db), period_end)`, `plan_map = reconciliation_service.plan_map(db, account_id)`, `fee_expected = lambda e: proposed_fx_fee(accounts_by_id[e.account_id], e.flow)` (`from app.services.entry_write_service import proposed_fx_fee`), `matching.match(kind, lines, entries, groups, plan_map, fee_expected, rules, parsed.currency)`.

- [ ] **Step 1: Write the failing tests** `tests/worker/test_verify.py`:

```python
import json
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.worker import fake_claude
from tests.worker.pdfgen import encrypt, make_pdf, statement_lines
from worker import config, parse_schema, verify
from worker.runner import FakeRunner, Result, SubprocessRunner


TABLES = ("statement_coverage", "reconciliation_case", "account_statement", "statement_revision", "ledger_entry",
          "coverage_dirty", "reconciliation_settings")


def _counts(db_session):
    return {t: db_session.execute(text(f"select count(*) from {t}")).scalar_one() for t in TABLES}


def test_dry_run_match_claims_an_exact_entry_without_writing(db_session, seed):
    account = seed.account("卡", is_credit=True)
    seed.entry(account, "-80", day=date(2026, 9, 4), name="COFFEE")
    seed.entry(account, "-500", day=date(2026, 9, 10), name="BOOKS")
    db_session.commit()
    before = _counts(db_session)
    parsed = parse_schema.StatementParse.model_validate(fake_claude.GOOD)
    dry = verify.dry_run_match(db_session, account.id, parsed)
    assert dry.guard.ok and len(dry.result.claims) == 2 and dry.result.cases == [] and dry.existing_statement_id is None
    db_session.rollback()
    assert _counts(db_session) == before


def test_dry_run_skips_matching_on_guardrail_failure(db_session, seed):
    account = seed.account("卡", is_credit=True)
    db_session.commit()
    parsed = parse_schema.StatementParse.model_validate({**fake_claude.GOOD, "statement_total": "999"})
    dry = verify.dry_run_match(db_session, account.id, parsed)
    assert not dry.guard.ok and dry.result is None


def test_dry_run_respects_currency_and_combined_accounts(db_session, seed):
    account = seed.account("卡", is_credit=True)
    usd_child = seed.account("USD子", currency="USD", combined_account_id=account.id)
    seed.entry(usd_child, "-80", day=date(2026, 9, 4), name="COFFEE")  # same number, wrong currency
    db_session.commit()
    dry = verify.dry_run_match(db_session, account.id, parse_schema.StatementParse.model_validate(fake_claude.GOOD))
    assert dry.result.claims == []


def test_dry_run_of_an_existing_statement_excludes_its_own_claims(client, db_session, seed, card):
    from tests.integration.test_statement_ingest_api import _claim, _revision
    seed.entry(card, "-80", day=date(2026, 9, 4), name="COFFEE")
    seed.entry(card, "-500", day=date(2026, 9, 10), name="BOOKS")
    db_session.commit()
    lease = _claim(client)
    body = {**_revision(lease, card, fake_claude.GOOD["lines"], "580"), "period_start": "2026-09-01", "period_end": "2026-09-30"}
    assert client.post("/statements/revisions", headers={"Authorization": "Bearer worker-token"}, json=body).status_code == 201
    statement_id = db_session.execute(text("select id from account_statement")).scalar_one()
    client.post(f"/accounts/{card.id}/statements/{statement_id}/reconcile", headers={"Authorization": "Bearer spa-token"})
    assert db_session.execute(text("select count(*) from statement_coverage where status='active'")).scalar_one() == 2
    dry = verify.dry_run_match(db_session, card.id, parse_schema.StatementParse.model_validate(fake_claude.GOOD))
    assert dry.existing_statement_id == statement_id and len(dry.result.claims) == 2  # not "claimed by another"


def test_export_masked_writes_snapshots_only(tmp_path):
    pdf_bytes = encrypt(make_pdf([statement_lines()]), "A123456789")

    def rclone(args, stdin):
        if args[1] == "lsjson":
            if not args[-1].endswith("/銀行"):
                return Result(0, b"[]", b"")
            import hashlib
            return Result(0, json.dumps([{"Path": "信用卡/國泰世華/2026-09.pdf", "Name": "2026-09.pdf", "Size": len(pdf_bytes),
                                          "ModTime": "x", "Hashes": {"md5": hashlib.md5(pdf_bytes).hexdigest()},
                                          "ID": "id-1", "MimeType": "application/pdf"}]).encode(), b"")
        from pathlib import Path
        Path(args[5]).write_bytes(pdf_bytes)
        return Result(0, b"", b"")

    pw = tmp_path / "pw.env"
    pw.write_text("STATEMENT_ID_NUMBER=A123456789\nSTATEMENT_BIRTH_DATE=19900101\nSTATEMENT_HOLDER_NAMES=王小明\n"
                  "信用卡/國泰世華=$ID\n", encoding="utf-8")
    cfg = config.load({"STATEMENT_STATE_DIR": str(tmp_path / "s"), "STATEMENT_VERIFY_DIR": str(tmp_path / "v"),
                       "STATEMENT_PASSWORD_FILE": str(pw)})
    out = verify.export_masked(cfg, FakeRunner({"rclone": rclone}), folder=None, limit=None)
    assert out["exported"] == 1 and out["password"] == 0
    snaps = list((tmp_path / "v" / "masked").glob("*.txt"))
    assert len(snaps) == 1 and oct(snaps[0].stat().st_mode)[-3:] == "600"
    assert oct((tmp_path / "v" / "masked").stat().st_mode)[-3:] == "700"
    assert "A123456789" not in snaps[0].read_text(encoding="utf-8")
    assert not (tmp_path / "s" / "inbox").exists()
    assert verify.export_masked(cfg, FakeRunner({"rclone": rclone}), folder=None, limit=None)["exported"] == 0
    # replacement bytes under the same Drive id are exported again
    pdf_bytes = encrypt(make_pdf([statement_lines(26)]), "A123456789")
    assert verify.export_masked(cfg, FakeRunner({"rclone": rclone}), folder=None, limit=None)["exported"] == 1
    assert len(list((tmp_path / "v" / "masked").glob("*.txt"))) == 2


def test_export_masked_refuses_mismatched_bytes(tmp_path):
    pdf_bytes = encrypt(make_pdf([statement_lines()]), "A123456789")

    def rclone(args, stdin):
        if args[1] == "lsjson":
            if not args[-1].endswith("/銀行"):
                return Result(0, b"[]", b"")
            return Result(0, json.dumps([{"Path": "信用卡/國泰世華/x.pdf", "Name": "x.pdf", "Size": len(pdf_bytes),
                                          "ModTime": "x", "Hashes": {"md5": "00" * 16}, "ID": "id-1",
                                          "MimeType": "application/pdf"}]).encode(), b"")
        from pathlib import Path
        Path(args[5]).write_bytes(pdf_bytes)
        return Result(0, b"", b"")

    pw = tmp_path / "pw.env"
    pw.write_text("STATEMENT_ID_NUMBER=A123456789\n信用卡/國泰世華=$ID\n", encoding="utf-8")
    cfg = config.load({"STATEMENT_STATE_DIR": str(tmp_path / "s"), "STATEMENT_VERIFY_DIR": str(tmp_path / "v"),
                       "STATEMENT_PASSWORD_FILE": str(pw)})
    out = verify.export_masked(cfg, FakeRunner({"rclone": rclone}), folder=None, limit=None)
    assert out["exported"] == 0 and out["errors"] == 1 and not list((tmp_path / "v" / "masked").glob("*"))


@pytest.fixture
def ro_url(pg_engine):
    """A SELECT-only role on the test database (ruling 11), dropped afterwards."""
    from sqlalchemy import create_engine
    with pg_engine.connect() as conn:
        conn.execute(text("DROP ROLE IF EXISTS verify_ro"))
        conn.execute(text("CREATE ROLE verify_ro LOGIN PASSWORD 'ro'"))
        conn.execute(text("GRANT USAGE ON SCHEMA public TO verify_ro"))
        conn.execute(text("GRANT SELECT ON ALL TABLES IN SCHEMA public TO verify_ro"))
        conn.execute(text("ALTER ROLE verify_ro SET default_transaction_read_only = on"))
        conn.commit()
    url = pg_engine.url.set(username="verify_ro", password="ro")
    yield url.render_as_string(hide_password=False)
    with pg_engine.connect() as conn:
        conn.execute(text("REASSIGN OWNED BY verify_ro TO CURRENT_USER"))
        conn.execute(text("DROP OWNED BY verify_ro"))
        conn.execute(text("DROP ROLE verify_ro"))
        conn.commit()


def test_verify_requires_the_read_only_url(tmp_path):
    cfg = config.load({"STATEMENT_VERIFY_DIR": str(tmp_path / "v")})
    with pytest.raises(Exception, match="STATEMENT_VERIFY_DB_URL"):
        verify.run(cfg, SubprocessRunner(), limit=None)


def test_verify_run_reports_aggregates_and_caches_parses(tmp_path, db_session, seed, pg_engine, ro_url, monkeypatch):
    account = seed.account("卡", is_credit=True)
    seed.entry(account, "-80", day=date(2026, 9, 4), name="COFFEE")
    db_session.commit()
    masked = tmp_path / "v" / "masked"
    masked.mkdir(parents=True)
    (masked / ("ab" * 32 + ".txt")).write_text("masked statement", encoding="utf-8")
    (masked / ("ab" * 32 + ".json")).write_text(json.dumps({"root": "mail", "folder": "信用卡/國泰世華", "name": "n",
                                                            "kind": "card", "drive_file_id": "id-1"}), encoding="utf-8")
    (tmp_path / "map.json").write_text(json.dumps({"mail/信用卡/國泰世華": account.id}), encoding="utf-8")
    cli = fake_claude.write(tmp_path / "claude", fake_claude.transcript())
    cfg = config.load({"STATEMENT_VERIFY_DIR": str(tmp_path / "v"), "STATEMENT_STATE_DIR": str(tmp_path / "s"),
                       "STATEMENT_PARSER_CLI": str(cli), "STATEMENT_PARSER_SANDBOX": "false",
                       "STATEMENT_PARSER_TIMEOUT": "5", "STATEMENT_PARSER_ATTEMPTS": "1",
                       "STATEMENT_ACCOUNT_MAP_FILE": str(tmp_path / "map.json"), "STATEMENT_VERIFY_DB_URL": ro_url})
    before = _counts(db_session)
    totals = verify.run(cfg, SubprocessRunner(), limit=None)
    assert totals["statements"] == 1 and totals["lines"] == 2 and totals["claims"] == 1 and totals["unmatched_lines"] == 1
    report = json.loads(next((tmp_path / "v" / "reports").glob("*.json")).read_text(encoding="utf-8"))
    assert report["rows"][0]["account_id"] == account.id and "merchant" not in json.dumps(report).lower()
    assert "COFFEE" not in json.dumps(report) and "BOOKS" not in json.dumps(report)
    assert len(list((tmp_path / "v" / "parsed").glob("*.json"))) == 1 and (tmp_path / "s" / "gate.json").exists()
    fake_claude.write(tmp_path / "claude", fake_claude.transcript(extra_tool=True))  # would fail if re-parsed
    assert verify.run(cfg, SubprocessRunner(), limit=None)["statements"] == 1
    assert _counts(db_session) == before


def test_verify_without_map_file_reads_settings_read_only(tmp_path, db_session, seed, ro_url, client):
    """Policies/settings exist in the DB (PUT once as the owner); verify reads them SELECT-only and writes nothing."""
    account = seed.account("卡", is_credit=True)
    db_session.commit()
    client.put("/settings/reconciliation", headers={"Authorization": "Bearer spa-token"},
               json={"account_map": {"mail/信用卡/國泰世華": account.id}, "dirty_enabled": True, "rules": {}})
    masked = tmp_path / "v" / "masked"
    masked.mkdir(parents=True)
    (masked / ("cd" * 32 + ".txt")).write_text("masked", encoding="utf-8")
    (masked / ("cd" * 32 + ".json")).write_text(json.dumps({"root": "mail", "folder": "信用卡/國泰世華", "name": "n",
                                                            "kind": "card", "drive_file_id": "id-9", "md5": "0" * 32}), encoding="utf-8")
    cli = fake_claude.write(tmp_path / "claude", fake_claude.transcript())
    cfg = config.load({"STATEMENT_VERIFY_DIR": str(tmp_path / "v"), "STATEMENT_STATE_DIR": str(tmp_path / "s"),
                       "STATEMENT_PARSER_CLI": str(cli), "STATEMENT_PARSER_SANDBOX": "false",
                       "STATEMENT_PARSER_TIMEOUT": "5", "STATEMENT_PARSER_ATTEMPTS": "1", "STATEMENT_VERIFY_DB_URL": ro_url})
    before = _counts(db_session)
    assert verify.run(cfg, SubprocessRunner(), limit=None)["statements"] == 1
    assert _counts(db_session) == before
```

- [ ] **Step 2:** run → import error.
- [ ] **Step 3: Implement** `worker/verify.py`:

```python
"""Verify mode (§5.8, rulings 6, 11, 12): masked snapshots from the worker, then parse + pure matching, read-only."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from worker import drive as drive_mod
from worker import inbox as inbox_mod
from worker import mask, parse_schema, parser as parser_mod, passwords, pdf
from worker.config import WorkerConfig
from worker.runner import Runner


class Refused(RuntimeError):
    pass


def _write_private(path: Path, data: str) -> None:
    inbox_mod.private_dir(path.parent)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = __import__("os").open(tmp, __import__("os").O_WRONLY | __import__("os").O_CREAT | __import__("os").O_TRUNC, 0o600)
    with __import__("os").fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(data)
    __import__("os").replace(tmp, path)


def export_masked(cfg: WorkerConfig, runner: Runner, *, folder: str | None, limit: int | None) -> dict:
    candidates, _ = passwords.load(cfg.password_file)
    masker = mask.Masker(passwords.identity(cfg.password_file))
    drv = drive_mod.Drive(cfg, runner)
    verify_cfg = __import__("dataclasses").replace(cfg, state_dir=cfg.verify_dir)  # inbox under verify_dir/inbox, staging under verify_dir/staging
    box, index = inbox_mod.Inbox(verify_cfg), inbox_mod.SourceIndex(cfg.verify_dir / "sources.json")
    out = {"listed": 0, "exported": 0, "password": 0, "no_text_layer": 0, "too_large": 0, "errors": 0}
    masked_dir = inbox_mod.private_dir(cfg.verify_dir / "masked")
    listing = [i for i in drv.list_pdfs() if folder is None or drive_mod.folder_of(i).startswith(folder)]
    out["listed"] = len(listing)
    for item in listing:
        if limit is not None and out["exported"] >= limit:
            break
        if index.get(item.drive_file_id, item.md5):
            continue
        if item.size > inbox_mod.MAX_PDF_BYTES:
            out["too_large"] += 1
            continue
        staged = inbox_mod.private_dir(verify_cfg.staging_dir) / f"{item.drive_file_id}.tmp"
        try:
            drv.download(item, staged)
            published = box.publish(staged, expected_md5=item.md5, expected_size=item.size)
        except (drive_mod.DownloadError, inbox_mod.VerifyError, inbox_mod.CollisionError):
            out["errors"] += 1
            continue
        data = published.path.read_bytes()
        key = passwords.password_key(item.root, drive_mod.folder_of(item))
        try:
            unlocked = pdf.unlock(data, candidates.get(key, []))
            password = candidates[key][unlocked.password_index] if unlocked.password_index is not None else None
            extracted = pdf.extract(data, password)
        except pdf.PasswordError:
            out["password"] += 1
            continue
        except pdf.NoTextLayer:
            out["no_text_layer"] += 1
            continue
        except (pdf.TooLarge, pdf.ExtractTimeout, pdf.Malformed):
            out["too_large"] += 1
            continue
        rendered = masker.render(extracted)
        if len(rendered.encode("utf-8")) > parser_mod.STDIN_CAP:
            out["too_large"] += 1
            continue
        kind = "bank" if drive_mod.folder_of(item).startswith("銀行帳戶") else "card"
        _write_private(masked_dir / f"{published.sha256}.txt", rendered)
        _write_private(masked_dir / f"{published.sha256}.json", json.dumps(
            {"root": item.root, "folder": drive_mod.folder_of(item), "name": item.name, "kind": kind,
             "drive_file_id": item.drive_file_id, "md5": item.md5}, ensure_ascii=False))
        index.put(item.drive_file_id, item.md5, published.sha256)
        out["exported"] += 1
    return out


def _account_map(cfg: WorkerConfig, db: Session) -> dict[str, int]:
    if cfg.account_map_file is not None:
        return {k: int(v) for k, v in json.loads(cfg.account_map_file.read_text(encoding="utf-8")).items()}
    from app.services import settings_service

    return settings_service.read_reconciliation_settings(db).get("account_map") or {}


@dataclass
class DryRun:
    result: object | None  # matching.MatchResult
    guard: object  # derive.GuardrailResult
    existing_statement_id: int | None


def dry_run_match(db: Session, account_id: int, parsed: parse_schema.StatementParse) -> DryRun:
    from app.models.ledger import Account
    from app.models.statements import AccountStatement
    from app.services import reconciliation_service, settings_service
    from app.services.coverage_service import participating_accounts
    from app.services.entry_write_service import proposed_fx_fee
    from app.services.statements import derive, matching

    accounts = participating_accounts(db, account_id)
    accounts_by_id = {a.id: a for a in db.execute(select(Account).where(Account.id.in_(accounts))).scalars()}
    account = accounts_by_id[account_id]
    line_ins = [derive.LineIn(seq=l.seq, txn_date=l.txn_date, posted_date=l.posted_date, merchant_raw=l.merchant_raw,
                              printed_amount=Decimal(l.printed_amount),
                              foreign_amount=Decimal(l.foreign_amount) if l.foreign_amount else None,
                              foreign_currency=l.foreign_currency, line_kind=l.line_kind,
                              installment_seq=l.installment_seq, installment_total=l.installment_total,
                              is_subtotal=l.is_subtotal) for l in parsed.lines if not l.is_subtotal]
    derived = derive.derive_lines(parsed.kind, line_ins)
    header = derive.Header(parsed.period_start, parsed.period_end, parsed.closing_date, parsed.due_date,
                           Decimal(parsed.opening_balance) if parsed.opening_balance else None,
                           Decimal(parsed.statement_total),
                           Decimal(parsed.minimum_payment) if parsed.minimum_payment else None, parsed.currency)
    guard = derive.guardrails(parsed.kind, header, account.currency, derived)
    existing = db.execute(select(AccountStatement.id).where(
        AccountStatement.account_id == account_id, AccountStatement.currency == parsed.currency,
        AccountStatement.period_end == parsed.period_end)).scalar_one_or_none()
    if not guard.ok:
        return DryRun(None, guard, existing)
    lines = [matching.Line(id=d.seq, event_id=d.seq, posted_date=d.line.posted_date, txn_date=d.line.txn_date,
                           flow=d.flow_amount, foreign_amount=d.line.foreign_amount,
                           foreign_currency=d.line.foreign_currency, line_kind=d.line.line_kind,
                           merchant_norm=d.merchant_norm, installment_seq=d.line.installment_seq,
                           installment_total=d.line.installment_total) for d in derived]
    rules = reconciliation_service.rules_for(settings_service.read_reconciliation_rules(db), parsed.period_end)
    stand_in = SimpleNamespace(id=existing or 0, account_id=account_id, period_start=parsed.period_start,
                               period_end=parsed.period_end, current_revision_id=None, kind=parsed.kind,
                               currency=parsed.currency)
    entries, groups = reconciliation_service.load_candidates(
        db, stand_in, accounts, window_days=rules.candidate_window_days,
        include_reward=any(l.line_kind == "reward" for l in lines))
    result = matching.match(parsed.kind, lines, entries, groups, reconciliation_service.plan_map(db, account_id),
                            lambda e: proposed_fx_fee(accounts_by_id[e.account_id], e.flow), rules, parsed.currency)
    return DryRun(result, guard, existing)


def run(cfg: WorkerConfig, runner: Runner, *, limit: int | None) -> dict:
    if not cfg.verify_db_url:
        raise Refused("STATEMENT_VERIFY_DB_URL is required for verify (read-only role, ruling 11)")
    prs = parser_mod.Parser(cfg, runner, config_dir=cfg.verify_parser_config_dir, credentials_writable=False)
    parser_mod.Gate(cfg).ensure(prs)  # raises ParserDisabled (ruling 9)
    version = parse_schema.version(prs.cli_version() or "unknown", cfg.parser_model)
    masked_dir, parsed_dir = cfg.verify_dir / "masked", inbox_mod.private_dir(cfg.verify_dir / "parsed")
    reports = inbox_mod.private_dir(cfg.verify_dir / "reports")
    engine = create_engine(cfg.verify_db_url)
    rows: list[dict] = []
    totals = {"statements": 0, "lines": 0, "claims": 0, "unmatched_lines": 0, "unmatched_entries": 0,
              "guardrail_failed": 0, "parse_failed": 0, "unmapped": 0, "existing": 0, "cases": {}, "errors": []}
    with Session(engine) as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        account_map = _account_map(cfg, db)
        for meta_path in sorted(masked_dir.glob("*.json")):
            if limit is not None and totals["statements"] + totals["parse_failed"] + totals["unmapped"] >= limit:
                break
            sha = meta_path.stem
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            key = "/".join([meta["root"], *[p for p in meta["folder"].split("/") if p][:2]])
            account_id = account_map.get(key)
            if account_id is None:
                totals["unmapped"] += 1
                continue
            cache = parsed_dir / f"{sha}.{version}.json"
            if cache.exists():
                parsed = parse_schema.StatementParse.model_validate_json(cache.read_text(encoding="utf-8"))
            else:
                try:
                    parsed = prs.parse((masked_dir / f"{sha}.txt").read_text(encoding="utf-8"))
                except parser_mod.ParseError as exc:
                    totals["parse_failed"] += 1
                    rows.append({"sha256": sha, "account_id": account_id, "error": exc.reason})
                    if exc.reason in ("sandbox", "cli_version", "auth"):
                        totals["errors"].append(exc.reason)
                        break
                    continue
                _write_private(cache, parsed.model_dump_json())
            dry = dry_run_match(db, account_id, parsed)
            row = {"sha256": sha, "account_id": account_id, "period_end": parsed.period_end.isoformat(),
                   "existing_statement_id": dry.existing_statement_id, "lines": len(parsed.lines),
                   "guardrail_ok": dry.guard.ok, "matched": dry.result is not None}
            totals["statements"] += 1
            totals["lines"] += row["lines"]
            totals["existing"] += 1 if dry.existing_statement_id else 0
            if dry.result is None:
                totals["guardrail_failed"] += 1
                row.update(claims=0, unmatched_lines=None, unmatched_entries=None, cases_by_kind={})
            else:
                cases: dict[str, int] = {}
                for case in dry.result.cases:
                    cases[case.kind] = cases.get(case.kind, 0) + 1
                    totals["cases"][case.kind] = totals["cases"].get(case.kind, 0) + 1
                claimed = {c.line_id for c in dry.result.claims}
                row.update(claims=len(dry.result.claims),
                           unmatched_lines=len([l for l in parsed.lines if not l.is_subtotal and l.seq not in claimed]),
                           unmatched_entries=len(dry.result.unmatched_entry_ids), cases_by_kind=cases)
                totals["claims"] += row["claims"]
                totals["unmatched_lines"] += row["unmatched_lines"]
                totals["unmatched_entries"] += row["unmatched_entries"]
            rows.append(row)
        db.rollback()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    _write_private(reports / f"{stamp}.json", json.dumps({"parser_version": version, "totals": totals, "rows": rows},
                                                         ensure_ascii=False, indent=1))
    return totals
```

`Claim.line_id` and `Case.kind` are the real field names (checked at a4a9207). `dataclasses.replace(cfg, state_dir=cfg.verify_dir)` gives verify its own inbox/staging under `verify_dir` without touching the run state dir. Check `Claim`'s field for the line id in `matching.py:122` (`line_id` or `line`) and `Case.kind` (`matching.py:128`) and 
- [ ] **Step 4:** run → 9 passed; then the whole suite. The `ro_url` fixture needs the test role to be creatable by the test connection (the suite's Postgres user is a superuser on `stonk-postgres-1`; if not, skip the two `ro_url` tests with a clear reason and keep `test_verify_requires_the_read_only_url`).
- [ ] **Step 5:** commit `feat(worker): export-masked snapshots and the read-only verify runner`.

---

### Task 9: Ops files, docs, spec notes, openspec

**Files:**
- Create: `services/accounting-service/deploy/statements/homehub-statements-poll.service`, `homehub-statements-poll.timer`, `homehub-statements.service`, `homehub-statements-daily.timer`, `gate.sh`, `README.md`
- Modify: `services/accounting-service/README.md` (worker section), `docs/superpowers/specs/2026-10-08-statement-reconciliation-design.md` (§17 R2 notes), create `openspec/changes/add-statement-reconciliation-r2/{proposal.md,tasks.md,.openspec.yaml,specs/accounting-reconciliation/spec.md}`

- [ ] **Step 1:** systemd **user** units (interim principal `opc`; the same files move to `homehub-worker` later). Two services, two timers (AGENT-95 Must 8):

`homehub-statements-poll.service` (every 5 min: claim enqueued runs only):
```ini
[Unit]
Description=HomeHub statement worker: claim enqueued runs
[Service]
Type=oneshot
WorkingDirectory=%h/workspace/home-hub/services/accounting-service
EnvironmentFile=-%h/.config/homehub-statements.env
UMask=0077
ExecStart=%h/workspace/home-hub/services/accounting-service/.venv/bin/python -m worker poll
Nice=10
```
`homehub-statements-poll.timer`: `OnBootSec=5min`, `OnUnitActiveSec=5min`, `Persistent=false`, `[Install] WantedBy=timers.target`.

`homehub-statements.service` (daily full run; includes the sweep, ruling 13):
```ini
[Unit]
Description=HomeHub statement worker: daily full run
[Service]
Type=oneshot
WorkingDirectory=%h/workspace/home-hub/services/accounting-service
EnvironmentFile=-%h/.config/homehub-statements.env
UMask=0077
ExecStart=%h/workspace/home-hub/services/accounting-service/.venv/bin/python -m worker run --trigger timer
Nice=10
```
`homehub-statements-daily.timer`: `OnCalendar=*-*-* 03:30:00 Asia/Taipei`, `Persistent=true`, `[Install] WantedBy=timers.target`.

`deploy/statements/README.md`: enable with `systemctl --user enable --now homehub-statements-poll.timer homehub-statements-daily.timer` (linger already on for `opc`); `already running` exits 0 so overlapping ticks never fail the unit; the read-only DB role creation (ruling 11, exact SQL), the verify parser login copy (`STATEMENT_VERIFY_PARSER_CONFIG_DIR`, chmod 500 dir / 400 files, refreshed by copying after a successful operator `gate`), and the §3 checklist for the dedicated users.

`gate.sh` (operator evidence, §5.5/§16 C7; runs as the worker principal, prints one JSON block, exits non-zero on any failed check):
```bash
#!/bin/sh
# usage: deploy/statements/gate.sh  (env: STATEMENT_* as for the worker)
set -eu
cd "$(dirname "$0")/../.."
PY=.venv/bin/python
CFG_DIR="${STATEMENT_PARSER_CONFIG_DIR:-$HOME/.local/state/home-hub-parser/claude}"
STAMP=$(mktemp)
sleep 1
# 1. the worker's own gate (ruling 9) — writes gate.json / clears or sets the latch
GATE_JSON=$($PY -m worker gate) || { echo "$GATE_JSON"; exit 1; }
# 2. nothing but the credentials file changed in the login dir during the canary
CHANGED=$(find "$CFG_DIR" -newer "$STAMP" -type f ! -name .credentials.json | wc -l)
# 3. the sandbox cannot read the host: /etc/passwd and $HOME are absent inside
CMD=$($PY - <<'EOF'
from worker import config, parser
cfg = config.load()
print(" ".join(parser.command(cfg, "{}")[:parser.command(cfg, "{}").index("--")]))
EOF
)
if $CMD -- /usr/bin/cat /etc/passwd >/dev/null 2>&1; then PASSWD=readable; else PASSWD=unreadable; fi
if $CMD -- /usr/bin/ls "$HOME" >/dev/null 2>&1; then HOMEDIR=readable; else HOMEDIR=unreadable; fi
# 4. no stray child processes after the canary (the CLI's own children die with the session)
STRAY=$(pgrep -u "$(id -u)" -f "/opt/claude/claude" | wc -l)
printf '{"gate": %s, "login_dir_changed_files": %s, "etc_passwd": "%s", "home": "%s", "stray_parser_processes": %s}\n' \
  "$GATE_JSON" "$CHANGED" "$PASSWD" "$HOMEDIR" "$STRAY"
[ "$CHANGED" = 0 ] && [ "$PASSWD" = unreadable ] && [ "$HOMEDIR" = unreadable ] && [ "$STRAY" = 0 ]
```

- [ ] **Step 2:** README section "Statement ingest worker (R2)": env table (Task 2), commands, the interim-principal note (ruling 3), the parser config dir setup (`mkdir -p ~/.local/state/home-hub-parser/claude && cp ~/.claude/.credentials.json ~/.local/state/home-hub-parser/claude/ && chmod 700 … && chmod 600 …/.credentials.json`), token file (`ACCOUNTING_API_TOKENS` worker label value, mode 600), `verify` flow (`export-masked --for-verify` then `verify`), report location, and the §3 checklist for the dedicated users.
- [ ] **Step 3:** spec §17, new sub-heading "R2 implementation notes (2026-10-xx, PR 'R2')" with rulings 1–13 verbatim, the stream-json evidence (tools `['StructuredOutput']`, `num_turns == 2`), and the explicit statement that ruling 3/11 is an accepted interim deviation from §3, not compliance.
- [ ] **Step 4:** openspec change `add-statement-reconciliation-r2` in the R1a/R1b style: requirements "Drive acquisition" (complete listing, download by id, verified atomic publish, source re-registered every listing, removed only after a complete listing), "Unlock and masking" (candidate order, index not persisted, canary classes incl. CJK-adjacent), "Parser sandbox and gate" (ruling 2 shape, bounds, mandatory gate, latch until operator gate), "Worker runs under a lease" (renew, guard before writes, resume after crash, singleton, retry table with the worker epoch), "Account map route and read-only settings reader", "Verify mode writes nothing" (read-only role required, guardrail failure skips matching, existing statement identity); 2–3 scenarios each mirroring the tests. `openspec validate add-statement-reconciliation-r2` passes.
- [ ] **Step 5:** commit `docs(worker): units, gate script, README, spec §17 R2 notes, openspec delta`.

---

### Task 10: Whole-branch verification and PR

- [ ] **Step 1:** `.venv/bin/pytest -q` → 1378 + the new worker tests (≈ 85) pass; `openspec validate --all` clean; `alembic heads` unchanged (`f3b1d2c4a9e7`).
- [ ] **Step 2:** `coderabbit review --agent --base main -t committed`; fix Critical/Warning items.
- [ ] **Step 3:** operator smoke as `opc` (no production writes), **only after the owner creates the read-only role (ruling 11) and confirms the interim principal**: `deploy/statements/gate.sh` with `STATEMENT_PARSER_SANDBOX=true` against the real CLI and the copied credentials → JSON block with `gate.ok true`, `login_dir_changed_files 0`, `etc_passwd unreadable`, `home unreadable`, `stray_parser_processes 0` (pasted into the PR as is — no secrets in it); `python -m worker export-masked --for-verify --limit 2` then `python -m worker verify --limit 2` with a two-entry `STATEMENT_ACCOUNT_MAP_FILE` and `STATEMENT_VERIFY_DB_URL` on the read-only role → a report file exists, totals printed (aggregates only), and every table's `count(*)` unchanged (checked with the same `TABLES` list as the test).
- [ ] **Step 4:** final whole-branch review (most capable model), one fix round, scoped re-review.
- [ ] **Step 5:** push `feat/reconciliation-r2`, PR "feat(accounting): statement ingest worker R2 — Drive acquisition, sandboxed parser, verify mode" with: scope, rulings 1–13, the interim-principal deploy notes and the §3 checklist, no migration, no env change for the API (one new ingest-scope route behind the flag; `GET /settings/reconciliation` now SELECT-only), verification counts and the gate evidence block; Multica non-author review issue for lead-claude.
