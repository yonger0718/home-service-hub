import { describe, expect, it } from 'vitest';

import { ReconciliationAction } from '../../accounting/inbox/inbox.model';
import { Counterparty, ImportRun } from '../../../models/accounting.model';
import { NotifTask } from '../notif-task';
import { debtsRow, handledText, importReviewTask, upcomingRow } from './ledger-misc';

function run(overrides: Partial<ImportRun> = {}, needsReview = 4): ImportRun {
  return {
    id: 7,
    status: 'succeeded',
    started_at: '2026-10-24T09:05:00',
    finished_at: null,
    file_name: '測試匯入.csv',
    file_sha256: 'x',
    row_count: 10,
    summary: { needs_review: { count: needsReview } } as unknown as ImportRun['summary'],
    ...overrides,
  };
}

function person(name: string, amount: string, open_count: number | null = 1, currency = 'TWD'): Counterparty {
  return {
    id: name.charCodeAt(0),
    name,
    open_amounts: amount === '0' ? [] : [{ currency, amount }],
    ...(open_count === null ? {} : { open_count }),
  };
}

function bill(title: string, sortKey: string): NotifTask {
  return {
    id: `bill:${title}`,
    source: 'ledger',
    from: 'bills',
    tier: 'tracking',
    icon: 'pi-credit-card',
    title,
    when: '',
    meta: null,
    amount: null,
    progress: null,
    primary: { label: '查看', route: '/x' },
    secondary: [],
    sortKey,
    fingerprint: '',
  };
}

describe('importReviewTask', () => {
  it('needs_review 4 gives the task', () => {
    expect(importReviewTask(run())).toMatchObject({
      id: 'import:7',
      source: 'ledger',
      from: 'importReview',
      tier: 'confirm',
      icon: 'pi-upload',
      title: '匯入紀錄 · 4 筆待確認',
      when: '10/24 09:05',
      meta: '測試匯入.csv',
      amount: null,
      progress: null,
      primary: { label: '查看', route: '/accounting/accounts' },
      secondary: [],
      sortKey: '2026-10-24T09:05:00',
      fingerprint: '4',
    });
  });

  it('falls back to started_at in the id when the run has no id', () => {
    expect(importReviewTask(run({ id: null }))!.id).toBe('import:2026-10-24T09:05:00');
  });

  it('0 reviews or a null run gives null', () => {
    expect(importReviewTask(run({}, 0))).toBeNull();
    expect(importReviewTask(null)).toBeNull();
  });
});

describe('debtsRow', () => {
  it('甲 +1000, 乙 +550, 丙 -5000 gives both sides with names', () => {
    expect(debtsRow([person('甲', '1000'), person('乙', '550'), person('丙', '-5000')])).toEqual({
      id: 'debts',
      source: 'ledger',
      title: '借還款 · 3 位',
      meta: '應收 $1,550（甲、乙）· 應付 $5,000（丙）',
      count: 3,
      link: { label: '查看', route: '/accounting/reminders', queryParams: { tab: 'debts' } },
    });
  });

  it('three people on one side name the first two then 等 1 位', () => {
    const row = debtsRow([person('甲', '100'), person('乙', '200'), person('丙', '300')]);
    expect(row!.meta).toBe('應收 $600（甲、乙 等 1 位）');
  });

  it('open_count 0 or missing is not a person; nobody gives null', () => {
    expect(debtsRow([person('甲', '100', 0), person('乙', '100', null)])).toBeNull();
    expect(debtsRow([])).toBeNull();
    expect(debtsRow([person('甲', '100'), person('乙', '100', 0)])!.count).toBe(1);
  });

  it('only 應付 leaves 應收 out', () => {
    expect(debtsRow([person('丙', '-5000')])!.meta).toBe('應付 $5,000（丙）');
  });

  it('amounts are summed per currency and joined with 、', () => {
    const a = person('甲', '100');
    const b: Counterparty = { ...person('乙', '50'), open_amounts: [{ currency: 'TWD', amount: '50' }, { currency: 'USD', amount: '3' }] };
    expect(debtsRow([a, b])!.meta).toBe('應收 $150、US$3（甲、乙）');
  });
});

describe('importReviewTask wire format', () => {
  it('falls back to the unpaired transfers when there is no needs_review', () => {
    const r = run({ summary: { unpaired_transfers: [{}, {}] } as unknown as ImportRun['summary'] });
    expect(importReviewTask(r)).toMatchObject({ title: '匯入紀錄 · 2 筆待確認', fingerprint: '2' });
  });

  it('shows the local date of an offset-aware started_at near midnight', () => {
    const started = '2026-10-24T23:30:00+00:00';
    const d = new Date(started);
    const p = (n: number) => String(n).padStart(2, '0');
    expect(importReviewTask(run({ started_at: started }))!.when).toBe(
      `${p(d.getMonth() + 1)}/${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`,
    );
  });
});

