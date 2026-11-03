# Reward terms check (回饋條款查核) — design

Date: 2026-11-03. Status: draft for owner review. Follows the reward-rules phase 1 design
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
  rules in HomeHub** against the **bank's own published terms**, and tell the owner only what
  differs or is newly announced, with a source URL and a quote for every claim.
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
- Hermes is invoked as `hermes -z "<prompt>" -t web,browser` (allowlist toolset: web search +
  extract + browser; **no `terminal`**), with a wall-clock timeout. The builder verifies that
  `-t` is an allowlist on the installed Hermes (the spike passed `web,browser,terminal`
  explicitly, and Hermes used all three).
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
hermes_toolsets = "web,browser"               # passed to `hermes -t`; never contains "terminal"
hermes_timeout_seconds = 1800
# hermes_model = ""                           # optional `-m`; empty = Hermes default
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
# year_end_window = true                      # default true: also check on the Nov–Dec weekly runs

[terms.cards."102"]
official = "國泰世華 CUBE 卡"
bank_domains = ["cathaybk.com.tw", "cathay-cube.com.tw"]
tier = "signal"

[terms.cards."301"]                           # an overseas card (fictional example)
official = "Example Bank Everyday Cash Card"
bank_domains = ["examplebank.com"]
tier = "precise"
country = "US"
language = "en"
currency = "USD"
aliases = ["Everyday Cash Visa"]
```

Validation: `official` non-empty, `bank_domains` non-empty list of hostnames, `tier` in
{precise, signal}, `country` two upper-case letters, `language` a BCP-47 tag, `currency`
three upper-case letters, `aliases` list of strings; account ids must be ints. Loader errors
name the key only.

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
`id,name,rate,fixed_amount,is_basic,total_cap,window,starts_on,ends_on,description`) and the
contract below. The template is the spike prompt, generalised:

- Two stages per card, **in the card's `language`** (search terms, page reading): **signal**
  (search news/aggregators/forums for「<card> <next year> 回饋」,「權益調整」; for an `en` card
  "<card> rewards changes <year>", "terms update") then **verify** (find the bank's own page or PDF on the listed domains, read it,
  extract rate / cap / period / main conditions). Aggregators are signal only, never a source.
- precise tier: compare each live rule field by field. signal tier: do stage 1 only and
  summarise in ≤ 3 lines; no rule comparison.
- If the next period's terms are announced, list them as rule-ready rows.
- The report prose and the message are always Traditional Chinese; `quote` stays in the
  page's original language; amounts carry the card's `currency`.
- Hard rules: every claim carries a source URL and a verbatim snippet; no official source ⇒
  `not_found`; read-only; no HomeHub calls; the last thing in the answer is one fenced
  ` ```json ` block that follows the schema below exactly. Prose before it is kept as the
  human-readable report but is not parsed.

### 3. Hermes runner

**Isolation is the first, blocking task of the plan**: on the installed Hermes, confirm that
`-t web,browser` is an allowlist (no terminal / file tools appear in the run), decide which of
`--safe-mode`, `--ignore-rules`, `--ignore-user-config`, `--skills` are needed so the
`homehub-api` skill (which documents the token path) is not loaded, and record the exact
flags in the unit file and in this document. If the allowlist cannot be made to hold, the
feature does not ship. The browser toolset can reach loopback URLs; that is accepted because
every HomeHub route needs a bearer token Hermes does not have, and the other loopback
listeners (Multica demo 18080, preview 18081) hold fictional data only.

`notify/hermes_run.py`: `run_hermes(prompt: str, toolsets: str, timeout_s: int, model: str | None,
runner=subprocess.run) -> HermesResult(stdout, stderr, returncode, seconds)`. Refuses
(`ValueError`) if `toolsets` contains `terminal`. Passes the prompt via argv (as the spike
did); if argv length is a problem on this host the builder may switch to `--usage-file`/stdin
— same contract. Non-zero exit or timeout ⇒ `HermesError` (message = exit code / timeout,
never the prompt).

### 4. Output contract and parsing

The final fenced JSON block:

```json
{
  "cards": [
    {
      "account_id": 94,
      "status": "unchanged | changed | announced | not_found | signal",
      "current_period_end": "2027-03-31",
      "findings": [
        {"rule_id": 162, "field": "ends_on", "site": "2027-03-31", "system": "2026-12-31",
         "source": "https://…", "quote": "…"},
        {"rule_id": null, "field": "missing_rule", "site": "精選國家餐飲 +1% cap 200", "system": null,
         "source": "https://…", "quote": "…"}
      ],
      "next_terms": {"announced": true, "period": ["2027-01-01", "2027-03-31"],
                     "rules": [{"name": "…", "rate": "0.88", "cap": null, "notes": "…"}],
                     "source": "https://…"},
      "sources": ["https://…"],
      "notes": "free text, ≤ 500 chars"
    }
  ],
  "fetch_log": {"tool_calls": 15, "failed": ["https://… (403)"]}
}
```

`notify/terms.py` → `parse_result(stdout: str) -> TermsResult`: takes the **last** fenced json
block; strict types; unknown status ⇒ parse error; `findings[].source` must be an `https://`
URL on one of the card's `bank_domains` (otherwise the finding is downgraded to
`unverified` and listed separately — this is the anti-fabrication check the model cannot
talk its way around); `quote` non-empty. A card present in input but absent from the JSON
⇒ synthetic `not_found`. Any parse failure ⇒ the run fails (see 6); nothing is inferred.

