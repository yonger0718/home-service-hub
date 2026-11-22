import {
  NO_TASKS,
  NotifTask,
  chipCounts,
  failureLine,
  isCommand,
  notifLabel,
  sortTasks,
  summarize,
} from './notif-task';

let seq = 0;
function task(overrides: Partial<NotifTask> = {}): NotifTask {
  seq += 1;
  return {
    id: `t${seq}`,
    source: 'ledger',
    from: 'bills',
    tier: 'today',
    icon: 'pi-credit-card',
    title: '測試銀行卡',
    when: '今天',
    meta: null,
    amount: null,
    progress: null,
    primary: { label: '記繳款', route: '/accounting' },
    secondary: [],
    sortKey: '2026-10-24',
    fingerprint: 'f',
    ...overrides,
  };
}

describe('notif-task', () => {
  it('count on review data: 3 overdue + 1 today + 2 confirm + 1 system, 3 tracked → 7, red', () => {
    const tasks = [
      ...[1, 2, 3].map(() => task({ tier: 'overdue' })),
      task({ tier: 'today' }),
      ...[1, 2].map(() => task({ tier: 'confirm' })),
      task({ tier: 'system' }),
      ...[1, 2, 3].map(() => task({ tier: 'tracking' })),
    ];
    expect(summarize(tasks)).toEqual({
      total: 7,
      overdue: 3,
      tone: 'red',
      label: '通知，7 件要處理，3 件已逾期',
    });
  });

  it('only today and confirm tasks → primary, no overdue clause', () => {
    expect(summarize([task({ tier: 'today' }), task({ tier: 'confirm' })])).toEqual({
      total: 2,
      overdue: 0,
      tone: 'primary',
      label: '通知，2 件要處理',
    });
  });

  it('a system task alone is red and adds no overdue clause', () => {
    expect(summarize([task({ tier: 'system' })])).toEqual({
      total: 1,
      overdue: 0,
      tone: 'red',
      label: '通知，1 件要處理',
    });
  });

  it('tracking never counts', () => {
    expect(summarize([task({ tier: 'tracking' }), task({ tier: 'tracking' })])).toEqual(NO_TASKS);
  });

  it('notifLabel at zero is the bare name', () => {
    expect(notifLabel(0, 0)).toBe('通知');
  });

  it('sortTasks: tiers in order, oldest first, ties by id', () => {
    const o1020 = task({ id: 'o_1020', tier: 'overdue', sortKey: '2026-10-20' });
    const o0820 = task({ id: 'o_0820', tier: 'overdue', sortKey: '2026-08-20' });
    const o0820b = task({ id: 'o_0820b', tier: 'overdue', sortKey: '2026-08-20' });
    const t1 = task({ id: 't1', tier: 'today' });
    const c2 = task({ id: 'c2', tier: 'confirm' });
    const tr = task({ id: 'tr', tier: 'tracking' });
    const input = [tr, c2, o1020, t1, o0820b, o0820];
    expect(sortTasks(input).map((t) => t.id)).toEqual(['o_0820', 'o_0820b', 'o_1020', 't1', 'c2', 'tr']);
    expect(input[0].id).toBe('tr'); // input untouched
  });

  it('chipCounts counts counted tasks per source', () => {
    const five = [1, 2, 3, 4, 5].map(() => task({ tier: 'overdue' }));
    expect(chipCounts([...five, task({ tier: 'tracking' })])).toEqual({ all: 5, ledger: 5, portfolio: 0 });
  });

  it('failureLine joins names in source order with 、', () => {
    expect(failureLine(['recon', 'bills'])).toBe('無法讀取：帳單、對帳');
    expect(failureLine([])).toBeNull();
  });

  it('isCommand tells a command action from a link', () => {
    expect(isCommand({ label: '入帳', command: { kind: 'postInstance', instanceId: 1, name: '測試月費' } })).toBe(true);
    expect(isCommand({ label: '記繳款', route: '/accounting' })).toBe(false);
  });

  it('an overdue task alone is red with one overdue clause', () => {
    expect(summarize([task({ tier: 'overdue' })])).toEqual({
      total: 1,
      overdue: 1,
      tone: 'red',
      label: '通知，1 件要處理，1 件已逾期',
    });
  });

  it('chipCounts splits mixed sources and ignores tracking of each', () => {
    const tasks = [
      task({ source: 'ledger', tier: 'overdue' }),
      task({ source: 'ledger', tier: 'today' }),
      task({ source: 'portfolio', tier: 'system' }),
      task({ source: 'ledger', tier: 'tracking' }),
      task({ source: 'portfolio', tier: 'tracking' }),
    ];
    expect(chipCounts(tasks)).toEqual({ all: 3, ledger: 2, portfolio: 1 });
  });

  it('sortTasks puts system after confirm and before tracking', () => {
    const ids = sortTasks([
      task({ id: 'tr', tier: 'tracking' }),
      task({ id: 'sy', tier: 'system' }),
      task({ id: 'co', tier: 'confirm' }),
      task({ id: 'to', tier: 'today' }),
    ]).map((t) => t.id);
    expect(ids).toEqual(['to', 'co', 'sy', 'tr']);
  });

  it('sortTasks orders tracking-only input by sortKey', () => {
    const ids = sortTasks([
      task({ id: 'b', tier: 'tracking', sortKey: '2026-10-26' }),
      task({ id: 'a', tier: 'tracking', sortKey: '2026-10-25' }),
    ]).map((t) => t.id);
    expect(ids).toEqual(['a', 'b']);
  });

  it('sortTasks of an empty list is an empty new array', () => {
    const input: NotifTask[] = [];
    const out = sortTasks(input);
    expect(out).toEqual([]);
    expect(out).not.toBe(input);
  });

  it('failureLine with a single key', () => {
    expect(failureLine(['debts'])).toBe('無法讀取：借還款');
  });

  it('equal sortKey orders by id', () => {
    const ids = sortTasks([
      task({ id: 'z', sortKey: '2026-10-24' }),
      task({ id: 'a', sortKey: '2026-10-24' }),
    ]).map((t) => t.id);
    expect(ids).toEqual(['a', 'z']);
  });

  it('a date-only sortKey sorts before a datetime of the same day', () => {
    const ids = sortTasks([
      task({ id: 'dt', sortKey: '2026-10-24T08:00:00' }),
      task({ id: 'd', sortKey: '2026-10-24' }),
    ]).map((t) => t.id);
    expect(ids).toEqual(['d', 'dt']);
  });
});
