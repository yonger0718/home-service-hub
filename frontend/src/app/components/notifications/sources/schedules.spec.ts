import { ScheduleInstance } from '../../../models/accounting.model';
import { formatSigned } from '../../accounting/format';
import { makeInstance } from '../../accounting/testing/fixtures';
import { scheduleTasks } from './schedules';

const TODAY = '2026-10-24';
const link = { label: '逐期處理', route: '/accounting/reminders', queryParams: { tab: 'pending' } };

function inst(id: number, dueDate: string, overrides: Partial<ScheduleInstance> = {}): ScheduleInstance {
  return makeInstance({
    id,
    definition_id: 1,
    definition_name: '測試月費',
    seq: id,
    due_date: dueDate,
    rule_date: dueDate,
    ...overrides,
  });
}

describe('scheduleTasks', () => {
  it('three overdue instances of one definition form one task', () => {
    const [t, ...rest] = scheduleTasks(
      [inst(1, '2026-08-20'), inst(2, '2026-09-20'), inst(3, '2026-10-20')],
      TODAY,
    );
    expect(rest).toEqual([]);
    expect(t).toMatchObject({
      id: 'sched:1',
      from: 'schedules',
      source: 'ledger',
      tier: 'overdue',
      icon: '🎬',
      title: '測試月費 · 3 期未入帳',
      when: '最早 08/20',
      meta: '範例卡',
      amount: formatSigned(-1170, 'TWD'),
      primary: { label: '全部入帳', command: { kind: 'catchUp', definitionId: 1, name: '測試月費' } },
      sortKey: '2026-08-20',
      fingerprint: '3|2026-08-20',
    });
    expect(t.amount).toBe('−$1,170');
    expect(t.secondary).toEqual([link]);
  });

  it('one overdue instance of a 12-period plan: 入帳 by instance id', () => {
    const [t] = scheduleTasks(
      [inst(5, '2026-10-10', { definition_id: 2, definition_name: '測試分期', kind: 'installment', times: 12, category_icon: null })],
      TODAY,
    );
    expect(t).toMatchObject({
      id: 'sched:2',
      tier: 'overdue',
      icon: '💳',
      title: '測試分期 第 5 / 12 期',
      when: '10/10 · 逾期 14 天',
      primary: { label: '入帳', command: { kind: 'postInstance', instanceId: 5, name: '測試分期' } },
      fingerprint: '1|2026-10-10',
    });
    expect(t.secondary).toEqual([link]);
  });

  it('a recurring instance without a category icon uses 🔁; one open without times is just the name', () => {
    const [t] = scheduleTasks([inst(1, TODAY, { category_icon: null })], TODAY);
    expect(t.icon).toBe('🔁');
    expect(t.title).toBe('測試月費');
  });

  it('due today only → 今天 with 入帳', () => {
    const [t] = scheduleTasks([inst(7, TODAY, { definition_id: 3, definition_name: '測試房租' })], TODAY);
    expect(t).toMatchObject({
      tier: 'today',
      title: '測試房租',
      when: '今天',
      primary: { label: '入帳', command: { kind: 'postInstance', instanceId: 7, name: '測試房租' } },
    });
  });

  it('one overdue and one due today → 已逾期, 2 期未入帳, 入帳 for the overdue one', () => {
    const [t] = scheduleTasks([inst(2, TODAY), inst(1, '2026-10-20')], TODAY);
    expect(t).toMatchObject({
      tier: 'overdue',
      title: '測試月費 · 2 期未入帳',
      when: '最早 10/20',
      amount: '−$780',
      primary: { label: '入帳', command: { kind: 'postInstance', instanceId: 1, name: '測試月費' } },
      fingerprint: '2|2026-10-20',
    });
  });

  it('future instances make no task and are not summed', () => {
    expect(scheduleTasks([inst(1, '2026-10-25')], TODAY)).toEqual([]);
    const [t] = scheduleTasks([inst(1, TODAY), inst(2, '2026-11-24')], TODAY);
    expect(t.amount).toBe('−$390');
    expect(t.title).toBe('測試月費');
  });

  it('only a posted-partial instance → 要你確認 金額未入滿 with the link 逐期處理 and no secondary', () => {
    const [t] = scheduleTasks(
      [inst(4, '2026-10-01', { status: 'posted', is_partial: true, totals: [{ currency: 'TWD', amount: '-100.0000' }] })],
      TODAY,
    );
    expect(t).toMatchObject({
      tier: 'confirm',
      when: '金額未入滿',
      amount: '−$100',
      primary: link,
      secondary: [],
      sortKey: '2026-10-01',
      fingerprint: '0|2026-10-01',
    });
  });

  it('a partial instance beside an overdue one does not change the count or the sum', () => {
    const [t, ...rest] = scheduleTasks(
      [inst(4, '2026-09-01', { status: 'posted', is_partial: true }), inst(5, '2026-10-20')],
      TODAY,
    );
    expect(rest).toEqual([]);
    expect(t).toMatchObject({ tier: 'overdue', title: '測試月費', amount: '−$390', fingerprint: '1|2026-10-20' });
  });

  it('two currencies are joined with 、', () => {
    const [t] = scheduleTasks(
      [
        inst(1, '2026-10-20'),
        inst(2, '2026-10-21', { totals: [{ currency: 'USD', amount: '-5.0000' }] }),
      ],
      TODAY,
    );
    expect(t.amount).toBe(`${formatSigned(-390, 'TWD')}、${formatSigned(-5, 'USD')}`);
  });

  it('two definitions give two tasks', () => {
    const tasks = scheduleTasks(
      [inst(1, '2026-10-20'), inst(2, TODAY, { definition_id: 9, definition_name: '測試房租' })],
      TODAY,
    );
    expect(tasks.map((t) => t.id).sort()).toEqual(['sched:1', 'sched:9']);
  });

  it('skipped and posted complete instances make no task', () => {
    expect(
      scheduleTasks([inst(1, '2026-10-01', { status: 'skipped' }), inst(2, '2026-10-02', { status: 'posted' })], TODAY),
    ).toEqual([]);
  });

  it('a skipped instance flagged partial makes no task', () => {
    expect(scheduleTasks([inst(1, '2026-10-01', { status: 'skipped', is_partial: true })], TODAY)).toEqual([]);
  });

  it('a future pending instance flagged partial makes no task (the backend never sends one)', () => {
    expect(scheduleTasks([inst(1, '2026-11-20', { status: 'pending', is_partial: true })], TODAY)).toEqual([]);
  });

  it('currency order follows the instances, USD first', () => {
    const [t] = scheduleTasks(
      [
        inst(1, '2026-10-20', { totals: [{ currency: 'USD', amount: '-5.0000' }] }),
        inst(2, '2026-10-21'),
      ],
      TODAY,
    );
    expect(t.amount).toBe(`${formatSigned(-5, 'USD')}、${formatSigned(-390, 'TWD')}`);
  });

  it('float amounts sum cleanly', () => {
    const [t] = scheduleTasks(
      [
        inst(1, '2026-10-20', { totals: [{ currency: 'USD', amount: '-0.1' }] }),
        inst(2, '2026-10-21', { totals: [{ currency: 'USD', amount: '-0.2' }] }),
      ],
      TODAY,
    );
    expect(t.amount).toBe('−US$0.3');
  });
});
