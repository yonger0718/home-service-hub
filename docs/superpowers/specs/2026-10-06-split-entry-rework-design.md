# Split (多類別) entry rework — design

Date: 2026-10-06 · Base: `main` @ 4e301e2 · Owner decisions: option B (MOZE-style parent bubble; everything per child except what the parent must own), convert an existing single entry into a split in this round.

Sources: MOZE docs §2.1/§2.9/§6.2 (feature digest), the owner's review of the first proposal (seven points, 2026-10-06), fact sheet of the current split implementation (split-lines, entry-save, split_service).

## Goals

1. One input method for every line of a record: the same category grid, keypad, tiles and pickers edit whichever child bubble is selected. No separate "新增拆帳行" modal, no `<select>`.
2. A split is a parent with N equal children. Children own their own category, kind, amount, account, counterparty, name, project, tags, fee/discount, FX, reward rules and notes; the parent owns merchant, date/time/posted date, group name and group notes.
3. Saving is atomic and id-stable: an existing single entry can become a split at save time without losing its id; editing a split never recreates untouched members.
4. Old splits round-trip without silent data loss (differing member dates or notes stay as they are unless the user changes them).

Non-goals: parent-to-child sync preferences (MOZE §2.9), e-invoice import to splits, reordering children, per-child dates, 多筆收還款, MOZE's "2個帳戶" card art.

## Global constraints

