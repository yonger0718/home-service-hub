# Reward rules (回饋規則) — design

Rev 1, 2026-10-31. Owner-approved direction (2026-10-31): reuse the existing `reward_rule` model as it stands, open it
up for creation and editing through the API (owner and the Hermes agent) and the SPA, and leave the posting engine
and the agent-side guessing to later phases. This document fully specifies **phase 1**; phases 2 and 3 are stated as
direction so phase 1 does not paint them into a corner.

## 1. Intent

HomeHub is the primary ledger. Card rewards (紅利回饋, points, miles) are a real part of the owner's card economics
and the owner used to manage them in MOZE: every card had rules, each purchase picked the rule(s) it earned under,
and MOZE posted the reward on the card's schedule. The MOZE backup import carried all of that into HomeHub —
the `reward_rule` table (95 rules on 18 cards), the purchase→rule links (2878 rows of `entry_reward_rule`) and
2379 posted reward rows linked to their rule — and the entry form already offers a card's rules as chips with a
預估回饋 line. Three things are missing, in the order the owner wants them:

1. **Rules cannot be created or edited.** The only writer is the MOZE backup import. Six open credit cards have no
   rules at all; rules for cards whose bank changed terms cannot be updated; 31 enabled rules are already past
   their `ends_on`. A new card today cannot be set up the way the owner described: 帳單週期, 繳款日, 自動扣繳 (all
   exist on the account) **and** 回饋有哪些.
2. **Nothing posts rewards.** Natively entered HomeHub purchases get an estimate on the form and nothing else
   (October 2026: 35 native expense rows, 0 reward rows; every reward row that month came from the backup
   import). Phase 2.
3. **The agent does not know rewards exist.** Hermes records purchases through `POST /entries` without
   `reward_rule_ids`. The owner wants it to guess the right chip (Cube 卡 has 0 / 0.3 / 2 / 3 / 5 / 10 % tiers),
   be corrected, and remember. Phase 3, seeded by the skill text in phase 1.

Success for phase 1: the owner (in the SPA) or Hermes (through the API) can define the rewards of a new card in
one sitting, the rules show up as chips on the next purchase, and a cross-card overview answers 「我有哪些回饋、哪些
還在跑、哪些實際有產出」 without opening MOZE.

## 2. Goals / non-goals / anti-goals

Goals (phase 1)
- Create / update / disable / delete reward rules for any account through the accounting API, under the existing
  `write` scope so the `hermes` token can use it.
- A 回饋規則 block on the account settings page: the card's rules as rows, an editor sheet per rule, add / edit /
  啟用 / 停用 / 刪除, reordering.
- A cross-account 回饋規則總覽 page: every rule, grouped by card, with its state (啟用中 / 已停用 / 已到期) and what it
  has actually produced (linked purchases, posted reward rows and their total).
- The MOZE backup import must never overwrite or delete a rule the owner has touched in HomeHub.
- The Hermes `homehub-api` skill learns the endpoints and the recording habit: look the card's rules up, attach the
  likely one(s) with `reward_rule_ids`, say which it picked, remember corrections.

Non-goals (later phases, their own design rounds)
- **Phase 2 — posting engine.** Turning linked purchases into reward rows (預估 at save, 已入帳 on the posting date,
  bank figure wins when the statement arrives, 未獲得 when it does not), the project inheritance and PUT propagation
  of issue #262, and the change to `create_system_entry` for statement reward lines. §9 states the direction.
- **Phase 3 — guessing.** Merchant → rule memory lives in the Hermes skill / memory, not in HomeHub.
- MCC codes, bank T&C scraping, any automatic rule discovery.
- Points-account reconciliation (小樹點 → 哩程 redemptions). Existing 餘額調整 covers it; §9.3 notes the one UI nudge
  that may come with phase 2.
- Changing the entry form's chips or estimate maths. They already do what the owner described.

Anti-goals
- A backup import that silently resets a rule the owner edited, or deletes a rule a purchase still points at.
- Deleting a rule that purchases or reward rows reference (FKs are RESTRICT; the API must say 409, not 500).
- Rules the SPA can create but Hermes cannot, or vice versa: one API, one validation.
- Owner financial data in the repo, tests, commit messages or Multica briefs. Fixtures are fictional; this document
  carries counts only.

## 3. Constraints

