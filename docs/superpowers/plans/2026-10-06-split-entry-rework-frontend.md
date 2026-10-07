# Split entry rework — frontend implementation plan

**Goal:** Edit a split through equal child drafts and a parent bubble; preserve ids, raw provenance and protected members across create, convert, upsert and dissolve.

**Architecture:** A form-local signal store owns child identity, parent fields and transitions. Pure serializers choose the five save routes and produce explicit member payloads. Existing tiles bind to the selected child through writable views; captured owner tokens protect asynchronous work. CategoryPicker retains PR-5 drill-in behavior and gains the bubble strip. Read-only summary functions serve both the form and detail card.

**Tech Stack:** Angular 21 standalone/signals, RxJS, existing Vitest/HttpTestingController/RouterTestingHarness; no new dependencies.

**Spec:** Binding v4 at [`a864393`](https://github.com/yonger0718/home-service-hub/blob/a864393/docs/superpowers/specs/2026-10-06-split-entry-rework-design.md), §2; HTTP contracts §1.3/§1.5/§1.6. Spec remains on its own branch and is not copied or edited here.

**Base / branch:** `main` @ `4e301e24a1c2852258fbced2d98ec0745a2a337c` → `feat/split-entry-rework-fe`. Anchors below refer to this base and include symbols to survive line movement. This commit contains only this plan. Do not execute its implementation steps before the owner's plan review clears (P1 blocks; P2/P3 proceed). Backend PR-B must land before frontend delivery; use contract HTTP mocks while its demo routes return 404.

**Owner / risk / budget:** lead-astra owns the plan and integration; medium/high regression risk (financial payload preservation, not backend transaction implementation). Implementation author and a capable non-author reviewer must be assigned after approval using the existing workspace routing/capacity rules. No second Lead, nested agents, added capacity or paid fallback. `budget_ref=unknown`; run/usage source unavailable = `null`.

## Global Constraints

- Frontend: Angular 21 standalone + signals, Vitest; backend: FastAPI + SQLAlchemy 2.0 + Alembic. **No schema migration** in this design; `SplitOut.group_id` becomes nullable (API change, listed in §1.6).
- Accepted contracts untouched: schedules (`openspec/specs/accounting-schedules`, incl. D29/D32/D33 lock order and loan references), the dirty-form registry and overlay Esc contract (UX refinement PR-2), the account picker (PR-3), the category drill-in grid incl. its Esc contract (PR-5: child grid → main grid; reopened main with a selection → strip + focus; unselected main → form).
- Backend changes ship first in their own PR (owner's session; Multica cannot reach Postgres); the frontend PR (Multica) targets the merged API.
- Every rule below has a test (§1.8, §2.11). `pytest` (incl. Postgres integration), `cd frontend && npm test -- --watch=false`, `npx ng build` green. Checked at 390×844, 760×820, 1280×800.
- Terms: **anchor** = the existing entry being converted; **protected member** = §1.2; **empty** (for copy rules) = `null` or whitespace-only after trim; **editable kinds** = `EditableKind` (expense, income, receivable, payable).


## Execution constraints

- Allowed implementation paths: `frontend/src/app/**` and this plan. Repository-root `services/**`, `openspec/**`, `.env*`, the binding spec, deployment and permissions are outside scope. `frontend/src/app/services/accounting.service.ts` is the frontend HTTP client and is in scope.
- Preserve Git identity. Every commit ends with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. The implementation PR body ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`; no merge.
- Use an isolated clone with a credential-free, untracked/ignored local `frontend/src/environments/environment.ts`; no tokens copied from another session. Use `npx ng build` so `set-env.js` does not read/write `.env`.
- Every implementation task follows red → green → full `cd frontend && npm test -- --watch=false` → `cd frontend && npx ng build` → commit. A focused command never substitutes for the full pre-commit suite. Plan-only commits do not claim application tests passed.
- A failed task gets one evidence-based repair attempt before handing over the diff, failure and remaining questions. Do not reset this budget by creating another task.
- Each code block below is an insertion/replacement at the named symbol, not a complete replacement of an existing component. Existing unrelated transfer/schedule/delete handlers and imports remain; imports listed for new modules are complete. Test additions to an existing `describe` use that file's named fixtures/helpers, which are identified explicitly. No backend assertions are claimed from mocked frontend tests.

## Review Focus

1. Spec focus 1: old members with different dates, change only one amount → every raw date/time/posted date retained. **Task 3, `preserves raw provenance and explicit empty fields`**, and Task 7 HTTP body test.
2. Spec focus 4: pending keypad commit flushed to old child before switch; subsequent input targets new child. **Task 4, `flushes before changing owner, including parent`** and Task 9 DOM regression.
3. Spec focus 5: removed/replaced child account/category/FX responses dropped by key + generation, including equal account/kind values. **Task 4, `drops same-value stale owners`**.
4. Additional: protected settlement/refund/system signs are server signs; no original FX currency summed. **Task 6, `uses stored signs and account currencies`**.
5. Additional: dissolution cannot discard the last persisted id; keep payload contains no financial/date properties and routes errors by submitted keys. **Task 2, `guards all four removals`**, **Task 3, `emits only keep metadata`**, **Task 7, `routes indexed errors using submitted keys`**.

## Resolved interpretations

- `EditableKind` in the spec is the existing `WritableEntryKind`; do not widen it. `ChildDraft.kind` may carry a real `EntryKind` for protected rows; single transfer/system mode remains a separate `FormKind` adapter.
- Current wire `fx_source` uses `fx_api` for online. Preserve that DTO spelling unless PR-B explicitly changes it; the spec's “online” is semantic, not permission to invent a wire enum. No main-currency member amount exists in current DTO: `loaded.signedBase=null` unless account currency is the preference's main currency.
- A switch invalidates the outgoing child's generation after flushing synchronous pending input. Returning to the child starts fresh async requests even with the same kind/account. Response identity is never inferred from these values.
- Parent mode's add derives from the last child; protected/non-editable kind falls back by scanning predecessors. Account inheritance still comes from that last/selected child, independently of the kind fallback.
- New members serialize `merchant:null`; persisted members use raw loaded merchant. When an unsaved split shrinks to one, parent merchant wins only if non-empty; single payload uses parent merchant. Existing-group single layout still edits group parent values until dissolve succeeds.
- Parent dates compare all three raw values on the first single→split transition. A user time edit may produce minute precision; an untouched loaded time must never be truncated. Date controls display raw values with `step="1"`; millisecond precision stays in the model if the browser display cannot express it.
- Async defaults must not rewrite the entire dirty baseline: track a default-only baseline patch for the same child/fields; user edits elsewhere stay dirty. Automatic rule selection is excluded until `rulesTouched`; FX quote rate/date are excluded when online, while user-selected FX inputs remain in the snapshot.
- Estimates are per-account-currency, exclude existing reward ledger rows and do not claim available statement/window caps. Unknown FX totals display `—`, never zero or an original-currency sum.
- No required §2 deviation identified. Owner review is still required for these interpretations before implementation.

---
### Task 1: Type the upsert/convert envelope and protected read flag

**Files:** Modify `frontend/src/app/models/accounting.model.ts:190` (`EntryDetail.group_members`), `:314` (`SplitInput`); `frontend/src/app/services/accounting.service.ts:192` (`createSplit`, `updateSplit`); test `frontend/src/app/services/accounting.service.spec.ts:153` (request table).

**Interfaces:** Consumes existing `EntryInput`, `LedgerEntry`, `AccountingService.bump<T>(request: Observable<T>): Observable<T>`. Produces `SplitMemberInput`, `SplitKeepInput`, `SplitResult`, `SplitGroupMember`; `convertEntryToSplit(entryId: number, input: SplitInput): Observable<SplitResult>`. Create/update methods keep their arguments and return `Observable<SplitResult>`.

- [ ] **Step 1 — red:** Add to the existing service suite (its `service`, `httpMock`, `API`, `SPLIT_INPUT` setup is retained); add the convert row to its request table.

```ts
it('serializes nullable group id and client keys for dissolve', () => {
  const result = { group_id: null, member_ids: [7], members: [{ id: 7, client_key: 'a' }] };
  let received: SplitResult | undefined;
  service.updateSplit(4, SPLIT_INPUT).subscribe(value => received = value);
  const req = httpMock.expectOne(`${API}/splits/4`);
  expect(req.request.method).toBe('PUT');
  req.flush(result);
  expect(received).toEqual(result);
});
// Add within the existing parameterized request table:
{ name: 'convertEntryToSplit', call: s => s.convertEntryToSplit(7, SPLIT_INPUT),
  method: 'PUT', url: `${API}/entries/7/split`, body: SPLIT_INPUT },
```

Run `cd frontend && npm test -- --watch=false --include src/app/services/accounting.service.spec.ts`; expect missing method/type failure.

- [ ] **Step 2 — green:** Replace the models with the following; `SplitGroupMember` is used only for `EntryDetail.group_members` so ordinary timeline entries do not acquire a fake read flag. Update group-member test fixtures to provide `protected:false, protected_reason:null` explicitly.

```ts
export type SplitMemberInput = EntryInput & { id?: number; client_key: string };
export interface SplitKeepInput {
  id: number;
  keep: true;
  client_key: string;
  name?: string | null;
  project_id?: number | null;
  tags?: string[];
  description?: string | null;
}
export interface SplitGroupMember extends LedgerEntry {
  protected: boolean;
  protected_reason: string | null;
}
export interface SplitInput {
  name: string | null;
  merchant: string | null;
  description: string | null;
  entry_date: string;
  entry_time: string | null;
  posted_date: string | null;
  project_id: number | null;
  tags: string[];
  members: Array<SplitMemberInput | SplitKeepInput>;
}
export interface SplitResult {
  group_id: number | null;
  member_ids: number[];
  members: Array<{ id: number; client_key: string | null }>;
}
// EntryDetail:
// replace group_members: LedgerEntry[] with:
group_members: SplitGroupMember[];
```

```ts
// AccountingService; add SplitResult to its model imports.
createSplit(input: SplitInput): Observable<SplitResult> {
  return this.bump(this.http.post<SplitResult>(`${this.apiUrl}/splits`, input));
}
updateSplit(groupId: number, input: SplitInput): Observable<SplitResult> {
  return this.bump(this.http.put<SplitResult>(`${this.apiUrl}/splits/${groupId}`, input));
}
convertEntryToSplit(entryId: number, input: SplitInput): Observable<SplitResult> {
  return this.bump(this.http.put<SplitResult>(`${this.apiUrl}/entries/${entryId}/split`, input));
}
```

For this type-only transition, existing `toSplitInput` temporarily assigns `client_key: String(index)` in its map; Task 3 replaces that function completely with UUID identity. This compatibility line is not the final serializer. Existing `SPLIT_INPUT` fixtures also acquire client keys.

- [ ] **Step 3 — verify/commit:** Focused suite, full `npm test -- --watch=false`, `npx ng build`; commit `feat(split): type stable member API` with required trailer.

### Task 2: Child/parent state and add/remove field transitions

**Files:** Create `frontend/src/app/components/accounting/entry-form/split-draft.ts` and `split-draft.spec.ts`; consume `entry-draft.ts:14` (`FormKind`), `:48` (`signFor`), `entry-save.ts:131` (`emptyEntryInput`), model types.

**Interfaces:** Produces `ChildDraft`, `ParentDraft`, `Owner`, `newChild(kind?: EntryKind, accountId?: number | null): ChildDraft`, `newParent(date: string, time: string): ParentDraft`, `SplitDraftStore`. Public store signals: `children`, `selected`, `parent`, `groupId`, `anchorId`, `droppedNotices`; methods `current(): ChildDraft|null`, `capture(): Owner|null`, `accept(owner: Owner, patch: Partial<ChildDraft>): boolean`, `invalidate(key: string): void`, `add(accounts: readonly LedgerAccount[]): ChildDraft|null`, `removeReason(key: string): string|null`, `remove(key: string): boolean`. `select` is implemented in Task 4, with explicit flush ownership.

- [ ] **Step 1 — red:** New test module:

```ts
import { describe, expect, it } from 'vitest';
import { makeAccount } from '../testing/fixtures';
import { newChild, newParent, SplitDraftStore } from './split-draft';

describe('SplitDraftStore transitions', () => {
  it('moves single merchant but keeps child metadata and edited dates', () => {
    const store = new SplitDraftStore(newParent('2026-10-06', '12:30:17'));
    const first = store.children()[0];
    store.children.set([{ ...first, name: 'child', description: 'note', accountId: 3 }]);
    store.parent.update(p => ({ ...p, merchant: 'shop' }));
    const added = store.add([makeAccount({ id: 3, is_archived: true }), makeAccount({ id: 4 })])!;
    expect(added.accountId).toBe(4);
    expect(store.parent().name).toBe('');
    expect(store.parent().dateTouched).toBe(true);
    expect(store.children()[0].name).toBe('child');
    store.parent.update(p => ({ ...p, name: 'group', description: 'group note' }));
    expect(store.remove(added.key)).toBe(true);
    expect(store.parent().merchant).toBe('shop');
    expect(store.droppedNotices()).toEqual(['整筆名稱「group」不會保留', '整筆備註不會保留']);
    expect(store.children()[0].description).toBe('note');
    store.add([makeAccount()]);
    expect(store.droppedNotices()).toEqual([]);
  });
  it('guards all four removals', () => {
    const s = new SplitDraftStore(newParent('2026-10-06', ''));
    const a = s.children()[0];
    expect(s.removeReason(a.key)).toBe('至少保留一項');
    const b = s.add([makeAccount()])!;
    s.children.update(rows => rows.map(c => c.key === a.key ? { ...c, id: 7 } : c));
    s.anchorId.set(7);
    expect(s.removeReason(a.key)).toBe('原記錄不可移除');
    s.anchorId.set(null); s.groupId.set(4);
    expect(s.removeReason(a.key)).toBe('至少保留一項原有記錄');
    s.children.update(rows => rows.map(c => c.key === b.key ? { ...c, id: 8, protected: true } : c));
    expect(s.removeReason(b.key)).toBe('受保護子項不可移除');
    expect(s.remove(a.key)).toBe(true);
    expect(s.selected()).toBe(b.key);
  });
  it('uses preceding editable kind and separately inherits account; caps growth', () => {
    const s = new SplitDraftStore(newParent('2026-10-06', ''));
    const a = newChild('income', 1), b = newChild('refund', 2);
    s.children.set([a, { ...b, protected: true }]); s.selected.set('parent');
    expect(s.add([makeAccount(), makeAccount({ id: 2 })])).toMatchObject({ kind: 'income', accountId: 2 });
    for (const count of [50, 51]) {
      s.children.set(Array.from({ length: count }, () => newChild()));
      expect(s.add([makeAccount()])).toBeNull();
      expect(s.children()).toHaveLength(count);
    }
  });
});
```

Run `cd frontend && npm test -- --watch=false --include src/app/components/accounting/entry-form/split-draft.spec.ts`; expect missing module.

- [ ] **Step 2 — green:** New module:

```ts
import { computed, signal } from '@angular/core';
import { ChildInput, EntryKind, LedgerAccount, RewardRule } from '../../../models/accounting.model';
import { FxValue } from '../fx-sheet/fx-sheet';
import { isWritableKind } from './entry-draft';

export interface ChildDraft {
  key: string; id: number | null; protected: boolean; protectedReason: string | null;
  kind: EntryKind; categoryId: number | null; amountExpr: string; accountId: number | null;
  counterpartyName: string; counterpartyId: number | null; name: string; projectId: number | null;
  tags: string[]; description: string; fee: ChildInput | null; discount: ChildInput | null;
  fx: FxValue | null; ruleIds: number[]; invoice: { number: string; random: string };
  loaded: {
    entryDate: string; entryTime: string | null; postedDate: string | null; merchant: string | null;
    attachedRuleIds: number[]; originalAccountId: number; source: string;
    signedAmount: string; signedBase: string | null; isSettlement: boolean; kind: EntryKind;
    currency: string;
  } | null;
  rulesTouched: boolean; pendingCategoryId: number | null; generation: number;
  // Derived presentation/default data; excluded from dirty identity.
  availableRules: RewardRule[];
}
export interface ParentDraft {
  name: string; merchant: string; description: string;
  entryDate: string; entryTime: string | null; postedDate: string | null; dateTouched: boolean;
}
export interface Owner { key: string; generation: number }
export function newParent(entryDate: string, entryTime: string): ParentDraft {
  return { name: '', merchant: '', description: '', entryDate, entryTime, postedDate: null, dateTouched: false };
}
export function newChild(kind: EntryKind = 'expense', accountId: number | null = null): ChildDraft {
  return {
    key: crypto.randomUUID(), id: null, protected: false, protectedReason: null, kind, categoryId: null,
    amountExpr: '', accountId, counterpartyName: '', counterpartyId: null, name: '', projectId: null,
    tags: [], description: '', fee: null, discount: null, fx: null, ruleIds: [],
    invoice: { number: '', random: '' }, loaded: null, rulesTouched: false, pendingCategoryId: null,
    generation: 0, availableRules: [],
  };
}
export const nonempty = (value: string | null | undefined): boolean => !!value?.trim();
export class SplitDraftStore {
  readonly children = signal<ChildDraft[]>([newChild()]);
  readonly selected = signal<string>('');
  readonly parent;
  readonly groupId = signal<number | null>(null);
  readonly anchorId = signal<number | null>(null);
  readonly droppedNotices = signal<string[]>([]);
  readonly isSplit = computed(() => this.children().length >= 2);
  constructor(parent: ParentDraft) {
    this.parent = signal(parent);
    this.selected.set(this.children()[0].key);
  }
  current(): ChildDraft | null {
    return this.children().find(c => c.key === this.selected()) ?? null;
  }
  capture(): Owner | null {
    const c = this.current();
    return c ? { key: c.key, generation: c.generation } : null;
  }
  accept(owner: Owner, patch: Partial<ChildDraft>): boolean {
    const c = this.children().find(c => c.key === owner.key);
    if (!c || c.generation !== owner.generation) return false;
    this.children.update(rows => rows.map(row => row.key === owner.key ? { ...row, ...patch } : row));
    return true;
  }
  invalidate(key: string): void {
    this.children.update(rows => rows.map(c => c.key === key ? { ...c, generation: c.generation + 1 } : c));
  }
  add(accounts: readonly LedgerAccount[]): ChildDraft | null {
    const rows = this.children();
    if (rows.length >= 50) return null;
    const previous = this.current() ?? rows[rows.length - 1];
    const index = rows.indexOf(previous);
    const editable = rows.slice(0, index + 1).reverse().find(c => isWritableKind(c.kind));
    const kind = editable?.kind ?? 'expense';
    const accountId = accounts.find(a => a.id === previous.accountId && !a.is_archived)?.id
      ?? accounts.find(a => !a.is_archived)?.id ?? null;
    if (rows.length === 1 && this.groupId() === null) {
      const p = this.parent(), loaded = rows[0].loaded;
      this.parent.set({ ...p, name: '', description: '', dateTouched: !loaded ||
        p.entryDate !== loaded.entryDate || p.entryTime !== loaded.entryTime || p.postedDate !== loaded.postedDate });
    }
    const next = newChild(kind, accountId);
    if (this.current()) this.invalidate(this.current()!.key);
    this.children.update(current => [...current, next]);
    this.selected.set(next.key); this.droppedNotices.set([]);
    return next;
  }
  removeReason(key: string): string | null {
    const rows = this.children(), c = rows.find(c => c.key === key);
    if (!c) return '找不到子項';
    if (rows.length === 1) return '至少保留一項';
    if (c.protected) return '受保護子項不可移除';
    if (c.id !== null && c.id === this.anchorId()) return '原記錄不可移除';
    if (this.groupId() !== null && !rows.some(row => row.key !== key && row.id !== null)) return '至少保留一項原有記錄';
    return null;
  }
  remove(key: string): boolean {
    if (this.removeReason(key)) return false;
    const index = this.children().findIndex(c => c.key === key);
    const rows = this.children().filter(c => c.key !== key);
    const old = this.current();
    if (old && old.key !== key) this.invalidate(old.key);
    // Preserve invalidation when removing an unselected row.
    const remaining = this.children().filter(c => c.key !== key);
    this.children.set(remaining);
    this.selected.set(remaining[Math.max(0, index - 1)].key);
    if (rows.length === 1 && this.groupId() === null) {
      const p = this.parent();
      this.droppedNotices.set([
        ...(nonempty(p.name) ? [`整筆名稱「${p.name}」不會保留`] : []),
        ...(nonempty(p.description) ? ['整筆備註不會保留'] : []),
      ]);
      this.parent.set({ ...p, name: '', description: '',
        merchant: nonempty(p.merchant) ? p.merchant : rows[0].loaded?.merchant ?? '' });
    }
    return true;
  }
}
```

- [ ] **Step 3 — verify/commit:** Focused tests, full suite, build; commit `feat(split): own child identity and transitions` with trailer. Add/remove UI wrappers in Task 5 must flush first; store mutation is not called directly from a pending input event.

### Task 3: Pure explicit serializer and five-row save planner

**Files:** Modify `entry-form/entry-save.ts:172–273` (`EntrySavePlan`, `SplitGroupFields`, `toSplitInput`, `planEntrySave`, `executeEntrySave`) and `:370` (`entryInputFromDetail`); tests `entry-save.spec.ts`; new `split-save.spec.ts`.

**Interfaces:** Consumes `ChildDraft`, `ParentDraft`, account lookup, resolved party IDs. Produces `SaveContext {entryId:number|null;groupId:number|null}`, `fullChild(c: ChildDraft, parent: ParentDraft, accounts: readonly LedgerAccount[], partyId: number|null, single: boolean): EntryInput`, `toSplitInput(children: readonly ChildDraft[], parent: ParentDraft, accounts: readonly LedgerAccount[], partyIds: ReadonlyMap<string,number>): SplitInput`, `planEntrySave(children, parent, accounts, partyIds, context): EntrySavePlan`. Keep `buildEntryInput`, `emptyEntryInput`, counterparty and related-load helpers unchanged except raw `entry_time` preservation.

- [ ] **Step 1 — red:** `split-save.spec.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { makeAccount } from '../testing/fixtures';
import { ChildDraft, newChild, newParent } from './split-draft';
import { planEntrySave, toSplitInput } from './split-save';

const accounts = [makeAccount()];
const child = (id: number | null = null): ChildDraft => ({ ...newChild('expense', 1), id, amountExpr: '10' });
const parent = () => newParent('2026-10-06', '10:00');
const parties = new Map<string, number>();
describe('split save contract', () => {
  it.each([
    [null, null, 1, 'create'], [7, null, 1, 'update'], [7, 4, 1, 'update-split'],
    [null, null, 2, 'create-split'], [7, null, 2, 'convert-split'], [7, 4, 2, 'update-split'],
  ] as const)('plans entry=%s group=%s count=%s as %s', (entryId, groupId, count, kind) => {
    const rows = [child(entryId), child()].slice(0, count);
    expect(planEntrySave(rows, parent(), accounts, parties, { entryId, groupId }).kind).toBe(kind);
  });
  it('preserves raw provenance and explicit empty fields', () => {
    const a = child(7), b = child(8);
    a.loaded = { entryDate: '2026-01-01', entryTime: '12:34:56.789', postedDate: '2026-01-03',
      merchant: 'legacy', attachedRuleIds: [99], originalAccountId: 1, source: 'manual',
      signedAmount: '-10', signedBase: '-10', isSettlement: false, kind: 'expense', currency: 'TWD' };
    b.loaded = { ...a.loaded, entryDate: '2026-02-02', entryTime: '03:04:05', merchant: 'other' };
    a.ruleIds = [99]; a.rulesTouched = true;
    const p = parent();
    const out = toSplitInput([a, b], p, accounts, parties);
    expect(out.members[0]).toMatchObject({ id: 7, client_key: a.key, entry_date: '2026-01-01',
      entry_time: '12:34:56.789', merchant: 'legacy', project_id: null, tags: [], reward_rule_ids: [99] });
    expect(out.members[1]).toMatchObject({ entry_date: '2026-02-02', entry_time: '03:04:05', merchant: 'other' });
    expect(toSplitInput([a, b], { ...p, entryTime: '11:11', dateTouched: true }, accounts, parties).members)
      .toEqual(expect.arrayContaining([expect.objectContaining({ id: 7, entry_time: '11:11' }),
        expect.objectContaining({ id: 8, entry_time: '11:11' })]));
  });
  it('emits only keep metadata', () => {
    const c = { ...child(7), kind: 'refund' as const, protected: true, name: 'memo' };
    expect(toSplitInput([c], parent(), accounts, parties).members).toEqual([
      { id: 7, keep: true, client_key: c.key, name: 'memo', project_id: null, tags: [], description: null },
    ]);
  });
  it('refuses conversion without its anchor and dissolution without a persisted survivor', () => {
    expect(() => planEntrySave([child(), child()], parent(), accounts, parties, { entryId: 7, groupId: null })).toThrow();
    expect(() => planEntrySave([child()], parent(), accounts, parties, { entryId: 7, groupId: 4 })).toThrow();
  });
});
```

Run `cd frontend && npm test -- --watch=false --include src/app/components/accounting/entry-form/split-save.spec.ts`; expect signature failures.

- [ ] **Step 2 — green:** Replace save-plan region; add imports `SplitResult`, `SplitMemberInput`, `ChildDraft`, `ParentDraft`, `evalOrNull`, `isWritableKind`.

```ts
export interface SaveContext { entryId: number | null; groupId: number | null }
export type EntrySavePlan =
  | { kind: 'create'; input: EntryInput }
  | { kind: 'update'; id: number; input: EntryInput }
  | { kind: 'create-split'; input: SplitInput }
  | { kind: 'convert-split'; id: number; input: SplitInput }
  | { kind: 'update-split'; groupId: number; input: SplitInput };

export function fullChild(c: ChildDraft, p: ParentDraft, accounts: readonly LedgerAccount[],
  partyId: number | null, single: boolean): EntryInput {
  if (c.protected || !isWritableKind(c.kind)) throw new Error('受保護子項只可更新備註資料');
  const account = accounts.find(a => a.id === c.accountId);
  if (!account) throw new Error('請選擇帳戶');
  const amount = evalOrNull(c.amountExpr, currencyDecimals(c.fx?.original_currency ?? account.currency));
  if (amount === null || amount <= 0) throw new Error('請輸入有效金額');
  const dates = single || p.dateTouched || !c.loaded ? p : c.loaded;
  const input = buildEntryInput({
    kind: c.kind, account, amount, fx: c.fx, categoryId: c.categoryId, counterpartyId: partyId,
    name: c.name, merchant: '', description: c.description, projectId: c.projectId, tags: [...c.tags],
    entryDate: dates.entryDate, entryTime: dates.entryTime ?? '', postedDate: dates.postedDate ?? '',
    invoiceNumber: c.invoice.number, invoiceRandom: c.invoice.random,
    fee: c.fee, discount: c.discount, ruleIds: [...c.ruleIds],
  });
  // Override sharedInput normalization: null and raw precision are contractual values here.
  return { ...input, entry_date: dates.entryDate, entry_time: dates.entryTime, posted_date: dates.postedDate,
    merchant: single ? (p.merchant.trim() || null) : c.loaded?.merchant ?? null };
}
export function toSplitInput(children: readonly ChildDraft[], p: ParentDraft,
  accounts: readonly LedgerAccount[], partyIds: ReadonlyMap<string, number>): SplitInput {
  return {
    name: p.name.trim() || null, merchant: p.merchant.trim() || null, description: p.description.trim() || null,
    entry_date: p.entryDate, entry_time: p.entryTime, posted_date: p.postedDate, project_id: null, tags: [],
    members: children.map(c => {
      if (c.protected) {
        if (c.id === null) throw new Error('受保護子項缺少記錄編號');
        return { id: c.id, keep: true as const, client_key: c.key, name: c.name.trim() || null,
          project_id: c.projectId, tags: [...c.tags], description: c.description.trim() || null };
      }
      const full: SplitMemberInput = {
        ...fullChild(c, p, accounts, partyIds.get(c.key) ?? c.counterpartyId, false), client_key: c.key,
      };
      if (c.id !== null) full.id = c.id;
      return full;
    }),
  };
}
export function planEntrySave(children: readonly ChildDraft[], parent: ParentDraft,
  accounts: readonly LedgerAccount[], partyIds: ReadonlyMap<string, number>, context: SaveContext): EntrySavePlan {
  if (children.length === 0) throw new Error('至少保留一項');
  if (context.groupId !== null) {
    if (!children.some(c => c.id !== null)) throw new Error('至少保留一項原有記錄');
    return { kind: 'update-split', groupId: context.groupId, input: toSplitInput(children, parent, accounts, partyIds) };
  }
  if (children.length === 1) {
    const c = children[0];
    const input = fullChild(c, parent, accounts, partyIds.get(c.key) ?? c.counterpartyId, true);
    return context.entryId === null ? { kind: 'create', input } : { kind: 'update', id: context.entryId, input };
  }
  const input = toSplitInput(children, parent, accounts, partyIds);
  if (context.entryId !== null) {
    if (children.filter(c => c.id === context.entryId).length !== 1 ||
      children.some(c => c.id !== null && c.id !== context.entryId)) throw new Error('原記錄不可移除或替換');
    return { kind: 'convert-split', id: context.entryId, input };
  }
  if (children.some(c => c.id !== null)) throw new Error('新增多類別不可包含既有編號');
  return { kind: 'create-split', input };
}
export function executeEntrySave(service: AccountingService, plan: EntrySavePlan): Observable<EntryDetail | SplitResult> {
  switch (plan.kind) {
    case 'create': return service.createEntry(plan.input);
    case 'update': return service.updateEntry(plan.id, plan.input);
    case 'create-split': return service.createSplit(plan.input);
    case 'update-split': return service.updateSplit(plan.groupId, plan.input);
    case 'convert-split': return service.convertEntryToSplit(plan.id, plan.input);
  }
}
```

Replace `entry_time: detail.entry_time ? detail.entry_time.slice(0, 5) : null` in `entryInputFromDetail` with `entry_time: detail.entry_time`. The new load adapter in Task 4 stores the rest of the provenance next to this input rather than forcing protected kinds into an editable DTO. Replace old unit tests that assert project/date inheritance with the explicit contract tests above; retain single/FX/fee/counterparty tests.

- [ ] **Step 3 — verify/commit:** Implement this serializer in new `split-save.ts` first, importing existing input-building helpers from `entry-save.ts`; keep the old form signature until the Tasks 4–7 vertical migration. Run focused tests, full suite/build before the pure-functions commit. Do not ship a compatibility save route that silently recreates a converted anchor.
### Task 4: Selected-child views, pending commits, async ownership and dirty identity

**Files:** Extend `split-draft.ts`; create `split-owner.spec.ts`; modify `entry-form.ts:220–257` (field signals), `:274` (`formState`), `:468` (account/category streams), `:922` (`moveToAccount`), `:1026` (sheet ownership), `:1053` (`recomputeFx`). Do not replace route-load `switchMap`/`loadId` protection.

**Interfaces:** Produces `ChildView<T> {():T;set(value:T):void;update(fn:(value:T)=>T):void}`, `childView<K extends keyof ChildDraft>(store: SplitDraftStore, field: K, fallback: ChildDraft[K], financial?: boolean): ChildView<ChildDraft[K]>`, `draftSnapshot(parent, children, scheduleDraftKey, transferDraftKey, targetExpr): string`; store `select(key: string, flush: (owner:Owner)=>boolean): boolean`. All async callbacks consume captured `Owner` and use `accept`; no callback writes through the currently selected view.

- [ ] **Step 1 — red:** `split-owner.spec.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { makeAccount } from '../testing/fixtures';
import { childView, draftSnapshot, newParent, SplitDraftStore } from './split-draft';

describe('split owner', () => {
  it('flushes before changing owner, including parent', () => {
    const s = new SplitDraftStore(newParent('2026-10-06', ''));
    const a = s.children()[0];
    const b = s.add([makeAccount()])!;
    s.select(a.key, () => true);
    const amount = childView(s, 'amountExpr', '', true);
    amount.set('10+2');
    expect(s.select(b.key, owner => s.accept(owner, { amountExpr: '12' }))).toBe(true);
    amount.set('5');
    expect(s.children().map(c => c.amountExpr)).toEqual(['12', '5']);
    s.select('parent', owner => s.accept(owner, { amountExpr: '6' }));
    amount.set('999');
    expect(s.children().map(c => c.amountExpr)).toEqual(['12', '6']);
  });
  it('drops same-value stale owners', () => {
    const s = new SplitDraftStore(newParent('2026-10-06', ''));
    const a = s.children()[0], request = s.capture()!;
    const b = s.add([makeAccount()])!;
    expect(s.accept(request, { ruleIds: [99], categoryId: 5 })).toBe(false);
    const bRequest = s.capture()!;
    s.select(a.key, () => true);
    expect(s.accept(bRequest, { fx: null, fee: { amount: '9', name: null } })).toBe(false);
    s.remove(b.key);
    expect(s.accept(bRequest, { amountExpr: '500' })).toBe(false);
    const current = s.capture()!;
    s.invalidate(current.key);
    expect(s.accept(current, { accountId: 1 })).toBe(false);
  });
  it('ignores identity, selection and online quotes but keeps mode-specific dirty state', () => {
    const s = new SplitDraftStore(newParent('2026-10-06', ''));
    const snap = () => draftSnapshot(s.parent(), s.children(), 'schedule', 'transfer', '10');
    const before = snap();
    s.invalidate(s.children()[0].key); s.selected.set('parent');
    expect(snap()).toBe(before);
    s.children.update(rows => rows.map(c => ({ ...c, ruleIds: [5], availableRules: [] })));
    expect(snap()).toBe(before);
    expect(draftSnapshot(s.parent(), s.children(), 'changed', 'transfer', '10')).not.toBe(before);
    expect(draftSnapshot(s.parent(), s.children(), 'schedule', 'changed', '10')).not.toBe(before);
    expect(draftSnapshot(s.parent(), s.children(), 'schedule', 'transfer', '11')).not.toBe(before);
    s.children.update(rows => rows.map(c => ({ ...c, name: 'user edit' })));
    expect(snap()).not.toBe(before);
  });
});
```

Run `cd frontend && npm test -- --watch=false --include src/app/components/accounting/entry-form/split-owner.spec.ts`; expect missing exports/method.

- [ ] **Step 2 — green:** Add to `split-draft.ts`:

```ts
export interface ChildView<T> {
  (): T;
  set(value: T): void;
  update(fn: (value: T) => T): void;
}
export function childView<K extends keyof ChildDraft>(store: SplitDraftStore, field: K,
  fallback: ChildDraft[K], financial = false): ChildView<ChildDraft[K]> {
  const read = (() => store.current()?.[field] ?? fallback) as ChildView<ChildDraft[K]>;
  read.set = value => {
    const c = store.current(), owner = store.capture();
    if (!c || !owner || (financial && c.protected)) return;
    store.accept(owner, { [field]: value });
  };
  read.update = fn => read.set(fn(read()));
  return read;
}
export function draftSnapshot(parent: ParentDraft, children: readonly ChildDraft[],
  scheduleDraftKey: unknown, transferDraftKey: string | null, targetExpr: string): string {
  return JSON.stringify({ parent, children: children.map(c => ({
    id: c.id, kind: c.kind, categoryId: c.categoryId, amountExpr: c.amountExpr, accountId: c.accountId,
    counterpartyName: c.counterpartyName, name: c.name, projectId: c.projectId, tags: c.tags,
    description: c.description, fee: c.fee, discount: c.discount, invoice: c.invoice,
    fx: c.fx?.use_online ? { original_amount: c.fx.original_amount, original_currency: c.fx.original_currency,
      account_currency: c.fx.account_currency, use_online: true } : c.fx,
    ruleIds: c.rulesTouched ? [...c.ruleIds].sort((a, b) => a - b) : null,
  })), scheduleDraftKey, transferDraftKey, targetExpr });
}
// Insert inside SplitDraftStore:
select(key: string, flush: (owner: Owner) => boolean): boolean {
  if (key === this.selected()) return true;
  if (key === 'parent' ? !this.isSplit() : !this.children().some(c => c.key === key)) return false;
  const owner = this.capture();
  if (owner && !flush(owner)) return false;
  if (owner) this.invalidate(owner.key);
  this.selected.set(key);
  return true;
}
```

- [ ] **Step 3 — bind the form:** Instantiate `readonly drafts = new SplitDraftStore(newParent(todayIso(), nowTime()))`. Expose `children=drafts.children`, `selected=drafts.selected`, `parent=drafts.parent`, `isSplit=drafts.isSplit`, and `groupId=drafts.groupId`. Replace financial field signals with the following views; remove duplicate definitions. `category` becomes a computed lookup by `categoryId` plus an explicit `setCategory(node)` handler; change CategoryPicker's banana binding to `[selected]="category()" (selectedChange)="setCategory($event)"` because `ChildView` is intentionally not an Angular `WritableSignal`.

```ts
readonly amountExpr = childView(this.drafts, 'amountExpr', '', true);
readonly accountId = childView(this.drafts, 'accountId', null, true);
readonly name = childView(this.drafts, 'name', '');
readonly projectId = childView(this.drafts, 'projectId', null);
readonly description = childView(this.drafts, 'description', '');
readonly tags = childView(this.drafts, 'tags', []);
readonly counterpartyName = childView(this.drafts, 'counterpartyName', '', true);
readonly fee = childView(this.drafts, 'fee', null, true);
readonly discount = childView(this.drafts, 'discount', null, true);
readonly fx = childView(this.drafts, 'fx', null, true);
readonly ruleIds = childView(this.drafts, 'ruleIds', [], true);
readonly rulesTouched = childView(this.drafts, 'rulesTouched', false, true);
readonly category = computed(() => findCategory(this.categories(), this.drafts.current()?.categoryId ?? null));
setCategory(node: CategoryNode | null): void {
  const owner = this.drafts.capture();
  if (owner && !this.drafts.current()?.protected) this.drafts.accept(owner, { categoryId: node?.id ?? null });
}
private flushChild(owner: Owner): boolean {
  const c = this.children().find(c => c.key === owner.key);
  if (!c || c.protected || !c.amountExpr.trim()) return true;
  const account = this.accounts().find(a => a.id === c.accountId);
  const value = evalOrNull(c.amountExpr, currencyDecimals(c.fx?.original_currency ?? account?.currency ?? this.preference().main_currency));
  if (value === null) { this.amountError.set(true); return false; }
  return this.drafts.accept(owner, { amountExpr: String(value) });
}
selectBubble(key: string): void {
  if (this.saving() || this.sheet()) return;
  if (this.drafts.select(key, owner => this.flushChild(owner))) {
    this.accountDetail.set(null); this.categories.set([]);
  }
}
```

`invoiceNumber`/`invoiceRandom` become computed reads of `current()?.invoice`; replace their `.set` calls with `setInvoice('number'|'random', value)` which captures owner and writes `{invoice:{...c.invoice,[field]:value}}` only for nonprotected children. Existing `originalAccountId` reads become `current()?.loaded?.originalAccountId ?? null`. `pendingCategoryId` is a per-child property; resolving it only changes the display cache, not the chosen `categoryId` or dirty baseline. For kind: retain `singleKind=signal<FormKind>('expense')` for transfer/system/definition workflows; `kind` is a writable adapter reading the selected child's actual kind in split/group mode and `singleKind` in single mode. `selectKind` updates only writable child kinds in split mode, invalidates the owner, clears `categoryId`/`pendingCategoryId`, then reopens the category grid. Protected kind labels use `ENTRY_KIND_LABELS[current.kind]`, not a forced `expense` cast. Pass `categoryKindFor` only a valid `FormKind`; protected real kinds use no editable category grid.

Parent field adapters retain the old callable `.set` API used by shared single/schedule/transfer code:

```ts
private parentView<K extends keyof ParentDraft>(field: K): ChildView<ParentDraft[K]> {
  const view = (() => this.parent()[field]) as ChildView<ParentDraft[K]>;
  view.set = value => this.parent.update(p => ({ ...p, [field]: value }));
  view.update = fn => view.set(fn(view()));
  return view;
}
readonly merchant = this.parentView('merchant');
readonly entryDate = this.parentView('entryDate');
readonly entryTime = this.parentView('entryTime');
readonly postedDate = this.parentView('postedDate');
setInvoice(field: 'number' | 'random', value: string): void {
  const c = this.drafts.current(), owner = this.drafts.capture();
  if (c && owner && !c.protected) this.drafts.accept(owner, { invoice: { ...c.invoice, [field]: value } });
}
```

Parent dates are nullable internally; the unchanged schedule/transfer `sharedFields()` uses `entryTime() ?? ''` and `postedDate() ?? ''`. Set `dateTouched` through the user date handler in Task 6, not on initial load.

- [ ] **Step 4 — async replacement:** Replace constructor account/category subscriptions with an owner-bearing selection stream. Import `mergeMap`, `catchError`, `takeUntilDestroyed`, `computed`, `toObservable`, `of`, `map` from the existing packages. Request key includes generation, even if kind/account are equal. Subscriptions are cancelled on destroy; out-of-order responses are filtered by `accept`.

```ts
const ownerState = computed(() => {
  const c = this.drafts.current();
  return c ? { owner: { key: c.key, generation: c.generation }, accountId: c.accountId, kind: c.kind } : null;
});
toObservable(ownerState).pipe(
  distinctUntilChanged((a, b) => JSON.stringify(a) === JSON.stringify(b)),
  mergeMap(state => !state || state.accountId === null ? of(null) :
    this.accounting.getAccount(state.accountId).pipe(
      map(detail => ({ ...state, detail })), catchError(() => of(null)))),
  takeUntilDestroyed(),
).subscribe(result => {
  if (!result) return;
  const c = this.children().find(c => c.key === result.owner.key);
  if (!c) return;
  const ids = rulesForDate(result.detail.reward_rules, this.parent().entryDate).filter(r => r.is_basic).map(r => r.id);
  const accepted = this.drafts.accept(result.owner, {
    availableRules: result.detail.reward_rules, ruleIds: c.rulesTouched ? c.ruleIds : ids,
  });
  if (accepted && this.selected() === result.owner.key) this.accountDetail.set(result.detail);
});
toObservable(ownerState).pipe(
  distinctUntilChanged((a, b) => JSON.stringify(a) === JSON.stringify(b)),
  mergeMap(state => !state || !isWritableKind(state.kind) ? of(null) :
    this.loadCategories(state.kind).pipe(map(list => ({ ...state, list })))),
  takeUntilDestroyed(),
).subscribe(result => {
  if (!result || !this.drafts.accept(result.owner, {})) return;
  if (this.selected() === result.owner.key) this.categories.set(result.list);
});
```

Before an account/kind/FX input change, invalidate that child's generation and capture its new owner. Keep `moveToAccount`'s existing currency-change reset logic, now operating on the selected views; it must not touch any other child. Clear displayed account/category caches immediately on selection to avoid transient wrong rules. Sheets store `sheetOwner:Owner|null` at open; replace `[(value)]="fx"`, `[(fee)]`, `[(discount)]` with explicit outputs `applySheet({fx:$event})`, `applySheet({fee:$event})`, `applySheet({discount:$event})`. `applySheet` calls `accept(sheetOwner, patch)` and never a current view; close copies original amount through the same owner before clearing it. Save callbacks and counterparty resolution use submitted owner tokens (Task 7).

```ts
private sheetOwner: Owner | null = null;
applySheet(patch: Partial<ChildDraft>): void {
  if (this.sheetOwner) this.drafts.accept(this.sheetOwner, patch);
}
// In openSheet after its existing checks:
this.sheetOwner = this.drafts.capture();
// In recomputeFx, after invalidating/capturing and constructing `online`:
const owner = this.drafts.capture();
if (!owner) return;
this.accounting.getFxRate(this.parent().entryDate, online.original_currency, online.account_currency)
  .pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
    next: result => {
      const c = this.children().find(c => c.key === owner.key);
      if (c?.fx?.use_online) this.drafts.accept(owner, {
        fx: { ...c.fx, fx_rate: String(Number(result.rate)), rate_date: result.date },
      });
    }, error: () => undefined,
  });
