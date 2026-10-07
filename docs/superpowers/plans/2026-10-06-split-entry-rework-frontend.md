# Split entry rework — frontend implementation plan

**Goal:** Edit a split through equal child drafts and a parent bubble; preserve ids, raw provenance and protected members across create, convert, upsert and dissolve.

**Architecture:** A form-local signal store owns child identity, parent fields and transitions. Pure serializers choose the five save routes and produce explicit member payloads. Existing tiles bind to the selected child through writable views; captured owner tokens protect asynchronous work. CategoryPicker retains PR-5 drill-in behavior and gains the bubble strip. Read-only summary functions serve both the form and detail card.

**Tech Stack:** Angular 21 standalone/signals, RxJS, existing Vitest/HttpTestingController/RouterTestingHarness; no new dependencies.

**Spec:** Binding v4, precedence update at [`f19e1fb`](https://github.com/yonger0718/home-service-hub/blob/f19e1fb/docs/superpowers/specs/2026-10-06-split-entry-rework-design.md), §2; HTTP contracts §1.3/§1.5/§1.6. Final API authority: [`abf1365:openspec/specs/accounting-ledger/spec.md`](https://github.com/yonger0718/home-service-hub/blob/abf1365/openspec/specs/accounting-ledger/spec.md), requirements Split endpoint, Protected split members, Split upsert by member id, Dissolving a split, Converting an entry into a split, Deleting a member dissolves a one-member split, and Split write phases/errors/lock order. Both spec files remain on their own branches and are not copied or edited here.

**Base / branch:** `main` @ `4e301e24a1c2852258fbced2d98ec0745a2a337c` → `feat/split-entry-rework-fe`. Anchors below refer to this base and include symbols to survive line movement. This commit contains only this plan. Do not execute its implementation steps before the owner's plan review clears (P1 blocks; P2/P3 proceed). PR-B is open as [#55](https://github.com/yonger0718/home-service-hub/pull/55), with final API contract at `feat/split-upsert-api` @ `abf1365`; it must land before frontend delivery. Use contract HTTP mocks until the demo exposes the new routes.

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
3. Spec focus 5: removed/replaced child account/category/FX responses dropped by key + generation, including equal account/kind values. **Task 4, `accepts unselected owners but drops removed or changed generations`**, plus Task 9 removed-category/FX HTTP regressions.
4. Additional: protected settlement/refund/system signs are server signs; no original FX currency summed. **Task 6, `uses stored signs and account currencies`**.
5. Additional: dissolution cannot discard the last persisted id; keep payload contains no financial/date properties and routes errors by submitted keys. **Task 2, `guards all four removals`**, **Task 3, `emits only keep metadata`**, **Task 7, `routes indexed errors using submitted keys`**.

## Resolved interpretations

- `EditableKind` in the spec is the existing `WritableEntryKind`; do not widen it. `ChildDraft.kind` may carry a real `EntryKind` for protected rows; single transfer/system mode remains a separate `FormKind` adapter.
- Current wire `fx_source` uses `fx_api` for online. Preserve that DTO spelling unless PR-B explicitly changes it; the spec's “online” is semantic, not permission to invent a wire enum. No main-currency member amount exists in current DTO: `loaded.signedBase=null` unless account currency is the preference's main currency.
- A switch flushes pending synchronous input but does not change generation. Existing children may receive valid defaults/quotes while unselected. Account/kind/currency/date dependency changes invalidate that child; removal eliminates its key. A per-child request reconciler refreshes every changed child, not just the visible one.
- Parent mode's add derives from the last child; protected/non-editable kind falls back by scanning predecessors. Account inheritance still comes from that last/selected child, independently of the kind fallback.
- New members serialize `merchant:null`; persisted members use raw loaded merchant. When an unsaved split shrinks to one, parent merchant wins only if non-empty; single payload uses parent merchant. Existing-group single layout still edits group parent values until dissolve succeeds.
- Parent dates compare time/date and normalized posting-date semantics on the first single→split transition; equal-to-entry-date means null in the parent editor, while untouched child provenance stays raw. A user time edit may produce minute precision; an untouched loaded time must never be truncated. Date controls display raw values with `step="1"`; millisecond precision stays in the model if the browser display cannot express it.
- Async defaults must not rewrite the entire dirty baseline: track a default-only baseline patch for the same child/fields; user edits elsewhere stay dirty. Automatic rule selection is excluded until `rulesTouched`; FX quote rate/date are excluded when online, while user-selected FX inputs remain in the snapshot.
- Estimates are per-account-currency, exclude existing reward ledger rows and do not claim available statement/window caps. Unknown FX totals display `—`, never zero or an original-currency sum.
- Naming deviation from §2.9: `childFromDetail` owns loaded provenance rather than expanding `entryInputFromDetail` into a mixed draft/API DTO; `entryInputFromDetail` still preserves raw time. No wire/behavior deviation. Owner re-review must clear the three P1 findings before implementation.

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

**Files:** Create `frontend/src/app/components/accounting/entry-form/split-draft.ts` and `split-draft.spec.ts`; consume `entry-draft.ts:6` (`FormKind`), `:36` (`signFor`), `entry-save.ts:112` (`emptyEntryInput`), model types.

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
import { computed, signal, WritableSignal } from '@angular/core';
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
    onlineFx?: { originalAmount: string; originalCurrency: string; amountExpr: string };
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
let childSequence = 0;
export function childKey(source: { randomUUID?: () => string } | undefined = globalThis.crypto): string {
  if (typeof source?.randomUUID === 'function') return source.randomUUID();
  // UI identity only, never an authentication token. Process-local monotonic suffix prevents collisions.
  return `child-${Date.now().toString(36)}-${(++childSequence).toString(36)}`;
}
export function newChild(kind: EntryKind = 'expense', accountId: number | null = null): ChildDraft {
  return {
    key: childKey(), id: null, protected: false, protectedReason: null, kind, categoryId: null,
    amountExpr: '', accountId, counterpartyName: '', counterpartyId: null, name: '', projectId: null,
    tags: [], description: '', fee: null, discount: null, fx: null, ruleIds: [],
    invoice: { number: '', random: '' }, loaded: null, rulesTouched: false, pendingCategoryId: null,
    generation: 0, availableRules: [],
  };
}
export const normalizePosted = (date: string, posted: string | null): string | null =>
  posted && posted !== date ? posted : null;
export const nonempty = (value: string | null | undefined): boolean => !!value?.trim();
export class SplitDraftStore {
  readonly children = signal<ChildDraft[]>([newChild()]);
  readonly selected = signal<string>('');
  readonly parent: WritableSignal<ParentDraft>;
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
        p.entryDate !== loaded.entryDate || p.entryTime !== loaded.entryTime ||
        normalizePosted(p.entryDate, p.postedDate) !== normalizePosted(loaded.entryDate, loaded.postedDate) });
    }
    const next = newChild(kind, accountId);
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
    // Removing the key invalidates all outstanding callbacks for that child.
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

**Files:** Create `entry-form/split-save.ts` and `split-save.spec.ts` for the new planner/serializer. Modify `entry-form/entry-save.ts:291` (`entryInputFromDetail`, raw time) and add the fee/discount extractor. Retire old `entry-save.ts:137–225` save-plan exports and migrate `entry-save.spec.ts` only in the Tasks 4–7 vertical integration commit.

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

- [ ] **Step 2 — green:** Create `split-save.ts` with the following save-plan code; add imports `SplitResult`, `SplitMemberInput`, `ChildDraft`, `ParentDraft`, `normalizePosted`, `evalOrNull`, `isWritableKind`, `rulesForDate`. Import `buildEntryInput` from `./entry-save` and `currencyDecimals` from `../format`.

