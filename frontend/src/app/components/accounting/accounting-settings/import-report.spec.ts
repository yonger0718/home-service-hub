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

  it('summarises the schedules block', () => {
    const view = summarizeReport({
      summary: {
        schedules: {
          definitions: {
            recurring: { created: 11, updated: 0, ended: 0, deleted: 0 },
            installment: { created: 14, updated: 0, ended: 0, deleted: 0 },
            single: { created: 0, updated: 0, ended: 0, deleted: 0 },
          },
          records_mapped: 534, rewards_ignored: 63, past_records_already_posted: 2, past_records_already_skipped: 1,
          past_records_amount_differs: { count: 1, instance_ids: [77] }, review: [{ moze_id: 'P-1', reason: 'interval_mismatch' }],
        },
      },
    });
    expect(view.schedules).toEqual({
      recurring: 11, installment: 14, single: 0, recordsMapped: 534, rewardsIgnored: 63, alreadyPosted: 2, alreadySkipped: 1,
      amountDiffers: 1, review: 1, ownerPending: 0, pendingAmountDiffers: 0, pastAmountLines: [], pendingAmountLines: [],
      ownerPendingLines: [], dependantsSuppressed: 0, dependantLines: [], reviewLines: [{ mozeId: 'P-1', label: '間隔與記錄不符' }],
    });
    expect(summarizeReport({ summary: {} }).schedules).toBeNull();
  });

  it('shows pending amount differences when they are the only finding', () => {
    // Multica R-A3: only amount_differs is non-empty; the view still carries its count and per-period lines.
    const view = summarizeReport({
      summary: {
        schedules: {
          definitions: {
            recurring: { created: 0, updated: 1, ended: 0, deleted: 0 },
            installment: { created: 0, updated: 0, ended: 0, deleted: 0 },
            single: { created: 0, updated: 0, ended: 0, deleted: 0 },
          },
          records_mapped: 4, rewards_ignored: 0, past_records_already_posted: 0, past_records_already_skipped: 0,
          past_records_amount_differs: { count: 0, instance_ids: [], lines: [] }, past_records_owner_pending: [], review: [],
          amount_differs: [
            { definition_id: 7, name: 'Netflix', seq: 3, date: '2026-10-05', line: 0, kind: 'expense', amount: '120', moze_amount: '100' },
          ],
        },
      },
    });
    expect(view.schedules?.pendingAmountDiffers).toBe(1);
    expect(view.schedules?.amountDiffers).toBe(0);
    expect(view.schedules?.pendingAmountLines).toEqual([
      { name: 'Netflix', seq: 3, date: '2026-10-05', kind: 'expense', amount: '120', mozeAmount: '100' },
    ]);
  });

  it('lists owner-pending periods, suppressed dependants and every review reason with Chinese copy', () => {
    const reasons = ['interval_mismatch', 'same_date', 'no_records', 'event_missing', 'seq_conflict', 'account_missing', 'loan_missing', 'not_live'];
    const view = summarizeReport({
      summary: {
        schedules: {
          definitions: { recurring: { created: 0 }, installment: { created: 0 }, single: { created: 0 } },
          past_records_owner_pending: [{ definition_id: 7, seq: 2, date: '2026-09-05' }],
          dependants_suppressed: { count: 1, records: [{ moze_id: 'D-1', parent_moze_id: 'P-1', type: 12 }] },
          review: reasons.map(reason => ({ moze_id: 'X', reason })),
        },
      },
    });
    expect(view.schedules?.ownerPendingLines).toEqual([{ definitionId: 7, seq: 2, date: '2026-09-05' }]);
    expect(view.schedules?.dependantsSuppressed).toBe(1);
    expect(view.schedules?.dependantLines).toEqual([{ mozeId: 'D-1', parentMozeId: 'P-1', type: 12 }]);
    const labels = view.schedules!.reviewLines.map(line => line.label);
    expect(labels).toContain('同日已有期別');
    expect(labels).toContain('沒有期別記錄');
    expect(labels).toContain('找不到貸款');
    expect(labels.every(label => !reasons.includes(label))).toBe(true);
  });
});
