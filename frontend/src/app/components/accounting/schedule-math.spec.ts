import { HttpErrorResponse } from '@angular/common/http';
import { describe, expect, it } from 'vitest';

import {
  ScheduleRule,
  installmentFooter,
  intervalLabel,
  isImportRunning,
  nextOccurrences,
  recurringFooter,
  ruleDayLabel,
  ruleSummary,
  scheduleProgress,
  schedulePill,
  splitInstallment,
} from './schedule-math';

const MONTHLY: ScheduleRule = { interval_unit: 'month', interval_n: 1, anchor_date: '2026-10-22', day_of_month: null };

describe('schedule-math', () => {
  it('labels intervals the MOZE way', () => {
    expect(intervalLabel('month', 1)).toBe('每月');
    expect(intervalLabel('week', 2)).toBe('每 2 週');
    expect(intervalLabel('day', 1)).toBe('每天');
    expect(intervalLabel('year', 1)).toBe('每年');
  });

  it('writes the 週期 footer', () => {
    expect(recurringFooter(MONTHLY, null)).toBe('週期：#1 / 無限期（每月 / 22號）');
    expect(recurringFooter(MONTHLY, 12)).toBe('週期：#1 / 12（每月 / 22號）');
    expect(recurringFooter(MONTHLY, null, '2027-01-31')).toBe('週期：#1 / 至 2027/01/31（每月 / 22號）');
    expect(recurringFooter({ ...MONTHLY, interval_unit: 'week', anchor_date: '2026-10-05' }, null)).toBe(
      '週期：#1 / 無限期（每週 / 星期一）',
    );
    expect(recurringFooter({ ...MONTHLY, interval_unit: 'day', interval_n: 3 }, 5)).toBe('週期：#1 / 5（每 3 天）');
  });

  it('splits an installment with the remainder on the last period', () => {
    expect(splitInstallment(10000, 3, 'TWD')).toEqual({ perPeriod: 3333, last: 3334 });
    expect(splitInstallment(300000, 36, 'TWD')).toEqual({ perPeriod: 8333, last: 8345 });
    expect(splitInstallment(100, 3, 'USD')).toEqual({ perPeriod: 33.33, last: 33.34 });
  });

  it('writes the 分期 footer', () => {
    expect(installmentFooter(10000, 3, '2026-11-03', 'TWD')).toBe('分期：#1 / 3（$10,000） 首期入帳日將從 2026/11/03 開始（3 期）');
  });

  it('summarises a rule and its progress', () => {
    const loan = { interval_unit: 'month' as const, interval_n: 1, anchor_date: '2026-11-09', day_of_month: 9, times: 36 };
    expect(ruleSummary(loan)).toBe('每月 9號 · 36 期 · 自 2026/11/09');
    expect(ruleSummary({ ...loan, interval_unit: 'year', anchor_date: '2026-10-22', day_of_month: null })).toBe(
      '每年 10月22號 · 36 期 · 自 2026/10/22',
    );
    expect(ruleDayLabel({ ...loan, interval_unit: 'year', anchor_date: '2026-10-22', day_of_month: null })).toBe('10月22號');
    expect(ruleSummary({ ...loan, interval_unit: 'week', anchor_date: '2026-10-05', day_of_month: null, times: null })).toBe(
      '每週 星期一 · 無限期 · 自 2026/10/05',
    );
    expect(scheduleProgress({ ...loan, posted_count: 3 })).toBe('已入帳 3 / 36');
    expect(scheduleProgress({ ...loan, times: null, posted_count: 3 })).toBe('每月');
    expect(scheduleProgress({ ...loan, interval_unit: 'week', interval_n: 2, times: null, posted_count: 0 })).toBe('每 2 週');
  });

  it('names schedule pills', () => {
    expect(schedulePill({ kind: 'installment', seq: 5, times: 36 })).toBe('分期 #5/36');
    expect(schedulePill({ kind: 'recurring', seq: 25, times: null })).toBe('週期 #25');
    expect(schedulePill({ kind: 'recurring', seq: 3, times: 12 })).toBe('週期 #3/12');
  });

  it('lists occurrences from the anchor, clamping month ends', () => {
    expect(nextOccurrences({ ...MONTHLY, anchor_date: '2026-01-31' }, 3)).toEqual(['2026-01-31', '2026-02-28', '2026-03-31']);
    expect(nextOccurrences(MONTHLY, 2, '2026-10-22')).toEqual(['2026-11-22', '2026-12-22']);
    expect(nextOccurrences({ ...MONTHLY, interval_unit: 'week' }, 2, '2026-10-23')).toEqual(['2026-10-29', '2026-11-05']);
  });

  it('recognises the import_running refusal', () => {
    const conflict = (message: string) =>
      new HttpErrorResponse({ status: 409, error: { code: 409, message, trace_id: 't' } });
    expect(isImportRunning(conflict('import_running'))).toBe(true);
    expect(isImportRunning(conflict('locked_until_cutover'))).toBe(false);
    expect(isImportRunning(new HttpErrorResponse({ status: 409, error: { detail: 'import_running' } }))).toBe(true);
    expect(isImportRunning(new HttpErrorResponse({ status: 500 }))).toBe(false);
    expect(isImportRunning(new Error('import_running'))).toBe(false);
  });
});
