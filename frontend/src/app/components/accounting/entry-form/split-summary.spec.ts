import { describe, expect, it } from 'vitest';

import { makeAccount, makeEntryDetail, makeRule } from '../testing/fixtures';
import { ChildDraft, newChild, newParent } from './split-draft';
import { childFromDetail } from './split-load';
import { dissolveNotices, netByCurrency, rewardEstimates } from './split-summary';

describe('split summary', () => {
  it('uses stored signs and account currencies', () => {
    const row = (kind: 'receivable' | 'payable' | 'balance_adjustment', amount: string) =>
      childFromDetail(makeEntryDetail({ kind, amount }), { protected: true, protected_reason: 'test' }, 'TWD');
    const fx = {
      ...newChild('expense', 2), amountExpr: '1000',
      fx: {
        original_amount: '1000', original_currency: 'JPY', account_currency: 'USD',
        amount: '6.7', fx_rate: null, use_online: false, manual: 'amount' as const, rate_date: null,
      },
    };
    const rows = [row('receivable', '-100'), row('receivable', '40'), row('payable', '80'),
      row('payable', '-30'), row('balance_adjustment', '-7'), row('balance_adjustment', '2'), fx];
    expect(netByCurrency(rows, [makeAccount(), makeAccount({ id: 2, currency: 'USD' })]))
      .toEqual([{ currency: 'TWD', amount: -15 }, { currency: 'USD', amount: -6.7 }]);
  });

  it('keeps unknown FX unknown and estimates only selected rules', () => {
    const c: ChildDraft = {
      ...newChild('expense', 1), amountExpr: '100', ruleIds: [3],
      availableRules: [makeRule({ id: 3, rate: '2' }), makeRule({ id: 4, rate: '50' })],
    };
    expect(rewardEstimates([c], [makeAccount()])).toEqual([{ currency: 'TWD', amount: 2 }]);
    c.fx = {
      original_amount: '100', original_currency: 'USD', account_currency: 'TWD',
      amount: null, fx_rate: null, use_online: true, manual: null, rate_date: null,
    };
    expect(netByCurrency([c], [makeAccount()])).toEqual([{ currency: 'TWD', amount: null }]);
    expect(netByCurrency([c, { ...newChild('expense', 1), amountExpr: '5' }], [makeAccount()]))
      .toEqual([{ currency: 'TWD', amount: null }]);
  });

  it('counts a blank child as 0 but an invalid expression as unknown', () => {
    const typed = { ...newChild('expense', 1), amountExpr: '100' };
    const blank = newChild('expense', 1);
    expect(netByCurrency([typed, blank], [makeAccount()])).toEqual([{ currency: 'TWD', amount: -100 }]);
    expect(netByCurrency([typed, { ...blank, amountExpr: '1÷0' }], [makeAccount()])).toEqual([{ currency: 'TWD', amount: null }]);
  });

  it('shows each copy-skipped field, treating whitespace as empty', () => {
    const p = { ...newParent('2026-10-06', ''), name: 'Group', merchant: 'Shop', description: 'Note' };
    const c = childFromDetail(
      makeEntryDetail({ name: 'child', merchant: '   ', description: 'child note' }),
      { protected: false, protected_reason: null },
      'TWD',
    );
    expect(dissolveNotices(p, c)).toEqual(['整筆名稱「Group」將不保留', '整筆備註將不保留']);
    c.name = ' ';
    expect(dissolveNotices(p, c)).toEqual(['整筆備註將不保留']);
  });
});
