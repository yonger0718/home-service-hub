import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';

import { EntryDetail } from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';

import { makeAccount, makeEntryDetail, makeRule } from '../testing/fixtures';
import { ChildDraft, newChild, newParent, normalizePosted } from './split-draft';
import { childFromDetail } from './split-load';
import { executeEntrySave, fullChild, permittedRuleIds, planEntrySave, toSplitInput } from './split-save';

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
    const a = child(7);
    const b = child(8);
    a.loaded = {
      entryDate: '2026-01-01', entryTime: '12:34:56.789', postedDate: '2026-01-03',
      merchant: 'legacy', attachedRuleIds: [99], originalAccountId: 1, source: 'manual', childKinds: [],
      signedAmount: '-10', signedBase: '-10', isSettlement: false, kind: 'expense', currency: 'TWD',
    };
    b.loaded = { ...a.loaded, entryDate: '2026-02-02', entryTime: '03:04:05', merchant: 'other' };
    a.ruleIds = [99];
    a.rulesTouched = true;
    const p = parent();
    const out = toSplitInput([a, b], p, accounts, parties);
    expect(out.members[0]).toMatchObject({
      id: 7, client_key: a.key, entry_date: '2026-01-01', entry_time: '12:34:56.789', posted_date: '2026-01-03',
      merchant: 'legacy', project_id: null, tags: [], reward_rule_ids: [99],
    });
    expect(out.members[1]).toMatchObject({ entry_date: '2026-02-02', entry_time: '03:04:05', merchant: 'other' });
    expect(out).toMatchObject({ project_id: null, tags: [], name: null, merchant: null, description: null });
    expect(toSplitInput([a, b], { ...p, entryTime: '11:11', dateTouched: true }, accounts, parties).members).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: 7, entry_time: '11:11' }),
        expect.objectContaining({ id: 8, entry_time: '11:11' }),
      ]),
    );
  });

  it('emits only keep metadata', () => {
    const c = { ...child(7), kind: 'refund' as const, protected: true, name: 'memo' };
    expect(toSplitInput([c], parent(), accounts, parties).members).toEqual([
      { id: 7, keep: true, client_key: c.key, name: 'memo', project_id: null, tags: [], description: null },
    ]);
  });

  it('refuses conversion without its anchor and dissolution without a persisted survivor', () => {
    expect(() => planEntrySave([child(), child()], parent(), accounts, parties, { entryId: 7, groupId: null })).toThrow();
    expect(() => planEntrySave([child(7), child(8)], parent(), accounts, parties, { entryId: 7, groupId: null })).toThrow();
    expect(() => planEntrySave([child()], parent(), accounts, parties, { entryId: 7, groupId: 4 })).toThrow();
    expect(() => planEntrySave([child(), child(3)], parent(), accounts, parties, { entryId: null, groupId: null })).toThrow();
  });

  // Planner-only (N4): the form never puts a single original into child scope; Task 9's form harness proves the
  // reachable single-original edit path.
  it('planner-only: a single original with settlements plans a plain update', () => {
    const d = makeEntryDetail({ id: 7, settled_by: [makeEntryDetail({ id: 8, is_settlement: true })] });
    const c = childFromDetail(d, { protected: false, protected_reason: null }, 'TWD');
    c.name = 'metadata edit';
    const p = {
      ...parent(), entryDate: d.entry_date, entryTime: d.entry_time,
      postedDate: normalizePosted(d.entry_date, d.posted_date),
    };
    expect(planEntrySave([c], p, accounts, parties, { entryId: 7, groupId: null }))
      .toMatchObject({ kind: 'update', input: { name: 'metadata edit' } });
  });

  it('normalizes parent posting date but preserves untouched child raw dates', () => {
    const d = makeEntryDetail({ id: 7, entry_date: '2026-01-01', posted_date: '2026-01-01', entry_time: '12:31:09.123' });
    const c = childFromDetail(d, { protected: false, protected_reason: null }, 'TWD');
    const p = {
      ...parent(), entryDate: d.entry_date, entryTime: d.entry_time,
      postedDate: normalizePosted(d.entry_date, d.posted_date),
    };
    expect(p.postedDate).toBeNull();
    p.entryDate = '2026-02-01';
    expect(fullChild(c, p, accounts, null, true)).toMatchObject({
      entry_date: '2026-02-01', posted_date: null, entry_time: '12:31:09.123',
    });
    expect(fullChild(c, p, accounts, null, false)).toMatchObject({ entry_date: '2026-01-01', posted_date: '2026-01-01' });
    p.dateTouched = true;
    for (const postedDate of [null, '', '2026-02-01']) {
      expect(fullChild(c, { ...p, postedDate }, accounts, null, false).posted_date).toBeNull();
    }
    expect(fullChild(c, { ...p, postedDate: '2026-02-03' }, accounts, null, false).posted_date).toBe('2026-02-03');
  });

  it('rejects incomplete manual FX instead of sending it as online', () => {
    const c = child();
    c.fx = {
      original_amount: '10', original_currency: 'USD', account_currency: 'TWD',
      use_online: false, manual: 'rate', fx_rate: null, amount: null, rate_date: null,
    };
    expect(() => fullChild(c, parent(), accounts, null, false)).toThrow('請輸入匯率或轉換後金額');
    c.fx.manual = 'amount';
    expect(() => fullChild(c, parent(), accounts, null, false)).toThrow('請輸入匯率或轉換後金額');
  });

  it.each(['receivable', 'payable'] as const)('single %s keeps the existing no-merchant rule', kind => {
    const c = { ...child(), kind, counterpartyId: 4 };
    const input = fullChild(c, { ...parent(), merchant: 'must not leak' }, accounts, 4, true);
    expect(input.merchant).toBeNull();
    expect(input.counterparty_id).toBe(4);
  });

  it('preserves attached rules only on the original account, otherwise filters by account/date', () => {
    const c = childFromDetail(
      makeEntryDetail({ rules: [makeRule({ id: 99, is_enabled: false })] }),
      { protected: false, protected_reason: null },
      'TWD',
    );
    c.ruleIds = [99, 10, 11];
    c.availableRules = [makeRule({ id: 10, account_id: 2 }), makeRule({ id: 11, account_id: 2, ends_on: '2020-01-01' })];
    expect(permittedRuleIds(c, '2026-01-01')).toContain(99);
    c.accountId = 2;
    expect(permittedRuleIds(c, '2026-01-01')).toEqual([10]);
  });

  it('re-sends unchanged online FX and raw seconds for no-op and metadata-only upsert', () => {
    const d = makeEntryDetail({
      id: 7, account_id: 1, amount: '-370.3680', original_amount: '-12.3456', original_currency: 'USD',
      fx_source: 'fx_api', fx_rate: '30.0000', entry_date: '2026-10-01', posted_date: '2026-10-01',
      entry_time: '19:00:37.125',
    });
    const c = childFromDetail(d, { protected: false, protected_reason: null }, 'TWD');
    const p = { ...parent(), entryDate: d.entry_date, entryTime: d.entry_time, postedDate: null };
    for (const name of [c.name, 'metadata only']) {
      c.name = name;
      const input = toSplitInput([c], p, accounts, parties).members[0];
      expect(input).toMatchObject({
        id: 7, name: name.trim() || null, entry_time: '19:00:37.125', posted_date: '2026-10-01',
        amount: null, fx_rate: null, original_amount: '12.3456', original_currency: 'USD',
      });
    }
  });

  it('keeps more original decimals than the input currency allows while untouched', () => {
    const d = makeEntryDetail({
      id: 7, amount: '-370.3700', original_amount: '-12.34567', original_currency: 'USD', fx_source: 'fx_api',
    });
    const c = childFromDetail(d, { protected: false, protected_reason: null }, 'TWD');
    expect(fullChild(c, parent(), accounts, null, false)).toMatchObject({ original_amount: '12.34567', amount: null });
    c.amountExpr = '13';
    expect(fullChild(c, parent(), accounts, null, false)).toMatchObject({ original_amount: '13', amount: null });
  });

  it.each([
    [null, null, 1, 'POST', '/api/accounting/entries'],
    [7, null, 1, 'PUT', '/api/accounting/entries/7'],
    [null, null, 2, 'POST', '/api/accounting/splits'],
    [7, null, 2, 'PUT', '/api/accounting/entries/7/split'],
    [7, 4, 2, 'PUT', '/api/accounting/splits/4'],
    [7, 4, 1, 'PUT', '/api/accounting/splits/4'],
  ] as const)('sends entry=%s group=%s count=%s as %s %s', (entryId, groupId, count, method, url) => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    const http = TestBed.inject(HttpTestingController);
    const rows = [child(entryId), child()].slice(0, count);
    const plan = planEntrySave(rows, parent(), accounts, parties, { entryId, groupId });
    executeEntrySave(TestBed.inject(AccountingService), plan).subscribe();
    const req = http.expectOne(url);
    expect(req.request.method).toBe(method);
    expect(req.request.body).toBe(plan.input);
    req.flush({});
    http.verify();
  });
});

