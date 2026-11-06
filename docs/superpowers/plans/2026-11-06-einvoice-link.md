# E-invoice Link (電子發票連結) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest the MOF monthly 手機條碼發票彙整 CSVs from Drive and store each invoice once with its items. Link
invoices to ledger entries or split groups (auto when unique, inbox when not). Show the invoice on entry detail, and
let the timeline search match linked item names.

**Architecture:** Four new tables (`einvoice`, `einvoice_item`, `einvoice_link`, `einvoice_seller_mute`). An AFTER
DELETE trigger returns cascaded links to `unmatched`. A pure rules module holds period math, tokens, tolerance and
the one-pass decision. The matcher module computes eligibility and candidates. The service module holds import,
owner actions and mutes. A read module builds the inbox section, the entry-detail block, the picker and the target
list. A new worker subcommand `einvoice` lists `電子發票/財政部`, parses the CSVs, drops the carrier columns, and
posts one import per run. The SPA adds an inbox section, an entry-detail card, a settings section and search hits.

**Tech Stack:** Python 3.13 / FastAPI / SQLAlchemy / Alembic / PostgreSQL; worker on httpx + rclone; Angular 21 +
Vitest.

**Spec:** `docs/superpowers/specs/2026-11-06-einvoice-link-design.md`, rev 3.4 (Multica PASS-with-minors). Plan rev 1.1 folds plan review AGENT-404 (PASS-with-minors). Section
references (§) point into it. The spec is binding; owner rulings marked **(owner)** there are not reopened here.

**Shipping:** three PRs, stacked in this order. Deploy in this order too: the worker posts to the new API.
- **PR A — backend** (Tasks 1–8): schema, rules, settings, import, matcher, owner actions, inbox/entry/search reads.
  Deploys with `einvoice.enabled = false`.
- **PR B — worker** (Tasks 9–10): CSV parser, `worker einvoice`, user timer and README. Based on PR A.
- **PR C — SPA** (Tasks 11–14): models/service and bell, inbox section, entry-detail card, settings and search
  hits. Based on PR A (independent of PR B).
- Task 15 (rollout runbook) lands in PR B, because the runbook is the worker README.

## Global Constraints

- Worktree `~/workspace/home-hub-einvoice`. Implementation branch `feat/einvoice-link`, created from `origin/main`
  (the spec and plan live on `docs/einvoice-link`). PR B and PR C branch from PR A's head. Never checkout or reset
  `main`. Never bare `git stash`. `rm -rf` only on literal paths.
- **Fictional data only.** Tests, fixtures, docs, commits and PR bodies use fictional amounts, seller names
  (「測試商店股份有限公司」, 「範例超商」, `Example SaaS Inc.`), seller tax ids (`00000001`, `00000002`), invoice
  numbers (`AB00000001`…) and fake carrier values (`測試載具`, `/FAKE+01`). Never real account ids, invoice numbers,
  Drive ids or owner data. Drive ids in tests look like `drive-fake-1`.
- **Never run `alembic` or `uvicorn` from a worktree against the real environment.** The service's `.env` loading
  uses `load_dotenv(override=True)` and points at production. Migrations are verified only by the test suite's DB
  fixtures (`pg_engine`, `database_factory` + `alembic_config` in `tests/conftest.py`), as existing tests do.
- Never copy or print any `.env`. Backend tests run from `services/accounting-service` with this exact prefix (the
  values stay inside the shell):
  `set -a; eval "$(grep -E '^(POSTGRES_(HOST|PORT|USER|PASSWORD|DB)|DB_HOST|ACCOUNTING_DB)=' ~/workspace/home-hub/.env)"; set +a; OTEL_SDK_DISABLED=true OTEL_SERVICE_NAME_ACCOUNTING=test OTEL_COLLECTOR_ENDPOINT_GRPC=http://127.0.0.1:1 /home/opc/workspace/home-hub/services/accounting-service/.venv/bin/python -m pytest <args>`
  Below, this prefix is written `$PYTEST`.
- Frontend tests: the controller sets up once before Task 11. It symlinks `frontend/node_modules` to
  `/home/opc/workspace/home-hub/frontend/node_modules`, after checking that `package-lock.json` is identical. It also
  writes a placeholder `frontend/src/environments/environment.ts`, untracked and gitignored, with no token. Run
  `cd frontend && npx ng test --watch=false --include <spec>`. Before each SPA commit run the full
  `cd frontend && npx ng test --watch=false` and `npx ng build`. Never run `npm run config`, `set-env.js`,
  `npm run build` or `npm start` in the worktree.
- **Privacy (§4, §6):** carrier name and carrier number never reach API payloads, DB rows or log lines. The worker
  drops them while parsing. The import schema forbids extra fields. The einvoice worker path names files by Drive id
  only: no file name or Drive path in a payload, log line, error message, summary or staging file name.
- Money is `Numeric(20,4)`. `total_amount` is stored as printed. "Integral" means `A == A.to_integral_value()`.
- Error shapes as today (`app/services/errors.py`): `ValidationError(field, msg)` → 422;
  `CodedConflictError(code, detail)` → 409 with message `<code>` or `<code>: <detail>`; `NotFoundError` → 404.
- A new table goes into `LEDGER_TABLES` in `tests/conftest.py:43-49`. Model and migration must agree for
  `tests/integration/test_migration.py::test_head_schema_matches_the_models`. Migrations inline their SQL (they import
  no app code); trigger SQL parity is unit-tested, as `tests/unit/test_dirty_trigger_sql_parity.py` does.
- Commit trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Conventional commits, scope
  `accounting` (backend/worker) or `frontend`.
- Names fixed by the spec, used verbatim:
  - tables `einvoice`, `einvoice_item`, `einvoice_link`, `einvoice_seller_mute`
  - states `unmatched | ambiguous | near | linked | dismissed | void`; `dismiss_reason` `owner | seller_muted |
    runbook`; `linked_by` `auto | owner`
  - routes `POST /einvoices/import`, `POST /einvoices/{id}/link|unlink|dismiss|undismiss`, `GET /einvoices/mutes`,
    `DELETE /einvoices/mutes/{tax_id}`, `PATCH /einvoices/preferences`
  - settings block `einvoice` with `enabled`, `remind_unmatched`, `date_window_days` (0..7, default 3),
    `mute_prompt_after` (1..10, default 3); `EINVOICE_DEFAULTS`, `_check_einvoice`, `read_einvoice_settings`
  - `InboxCounts.einvoice_ask`, `InboxOut.einvoices`, `EntryDetailOut.einvoice`, list-row `invoice_item_hits`,
    `_text_filter(..., include_invoice_items=...)`
  - worker subcommand `einvoice`; units `homehub-einvoice.service` / `homehub-einvoice.timer`, daily 04:10
    Asia/Taipei
  - SPA copy: 「發票待對帳」, 「有發票沒有帳」, 「不用記帳」, 「選帳…」, 「都不是」, 「選其他帳…」,
    「這家以後都不提醒」, 「發票」, 「解除連結」, 「連結發票…」, 「這筆」, 「整組」

## Plan rulings (where the spec leaves a choice)

