import { describe, expect, it } from 'vitest';

import { makeAccount } from '../testing/fixtures';
import { childKey, newChild, newParent, SplitDraftStore } from './split-draft';

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
    s.children.update(rows => rows.map(c => (c.key === a.key ? { ...c, id: 7 } : c)));
    s.anchorId.set(7);
    expect(s.removeReason(a.key)).toBe('原記錄不可移除');
    s.anchorId.set(null);
    s.groupId.set(4);
    expect(s.removeReason(a.key)).toBe('至少保留一項原有記錄');
    s.children.update(rows => rows.map(c => (c.key === b.key ? { ...c, id: 8, protected: true } : c)));
    expect(s.removeReason(b.key)).toBe('受保護子項不可移除');
    expect(s.remove(a.key)).toBe(true);
    expect(s.selected()).toBe(b.key);
  });

  it('uses preceding editable kind and separately inherits account; caps growth', () => {
    const s = new SplitDraftStore(newParent('2026-10-06', ''));
    const a = newChild('income', 1);
    const b = newChild('refund', 2);
    s.children.set([a, { ...b, protected: true }]);
    s.selected.set('parent');
    expect(s.add([makeAccount(), makeAccount({ id: 2 })])).toMatchObject({ kind: 'income', accountId: 2 });
    for (const count of [50, 51]) {
      s.children.set(Array.from({ length: count }, () => newChild()));
      expect(s.add([makeAccount()])).toBeNull();
      expect(s.children()).toHaveLength(count);
    }
  });

  it('makes unique short UI keys without secure-context randomUUID', () => {
    const keys = Array.from({ length: 100 }, () => childKey({}));
    expect(new Set(keys).size).toBe(100);
    expect(keys.every(key => key.length <= 64)).toBe(true);
  });
});