```

- [ ] **Step 5 — dirty integration:** `formState` uses `draftSnapshot(parent(), children(), scheduleDraftKey(scheduleDraft()), transferPanel()?.draftKey() ?? null, targetExpr())`; preserve explicit single transfer/system kind in the outer JSON (`singleKind()`), since changing a system tab is a user edit. Keep `baseline`, `markClean`, discard focus and pending-discard clearing at their existing load/save/continue boundaries. Initial route load sets baseline only after related rows are applied. The new async streams above update only snapshot-excluded presentation/rule-default fields; online quote rate/date are excluded. Synchronous category last-use defaults occur inside the user's category-pick operation and therefore remain dirty as part of that user edit. No async `markClean()` call is added.

- [ ] **Step 6 — verify/commit:** Run `split-owner.spec.ts`, the existing `entry-form.spec.ts` and `entry-form-schedule.spec.ts`, full suite/build. Commit `refactor(split): bind draft views by owner` with trailer once Task 5 load adapters compile; the form migration is a single vertical commit including Task 7 save wiring, as specified in the commit map at the end.

### Task 5: Load provenance, add availability and field-map integration

**Files:** New `entry-form/split-load.ts`, `split-load.spec.ts`; modify `entry-form.ts:723` (`applyLoaded`), `:757` (`applyDetail`), `:834` (`tabDisabled`), `:889` (`onCategoryPicked`), `:653` (`resetFields`), `:1549` (`continueEntry`).

**Interfaces:** Produces `childFromDetail(detail: EntryDetail, flag: Pick<SplitGroupMember,'protected'|'protected_reason'>, mainCurrency: string): ChildDraft`, `splitFromDetails(opened: EntryDetail, related: EntryDetail[], mainCurrency:string): {children:ChildDraft[];parent:ParentDraft;selected:string}`, `addAvailability(mode: AddMode): {visible:boolean;disabled:boolean;hint:string|null}`. Consumes existing `loadRelatedRows`/`RelatedLoad`; order must come from `opened.group_members`, never GET completion order.

- [ ] **Step 1 — red:** `split-load.spec.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { makeEntryDetail } from '../testing/fixtures';
import { addAvailability, splitFromDetails } from './split-load';

