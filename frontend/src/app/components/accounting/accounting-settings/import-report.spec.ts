import { describe, expect, it } from 'vitest';

import { summarizeReport } from './import-report';

const SUMMARY = {
  kind: 'moze_backup',
  kind_counts: { expense: 2520, income: 216, transfer_out: 544 },
  // Task 10 keys skipped_future by MOZE record type code (6 = payable repayment, 5 and 3 = receivable, 15 = interest).
  skipped_future: { '3': 2, '5': 1, '6': 240, '15': 4 },
  compared_accounts: { compared: 0, total: 60 },
  not_compared: ['玉山 Only', '錢包', '新帳戶'],
  accounts: [
    { name: '玉山 Only', currency: 'TWD', balance: '-4905.0000', moze_part: '-4905.0000', previous_moze_part: '-4800.0000', moze_balance: null, difference: null, compared: false },
    { name: '錢包', currency: 'TWD', balance: '3070.0000', moze_part: '3070.0000', previous_moze_part: '3070.0000', moze_balance: null, difference: null, compared: false },
    { name: '新帳戶', currency: 'TWD', balance: '0.0000', moze_part: '0.0000', previous_moze_part: null, moze_balance: null, difference: null, compared: false },
  ],
  settings_skipped: [{ name: '玉山 Only', differences: ['closing_day: 15 → kept 20'] }],
  needs_review: { count: 3, reasons: { unpaired_transfer: 2, reward_rule_missing: 1 } },
  fx_outliers: [{ moze_id: 'R-1', date: '2026-09-01', currency: 'JPY', moze_rate: '2.163', cached_rate: '0.2163' }],
};

describe('summarizeReport', () => {
  it('reads the summary from an import_run-shaped report', () => {
    const view = summarizeReport({ status: 'dry_run', summary: SUMMARY });

    expect(view.status).toBe('dry_run');
    expect(view.kindCounts).toEqual([['expense', 2520], ['income', 216], ['transfer_out', 544]]);
    // integer-like keys iterate in ascending numeric order (3, 5, 6, 15), as the backend sorts them
    expect(view.skippedFuture).toEqual([['receivable', 3], ['payable', 240], ['interest', 4]]);
    expect(view.compared).toEqual({ compared: 0, total: 60 });
    expect(view.changedAccounts).toEqual([{ name: '玉山 Only', currency: 'TWD', previous: '-4800.0000', current: '-4905.0000' }]);
    expect(view.settingsSkipped).toEqual(['玉山 Only']);
    expect(view.needsReview).toBe(3);
    expect(view.reviewReasons).toEqual([['轉帳未配對', 2], ['reward_rule_missing', 1]]);
    expect(view.fxOutliers).toBe(1);
  });

  it('accepts a bare summary and tolerant shapes', () => {
    const view = summarizeReport({ kind_counts: {}, settings_skipped: [{ name: 'A' }], needs_review: 2 });
    expect(view.status).toBeNull();
    expect(view.settingsSkipped).toEqual(['A']);
    expect(view.needsReview).toBe(2);
    expect(view.compared).toBeNull();
    expect(view.changedAccounts).toEqual([]);
  });

  it('labels the known review reasons in Chinese and keeps unknown keys', () => {
    const view = summarizeReport({
      needs_review: {
        count: 5,
        reasons: { unpaired_transfer: 1, fx_backup_rate_missing: 2, reward_source_from_package: 1, something_new: 1 },
      },
    });
    expect(view.reviewReasons).toEqual([['轉帳未配對', 1], ['缺少備份匯率', 2], ['回饋來源推定', 1], ['something_new', 1]]);
    expect(summarizeReport({ needs_review: { reasons: ['unpaired_transfer', 'x'] } }).reviewReasons).toEqual([['轉帳未配對', 1], ['x', 1]]);
  });

  it('labels the settlement review reasons in Chinese', () => {
    const view = summarizeReport({
      needs_review: {
        count: 4,
        reasons: { cross_currency_settlement: 1, settlement_overflow: 2, settlement_original_missing: 1 },
      },
    });
    expect(view.reviewReasons).toEqual([['跨幣別結清', 1], ['結清金額超過原款', 2], ['找不到結清對應款項', 1]]);
  });
});
