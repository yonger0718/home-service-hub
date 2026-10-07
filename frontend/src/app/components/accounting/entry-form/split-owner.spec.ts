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

  it('refuses the switch when the flush refuses, and has no parent for one child', () => {
    const s = new SplitDraftStore(newParent('2026-10-06', ''));
    expect(s.select('parent', () => true)).toBe(false);
    const a = s.children()[0];
    s.add([makeAccount()]);
    expect(s.select(a.key, () => false)).toBe(false);
    expect(s.selected()).not.toBe(a.key);
  });

  it('accepts unselected owners but drops removed or changed generations', () => {
    const s = new SplitDraftStore(newParent('2026-10-06', ''));
    const a = s.children()[0];
    const request = s.capture()!;
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

  it('never writes a financial view of a protected child', () => {
    const s = new SplitDraftStore(newParent('2026-10-06', ''));
    s.children.update(rows => rows.map(c => ({ ...c, protected: true, amountExpr: '5' })));
    childView(s, 'amountExpr', '', true).set('9');
    childView(s, 'name', '').set('memo');
    expect(s.children()[0]).toMatchObject({ amountExpr: '5', name: 'memo' });
  });

  it('ignores identity, selection and online quotes but keeps mode-specific dirty state', () => {
    const s = new SplitDraftStore(newParent('2026-10-06', ''));
    const snap = () => draftSnapshot(s.parent(), s.children(), 'schedule', 'transfer', '10');
    const before = snap();
    s.invalidate(s.children()[0].key);
    s.selected.set('parent');
    expect(snap()).toBe(before);
    s.children.update(rows => rows.map(c => ({ ...c, ruleIds: [5], availableRules: [] })));
    expect(snap()).toBe(before);
    expect(draftSnapshot(s.parent(), s.children(), 'changed', 'transfer', '10')).not.toBe(before);
    expect(draftSnapshot(s.parent(), s.children(), 'schedule', 'changed', '10')).not.toBe(before);
    expect(draftSnapshot(s.parent(), s.children(), 'schedule', 'transfer', '11')).not.toBe(before);
    s.children.update(rows => rows.map(c => ({ ...c, name: 'user edit' })));
    expect(snap()).not.toBe(before);
    const owner = s.children()[0];
    const fx = {
      original_amount: '20', original_currency: 'USD', account_currency: 'TWD',
      use_online: true, manual: null, amount: null, fx_rate: '30', rate_date: '2026-10-01',
    };
    s.accept({ key: owner.key, generation: owner.generation }, { fx });
    const quoted = snap();
    s.accept({ key: owner.key, generation: owner.generation }, { fx: { ...fx, fx_rate: '31', rate_date: '2026-10-02' } });
    expect(snap()).toBe(quoted);
  });
});
