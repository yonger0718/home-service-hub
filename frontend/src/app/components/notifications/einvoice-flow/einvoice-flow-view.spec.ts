import { describe, expect, it } from 'vitest';

import { makeEinvoice, makeEinvoiceTarget } from '../../accounting/inbox/testing';
import { answerKeys, einvoiceStep } from './einvoice-flow-view';

const coffee = (over = {}) =>
  makeEinvoice({ seller_name: '測試咖啡', total_amount: '85', issue_date: '2026-10-22', ...over });
const entry = (over = {}) =>
  makeEinvoiceTarget({ amount: '-85', date: '2026-10-22', account_name: '測試 Pi 卡', category: '餐飲', name: '咖啡', ...over });

describe('einvoiceStep', () => {
  it('the spec scenario: ambiguous, two −85 candidates on 10/22 and 10/21 → 有兩筆都像, the second marked 日期差 1 天', () => {
    const step = einvoiceStep(coffee({ candidates: [entry({ id: 1 }), entry({ id: 2, date: '2026-10-21', account_name: '現金' })] }));
    expect(step.chip).toBe('有兩筆都像');
    expect(step.reason).toBe('金額相同的帳有 2 筆');
    expect(step.answers.map((a) => [a.label, a.sub, a.amount, a.chips])).toEqual([
      ['連結 咖啡', '測試 Pi 卡 · 10/22 · 餐飲', '$85', []],
      ['連結 咖啡', '現金 · 10/21 · 餐飲', '$85', ['日期差 1 天']],
    ]);
    expect(answerKeys(2)).toEqual({ candidates: [1, 2], dismiss: 3 });
  });

  it('ambiguous with every candidate on the invoice date reads 同一天、同金額', () => {
    const step = einvoiceStep(coffee({ candidates: [entry({ id: 1 }), entry({ id: 2 }), entry({ id: 3 })] }));
    expect(step.reason).toBe('同一天、同金額的帳有 3 筆');
  });

  it('near within the matcher tolerance (max 50, 20%) → 這筆最接近, with 金額差', () => {
    const step = einvoiceStep(coffee({ state: 'near', candidates: [entry({ amount: '-80' })] }));
    expect(step.chip).toBe('差一點');
    expect(step.reason).toBe('沒有金額完全相同的帳，這筆最接近');
    expect(step.answers[0].chips).toEqual(['金額差 $5']);
  });

  it('near outside the tolerance (a seller-name match) → 商家名稱對得上，金額不同', () => {
    const step = einvoiceStep(coffee({ state: 'near', candidates: [entry({ amount: '-300', date: '2026-10-24' })] }));
    expect(step.reason).toBe('商家名稱對得上，金額不同');
    expect(step.answers[0].chips).toEqual(['日期差 2 天', '金額差 $215']);
  });

  it('a large invoice uses the 20% tolerance: 1000 vs 1150 is close, 1000 vs 1250 is not', () => {
    const at = (amount: string) =>
      einvoiceStep(coffee({ state: 'near', total_amount: '1000', candidates: [entry({ amount })] })).reason;
    expect(at('-1150')).toBe('沒有金額完全相同的帳，這筆最接近');
    expect(at('-1250')).toBe('商家名稱對得上，金額不同');
  });

  it('labels fall back from name to category to 這筆帳; no category leaves it out of the sub line', () => {
    const step = einvoiceStep(coffee({ candidates: [entry({ name: null }), entry({ name: null, category: null })] }));
    expect(step.answers.map((a) => [a.label, a.sub])).toEqual([
      ['連結 餐飲', '測試 Pi 卡 · 10/22 · 餐飲'],
      ['連結 這筆帳', '測試 Pi 卡 · 10/22'],
    ]);
  });

  it('the invoice card: seller, date, number, tax id (or 沒有統編), items and total', () => {
    const step = einvoiceStep(coffee({ seller_tax_id: '', items: [{ name: '拿鐵', subtotal: '85' }] }));
    expect(step.card).toEqual({
      seller: '測試咖啡', date: '2026/10/22', number: 'AB00000001', taxId: '沒有統編',
      items: [{ name: '拿鐵', subtotal: '$85' }], total: '$85',
    });
  });
});

describe('answerKeys', () => {
  it('digits 1–9 only: candidates first, 都不是 at N+1 when it fits', () => {
    expect(answerKeys(1)).toEqual({ candidates: [1], dismiss: 2 });
    expect(answerKeys(8)).toEqual({ candidates: [1, 2, 3, 4, 5, 6, 7, 8], dismiss: 9 });
    expect(answerKeys(9)).toEqual({ candidates: [1, 2, 3, 4, 5, 6, 7, 8, 9], dismiss: null });
    expect(answerKeys(10)).toEqual({ candidates: [1, 2, 3, 4, 5, 6, 7, 8, 9, null], dismiss: null });
    expect(answerKeys(0)).toEqual({ candidates: [], dismiss: 1 });
  });
});