- **Model reuse.** `reward_rule` (`app/models/ledger.py`) and `RewardRuleOut` (`app/schemas/ledger.py`) stay the
  contract: `method` percent|fixed, `window` statement_cycle (the only value the enum has), `posting`
  after_window|after_transaction|manual, `delay_days`, `post_month_offset` 0–2, `post_day` 1–31, `txn_rounding` /
  `total_rounding` keep|round|floor|ceil, `total_cap`, `shared_cap_id` (UUID shared by the rules that share one cap),
  `is_basic`, `reward_account_id`, `reward_project_id`, `starts_on`, `ends_on`, `is_enabled`, `description`,
  `sort_order`, `moze_id`. One column is added (§5.2); nothing is renamed.
- **Scopes.** Rule routes sit under the `/accounts` router (`method_scope()` → GET needs `read`, writes need `write`),
  so the `hermes` label (read, propose, write; no admin) can write them. Settings routes need `admin`; rule CRUD is
  deliberately not a settings route.
- **Backup import coexistence.** `POST /imports/moze-backup` is manual (no timer) but still used. Its rule pass
  (`moze_backup_import_service.py` ~L425–480) upserts by `moze_id` and deletes "gone" MOZE rules that nothing
  references. Phase 1 fences it (§5.2).
- **SPA.** Angular 21, standalone components, signals; phone-first with the wide layout; `anyComponentStyle` budget
  16 kB warn / 20 kB error; routes are allow-listed in Caddy (`@hub_spa` regexp) — a new route 404s until the
  allowlist is extended (memory `homehub-caddy-spa-allowlist`).
- **Tests.** Backend `tests/integration` (fixtures, head-constant pattern in `test_migration.py`), frontend Vitest
  specs beside the components. Alembic head today: `c7a2e9f4b1d8`.
- **Demo stack.** Multica reviewers only see 127.0.0.1:18080 with fictional data; nothing in this design needs
  owner data to review.

## 4. Shape

```
phase 1 (this design)
  API      POST/PUT/DELETE reward rules · GET /reward-rules overview with stats · locally_edited fence on import
  SPA      account settings › 回饋規則 block + editor sheet · 回饋規則總覽 page · (entry form unchanged)
  Hermes   skill text: endpoints, "attach the likely rule" habit, remember corrections, 餘額調整 for points accounts
phase 2   posting engine (預估 → 已入帳 → bank wins / 未獲得), #262 inheritance + PUT propagation, statement matching
phase 3   Hermes merchant→rule memory, MCC-miss memory
```

## 5. Backend

### 5.1 Semantics of a rule (what the UI and the skill say about each field)

