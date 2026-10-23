import { CashTransaction, CashTransactionQuery, CashTransactionType } from '../../../models/portfolio.model';
import { CASH_TYPE_ICONS, balanceColumnShown, cashRow, formatCash, runningBalances } from './cash-rows';

function txn(over: Partial<CashTransaction> = {}): CashTransaction {
  return {
    id: 1,
    account_id: 7,
    txn_date: '2026-10-20',
    type: 'deposit',
    amount: '20000.00',
    currency: 'TWD',
    note: null,
    related_transaction_id: null,
    related_dividend_id: null,
    child_legs: null,
    source: 'manual',
    import_fingerprint: 'fp',
    created_at: '2026-10-20T00:00:00Z',
    ...over,
  };
}

describe('cashRow', () => {
  it('deposit with a note: 入金 / note / 手動 / +20,000 up', () => {
    const row = cashRow(txn({ id: 3, note: '從台新 Richart 轉入' }));
    expect(row).toEqual({
      id: 3,
      date: '2026/10/20',
      icon: CASH_TYPE_ICONS.deposit,
      title: '入金',
      subtitle: '從台新 Richart 轉入',
      source: '手動',
      amount: '+20,000',
      amountClass: 'up',
      hasLegs: false,
    });
  });

  it('an empty or blank note gives a null subtitle', () => {
    expect(cashRow(txn({ note: '' })).subtitle).toBeNull();
    expect(cashRow(txn({ note: '   ' })).subtitle).toBeNull();
    expect(cashRow(txn({ note: undefined })).subtitle).toBeNull();
  });

  it('buy_settle with related data: 交割 · 買進 00878 / 5,000 股 · 10/15 成交 / 交易 / −110,407 down', () => {
    const row = cashRow(
      txn({
        type: 'buy_settle',
        source: 'auto_derive',
        amount: '-110407.00',
        related_transaction_id: 55,
        related_symbol: '00878',
        related_quantity: '5000.0000',
        related_trade_date: '2026-10-15',
        note: 'auto',
      }),
    );
    expect(row.title).toBe('交割 · 買進 00878');
    expect(row.subtitle).toBe('5,000 股 · 10/15 成交');
    expect(row.source).toBe('交易');
    expect(row.amount).toBe('−110,407');
    expect(row.amountClass).toBe('down');
    expect(row.hasLegs).toBe(false);
  });

  it('sell_settle and fee / tax legs share the trade subtitle', () => {
    const related = {
      source: 'auto_derive' as const,
      related_transaction_id: 56,
      related_symbol: '2330',
      related_quantity: '1000.0000',
      related_trade_date: '2026-08-21',
    };
    const sell = cashRow(txn({ ...related, type: 'sell_settle', amount: '1045000.00' }));
    expect(sell.title).toBe('交割 · 賣出 2330');
    expect(sell.subtitle).toBe('1,000 股 · 08/21 成交');
    const fee = cashRow(txn({ ...related, type: 'fee', amount: '-1489.00' }));
    expect(fee.title).toBe('手續費');
    expect(fee.subtitle).toBe('1,000 股 · 08/21 成交');
    const tax = cashRow(txn({ ...related, type: 'tax', amount: '-3135.00' }));
    expect(tax.title).toBe('交易稅');
    expect(tax.subtitle).toBe('1,000 股 · 08/21 成交');
  });

  it('merged trade with two legs: 交割 · 2330 / 已合併買賣 2 筆 · 08/21 成交, hasLegs', () => {
    const legs = [
      txn({ id: 10, type: 'sell_settle', amount: '150.00', source: 'auto_derive', related_transaction_id: 56 }),
      txn({ id: 11, type: 'fee', amount: '-50.00', source: 'auto_derive', related_transaction_id: 56 }),
    ];
    const row = cashRow(
      txn({
        id: -56,
        type: 'trade',
        amount: '100.00',
        source: 'auto_derive',
        related_transaction_id: 56,
        related_symbol: '2330',
        related_quantity: '1000.0000',
        related_trade_date: '2026-08-21',
        child_legs: legs,
      }),
    );
    expect(row.title).toBe('交割 · 2330');
    expect(row.subtitle).toBe('已合併買賣 2 筆 · 08/21 成交');
    expect(row.hasLegs).toBe(true);
    expect(row.source).toBe('交易');
  });

  it('merged trade without a trade date: 已合併買賣 {n} 筆', () => {
    const legs = [txn({ id: 10 }), txn({ id: 11 }), txn({ id: 12 })];
    const row = cashRow(txn({ type: 'trade', source: 'auto_derive', related_symbol: '2330', child_legs: legs }));
    expect(row.subtitle).toBe('已合併買賣 3 筆');
  });

  it('dividend_cash: 股利 · 2317 / 5.80 × 3,000 · 扣匯費 10 / 股利', () => {
    const row = cashRow(
      txn({
        type: 'dividend_cash',
        source: 'auto_derive',
        amount: '17390.00',
        related_dividend_id: 9,
        related_symbol: '2317',
        related_per_share: '5.8000',
        related_quantity: '3000.0000',
        related_fee: '10.00',
        related_tax: '0.00',
      }),
    );
    expect(row.title).toBe('股利 · 2317');
    expect(row.subtitle).toBe('5.80 × 3,000 · 扣匯費 10');
    expect(row.source).toBe('股利');
    expect(row.amount).toBe('+17,390');
  });

  it('dividend_cash appends 補充保費 when the tax is above zero, and drops 扣匯費 at 0', () => {
    const base = {
      type: 'dividend_cash' as const,
      source: 'auto_derive' as const,
      related_dividend_id: 9,
      related_symbol: '00878',
      related_per_share: '0.4800',
      related_quantity: '20000.0000',
    };
    expect(cashRow(txn({ ...base, related_fee: '10.00', related_tax: '201.00' })).subtitle).toBe(
      '0.48 × 20,000 · 扣匯費 10 · 補充保費 201',
    );
    expect(cashRow(txn({ ...base, related_fee: '0.00', related_tax: '0.00' })).subtitle).toBe('0.48 × 20,000');
  });

  it('csv_import → 匯入', () => {
    expect(cashRow(txn({ source: 'csv_import', type: 'withdraw', amount: '-5000' })).source).toBe('匯入');
  });

  it('a settle row without related data falls back to the plain title and the note', () => {
    const row = cashRow(txn({ type: 'buy_settle', source: 'csv_import', amount: '-1000', note: '券商匯入' }));
    expect(row.title).toBe('交割 · 買進');
    expect(row.subtitle).toBe('券商匯入');
    expect(cashRow(txn({ type: 'sell_settle', note: null })).title).toBe('交割 · 賣出');
    expect(cashRow(txn({ type: 'sell_settle', note: null })).subtitle).toBeNull();
    const div = cashRow(txn({ type: 'dividend_cash', source: 'auto_derive', note: '股利入帳' }));
    expect(div.title).toBe('股利');
    expect(div.subtitle).toBe('股利入帳');
  });

  it('plain types: titles and note subtitles', () => {
    const titles: [CashTransactionType, string][] = [
      ['deposit', '入金'],
      ['withdraw', '出金'],
      ['interest_in', '利息收入'],
      ['margin_interest', '融資利息'],
      ['wire_fee', '匯費'],
      ['fx_convert', '換匯'],
    ];
    for (const [type, title] of titles) {
      const row = cashRow(txn({ type, note: 'n' }));
      expect(row.title).toBe(title);
      expect(row.subtitle).toBe('n');
    }
  });

  it('auto_derive without a dividend id → 交易, with one → 股利', () => {
    expect(cashRow(txn({ source: 'auto_derive', type: 'fee' })).source).toBe('交易');
    expect(cashRow(txn({ source: 'auto_derive', type: 'dividend_cash', related_dividend_id: 4 })).source).toBe('股利');
  });

  it('a zero amount has no sign and no class; a USD amount keeps 2 dp', () => {
    const zero = cashRow(txn({ amount: '0.00' }));
    expect(zero.amount).toBe('0');
    expect(zero.amountClass).toBe('');
    const usd = cashRow(txn({ amount: '-2379.54', currency: 'USD' }));
    expect(usd.amount).toBe('−2,379.54');
    expect(usd.amountClass).toBe('down');
  });

  it('maps every type to a non-empty pi-* icon', () => {
    const types: CashTransactionType[] = [
      'deposit', 'withdraw', 'trade', 'buy_settle', 'sell_settle', 'fee', 'tax',
      'dividend_cash', 'interest_in', 'margin_interest', 'wire_fee', 'fx_convert',
    ];
    for (const type of types) {
      expect(CASH_TYPE_ICONS[type]).toMatch(/^pi-[a-z-]+$/);
      expect(cashRow(txn({ type })).icon).toBe(CASH_TYPE_ICONS[type]);
    }
  });
});

