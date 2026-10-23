import { describe, expect, it } from 'vitest';

import { Dividend } from '../../../models/portfolio.model';
import { dividendStatus, dividendTotals, netAmountLines, payLine, statusTag } from './dividend-math';

let nextId = 1;
function dv(overrides: Partial<Dividend>): Dividend {
  return { id: nextId++, symbol: '00878', amount: 0, ex_dividend_date: '2026-10-17', receipt_status: 'pending', ...overrides };
}

describe('dividend-math', () => {
  describe('dividendStatus', () => {
    it('maps pending, confirmed and unresolved to themselves', () => {
      expect(dividendStatus(dv({ receipt_status: 'pending' }))).toBe('pending');
      expect(dividendStatus(dv({ receipt_status: 'confirmed' }))).toBe('confirmed');
      expect(dividendStatus(dv({ receipt_status: 'unresolved' }))).toBe('unresolved');
    });

    it('maps legacy_unknown and a missing status to legacy', () => {
      expect(dividendStatus(dv({ receipt_status: 'legacy_unknown' }))).toBe('legacy');
      expect(dividendStatus(dv({ receipt_status: undefined }))).toBe('legacy');
    });
  });

  describe('statusTag', () => {
    it('uses the CONTEXT.md tags', () => {
      expect(statusTag('pending')).toBe('待收');
      expect(statusTag('confirmed')).toBe('已收款');
      expect(statusTag('legacy')).toBe('舊資料 · 未核實');
      expect(statusTag('unresolved')).toBe('待人工釐清');
    });
  });

  describe('dividendTotals', () => {
    it('records confirmed + legacy and leaves pending out (spec scenario)', () => {
      const totals = dividendTotals([
        dv({ amount: 37578, receipt_status: 'confirmed' }),
        dv({ amount: 21342, receipt_status: 'legacy_unknown' }),
        dv({ amount: 14600, receipt_status: 'pending' }),
      ]);
      expect(totals).toEqual({
        confirmed: 37578, confirmedCount: 1,
        pending: 14600, pendingCount: 1,
        legacy: 21342, legacyCount: 1,
        recorded: 58920,
        foreignWithoutFx: 0,
      });
    });

    it('converts foreign rows by fx_rate_to_twd and counts rows without fx once, outside every bucket', () => {
      const totals = dividendTotals([
        dv({ amount: 9600, currency: 'TWD', receipt_status: 'confirmed' }),
        dv({ amount: 7.8, currency: 'USD', market: 'US', fx_rate_to_twd: '30.80', receipt_status: 'confirmed' }),
        dv({ amount: 5, currency: 'USD', market: 'US', fx_rate_to_twd: null, receipt_status: 'pending' }),
      ]);
      expect(totals.confirmed).toBeCloseTo(9840.24, 10);
      expect(totals.confirmedCount).toBe(2);
      expect(totals.pending).toBe(0);
      expect(totals.pendingCount).toBe(0);
      expect(totals.recorded).toBeCloseTo(9840.24, 10);
      expect(totals.foreignWithoutFx).toBe(1);
    });

    it('counts a missing status as legacy and an unresolved row nowhere', () => {
      const totals = dividendTotals([
        dv({ amount: 100, receipt_status: undefined }),
        dv({ amount: 400, receipt_status: 'unresolved' }),
        dv({ amount: 50, currency: 'USD', receipt_status: 'unresolved' }),
      ]);
      expect(totals).toEqual({
        confirmed: 0, confirmedCount: 0,
        pending: 0, pendingCount: 0,
        legacy: 100, legacyCount: 1,
        recorded: 100,
        foreignWithoutFx: 0,
      });
    });
  });

  describe('payLine', () => {
    const accounts = (id: number) => (id === 7 ? '元大交割戶' : null);

    it('shows the scheduled payment date with its source on a pending row', () => {
      expect(payLine(dv({ payment_date: '2026-11-18', payment_date_source: 'TWSE 公告' }), accounts))
        .toEqual({ main: '預定 11/18', sub: 'TWSE 公告' });
      expect(payLine(dv({ payment_date: '2026-11-18', payment_date_source: null }), accounts))
        .toEqual({ main: '預定 11/18', sub: null });
    });

    it('shows the receipt date with the receiving account on a confirmed row', () => {
      expect(payLine(dv({ receipt_status: 'confirmed', payment_date: '2026-08-10', receipt_date: '2026-08-14', receipt_account_id: 7 }), accounts))
        .toEqual({ main: '收款 08/14', sub: '元大交割戶' });
      expect(payLine(dv({ receipt_status: 'confirmed', receipt_date: '2026-08-14', receipt_account_id: 99 }), accounts))
        .toEqual({ main: '收款 08/14', sub: null });
    });

    it('shows — without a usable date', () => {
      expect(payLine(dv({ payment_date: null }), accounts)).toEqual({ main: '—', sub: null });
      expect(payLine(dv({ receipt_status: 'confirmed', receipt_date: null, payment_date: '2026-08-10' }), accounts)).toEqual({ main: '—', sub: null });
    });

    it('renders the full date when its year differs from the ex-date year', () => {
      expect(payLine(dv({ ex_dividend_date: '2026-12-17', payment_date: '2027-01-08' }), accounts).main).toBe('預定 2027/01/08');
    });

    it('reads the dates from their string parts and an ex-date Date by its local year', () => {
      expect(payLine(dv({ ex_dividend_date: new Date(2026, 11, 31, 23, 30), payment_date: '2026-01-08T00:00:00+08:00' }), accounts).main)
        .toBe('預定 01/08');
      expect(payLine(dv({ ex_dividend_date: new Date(2026, 0, 1, 0, 30), payment_date: '2027-01-08' }), accounts).main)
        .toBe('預定 2027/01/08');
    });
  });

  describe('netAmountLines', () => {
    it('renders a TWD row as one grouped main line', () => {
      expect(netAmountLines(dv({ amount: 9600, currency: 'TWD' }))).toEqual({ main: '+9,600', sub: null, twd: 9600 });
      expect(netAmountLines(dv({ amount: 37578 }))).toEqual({ main: '+37,578', sub: null, twd: 37578 });
    });

    it('renders a foreign row with fx as the TWD figure over the native amount and rate', () => {
      const lines = netAmountLines(dv({ amount: 7.8, currency: 'USD', fx_rate_to_twd: '30.80' }));
      expect(lines.main).toBe('+240');
      expect(lines.sub).toBe('7.80 USD · 匯率 30.80');
      expect(lines.twd).toBeCloseTo(240.24, 10);
    });

    it('renders a foreign row without fx as the native amount over —', () => {
      expect(netAmountLines(dv({ amount: 7.8, currency: 'USD', fx_rate_to_twd: null }))).toEqual({ main: '+7.80 USD', sub: '—', twd: null });
    });
  });
});