describe('dropping a loaded fee / discount (#396)', () => {
  const fee = { id: 71, kind: 'fee', amount: '-15.0000', name: '手續費' };
  const discount = { id: 72, kind: 'discount', amount: '5.0000', name: '折扣' };

  function loaded(id: number, children: unknown[]) {
    const d = makeEntryDetail({ id, children: children as EntryDetail['children'] });
    const c = childFromDetail(d, { protected: false, protected_reason: null }, 'TWD');
    const p = {
      ...parent(), entryDate: d.entry_date, entryTime: d.entry_time,
      postedDate: normalizePosted(d.entry_date, d.posted_date),
    };
    return { c, p };
  }
  const flag = (plan: ReturnType<typeof planEntrySave>) => (plan as { dropChildren?: boolean }).dropChildren;

  it('remembers which child kinds the stored entry had', () => {
    expect(loaded(7, [fee, discount]).c.loaded?.childKinds).toEqual(['fee', 'discount']);
    expect(loaded(7, []).c.loaded?.childKinds).toEqual([]);
  });

  it('a single update confirms only when the form dropped a kind it loaded', () => {
    const kept = loaded(7, [fee]);
    kept.c.name = '午餐';
    const keptPlan = planEntrySave([kept.c], kept.p, accounts, parties, { entryId: 7, groupId: null });
    expect([keptPlan.kind, flag(keptPlan)]).toEqual(['update', false]);

    const cleared = loaded(7, [fee]);
    cleared.c.fee = null;
    expect(flag(planEntrySave([cleared.c], cleared.p, accounts, parties, { entryId: 7, groupId: null }))).toBe(true);
    // a save that failed for another reason is planned again from the same drafts: still confirmed
    expect(flag(planEntrySave([cleared.c], cleared.p, accounts, parties, { entryId: 7, groupId: null }))).toBe(true);

    const never = loaded(7, []);
    never.c.name = '午餐';
    expect(flag(planEntrySave([never.c], never.p, accounts, parties, { entryId: 7, groupId: null }))).toBe(false);

    const other = loaded(7, [fee]);
    other.c.discount = { amount: '5', name: '折扣' };
    expect(flag(planEntrySave([other.c], other.p, accounts, parties, { entryId: 7, groupId: null }))).toBe(false);
  });

  it('a copy is a new record and never confirms', () => {
    const copy = loaded(7, [fee]);
    copy.c.id = null;
    copy.c.loaded = null;
    copy.c.fee = null;
    const plan = planEntrySave([copy.c], copy.p, accounts, parties, { entryId: null, groupId: null });
    expect([plan.kind, flag(plan)]).toEqual(['create', undefined]);
  });

  it('a split update confirms for a kept member that lost a kind, not for a member removed from the list', () => {
    const a = loaded(7, [fee, discount]);
    const b = loaded(8, []);
    a.c.discount = null;
    expect(flag(planEntrySave([a.c, b.c], a.p, accounts, parties, { entryId: 7, groupId: 4 }))).toBe(true);

    const gone = loaded(7, [fee]);
    const stays = loaded(8, []);
    const plan = planEntrySave([stays.c, child()], gone.p, accounts, parties, { entryId: 8, groupId: 4 });
    expect([plan.kind, flag(plan)]).toEqual(['update-split', false]);
  });

  it('a convert confirms when the anchor lost its fee', () => {
    const anchor = loaded(7, [fee]);
    anchor.c.fee = null;
    const plan = planEntrySave([anchor.c, child()], anchor.p, accounts, parties, { entryId: 7, groupId: null });
    expect([plan.kind, flag(plan)]).toEqual(['convert-split', true]);

    const kept = loaded(7, [fee]);
    expect(flag(planEntrySave([kept.c, child()], kept.p, accounts, parties, { entryId: 7, groupId: null }))).toBe(false);
  });

  it.each([
    [7, null, 1, '/api/accounting/entries/7'],
    [7, null, 2, '/api/accounting/entries/7/split'],
    [7, 4, 2, '/api/accounting/splits/4'],
  ] as const)('sends the flag of the plan: entry=%s group=%s count=%s', (entryId, groupId, count, url) => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    const http = TestBed.inject(HttpTestingController);
    for (const drop of [true, false]) {
      const first = loaded(7, [fee]);
      if (drop) {
        first.c.fee = null;
      }
      first.c.name = '午餐';
      const rows = [first.c, child()].slice(0, count);
      const plan = planEntrySave(rows, first.p, accounts, parties, { entryId, groupId });
      executeEntrySave(TestBed.inject(AccountingService), plan).subscribe();
      const req = http.expectOne(r => r.url === url);
      expect(req.request.params.keys()).toEqual(drop ? ['drop_children'] : []);
      req.flush({});
    }
    http.verify();
  });
});

