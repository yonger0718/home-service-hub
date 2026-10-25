import { ScheduleInstance, ScheduleInstanceStatus } from '../../../models/accounting.model';
import { DueBill, nextDueItems } from './due-items';

describe('nextDueItems', () => {
  const inst = (id: number, due: string, status: ScheduleInstanceStatus = 'pending', amount = '-1200') =>
    ({ id, definition_name: '房租', seq: 3, times: null, due_date: due, status, totals: [{ currency: 'TWD', amount }] }) as ScheduleInstance;
  const bill: DueBill = { key: 'bill:7:2026-10', name: '玉山 UNI', date: '2026-10-28', amountText: '$12,345' };

  it('merges bills and pending instances, earliest first, three at most', () => {
    const items = nextDueItems([bill], [inst(1, '2026-10-26'), inst(2, '2026-11-05'), inst(3, '2026-11-20')], '2026-10-25');
    expect(items.map(i => [i.kind, i.date])).toEqual([['schedule', '2026-10-26'], ['bill', '2026-10-28'], ['schedule', '2026-11-05']]);
    expect(items[0]).toMatchObject({ name: '房租 #3', amountText: '−$1,200', dueText: '明天' });
    expect(items[1]).toMatchObject({ amountText: '$12,345', dueText: '還有 3 天' });
  });
  it('skips posted and skipped instances and lists overdue ones first', () => {
    const items = nextDueItems([], [inst(1, '2026-10-20'), inst(2, '2026-10-30', 'posted'), inst(3, '2026-10-29', 'skipped')], '2026-10-25');
    expect(items).toHaveLength(1);
    expect(items[0].dueText).toBe('已逾期 5 天');
  });
  it('puts a bill before an instance due the same day and tolerates an instance without totals', () => {
    const items = nextDueItems([bill], [{ ...inst(9, '2026-10-28'), totals: [] }], '2026-10-25');
    expect(items.map(i => i.kind)).toEqual(['bill', 'schedule']);
    expect(items[1].amountText).toBeNull();
  });
});