| # | Ruling | Why |
|---|---|---|
| R1 | Router `app/routers/einvoices.py`, prefix `/einvoices`, behind `require_feature()` like the statements and reconciliation routers. Scopes: `import`, `match` → `ingest`; `link`/`unlink`/`dismiss`/`undismiss`, `GET`/`DELETE /mutes`, `GET`/`PATCH /preferences` → `write`; `GET /{id}/targets`, `GET /picker` → `read`; `POST /runbook-dismiss` → `admin`. | §7.5 / §8 name `write` for owner actions and mutes; ingest mirrors statements. |
| R2 | The worker parses **every** listed file each run, merges invoices by `(number, issue_date)` with the later file in path order winning, and posts **one** `POST /einvoices/import` per run (each invoice carries its own `source_drive_id`). When no invoice was parsed it calls `POST /einvoices/match` instead. No worker state file. | Overlapping files re-posted one by one would flip totals and `source_drive_id` back and forth on every run, and run the matcher on the stale value in between. One merged import is deterministic, so "re-importing an unchanged file changes nothing" holds. |
| R3 | On conflict, the import updates the row (including `source_drive_id`) only when a value or the item set differs. An identical invoice touches nothing, `updated_at` included. | §6 "Re-importing an unchanged file changes nothing". |
| R4 | Known statuses: `KNOWN_STATUSES = ("開立", "作廢", "折讓")`. Any other value is stored in `status_raw`, keeps state/link, and counts in `unknown_status`. | §4. |
| R5 | `einvoice_link` has `invoice_id` as its primary key (the spec's "unique"). | One link per invoice; no surrogate id needed. |
| R6 | All einvoice writes (import, matcher, owner actions, mutes, runbook) first take `pg_advisory_xact_lock(EINVOICE_LOCK_KEY)`, with `EINVOICE_LOCK_KEY = 0x45494E56` ("EINV"). An `IntegrityError` on the link indexes (a concurrent ledger write) becomes 409 `retry`. | Tens of invoices a month: one serialising key is enough, and the matcher's one-pass decision stays consistent. |
| R7 | **Family conflict in the one pass.** Two invoices whose unique candidates are a group and one of its members (or two members' group and the group) would break §7.6 "never both inside the same group". The decision treats them as contested: both `ambiguous`. | §7.4 decides all invoices before writing; §7.6 is an invariant. |
| R8 | `stamp` and `clear_stamp` ignore `ledger_service.is_locked`: the stamp is link metadata written by the einvoice service, not an owner edit, so locked and unlocked rows behave the same (§5). | Keeps the exact-number rule consistent; after cutover `is_locked` is false anyway. |
| R9 | `offer_mute` is set on `unmatched` cards only, when `seller_tax_id` is non-empty, the seller is not muted, and the count of `dismissed(owner)` invoices of that seller is ≥ `mute_prompt_after`. The mute is created through `POST /einvoices/{id}/dismiss {"mute_seller": true}`. | §8 "the next dismiss card for that seller also offers"; one tap dismisses and mutes. |
| R10 | `GET /reconciliation/inbox?account_id=…` returns `einvoices = []` and `einvoice_ask = 0` (invoices have no account). With `einvoice.enabled = false` the section is empty and the count 0 (§11). The `einvoices` list is sliced to `limit` separately; `einvoice_ask` is counted before the slice. | §9. |
| R11 | Search opt-in is an explicit query parameter `include_invoice_items` on `GET /entries` (default false). Only the SPA timeline sends it. `invoice_item_hits` is filled only when `q` is set and the flag is on. | §10 "Only the entries list behind the timeline search sets the flag"; Hermes and other callers keep today's results. |
| R12 | `GET /einvoices/{id}/targets?days=14` (1..60) backs 「選帳…」 and 「選其他帳…」: eligible targets (§7.2) within ±days of the issue date, any amount, sorted by amount closeness, then date distance, then id. `GET /einvoices/picker?entry_id=` backs 「連結發票…」. `GET /einvoices/preferences` returns `{enabled, remind_unmatched, version}` for the settings page. | §15 leaves JSON names and extra endpoints to the builder. |
| R13 | Runbook dismiss (§13 step 3) is `POST /einvoices/runbook-dismiss {"invoices": [{"number", "issue_date"}]}` (admin). It applies the owner-dismiss path with reason `runbook` and answers `{dismissed, not_found}` (`not_found` = positions in the input list). | The list lives in the release record; the API is the only writer. |
| R14 | Card copy the spec leaves open: ambiguous 「哪一筆是這張發票？」, near 「金額或商家相近，是這筆嗎？」, `linked_by` 「自動連結」 / 「手動連結」, settings switch 「沒有帳的發票提醒」, mute list 「不提醒的商家」 with 「移除」, timeline hit `發票：<a>、<b>`. | §9: minimal layout; a design pass comes later **(owner)**. |
| R15 | The reminders page also reads `counts.ask` (`reminders.html:31` preview gate, `reminders.ts:369-375` empty state). Both use the bell helper too, so a page with only invoice questions is not shown as empty. | Same intent as §9's three call sites. |

## Review Focus

1. **A group and one of its members each the only candidate of a different invoice in the same pass.** Neither
   auto-links; both invoices end `ambiguous`, and no state has a link on both the group and a member (R7). Test in
   Task 5.
2. **CSV quirks: CRLF line endings, trailing blank lines, a BOM, and a `D` row whose number differs from the
   preceding `M` row (or a `D` row before any `M`).** CRLF/blank/BOM parse cleanly. A bad row fails that file only:
   nothing from it is posted, its Drive id and a code appear in `failures`, and the other files still import. Tests
   in Tasks 9 and 10.
3. **The same invoice twice in one import (two overlapping files merged, or a repeat inside one file).** One row; the
   later copy's values; items replaced, never doubled. Tests in Tasks 4 and 9.
4. **An owner link racing the daily matcher or a ledger delete.** Serialised by the advisory key (R6). A link whose
   target vanished or was linked meanwhile answers 409 (`target_ineligible` or `retry`), never 500. Test in Task 6.
5. **Period and window edges: an invoice dated 2026-12-31 or 2027-01-01 (ROC year change), and one dated on the
   last day of a two-month period.** `period_of` gives `11511` / `11601`. The exact-number rule uses that period's
   bounds; the ±window may cross the period edge. Tests in Tasks 2 and 5.

---

## PR A — backend

### Task 1: Schema: einvoice tables, enums, unlink trigger

**Files:**
- Create: `services/accounting-service/app/models/einvoice.py`
- Create: `services/accounting-service/alembic/versions/b6e2c8f4a9d1_einvoice_link.py` (`down_revision =
  "a3d9e5c1f7b2"`, the current head: no migration revises it)
- Modify: `services/accounting-service/app/models/__init__.py` (export the new names)
- Modify: `services/accounting-service/tests/conftest.py:43-49` (`LEDGER_TABLES` += `einvoice_link, einvoice_item,
  einvoice_seller_mute, einvoice`, before `ledger_entry`)
- Modify: `services/accounting-service/tests/integration/test_migration.py` (constant `EINVOICE_HEAD =
  "b6e2c8f4a9d1"`; round-trip test)
- Test: `services/accounting-service/tests/integration/test_einvoice_schema.py`,
  `services/accounting-service/tests/unit/test_einvoice_sql_parity.py`

**Interfaces:**
- Produces (`app.models`):
  - `EINVOICE_STATES = ("unmatched", "ambiguous", "near", "linked", "dismissed", "void")`,
    `EINVOICE_DISMISS_REASONS = ("owner", "seller_muted", "runbook")`, `EINVOICE_LINKED_BY = ("auto", "owner")`;
    PG enums `einvoice_state`, `einvoice_dismiss_reason`, `einvoice_linked_by`.
  - `Einvoice`: `id` PK; `number` String(10) not null; `issue_date` Date not null; `period` CHAR(5) not null;
    `seller_tax_id` String(16) not null, server default `''`; `seller_name` String(128) not null; `total_amount`
    Numeric(20,4) not null; `status_raw` String(16) not null; `is_void` Boolean not null default false; `state`
    not null default `'unmatched'`; `dismiss_reason` nullable; `first_seen_at`, `updated_at` timestamptz not null
    default now() (`updated_at` `onupdate=func.now()`); `source_drive_id` String(128) not null.
    `UniqueConstraint("number", "period", name="ux_einvoice_number_period")`, `Index("ix_einvoice_state", "state")`,
    `Index("ix_einvoice_seller_tax_id", "seller_tax_id")`,
    `CheckConstraint("(state = 'dismissed') = (dismiss_reason IS NOT NULL)", name="ck_einvoice_dismiss_reason")`,
    `CheckConstraint("NOT is_void OR state = 'void'", name="ck_einvoice_void_state")`.
  - `EinvoiceItem`: `invoice_id` FK `einvoice.id` ON DELETE CASCADE, `seq` Integer, PK (`invoice_id`, `seq`);
    `name` String(256) not null; `subtotal` Numeric(20,4) not null.
  - `EinvoiceLink`: `invoice_id` PK, FK `einvoice.id` ON DELETE CASCADE; `entry_id` FK `ledger_entry.id` ON DELETE
    CASCADE nullable; `group_id` FK `entry_group.id` ON DELETE CASCADE nullable; `linked_by` not null; `linked_at`
    timestamptz not null default now().
    `CheckConstraint("(entry_id IS NULL) <> (group_id IS NULL)", name="ck_einvoice_link_one_target")`; partial unique
    `Index("ux_einvoice_link_entry", "entry_id", unique=True, postgresql_where=text("entry_id IS NOT NULL"))`, same
    for `ux_einvoice_link_group` on `group_id`.
  - `EinvoiceSellerMute`: `seller_tax_id` String(16) PK, `CheckConstraint("seller_tax_id <> ''",
    name="ck_einvoice_seller_mute_tax_id")`; `created_at` timestamptz not null default now().
  - `EINVOICE_UNLINK_FUNCTION_SQL` (function `einvoice_link_deleted()`) and `EINVOICE_UNLINK_TRIGGER_SQL` (trigger
    `trg_einvoice_link_deleted AFTER DELETE ON einvoice_link FOR EACH ROW`), registered with
    `event.listen(EinvoiceLink.__table__, "after_create", DDL(...))` as `app/models/ledger.py:350-351` does. Body:
    `UPDATE einvoice SET state = 'unmatched', dismiss_reason = NULL, updated_at = now() WHERE id = OLD.invoice_id
    AND state = 'linked'; RETURN NULL;`

- [ ] **Step 1: Write failing tests** in `test_einvoice_schema.py` (use `seed` and `db_session`; insert rows via the
  models; fictional values):
  - `test_invoice_round_trip`: an `Einvoice` with two items reads back with `state == "unmatched"`,
    `is_void is False`, `dismiss_reason is None`.
  - `test_number_unique_per_period`: same `number` and `period` → `IntegrityError`; same number, period `11511` vs
    `11601` → both insert.
  - `test_link_needs_exactly_one_target`: both `entry_id` and `group_id` set → `IntegrityError`; neither → same.
  - `test_one_invoice_per_entry_and_per_group`: two links to one entry → `IntegrityError`; two to one group → same.
  - `test_dismiss_reason_only_with_dismissed`: `state="unmatched", dismiss_reason="owner"` → `IntegrityError`;
    `state="dismissed", dismiss_reason=None` → `IntegrityError`.
  - `test_entry_delete_cascades_and_trigger_unmatches`: invoice `linked` + link to entry; SQL `DELETE FROM
    ledger_entry` → no link row, invoice `state == "unmatched"`.
  - `test_group_delete_cascades_and_trigger_unmatches`: same with `DELETE FROM entry_group`.
  - `test_trigger_leaves_non_linked_states`: invoice set to `void` (`is_void=True`) first, then its link row deleted
    → `state == "void"`; same with `dismissed`/`owner` → stays `dismissed`.
  - `test_mute_tax_id_not_empty`: `EinvoiceSellerMute(seller_tax_id="")` → `IntegrityError`.
  In `test_einvoice_sql_parity.py`: `test_migration_trigger_sql_matches_the_model` loads the migration module with
  `importlib` (as `test_dirty_trigger_sql_parity._load`) and asserts whitespace-normalised equality of both
  constants.
  In `test_migration.py`: `test_einvoice_round_trip` — upgrade head → `_version == EINVOICE_HEAD`, the four tables
  present; downgrade to `REWARD_ACCRUAL_HEAD` → tables gone; upgrade head again.
- [ ] **Step 2:** `$PYTEST tests/integration/test_einvoice_schema.py tests/unit/test_einvoice_sql_parity.py -v` →
  FAIL (no module `app.models.einvoice`).
- [ ] **Step 3: Write the migration and the models.** Create the three enums with `postgresql.ENUM(...).create(bind,
  checkfirst=False)` and reference them with `create_type=False` (as `a3d9e5c1f7b2_reward_accrual.py:39-52`). Copy
  the trigger SQL verbatim into the migration with the comment "Copied verbatim from app/models/einvoice.py
  (migrations do not import app code); parity is unit-tested." Downgrade: drop trigger, function, the four tables,
  then the three types.
- [ ] **Step 4:** run Step 2's command, then
  `$PYTEST tests/integration/test_migration.py tests/integration/test_models_schema.py -v` → PASS.
- [ ] **Step 5:** commit `feat(accounting): einvoice tables and unlink trigger`.

### Task 2: Pure matching rules

**Files:**
- Create: `services/accounting-service/app/services/einvoice_rules.py` (stdlib only)
- Test: `services/accounting-service/tests/unit/test_einvoice_rules.py`

**Interfaces:**
- Produces:
  - `period_of(day: date) -> str`: `f"{day.year - 1911:03d}{odd:02d}"` where `odd` is the odd month starting the
    two-month period.
  - `period_bounds(period: str) -> tuple[date, date]`: first day of the odd month, last day of the following month.
  - `is_integral(amount: Decimal) -> bool`.
  - `near_tolerance(amount: Decimal) -> Decimal`: `max(Decimal(50), amount * Decimal("0.2"))`.
  - `seller_tokens(text: str | None) -> frozenset[str]`: lower-cased maximal `[A-Za-z]+` runs and maximal `[0-9]+`
    runs, plus every substring of length ≥ 2 of each maximal CJK run (`\u3400-\u9fff\uf900-\ufaff`). Empty/None →
    empty set.
  - `shares_token(seller_name: str, *texts: str | None) -> bool`: `seller_tokens(seller_name)` intersects the
    tokens of any text.
  - `TargetKey = tuple[str, int]` (`("entry", id)` / `("group", id)`).
  - `Decision = Literal["link", "ambiguous", "near", "nothing"]`.
  - `decide(candidates: Mapping[int, Sequence[TargetKey]], near: Mapping[int, bool], family: Mapping[TargetKey, int | None]) -> dict[int, tuple[Decision, TargetKey | None]]`
    — keys are invoice ids; `family[key]` is the group id of an entry target (None when ungrouped) and the group's own
    id for a group target.

  `decide` algorithm (not determined by the signature):

  ```
  only = {inv: c[0] for inv, c in candidates.items() if len(c) == 1}
  holders = Counter(only.values())
  group_holders = defaultdict(set)   # group id -> invoices whose single target is that group
  member_holders = defaultdict(set)  # group id -> invoices whose single target is a member of it
  for inv, key in only.items():
      if key[0] == "group": group_holders[key[1]].add(inv)
      elif family.get(key) is not None: member_holders[family[key]].add(inv)
  for inv, c in candidates.items():
      if len(c) >= 2: out[inv] = ("ambiguous", None)
      elif len(c) == 1:
          key = c[0]
          # R7: only group-vs-own-member is a family contest; two different members of one group are not
          family_contest = (member_holders[key[1]] if key[0] == "group"
                            else group_holders.get(family.get(key), set()))
          contested = holders[key] > 1 or bool(family_contest)
          out[inv] = ("ambiguous", None) if contested else ("link", key)
      else: out[inv] = ("near", None) if near.get(inv) else ("nothing", None)
  ```

- [ ] **Step 1: Failing tests:**
  - `test_period_of`: `date(2026,9,1)` → `"11509"`; `date(2026,10,31)` → `"11509"`; `date(2026,12,31)` →
    `"11511"`; `date(2027,1,1)` → `"11601"`; `date(2026,2,28)` → `"11501"`.
  - `test_period_bounds`: `"11509"` → `(date(2026,9,1), date(2026,10,31))`; `"11601"` → `(date(2027,1,1),
    date(2027,2,28))`; `"11511"` → `(date(2026,11,1), date(2026,12,31))`.
  - `test_is_integral`: `Decimal("100.00")` True, `Decimal("100")` True, `Decimal("20.99")` False.
  - `test_near_tolerance`: `Decimal("100")` → `50`; `Decimal("1000")` → `200`.
  - `test_seller_tokens`: `seller_tokens("Example SaaS Inc.")` ⊇ `{"example", "saas", "inc"}`;
    `seller_tokens("範例超商")` == `{"範例", "例超", "超商", "範例超", "例超商", "範例超商"}`;
    `seller_tokens("7-ELEVEN")` == `{"7", "eleven"}`; `seller_tokens(None)` == `frozenset()`.
  - `test_shares_token`: `shares_token("範例超商股份有限公司", None, "超商午餐")` True;
    `shares_token("Example SaaS Inc.", "EXAMPLE*SUB")` True; `shares_token("範例超商", "咖啡")` False.
  - `test_decide_unique_links`: `{1: [("entry", 10)]}` → `{1: ("link", ("entry", 10))}`.
  - `test_decide_contested_single_target`: `{1: [E], 2: [E]}` → both `ambiguous`.
  - `test_decide_several_candidates`: `{1: [E, G]}` → `ambiguous`.
  - `test_decide_family_conflict`: `{1: [("group", 5)], 2: [("entry", 11)]}` with `family = {("group",5): 5,
    ("entry",11): 5}` → both `ambiguous` (R7).
  - `test_decide_near_and_nothing`: `{1: [], 2: []}`, `near={1: True}` → `1: near`, `2: nothing`.
- [ ] **Step 2:** `$PYTEST tests/unit/test_einvoice_rules.py -v` → FAIL.
- [ ] **Step 3:** implement the module.
- [ ] **Step 4:** Step 2's command → PASS.
- [ ] **Step 5:** commit `feat(accounting): einvoice matching rules`.

### Task 3: Settings block `einvoice` and the preferences merge

**Files:**
- Modify: `services/accounting-service/app/services/settings_service.py` (`EINVOICE_DEFAULTS` next to
  `SETTLEMENT_DEFAULTS` :158; `_reconciliation_dict` :162-175; `_check_einvoice` after `_check_settlement`;
  `update_reconciliation_settings` :531-559; `read_einvoice_settings` after `read_settlement_settings` :508)
- Modify: `services/accounting-service/app/schemas/statements.py:415-443` (`einvoice: dict = {}` on
  `ReconciliationSettingsIn`, `einvoice: dict` on `ReconciliationSettingsOut`)
- Test: `services/accounting-service/tests/integration/test_einvoice_settings.py` (pattern:
  `tests/integration/test_auto_pay_settings.py`, autouse `monkeypatch.setenv(auth.FEATURE_ENV, "true")`)

**Interfaces:**
- Produces:
  - `EINVOICE_DEFAULTS = {"enabled": False, "remind_unmatched": True, "date_window_days": 3, "mute_prompt_after": 3}`
  - `_check_einvoice(einvoice) -> dict`: unknown key → `ValidationError("einvoice", "unknown key 'x'")`;
    `enabled` / `remind_unmatched` booleans; `date_window_days` int 0..7; `mute_prompt_after` int 1..10 (bools
    refused as ints, as `_check_auto_pay`).
  - `read_einvoice_settings(db: Session) -> dict`: the effective block plus `version` (SELECT-only, like
    `read_settlement_settings`).
  - `merge_einvoice_preferences(db: Session, *, remind_unmatched: bool) -> dict`: locks the row
    (`_reconciliation_row(db, lock=True)`), sets `data["einvoice"] = {**stored_block, "remind_unmatched": value}`,
    bumps `version` by 1, flushes, returns `read_einvoice_settings`. It never calls `update_reconciliation_settings`
    (§8).

- [ ] **Step 1: Failing tests:**
  - `test_defaults`: `GET /settings/reconciliation` → `["einvoice"] == settings_service.EINVOICE_DEFAULTS ==
    {"enabled": False, "remind_unmatched": True, "date_window_days": 3, "mute_prompt_after": 3}`.
  - `test_put_stores_block_and_bumps_version`: PUT with `einvoice={"enabled": True}` → 200, `einvoice.enabled is
    True`, `date_window_days == 3` (defaults overlaid), `version` +1, `policy_version`/`rules_version` unchanged.
  - `test_refusals` (parametrised): `{"date_window_days": 8}`, `{"date_window_days": -1}`,
    `{"mute_prompt_after": 0}`, `{"mute_prompt_after": 11}`, `{"enabled": "yes"}`, `{"bogus": 1}` → 422 naming
    `einvoice`.
  - `test_put_without_block_reverts_to_defaults`: after a PUT with `enabled: True`, a PUT body without `einvoice` →
    `enabled is False` (PUT replaces the whole document, §11).
  - `test_merge_preferences_keeps_other_keys`: PUT `einvoice={"enabled": True, "date_window_days": 5}` plus a
    non-empty `auto_pay`; then `merge_einvoice_preferences(db, remind_unmatched=False)` + commit →
    `enabled True`, `date_window_days 5`, `remind_unmatched False`, `auto_pay` unchanged, `version` +1.
- [ ] **Step 2:** `$PYTEST tests/integration/test_einvoice_settings.py -v` → FAIL.
- [ ] **Step 3:** implement. `_reconciliation_dict` adds `"einvoice": {**EINVOICE_DEFAULTS, **(data.get("einvoice")
  or {})}`; `update_reconciliation_settings` validates with `_check_einvoice(data.get("einvoice", {}))` and stores
  the result in `row.data`.
- [ ] **Step 4:** Step 2's command plus `$PYTEST tests/integration/test_auto_pay_settings.py
  tests/integration/test_settings_api.py -v` → PASS.
- [ ] **Step 5:** commit `feat(accounting): einvoice settings block`.

### Task 4: Import endpoint (upsert, void, privacy)

**Files:**
- Create: `services/accounting-service/app/schemas/einvoices.py`
- Create: `services/accounting-service/app/services/einvoice_service.py`
- Create: `services/accounting-service/app/routers/einvoices.py`
- Modify: `services/accounting-service/app/main.py:9-26` (import and register `einvoices.router`)
- Modify: `services/accounting-service/tests/integration/test_scope_enforcement.py:26-31` (`_fill` also replaces
  `invoice_id` and `tax_id`)
- Create: `services/accounting-service/tests/integration/einvoice_fixtures.py` (builders, as
  `statement_fixtures.py`)
- Test: `services/accounting-service/tests/integration/test_einvoice_import.py`

**Interfaces:**
- Import rule: `app/schemas/einvoices.py` may import `Strict` (`schemas/statements.py:22`) and `Int32`
  (`schemas/writes.py:31`). `schemas/ledger.py` must never import it: `writes.py:10` imports `ledger`, so that
  would close a cycle. The two models the entry detail needs (`EinvoiceItemOut`, `EntryEinvoiceOut`) therefore
  live in `schemas/ledger.py`, and `schemas/einvoices.py` imports them from there.
- Produces (`app.schemas.einvoices`, all `model_config = Strict`):
  - `EinvoiceItemIn`: `seq: int (1..1000)`, `name: str (1..256)`, `subtotal: Decimal`.
  - `EinvoiceIn`: `number: str` pattern `^[A-Z]{2}[0-9]{8}$`, `issue_date: date`, `seller_tax_id: str` pattern
    `^[0-9A-Za-z]{0,16}$`, `seller_name: str (1..128)`, `total_amount: Decimal`, `status: str (1..16)`,
    `source_drive_id: str` pattern `^[A-Za-z0-9_-]{1,128}$`, `items: list[EinvoiceItemIn]` (max 500).
  - `EinvoiceImportIn`: `invoices: list[EinvoiceIn]` (max 5000).
  - `EinvoiceMatchOut`: `ran: bool`, `processed`, `linked`, `ambiguous`, `near`, `unmatched`, `unchanged: int`.
  - `EinvoiceImportOut`: `inserted`, `updated`, `unchanged`, `voided`, `unknown_status: int`,
    `match: EinvoiceMatchOut`.
- Produces (`app.services.einvoice_service`):
  - `EINVOICE_LOCK_KEY = 0x45494E56`, `KNOWN_STATUSES = ("開立", "作廢", "折讓")`, `VOID_STATUS = "作廢"`.
  - `lock(db: Session) -> None` (`SELECT pg_advisory_xact_lock(:k)`).
  - `import_invoices(db: Session, body: EinvoiceImportIn) -> dict` (the `EinvoiceImportOut` fields except `match`).
  - `void_invoice(db: Session, invoice: Einvoice) -> None`: `is_void = True`, `state = "void"`, `dismiss_reason =
    None`, flush, then `remove_link(db, invoice)`.
  - `remove_link(db: Session, invoice: Einvoice) -> None`: deletes the link row (if any) **after** the caller set the
    new state, and clears the linked entry's `invoice_number` only when it equals `invoice.number` (§5).
  - `clear_stamp(db: Session, entry_id: int, number: str) -> None`, `stamp(db: Session, entry_id: int, number: str)
    -> None` (sets `invoice_number` only when NULL; ignores `is_locked`, R8).
- Produces (router): `POST /einvoices/import` (`ingest`) → `EinvoiceImportOut`: `lock`, `import_invoices`, then
  `einvoice_matcher.run_matcher(db)` (Task 5; until then the handler returns `match` with `ran=False` and zero
  counts), commit.
- Produces (fixtures): `invoice(number="AB00000001", issue_date="2026-10-05", seller_tax_id="00000001",
  seller_name="測試商店股份有限公司", total="120", status="開立", items=(("測試品項", "120"),),
  source="drive-fake-1") -> dict`; `import_(client, *invoices) -> dict` (POST, asserts 200); `enable(client,
  **einvoice)` (PUT settings from the current document with `einvoice={"enabled": True, **einvoice}`).

- [ ] **Step 1: Failing tests** (autouse feature on):
  - `test_new_invoice_is_unmatched`: import one → `inserted == 1`; row `state == "unmatched"`, `period == "11509"`,
    two items in `seq` order.
  - `test_reimport_identical_changes_nothing`: import twice → second `unchanged == 1`, `updated == 0`; `updated_at`
    and `source_drive_id` equal to the first (R3).
  - `test_conflict_updates_fields_and_replaces_items`: second import with new `seller_name`, `total`, items
    `(("新品項", "80"),)` and `source="drive-fake-2"` → `updated == 1`; one item; `source_drive_id ==
    "drive-fake-2"`.
  - `test_duplicate_in_one_payload_last_wins` (Review Focus 3): one body with the same invoice twice (totals 120 then
    90) → one row, total 90, items not doubled.
  - `test_number_reuse_across_periods_is_two_rows`: same number, dates 2026-10-05 and 2026-12-05 → two rows.
  - `test_void_sets_state_and_removes_link`: insert, link to an entry by hand (state `linked`, link row, entry
    `invoice_number` = number), import again with `status="作廢"` → `voided == 1`, `state == "void"`, `is_void`,
    no link row, entry `invoice_number is None`.
  - `test_void_is_sticky`: after the void, import with `status="開立"` → still `void` (§6).
  - `test_allowance_keeps_link` (`折讓`, new total 100) → `updated == 1`, `state == "linked"`, link kept,
    `total_amount == 100`.
  - `test_unknown_status_counted`: `status="未知"` → `unknown_status == 1`, stored in `status_raw`, state kept.
  - `test_extra_carrier_fields_refused` (privacy): body with an invoice carrying `carrier_name: "測試載具"` and
    `carrier_number: "/FAKE+01"` → 422; `SELECT count(*) FROM einvoice` == 0.
  - `test_no_carrier_values_in_rows` (privacy): after a normal import, `SELECT row_to_json(e)::text FROM einvoice e`
    and the same for `einvoice_item` contain neither `測試載具` nor `/FAKE+01`.
  - `test_import_needs_ingest_scope`: with `TOKENS`/`SCOPES` as in `test_scope_enforcement.py`, the `hermes` token
    → 403.
- [ ] **Step 2:** `$PYTEST tests/integration/test_einvoice_import.py -v` → FAIL.
- [ ] **Step 3:** implement schemas, service and router. Router: `router = APIRouter(prefix="/einvoices",
  tags=["E-invoices"], dependencies=[Depends(require_feature())])`, scope lists as `app/routers/statements.py:73-77`,
  handlers wrap service calls in `service_errors()` and commit. Import order: invoices are processed in body order
  (later copy wins). For each: `period_of(issue_date)`; select by (`number`, `period`) `FOR UPDATE`; insert as
  `unmatched`, or compare fields + item tuples and update only on a difference (R3); a `作廢` status on a
  non-void row calls `void_invoice` (count `voided`).
- [ ] **Step 4:** Step 2's command plus `$PYTEST tests/integration/test_scope_enforcement.py -v` → PASS.
- [ ] **Step 5:** commit `feat(accounting): einvoice import endpoint`.

### Task 5: Matcher

**Files:**
- Create: `services/accounting-service/app/services/einvoice_matcher.py`
- Modify: `services/accounting-service/app/routers/einvoices.py` (import calls `run_matcher`; new `POST
  /einvoices/match`, `ingest`)
- Test: `services/accounting-service/tests/integration/test_einvoice_matcher.py`

**Interfaces:**
- Consumes: Task 2 `period_bounds`, `is_integral`, `near_tolerance`, `shares_token`, `decide`, `TargetKey`;
  Task 3 `read_einvoice_settings`; Task 4 `lock`, `stamp`; `ledger_service.main_currency(db)`.
- Produces:
  - `@dataclass(frozen=True) class Target`: `key: TargetKey`, `family: int | None`, `day: date`, `net: Decimal`,
    `currency: str`, `original_currency: str | None`, `original_amount: Decimal | None`, `invoice_number: str |
    None`, `merchant: str | None`, `name: str | None`, `account_name: str`, `category_id: int | None`.
    For a group: `day` = earliest member `entry_date`; `net` = Σ nets of `expense`/`receivable` members; `currency`
    = their shared currency; `original_*` and `invoice_number` None; `merchant`/`name` from `entry_group`;
    `account_name` / `category_id` of the earliest member.
  - `eligible_targets(db: Session, date_from: date, date_to: date) -> list[Target]` — §7.2: entries `kind =
    'expense'`, `parent_entry_id IS NULL`, no link row on the entry, no link row on its `group_id`; `net` = amount +
    Σ amounts of its `fee`/`discount` children. Groups: `entry_group.kind = 'split'`, no link row on the group, no
    member with a link row, one currency over `expense`/`receivable` members. The `invoice_number` condition is
    per invoice, applied in `rule_candidates`.
  - `entry_target(db: Session, entry_id: int) -> Target | None` and `group_target(db: Session, group_id: int) ->
    Target | None`: the same predicates for one target (None = not eligible).
  - `rule_candidates(invoice: Einvoice, targets: Sequence[Target], *, window_days: int, main: str) -> list[Target]`
    — §7.3: exact-number entries (`invoice_number == number`, day within `period_bounds(invoice.period)`) when any,
    else amount candidates. TWD rule (integral `A` only): entries in window with `currency == main`,
    `original_currency is None`, `net == -A`; groups in window with `currency == main`, `net == -A`. Foreign rule
    (non-integral only): entries in window with `original_currency not in (None, main)` and `abs(original_amount) ==
    A`. Every entry candidate also needs `invoice_number is None or == number`.
  - `near_candidates(invoice: Einvoice, targets: Sequence[Target], *, window_days: int) -> list[Target]` — targets in
    window with `abs(net + A) <= near_tolerance(A)` or `shares_token(seller_name, merchant, name)`; entry targets also
    need the `invoice_number` condition.
  - `order_by_closeness(targets, amount: Decimal, day: date) -> list[Target]`: by `abs(abs(net) - amount)`, then
    `abs((t.day - day).days)`, then key.
  - `candidates_for(db: Session, invoice: Einvoice) -> tuple[list[Target], bool]`: `rule = rule_candidates(...)`;
    return `(rule, False)` when `rule` is non-empty, else `(near_candidates(...), True)`. Spell it as an explicit
    `if`, never `(a, False) or (b, True)` (a tuple is always truthy). Read side of Task 7; near cards get their
    candidates from the second branch.
  - `run_matcher(db: Session) -> dict` (`EinvoiceMatchOut` fields). Returns `ran=False` with zeros when
    `einvoice.enabled` is false. Otherwise: `lock`; processed set = states `unmatched`, `ambiguous`, `near`, and
    `dismissed` with `dismiss_reason = 'seller_muted'` (§7.1), `FOR UPDATE`; one `eligible_targets` call over the
    union of all windows and periods; `decide`; then write:
    - `link`: state `linked`, `dismiss_reason = None`, insert `EinvoiceLink(linked_by="auto")`, `stamp` for an
      entry target. Each link insert runs in a savepoint (`db.begin_nested()`): an `IntegrityError` (a concurrent
      ledger write took the target) rolls back that invoice only, leaves its state unchanged and counts
      `retry_later`; the import and the rest of the pass commit (R6);
    - `ambiguous` / `near`: that state, `dismiss_reason = None`;
    - `nothing`: `dismissed`/`seller_muted` when the seller has a mute row, else `unmatched`.
    An invoice whose state does not change counts `unchanged`.

- [ ] **Step 1: Failing tests** (each enables the block with `enable(client)`; accounts via `seed.account`, TWD main
  currency; the window default 3; amounts fictional):
  - `test_unique_twd_entry_auto_links`: invoice 2026-10-05, 120; expense −120 on 10-06 → `linked`, `linked_by
    "auto"`, entry `invoice_number == "AB00000001"`.
  - `test_fee_and_discount_children_count_in_net`: parent −130 with a `fee` child −10 and a `discount` child +20
    (net −120) → linked.
  - `test_outside_window_or_wrong_kind_never_links`: expense on 10-09 (4 days); an `income` +120; a child row → no
    link.
  - `test_foreign_decimal_with_original_amount_links` (§14): invoice `20.99`, seller `Example SaaS Inc.`; entry
    −660 TWD with `original_currency="USD", original_amount=Decimal("-20.99")` → linked.
  - `test_foreign_decimal_without_original_amount_is_near_by_token` (§14): same invoice; entry −660 TWD, no
    original, `merchant="EXAMPLE SAAS"` → `near`, no link.
  - `test_two_invoices_one_entry_both_ambiguous` (§14): two invoices 120 on 10-05 (different numbers) and one
    expense −120 → both `ambiguous`, no link.
  - `test_group_and_single_entry_same_amount_ambiguous` (§14): split group G with expense members −150 and −50 (net
    −200) and an ungrouped expense −200 in the window; invoice 200 → `ambiguous`, no link.
  - `test_group_members_same_amount_ambiguous` (§14): split group G with expense members −100 and −100 (net −200);
    invoice 100 → both members are candidates (G is not) → `ambiguous`, no link to a member.
  - `test_group_and_own_member_same_amount_ambiguous` (§14, one invoice): split group G with an expense member
    −200 and an income member +50 (G's expense+receivable net −200); invoice 200 → candidates G and its member →
    `ambiguous`, no link.
  - `test_two_members_of_one_group_both_link` (R7 narrow): invoice X 150 whose only candidate is member M1 (−150) of
    G, invoice Y 70 whose only candidate is member M2 (−70) of G → both `linked` to their members.
  - `test_matcher_integrity_error_is_retry_later` (R6): a link insert that raises `IntegrityError` leaves that
    invoice's state unchanged, counts `retry_later`, and the import still commits.
  - `test_family_conflict_both_ambiguous` (Review Focus 1, R7): invoice X 200 whose only candidate is group G (net
    −200); invoice Y 150 whose only candidate is member M (−150) of G (G's other member −50) → both `ambiguous`; no
    link row.
  - `test_whole_number_against_usd_original_never_auto_links` (§14): invoice 30 (integral); entry −950 TWD with
    `original_currency="USD", original_amount=-30` → not linked (state `near` or `unmatched`).
  - `test_twd_invoice_against_usd_account_never_auto_links` (§14): USD account, expense −120, no original → not
    linked.
  - `test_exact_number_beats_amount`: entry A stamped `AB00000001`, amount −999, date 10-20 (inside period 11509,
    outside window); entry B −120 on 10-05 → linked to A.
  - `test_exact_number_respects_period` (§14 reuse): entry stamped `AB00000001` dated 2026-10-20; invoice
    `AB00000001` dated 2026-12-03 (period 11511) → not an exact match (no link to it).
  - `test_period_edge` (Review Focus 5): invoice 2026-12-31 with an exact-number entry dated 2026-11-01 → linked;
    with one dated 2027-01-02 (in window, other period) → only amount rule applies.
  - `test_stamped_with_other_number_is_ineligible`: entry stamped `AB00000009`, amount match → not linked.
  - `test_linked_group_member_and_grouped_link_ineligible` (§7.6): entry linked → its group not a candidate; group
    linked → its members not candidates.
  - `test_installment_and_reward_claim_groups_ineligible`: group kinds `installment`, `reward_claim` → never
    candidates.
  - `test_mixed_currency_group_ineligible`: members TWD and USD → not a candidate.
  - `test_skips_linked_void_and_owner_dismissed`: states `linked`, `void`, `dismissed(owner)`,
    `dismissed(runbook)` are untouched by a run with matching entries.
  - `test_muted_invoice_still_auto_links_or_stays`: `dismissed(seller_muted)` with a unique candidate → `linked`;
    without → stays `dismissed(seller_muted)`, counted `unchanged`.
  - `test_disabled_runs_nothing`: `enabled` false → import response `match.ran is False`, invoice stays
    `unmatched` despite a unique candidate.
  - `test_match_endpoint_runs_and_is_idempotent`: `POST /einvoices/match` links once; a second call → all
    `unchanged`.
  - `test_invoice_number_is_not_structural`: `assert "invoice_number" not in coverage_service.STRUCTURAL_KEYS`
    (§12: stamping never releases statement lines).
- [ ] **Step 2:** `$PYTEST tests/integration/test_einvoice_matcher.py -v` → FAIL.
- [ ] **Step 3:** implement `einvoice_matcher.py`; wire `run_matcher` into the import handler (same transaction,
  before commit) and add `POST /einvoices/match`.
- [ ] **Step 4:** Step 2's command plus `$PYTEST tests/integration/test_einvoice_import.py -v` → PASS.
- [ ] **Step 5:** commit `feat(accounting): einvoice matcher`.

### Task 6: Owner actions, mutes, preferences, runbook dismiss

**Files:**
- Modify: `services/accounting-service/app/services/einvoice_service.py`
- Modify: `services/accounting-service/app/schemas/einvoices.py`
- Modify: `services/accounting-service/app/routers/einvoices.py`
- Test: `services/accounting-service/tests/integration/test_einvoice_actions.py`

**Interfaces:**
- Consumes: Task 4 `lock`, `remove_link`, `stamp`, `clear_stamp`; Task 5 `entry_target`, `group_target`; Task 3
  `read_einvoice_settings`, `merge_einvoice_preferences`.
- Produces (schemas): `EinvoiceLinkIn` (`entry_id: Int32 | None = None`, `group_id: Int32 | None = None`; a
  `model_validator` refuses both or neither: 422 `target`), `EinvoiceDismissIn` (`mute_seller: bool = False`),
  `EinvoiceStateOut` (`id`, `state`, `dismiss_reason`, `linked_by: str | None`, `entry_id: int | None`, `group_id:
  int | None`), `EinvoiceMuteOut` (`seller_tax_id`, `seller_name: str | None` (the latest invoice's), `created_at`),
  `EinvoicePreferencesIn` (`remind_unmatched: bool`), `EinvoicePreferencesOut` (`enabled`, `remind_unmatched: bool`,
  `version: int`), `RunbookRefIn` (`number`, `issue_date`), `RunbookDismissIn` (`invoices: list[RunbookRefIn]`,
  1..500), `RunbookDismissOut` (`dismissed: int`, `not_found: list[int]`).
- Produces (service; every function calls `lock` first and raises `NotFoundError` for a missing invoice):
  - `link(db, invoice_id: int, *, entry_id: int | None, group_id: int | None) -> dict` — 409 `einvoice_void` on a
    void invoice; 409 `target_ineligible` unless `entry_target`/`group_target` returns a target or it is the
    invoice's current target; an entry target also needs `invoice_number` NULL or equal. Existing link: update the
    row in place (`entry_id`/`group_id`, `linked_by="owner"`, `linked_at=now()`), `clear_stamp` the previous entry
    (when the previous target was an entry), then `stamp` the new target only when it is an entry. Group targets
    are never stamped (§5). New link: insert. Always `state="linked"`, `dismiss_reason=None`. `IntegrityError` on
    flush → `CodedConflictError("retry")` (R6).
  - `unlink(db, invoice_id: int) -> dict` — 409 `not_linked` when no link row; sets `dismissed`/`owner`, flushes,
    then `remove_link`.
  - `dismiss(db, invoice_id: int, *, reason: str = "owner", mute_seller: bool = False) -> dict` — 409 `einvoice_void`
    on void; sets `dismissed`/`reason`, flushes, `remove_link`; with `mute_seller`, 422 `mute_seller` when
    `seller_tax_id` is empty, else `create_mute`.
  - `undismiss(db, invoice_id: int) -> dict` — 409 `not_dismissed` unless `dismissed` with reason `owner` or
    `runbook`; 409 `seller_muted` while a mute row exists for its tax id; sets `unmatched`, `dismiss_reason=None`.
  - `create_mute(db, tax_id: str) -> None` (idempotent) — also turns that seller's `unmatched` invoices into
    `dismissed`/`seller_muted`.
  - `delete_mute(db, tax_id: str) -> None` — 404 when absent; turns that seller's `dismissed`/`seller_muted`
    invoices into `unmatched`.
  - `list_mutes(db) -> list[dict]`.
  - `runbook_dismiss(db, refs: list[RunbookRefIn]) -> dict` — looks up each by (`number`, `period_of(issue_date)`);
    missing or void → its index in `not_found`; else `dismiss(..., reason="runbook")`.
- Produces (routes, R1): `POST /einvoices/{invoice_id}/link` (body `EinvoiceLinkIn`), `/unlink`, `/dismiss` (body
  optional `EinvoiceDismissIn`), `/undismiss` → `EinvoiceStateOut`; `GET /einvoices/mutes` →
  `list[EinvoiceMuteOut]`; `DELETE /einvoices/mutes/{tax_id}` → 204; `GET`/`PATCH /einvoices/preferences` →
  `EinvoicePreferencesOut`; `POST /einvoices/runbook-dismiss` → `RunbookDismissOut`. Static paths are declared
  before `/{invoice_id}` routes.

- [ ] **Step 1: Failing tests:**
  - `test_owner_link_any_state_but_void`: unmatched → link entry → `linked`, `linked_by "owner"`, stamp set; a void
    invoice → 409 `einvoice_void`.
  - `test_link_refuses_ineligible_target`: an `income` entry, a child row, an entry stamped with another number, an
    entry already linked to another invoice, a member of a linked group → each 409 `target_ineligible`.
  - `test_relink_in_place_keeps_linked_and_next_run_leaves_it` (§14): auto-linked to A; link to B → same link row
    updated, `linked_by "owner"`; `POST /einvoices/match` → unchanged.
  - `test_relink_then_delete_b_returns_unmatched_and_a_is_clear` (§14): invoice 2026-10-05; A stamped by the auto
    link and dated 2026-10-25 (in period 11509, outside the window); owner re-links to B → A's `invoice_number is
    None`; `delete_entries_cascade(db, [B.id])` + commit → `unmatched`; a match run does not link A (state
    `unmatched` or `near`). Repeat with the re-link target a group, then `dissolve_group` → `unmatched`.
  - `test_unlink_dismisses_and_next_run_does_not_relink` (§14): auto-linked; unlink → `dismissed`/`owner`, link
    gone, stamp cleared; match run → still `dismissed`.
  - `test_unlink_overwrites_seller_muted`: a muted seller's auto-linked invoice; unlink → `dismiss_reason "owner"`.
  - `test_dismiss_linked_removes_link_and_detail` (§14): dismiss a linked invoice → link gone, stamp cleared. (Task 8
    adds the `GET /entries/{id}` → `einvoice is None` assertion.)
  - `test_undismiss_returns_to_matching`: dismissed(owner) → undismiss → `unmatched`; next run links a unique
    candidate.
  - `test_undismiss_rejected_while_seller_muted` (§14): dismissed(owner) invoice of a muted seller → 409
    `seller_muted`.
  - `test_mute_converts_unmatched_and_removal_restores`: two unmatched invoices of `00000001`;
    `dismiss(..., mute_seller=True)` on a third → the two become `dismissed`/`seller_muted`, the third
    `dismissed`/`owner`; `DELETE /einvoices/mutes/00000001` → the two `unmatched`, the third unchanged.
  - `test_mute_needs_tax_id`: empty `seller_tax_id` + `mute_seller` → 422.
  - `test_preferences_patch_merges_and_bumps`: `PATCH {"remind_unmatched": false}` → `remind_unmatched False`,
    `enabled` unchanged, `version` +1; `GET /settings/reconciliation` shows the merged block and the other blocks
    unchanged.
  - `test_runbook_dismiss`: two existing refs and one unknown → `dismissed == 2`, `not_found == [2]`, reason
    `runbook`; the next match run leaves them; `undismiss` brings one back.
  - `test_link_race_is_409_not_500` (Review Focus 4): two sessions; session 1 takes the lock and links entry E to
    invoice X (not committed); session 2 calls `link(Y, entry_id=E)` in a thread and blocks; session 1 commits →
    session 2 raises `CodedConflictError` (`target_ineligible`), never `IntegrityError`. Use
    `tests.helpers.wait_until_blocked` for the ordering.
  - `test_owner_routes_need_write`: hermes token → 403 on `link`, `PATCH /preferences`, `GET /mutes`.
  - `test_moze_full_replace_unmatches` (§12, §14): import a fictional MOZE CSV with `tests.helpers._import` (the
    `moze` fixture builds it), auto-link an invoice to one imported row, import the same CSV again (the full
    replace's `delete(LedgerEntry).where(is_moze)`, `moze_import_service.py:328`) → `unmatched`; the next match run
    links it to the re-imported row.
  - `test_void_then_cascade_stays_void` (§14): void a linked invoice by import, then delete the former entry → still
    `void`.
- [ ] **Step 2:** `$PYTEST tests/integration/test_einvoice_actions.py -v` → FAIL.
- [ ] **Step 3:** implement service functions, schemas and routes.
- [ ] **Step 4:** Step 2's command plus `$PYTEST tests/integration/test_einvoice_matcher.py
  tests/integration/test_scope_enforcement.py -v` → PASS.
- [ ] **Step 5:** commit `feat(accounting): einvoice owner actions and seller mutes`.

### Task 7: Inbox section, counts and target list

**Files:**
- Create: `services/accounting-service/app/services/einvoice_read.py`
- Modify: `services/accounting-service/app/schemas/einvoices.py`
- Modify: `services/accounting-service/app/schemas/inbox.py:126-142` (`InboxCounts.einvoice_ask: int = 0`;
  `InboxOut.einvoices: list[InboxEinvoice] = []`)
- Modify: `services/accounting-service/app/services/inbox_service.py:407-446` (`build_inbox` adds the section)
- Modify: `services/accounting-service/app/routers/einvoices.py` (`GET /einvoices/{invoice_id}/targets`)
- Test: `services/accounting-service/tests/integration/test_einvoice_inbox.py`

**Interfaces:**
- Consumes: Task 5 `candidates_for`, `eligible_targets`, `order_by_closeness`, `Target`; Task 3
  `read_einvoice_settings`.
- Produces (schemas): `EinvoiceItemOut` (`name`, `subtotal`; in `schemas/ledger.py`, see Task 4's import rule),
  `EinvoiceTargetOut` (`kind: Literal["entry",
  "group"]`, `id`, `account_name`, `date`, `amount` (the net), `currency`, `category: str | None`, `name: str |
  None`), `InboxEinvoice` (`id`, `state: Literal["ambiguous", "near", "unmatched"]`, `number`, `issue_date`,
  `seller_name`, `seller_tax_id`, `total_amount`, `status_raw`, `items: list[EinvoiceItemOut]`, `candidates:
  list[EinvoiceTargetOut]`, `offer_mute: bool`).
- Produces (`einvoice_read`):
  - `target_out(db, target: Target) -> dict` (category as `parent/child` the way `ledger_service._entry_rows`
    builds `category`; `name` = entry name or group name).
  - `inbox_section(db: Session, *, limit: int) -> tuple[list[dict], int]` — `([], 0)` when `enabled` is false;
    items: `ambiguous` and `near` always, `unmatched` only when `remind_unmatched`; ordered state (`ambiguous`,
    `near`, `unmatched`), then `issue_date` desc, then id; candidates from `candidates_for` (empty for unmatched),
    ordered by closeness; `offer_mute` per R9; `einvoice_ask` = number of `ambiguous` + `near` over the full set;
    the list sliced to `limit`.
  - `invoice_targets(db: Session, invoice_id: int, *, days: int) -> list[dict]` (R12; 404 missing, 409
    `einvoice_void`).
  - `dismissed_invoices(db: Session, *, limit: int = 100) -> list[dict]` (owner ruling 2026-11-06, spec §8):
    `state = 'dismissed'` with `dismiss_reason` in (`owner`, `runbook`), ordered `updated_at` desc then id desc,
    up to `limit`; each as `EinvoiceSummaryOut` plus `dismiss_reason`. Route `GET /einvoices/dismissed`
    (`read`, declared before `/{invoice_id}` routes) → `list[EinvoiceDismissedOut]` (`EinvoiceSummaryOut` fields +
    `dismiss_reason`; define `EinvoiceSummaryOut` here, Task 8 reuses it).
- `build_inbox(...)` sets `counts["einvoice_ask"]` and `einvoices` from `inbox_section(db, limit=limit)` when
  `account_id is None`, else 0 and `[]` (R10).

- [ ] **Step 1: Failing tests:**
  - `test_section_lists_cards_with_candidates`: one ambiguous (two candidates), one near, one unmatched → `einvoices`
    in that order; ambiguous `candidates` has two `EinvoiceTargetOut` with account, date, amount, category, name;
    items listed.
  - `test_bell_count_with_limit_one` (§14): same data, `GET /reconciliation/inbox?limit=1` →
    `counts.einvoice_ask == 2`, `counts.ask` unchanged (reconciliation count), `len(einvoices) == 1`.
  - `test_unmatched_reminders_not_counted`: three unmatched only → `einvoice_ask == 0`, three cards.
  - `test_remind_switch_hides_reminders_only`: `PATCH remind_unmatched false` → unmatched cards gone, ambiguous and
    near stay.
  - `test_muted_seller_has_no_reminder_but_keeps_questions`: muted seller with one `near` and one
    `dismissed(seller_muted)` → only the near card.
  - `test_offer_mute_after_threshold`: three `dismissed(owner)` invoices of `00000001`, a fourth unmatched → its card
    `offer_mute is True`; with `mute_prompt_after=4` → False; an empty tax id → False.
  - `test_disabled_hides_section`: `enabled` false → `einvoices == []`, `einvoice_ask == 0`.
  - `test_account_filter_has_no_invoices`: `?account_id=<id>` → `einvoices == []`, `einvoice_ask == 0`.
  - `test_dismissed_list_owner_and_runbook_only`: one `dismissed(owner)`, one `dismissed(runbook)`, one
    `dismissed(seller_muted)`, one `unmatched` → `GET /einvoices/dismissed` lists the first two (newest first) with
    items and `dismiss_reason`; after `undismiss` on one it is gone from the list.
  - `test_targets_lists_eligible_in_days`: invoice 120 on 10-05; eligible entries −500 (10-10), −118 (10-04), and
    an ineligible income → `GET /einvoices/{id}/targets?days=14` → `[−118 entry, −500 entry]`.
- [ ] **Step 2:** `$PYTEST tests/integration/test_einvoice_inbox.py -v` → FAIL.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** Step 2's command plus `$PYTEST tests/integration/test_inbox_api.py tests/unit/test_inbox_service.py
  -v` → PASS.
- [ ] **Step 5:** commit `feat(accounting): einvoice inbox section and counts`.

### Task 8: Entry detail block, picker and item search

**Files:**
- Modify: `services/accounting-service/app/services/einvoice_read.py`
- Modify: `services/accounting-service/app/schemas/einvoices.py`
- Modify: `services/accounting-service/app/schemas/ledger.py:219-296` (`EntryOut.invoice_item_hits: list[str] =
  []`; `EntryDetailOut.einvoice: EntryEinvoiceOut | None = None`)
- Modify: `services/accounting-service/app/services/ledger_service.py` (`_text_filter` :278, `_filters` :361,
  `_entry_rows` :494, `list_all_entries` :611, `get_entry_detail` :803)
- Modify: `services/accounting-service/app/routers/entries.py:17-50` (query `include_invoice_items: bool = False`)
- Modify: `services/accounting-service/app/routers/einvoices.py` (`GET /einvoices/picker`)
- Test: `services/accounting-service/tests/integration/test_einvoice_entry_read.py`

**Interfaces:**
- Consumes: Task 5 `entry_target`, `group_target`; Task 1 models.
- Produces (schemas): `EntryEinvoiceOut` in `schemas/ledger.py` (`id`, `number`, `issue_date`, `seller_name`,
  `total_amount`, `linked_by`, `linked_to: Literal["entry", "group"]`, `items: list[EinvoiceItemOut]`); in
  `schemas/einvoices.py`: `EinvoiceSummaryOut` (`id`, `number`,
  `issue_date`, `seller_name`, `total_amount`, `state`, `items`), `EinvoicePickerOut` (`entry_eligible: bool`,
  `group_id: int | None`, `group_eligible: bool`, `invoices: list[EinvoiceSummaryOut]`).
- Produces (`einvoice_read`):
  - `entry_einvoice(db: Session, entry: LedgerEntry) -> dict | None` — the entry's own link, else the link of its
    `group_id` (§7.6, §10).
  - `picker(db: Session, entry_id: int) -> dict` — 404 for a missing entry; `entry_eligible` = `entry_target(db,
    id) is not None`; `group_eligible` for a split member's group; `invoices` = non-void invoices without a link row,
    `issue_date` within ±7 days of the entry date, ordered by `abs(total_amount - abs(entry net))`, then date
    distance, then id.
- Produces (`ledger_service`):
  - `_text_filter(q: str, *, include_invoice_items: bool = False)` — with the flag, adds to the `or_`: EXISTS
    `einvoice_link` (`entry_id = LedgerEntry.id` or `group_id = LedgerEntry.group_id`) joined to `einvoice_item`
    with `name ILIKE pattern ESCAPE '\'` (same escaped pattern).
  - `_filters(..., include_invoice_items: bool = False)`, `list_all_entries(..., include_invoice_items: bool =
    False)`; `report_rules.entry_filters` keeps calling `_text_filter(f.q)` unchanged.
  - `_invoice_item_hits(db: Session, entries: list[LedgerEntry], q: str) -> dict[int, list[str]]` — up to 3
    distinct matching item names per entry (own link first, then group link), ordered by invoice id and `seq`.
  - `_entry_rows(..., item_hits_q: str | None = None)` fills `invoice_item_hits` (empty list otherwise).
  - `get_entry_detail` sets `detail["einvoice"] = einvoice_read.entry_einvoice(db, entry)` (lazy import, as the
    module already does for `schedule_read`).

- [ ] **Step 1: Failing tests:**
  - `test_detail_shows_own_invoice`: linked entry → `einvoice.number`, `seller_name`, `total_amount`, `linked_by`,
    `linked_to "entry"`, items `[{"name": "測試品項", "subtotal": "120.0000"}]`.
  - `test_member_shows_group_invoice`: invoice linked to a split group → each member's detail `linked_to "group"`.
  - `test_unlinked_entry_has_no_invoice`: `einvoice is None`.
  - `test_search_matches_linked_item_names_with_flag` (§10, owner G6): linked entry with item 「衛生紙」 →
    `GET /entries?q=衛生紙&include_invoice_items=true` returns it with `invoice_item_hits == ["衛生紙"]`; without
    the flag → not returned.
  - `test_search_matches_group_invoice_items`: group-linked invoice → all members returned.
  - `test_unlinked_invoice_items_never_match` (owner): an unmatched invoice with 「衛生紙」 → no rows.
  - `test_search_literal_percent`: item 「100%純棉」 → `q=100%` matches; `q=10_` does not (escape kept).
  - `test_item_hits_capped_at_three`: five matching items → three names.
  - `test_reports_unchanged`: `report_rules.entry_filters` with `q="衛生紙"` matches no row whose only match is an
    item (saved reports keep their behaviour).
  - `test_picker_lists_unlinked_non_void_near_date`: entry −120 on 10-05; invoices 118 (10-07), 300 (10-01), a void
    one, a linked one, one on 10-20 → `invoices` = [118, 300]; `entry_eligible True`.
  - `test_picker_split_member`: member of an eligible split group → `group_id` set, `group_eligible True`; once the
    group is linked → both false.
- [ ] **Step 2:** `$PYTEST tests/integration/test_einvoice_entry_read.py -v` → FAIL.
- [ ] **Step 3:** implement; add the `einvoice is None` detail assertion to Task 6's
  `test_dismiss_linked_removes_link_and_detail`.
- [ ] **Step 4:** Step 2's command plus `$PYTEST tests/integration/test_entries_api.py
  tests/integration/test_einvoice_actions.py tests/integration/reports -v` → PASS. Then the whole backend suite
  `$PYTEST -q` → PASS (PR A gate).
- [ ] **Step 5:** commit `feat(accounting): einvoice on entry detail, picker and item search`.

---

## PR B — worker

### Task 9: CSV parser (privacy at the source)

**Files:**
- Create: `services/accounting-service/worker/einvoice_csv.py` (stdlib only)
- Test: `services/accounting-service/tests/worker/test_einvoice_parse.py` (fixtures built in the test from
  fictional rows; no fixture file with a real layout copy)

**Interfaces:**
- Produces:
  - `class ParseError(ValueError)` with `.code` in `{"encoding", "header", "row_shape", "orphan_item",
    "item_number", "date", "amount", "number"}`; its message is the code only (never row content).
  - `@dataclass(frozen=True) class ParsedItem`: `seq: int`, `name: str`, `subtotal: Decimal`.
  - `@dataclass(frozen=True) class ParsedInvoice`: `number`, `issue_date: date`, `seller_tax_id`, `seller_name`,
    `total_amount: Decimal`, `status`, `items: tuple[ParsedItem, ...]`. No carrier fields exist on the type.
  - `parse(data: bytes) -> list[ParsedInvoice]` — decode `utf-8-sig`; `splitlines()`; skip blank lines; the first
    two non-blank lines must start with `表頭=M|` and `明細=D|` (`header`); split rows on `|` and drop one trailing
    empty field. `M` rows need exactly 9 fields; fields 1 and 2 (carrier name/number) are discarded at once.
    `D` rows need ≥ 4 fields; the name is `"|".join(fields[3:])`; its number must equal the open `M` row's
    (`item_number`); a `D` before any `M` → `orphan_item`. `seq` counts from 1 per invoice. Number pattern
    `^[A-Z]{2}[0-9]{8}$` (`number`), date `YYYYMMDD` (`date`), amounts `Decimal` (`amount`). A repeated invoice
    (same number and date) in one file: the later one replaces the earlier.
  - `to_payload(invoice: ParsedInvoice, *, source_drive_id: str) -> dict` — exactly the `EinvoiceIn` keys of Task 4
    (`status` from the row).

- [ ] **Step 1: Failing tests** (a local helper `csv_bytes(*rows, bom=True, newline="\n")` builds the two header
  rows plus data rows; carrier fields are `測試載具` and `/FAKE+01`):
  - `test_parses_invoices_and_items`: two `M` rows, three `D` rows → two invoices, items `seq` 1..n, `Decimal`
    amounts, `issue_date` from `20261005`.
  - `test_drops_carrier_fields`: `"測試載具" not in repr(result)` and `"/FAKE+01" not in json.dumps([to_payload(i,
    source_drive_id="drive-fake-1") for i in result], ensure_ascii=False, default=str)`; payload keys ==
    `{"number", "issue_date", "seller_tax_id", "seller_name", "total_amount", "status", "source_drive_id",
    "items"}`.
  - `test_crlf_blank_lines_and_bom` (Review Focus 2): `newline="\r\n"`, trailing blank lines, BOM and no BOM → same
    result as the plain file.
  - `test_item_number_mismatch_fails_file` (Review Focus 2): `D` number differs → `ParseError` with `code ==
    "item_number"`; `str(exc) == "item_number"` (no content).
  - `test_orphan_item_fails_file`: `D` before `M` → `orphan_item`.
  - `test_bad_header_date_amount_number`: each → its code.
  - `test_item_name_with_pipe`: `D|AB00000001|50|測試|品項|` → name `測試|品項`.
  - `test_foreign_decimal_total`: total `20.99`, empty tax id → `Decimal("20.99")`, `seller_tax_id == ""`.
  - `test_repeat_in_one_file_last_wins` (Review Focus 3): same invoice twice → one invoice, later items.
- [ ] **Step 2:** `$PYTEST tests/worker/test_einvoice_parse.py -v` → FAIL.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** Step 2's command → PASS.
- [ ] **Step 5:** commit `feat(accounting): einvoice CSV parser`.

### Task 10: `worker einvoice` job, CLI, timer

**Files:**
- Create: `services/accounting-service/worker/einvoice.py`
- Modify: `services/accounting-service/worker/drive.py:50-70` (`_list_root(self, root, drive_path, *, extensions:
  tuple[str, ...] = (".pdf",))`)
- Modify: `services/accounting-service/worker/api.py` (`ApiClient.import_einvoices`, `ApiClient.match_einvoices`)
- Modify: `services/accounting-service/worker/config.py` (`einvoice_folder: str = "電子發票/財政部"` from
  `STATEMENT_EINVOICE_FOLDER`, `.strip("/")`; `einvoice_timeout_s: int = 60` from `STATEMENT_EINVOICE_TIMEOUT_S`)
- Modify: `services/accounting-service/worker/cli.py` (docstring, `_einvoice`, subparser `einvoice`, dispatch)
- Create: `services/accounting-service/deploy/statements/homehub-einvoice.service`,
  `services/accounting-service/deploy/statements/homehub-einvoice.timer`
- Test: `services/accounting-service/tests/worker/test_einvoice_job.py`; extend
  `services/accounting-service/tests/worker/test_drive.py`, `tests/worker/test_config.py`

**Interfaces:**
- Consumes: Task 9 `parse`, `ParseError`, `to_payload`; Task 4/5 routes; `Drive.download(item, dest, *,
  max_bytes, timeout_s)`; `inbox.private_dir`; `ApiClient`/`ApiError`.
- Produces:
  - `ApiClient.import_einvoices(self, invoices: list[dict], *, timeout: float) -> dict` (`POST
    /einvoices/import`), `ApiClient.match_einvoices(self, *, timeout: float) -> dict` (`POST /einvoices/match`).
  - `worker.einvoice`: `LOCK = ".einvoice.lock"`, `STAGE_PREFIX = "einvoice-"`, `MAX_CSV_BYTES = 5_000_000`,
    `class AlreadyRunning(RuntimeError)`,
    `run(cfg: WorkerConfig, *, drive: Drive, api: ApiClient) -> dict`.
  - Summary (one JSON line; counts, Drive ids and codes only): `files_seen`, `files_parsed`, `parse_failed`,
    `invoices`, `import` (the API's counts or None), `match` (the API's `EinvoiceMatchOut` or None), `failures`
    (`[{"file": <drive id>, "code": <code>}]`), `codes` (run-level: `drive_listing`, `api:<status>`,
    `api:<ExceptionClass>`, `internal:<ExceptionClass>`).
  - Job steps: flock `LOCK` (non-blocking → `AlreadyRunning`); sweep `staging_dir/einvoice-*.csv`; list
    `drive._list_root("einvoice", f"{cfg.drive_root}/{cfg.einvoice_folder}", extensions=(".csv",))`;
    `ListingError` → code `drive_listing`, stop (never `str(exc)`); sort items by `path` in memory; per item:
    download to `staging_dir/einvoice-<pid>-<n>.csv` with `max_bytes=MAX_CSV_BYTES`, check md5 and size (as
    `cathay._Job.fetch`), unlink in `finally`; `DownloadError`/`OSError` → failure `drive:<ExceptionClass>`,
    `ParseError` → failure `parse:<code>`; merge parsed invoices by `(number, issue_date)`, later item wins (R2);
    then `import_einvoices` when the merge is non-empty, else `match_einvoices`. `ApiError` → code
    `api:<status>` (never `str(exc)`); httpx errors → `api:<ExceptionClass>`.
  - Log lines (`logging.getLogger("worker")`): `einvoice: file <drive id> <code>` per failure and one summary line
    `einvoice: files=%d parsed=%d failed=%d invoices=%d`.
  - CLI `python -m worker einvoice`: missing token file → stderr `API token file is missing or not a file`, exit 2;
    `AlreadyRunning` → prints `already running`, exit 3; any other exception → stderr `einvoice failed:
    <ExceptionClass>`, exit 1; else prints the summary and exits 1 when `codes` or `failures` is non-empty, 0
    otherwise. HTTP client: `httpx.Client(base_url=cfg.api_url, timeout=cfg.einvoice_timeout_s)` +
    `ApiClient(http, token)`.
  - Units (pattern `homehub-portfolio-cathay.{service,timer}`): service `Type=oneshot`, same `WorkingDirectory`,
    `EnvironmentFile=-%h/.config/homehub-statements.env`, `UMask=0077`, `ExecStart=… -m worker einvoice`,
    `Nice=10`; timer `OnCalendar=*-*-* 04:10:00 Asia/Taipei`, `Persistent=true`,
    `Unit=homehub-einvoice.service`, `WantedBy=timers.target`.

- [ ] **Step 1: Failing tests** (`FakeRunner`/`Result` from `worker.runner` for rclone, as
  `tests/worker/test_cathay_job.py`; a fake HomeHub `httpx.MockTransport` that records `(method, path, json)`; Drive
  rows with fictional names such as `2026-10_財政部_手機條碼發票彙整_FAKE.csv` and ids `drive-fake-1`…):
  - `test_drive_extension_filter` (`test_drive.py`): default lists `.pdf` only; `extensions=(".csv",)` lists the
    CSV only.
  - `test_config_einvoice_defaults` (`test_config.py`): `load({})` → `einvoice_folder == "電子發票/財政部"`,
    `einvoice_timeout_s == 60`.
  - `test_run_posts_one_merged_import`: two overlapping files (the later file voids one invoice and changes another
    total) → exactly one `POST /einvoices/import`; the shared invoice appears once with the later values and
    `source_drive_id == "drive-fake-2"`; no `/einvoices/match` call.
  - `test_bad_file_does_not_stop_others` (Review Focus 2): file 1 has an orphan `D`, file 2 is fine → `failures ==
    [{"file": "drive-fake-1", "code": "parse:orphan_item"}]`, file 2's invoices posted, exit code 1 via
    `cli.main(["einvoice"])`.
  - `test_no_invoices_calls_match`: empty listing → one `POST /einvoices/match`, `files_seen == 0`.
  - `test_too_large_and_md5_mismatch_are_file_failures`: a `copyid` that writes more than `MAX_CSV_BYTES` → failure
    code `drive:DownloadError`; a download whose md5 differs from the listing → failure code `drive:VerifyError`;
    the other files are still posted.
  - `test_listing_error_is_a_code_without_path`: rclone exit 1 → `codes == ["drive_listing"]`; a listing row
    without md5 → same code; the file name is in neither the summary nor `caplog.text`.
  - `test_api_error_is_a_code`: import answers 503 → `codes == ["api:503"]`; 422 with a body that echoes input →
    `codes == ["api:422"]`, the echoed text absent from `caplog.text` and the summary.
  - `test_privacy_no_names_or_carrier_anywhere`: run over files whose rows carry `測試載具` / `/FAKE+01` and whose
    names contain `FAKE.csv` → none of `測試載具`, `/FAKE+01`, `FAKE.csv`, `財政部_手機條碼` appear in any recorded
    request body, the printed summary (`capsys`), `caplog.text`, or the staging dir file names; staging is empty
    after the run.
  - `test_second_run_already_running`: lock held → `cli.main(["einvoice"])` prints `already running`, returns 3.
  - `test_missing_token_file_exit_2`.
  - `test_units_shape`: parse both unit files with `configparser` → service `ExecStart` ends with `-m worker
    einvoice`; timer `OnCalendar == "*-*-* 04:10:00 Asia/Taipei"`, `Persistent == "true"`.
- [ ] **Step 2:** `$PYTEST tests/worker/test_einvoice_job.py tests/worker/test_drive.py tests/worker/test_config.py
  -v` → FAIL.
- [ ] **Step 3:** implement (CLI: `_einvoice(cfg, args)` beside `_cathay`; dispatch before `runner =
  SubprocessRunner()`; update the module docstring's command list).
- [ ] **Step 4:** Step 2's command plus `$PYTEST tests/worker -q` → PASS.
- [ ] **Step 5:** commit `feat(accounting): worker einvoice job and timer`.

---

## PR C — SPA

### Task 11: SPA models, `EinvoiceService`, bell count

**Files:**
- Create: `frontend/src/app/components/accounting/inbox/einvoice.model.ts`
- Create: `frontend/src/app/components/accounting/inbox/einvoice.service.ts` (+ `einvoice.service.spec.ts`)
- Modify: `frontend/src/app/components/accounting/inbox/inbox.model.ts:96-110` (`InboxCounts.einvoice_ask?:
  number`; `InboxOut.einvoices?: InboxEinvoice[]`)
- Modify: `frontend/src/app/components/accounting/inbox/inbox-view.ts` (`inboxBellCount`)
- Modify: `frontend/src/app/components/accounting/inbox/testing.ts:41-55` (`makeInbox` defaults `einvoice_ask: 0`,
  `einvoices: []`)
- Modify: `frontend/src/app/models/accounting.model.ts` (`LedgerEntry.invoice_item_hits?: string[]`;
  `EntryDetail.einvoice?: EntryEinvoice | null`; `AllEntriesQuery.include_invoice_items?: boolean`)
- Modify (bell sites): `frontend/src/app/services/nav-badge.service.ts:183`,
  `frontend/src/app/components/accounting/timeline/timeline.ts:970`,
  `frontend/src/app/components/accounting/inbox/inbox-panel/inbox-panel.ts:200-203`,
  `frontend/src/app/components/accounting/reminders/reminders.html:31`,
  `frontend/src/app/components/accounting/reminders/reminders.ts:369-375` (R15)
- Test: `einvoice.service.spec.ts`, `inbox-view.spec.ts`, `nav-badge.service.spec.ts`, `inbox-panel.spec.ts`,
  `reminders.spec.ts`, `timeline.spec.ts`

**Interfaces:**
- Produces (`einvoice.model.ts`): `EinvoiceState`, `EinvoiceItem {name; subtotal}`, `EinvoiceTarget {kind:
  'entry' | 'group'; id; account_name; date; amount; currency; category: string | null; name: string | null}`,
  `InboxEinvoice {id; state: 'ambiguous' | 'near' | 'unmatched'; number; issue_date; seller_name; seller_tax_id;
  total_amount; status_raw; items: EinvoiceItem[]; candidates: EinvoiceTarget[]; offer_mute: boolean}`,
  `EntryEinvoice {id; number; issue_date; seller_name; total_amount; linked_by: 'auto' | 'owner'; linked_to:
  'entry' | 'group'; items}`, `EinvoiceSummary {id; number; issue_date; seller_name; total_amount; state; items}`,
  `EinvoicePicker {entry_eligible; group_id: number | null; group_eligible; invoices: EinvoiceSummary[]}`,
  `EinvoiceMute {seller_tax_id; seller_name: string | null; created_at}`, `EinvoicePreferences {enabled;
  remind_unmatched; version}`, `EinvoiceStateOut {id; state; dismiss_reason; linked_by; entry_id; group_id}`,
  `EinvoiceLinkTarget = { entry_id: number } | { group_id: number }`. Field names = the Task 4–8 schemas.
- Produces (`EinvoiceService`, `providedIn: 'root'`, base `/api/accounting/einvoices`): `link(id, target:
  EinvoiceLinkTarget)`, `unlink(id)`, `dismiss(id, muteSeller = false)` (body `{mute_seller}`), `undismiss(id)` →
  `Observable<EinvoiceStateOut>`; `targets(id, days = 14): Observable<EinvoiceTarget[]>`; `picker(entryId):
  Observable<EinvoicePicker>`; `dismissed(): Observable<EinvoiceDismissed[]>` (`GET …/dismissed`; `EinvoiceDismissed` =
  `EinvoiceSummary` + `dismiss_reason`); `mutes(): Observable<EinvoiceMute[]>`; `deleteMute(taxId): Observable<void>`
  (path-encoded); `preferences(): Observable<EinvoicePreferences>`; `setRemindUnmatched(on: boolean)` (PATCH). Every
  write pipes `tap(() => accounting.markEntriesChanged())` (`accounting.service.ts:95-97`), so the reminders page,
  timeline and nav badge re-read.
- Produces (`inbox-view.ts`): `inboxBellCount(counts: Pick<InboxCounts, 'ask' | 'einvoice_ask'>): number` = `ask +
  (einvoice_ask ?? 0)`.
- Bell sites use it: `nav-badge` maps `data => inboxBellCount(data.counts)`; `timeline.inboxAsk.set(inboxBellCount(
  data.counts))`; `headText` = `要你決定 ${inboxBellCount(counts)} · 其他 …`; `reminders.html` gate
  `inboxBellCount(data.counts) > 0` (via a component method or computed); in `reminders.ts:369-375` the helper
  replaces only the `counts.ask` term: `todo` = `inboxBellCount(counts) + proposed + balance_gap + settlement +
  dividend + other`, `all` branch likewise. Test: a page whose only cards are `proposed` is not empty; a page whose
  only questions are invoices is not empty.

- [ ] **Step 1: Failing tests:**
  - `einvoice.service.spec.ts`: each method hits the right URL and verb (e.g. `link(7, {entry_id: 3})` → `POST
    /api/accounting/einvoices/7/link` body `{entry_id: 3}`; `dismiss(7, true)` body `{mute_seller: true}`;
    `targets(7)` → `?days=14`; `picker(3)` → `/picker?entry_id=3`; `deleteMute('00000001')` → `DELETE
    …/mutes/00000001`); a write bumps `AccountingService.entriesChanged`, a read does not.
  - `inbox-view.spec.ts`: `inboxBellCount({ask: 2, einvoice_ask: 3}) === 5`; `inboxBellCount({ask: 2}) === 2`.
  - `nav-badge.service.spec.ts`: an inbox reply with `counts {ask: 1, einvoice_ask: 2}` → reminders input
    `inboxAsk === 3`.
  - `timeline.spec.ts`: same reply → the 🔔 count includes 3.
  - `inbox-panel.spec.ts`: head text `要你決定 3 · 其他 0`.
  - `reminders.spec.ts`: on 全部, an inbox with `ask 0, einvoice_ask 1` shows the 待處理 preview section and the
    page is not empty.
- [ ] **Step 2:** run each spec with `npx ng test --watch=false --include <spec>` → FAIL.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** the specs → PASS; full `npx ng test --watch=false` and `npx ng build` → PASS.
- [ ] **Step 5:** commit `feat(frontend): einvoice models, service and bell count`.

### Task 12: Inbox section 「發票待對帳」

**Files:**
- Create: `frontend/src/app/components/accounting/inbox/einvoice-section/einvoice-section.{ts,html,scss,spec.ts}`
- Create: `frontend/src/app/components/accounting/inbox/einvoice-target-sheet/einvoice-target-sheet.{ts,html,scss,spec.ts}`
- Modify: `frontend/src/app/components/accounting/inbox/inbox-panel/inbox-panel.{ts,html}` (render the section)

**Interfaces:**
- Consumes: Task 11 `EinvoiceService`, `InboxEinvoice`, `EinvoiceTarget`; `formatMoney` (`../../format`),
  `slashDate` (`../../dates`); the panel's card styles (`inbox-panel.scss`, `../sheet.scss`).
- Produces:
  - `AppEinvoiceSectionComponent` (`selector: 'app-einvoice-section'`, OnPush): inputs `items:
    InboxEinvoice[]` (required), `mode: 'full' | 'preview'` (default `'full'`); output `changed`. Preview mode shows
    `ambiguous`/`near` cards only. Renders `<h3>發票待對帳</h3>` and one card per invoice: seller, `slashDate`,
    amount, items (name + subtotal).
    - `ambiguous` / `near`: question 「哪一筆是這張發票？」 / 「金額或商家相近，是這筆嗎？」 (R14); one button per
      candidate (account · date · amount · category · name) → `link`; 「都不是」 → `dismiss`; 「選其他帳…」 →
      opens the target sheet.
    - `unmatched`: title 「有發票沒有帳」; 「不用記帳」 → `dismiss`; 「選帳…」 → target sheet; when `offer_mute`,
      「這家以後都不提醒」 → `dismiss(id, true)`.
    - A card leaves at once after a successful write (local `gone` set, as the panel's); a refusal shows
      `沒成功：…` from `einvoiceErrorText(err)`.
  - `einvoiceErrorText(err: unknown): string` (exported from the section file): maps 409 codes `einvoice_void` →
    「發票已作廢」, `target_ineligible` → 「這筆帳不能連結」, `not_linked` → 「已解除連結」, `seller_muted` →
    「這家已設為不提醒」, `retry` → 「請再試一次」; otherwise `INBOX_GENERIC_ERROR`.
  - `AppEinvoiceTargetSheetComponent` (`selector: 'app-einvoice-target-sheet'`): input `invoice: InboxEinvoice`;
    outputs `picked: EinvoiceLinkTarget`, `closed`. Loads `targets(invoice.id)` and lists them like the match
    sheet; 讀取中 / 讀取失敗 / 沒有可連結的帳 states.
  - `inbox-panel.html`: `<app-einvoice-section [items]="data().einvoices ?? []" [mode]="mode()" (changed)="changed.emit()" />`
    after `.ask-cards` and the more-link, rendered only when the list (filtered for preview) is non-empty.

- [ ] **Step 1: Failing tests** (`einvoice-section.spec.ts`, `einvoice-target-sheet.spec.ts`, plus one in
  `inbox-panel.spec.ts`; `HttpTestingController`; fictional data):
  - `renders the three card kinds with items`: one of each state → title 「發票待對帳」, 「有發票沒有帳」 on the
    unmatched card, item names visible.
  - `one-tap link posts and removes the card`: click the first candidate → `POST …/7/link` `{entry_id: 3}`; flush →
    card gone, `changed` emitted once.
  - `都不是 and 不用記帳 dismiss`: → `POST …/dismiss` `{mute_seller: false}`.
  - `mute offer only when offered`: `offer_mute` true → 「這家以後都不提醒」 posts `{mute_seller: true}`; false → no
    button.
  - `group candidate links by group_id`.
  - `refusal shows the mapped text`: 409 `{detail: "target_ineligible"}` → 「沒成功：這筆帳不能連結」, card stays.
  - `preview hides unmatched reminders`.
  - `target sheet lists targets and emits picked`: flush `targets` → two rows; click → `picked` `{entry_id: …}`;
    empty list → 「沒有可連結的帳」.
  - `inbox-panel renders the section when einvoices exist` and not when `einvoices` is missing or empty.
- [ ] **Step 2:** the three specs → FAIL.
- [ ] **Step 3:** implement (minimal layout reusing the existing card and sheet classes, §9 **(owner)**).
- [ ] **Step 4:** the specs → PASS; full `npx ng test --watch=false` and `npx ng build` → PASS.
- [ ] **Step 5:** commit `feat(frontend): einvoice inbox section`.

### Task 13: Entry-detail 「發票」 card

**Files:**
- Create: `frontend/src/app/components/accounting/entry-detail/einvoice-card.{ts,html,spec.ts}`
- Modify: `frontend/src/app/components/accounting/entry-detail/entry-detail.ts:69` (imports), `entry-detail.html`
  (after the 預估回饋 block, :109-117)

**Interfaces:**
- Consumes: Task 11 `EinvoiceService`, `EntryEinvoice`, `EinvoicePicker`; `EntryDetail`.
- Produces: `AppEntryEinvoiceCardComponent` (`selector: 'app-entry-einvoice-card'`): input `detail: EntryDetail`
  (required); output `changed` (the parent calls `load(detail.id, true)`).
  - With `detail.einvoice`: section 「發票」 with number, `slashDate(issue_date)`, seller, total, 「自動連結」 /
    「手動連結」, 「整組」 note when `linked_to === 'group'`, items; 「解除連結」 → `unlink(einvoice.id)` → `changed`.
  - Without: 「連結發票…」 button. On click it loads `picker(detail.id)`. When neither `entry_eligible` nor
    `group_eligible`, the button is hidden after the load (the entry is not eligible, §10). For a split member with
    both eligible, it asks 「這筆」 / 「整組」 first; with one eligible, it uses that one. Then it lists the
    invoices (seller · date · total · first items); a tap → `link(id, {entry_id} | {group_id})` → `changed`.
  - Errors use `einvoiceErrorText` (Task 12).
- `entry-detail.html`: `<app-entry-einvoice-card [detail]="d" (changed)="load(d.id, true)" />`. It shows on
  MOZE-locked rows too: a link never edits the entry's owner fields (R8: the stamp is link metadata).

- [ ] **Step 1: Failing tests** (`einvoice-card.spec.ts`; one wiring test in `entry-detail.spec.ts`):
  - `shows the linked invoice and unlinks`: detail with `einvoice` → 「發票」, items, 「自動連結」; 「解除連結」 →
    `POST …/unlink`; flush → `changed` emitted.
  - `group invoice is marked 整組`.
  - `picker for a plain entry`: no `einvoice`; click 「連結發票…」 → `GET …/picker?entry_id=…`; flush two invoices →
    tap one → `POST …/link` `{entry_id}`.
  - `split member asks 這筆 or 整組`: picker with both eligible → choose 「整組」 → link body `{group_id}`.
  - `not eligible hides the picker`: both false → no invoice list, the button gone.
  - `entry-detail mounts the card and reloads on changed`.
- [ ] **Step 2:** the specs → FAIL.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** the specs → PASS; full suite and build → PASS.
- [ ] **Step 5:** commit `feat(frontend): einvoice card on entry detail`.

### Task 14: Settings 「發票」 section and timeline item hits

**Files:**
- Modify: `frontend/src/app/components/accounting/accounting-settings/accounting-settings.{ts,html}` (a 「發票」
  block after 顯示, before 匯入 at html :163)
- Modify: `frontend/src/app/components/accounting/timeline/timeline.ts` (`TimelineRow.invoiceHits: string | null`
  :134-153; set in `buildDays` :238; send `include_invoice_items: true` with `q` at :787 and :909),
  `timeline.html:133-158` (`<small class="invoice-hits">`)
- Test: `accounting-settings.spec.ts`, `timeline.spec.ts`

**Interfaces:**
- Consumes: Task 11 `EinvoiceService.preferences/setRemindUnmatched/mutes/deleteMute`;
  `LedgerEntry.invoice_item_hits`.
- Produces:
  - Settings: on init `preferences()` and `mutes()`. Shown only when `preferences().enabled`. Switch 「沒有帳的發票
    提醒」 (`role="switch"`, class `tog`, as `pref-hide-rewards`) → `setRemindUnmatched(!on)`; list 「不提醒的商家」
    with seller name (or tax id) and 「移除」 (two-tap confirm like `row-delete`) → `deleteMute(taxId)` → reload the
    list. A failed load shows 「發票設定讀取失敗」.
  - List 「已忽略的發票」 (owner ruling 2026-11-06): `dismissed()` rows with seller · date · amount and the item
    names; 「復原」 → `undismiss(id)` → reload the list. Empty → 「沒有已忽略的發票」.
  - Timeline: `invoiceHits = entry.invoice_item_hits?.length ? '發票：' + hits.join('、') : null`; rendered under
    the title only when set.

- [ ] **Step 1: Failing tests:**
  - `accounting-settings.spec.ts`: `einvoice section hidden while disabled`; `switch toggles remind_unmatched` →
    `PATCH …/preferences` `{remind_unmatched: false}`; `removes a mute after confirm` → `DELETE …/mutes/00000001`,
    then a reloaded list without it; `lists dismissed invoices and restores one` → 「復原」 posts `…/{id}/undismiss`,
    then a reloaded list without it; empty list → 「沒有已忽略的發票」.
  - `timeline.spec.ts`: a search sends `include_invoice_items=true` with `q`; without `q` it is not sent; a row
    with `invoice_item_hits: ['衛生紙', '牙膏']` shows 「發票：衛生紙、牙膏」; a row without hits shows nothing.
- [ ] **Step 2:** the specs → FAIL.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** the specs → PASS; full suite and build → PASS (PR C gate).
- [ ] **Step 5:** commit `feat(frontend): einvoice settings and search hits`.

---

### Task 15: Docs and rollout runbook (§13)

**Files:**
- Modify: `services/accounting-service/deploy/statements/README.md` (install glob at :27 gains `homehub-einvoice`;
  new section 「電子發票 (`homehub-einvoice`)」)
- Modify: `services/accounting-service/README.md` (one line in the worker command list, if it lists subcommands)

**Interfaces:** none (text only; fictional values; no real invoice numbers, Drive ids or account ids).

- [ ] **Step 1: Write the README section:** what the job does (§6), the Drive folder, state/lock files
  (`<state_dir>/.einvoice.lock`, staging `einvoice-*.csv` removed after each file), the summary fields and exit
  codes (0 / 1 / 2 / 3), the privacy rule (Drive ids only; carrier columns dropped), the Loki query
  `{job="homehub", unit="homehub-einvoice.service"} |~ "\"codes\": \\[\"|\"failures\": \\[\\{"`, and this runbook:
  1. Merge PR A. Deploy the API by the existing API release procedure: alembic upgrade to `b6e2c8f4a9d1` on the
     host checkout (never from a worktree), then restart the API. Settings keep `einvoice.enabled = false` (the
     default).
  2. Merge PR B. Update the worker checkout. Link and install the units:
     `ln -sf …/deploy/statements/homehub-einvoice.{service,timer} ~/.config/systemd/user/ && systemctl --user
     daemon-reload`. Add `_SYSTEMD_USER_UNIT=homehub-einvoice.service` to the Alloy journal matcher. Run once as
     the worker user:
     `sudo -u homehub-worker -H bash -c 'cd ~/workspace/home-hub/services/accounting-service && set -a && . ~/.config/homehub-statements.env && set +a && .venv/bin/python -m worker einvoice'`
     → `import.inserted` > 0, `match.ran == false`, `failures == []`.
  3. Runbook dismiss: the owner's list of already-ruled invoices (number + issue date) is kept in the release
     record, never in the repo. `POST /einvoices/runbook-dismiss` with the admin token and that list →
     `not_found == []` (any index there is checked with the owner).
  4. Settings PUT from the current document with `einvoice.enabled = true` (admin; start from the current version,
     the PUT replaces the whole document). Run `worker einvoice` again → `match.ran == true`. Review the inbox
     section with the owner. Then `systemctl --user enable --now homehub-einvoice.timer` and check
     `systemctl --user list-timers 'homehub-*'`.
  5. Merge PR C and publish the SPA by the existing SPA release procedure (no new SPA route, so the Caddy SPA
     allowlist is unchanged; `/api/accounting/einvoices` sits under the existing API proxy).
  Rollback: stop the timer; settings `einvoice.enabled = false` hides the section and stops matching; the API and
  SPA roll back by release; the migration's downgrade drops the four tables.
- [ ] **Step 2: Check:** `grep -nE "[A-Z]{2}[0-9]{8}" services/accounting-service/deploy/statements/README.md` shows
  only the fictional `AB00000001` style values (or nothing); no Drive id other than `drive-fake-*`.
- [ ] **Step 3:** commit `docs(accounting): einvoice worker and rollout runbook`.

---

## Self-review notes

- Spec coverage: §4 → Tasks 4, 9; §5 → Tasks 1, 4, 6; §6 → Tasks 4, 9, 10; §7.1–7.4 → Tasks 2, 5; §7.5 → Task 6;
  §7.6 → Tasks 5, 8, 13; §7.7 → Tasks 5, 6 (each row has a test); §8 → Tasks 3, 6, 7, 14; §9 → Tasks 7, 11, 12;
  §10 → Tasks 8, 13, 14; §11 → Task 3; §12 → Tasks 1 (cascade), 6 (MOZE full replace), 5 (structural keys);
  §13 → Task 15; §14 → tests named in Tasks 4–10.
- Review Focus 1–5 each have their test in the owning task.