```ts
export interface SaveContext { entryId: number | null; groupId: number | null }
export type EntrySavePlan =
  | { kind: 'create'; input: EntryInput }
  | { kind: 'update'; id: number; input: EntryInput }
  | { kind: 'create-split'; input: SplitInput }
  | { kind: 'convert-split'; id: number; input: SplitInput }
  | { kind: 'update-split'; groupId: number; input: SplitInput };

export function permittedRuleIds(c: ChildDraft, date: string): number[] {
  const offered = new Set(rulesForDate(c.availableRules.filter(rule => rule.account_id === c.accountId), date).map(r => r.id));
  if (c.loaded?.originalAccountId === c.accountId)
    for (const id of c.loaded.attachedRuleIds) offered.add(id);
  return c.ruleIds.filter(id => offered.has(id));
}
export function fullChild(c: ChildDraft, p: ParentDraft, accounts: readonly LedgerAccount[],
  partyId: number | null, single: boolean): EntryInput {
  if (c.protected || !isWritableKind(c.kind)) throw new Error('受保護子項只可更新備註資料');
  const account = accounts.find(a => a.id === c.accountId);
  if (!account) throw new Error('請選擇帳戶');
  const original = c.loaded?.onlineFx;
  const unchangedOriginal = c.fx?.use_online && original &&
    c.amountExpr === original.amountExpr && c.fx.original_currency === original.originalCurrency;
  const amount = unchangedOriginal && original ? Number(original.originalAmount)
    : evalOrNull(c.amountExpr, currencyDecimals(c.fx?.original_currency ?? account.currency));
  if (amount === null || !Number.isFinite(amount) || amount <= 0) throw new Error('請輸入有效金額');
  const fx = c.fx;
  const manual = fx?.manual === 'amount' ? fx.amount : fx?.manual === 'rate' ? fx.fx_rate : null;
  if (fx && !fx.use_online && (!manual?.trim() || !Number.isFinite(Number(manual)) || Number(manual) <= 0))
    throw new Error('請輸入匯率或轉換後金額');
  const parentDates = single || p.dateTouched || !c.loaded;
  const dates = parentDates ? p : c.loaded!;
  const input = buildEntryInput({
    kind: c.kind, account, amount, fx: c.fx, categoryId: c.categoryId, counterpartyId: partyId,
    name: c.name, merchant: '', description: c.description, projectId: c.projectId, tags: [...c.tags],
    entryDate: dates.entryDate, entryTime: dates.entryTime ?? '', postedDate: dates.postedDate ?? '',
    invoiceNumber: c.invoice.number, invoiceRandom: c.invoice.random,
    fee: c.fee, discount: c.discount, ruleIds: permittedRuleIds(c, dates.entryDate),
  });
  const party = c.kind === 'receivable' || c.kind === 'payable';
  return { ...input,
    ...(c.fx?.use_online ? { amount: null, fx_rate: null } : {}),
    ...(unchangedOriginal && original ? { original_amount: original.originalAmount, original_currency: original.originalCurrency } : {}),
    entry_date: dates.entryDate, entry_time: dates.entryTime,
    posted_date: parentDates ? normalizePosted(dates.entryDate, dates.postedDate) : dates.postedDate,
    merchant: single ? (party ? null : p.merchant.trim() || null) : c.loaded?.merchant ?? null };
}
export function toSplitInput(children: readonly ChildDraft[], p: ParentDraft,
  accounts: readonly LedgerAccount[], partyIds: ReadonlyMap<string, number>): SplitInput {
  return {
    name: p.name.trim() || null, merchant: p.merchant.trim() || null, description: p.description.trim() || null,
    entry_date: p.entryDate, entry_time: p.entryTime, posted_date: normalizePosted(p.entryDate, p.postedDate), project_id: null, tags: [],
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
  it('accepts unselected owners but drops removed or changed generations', () => {
    const s = new SplitDraftStore(newParent('2026-10-06', ''));
    const a = s.children()[0], request = s.capture()!;
    const b = s.add([makeAccount()])!;
    expect(s.accept(request, { ruleIds: [99], categoryId: 5 })).toBe(true);
    expect(s.children()[1].ruleIds).toEqual([]);
    const bRequest = s.capture()!;
    s.select(a.key, () => true);
    expect(s.accept(bRequest, { fx: null, fee: { amount: '9', name: null } })).toBe(true);
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
    const owner = s.children()[0];
    const fx = { original_amount: '20', original_currency: 'USD', account_currency: 'TWD',
      use_online: true, manual: null, amount: null, fx_rate: '30', rate_date: '2026-10-01' };
    s.accept({ key: owner.key, generation: owner.generation }, { fx });
    const quoted = snap();
    s.accept({ key: owner.key, generation: owner.generation }, { fx: { ...fx, fx_rate: '31', rate_date: '2026-10-02' } });
    expect(snap()).toBe(quoted);
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
  // Loaded online input must not be re-rounded merely by switching or saving metadata.
  if (c.fx?.use_online && c.loaded?.onlineFx?.amountExpr === c.amountExpr &&
      c.loaded.onlineFx.originalCurrency === c.fx.original_currency) return true;
  const account = this.accounts().find(a => a.id === c.accountId);
  const value = evalOrNull(c.amountExpr, currencyDecimals(c.fx?.original_currency ?? account?.currency ?? this.preference().main_currency));
  if (value === null) { this.amountError.set(true); return false; }
  return this.drafts.accept(owner, { amountExpr: String(value) });
}
selectBubble(key: string): void {
  if (this.saving() || this.sheet() || key === this.selected()) return;
  if (this.drafts.select(key, owner => this.flushChild(owner))) {
    const c = this.drafts.current();
    this.accountDetail.set(c ? this.childAccountCache.get(c.key) ?? null : null);
    this.categories.set(c ? this.categoryCache.get(c.kind) ?? [] : []);
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

- [ ] **Step 4 — async replacement:** Replace the selection-only constructor account/category subscriptions with a per-child reconciler. Import `effect`, `untracked` from Angular, `forkJoin`, `of`, `map`, `finalize`, `takeUntilDestroyed` from their existing packages. Keep `categoryCache` for presentation only; neither selection nor same account/kind values identify an owner. Every current generation starts once, including unselected children after a date edit. Add `AccountDetail` to model imports. Add these members and constructor effect:

```ts
private readonly childAccountCache = new Map<string, AccountDetail | null>();
private readonly requestedGeneration = new Map<string, number>();
private readonly pendingDraftLoads = signal<Owner[]>([]);
private readonly draftLoadErrors = signal<Record<string, string>>({});
readonly draftLoadsPending = computed(() => this.pendingDraftLoads().some(o =>
  this.children().some(c => c.key === o.key && c.generation === o.generation)));
readonly draftLoadsFailed = computed(() => this.children().some(c =>
  this.draftLoadErrors()[`${c.key}:${c.generation}`] !== undefined));
// Constructor:
effect(() => {
  const rows = this.children();
  if (!this.childScope() && !isWritableKind(this.singleKind())) return;
  untracked(() => {
    for (const c of rows) {
      if (this.requestedGeneration.get(c.key) === c.generation) continue;
      this.requestedGeneration.set(c.key, c.generation);
      this.loadChildResources(c);
    }
    for (const key of this.requestedGeneration.keys())
      if (!rows.some(c => c.key === key)) { this.requestedGeneration.delete(key); this.childAccountCache.delete(key); }
  });
});
```

```ts
private loadChildResources(child: ChildDraft): void {
  const owner: Owner = { key: child.key, generation: child.generation };
  const date = this.parent().dateTouched || !child.loaded ? this.parent().entryDate : child.loaded.entryDate;
  const fx = child.fx;
  const token = `${owner.key}:${owner.generation}`;
  this.pendingDraftLoads.update(rows => [...rows, owner]);
  this.draftLoadErrors.update(errors => { const next = { ...errors }; delete next[token]; return next; });
  forkJoin({
    account: child.accountId === null ? of(null) : this.accounting.getAccount(child.accountId),
    categories: isWritableKind(child.kind) ? this.loadCategories(child.kind) : of([]),
    quote: fx?.use_online && fx.fx_rate === null ? this.accounting.getFxRate(date, fx.original_currency, fx.account_currency) : of(null),
  }).pipe(takeUntilDestroyed(this.destroyRef), finalize(() => {
    this.pendingDraftLoads.update(rows => rows.filter(o => o.key !== owner.key || o.generation !== owner.generation));
  })).subscribe({
    next: result => {
      const c = this.children().find(c => c.key === owner.key && c.generation === owner.generation);
      if (!c) return;
      const accountRules = result.account?.reward_rules ?? [];
      const unchangedAccount = c.loaded?.originalAccountId === c.accountId;
      const attached = unchangedAccount ? c.availableRules.filter(r => c.loaded!.attachedRuleIds.includes(r.id)) : [];
      const availableRules = [...new Map([...attached, ...accountRules].map(r => [r.id, r])).values()];
      const eligible = rulesForDate(accountRules, date);
      const selected = c.rulesTouched ? c.ruleIds : eligible.filter(r => r.is_basic).map(r => r.id);
      const allowed = new Set(eligible.map(r => r.id));
      if (unchangedAccount) for (const id of c.loaded!.attachedRuleIds) allowed.add(id);
      const patch: Partial<ChildDraft> = { availableRules, ruleIds: selected.filter(id => allowed.has(id)) };
      if (result.quote && c.fx?.use_online) patch.fx = { ...c.fx,
        fx_rate: String(Number(result.quote.rate)), rate_date: result.quote.date };
      this.categoryCache.set(child.kind, result.categories);
      if (!this.drafts.accept(owner, patch)) return;
      this.childAccountCache.set(owner.key, result.account);
      if (this.selected() === owner.key) {
        this.categories.set(result.categories); this.accountDetail.set(result.account);
      }
    },
    error: error => {
      if (this.children().some(c => c.key === owner.key && c.generation === owner.generation)) {
        this.draftLoadErrors.update(errors => ({ ...errors, [token]: writeErrorMessage(error) }));
        this.error.set(writeErrorMessage(error));
      }
    },
  });
}
retryChildResources(): void {
  for (const c of this.children()) if (this.draftLoadErrors()[`${c.key}:${c.generation}`]) this.drafts.invalidate(c.key);
}
```

Use `draftLoadsPending() || draftLoadsFailed()` in save-disabled and the opening `save()` guard, before counterparty creation. The error strip offers `重新載入子項設定` → `retryChildResources()` (preserves edits). This ensures fast ＋ followed by save cannot silently omit an unselected child's pending defaults. Existing loaded online rates are retained until their currency/date inputs change; the reconciler requests only a missing online rate, so untouched metadata does not require a new quote. Ordinary blank children have no account/FX dependency; valid in-flight results still apply while parent mode is selected. Cache `AccountDetail` by child key for tile display on reselection; selecting an already-loaded child synchronously sets `accountDetail` from that cache and categories from `categoryCache`, without invalidating its owner. Store that cache only after the owner check above, and clear it on removal/reset. Remove redundant standalone FX subscription from `recomputeFx`: set online inputs, invalidate the owner, and let this reconciler perform the one versioned quote request. User sheet callbacks still capture `sheetOwner` and apply only through `accept`.

On an account/kind/currency/date dependency change, invalidate that child's generation and capture its new owner; selection alone never invalidates it. Keep `moveToAccount`'s existing currency-change reset logic, now operating on the selected views; it must not touch any other child. On a different selection use that child's own cached account/category data; clear display only if its cache is missing. Re-selecting the same key is a no-op. Sheets store `sheetOwner:Owner|null` at open; replace `[(value)]="fx"`, `[(fee)]`, `[(discount)]` with explicit outputs `applySheet({fx:$event})`, `applySheet({fee:$event})`, `applySheet({discount:$event})`. `applySheet` calls `accept(sheetOwner, patch)` and never a current view; close copies original amount through the same owner before clearing it. Save callbacks and counterparty resolution use submitted owner tokens (Task 7).

```ts
private sheetOwner: Owner | null = null;
applySheet(patch: Partial<ChildDraft>): void {
  if (this.sheetOwner) this.drafts.accept(this.sheetOwner, patch);
}
// In openSheet after its existing checks:
this.sheetOwner = this.drafts.capture();
// In recomputeFx, after its existing amount/lock guards and constructing `online`:
const owner = this.drafts.capture();
if (!owner) return;
this.drafts.accept(owner, { fx: online });
this.drafts.invalidate(owner.key); // reconciler fetches the quote, including if selection changes now
```

- [ ] **Step 5 — dirty integration:** `formState` uses `draftSnapshot(parent(), children(), scheduleDraftKey(scheduleDraft()), transferPanel()?.draftKey() ?? null, targetExpr())`; preserve explicit single transfer/system kind in the outer JSON (`singleKind()`), since changing a system tab is a user edit. Keep `baseline`, `markClean`, discard focus and pending-discard clearing at their existing load/save/continue boundaries. Initial route load sets baseline only after related rows are applied. The per-child request reconciler above updates only snapshot-excluded presentation/rule-default fields; online quote rate/date are excluded. Synchronous category last-use defaults occur inside the user's category-pick operation and therefore remain dirty as part of that user edit. No async `markClean()` call is added.

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
    kind: 'expense', convertBlockedReason: null, source: 'manual', locked: false };
  it.each([
    [{}, true, false], [{ eventTab: 'recurring' }, false, false], [{ eventTab: 'installment' }, false, false],
    [{ scheduleId: 4 }, false, false], [{ editing: true }, true, false],
    [{ kind: 'transfer' }, false, false], [{ kind: 'system' }, false, false],
    [{ source: 'schedule' }, false, false], [{ convertBlockedReason: '已有收還款' }, false, false], [{ locked: true }, false, false],
    [{ split: true, count: 2, convertBlockedReason: null, kind: 'refund' }, true, false],
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
import { newChild, ChildDraft, ParentDraft, normalizePosted } from './split-draft';
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
      onlineFx: d.fx_source === 'fx_api' && d.original_amount !== null && d.original_currency !== null
      ? { originalAmount: d.original_amount.replace(/^[+-]/, ''), originalCurrency: d.original_currency,
          amountExpr: fx?.original_amount ?? '' } : undefined,
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
    entryDate: first.entryDate, entryTime: first.entryTime,
    postedDate: normalizePosted(first.entryDate, first.postedDate), dateTouched: false,
  } };
}
export interface AddMode {
  split: boolean; count: number; scheduleId: number | null; eventTab: string; editing: boolean;
  kind: string; convertBlockedReason: string | null; source: string; locked: boolean;
}
export function addAvailability(m: AddMode): { visible: boolean; disabled: boolean; hint: string | null } {
  if (m.scheduleId !== null || (!m.editing && m.eventTab !== 'single')) return { visible: false, disabled: false, hint: null };
  if (m.locked) return { visible: false, disabled: false, hint: '此記錄不能拆帳' };
  if (!m.split && (!isWritableKind(m.kind) || m.convertBlockedReason !== null || m.source === 'schedule'))
    return { visible: false, disabled: false, hint: m.convertBlockedReason ?? '此記錄不能拆帳' };
  return { visible: true, disabled: m.count >= 50, hint: m.count >= 50 ? '最多 50 項' : null };
}
```

