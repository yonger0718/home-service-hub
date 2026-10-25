import { Observable, of } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';

import { Dividend, NetworthPoint, Paged, StockHolding } from '../../../models/portfolio.model';
import {
  ALLOCATION_SERIES,
  abbrInk,
  MARKET_LABELS,
  allDividendPages,
  allocation,
  carryHoldings,
  composition,
  holdingAbbr,
  isCashOnlyPoint,
  millionsTick,
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

    it('clamps a negative part to zero for the percents but keeps the raw amounts', () => {
      expect(
        composition({ total_market_value: 1000, total_cash_twd: '-200', total_pending_dividends_net: 0 }),
      ).toEqual({ stocks: 1000, cash: -200, pending: 0, stocksPct: 100, cashPct: 0 });
      expect(
        composition({ total_market_value: -50, total_cash_twd: '-10', total_pending_dividends_net: 0 }),
      ).toEqual({ stocks: -50, cash: -10, pending: 0, stocksPct: 0, cashPct: 0 });
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

    it('takes the time from finished_at and the tag from now, dated when finished_at is an earlier Taipei day', () => {
      expect(quoteTimeLabel('2026-10-21T09:00:00Z', new Date('2026-10-22T02:00:00Z'))).toBe('行情更新於 10/21 17:00 · 盤中');
      // Friday 13:30 seen on Monday 10:00 Taipei.
      expect(quoteTimeLabel('2026-10-16T05:30:00Z', new Date('2026-10-19T02:00:00Z'))).toBe('行情更新於 10/16 13:30 · 盤中');
    });

    it('compares days in Taipei, not UTC', () => {
      // 23:30 UTC on 10/21 is 07:30 on 10/22 in Taipei: the same day as 08:00 Taipei.
      expect(quoteTimeLabel('2026-10-21T23:30:00Z', new Date('2026-10-22T00:00:00Z'))).toBe('行情更新於 07:30 · 台股收盤');
      // 15:59 UTC on 10/21 is 23:59 on 10/21 in Taipei: the day before 00:01 on 10/22.
      expect(quoteTimeLabel('2026-10-21T15:59:00Z', new Date('2026-10-21T16:01:00Z'))).toBe('行情更新於 10/21 23:59 · 台股收盤');
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

    it('shows the per-share amount with two to four decimals', () => {
      const notes = pendingRows(
        [
          dividend(1, { cash_dividend_per_share: 5, quantity_at_record_date: undefined, payment_date: '2026-11-01' }),
          dividend(2, { cash_dividend_per_share: 0.4825, quantity_at_record_date: undefined, payment_date: '2026-11-02' }),
          dividend(3, { cash_dividend_per_share: 1.123456, quantity_at_record_date: undefined, payment_date: '2026-11-03' }),
        ],
        nameFor,
      ).map(r => r.note);

      expect(notes).toEqual(['每股 5.00', '每股 0.4825', '每股 1.1235']);
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
  describe('holdingAbbr', () => {
    it('takes the last two digits of numeric symbols', () => {
      expect(holdingAbbr('2330', '台積電')).toBe('30');
      expect(holdingAbbr('0050', '元大台灣50')).toBe('50');
      expect(holdingAbbr('00878', '國泰永續高股息')).toBe('78');
    });

    it('takes the first character of the name otherwise, falling back to the symbol', () => {
      expect(holdingAbbr('AAPL', 'Apple')).toBe('A');
      expect(holdingAbbr('VOD', '')).toBe('V');
    });
  });
});

describe('abbrInk', () => {
  it.each([
    ['--app-primary', 'light'],
    ['--app-accent', 'light'],
    ['--app-dividend', 'dark'],
    ['--app-success', 'dark'],
    ['--app-warning', 'dark'],
    ['--app-text-muted', 'light'],
  ])('%s -> %s', (token, ink) => {
    expect(abbrInk(token)).toBe(ink);
  });
});

function row(
  date: string,
  o: { market?: string; cost?: string; upnl?: string; div?: string; rpnl?: string; xirr?: string | null; cash?: string } = {},
): NetworthPoint {
  const { market = '0', cost = '0', upnl = '0', div = '0', rpnl = '0', xirr = null, cash = '0' } = o;
  return {
    date,
    total_market_value: market,
    total_cost: cost,
    total_unrealized_pnl: upnl,
    total_dividends: div,
    total_realized_pnl: rpnl,
    portfolio_xirr: xirr,
    total_cash_twd: cash,
    total_assets_twd: String(Number(market) + Number(cash)),
  } as NetworthPoint;
}

const FRIDAY = () =>
  row('2026-01-09', { market: '2900000', cost: '2500000', upnl: '400000', div: '10000', rpnl: '20000', xirr: '0.1', cash: '100000' });

describe('isCashOnlyPoint', () => {
  it('is true for all-zero stock columns with null xirr', () => {
    expect(isCashOnlyPoint(row('2026-01-10', { cash: '5' }))).toBe(true);
  });

  it.each([{ market: '1' }, { cost: '1' }, { upnl: '1' }, { div: '1' }, { rpnl: '1' }])('is false when %o is non-zero', o => {
    expect(isCashOnlyPoint(row('2026-01-10', o))).toBe(false);
  });

  it.each(['0', '0.12'])('is false when xirr is %s', xirr => {
    expect(isCashOnlyPoint(row('2026-01-10', { xirr }))).toBe(false);
  });
});

describe('carryHoldings', () => {
  it('leaves leading cash-only rows as written', () => {
    const first = row('2026-01-05', { cash: '50' });
    const out = carryHoldings([first, row('2026-01-06', { market: '10', cash: '5', xirr: '0.1' })]);
    expect(out[0]).toBe(first);
  });

  it('carries Friday onto a Saturday between two trading days', () => {
    const mon = row('2026-01-12', { market: '3000000', xirr: '0.1', cash: '1' });
    const out = carryHoldings([FRIDAY(), row('2026-01-10', { cash: '114600' }), mon]);
    expect(out[1]).toMatchObject({
      date: '2026-01-10',
      total_market_value: '2900000',
      total_cost: '2500000',
      total_unrealized_pnl: '400000',
      total_dividends: '10000',
      total_realized_pnl: '20000',
      total_cash_twd: '114600',
      total_assets_twd: '3014600',
    });
    expect(out[1].portfolio_xirr).toBeNull();
    expect(out[2]).toBe(mon);
  });

  it('carries Friday onto both Saturday and Sunday', () => {
    const mon = row('2026-01-12', { market: '3000000', xirr: '0.1', cash: '1' });
    const out = carryHoldings([FRIDAY(), row('2026-01-10', { cash: '10' }), row('2026-01-11', { cash: '20' }), mon]);
    expect(out[1]).toMatchObject({ total_market_value: '2900000', total_assets_twd: '2900010' });
    expect(out[2]).toMatchObject({ total_market_value: '2900000', total_assets_twd: '2900020' });
    expect(out[3]).toBe(mon);
  });

  it('does not bridge a liquidation row', () => {
    const liq = row('2026-01-10', { rpnl: '35205', cash: '100' });
    const out = carryHoldings([FRIDAY(), liq]);
    expect(out[1]).toBe(liq);
    expect(out[1].total_market_value).toBe('0');
  });

  it('keeps a cash-only day after a liquidation at 0', () => {
    const out = carryHoldings([FRIDAY(), row('2026-01-10', { rpnl: '35205', cash: '100' }), row('2026-01-11', { cash: '100' })]);
    expect(out[2].total_market_value).toBe('0');
  });

  it('does not treat holdings with zero cash as cash-only', () => {
    const h = row('2026-01-10', { market: '100', cash: '0' });
    expect(carryHoldings([FRIDAY(), h])[1]).toBe(h);
  });

  it('carries a cash-only row with total_cash_twd 0 after holdings, total assets = the carried market value', () => {
    const out = carryHoldings([FRIDAY(), row('2026-01-10', { cash: '0' })]);
    expect(out[1]).toMatchObject({ total_market_value: '2900000', total_cash_twd: '0', total_assets_twd: '2900000' });
  });
});

describe('millionsTick', () => {
  const t = (...v: number[]) => v.map(value => ({ value }));
  it('uses one decimal at a 100k step', () => {
    expect(millionsTick(3_400_000, t(3_400_000, 3_500_000))).toBe('3.4M');
  });
  it('uses two decimals at a 50k step', () => {
    expect(millionsTick(3_450_000, t(3_400_000, 3_450_000))).toBe('3.45M');
    expect(millionsTick(3_400_000, t(3_400_000, 3_450_000))).toBe('3.40M');
  });
  it('uses two decimals at a 10k step', () => {
    expect(millionsTick(3_420_000, t(3_410_000, 3_420_000))).toBe('3.42M');
  });
  it('uses three decimals at a 5k step', () => {
    expect(millionsTick(3_405_000, t(3_400_000, 3_405_000))).toBe('3.405M');
  });
  it('keeps one decimal at a 500k step', () => {
    expect(millionsTick(3_500_000, t(3_000_000, 3_500_000))).toBe('3.5M');
  });
  it('defaults to one decimal with a single or no tick', () => {
    expect(millionsTick(2_000_000, t(1))).toBe('2.0M');
    expect(millionsTick(2_000_000, [])).toBe('2.0M');
  });
});