- Frontend: Angular 21 standalone + signals, Vitest; backend: FastAPI + SQLAlchemy 2.0 + Alembic (no schema change in this design); all strings 繁體中文, noun 記錄.
- Accepted contracts untouched: schedules (`openspec/specs/accounting-schedules`), the dirty-form registry and overlay Esc contract (UX refinement PR-2), the account picker (PR-3), the category drill-in grid (PR-5).
- Backend changes ship first in their own PR (owner's session; Multica cannot reach Postgres); the frontend PR (Multica) targets the merged API.
- Every behaviour below has a test: backend `tests/integration/test_splits.py` (+ new file for conversion), frontend component specs. `cd frontend && npm test -- --watch=false` and `pytest` green; `npx ng build` green.
- Checked at 390×844, 760×820, 1280×800.

## 1. Data and API contract (backend, PR-B)

### 1.1 Storage (unchanged schema)

- `EntryGroup(kind='split')` holds `name`, `merchant`, `description` — the parent's 整筆名稱／商家／整筆備註.
- Every member is a `ledger_entry` with `entry_group_id`. Date, time and posted date live on members; the parent date is "the value written to every member".
- Invoice stays per member (no parent invoice).

### 1.2 `SplitIn` / `SplitMemberIn` (schemas/writes.py)

- `SplitMemberIn` gains `id: int | None = None` (an existing member to update in place) and `client_key: str | None = None` (echoed back for the client; not stored).
- Members always send explicit `project_id` (nullable), `tags` (list, may be empty), `entry_date`, `entry_time`, `posted_date`, `description`. The server-side inheritance in `member_payloads()` for omitted fields stays for other callers; this frontend never relies on it. `null` and `[]` are values, not "inherit".
- `members` min length 1 (dissolve case, §1.4).

### 1.3 `PUT /splits/{group_id}` becomes upsert

In one transaction, under the existing locks (group FOR UPDATE → members FOR UPDATE) and the existing refusals (scheduled group, locked group):
1. Validate every member with `prepare_entry` (errors keyed `members.{i}.{field}` as today).
2. Partition: `keep` = members with an `id` that belongs to this group; `new` = members without `id`; `drop` = current members whose id is not in the request. An `id` from another group or unknown → 404 `member_not_found`.
3. **Protected members** (have settlements/refunds, or are transfer legs / system entries): must appear in `keep` with the same `kind`, `amount`, `account_id`, `category_id`, `counterparty_id`, `original_amount/currency/fx_rate` as stored, else 409 `member_locked`; dropping one → 409 `member_locked`. Their other fields (name, tags, project, description, dates) may change.
4. `drop` → `delete_entries_cascade`; `keep` → update in place (children such as fee/discount rows are replaced, reward rows recomputed as for a single-entry update); `new` → `insert_prepared(..., group_id)`.
5. Group fields updated from the payload.
6. Response `{group_id, member_ids}` with `member_ids` in **request order**, plus `members: [{id, client_key}]`.

`remember_all_defaults` runs for new and changed members as today.

### 1.4 Dissolve

`PUT /splits/{gid}` with exactly one member (its `id` must be an existing member) dissolves the group in the same transaction: the member keeps its id and is updated from the payload; `group.name/merchant/description` are copied onto the member **only where the member's own field is empty** (the frontend shows the warning in §2.7 when both are set); the group row is deleted. Response `{group_id: null, member_ids: [id]}`. A dissolve on a protected member follows §1.3 rule 3.

### 1.5 Convert a single entry: `PUT /entries/{entry_id}/split`

Body = `SplitIn` whose members include exactly one member with `id == entry_id` (the entry being converted; its draft values apply) and ≥1 member without `id`. In one transaction: refuse when the entry is already in any group (409 `already_grouped`), is a transfer leg / system entry / schedule-generated (409 `kind_not_splittable`), has settlements or refunds (409 `entry_locked`), or the ledger is import-locked; otherwise create `EntryGroup(kind='split', …)`, attach and update the existing entry in place, insert the others. Response as §1.3. Nothing is written on failure; a retry after a success hits `already_grouped`.

### 1.6 Delete

`DELETE /splits/{gid}` unchanged (whole group). Deleting one member through `DELETE /entries/{id}` stays as today; when the group would be left with one member the server dissolves it (same copy rule as §1.4) so a one-member group never persists.

### 1.7 Backend tests (test_splits.py + test_split_convert.py)

Upsert: keep/new/drop in one call, ids stable, order of `member_ids`; unknown/foreign id → 404; protected member dropped → 409; protected member changed amount → 409; protected member name change → 200; dissolve copies name only into empty field; dissolve on last member keeps id; convert happy path keeps entry id and returns new ids; convert refusals (already grouped, transfer, system, scheduled, settled, import-locked); convert retry → 409 `already_grouped` with no duplicate group; concurrency: two PUTs on the same group serialize; `DELETE /entries/{id}` leaving one member dissolves.

## 2. Frontend model and flow (PR-6, Multica)

### 2.1 State (`entry-form`)

```ts
interface ChildDraft { key: string /* uuid */; id: number | null; kind; categoryId; amountExpr; accountId; counterpartyName; name; projectId; tags; description; fee; discount; fx; ruleIds; invoice; locked: boolean }
children = signal<ChildDraft[]>([])      // length ≥ 1
selected = signal<string | 'parent'>(key) // which bubble the tiles edit
parent = { name, merchant, description, entryDate, entryTime, postedDate, dateTouched: boolean }
```

- A single-child form (`children().length === 1`) is today's single-entry form; no parent bubble, the parent fields render inline as now (date/time/merchant/notes tiles) and save as a plain entry.
- `isSplit = computed(() => children().length >= 2)`.
- All existing per-entry signals (`amountExpr`, `accountId`, `category`, `fx`, `fee`, `discount`, `ruleIds`, …) become **views over the selected child** (read from and write to `children()[selectedIndex]`), so the keypad, amount tile, category picker, account picker, fee/fx sheets and reward chips keep their current bindings.
- `DirtyFormRegistry` snapshot = serialised `{parent, children}` (keys excluded).

### 2.2 Bubble strip (`category-picker` strip, extended)

- Single child: `[selected category bubble] [＋]` (today's strip plus the ＋ that used to be `splittable`).
- Split: `[多類別 <net> (N)] [child bubbles…] [＋]`, horizontally scrollable, selected bubble outlined. Child bubble = category icon/colour, category name (or kind label), signed amount; a child with no category yet shows a dashed bubble "未選類別".
- ＋ → `addChild()`: pushes `{kind: prev.kind, accountId: prev.accountId, everything else empty}`, selects it, opens the category grid (drill-in) for it. After the pick the grid collapses to the strip and focus goes to the amount tile (keypad on phone).
- Tapping a child bubble selects it; tapping the parent bubble selects `'parent'`.
- Removing a child: a「移除此項」button in the child's tile area (not on the bubble) → removes it and selects the previous child; disabled when it is the last child or the child is `locked`.
- When a split drops to one child the form returns to single mode visually; if that child has a persisted `id` inside a group, saving performs the dissolve (§1.4) and the parent card shows the merge warning (§2.7) when both names are set.

### 2.3 Child mode (a child bubble selected)

Tiles: 金額 (keypad), 名稱, 帳戶 (picker), 專案, 對象 (應收／應付 only), 發票, 標籤 chips, 備註 (this child), fee/discount chips, FX, reward rules — exactly today's single-entry tiles minus merchant/date/time. The 記錄類型 tabs act on the selected child (the first child's kind is what the top tabs show; switching kind on a child re-validates its category). Event type (單次／週期／分期) is shown only in single mode; a split is always 單次 (the row renders disabled with the hint 拆帳不支援週期／分期 when `isSplit`).

### 2.4 Parent mode (`selected === 'parent'`)

- Parent card: net per currency over child base amounts (sum of signed amounts; fee/discount/rewards excluded), one line per currency; `N 項 · M 個帳戶`; `預估回饋 <sum>` line when any child has rule rewards. Read-only.
- Tiles: 商家, 日期, 時間, 入帳日, 整筆名稱, 整筆備註, and the 刪除整組 action when editing. Keypad hidden.
- `dateTouched` becomes true the first time the user changes 日期／時間／入帳日 in this session.

### 2.5 Load (edit an existing split; entry-detail 編輯 on any member)

- Fetch all members; build `children` in `group_members` order with `id`, `locked` (settlement/refund/transfer/system), and the opened member selected.
- Parent tiles from the group (name/merchant/description) and from the **first member's** dates; `dateTouched=false`. If members' dates differ, the parent date tile shows the first member's date with the caption「子項日期不一致」.
- Per-child description/tags/project come from each member; nothing is merged.

### 2.6 Save (`planEntrySave` → `toSplitInput`)

- Single child, no `groupId` → today's create/update entry.
- Single child with `groupId` (dropped to one) → `PUT /splits/{gid}` with one member (dissolve).
- ≥2 children, no `groupId`, new record → `POST /splits`.
- ≥2 children, no `groupId`, editing an existing single entry (`entryId`) → `PUT /entries/{entryId}/split`; the converted entry is the child whose `id === entryId`.
- ≥2 children with `groupId` → `PUT /splits/{gid}` (upsert) with each child's `id` (null for new).
- Member payload: every field explicit — `project_id` (null ok), `tags` (list), `description`, `entry_date/entry_time/posted_date` = parent values when `dateTouched` or when the member is new; otherwise the member's own loaded dates. `client_key` sent; the response maps new ids back by `client_key`.
- After save: navigate to the member that was selected (by id from the response); `rememberEntryUse` only for the single-entry path (last-use prefill is not updated from children); recent accounts updated with every child's account.
- Failed save keeps the draft and dirty state; errors `members.{i}.{field}` map back to the child by index → key and select that child, showing the field error on its tile.

### 2.7 Guards and notices

- ＋ disabled (with tooltip/hint) when: editing a transfer, 系統 record, schedule-generated entry, or an entry with settlements/refunds; or when `locked()`/import-locked.
- Dropping to one child while the group has a name/merchant/description and the remaining child has its own non-empty name → parent card line「整筆名稱「X」將不保留」(same for merchant/備註) until saved or undone.
- Esc contract unchanged: picker/sheet → form cancel → discard prompt. In parent mode Esc behaves like child mode.
- Switching kind on a child whose category belongs to another kind clears the category and reopens the grid.

### 2.8 Removal of old code

`split-lines/*` (component, modal, spec) deleted; `category-picker` `splittable`/`addLine` replaced by the strip's ＋ described in §2.2; `entry-save.ts` `toSplitInput` rewritten to the explicit-field contract; `members`/`splitGroup` signals replaced by `children`/`parent`.

### 2.9 Timeline and detail

- Timeline: one row per group (unchanged); the row's title = group name, else `多類別`; badge N (existing `groupCount`).
- Entry detail of a member: adds a parent card at the top (多類別 · net · N 項 · M 個帳戶 · merchant · date) with the child list (each child's icon, name/category, amount, account; the open one highlighted); existing per-member actions stay; 編輯 opens the form with that child selected.

### 2.10 Frontend tests

entry-form: ＋ creates a child with previous kind/account and opens the grid; picking a category selects the new child and focuses the amount; keypad writes the selected child only; switching bubbles swaps tile values; parent mode shows per-currency net, N and M, hides the keypad; remove child re-selects previous and is disabled on the last/locked child; drop-to-one shows the merge warning when both names set; save plans: single → entry, 2 new → POST /splits, existing single + ＋ → PUT /entries/{id}/split with the converted child's id, existing group → PUT /splits with ids, dropped to one → dissolve; explicit `project_id: null` and `tags: []` sent; untouched parent date re-sends each member's own date, touched date unifies; `members.1.amount` error selects child 2; ＋ disabled for transfer/system/scheduled/settled; dirty snapshot includes children; Esc order picker → form → discard. category-picker: strip renders parent/child/＋ bubbles, selection outline, dashed 未選類別. entry-detail: parent card and child list; 編輯 opens with the child selected. timeline: title fallback 多類別.

## 3. Delivery

- **PR-B (backend, owner's session)**: §1 with tests; no frontend change; `openspec` ledger spec updated for the upsert/dissolve/convert rules.
- **PR-6 (frontend, Multica via lead-astra)**: §2 after PR-B is merged; branch from main; same rules as AGENT-61/62/64.
- Demo preview and 4300 refresh before release; release as a frontend publish + backend restart (the backend PR adds routes; no migration).

## Review focus

1. Editing an old split whose members have different dates, then changing only a child's amount: every member must keep its own date (no unification).
2. Convert-then-fail: `PUT /entries/{id}/split` with an invalid second member must leave the entry ungrouped and unchanged.
3. Upsert with a protected member listed unchanged and another member dropped must succeed; the same call with the protected member's amount changed must 409 and write nothing.
4. The keypad after a bubble switch must target the newly selected child (no write to the previous child from a pending `↵`/debounce).
5. A new child added while the category request for the previous child is still in flight must not receive that category (clientKey routing).
