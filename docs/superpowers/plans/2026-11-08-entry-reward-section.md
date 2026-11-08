# Entry Reward Section (#323 PR D) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the entry detail's 紅利回饋 and 預估回饋 lists with one 回饋 section of per-accrual cards, each carrying a new `RewardStateTag`.

**Architecture:** A shared `RewardStateTag` lives in `components/ui`. A pure function `rewardCards()` turns an `EntryDetail` into card view models (amount, struck figure, state line, link) using a caller-supplied amount formatter, so every wording rule is unit-tested without TestBed. A small `app-entry-reward-section` component renders the cards and projects the existing #306 reprice card as its last item; `entry-detail` only swaps its two old blocks for the section.

**Tech Stack:** Angular 20 standalone components and signals, Vitest (`npx ng test`), SCSS with the app's `--app-*` tokens.

**Spec:** `openspec/changes/polish-reward-and-account-settings/` — design D1a, D7, D8; tasks 2.1 (tag half), 2.3 (tag tests), 5.1–5.3; `specs/frontend-accounting-ledger/spec.md` requirements "Entry reward section", "Entry reward card", "Entry reward state lines", "Reward state tag".

## Global Constraints

- No API change: every card field derives from today's `RewardAccrualOut` / `EntryDetail` (D7). PR D does not depend on PR A (#342).
- Reward figures use `EntryDetail.rewardAmount` (D1a): money when the reward account is the entry's account, unknown, a credit account, or (owner ruling 2026-11-08, PR A2 / AGENT-462) a `cash_equivalent` account (「1 點 = 1 元」); else `N 帳戶名` (points / miles). Never `$` for non-cash points. The only change to `rewardAmount` is that `cash_equivalent` clause (Task 3).
- `ledger_amount` may be `undefined` (older API); treat as `null`.
- Tag labels exactly 預估 / 已入帳 / 銀行確認 / 未獲得; states never differ by colour alone (D8).
- Line icons only: no emoji in templates (`theme/line-icons.spec.ts`); icons are inline SVG paths, stroked, `aria-hidden="true"`; no `url(#id)` paint references (WebKit under `<base href="/hub/">`).
- Copy is exact as in D7 (Chinese full-width punctuation: 「，」, 「›」).
- Fixtures are fictional (spec examples like 玉山 Pi 卡 / 亞洲萬里通 are fine; no owner card names, rule ids or amounts).
- Worktree rules: never `npm run config`/`npm run build`/`npm start`; tests with `npx ng test --watch=false`, types with `npx tsc -p tsconfig.app.json --noEmit`.

## Review Focus

