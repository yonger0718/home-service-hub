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
- `GET /entries?limit&offset&kind&date_from&date_to&q&account_id=(repeatable)&hide_rewards`, `GET /entries/summary?month=YYYY-MM`,
  `GET /entries/summary/daily?month=YYYY-MM` (calendar view: the month summary's expense/income rules per `entry_date`,
  with a `count` of counted rows; days without entries omitted; rewards left out when the preference hides them on
  the timeline; `missing_rates` as in the month summary), `GET /entries/{id}`
- `GET /account-groups`, `GET /categories?kind=`, `GET /projects`, `GET /counterparties` (with `open_amounts` per currency)
- `GET /preference`, `GET /fx-rate?date=&base=&quote=` (for today or a future date, or a date whose release is
  missing, the latest available release; `rate_date` names the release the rate comes from)
- `GET /imports/latest` (reports `kind`: `moze_csv` or `moze_backup`), `GET /imports/schedules?kind=period|installment|skipped_record`

Writes (every write sets `source = 'manual'`; amounts are unsigned with at most 4 decimals; 422 names the field, 409 is a conflict or `locked_until_cutover`)

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
- `POST /imports/moze-backup` (multipart `file` <= 200 MB, optional `renames` JSON object, `?dry_run=&strict=&allow_fx_outliers=`)

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
`ACCOUNTING_SCHEDULER_ENABLED=false` turns the in-process job off (tests do); the job lock
(`pg_try_advisory_lock(0x53434844)`) makes a second runner return `busy`.

    .venv/bin/python -m app.services.schedule_job --dry-run     # print what would be generated and posted
    .venv/bin/python -m app.services.schedule_job               # run now (same as POST /schedules/run-now)

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