it('loads group order, opened selection, raw times and each protected flag', () => {
  const first = makeEntryDetail({ id: 7, entry_time: '01:02:03.456', merchant: 'first', amount: '-20' });
  const opened = makeEntryDetail({ id: 8, merchant: 'second', amount: '5', kind: 'refund',
    group: { id: 4, kind: 'split', name: 'group', merchant: 'parent', description: 'note', count: 2, total: '-15', currency: 'TWD' },
    group_members: [{ ...first, protected: false, protected_reason: null },
      { ...makeEntryDetail({ id: 8, kind: 'refund' }), protected: true, protected_reason: 'refund' }] });
  const state = splitFromDetails(opened, [first], 'TWD');
  expect(state.children.map(c => c.id)).toEqual([7, 8]);
  expect(state.selected).toBe(state.children[1].key);
  expect(state.parent).toMatchObject({ merchant: 'parent', entryTime: '01:02:03.456', dateTouched: false });
  expect(state.children[1]).toMatchObject({ protected: true, rulesTouched: true,
    loaded: { merchant: 'second', signedAmount: '5', kind: 'refund' } });
});
describe('add mode matrix', () => {
  const mode = { split: false, count: 1, scheduleId: null, eventTab: 'single', editing: false,
    kind: 'expense', protected: false, source: 'manual', locked: false };
  it.each([
    [{}, true, false], [{ eventTab: 'recurring' }, false, false], [{ eventTab: 'installment' }, false, false],
    [{ scheduleId: 4 }, false, false], [{ editing: true }, true, false],
    [{ kind: 'transfer' }, false, false], [{ kind: 'system' }, false, false],
    [{ source: 'schedule' }, false, false], [{ protected: true }, false, false], [{ locked: true }, false, false],
    [{ split: true, count: 2, protected: true, kind: 'refund' }, true, false],
    [{ split: true, count: 50 }, true, true], [{ split: true, count: 51 }, true, true],
  ])('availability %j', (patch, visible, disabled) => {
    expect(addAvailability({ ...mode, ...patch })).toMatchObject({ visible, disabled });
  });
});
```

Run `cd frontend && npm test -- --watch=false --include src/app/components/accounting/entry-form/split-load.spec.ts` → missing module.

- [ ] **Step 2 — green:** New module (imports listed below):

```ts
import { EntryDetail, SplitGroupMember } from '../../../models/accounting.model';
import { fxFromDetail, feeAndDiscountFromDetail } from './entry-save';
import { newChild, ChildDraft, ParentDraft } from './split-draft';
import { isWritableKind } from './entry-draft';
export function childFromDetail(d: EntryDetail, flag: Pick<SplitGroupMember, 'protected' | 'protected_reason'>,
  mainCurrency: string): ChildDraft {
  const input = feeAndDiscountFromDetail(d), fx = fxFromDetail(d);
  return {
    ...newChild(d.kind, d.account_id), id: d.id, protected: flag.protected, protectedReason: flag.protected_reason,
    categoryId: d.category_id, pendingCategoryId: d.category_id, amountExpr: fx?.original_amount ?? String(Math.abs(Number(d.amount))),
    counterpartyId: d.counterparty_id, counterpartyName: d.counterparty ?? '', name: d.name ?? '',
    projectId: d.project_id, tags: [...d.tags], description: d.description ?? '', fee: input.fee, discount: input.discount,
    fx, ruleIds: d.rules.map(r => r.id), rulesTouched: true, availableRules: [...d.rules],
    invoice: { number: d.invoice_number ?? '', random: d.invoice_random ?? '' },
    loaded: { entryDate: d.entry_date, entryTime: d.entry_time, postedDate: d.posted_date, merchant: d.merchant,
      attachedRuleIds: d.rules.map(r => r.id), originalAccountId: d.account_id, source: d.source,
      signedAmount: d.amount, signedBase: d.currency === mainCurrency ? d.amount : null,
      isSettlement: d.is_settlement, kind: d.kind, currency: d.currency },
  };
}
export function splitFromDetails(opened: EntryDetail, related: EntryDetail[], mainCurrency: string):
  { children: ChildDraft[]; parent: ParentDraft; selected: string } {
  const details = new Map([opened, ...related].map(d => [d.id, d]));
  const group = opened.group;
  if (!group || group.kind !== 'split') throw new Error('不是多類別記錄');
  const children = opened.group_members.map(member => {
    const detail = details.get(member.id);
    if (!detail) throw new Error('子項載入不完整');
    return childFromDetail(detail, member, mainCurrency);
  });
  const first = children[0]?.loaded, selected = children.find(c => c.id === opened.id);
  if (!first || !selected) throw new Error('子項載入不完整');
  return { children, selected: selected.key, parent: {
    name: group.name ?? '', merchant: group.merchant ?? '', description: group.description ?? '',
    entryDate: first.entryDate, entryTime: first.entryTime, postedDate: first.postedDate, dateTouched: false,
  } };
}
export interface AddMode {
  split: boolean; count: number; scheduleId: number | null; eventTab: string; editing: boolean;
  kind: string; protected: boolean; source: string; locked: boolean;
}
export function addAvailability(m: AddMode): { visible: boolean; disabled: boolean; hint: string | null } {
  if (m.scheduleId !== null || (!m.editing && m.eventTab !== 'single')) return { visible: false, disabled: false, hint: null };
  if (m.locked) return { visible: false, disabled: false, hint: '此記錄不能拆帳' };
  if (!m.split && (!isWritableKind(m.kind) || m.protected || m.source === 'schedule'))
    return { visible: false, disabled: false, hint: '此記錄不能拆帳' };
  return { visible: true, disabled: m.count >= 50, hint: m.count >= 50 ? '最多 50 項' : null };
}
```

Protected rows never reach `fullChild` or `entryInputFromDetail`. Add this extractor to `entry-save.ts` beside existing `childFrom`:

```ts
export function feeAndDiscountFromDetail(detail: EntryDetail): { fee: ChildInput | null; discount: ChildInput | null } {
  return { fee: childFrom(detail.children ?? [], 'fee'), discount: childFrom(detail.children ?? [], 'discount') };
}
```

- [ ] **Step 3 — apply after all GETs:** In `applyLoaded`, before old `applyDetail`, branch on `!copy && group.kind==='split'`. Use the block below; otherwise initialize one `childFromDetail` and parent from the single record, retaining existing transfer copy/definition paths. The single protected predicate is `is_settlement || !isWritableKind(kind) || transfer_group_id !== null || settled_by.length>0 || refunded_by.length>0 || loan_schedule != null`; the server remains authoritative at convert. A locked single cannot add. Copies clear `id`, `loaded`, attached rules according to today's copy behavior, and anchor/group ids; they never accidentally convert their source.

```ts
if (!loaded.copy && loaded.detail.group?.kind === 'split') {
  const state = splitFromDetails(loaded.detail, loaded.related.members, this.preference().main_currency);
  this.children.set(state.children); this.parent.set(state.parent); this.selected.set(state.selected);
  this.groupId.set(loaded.detail.group.id); this.drafts.anchorId.set(null);
  this.locked.set(loaded.detail.locked); this.unsupported.set(null);
  this.loading.set(false); this.markClean();
  return;
}
```

Archived account options use the selected child's `loaded.originalAccountId` and include that original only; switching between two archived originals must swap the retained option. Loaded rules are never filtered down to currently offered enabled/date-valid rules at serialization; selecting a new rule still uses `rulesForDate`. New-account basic defaults do not replace attached disabled rules on a loaded child.

Form wrappers (add `readonly categoryPicker=viewChild(CategoryPickerComponent)`):

```ts
addChild(): void {
  const a = this.addState();
  if (!a.visible || a.disabled || this.saving() || this.sheet()) return;
  const owner = this.drafts.capture();
  if (owner && !this.flushChild(owner)) return;
  if (!this.drafts.add(this.accounts())) return;
  afterNextRender(() => this.categoryPicker()?.reopen(), { injector: this.injector });
}
removeChild(): void {
  const c = this.drafts.current(), owner = this.drafts.capture();
  if (!c || !owner || this.saving() || !this.flushChild(owner)) return;
  this.drafts.remove(c.key);
}
onCategoryPicked(node: CategoryNode): void {
  this.setCategory(node); this.error.set(null);
  if (!this.isSplit() && this.groupId() === null && this.entryId() === null) {
    const last = readLastUse(node.id);
    const candidate = last?.account_id ?? node.default_account_id;
    if (this.accounts().some(a => a.id === candidate && !a.is_archived)) this.moveToAccount(candidate);
    this.projectId.set(last ? last.project_id : node.default_project_id);
  }
  afterNextRender(() => {
    const host = this.host.nativeElement;
    (host.querySelector<HTMLElement>('.amount-input') ?? host.querySelector<HTMLElement>('.amount-value'))?.focus();
  }, { injector: this.injector });
}
```

The amount value gets `tabindex="-1"` on phone. `addState` is a computed call to `addAvailability` with current schedule, entry source/protection/lock, child count and single kind. After removal, single layout selects the survivor and still retains `groupId` so saving uses dissolve.

- [ ] **Step 4 — verify:** Focused load tests plus component tests for opened protected child and two archived originals; full suite/build before the vertical integration commit. Reset/continue initializes a fresh UUID child and new parent, clears `anchorId/groupId/droppedNotices/sheetOwner`, then calls the existing clean/reset flow. Do not clear the transfer panel's own draft key or schedule key from dirty registration.
### Task 6: Bubble strip, child tiles, parent totals and notices

**Files:** New `entry-form/split-summary.ts`, `split-summary.spec.ts`; modify `category-picker.ts:41`, `category-picker.html:1`, `category-picker.scss` (strip); `entry-form.html:90` (kind tabs), `:166` (picker/old lines), `:194` (tiles), `:398` (keypad), `entry-form.scss`; tests `category-picker.spec.ts:98`, `entry-form.spec.ts`.

**Interfaces:** Produces `CurrencyNet {currency:string;amount:number|null}`, `netByCurrency(children, accounts): CurrencyNet[]`, `dissolveNotices(parent, survivor): string[]`, `rewardEstimates(children, accounts): CurrencyNet[]`. Picker consumes `bubbles: Bubble[]`, `activeBubble:string|null`, `parentText:string|null`, `addVisible:boolean`, `addDisabled:boolean`, `addHint:string|null`, `gridAllowed:boolean`; emits `bubbleSelected:string`, `addChild:void`. `Bubble` is `{key:string;label:string;icon:string;color:string;amount:string;empty:boolean;protected:boolean}`. Keep existing `selected`, `picked`, drill-in keyboard events and `stripExtra` projection for non-form callers.

- [ ] **Step 1 — red:** New summary tests:

```ts
import { describe, expect, it } from 'vitest';
import { makeAccount, makeEntryDetail, makeRule } from '../testing/fixtures';
import { childFromDetail } from './split-load';
import { ChildDraft, newChild, newParent } from './split-draft';
import { dissolveNotices, netByCurrency, rewardEstimates } from './split-summary';

