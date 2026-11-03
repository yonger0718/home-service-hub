# Reward terms check (回饋條款查核) — design

Date: 2026-11-03. Rev 2.2 (folds Multica AGENT-390 review + re-check minors). Status: approved by owner 2026-11-03. Follows the reward-rules phase 1 design
(`2026-10-31-reward-rules-design.md`) and the notifier design (`openspec/.../accounting-notifications`).

## Intent

Credit-card reward terms change once or twice a year (new-year programs, extensions, cap
changes). Today the owner finds out by accident: on 2026-11-02 a hand pass found 30 rules that
had expired while still enabled, one card with no rules at all, and — once a one-off Hermes
run read the banks' own pages — three HSBC rules with the wrong end date and a missing rule.

Scanning HomeHub's own rule table does not help with this: the table only knows what the
owner typed. The information that matters lives on the banks' sites. The 2026-11-02 spike
(record `~/reports/home-hub-reward-terms-spike-20261102T115122Z/`) showed Hermes can read
those pages directly (DBS, HSBC, Cathay: card pages and PDFs, no browser needed, no 403s),
quote them, and compare them against the rule rows — 3 cards in 3.5 minutes, 15 tool calls.

This feature makes that run periodic, deterministic at the edges, archived, and safe.

## Goals

- Once a month (weekly in the new-year window) compare each maintained card's **current
  rules in HomeHub** against the **bank's own published terms**, and tell the owner what
  differs, is newly announced, or could not be verified — repeated until acted on, per the
  suppression rules in §5 — with a source URL and a quote for every claim.
- Produce proposals in a shape the owner (or an assistant) can turn into rule rows by hand.
- Keep a full archive of every run (input, prompt, raw model output, parsed result, message).
- Fail loudly and visibly; never fill a value the page did not say.

## Non-goals

- Writing or changing reward rules automatically. Hermes proposes; a person applies.
- Modelling cards whose terms the rule model cannot express (tiers, scheme choice,
  merchant lists, spend caps). CUBE is the reference case: owner ruling 2026-11-02 — keep
  its approximate rules, do not chase its terms. Such cards get a **signal-only** tier.
- Merchant/MCC matching of transactions to rules (a separate phase-2 topic, "rule scope").
- A Hermes cron job. The schedule is a systemd user timer that runs a one-shot Hermes.
- Any SPA surface.

## Anti-goals

- A report that states a rate, cap or date without a source URL and quote. A wrong "the bank
  says X" is worse than "not found".
- Hermes running with a shell while reading untrusted web pages (prompt-injection → VPS).
- Hermes holding a HomeHub token during the run. It gets rule data in the prompt and nothing
  else.
- Owner financial data in the repo. The card registry lives in the notifier's TOML config
  outside the repo; fixtures and the example config are fictional.

## Constraints

- Lives in the existing notifier package `services/accounting-service/notify/` and reuses
  its config loader, API client, `send.deliver` (Discord via `hermes send`) and
  `render.split_blocks` chunking. Deployed the same way: release checkout under
  `~/releases/home-hub-notify/<sha>`, user units from `deploy/notify/`, config at
  `~/.config/homehub-notify.toml` (0600).
- Hermes is invoked as a one-shot subprocess with the isolation contract in §3. The toolset
  is exactly `web` (= `web_search` + `web_extract` on the installed Hermes; the spike needed
  nothing else — its only other tool use was reading a cached PDF, which this design gives
  up). `browser` is not enabled in v1: it carries `browser_vault` (saved logins) and reaches
  loopback; adding it later needs its own review.
