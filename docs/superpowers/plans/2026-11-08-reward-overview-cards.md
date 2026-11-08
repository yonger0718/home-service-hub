# Reward overview cards (polish-reward-and-account-settings PR B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/accounting/reward-rules` becomes the card grid of the spec: 記帳設定 is the current nav item, a summary header with counted state chips, one card per account with its window line, period total and one row per rule (rate chip, state tag, posting, cap bar), rules-less cards as chips, and the expired warning banner — all in the reward's unit.

**Architecture:** SPA only (the API shipped in #342: `usage`, `icon`, `logo`, `logo_hash`, `closing_day` on each overview account; `reward_account_id` / `reward_account_name` / `reward_is_money` on each usage row). A reusable `CapBar` goes to `components/ui/cap-bar` (PR C reuses it in account settings). Pure text helpers go to `reward-rule-text.ts`; the overview component builds view models from the one `GET /reward-rules` response.

**Depends on:** PR A2 (`docs/superpowers/plans/2026-11-08-reward-cash-equivalent.md`, branch `feat/reward-cash-equivalent`) for the amended spec copy; no API dependency (the field set is unchanged).

**Tech Stack:** Angular 21 standalone components with signals, vitest via `ng test`, SCSS tokens from `src/styles.scss`.

**Spec:** `openspec/changes/polish-reward-and-account-settings/` — `design.md` D1a, D2, D3; `specs/frontend-accounting-ledger/spec.md` requirements "Reward rules overview page", "Reward overview cards", "Reward overview units and windows", "Reward overview rule lines", "Reward overview empty and expired states", "Reward cap bar"; `tasks.md` 2.1 (CapBar half), 2.3 (bar tests), 3.1–3.5. Artboard: `design_handoff_v3/design_refs/RewardRulesDesktop.dc.html`.

## Global Constraints

- **Out of scope here:** `RewardStateTag` (PR D, another session, `components/ui/reward-state-tag/`), entry detail, account-settings layout (PR C). Steppers / pickers (task 2.2) move to PR C, their only user (ruling, noted in tasks.md).
- **Units (D1a):** a usage row's unit comes only from its own `reward_is_money` / `reward_account_name`; never resolve units through `accounts`. Money is formatted with `formatMoney(v, 'TWD')`; points as `n0(v) + ' ' + reward_account_name` (e.g. `120 測試里程`).
- **Copy (exact):**
  - chips `全部 N` `生效中 N` `已停用 N` `已過期 N` `未開始 N`, checkbox `含已封存帳戶`, back link `‹ 記帳設定`, title `回饋規則`;
  - summary `N 張卡 · M 條規則 · 本期回饋合計 $X（含預估）`, then ` ＋ N 種點數` where N = distinct `reward_account_id`s among the shown cards' non-money usage rows (omitted when 0; owner ruling 2026-11-08, spec amended by PR A2);
  - window line `結帳日 25 號 · 本期 10/26–11/25`; calendar `每月 1 日起算 · 本月 11/1–11/30`; mixed windows: `結帳日 25 號` only and each rule row gets `本期 11/1–11/30`;
  - card total label `本期回饋（含預估）`; footer `近 12 個月 N 筆 · 已入帳 M 筆` and link `＋ 新增規則`;
  - bar `$193 / $200 · 快到上限`, `$64 / $500 · 剩 $436`; no cap `無上限 · 本期 $X 已入帳 + $Y 預估`; aria `本期已入帳 $X，預估 $Y，上限 $Z`;
  - inactive lines `停用中，不估算回饋` (disabled), `已結束，不再估算` (expired), `2027/1/1 開始` (upcoming: `slashDate(starts_on)` + ` 開始`);
  - banner `N 條規則已過期但仍啟用，之後的消費不會再估算回饋` with button `只看已過期`;
  - bare chips row label `沒有規則的卡片`.
- **State labels:** `RULE_STATE_LABELS` becomes `生效中 / 已停用 / 已過期 / 未開始` (the spec's words; account settings shows the same map).
- **Dates in copy:** `M/D` without zero padding.
- **Warning ink (D2 "until the banner token lands"):** near-cap bar and text use the existing callout ink `var(--tone-neg-pill-ink, var(--c-red-pill-ink))`; the banner keeps `--app-state-warning-bg`. No new global tokens.
- **Worktree rules:** never run `npm run build`, `npm run config`, `set-env.js` or `npm start` in the worktree; tests: `cd frontend && npx ng test --watch=false --include='<glob>'`; the worktree needs `frontend/node_modules` symlinked to `~/workspace/home-hub/frontend/node_modules` and a placeholder `frontend/src/environments/environment.ts` (git-ignored; shape of `environment.prod.ts`, dummy values).
- Fixtures fictional. Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; never commit `.superpowers/`.

## Review Focus

1. A points rule whose usage row says `reward_is_money: false` while the account list is filtered by `?account_id=` → bar and card total still in points (Task 4 `points unit survives a filter`).
2. A card with both money and points rules → card total shows both parts; header money total excludes points and folds them into `＋ 1 種點數` (Task 4 `mixed units`).
3. A shared-cap group: two rule rows map to one usage row via `rule_ids` → both show the same bar; a disabled member of that group shows no bar (Task 4 `shared cap and disabled member`).
4. Rule name already ending in its rate (`玩數位 3%`) → title `玩數位`, chip `3%`, never `3% (3%)` (Task 1 `ruleTitle`).
5. `cap` present but `used + estimated` above it → bar widths clamp to 100 % total, text still `· 快到上限` (Task 2 `over the cap`).

---

### Task 1: Models and text helpers

**Files:**
- Modify: `frontend/src/app/models/accounting.model.ts` (`RewardUsage` ~62, `RewardRuleOverviewAccount` ~161)
- Modify: `frontend/src/app/components/accounting/reward-rule-text.ts`
- Test: `frontend/src/app/components/accounting/reward-rule-text.spec.ts`; update label assertions in `account-settings/account-settings.spec.ts` (~207, ~781, ~848, ~1096–1102)

**Interfaces:**
- Produces:
  - `RewardUsage` + `reward_account_id: number; reward_account_name: string; reward_is_money: boolean`.
  - `RewardRuleOverviewAccount` + `icon: string | null; logo: string | null; logo_hash: string | null; closing_day: number | null; usage: RewardUsage[]`.
  - `export type RewardUnit = { money: true } | { money: false; name: string }`.
  - `export function usageUnit(usage: RewardUsage): RewardUnit`.
  - `export function rewardFigure(value: string | number, unit: RewardUnit): string` — money `formatMoney(v, 'TWD')`, points `${n0(v)} ${name}`.
  - `export function rateChip(rule): string` — `methodSummary(rule)`.
  - `export function ruleTitle(rule): string` — `rule.name` with a trailing ` ${rateChip(rule)}` (or `(${rateChip})`) removed, trimmed.
  - `export function monthDay(iso: string): string` — `2026-10-26` → `10/26`.
  - `export function windowText(usage: RewardUsage): string` — `10/26–11/25`.
  - `RULE_STATE_LABELS` = `{ active: '生效中', disabled: '已停用', expired: '已過期', upcoming: '未開始' }`.

- [ ] **Step 1: Failing tests** in `reward-rule-text.spec.ts`:

```ts
describe('overview text helpers', () => {
  const usage = (over: Partial<RewardUsage> = {}): RewardUsage => ({
    rule_ids: [1], shared_cap_id: null, name: 'A', window_start: '2026-10-26', window_end: '2026-11-25',
    used: '236', estimated: '64', cap: '500', remaining: '200',
    reward_account_id: 9, reward_account_name: '測試卡', reward_is_money: true, ...over,
  });
  it('usageUnit / rewardFigure', () => {
    expect(rewardFigure('1236', usageUnit(usage()))).toBe('$1,236');
    expect(rewardFigure('120', usageUnit(usage({ reward_is_money: false, reward_account_name: '測試里程' })))).toBe('120 測試里程');
  });
  it('ruleTitle strips a trailing rate', () => {
    const rule = { name: '玩數位 3%', method: 'percent', rate: '3', fixed_amount: null, unit_spend: null } as RewardRule;
    expect([ruleTitle(rule), rateChip(rule)]).toEqual(['玩數位', '3%']);
    expect(ruleTitle({ ...rule, name: '全聯・家樂福加碼' })).toBe('全聯・家樂福加碼');
    expect(ruleTitle({ ...rule, name: '海外 (3%)' })).toBe('海外');
  });
  it('monthDay / windowText', () => {
    expect(monthDay('2026-11-06')).toBe('11/6');
    expect(windowText(usage())).toBe('10/26–11/25');
  });
  it('state labels use the spec words', () => {
    expect(RULE_STATE_LABELS).toEqual({ active: '生效中', disabled: '已停用', expired: '已過期', upcoming: '未開始' });
  });
});
```

- [ ] **Step 2: Run** `npx ng test --watch=false --include='src/app/components/accounting/reward-rule-text.spec.ts'` — FAIL (missing exports).
- [ ] **Step 3: Implement** the Interfaces above. Update the account-settings spec assertions from `啟用中 / 已到期 / 尚未開始` to `生效中 / 已過期 / 未開始` (badge behaviour unchanged).
- [ ] **Step 4: Run** the two spec files (`reward-rule-text.spec.ts`, `account-settings/account-settings.spec.ts`) — PASS.
- [ ] **Step 5: Commit** `feat(reward-rules-spa): usage unit and overview text helpers (#323 D1a)`

---

### Task 2: CapBar

**Files:**
- Create: `frontend/src/app/components/ui/cap-bar/cap-bar.ts`, `cap-bar.scss`, `cap-bar.spec.ts`

**Interfaces:**
- Consumes: Task 1 `RewardUnit`, `rewardFigure`.
- Produces: `<app-cap-bar [used] [estimated] [cap] [unit]>` — `CapBarComponent` with inputs `used: string`, `estimated: string`, `cap: string | null`, `unit: RewardUnit` (all required); exported pure `capBarView(used, estimated, cap, unit): { near: boolean; usedPct: number; estPct: number; text: string; aria: string } | { near: false; text: string }` (no-cap form).

Rules: `total = used + estimated`; `near = cap > 0 && total / cap >= 0.9`; `usedPct = min(100, used / cap * 100)`, `estPct = min(100 − usedPct, estimated / cap * 100)`; text `fig(total) / fig(cap) · 快到上限` when near else `· 剩 fig(max(0, cap − total))`; points: the unit name appears once after the cap — `300 / 500 測試里程 · 剩 200`. No cap: `無上限 · 本期 fig(used) 已入帳 + fig(estimated) 預估`, no bar. Markup: `<span role="img" [attr.aria-label]>` with two fills (`.cap-used`, `.cap-est` at 30 % opacity of `--app-primary`, track `--app-surface-soft`), `[class.near]` on the host switching fills and text to the warning ink.

- [ ] **Step 1: Failing tests** (`cap-bar.spec.ts`): `capBarView` at 89 % (`'178', '0', '200'` → near false, text `$178 / $200 · 剩 $22`) and 90 % (`'180', '0', '200'` → near true, `$180 / $200 · 快到上限`); spec scenario (`'180', '13', '200'` → `$193 / $200 · 快到上限`); over the cap (`'190', '30', '200'` → `usedPct + estPct === 100`, near); no cap (`'236', '64', null` → `無上限 · 本期 $236 已入帳 + $64 預估`); points (`'300', '0', '500'`, `{money:false,name:'測試里程'}` → `300 / 500 測試里程 · 剩 200`); aria `本期已入帳 $236，預估 $64，上限 $500`; component render: `.cap-bar.near` present at 90 %, no `[role=img]` without cap.
- [ ] **Step 2: Run** `--include='src/app/components/ui/cap-bar/cap-bar.spec.ts'` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(ui): CapBar — posted + estimated against a cap, in the reward's unit (#323 D2)`

---

### Task 3: 記帳設定 is current on the overview

**Files:**
- Modify: `frontend/src/app/components/shell/navigation.ts` (`NavItem`, `accounting-settings` item, `navItemForUrl`)
- Test: `frontend/src/app/components/shell/navigation.spec.ts`

**Interfaces:** `NavItem.also?: readonly string[]` — extra path prefixes that select the item; `accounting-settings` gets `also: ['/accounting/reward-rules']`. `navItemForUrl` checks exact items, then `also` prefixes (`url === p || url.startsWith(p + '/')`), then the existing longest-prefix rule.

- [ ] **Step 1: Failing test:** `navItemForUrl('/accounting/reward-rules?state=all').id === 'accounting-settings'`; `navItemForUrl('/accounting').id === 'accounting-timeline'` still; `navItemForUrl('/accounting/entries/5').id` unchanged from today's value.
- [ ] **Step 2–4:** run `--include='src/app/components/shell/navigation.spec.ts'` FAIL → implement → PASS.
- [ ] **Step 5: Commit** `fix(shell): the reward rules overview marks 記帳設定 current (#323 3.1)`

---

### Task 4: The overview page

**Files:**
- Modify: `frontend/src/app/components/accounting/reward-rules/reward-rules.ts`, `.html`, `.scss`
- Rewrite: `frontend/src/app/components/accounting/reward-rules/reward-rules.spec.ts` (keep its HTTP-testing setup; replace assertions on the old list layout)

**Interfaces:**
- Consumes: Task 1 helpers and types, Task 2 `CapBarComponent`, `AccountIconComponent` (`[logo] [accountId] [logoHash] [icon] fallback="card" size="big"`).
- Produces (component, for tests): `cards: Signal<OverviewCard[]>`, `bare: Signal<RewardRuleOverviewAccount[]>`, `headline: Signal<string>`, `chipCounts: Signal<Record<RewardRuleFilter, number>>`.

Behaviour (spec + D3; decisions marked *ruling*):
- **Default filter `all`** (*ruling*: the artboard's initial state and the only filter under which the bare-chip row exists; `?state=` still selects). Chips in order 全部, 生效中, 已停用, 已過期, 未開始 with counts from `counts` (全部 = sum of the four).
- Header: `‹ 記帳設定` link (`leavePage` to `/accounting/settings`, at every width — replaces the phone-only ‹ button), `<h1>回饋規則</h1>`, headline. `N 張卡` = cards shown; `M 條規則` = `counts` total; money total = Σ `used + estimated` over money usage rows of the shown cards; then ` ＋ N 種點數` (distinct non-money `reward_account_id`s). Units come only from `reward_is_money`, which PR A2 widens to `cash_equivalent` accounts — no SPA logic for it here.
- Banner when `counts.enabled_expired > 0 && filter !== 'expired'`: `role="status"`, copy above, `只看已過期` → `setFilter('expired')`.
- Grid: `display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 520px), 1fr))`.
- A card per account with ≥ 1 listed rule, in response order: icon, name (+ `（已封存）`), window line (Global Constraints; built from `closing_day` and the usage rows — calendar when every usage row's rule is `calendar_month`, mixed when the rows' `window_start`s differ; no usage rows → `結帳日 N 號` or nothing), right side the card total per unit (money part, then each points part; `$0` when there are no usage rows) over `本期回饋（含預估）`.
- Rule row (`<a>` to settings `?rule=<id>`, as today): `ruleTitle`, rate chip `rateChip`, state tag only when `state !== 'active'` or `is_basic` (`基本`), posting summary right-aligned; then for an active rule with a usage row (`usage.find(u => u.rule_ids.includes(rule.id))`): `<app-cap-bar>` with that row's figures and `usageUnit` (cap null → CapBar's no-cap line); active rule without a usage row: nothing; inactive: the muted line (Global Constraints) and the row at `opacity: .7`; mixed windows: the row's own `本期 M/D–M/D` under it.
- Footer: `近 12 個月 N 筆 · 已入帳 M 筆` (*ruling*: Σ `stats.linked_entries_12m` and Σ `stats.rewards_count` over the listed rules — the artboard's `回饋 $X` is dropped because `stats.rewards_total` is all-time and in the reward account's currency, so it is neither 12-month nor unit-safe) and `＋ 新增規則` → settings `?rule=new`.
- Bare chips (filter `all` only): accounts with `is_credit && rules.length === 0`, one dashed row labelled `沒有規則的卡片`, each chip `<a>` to settings `?rule=new` with `name ＋`.
- Loading / error: keep `aria-busy` and the `role="alert"` error line.

- [ ] **Step 1: Failing tests** (one `it` each; fixtures via the existing HTTP mock, fictional cards):
  - `requests GET /reward-rules once with state=all by default` (one request; chips read `全部 5`, `生效中 3`, …);
  - `card header`: icon present, window line `結帳日 25 號 · 本期 10/26–11/25`, total `$300`;
  - `calendar card`: `每月 1 日起算 · 本月 11/1–11/30`;
  - `mixed windows`: header `結帳日 25 號`, each row its own `本期 …`;
  - `rule row`: title `全聯・家樂福加碼` + chip `5%`; spec scenario `$193 / $200 · 快到上限` with `.near`;
  - `shared cap and disabled member`: two active rows share one bar; disabled row shows `停用中，不估算回饋`, no bar, `opacity`;
  - `points unit survives a filter` (`?account_id=1`): bar `300 / 500 測試里程 · 剩 200`, card total `300 測試里程`;
  - `mixed units`: card total `$64 ＋ 300 測試里程`, headline money total excludes points and ends with `＋ 1 種點數`; two points accounts → `＋ 2 種點數`;
  - `bare chips`: only under 全部, only credit accounts with no rules, link `?rule=new`;
  - `expired banner`: shown with count, hidden under `state=expired`, button sets `state=expired`;
  - `footer`: `近 12 個月 N 筆 · 已入帳 M 筆`, `＋ 新增規則` → `?rule=new`;
  - `back link` `‹ 記帳設定` at desktop width.
- [ ] **Step 2: Run** `--include='src/app/components/accounting/reward-rules/reward-rules.spec.ts'` — FAIL.
- [ ] **Step 3: Implement** (component signals `cards`, `bare`, `headline`, `chipCounts`; template per the artboard; SCSS from tokens, light and dark via existing variables only).
- [ ] **Step 4: Run** — PASS; also `src/app/components/shell/**/*.spec.ts` — PASS.
- [ ] **Step 5: Commit** `feat(reward-rules-spa): card grid with cap bars, counts and units (#323 D3)`

---

### Task 5: `?rule=new` opens the new-rule sheet

**Files:**
- Modify: `frontend/src/app/components/accounting/account-settings/account-settings.ts` (`?rule=` handling ~432–458)
- Test: `account-settings/account-settings.spec.ts`

**Interfaces:** `?rule=new` → once the account's detail is loaded, `openRule(null)` (the existing create path), then the param is cleared like a numeric id. A numeric id keeps today's behaviour.

- [ ] **Step 1: Failing test** `?rule=new opens an empty rule sheet once and clears the param`.
- [ ] **Step 2–4:** run account-settings spec FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat(account-settings-spa): ?rule=new opens the new-rule sheet (#323 D3 chips)`

---

### Task 6: Spec bookkeeping and full SPA check

- [ ] **Step 1:** In `openspec/changes/polish-reward-and-account-settings/tasks.md`: tick 3.1–3.5; under 2.1 note `CapBar: PR B; RewardStateTag: PR D`; under 2.2 note `moved to PR C (its only user)`; tick 2.3's bar part by adding `(bar tests: PR B)`. `openspec validate polish-reward-and-account-settings --strict` passes.
- [ ] **Step 2:** Full SPA suite `cd frontend && npx ng test --watch=false` — all pass; record the count.
- [ ] **Step 3: Commit** `docs(openspec): polish-reward-and-account-settings — PR B tasks`

## After merge (outside the repo)

SPA release per the latest kit (`~/reports/home-hub-reportsB2-release-20261108T091033Z/RELEASE.md` pins, PORTFOLIO_SPA_TOKEN required, owner runs the approve step) — coordinate with the other session, which also publishes SPA releases. Screens at 1440 / 820 / 390 light + dark against the artboard (task 6.1) before the release.
