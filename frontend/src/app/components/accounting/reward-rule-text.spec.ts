import { HttpErrorResponse } from '@angular/common/http';
import { describe, expect, it } from 'vitest';
import { makeRule } from './testing/fixtures';
import { basicOverlapRuleId, parseReferenced, periodText, postingSummary, ruleState, ruleToInput } from './reward-rule-text';

describe('ruleState', () => {
  it('orders disabled > expired > upcoming > active', () => {
    expect(ruleState(makeRule({ is_enabled: false, ends_on: '2020-01-01' }), '2026-11-01')).toBe('disabled');
    expect(ruleState(makeRule({ ends_on: '2026-10-31' }), '2026-11-01')).toBe('expired');
    expect(ruleState(makeRule({ starts_on: '2026-11-02', ends_on: null }), '2026-11-01')).toBe('upcoming');
    expect(ruleState(makeRule({ starts_on: '2026-11-01', ends_on: '2026-11-01' }), '2026-11-01')).toBe('active');
    expect(ruleState(makeRule({ starts_on: null, ends_on: null }), '2026-11-01')).toBe('active');
  });
});

describe('text', () => {
  it('summarises posting', () => {
    expect(postingSummary(makeRule({ posting: 'after_window', post_month_offset: 1, post_day: 15 }))).toBe('帳單結帳後第 1 月 15 日');
    expect(postingSummary(makeRule({ posting: 'after_transaction', delay_days: 3 }))).toBe('交易後 3 天');
    expect(postingSummary(makeRule({ posting: 'manual' }))).toBe('手動');
  });
  it('renders the period', () => {
    expect(periodText({ starts_on: '2026-01-01', ends_on: '2026-12-31' })).toBe('2026/01/01 – 2026/12/31');
    expect(periodText({ starts_on: '2026-01-01', ends_on: null })).toBe('2026/01/01 起');
    expect(periodText({ starts_on: null, ends_on: '2026-12-31' })).toBe('至 2026/12/31');
    expect(periodText({ starts_on: null, ends_on: null })).toBe('無期限');
  });
  it('maps a rule to its input without id, account, moze_id, shared cap', () => {
    const input = ruleToInput(makeRule({ rate: '1.5000', total_cap: '150.0000', moze_id: 'B-1', shared_cap_id: 'x' }));
    expect(input).not.toHaveProperty('id');
    expect(input).not.toHaveProperty('moze_id');
    expect(input).not.toHaveProperty('shared_cap_id');
    expect(input.total_cap).toBe('150');
    expect(input.rate).toBe('1.5');
  });
});

describe('409 parsing', () => {
  const conflict = (message: string) => new HttpErrorResponse({ status: 409, error: { code: 'conflict', message } });
  it('reads rule_referenced counts', () => {
    expect(parseReferenced(conflict('rule_referenced: {"entries": 3, "rewards": 2}'))).toEqual({ entries: 3, rewards: 2 });
    expect(parseReferenced(conflict('basic_rule_overlap: rule 9'))).toBeNull();
  });
  it('reads the clashing basic rule id', () => {
    expect(basicOverlapRuleId(conflict('basic_rule_overlap: rule 9'))).toBe(9);
    expect(basicOverlapRuleId(new HttpErrorResponse({ status: 422 }))).toBeNull();
  });
});