Protected rows never reach `fullChild` or `entryInputFromDetail`. Add this extractor to `entry-save.ts` beside existing `childFrom`:

```ts
export function feeAndDiscountFromDetail(detail: EntryDetail): { fee: ChildInput | null; discount: ChildInput | null } {
  return { fee: childFrom(detail.children ?? [], 'fee'), discount: childFrom(detail.children ?? [], 'discount') };
}
```

- [ ] **Step 3 — apply after all GETs:** In `applyLoaded`, before old `applyDetail`, branch on `!copy && group.kind==='split'`. Use the block below; otherwise initialize one `childFromDetail` and parent from the single record, retaining existing transfer copy/definition paths. The single **conversion-only** predicate is `is_settlement || !isWritableKind(kind) || transfer_group_id !== null || settled_by.length>0 || refunded_by.length>0 || loan_schedule != null || source==='schedule' || schedule?.posted_entry_ids.includes(id)`; store its hint in `convertBlockedReason`, keep single `ChildDraft.protected=false`, and let the server remain authoritative at convert. A locked single cannot add. Copies clear `id`, `loaded`, attached rules according to today's copy behavior, and anchor/group ids; they never accidentally convert their source.

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
  const c = this.drafts.current();
  if (!c || this.saving() || this.drafts.removeReason(c.key)) return;
  // Deletion must also work for an unfinished/invalid draft. Do not flush the discarded expression.
  this.drafts.remove(c.key);
}
onCategoryPicked(node: CategoryNode): void {
  this.setCategory(node); this.error.set(null);
  if (!this.isSplit() && this.groupId() === null && this.entryId() === null) {
    const last = readLastUse(node.id);
    const usable = (id: number | null | undefined): id is number =>
      id != null && this.accounts().some(a => a.id === id && !a.is_archived);
    if (usable(last?.account_id)) this.moveToAccount(last!.account_id);
    else if (usable(node.default_account_id)) this.moveToAccount(node.default_account_id);
    if (last) this.projectId.set(last.project_id);
    else if (node.default_project_id !== null) this.projectId.set(node.default_project_id);
  }
  afterNextRender(() => {
    const host = this.host.nativeElement;
    (host.querySelector<HTMLElement>('.amount-input') ?? host.querySelector<HTMLElement>('.amount-value'))?.focus();
  }, { injector: this.injector });
}
```

The amount value gets `tabindex="-1"` on phone. `addState` is a computed call to `addAvailability` with current schedule, entry source/convertBlockedReason/lock, child count and single kind. After removal, single layout selects the survivor and still retains `groupId` so saving uses dissolve.

- [ ] **Step 4 — verify:** Focused load tests plus component tests for opened protected child and two archived originals; full suite/build before the vertical integration commit. Reset/continue initializes a fresh UUID child and new parent, clears `anchorId/groupId/droppedNotices/sheetOwner/convertBlockedReason`, then calls the existing clean/reset flow. Do not clear the transfer panel's own draft key or schedule key from dirty registration.
### Task 6: Bubble strip, child tiles, parent totals and notices

**Files:** New `entry-form/split-summary.ts`, `split-summary.spec.ts`; modify `category-picker.ts:41`, `category-picker.html:1`, `category-picker.scss` (strip); `entry-form.html:77` (kind tabs), `:146` (picker/old lines), `:167` (tiles), `:332` (keypad), `entry-form.scss`; tests `category-picker.spec.ts:98`, `entry-form.spec.ts`.

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

In the form template: Render `app-category-picker` only in ordinary writable/split child modes. Single transfer/system modes render only `<p class="split-unavailable">此記錄不能拆帳</p>`; they never render an expense bubble or a second picker. Keep the transfer panel's own picker unchanged. Bind `[bubbles]="bubbles()" [activeBubble]="selected()" [parentText]="isSplit() ? parentText() : null" [addVisible]="addState().visible" [addDisabled]="addState().disabled" [addHint]="addState().hint" [gridAllowed]="!parentMode() && !protectedChild() && kind() !== 'transfer' && !isSystem()" (bubbleSelected)="selectBubble($event)" (addChild)="addChild()"`. Hide kind tabs in parent mode; disable transfer/system tabs in split mode and every tab for a protected child. Show the protected child's actual kind/reason above financial fields.

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
  // Invalidate all date-dependent FX/rule requests; the reconciler restarts every surviving child.
  for (const c of this.children()) {
    if (c.fx?.use_online) this.drafts.accept({ key: c.key, generation: c.generation }, {
      fx: { ...c.fx, fx_rate: null, rate_date: null },
    });
    this.drafts.invalidate(c.key);
  }
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
    expect(memberError({ error: { code: 'conflict', message: 'retry' } }, ['a', 'b'])).toBeNull();
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
    const match = /(?:^|\.)members\.(\d+)\.(?:(?:SplitMemberIn|SplitKeepIn|function-after\[[^\]]+\])\.)*(.+)$/.exec(path);
    const key = match ? keys[Number(match[1])] : undefined;
    if (match && key !== undefined) return { key, field: match[2], message };
  }
  return null;
}
```

- [ ] **Step 3 — replace single/split branch:** Import `shareReplay` for request-local duplicate-name coalescing.  In `save()` after `createdScheduleId` handling and BEFORE `const problem = this.validate()`, insert:

```ts
if (this.childScope()) {
  if (this.locked() || this.definitionLocked()) { this.error.set('MOZE 匯入資料，切換後可編輯'); return; }
  if (this.unsupported()) { this.error.set(this.unsupported()); return; }
  this.saveChildren(continuous);
  return;
}
```

Thus parent mode never evaluates current-child account/amount fallbacks. `saveChildren` validates every nonprotected child via `fullChild`, including the manual-FX guard. Ordinary single/schedule/transfer/system retain existing `validate()` and dispatch. Leave schedule, transfer, system branches intact, but enter them only when `!isSplit() && groupId()===null` (definition edit remains its earlier branch). Replace the old `splitGroup/members` save branch with a `saveChildren(continuous)` method. Before it sets `saving=true`, validate all editable children with `fullChild` (parties may be unresolved; check non-empty counterparty name separately); on the first failure select that child and show its error. Do not validate only the currently selected/parent view. Disable form fieldset, bubble/add/remove and all sheet changes while saving so submitted data cannot drift.

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
  const partyRequests = new Map<string, Observable<number>>();
  const resolveName = (typed: string): Observable<number> => {
    const name = typed.trim();
    let request = partyRequests.get(name);
    if (!request) {
      request = resolveCounterpartyId(this.accounting, name, this.counterparties(), party => {
        if (this.loadId === loadId && owners.every(o => this.children().some(c => c.key === o.key && c.generation === o.generation)))
          this.counterparties.update(rows => rows.some(p => p.id === party.id) ? rows : [...rows, party]);
      }).pipe(shareReplay({ bufferSize: 1, refCount: true }));
      partyRequests.set(name, request);
    }
    return request;
  };
  const resolutions = submitted.map(c => c.protected || (c.kind !== 'receivable' && c.kind !== 'payable')
    ? of([c.key, null] as const)
    : resolveName(c.counterpartyName).pipe(map(id => [c.key, id] as const)));
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