describe('formatCash', () => {
  it('groups and rounds by currency; negatives carry U+2212, positives no sign (balance form)', () => {
    expect(formatCash('438360', 'TWD')).toBe('438,360');
    expect(formatCash('-2379.54', 'USD')).toBe('−2,379.54');
    expect(formatCash('20000', 'JPY')).toBe('20,000');
    expect(formatCash('0', 'TWD')).toBe('0');
    expect(formatCash(1234.5, 'USD')).toBe('1,234.50');
    expect(formatCash('-110407.00', 'twd')).toBe('−110,407');
  });

  it('a value that rounds to zero carries no sign', () => {
    expect(formatCash('-0.4', 'TWD')).toBe('0');
    expect(formatCash('-0.004', 'USD')).toBe('0.00');
  });

  it('a non-numeric value is —', () => {
    expect(formatCash('abc', 'TWD')).toBe('—');
  });
});

describe('runningBalances', () => {
  const today = '2026-10-22';
  const rows = [
    txn({ id: 1, txn_date: '2026-10-22', amount: '20000.00' }),
    txn({ id: 2, txn_date: '2026-10-15', amount: '-110407.00', type: 'buy_settle' }),
    txn({ id: 3, txn_date: '2026-10-01', amount: '174226.00' }),
  ];
  const q: CashTransactionQuery = { offset: 0, sort: 'txn_date:desc' };

  it('walks back from the current balance, newest first', () => {
    expect(runningBalances(rows, 438360, q, today)).toEqual([438360, 418360, 528767]);
    expect(runningBalances(rows, '438360.00', q, today)).toEqual([438360, 418360, 528767]);
    expect(balanceColumnShown(q, rows, today)).toBe(true);
  });

  it('defaults: missing offset and sort count as the first page by txn_date:desc', () => {
    expect(runningBalances(rows, 438360, {}, today)).toEqual([438360, 418360, 528767]);
  });

  it('a merged row moves the balance by its own amount, not its legs', () => {
    const merged = [
      txn({
        id: -5,
        type: 'trade',
        amount: '+100',
        child_legs: [txn({ id: 50, amount: '+150' }), txn({ id: 51, amount: '-50' })],
      }),
      txn({ id: 6, amount: '10' }),
    ];
    expect(runningBalances(merged, 1000, q, today)).toEqual([1000, 900]);
  });

  it('merge_related alone keeps the column', () => {
    expect(runningBalances(rows, 438360, { ...q, merge_related: true }, today)).toEqual([438360, 418360, 528767]);
    expect(balanceColumnShown({ ...q, merge_related: true }, rows, today)).toBe(true);
  });

  const hidden: [string, CashTransactionQuery][] = [
    ['offset 25', { ...q, offset: 25 }],
    ['date_from', { ...q, date_from: '2026-01-01' }],
    ['date_to', { ...q, date_to: '2026-10-31' }],
    ['type', { ...q, type: 'deposit' }],
    ["sort 'txn_date:asc'", { ...q, sort: 'txn_date:asc' }],
  ];
  for (const [label, query] of hidden) {
    it(`${label} → null`, () => {
      expect(runningBalances(rows, 438360, query, today)).toBeNull();
      expect(balanceColumnShown(query, rows, today)).toBe(false);
    });
  }

  it('a row dated after today → null', () => {
    const future = [txn({ id: 9, txn_date: '2026-10-23', amount: '5' }), ...rows];
    expect(runningBalances(future, 438360, q, today)).toBeNull();
    expect(balanceColumnShown(q, future, today)).toBe(false);
  });

  it('an empty page yields no balances', () => {
    expect(runningBalances([], 438360, q, today)).toEqual([]);
  });
});
