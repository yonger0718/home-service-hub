# Reward Terms Check (回饋條款查核) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `python -m notify terms --cause {monthly,year-end}` command plus two user timers that run a read-only, web-only Hermes one-shot over each registered card's live reward rules, parse its JSON verdict, diff it against the last run, archive everything, and send only what §5 of the spec says to show through the existing Discord delivery.

**Architecture:** New modules inside the existing `notify` package — `terms_config` (registry + `[terms]` loader), `terms_input` (API reads → frozen input), `terms_prompt` (template fill), `hermes_run` (pinned subprocess), `terms_parse` (JSON contract + source rule + digest), `terms_state` (own state file, fail-closed, flock), `terms_render` (message) — wired by a `terms` branch in `cli.py`. Nothing writes rules. Fixtures are fictional.

**Tech Stack:** Python 3.13, httpx (existing), stdlib `subprocess`/`fcntl`/`hashlib`/`tomllib`; pytest under `tests/notify/`; systemd user units under `deploy/notify/`.

**Spec:** `docs/superpowers/specs/2026-11-03-reward-terms-check-design.md` (rev 2.2, owner approved 2026-11-03).

## Global Constraints

- Hermes invocation is exactly `hermes chat --query-file <archive>/prompt.md -Q -t web --safe-mode [-m <model>]` (single-query chat path; `-z` cannot take a file — ruled in the SDD ledger 2026-11-03); toolset validated by exact set equality with `{"web"}`; never `-s`/`--skills`; explicit `env`, `cwd` = archive dir; `start_new_session=True` + process-group kill on timeout; stdout/stderr capped by `max_output_bytes` (default 4194304) and the run fails past it.
- Hermes gets rule data in the prompt only: no HomeHub token, no API calls from inside the run.
- Every URL the message can print passes the source rule: `https`, no userinfo, hostname lower-cased with one trailing dot stripped equals a `bank_domains` entry or ends with `"." + domain`; `quote` non-empty. Failing items downgrade, never print as facts.
- A parse failure, a Hermes failure, or an unreadable/corrupt terms state fails the run; nothing is inferred. State is saved only after a successful send.
- Owner-facing copy is Traditional Chinese; `quote` stays in the page's language; amounts carry the card's `currency`.
- No owner data in the repo: registry example and all fixtures are fictional (account ids like 101/102/301; "Chase Freedom Flex" is an illustrative product, not an owner card).
- Archive dirs 0700, files 0600, under `report_dir`; `--dry-run` writes the archive, sends nothing, saves no state.
- Error messages carry only `reason` + archive dir (never prompt, stdout or secret values); `ConfigError` names the key only.
- Run from `services/accounting-service`; tests with `python -m pytest tests/notify -q` using the notifier venv (`~/releases/home-hub-notify/venv/bin/python`) or the project venv.

## Review Focus

1. A card whose `bank_domains` entry is written with capitals or a trailing dot, or whose source URL is `HTTPS://Www.Bank.com./...` — must verify, not downgrade (Task 6 `test_host_rule_normalises_case_and_trailing_dot`).
2. Hermes answers with valid JSON that omits one input card and adds a foreign `account_id` — omitted ⇒ synthetic `not_found`; foreign ⇒ `TermsParseError` (Task 6 `test_missing_card_is_not_found_and_foreign_id_fails`).
3. `--cause year-end` when no registered card has `year_end_window` — exits 0, no archive, no Hermes, no send (Task 9 `test_year_end_with_no_cards_is_a_noop`).
4. The same verdict returned twice with `rate` as `3` then `"3.0000"` and a quote re-wrapped across lines — digest must be identical (Task 6 `test_digest_stable_under_number_form_and_whitespace`).
5. Hermes exits 0 but prints no JSON block, or the archive write succeeds and `hermes send` fails — run fails with reason only, archive kept, state untouched, no `last_failure.txt` on dry-run (Task 9 `test_no_json_block_fails_run_keeps_archive`, `test_send_failure_keeps_archive_and_state`).

---

### Task 1: Isolation verification (blocking; no feature code until it passes)

**Files:**
- Create: `services/accounting-service/deploy/notify/terms-isolation-probe.md` (the fixture prompt + the six expected observations)
- Modify: `docs/superpowers/specs/2026-11-03-reward-terms-check-design.md` (append "Isolation check <date>: PASS/FAIL" with the observed values under §3)