Add `saveResponseInvalid=signal(false)` and include it in save-disabled/guard conditions; explicit reload clears it. The UI reload action must go through DirtyFormRegistry if draft is dirty. Read the implemented §1.6 order at `f19e1fb`: schema 422 → group 404 → group_scheduled 409 → not-a-split 404 → locked_until_cutover 409 → DB-dependent cardinality 422 → member_not_found 404 → member_locked 409 → member validation 422 → retry 409; the final `abf1365` convert order is request schema/cardinality 422 → missing entry 404 → already_grouped 409 → entry_locked 409 → kind_not_splittable 409 (schedule source OR listed by a posted period) → locked_until_cutover 409 → member validation 422 → retry 409. Do not invent client-side precedence or a group_locked response contract. `409 retry`, `member_locked`, `already_grouped`, `entry_locked`, `kind_not_splittable`, `import_running`, `group_scheduled`, `locked_until_cutover` keep the draft and use translated `writeErrorMessage` messages (Task 7 revision below); never automatically resubmit a convert or silently reload away edits. Complete cancellation uses `finalize` guarded by `loadId` to reset saving after EMPTY/destroy. Do not call existing `write` here: it marks clean before mapping results and cannot route child errors.

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
  const fixture = render(detail({ id: 42, kind: 'receivable', is_settlement: true, amount: '40', group,
    group_members: [
      { ...entry({ id: 7, amount: '-100', group }), protected: false, protected_reason: null },
      { ...entry({ id: 42, kind: 'receivable', is_settlement: true, amount: '40', group }), protected: true, protected_reason: 'settlement' },
    ] }));
  expect(el(fixture).querySelector('.split-parent-card')?.textContent).toContain('多類別');
  expect(el(fixture).querySelectorAll('.split-child')).toHaveLength(2);
  expect(el(fixture).querySelector('.related')?.textContent ?? '').not.toContain('拆帳');
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

In `formEditable`, after the existing `detail.schedule && detail.group` refusal and before the kind check, add `if (detail.group?.kind === 'split' && !detail.locked) return true;`. Keep per-member settle/refund/repost/delete actions unchanged; editing a protected child now reaches metadata-only split form. Also replace `canEdit` with:

```ts
readonly canEdit = computed(() => {
  const detail = this.detail();
  return !!detail && !this.locked() &&
    (detail.group?.kind === 'split' || !detail.is_settlement || !!detail.schedule);
});
```

