Accounting Service
==================

FastAPI service holding the MOZE-based personal ledger: accounts, categories, projects and ledger entries.

Setup
-----

    uv venv --python 3.13 .venv
    uv pip install --python .venv/bin/python -r requirements.txt
    .venv/bin/alembic upgrade head
    .venv/bin/uvicorn app.main:app --port 8000

Configuration comes from the repository root `.env` (`POSTGRES_*`, `DB_HOST`, `ACCOUNTING_DB`, OpenTelemetry variables).
Set `ACCOUNTING_IMPORT_LOCKED=true` after the cutover to refuse every further MOZE import.
Until then, entries and entry groups that came from MOZE (`source` `moze_import` or `moze_backup`, or any row with a `moze_id`) are read-only: `PUT`, `DELETE`, settle and refund on them answer HTTP 409 `locked_until_cutover`. Manual rows and account settings are always editable; an account settings `PUT` sets `settings_locally_edited` so a re-import keeps the local settings until `POST /accounts/{id}/reset-settings-flag`.
Imports with foreign-currency rows need outbound HTTPS to `cdn.jsdelivr.net` (fallback `currency-api.pages.dev`) for daily rates; fetched rates are cached in the `fx_rate` table and never cleared.

MOZE import
-----------

    .venv/bin/python -m app.services.moze_import_service <export.csv> --dry-run
    .venv/bin/python -m app.services.moze_import_service <export.csv> [--rename OLD=NEW ...]

An import replaces every MOZE-sourced entry (`moze_import` and `moze_backup`) in one transaction and records an `import_run`.
Only one import runs at a time (PostgreSQL advisory lock); a second one is refused.

MOZE backup import
------------------

    (cd ../../tools/moze-realm-export && npm ci)    # once; see tools/moze-realm-export/README.md
    .venv/bin/python -m app.services.moze_backup_import_service <MOZE_4.0.zip> --dry-run
    .venv/bin/python -m app.services.moze_backup_import_service <MOZE_4.0.zip> [--rename OLD=NEW ...] [--allow-fx-outliers] [--no-strict] [--keep-json PATH]

The backup importer converts the archive with the Node tool (`MOZE_REALM_EXPORTER` in the root `.env`; default
`node <repo>/tools/moze-realm-export/index.js`, `node` from `PATH`, 10-minute timeout; one file path, or a command
line such as `/usr/bin/node /path/to/index.js`), then replaces every
`moze_import` and `moze_backup` entry and MOZE group in one transaction under the same lock, upserts groups,
accounts, categories, projects, counterparties, reward rules (by `moze_id`, never recreated) and the preference row,
and maps future-dated rows, periods and installments to schedule definitions and periods (see Schedules). Manual entries and their rule
attachments are never touched. Accounts edited locally (`settings_locally_edited`) keep their settings and, when
the backup no longer names them, are neither archived nor zeroed. Neither importer deletes a category, project or
counterparty without a `moze_id` (CSV-created or created in Settings); the backup importer deletes only unused
rows whose `moze_id` left the backup. MOZE's `startDay` is the first day of the statement period, so an account's
`closing_day` is `startDay − 1`; `startDay` 1 (the calendar month) imports as no closing day, for every account.
The report lists per-type counts, skipped future rows, `needs_review` reasons and per-account `moze_part` /
`previous_moze_part`; `balanceInfo` is not compared until a rule is confirmed, so the CLI warns
`WARNING: 0 of N accounts compared` and the balances are checked by hand.

Collections and repayments (MOZE types 5 / 6) settle the receivable / payable their `relatedID` names. MOZE leaves
`relatedID` empty on collections, so a settlement whose `relatedID` is empty or names no imported original of the
matching type (a receivable for a collection, a payable for a repayment) is linked to the imported original of the
matching type that shares its `target` (counterparty); with several, the target's settlements are allocated FIFO by
date to the earliest original with an open amount left (same-currency settlements only). The report counts both in
`settlements_linked` (`by_related_id`, `by_target`). Links are reviewed as `settlement_overflow` (more than the
original has left open, whether linked by `relatedID` or by target), `cross_currency_settlement` (the settlement's
currency differs from the original's; it stays linked but never reduces the original's `open_amount`, `is_settled` or
the counterparty's `open_amounts`, and is resolved by hand or closed by `isSettle` below) or, with no original,
`settlement_original_missing`. Future-dated and disabled rows stay skipped: a settlement whose original is
future-dated or disabled links by target to another imported original if one exists; otherwise it is reviewed as
`settlement_original_missing`.

MOZE's own "debt closed" flag, `AHTarget.isSettle`, is the authority: every imported receivable / payable original
whose target has `isSettle = true` is imported with `is_closed = true` (counted in `debts_closed_from_target`), even
where its settlements do not net (one collection covering several originals, another currency, rounding). A closed
debt reads `open_amount` 0 and `is_settled` true, leaves the counterparty's `open_amounts` together with the
settlements linked to it, and refuses `POST /entries/{id}/settle` (422 on `is_closed`). Links and review flags are
kept as they are; each full replace sets the flag again from the backup.

API
---

Paths inside the service; the dev proxy and Caddy add the `/api/accounting` prefix.

Reads

- `GET /accounts?include_archived=false&as_of=`, `GET /accounts/{id}?as_of=` (`as_of`, default today in Asia/Taipei:
  balances count entries posted on or before it), `GET /accounts/{id}/summary?date_from=&date_to=` (spend with refunds,
  income, rewards, net and count over entries posted in the range, plus the balance as of `date_to`),
  `GET /accounts/{id}/reward-rules`
- `GET /accounts/{id}/entries?limit=50&offset=0&kind=&date_from=&date_to=&q=`
- `GET /entries?limit&offset&kind&date_from&date_to&q&account_id=(repeatable)&hide_rewards&counterparty_id&open&tag=(repeatable, any of)&category_id=(repeatable, sub-categories included)&project_id&merchant=(case-insensitive literal substring of the row's or its group's merchant)&amount_min&amount_max=(signed, inclusive, in the entry's ACCOUNT currency — no conversion; 422 when min > max)` (2026-10-21: the attribute filters AND with the older ones), `GET /entries/summary?month=YYYY-MM`,
  `GET /entries/summary/daily?month=YYYY-MM` (calendar view: the month summary's expense/income rules per `entry_date`,
  with a `count` of counted rows; days without entries omitted; rewards left out when the preference hides them on
  the timeline; `missing_rates` as in the month summary), `GET /entries/{id}`
- `GET /account-groups`, `GET /categories?kind=`, `GET /projects`, `GET /counterparties` (with `open_amounts` per currency)
- `GET /preference`, `GET /fx-rate?date=&base=&quote=` (for today or a future date, or a date whose release is
  missing, the latest available release; `rate_date` names the release the rate comes from)
- `GET /imports/latest` (reports `kind`: `moze_csv` or `moze_backup`); MOZE's schedules are read through `/schedules/*` (see Schedules)

Writes (every new row gets `source = 'manual'`, and an edit of a schedule-posted row keeps `source = 'schedule'`; an edit of a statement-booked row (`source = 'statement'`, e.g. a stand-in) keeps that source when it changes no structural field (`coverage_service.STRUCTURAL_KEYS`) and the row has no fee / discount child before or after (category, name, merchant, description, tags), so a categorised stand-in stays replaceable; any other edit makes the row `manual`; amounts are unsigned with at most 4 decimals; 422 names the field, 409 is a conflict or `locked_until_cutover`)

- `POST /entries` (expense, income, receivable, payable; optional `fee` / `discount` children, FX fields, `reward_rule_ids`; the response may carry `proposed_fee`), `PUT /entries/{id}`, `DELETE /entries/{id}`
- `POST /entries/{id}/settle` (收款 / 還款, same currency, at most the open amount), `POST /entries/{id}/refund` (expense only, same currency, at most the unrefunded amount)
- `POST /transfers` -> `{transfer_group_id, out_entry_id, in_entry_id}`, `PUT /transfers/{transfer_group_id}`
- `POST /splits` -> `{group_id, member_ids}`, `PUT /splits/{group_id}`, `DELETE /splits/{group_id}`
- `POST /balance-adjustments` (`target_balance`; stores the delta)
- `POST /accounts`, `PUT /accounts/{id}`, `DELETE /accounts/{id}` (409 while it has entries; archive instead), `POST /accounts/{id}/reset-settings-flag`
- `POST /account-groups`, `PUT /account-groups/{id}`, `DELETE /account-groups/{id}`, `PUT /account-groups/order` (`{"ids": [...]}`)
- `POST /categories`, `PUT /categories/{id}`, `DELETE /categories/{id}`, `PUT /categories/order`
- `POST /projects`, `PUT /projects/{id}`, `DELETE /projects/{id}`
- `POST /counterparties`, `PUT /counterparties/{id}` (rename), `DELETE /counterparties/{id}`
- `PUT /preference`
- `POST /imports/moze` (multipart `file`, optional `renames` JSON object, `?dry_run=true`)
- `POST /imports/moze-backup` (multipart `file` <= 200 MB, optional `renames` JSON object, `?dry_run=&strict=&allow_fx_outliers=`) Posting dates: `posted_date` is MOZE's `chargeDate`, except that a charge more than 7 days BEFORE the record (MOZE stamps every instance of a recurring or installment record with the template's first `chargeDate`) falls back to the record date, so reconciliation sees the instance on its own month (2026-10-20).

API access for agents
---------------------

The API has no auth unless `ACCOUNTING_API_TOKENS` is set in the root `.env`: a comma-separated list of
`label:token` (or bare `token`) values, e.g. `ACCOUNTING_API_TOKENS=spa:<token1>,agent-x:<token2>`. Generate tokens
with `openssl rand -hex 32`; a token containing `:` needs a label. Unset or empty, nothing changes (startup logs
`API auth: disabled`); an item with an empty token stops the service at startup. Set, every request except `GET`/`HEAD`
on `/health` and `/health/ready` needs exactly `Authorization: Bearer <token>` (scheme in any case, one space, no other whitespace) matching
one configured token (SHA-256 digests compared in constant time), otherwise 401 `{"detail": "unauthorized"}` with
`WWW-Authenticate: Bearer`. `/docs`, `/redoc`, `/docs/oauth2-redirect` and `/openapi.json` are open only with
`ACCOUNTING_DOCS_PUBLIC=true`. The matching label is on `request.state.client_label` (None for a bare token); tokens
are never logged. Restart the service after changing either variable. The SPA sends its own token
(`ACCOUNTING_SPA_TOKEN`, see `docs/deploy/accounting-phase-2a.md`).

