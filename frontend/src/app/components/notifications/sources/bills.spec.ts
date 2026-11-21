import { BillState } from '../../accounting/billing/billing.service';
import { BillingEvent } from '../../accounting/billing/billing-math';
import { BILL_TRACKING_DAYS, billTasks } from './bills';

const TODAY = '2026-10-24';

function due(overrides: Partial<BillingEvent> = {}): BillingEvent {
  return {
    accountId: 11,
    name: '測試銀行卡',
    kind: 'due',
    date: '2026-10-20',
    period: { start: '2026-09-01', end: '2026-09-30' },
    nextClosing: '2026-10-31',
    ...overrides,
  };
}

function state(overrides: Partial<BillState> = {}): BillState {
  return {
    accountId: 11,
    name: '測試銀行卡',
    period: { start: '2026-09-01', end: '2026-09-30' },
    due: '2026-10-20',
    payDate: '2026-10-20',
    currency: 'TWD',
    statement: 16639,
    paid: 0,
    remaining: 16639,
    ...overrides,
  };
}

function run(event: BillingEvent, bill: BillState | null) {
  return billTasks({ dues: [event], bill: () => bill, today: TODAY });
}

describe('billTasks', () => {
  it('tracks a week', () => expect(BILL_TRACKING_DAYS).toBe(7));

  it.each([
    ['2026-10-20', 'overdue', '10/20 截止 · 逾期 4 天'],
    ['2026-10-24', 'today', '今天截止'],
    ['2026-10-25', 'tracking', '10/25 截止'],
    ['2026-10-31', 'tracking', '10/31 截止'],
  ])('payDate %s → %s', (payDate, tier, when) => {
    const [t, ...rest] = run(due({ date: payDate }), state({ due: '2026-10-20', payDate }));
    expect(rest).toEqual([]);
    expect(t).toMatchObject({
      id: 'bill:11:2026-10-20',
      from: 'bills',
      source: 'ledger',
      icon: 'pi-credit-card',
      tier,
      when,
      title: '測試銀行卡帳單',
      amount: '$16,639',
      progress: null,
      secondary: [],
      primary: { label: '記繳款', route: '/accounting/accounts/11' },
      sortKey: payDate,
    });
  });

  it('a bill due in 8 days is absent', () => {
    expect(run(due(), state({ payDate: '2026-11-01' }))).toEqual([]);
  });

  it('a paid bill (remaining 0) and an unresolved bill (null) are absent', () => {
    expect(run(due(), state({ paid: 16639, remaining: 0 }))).toEqual([]);
    expect(run(due(), null)).toEqual([]);
  });

  it('tiers by the debit day, keeps the printed due date in the id', () => {
    const [t] = run(due({ date: '2026-10-24' }), state({ due: '2026-10-24', payDate: '2026-10-26' }));
    expect(t.tier).toBe('tracking');
    expect(t.id).toBe('bill:11:2026-10-24');
    expect(t.when).toBe('10/26 截止');
  });

  it('meta names the payment only when something was paid', () => {
    expect(run(due(), state())[0].meta).toBe('帳單 $16,639');
    expect(run(due(), state({ paid: 5000, remaining: 11639 }))[0]).toMatchObject({
      meta: '帳單 $16,639 · 已繳 $5,000',
      amount: '$11,639',
    });
  });

  it('fingerprint changes with the remaining amount and the date', () => {
    const a = run(due(), state())[0].fingerprint;
    expect(a).toBe('16639|2026-10-20');
    expect(run(due(), state({ remaining: 100 }))[0].fingerprint).not.toBe(a);
    expect(run(due(), state({ payDate: '2026-10-22' }))[0].fingerprint).not.toBe(a);
  });

  it('skips closing events', () => {
    expect(billTasks({ dues: [due({ kind: 'closing' })], bill: () => state(), today: TODAY })).toEqual([]);
  });
});
