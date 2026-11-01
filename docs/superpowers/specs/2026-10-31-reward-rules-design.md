# Reward rules (回饋規則) — design

Rev 2, 2026-10-31 (rev 1 `de737e3f`; rev 2 folds the builder check and the owner ruling 「MOZE 之後不會動了」: the backup
import stops writing rules, so the `locally_edited` fence, tombstones and a reset action are gone and phase 1 needs no
migration; basic-rule uniqueness is by overlapping 期間; `rewards_total` is per currency; `share_cap_with` is the only
way to set a shared cap; the overview surfaces enabled-but-expired rules; rollout extends still-valid rules first).
Owner-approved direction (2026-10-31): reuse the existing `reward_rule` model as it stands, open it up for creation and
editing through the API (owner and the Hermes agent) and the SPA, and leave the posting engine and the agent-side
guessing to later phases. This document fully specifies **phase 1**; phases 2 and 3 are stated as direction so phase 1
does not paint them into a corner.

## 1. Intent

HomeHub is the primary ledger. Card rewards (紅利回饋, points, miles) are a real part of the owner's card economics
and the owner used to manage them in MOZE: every card had rules, each purchase picked the rule(s) it earned under,
and MOZE posted the reward on the card's schedule. The MOZE backup import carried all of that into HomeHub —
the `reward_rule` table (95 rules on 18 cards), the purchase→rule links (2878 rows of `entry_reward_rule`) and
2379 posted reward rows linked to their rule — and the entry form already offers a card's rules as chips with a
預估回饋 line. Three things are missing, in the order the owner wants them:

1. **Rules cannot be created or edited.** The only writer has been the MOZE backup import, and MOZE is no longer
   maintained (owner, 2026-10-31). Six open credit cards have no rules at all; rules for cards whose bank changed
   terms cannot be updated; 31 of the 49 enabled rules are already past their `ends_on`, so the form shows no chip
   for them and the API refuses them (`check_rules`), leaving 18 rules that actually work today. A new card cannot
   be set up the way the owner described: 帳單週期, 繳款日, 自動扣繳 (all exist on the account) **and** 回饋有哪些.
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
  `write` scope so the `hermes` token can use it. HomeHub is the only writer from now on.
- A 回饋規則 block on the account settings page: the card's rules as rows, an editor sheet per rule, add / edit /
  啟用 / 停用 / 刪除, reordering.
- A cross-account 回饋規則總覽 page: every rule, grouped by card, with its state (啟用中 / 已停用 / 已到期 / 尚未開始)
  and what it has actually produced (linked purchases, posted reward rows and their totals), with the
  enabled-but-expired rules called out so they get fixed.
- The MOZE backup import stops creating, updating or deleting rules; it keeps resolving a MOZE reward record's rule
  by `moze_id` for the purchase→rule links.
- The Hermes `homehub-api` skill learns the endpoints and the recording habit: look the card's rules up, attach the
  likely in-date one(s) with `reward_rule_ids`, say which it picked, remember corrections.

Non-goals (later phases, their own design rounds)
- **Phase 2 — posting engine.** Turning linked purchases into reward rows (預估 at save, 已入帳 on the posting date,
  bank figure wins when the statement arrives, 未獲得 when it does not), the project inheritance and PUT propagation
  of issue #262, and the change to `create_system_entry` for statement reward lines. §9 states the direction.
- **Phase 3 — guessing.** Merchant → rule memory lives in the Hermes skill / memory, not in HomeHub.
- MCC codes, bank T&C scraping, any automatic rule discovery.
- Points-account reconciliation (小樹點 → 哩程 redemptions). Existing 餘額調整 covers it; §9.3 notes the one UI nudge
  that may come with phase 2.
- Changing the entry form's chips or estimate maths. They already do what the owner described.
- Bulk edits (extend every expired rule at once). The overview lists them; each is one PUT, or Hermes does the
  batch from the owner's words.

Anti-goals
- A backup import that writes rules again by accident (a test pins the rule pass as read-only).
- Deleting a rule that purchases or reward rows reference (FKs are RESTRICT; the API must say 409, not 500).
- Rules the SPA can create but Hermes cannot, or vice versa: one API, one validation.
- Owner financial data in the repo, tests, commit messages or Multica briefs. Fixtures are fictional; this document
  carries counts only.