1. **A posted reward row also listed in `d.rewards`** — the accrual's card must be the only card for it (no duplicate 已入帳 card for the same row). Test in Task 2.
2. **Struck estimate equal in value but different in string** (`'13'` vs `'13.00'`) — no strike when numerically equal. Test in Task 2.
3. **A reward_posted entry whose accruals and rewards are both empty** — the reprice card must stay reachable (its visibility is unchanged), so the section also renders when `review_reason === 'reward_posted'`. Test in Task 3.
4. **Posted accrual whose reward row is dated in the future** — reads 「將於 12/15 入帳到 …」, not 「12/15 已入帳到 …」 (keeps today's `rewardWhen` behaviour). Test in Task 2.
5. **Points reward in the struck estimate and in 「算出 …，本期上限只剩 …」** — both figures in points units. Test in Task 2.

---

## File Structure

| File | Responsibility |
|---|---|
| `frontend/src/app/components/ui/reward-state-tag/reward-state-tag.ts` (+ `.scss`, `.spec.ts`) | Create. The D8 pill. |
| `frontend/src/app/components/accounting/entry-detail/reward-cards.ts` (+ `.spec.ts`) | Create. Pure `rewardCards()` view model. |
| `frontend/src/app/components/accounting/entry-detail/reward-section.ts` (+ `.html`) | Create. Renders the section; projects the reprice card. Inline `styles` like its siblings `einvoice-card.ts` / `reward-reprice-card.ts`. |
| `frontend/src/app/models/accounting.model.ts` | Modify. `LedgerAccount` gains `cash_equivalent?: boolean` (optional: absent from the API until PR A2 deploys). |
| `frontend/src/app/components/accounting/entry-detail/entry-detail.{ts,html,scss}` | Modify. Swap the two old blocks (`entry-detail.html:99-118`) for the section; drop now-unused helpers. |
| `frontend/src/app/components/accounting/entry-detail/entry-detail.spec.ts` | Modify. Rewrite the reward tests at `:103-180`. |

---

### Task 1: RewardStateTag

**Files:**
- Create: `frontend/src/app/components/ui/reward-state-tag/reward-state-tag.ts`, `reward-state-tag.scss`, `reward-state-tag.spec.ts`

**Interfaces:**
- Produces: `RewardStateTagComponent`, selector `app-reward-state-tag`, `state = input.required<RewardAccrualState>()`; `export const REWARD_STATE_LABELS: Record<RewardAccrualState, string>`.

Icon paths (24 viewBox, `fill="none"`, `stroke="currentColor"`, stroke-width 2, round caps/joins), from `design_handoff_v3/design_refs/RewardStatesSpec.dc.html`:

| State | Label | Path |
|---|---|---|
| estimated | 預估 | `M12 7v5l3 2 M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z` |
| posted | 已入帳 | `M5 12l4 4 10-10` |
| bank_confirmed | 銀行確認 | `M4 12l4 4 6-6 M10 16l1 1 9-9` |
| forfeited | 未獲得 | `M18 6L6 18 M6 6l12 12` |

Styles (on `.rst[data-state=…]`, pill shape like `.tag`: inline-flex, gap 4px, `--fs-micro`, bold, radius 999px, padding `0.15rem 0.55rem`, `border: 1.5px solid transparent`, icon 12px):
- estimated: `border-style: dashed; border-color: var(--app-primary); color: var(--app-primary); background: transparent`
- posted: `background: var(--app-primary-soft); color: var(--app-primary)`
- bank_confirmed: `background: var(--app-state-success-bg); color: var(--app-success)`
- forfeited: `background: var(--app-surface-soft); border-color: var(--app-border); color: var(--app-text-muted)`

- [ ] **Step 1: Write the failing tests** (host component pattern as in `ui/tag/tag.spec.ts`)
  - `renders the label and data-state for each state`: for each of the four states, `.rst` text is the label above and `getAttribute('data-state')` is the state.
  - `gives every state its own icon`: the four `svg path` `d` attributes are the four paths above (all distinct); the `svg` has `aria-hidden="true"`, `fill="none"` and `stroke="currentColor"` (a line icon, not a filled shape).
  - `is announced by its label only`: the tag's accessible text (`textContent.trim()`) equals the label (the icon adds no text).
- [ ] **Step 2: Run** `npx ng test --watch=false --include='src/app/components/ui/reward-state-tag/*.spec.ts'` — FAIL (module missing).
- [ ] **Step 3: Implement** the component (template: `<span class="rst" [attr.data-state]="state()"><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path [attr.d]="path()" /></svg>{{ label() }}</span>`, `OnPush`).
- [ ] **Step 4: Run** the same command — PASS.
- [ ] **Step 5: Commit** `feat(spa): RewardStateTag for accrual states (#323)`

---

### Task 2: `rewardCards()` view model

**Files:**
- Create: `frontend/src/app/components/accounting/entry-detail/reward-cards.ts`, `reward-cards.spec.ts`

**Interfaces:**
- Consumes: `EntryDetail`, `RewardAccrual`, `RewardAccrualState`, `LedgerEntry` from `models/accounting.model`; `shortDate` from `../dates`.
- Produces:

```ts
/** EntryDetail.rewardAmount's signature: a figure in the reward's unit (D1a). */
export type RewardAmountText = (value: string, accountId: number, accountName: string, currency: string, sign?: boolean) => string;

export interface RewardCard {
  key: string;                 // `a${accrual.id}` or `r${reward.id}`
  name: string;                // rule_name; for a reward row without accrual: reward.name || '紅利回饋'
  state: RewardAccrualState;
  amount: string;              // formatted; signed (+) unless forfeited
  was: string | null;          // formatted struck estimate beside amount (never for forfeited)
  line: string;                // the state line
  link: number | null;         // reward row id for 「看回饋記錄 ›」 (posted cards only)
}

export function rewardCards(detail: EntryDetail, today: string, text: RewardAmountText): RewardCard[];
```

Rules (D7), with `unit(v, sign)` = `text(v, accrual.reward_account_id, accrual.reward_account_name, detail.currency, sign)`, `row` = `detail.rewards.find(r => r.id === accrual.ledger_entry_id)`, `ledger` = `accrual.ledger_amount ?? null`, numbers compared with `Number()`:

- Order: accruals in the order received (the API sends rule order), then `detail.rewards` rows whose id is no accrual's `ledger_entry_id`, in received order.
- Shown figure (not forfeited): `ledger ?? accrual.amount`, `unit(…, true)`. `was` = `unit(estimate, false)` when `Number(estimate) !== Number(shown)`, else `null`.
- Forfeited figure: `Number(amount) > 0 ? amount : estimate`, `unit(…, false)`; `was = null`.
- Lines:
  - estimated: `Number(amount) < Number(estimate)` → `算出 ${unit(estimate)}，本期上限只剩 ${unit(amount)}`; else `post_on` → `預計 ${shortDate(post_on)} 入帳到 ${reward_account_name}`; else `手動入帳`.
  - posted: date `d` = `row?.posted_date ?? post_on`; `d > today` → `將於 ${shortDate(d)} 入帳到 ${name}`, else `${shortDate(d)} 已入帳到 ${name}` (`name` = `reward_account_name`); `d` null → `已入帳到 ${name}`. `link = accrual.ledger_entry_id`.
  - bank_confirmed: `ledger === null || Number(ledger) === Number(amount)` → `與帳單相符`; else `銀行實際給 ${unit(ledger)}`.
  - forfeited: `ledger_entry_id !== null` → `帳單上沒有這筆回饋`; else `Number(amount) === 0` → `本期上限已用完`; else `回饋記錄已刪除，不會入帳`.
- Reward row without accrual: state `posted`, amount `text(r.amount, r.account_id, r.account_name, r.currency, true)`, `was null`, line as posted with `d = r.posted_date` and `name = r.account_name`, `link = r.id`.

Fake formatter for the tests: `const text: RewardAmountText = (v, id, name, _c, sign) => id === 99 ? \`${sign && Number(v) > 0 ? '+' : ''}${Number(v)} ${name}\` : \`${sign && Number(v) > 0 ? '+' : ''}$${Number(v)}\``; `today = '2026-11-21'`; fixture accrual defaults `{ rule_name: '一般消費 1%', estimate: '13', amount: '13', state: 'estimated', post_on: '2026-12-15', ledger_entry_id: null, ledger_amount: null, reward_account_id: 7, reward_account_name: '玉山 Pi 卡' }`.

- [ ] **Step 1: Write the failing tests** in `reward-cards.spec.ts`:
  - `estimated: expected date and account` → `{ state: 'estimated', amount: '+$13', was: null, line: '預計 12/15 入帳到 玉山 Pi 卡', link: null }`.
  - `estimated without post_on reads 手動入帳` → line `手動入帳`.
  - `estimated cut by the cap` (estimate `'64'`, amount `'20'`) → amount `+$20`, was `$64`, line `算出 $64，本期上限只剩 $20`.
  - `no strike when the estimate equals the figure numerically` (estimate `'13.00'`, amount `'13'`) → was `null`.
  - `posted: row date, account and link` (state posted, ledger_entry_id 501, ledger_amount `'13'`, rewards `[{ id: 501, posted_date: '2026-11-15', … }]`) → line `11/15 已入帳到 玉山 Pi 卡`, link 501, and only ONE card in the result (the row is not repeated).
  - `posted to a future date reads 將於` (row posted_date `'2026-12-15'`) → line `將於 12/15 入帳到 玉山 Pi 卡`.
  - `bank_confirmed matching` (ledger_amount `'13'`) → line `與帳單相符`, was null.
  - `bank_confirmed with another figure` (amount `'13'`, estimate `'13'`, ledger_amount `'12'`) → amount `+$12`, was `$13`, line `銀行實際給 $12`.
  - `bank_confirmed from an older API without ledger_amount` (field deleted from the fixture) → amount `+$13`, line `與帳單相符`.
  - `forfeited by the statement` (amount `'10'`, estimate `'10'`, ledger_entry_id 502, ledger_amount `'0'`) → amount `$10`, was null, line `帳單上沒有這筆回饋`.
  - `forfeited by the statement prints the capped amount, not the estimate` (estimate `'64'`, amount `'20'`, ledger_entry_id 503, ledger_amount `'0'`) → amount `$20`, line `帳單上沒有這筆回饋`.
  - `forfeited past the cap` (estimate `'8'`, amount `'0'`, no row) → amount `$8`, line `本期上限已用完`.
  - `forfeited after the row was deleted` (amount `'13'`, no row) → amount `$13`, line `回饋記錄已刪除，不會入帳`.
  - `points in every figure` (reward_account_id 99, name `亞洲萬里通`, estimate `'150'`, amount `'120'`) → amount `+120 亞洲萬里通`, was `150 亞洲萬里通`, line `算出 150 亞洲萬里通，本期上限只剩 120 亞洲萬里通`.
  - `imported reward row without an accrual` (accruals `[]`, rewards `[{ id: 600, name: null, amount: '30', account_id: 7, account_name: '玉山 Pi 卡', currency: 'TWD', posted_date: '2026-10-15' }]`) → `{ name: '紅利回饋', state: 'posted', amount: '+$30', line: '10/15 已入帳到 玉山 Pi 卡', link: 600 }`.
  - `accrual cards keep API order and come before unlinked rows`.
- [ ] **Step 2: Run** `npx ng test --watch=false --include='src/app/components/accounting/entry-detail/reward-cards.spec.ts'` — FAIL (module missing).
- [ ] **Step 3: Implement** `rewardCards` per the rules above.
- [ ] **Step 4: Run** the same command — PASS; `npx tsc -p tsconfig.app.json --noEmit` clean.
- [ ] **Step 5: Commit** `feat(spa): reward card view model for the entry detail (#323)`

---

### Task 3: 回饋 section in the entry detail

**Files:**
- Create: `frontend/src/app/components/accounting/entry-detail/reward-section.ts` (inline `styles`), `reward-section.html`
- Modify: `frontend/src/app/models/accounting.model.ts` (`LedgerAccount.cash_equivalent?: boolean`), `entry-detail.html:99-118`, `entry-detail.ts` (`rewardAmount` money test gains `|| account.cash_equivalent === true`; imports; `accrualStateLabels`, `accrualValue`, `rewardWhen` removed when unused), `entry-detail.scss` (drop `.reward-line`/`.accrual-line` rules if only these used them)
- Test: `entry-detail.spec.ts` (replace the six reward tests at `:103-180`)

**Interfaces:**
- Consumes: `RewardStateTagComponent` (Task 1); `rewardCards`, `RewardAmountText`, `RewardCard` (Task 2); `REWARD_POSTED` from `../review-reason`.
- Produces: `AppEntryRewardSectionComponent`, selector `app-entry-reward-section`, inputs `detail = input.required<EntryDetail>()`, `amountText = input.required<RewardAmountText>()`, `today = input.required<string>()`; projects `<ng-content />` as the last item. `entry-detail.ts` gains `readonly rewardText: RewardAmountText` (an arrow delegating to `rewardAmount`).

Styles are view-encapsulated, so the section declares its own `.dsec` rule, copied from `entry-detail.scss:106` (`color: var(--app-text-muted); font-size: 0.78rem; font-weight: 800; margin: 12px 0 4px`). Cards: `border-bottom: 1px solid var(--app-border); padding: 8px 0; font-size: 0.88rem`; the link row `min-height: 44px` tap target.

Section markup: `@if (visible())` where `visible = cards().length > 0 || detail().review_reason === REWARD_POSTED`; `<div class="dsec">回饋</div>`, then one `<article class="reward-card" [attr.data-state]="card.state">` per card:
- line 1: `<b class="reward-name">` + `<app-reward-state-tag [state]="card.state" />`;
- line 2: the amount — for forfeited an `<s class="reward-amount">` (semantic strike; `--app-text-muted`), else `<span class="reward-amount">` in `var(--tone-pos, var(--c-green))`, the old `.pos` colour — and `<s class="reward-was">` when `was`;
- line 3: `<p class="reward-note">{{ card.line }}</p>`, and for `link` a `<a class="reward-link" [routerLink]="['/accounting/entries', card.link]">看回饋記錄 ›</a>`;
then `<ng-content />`.

In `entry-detail.html`, replace the reprice card line and both old blocks with:

```html
<app-entry-reward-section [detail]="d" [amountText]="rewardText" [today]="today">
  <app-entry-reward-reprice-card [detail]="d" [amountText]="repriceAmount" (changed)="load(d.id, true)" />
</app-entry-reward-section>
```

Ruling (spec gap): the section also shows for a `reward_posted` entry with no cards so the reprice card, whose visibility is unchanged, never loses its host.

- [ ] **Step 1: Rewrite the failing tests** in `entry-detail.spec.ts` (reuse the `detail()` fixture helper and the existing accounts setup at `:27`):
  - `shows one 回饋 section with a tagged card per accrual`: two estimated accruals (the spec's 全聯 example: `+$13` / `預計 12/15 入帳到 玉山 Pi 卡`, and `+$20`, `$64` struck, `算出 $64，本期上限只剩 $20`) → one `.dsec` with text `回饋`; two `.reward-card`; each has `app-reward-state-tag` with text `預估`; no element with text `紅利回饋` or `預估回饋`.
  - `links posted cards to their reward rows without a second list`: two posted accruals with rows 501/502 in `rewards` → two cards, two `.reward-link` with `href` ending `/accounting/entries/501` and `/502`, no `紅利回饋` heading.
  - `prints points with the reward account name, never $` (moved from the #294 test): `120 亞洲萬里通` in the card and no `$120`.
  - `prints a cash-equivalent points reward as money`: the same fixture with the reward account `cash_equivalent: true` → `+$120` in the card and no `亞洲萬里通` figure.
  - `keeps points when the API sends no cash_equivalent`: the reward account without the field → `120 亞洲萬里通`.
  - `shows an imported reward row as a posted card`: no accruals, one reward row → one card tagged `已入帳` with its amount and date.
  - `keeps the reprice card as the section's last item and reloads after 重算 (#306)`: the existing #306 test, plus `section.lastElementChild` is (or contains) `app-entry-reward-reprice-card`.
  - `keeps the reprice card for a reward_posted entry without accruals or rows`: `review_reason: 'reward_posted'`, empty lists → section present, reprice card rendered.
  - `strikes the expected figure on a forfeited card`: one forfeited accrual (amount `'13'`, no row) → the card's amount element is an `S` element (`tagName === 'S'`) with text `$13`, and the line `回饋記錄已刪除，不會入帳`.
  - `has no 回饋 section without accruals, reward rows or a reward_posted review`.
  - `places the section after the debt state and before the e-invoice card`: in DOM order, `app-entry-reward-section` follows the debt block and precedes `app-entry-einvoice-card`.
- [ ] **Step 2: Run** `npx ng test --watch=false --include='src/app/components/accounting/entry-detail/*.spec.ts'` — new tests FAIL.
- [ ] **Step 3: Implement** the section component and the `entry-detail` swap.
- [ ] **Step 4: Run** the same command — PASS; then the full suite `npx ng test --watch=false` and `npx tsc -p tsconfig.app.json --noEmit` — all green (the line-icon gate included).
- [ ] **Step 5: Commit** `feat(spa): one 回饋 section of reward cards in the entry detail (#323)`

---

### Task 4: Screens and spec bookkeeping

**Files:**
- Modify: `openspec/changes/polish-reward-and-account-settings/tasks.md` (tick 5.1–5.3; under 2.1/2.3 note the tag half done in PR D)
- Modify: `openspec/changes/polish-reward-and-account-settings/specs/frontend-accounting-ledger/spec.md` — requirement "Entry reward section": the absence sentence becomes 「The section SHALL be absent when the entry has neither accruals nor reward rows and is not awaiting a reward reprice (`review_reason` `reward_posted`); in that case the section holds only the reprice card.」 Also note the ruling in design D7 (one sentence after the reprice-card paragraph).
- Evidence: `~/reports/home-hub-entry-reward-section-<TS>/` (700/600), not in the repo

- [ ] **Step 1:** Dev build (`npx ng build --configuration development`), rsync `dist/inventory-ui/browser/` to the preview dir, start the 18081 preview, and capture with the Playwright container at 390 / 820 / 1440, light and dark, an entry with the four states (stub data: fictional names/amounts). Check by eye against `RewardStatesSpec.dc.html`: dashed 預估 border visible, icons visible in a greyscale copy (`convert -colorspace Gray`), struck figures, link row.
- [ ] **Step 2:** Stop the preview (by PID). `openspec validate polish-reward-and-account-settings --strict` — valid.
- [ ] **Step 3: Commit** `docs(openspec): tick #323 PR D tasks, reprice host exception`