Downgrade semantics: a card keeps the status the model gave it; its findings are split into
`findings` (on-domain) and `unverified` (off-domain). A `changed` card whose verified list is
empty is rendered like `not_found` with the line `· 找不到官網來源（N 項未驗證）` and the
unverified items under it. `current_period_end` is shown on every `changed`/`announced`
card line (`條款期間至 2027-03-31`) and is part of the digest; the digest also covers the
`unverified` list so the same off-domain claim does not re-alert every month.

### 5. Change detection and message

State: its own file `terms_state_file` (my call: the daily and weekly timers rewrite the
shared `state.json` whole, and `State` is a typed dataclass; a separate file avoids both
clobbering and a schema migration) —
`{"<account_id>": {"digest": sha256 of canonical (status, current_period_end, findings,
unverified, next_terms), "seen_at": iso}}`; a missing file is an empty state. A card is **new news** when its digest differs from the stored
one or has no stored one.

Message (`render_terms(result, cfg, today) -> list[str]`):

- Header: `📋 回饋條款查核 2026-11-03（M 張卡，N 張有更新）` and the archive path.
- One block per card with new news, in registry order:
  - `changed`: `【滙豐 Live+】` then one line per finding
    `· 3%：結束日 官網 2027-03-31 / 系統 2027-12-31 — <source>`; missing rules as
    `· 缺規則：精選國家餐飲 +1% cap 200 — <source>`.
  - `announced`: `· 下期條款已公告 2027-01-01～03-31：` then rule-ready lines.
  - `signal`: the ≤ 3-line summary.
  - `not_found`: `· 找不到官網來源`（with the fetch_log failures for that card if any）.
  - `unverified` findings: `· 未驗證（來源不在銀行網域）：…`.
- 「未登錄」 line listing active-rule cards missing from the registry.
- Cards with no new news are folded into one line: `無變動：A、B、C`.
- Nothing new at all ⇒ a single line `📋 回饋條款查核 2026-11-03：M 張卡無變動`. (My call:
  always send one line rather than stay silent, so a dead timer is noticeable. Owner may
  flip this to silent later via a config flag.)
- Chunked with `split_blocks`, delivered with `send.deliver` to `discord_target`.

The state is updated only after a successful send (same rule as the daily reminder). A send
that succeeds and a save that then fails means the next run re-sends the same news; accepted,
the message is informational.

### 6. Archive and failure

Each run creates `<report_dir>/<YYYYMMDDTHHMMSSZ>/` (0700) with `input.json`, `prompt.md`,
`hermes.out`, `hermes.err`, `result.json`, `message.md` (0600). The CLI mirrors the other
commands: `terms [--as-of D] [--dry-run | --send] [--config P]`, `--dry-run` the default when
neither is given; `--dry-run` writes the archive and prints the message, sends nothing,
updates no state. If Hermes succeeds and the send fails, the archive is kept and the state
untouched. `HermesError` and the parse error (`TermsParseError`) join `FAILURES`; `_short`
renders them as stage + archive dir only.

Failures (config, API, Hermes exit/timeout, parse) go through the notifier's existing failure
path: `failed@` unit sends `render_failure(reason)` — reason names the stage and the archive
dir, never a secret or the prompt. A failed run leaves the state untouched so the next run
re-reports.

### 7. Schedule

`deploy/notify/homehub-notify-terms.{service,timer}`, installed by the existing `install.sh`:

```
OnCalendar=*-*-01 09:00:00 Asia/Taipei
OnCalendar=Mon *-11,12-* 09:00:00 Asia/Taipei
Persistent=true
```

(1st of every month; every Monday in Nov–Dec when new-year terms land. Two `OnCalendar`
lines that resolve to the same instant are one activation; `Persistent=true` fires at most
one catch-up run after downtime. A repeated run is harmless: the digest turns it into the
one-line no-change message.) Service: `python -m notify terms --send`, `TimeoutStartSec` ≥ hermes timeout + 60 s,
`OnFailure=homehub-notify-failed@%n.service`.

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
- `year_end_window = false` lets a card skip the Nov–Dec weekly runs when its programme does
  not follow the Taiwan calendar; the monthly run still covers it. Cards with rotating
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
3. `python -m notify terms --dry-run` once by hand; read the archive; then enable the timer.
4. First real send: the owner applies accepted findings by hand (or asks); the assistant
   records what was applied in the data-pass log as on 2026-11-02.

## Left to the builder

- Exact prompt wording (start from the spike's `prompt.md`); keep the contract section
  verbatim from §4.
- Whether to pass the prompt via argv or a file; whether `--safe-mode` / `--ignore-rules`
  are needed to keep Hermes from loading the `homehub-api` skill (which documents the token
  path) — verify on the installed version and record the chosen flags in the unit file.
- Test fixtures: a fictional two-card registry, a canned Hermes stdout with one `changed`, one
  `signal`, one off-domain source, and one card missing from the JSON.

## Open questions for the owner

None blocking. Later: whether the registry should move into HomeHub (an account field)
once more than one consumer needs it; whether signal-tier summaries should go to a separate
channel.