- Hermes facts verified on the installed binary (v0.22.2+1692, 2026-11-03, Task 1 rounds 1–2):
  `hermes chat` is a **gateway client** — the turn runs inside the owner's live Hermes gateway
  process, and even with `-t web` the gateway's MCP servers (agentmemory, 54 tools incl. a
  file writer) stay reachable through the `tool_search`/`tool_describe`/`tool_call` bridge;
  `--safe-mode` (chat or `-z`) binds **no tools at all** on this build. `hermes -z` builds
  `AIAgent` in-process (`hermes_cli/oneshot.py`), takes the prompt as its argument (no
  `--query-file`), honours an explicit `-t` list (`_validate_explicit_toolsets`; `all` is
  rejected, unknown names dropped), and with `--ignore-user-config --ignore-rules` the model
  sees exactly `mcp__web_extract` + `mcp__web_search` (model self-report in the probe; the resolver proof is
  `resolve_toolset('web')`) (the Anthropic OAuth wire alias of the
  built-in `web_extract`/`web_search`, `agent/anthropic_adapter.py`) and no bridge: config
  bypass drops the config-defined MCP servers, plugins and hooks, and `HERMES_IGNORE_RULES`
  is honoured by `agent/system_prompt.py` (rules/skills) — whether the built-in memory file
  stays out is what the probe's scratch-home run decides. Config bypass reads no profile
  model default, so `--provider`/`-m` are mandatory. The one-shot path sets
  `HERMES_YOLO_MODE=1` and `HERMES_ACCEPT_HOOKS=1` itself, so the agent must have nothing
  dangerous to approve.
- Python 3.13; the notifier's existing runtime deps (httpx) and nothing new. Tests under
  `tests/notify/`, fictional data only.
- The notifier token already reaches both routes (`GET /reward-rules?state=active`,
  `GET /accounts/{id}/reward-rules` → 200 with the `homehub-notify-token` on 2026-11-03), so
  no scope or deploy change is needed. `ReadClient` gains two methods for them. Rule JSON:
  `rate` is a 4-dp decimal string or null, `total_cap`/`fixed_amount` decimal strings or null,
  `window` is `"statement_cycle"`, dates ISO strings.
- Runs as the `opc` user like the other notifier units (needs `hermes` on PATH; the units
  already set `PATH=%h/.local/bin:…`).
- Message copy in Traditional Chinese, same voice as the weekly digest.

## How it works

### 1. Input: the card registry + live rules

`[terms]` section in the notifier TOML (new; absent ⇒ the `terms` command exits 2 with
`ConfigError("terms")` and the timer is simply not installed):

```toml
[terms]
report_dir = "~/reports/home-hub-terms"      # archive root, created 0700
hermes_toolsets = "web"                       # passed to `hermes -t`; must be exactly "web" (§3)
max_output_bytes = 4194304                    # cap on archived hermes stdout/stderr; past it the run fails
hermes_timeout_seconds = 1800
hermes_model = "claude-sonnet-5-5"            # required: config bypass has no model default
# hermes_provider = "anthropic"                # default anthropic
ignore_accounts = []                          # active-rule cards the owner chose not to maintain; never reported as 未登錄
terms_state_file = "~/.local/state/homehub-notify/terms-state.json"

[terms.cards."101"]                            # key = HomeHub account id (string, TOML table key)
official = "滙豐 Live+ 現金回饋卡"
bank_domains = ["hsbc.com.tw"]
tier = "precise"                              # precise | signal
# country = "TW"                              # default TW; ISO 3166-1 alpha-2
# language = "zh-TW"                          # default zh-TW; language to search and read in
# currency = "TWD"                            # default TWD; unit of caps/amounts in the terms
# aliases = []                                # extra names to search (old names, English names)
# year_end_window = true                      # default: true when country is TW, false otherwise

[terms.cards."102"]
official = "國泰世華 CUBE 卡"
bank_domains = ["cathaybk.com.tw", "cathay-cube.com.tw"]
tier = "signal"

[terms.cards."301"]                           # an overseas card — illustrative product, fictional id
official = "Chase Freedom Flex"
bank_domains = ["chase.com", "creditcards.chase.com"]
tier = "precise"                              # 5% rotating quarterly categories (cap per quarter) fit the rule model
country = "US"
language = "en"
currency = "USD"
aliases = ["Freedom Flex", "Chase Freedom Flex Mastercard"]
# year_end_window defaults to false for country != TW; the monthly run catches each quarter's categories
```

Validation: `official` non-empty, `bank_domains` non-empty list of hostnames, `tier` in
{precise, signal}, `country` two upper-case letters, `language` a BCP-47 tag, `currency`
three upper-case letters, `aliases` list of strings, `year_end_window` bool (default derived
from `country`); account ids must be ints. Card keys must be canonical decimal ids
(`[1-9][0-9]*`, a duplicate id is refused), each `bank_domains` entry an LDH hostname with at
least one dot, and `hermes_timeout_seconds` within 1..1840 so the units' `TimeoutStartSec=1900`
outlasts Hermes (SDD final review). Loader errors name the key only.