### Token scopes and the reconciliation feature

Three further variables (design `docs/superpowers/specs/2026-10-08-statement-reconciliation-design.md` §8.1):

- `ACCOUNTING_TOKEN_SCOPES`: `label=scope,scope; label=scope`, e.g.
  `spa=legacy; ops=legacy,admin; hermes=read,propose; worker=ingest`. Scopes: `read`, `propose`, `write`, `admin`,
  `enqueue`, `ingest`, `legacy` (`legacy` passes every scope check, the statements routes included, but cannot be combined with
  `ingest` or `enqueue`). Unset keeps the compatibility behaviour (every valid token may do everything).
- `ACCOUNTING_RESTRICTED_LABELS`: extra comma-separated labels (besides the built-in `hermes` and `worker`) that must
  never run without a scope entry.
- `ACCOUNTING_RECONCILIATION_ENABLED`: `1`, `true` or `yes` (any case) serves the statement routes (`/statements/...`,
  `/accounts/{id}/statements`, `/settings/reconciliation`); otherwise they answer 404 before any scope check. Scope
  checks stay active either way on the legacy and settings routers.

The service refuses to start when scopes are set and a token is bare, a token value is duplicated, a label has no
scope entry (or a scope entry has no token), a scope name is unknown, or `legacy` is combined with `ingest`/`enqueue`;
and when the feature is on with scopes set but no tokens; and when scopes are unset but the feature is `true` or a restricted label holds a token. A token whose scopes do
not cover a route gets HTTP 403 with the message `scope`.

Deploy note: before setting `ACCOUNTING_TOKEN_SCOPES`, map the existing SPA and ops labels to `legacy`
(`spa=legacy; ops=legacy,admin`), the statement worker label to `ingest` (`worker=ingest`) and the hermes label to
`read,propose` (`hermes=read,propose`). Restart the service after changing any of them.

Enabling reconciliation: set `ACCOUNTING_RECONCILIATION_ENABLED`, configure the scopes above, then, once the statement
worker is live, `PUT /settings/reconciliation {"dirty_enabled": true}` (the ledger dirty triggers write nothing until then;
a PUT replaces the whole settings, so send the current `account_map` with it).
Deploy order: run `.venv/bin/alembic upgrade head` before restarting the service.

