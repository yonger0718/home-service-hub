import { describe, expect, it } from 'vitest';

import { Transaction, TransactionType } from '../../../models/portfolio.model';
import { nativeTotal, quantityLine, rowAmount, transactionRow } from './transaction-rows';

function tx(overrides: Partial<Transaction>): Transaction {
  return { id: 1, symbol: '2330', type: TransactionType.BUY, quantity: 1, price: 100, fee: 0, tax: 0, trade_date: '2026-10-15', ...overrides };
}

describe('transaction rows', () => {
  it('formats foreign sells without fx as + native main and — note, GBp with 4 decimals', () => {
    expect(rowAmount(tx({ type: TransactionType.SELL, currency: 'USD', price: 1000, quantity: 2, fee: 1 }))).toEqual({ amount: '+1,999.00 USD', amountNote: '—' });
    expect(nativeTotal(tx({ currency: 'GBp', price: 1234.5, quantity: 1 }))).toBe('1,234.5000 GBp');
    expect(quantityLine(tx({ currency: 'GBp', price: 1234.5, quantity: 3 }))).toBe('3 股 @ 1,234.5000 GBp');
  });

  it('treats an unusable fx like a missing one', () => {
    expect(rowAmount(tx({ currency: 'USD', fx_rate_to_twd: 0 })).amountNote).toBe('—');
    expect(rowAmount(tx({ currency: 'USD', fx_rate_to_twd: 'abc' })).amountNote).toBe('—');
  });

  it('rounds TWD amounts to whole dollars', () => {
    expect(rowAmount(tx({ price: 22.05, quantity: 3 })).amount).toBe('−66');
    expect(rowAmount(tx({ type: TransactionType.SELL, price: 1000.4, quantity: 1 })).amount).toBe('+1,000');
  });

  it('shows a zero amount as 0 without a sign on either side', () => {
    expect(rowAmount(tx({ price: 0, quantity: 1 }))).toEqual({ amount: '0', amountNote: '總付出' });
    expect(rowAmount(tx({ type: TransactionType.SELL, price: 0.4, quantity: 1 })).amount).toBe('0');
    expect(rowAmount(tx({ price: 0, quantity: 1, currency: 'USD', fx_rate_to_twd: 30 }))).toEqual({ amount: '0', amountNote: '0.00 USD' });
    expect(rowAmount(tx({ type: TransactionType.SELL, price: 0, quantity: 1, currency: 'USD' }))).toEqual({ amount: '0.00 USD', amountNote: '—' });
  });

  it('leaves out tags and broker when there are none', () => {
    const row = transactionRow(tx({ is_day_trade: false, broker: null }), '台積電');
    expect(row.tags).toBeUndefined();
    expect(row.broker).toBeUndefined();
    expect(row.primary).toBe('台積電 2330');
    expect(row.square?.text).toBe('30');
  });
});
