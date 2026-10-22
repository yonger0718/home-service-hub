import { Observable, of } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';

import { Dividend, Paged, StockHolding } from '../../../models/portfolio.model';
import {
  ALLOCATION_SERIES,
  MARKET_LABELS,
  allDividendPages,
  allocation,
  composition,
  pendingRows,
  quoteTimeLabel,
  todayChange,
} from './dashboard-math';

function holding(symbol: string, market_value: number, name?: string): StockHolding {
  return { symbol, name, market_value, market: 'TW' } as StockHolding;
}

function dividend(id: number, overrides: Partial<Dividend> = {}): Dividend {
  return {
    id,
    symbol: '00878',
    amount: 9600,
    ex_dividend_date: '2026-10-17',
    payment_date: '2026-11-18',
    receipt_status: 'pending',
    cash_dividend_per_share: 0.48,
    quantity_at_record_date: 20000,
    ...overrides,
  };
}

describe('dashboard math', () => {
  describe('todayChange', () => {
    it('divides the day P&L by the assets before today', () => {
      const change = todayChange({ total_day_pnl: 10, total_assets_twd: '1250' });

      expect(change.amount).toBe(10);
      expect(change.percent).toBeCloseTo((10 / 1240) * 100, 10);
      expect(change.percent).toBeCloseTo(0.806, 3);
    });

    it('has no percent when the assets before today are not positive', () => {
      expect(todayChange({ total_day_pnl: 10, total_assets_twd: '10' }).percent).toBeNull();
      expect(todayChange({ total_day_pnl: 10, total_assets_twd: '0' }).percent).toBeNull();
      expect(todayChange({ total_day_pnl: 0, total_assets_twd: '0' })).toEqual({ amount: 0, percent: null });
    });
  });

  describe('composition', () => {
    it('splits stocks and cash and keeps pending dividends out of the bar', () => {
      expect(
        composition({ total_market_value: 1000, total_cash_twd: '250', total_pending_dividends_net: 14600 }),
      ).toEqual({ stocks: 1000, cash: 250, pending: 14600, stocksPct: 80, cashPct: 20 });
    });

    it('returns zero percents when everything is zero', () => {
      expect(
        composition({ total_market_value: 0, total_cash_twd: '0', total_pending_dividends_net: undefined }),
      ).toEqual({ stocks: 0, cash: 0, pending: 0, stocksPct: 0, cashPct: 0 });
    });
  });

  describe('quoteTimeLabel', () => {
    it('tags 盤中 inside the half-open Taipei session', () => {
      expect(quoteTimeLabel('2026-10-22T05:29:00Z', new Date('2026-10-22T05:29:30Z'))).toBe('行情更新於 13:29 · 盤中');
    });

    it('tags 台股收盤 at 13:30 Taipei (the window is closed)', () => {
      expect(quoteTimeLabel('2026-10-22T05:30:00Z', new Date('2026-10-22T05:30:30Z'))).toBe(
        '行情更新於 13:30 · 台股收盤',
      );
    });

    it('tags 台股收盤 after hours', () => {
      expect(quoteTimeLabel('2026-10-22T09:00:00Z', new Date('2026-10-22T09:00:30Z'))).toBe(
        '行情更新於 17:00 · 台股收盤',
      );
    });

    it('tags 台股收盤 before 09:00 Taipei', () => {
      expect(quoteTimeLabel('2026-10-22T00:59:00Z', new Date('2026-10-22T00:59:30Z'))).toBe(
        '行情更新於 08:59 · 台股收盤',
      );
    });

    it('tags 台股收盤 on a Saturday morning', () => {
      // 2026-10-24 is a Saturday; 02:00Z is 10:00 Taipei.
      expect(quoteTimeLabel('2026-10-24T02:00:00Z', new Date('2026-10-24T02:00:00Z'))).toBe(
        '行情更新於 10:00 · 台股收盤',
      );
    });

    it('takes the time from finished_at and the tag from now', () => {
      expect(quoteTimeLabel('2026-10-21T09:00:00Z', new Date('2026-10-22T02:00:00Z'))).toBe('行情更新於 17:00 · 盤中');
    });

    it('returns null without a parseable time', () => {
      expect(quoteTimeLabel(null, new Date('2026-10-22T02:00:00Z'))).toBeNull();
      expect(quoteTimeLabel(undefined, new Date('2026-10-22T02:00:00Z'))).toBeNull();
      expect(quoteTimeLabel('not a date', new Date('2026-10-22T02:00:00Z'))).toBeNull();
    });
  });

  describe('allocation', () => {
    it('keeps the five largest holdings, merges the rest into 其他 and sums to exactly 100.0', () => {
      const slices = allocation([
        holding('2317', 300, '鴻海'),
        holding('2330', 1000, '台積電'),
        holding('0050', 500, '元大台灣50'),
        holding('AAPL', 100),
        holding('00878', 200, '國泰永續高股息'),
        holding('2412', 233, '中華電'),
      ]);

      expect(slices.map(s => [s.name, s.symbol, s.pct, s.token])).toEqual([
        ['台積電', '2330', 42.9, '--app-primary'],
        ['元大台灣50', '0050', 21.4, '--app-accent'],
        ['鴻海', '2317', 12.8, '--app-dividend'],
        ['中華電', '2412', 10.0, '--app-success'],
        ['國泰永續高股息', '00878', 8.6, '--app-warning'],
        ['其他', '', 4.3, '--app-text-muted'],
      ]);
      expect(Math.round(slices.reduce((sum, s) => sum + s.pct, 0) * 10)).toBe(1000);
    });

    it('rounds by largest remainder so three equal holdings still total 100.0', () => {
      const slices = allocation([holding('A', 1), holding('B', 1), holding('C', 1)]);

      expect(slices.map(s => s.pct)).toEqual([33.4, 33.3, 33.3]);
      expect(slices.map(s => s.name)).toEqual(['A', 'B', 'C']);
    });

    it('uses the series tokens in order and no 其他 for five or fewer holdings', () => {
      const slices = allocation([5, 4, 3, 2, 1].map(v => holding(`S${v}`, v)));

      expect(slices.map(s => s.token)).toEqual([...ALLOCATION_SERIES]);
      expect(ALLOCATION_SERIES).toEqual(['--app-primary', '--app-accent', '--app-dividend', '--app-success', '--app-warning']);
    });

    it('is empty without holdings or market value', () => {
      expect(allocation([])).toEqual([]);
      expect(allocation([holding('A', 0), holding('B', 0)])).toEqual([]);
    });
  });

  describe('pendingRows', () => {
    const nameFor = (d: Dividend) => ({ '00878': '國泰永續高股息', '2330': '台積電' })[d.symbol] ?? d.symbol;

    it('lists pending rows only, dated by payment date first, then undated by ex date', () => {
      const rows = pendingRows(
        [
          dividend(1, { payment_date: '2027-01-08', symbol: '2330' }),
          dividend(2, { receipt_status: 'confirmed', payment_date: '2026-10-01' }),
          dividend(3, { payment_date: null, ex_dividend_date: '2025-03-01' }),
          dividend(4, { payment_date: '2026-11-18' }),
          dividend(5, { receipt_status: 'legacy_unknown', payment_date: '2026-10-02' }),
        ],
        nameFor,
      );

      expect(rows.map(r => r.id)).toEqual([4, 1, 3]);
    });

    it('keeps at most three rows and never lets an old undated row displace a dated one', () => {
      const rows = pendingRows(
        [
          dividend(1, { payment_date: null, ex_dividend_date: '2020-01-01' }),
          dividend(2, { payment_date: '2026-12-01' }),
          dividend(3, { payment_date: '2026-11-01' }),
          dividend(4, { payment_date: '2027-02-01' }),
        ],
        nameFor,
      );

      expect(rows.map(r => r.id)).toEqual([3, 2, 4]);
    });

    it('builds the tile from the date string parts, independent of the machine time zone', () => {
      const [row] = pendingRows([dividend(1)], nameFor);

      expect(row).toEqual({ id: 1, month: '11月', day: '18', name: '國泰永續高股息', note: '每股 0.48 · 20,000 股', amount: 9600, currency: 'TWD' });
      const [january] = pendingRows([dividend(2, { payment_date: '2027-01-08T00:00:00' })], nameFor);
      expect([january.month, january.day]).toEqual(['1月', '08']);
    });

    it('falls back to the ex date and marks 除息 without a payment date', () => {
      const [row] = pendingRows([dividend(1, { payment_date: null, ex_dividend_date: '2026-10-17' })], nameFor);

      expect([row.month, row.day, row.note]).toEqual(['10月', '17', '每股 0.48 · 20,000 股 · 除息']);
    });

    it('omits a missing half of the note', () => {
      const rows = pendingRows(
        [
          dividend(1, { cash_dividend_per_share: undefined }),
          dividend(2, { quantity_at_record_date: undefined, payment_date: '2026-11-19' }),
        ],
        nameFor,
      );

      expect(rows.map(r => r.note)).toEqual(['20,000 股', '每股 0.48']);
    });

    it('carries the dividend currency', () => {
      const [row] = pendingRows([dividend(1, { symbol: 'AAPL', amount: 12.35, currency: 'USD' })], nameFor);

      expect([row.amount, row.currency]).toEqual([12.35, 'USD']);
    });

    it('names rows through nameFor and honours the limit', () => {
      const rows = pendingRows([dividend(1, { symbol: '2330' }), dividend(2, { symbol: '9999' })], nameFor, 1);

      expect(rows.map(r => r.name)).toEqual(['台積電']);
    });
  });

  describe('allDividendPages', () => {
    function pages(total: number) {
      return vi.fn((offset: number): Observable<Paged<Dividend>> => {
        const count = Math.max(0, Math.min(100, total - offset));
        return of({ total, items: Array.from({ length: count }, (_, i) => dividend(offset + i + 1)) });
      });
    }

    it('fetches offsets 0, 100, 200 until the total is covered and concatenates them', () => {
      const fetch = pages(250);
      let result: Dividend[] = [];
      allDividendPages(fetch).subscribe(rows => (result = rows));

      expect(fetch.mock.calls.map(([offset]) => offset)).toEqual([0, 100, 200]);
      expect(result.map(d => d.id)).toEqual(Array.from({ length: 250 }, (_, i) => i + 1));
    });

    it('fetches once and returns nothing when there are no dividends', () => {
      const fetch = pages(0);
      let result: Dividend[] | null = null;
      allDividendPages(fetch).subscribe(rows => (result = rows));

      expect(fetch).toHaveBeenCalledTimes(1);
      expect(result).toEqual([]);
    });

    it('starts from offset 0 again on each subscription', () => {
      const fetch = pages(150);
      const all = allDividendPages(fetch);
      all.subscribe();
      all.subscribe();

      expect(fetch.mock.calls.map(([offset]) => offset)).toEqual([0, 100, 0, 100]);
    });

    it('stops after 50 pages', () => {
      const fetch = pages(100_000);
      allDividendPages(fetch).subscribe();

      expect(fetch).toHaveBeenCalledTimes(50);
    });

    it('stops when a page comes back empty before the total is reached', () => {
      const fetch = vi.fn((offset: number) => of({ total: 500, items: offset === 0 ? [dividend(1)] : [] }));
      let result: Dividend[] = [];
      allDividendPages(fetch).subscribe(rows => (result = rows));

      expect(fetch).toHaveBeenCalledTimes(2);
      expect(result.map(d => d.id)).toEqual([1]);
    });
  });

  it('labels the markets in Chinese', () => {
    expect(MARKET_LABELS).toEqual({ TW: '台股', US: '美股', LSE: '英股' });
  });
});