Matching (R6a). `GET /settings/reconciliation` also returns `rules` (the matcher's tunables, defaults shown) and
`rules_version`; `PUT /settings/reconciliation {"account_map": <current>, "dirty_enabled": <current>, "rules": {...}}`
(the full body: a PUT replaces all three) overrides, inside `rules`, any subset of `exact_window_days` (3),
`foreign_window_days` (5), `near_window_days` (5), `near_tolerance_abs` (`"10"`, at least 0), `near_tolerance_pct` (`"0.05"`),
`accept` (`"0.80"`), `margin` (`"0.05"`), `ambiguous_floor` (`"0.50"`), `candidate_window_days` (10), `drift_window_days` (15), `deferral_days` (2: entries posted later than `period_end` minus that many days are deferred)
and `bank_only_patterns`. Decimals are strings; `accept`, `margin`, `ambiguous_floor` and `near_tolerance_pct` must lie in
0..1, integers in 0..10000; unknown keys and `period_end` are refused with 422 on `rules`. A PUT replaces `account_map`,
`dirty_enabled` and `rules` wholesale (send them all). `rules_version` becomes `r6a-<settings version>` when the effective
rules change. A stored row under an `r1b-*` `rules_version` is read with its old-scale values ignored: a stored `margin`
`0.15` or `near_tolerance_pct` `0.03` (the R1b defaults) is not an override, and a differing `accept` / `margin` /
`ambiguous_floor` is ignored with a warning. `GET` shows the effective rules, so a GET-modify-PUT round trip writes the
new defaults.

Daily sweep. The statement worker is expected to call `POST /reconciliation/sweep` (`ingest` scope, body `{run_id, lease_token}`) once a
day (a worker convention, not enforced) under its run lease: it reconciles every statement with a relevant ledger change past its watermark or with
`needs_recheck`, one transaction per statement, then fills deferral links (a deferral to a later period shows its link only
after the later statement is reconciled or this batch runs). It answers 409 `lease` if the lease lapses between items;
the finished items stay committed. One statement is reconciled with
`POST /accounts/{id}/statements/{statement_id}/reconcile` (`write`); `GET /reconciliation/cases` lists cases (`read`;
`status`, `account_id`, `kind`, `limit` 1..1000, default 200).

Actions, proposals, policy and confirm (R1c; design §17 "R1c implementation notes"). The owner acts on a case through
one idempotent, snapshot-checked, audited action engine; the policy engine ships **off**. No migration and no
environment variable: the new settings are JSON keys. All routes sit behind the reconciliation feature flag (404
otherwise). Amounts are strings; a 422 message starts with a stable code (`code` or `code: detail`), a 409 detail is
`code` or `code: detail`.

| Method and path | Scope | Body → response |
| --- | --- | --- |
| `GET /reconciliation/cases/{id}` | `read` | → `CaseDetailOut`: the case row (R1b `CaseOut` fields plus `statement_id`, `revision_id`, `resolved_by`, `resolved_action_id`, `resolved_at`, `deferred_to_period_end`, `deferred_to_statement_id`, `note`), `actions_allowed`, `pending_proposals`, `line` with `matched`, `coverage`, `event_flag` (`text_changed` after a normalised re-parse, else null) and `event_status` (`live`/`retired`/`quarantined`) — the statement detail's `lines[]` carry the same two fields (R3). Stale proposals are superseded first, which waits briefly for the case's statement lock (R1d). 404 unknown |
| `POST /reconciliation/cases/{id}/actions` | `write` | `{action, params}` → `ActionOut` (201; 200 with `replayed: true` for an identical earlier request) |
| `POST /reconciliation/cases/{id}/dismiss` | `write` | `{note}` (1..1000) → `ActionOut`; the `dismiss` action |
| `GET /reconciliation/actions` | `read` | query `statement_id`, `case_id`, `limit` 1..1000 (200) → `ActionOut[]`, newest first |
| `POST /reconciliation/actions/{id}/revert` | `write` | → the new `revert` `ActionOut` (201); the original becomes `reverted`, its case reopens at `version + 1` |
| `GET /reconciliation/audit` | `read` | query `statement_id`, `limit` 1..1000 (200), `since` (ISO datetime, naive = UTC), `event`, `offset` ≥ 0 (#201) → `AuditOut[]` (`event`, `actor`, `detail`, `request_id`, ...), newest first |
| `POST /reconciliation/cases/{id}/proposals` | `propose` | `{action, params, rationale, confidence}` → `ProposalOut` (201); the case becomes `proposed` |
| `GET /reconciliation/proposals` | `read` | query `status` (`pending`, `applied`, `rejected`, `superseded`), `case_id`, `limit` → `ProposalOut[]`; stale pending rows are superseded first; one that could not be (its statement or case is locked by another transaction) is hidden from the listing until it is |
| `POST /reconciliation/proposals/{id}/apply` | `write` | → `ActionOut` (the stored action applied as `owner`; 201, 200 on replay) |
| `POST /reconciliation/proposals/{id}/reject` | `write` | optional `{note}` → `ProposalOut`; reopens the case when it was the only live proposal |
| `POST /accounts/{id}/statements` | `write` | `ManualStatementIn` (the import header and up to 2000 `lines`, no run/lease/file fields) → `RevisionOut` (201); `origin='manual'`, audited, policy runs after the commit when live; an equal-to-persisted resubmission answers 201 `replayed: true` and skips the policy |
| `POST /accounts/{id}/statements/{sid}/confirm` | `write` | `{force=false, note?}` → `{status, checks, reopened_cases, forced}` |
| `POST /accounts/{id}/statements/{sid}/retire` | `admin` | `{note}` (1..1000, not blank) → `{statement_id, mode, already_retired, released_coverage, retired_events, superseded_cases}`; 409 `actions_applied` |

Changed R1b routes: `POST /accounts/{id}/statements/{sid}/reconcile` also returns `policy: {applied, proposed}`;
statement list and detail carry `incoming_deferrals_pending`; `PUT /settings/reconciliation` accepts `policy` and
`account_siblings` (below). `hermes` (`read,propose`) gets 403 on every other mutation route.

Actions (`params`; the kinds a case must have; `actions_allowed` lists what a case offers now):

| Action | Params | Case kinds | Policy |
| --- | --- | --- | --- |
| `match` | `line_id`, `entry_id` xor `group_id`, `with_children=false`, `allow_sibling=false` | `line_unmatched`, `ambiguous`, `amount_delta`, `recheck` | agent proposals only |
| `unmatch` | `line_id` | `duplicate_claim`, `recheck` | no |
| `dismiss` | `note` | all but `statement_conflict` | no |
| `defer_next_period` | `entry_id` | `entry_unmatched` | no |
| `link_installment_plan` | `definition_id` | `line_unmatched` (hint `installment_unmapped`) | no |
| `create_entry` | `kind` (`expense`/`income`), `category_id`, `name?`, `project_id?` | `line_unmatched` | no |
| `create_system_entry` | `kind` (`fee`/`interest`/`reward`) | `line_unmatched` (bank-only hint or fee/interest/reward line) | yes |
| `backfill_entry` | `kind` (`expense`/`income`) | `line_unmatched` | yes |
| `anchor_opening` | none | `balance_gap` (bank account; needs `dirty_enabled`) | yes |
| `replace_stand_in` | none | `entry_unmatched` (stand-in hint) | yes |
| `create_refund` | `original_entry_id` | `line_unmatched` (refund line) | no |
| `attach_fee` | `entry_id` | `amount_delta` with `fee_matches` (bundled fee only) | yes |
| `adjust_amount` | `entry_id`, `line_id`, `group_id` (optional: a foreign group case) | `amount_delta`; `ambiguous` with a listed entry candidate (no `group_id`) | no |
| `adjust_transfer` | `entry_id`, `line_id` | an `amount_delta` whose entry is a transfer leg (any hint, incl. `date_drift`) and an `ambiguous` listing a transfer leg (`CONDITIONS`: `_designates_transfer_leg` / `_lists_transfer_leg`); owner only; rewrites both legs to the statement figures; both statements' cases resolve | no |
| `pair_transfer` | `peer_line_id` | `line_unmatched` (hint `needs_peer`); owner, or the policy under `actions.pair_transfer.max_amount` (default off); books one transfer from two complementary statement lines; the policy needs the twin and occupancy guards to pass | yes (default off) |
| `move_account` | `entry_id`, `account_id` | `entry_unmatched` | no |
| `split_entry` | `entry_id`, `parts[{amount, category_id, name?}]` (2 or more) | `amount_delta`, `line_unmatched` naming the entry | no |
| `balance_adjustment_asof` | none | `balance_gap` of a bank or single-account card statement (needs `dirty_enabled`) | no |
| `resolve_conflict` | `keep` (`current`/`new`) | `statement_conflict` | no |

Actions on historical statements: only `match` and `unmatch` (coverage); cases exist on live statements only, so there is
no line route yet. Created rows carry `source='statement'`, `needs_review` (except `create_entry`) and a description
starting `對帳建立`. `revert` works while the touched rows still equal what the action left; `resolve_conflict` cannot be
reverted (submit the statement again).

Statement checks (2026-10-17). Every revision runs the guardrails `equation`, `currency`, `period_length` (at most 62
days), `dates_in_window`, `installments`, `line_count`, `opening_present` (bank) and, on card statements only,
`closing_day`: when the card has a `closing_day`, `period_end` and, when printed, `closing_date` must each lie within 3
days of that day of the month (a day past the month's end means its last day; month ends are compared across). A
misread period (the due date read as the closing date, a calendar month on a card that closes mid-month, or a holiday
shift beyond 3 days) fails it; the revision gets `guardrail_ok = false` and a `parse_review` case, matches nothing and
does not replace a passing current revision. `revision.guardrail.detail.closing_day` holds `expected_day`, `period_end`,
`period_end_days_off`, `closing_date` and `closing_date_days_off` (signed; null without a closing date). Cards without
`closing_day` and all bank statements skip the check. Balance check: every reconcile computes the statement's gap at
`period_end` against the held balance (`held_balance`, coverage-based): the ledger balance at `period_end`, minus rows
posted by then that a later statement of the same accounts claims or that this statement defers to the next one and
no statement claims yet (its resolved `deferred_next_period` cases: the matcher's late entries within `deferral_days`
and the owner's `defer_next_period`, with their children), plus rows posted after `period_end` that this statement
claims. Informational rows (#152, 2026-10-25): a zero-amount `unknown` line — the parse contract's kind for a printed row whose amount is 0 (an annual-fee waiver notice, a reward summary, a zero interest line) — is not reconciled: no representation, no case, not a guard line (`matching.is_informational`); it stays a line for audit and is counted neither matched nor open. Split groups (2026-10-19): a group is read whole (the net reading: {expense, discount} for a discounted purchase) and, when the statement prints a member as its own line — the fee member of a purchase abroad, printed as its own 國外交易服務費 line, a `split` member of kind fee / interest / reward / refund whose flow equals such a line's — also without any combination of those members (one printed fee line excuses one fee member; inside the exact window of that line); a reading with a member another line claimed is dropped; a reading is dropped only when a strict subset of it sums to the same amount (a fee and a reward printed alone go to their own lines); readings that are not nested compete and tie as `ambiguous`. Above 8 printed-alone members in one group the readings are not enumerated and the line is never auto-claimed (`ambiguous`, hint `group_readings_incomplete`). Such a member stands alone for a fee, interest, reward or refund line (never for a purchase, withdrawal, deposit, payment or transfer line; never a member of another group kind). Foreign match (2026-10-19): a `split` group whose counted members all carry the line's foreign currency and whose original amounts sum to the printed foreign amount, within `foreign_window_days`, is a foreign candidate like a standalone entry (admissible as for an exact group claim): one candidate → `amount_delta` with `group_id` and `entry_ids` in the context (no `entry_id`; `fee_expected` is the account's fee on the group's total), beside a standalone twin → `ambiguous`. The owner resolves it with `adjust_amount` + `group_id` (the members the line counts — unclaimed top-level members of a kind the line's gate allows, rebuilt under the locks and required to equal the case's `entry_ids`, else 409 `candidate_changed` — are claimed and the named one absorbs their residual; a fee member its fee line claimed stays with that line) or by editing a member in the ledger (the next reconcile claims the group exactly). The matcher never defers the one entry an `amount_delta` case (near, foreign, drift) designates for its line, nor the members of the foreign group it designates;
ambiguous alternatives and secondary candidates defer as usual. Bank: `statement_total − held`; card:
`−statement_total − held(participating accounts in the statement's currency)` (the amount owed is positive on the
statement, card balances are negative; a co-branded card billed on the same statement counts). A deferral the next
statement's confirm reopens counts again. While an open line case designates a late entry, the gap assumes that entry
is printed here: resolve the case before booking the gap. A non-zero gap opens a `balance_gap` case (`context.gap`; card cases also carry
`card: true`); a negative gap means the ledger lacks an outflow on either kind. On a card `anchor_opening` is never
offered and the policy never acts; `balance_adjustment_asof` books the gap on a single-account card (a card with a co-branded account in the statement's
currency is refused `combined_card`: the residual may sit on either account).

Settings (`GET`/`PUT /settings/reconciliation`; a PUT replaces `account_map`, `composite_map`, `dirty_enabled`, `rules`, `policy` and
`account_siblings` wholesale, so send the current values of the ones you keep; `admin` scope):

- `policy` (default, ships off): `{"enabled": false, "accept_agent_proposals": false, "per_statement_count": 5,
  "per_statement_total": "5000", "monthly_total": "20000", "per_currency_enabled": ["TWD"], "actions":
  {"create_system_entry": {"enabled": false, "max_amount": "2000"}, "attach_fee": {"enabled": false, "max_amount": "500"},
  "match": {"enabled": false, "max_amount": "0"}}}`. Any subset may be sent (actions merge per action); unknown keys,
  non-boolean flags, a count outside 0..1000, non-amount strings and non-ISO currency codes are 422. `policy_version` is
  `r1c-<settings version>` and changes with the effective policy.
- R6b policy keys: `backfill_grace_days` (default 14: a line is backfilled only that many days after its statement's period end), `backfill_accounts` (default `[]` = every account; otherwise the account ids the policy may backfill) and three more `actions`: `backfill_entry` (`enabled: false`, `max_amount` "5000"), `anchor_opening` (`enabled: false`, `max_amount` "200000") and `replace_stand_in` (`enabled: true`, `max_amount` "0"). `policy_version` is now `r6b-<settings version>`. `backfill_entry` books an unmatched bank or card line as a stand-in entry in the category `對帳待分類` (`needs_review`, description starting `對帳建立`); `anchor_opening` books the opening-balance anchor for an account's `balance_gap` (one per account per run; refused with `closing_adjustments_present` on apply and on revert while a later `balance_adjustment_asof` exists, revert that first); `replace_stand_in` removes a stand-in once an owner entry covers the same line (its policy amount is 0, a deliberate budget exemption, but it counts toward `per_statement_count`; replacements are never reverted). Owner-deleted rule: delete a backfilled entry in the ledger and the policy will not recreate it; dismiss the case or re-run the action from the case (`backfill_entry`/`create_system_entry` stay available to the owner). The policy never backfills or books a system entry for a line an owner entry may already record: one that alone conserves the line within the drift window (a MOZE `split` member included, which the matcher never claims alone), one within the matcher's near tolerance (`near_tolerance_abs` / `near_tolerance_pct`, `near_window_days`; an FX amount the owner estimated — no merchant overlap required), an unclaimed child entry (an owner's fee under a purchase, for a fee line), the members of a `split` group subset (never a reward_claim or installment group; members of kinds eligible for the line) that together conserve the line exactly, or within the near tolerance and the near window (2026-10-19: a purchase abroad split into an expense and a receivable, estimated in TWD without the FX fee; groups above 8 eligible members as a whole only), or an event whose stand-in was already replaced; the case detail lists those entries as `owner_twins` (computed on read) so the owner sees why the line stayed open and what to pair it with; such a pending policy proposal is superseded by the next policy run (audit `proposal_superseded`, `by: owner_may_record`) and applying it before then is 409 `owner_may_record`. Both actions take the account row and the writer barrier and re-check this under it, so a twin committed during the apply is caught too; the policy and policy proposals therefore need `dirty_enabled` (otherwise refused `dirty_disabled`). The owner may still book the line from the case. A second `replace_stand_in` on the same event is refused with `duplicate_effect` while the first replacement stays applied; delete the stand-in by hand in that case.
- R6c policy keys: rules `transfer_tolerance_pct` (default "0.10", bound 0..1) and `transfer_pair_window_days` (default 7, bound 0..60), and a further `actions.pair_transfer` block (`enabled: false`, `max_amount` "50000"). `policy_version` is now `r6c-<settings version>`.
- R6c transfer matcher note (2026-10-25): a transfer line (kind `payment`, `transfer_in` or `transfer_out`: `matching.PEER_LINE_KINDS`; bank `withdrawal`/`deposit` lines count only as peer lines for `adjust_transfer`) whose same-account leg is within `transfer_tolerance_pct` of the printed amount, with no merchant condition, becomes an `amount_delta` case with hint `transfer_delta` (`matching.TRANSFER_HINT`) designating that leg; `adjust_transfer` resolves it. A transfer line with no leg stays `line_unmatched` (`needs_peer`) for `pair_transfer`. The dry-run `--transfers` mode counts both per account (`scripts/README-r6-dryrun.md`).
- `account_siblings` (default `{}`): `{"<account id>": [account ids]}` for the 綜合月結單 case. Siblings must exist, not be
  archived, share the key account's currency, be distinct and not the key itself (422 otherwise). Unmatched lines of that
  account then list up to five `context.sibling_candidates` (`entry_id`, `account_id`, `flow`, `posted_date`); the owner
  matches one with `match {..., "allow_sibling": true}`. Agents cannot propose `allow_sibling`.

Card statement auto-pay (spec `docs/superpowers/specs/2026-10-26-statement-auto-pay-design.md`; table `statement_auto_pay`, migration `c4a7e2d9f1b3`, which creates the `btree_gist` extension). For each live card statement (`kind='card'`, `mode='live'`) with a `due_date` and a positive `statement_total` whose card has an `auto_pay_account_id`, the job books the payment the bank makes as a system transfer pair (`source='statement'`, name `自動扣繳`, bank account to card master) once the pay date has come: the due date, moved to the first business day on or after it (TW calendar with `extra_holidays` / `extra_workdays`; beyond the built-in calendar only weekends shift and the run reports `calendar_stale`, audited `auto_pay_calendar_stale` once per day). Both legs are dated the pay date; the card leg is posted the next business day. It runs daily at 06:00 Asia/Taipei and once at startup, under an advisory lock (`0x41505041`); a second runner returns `busy`. `ACCOUNTING_SCHEDULER_ENABLED=false` switches this job off together with the schedule job. After `busy` or `import_running` nothing is retried until the next day or the next restart. The CLI (`python -m app.services.auto_pay_job [--dry-run] [--as-of YYYY-MM-DD]`) refuses an `--as-of` later than today (Asia/Taipei) unless `--dry-run` (exit 2).
- Settings block `auto_pay` in `PUT /settings/reconciliation` (defaults): `enabled` false, `enabled_from` null (required, an ISO date, once `enabled`), `max_amount` "200000", `monthly_total` "500000" (booked pay dates in the calendar month), `twin_window_days` 7, `retry_days` 30, `extra_holidays` [], `extra_workdays` []. A PUT that omits `auto_pay` resets the block to its defaults (as it does `policy` and `rules`), so send the whole body.
- Gates, in order; the first failing one is the skip reason: `cutoff` (pay date before `enabled_from`), `duplicate_statement` (another statement of the card whose row is final — `booked`, `retired`, `owner_deleted` or `owner_edited` — with a pay date less than 20 days away), `header_disputed` (an open `parse_review` / `statement_conflict` case), `no_link`, `link_ineligible` (the linked account is not an active, non-credit account in the card's currency), `twin`, `over_cap`, `monthly_budget`. Terminal reasons (`cutoff`, `duplicate_statement`, `twin`, `expired`) are never re-evaluated; transient ones (`header_disputed`, `no_link`, `link_ineligible`, `over_cap`, `monthly_budget`) are retried daily until `retry_days` after the pay date, then become `expired`. Skips are audited `auto_pay_skipped` once per reason.
- Twin rule: nothing is booked (`twin`) when the same payment is already in the ledger, whatever its source (owner, import, policy), as one of three shapes dated within `twin_window_days` of the pay date: a `transfer_in` of the amount on the card's family (the card and the accounts combined into it); a `transfer_out` of the negative amount on the pay account whose peer leg is on the card's family, or that has no peer leg (a transfer from the pay account to savings or to another card is not a twin); a statement-booked `expense` of the negative amount on the pay account. A pair booked earlier is retired (legs deleted, claims released, row `retired`, audit `auto_pay_retired`) when such a twin appears: the daily housekeeping looks for one until `twin_window_days + 7` days after the pay date, and the statement sweep catches later twins. The retire locks the row and both legs and re-checks the row, the legs and the twin under the lock; when another write got there first it does nothing. Housekeeping also moves a booked row to `owner_deleted` / `owner_edited` when the owner deletes or edits a leg (the owner's version stands; audits `auto_pay_owner_deleted`, `auto_pay_owner_edited`) and notes a changed statement header once (`auto_pay_header_changed`).
- Statements: the legs are stand-ins, so the bank statement's debit line and the next card statement's payment line claim them through the matcher rule `auto_pay` (an owner entry that fits the line wins). A leg is never offered `replace_stand_in`; to replace the pair, delete it. Retiring a statement that has a booked pair leaves the pair and its row and audits `auto_pay_statement_retired`.
- Calendar for the SPA: `GET /calendar/business-days?from=YYYY-MM-DD&to=YYYY-MM-DD` (read scope, span ≤ 1100 days) returns `holidays` (the built-in TW table ∪ `extra_holidays`, within the range), `workdays` (`extra_workdays`, within the range) and `last_known_day`; weekends are implied and a listed workday wins, exactly as `business_days.is_business_day` decides. The frontend uses it to shift a card's printed due date to the bank's debit day (#197).
- Rollout: apply the migration, restart the API (the job starts disabled: housekeeping runs, nothing books), run the host dry run, then the owner sets `enabled: true` and `enabled_from` (the first live run books nothing due before it), then watch the first run in `GET /reconciliation/audit`. Rollback: `enabled: false`; booked pairs stay and housekeeping continues.

Notifications (notify/)
-----------------------

`notify/` is a small read-only notifier (spec `docs/superpowers/specs/2026-10-27-accounting-notifications-design.md`): a daily
debit reminder (07:00 Asia/Taipei, sent only when something is actionable (a new or due item, an overdue one, a shortfall, a cleared shortfall or an auto-pay skip)) and a Saturday 10:00 weekly digest (always sent),
delivered through `hermes send` to `discord_target`. It only issues GET requests with a token labelled `notify`, scope `read`
(`token_file` in the config, default `~/.config/homehub-notify-token`, mode 0600); it adds no tables, routes or frontend.
Business days come from `GET /calendar/business-days`; a due date past its `last_known_day` is a failure.

- Config: `~/.config/homehub-notify.toml` (mode 0600), copied from `deploy/notify/homehub-notify.example.toml`. Required keys
  `api_base`, `token_file`, `hub_base_url`, `discord_target` (write `<discord target>` placeholders in anything committed); optional
  `lead_business_days`, `weekly_open_case_limit`, `expected_statement_grace_days`, `min_buffer`, `state_file`
  (default `~/.local/state/homehub-notify/state.json`) and a `[lead_overrides]` table per account id.
- CLI (run from `services/accounting-service` with the notifier venv):

      python -m notify daily  [--as-of YYYY-MM-DD] [--dry-run | --send] [--config PATH]
      python -m notify weekly [--as-of YYYY-MM-DD] [--dry-run | --send] [--config PATH]
      python -m notify failed <unit> [--config PATH]

  Without `--send` the rendered message is printed and nothing is written (`--dry-run` is the same, explicit). `--send` delivers
  and then writes the state file. Exit codes: 0 sent or nothing to send, 1 failure, 2 bad arguments.
- Failures (spec section 7): a failing `--send` run writes one line to `last_failure.txt` next to the state file, delivers nothing
  itself and leaves the state untouched. The user units `homehub-notify-daily.service` / `-weekly.service` carry
  `OnFailure=homehub-notify-failed@%n.service`; that template unit runs `python -m notify failed <unit>`, which sends the stored
  text (or a generic line when the run died before writing one) and always exits 0.
- Units (`deploy/notify/`, user scope for `opc`): `homehub-notify-daily.timer`, `homehub-notify-weekly.timer` (both
  `Persistent=true`), the two services and `homehub-notify-failed@.service`. They run
  `%h/releases/home-hub-notify/venv/bin/python` from `%h/releases/home-hub-notify/current/services/accounting-service`
  with `Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin`: `hermes` lives in `~/.local/bin`, which the systemd
  user manager's default PATH does not contain. Python dependency: `requirements-notify.txt` (`httpx`); Python 3.11+
  (`tomllib`), the venv uses CPython 3.13 like the rest of the repo.
- Rollout (spec section 9), in this order:
  0. Every card the bank debits automatically has its 扣款帳戶 link set in HomeHub; the weekly 設定缺漏 list is the
     checklist, and an unlinked card is reminded as 手動繳 and never reduces a projection.
  1. Add label `notify`, scope `read` to `ACCOUNTING_TOKEN_SCOPES` and its token to `ACCOUNTING_API_TOKENS` in the API
     `.env`, restart through `homehub-api-ctl`, and write the token to `~/.config/homehub-notify-token` (0600).
  2. Write `~/.config/homehub-notify.toml` (0600) from the example (`<discord target>`, hub URL).
  3. Release checkout `~/releases/home-hub-notify/<sha>` (a clean checkout of `<sha>`), the `current` link and the venv.
     The host `python3` is 3.8, too old (`tomllib` needs 3.11), so the venv is made with the uv CPython 3.13:

         ln -sfn ~/releases/home-hub-notify/<sha> ~/releases/home-hub-notify/current
         uv venv --seed --python 3.13 ~/releases/home-hub-notify/venv
         #   or: ~/.local/share/uv/python/cpython-3.13.13-linux-aarch64-gnu/bin/python3.13 -m venv ~/releases/home-hub-notify/venv
         ~/releases/home-hub-notify/venv/bin/pip install -r ~/releases/home-hub-notify/current/services/accounting-service/requirements-notify.txt

  4. First run by hand, BEFORE installing the units (`install.sh` enables the timers with `--now`):
     `cd ~/releases/home-hub-notify/current/services/accounting-service`, then
     `~/releases/home-hub-notify/venv/bin/python -m notify daily --dry-run`, then `--send`; the same for `weekly`; the
     owner confirms both messages in the channel.
  5. Acceptance in the real unit environment (PATH, working directory, venv), before any timer is enabled: copy the units
     without enabling them (from the release's `services/accounting-service`): `mkdir -p ~/.config/systemd/user &&
     install -m 0644 deploy/notify/homehub-notify-* ~/.config/systemd/user/ && systemctl --user daemon-reload`, then
     `systemctl --user start homehub-notify-failed@smoke.service`. It must post the generic line
     「❌ 帳務通知失敗：smoke 非正常結束，請看 journalctl --user -u smoke」 through hermes (or the stored line, if a failing
     hand run left `last_failure.txt`). `failed` always exits 0, so check the channel, then
     `journalctl --user -u 'homehub-notify-*'`: a `failed: SendError` line means `hermes` could not be started or failed
     under the unit environment.
  6. `deploy/notify/install.sh` copies the five units, reloads and enables both timers with `--now` (safe to repeat).
  7. Follow-ups: a Loki/Grafana heartbeat alert (#200), a periodic Hermes review of the digest.

  First-run note (historical data): expect 「⚠ 逾期未繳 N 天」 lines for last cycle's hand-paid bills whose payment is not
  a `transfer_in` on the card (or a 副卡) dated inside `[period_end, next_closing − 1]`, and a daily 帳單未到 line for every
  rule-bearing card whose statements are not ingested. Remedies: record the payment, ingest the statement, or clear the
  card's rule (closing day / due rule) — it then surfaces weekly under 設定缺漏 instead.

Enabling the policy (it does nothing until every step is done): (0) deploy R1d first (the policy's claims, budget, skip audit, supersede order and plan-revert fixes are not in R1c); (1) keep `dirty_enabled` on and reconcile working;
(2) set `per_currency_enabled` and the caps first, `PUT /settings/reconciliation` with the full body and a `policy` that
still has `enabled: false`; (3) enable ONE action (`actions.create_system_entry.enabled: true`, with its `max_amount`),
then set `policy.enabled: true`; (4) watch `GET /reconciliation/audit` for `policy_applied`, `policy_proposed` and
`policy_skipped` and `GET /reconciliation/actions`; revert anything wrong with `POST /reconciliation/actions/{id}/revert`: policy claims are `agent` coverage, kept across reconciles and revertable like the owner's (R1d), until the booked rows are edited (the revert is then refused); (5) add `attach_fee`, and only last `accept_agent_proposals: true` with `actions.match.enabled: true` and
a `max_amount` above 0 (the default `"0"` means an agent match is never applied). The policy runs after the matching transaction committed (reconcile route, revision submit
and manual statement of a live statement, each statement of the daily sweep). It acts only on live statements without an
open `parse_review` or `statement_conflict`, `conflict_open` or `needs_recheck`; it books only a bank-only fee, interest or
reward line (`create_system_entry`), attaches the expected FX fee (`attach_fee`) and, when accepted, applies an agent
`match` on an `ambiguous` case with exactly one admissible representation. Caps: `max_amount` per action, `per_statement_count`
and `per_statement_total` per statement (counted from applied policy actions), and `monthly_total` per month (the calendar
month of the application day) and currency in `policy_budget`. A candidate over a cap becomes a proposal authored `policy`
for the owner; a candidate that fails is rolled back and audited `policy_skipped`. A revert does not refund the budget.
R1d behaviours: a deterministic skip is audited `policy_skipped` once per case version (`detail.case_version`); "agents" for `accept_agent_proposals` are propose-only tokens (`ACCOUNTING_TOKEN_SCOPES`: `propose` without `write`/`legacy`; an owner token's proposal is the owner's draft, and with scopes unconfigured every non-policy author counts); the engine answers 422 `budget` to a policy apply without a budget; the `attach_fee` amount is recomputed under the lock (a changed one is skipped as `candidate_changed`); `revert` of a `link_installment_plan` locks the plan and releases the claims it produced on the account's other statements (reason `plan_unmapped`, those statements get `needs_recheck`, a confirmed one keeps its status but refuses confirm until its next reconcile), answering 409 `action_busy` when a posting or claim raced it (retry). Known limitation: two plan maps for one definition under different `plan_key`s on an account are not refused, and reverting one releases the other's claims too (self-healing on the next reconcile).
Turn it off with `policy.enabled: false`.

Confirm (`POST /accounts/{id}/statements/{sid}/confirm`): first processes the deferrals other statements pointed here (an
entry this statement claimed stays resolved; one still unclaimed reopens the earlier statement's case at `version + 1`,
audited `deferral_reopened`), then runs the checks `current_revision`, `guardrail_ok`, `conflict_open`, `needs_recheck`,
`open_cases`, `pending_proposals` (stale ones are superseded first) and `conserved`. A failing check is 409
`confirm_refused` with the checks as JSON; `force: true` needs a non-blank `note` (422 `note_required`) and confirms anyway
(audited `statement_confirmed_forced`). Incoming deferrals are never a reason to refuse; a set `needs_recheck` is. 409
`confirm_retry`: a deferral link appeared while the locks were taken; repeat the call. Confirming again is allowed.

One file, one statement (fix 2026-10-17): a revision whose `file_id` already backs a non-retired statement of the same
account and currency becomes a revision of that statement whatever period it parsed (the statement identity is
otherwise `(account, currency, period_end)`); a changed period is a header change (`header_changes.period_end`, an open
`parse_review`) unless it corrects a failed current revision — one that failed its guardrails, or a card revision whose `period_end`, the day before its `period_start`, or its `closing_date` is more than 3 days off the account's cycle (`stale_current`: the check landed after the revision did) — and never moves onto a period another statement holds
(same `period_end`, or an overlap — except an overlap with a non-reconciled card statement whose own period bounds (`period_end`, or the day before `period_start`) are off the cycle: misread neighbours overlap each other in chains and are corrected together by re-queueing every member): that revision stays non-current with `context.period_blocked_by`, and promoting such
a `statement_conflict` revision is 409 `period_conflict`. A different account (the folder remapped) still gets its own
statement. Duplicates made before the fix are retired with `POST /accounts/{id}/statements/{sid}/retire` (`admin`):
under the statement lock it refuses 409 `anchor_depends` while an applied `anchor_opening` of the account (on any of its
statements' cases) rests on this statement (recorded in the anchor's `statement_ids`, or this is the account's opening
target; the anchor's statements are locked with it in ascending id order), and 409 `actions_applied` while any action on
the statement's cases is applied (the `revert` rows excepted: `applied` but without an effect of their own), else releases its coverage (`retired`), retires its events, supersedes its open cases and their pending
proposals and its resolved `deferred_next_period` deferrals, unlinks other statements' deferrals pointing at it, sets
`mode = historical` with the note and audits `statement_retired` (that row marks it retired). A retired statement
leaves the pending selection, `stale_events_pending`, `reconcile` (`skipped: "retired"`), the deferral linkers (neither
source nor target), the policy and the dry-run dump; confirm refuses it (409 `statement_retired`); a revision for its
identity is 422 `statement_retired`; the statement list and detail carry `retired: true`; retiring it again is a 200
no-op (`already_retired: true`). Retire sweeps the statement first (409 `sweep_barrier_busy` when the writer barrier
is busy: retry), and the sweep retires applied actions a ledger delete made unrevertable (e.g. a MOZE re-import
re-creating every entry): `match`, `adjust_amount`, `attach_fee`, `move_account`, `split_entry` once EVERY entry they
touched or created (a match's principal, children or group members; a fee child; a split member) and every group they
touched is gone — `status = reverted`, `inverse.deleted_by_event` (`deleted_before_watermark` in the catch-up, which
also releases coverage such an action still holds, e.g. after a delete made with dirty tracking off, and reopens the
line's case in live mode), audit `action_retired`; unlike a retired creation (`stand_in_retired`) this never marks the
line owner-deleted, so the owner, the matcher and the policy may claim it again. An action with SOME targets gone and
some surviving (a split whose original was re-imported away while its members and group stay; a `with_children` match
whose child is gone; an `attach_fee` whose fee child is gone) keeps a financial effect: it stays `applied`, is counted
(the sweep's `partially_stranded`, the dry-run's `action_partially_stranded`, no audit row) and keeps refusing a retire.

Composite statements (2026-10-18): a manual folder listed in `composite_map` (`PUT /settings/reconciliation`, `{"manual/國泰世華": {"91/TWD": {"account_id": 67}, "50/JPY": {"account_id": 81, "anchor_only": true}}}`; section key = the digits the bank prints after the masked account number + the currency; targets are deposit accounts in that currency, one account per folder, never a folder that is also in `account_map`; a PUT replaces `composite_map` wholesale like `account_map`) is split by the worker without a model call: the statement period and the 持有明細 closing balances come from the text layer, every dated row from the word geometry of its page (`pdf.extract_lines`; direction 提出 / 存入 by the token's right edge against the column header), the running balance is verified row by row and against the 持有明細 balance, and every ambiguity fails the file (`failed / parse`), among them a `持有明細` line inside the transaction area and a section header without rows. Rows are keyed by the account suffix and the currency each row carries (2026-10-19): the FX account's JPY and USD rows under one header are two sections with their own balance chains; a currency of a declared account the map leaves out (`50/USD` beside `50/JPY`) is skipped with its rows (`skipped / unmapped`), the rows of an undeclared account still fail the file. One `POST /statements/revisions` per declared section (`parser` `composite-rows`, or `composite-anchor` with no lines when an `anchor_only` key — a section is optional for it, the FX account prints a `外匯活期存款` section only in months with activity — has no section; `raw.section`, `raw.anchor`, `raw.rows`); `_assert_folder_maps` accepts a composite file only for the submitted section's account and currency; `statement_file.account_id` stays NULL. `line_kind` is `deposit` / `withdrawal` by the filled cell, `interest` / `fee` only when the whole description, NFKC-normalised with whitespace collapsed, equals an entry of the worker's table. Outcomes are decided per section in the order guardrail → replayed → anchor / parsed; file status after all sections: any 422 or split error → `failed / parse`, any failed guardrail (replayed or not) → `needs_review / guardrail`, else `parsed`. The run summary carries `composite_files`, `sections_parsed`, `sections_anchor`, `sections_replayed`, `sections_skipped`, `sections_failed` and `composite[]` (per file: the sections with key, outcome and reason label). `GET /statements/account-map` (`ingest`) returns `account_map`, `composite_map`, `closing_days`, `card_accounts` (#205, 2026-10-28: the credit account ids — a mapped file's `statement_file.kind` follows its account, `card` for a credit account and `bank` otherwise; an unmapped file keeps the folder convention, manual root and `銀行帳戶/` → `bank`, else `card`; after the deploy, backfill the rows registered before it by their source folder key against the stored map — `statement_file.account_id` is set only once a revision is submitted, so a file still `new` or failed before its first revision has none: `WITH keyed AS (SELECT f.id, s.root || '/' || array_to_string((string_to_array(regexp_replace(s.drive_path, '/[^/]*$', ''), '/'))[1:2], '/') AS key FROM statement_file f JOIN statement_source s ON s.file_id = f.id WHERE s.removed_at IS NULL AND f.kind = 'bank') UPDATE statement_file f SET kind = 'card' FROM keyed k JOIN reconciliation_settings rs ON rs.id = 1 JOIN account a ON a.id = (rs.data->'account_map'->>k.key)::int WHERE f.id = k.id AND a.is_credit`; a folder unmapped since then is left as registered) and `mapping_version`; `mapping_version` now hashes `{"account_map", "composite_map"}`, so the first deploy re-tries every `mapping` failure once. `python -m worker verify` (2026-10-20) splits a composite folder the same way instead of calling the parser: one report row per declared section (`sha256` = `<file sha>#<suffix>/<CUR>`, the section's account), counted in `composite_files` / `composite_sections`; a split failure counts as `parse_failed` with `error: split_error`; a composite without a usable text layer counts as `no_text_layer` (the pipeline's `needs_review`), a password / size / malformed / timeout failure as `pdf_failed` with the class name; `--limit` counts files. The map file may carry the envelope `{"account_map": {...}, "composite_map": {...}}` (the flat form stays valid and has no composite map); without a map file both come from the settings. Recovery after a split or 422 failure, or after adding a key for an already-parsed file, is the operator re-queue (`UPDATE statement_file SET status='new', failure=NULL WHERE id = …`); `retry_due` is unchanged. Replay: a resubmission equal to what is persisted (same file, parser, `parser_version`, header and stored line columns, compared with the CURRENT revision) answers `replayed: true` without a new revision or case, on `POST /statements/revisions` and on the owner's manual `POST /accounts/{id}/statements` alike, and the policy is not run on a replay.

Owner recovery: a 409 `anchor_depends` or `actions_applied` is cleared by reverting those actions where the engine
allows it (`POST /reconciliation/actions/{id}/revert`). Not every action can be: `replace_stand_in` is never
revertable, and a revert can be refused by its snapshot check (`snapshot_conflict`) or as `not_revertable`. A statement
holding an irreversible or snapshot-refused applied action, or a partially stranded one, has no supported retire path:
it stays as it is and goes to the owner / Lead. There is no shortcut that edits an action's `applied` marker.

Maintenance contract (statements): the `statement_retired` audit row is the retired marker
(`app.services.statements.retired`) — never delete or rewrite it, in any audit cleanup, export/re-import or manual
repair; without it a retired statement is live again (pending selection, reconcile, confirm, deferral links).

Action error codes (422 unless noted):

| Code | Meaning |
| --- | --- |
| `action` "not allowed for this case"; `actor` | the action is not offered for the case kind/context, or not served for that actor |
| `historical_statement` | a historical statement allows only `match`/`unmatch` |
| `duplicate_effect` | the effect slot is taken (one creation per statement event; one coverage-only effect per case version, slot `c<case id>v<version>`) |
| `line_not_in_case`, `line_claimed`, `not_claimed` | line problems (`match`, `unmatch`, creation family) |
| `entry_out_of_scope`, `id_out_of_scope` | the id is not among the case's candidates/context (`id_out_of_scope` also for proposals) |
| `not_found`, `wrong_account`, `wrong_currency`, `entry_claimed`, `with_children_on_child`, `entry_in_group`, `empty_group`, `not_conserved` | `match` and entry gates (`with_children_on_child`: a child is matched alone, as the line's principal) |
| `line_not_printed`, `kind_sign`, `refund_sign`, `refund_exceeds`, `not_refundable` | creation family (`line_not_printed`: the case's line is not in the current revision; also `attach_fee`, `adjust_amount`) |
| `fee_sign`, `fee_out_of_bounds`, `fee_quantum`, `fee_unexpected`, `not_foreign`, `not_standalone` | `attach_fee` (also `not_standalone` for `adjust_amount`) |
| `fx_pair_required`, `foreign_zero`, `kind_sign`, `not_member` | `adjust_amount` (`fx_pair_required`: an FX entry needs the line's printed pair, and a line printing only half of it is refused; `not_member`: with `group_id`, `entry_id` must be a top-level member of that group, and the group must be the one the case names — `entry_out_of_scope` otherwise) |
| `not_movable`, `archived`, `currency`, `same_account`, `account_id` not found | `move_account` |
| `not_splittable`, `parts_sum` | `split_entry` |
| `dirty_disabled`, `zero_delta` | `balance_adjustment_asof` |
| `note_required` | `confirm` with `force` and no note |
| `sibling_not_proposable` | `allow_sibling` in a proposal |
| `not_revertable` | revert of a revert, a reverted action, a later action on the same rows or the same case, released coverage, or an action without inverse |
| 409 `action_busy` | a row the action needs is locked by another write (retry) |
| 409 `snapshot_conflict` | a row changed between the read and the locks (reload the case) |
| 409 `case_moved`, `case_closed` | the case version moved on, or the case is not open/proposed |
| 409 `action_reverted`, `action_failed` | an earlier action under the same key |
| 409 `protected`, `has_settlements`, `fee_exists`, `plan_already_mapped` | the entry/plan state forbids the action |
| 409 `conflict_stale`, `conflict_guardrail_failed` | `resolve_conflict` |
| 409 `proposal_not_pending`, `proposal_superseded`, `proposal_mismatch` | proposal apply/reject |
| 409 `sweep_barrier_busy`, `reconcile_busy`, `import_running` | a writer barrier, a candidate row or an import is in the way (retry) |
| 409 `budget_exceeded`, `cap_exceeded`, `policy_disabled`, `action_disabled`, `statement_excluded`, `candidate_changed`, `not_single_admissible` | policy outcomes, never an HTTP error: a candidate over a cap or the monthly budget becomes a proposal authored `policy` (audited `policy_proposed`); only an agent's own proposal over a cap is audited `policy_skipped` (it stays pending for the owner); `action_disabled` is only counted, not audited; the rest are audited `policy_skipped` |
| 409 `confirm_refused`, `confirm_retry` | confirm |

Migrations (R1b adds two revisions after `d1f3a7c2e9b4`, run `alembic upgrade head` before restarting): `e2a9c4d1b7f0`
replaces the dirty trigger functions so every ledger writer takes the shared advisory lock `0x44495254` (the sweep takes it
exclusively to read a committed event cap; triggers and table unchanged, downgrade restores the old bodies), and
`f3b1d2c4a9e7` adds the nullable `statement_event.flag`. The sweep needs READ COMMITTED isolation and
`coverage_dirty_id_seq` with CACHE 1 (the default).

Amounts are unsigned decimal strings (at most 4 decimals; the `kind` or the endpoint gives the direction) and dates
are Asia/Taipei local `YYYY-MM-DD`. Through Caddy the paths carry the `/api/accounting` prefix:

    export HUB=https://<hub tailscale hostname>/api/accounting
    export AUTH="Authorization: Bearer $ACCOUNTING_AGENT_TOKEN"

    # list accounts (balances as of today)
    curl -sf -H "$AUTH" "$HUB/accounts"

    # create an expense
    curl -sf -H "$AUTH" -H 'Content-Type: application/json' -X POST "$HUB/entries" \
      -d '{"account_id": 3, "kind": "expense", "amount": "120.50", "entry_date": "2026-10-03",
           "category_id": 12, "name": "Lunch"}'

    # create a transfer (same currency; a cross-currency transfer also needs "in_amount")
    curl -sf -H "$AUTH" -H 'Content-Type: application/json' -X POST "$HUB/transfers" \
      -d '{"from_account_id": 3, "to_account_id": 5, "out_amount": "5000", "entry_date": "2026-10-03"}'

    # settle a receivable (entry 42, kind receivable): money arrives in account 3, at most the open amount
    curl -sf -H "$AUTH" -H 'Content-Type: application/json' -X POST "$HUB/entries/42/settle" \
      -d '{"account_id": 3, "amount": "300", "entry_date": "2026-10-03"}'

    # month summary
    curl -sf -H "$AUTH" "$HUB/entries/summary?month=2026-10"

    # daily summary (from the calendar PR; 404 until that lands)
    curl -sf -H "$AUTH" "$HUB/entries/summary/daily?month=2026-10"

Statement ingest worker (R2)
----------------------------

`worker/` is a separate package (same venv; install `requirements-worker.txt`) that turns the owner's Google Drive
statement PDFs into statement revisions through the ingest API (design
`docs/superpowers/specs/2026-10-08-statement-reconciliation-design.md`, section 17 "R2 implementation notes").
The run path talks to the API only over HTTP and never imports `app.database`; `verify` reads the ledger in-process
through a read-only role. Ops files (systemd user units, `gate.sh`, the read-only role SQL, the section 3 checklist)
are in `deploy/statements/README.md`.

    python -m worker run [--trigger timer|owner_cli]   # full run: list, download, unlock, mask, parse, submit, sweep
    python -m worker poll                              # claim enqueued runs only (timer, every 5 minutes)
    python -m worker backfill --acknowledge-live-periods
    python -m worker gate                              # operator gate: canaries + host checks (deploy/statements/gate.sh)
    python -m worker export-masked --for-verify [--folder PREFIX] [--limit N]
    python -m worker verify [--limit N]
    python -m worker flex [--dry-run] [--query-id ID] [--from-file XML] [--legacy-until DATE] [--unlatch]   # IB Flex statement -> portfolio

Exit codes: `0` success (a `done` run with transient codes in `errors` is still 0; `already running` is 0 for `run`/`poll` so timers never
fail on overlap, and 3 for `gate`, `verify` and `export-masked`, which operators must notice); `1` a run `failed` or `aborted`, `parser_disabled`, any verify/export error (`verify` also exits 1 on `parse_failed > 0` or `verify_errors > 0`), a failed gate;
`2` refused (missing token or password file for `run`, `poll` and `backfill` only; `backfill` without `--acknowledge-live-periods`, `verify` without
`STATEMENT_VERIFY_DB_URL`). Output is one JSON line of codes and counts (never text, names or paths).

`flex` exit codes: `0` imported (errors in the body are counted, not fatal); `1` any code in `codes` (`flex_disabled`, `failed`, `transient`, `stale_fx`), the latch, or an unexpected exception (class name on stderr); `2` credentials file missing (unless `--from-file`) / malformed, a malformed `--query-id` (digits only) or a config error; `3` already running.

Interim principal (accepted deviation, not compliance). The dedicated users of design section 3 do not exist yet, so the
worker runs as `opc`, accepted by the owner on 2026-10-10 ("ok ruling 11"). Scope: `export-masked` and `verify` only
(under the read-only `accounting_ro` role); no real-data `run`/`backfill` and the reconciliation feature flag stays off
until the section 3 users exist (checklist in `deploy/statements/README.md`); the residual host access of `opc` is
acknowledged. Expiry = the section 3 user split.

Configuration (all optional; empty means default):

| Variable | Default | Meaning |
|---|---|---|
| `STATEMENT_API_URL` | `http://127.0.0.1:8000` | accounting API base URL |
| `STATEMENT_API_TOKEN_FILE` | `~/.config/homehub-statement-token` | file holding only the `worker` label's token value (the one in `ACCOUNTING_API_TOKENS`, scope `ingest`), mode 0600 |
| `STATEMENT_STATE_DIR` | `~/.local/state/home-hub-statements` | run state, lock, inbox, staging, gate evidence, latch (0700) |
| `STATEMENT_PASSWORD_FILE` | `~/.config/homehub-statement-passwords.env` | per-folder PDF passwords and identity (mode 0600) |
| `STATEMENT_RCLONE_REMOTE` | `gdrive:` | rclone remote |
| `STATEMENT_DRIVE_ROOT` | `財務對帳單` | Drive root; the worker lists `<root>/銀行` and `<root>/手動下載` |
| `STATEMENT_MINIO_ENDPOINT` | unset | object store; unset skips the put (`object_key` is still `by-sha/<sha256>.pdf`) |
| `STATEMENT_MINIO_BUCKET` | `homehub-statements` | bucket |
| `STATEMENT_MINIO_KEY_FILE` | unset | access/secret key file (two lines) |
| `STATEMENT_PARSER_CLI` | `/home/opc/.local/bin/claude` | Claude Code CLI binary; set it to the pinned copy (see "Pinned CLI copy"), because the default is the auto-updater's symlink |
| `STATEMENT_PARSER_CLI_VERSION` | `2.1.296` | pinned CLI version; any other version disables parsing (`failed/sandbox`) |
| `STATEMENT_PARSER_MODEL` | `claude-sonnet-5-5` | pinned model |
| `STATEMENT_PARSER_CONFIG_DIR` | `~/.local/state/home-hub-parser/claude` | parser login directory (worker) |
| `STATEMENT_PARSER_SANDBOX` | `true` | run the parser under bubblewrap |
| `STATEMENT_PARSER_ALLOW_UNSANDBOXED` | `false` | must be `true` for `STATEMENT_PARSER_SANDBOX=false`; otherwise the parser refuses to start (tests only) |
| `STATEMENT_PARSER_TIMEOUT` | `120` | seconds per parser attempt |
| `STATEMENT_OCR` | `true` | sandboxed OCR child (RapidOCR, no network) for PDFs without a text layer (R5), and to repair `(cid:N)` merchants of a text-layer parse (#206: amounts stay the text layer's; a line takes the OCR merchant only on a unique date/amount/kind match); `false` → needs_review/no_text_layer, no repair |
| `STATEMENT_OCR_THREADS` | `2` | onnxruntime intra-op threads in the OCR child |
| `STATEMENT_OCR_TIMEOUT` | `120` | wall-clock seconds per file for the OCR child, model load included |
| `STATEMENT_OCR_RLIMIT_MB` | `2048` | RLIMIT_AS of the OCR child (measured peak 1.2 GB) |
| `STATEMENT_OCR_CHILD` | unset | tests only: shell words replacing `<interpreter> -B -m worker.ocr_child …` |
| `STATEMENT_PARSER_ATTEMPTS` | `3` | attempts per file |
| `STATEMENT_VERIFY_DIR` | `~/.local/state/home-hub-verify` | verify area: `masked/`, `parsed/`, `reports/`, `pdf-cache/`, `sources.json` |
| `STATEMENT_VERIFY_DB_URL` | unset | read-only role URL; required by `verify`, no fallback; keep it only in `~/.config/homehub-statements.env` (0600) |
| `STATEMENT_VERIFY_PARSER_CONFIG_DIR` | `~/.local/state/home-hub-parser/claude-verify` | read-only copy of the parser login for `verify` (chmod 500 dir, 400 files) |
| `STATEMENT_ACCOUNT_MAP_FILE` | unset | account map JSON for `verify` while the feature flag is off (same key format as settings) |
| `STATEMENT_FLEX_CREDENTIALS_FILE` | `~/.config/homehub-portfolio-flex.env` | `flex`: file with `IB_FLEX_TOKEN` and `IB_FLEX_QUERY_ID`, mode 0600 |
| `STATEMENT_FLEX_BASE_URL` | `https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService` | `flex`: IB Flex Web Service base URL |
| `STATEMENT_FLEX_TIMEOUT` | `60` | `flex`: HTTP timeout in seconds (IB and portfolio calls) |
| `STATEMENT_FLEX_POLL_ATTEMPTS` | `12` | `flex`: GetStatement attempts while IB reports the statement pending |
| `STATEMENT_FLEX_POLL_INTERVAL` | `10` | `flex`: seconds between those attempts |
| `STATEMENT_PORTFOLIO_API_URL` | `http://127.0.0.1:8001` | `flex`: portfolio service base URL |

IB Flex statements (2026-10-20). `python -m worker flex` fetches the saved Flex Query as XML (SendRequest, then GetStatement; the token travels only in the `t` query parameter, never in a header or the portfolio request), saves it as `<state_dir>/flex/flex-<ts>-<pid>.xml` (0600) and posts it to `POST /api/portfolio/imports/ib-flex` (query parameters `dry_run`, `legacy_until`). The adapter is `broker_ib_flex_service` in the portfolio service. It reuses the existing fingerprint functions with the Flex tradeID / transactionID in the note, so two identical same-day fills stay two rows; the market comes from `listingExchange` (LSE for LSE/LSEETF/LSEIOB); cash amounts keep IB's sign; the parsed key is quantized to the column scale (fee/tax 2 dp, quantity/price 4 dp) and dates are UTC. Imported sections: Trades (EXECUTION level, STK) and CashTransactions. Reported, not imported: Transfers and CorporateActions. Ignored: OpenPositions and ConversionRates (positions derive from transactions; corporate actions are reported, not applied). Legacy pass: with `legacy_until=<date>` the existing rows dated up to that day that came from the manual IB Transfer-History CSV are paired one-to-one with Flex rows by business key (symbol, type, quantity, price, date, currency, fee, tax; cash: date, type, signed amount, currency), the pass is fingerprint-first, and a paired row ADOPTS the Flex fingerprint, so no later import re-creates it; a key with unequal counts is reported as `ambiguous legacy match` and nothing is paired for it. Provenance: an existing row is manual-era only if its fingerprint is NULL or equals the CSV-style fingerprint recomputed from its stored fields (market ∈ {stored, US, LSE}; note = name / note); any other fingerprint is Flex-origin (inserted or adopted by an earlier Flex import) and never pairs, so a later file that lacks an event cannot hand that row to another event. The daily run never pairs. `legacy_pairs` (the row ids) is returned on every run, and the dry run is where you read it as the pairing evidence, plus `legacy_duplicates`, `error_counts` and `candidates`. The legacy key quantizes quantity, price and cash amount to 4 dp and fee/tax to 2 dp. `buySell` must be exactly BUY or SELL with a matching quantity sign (BUY positive, SELL negative), else a row error; CashTransaction rows are read at DETAIL level only. `error_counts.stale_fx` counts missing-FX rows dated more than 3 days ago in Asia/Taipei; the worker reports it as the code `stale_fx` (exit 1, no latch). The worker's JSON summary keys are `dry_run, source, bytes, saved, parsed, created, skipped_duplicates, legacy_duplicates, candidates, errors, error_codes, recalc_scheduled, stale_fx, codes`; `error_codes` is an allowlist of code labels with counts (never text). Failures: IB status codes 1001, 1003-1009, 1018, 1019, 1021 (`IB_TRANSIENT_CODES`), HTTP 5xx / 408 / 429, timeouts, `get:pending` and `portfolio:body` (malformed portfolio response) are transient (exit 1, no latch); any other IB code, an HTTP 3xx (`http:3xx`), an unparseable response or a portfolio 4xx latches `<state_dir>/flex-disabled.json` until a plain `python -m worker flex --unlatch` run ends with no codes (a `stale_fx` day keeps the latch), but only for a plain daily run (`--dry-run`, `--query-id`, `--from-file` report `failed` codes without latching and never clear the latch). Rule: once Flex is live the IB Transfer-History CSV is never uploaded again (a CSV row and its Flex twin carry different fingerprints; the legacy pass replaced the CSV fingerprint by adoption), and the manual-era overlap is handled exactly once, by the backfill run with `--legacy-until`. Units and the backfill procedure: `deploy/statements/README.md`.

Pinned CLI copy. `~/.local/bin/claude` is the auto-updater's symlink, so the pin (`STATEMENT_PARSER_CLI_VERSION`) would
break the day the updater moves it. Copy the pinned binary and point the worker at the copy:

    mkdir -p ~/.local/state/home-hub-parser/bin
    cp "$(readlink -f ~/.local/bin/claude)" ~/.local/state/home-hub-parser/bin/claude-2.1.296
    chmod 500 ~/.local/state/home-hub-parser/bin/claude-2.1.296
    # STATEMENT_PARSER_CLI=$HOME/.local/state/home-hub-parser/bin/claude-2.1.296

Bumping the pin: copy the new binary, set `STATEMENT_PARSER_CLI` and `STATEMENT_PARSER_CLI_VERSION`, run `gate`. The
parser version changes, so every `parse`, `guardrail`, `no_text_layer` and `too_large` file is re-parsed once (a model
call per file); budget for that before bumping. The version also folds in the parser instruction, the OCR fingerprint
and `worker.mask.FINGERPRINT` (bumped whenever a masking rule changes what the parser receives), so a code change to
any of them re-parses the failed files the same way.

Parser logins. The parser runs in a sandbox that sees only its own login directory. Create separate logins, never a
copy of `~/.claude/.credentials.json`: a copied refresh token rotates on use and can log out the owner's interactive CLI
and the Multica agents that share it.

    CLAUDE_CONFIG_DIR=~/.local/state/home-hub-parser/claude claude /login          # worker login
    chmod 700 ~/.local/state/home-hub-parser/claude; chmod 600 ~/.local/state/home-hub-parser/claude/.credentials.json
    CLAUDE_CONFIG_DIR=~/.local/state/home-hub-parser/claude-verify claude /login   # verify login
    chmod 400 ~/.local/state/home-hub-parser/claude-verify/.credentials.json; chmod 500 ~/.local/state/home-hub-parser/claude-verify

The worker's run may refresh its file; `verify` never does (its login is read-only and an expired token fails the
verify with `auth`; log in again and `chmod` as above). Run `gate` after a new login as described in `deploy/statements/README.md`.
For a manual run load the optional env file first: `set -a; . ~/.config/homehub-statements.env; set +a`.
Grant the worker its token by adding a `worker:<token>` item to `ACCOUNTING_API_TOKENS` and `worker=ingest` to
`ACCOUNTING_TOKEN_SCOPES`, and put just the token value into `STATEMENT_API_TOKEN_FILE` (`chmod 600`).

Password file. One `folder=value` line per Drive folder (suffix `.alt`, `.alt2` ... for alternatives; tokens such as
`$ID`, `$BIRTH8`, `$BIRTH6`, `$BIRTH4` with optional slices, `$BIRTH{FORMAT}`, parts joined with `+`), plus the identity keys `STATEMENT_ID_NUMBER`,
`STATEMENT_BIRTH_DATE`, `STATEMENT_HOLDER_NAMES` that drive masking. An inline comment starts with two spaces and `#`.
A literal `+` in a password value is not supported (the grammar splits on `+` to join parts, e.g. `$ID+$BIRTH4`): a bank
whose password contains a plus sign cannot be configured in R2. The values never appear in logs, errors, reports or API calls.
A PDF encrypted with an owner password only (an empty user password, e.g. AMEX) needs no line: the worker tries the
empty password first and treats the file as unencrypted. A file already `failed/password` is retried when the password file
changes (`credential_version`), so add a comment line to it after deploying a worker that gained an unlock path.

Gate and latch. Every `run`, `poll`, `backfill` and `verify` first checks `<state_dir>/gate-<hash>.json` (one evidence
file per parser configuration, valid for 24 h while the CLI version, model, login directory listing, argv template and
sandbox flag are unchanged) and otherwise runs the canary. Any violation (gate or real input: a second tool call, a tool
other than `StructuredOutput`, `num_turns` other than 2, a changed CLI version, an `auth` failure, a schema failure of the
canary) writes
`<state_dir>/parser-disabled.json`. While it exists nothing parses: runs finish `failed` with `parser_disabled` and the
command exits 1. Only a successful operator `python -m worker gate` (worker canary, verify canary and the host checks)
removes it. A canary that merely cannot run (CLI exit, timeout, outage) does not latch: the run skips parsing,
records `gate_unavailable` and finishes `done`; the next run tries again. A file that failed `sandbox` becomes due again once a newer successful gate exists.

Verify flow (read-only against the live ledger; writes only its report):

    python -m worker export-masked --for-verify --limit 5     # download, unlock, mask: snapshots under <verify_dir>/masked
    python -m worker verify                                   # parse (cached by sha256 + parser version), match, report

`verify` parses first, then opens one short `REPEATABLE READ READ ONLY` transaction on `STATEMENT_VERIFY_DB_URL` for the
matching, and writes `<verify_dir>/reports/<UTC timestamp>.json` (0600) containing `parser_version`, `totals` and per-statement
`rows`. Reporting rule: only aggregates leave the machine. Quote `totals` (counts of statements, lines, claims,
unmatched lines and entries, guardrail and parse failures, unmapped, cases by kind) in chats, PRs and reviews; never paste
`rows`, merchant text, amounts, file names, Drive paths or hashes of real statements. The verify area also holds raw
(encrypted) PDFs in `pdf-cache`; see `deploy/statements/README.md` for retention and purging.

Run summary keys (JSON on stdout and `finish.summary`): `listed`, `new_files`, `parsed`, `needs_review`, `failed`,
`ignored`, `skipped`, `unchanged`, `deferred`, `transient`, `too_large`, `cases_opened`, `live_revisions` (new, non-replayed live revisions only, on the composite and the single-account path), `resumed`, `composite_files`, `sections_parsed`, `sections_anchor`, `sections_replayed`, `sections_skipped`, `sections_failed`, `composite`,
`errors` (bounded codes), `sweep`, and `versions` (`parser_version`, `credential_version`, `mapping_version`,
`cli_version`, `model`). `poll` adds `claimed` and `bad_runs`.

Tests
-----

    .venv/bin/pytest

Integration tests create a throw-away database `accounting_test_<random>` on the Postgres server named in the root `.env` and drop it afterwards.

Schedules (週期 / 分期)
-----------------------

Definitions (`schedule_definition`) hold the rule and a template of lines; instances (`schedule_instance`) are the
periods. Posting writes a period through the ledger services in one transaction (`source = 'schedule'`); a failure
stays `pending` with `last_error`.

    GET    /schedules/definitions?status=&kind=          POST /schedules/definitions (optional `loan`)
    GET    /schedules/definitions/{id}                   PUT  /schedules/definitions/{id}    DELETE (only before anything posted)
    POST   /schedules/definitions/{id}/pause | /resume {"backlog": "skip"|"post"} | /end | /catch-up
    PUT    /schedules/definitions/{id}/mode {"posting_mode": "auto"|"confirm"}
    GET    /schedules/instances?from=&until=&status=&definition_id=&queue=true
    PUT    /schedules/instances/{id} {"due_date", "amounts"}
    POST   /schedules/instances/{id}/post | /skip | /reopen | /repost {"amounts"} | /accept-partial
    POST   /schedules/run-now

The daily job runs in-process (APScheduler) at 00:05 Asia/Taipei, about 10 s after startup, after every real backup
import through the API, and every 10 minutes after a run that found the job lock or an import busy. The importer CLI
(`python -m app.services.moze_backup_import_service`) has no scheduler: after a real import it runs the job itself,
once the import lock is released — generation always, posting only when `ACCOUNTING_SCHEDULER_ENABLED` is on (else it
prints the number of due but unposted periods on its `schedule_job:` stderr line). The job generates instances 13 months
ahead and posts due periods of `active`, `auto` definitions dated on or after the definition's `auto_post_from`
(never a reopened period), per definition in `seq` order: a period waits while an earlier one is due or failed.
A pending period due before `auto_post_from`, or a reopened one, is the owner's and does not hold the series — except
when it carries `last_error` (an owner post of it failed): then it holds the later periods until the owner posts or
skips it.
`ACCOUNTING_SCHEDULER_ENABLED=false` turns the in-process job off (tests do); the job lock
(`pg_try_advisory_lock(0x53434844)`) makes a second runner return `busy`.

    .venv/bin/python -m app.services.schedule_job --dry-run     # print what would be generated and posted
    .venv/bin/python -m app.services.schedule_job               # run now (same as POST /schedules/run-now)
    .venv/bin/python -m app.services.auto_pay_job --dry-run [--as-of YYYY-MM-DD]   # every candidate's would-be decision, writes nothing
    .venv/bin/python -m app.services.auto_pay_job [--as-of YYYY-MM-DD]             # run now (housekeeping, then booking when enabled; a future --as-of exits 2)
    .venv/bin/python -m notify daily  --dry-run [--as-of YYYY-MM-DD]   # print the daily debit reminder, send and write nothing (notify venv, see Notifications)
    .venv/bin/python -m notify weekly --dry-run [--as-of YYYY-MM-DD]   # print the weekly digest

Every schedule write shares the import advisory key; during a backup import (a few seconds) schedule writes answer
HTTP 409 `import_running`. An import waits up to 30 s (`IMPORT_LOCK_WAIT_SEC` in `moze_import_service`) for
schedule writers to finish and is refused afterwards (`ImportAlreadyRunningError`, HTTP 409 `import already running`);
the job shares the key per period, so an import started during a long job posting loop may be refused — run it again
once the job is done. A posting failure that finds an import running leaves the period `pending` without
`last_error`; the job retries it on its next run. Before cutover (`ACCOUNTING_IMPORT_LOCKED` not `true`) imported
definitions cannot be edited or deleted and MOZE-booked periods cannot be reposted (409 `locked_until_cutover`);
pause, resume, end, mode, catch-up and per-period actions work. A re-import never duplicates a period HomeHub
posted or skipped (`schedules.past_records_already_posted` / `…_skipped` in the report), nor one the owner edited
while it was pending (`past_records_owner_pending`: MOZE's record is not imported, the period stays pending), and
never touches local definitions. Amount differences are reported per line (`past_records_amount_differs.lines`,
`amount_differs`).

Lock order (D32, every schedule write path): the shared import key (`pg_try_advisory_xact_lock_shared`, 409
`import_running` while an import holds it) → the `schedule_definition` row (`FOR SHARE` when posting or acting on one
period; `FOR UPDATE` when editing, pausing, resuming, ending, changing mode or deleting, and for a post or repost of a
loan template, which may end the definition) → `schedule_instance` rows (`FOR UPDATE`, ascending id; a loan post also
locks the later pending periods) → the ledger's order (`entry_group` rows ascending → the entries with their transfer
legs, and the loan entry of a repost, in one statement). No path holding an entry or group lock locks a schedule row.
An entry delete that touches a period takes the key, the definition (`FOR UPDATE` when it is `ended`, since the delete
may revive it) and the instance before the ledger's locks; if the definition ended while it waited it answers 409
`排程剛結束，請重試刪除`, and the retry revives it. `tests/integration/test_schedule_lock_order.py` pins this with
two overlapping transactions per case (`tests.helpers.race`): the first holds its locks, the second must block, then
complete once the first commits. These tests serialize whole operations; they are not a proof of deadlock freedom
under arbitrary interleavings.