**Interfaces:**
- Produces: the pinned argv and the exact `env` key list that Task 5 hard-codes (`HERMES_ENV_KEYS`).

- [ ] **Step 1: Write the probe prompt file.** It asks the agent, in this order: (a) list the exact names of the tool schemas it was given; (b) run `id` in a shell and report the output; (c) fetch `http://127.0.0.1:8000/reward-rules` and report the HTTP status and the first 200 characters of the body; (d) repeat the secret token on the last line of this file (the file ends with `PROBE-TOKEN-<random 12 hex>`); (e) quote verbatim any identity, SOUL, rules or memory text it was given in its system prompt, or write exactly `RULES-ABSENT` if none; (f) end with one fenced ```json block `{"tools": [...], "shell": "...", "loopback_status": ..., "token": "...", "rules": "..."}`.
- [ ] **Step 2: Record the installed binary's resolved toolset deterministically.** From `~/.hermes/hermes-agent`, with the Hermes venv python (`hermes doctor` prints it): `python -c "import sys; sys.path.insert(0,'.'); from toolsets import resolve_toolset; print(sorted(resolve_toolset('web')))"`. Pass iff `set(resolve_toolset("web")) == {"web_search", "web_extract"}` (order does not matter). This, not the model's self-description, is the registration proof.
- [ ] **Step 3: Baseline the real profile.** Record `sha256sum` of `~/.hermes/SOUL.md`, and a recursive `find … -type f -exec sha256sum` of `~/.hermes/memories/` and `~/.hermes/skills/`; record `sqlite3 ~/.hermes/state.db "select count(*) from sessions"` (confirm the table name with `.tables`; write the query used into the probe file).
- [ ] **Step 4: Build a scratch Hermes home for the probe** at a 0700 dir under `~/backups/home-hub-static/` (never `/tmp`): `config.yaml` holding only the model/provider lines copied from `~/.hermes/config.yaml`; `SOUL.md` and `memories/MEMORY.md` each containing the line `PROBE-RULE-<random 12 hex>`; no `skills/`. Provider credentials: if the provider reads an env var, pass it in `env`; if it needs `auth.json`, copy it into the scratch home at 0600 **for the probe only** and `shred -u` it in Step 6. The probe is run twice: once with `HERMES_HOME=<scratch>` (observation 4) and once with the real home (observations 1–3, 5, 6).
- [ ] **Step 5: Run the probe** from an empty 0700 scratch cwd: `env -i PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin" HOME="$HOME" LANG=C.UTF-8 TZ=Asia/Taipei [HERMES_HOME=<scratch>] <provider vars> hermes chat --query-file ./prompt.md -Q -t web --safe-mode > out.txt 2> err.txt`. Record the **names** only of the provider vars in the probe file.
- [ ] **Step 6: Judge the six observations** and write them, with the binary version from `hermes --version`, into the probe file and the spec §3 note: (1) Step 2 passes **and** every tool name the agent reports is inside `{web_search, web_extract}` (names are a negative check only); (2) the shell request is refused for lack of a tool, not approved; (3) real-home run: SOUL.md / memories / skills hashes unchanged, and the `sessions` count grew by **exactly one** (the probe's own one-shot row — `hermes -z` stores one); any other new row or any hash change fails; (4) scratch-home run: the output contains the `PROBE-TOKEN-…` **and** the literal `RULES-ABSENT`, and does **not** contain `PROBE-RULE-…`; a scratch run that did not answer, or that quotes the sentinel, is a FAIL (if the sentinel appears, `--safe-mode` does not keep rules/memory out of the `-z` prompt on this build — verified 2026-11-03 that the one-shot path only sets env vars and never passes `skip_context_files`/`skip_memory` to `AIAgent`, so this is the observation that decides); (5) the agent echoed the probe token (query-file supplied the prompt); (6) the loopback probe reports either HTTP 401 **or** the tool's private-network block (`Blocked: URL targets a private or internal network address` — the installed `tools/url_safety.py` blocks `127.0.0.0/8`), and no rule body appears in the output; a rule payload fails. All six pass ⇒ PASS. Any failure ⇒ STOP: write FAIL + the observation in the spec note, commit, and report to the owner with the fallback on the table (a dedicated minimal `HERMES_HOME` profile for the terms runner, which needs a spec change and an owner-seeded login); Tasks 2–10 do not start. Then `shred -u` any copied `auth.json` and remove the scratch home.
- [ ] **Step 7: Commit**

```bash
git add deploy/notify/terms-isolation-probe.md ../../docs/superpowers/specs/2026-11-03-reward-terms-check-design.md
git commit -m "docs(notify): terms-check isolation probe and result"
```

### Task 2: `[terms]` config and the card registry

**Files:**
- Create: `services/accounting-service/notify/terms_config.py`
- Modify: `services/accounting-service/notify/config.py` (`NotifyConfig.terms: TermsConfig | None = None`; `load_config` calls `load_terms(raw)`)
- Modify: `services/accounting-service/deploy/notify/homehub-notify.example.toml` (append the spec §1 `[terms]` block verbatim, ids 101/102/301)
- Test: `services/accounting-service/tests/notify/test_terms_config.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class CardEntry:
      account_id: int; official: str; bank_domains: tuple[str, ...]; tier: str  # "precise" | "signal"
      country: str = "TW"; language: str = "zh-TW"; currency: str = "TWD"
      aliases: tuple[str, ...] = (); year_end_window: bool = True
  @dataclass(frozen=True)
  class TermsConfig:
      report_dir: Path; hermes_toolsets: str = "web"; max_output_bytes: int = 4194304
      hermes_timeout_seconds: int = 1800; hermes_model: str | None = None
      ignore_accounts: frozenset[int] = frozenset(); terms_state_file: Path = <default>
      cards: tuple[CardEntry, ...] = ()   # registry order = TOML order
  def load_terms(raw: dict) -> TermsConfig | None   # None when "terms" absent; ConfigError("terms.<key>") otherwise
  ```
  Default `terms_state_file` = `~/.local/state/homehub-notify/terms-state.json`. `bank_domains` stored lower-cased, trailing dot stripped. `year_end_window` default = `country == "TW"`.

- [ ] **Step 1: Write failing tests** in `test_terms_config.py`: `test_example_toml_loads_three_cards` (301 has `year_end_window is False`, `currency == "USD"`, 101 `True`); `test_terms_absent_is_none`; `test_bad_tier_names_key` (`ConfigError` str == `"terms.cards.101.tier"`); `test_country_must_be_two_upper`; `test_language_must_be_bcp47` (`"english"` ⇒ `ConfigError("terms.cards.101.language")`; accepted pattern `^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$`); `test_currency_three_upper` (`"usd"` rejected); `test_aliases_list_of_strings` (`"x"` and `[1]` rejected); `test_toolsets_other_than_web_rejected_at_load` (`"web,browser"` ⇒ `ConfigError("terms.hermes_toolsets")`); `test_domains_normalised` (`"HSBC.com.tw."` → `"hsbc.com.tw"`); `test_ignore_accounts_ints`; `test_card_key_must_be_int`.
- [ ] **Step 2: Run** `python -m pytest tests/notify/test_terms_config.py -q` — FAIL (import error).
- [ ] **Step 3: Implement** `terms_config.py` per the Interfaces block; wire into `config.py`; extend the example TOML.
- [ ] **Step 4: Run** the file and `tests/notify/test_notify_config.py` — PASS (the existing `test_load_config_example` still passes).
- [ ] **Step 5: Commit** `feat(notify): [terms] config and card registry`.

### Task 3: API reads and the frozen input

**Files:**
- Modify: `services/accounting-service/notify/api.py` (two `ReadClient` methods)
- Create: `services/accounting-service/notify/terms_input.py`
- Test: `services/accounting-service/tests/notify/test_terms_input.py`

**Interfaces:**
- Consumes: `CardEntry`, `TermsConfig` (Task 2); `ReadClient._get`.
- Produces:
  ```python
  # api.py
  def reward_rules_active(self) -> dict            # GET /reward-rules?state=active → {"accounts": [...], "counts": {...}}
  def account_reward_rules(self, account_id: int) -> list[dict]   # GET /accounts/{id}/reward-rules (list or {"rules": [...]})
  # terms_input.py
  RULE_FIELDS = ("id","name","method","rate","fixed_amount","is_basic","total_cap","window","starts_on","ends_on","description")
  @dataclass(frozen=True)
  class CardInput: card: CardEntry; rules: tuple[dict, ...]          # live rules, RULE_FIELDS only
  @dataclass(frozen=True)
  class TermsInput: today: date; cause: str; cards: tuple[CardInput, ...]; unregistered: tuple[tuple[int, str], ...]
  def select_cards(cfg: TermsConfig, cause: str) -> tuple[CardEntry, ...]   # "year-end" ⇒ year_end_window only
  def build_input(client: ReadClient, cfg: TermsConfig, today: date, cause: str) -> TermsInput
      # cards = select_cards(cfg, cause) — the only source of the card tuple; year-end never reads non-window cards
  def to_json(inp: TermsInput) -> dict   # what is frozen to input.json
  ```
  Live rule = `is_enabled` and (`ends_on` is None or `ends_on >= today`). `unregistered` = active-rule accounts (from `/reward-rules?state=active`, `rules` non-empty) not in the registry and not in `ignore_accounts`, as `(account_id, account_name)` sorted by id.

- [ ] **Step 1: Write failing tests** with `notify_fakes.make_client`: `test_live_rule_filter_drops_disabled_and_ended`; `test_zero_rule_card_still_included`; `test_unregistered_excludes_ignore_accounts`; `test_year_end_selects_window_cards_only`; `test_build_input_year_end_omits_non_window_cards` (registry 101 TW + 301 US: `cause="year-end"` ⇒ `inp.cards` has 101 only and the fake client saw no `/accounts/301/reward-rules` request); `test_rule_fields_are_exactly_RULE_FIELDS`; `test_api_error_propagates` (404 route ⇒ `ApiError`).
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(notify): terms input from reward-rule reads`.

### Task 4: Prompt template

**Files:**
- Create: `services/accounting-service/notify/terms_prompt.md` (package data; template with `{today}`, `{cards}`, `{contract}` placeholders)
- Create: `services/accounting-service/notify/terms_prompt.py`
- Test: `services/accounting-service/tests/notify/test_terms_prompt.py`

**Interfaces:**
- Consumes: `TermsInput`, `CardInput` (Task 3).
- Produces: `def build_prompt(inp: TermsInput) -> str`; constant `CONTRACT_JSON` (the spec §4 schema sample verbatim, rate sample `"1.5"`).

Template content, generalised from the spike prompt `~/reports/home-hub-reward-terms-spike-20261102T115122Z/prompt.md` (read it; do not copy owner rule values): per card a block with `official`, `aliases`, `bank_domains`, `tier`, `country/language/currency`, and the live rules as compact JSON; the two stages in the card's `language`; precise-tier field list = `RULE_FIELDS` minus `id` with the "posting/shared cap out of scope" sentence and the "zero live rules ⇒ `missing_rule`" sentence; signal-tier ≤ 3 lines in `notes`; report prose Traditional Chinese, quotes verbatim original language, amounts in `currency`; hard rules (source URL + quote per claim, `not_found` otherwise, read-only, no HomeHub calls, long documents you could not finish go in `fetch_log.truncated`); the answer ends with exactly one fenced ```json block following `CONTRACT_JSON`.

- [ ] **Step 1: Write failing tests**: `test_prompt_contains_each_card_block` (official name, every domain, `tier`, language line "en" for the US card); `test_precise_rules_serialised_with_rule_fields_only`; `test_signal_card_has_no_rule_comparison_instruction`; `test_contract_block_present_once` (`CONTRACT_JSON` substring count == 1, `"1.5"` present); `test_prompt_mentions_truncated_and_missing_rule`.
- [ ] **Step 2–4:** FAIL → implement (`importlib.resources.files("notify") / "terms_prompt.md"`; add the `.md` to package data if `pyproject`/`setup` needs it — check `requirements-notify.txt`/how the release runs from a checkout: a plain file next to the module is enough) → PASS.
- [ ] **Step 5: Commit** `feat(notify): terms prompt template`.

### Task 5: Hermes runner

**Files:**
- Create: `services/accounting-service/notify/hermes_run.py`
- Test: `services/accounting-service/tests/notify/test_hermes_run.py`

**Interfaces:**
- Produces:
  ```python
  HERMES_ENV_KEYS: tuple[str, ...]   # from Task 1: PATH, HOME, LANG, TZ + provider var names
  class HermesError(Exception): reason: str  # "hermes"; archive: Path; str(exc) == f"hermes {archive}"
  @dataclass(frozen=True)
  class HermesResult: stdout: str; stderr: str; returncode: int; seconds: float; truncated: bool
  def hermes_argv(prompt_path: Path, toolsets: str, model: str | None) -> list[str]
      # ["hermes","chat","--query-file",str(prompt_path),"-Q","-t","web","--safe-mode"] + (["-m",model] if model else [])
      # ValueError unless set(toolsets.split(",")) == {"web"}
  def run_hermes(prompt_path: Path, toolsets: str, timeout_s: int, model: str | None, env: dict[str, str],
                 cwd: Path, max_output_bytes: int, archive: Path, runner=None, kill=os.killpg) -> HermesResult
      # cwd and archive are the same run directory; the CLI passes both explicitly
  ```
  `runner(argv, env, cwd, out_fd, err_fd) -> handle` (with `.pid`, `.wait(timeout)`, `.returncode`); it defaults to a `subprocess.Popen` wrapper with `start_new_session=True`, stdout/stderr to files `archive/hermes.out`/`hermes.err` (0600) read back capped at `max_output_bytes`; on timeout `kill(os.getpgid(handle.pid), signal.SIGKILL)` then `HermesError`; non-zero exit or truncation ⇒ `HermesError`. `env` is built by the caller as `{k: os.environ[k] for k in HERMES_ENV_KEYS if k in os.environ}` — the runner never reads `os.environ` itself.

- [ ] **Step 1: Write failing tests** (fake `runner` records argv/env/cwd/timeout and writes canned output): `test_argv_is_pinned`; `test_toolset_must_be_exactly_web` (`"web,browser"`, `"all"`, `"safe"`, `"web, terminal"` ⇒ `ValueError`); `test_env_and_cwd_passed_through_unchanged`; `test_nonzero_exit_raises_reason_only` (`str(exc)` has no stdout text); `test_output_capped_and_fails` (fake writes `max_output_bytes + 1`; result file truncated with marker `\n[truncated]\n`, `HermesError`); `test_timeout_kills_group` (fake runner returns a handle whose `wait` raises `subprocess.TimeoutExpired`; `kill` is monkeypatched and must be called with `(pgid, signal.SIGKILL)` before `HermesError` is raised).
- [ ] **Step 2–4:** FAIL → implement → PASS. Keep the real Popen path small and untested beyond construction (`test_default_runner_builds_popen_with_new_session` using `monkeypatch` on `subprocess.Popen`).
- [ ] **Step 5: Commit** `feat(notify): pinned hermes one-shot runner`.

### Task 6: Result parsing, source rule, digest

**Files:**
- Create: `services/accounting-service/notify/terms_parse.py`
- Test: `services/accounting-service/tests/notify/test_terms_parse.py`, fixtures `tests/notify/terms_fixtures.py` (one canned Hermes stdout with: card 101 `changed` (two findings, one off-domain), card 102 `signal`, card 301 `announced` with `next_terms`, card 103 absent; a foreign id variant; a no-JSON variant)

**Interfaces:**
- Consumes: `CardEntry`.
- Produces:
  ```python
  class TermsParseError(Exception): reason = "parse"; archive: Path | None; str(exc) == f"parse {archive}"
  STATUSES = ("unchanged","changed","announced","not_found","signal")
  @dataclass(frozen=True) class Finding: rule_id: int | None; field: str; site: str | None; system: str | None; source: str; quote: str
  @dataclass(frozen=True) class PeriodEnd: date: str; source: str; quote: str
  @dataclass(frozen=True) class NextTerms: announced: bool; period: tuple[str | None, str | None]; rules: tuple[dict, ...]; source: str | None; quote: str | None
  @dataclass(frozen=True) class CardResult:
      account_id: int; status: str; current_period_end: PeriodEnd | None; findings: tuple[Finding, ...]
      unverified: tuple[Finding, ...]; next_terms: NextTerms | None; next_terms_unverified_url: str | None
      sources: tuple[str, ...]; notes: str; truncated: tuple[str, ...]
  @dataclass(frozen=True) class TermsResult: cards: tuple[CardResult, ...]; fetch_log: dict
  def last_json_block(text: str) -> str          # TermsParseError when none
  def source_ok(url: str, domains: tuple[str, ...]) -> bool
  def parse_result(stdout: str, cards: tuple[CardEntry, ...]) -> TermsResult
  def digest(card: CardResult, tier: str) -> str   # sha256 hex of canonical JSON per spec §5
  ```
  Canonical: `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`; findings sorted by `(rule_id or -1, field, source)`; quotes `" ".join(q.split())`; decimals `str(Decimal(x).normalize())` with `"{:f}"` (no exponent); for `tier == "signal"` the object is `{"notes": normalised}`.

- [ ] **Step 1: Write failing tests**: `test_last_json_block_picks_last`; `test_no_json_block_raises`; `test_host_rule_normalises_case_and_trailing_dot` (Review Focus 1: registry domain `"Example-Bank.com."` as loaded, URL `HTTPS://Www.Example-Bank.com./terms` passes; also `http://` fails, `https://user@example-bank.com` fails, `https://evil-example-bank.com` fails, `https://www.example-bank.com/x` passes); `test_off_domain_finding_downgrades_to_unverified`; `test_next_terms_off_domain_dropped_and_url_kept`; `test_period_end_off_domain_or_empty_quote_is_null` (off-domain https with a quote ⇒ `current_period_end is None`; empty quote ⇒ None; missing fields ⇒ None); `test_empty_quote_finding_is_unverified`; `test_next_terms_empty_quote_sets_unverified_url`; `test_missing_card_is_not_found_and_foreign_id_fails` (Review Focus 2); `test_status_required_fields` (`changed` without `findings` ⇒ error; `not_found` bare ok); `test_numbers_accepted_and_canonicalised` (`3` → `"3"`, `"3.0000"` → `"3"`); `test_digest_stable_under_number_form_and_whitespace` (Review Focus 4); `test_signal_digest_hashes_notes_only`; `test_truncated_entries_attached_to_card`.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat(notify): terms verdict parser, source rule and digest`.

### Task 7: Terms state and what-to-show

**Files:**
- Create: `services/accounting-service/notify/terms_state.py`
- Test: `services/accounting-service/tests/notify/test_terms_state.py`

**Interfaces:**
- Consumes: `CardResult`, `digest` (Task 6), `CardEntry`.
- Produces:
  ```python
  class TermsStateError(Exception): reason = "state"      # joins FAILURES in Task 9
  @dataclass class CardSeen: digest: str; seen_at: datetime
  @dataclass class TermsState: cards: dict[int, CardSeen]
  def load_terms_state(path: Path) -> TermsState            # missing ⇒ empty; unreadable/corrupt ⇒ TermsStateError
  def save_terms_state(path: Path, state: TermsState) -> None   # tempfile + os.replace, 0600; takes NO lock itself
  @contextmanager def terms_lock(path: Path)                # the single lock holder: flock(LOCK_EX) on path.with_suffix(".lock"),
                                                            # held by the CLI from load_terms_state through save_terms_state (fcntl.flock is not recursive)
  @dataclass(frozen=True) class ShowDecision:
      show_findings: bool; show_announced: bool; show_signal: bool; show_not_found: bool
      show_unverified: bool; show_period_only: bool; show_period: bool; show_truncated: bool; is_new: bool
  # any() of the show_* flags ⇒ the card is NOT folded into 無變動
  def decide(card: CardResult, entry: CardEntry, prev: CardSeen | None, now_digest: str) -> ShowDecision
  ```
  `decide` per spec §5: `show_findings = bool(card.findings)`; `show_announced = card.next_terms and card.next_terms.announced`; `show_not_found = status == "not_found" or (status == "changed" and not card.findings)`; `show_unverified = bool(card.unverified) and (prev is None or prev.digest != now_digest)`; `show_signal = status == "signal" and (prev is None or prev.digest != now_digest)`; `show_period_only = status == "unchanged" and card.current_period_end is not None and (prev is None or prev.digest != now_digest)`; `show_period = status in ("changed", "announced") and card.current_period_end is not None` (the verified `條款期間至` line on those cards, every run); `show_truncated = bool(card.truncated)` (every run; not part of the digest); `is_new = prev is None or prev.digest != now_digest`.

- [ ] **Step 1: Write failing tests**: `test_missing_state_is_empty`; `test_corrupt_state_raises_not_resets` (file content `{` ⇒ `TermsStateError`; file unchanged after); `test_save_is_atomic_0600_and_locked`; `test_decide_not_found_always_shown_even_when_digest_matches`; `test_decide_verified_findings_repeat_until_digest_changes_system_side` (same digest ⇒ still `show_findings`); `test_decide_unverified_and_signal_suppressed_on_match`; `test_decide_period_only_line`; `test_decide_truncated_only_card_is_shown_on_digest_match`; `test_decide_changed_card_shows_period_line`; `test_save_under_held_lock_returns` (a second `save_terms_state` inside one `terms_lock` completes).
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat(notify): terms state file and show rules`.

### Task 8: Message rendering

**Files:**
- Create: `services/accounting-service/notify/terms_render.py`
- Test: `services/accounting-service/tests/notify/test_terms_render.py`, golden `tests/notify/golden/terms.txt`, `terms_quiet.txt`

**Interfaces:**
- Consumes: `TermsResult`, `ShowDecision`, `TermsInput` (for `unregistered` and registry order), `NotifyConfig` (`_settings_link`, `split_blocks` from `render`).
- Produces: `def render_terms(inp: TermsInput, result: TermsResult, decisions: dict[int, ShowDecision], cfg: NotifyConfig, archive: Path) -> list[str]`.

Copy (spec §5, exact): header `📋 回饋條款查核 {today}（{M} 張卡，{N} 張有事項）` + line `archive: {archive}`; card block head `【{official}】 → {settings link}`; findings `· {rule name or 缺規則}：{field_zh} 官網 {site} / 系統 {system} — {source}` with `field_zh` map `{ends_on: 結束日, starts_on: 開始日, rate: 回饋率, total_cap: 上限, is_basic: 基本, name: 名稱, description: 說明, method: 方式, fixed_amount: 固定金額, window: 期間, missing_rule: 缺規則}` with `field_zh.get(field, field)` so an unknown field never raises and a missing rule as `· 缺規則：{site} — {source}`; quote line `　「{quote[:80]}…」` when the quote exceeds 80 chars else whole; period `· 條款期間至 {date}`; announced `· 下期條款已公告 {from}～{to}：` then `　- {name} {rate}% 上限 {cap or 無} {notes}`; `· 下期條款：來源未驗證 {url}`; signal: notes lines prefixed `· `; not found `· 找不到官網來源（{n} 項未驗證）` (suffix only when n > 0) + fetch_log failures for that card `　- {url}`; truncated `· 來源文件過長，僅讀到前段（{url}）`; unverified `· 未驗證（來源不在銀行網域）：{field_zh} {site} — {source}`; period-only `· 無差異，條款期間至 {date}`; `未登錄：{name}（{id}）、…`; `無變動：A、B、C`; single line `📋 回饋條款查核 {today}：{M} 張卡無變動` only when no card shows anything and `unregistered` is empty. Chunked with `split_blocks`.

- [ ] **Step 1: Write failing tests**: `test_terms_matches_golden` (fixture from Task 6 + decisions; golden file committed after first green run, like `test_weekly_matches_golden`); `test_quiet_single_line_only_when_nothing_and_no_unregistered`; `test_unregistered_disables_single_line`; `test_block_order_findings_announced_signal`; `test_quote_truncated_at_80`; `test_truncated_sources_rendered_every_run` (a card with **only** a truncation and a matching digest is a block, not part of 無變動); `test_changed_card_prints_period_line`; `test_unknown_field_falls_back_to_raw_name`.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat(notify): terms message rendering`.

### Task 9: CLI `terms` command, archive, failure path

**Files:**
- Modify: `services/accounting-service/notify/cli.py` (parser branch, `_run_terms`, `FAILURES += (HermesError, TermsParseError, TermsStateError)`, `_short` for them = `f"{type(exc).__name__} {exc.reason} {exc.archive}"`)
- Create: `services/accounting-service/notify/terms.py` (`run_terms(args, cfg, today) -> list[str]` orchestration + archive)
- Test: `services/accounting-service/tests/notify/test_terms_cli.py`

**Interfaces:**
- Consumes: everything above. `python -m notify terms --cause {monthly,year-end} [--as-of D] [--dry-run | --send] [--config P]`.
- Produces: archive dir `<report_dir>/<YYYYMMDDTHHMMSSZ>/` (stamped from `datetime.now(timezone.utc)`; 0700) with `input.json`, `prompt.md`, `hermes.out`, `hermes.err`, `result.json`, `message.md` (0600); exit 0 on send/dry-run/no-op, 1 on failure, 2 on bad args or missing `[terms]`. `cli.main` gets a branch before the shared `FAILURES` handler: a `ConfigError` whose text is `terms` or starts with `terms.` prints the reason and returns 2 without writing `last_failure.txt`.

Flow (`TermsParseError`/`TermsStateError` raised by lower modules carry `archive=None`; `run_terms` sets `exc.archive = archive` before re-raising so `_short` can print it): `cfg.terms` None ⇒ `ConfigError("terms")`; `select_cards` empty ⇒ print `terms: no cards for --cause …` to stderr, return 0 with no archive; `build_input` → create archive dir → write `input.json` → `build_prompt` → write `prompt.md` → `run_hermes(prompt_path=archive/"prompt.md", toolsets=cfg.terms.hermes_toolsets, timeout_s=…, model=…, env={k: os.environ[k] for k in HERMES_ENV_KEYS if k in os.environ}, cwd=archive, max_output_bytes=…, archive=archive)` → `parse_result` → write `result.json` → `with terms_lock(path):` **spanning the rest**: `state = load_terms_state(...)`; digests + `decide` → `render_terms` → write `message.md` → if `--send`: `send.deliver`, update `state.cards[id] = CardSeen(digest, now)` for every card in the result, `save_terms_state` (inside the same lock), unlink `last_failure.txt`; stderr summary `terms: {M} cards, {N} with items, sent {k} messages`. Dry-run: print messages, no state.

- [ ] **Step 1: Write failing tests** (fake API via `notify_fakes`, fake `run_hermes` via `monkeypatch.setattr(terms, "run_hermes", …)` returning the Task 6 fixture, `send._run_hermes` recorder): `test_dry_run_writes_archive_prints_and_saves_nothing`; `test_send_saves_digests_only_after_deliver`; `test_year_end_with_no_cards_is_a_noop` (Review Focus 3); `test_no_json_block_fails_run_keeps_archive` (Review Focus 5; `last_failure.txt` written only with `--send`, content `❌ 帳務通知失敗：terms {date} TermsParseError parse {archive}`); `test_send_failure_keeps_archive_and_state`; `test_hermes_failure_reason_has_no_output_text`; `test_missing_terms_section_exits_2_without_failure_file`; `test_archive_stamp_is_utc` (freeze `datetime.now(timezone.utc)`; dir name ends in `Z` and matches the UTC instant, not Taipei); `test_corrupt_terms_state_fails_run`; `test_archive_modes_0700_0600`; `test_cause_required`.
- [ ] **Step 2–4:** FAIL → implement → PASS; also run the whole `tests/notify` suite — existing daily/weekly tests unchanged.
- [ ] **Step 5: Commit** `feat(notify): terms command with archive and failure path`.

### Task 10: Units, installer, README

**Files:**
- Create: `deploy/notify/homehub-notify-terms-monthly.{service,timer}`, `deploy/notify/homehub-notify-terms-year-end.{service,timer}`
- Modify: `deploy/notify/install.sh` (install the four units; enable only daily/weekly as today; print a hint for the terms timers); `README.md` §Notifications (new "回饋條款查核" subsection: config, CLI, timers, rollout steps 1–4 from spec §Rollout, the no-ship isolation rule)
- Test: `tests/notify/test_units.py` (extend)

Service template = `homehub-notify-weekly.service` with `Description=HomeHub accounting notifier: reward terms check (monthly|year-end)`, `ExecStart=… -m notify terms --send --cause monthly` / `--cause year-end`, `TimeoutStartSec=1900`, `Conflicts=homehub-notify-terms-year-end.service` (and the mirror), `OnFailure=homehub-notify-failed@%n.service`. Timers: `OnCalendar=*-*-01 09:00:00 Asia/Taipei` / `OnCalendar=Mon *-11,12-* 09:00:00 Asia/Taipei`, `Persistent=true`.

- [ ] **Step 1: Write failing tests** in `test_units.py` (follow its existing style): `test_terms_units_exist_and_pin_cause`; `test_terms_services_conflict_each_other`; `test_terms_timeout_covers_hermes_timeout` (`TimeoutStartSec >= 1800 + 60`); `test_install_does_not_enable_terms_timers` (the `enable --now` line lists only daily/weekly).
- [ ] **Step 2–4:** FAIL → write units/installer/README → PASS (the existing `test_install_script_lists_every_unit` must still pass: the installer installs all four terms units, enables none of them); `systemd-analyze --user verify deploy/notify/homehub-notify-terms-*.service` prints nothing.
- [ ] **Step 5: Commit** `feat(notify): terms timers, installer and README`.