describe('debtsRow edge data', () => {
  it('a person with open_count but empty or all-zero amounts counts but adds no side; nobody else gives empty meta', () => {
    const empty: Counterparty = { id: 1, name: '丁', open_amounts: [], open_count: 2 };
    const zero: Counterparty = { id: 2, name: '戊', open_amounts: [{ currency: 'TWD', amount: '0.0000' }], open_count: 1 };
    expect(debtsRow([empty, zero, person('甲', '100')])).toMatchObject({
      title: '借還款 · 3 位',
      meta: '應收 $100（甲）',
      count: 3,
    });
    expect(debtsRow([empty, zero])).toMatchObject({ title: '借還款 · 2 位', meta: '' });
  });

  it('應收 in one currency and 應付 in another puts the person on both sides, counted once', () => {
    const both: Counterparty = {
      id: 1,
      name: '甲',
      open_count: 2,
      open_amounts: [{ currency: 'TWD', amount: '300' }, { currency: 'USD', amount: '-5' }],
    };
    const row = debtsRow([both]);
    expect(row!.count).toBe(1);
    expect(row!.meta).toBe('應收 $300（甲）· 應付 US$5（甲）');
  });

  it('float sums format cleanly', () => {
    const row = debtsRow([person('甲', '0.1', 1, 'USD'), person('乙', '0.2', 1, 'USD')]);
    expect(row!.meta).toBe('應收 US$0.3（甲、乙）');
  });
});

describe('upcomingRow', () => {
  it('two tracking bills', () => {
    expect(upcomingRow([bill('測試銀行卡帳單', '2026-10-27'), bill('測試二卡帳單', '2026-10-30')])).toEqual({
      id: 'upcoming',
      source: 'ledger',
      title: '即將到來 · 2 件',
      meta: '測試銀行卡帳單 10/27 · 測試二卡帳單 10/30',
      count: 2,
      link: { label: '查看', route: '/accounting/reminders', queryParams: { tab: 'cards' } },
    });
  });

  it('four: the first three, then 等 1 件; none gives null', () => {
    const four = ['甲', '乙', '丙', '丁'].map((n, i) => bill(n, `2026-10-2${i}`));
    expect(upcomingRow(four)!.meta).toBe('甲 10/20 · 乙 10/21 · 丙 10/22 · 等 1 件');
    expect(upcomingRow([])).toBeNull();
  });
});

describe('handledText', () => {
  const now = '2026-10-24T12:00:00Z';
  function action(created_at: string, reverted: number | null = null): ReconciliationAction {
    return { created_at, reverted_by_action_id: reverted } as ReconciliationAction;
  }

  it('counts actions of the last 7 days that were not reverted', () => {
    const list = [
      action('2026-10-18T09:00:00Z'),
      action('2026-10-17T11:59:00Z'),
      action('2026-10-20T09:00:00Z', 99),
    ];
    expect(handledText(list, now, 50)).toBe('1 件');
  });

  it('a full page with reverted ones still reads N+ (more may lie beyond the page)', () => {
    const page = Array.from({ length: 50 }, (_, i) => action('2026-10-23T09:00:00Z', i < 3 ? 99 : null));
    expect(handledText(page, now, 50)).toBe('47+ 件');
  });

  it('a full page whose oldest action is outside the window is exact', () => {
    const page = [...Array.from({ length: 49 }, () => action('2026-10-23T09:00:00Z')), action('2026-10-10T09:00:00Z')];
    expect(handledText(page, now, 50)).toBe('49 件');
  });

  it('a short page is exact', () => {
    expect(handledText([action('2026-10-23T09:00:00Z'), action('2026-10-22T09:00:00Z')], now, 50)).toBe('2 件');
  });

  it('a missing reverted_by_action_id does not drop the action', () => {
    expect(handledText([{ created_at: '2026-10-23T09:00:00Z' } as ReconciliationAction], now, 50)).toBe('1 件');
  });

  it('the 7-day boundary holds with the +08:00 wire format near midnight', () => {
    // now = 2026-10-24T12:00Z, so the window opens 2026-10-17T12:00Z = 20:00+08:00.
    expect(handledText([action('2026-10-17T20:00:00+08:00')], now, 50)).toBe('1 件');
    expect(handledText([action('2026-10-17T19:59:00+08:00')], now, 50)).toBeNull();
    expect(handledText([action('2026-10-18T00:30:00+08:00')], now, 50)).toBe('1 件');
  });

  it('a full page inside the window reads 50+ 件; none gives null', () => {
    const full = Array.from({ length: 50 }, () => action('2026-10-23T09:00:00Z'));
    expect(handledText(full, now, 50)).toBe('50+ 件');
    expect(handledText([], now, 50)).toBeNull();
  });
});