## 3. Constraints

- **Model reuse, no migration.** `reward_rule` (`app/models/ledger.py`) and `RewardRuleOut` (`app/schemas/ledger.py`)
  stay the contract: `method` percent|fixed, `window` statement_cycle (the only value the enum has), `posting`
  after_window|after_transaction|manual, `delay_days`, `post_month_offset` 0–2, `post_day` 1–31, `txn_rounding` /
  `total_rounding` keep|round|floor|ceil, `total_cap`, `shared_cap_id` (UUID shared by the rules that share one cap),
  `is_basic`, `reward_account_id`, `reward_project_id`, `starts_on`, `ends_on`, `is_enabled`, `description`,
  `sort_order`, `moze_id`. No column is added or renamed; alembic head stays `c7a2e9f4b1d8`.
- **Scopes.** Rule routes sit under the `/accounts` router (`method_scope()` → GET needs `read`, writes need `write`),
  so the `hermes` label (read, propose, write; no admin) can write them. Settings routes need `admin`; rule CRUD is
  deliberately not a settings route.
- **Backup import.** `POST /imports/moze-backup` is manual (no timer). Its rule pass
  (`moze_backup_import_service.py` ~L425–500) today upserts by `moze_id` and deletes "gone" unreferenced MOZE rules.
  Phase 1 makes it read-only (§5.2).
- **SPA.** Angular 21, standalone components, signals; phone-first with the wide layout; `anyComponentStyle` budget
  16 kB warn / 20 kB error; routes are allow-listed in Caddy (`@hub_spa` regexp) — a new route 404s until the
  allowlist is extended (memory `homehub-caddy-spa-allowlist`).
- **Tests.** Backend `tests/integration` (fixtures; `test_backup_settings_import.py` covers the rule pass), frontend
  Vitest specs beside the components.
- **Demo stack.** Multica reviewers only see 127.0.0.1:18080 with fictional data; nothing in this design needs
  owner data to review.

## 4. Shape

```
phase 1 (this design)
  API      POST/PUT/DELETE reward rules · reorder · GET /reward-rules overview with stats · import rule pass read-only
  SPA      account settings › 回饋規則 block + editor sheet · 回饋規則總覽 page · (entry form unchanged)
  Hermes   skill text: endpoints, "attach the likely in-date rule" habit, remember corrections, 餘額調整 for points
phase 2   posting engine (預估 → 已入帳 → bank wins / 未獲得), #262 inheritance + PUT propagation, statement matching
phase 3   Hermes merchant→rule memory, MCC-miss memory
```

## 5. Backend

### 5.1 Semantics of a rule (what the UI and the skill say about each field)

