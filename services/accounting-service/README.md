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
and stores future-dated rows, periods and installments in `moze_schedule`. Manual entries and their rule
attachments are never touched. Accounts edited locally (`settings_locally_edited`) keep their settings and, when
the backup no longer names them, are neither archived nor zeroed. Neither importer deletes a category, project or
counterparty without a `moze_id` (CSV-created or created in Settings); the backup importer deletes only unused
rows whose `moze_id` left the backup.
The report lists per-type counts, skipped future rows, `needs_review` reasons and per-account `moze_part` /
`previous_moze_part`; `balanceInfo` is not compared until a rule is confirmed, so the CLI warns
`WARNING: 0 of N accounts compared` and the balances are checked by hand.

API
---

Paths inside the service; the dev proxy and Caddy add the `/api/accounting` prefix.

Reads

- `GET /accounts?include_archived=false&as_of=`, `GET /accounts/{id}?as_of=` (`as_of`, default today in Asia/Taipei:
  balances count entries posted on or before it), `GET /accounts/{id}/summary?date_from=&date_to=` (spend with refunds,
  income, rewards, net and count over entries posted in the range, plus the balance as of `date_to`),
  `GET /accounts/{id}/reward-rules`
- `GET /accounts/{id}/entries?limit=50&offset=0&kind=&date_from=&date_to=&q=`
- `GET /entries?limit&offset&kind&date_from&date_to&q&account_id=(repeatable)&hide_rewards`, `GET /entries/summary?month=YYYY-MM`, `GET /entries/{id}`
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
