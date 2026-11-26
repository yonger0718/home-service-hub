import { AUTO_PAY_REASON_TEXT, AUTO_PAY_TASK_REASONS, AutoPayRow, autoPaidSince, autoPayPending, autoPayRowsSince } from './auto-pay';

const row = (over: Partial<AutoPayRow> = {}): AutoPayRow => ({
  statement_id: 11,
  card_account_id: 7,
  pay_account_id: 3,
  amount: '580.0000',
  due_date: '2026-10-24',
  pay_date: '2026-10-26',
  status: 'booked',
  reason: null,
  updated_at: '2026-10-26T01:00:00Z',
  ...over,
});
const ON = { enabled: true, enabled_from: '2026-10-01' };
const linked = { id: 7, auto_pay_account_id: 3 };

describe('auto-pay copy (D9)', () => {
  it('names every skip reason; the task reasons are the transient ones plus expired', () => {
    expect(AUTO_PAY_REASON_TEXT).toEqual({
      over_cap: '超過單筆上限',
      monthly_budget: '超過當月上限',
      no_link: '沒有設定扣款帳戶',
      link_ineligible: '扣款帳戶不符規定',
      header_disputed: '帳單金額待確認',
      twin: '已有相同的繳款記錄',
      duplicate_statement: '重複的帳單',
      cutoff: '早於起始日',
      expired: '已超過重試期限',
    });
    expect([...AUTO_PAY_TASK_REASONS].sort()).toEqual(
      ['expired', 'header_disputed', 'link_ineligible', 'monthly_budget', 'no_link', 'over_cap'],
    );
  });
});

describe('autoPayPending', () => {
  it('a linked card, the switch on, the debit day on or after 起始日 and not past: pending', () => {
    expect(autoPayPending(ON, linked, '2026-10-26', '2026-10-22', [])).toBe(true);
    expect(autoPayPending(ON, linked, '2026-10-26', '2026-10-26', [])).toBe(true); // the debit day itself
  });

  it('not pending: switch off, no start date, unlinked card, before 起始日, past the debit day, or unread switch', () => {
    expect(autoPayPending({ enabled: false, enabled_from: '2026-10-01' }, linked, '2026-10-26', '2026-10-22', [])).toBe(false);
    expect(autoPayPending({ enabled: true, enabled_from: null }, linked, '2026-10-26', '2026-10-22', [])).toBe(false);
    expect(autoPayPending(ON, { id: 7, auto_pay_account_id: null }, '2026-10-26', '2026-10-22', [])).toBe(false);
    expect(autoPayPending({ enabled: true, enabled_from: '2026-10-27' }, linked, '2026-10-26', '2026-10-22', [])).toBe(false);
    expect(autoPayPending(ON, linked, '2026-10-26', '2026-10-27', [])).toBe(false);
    expect(autoPayPending(null, linked, '2026-10-26', '2026-10-22', [])).toBe(false);
  });

  it('a skipped row of that card and debit day (any reason) means it will not pay; another card or day does not', () => {
    const skipped = row({ status: 'skipped', reason: 'over_cap' });
    expect(autoPayPending(ON, linked, '2026-10-26', '2026-10-22', [skipped])).toBe(false);
    expect(autoPayPending(ON, linked, '2026-10-26', '2026-10-22', [row({ status: 'skipped', reason: 'twin' })])).toBe(false);
    expect(autoPayPending(ON, linked, '2026-10-26', '2026-10-22', [{ ...skipped, card_account_id: 8 }])).toBe(true);
    expect(autoPayPending(ON, linked, '2026-10-26', '2026-10-22', [{ ...skipped, pay_date: '2026-09-25' }])).toBe(true);
  });

  it('a bill over 單筆上限 will be skipped (over_cap) on the debit day, so it is not pending now; at the cap it is', () => {
    const capped = { ...ON, max_amount: '20000' };
    expect(autoPayPending(capped, linked, '2026-10-26', '2026-10-22', [], 20000.01)).toBe(false);
    expect(autoPayPending(capped, linked, '2026-10-26', '2026-10-22', [], 20000)).toBe(true);
    expect(autoPayPending(ON, linked, '2026-10-26', '2026-10-22', [], 20000.01)).toBe(true); // cap not read
  });
});

describe('autoPaidSince', () => {
  it('a booked or owner-edited row of the card paying on or after the date', () => {
    expect(autoPaidSince([row()], 7, '2026-10-01')).toBe(true);
    expect(autoPaidSince([row({ status: 'owner_edited' })], 7, '2026-10-01')).toBe(true);
    expect(autoPaidSince([row()], 7, '2026-10-27')).toBe(false);
    expect(autoPaidSince([row()], 8, '2026-10-01')).toBe(false);
    for (const status of ['retired', 'owner_deleted', 'skipped'] as const) {
      expect(autoPaidSince([row({ status })], 7, '2026-10-01')).toBe(false);
    }
    expect(autoPaidSince(null, 7, '2026-10-01')).toBe(false);
  });
});

describe('autoPayRowsSince', () => {
  it('the read window: 45 days before today', () => {
    expect(autoPayRowsSince('2026-11-26')).toBe('2026-10-12');
  });
});