`edit()` keeps its schedule-first routing. For split groups, remove sibling rows only from `related()` (guard its sibling loop with `detail.group?.kind !== 'split'`); retain fee children, settlements, refunds and transfer counterpart relationships. Do not change ordinary single-entry action guards.

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
        <span>{{ member.name || member.category || kindLabels[member.kind] }} · {{ member.account_name }}</span>
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
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { EntryDetail, LedgerAccount } from '../../../models/accounting.model';
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
  let fixtureAccounts: LedgerAccount[];
  beforeEach(() => {
    entries.clear(); localStorage.clear();
    fixtureAccounts = [makeAccount(), makeAccount({ id: 2 })];
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([
      { path: 'accounting', component: Destination },
      { path: 'accounting/entry', component: EntryFormComponent },
      { path: 'accounting/entries/:id/edit', component: EntryFormComponent },
      { path: 'accounting/entries/:id', component: Destination },
    ])] });
    http = TestBed.inject(HttpTestingController);
    TestBed.inject(LayoutModeService).set('phone');
  });
  afterEach(() => { http.verify(); localStorage.clear(); vi.restoreAllMocks(); });
  function settle(): void {
    for (let round = 0; round < 8; round++) {
      harness.detectChanges();
      const requests = http.match(r => r.method === 'GET');
      for (const req of requests) {
        if (req.cancelled) continue;
        const path = req.request.url.replace('/api/accounting', '');
        if (path === '/accounts') req.flush(fixtureAccounts);
        else if (path === '/projects' || path === '/counterparties') req.flush([]);
        else if (path === '/preference') req.flush(makePreference());
        else if (path === '/fx-rate') req.flush({ date: req.request.params.get('date'), base: req.request.params.get('base'), quote: req.request.params.get('quote'), rate: '30', source: 'test' });
        else if (path === '/categories') req.flush([makeCategory({ id: 12, kind: req.request.params.get('kind') ?? 'expense' })]);
        else if (/^\/accounts\/\d+$/.test(path)) req.flush(makeAccountDetail(fixtureAccounts.find(a => a.id === Number(path.split('/').pop()))));
        else if (/^\/entries\/\d+$/.test(path)) {
          const detail = entries.get(Number(path.split('/').pop()));
          if (!detail) throw new Error(`missing fixture ${path}`);
          req.flush(detail);
        } else throw new Error(`unexpected GET ${path}`);
      }
    }
  }
  function seedGroup(a: EntryDetail, b: EntryDetail): void {
    const group = { id: 4, kind: 'split' as const, name: null, merchant: null, description: null,
      count: 2, total: null, currency: 'TWD' };
    const members = [a, b].map(c => ({ ...c, protected: false, protected_reason: null }));
    entries.set(a.id, { ...a, group, group_members: members });
    entries.set(b.id, { ...b, group, group_members: members });
  }
  function clickKey(key: string): void {
    const button = harness.routeNativeElement!.querySelector<HTMLButtonElement>(`app-amount-keypad [data-key="${key}"]`);
    expect(button).not.toBeNull(); button!.click(); settle();
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
    const discarded = vi.fn();
    const registry = TestBed.inject(DirtyFormRegistry);
    registry.requestClose(discarded); settle();
    expect(registry.promptOpen()).toBe(true); expect(discarded).not.toHaveBeenCalled();
    const members = form.children().map((c, i) => ({ id: i + 7, client_key: c.key }));
    req.flush({ group_id: 4, member_ids: [7, 8], members });
    await harness.fixture.whenStable();
    expect(registry.promptOpen()).toBe(false);
    registry.confirmDiscard(); expect(discarded).not.toHaveBeenCalled();
  });
  it('switches loaded bubbles cleanly and flushes keypad to the old child', async () => {
    seedGroup(makeEntryDetail({ id: 7, amount: '-10' }), makeEntryDetail({ id: 8, amount: '-5' }));
    await open('/accounting/entries/7/edit');
    expect(form.isDirty()).toBe(false);
    form.selectBubble(form.children()[1].key); settle();
    expect(form.amountExpr()).toBe('5'); expect(form.isDirty()).toBe(false);
    form.selectBubble(form.children()[0].key); settle();
    expect(form.isDirty()).toBe(false);
    clickKey('+'); clickKey('2');
    const second = harness.routeNativeElement!.querySelector<HTMLButtonElement>(`[data-bubble="${form.children()[1].key}"]`)!;
    second.click(); settle(); // flush pending 10+2 before changing selected owner
    expect(form.children().map(c => c.amountExpr)).toEqual(['12', '5']);
    clickKey('C'); clickKey('9'); clickKey('↵');
    expect(form.children().map(c => c.amountExpr)).toEqual(['12', '9']);
    form.selectBubble('parent'); settle();
    expect(harness.routeNativeElement!.querySelector('app-amount-keypad')).toBeNull();
    expect(form.children().map(c => c.amountExpr)).toEqual(['12', '9']);
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
    expect(req.request.body).toMatchObject({ name: 'Parent', merchant: 'Shop', description: 'Note' });
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
  http.expectOne(r => r.method === 'POST').flush({ code: 'conflict', message: 'retry', trace_id: 'test' }, { status: 409, statusText: 'Conflict' });
  settle();
  expect(form.isDirty()).toBe(true);
  expect(form.error()).toBe('記錄剛被更新，請重新載入後再試');
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

Async interleaving acceptance is pinned to Task 4's concrete owner tests plus these implementation checks: a captured owner token is passed through account/category/FX/sheet/counterparty callbacks; account changes increment generation before clearing FX/fees/rules; switching preserves valid unselected callbacks; dependency changes/removal invalidate stale owners. Add Subject-based component tests to replace any current value-comparison assertions, and retain existing PR-2 schedule-only/transfer-only/target-only dirty tests. This is required evidence, not permission to discard failing legacy tests.

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

## Integration details pinned during self-review

The following code belongs to the named tasks above. Review revision R1 additionally pins the regression tests and per-finding dispositions below; it replaces the earlier selection-invalidation interpretation.

**Task 5 — existing editable single initialization:** Place after the split branch and before the legacy transfer/system/definition branches. On copy use current date/time, no provenance/id/anchor; reuse today's archived-account fallback. This block prevents an anchor from being removable and prevents `applyDetail` from truncating its time.

```ts
if (isWritableKind(loaded.detail.kind)) {
  const d = loaded.detail;
  const convertBlocked = d.is_settlement || d.transfer_group_id !== null || d.settled_by.length > 0 ||
    d.refunded_by.length > 0 || d.loan_schedule != null || d.source === 'schedule' ||
    (d.schedule?.posted_entry_ids.includes(d.id) ?? false);
  this.convertBlockedReason.set(!loaded.copy && convertBlocked ? '此記錄不能拆帳' : null);
  let c = childFromDetail(d, { protected: false, protected_reason: null }, this.preference().main_currency);
  if (loaded.copy) c = { ...c, id: null, loaded: null, protected: false, protectedReason: null,
    invoice: { number: '', random: '' } };
  this.children.set([c]); this.selected.set(c.key);
  this.groupId.set(null); this.drafts.anchorId.set(loaded.copy ? null : d.id);
  this.parent.set({ name: '', merchant: d.merchant ?? '', description: '',
    entryDate: loaded.copy ? todayIso() : d.entry_date,
    entryTime: loaded.copy ? nowTime() : d.entry_time,
    postedDate: loaded.copy ? null : normalizePosted(d.entry_date, d.posted_date), dateTouched: false });
  if (isWritableKind(d.kind)) this.singleKind.set(d.kind);
  this.locked.set(!loaded.copy && d.locked);
  if (loaded.copy && this.accounts().find(a => a.id === c.accountId)?.is_archived)
    this.moveToAccount(this.accounts().find(a => !a.is_archived)?.id ?? null);
  this.loading.set(false); this.markClean();
  return;
}
```

**Task 4 — type-safe form kind adapter:** Preserve `FormKind`/schedule inputs without widening `WritableEntryKind`. For a protected noneditable child, the form's layout adapter uses `system`, but the visible disabled strip/tab label uses its real `ENTRY_KIND_LABELS` and the system balance editor never renders (`isSystem` gates on scope). Ordinary kind tabs render for writable kinds only; protected noneditable children show a single disabled actual-kind tab instead of a misleading selected system tab.

```ts
readonly convertBlockedReason = signal<string | null>(null);
readonly singleKind = signal<FormKind>('expense');
readonly childScope = computed(() => this.isSplit() || this.groupId() !== null);
readonly kind: ChildView<FormKind> = Object.assign(
  () => {
    const c = this.drafts.current();
    return this.childScope() ? (c && isWritableKind(c.kind) ? c.kind : 'system') : this.singleKind();
  },
  {
    set: (value: FormKind) => {
      const c = this.drafts.current(), owner = this.drafts.capture();
      if (this.childScope()) {
        if (c && owner && !c.protected && isWritableKind(value)) this.drafts.accept(owner, { kind: value });
      } else {
        this.singleKind.set(value);
        if (owner && isWritableKind(value)) this.drafts.accept(owner, { kind: value });
      }
    },
    update: (fn: (value: FormKind) => FormKind) => this.kind.set(fn(this.kind())),
  },
);
readonly isSystem = computed(() => !this.childScope() && this.kind() === 'system');
readonly categoryKind = computed(() => {
  const c = this.drafts.current();
  return this.childScope() ? (c && isWritableKind(c.kind) ? c.kind : null) : categoryKindFor(this.kind());
});
tabDisabled(kind: FormKind): boolean {
  if (this.childScope()) return this.parentMode() || this.protectedChild() || kind === 'transfer' || kind === 'system';
  if (this.scheduleId() !== null) return kind !== this.kind();
  return this.editing() && (kind === 'system' || (kind === 'transfer') !== (this.kind() === 'transfer'));
}
selectKind(kind: FormKind): void {
  if (kind === this.kind() || this.tabDisabled(kind)) return;
  const owner = this.drafts.capture();
  if (owner && !this.flushChild(owner)) return;
  if (owner) this.drafts.invalidate(owner.key);
  this.kind.set(kind); this.setCategory(null); this.error.set(null);
  if (!this.eventTabEnabled(this.scheduleDraft().tab)) this.selectEventTab('single');
  afterNextRender(() => this.categoryPicker()?.reopen(), { injector: this.injector });
}
```

The split child template condition is `childScope() || kind() !== 'transfer'`; the transfer panel condition is `!childScope() && kind()==='transfer'`. The parent form never calls either single-system or transfer serialization. A one-child existing group also remains in `childScope` for keep/dissolve semantics.

**Task 9 — concrete interleaved account callback regression:** Add `vi` to the Vitest imports, `Subject` from RxJS, `AccountDetail` model, `AccountingService` and `makeRule` fixture imports. Insert this test in the Task 9 harness. It exercises actual form subscription code with identical account/kind siblings, not only the store's predicate.

```ts
it('retains outgoing defaults, isolates equal-value siblings and drops removed owners', async () => {
  await open(); fill();
  const pending: Subject<AccountDetail>[] = [];
  const spy = vi.spyOn(TestBed.inject(AccountingService), 'getAccount').mockImplementation(() => {
    const response = new Subject<AccountDetail>(); pending.push(response); return response;
  });
  const first = form.children()[0].key;
  form.drafts.invalidate(first); settle();
  expect(pending).toHaveLength(1);
  form.addChild(); settle();
  expect(pending).toHaveLength(2);
  pending[0].next(makeAccountDetail({ reward_rules: [makeRule({ id: 99, is_basic: true })] }));
  pending[0].complete(); settle();
  expect(form.children()[0].ruleIds).toEqual([99]);
  expect(form.children()[1].ruleIds).toEqual([]);
  form.removeChild(); settle();
  pending[1].next(makeAccountDetail({ reward_rules: [makeRule({ id: 6, is_basic: true })] }));
  pending[1].complete(); settle();
  expect(form.children()).toHaveLength(1);
  expect(form.children()[0].ruleIds).toEqual([99]);
  expect(form.draftLoadsPending()).toBe(false);
  spy.mockRestore();
});
it('parent Escape uses the existing discard prompt', async () => {
  await open(); fill(); form.addChild(); settle(); form.selectBubble('parent'); settle();
  harness.routeNativeElement!.querySelector('.entry-form')!.dispatchEvent(
    new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
  settle();
  expect(TestBed.inject(DirtyFormRegistry).promptOpen()).toBe(true);
});
```

**Task 6 — delete-group prompt implementation:** Use the component's existing overlay focus utilities; capture opener in `openDeleteGroup(event)` and call it from the parent delete button instead of setting the signal directly. This is an explicit delete confirmation, not a backend permission bypass.

```ts
readonly deleteGroupPrompt = signal(false);
private deleteGroupOpener: HTMLElement | null = null;
openDeleteGroup(event: Event): void {
  if (this.groupId() === null || this.saving() || this.locked()) return;
  this.deleteGroupOpener = event.currentTarget as HTMLElement;
  this.deleteGroupPrompt.set(true);
  afterNextRender(() => this.host.nativeElement.querySelector<HTMLButtonElement>('.delete-group-cancel')?.focus(), { injector: this.injector });
}
closeDeleteGroup(): void {
  this.deleteGroupPrompt.set(false);
  restoreOverlayFocus(this.deleteGroupOpener, this.host.nativeElement); this.deleteGroupOpener = null;
}
onDeleteGroupKey(event: KeyboardEvent): void {
  if (isHandledKey(event)) return;
  if (event.key === 'Escape') { event.preventDefault(); this.closeDeleteGroup(); }
  else if (event.key === 'Tab') trapFocus(event.currentTarget as HTMLElement, event);
}
confirmDeleteGroup(): void {
  const id = this.groupId();
  if (id === null || this.saving() || this.locked()) return;
  this.write(this.accounting.deleteSplit(id), () => { this.deleteGroupPrompt.set(false); this.leave(); });
}
```

```html
@if (deleteGroupPrompt()) {
  <section class="delete-group-dialog" data-overlay role="alertdialog" aria-modal="true" aria-label="刪除整組記錄？" (keydown)="onDeleteGroupKey($event)">
    <p>刪除整組記錄？</p>
    <button type="button" class="delete-group-cancel" [disabled]="saving()" (click)="closeDeleteGroup()">取消</button>
    <button type="button" [disabled]="saving()" (click)="confirmDeleteGroup()">刪除整組</button>
  </section>
}
```

## Review revision R1 — executable regressions and dispositions

Source: owner attachment `plan-review-c31eaa6.md` on AGENT-67, comment `01a11408-ac49-7bf0-96dd-7fccc7a32036`. All changes below revise plan code only. §1.6 was re-read at `f19e1fb`; backend remains owner-owned. Tests here are implementation instructions, not a claim that application tests have run during planning.

### Task 3 additions — single compatibility and parent date semantics (findings 1–3, 5–6)

Add to `split-save.spec.ts`, importing `fullChild`, `permittedRuleIds`, `childFromDetail`, `makeEntryDetail`, `makeRule` and `normalizePosted`:

```ts
it('single originals remain editable; conversion eligibility is separate', () => {
  const d = makeEntryDetail({ id: 7, settled_by: [makeEntryDetail({ id: 8, is_settlement: true })] });
  const c = childFromDetail(d, { protected: false, protected_reason: null }, 'TWD');
  c.name = 'metadata edit';
  const p = { ...parent(), entryDate: d.entry_date, entryTime: d.entry_time,
    postedDate: normalizePosted(d.entry_date, d.posted_date) };
  expect(planEntrySave([c], p, accounts, parties, { entryId: 7, groupId: null }))
    .toMatchObject({ kind: 'update', input: { name: 'metadata edit' } });
});
it('normalizes parent posting date but preserves untouched child raw dates', () => {
  const d = makeEntryDetail({ id: 7, entry_date: '2026-01-01', posted_date: '2026-01-01', entry_time: '12:31:09.123' });
  const c = childFromDetail(d, { protected: false, protected_reason: null }, 'TWD');
  const p = { ...parent(), entryDate: d.entry_date, entryTime: d.entry_time,
    postedDate: normalizePosted(d.entry_date, d.posted_date) };
  expect(p.postedDate).toBeNull();
  p.entryDate = '2026-02-01';
  expect(fullChild(c, p, accounts, null, true)).toMatchObject({ entry_date: '2026-02-01', posted_date: null, entry_time: '12:31:09.123' });
  expect(fullChild(c, p, accounts, null, false)).toMatchObject({ entry_date: '2026-01-01', posted_date: '2026-01-01' });
  p.dateTouched = true;
  for (const postedDate of [null, '', '2026-02-01'])
    expect(fullChild(c, { ...p, postedDate }, accounts, null, false).posted_date).toBeNull();
  expect(fullChild(c, { ...p, postedDate: '2026-02-03' }, accounts, null, false).posted_date).toBe('2026-02-03');
});
it('rejects incomplete manual FX instead of sending it as online', () => {
  const c = child();
  c.fx = { original_amount: '10', original_currency: 'USD', account_currency: 'TWD',
    use_online: false, manual: 'rate', fx_rate: null, amount: null, rate_date: null };
  expect(() => fullChild(c, parent(), accounts, null, false)).toThrow('請輸入匯率或轉換後金額');
  c.fx.manual = 'amount';
  expect(() => fullChild(c, parent(), accounts, null, false)).toThrow('請輸入匯率或轉換後金額');
});
it.each(['receivable', 'payable'] as const)('single %s keeps the existing no-merchant rule', kind => {
  const c = { ...child(), kind, counterpartyId: 4 };
  expect(fullChild(c, { ...parent(), merchant: 'must not leak' }, accounts, 4, true).merchant).toBeNull();
});
it('preserves attached rules only on the original account, otherwise filters by account/date', () => {
  const c = childFromDetail(makeEntryDetail({ rules: [makeRule({ id: 99, is_enabled: false })] }),
    { protected: false, protected_reason: null }, 'TWD');
  c.ruleIds = [99, 10, 11];
  c.availableRules = [makeRule({ id: 10, account_id: 2 }), makeRule({ id: 11, account_id: 2, ends_on: '2020-01-01' })];
  expect(permittedRuleIds(c, '2026-01-01')).toContain(99);
  c.accountId = 2;
  expect(permittedRuleIds(c, '2026-01-01')).toEqual([10]);
});
```

On account change, `moveToAccount` first captures the old currency, then invalidates only the selected child's generation, clears that child's `availableRules/ruleIds`, and applies its existing FX/fee currency-reset logic. For new/copy children reset `rulesTouched=false`; loaded children remain touched and may reselect new-account rules. The reconciler intersects selected ids with date-valid new-account rules; attached exceptions apply only when `loaded.originalAccountId === accountId`. `permittedRuleIds` must additionally filter `availableRules` by `rule.account_id===c.accountId` before calling `rulesForDate`, so stale account caches cannot pass a rule through.

### Task 7 additions — translated errors with implemented envelopes (finding 7)

**Files:** `http-errors.ts:33` (`CONFLICT_MESSAGES`), `:44` (`writeErrorMessage`), `http-errors.spec.ts`; consume `schedule-math.ts:22` (`isImportRunning`) and `IMPORT_RUNNING_TOAST`.

```ts
// Add to CONFLICT_MESSAGES; keep existing schedule entries and locked_until_cutover handling.
retry: '記錄剛被更新，請重新載入後再試',
member_locked: '子項已受保護，請重新載入後再試',
already_grouped: '此記錄已屬於群組，請重新載入',
entry_locked: '此記錄目前不能拆帳',
kind_not_splittable: '此記錄類型不能拆帳',
import_running: IMPORT_RUNNING_TOAST,
group_scheduled: '排程產生的群組不可在此修改',
// First line of writeErrorMessage, importing both from './schedule-math':
if (isImportRunning(err)) return IMPORT_RUNNING_TOAST;
```

```ts
it.each([
  ['retry', '記錄剛被更新，請重新載入後再試'],
  ['member_locked', '子項已受保護，請重新載入後再試'],
  ['already_grouped', '此記錄已屬於群組，請重新載入'],
  ['entry_locked', '此記錄目前不能拆帳'],
  ['kind_not_splittable', '此記錄類型不能拆帳'],
  ['import_running', '匯入進行中，請稍後再試'],
  ['group_scheduled', '排程產生的群組不可在此修改'],
  ['locked_until_cutover', 'MOZE 匯入資料，切換後可編輯'],
])('translates the real shared-lib 409 envelope: %s', (message, translated) => {
  expect(writeErrorMessage(new HttpErrorResponse({ status: 409,
    error: { code: 'conflict', message, trace_id: 'test-trace' } }))).toBe(translated);
});
// split-result.spec.ts:
it('normalizes discriminated member loc before field routing', () => {
  expect(memberError({ error: { detail: [
    { loc: ['body', 'members', 1, 'SplitMemberIn', 'amount'], msg: 'invalid amount' },
  ] } }, ['a', 'b'])).toEqual({ key: 'b', field: 'amount', message: 'invalid amount' });
});
```

409 always retains draft/dirty state; no silent reread or auto-resubmit. `locked_until_cutover` is the implemented cutover refusal, distinct from transient `import_running`. Group/member 404, limit 422, and post-lock retry are separate server stages; client error routing does not reorder them.

### Task 9 additions — missing form-level acceptance (findings 1–3, 11–12, 18–19)

Insert all tests below inside the existing Task 9 harness `describe`, using its `seedGroup`, `clickKey`, `fixtureAccounts`, `entries`, `open`, `fill`, `settle`, `http`, `harness`, `form`. Add imports `AccountDetail`, `CategoryNode`, `FxRateOut`, `Subject`, `AccountingService`, `makeRule`, `LAST_USE_PREFIX`; restore mocks in `afterEach`. Extend `settle` with `/fx-rate` → `{date:req.request.params.get('date'),base:req.request.params.get('base'),quote:req.request.params.get('quote'),rate:'30',source:'test'}` so normal quotes complete. Tests that examine stale quotes remove/capture the request before `settle`, then release it explicitly.

```ts
it.each(['settled', 'refunded', 'loan'] as const)('keeps %s single editing but hides convert plus', async marker => {
  const d = makeEntryDetail({ id: 7 });
  if (marker === 'settled') d.settled_by = [makeEntryDetail({ id: 8, is_settlement: true })];
  if (marker === 'refunded') d.refunded_by = [makeEntryDetail({ id: 8, kind: 'refund' })];
  if (marker === 'loan') {
    d.kind = 'payable'; d.counterparty = 'Alan'; d.counterparty_id = 4;
    d.loan_schedule = {
      definition_id: 12, name: '信貸 每月還款', status: 'active', posting_mode: 'auto', posted_count: 3, times: 36,
      next_due_date: '2027-02-09', next_amount: [{ currency: 'TWD', amount: '-8953.0000' }],
      remaining: '-275001.0000', repaid: '24999.0000', needs_check: false,
    };
  }
  entries.set(7, d); await open('/accounting/entries/7/edit');
  if (marker === 'loan') form.counterparties.set([{ id: 4, name: 'Alan', open_amounts: [], moze_id: null }]);
  expect(form.children()[0].protected).toBe(false);
  expect(form.addState().visible).toBe(false);
  form.name.set('changed'); form.save(false);
  const req = http.expectOne(r => r.method === 'PUT' && r.url.endsWith('/entries/7'));
  expect(req.request.body.name).toBe('changed');
  req.flush(makeEntryDetail({ id: 7, name: 'changed' })); await harness.fixture.whenStable();
});
it('parent validates each child and blocks missing manual FX before HTTP', async () => {
  await open(); fill(); form.addChild(); settle(); fill('20');
  form.fx.set({ original_amount: '20', original_currency: 'USD', account_currency: 'TWD',
    use_online: false, manual: 'rate', fx_rate: null, amount: null, rate_date: null });
  const invalidKey = form.selected(); form.selectBubble('parent'); settle(); form.save(false);
  http.expectNone(r => r.method === 'POST' || r.method === 'PUT');
  expect(form.selected()).toBe(invalidKey);
  expect(form.error()).toBe('請輸入匯率或轉換後金額');
});
it('plus opens main grid; picking focuses phone amount and keeps inherited account', async () => {
  await open(); fill();
  localStorage.setItem(LAST_USE_PREFIX + '12', JSON.stringify({ account_id: 2, project_id: 9 }));
  form.addChild(); settle();
  const root = harness.routeNativeElement!;
  expect(root.querySelector('app-category-picker .grid')).not.toBeNull();
  root.querySelector<HTMLButtonElement>('app-category-picker .grid .cat')!.click(); settle();
  expect(root.querySelector('app-category-picker .grid')).toBeNull();
  expect(document.activeElement).toBe(root.querySelector('.amount-value'));
  expect(form.accountId()).toBe(1); expect(form.projectId()).toBeNull();
});
it('mixed kinds select their own tabs, with transfer and system disabled', async () => {
  seedGroup(makeEntryDetail({ id: 7, kind: 'expense' }), makeEntryDetail({ id: 8, kind: 'income', amount: '30' }));
  await open('/accounting/entries/7/edit');
  expect(form.kind()).toBe('expense');
  form.selectBubble(form.children()[1].key); settle();
  expect(form.kind()).toBe('income');
  const tabs = [...harness.routeNativeElement!.querySelectorAll<HTMLButtonElement>('.kind-tab')];
  expect(tabs.find(t => t.getAttribute('aria-selected') === 'true')?.textContent).toContain('收入');
  for (const word of ['轉帳', '系統']) expect(tabs.find(t => t.textContent?.includes(word))?.disabled).toBe(true);
});
it('changing A account does not clear B FX, fee or rules', async () => {
  await open(); fill(); form.addChild(); settle(); fill('20');
  form.fx.set({ original_amount: '20', original_currency: 'USD', account_currency: 'TWD', use_online: false,
    manual: 'amount', amount: '600', fx_rate: null, rate_date: null });
  form.fee.set({ amount: '2', name: null }); form.ruleIds.set([99]); form.rulesTouched.set(true);
  const b = structuredClone(form.children()[1]);
  form.selectBubble(form.children()[0].key); form.chooseAccount(2); settle();
  expect(form.children()[1]).toEqual(b);
});
it('each of two archived originals retains only its own archived option', async () => {
  fixtureAccounts = [makeAccount(), makeAccount({ id: 3, is_archived: true }), makeAccount({ id: 4, is_archived: true })];
  seedGroup(makeEntryDetail({ id: 7, account_id: 3 }), makeEntryDetail({ id: 8, account_id: 4 }));
  await open('/accounting/entries/7/edit');
  expect(form.accountOptions().map(a => a.id)).toEqual([1, 3]);
  form.selectBubble(form.children()[1].key); settle();
  expect(form.accountOptions().map(a => a.id)).toEqual([1, 4]);
});
it('existing anchor round-trips metadata and all edited parent dates through plus', async () => {
  entries.set(7, makeEntryDetail({ id: 7, name: 'child', description: 'note', merchant: 'old shop',
    entry_date: '2026-01-01', entry_time: '01:02:03.456', posted_date: '2026-01-01' }));
  await open('/accounting/entries/7/edit');
  form.setParentDate('entryDate', '2026-02-01'); form.setParentDate('entryTime', '11:12');
  form.setParentDate('postedDate', '2026-02-03'); settle();
  form.addChild(); settle(); fill('20'); form.save(false);
  const req = http.expectOne(r => r.method === 'PUT' && r.url.endsWith('/entries/7/split'));
  expect(req.request.body.members[0]).toMatchObject({ id: 7, name: 'child', description: 'note', merchant: 'old shop' });
  for (const m of req.request.body.members) expect(m).toMatchObject({ entry_date: '2026-02-01', entry_time: '11:12', posted_date: '2026-02-03' });
  req.flush({ detail: [{ loc: ['body', 'members.1.amount'], msg: 'test rejection' }] }, { status: 422, statusText: 'Unprocessable Entity' });
  form.removeChild(); settle(); form.merchant.set('new shop'); form.save(false);
  const single = http.expectOne(r => r.method === 'PUT' && r.url.endsWith('/entries/7'));
  expect(single.request.body).toMatchObject({ name: 'child', description: 'note', merchant: 'new shop', posted_date: '2026-02-03' });
  single.flush(makeEntryDetail({ id: 7 })); await harness.fixture.whenStable();
});
it('untouched single keeps time precision and derives posted date after changing date', async () => {
  entries.set(7, makeEntryDetail({ id: 7, entry_date: '2026-01-01', posted_date: '2026-01-01', entry_time: '12:31:09.123' }));
  await open('/accounting/entries/7/edit');
  expect(form.parent().postedDate).toBeNull();
  form.setParentDate('entryDate', '2026-02-01'); settle(); form.save(false);
  const req = http.expectOne(r => r.method === 'PUT');
  expect(req.request.body).toMatchObject({ entry_date: '2026-02-01', posted_date: null, entry_time: '12:31:09.123' });
  req.flush(makeEntryDetail({ id: 7 })); await harness.fixture.whenStable();
});
it('a removed child cannot apply its category response to the survivor', async () => {
  await open(); fill();
  const pending = new Subject<CategoryNode[]>();
  const spy = vi.spyOn(TestBed.inject(AccountingService), 'getCategories').mockReturnValue(pending);
  form.addChild(); settle(); form.selectKind('income'); settle();
  const removed = form.selected(); form.removeChild(); settle();
  pending.next([makeCategory({ id: 99, kind: 'income' })]); pending.complete(); settle();
  expect(form.children().some(c => c.key === removed)).toBe(false);
  expect(form.category()?.id).toBe(12); expect(form.kind()).toBe('expense');
  expect(form.categories().some(c => c.id === 99)).toBe(false);
  spy.mockRestore();
});
it('a removed child cannot apply its FX response to the survivor', async () => {
  await open(); fill(); form.addChild(); settle(); fill('20');
  const pending = new Subject<FxRateOut>();
  const spy = vi.spyOn(TestBed.inject(AccountingService), 'getFxRate').mockReturnValue(pending);
  form.fx.set({ original_amount: '20', original_currency: 'USD', account_currency: 'TWD', use_online: true,
    manual: null, amount: null, fx_rate: null, rate_date: null });
  form.drafts.invalidate(form.selected()); settle();
  expect(spy).toHaveBeenCalledTimes(1);
  form.removeChild(); settle();
  pending.next({ date: '2026-10-06', base: 'USD', quote: 'TWD', rate: '999', source: 'test' }); pending.complete(); settle();
  expect(form.children()).toHaveLength(1); expect(form.fx()).toBeNull();
  spy.mockRestore();
});
it('shows inconsistent dates and read-only child merchant', async () => {
  seedGroup(makeEntryDetail({ id: 7, entry_date: '2026-01-01', merchant: 'member shop' }),
    makeEntryDetail({ id: 8, entry_date: '2026-02-01' }));
  await open('/accounting/entries/7/edit');
  expect(harness.routeNativeElement!.textContent).toContain('商家（此項）');
  expect(harness.routeNativeElement!.textContent).toContain('member shop');
  form.selectBubble('parent'); settle();
  expect(harness.routeNativeElement!.textContent).toContain('子項日期不一致');
});
it('reselecting a bubble keeps its caches and invalid draft removal is allowed', async () => {
  await open(); fill(); const categories = form.categories();
  form.selectBubble(form.selected()); expect(form.categories()).toBe(categories);
  form.addChild(); settle(); form.onAmountInput('10+'); form.removeChild(); settle();
  expect(form.children()).toHaveLength(1);
});
it('creates a duplicate new counterparty name once per submission', async () => {
  await open(); form.selectKind('receivable'); settle(); fill(); form.counterpartyName.set('Alan');
  form.addChild(); settle(); fill('20'); form.counterpartyName.set(' Alan '); form.save(false);
  const party = http.expectOne(r => r.method === 'POST' && r.url.endsWith('/counterparties'));
  party.flush({ id: 4, name: 'Alan', open_amounts: [], moze_id: null });
  const split = http.expectOne(r => r.method === 'POST' && r.url.endsWith('/splits'));
  expect(split.request.body.members.map((c: { counterparty_id: number }) => c.counterparty_id)).toEqual([4, 4]);
  split.flush({ detail: [{ loc: ['body', 'members.1.amount'], msg: 'test rejection' }] }, { status: 422, statusText: 'Unprocessable Entity' });
});
```

### Task 9 migration inventory (finding 10)

All anchors below are at base `4e301e2`. Rewrite each existing test rather than deleting its behavioral assertion; remove only the component-specific tests with the deleted split-lines component.

| Base test anchor | Replacement and retained contract |
|---|---|
| `entry-form.spec.ts:640–692` loaded/member race | `children.length===2`/bubble count; related GET still blocks saving; stale route result cannot replace current child; response contains client_key/id envelope |
| `entry-form.spec.ts:719` transfer layout | Keep no `app-category-picker` assertion; add `.split-unavailable` hint; transfer panel's picker unaffected |
| `entry-form.spec.ts:841–878` single plus / old sheet / reload | Editable single plus enabled; protected conversion hint; old modal Escape becomes delete-group dialog; reload dissolved entry resets children to one and groupId null |
| `entry-form.spec.ts:1031–1132` split saves/new-id assumptions | Stable 7/8 ids + client keys; raw `19:00:00`; navigate to opened id 8; detail closeTo list preserved; parent date change updates each full member and derives equal posting date |
| `entry-form.spec.ts:1501` pending-close new split | Add via form.addChild, fill selected child; use existing `pendingClose()` before flushing full SplitResult; assert pending callback cleared |
| `entry-form.spec.ts:1556` PR-2 overlay table | Replace old split modal row with loaded-group parent delete dialog, `.delete-group` opener, `.delete-group-dialog[data-overlay]`; retain wrap, opener focus, next Escape→discard |
| `entry-form.spec.ts:1816–1834` nested account picker | Open selected child `.account-tile .acct-trigger`; first Escape closes picker/restores trigger, next Escape prompts dirty form; no now-removed middle split-modal step |
| `entry-save.spec.ts:139` detail mapping | Expect raw `12:31:00` time; keep unsigned FX/fee metadata assertions |
| `category-picker.spec.ts:90–101,126–127` old plus | `addChild/addVisible/addDisabled`; preserve reopen/drill-in/focus contract and use active bubble focus selector |

### Task 2 key fallback / Task 5 last-use regression (findings 13, 17)

Use `childKey(source: {randomUUID?:()=>string}|undefined=globalThis.crypto)` so tests can simulate an insecure context without asserting a fake Crypto type. Add to `split-draft.spec.ts`:

```ts
it('makes unique short UI keys without secure-context randomUUID', () => {
  const keys = Array.from({ length: 100 }, () => childKey({}));
  expect(new Set(keys).size).toBe(100);
  expect(keys.every(key => key.length <= 64)).toBe(true);
});
```

Add to the Task 9 form harness:

```ts
it('falls back from archived last-use account and retains project without a default', async () => {
  fixtureAccounts.push(makeAccount({ id: 3, is_archived: true }));
  await open();
  localStorage.setItem(LAST_USE_PREFIX + '12', JSON.stringify({ account_id: 3, project_id: null }));
  form.onCategoryPicked(makeCategory({ id: 12, default_account_id: 2 })); settle();
  expect(form.accountId()).toBe(2);
  localStorage.removeItem(LAST_USE_PREFIX + '12'); form.projectId.set(9);
  form.onCategoryPicked(makeCategory({ id: 12, default_account_id: null, default_project_id: null })); settle();
  expect(form.projectId()).toBe(9);
});
```

Task 4 account transition insertion: at the start of `moveToAccount`, before its existing `const before = this.accountCurrency()` and before `accountId.set(id)`, insert:

```ts
if (id === this.accountId()) return;
const selected = this.drafts.current();
if (!selected || selected.protected) return;
this.drafts.invalidate(selected.key);
this.drafts.accept(this.drafts.capture()!, {
  availableRules: [], ruleIds: [], rulesTouched: selected.loaded !== null,
});
```

Remove the old trailing `chooseAccount` rule reset based on the form's `entryId`; the child's provenance now governs it. Keep the existing currency-specific clearing after this insertion. Add a currency-changing sheet result through a method that writes the captured owner and invalidates that owner only when `fx.original_currency`, `fx.account_currency`, manual/online choice or date dependencies changed. Amount/rate callbacks never invalidate themselves; newly started resources must not recursively restart on their own derived quote update.

Task 9 delete-group/account-picker overlay replacements use these executable harness tests (finding 10):

```ts
it('delete-group dialog traps focus, restores opener, then Escape asks about dirty parent', async () => {
  seedGroup(makeEntryDetail({ id: 7 }), makeEntryDetail({ id: 8 }));
  await open('/accounting/entries/7/edit'); form.selectBubble('parent'); form.setParentText('name', 'dirty'); settle();
  const root = harness.routeNativeElement!;
  const opener = root.querySelector<HTMLButtonElement>('.delete-group')!;
  opener.focus(); opener.click(); settle();
  const dialog = root.querySelector<HTMLElement>('.delete-group-dialog')!;
  expect(dialog.hasAttribute('data-overlay')).toBe(true);
  const buttons = [...dialog.querySelectorAll<HTMLButtonElement>('button')];
  buttons.at(-1)!.focus();
  buttons.at(-1)!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }));
  expect(document.activeElement).toBe(buttons[0]);
  buttons[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })); settle();
  expect(root.querySelector('.delete-group-dialog')).toBeNull(); expect(document.activeElement).toBe(opener);
  expect(TestBed.inject(DirtyFormRegistry).promptOpen()).toBe(false);
  opener.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })); settle();
  expect(TestBed.inject(DirtyFormRegistry).promptOpen()).toBe(true);
});
it('child account picker Escape restores trigger; next Escape prompts the dirty form', async () => {
  await open(); fill(); form.addChild(); settle(); fill('20');
  const root = harness.routeNativeElement!;
  const trigger = root.querySelector<HTMLButtonElement>('.account-tile .acct-trigger')!;
  trigger.focus(); trigger.click(); settle();
  const panel = root.querySelector('.account-tile .acct-panel')!;
  panel.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })); settle();
  expect(root.querySelector('.account-tile .acct-panel')).toBeNull(); expect(document.activeElement).toBe(trigger);
  expect(TestBed.inject(DirtyFormRegistry).promptOpen()).toBe(false);
  trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })); settle();
  expect(TestBed.inject(DirtyFormRegistry).promptOpen()).toBe(true);
});
```

### Per-finding disposition

| Finding | Disposition | Revised plan evidence |
|---|---|---|
| 1 · P1 | fixed | Task 5 single load sets protected=false; separate convertBlockedReason feeds plus only; R1 settled/refunded/loan PUT tests |
| 2 · P1 | fixed | normalizePosted at parent load, first-split comparison, parent/full serialization; untouched loaded child stays raw; R1 raw-time/date tests |
| 3 · P1 | fixed | save dispatches childScope before selected-field validate; fullChild validates manual FX; parent POST and incomplete-FX tests |
| 4 · P2 | fixed | select/add no generation bump; per-child resource reconciler; valid unselected callbacks retained, remove/dependency stale callbacks dropped; pending resources block save |
| 5 · P2 | fixed | account transition clears old rule selections; per-account/date filter; only unchanged-account attached ids exempt |
| 6 · P2 | fixed | single receivable/payable merchant remains null; per-kind serializer regression |
| 7 · P2 | fixed | f19e1fb §1.6 order; actual shared-lib 409 envelope; translated conflict map and isImportRunning reuse |
| 8 · P2 | fixed | formEditable and canEdit admit split metadata editing; test opens an is_settlement member |
| 9 · P2 | fixed | forbidden transfer/system single modes show hint only; no extra category-picker/stale expense bubble |
| 10 · P2 | fixed | explicit base-line legacy replacement inventory plus delete-dialog and child account-picker Escape tests |
| 11 · P2 | fixed | clean loaded-group switch assertions; actual keypad DOM; pending-close precondition and callback clearing; changing online quote in snapshot test; remove trivial parent assertion |
| 12 · P2 | fixed | R1 concrete grid/focus, mixed kind, sibling account isolation, archived originals, anchor field-map/date/time, removed category/FX callback and keypad tests |
| 13 · P2 | fixed | injectable short unique childKey fallback when crypto.randomUUID unavailable; collision/length test |
| 14 · P2 | fixed | WritableSignal<ParentDraft>; narrow d.kind before singleKind.set; template uses kindLabels[member.kind] |
| 15 · P3 | fixed | corrected base anchors for entry-draft, entry-save and entry-form.html |
| 16 · P3 | fixed | Task 3 Files/green step name new split-save.ts; old exports retire only in vertical integration |
| 17 · P3 | fixed | usable last-use account → default account fallback; retain project when no last-use/default; regression test |
| 18 · P3 | fixed | same-bubble no-op; remove invalid draft without flush; submission-local shared counterparty observable per trimmed name |
| 19 · P3 | fixed | union loc normalization; no duplicate split sibling related rows; dissolve parent fields asserted; mixedDates/member merchant tests; childFromDetail naming deviation explicit |

No disagreements. P1 re-review remains an owner gate. This table records changes to the plan, not implementation/test execution; compile and full application tests remain mandatory at implementation commits.

## PR-B final contract alignment — abf1365

Owner update: AGENT-67 comment `01a11415-6fe8-7dd9-8b1e-4a6bd5ba8d03`. API reference is PR #55 at `abf1365`, not a claim that it has merged. This amendment adds no backend/spec edits and does not clear the owner's P1 re-review gate.

### Tasks 3 / 5: unchanged payload is a wire invariant

`entry_time` is the exact loaded string, including seconds/fractional seconds, until the owner changes parent dates. Online rows (`fx_source='fx_api'`) always send `amount:null`, `fx_rate:null`; a cached display quote must not turn them into manual FX. If original amount/currency are untouched, retain their loaded unsigned value even when it has more decimal places than the usual input currency precision. `loaded.onlineFx` captures that provenance; `flushChild` does not round it just because of selection/save. A user-edited original amount follows the existing expression validation. This is frontend payload evidence only; PR-B owns the unchanged/meta/financial classification and absence of FX/write side effects.

Add to Task 3 `split-save.spec.ts` with its existing imports/fixtures:

```ts
it('re-sends unchanged online FX and raw seconds for no-op and metadata-only upsert', () => {
  const d = makeEntryDetail({ id: 7, account_id: 1, amount: '-370.3680',
    original_amount: '-12.3456', original_currency: 'USD', fx_source: 'fx_api', fx_rate: '30.0000',
    entry_date: '2026-10-01', posted_date: '2026-10-01', entry_time: '19:00:37.125' });
  const c = childFromDetail(d, { protected: false, protected_reason: null }, 'TWD');
  const p = { ...parent(), entryDate: d.entry_date, entryTime: d.entry_time, postedDate: null };
  for (const name of [c.name, 'metadata only']) {
    c.name = name;
    const input = toSplitInput([c], p, accounts, parties).members[0];
    expect(input).toMatchObject({ id: 7, name: name.trim() || null,
      entry_time: '19:00:37.125', posted_date: '2026-10-01',
      amount: null, fx_rate: null, original_amount: '12.3456', original_currency: 'USD' });
  }
});
```

Add to Task 9 harness to check the actual PUT body and `flushChild`, not only the pure serializer:

```ts
it('metadata PUT retains online wire mode and exact original quantity/time', async () => {
  seedGroup(makeEntryDetail({ id: 7, amount: '-370.3680', original_amount: '-12.3456',
    original_currency: 'USD', fx_source: 'fx_api', fx_rate: '30', entry_time: '19:00:37.125' }),
    makeEntryDetail({ id: 8 }));
  await open('/accounting/entries/7/edit'); form.name.set('metadata only'); form.save(false);
  const req = http.expectOne(r => r.method === 'PUT' && r.url.endsWith('/splits/4'));
  expect(req.request.body.members[0]).toMatchObject({ id: 7, entry_time: '19:00:37.125',
    amount: null, fx_rate: null, original_amount: '12.3456', original_currency: 'USD' });
  req.flush({ code: 'CONFLICT', message: 'retry: member changed before lock', trace_id: 'test' },
    { status: 409, statusText: 'Conflict' }); settle();
  expect(form.isDirty()).toBe(true);
  expect(form.error()).toBe('記錄剛被更新，請重新載入後再試');
});
```

### Task 7: shared-lib envelope and code-prefix translation

For 404/409 read the business code from `message`, not the generic HTTP category in `code`. `message` may be the bare business code or `<code>: <detail>` (PR-B `services/errors.py:CodedConflictError`). Replace the exact-string conflict lookup in `writeErrorMessage` with this block; retain existing 422 parsing, network fallback and the earlier `isImportRunning` check. `detail` string fallback remains only for compatibility with existing schedule tests. Do not match arbitrary partial names such as `retrying`.

```ts
const message = typeof body?.message === 'string' ? body.message :
  typeof detail === 'string' ? detail : null;
const messageCode = message?.split(':', 1)[0].trim();
if (err.status === 409 && messageCode === 'locked_until_cutover') return 'MOZE 匯入資料，切換後可編輯';
if (err.status === 409 && messageCode && Object.hasOwn(CONFLICT_MESSAGES, messageCode))
  return CONFLICT_MESSAGES[messageCode];
if (err.status === 404 && messageCode === 'member_not_found') return '找不到群組中的子項，請重新載入';
```

Add to `http-errors.spec.ts` (same imported `HttpErrorResponse` / `writeErrorMessage`):

```ts
it.each([
  [409, 'retry: member 7 changed', '記錄剛被更新，請重新載入後再試'],
  [409, 'member_locked: member 7 (settlement)', '子項已受保護，請重新載入後再試'],
  [409, 'already_grouped: entry 7 belongs to group 4', '此記錄已屬於群組，請重新載入'],
  [409, 'kind_not_splittable: entry 7 is listed by a posted schedule period', '此記錄類型不能拆帳'],
  [404, 'member_not_found: member 7', '找不到群組中的子項，請重新載入'],
])('reads message prefix for HTTP %i: %s', (status, message, expected) => {
  expect(writeErrorMessage(new HttpErrorResponse({ status,
    error: { code: status === 404 ? 'NOT_FOUND' : 'CONFLICT', message, trace_id: 'test' } }))).toBe(expected);
});
it('does not treat an arbitrary prefix as a known conflict code', () => {
  expect(writeErrorMessage(new HttpErrorResponse({ status: 409,
    error: { code: 'CONFLICT', message: 'retrying is unavailable', trace_id: 'test' } }))).toBe('retrying is unavailable');
});
```

### Task 5 / 9: posted-period membership blocks convert regardless of source

The single-load `convertBlockedReason` predicate now includes `d.schedule?.posted_entry_ids.includes(d.id)`. The read DTO's schedule link describes the posted period; a MOZE/import-booked row may have `source!='schedule'`. Do not set single `protected=true` or block existing single editing because of this membership. If the link is absent or becomes stale, the server's `409 kind_not_splittable` remains authoritative; preserve dirty draft and show its translated message without retrying or creating another split.

Add to Task 9's form harness:

```ts
it('hides convert for an import-booked posted period without protecting single editing', async () => {
  entries.set(7, makeEntryDetail({ id: 7, source: 'moze_backup', locked: false,
    schedule: { definition_id: 12, instance_id: 77, kind: 'recurring', seq: 1, times: null,
      name: '每月支出', is_partial: false, acted_by: 'import', posted_entry_ids: [7] } }));
  await open('/accounting/entries/7/edit');
  expect(form.children()[0].protected).toBe(false);
  expect(form.addState().visible).toBe(false);
  form.addChild(); expect(form.children()).toHaveLength(1);
});
it('keeps convert draft on authoritative posted-period refusal and does not retry', async () => {
  entries.set(7, makeEntryDetail({ id: 7, source: 'manual', schedule: null }));
  await open('/accounting/entries/7/edit'); form.addChild(); settle(); fill('20'); form.save(false);
  http.expectOne(r => r.method === 'PUT' && r.url.endsWith('/entries/7/split')).flush({
    code: 'CONFLICT', message: 'kind_not_splittable: entry 7 is listed by a posted schedule period', trace_id: 'test',
  }, { status: 409, statusText: 'Conflict' }); settle();
  expect(form.children()).toHaveLength(2); expect(form.isDirty()).toBe(true);
  expect(form.error()).toBe('此記錄類型不能拆帳');
  http.expectNone(r => r.method === 'POST' || r.method === 'PUT');
});
```

Acceptance additions: Tasks 3/9 wire-body tests pin unchanged raw time and online-FX inputs; Task 7 error tests pin 404/409 `message` prefix parsing; Tasks 5/9 distinguish schedule source from posted-period membership. These extend, rather than replace, the 19 R1 dispositions and existing human plan-review gate.