describe('split summary', () => {
  it('uses stored signs and account currencies', () => {
    const row = (kind: 'receivable' | 'payable' | 'balance_adjustment', amount: string) => childFromDetail(
      makeEntryDetail({ kind, amount }), { protected: true, protected_reason: 'test' }, 'TWD');
    const fx = { ...newChild('expense', 2), amountExpr: '1000',
      fx: { original_amount: '1000', original_currency: 'JPY', account_currency: 'USD',
        amount: '6.7', fx_rate: null, use_online: false, manual: 'amount' as const, rate_date: null } };
    const rows = [row('receivable', '-100'), row('receivable', '40'), row('payable', '80'),
      row('payable', '-30'), row('balance_adjustment', '-7'), row('balance_adjustment', '2'), fx];
    expect(netByCurrency(rows, [makeAccount(), makeAccount({ id: 2, currency: 'USD' })]))
      .toEqual([{ currency: 'TWD', amount: -15 }, { currency: 'USD', amount: -6.7 }]);
  });
  it('keeps unknown FX unknown and estimates only selected rules', () => {
    const c: ChildDraft = { ...newChild('expense', 1), amountExpr: '100', ruleIds: [3],
      availableRules: [makeRule({ id: 3, rate: '2' })] };
    expect(rewardEstimates([c], [makeAccount()])).toEqual([{ currency: 'TWD', amount: 2 }]);
    c.fx = { original_amount: '100', original_currency: 'USD', account_currency: 'TWD',
      amount: null, fx_rate: null, use_online: true, manual: null, rate_date: null };
    expect(netByCurrency([c], [makeAccount()])).toEqual([{ currency: 'TWD', amount: null }]);
  });
  it('shows each copy-skipped field, treating whitespace as empty', () => {
    const p = { ...newParent('2026-10-06', ''), name: 'Group', merchant: 'Shop', description: 'Note' };
    const c = childFromDetail(makeEntryDetail({ name: 'child', merchant: '   ', description: 'child note' }),
      { protected: false, protected_reason: null }, 'TWD');
    expect(dissolveNotices(p, c)).toEqual(['整筆名稱「Group」將不保留', '整筆備註將不保留']);
    c.name = ' ';
    expect(dissolveNotices(p, c)).toEqual(['整筆備註將不保留']);
  });
});
```

Run `cd frontend && npm test -- --watch=false --include src/app/components/accounting/entry-form/split-summary.spec.ts` → missing module.

- [ ] **Step 2 — green:** `split-summary.ts`:

```ts
import { LedgerAccount } from '../../../models/accounting.model';
import { roundHalfAway } from '../amount-math';
import { currencyDecimals } from '../format';
import { evalOrNull, isWritableKind, signFor } from './entry-draft';
import { ChildDraft, ParentDraft, nonempty } from './split-draft';
export interface CurrencyNet { currency: string; amount: number | null }
export function accountAmount(c: ChildDraft, account: LedgerAccount | undefined): number | null {
  if (c.protected || !isWritableKind(c.kind)) return c.loaded ? Number(c.loaded.signedAmount) : null;
  if (!account) return null;
  const amount = evalOrNull(c.amountExpr, currencyDecimals(c.fx?.original_currency ?? account.currency));
  if (amount === null) return null;
  let converted = amount;
  if (c.fx) {
    if (!c.fx.use_online && c.fx.manual === 'amount' && c.fx.amount !== null) converted = Number(c.fx.amount);
    else if (c.fx.fx_rate !== null) converted = amount * Number(c.fx.fx_rate);
    else return null;
  }
  return signFor(c.kind) * roundHalfAway(converted, currencyDecimals(account.currency));
}
function sum(lines: CurrencyNet[]): CurrencyNet[] {
  const values = new Map<string, number | null>();
  for (const line of lines) {
    const previous = values.get(line.currency) ?? 0;
    const unknown = values.has(line.currency) && values.get(line.currency) === null;
    values.set(line.currency, unknown || line.amount === null ? null : previous + line.amount);
  }
  return [...values].map(([currency, amount]) => ({ currency,
    amount: amount === null ? null : roundHalfAway(amount, currencyDecimals(currency)) }));
}
export function netByCurrency(children: readonly ChildDraft[], accounts: readonly LedgerAccount[]): CurrencyNet[] {
  return sum(children.map(c => {
    const account = accounts.find(a => a.id === c.accountId);
    return { currency: account?.currency ?? c.loaded?.currency ?? '—', amount: accountAmount(c, account) };
  }));
}
export function rewardEstimates(children: readonly ChildDraft[], accounts: readonly LedgerAccount[]): CurrencyNet[] {
  return sum(children.flatMap(c => {
    const account = accounts.find(a => a.id === c.accountId);
    if (!account) return [];
    const amount = accountAmount(c, account);
    return c.availableRules.filter(rule => c.ruleIds.includes(rule.id)).map(rule => {
      let reward = rule.method === 'fixed' ? Number(rule.fixed_amount ?? 0) :
        amount === null ? null : Math.abs(amount) * Number(rule.rate ?? 0) / 100;
      if (reward !== null) {
        if (rule.txn_rounding === 'floor') reward = Math.floor(reward);
        if (rule.txn_rounding === 'ceil') reward = Math.ceil(reward);
        if (rule.txn_rounding === 'round') reward = roundHalfAway(reward, 0);
      }
      return { currency: account.currency, amount: reward };
    });
  }));
}
export function dissolveNotices(p: ParentDraft, c: ChildDraft): string[] {
  return [
    ...(nonempty(p.name) && nonempty(c.name) ? [`整筆名稱「${p.name}」將不保留`] : []),
    ...(nonempty(p.merchant) && nonempty(c.loaded?.merchant) ? [`整筆商家「${p.merchant}」將不保留`] : []),
    ...(nonempty(p.description) && nonempty(c.description) ? ['整筆備註將不保留'] : []),
  ];
}
```

- [ ] **Step 3 — picker red:** Replace the old addLine/splittable spec, retain all PR-5 keyboard tests. Add this test using its existing `render` helper:

```ts
it('renders outlined, empty and locked bubbles and caps add', () => {
  const { el, component, fixture } = render({ activeBubble: 'b', parentText: '多類別 TWD -10 (2)',
    bubbles: [
      { key: 'a', label: '午餐', icon: '🍜', color: '#fff', amount: '-10', empty: false, protected: true },
      { key: 'b', label: '未選類別', icon: '○', color: '#fff', amount: '0', empty: true, protected: false },
    ], addVisible: true, addDisabled: true, addHint: '最多 50 項' });
  expect(el.querySelector('[data-bubble="b"]')?.classList.contains('empty')).toBe(true);
  expect(el.querySelector('[data-bubble="b"]')?.getAttribute('aria-pressed')).toBe('true');
  expect(el.querySelector('[data-bubble="a"]')?.textContent).toContain('🔒');
  expect((el.querySelector('.add') as HTMLButtonElement).disabled).toBe(true);
  const selected: string[] = [];
  component.bubbleSelected.subscribe(key => selected.push(key));
  (el.querySelector('[data-bubble="parent"]') as HTMLButtonElement).click();
  fixture.detectChanges();
  expect(selected).toEqual(['parent']);
});
```

- [ ] **Step 4 — picker green:** Remove `splittable/addLine`; add these inputs/outputs and event method:

```ts
export interface Bubble {
  key: string; label: string; icon: string; color: string; amount: string; empty: boolean; protected: boolean;
}
// Inside CategoryPickerComponent:
readonly bubbles = input<Bubble[]>([]);
readonly activeBubble = input<string | null>(null);
readonly parentText = input<string | null>(null);
readonly addVisible = input(false);
readonly addDisabled = input(false);
readonly addHint = input<string | null>(null);
readonly gridAllowed = input(true);
readonly bubbleSelected = output<string>();
readonly addChild = output<void>();
chooseBubble(key: string): void {
  if (key === this.activeBubble() && key !== 'parent' && this.gridAllowed()) this.reopen();
  else this.bubbleSelected.emit(key);
}
```

Prepend this strip to `category-picker.html`; for callers with no bubbles retain the existing `.sel` strip. Remove its old `splittable` block. Wrap grid branches in `@if (gridAllowed())`; while `activeBubble==='parent'` or a protected child is selected the form sets `gridAllowed=false`. Keep strip visible while grid is open for switching children. On switching bubbles, reset `openParent/reopened` through an effect on `activeBubble` so a previous child's drill-in is never inherited. Reopening a selected bubble goes to main grid. In the Escape branch change the focus selector to `'[data-bubble][aria-pressed="true"], .strip .sel'`.

```html
@if (bubbles().length) {
  <div class="strip bubbles" aria-label="多類別子項">
    @if (parentText(); as text) {
      <button type="button" class="sel parent-bubble" data-bubble="parent"
        [attr.aria-pressed]="activeBubble() === 'parent'" (click)="chooseBubble('parent')">{{ text }}</button>
    }
    @for (bubble of bubbles(); track bubble.key) {
      <button type="button" class="sel" [class.empty]="bubble.empty" [attr.data-bubble]="bubble.key"
        [attr.aria-pressed]="activeBubble() === bubble.key" (click)="chooseBubble(bubble.key)">
        <span class="ico" [style.background]="bubble.color">{{ bubble.icon }}</span>
        <span class="sel-text"><b>{{ bubble.label }}</b><small>{{ bubble.amount }}</small></span>
        @if (bubble.protected) { <span aria-label="受保護">🔒</span> }
      </button>
    }
    @if (addVisible()) {
      <button type="button" class="add" aria-label="新增子項" [disabled]="addDisabled()" (click)="addChild.emit()">＋</button>
    }
    @if (addHint(); as hint) { <span class="hint">{{ hint }}</span> }
  </div>
}
```

```scss
.bubbles { display: flex; gap: .5rem; overflow-x: auto; align-items: center; padding: .35rem; }
.bubbles .sel { flex: 0 0 auto; }
.bubbles .sel[aria-pressed='true'] { outline: 2px solid currentColor; outline-offset: 1px; }
.bubbles .empty { border-style: dashed; }
.bubbles .hint { min-width: max-content; font-size: .75rem; }
```

Avoid duplicate selected strips: the old `@if (strip(); as sel)` renders only when `bubbles().length===0`; the grid renders when `!strip()` and `gridAllowed()`. A form child with no category always reaches main grid; an unselected main Escape continues bubbling to form as before.

- [ ] **Step 5 — parent/child integration:** Add computed `parentMode = selected()==='parent'`, `protectedChild = current()?.protected ?? false`, `dateLocked = children().some(c=>c.protected)`, `summary=netByCurrency`, `rewards=rewardEstimates`, distinct non-null `accountCount`, and `mixedDates` comparing loaded triples. Produce each `Bubble` from its child's category cache keyed by kind, falling back to `ENTRY_KIND_LABELS[kind]` and `defaultCategoryIcon`; no category uses `未選類別`, `○`, `empty:true`; amount comes from `accountAmount`, formatting account currency only. Cache every loaded category label/icon/color when loading group_members so unvisited kinds do not lose their bubble labels. Parent text joins per-currency amounts and `(N)`.

In the form template: category picker goes above the transfer/system switch so hidden-plus hints remain visible in forbidden single modes. Bind `[bubbles]="bubbles()" [activeBubble]="selected()" [parentText]="isSplit() ? parentText() : null" [addVisible]="addState().visible" [addDisabled]="addState().disabled" [addHint]="addState().hint" [gridAllowed]="!parentMode() && !protectedChild() && kind() !== 'transfer' && !isSystem()" (bubbleSelected)="selectBubble($event)" (addChild)="addChild()"`. Hide kind tabs in parent mode; disable transfer/system tabs in split mode and every tab for a protected child. Show the protected child's actual kind/reason above financial fields.

Replace the old `app-split-lines` block with no content; child controls now edit selected views. Wrap the existing ordinary tiles/chips/textarea in `@if (!parentMode())`. Financial controls (amount, account, category grid, counterparty, invoice, fee, FX, reward buttons) get `[disabled]="protectedChild()"` individually; metadata controls remain enabled. Do not disable the whole child fieldset. Protected phone amount is read-only; hide its keypad. Display loaded non-empty child merchant below the note as `商家（此項）`. Remove merchant/date/time/event row/schedule block from split child mode by gating on `!isSplit()`; never hide them from single schedule/transfer mode. An existing group reduced to one still uses group parent merchant/date controls and `dateLocked` even though it uses single layout.

```html
@if (parentMode()) {
  <section class="split-parent" aria-label="多類別摘要">
    @for (line of summary(); track line.currency) {
      <p class="split-net">{{ line.currency }} {{ line.amount === null ? '—' : line.amount }}</p>
    }
    <p>{{ children().length }} 項 · {{ accountCount() }} 個帳戶</p>
    @for (line of rewards(); track line.currency) {
      <p>預估回饋 {{ line.currency }} {{ line.amount === null ? '—' : line.amount }}</p>
    }
    <label class="tile">商家<input aria-label="整筆商家" [value]="parent().merchant" (input)="setParentText('merchant', $any($event.target).value)" /></label>
    <label class="tile">日期<input type="date" aria-label="整筆日期" [disabled]="dateLocked()" [value]="parent().entryDate" (change)="setParentDate('entryDate', $any($event.target).value)" /></label>
    <label class="tile">時間<input type="time" step="1" aria-label="整筆時間" [disabled]="dateLocked()" [value]="parent().entryTime" (change)="setParentDate('entryTime', $any($event.target).value)" /></label>
    <label class="tile">入帳日<input type="date" aria-label="整筆入帳日" [disabled]="dateLocked()" [value]="parent().postedDate" (change)="setParentDate('postedDate', $any($event.target).value)" /></label>
    @if (dateLocked()) { <p>含受保護子項，日期不可更改</p> }
    @if (mixedDates()) { <p>子項日期不一致</p> }
    <label class="tile">整筆名稱<input aria-label="整筆名稱" [value]="parent().name" (input)="setParentText('name', $any($event.target).value)" /></label>
    <label class="tile">整筆備註<textarea aria-label="整筆備註" [value]="parent().description" (input)="setParentText('description', $any($event.target).value)"></textarea></label>
    @if (groupId() !== null) { <button type="button" class="delete-group" (click)="deleteGroupPrompt.set(true)">刪除整組</button> }
  </section>
}
@if (!parentMode()) {
  @for (message of removalNotices(); track message) { <p class="form-notice" role="status">{{ message }}</p> }
  <button type="button" class="remove-child" [disabled]="removeReason() !== null" (click)="removeChild()">移除此項</button>
  @if (removeReason(); as reason) { <p class="remove-hint">{{ reason }}</p> }
}
```

```ts
setParentText(field: 'name' | 'merchant' | 'description', value: string): void {
  this.parent.update(p => ({ ...p, [field]: value }));
}
setParentDate(field: 'entryDate' | 'entryTime' | 'postedDate', value: string): void {
  if (this.dateLocked()) return;
  this.parent.update(p => ({ ...p, [field]: field === 'entryDate' ? value : value || null, dateTouched: true }));
  // Invalidate all date-dependent FX/rule requests; current owner's effect restarts with its new generation.
  for (const c of this.children()) this.drafts.invalidate(c.key);
}
readonly removalNotices = computed(() => this.isSplit() ? [] : this.groupId() !== null
  ? dissolveNotices(this.parent(), this.children()[0]) : this.drafts.droppedNotices());
