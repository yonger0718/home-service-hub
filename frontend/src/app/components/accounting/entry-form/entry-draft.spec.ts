import { afterEach, describe, expect, it } from 'vitest';

import { makeCategory, makeRule } from '../testing/fixtures';
import {
  categoryKindFor,
  evalOrNull,
  findCategory,
  parseTags,
  quickAmounts,
  readLastUse,
  recordAmount,
  ruleLabel,
  rulesForDate,
  todayIso,
  writeLastUse,
} from './entry-draft';

afterEach(() => localStorage.clear());

describe('entry draft helpers', () => {
  it('remembers the last account and project per category and ignores corrupt values', () => {
    expect(readLastUse(12)).toBeNull();
    writeLastUse(12, { account_id: 2, project_id: null });
    expect(readLastUse(12)).toEqual({ account_id: 2, project_id: null });
    expect(localStorage.getItem('hh.accounting.lastUse.12')).toBe('{"account_id":2,"project_id":null}');

    localStorage.setItem('hh.accounting.lastUse.13', '{not json');
    expect(readLastUse(13)).toBeNull();

    // Amount history: only a plain object of finite non-negative integer counts is trusted.
    localStorage.setItem('hh.accounting.amounts.14', '[3, 5]');
    expect(quickAmounts(14)).toEqual([]);
    localStorage.setItem('hh.accounting.amounts.15', '{"170":3,"85":"9","90":-1,"95":1.5,"99":null,"60":2}');
    expect(quickAmounts(15)).toEqual([170, 60]);
    recordAmount(15, 85);
    expect(JSON.parse(localStorage.getItem('hh.accounting.amounts.15')!)).toEqual({ '170': 3, '60': 2, '85': 1 });
  });

  it('ranks quick amounts by how often they were saved, six at most', () => {
    for (const amount of [170, 170, 170, 120, 120, 85, 240, 310, 1500, 999]) {
      recordAmount(12, amount);
    }
    expect(quickAmounts(12)).toEqual([170, 120, 85, 240, 310, 999]);
    expect(quickAmounts(99)).toEqual([]);
  });

  it('offers enabled rules covering the entry date and labels them with their rate', () => {
    const rules = [
      makeRule({ id: 1, name: '一般', rate: '1.0000' }),
      makeRule({ id: 2, name: 'Up選', rate: '3.5000', starts_on: '2026-07-01', ends_on: '2026-12-31' }),
      makeRule({ id: 3, name: '過期', ends_on: '2026-06-30' }),
      makeRule({ id: 4, name: '停用', is_enabled: false }),
      makeRule({ id: 5, name: '首刷禮', method: 'fixed', rate: null, fixed_amount: '200.0000' }),
    ];
    expect(rulesForDate(rules, '2026-10-02').map(rule => rule.id)).toEqual([1, 2, 5]);
    expect(rules.slice(0, 2).map(ruleLabel)).toEqual(['一般 (1%)', 'Up選 (3.5%)']);
    expect(ruleLabel(rules[4])).toBe('首刷禮 ($200)');
  });

  it('parses #tags, finds categories at both levels and maps tabs to category kinds', () => {
    expect(parseTags('#午餐 #公司, LinePay ＃午餐')).toEqual(['午餐', '公司', 'LinePay']);
    const lunch = makeCategory({ id: 12, parent_id: 1, name: '午餐' });
    const tree = [makeCategory({ id: 1, children: [lunch] })];
    expect(findCategory(tree, 12)).toBe(lunch);
    expect(findCategory(tree, 99)).toBeNull();
    expect(categoryKindFor('system')).toBeNull();
    expect(categoryKindFor('transfer')).toBe('transfer_out');
    expect(categoryKindFor('payable')).toBe('payable');
    expect(evalOrNull('3×40', 0)).toBe(120);
    expect(evalOrNull('', 0)).toBeNull();
    expect(evalOrNull('1÷0', 0)).toBeNull();
    expect(todayIso(new Date(2026, 9, 2, 23, 59))).toBe('2026-10-02');
  });
});