At run time the command reads, for each registered card, `GET /accounts/{id}/reward-rules`
through the notifier's existing token (read scope). It keeps rules that are enabled and not
yet ended (`ends_on` null or ≥ today). It also reads `GET /reward-rules?state=active` once to
find **active-rule cards that are not registered** — those are reported as 「未登錄」 so the
registry does not silently rot, except ids listed in `ignore_accounts` (cards the owner keeps
rules on but no longer uses). Registered cards with zero live rules are still checked
(the bank may have published terms the owner has not entered).

The input is frozen to `input.json` in the run's archive dir before Hermes starts.

### 2. Prompt

A template file in the package, `notify/terms_prompt.md`, filled with today's date, the
card list (official name, domains, tier, live rules as compact JSON with
`id,name,method,rate,fixed_amount,is_basic,total_cap,window,starts_on,ends_on,description`) and the
contract below. The template is the spike prompt, generalised:

- Two stages per card, **in the card's `language`** (search terms, page reading): **signal**
  (search news/aggregators/forums for「<card> <next year> 回饋」,「權益調整」; for an `en` card
  "<card> rewards changes <year>", "terms update") then **verify** (find the bank's own page or PDF on the listed domains, read it,
  extract rate / cap / period / main conditions). Aggregators are signal only, never a source.
- precise tier: compare each live rule on exactly these fields: `name`, `method`, `rate`
  (a percent, `0 < rate ≤ 100`, 4 dp — never a fraction), `fixed_amount`, `is_basic`,
  `total_cap`, `window`, `starts_on`, `ends_on`, `description`. Posting fields
  (`posting`, `delay_days`, `post_month_offset`, `post_day`, roundings) and `shared_cap_id` are
  **out of scope** in this version and are not sent. A programme on the official page with
  no matching live rule is a `missing_rule` finding — including when the card has zero live
  rules because its rows all expired (the prompt says so explicitly, so a zero-rule card
  cannot come back `unchanged`). signal tier: stage 1 only, summarise in ≤ 3 lines in
  `notes`; no rule comparison.
- If the next period's terms are announced, list them as rule-ready rows.
- The report prose and the message are always Traditional Chinese; `quote` stays in the
  page's original language; amounts carry the card's `currency`.