readonly removeReason = computed(() => this.drafts.current()
  ? this.drafts.removeReason(this.drafts.current()!.key) : null);
```

Replace all user date input handlers, including single mode, with `setParentDate`. During initial load use `parent.set` directly. Keypad visibility additionally requires `!parentMode() && !protectedChild()`; use explicit `[value]="amountExpr()" (valueChange)="amountExpr.set($event)"`, so ownership is changed only by synchronous form handlers. The footer must use selected-child amounts only in child mode, summary only in parent mode. A switch cannot execute the system/transfer save path because its dispatch checks mode before single kind.

Delete-group confirmation uses existing `data-overlay`, `trapFocus`, handled Escape and focus restoration utilities. Add `deleteGroupPrompt=signal(false)`; on confirmation call existing `AccountingService.deleteSplit(groupId)` through `write`, then `leave`. Cancel/Escape closes only this prompt. Parent-mode form Escape still reaches existing `cancel()` and DirtyFormRegistry.

- [ ] **Step 6 — verify/commit:** Focused summary/picker/form suites; full suite/build. Check at all three requested viewports for strip scrolling, readable dates and disabled hints. Include in the vertical form commit; do not claim screenshots until captured from the actual build.

### Task 7: Save every child, map keys, retain dirty errors and navigate

**Files:** Modify `entry-form.ts:1162` (`validate`), `:1203–1306` (`save`), `:1467` (`write`); new `entry-form/split-result.ts`, `split-result.spec.ts`; component HTTP assertions in `entry-form.spec.ts`.

**Interfaces:** Produces `resultIds(result:SplitResult, submitted:readonly ChildDraft[]): Map<string,number>`, `memberError(error:unknown, keys:readonly string[]): {key:string;field:string;message:string}|null`. Save consumes immutable submitted children/parent/selected key/loadId, resolves each editable party, and calls Task 3 planner. Existing single-entry proposed-fee prompt stays single-only. `rememberEntryUse` is never called for a split.

- [ ] **Step 1 — red:** `split-result.spec.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { newChild } from './split-draft';
import { memberError, resultIds } from './split-result';

