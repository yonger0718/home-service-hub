import { describe, expect, it } from 'vitest';

import { Dividend } from '../../../models/portfolio.model';
import { stockSquare } from '../square';
import { deductionLine, dividendRow, exDateLabel, matchesStatus, perShareLine, statusTagVariant } from './dividend-rows';

let nextId = 1;
function dv(overrides: Partial<Dividend>): Dividend {
  return { id: nextId++, symbol: '00878', amount: 9600, ex_dividend_date: '2026-10-17', receipt_status: 'pending', ...overrides };
}

const noAccounts = () => null;

describe('dividend rows', () => {
  describe('exDateLabel', () => {
    it('reads a string from its own parts and a Date from its local parts', () => {
      expect(exDateLabel('2026-10-17')).toBe('2026/10/17');
      expect(exDateLabel('2026-10-17T23:30:00Z')).toBe('2026/10/17');
      expect(exDateLabel(new Date(2025, 2, 16))).toBe('2025/03/16');
    });
  });

  describe('perShareLine', () => {
    it('keeps 2–4 fraction digits and groups the quantity', () => {
      expect(perShareLine(dv({ cash_dividend_per_share: 0.48, quantity_at_record_date: 20000 }))).toBe('0.48 × 20,000');
      expect(perShareLine(dv({ cash_dividend_per_share: 0.042, quantity_at_record_date: 1000 }))).toBe('0.042 × 1,000');
      expect(perShareLine(dv({ cash_dividend_per_share: 0.0421, quantity_at_record_date: 1000 }))).toBe('0.0421 × 1,000');
      expect(perShareLine(dv({ cash_dividend_per_share: 3.1, quantity_at_record_date: 1000 }))).toBe('3.10 × 1,000');
    });

    it('drops the quantity when it is missing and falls back to 現金股利 without a per-share figure', () => {
      expect(perShareLine(dv({ cash_dividend_per_share: 0.0001, quantity_at_record_date: undefined }))).toBe('0.0001');
      expect(perShareLine(dv({ cash_dividend_per_share: undefined, quantity_at_record_date: 1000 }))).toBe('現金股利');
    });

    it('suffixes the currency on a foreign row', () => {
      expect(perShareLine(dv({ currency: 'USD', cash_dividend_per_share: 0.26, quantity_at_record_date: 30 }))).toBe('0.26 USD × 30');
    });
  });

  describe('deductionLine', () => {
    it('lists only the deductions above zero', () => {
      expect(deductionLine(dv({ fee: 10, tax: 202 }))).toBe('手續費 10 · 補充保費 202');
      expect(deductionLine(dv({ fee: 0, tax: 1202 }))).toBe('補充保費 1,202');
      expect(deductionLine(dv({ fee: 10 }))).toBe('手續費 10');
      expect(deductionLine(dv({ fee: 0, tax: 0 }))).toBeNull();
      expect(deductionLine(dv({}))).toBeNull();
    });

    it('formats a foreign row in its own currency', () => {
      expect(deductionLine(dv({ currency: 'USD', tax: 2.34 }))).toBe('補充保費 2.34 USD');
    });
  });

  describe('statusTagVariant', () => {
    it('maps each status to an existing tag variant', () => {
      expect(statusTagVariant('pending')).toBe('primary');
      expect(statusTagVariant('confirmed')).toBe('dividend');
      expect(statusTagVariant('legacy')).toBe('neutral');
      expect(statusTagVariant('unresolved')).toBe('warning');
    });
  });

  describe('matchesStatus', () => {
    it('keeps unresolved rows only under 全部 and counts a missing status as 舊資料', () => {
      const unresolved = dv({ receipt_status: 'unresolved' });
      expect(matchesStatus(unresolved, 'all')).toBe(true);
      for (const filter of ['pending', 'confirmed', 'legacy'] as const) expect(matchesStatus(unresolved, filter)).toBe(false);
      expect(matchesStatus(dv({ receipt_status: undefined }), 'legacy')).toBe(true);
      expect(matchesStatus(dv({ receipt_status: 'legacy_unknown' }), 'legacy')).toBe(true);
      expect(matchesStatus(dv({ receipt_status: 'pending' }), 'pending')).toBe(true);
      expect(matchesStatus(dv({ receipt_status: 'pending' }), 'confirmed')).toBe(false);
    });
  });

  describe('dividendRow', () => {
    it('builds a pending row: square, name + code, ex-date, schedule, per-share, deductions, primary amount', () => {
      const d = dv({ id: 5, cash_dividend_per_share: 0.48, quantity_at_record_date: 20000, tax: 202,
        payment_date: '2026-11-18', payment_date_source: 'TWSE 公告' });
      expect(dividendRow(d, '國泰永續高股息', noAccounts)).toEqual({
        id: 5,
        status: 'pending',
        square: stockSquare('00878', '國泰永續高股息'),
        primary: '國泰永續高股息 00878',
        exDate: '2026/10/17',
        dateLine: '除息 2026/10/17 · 預定 11/18',
        pay: { main: '預定 11/18', sub: 'TWSE 公告' },
        perShare: '0.48 × 20,000',
        deductions: '補充保費 202',
        net: { main: '+9,600', sub: null },
        netClass: 'primary',
        tag: { text: '待收', variant: 'primary' },
        reviewReason: null,
        sourceCorrected: false,
      });
    });

    it('uses the dividend colour for confirmed and legacy rows and the account name under the receipt date', () => {
      const confirmed = dividendRow(dv({ receipt_status: 'confirmed', receipt_date: '2026-11-20', receipt_account_id: 7 }), null, (id: number) => (id === 7 ? '元大交割戶' : null));
      expect(confirmed).toMatchObject({ primary: '00878', netClass: 'dividend', pay: { main: '收款 11/20', sub: '元大交割戶' }, tag: { text: '已收款' } });
      expect(confirmed.dateLine).toBe('除息 2026/10/17 · 收款 11/20');
      expect(dividendRow(dv({ receipt_status: 'legacy_unknown' }), null, noAccounts))
        .toMatchObject({ netClass: 'dividend', tag: { text: '舊資料 · 未核實', variant: 'neutral' }, dateLine: '除息 2026/10/17' });
    });

    it('keeps the review reason and flags a source correction', () => {
      const row = dividendRow(dv({ receipt_status: 'unresolved', review_reason: 'Synthetic review', source_correction: { amount: 1 } }), null, noAccounts);
      expect(row).toMatchObject({ reviewReason: 'Synthetic review', sourceCorrected: true, tag: { text: '待人工釐清', variant: 'warning' } });
    });

    it('shows a foreign row in TWD over its native amount, or native over — without fx', () => {
      expect(dividendRow(dv({ currency: 'USD', amount: 7.8, fx_rate_to_twd: '30.80' }), null, noAccounts).net).toEqual({ main: '+240', sub: '7.80 USD · 匯率 30.80' });
      expect(dividendRow(dv({ currency: 'USD', amount: 7.8, fx_rate_to_twd: null }), null, noAccounts).net).toEqual({ main: '+7.80 USD', sub: '—' });
    });
  });
});
