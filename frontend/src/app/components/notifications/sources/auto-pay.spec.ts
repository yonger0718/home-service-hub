import { AutoPayRow } from '../../accounting/billing/auto-pay';
import { LedgerAccount } from '../../../models/accounting.model';
import { AUTO_PAY_TASK_DAYS, autoPayTasks } from './auto-pay';

const TODAY = '2026-11-26';

const row = (over: Partial<AutoPayRow> = {}): AutoPayRow => ({
  statement_id: 31,
  card_account_id: 7,
  pay_account_id: 3,
  amount: '16639.0000',
  due_date: '2026-11-24',
  pay_date: '2026-11-24',
  status: 'skipped',
  reason: 'over_cap',
  updated_at: '2026-11-24T01:00:00Z',
  ...over,
});
const card = { id: 7, name: '測試卡', currency: 'TWD' } as LedgerAccount;

function run(rows: AutoPayRow[], settled: (row: AutoPayRow) => boolean = () => false) {
  return autoPayTasks({ rows, accounts: [card], settled, today: TODAY });
}

describe('autoPayTasks', () => {
  it('reads 30 days', () => expect(AUTO_PAY_TASK_DAYS).toBe(30));

  it('a skipped statement with a task reason → one 系統 task linking to the statement', () => {
    expect(run([row()])).toEqual([
      {
        id: 'autopay:31',
        source: 'ledger',
        from: 'autopay',
        tier: 'system',
        icon: 'pi-credit-card',
        title: '自動扣繳未執行：超過單筆上限',
        when: '11/24 扣款',
        meta: '測試卡',
        amount: '$16,639',
        progress: null,
        primary: { label: '查看帳單', route: '/accounting/accounts/7/statements/31' },
        secondary: [],
        sortKey: '2026-11-24',
        wake: { key: 'over_cap' },
      },
    ]);
  });

  it.each([
    ['monthly_budget', '超過當月上限'],
    ['header_disputed', '帳單金額待確認'],
    ['expired', '已超過重試期限'],
  ])('%s → 自動扣繳未執行：%s', (reason, text) => {
    expect(run([row({ reason })])[0].title).toBe(`自動扣繳未執行：${text}`);
  });

  it('no_link / link_ineligible also offer 設定扣款帳戶', () => {
    for (const [reason, text] of [['no_link', '沒有設定扣款帳戶'], ['link_ineligible', '扣款帳戶不符規定']]) {
      const [t] = run([row({ reason, pay_account_id: null })]);
      expect(t.title).toBe(`自動扣繳未執行：${text}`);
      expect(t.secondary).toEqual([{ label: '設定扣款帳戶', route: '/accounting/accounts/7/settings' }]);
    }
  });

  it('no task: informational reasons, other statuses, older than 30 days, or settled (paid, or an earlier cycle)', () => {
    for (const reason of ['twin', 'cutoff', 'duplicate_statement']) {
      expect(run([row({ reason })])).toEqual([]);
    }
    for (const status of ['booked', 'retired', 'owner_deleted', 'owner_edited'] as const) {
      expect(run([row({ status, reason: null })])).toEqual([]);
    }
    expect(run([row({ pay_date: '2026-10-26' })])).toEqual([]);
    expect(run([row({ pay_date: '2026-10-27' })])).toHaveLength(1); // exactly 30 days
    const seen: unknown[] = [];
    expect(run([row()], r => (seen.push(r.statement_id), true))).toEqual([]);
    expect(seen).toEqual([row().statement_id]);
  });

  it('an unknown card: no meta, the amount without a currency guess falls back to TWD', () => {
    const [t] = run([row({ card_account_id: 99 })]);
    expect(t.meta).toBeNull();
    expect(t.amount).toBe('$16,639');
  });
});