describe('split results', () => {
  it('maps by client key even when response array differs', () => {
    const a = newChild(), b = newChild();
    const ids = resultIds({ group_id: 4, member_ids: [7, 8], members: [
      { id: 8, client_key: b.key }, { id: 7, client_key: a.key },
    ] }, [a, b]);
    expect(ids.get(a.key)).toBe(7); expect(ids.get(b.key)).toBe(8);
  });
  it('routes indexed errors using submitted keys', () => {
    expect(memberError({ error: { detail: [{ loc: ['body', 'members', 1, 'amount'], msg: 'must be positive' }] } }, ['a', 'b']))
      .toEqual({ key: 'b', field: 'amount', message: 'must be positive' });
    expect(memberError({ error: { detail: { 'members.1.amount': 'invalid' } } }, ['a', 'b']))
      .toEqual({ key: 'b', field: 'amount', message: 'invalid' });
    expect(memberError({ error: { detail: 'retry' } }, ['a', 'b'])).toBeNull();
  });
  it('rejects missing/duplicate keys instead of navigating to a wrong row', () => {
    const a = newChild();
    expect(() => resultIds({ group_id: null, member_ids: [7], members: [{ id: 7, client_key: null }] }, [a])).toThrow();
  });
});
```

Run `cd frontend && npm test -- --watch=false --include src/app/components/accounting/entry-form/split-result.spec.ts` → missing module.

- [ ] **Step 2 — green:** `split-result.ts` (imports `SplitResult`, `ChildDraft`):

```ts
export function resultIds(result: SplitResult, submitted: readonly ChildDraft[]): Map<string, number> {
  const ids = new Map<string, number>();
  for (const member of result.members) {
    if (member.client_key === null || ids.has(member.client_key)) throw new Error('儲存回應缺少子項對應，請重新載入');
    ids.set(member.client_key, member.id);
  }
  if (ids.size !== submitted.length || submitted.some(c => !ids.has(c.key)))
    throw new Error('儲存回應缺少子項對應，請重新載入');
  return ids;
}
export function memberError(error: unknown, keys: readonly string[]): { key: string; field: string; message: string } | null {
  if (typeof error !== 'object' || error === null || !('error' in error)) return null;
  const body = error.error;
  if (typeof body !== 'object' || body === null || !('detail' in body)) return null;
  const detail = body.detail;
  const entries: Array<[string, string]> = [];
  if (Array.isArray(detail)) {
    for (const item of detail) if (item && Array.isArray(item.loc)) entries.push([item.loc.join('.'), String(item.msg)]);
  } else if (detail && typeof detail === 'object') {
    for (const [field, message] of Object.entries(detail)) entries.push([field, String(message)]);
  }
  for (const [path, message] of entries) {
    const match = /(?:^|\.)members\.(\d+)\.(.+)$/.exec(path);
    const key = match ? keys[Number(match[1])] : undefined;
    if (match && key !== undefined) return { key, field: match[2], message };
  }
  return null;
}
```

- [ ] **Step 3 — replace single/split branch:** Leave schedule, transfer, system branches intact, but enter them only when `!isSplit() && groupId()===null` (definition edit remains its earlier branch). Replace the old `splitGroup/members` save branch with a `saveChildren(continuous)` method. Before it sets `saving=true`, validate all editable children with `fullChild` (parties may be unresolved; check non-empty counterparty name separately); on the first failure select that child and show its error. Do not validate only the currently selected/parent view. Disable form fieldset, bubble/add/remove and all sheet changes while saving so submitted data cannot drift.

```ts
private saveChildren(continuous: boolean): void {
  const owner = this.drafts.capture();
  if (owner && !this.flushChild(owner)) return;
  const submitted = structuredClone(this.children());
  const parent = structuredClone(this.parent());
  const selected = this.selected();
  const loadId = this.loadId;
  const context = { entryId: this.entryId(), groupId: this.groupId() };
  const owners = submitted.map(c => ({ key: c.key, generation: c.generation }));
  const keepGoing = continuous && context.entryId === null && context.groupId === null;
  for (const c of submitted) {
    if (c.protected) continue;
    try {
      fullChild(c, parent, this.accounts(), c.counterpartyId, submitted.length === 1 && context.groupId === null);
      if ((c.kind === 'receivable' || c.kind === 'payable') && !c.counterpartyName.trim()) throw new Error('請輸入對象');
    } catch (error) {
      this.drafts.select(c.key, () => true);
      this.error.set(error instanceof Error ? error.message : '請檢查子項');
      return;
    }
  }
  this.saving.set(true); this.error.set(null);
  const resolutions = submitted.map(c => c.protected || (c.kind !== 'receivable' && c.kind !== 'payable')
    ? of([c.key, null] as const)
    : resolveCounterpartyId(this.accounting, c.counterpartyName, this.counterparties(), party => {
        if (this.loadId === loadId && owners.every(o => this.children().some(c => c.key === o.key && c.generation === o.generation)))
          this.counterparties.update(rows => rows.some(p => p.id === party.id) ? rows : [...rows, party]);
      }).pipe(map(id => [c.key, id] as const)));
  forkJoin(resolutions).pipe(
    map(pairs => new Map(pairs.filter((pair): pair is readonly [string, number] => pair[1] !== null))),
    switchMap(parties => {
      if (this.loadId !== loadId || !owners.every(o => this.children().some(c => c.key === o.key && c.generation === o.generation))) return EMPTY;
      const plan = planEntrySave(submitted, parent, this.accounts(), parties, context);
      return executeEntrySave(this.accounting, plan).pipe(map(result => ({ plan, result })));
    }), takeUntilDestroyed(this.destroyRef),
    finalize(() => { if (this.loadId === loadId) this.saving.set(false); }),
  ).subscribe({
    next: ({ plan, result }) => {
      if (this.loadId !== loadId) return;
      this.saving.set(false);
      rememberRecentAccounts(submitted.map(c => c.accountId));
      if (plan.kind === 'create' || plan.kind === 'update') {
        const detail = result as EntryDetail;
        rememberEntryUse(plan.input, Number(plan.input.original_amount ?? plan.input.amount));
        this.markClean();
        if (detail.proposed_fee && Number(detail.proposed_fee) > 0 && !plan.input.fee) {
          this.feeProposal.set({ entryId: detail.id, amount: detail.proposed_fee, input: plan.input, continuous: keepGoing });
          return;
        }
        this.finish(keepGoing); return;
      }
      let ids: Map<string, number>;
      try { ids = resultIds(result as SplitResult, submitted); }
      catch (error) {
        // The server may have committed. Do not offer an automatic POST retry.
        this.error.set(error instanceof Error ? error.message : '請重新載入');
        this.saveResponseInvalid.set(true); return;
      }
      this.children.update(rows => rows.map(c => ({ ...c, id: ids.get(c.key) ?? c.id })));
      this.groupId.set((result as SplitResult).group_id);
      this.drafts.droppedNotices.set([]); this.markClean();
      if (keepGoing) { this.continueEntry(); return; }
      const key = selected === 'parent' ? submitted[0].key : selected;
      void this.router.navigateByUrl(`/accounting/entries/${ids.get(key)!}`, { replaceUrl: true, state: { closeTo: 'list' } });
    },
    error: error => {
      if (this.loadId !== loadId) return;
      this.saving.set(false);
      const routed = memberError(error, submitted.map(c => c.key));
      if (routed) {
        this.drafts.select(routed.key, () => true);
        this.fieldErrors.set({ [routed.field]: routed.message });
        this.error.set(routed.message);
      } else this.error.set(writeErrorMessage(error));
      // Neither baseline nor drafts are cleared on 404/409/422/network failures.
    },
  });
}
```

Add `saveResponseInvalid=signal(false)` and include it in save-disabled/guard conditions; explicit reload clears it. The UI reload action must go through DirtyFormRegistry if draft is dirty. `409 retry`, `member_locked`, `already_grouped`, `import_running`, `group_scheduled`, `group_locked` keep the draft and show the existing server error; never automatically resubmit a convert or silently reload away edits. Complete cancellation uses `finalize` guarded by `loadId` to reset saving after EMPTY/destroy. Do not call existing `write` here: it marks clean before mapping results and cannot route child errors.

Counterparty creation before the atomic entry write is already the single-entry contract; this plan does not claim rollback of a newly created counterparty if the subsequent split fails. The callback is owner/load-bound and cannot mutate another child's draft.

- [ ] **Step 4 — HTTP acceptance additions:** In existing `entry-form.spec.ts`, `open`/`respond`/`settle` are retained. Add tests for each save route with HttpTestingController body assertions: POST new split has no ids; convert contains exactly id 7 anchor; update group contains protected keep with exact keys; dissolve contains one existing id and nullable group response; single path still uses create/update. Flush 422 body with `members.1.amount`, assert selection child 2, visible error and `isDirty()===true`; flush 409 retry, assert no second write. Save success/continuous clear DirtyFormRegistry pending-discard state. Task 9 includes the executable shared form test harness and representative complete route test.

- [ ] **Step 5 — verify/commit:** Focused serializer/result/form/schedule suites, full tests and build. Commit vertical migration `feat(split): edit and save selected child drafts` with trailer. This commit includes Tasks 4–7; pure helper modules and their tests can land earlier as specified below.
### Task 8: Timeline fallback and detail parent card

**Files:** Modify `timeline/timeline.ts:229` (`buildDays` title), `timeline.spec.ts`; `entry-detail/entry-detail.ts:138` (`formEditable`), `:251` (`siblings`), `entry-detail.html:14` (before dhead), `entry-detail.scss`, `entry-detail.spec.ts:68` (`render`). Create `entry-detail/split-card.ts`, `split-card.spec.ts`.

**Interfaces:** Produces `storedGroupNet(members:readonly LedgerEntry[]): CurrencyNet[]`, consuming server signed account-currency amounts. Detail `splitMembers` includes opened member (unlike old `siblings`); `edit()` retains `/accounting/entries/:id/edit`, so Task 5 selects that member. No speculative extra GETs or client FX conversion for the detail card.

- [ ] **Step 1 — red:** Add complete tests:

```ts
// split-card.spec.ts
import { expect, it } from 'vitest';
import { makeEntry } from '../testing/fixtures';
import { storedGroupNet } from './split-card';
it('nets only stored amounts in their currencies', () => {
  expect(storedGroupNet([
    makeEntry({ amount: '-100', currency: 'TWD' }),
    makeEntry({ kind: 'receivable', is_settlement: true, amount: '40', currency: 'TWD' }),
    makeEntry({ amount: '-8', currency: 'USD', original_currency: 'JPY', original_amount: '1000' }),
  ])).toEqual([{ currency: 'TWD', amount: -60 }, { currency: 'USD', amount: -8 }]);
});
// In existing entry-detail.spec.ts describe, using render/detail/entry/el:
it('shows parent card and permits metadata edit of an opened protected split child', () => {
  const group = { id: 4, kind: 'split', name: '旅遊', merchant: '商家', description: '備註', count: 2, total: '-60', currency: 'TWD' };
  const fixture = render(detail({ id: 42, kind: 'refund', amount: '40', group,
    group_members: [
      { ...entry({ id: 7, amount: '-100', group }), protected: false, protected_reason: null },
      { ...entry({ id: 42, kind: 'refund', amount: '40', group }), protected: true, protected_reason: 'refund' },
    ] }));
  expect(el(fixture).querySelector('.split-parent-card')?.textContent).toContain('多類別');
  expect(el(fixture).querySelectorAll('.split-child')).toHaveLength(2);
  expect(el(fixture).querySelector('.split-child[aria-current="true"]')?.textContent).toContain('🔒');
  const navigate = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
  fixture.componentInstance.edit();
  expect(navigate).toHaveBeenCalledWith(['/accounting/entries', 42, 'edit']);
});
// In timeline.spec.ts, import buildDays and use its makeEntry fixture:
it('uses group name or 多類別 and preserves one row and count', () => {
  const group = { id: 4, kind: 'split' as const, name: null, merchant: null, description: null, count: 2, total: '-30', currency: 'TWD' };
  const rows = [makeEntry({ id: 7, group }), makeEntry({ id: 8, group })];
  const days = buildDays(rows, 'TWD', false);
  expect(days.flatMap(d => d.rows)).toHaveLength(1);
  expect(days[0].rows[0]).toMatchObject({ title: '多類別', groupCount: 2 });
  rows[0].group = { ...group, name: '旅遊' };
  expect(buildDays(rows, 'TWD', false)[0].rows[0].title).toBe('旅遊');
});
```

Run focused `split-card.spec.ts`, `entry-detail.spec.ts`, `timeline.spec.ts`; expect missing card/helper and old title fallback.

- [ ] **Step 2 — green:** New helper and component additions:

```ts
// split-card.ts
import { LedgerEntry } from '../../../models/accounting.model';
import { roundHalfAway } from '../amount-math';
import { currencyDecimals } from '../format';
import { CurrencyNet } from '../entry-form/split-summary';
export function storedGroupNet(members: readonly LedgerEntry[]): CurrencyNet[] {
  const totals = new Map<string, number>();
  for (const member of members) totals.set(member.currency, (totals.get(member.currency) ?? 0) + Number(member.amount));
  return [...totals].map(([currency, amount]) => ({ currency, amount: roundHalfAway(amount, currencyDecimals(currency)) }));
}
// EntryDetailComponent:
readonly splitMembers = computed(() => this.detail()?.group?.kind === 'split' ? this.detail()!.group_members : []);
readonly splitNet = computed(() => storedGroupNet(this.splitMembers()));
readonly splitAccountCount = computed(() => new Set(this.splitMembers().map(c => c.account_id)).size);
```

In `formEditable`, after the existing `detail.schedule && detail.group` refusal and before the kind check, add `if (detail.group?.kind === 'split' && !detail.locked) return true;`. Keep per-member settle/refund/repost/delete actions unchanged; editing a protected child now reaches metadata-only split form. Do not grant ordinary protected single editing through this new condition.

```html
@if (d.group?.kind === 'split') {
  <section class="split-parent-card" aria-label="多類別摘要">
    <h3>{{ d.group?.name || '多類別' }} · 多類別</h3>
    @for (line of splitNet(); track line.currency) {
      <p>{{ line.currency }} {{ formatMoney(line.amount, line.currency, { sign: true }) }}</p>
    }
    <p>{{ splitMembers().length }} 項 · {{ splitAccountCount() }} 個帳戶</p>
    <p>商家 {{ d.group?.merchant || '—' }} · 日期 {{ splitMembers()[0]?.entry_date }}</p>
    @for (member of splitMembers(); track member.id) {
      <a class="split-child" [routerLink]="['/accounting/entries', member.id]" [attr.aria-current]="member.id === d.id ? 'true' : null">
        <span class="ico" [style.background]="colorOf(member)">{{ iconOf(member) }}</span>
        <span>{{ member.name || member.category || kindLabel(member.kind) }} · {{ member.account_name }}</span>
        <span>{{ formatMoney(member.amount, member.currency, { sign: true }) }}</span>
        @if (member.protected) { <span aria-label="受保護">🔒</span> }
      </a>
    }
  </section>
}
```

```scss
.split-parent-card { border: 1px solid var(--app-border); border-radius: .75rem; padding: .75rem; margin-bottom: .75rem; }
.split-child { display: flex; gap: .5rem; align-items: center; padding: .5rem; font-size: .85rem; }
.split-child[aria-current='true'] { outline: 2px solid currentColor; border-radius: .4rem; }
```

Timeline `title` becomes `entry.group.name?.trim() || (entry.group.kind === 'split' ? '多類別' : displayTitle(entry))`; other group kinds retain their fallback. Remove stale comment claiming upsert re-inserts all members at `entry-detail.ts:93`; stable ids are now contractual.

- [ ] **Step 3 — verify/commit:** Focused detail/timeline/helper suites, full tests and build; commit `feat(split): show parent cards and group titles` with trailer.

### Task 9: Integration acceptance, old-code removal and delivery evidence

**Files:** Create `entry-form/entry-form-split.spec.ts`; modify existing entry-form/picker/account-picker regression specs as named below; delete all four tracked `split-lines/*` files; remove `SplitLinesComponent` import/component import entry from `entry-form.ts:70,174`; remove `members`, `splitGroup`, `loadedDates`, `datesKey`, `SplitGroupFields` and old inheritance serializer only after new wiring replaces every use. Document screenshots/test output in PR, not as runtime-local links.

**Interfaces:** Consumes all earlier public form handlers and signals. Produces frontend acceptance evidence for the exact implementation SHA; no new production interface. All tests use frontend mocks; screenshots run the real local frontend against `http://127.0.0.1:8011` with PR-B availability stated.

- [ ] **Step 1 — red:** New complete harness and route tests. Imports/types are explicit; fixture GET resolution throws on an unexpected URL to avoid masking an accidental dependency.

```ts
import { Component } from '@angular/core';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { EntryDetail } from '../../../models/accounting.model';
import { EntryFormComponent } from './entry-form';
import { DirtyFormRegistry } from '../dirty-form.service';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { makeAccount, makeAccountDetail, makeCategory, makeEntryDetail, makePreference } from '../testing/fixtures';
@Component({ template: '' }) class Destination {}

describe('split form integration', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;
  let form: EntryFormComponent;
  const entries = new Map<number, EntryDetail>();
  beforeEach(() => {
    entries.clear(); localStorage.clear();
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([
      { path: 'accounting', component: Destination },
      { path: 'accounting/entry', component: EntryFormComponent },
      { path: 'accounting/entries/:id/edit', component: EntryFormComponent },
      { path: 'accounting/entries/:id', component: Destination },
    ])] });
    http = TestBed.inject(HttpTestingController);
    TestBed.inject(LayoutModeService).set('phone');
  });
  afterEach(() => { http.verify(); localStorage.clear(); });
  function settle(): void {
    for (let round = 0; round < 8; round++) {
      harness.detectChanges();
      const requests = http.match(r => r.method === 'GET');
      for (const req of requests) {
        if (req.cancelled) continue;
        const path = req.request.url.replace('/api/accounting', '');
        if (path === '/accounts') req.flush([makeAccount(), makeAccount({ id: 2 })]);
        else if (path === '/projects' || path === '/counterparties') req.flush([]);
        else if (path === '/preference') req.flush(makePreference());
        else if (path === '/categories') req.flush([makeCategory({ id: 12 })]);
        else if (/^\/accounts\/\d+$/.test(path)) req.flush(makeAccountDetail({ id: Number(path.split('/').pop()) }));
        else if (/^\/entries\/\d+$/.test(path)) {
          const detail = entries.get(Number(path.split('/').pop()));
          if (!detail) throw new Error(`missing fixture ${path}`);
          req.flush(detail);
        } else throw new Error(`unexpected GET ${path}`);
      }
    }
  }
  async function open(path = '/accounting/entry'): Promise<void> {
    harness = await RouterTestingHarness.create();
    form = await harness.navigateByUrl(path, EntryFormComponent);
    settle();
  }
  function fill(amount = '10'): void {
    form.chooseAccount(1); form.onCategoryPicked(makeCategory({ id: 12 })); form.onAmountInput(amount); settle();
  }
  it('POST new split sends explicit children; indexed 422 selects and stays dirty', async () => {
    await open(); fill();
    form.name.set('first'); form.merchant.set('shop');
    form.addChild(); settle(); fill('20');
    expect(form.children()).toHaveLength(2);
    form.selectBubble('parent'); settle();
    expect(harness.routeNativeElement!.querySelector('app-amount-keypad')).toBeNull();
    expect(harness.routeNativeElement!.querySelector('.kinds')).toBeNull();
    form.save(false);
    const req = http.expectOne(r => r.method === 'POST' && r.url.endsWith('/splits'));
    expect(req.request.body.members).toHaveLength(2);
    expect(req.request.body.members.every((m: Record<string, unknown>) => !('id' in m))).toBe(true);
    expect(req.request.body.members[0]).toMatchObject({ project_id: null, tags: [], merchant: null, name: 'first' });
    req.flush({ detail: [{ loc: ['body', 'members', 1, 'amount'], msg: 'invalid' }] }, { status: 422, statusText: 'Unprocessable Entity' });
    settle();
    expect(form.selected()).toBe(form.children()[1].key);
    expect(form.isDirty()).toBe(true);
    expect(harness.routeNativeElement!.textContent).toContain('invalid');
  });
  it('convert keeps anchor id and pre-split date edits, success clears pending discard', async () => {
    entries.set(7, makeEntryDetail({ id: 7, entry_time: '01:02:03.456' }));
    await open('/accounting/entries/7/edit');
    form.setParentDate('entryTime', '11:12');
    form.addChild(); settle(); fill('20');
    form.save(false);
    const req = http.expectOne(r => r.method === 'PUT' && r.url.endsWith('/entries/7/split'));
    expect(req.request.body.members.map((m: Record<string, unknown>) => m['id'])).toEqual([7, undefined]);
    expect(req.request.body.members.every((m: Record<string, unknown>) => m['entry_time'] === '11:12')).toBe(true);
    const members = form.children().map((c, i) => ({ id: i + 7, client_key: c.key }));
    req.flush({ group_id: 4, member_ids: [7, 8], members });
    await harness.fixture.whenStable();
    expect(TestBed.inject(DirtyFormRegistry).promptOpen()).toBe(false);
  });
  it('switches tiles without dirtying and flushes old keypad expression', async () => {
    await open(); fill('10+2'); form.addChild(); settle(); fill('5');
    expect(form.children()[0].amountExpr).toBe('12');
    const before = form.isDirty();
    form.selectBubble(form.children()[0].key); settle();
    expect(form.amountExpr()).toBe('12');
    form.selectBubble(form.children()[1].key); settle();
    expect(form.amountExpr()).toBe('5'); expect(form.isDirty()).toBe(before);
  });
  it('dissolves with a persisted protected survivor using keep and parent values', async () => {
    const group = { id: 4, kind: 'split' as const, name: 'Parent', merchant: 'Shop', description: 'Note', count: 2, total: '0', currency: 'TWD' };
    const a = makeEntryDetail({ id: 7, kind: 'refund', amount: '10', group });
    const b = makeEntryDetail({ id: 8, group });
    a.group_members = [{ ...a, protected: true, protected_reason: 'refund' }, { ...b, protected: false, protected_reason: null }];
    entries.set(7, a); entries.set(8, b);
    await open('/accounting/entries/7/edit');
    form.selectBubble(form.children()[1].key); form.removeChild(); settle();
    form.save(false);
    const req = http.expectOne(r => r.method === 'PUT' && r.url.endsWith('/splits/4'));
    expect(req.request.body.members).toEqual([{ id: 7, keep: true, client_key: form.children()[0].key,
      name: null, project_id: null, tags: [], description: null }]);
    req.flush({ group_id: null, member_ids: [7], members: [{ id: 7, client_key: form.children()[0].key }] });
    await harness.fixture.whenStable();
  });
  it('single→split→single preserves child name/note and chooses parent merchant', async () => {
    await open(); fill(); form.name.set('child'); form.description.set('note'); form.merchant.set('shop');
    form.addChild(); settle();
    form.setParentText('name', 'group'); form.setParentText('merchant', 'new shop');
    form.removeChild(); settle(); form.save(false);
    const req = http.expectOne(r => r.method === 'POST' && r.url.endsWith('/entries'));
    expect(req.request.body).toMatchObject({ name: 'child', description: 'note', merchant: 'new shop' });
    req.flush(makeEntryDetail({ id: 30 }));
    await harness.fixture.whenStable();
  });
});
```

- [ ] **Step 2 — additional adversarial component cases:** Extend the same suite with the following tests before green. Each test is a distinct required behavior; use the harness above and leave the relevant HTTP requests pending rather than flushing them through `settle` when testing interleavings.

```ts
it('keeps failed save dirty and never automatically retries a 409', async () => {
  await open(); fill(); form.addChild(); settle(); fill('20'); form.save(false);
  http.expectOne(r => r.method === 'POST').flush({ detail: 'retry' }, { status: 409, statusText: 'Conflict' });
  settle();
  expect(form.isDirty()).toBe(true);
  http.expectNone(r => r.method !== 'GET');
});
it('retains each untouched raw date on group upsert', async () => {
  const group = { id: 4, kind: 'split' as const, name: null, merchant: null, description: null, count: 2, total: '-20', currency: 'TWD' };
  const a = makeEntryDetail({ id: 7, group, entry_date: '2026-01-01', entry_time: '01:02:03.456' });
  const b = makeEntryDetail({ id: 8, group, entry_date: '2026-02-02', entry_time: '04:05:06' });
  a.group_members = [{ ...a, protected: false, protected_reason: null }, { ...b, protected: false, protected_reason: null }];
  entries.set(7, a); entries.set(8, b);
  await open('/accounting/entries/7/edit'); form.onAmountInput('15'); form.save(false);
  const req = http.expectOne(r => r.method === 'PUT');
  expect(req.request.body.members.map((m: Record<string, unknown>) => [m['entry_date'], m['entry_time']]))
    .toEqual([['2026-01-01', '01:02:03.456'], ['2026-02-02', '04:05:06']]);
  req.flush({ detail: 'test rejection' }, { status: 422, statusText: 'Unprocessable Entity' });
});
it('parent date controls reject changes whenever any child is protected', async () => {
  await open(); fill(); form.addChild(); settle();
  form.children.update(rows => rows.map((c, i) => i ? { ...c, id: 8, protected: true } : c));
  form.selectBubble('parent'); settle();
  const before = structuredClone(form.parent());
  form.setParentDate('entryDate', '2030-01-01');
  expect(form.parent()).toEqual(before);
  const date = harness.routeNativeElement!.querySelector<HTMLInputElement>('[aria-label="整筆日期"]')!;
  expect(date.disabled).toBe(true);
});
it('save-and-continue clears children, notices and dirty state', async () => {
  await open(); fill(); form.addChild(); settle(); fill('20'); form.save(true);
  const req = http.expectOne(r => r.method === 'POST');
  req.flush({ group_id: 4, member_ids: [7, 8], members: form.children().map((c, i) => ({ id: i + 7, client_key: c.key })) });
  settle();
  expect(form.children()).toHaveLength(1);
  expect(form.drafts.droppedNotices()).toEqual([]);
  expect(form.isDirty()).toBe(false);
});
```

Async interleaving acceptance is pinned to Task 4's concrete owner tests plus these implementation checks: a captured owner token is passed through account/category/FX/sheet/counterparty callbacks; account changes increment generation before clearing FX/fees/rules; switching invalidates outgoing requests even for same-value siblings. Add Subject-based component tests to replace any current value-comparison assertions, and retain existing PR-2 schedule-only/transfer-only/target-only dirty tests. This is required evidence, not permission to discard failing legacy tests.

- [ ] **Step 3 — preserve keyboard/account regression:** Run unchanged category-picker PR-5 tests for child→main, reopened main→strip focus, unselected main→form. Add a parent-mode dirty Escape assertion in the component suite using `new KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true})` dispatched at `.entry-form`; expect registry prompt true. Keep account-picker archived filtering, transfer exclusion, overlay Escape/Tab and focus-return tests. Bubble switch must not remount/re-register the whole form or bypass `isHandledKey`.

- [ ] **Step 4 — remove old code and verify:** Delete `split-lines.ts`, `.html`, `.scss`, `.spec.ts` only after host replacement and regression tests pass. Run:

```bash
rg -n 'SplitLinesComponent|app-split-lines|splittable|addLine|SplitGroupFields|splitGroup|this\.members|project_id: line\.project_id \?\?' frontend/src/app
```

Expected: no production matches (unrelated domain `members` remains valid). Run `cd frontend && npm test -- --watch=false`; run `cd frontend && npx ng build`. Commit `test(split): cover migration and remove old lines` with trailer. Preserve full command output against the exact SHA for review; a later code fix invalidates that SHA's review and requires new evidence.

- [ ] **Step 5 — review and screenshots:** Non-author review the exact final implementation SHA for all five Review Focus items and §2 coverage. Use the permitted existing reviewer/capacity; no paid fallback. Capture from own local build at 390×844 / 760×820 / 1280×800: child selected with strip, parent with mixed-currency totals, protected child/date lock, dissolve warning, invalid child error and relevant picker/drill-in overlay. Use demo API at `http://127.0.0.1:8011`; record PR-B route availability. Before PR-B upgrades the demo, endpoint 404 flows are covered by HTTP mock evidence and explicitly labelled as such, never presented as live persistence success.

- [ ] **Step 6 — delivery:** Push branch, create frontend-only PR after plan gate and backend merge dependency are satisfied; include full tests/build result, screenshot attachments, base/final SHA, exact-head review evidence and any unresolved limitations. PR body ends with the required Claude Code attribution. Post PR link to AGENT-67; status `in_review`; do not merge, deploy, install or switch production.

## Compile-safe commit map

1. Task 1 wire types/client plus fixture compatibility (`client_key` required on full split members).
2. Task 2 state module and Task 4 pure view/snapshot/owner additions; these are unused production helpers until integration.
3. Task 3 serializer in **new `entry-form/split-save.ts`**, importing existing `buildEntryInput`/`emptyEntryInput`; Task 5 pure load helper and Task 6 pure summary helper, with tests. Keep old `entry-save.ts` public save-plan exports temporarily for the current form. Export fee/discount extractor and preserve raw time in the existing helper at this step. This is a pure-functions commit, not a half-migrated form.
4. Tasks 4–7 form/picker integration in one vertical commit: switch form imports to `split-save.ts`, replace old state, wire UI/load/save/error/default ownership together. Remove obsolete plan exports from `entry-save.ts` and migrate their unit tests here. Do not commit the intermediate template/type errors.
5. Task 8 detail/timeline.
6. Task 9 remaining integration acceptance and deleted old component. Every commit runs the full suite and build; combine a deletion with commit 4 if unused imports/types otherwise break the full suite.

Task 3's final `EntrySavePlan`, `planEntrySave`, `toSplitInput`, `executeEntrySave` live in `split-save.ts`; the step's named old region is the replacement target, not a demand to break the old caller mid-task. `split-save.spec.ts` imports `./split-save`. This module seam preserves the requested `planEntrySave → toSplitInput` architecture without an overload accepting incompatible legacy data.

## Self-review and coverage map

| Spec | Tasks / acceptance |
|---|---|
| §2.1 child state / async / dirty | 2 + 4 + 9; key/generation stale rejection, inert parent, flush, mode-specific snapshot |
| §2.2 mode matrix | 5 matrix test + 6 tabs/event template gating |
| §2.3 strip / inheritance / limit / removal | 2 transition and all-four-guard tests + 6 picker test |
| §2.3a field map | 2 metadata/notice test + 9 single round-trip and conversion date test |
| §2.4 child / protected metadata | 3 exact keep payload + 6 individual financial locks + 9 protected dissolve |
| §2.5 parent totals / rewards / dates | 6 signed/currency/unknown/estimate tests + 9 date lock |
| §2.6 ordered load / provenance / archived originals | 5 loader test and form accountOptions integration |
| §2.7 five save rows / explicit fields / ids / errors | 1 wire test + 3 route matrix/provenance + 7 key/error tests + 9 real HTTP bodies |
| §2.8 notices / Esc | 2 dropped notices + 6 dissolve notices + 9 PR-2/5 regression |
| §2.9 old-code removal | 9 deletion/search; 3 and compile-safe map replace inheritance serializer |
| §2.10 timeline/detail | 8 card, signs, opened child edit and title fallback tests |
| §2.11 regression matrix | 4, 5, 6, 8, 9; full suite at every implementation commit and final screenshots |

Signature audit: canonical child `key` is generated once; every serializer call receives `children,parent,accounts,partyIds,context`; no `primary/extra/group.index` callers survive integration. `SplitKeepInput` is a union member, never asserted as `EntryInput`. All four API responses use `SplitResult`. Dates/protection are loaded before baseline; submitted keys are captured before counterparty HTTP. No alternate save code may fall back to POST after a missing load or failed convert.

Plan self-review checks: compare Global Constraints text byte-for-byte with the binding spec section; enumerate §2.1–§2.11 including §2.3a above; search for unresolved TODO/TBD, dummy code, omitted test/implementation blocks, and signature drift; verify each Review Focus cites a named test. Review the exact plan commit, not a predecessor. Any finding left open is included in the issue handoff; the owner's plan gate is not replaced by self-review or an automated review.