| field | meaning | UI label / rendering | validation (422 on failure) |
|---|---|---|---|
| `name` | what the bank calls it | 名稱 | 1–128 chars, trimmed, unique per account among enabled rules? **No** — MOZE has duplicates; not enforced |
| `method` + `rate` / `fixed_amount` | % of the purchase, or a fixed amount per purchase | 方式: 比例 % / 固定金額 | percent: `rate` required, 0 < rate ≤ 100, 4 dp, `fixed_amount` null; fixed: `fixed_amount` required > 0, `rate` null |
| `window` | the accumulation window for caps and 總額 rounding | not shown (only 帳單週期 exists) | must be `statement_cycle` |
| `posting` | when the reward lands | 入帳: 帳單結帳後 / 交易後 / 手動 | enum |
| `post_month_offset`, `post_day` | after_window: posts on `post_day` of the month `offset` months after the window's closing month | 結帳後第 N 月 D 日 (shown only for 帳單結帳後) | 0–2 and 1–31 (DB checks exist); ignored and reset to 0 / 1 for the other postings |
| `delay_days` | after_transaction: days after the purchase | 交易後 N 天 (shown only for 交易後) | 0–365; reset to 0 otherwise |
| `txn_rounding`, `total_rounding` | per-purchase and per-window rounding | 單筆 / 總額 四捨五入·無條件捨去·無條件進位·不取整 | enum |
| `total_cap` | per-window cap in the reward account's unit | 每期上限 | null or > 0 |
| `shared_cap_id` | rules sharing one cap | 與…共用上限 (picker of the account's other rules; see 5.3) | null, or a UUID some rule of the **same account** carries, or a fresh UUID from the server |
| `is_basic` | the always-on base rate of the card | 基本回饋 toggle | at most one enabled basic rule per account at a time; a second one is 409 `basic_rule_exists` |
| `reward_account_id` | where the reward lands (points / miles account, or the card itself) | 入帳帳戶 | must exist, not archived; null means the card itself (the import stores the card id explicitly — both are accepted, `rule_out` returns what is stored) |
| `reward_project_id` | project the posted reward carries | 專案 | must exist, not archived; phase 2 inheritance (#262) wins when null |
| `starts_on`, `ends_on` | validity; the entry form offers a rule only if enabled and the purchase date is inside | 期間 | `starts_on ≤ ends_on` when both set |
| `is_enabled` | hard switch | 啟用 | — |
| `description` | free text (bank wording, exclusions) | 說明 | ≤ 2000 chars |
| `sort_order` | chip order on the form and row order in settings | drag / 上移 下移 | integer |
| `moze_id` | import key | read-only badge 來自 MOZE | never writable |

A rule's **state** for display: 已停用 (`is_enabled` false) · 已到期 (`ends_on` < today) · 尚未開始 (`starts_on` > today)
· 啟用中 (otherwise). Today 31 rules are enabled-but-expired; the form already hides them (`rulesForDate`), the
overview must make that visible.

### 5.2 Fencing the backup import — `locally_edited`

Migration (new head after `c7a2e9f4b1d8`): `reward_rule.locally_edited BOOLEAN NOT NULL DEFAULT false`. Mirrors
`account.settings_locally_edited`.

- Set `true` by every API create / update / reorder / delete-attempt path of §5.3. A rule created through the API has
  `moze_id` null and `locally_edited` true.
- `_import_reward_rules` (backup import): a MOZE record whose `moze_id` matches a rule with `locally_edited` true is
  **skipped** (counted in the report as `rules_kept_local`, the way accounts report `settings_locally_edited`);
  the "gone" sweep never deletes a `locally_edited` rule. Everything else in the import is unchanged.
- `RewardRuleOut` gains `locally_edited: bool` so the SPA can show 本地已修改 beside 來自 MOZE, as the account page does.

### 5.3 Endpoints (all under the `/accounts` router's scope dependency)

| route | purpose | notes |
|---|---|---|
| `GET /accounts/{id}/reward-rules` | exists | unchanged shape plus `locally_edited`; keeps returning every rule (the form filters by date itself) |
| `POST /accounts/{id}/reward-rules` → 201 `RewardRuleOut` | create | body `RewardRuleIn` (every writable field of 5.1; `sort_order` optional → appended last); `share_cap_with: int | null` (another rule id of the same account) sets `shared_cap_id` to that rule's UUID, creating one on both when it has none; 409 `basic_rule_exists` |
| `PUT /accounts/{id}/reward-rules/{rule_id}` → `RewardRuleOut` | full update | same body; the whole row is written (the SPA sends everything it has, as `saveProject` does); `moze_id` untouched; sets `locally_edited` |
| `PUT /accounts/{id}/reward-rules/order` → `list[RewardRuleOut]` | reorder | body `{ "ids": [...] }` = every rule of the account exactly once; 422 otherwise; sets `locally_edited` on all |
| `DELETE /accounts/{id}/reward-rules/{rule_id}` → 204 | delete | 409 `rule_referenced` with `{"entries": n, "rewards": m}` when `entry_reward_rule` or `ledger_entry.reward_rule_id` points at it — the SPA then offers 停用 instead; a rule that is the last member of a shared cap leaves the UUID with the remaining member(s) (harmless) |
| `GET /reward-rules` → `RewardRuleOverviewOut` | the overview | query `account_id` (repeatable), `state` = active\|disabled\|expired\|all (default all), `include_archived_accounts=false`; returns `accounts: [{account_id, account_name, is_credit, is_archived, rules: [RewardRuleOut + stats]}]` sorted by account `sort_order`, name |

`stats` per rule (counts and sums; no owner-specific semantics):

```
linked_entries        count(entry_reward_rule.rule_id = id)
linked_entries_12m    same, entry_date in the last 12 months
rewards_count         count(ledger_entry.kind = 'reward' and reward_rule_id = id)
rewards_total         sum(amount) of those rows, in the reward account's currency (string decimal)
rewards_last_date     max(entry_date) of those rows, null when none
```

One grouped query per statistic family (no per-rule round trips); the overview on today's data is 95 rules and
must stay well under the 300 ms the reports hold themselves to.

Error style follows the ledger routes: `service_errors()` → 404 `account not found` / `rule not found`, 409 with a
`code`, 422 pydantic. A rule's `account_id` never changes (move = delete + create).

### 5.4 Service

`app/services/reward_rule_service.py`: `create`, `update`, `reorder`, `delete`, `overview`; `ledger_service.rule_out`
and `_rules_by_account` stay the readers. Validation lives in the schema (`RewardRuleIn` with a model validator for
the per-posting field resets and the method pairing) plus the service for cross-row checks (basic uniqueness, shared
cap same-account, referenced delete).

## 6. SPA

### 6.1 Account settings › 回饋規則

Placement: a block after 自動扣繳 / 額度共用 on `accounts/:id/settings`, for every account (credit or not — Line Bank
style debit cards carry rules too). Hidden on `accounts/new` until the account exists (the block needs an id); the
page tells the owner 儲存帳戶後即可新增回饋規則.

Rows (phone: stacked card; wide: table): `ruleLabel` (the same text the chip shows: 名稱 (3%) / 名稱 ($50)), a one-line
入帳 summary (帳單結帳後第 1 月 15 日 / 交易後 3 天 / 手動), 期間 (yyyy/MM/dd – yyyy/MM/dd, 無期限), state badge, 基本 badge,
來自 MOZE / 本地已修改 badge, 啟用 switch (`app-switch`, saves immediately like the project 不計入報表 switch), 編輯,
上移 / 下移 (wide) or a reorder mode (phone). 新增規則 at the end.

Editor: a sheet (the `sheet.scss` pattern the fee / fx sheets use) with four groups — 基本 (名稱, 方式, 比例/金額, 基本回饋,
說明), 入帳 (入帳 select; the dependent fields appear for the chosen posting; 入帳帳戶 picker of open accounts with the
card preselected; 專案 select), 上限 (每期上限, 單筆取整, 總額取整, 與…共用上限), 期間 (起 / 迄, 啟用). Save = POST or PUT, then
the account detail is re-read so the block and the form's chips agree. 刪除 asks once; on 409 the sheet says 此規則已有
N 筆交易 / M 筆回饋引用，改為停用？ with a 停用 button.

Dirty-form rules as the rest of the page (`dirty-form.service`): closing the sheet with unsaved input asks first.

### 6.2 回饋規則總覽

Route `accounting/reward-rules` (phone + wide; added to `app.routes.ts` **and** the Caddy allowlist). Entry points:
a 回饋規則 link in 設定 (accounting-settings page, beside 分類 / 專案) and 全部卡片 in the block header of 6.1.

Content: filter chips 啟用中 / 已到期 / 已停用 / 全部 (default 啟用中), then one card per account (archived accounts
hidden unless 顯示封存) with its rules as rows: label, 入帳 summary, 期間, state, and the stats: 綁定 N 筆 (近 12 月 M),
已入帳 K 筆 · 合計 X <currency>, 最近 yyyy/MM/dd. A rule row links to the account's settings page with the rule's
editor open (`?rule=<id>`). Cards without rules show 尚無規則 → 新增 (to the account settings). Empty overall: 尚無回饋
規則.

### 6.3 Models / service

`RewardRule` gains `locally_edited`; new `RewardRuleInput`, `RewardRuleOverview`; `AccountingService` gains
`createRewardRule`, `updateRewardRule`, `reorderRewardRules`, `deleteRewardRule`, `getRewardRuleOverview`, all through
`bump` so dependent views refresh. The entry form keeps reading rules from `AccountDetail.reward_rules`.

## 7. Hermes skill (`~/.hermes/skills/productivity/homehub-api/`, outside the repo)

`references/accounting-api.md` gains a section 回饋規則 with the routes of §5.3 and these habits; `SKILL.md`'s
"Write Operations" list adds reward rules. Text is drafted in the plan and applied at rollout (the skill is not in
git; the PR body quotes it):

1. **Recording a card purchase**: GET the account's `reward-rules` once per session, keep the enabled, in-date ones.
   Pick the rule(s) the merchant / category most plausibly earns under (the 基本 rule when nothing better fits),
   send them as `reward_rule_ids`, and tell the owner in one line which chip was attached (名稱 (3%)). If the owner
   corrects it, PUT the entry with the corrected ids and write the pair 「商家/類別 → 規則」 into memory. Unknown card
   or no plausible rule: attach nothing and say so.
2. **Defining a card's rewards** when the owner describes them in chat: translate to `RewardRuleIn` rows, show the
   table back, then POST one by one; report ids.
3. **Points accounts**: when the owner states a current balance (「小樹點現在 12,345」), `POST /balance-adjustments`
   with `target_balance` and description 核對; never guess a balance.
4. Never disable or delete a rule without the owner asking; prefer 停用 to 刪除.

## 8. Rollout

1. API: migrate (new head), deploy from the release checkout as usual; `GET /reward-rules` smoke with the hermes
   token (counts only).
2. SPA release per the standing recipe; **extend the Caddy `@hub_spa` regexp** with `reward-rules` before the live
   probe; probe `/hub/accounting/reward-rules`.
3. Skill text applied; a one-line test: Hermes lists a card's rules.
4. Owner (or Hermes from the owner's description) defines the six rule-less cards; no data backfill in phase 1.
5. Issues to open at closeout: phase 2 design (engine), phase 3 (guess memory), and anything the review parks.

Rollback: SPA → previous publisher record; API → previous release checkout (the migration is additive; a rollback
leaves the column in place).

## 9. Direction for the later phases (not specified here)

### 9.1 Phase 2 — posting engine (own design round)

Owner rulings already in hand (2026-10-31):
- A purchase with linked rules creates its reward row(s) **at save**, state 預估, `entry_date` = computed posting
  date, amount = the form's estimate, `reward_source_entry_id` / `reward_rule_id` set, project inherited from the
  purchase (#262); balances and reports ignore 預估 rows.
- On the posting date the window is recomputed (cap, shared cap, 總額 rounding) and the row becomes 已入帳.
- When the card statement arrives, its reward line matches the window's 預估/已入帳 rows: the **bank figure wins**,
  rows the bank did not pay become 未獲得 (kept, amount 0, visible), unmatched bank rewards are booked as today
  (`create_system_entry`). The weekly digest reports 預估 / 實得 / 差 and the 未獲得 count. The owner does not reconcile
  by hand.
- `PUT /entries/{id}` changing a purchase's project moves its rewards' project (null or equal-to-old only).

Open for that design: the reward-state column vs reusing `needs_review`/`posted_date`, estimate currency when
`reward_account_id` is a points account, what the engine does for `manual` posting, and re-running the engine over
the MOZE years as its oracle (2379 posted rows to diff against).

### 9.2 Phase 3 — guessing

Lives in the Hermes skill and its memory: merchant → rule pairs, and 「這家刷 X 卡不算 Y」 misses learned from phase 2's
未獲得 rows. No HomeHub change expected beyond maybe a read endpoint listing a merchant's past rule choices.

### 9.3 Points accounts

Redemptions (小樹點 → 哩程) stay 餘額調整. A possible phase 2 nudge: the overview shows each points account's 上次核對日,
and the weekly digest says 「小樹點 N 天未核對」 past a threshold.

## 10. Left to the builder

- Exact `RewardRuleIn` pydantic shape and the validator split between schema and service.
- The overview query plan (one CTE per stats family vs one query with conditional aggregates).
- Sheet layout details, reorder affordance on phones, how `?rule=<id>` opens the editor.
- Whether `GET /reward-rules` stats use `entry_date` or `posted_date` for the 12-month window (pick `entry_date`,
  the reports' convention, unless a reason appears).
- Test fixtures (fictional cards with two or three rules each, one shared cap, one basic rule, one expired).

## 11. Decisions made in this document (the owner can veto)

- Rule CRUD under `/accounts/{id}/reward-rules` with the `write` scope, so Hermes can write without admin.
- `locally_edited` fence instead of nulling `moze_id`: the import keeps recognising the row, it just stops touching it.
- Delete is refused (409) when referenced; 停用 is the normal path. No soft-delete column.
- Overview is its own page (`accounting/reward-rules`), linked from 設定 and from each account's block, rather than a
  section of the settings page: 95 rules with stats is too much for that page.
- `window` stays a single-value enum; no new windows in phase 1.
- Stats are all-time plus a 12-month count; no per-month history in phase 1.
- At most one enabled 基本 rule per account; MOZE data has at most one today.
