import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { makeCategory, makeRule } from '../testing/fixtures';
import {
  categoryKindFor,
  evalOrNull,
  findCategory,
  parseTags,
  quickAmounts,
  readLastUse,
  recordAmount,
  resetLegacyHistoryCleanup,
  ruleLabel,
  rulesForDate,
  todayIso,
  writeLastUse,
} from './entry-draft';

// Other spec files share this jsdom storage: start and end clean.
beforeEach(() => localStorage.clear());
afterEach(() => localStorage.clear());

describe('entry draft helpers', () => {
  it('remembers the last account and project per category and ignores corrupt values', () => {
    expect(readLastUse(12)).toBeNull();
    writeLastUse(12, { account_id: 2, project_id: null });
    expect(readLastUse(12)).toEqual({ account_id: 2, project_id: null });
    expect(localStorage.getItem('hh.accounting.lastUse.12')).toBe('{"account_id":2,"project_id":null}');

    localStorage.setItem('hh.accounting.lastUse.13', '{not json');
    expect(readLastUse(13)).toBeNull();

    // Amount history: only an array of finite non-negative amounts is trusted, each once.
    localStorage.setItem('hh.accounting.amounts.14.TWD', '{"170":3}');
    expect(quickAmounts(14, 'TWD')).toEqual([]);
    localStorage.setItem('hh.accounting.amounts.15.TWD', '[170,"85",-1,null,60,170]');
    expect(quickAmounts(15, 'TWD')).toEqual([170, 60]);
    recordAmount(15, 'TWD', 85);
    expect(JSON.parse(localStorage.getItem('hh.accounting.amounts.15.TWD')!)).toEqual([85, 170, 60]);
  });

  it('keeps the last eight distinct quick amounts per currency, most recent first', () => {
    for (const amount of [170, 120, 85, 240, 310, 1500, 999, 60, 45, 120, 170]) {
      recordAmount(12, 'TWD', amount);
    }
    expect(quickAmounts(12, 'TWD')).toEqual([170, 120, 45, 60, 999, 1500, 310, 240]);
    expect(JSON.parse(localStorage.getItem('hh.accounting.amounts.12.TWD')!)).toHaveLength(8);
    // Original-currency amounts are a separate history: ¥1200 is never offered as TWD 1200.
    recordAmount(12, 'JPY', 1200);
    expect(quickAmounts(12, 'JPY')).toEqual([1200]);
    expect(quickAmounts(12, 'TWD')).not.toContain(1200);
    expect(quickAmounts(99, 'TWD')).toEqual([]);
  });

  it('removes the pre-currency quick-amount maps once, on the first read, without migrating them', () => {
    resetLegacyHistoryCleanup();
    localStorage.setItem('hh.accounting.amounts.12', '{"170":3}');
    localStorage.setItem('hh.accounting.amounts.13', '{"85":1}');
    localStorage.setItem('hh.accounting.amounts.12.TWD', '[60]');
    localStorage.setItem('hh.accounting.lastUse.12', '{"account_id":1,"project_id":null}');
    expect(quickAmounts(12, 'TWD')).toEqual([60]);
    expect(localStorage.getItem('hh.accounting.amounts.12')).toBeNull();
    expect(localStorage.getItem('hh.accounting.amounts.13')).toBeNull();
    expect(localStorage.getItem('hh.accounting.amounts.12.TWD')).toBe('[60]');
    expect(localStorage.getItem('hh.accounting.lastUse.12')).not.toBeNull();
    // One-shot: a legacy key written later is left alone in this session.
    localStorage.setItem('hh.accounting.amounts.14', '{"1":1}');
    quickAmounts(14, 'TWD');
    expect(localStorage.getItem('hh.accounting.amounts.14')).toBe('{"1":1}');
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
