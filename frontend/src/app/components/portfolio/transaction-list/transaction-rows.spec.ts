import { describe, expect, it } from 'vitest';

import { Transaction, TransactionType } from '../../../models/portfolio.model';
import { ALLOCATION_SERIES } from '../dashboard/dashboard-math';
import { nativeTotal, quantityLine, rowAmount, squareToken, transactionRow } from './transaction-rows';

function tx(overrides: Partial<Transaction>): Transaction {
  return { id: 1, symbol: '2330', type: TransactionType.BUY, quantity: 1, price: 100, fee: 0, tax: 0, trade_date: '2026-10-15', ...overrides };
}

describe('transaction rows', () => {
  it('picks a series token by a deterministic hash of the symbol', () => {
    const symbols = ['2330', '0050', '2317', '00878', 'AAPL', 'VWRA.L'];
    for (const symbol of symbols) {
      expect(ALLOCATION_SERIES).toContain(squareToken(symbol));
      expect(squareToken(symbol)).toBe(squareToken(symbol));
    }
    expect(new Set(symbols.map(squareToken)).size).toBeGreaterThan(1);
    expect(squareToken('')).toBe(ALLOCATION_SERIES[5381 % ALLOCATION_SERIES.length]);
  });

  it('formats foreign sells without fx as + native main and — note, GBp with 4 decimals', () => {
    expect(rowAmount(tx({ type: TransactionType.SELL, currency: 'USD', price: 1000, quantity: 2, fee: 1 }))).toEqual({ amount: '+1,999.00 USD', amountNote: '—' });
    expect(nativeTotal(tx({ currency: 'GBp', price: 1234.5, quantity: 1 }))).toBe('1,234.5000 GBp');
    expect(quantityLine(tx({ currency: 'GBp', price: 1234.5, quantity: 3 }))).toBe('3 股 @ 1234.5000 GBp');
  });

  it('treats an unusable fx like a missing one', () => {
    expect(rowAmount(tx({ currency: 'USD', fx_rate_to_twd: 0 })).amountNote).toBe('—');
    expect(rowAmount(tx({ currency: 'USD', fx_rate_to_twd: 'abc' })).amountNote).toBe('—');
  });

  it('rounds TWD amounts to whole dollars without a negative zero', () => {
    expect(rowAmount(tx({ type: TransactionType.SELL, price: 0.4, quantity: 1 })).amount).toBe('+0');
    expect(rowAmount(tx({ price: 22.05, quantity: 3 })).amount).toBe('−66');
  });

  it('leaves out tags and broker when there are none', () => {
    const row = transactionRow(tx({ is_day_trade: false, broker: null }), '台積電');
    expect(row.tags).toBeUndefined();
    expect(row.broker).toBeUndefined();
    expect(row.primary).toBe('台積電 2330');
    expect(row.square?.text).toBe('30');
  });
});