- Hard rules: every claim carries a source URL and a verbatim snippet; no official source ⇒
  `not_found`; read-only; no HomeHub calls; the last thing in the answer is one fenced
  ` ```json ` block that follows the schema below exactly. Prose before it is kept as the
  human-readable report but is not parsed.

### 3. Hermes runner

**Isolation contract.** A subprocess of the same user cannot be sandboxed by the notifier;
what it can do is pin the Hermes invocation so that the agent has no tool that touches the
host, no skill text that names the token, and no persistent trace. The exact invocation:

```
hermes -z <prompt text> -t web --ignore-user-config --ignore-rules --provider <hermes_provider> -m <hermes_model>
```

- `-t web` and nothing else. `run_hermes` validates `hermes_toolsets` by **exact set
  equality** with `{"web"}` (not a substring ban): `all`, `*`, composites (`debugging`,
  `coding`, `safe`), `file`, `code_execution`, `browser`, any `mcp-*` name ⇒ `ValueError`.
- `--ignore-user-config --ignore-rules`, never `--safe-mode` (it binds no tools on this build) and
  never the `hermes chat` path (gateway-hosted; its MCP servers leak through the tool bridge).
  Bypass = code-default config (no MCP servers, hooks, plugins, memory provider) + no
  rules/skills injection.
  Never `-s`/`--skills` — that flag *injects* skills, and `homehub-api` documents the token
  path.
- Explicit `env` (PATH, HOME, LANG, TZ and the provider variables Hermes needs — the builder
  lists them from the working spike environment) and `cwd` = the run's archive directory,
  never the release checkout.
- `start_new_session=True`; on timeout the whole process group is killed. The unit's
  `TimeoutStartSec` ≥ `hermes_timeout_seconds` + 60.
- Prompt text passed as the `-z` argument, read from the 0600 `prompt.md` in the archive. argv is
  visible in `/proc/*/cmdline` on this single-user VPS and carries only the registry rows (no
  secrets); `run_hermes` refuses a prompt over 120 000 bytes (Linux `MAX_ARG_STRLEN`).
- `hermes.out`/`hermes.err` are capped (`max_output_bytes`, default 4 MiB): the archive copy
  is truncated with a marker and the run fails past the ceiling, so a page cannot fill the
  disk.
- Known coverage loss vs the spike: without a shell Hermes cannot page through a long PDF
  from its local cache (the spike read the middle of a 130k-character card handbook that
  way), so exclusion lists buried deep in long documents may go unread. The agent must
  report such documents in `fetch_log.truncated` (§4) and the message shows them per card,
  so a gap reads as "unread", never as "unchanged". Adding `browser` later is the recorded
  remedy if the first dry-runs show it matters.

**Blocking first task of the plan — pass bar**, run on the installed Hermes with a fixture
prompt: (1) the tools Hermes **registers** for that process (the bound tool schema list, read
from the run's debug/usage output or the toolset resolver — not the model's description of
itself) are exactly `web_search`, `web_extract` — the model may report them under the OAuth alias
`mcp__web_search`/`mcp__web_extract`, and must report no `tool_search`/`tool_describe`/`tool_call`; (2) a shell request in the prompt is refused
for lack of a tool, not approved; (3) exactly one new row in the Hermes session DB, the probe's own `source=oneshot` row (rows
the live gateway adds with another source are listed, not failed), and no memory/skill file changes; (4) `--ignore-rules` is visibly honoured (no persona/SOUL/user-rule/memory text and no
scratch-home sentinel in the output); (5) the argument actually supplies the prompt
(the fixture asks for a token only the file contains); (6) the loopback probe
`http://127.0.0.1:8000/reward-rules` returns HTTP 401 **or** the tool's private-network block
(`Blocked: URL targets a private or internal network address`), with no rule body in the output. **All six
are the no-ship list**; if any fails, the feature does not ship until it does — no fallback
to a looser toolset.

Isolation check round 1, 2026-11-03 (hermes v0.22.2+1692.g22063fc, `hermes chat … --safe-mode`): FAIL - observation 4 (literal
`RULES-ABSENT` not produced; PROBE-RULE did not leak) and observation 6 (agent had no web/fetch
tool bound, so neither 401 nor the private-network block appeared). Observations 2, 3, 5 pass.
Details: `services/accounting-service/deploy/notify/terms-isolation-probe.md`. 

Isolation check round 2, 2026-11-03 (hermes v0.22.2+1692.g22063fc, `hermes -z "$(cat prompt.md)" -t web
--ignore-user-config --ignore-rules --provider anthropic -m claude-sonnet-5-5`): PASS - all six observations
(tools `mcp__web_search`/`mcp__web_extract` only, no shell, hashes unchanged and one oneshot session row,
scratch run `RULES-ABSENT` without `PROBE-RULE`, token echoed, loopback blocked as private network).

Residual risk, stated: `web_extract` fetches arbitrary URLs including loopback. HomeHub
routes need a bearer token the agent does not have; the other loopback listeners (18080
demo, 18081 preview) hold fictional data. This is accepted and the builder's isolation task
includes one probe that asks the agent to read `http://127.0.0.1:8000/reward-rules` and
confirms a 401 in the output.

`notify/hermes_run.py`: `run_hermes(prompt_path: Path, toolsets: str, timeout_s: int,
model: str, provider: str, env: dict[str, str], cwd: Path, max_output_bytes: int,
archive: Path, runner=None, kill=os.killpg) -> HermesResult(stdout, stderr, returncode, seconds, truncated)`.
Non-zero exit, timeout or truncation ⇒ `HermesError(reason="hermes", archive=<dir>)`
(never the prompt or output).

### 4. Output contract and parsing

The final fenced JSON block:

```json
{
  "cards": [
    {
      "account_id": 101,
      "status": "unchanged | changed | announced | not_found | signal",
      "current_period_end": {"date": "2027-03-31", "source": "https://…", "quote": "…"},
      "findings": [
        {"rule_id": 162, "field": "ends_on", "site": "2027-03-31", "system": "2026-12-31",
         "source": "https://…", "quote": "…"},
        {"rule_id": null, "field": "missing_rule", "site": "精選國家餐飲 +1% cap 200", "system": null,
         "source": "https://…", "quote": "…"}
      ],
      "next_terms": {"announced": true, "period": ["2027-01-01", "2027-03-31"],
                     "rules": [{"name": "…", "rate": "1.5", "cap": null, "notes": "…"}],
                     "source": "https://…", "quote": "…"},
      "sources": ["https://…"],
      "notes": "free text, ≤ 500 chars"
    }
  ],
  "fetch_log": {"tool_calls": 15, "failed": ["https://… (403)"],
                "truncated": [{"account_id": 101, "url": "https://…", "read": "first ~40k chars of ~130k"}]}
}
```

`notify/terms.py` → `parse_result(stdout: str, cards: Registry) -> TermsResult`: takes the
**last** fenced json block. Rules, in order:

- **Shape**: strict on the fields it uses, lenient elsewhere — unknown keys ignored;
  per-status required fields: `changed` needs `findings`, `announced` needs `next_terms`,
  `not_found`/`unchanged`/`signal` may omit both (absent = empty/null). `rate`/`cap` accept a
  JSON number or string and are stored as canonical decimal strings; dates ISO strings or
  null; `current_period_end` may be absent or null on any status; decimals are stored as
  `Decimal(...).normalize()` strings without exponent; unknown status ⇒ parse error. The
  set of `account_id`s must equal the input set,
  each once; a foreign id ⇒ parse error. A card in the input and missing from the JSON ⇒
  synthetic `not_found`.
- **Source rule** (the anti-fabrication gate), applied to **every URL the message can
  print**: `findings[].source`, `next_terms.source`, and `current_period_end` (which now
  requires its own `source` + `quote` in the schema). A source passes when it is `https`,
  has no userinfo, and its hostname (lower-cased, one trailing dot stripped) equals a listed
  `bank_domains` entry or ends with `"." + domain`. Path/query matches and look-alike
  suffixes fail. The whole URL must also fullmatch `[A-Za-z0-9\-._~:/?#!$&+,;=%()]+` (no
  `@ < > " ' * [ ] { } | \ ^`, backtick or space), so no path, query or fragment can carry a
  Discord link or mention, and must not contain `discord` (case-insensitive; invite hosts render without a
  scheme) (PR review); `fetch_log` URLs pass the same rule — a failing `failed` entry is
  dropped, a failing `truncated` entry keeps its card's truncation line with the host only
  (SDD final review). `quote` must be
  non-empty. A failing finding moves to `unverified`; a failing `next_terms` is dropped from
  the rule-ready block and reported as `· 下期條款：來源未驗證` with its URL; a failing
  `current_period_end` is nulled and the line omitted.
- **Input join** (SDD final review): verified findings are joined to the frozen input rules
  of their card. A non-null `rule_id` that is not one of them, or a null `rule_id` with a
  `field` other than `missing_rule`, moves to `unverified` (reason `未知規則`). For a field of
  the rule row, `system` is taken from the input rule, not the model; for any other field it
  is omitted. 「缺規則」 is used only for `field == "missing_rule"`.
- Any parse failure ⇒ the run fails (see 6); nothing is inferred.

Residual, stated: the notifier does not re-fetch the page, so an on-domain URL paired with
a sentence the page never contained still passes. Nothing is written automatically, and the
owner sees the URL next to the quote. A later tightening is a whitespace-normalised
substring check of each `quote` against the archived `web_extract` bodies (PDF whitespace
normalised) — recorded as a follow-up, not built in v1.

Downgrade semantics: a card keeps the status the model gave it; its findings are split into
`findings` (verified) and `unverified`. A `changed` card whose verified list is empty is
rendered like `not_found` with the line `· 找不到官網來源（N 項未驗證）` and the unverified
items under it. A verified `current_period_end` is shown on `changed`/`announced` card
lines (`條款期間至 2027-03-31`).

### 5. Change detection and message

State: its own file `terms_state_file` (my call: the daily and weekly timers rewrite the
shared `state.json` whole, and `State` is a typed dataclass; a separate file avoids both
clobbering and a schema migration) —
`{"<account_id>": {"digest": …, "seen_at": iso}}`. The digest is sha256 of canonical JSON
(UTF-8, `sort_keys`, compact separators) of: `status`, `current_period_end`, `findings`
sorted by `(rule_id, field, source)`, `unverified` sorted the same, `next_terms`,
`next_terms_unverified_url` (normalised), with
quotes whitespace-collapsed and decimals normalised; for `signal` cards the normalised
`notes` text is hashed instead. A missing file is an empty state. An unreadable or corrupt
file **fails the run** and is not replaced (unlike `load_state`'s empty-on-error path, which
would silently reset the baseline). A card is **new news** when its digest differs from the stored
one or has no stored one.

What the digest suppresses and what it does not (the owner must keep seeing open items):

- **Rendered every run regardless of digest**: `not_found` cards (a terms page that has
  failed for months must not look like "no change"); `changed` cards with empty verified
  findings; verified `changed` findings and `announced` blocks — these repeat on every run
  until the system side of the comparison changes (the owner applied the rule, or the
  site moved), because one missed Discord message must not be the only notice; the
  「未登錄」 line whenever the set is non-empty.
- **Suppressed by digest match**: repeat `unverified` items and unchanged `signal` text.
- `unchanged` with a digest change (first run, or only `current_period_end` moved) renders
  as `· 無差異，條款期間至 <date>`.

Rendering is driven by payloads, not by a switch on `status`: finding lines print when
verified findings exist, the announced block when `next_terms.announced`, the signal lines
when status is `signal`, in that order, all of them when more than one applies.

Message (`render_terms(result, state, cfg, today) -> list[str]`):

- Header: `📋 回饋條款查核 2026-11-03（M 張卡，N 張有事項）` and the archive path.
- One block per card with something to show, in registry order, headed
  `【滙豐 Live+】` + the account settings link (`render._settings_link`) so it is actionable
  from Discord:
  - findings: `· 3%：結束日 官網 2027-03-31 / 系統 2027-12-31 — <source>`; missing rules as
    `· 缺規則：精選國家餐飲 +1% cap 200 — <source>`; the quote shown truncated to 80 chars
    (full text in `result.json`).
  - announced: `· 下期條款已公告 2027-01-01～03-31：` then rule-ready lines.
  - signal: the ≤ 3-line summary.
  - not found: `· 找不到官網來源`（with the fetch_log failures for that card if any）.
  - truncated sources (from `fetch_log.truncated`, every run they appear):
    `· 來源文件過長，僅讀到前段（<url>）` — a gap, not a no-change.
  - unverified: `· 未驗證（來源不在銀行網域）：…`.
- 「未登錄」 line listing active-rule cards missing from the registry.
- Cards with nothing to show are folded into one line: `無變動：A、B、C`.
- Nothing to show at all (and 「未登錄」 empty) ⇒ a single line
  `📋 回饋條款查核 2026-11-03：M 張卡無變動`. (My call:
  always send one line rather than stay silent, so a dead timer is noticeable. Owner may
  flip this to silent later via a config flag.)
- Chunked with `split_blocks`, delivered with `send.deliver` to `discord_target`.
- Every printed full URL, the settings link included, renders as `<url>` (one Discord token,
  no embed); model text has its Discord markdown escaped (SDD final review); `discord` is broken with
  U+200B in model text so no invite form survives (PR review).

The state is updated only after a successful send (same rule as the daily reminder). A send
that succeeds and a save that then fails means the next run re-sends the same news; accepted,
the message is informational.

### 6. Archive and failure

Each run creates `<report_dir>/<YYYYMMDDTHHMMSSZ>/` (0700) with `input.json`, `prompt.md`,
`hermes.out`, `hermes.err`, `result.json`, `message.md` (0600). The CLI mirrors the other
commands: `terms --cause {monthly,year-end} [--as-of D] [--dry-run | --send] [--config P]`,
`--cause` required, `--dry-run` the default when neither mode flag is given; `--dry-run` writes the archive and prints the message, sends nothing,
updates no state. If Hermes succeeds and the send fails, the archive is kept and the state
untouched. `HermesError` and the parse error (`TermsParseError`) join `FAILURES`; both carry only
`reason` (`hermes` / `parse`) and the archive dir, so `_short` can never receive stdout or
the prompt.

Failures (config, API, Hermes exit/timeout, parse) go through the notifier's existing failure
path: `failed@` unit sends `render_failure(reason)` — reason names the stage and the archive
dir, never a secret or the prompt. A failed run leaves the state untouched so the next run
re-reports.

### 7. Schedule

Two timers, two services, one binary — the runner cannot tell the slots apart from the wall
clock (a `Persistent` catch-up fires on whatever weekday the host comes back), so the slot
is an explicit argument:

```
homehub-notify-terms-monthly.timer    OnCalendar=*-*-01 09:00:00 Asia/Taipei      → terms --send --cause monthly
homehub-notify-terms-year-end.timer   OnCalendar=Mon *-11,12-* 09:00:00 Asia/Taipei → terms --send --cause year-end
```

`--cause monthly` checks every registered card; `--cause year-end` only cards with
`year_end_window = true` (default from `country`: `TW` ⇒ true, else false), and exits 0
with no run when that set is empty. Both `Persistent=true`. A 1st that is also a Monday (or
a double catch-up) runs twice; the second run sends whatever §5 still has to show (open
`not_found`, unapplied findings, announced blocks) and the one-line message only when §5 has
nothing. The two services are ordered, not conflicting: the year-end service carries
`After=homehub-notify-terms-monthly.service`, so a simultaneous start runs monthly first and
then year-end, and neither stops the other (`Conflicts=` would stop an in-flight run instead
of queuing it — SDD final review I1); the terms-state load/save is wrapped in an `flock` on
the state file, so two runs cannot clobber each other's state. Services: `TimeoutStartSec` ≥
`hermes_timeout_seconds` + 60 s, `OnFailure=homehub-notify-failed@%n.service`.
`install.sh` installs both units but does **not** `enable --now` them (today it enables every
unit it installs — the terms units are excluded); rollout step 3 enables them after the
dry-run.

Expected cost: ~1–2 min and ~5 tool calls per precise card on the Hermes default model;
7 cards ≈ 10–15 min, once a month.

## Extension points (owner: overseas cards are coming)

The registry is the only place a card's locale lives, so adding a non-Taiwan card is a TOML
entry, not a code change:

- `country` / `language` / `currency` drive the search language, the reading language and
  the unit of caps in findings; defaults keep today's Taiwan cards unchanged.
- `bank_domains` is already any hostname list; the on-domain source check works the same.
- `aliases` covers cards whose marketing name differs from the issuer's legal product name
  or that were renamed.
- `year_end_window` (default from `country`) keeps non-Taiwan cards out of the Nov–Dec
  weekly runs; the monthly run still covers them. Cards with rotating
  categories (quarterly schemes) are caught by the monthly run; a per-card `extra_calendar`
  is a possible later addition, not built now.
- Rule rows already carry `rate` as a percent and `total_cap` as an amount in the card's
  own currency, so proposals for a USD/JPY card need no model change. Points-per-dollar
  programmes (miles, points) are expressed as the existing `fixed`/`percent` methods the
  same way the AMEX and 華航 rules are today.
- The output schema has no Taiwan-specific field. The builder keeps it that way.

What does **not** scale without a later phase: the rule model itself (tiers, merchant
scopes, shared caps across channels) — the same limit that keeps CUBE in the signal tier
applies to complex overseas programmes; they enter as `signal` until a rule-scope phase
exists.

## Rollout

1. Merge, deploy a notifier release checkout as usual.
2. Owner adds `[terms]` + the 7 maintained cards to the TOML (ids from the account list;
   CUBE as `signal`). Cards the owner no longer uses (ruled 2026-11-02: 星展 eco, Daway) are
   left out even though they have rules.
3. `python -m notify terms --dry-run --cause monthly` once by hand; read the archive — per
   card, check `fetch_log.truncated` and `failed` to see how much of each bank's document
   was actually read; then enable both timers.
4. First real send: the owner applies accepted findings by hand (or asks); the assistant
   records what was applied in the data-pass log as on 2026-11-02.

## Left to the builder

- Exact prompt wording (start from the spike's `prompt.md`); keep the contract section
  verbatim from §4.
- The provider environment variables the explicit `env` must carry (take them from the
  working spike environment; never the HomeHub token).
- Test fixtures: a fictional two-card registry, a canned Hermes stdout with one `changed`, one
  `signal`, one off-domain source, and one card missing from the JSON.

## Open questions for the owner

None blocking. Later: whether the registry should move into HomeHub (an account field)
once more than one consumer needs it; whether signal-tier summaries should go to a separate
channel; the quote-vs-archived-page substring check (§4 residual); whether to add `browser`
for pages `web_extract` cannot read (its own review: vault off, empty profile, no `file:`).
