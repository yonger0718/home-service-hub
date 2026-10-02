## Context

The owner records personal finances in MOZE and will switch to HomeHub by 2026-10-31 (hard cutover). This document holds the decisions for the whole migration, agreed in a design interview on 2026-10-01, and the detailed design for phase 1 (this change).

Facts this design relies on:

- **MOZE export** (`MOZE_20261001_170037.csv`): UTF-8 with BOM, 16 columns `帳戶,幣種,記錄類型,主類別,子類別,金額,手續費,折扣,名稱,商家,日期,時間,專案,描述,標籤,對象`. It has 6,976 rows dated 2020/09/15 – 2026/10/01, 61 accounts, currencies TWD/JPY/USD/USDT, and 1,519 amounts with decimals.
- **Record types in the export**:

  | 記錄類型 | Rows | Sign of 金額 |
  |---|---|---|
  | 支出 | 2,520 | − (one row is 0) |
  | 紅利回饋 | 2,327 | + |
  | 應收款項 | 617 | ± |
  | 轉出 | 509 | − |
  | 轉入 | 509 | + |
  | 收入 | 216 | + |
  | 餘額調整 | 70 | ± |
  | 手續費 | 64 | − |
  | 初始金額 | 61 | ± or 0 (no date) |
  | 應付款項 | 39 | ± |
  | 利息 | 36 | − |
  | 折扣 | 8 | + |

- **Fee and discount columns**: 249 rows carry a non-zero 手續費 column (always negative) and 4 rows a non-zero 折扣 column. These are mostly separate from the standalone 手續費/折扣 records; only 2 of 64 standalone 手續費 rows coincide with a fee-column row.
- **What the export does not carry**: links between records (reward ↔ expense, repayment ↔ receivable, card payment ↔ statement, recurring and installment events) and account settings (group, credit flag, closing day, limit, reward rules).
- **Transfers**: of 509 轉出/轉入 pairs, only 337 sit on adjacent rows.
- **紅利回饋 rows** are positive deposits into points and miles accounts; none share account, date and time with an expense.
- **Existing accounting-service**: FastAPI with SQLAlchemy and Alembic on Postgres (`accounting_db` in the `stonk-postgres-1` container). All tables hold zero rows. The production SPA ships the accounting pages, and Caddy proxies `/api/accounting/*` to the uvicorn process on `:8000`.
- **stock-portfolio-service**: already has `broker_account`, a signed `cash_transaction` ledger and an `fx_rate` table. Broker accounts are 國泰主帳戶 (TWD), FT, IB, CS (USD).

## Goals / Non-Goals

**Goals (phase 1)**
- A ledger model that can represent every MOZE record type in the export without loss of the exported fields.
- A repeatable full-replace import whose per-account balances equal MOZE's balances.
- A read-only view in which the owner can check the import against MOZE.

**Non-Goals (phase 1)**
- Entry, reward rules, statements, recurring records, installments, net worth, reports, receivables view, agent API, digest. These come in later phases.
- Reading MOZE backup files.
- Linking accounts to broker accounts.
- Authentication: access stays Tailscale-only. The tailnet contains only the owner's 7 devices; revisit if that changes.

## Phasing (whole migration)

Each phase is its own OpenSpec change with its own spec, plan and review.

1. **`rebuild-accounting-moze-ledger`** (this change; target 2026-10-10): ledger model, MOZE import, read-only accounts view. Done when the import is correct and balances match MOZE.
2. **`add-accounting-entry-and-rules`**: mobile-first entry page (an installable web page used over Tailscale) for expense, income, transfer, receivable/payable and balance adjustment, with per-entry fee and discount. Also account settings, the full reward-rule engine (percent or fixed reward, target account, category filter, campaign window, caps, thresholds, stacking, period-end posting, default fees), and credit-card statement cycles (closing day, due day, limit, period spend). Settings are seeded from the MOZE backup file if it is readable, otherwise from screenshots or web lookup by an agent. This phase includes the cutover runbook: final export, import, lock import, 3-day rollback window.
3. **`add-accounting-insights`**: combined net worth (MOZE accounts plus linked broker accounts, converted to TWD via portfolio `fx_rate`, linked accounts not double-counted, transfers cross-checked), reports by category, subcategory, project and counterparty, and receivables/payables net per counterparty with history.
4. **`add-accounting-agent-integration`**: agent query and write API used by Hermes (entries tagged `source=hermes`, an activity view of agent writes), the updated Hermes `homehub-api` skill, and a weekly Hermes cron job that reads the MOZE changelog, filters for HomeHub relevance and posts a Discord digest, flagging CSV-format changes as high priority. Recurring records and installments (equal payment and fixed interest first) land here or in a follow-up; they are part of the final version but do not block cutover.

