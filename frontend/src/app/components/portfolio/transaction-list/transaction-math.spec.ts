import { describe, expect, it } from 'vitest';

import { Transaction, TransactionType } from '../../../models/portfolio.model';
import { allInTotal, feeLine, monthHeading, monthKey, monthLabel, periodLabel, tradeTotals } from './transaction-math';

let nextId = 1;
function tx(overrides: Partial<Transaction>): Transaction {
  return {
    id: nextId++,
    symbol: '2330',
    type: TransactionType.BUY,
    quantity: 1,
    price: 1,
    fee: 0,
    tax: 0,
    trade_date: '2026-10-15',
    ...overrides,
  };
}

const twBuy = tx({ symbol: '00878', quantity: 5000, price: 22.05, fee: 157, trade_date: '2026-10-15' });
const usBuy = tx({ symbol: 'AAPL', market: 'US', currency: 'USD', quantity: 10, price: 238.4, fee: 1, fx_rate_to_twd: 30.7, trade_date: '2026-10-08' });
const twSell = tx({ symbol: '2330', type: TransactionType.SELL, quantity: 1000, price: 175, fee: 249, tax: 525, trade_date: '2026-10-02' });

describe('transaction math', () => {
  describe('allInTotal', () => {
    it('adds fee and tax to a buy and subtracts them from a sell', () => {
      expect(allInTotal(twBuy)).toBeCloseTo(110407, 6);
      expect(allInTotal(usBuy)).toBeCloseTo(2385, 6);
      expect(allInTotal(twSell)).toBe(174226);
    });
  });

  describe('tradeTotals', () => {
    it('sums TWD all-in buys and sells, converting foreign rows by fx_rate_to_twd', () => {
      const totals = tradeTotals([twBuy, usBuy, twSell]);

      expect(totals.buyTotal).toBeCloseTo(183626.5, 6);
      expect(totals.buyCount).toBe(2);
      expect(totals.sellTotal).toBeCloseTo(174226, 6);
      expect(totals.sellCount).toBe(1);
      expect(totals.fees).toBeCloseTo(157 + 30.7 + 774, 6);
      expect(totals.turnover).toBeCloseTo(110250 + 73188.8 + 175000, 6);
      expect(totals.feeShare).toBeCloseTo((157 + 30.7 + 774) / (110250 + 73188.8 + 175000), 12);
      expect(totals.foreignWithoutFx).toBe(0);
    });

    it('treats a lower-case twd currency as TWD', () => {
      const totals = tradeTotals([{ ...twBuy, currency: 'twd', fx_rate_to_twd: undefined }]);
      expect(totals.buyTotal).toBeCloseTo(110407, 6);
      expect(totals.foreignWithoutFx).toBe(0);
      expect(feeLine({ ...twSell, currency: 'twd' })).toBe('手續費 249 · 交易稅 525');
    });

    it('accepts fx as a decimal string', () => {
      const totals = tradeTotals([{ ...usBuy, fx_rate_to_twd: '30.70' }]);
      expect(totals.buyTotal).toBeCloseTo(73219.5, 6);
    });

    it.each([
      ['missing', undefined],
      ['non-numeric', 'abc'],
      ['zero', 0],
    ])('excludes a foreign row whose fx is %s from every sum and count and flags it once', (_label, fx) => {
      const noFx = { ...usBuy, fx_rate_to_twd: fx as number | string | undefined };
      const totals = tradeTotals([twBuy, noFx, twSell]);

      expect(totals.buyTotal).toBeCloseTo(110407, 6);
      expect(totals.buyCount).toBe(1);
      expect(totals.sellTotal).toBe(174226);
      expect(totals.fees).toBeCloseTo(157 + 774, 6);
      expect(totals.turnover).toBeCloseTo(110250 + 175000, 6);
      expect(totals.foreignWithoutFx).toBe(1);
    });

    it('treats a row without a currency as TWD', () => {
      const totals = tradeTotals([{ ...twBuy, currency: undefined, fx_rate_to_twd: undefined }]);
      expect(totals.buyTotal).toBeCloseTo(110407, 6);
      expect(totals.foreignWithoutFx).toBe(0);
    });

    it('returns zeros and a null fee share for no rows', () => {
      expect(tradeTotals([])).toEqual({
        buyTotal: 0,
        buyCount: 0,
        sellTotal: 0,
        sellCount: 0,
        fees: 0,
        turnover: 0,
        feeShare: null,
        foreignWithoutFx: 0,
      });
    });
  });

  describe('monthKey', () => {
    it('takes YYYY-MM from the string parts of a date string', () => {
      expect(monthKey('2026-10-15')).toBe('2026-10');
      expect(monthKey('2026-01-01')).toBe('2026-01');
      expect(monthKey('2026-10-31T23:30:00Z')).toBe('2026-10');
    });

    it('uses the local calendar month of a Date', () => {
      expect(monthKey(new Date(2026, 0, 31, 23, 59))).toBe('2026-01');
      expect(monthKey(new Date(2026, 11, 1))).toBe('2026-12');
    });
  });

  describe('monthLabel', () => {
    it('renders YYYY 年 M 月 without a leading zero', () => {
      expect(monthLabel('2026-10')).toBe('2026 年 10 月');
      expect(monthLabel('2026-03')).toBe('2026 年 3 月');
    });
  });

  describe('monthHeading', () => {
    it('sums the month’s TWD all-in buys and sells, ignoring other months', () => {
      const other = tx({ quantity: 1000, price: 100, trade_date: '2026-09-30' });
      expect(monthHeading('2026-10', [twBuy, twSell, other])).toBe('買 110,407 · 賣 174,226');
    });

    it('includes converted foreign rows and skips rows without fx', () => {
      const noFx = { ...usBuy, id: 999, fx_rate_to_twd: undefined };
      expect(monthHeading('2026-10', [twBuy, usBuy, noFx, twSell])).toBe('買 183,627 · 賣 174,226');
    });

    it('omits the sell side when the month only has buys, and the buy side when it only has sells', () => {
      expect(monthHeading('2026-10', [twBuy])).toBe('買 110,407');
      expect(monthHeading('2026-10', [twSell])).toBe('賣 174,226');
    });

    it('is empty when the month has nothing to show', () => {
      expect(monthHeading('2026-11', [twBuy, twSell])).toBe('');
    });
  });

  describe('feeLine', () => {
    it('shows only the fee when the tax is 0', () => {
      expect(feeLine(twBuy)).toBe('手續費 157');
    });

    it('adds the transaction tax when it is positive', () => {
      expect(feeLine(twSell)).toBe('手續費 249 · 交易稅 525');
    });

    it('groups thousands on TWD amounts', () => {
      expect(feeLine(tx({ type: TransactionType.SELL, fee: 1425, tax: 3000 }))).toBe('手續費 1,425 · 交易稅 3,000');
    });

    it('formats foreign fees natively and appends the fx rate', () => {
      expect(feeLine(usBuy)).toBe('手續費 1.00 USD · 匯率 30.70');
      expect(feeLine({ ...usBuy, type: TransactionType.SELL, tax: 0.02, fx_rate_to_twd: '31.2345' })).toBe(
        '手續費 1.00 USD · 交易稅 0.02 USD · 匯率 31.2345',
      );
    });

    it('formats GBp fees with 4 decimals', () => {
      const lse = tx({ market: 'LSE', currency: 'GBp', fee: 150, fx_rate_to_twd: 0.41 });
      expect(feeLine(lse)).toBe('手續費 150.0000 GBp · 匯率 0.41');
    });

    it('omits the fx part on a foreign row without a usable fx', () => {
      expect(feeLine({ ...usBuy, fx_rate_to_twd: undefined })).toBe('手續費 1.00 USD');
      expect(feeLine({ ...usBuy, fx_rate_to_twd: 'abc' })).toBe('手續費 1.00 USD');
    });
  });

  describe('periodLabel', () => {
    it('is 全部期間 without date bounds', () => {
      expect(periodLabel({})).toBe('全部期間');
      expect(periodLabel({ date_from: null, date_to: '' })).toBe('全部期間');
    });

    it('formats a full range with an en dash', () => {
      expect(periodLabel({ date_from: '2026-01-01', date_to: '2026-10-22' })).toBe('2026/01/01 – 2026/10/22');
    });

    it('formats an open-ended range', () => {
      expect(periodLabel({ date_from: '2026-01-01' })).toBe('2026/01/01 起');
      expect(periodLabel({ date_to: '2026-10-22' })).toBe('至 2026/10/22');
    });
  });
});
