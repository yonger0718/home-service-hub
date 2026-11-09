import { HttpErrorResponse } from '@angular/common/http';
import { describe, expect, it } from 'vitest';
import { makeRule } from './testing/fixtures';
import { RewardUsage } from '../../models/accounting.model';
import {
  REWARD_WINDOW_LABELS, RULE_STATE_LABELS, basicOverlapRuleId, fullDate, methodSummary, monthDay, parseReferenced, periodText,
  rateChip, referencedText, rewardFigure, postingSummary, ruleState, ruleTitle, ruleToInput, usageUnit, windowText,
} from './reward-rule-text';

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

describe('per_unit', () => {
  it('per_unit summary', () => {
    expect(methodSummary(makeRule({ method: 'per_unit', rate: null, unit_spend: '30.0000' }))).toBe('每 NT$30 一單位');
    expect(methodSummary(makeRule({ rate: '1.5000' }))).toBe('1.5%');
    expect(REWARD_WINDOW_LABELS).toEqual({ statement_cycle: '帳單週期', calendar_month: '自然月' });
  });
  it('carries unit_spend and window into the input', () => {
    const input = ruleToInput(makeRule({ method: 'per_unit', rate: null, unit_spend: '30.0000', window: 'calendar_month' }));
    expect(input.unit_spend).toBe('30');
    expect(input.window).toBe('calendar_month');
  });
});

describe('409 parsing', () => {
  const conflict = (message: string) => new HttpErrorResponse({ status: 409, error: { code: 'conflict', message } });
  it('reads rule_referenced counts', () => {
    expect(parseReferenced(conflict('rule_referenced: {"entries": 3, "rewards": 2}'))).toEqual({ entries: 3, rewards: 2, accruals: 0 });
    expect(parseReferenced(conflict('rule_referenced: {"entries": 0, "rewards": 0, "accruals": 4}')))
      .toEqual({ entries: 0, rewards: 0, accruals: 4 });
    expect(parseReferenced(conflict('basic_rule_overlap: rule 9'))).toBeNull();
  });
  it('words only the non-zero references (#294)', () => {
    expect(referencedText({ entries: 3, rewards: 2, accruals: 0 })).toBe('3 筆交易 / 2 筆回饋');
    expect(referencedText({ entries: 0, rewards: 0, accruals: 4 })).toBe('4 筆預估回饋');
    expect(referencedText({ entries: 1, rewards: 0, accruals: 2 })).toBe('1 筆交易 / 2 筆預估回饋');
  });
  it('reads the clashing basic rule id', () => {
    expect(basicOverlapRuleId(conflict('basic_rule_overlap: rule 9'))).toBe(9);
    expect(basicOverlapRuleId(new HttpErrorResponse({ status: 422 }))).toBeNull();
  });
});

// polish-reward-and-account-settings PR B (D1a, D3): overview text helpers.
describe('overview text helpers', () => {
  const usage = (over: Partial<RewardUsage> = {}): RewardUsage => ({
    rule_ids: [1], shared_cap_id: null, name: 'A', window_start: '2026-10-26', window_end: '2026-11-25',
    used: '236', estimated: '64', cap: '500', remaining: '200',
    reward_account_id: 9, reward_account_name: '測試卡', reward_is_money: true, ...over,
  });

  it('usageUnit / rewardFigure: money with $, points as the number and the account name', () => {
    expect(rewardFigure('1236', usageUnit(usage()))).toBe('$1,236');
    expect(rewardFigure(0, usageUnit(usage()))).toBe('$0');
    const points = usageUnit(usage({ reward_is_money: false, reward_account_name: '測試里程' }));
    expect(points).toEqual({ money: false, name: '測試里程' });
    expect(rewardFigure('120', points)).toBe('120 測試里程');
  });

  it('ruleTitle strips a trailing rate; rateChip is the method summary', () => {
    const rule = makeRule({ name: '玩數位 3%', method: 'percent', rate: '3' });
    expect([ruleTitle(rule), rateChip(rule)]).toEqual(['玩數位', '3%']);
    expect(ruleTitle(makeRule({ ...rule, name: '全聯・家樂福加碼' }))).toBe('全聯・家樂福加碼');
    expect(ruleTitle(makeRule({ ...rule, name: '海外 (3%)' }))).toBe('海外');
    expect(ruleTitle(makeRule({ ...rule, name: '3%' }))).toBe('3%');
  });

  it('monthDay / windowText / fullDate drop the zero padding', () => {
    expect(monthDay('2026-11-06')).toBe('11/6');
    expect(windowText(usage())).toBe('10/26–11/25');
    expect(fullDate('2027-01-01')).toBe('2027/1/1');
  });

  it('state labels use the spec words', () => {
    expect(RULE_STATE_LABELS).toEqual({ active: '生效中', disabled: '已停用', expired: '已過期', upcoming: '未開始' });
  });
});