describe('rules-only save (#293)', () => {
  const oldRule = makeRule({ id: 5 });
  const newRule = makeRule({ id: 6, name: '新規則' });

  function loaded() {
    const d = makeEntryDetail({ id: 7, rules: [oldRule] });
    const c = childFromDetail(d, { protected: false, protected_reason: null }, 'TWD');
    c.availableRules = [oldRule, newRule];
    const p = {
      ...parent(), entryDate: d.entry_date, entryTime: d.entry_time,
      postedDate: normalizePosted(d.entry_date, d.posted_date),
    };
    return { c, p, saved: { child: structuredClone(c), parent: structuredClone(p) } };
  }

  it('plans a rules-only change as a link-only save', () => {
    const { c, p, saved } = loaded();
    c.ruleIds = [6];
    expect(planEntrySave([c], p, accounts, parties, { entryId: 7, groupId: null, saved }))
      .toMatchObject({ kind: 'rules', id: 7, input: { reward_rule_ids: [6] } });
  });

  it('keeps the full update when any other field changed, nothing changed, or nothing was loaded', () => {
    const renamed = loaded();
    renamed.c.ruleIds = [6];
    renamed.c.name = '改名';
    expect(planEntrySave([renamed.c], renamed.p, accounts, parties, { entryId: 7, groupId: null, saved: renamed.saved }).kind)
      .toBe('update');
    const same = loaded();
    expect(planEntrySave([same.c], same.p, accounts, parties, { entryId: 7, groupId: null, saved: same.saved }).kind)
      .toBe('update');
    const unsaved = loaded();
    unsaved.c.ruleIds = [6];
    expect(planEntrySave([unsaved.c], unsaved.p, accounts, parties, { entryId: 7, groupId: null }).kind).toBe('update');
  });

  it('sends a link-only save to PUT /entries/{id}/reward-rules', () => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    const http = TestBed.inject(HttpTestingController);
    const { c, p, saved } = loaded();
    c.ruleIds = [];
    const plan = planEntrySave([c], p, accounts, parties, { entryId: 7, groupId: null, saved });
    expect(plan.kind).toBe('rules');
    executeEntrySave(TestBed.inject(AccountingService), plan).subscribe();
    const req = http.expectOne('/api/accounting/entries/7/reward-rules');
    expect(req.request.method).toBe('PUT');
    expect(req.request.body).toEqual({ reward_rule_ids: [] });
    req.flush({});
    http.verify();
  });
});