Cutover priority if time runs short: phase 1 → entry → rules and default fees → net worth and reports → recurring records → receivables view → digest. **Phase 1 plus entry plus rules is the minimum for cutover.**

## Decisions

### D1. Rebuild accounting-service instead of reusing portfolio's ledger
The portfolio `cash_transaction` types are brokerage-specific (buy_settle, dividend_cash, …), and mixing everyday spending into them blurs both domains. accounting-service has an empty database and live routes, so rebuilding it is the cheapest option. We borrow portfolio's patterns: `NUMERIC(20,4)` with a currency per row, an import `source` per row, and `fx_rate` for conversion (phase 3).

### D2. One table for every record kind
`ledger_entry` holds every kind (expense, income, transfer legs, receivable, payable, balance adjustment, fee, discount, reward, interest, refund). Each row affects exactly one account, by its signed `amount`. An account's balance is `opening_balance + Σ amount`. This mirrors MOZE's model and keeps balance computation trivial. Kind-specific behaviour (pairing, counterparty, category level) is enforced by validation, not separate tables.

### D3. Initial balance lives on the account
MOZE's 初始金額 is an account field; the export emits it as an undated row. The importer stores it in `account.opening_balance` and creates no entry.

### D4. Fee and discount columns become child entries
A parent row's 手續費 and 折扣 columns are stored as separate `fee` and `discount` entries on the same account, date and time, with `parent_entry_id` pointing to the parent. The parent's own `amount` is the 金額 column unchanged. The balance effect of an export row is therefore 金額 + 手續費 + 折扣. This is a hypothesis to be confirmed by the phase-1 reconciliation (see Risks). Separate entries keep the parent's spend clean in category reports and match MOZE's system categories.

### D5. Transfer pairing by amount, unpaired legs allowed
Adjacency in the export is unreliable: rows can interleave, e.g. `+30000, −30000, +7500, −7500` in one minute. So adjacency is only a tie-breaker, never enough on its own.

- **Pass 1** pairs a `轉出` with the immediately following `轉入` only when the currency matches and the amounts are exact negations.
- **Pass 2** pairs remaining same-currency, exactly-negated legs only when the match is unique in **both** directions.
- **Pass 3** pairs cross-currency legs only when the match is unique on both sides.

Simulated on the real export, the passes pair all 509 transfers: 316 in pass 1, 176 in pass 2 and 17 in pass 3. None are left for review. The previous adjacency-first rule would have mis-paired 2 transfers.

Legs that remain unpaired are imported with `needs_review = true`. Balances never depend on pairing, so a pairing mistake cannot be caught by balance reconciliation. That is why the rule must be conservative.

### D6. Full replace, transactional, source-scoped
**Ledger transaction.** One ledger transaction does all of the following:

- deletes every `source = 'moze_import'` entry;
- upserts accounts named in the file;
- inserts the new entries;
- removes categories and projects that are no longer used.

**Categories.** A category is kept while it or any of its descendants is referenced.

**Accounts.** Accounts are never deleted, because settings attach to them in phase 2.

**Renames and disappearing accounts.** The export has no account IDs, so a MOZE rename looks like a disappearance plus a new account.

- An account no longer named in the file and left with no entries is archived, and its `opening_balance` is reset to 0. It therefore cannot keep a stale balance.
- The owner can preserve identity with an explicit `--rename OLD=NEW` mapping.
- The dry-run report lists to-be-archived and to-be-created accounts so that renames are visible before committing.

**Concurrency.** All entry points (CLI, REST, dry run) take one PostgreSQL advisory lock, and a second import is refused rather than queued. Two overlapping full replaces can therefore never interleave.

**Failure reporting.** The `import_run` row is created as `running` in its own transaction.

- On success it flips to `succeeded` inside the ledger transaction, so the report and the data commit atomically.
- On failure it is set to `failed` after the rollback.
- A crash mid-import leaves a stale `running` row, which the next lock holder marks `failed: interrupted`.

Dry runs write nothing.

**Opening balances.** Every account must have exactly one 初始金額 row; a missing or duplicate row rejects the import. The real export has exactly one per account (61/61).

**After cutover.** Import is disabled by `ACCOUNTING_IMPORT_LOCKED`.

