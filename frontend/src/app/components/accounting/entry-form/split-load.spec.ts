import { describe, expect, it } from 'vitest';

import { makeEntryDetail, makeGroupMember } from '../testing/fixtures';
import { addAvailability, splitFromDetails } from './split-load';

describe('split load', () => {
  it('loads group order, opened selection, raw times and each protected flag', () => {
    const first = makeEntryDetail({ id: 7, entry_time: '01:02:03.456', merchant: 'first', amount: '-20' });
    const opened = makeEntryDetail({
      id: 8, merchant: 'second', amount: '5', kind: 'refund',
      group: { id: 4, kind: 'split', name: 'group', merchant: 'parent', description: 'note', count: 2, total: '-15', currency: 'TWD' },
      group_members: [
        { ...first, protected: false, protected_reason: null },
        makeGroupMember({ id: 8, kind: 'refund', protected: true, protected_reason: 'refund' }),
      ],
    });
    const state = splitFromDetails(opened, [first], 'TWD');
    expect(state.children.map(c => c.id)).toEqual([7, 8]);
    expect(state.selected).toBe(state.children[1].key);
    expect(state.parent).toMatchObject({ merchant: 'parent', entryTime: '01:02:03.456', dateTouched: false });
    expect(state.children[1]).toMatchObject({
      protected: true, protectedReason: 'refund', rulesTouched: true,
      loaded: { merchant: 'second', signedAmount: '5', kind: 'refund' },
    });
    expect(state.children[0].loaded).toMatchObject({ entryTime: '01:02:03.456', merchant: 'first' });
  });

  it('refuses an incomplete related load instead of dropping a member', () => {
    const opened = makeEntryDetail({
      id: 8,
      group: { id: 4, kind: 'split', name: null, merchant: null, description: null, count: 2, total: null, currency: 'TWD' },
      group_members: [makeGroupMember({ id: 7 }), makeGroupMember({ id: 8 })],
    });
    expect(() => splitFromDetails(opened, [], 'TWD')).toThrow('子項載入不完整');
  });
});

describe('add mode matrix', () => {
  const mode = {
    split: false, count: 1, scheduleId: null, eventTab: 'single', editing: false,
    kind: 'expense', convertBlockedReason: null, source: 'manual', locked: false,
  };
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