| field | meaning | UI label / rendering | validation (422 on failure unless noted) |
|---|---|---|---|
| `name` | what the bank calls it | 名稱 | 1–128 chars, trimmed; duplicates allowed (MOZE has them) |
| `method` + `rate` / `fixed_amount` | % of the purchase, or a fixed amount per purchase | 方式: 比例 % / 固定金額 | percent: `rate` required, 0 < rate ≤ 100, 4 dp, `fixed_amount` null; fixed: `fixed_amount` required > 0, `rate` null |
| `window` | the accumulation window for caps and 總額 rounding | not shown (only 帳單週期 exists) | must be `statement_cycle` |
| `posting` | when the reward lands | 入帳: 帳單結帳後 / 交易後 / 手動 | enum |
| `post_month_offset`, `post_day` | after_window: posts on `post_day` of the month `offset` months after the window's closing month | 結帳後第 N 月 D 日 (shown only for 帳單結帳後) | 0–2 and 1–31 (DB checks exist); reset to 0 / 1 for the other postings |
| `delay_days` | after_transaction: days after the purchase | 交易後 N 天 (shown only for 交易後) | 0–365; reset to 0 otherwise |
| `txn_rounding`, `total_rounding` | per-purchase and per-window rounding | 單筆 / 總額 四捨五入·無條件捨去·無條件進位·不取整 | enum |
| `total_cap` | per-window cap in the reward account's unit | 每期上限 | null or > 0 |
| `shared_cap_id` | rules sharing one cap | 與…共用上限 (picker of the account's other rules) | read-only in the output; set through `share_cap_with` (§5.3); members are always rules of the same account (true of all MOZE data) |
| `is_basic` | the always-on base rate of the card | 基本回饋 toggle | no two **enabled** basic rules of one account may have overlapping 期間 (a null bound is open-ended); checked only when the saved row ends up enabled and basic; violation is 409 `basic_rule_overlap` naming the other rule id. A bank changing terms (old ends 06/30, new starts 07/01) is therefore two enabled rules |
| `reward_account_id` | where the reward lands (points / miles account, or the card itself) | 入帳帳戶 | must exist, not archived; null means the card itself (the import stored the card id explicitly — both are accepted and returned as stored) |
| `reward_project_id` | project the posted reward carries | 專案 | must exist, not archived; phase 2 inheritance (#262) wins when null |
| `starts_on`, `ends_on` | validity; the entry form offers a rule only if enabled and the purchase date is inside, and `check_rules` refuses a rule outside its dates | 期間 | `starts_on ≤ ends_on` when both set |
| `is_enabled` | hard switch | 啟用 | — |
| `description` | free text (bank wording, exclusions) | 說明 | ≤ 2000 chars |
| `sort_order` | chip order on the form and row order in settings | drag / 上移 下移 | integer |
| `moze_id` | import key | read-only badge 來自 MOZE | never writable |

A rule's **state** for display: 已停用 (`is_enabled` false) · 已到期 (`ends_on` < today) · 尚未開始 (`starts_on` > today)
· 啟用中 (otherwise). The form already hides 已到期 / 尚未開始 rules (`rulesForDate`); the overview must make them
visible (§6.2).

### 5.2 Backup import: the rule pass becomes read-only

`_import_reward_rules` no longer creates, updates or deletes `reward_rule` rows. It resolves each MOZE reward-rule
record to the existing row by `moze_id` (into `result.rules`, so `_link_rewards_and_attachments` keeps linking
purchases and posted rewards) and counts records it cannot resolve as `rules_unknown` in the import report; a reward
record pointing at an unknown rule gets the existing `reward_rule_missing` review flag. The "gone" sweep is removed.
The report's existing rule counters keep their names where they still mean something (`rules` = resolved); the
settings-import tests change accordingly, and one test asserts that an import with a changed rule record leaves the
HomeHub row untouched.

### 5.3 Endpoints (all under the `/accounts` router's scope dependency)

| route | purpose | notes |
|---|---|---|
| `GET /accounts/{id}/reward-rules` | exists | unchanged; keeps returning every rule (the form filters by date itself) |
| `POST /accounts/{id}/reward-rules` → 201 `RewardRuleOut` | create | body `RewardRuleIn` (every writable field of 5.1; `sort_order` optional → appended last); `share_cap_with` as below; 409 `basic_rule_overlap` |
| `PUT /accounts/{id}/reward-rules/{rule_id}` → `RewardRuleOut` | full update | same body; the whole row is written (the SPA sends everything it has, as `saveProject` does); `moze_id` untouched |
| `PUT /accounts/{id}/reward-rules/order` → `list[RewardRuleOut]` | reorder | body `{ "ids": [...] }` = every rule of the account exactly once; 422 otherwise; registered before the `{rule_id}` route |
| `DELETE /accounts/{id}/reward-rules/{rule_id}` → 204 | delete | 409 `rule_referenced` with `{"entries": n, "rewards": m}` when `entry_reward_rule` or `ledger_entry.reward_rule_id` points at it — the SPA then offers 停用 instead; a shared cap left with one member keeps its UUID (harmless) |
| `GET /reward-rules` → `RewardRuleOverviewOut` | the overview | query `account_id` (repeatable), `state` = active\|disabled\|expired\|upcoming\|all (default all), `include_archived_accounts=false`; returns `accounts: [{account_id, account_name, is_credit, is_archived, rules: [RewardRuleOut + state + stats]}]` sorted by account `sort_order`, name, plus top-level `counts: {active, disabled, expired, upcoming, enabled_expired}` so the SPA can render the callout without a second request |

`share_cap_with` (POST and PUT body; the only way to touch `shared_cap_id`):

| value | effect |
|---|---|
| omitted / `null` | leave the cap membership as it is (new rule: none) |
| `<rule id>` of another rule of the **same** account | join that rule's cap; when that rule has none, a fresh UUID is set on both |
| `"none"` | leave the cap (`shared_cap_id` → null) |

Another account's rule id → 422.

`stats` per rule (counts and sums; no owner-specific semantics):

```
linked_entries        count(entry_reward_rule.rule_id = id)
linked_entries_12m    same, entry_date in the last 12 months
rewards_count         count(ledger_entry.kind = 'reward' and reward_rule_id = id)
rewards_total         [{currency, amount}] summing those rows per ledger_entry.currency (a rule's rows may sit on
                      a points account and a TWD card over its life); empty list when none
rewards_last_date     max(entry_date) of those rows, null when none
```

One grouped query per statistic family (no per-rule round trips); the overview on today's data is 95 rules and
must stay well under the 300 ms the reports hold themselves to.

Error style follows the ledger routes: `service_errors()` → 404 `account not found` / `rule not found`, 409 with a
`code`, 422 pydantic. A rule's `account_id` never changes (move = delete + create).

### 5.4 Service

`app/services/reward_rule_service.py`: `create`, `update`, `reorder`, `delete`, `overview`; `ledger_service.rule_out`
and `_rules_by_account` stay the readers. Validation lives in the schema (`RewardRuleIn` with a model validator for
the per-posting field resets and the method pairing) plus the service for cross-row checks (basic overlap, shared cap
same-account, referenced delete).

## 6. SPA

### 6.1 Account settings › 回饋規則

Placement: a block after 自動扣繳 / 額度共用 on `accounts/:id/settings`, for every account (credit or not — Line Bank
style debit cards carry rules too). Hidden on `accounts/new` until the account exists (the block needs an id); the
page tells the owner 儲存帳戶後即可新增回饋規則.

Rows (phone: stacked card; wide: table): `ruleLabel` (the same text the chip shows: 名稱 (3%) / 名稱 ($50)), a one-line
入帳 summary (帳單結帳後第 1 月 15 日 / 交易後 3 天 / 手動), 期間 (yyyy/MM/dd – yyyy/MM/dd, 無期限), state badge, 基本 badge,
來自 MOZE badge, 啟用 switch (`app-switch`, saves immediately like the project 不計入報表 switch), 編輯, 上移 / 下移
(wide) or a reorder mode (phone). 新增規則 at the end. Rows in 已到期 state show the badge in the warning colour.

Editor: a sheet (the `sheet.scss` pattern the fee / fx sheets use) with four groups — 基本 (名稱, 方式, 比例/金額, 基本回饋,
說明), 入帳 (入帳 select; the dependent fields appear for the chosen posting; 入帳帳戶 picker of open accounts with the
card preselected; 專案 select), 上限 (每期上限, 單筆取整, 總額取整, 與…共用上限 → `share_cap_with`), 期間 (起 / 迄, 啟用).
Save = POST or PUT, then the account's rules are re-read so the block and the form's chips agree. 刪除 asks once; on
409 `rule_referenced` the sheet says 此規則已有 N 筆交易 / M 筆回饋引用，改為停用？ with a 停用 button; on 409
`basic_rule_overlap` it names the clashing rule.

Dirty-form rules as the rest of the page (`dirty-form.service`): closing the sheet with unsaved input asks first.

### 6.2 回饋規則總覽

Route `accounting/reward-rules` (phone + wide; added to `app.routes.ts` **and** the Caddy allowlist). Entry points:
a 回饋規則 link in 設定 (accounting-settings page, beside 分類 / 專案) and 全部卡片 in the block header of 6.1.

Content: when `counts.enabled_expired > 0`, a fixed callout at the top — 「N 條規則啟用中但已到期，記帳時不會出現」 — that
switches the filter to 已到期. Filter chips 啟用中 / 已到期 / 尚未開始 / 已停用 / 全部 (default 啟用中), then one card per
account (archived accounts hidden unless 顯示封存) with its rules as rows: label, 入帳 summary, 期間, state, and the
stats: 綁定 N 筆 (近 12 月 M), 已入帳 K 筆 · 合計 X <currency> (one line per currency), 最近 yyyy/MM/dd. A rule row links to
the account's settings page with the rule's editor open (`?rule=<id>`). Cards without rules show 尚無規則 → 新增 (to
the account settings). Empty overall: 尚無回饋規則.

### 6.3 Models / service

New `RewardRuleInput` (with `share_cap_with`), `RewardRuleOverview`; `AccountingService` gains `createRewardRule`,
`updateRewardRule`, `reorderRewardRules`, `deleteRewardRule`, `getRewardRuleOverview`, all through `bump` so dependent
views refresh. `RewardRule` is unchanged. The entry form keeps reading rules from `AccountDetail.reward_rules`.

## 7. Hermes skill (`~/.hermes/skills/productivity/homehub-api/`, outside the repo)

`references/accounting-api.md` gains a section 回饋規則 with the routes of §5.3 and these habits; `SKILL.md`'s
"Write Operations" list adds reward rules. Text is drafted in the plan and applied at rollout (the skill is not in
git; the PR body quotes it):

1. **Recording a card purchase**: GET the account's `reward-rules` once per session; keep only rules that are enabled
   **and** whose 期間 contains the purchase date (the API refuses the rest with 422). Pick the rule(s) the merchant /
   category most plausibly earns under (the 基本 rule when nothing better fits), send them as `reward_rule_ids`, and
   tell the owner in one line which chip was attached (名稱 (3%)). If the owner corrects it, PUT the entry with the
   corrected ids and write the pair 「商家/類別 → 規則」 into memory. Unknown card or no plausible rule: attach nothing
   and say so.
2. **Defining or fixing a card's rewards** when the owner describes them in chat: translate to `RewardRuleIn` rows
   (or PUTs that extend `ends_on`), show the table back, then write one by one; report ids.
3. **Points accounts**: when the owner states a current balance (「小樹點現在 12,345」), `POST /balance-adjustments`
   with `target_balance` and description 核對; never guess a balance.
4. Never disable or delete a rule without the owner asking; prefer 停用 to 刪除.

## 8. Rollout

1. API: deploy from the release checkout as usual (no migration); `GET /reward-rules` smoke with the hermes token
   (counts only).
2. SPA release per the standing recipe; **extend the Caddy `@hub_spa` regexp** with `reward-rules` before the live
   probe; probe `/hub/accounting/reward-rules`.
3. Skill text applied; a one-line test: Hermes lists a card's rules.
4. Owner data pass, in this order: (a) from the overview's 已到期 list, extend or clear `ends_on` on the rules still in
   force and 停用 the dead ones (31 today); (b) define the six rule-less cards. Either by hand or by Hermes from the
   owner's description. No scripted backfill.
5. Issues to open at closeout: phase 2 design (engine), phase 3 (guess memory), and anything the review parks.

Rollback: SPA → previous publisher record; API → previous release checkout. No schema change to undo.

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
- Whether stats use `entry_date` or `posted_date` for the 12-month window (pick `entry_date`, the reports'
  convention, unless a reason appears).
- Whether the import report keeps a `rules_deleted` counter at 0 or drops it (drop it; the backup README notes the
  change).
- Test fixtures (fictional cards with two or three rules each, one shared cap, one basic rule, one expired, one
  upcoming).

## 11. Decisions made in this document (the owner can veto)

- Rule CRUD under `/accounts/{id}/reward-rules` with the `write` scope, so Hermes can write without admin.
- The backup import's rule pass is read-only from now on (owner: MOZE no longer maintained); `moze_id` stays as a
  provenance badge. No `locally_edited` flag, no tombstones, no reset action, no migration.
- Delete is refused (409) when referenced; 停用 is the normal path. No soft-delete column.
- Basic-rule uniqueness = no overlapping 期間 among enabled basic rules of one account, checked only when the saved row
  is enabled and basic (today: 2 basic rules on 2 accounts, no conflict).
- `share_cap_with` is the only writer of `shared_cap_id`; omitted keeps, `"none"` leaves, a same-account rule id joins.
- `rewards_total` is a per-currency list, not one number.
- Overview is its own page (`accounting/reward-rules`), linked from 設定 and from each account's block, rather than a
  section of the settings page: 95 rules with stats is too much for that page. It opens on 啟用中 with a fixed callout
  for enabled-but-expired rules.
- `window` stays a single-value enum; no new windows in phase 1.
- Stats are all-time plus a 12-month count; no per-month history in phase 1.
- Reorder is not fenced from anything (nothing else writes `sort_order` any more).
