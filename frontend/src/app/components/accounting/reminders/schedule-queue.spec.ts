import { describe, expect, it } from 'vitest';

import { makeInstance } from '../testing/fixtures';
import { queueRows, queueTitle } from './schedule-queue';

const TODAY = '2026-10-03';

describe('queueRows', () => {
  it('splits 已到期 from 即將到來 and words the date', () => {
    const groups = queueRows(
      [
        makeInstance({ id: 1, due_date: '2026-09-30', overdue_days: 3 }),
        makeInstance({ id: 2, definition_id: 2, due_date: '2026-10-03' }),
        makeInstance({ id: 3, definition_id: 3, due_date: '2026-10-20' }),
      ],
      TODAY,
    );
    expect(groups.due.map(row => [row.id, row.overdueText, row.overdue, row.today])).toEqual([
      [1, '已逾期 3 天', true, false], [2, '今天', false, true],
    ]);
    expect(groups.upcoming.map(row => [row.id, row.overdueText, row.dueText])).toEqual([[3, null, '10/20']]);
  });

  it('keeps a partial period under 已到期 whatever its date', () => {
    const groups = queueRows([makeInstance({ id: 9, status: 'posted', is_partial: true, due_date: '2026-11-09' })], TODAY);
    expect(groups.due.map(row => [row.id, row.partial])).toEqual([[9, true]]);
    expect(groups.upcoming).toEqual([]);
  });

  it('offers 補入帳至今天 on the first of several overdue periods of one definition', () => {
    const groups = queueRows(
      [
        makeInstance({ id: 1, definition_id: 7, due_date: '2026-09-22', overdue_days: 11 }),
        makeInstance({ id: 2, definition_id: 7, due_date: '2026-10-01', overdue_days: 2 }),
        makeInstance({ id: 3, definition_id: 8, due_date: '2026-10-02', overdue_days: 1 }),
      ],
      TODAY,
    );
    expect(groups.due.map(row => [row.id, row.offerCatchUp])).toEqual([[1, true], [2, false], [3, false]]);
  });

  it('titles, lines and totals', () => {
    const item = makeInstance({
      definition_name: '房租', seq: 4, times: 12,
      lines: [
        { kind: 'transfer', account_id: 1, account_name: '薪轉', to_account_id: 3, to_account_name: '交割', category: null,
          counterparty: null, amount: '-15000.0000', currency: 'TWD' },
        { kind: 'expense', account_id: 1, account_name: '薪轉', to_account_id: null, to_account_name: null, category: null,
          counterparty: null, amount: '-18000.0000', currency: 'TWD' },
      ],
      totals: [{ currency: 'TWD', amount: '-33000.0000' }],
    });
    expect(queueTitle(item)).toBe('房租 #4/12');
    expect(queueTitle({ ...item, times: null })).toBe('房租 #4');
    const [row] = queueRows([item], TODAY).upcoming.concat(queueRows([item], TODAY).due);
    expect(row.lines).toEqual([
      { text: '薪轉 → 交割', amount: '$15,000', tone: 'neutral' },
      { text: '薪轉', amount: '−$18,000', tone: 'out' },
    ]);
    expect(row.totalText).toBe('−$33,000');
  });
});
