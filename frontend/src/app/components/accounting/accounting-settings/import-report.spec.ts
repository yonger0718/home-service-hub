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
    expect(view.reviewReasons).toEqual([['unpaired_transfer', 2], ['reward_rule_missing', 1]]);
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
});