### D7. Categories are scoped by record kind
MOZE categories are two-level for expenses, and the export also carries meaningful sub-categories for other kinds (收入/薪水 vs 收入/收款, 應收款項/代付 vs 應收款項/報帳). For system kinds the export repeats the name (紅利回饋/紅利回饋). `category` has `(kind, parent_id, name)`, unique per `(kind, parent_id, name)`. A sub-category is created only when 子類別 is non-empty and differs from 主類別. System kinds (fee, discount, reward, interest, balance adjustment) get one fixed category each.

### D8. Date and time
`entry_date DATE NOT NULL` and `entry_time TIME NULL` are stored separately. They are naive and interpreted as Asia/Taipei; MOZE exports local wall time with no zone.

### D11. Foreign-currency rows are converted at import, keeping the original
The real export has 130 rows across 9 accounts whose 幣種 differs from the account currency. These are mostly JPY or USD spending on TWD credit cards. 金額 is in the row currency, and the export carries no converted amount. The owner chose option C (2026-10-02):

- Convert now with a daily historical rate, keeping `original_amount`, `original_currency`, `fx_rate` and `fx_source` on the entry.
- Replace with MOZE's actual converted amounts (`fx_source = 'moze_backup'`) in phase 2, if the MOZE backup file turns out to be readable.

Rates come from the accounting service's own `fx_rate` cache, filled on demand from the same fawazahmed currency API that stock-portfolio-service uses. The portfolio tables cannot be reused: they start in 2026-06 (JPY) and 2025-12 (USD), while the foreign rows start in 2025-05.

Consequences:

- The 52 accounts without converted rows must match MOZE exactly.
- The 9 converted accounts will differ slightly. MOZE used its own daily rate or the bank's actual amount, and card foreign-transaction fees may be included. The report lists their converted totals, and the owner reviews the differences.
- Phase 2's entry page needs the same fields, because the owner enters a foreign charge in its original currency and the converted amount from the card statement.

### D10. One canonical entry order
The export has 711 groups of rows sharing account, date and time, and fee children share their parent's timestamp. Order is therefore defined once:

1. `entry_date`
2. `entry_time`, with NULL first
3. `seq`, a unique insertion sequence, assigned in file row order with children immediately after their parent

Running balances, pagination and listings all use this order or its exact reverse.

### D9. Read-only API in phase 1
Only GET endpoints and the import endpoint exist. Write endpoints arrive with entry in phase 2. This prevents manual edits that a re-import would silently wipe.

## Risks / Trade-offs

- **[D4 may be wrong]** → If balances disagree, the reconciliation report shows the difference per account, and the fee sign rule is the first suspect. The POC is not done until the owner confirms balances for at least 10 accounts, including 3 credit cards and 1 JPY account, all chosen among the 52 accounts without converted rows.
- **[Converted balances differ from MOZE]** → This is expected for the 9 accounts with foreign-currency rows (D11). The report shows each account's converted total so that the size of the difference is visible. Exact values can come later from the MOZE backup.
- **[The FX source is unreachable during import]** → The import fails before touching the ledger. Fetched rates are cached permanently, so a retry only needs the missing dates.
- **[A pairing mistake is invisible to balance reconciliation]** → Pairing requires an exact opposite amount in the same currency, or a cross-currency match that is unique on both sides. Ambiguous legs stay unpaired and flagged. The import report lists every unpaired leg.
- **[Full replace wipes manual work]** → Only `source='moze_import'` rows are deleted, phase 1 has no write endpoints, and after cutover import is locked.
- **[A MOZE account rename splits history]** → Without a mapping, the old account is archived with a zero balance, so totals stay correct. With `--rename`, identity and settings are kept. The dry run surfaces likely renames.
- **[Six years of history in one transaction]** → About 7k rows; well within Postgres limits, and the import is measured in tests.
- **[The production SPA breaks while the old pages are removed]** → Frontend and backend ship together, and the Caddy `@hub_spa` routes are updated in the same deploy.
- **[Points and miles accounts are valued in TWD]** → They are stored as recorded. Inclusion in net worth is a per-account flag added in phase 3, off by default for points and miles.

## Migration Plan

1. Write an Alembic migration that drops the six legacy tables (all empty; the migration asserts zero rows first and aborts otherwise) and creates `account`, `category`, `project`, `ledger_entry` and `import_run`.
2. Deploy the backend and frontend together, and update the Caddy SPA route list.
3. Run `python -m app.services.moze_import_service <csv>` (or upload through the API), then review the import report.
4. The owner compares balances against MOZE.
5. Rollback: `alembic downgrade` to `8a4c4f9b2d1b` restores the empty legacy schema, and the previous SPA build is republished.

## Open Questions

None blocking phase 1. MOZE backup-file readability is checked separately before phase 2. The owner will place a backup in `~/workspace/moze-backup/`.
