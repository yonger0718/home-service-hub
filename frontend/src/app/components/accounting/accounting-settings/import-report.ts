export interface ChangedAccount {
  name: string;
  currency: string;
  previous: string;
  current: string;
}

export interface ReportView {
  status: string | null;
  kindCounts: [string, number][];
  skippedFuture: [string, number][];
  compared: { compared: number; total: number } | null;
  changedAccounts: ChangedAccount[];
  settingsSkipped: string[];
  needsReview: number;
  reviewReasons: [string, number][];
  fxOutliers: number;
  schedules: ScheduleReportView | null;
}

type Loose = Record<string, unknown>;

function record(value: unknown): Loose {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? (value as Loose) : {};
}

function counts(value: unknown): [string, number][] {
  return Object.entries(record(value)).map(([key, count]) => [key, Number(count) || 0]);
}

/** MOZE AHRecord.type -> entry kind (Task 7's RECORD_TYPE_TO_KIND); `skipped_future` is keyed by these codes. */
const MOZE_TYPE_KINDS: Record<string, string> = {
  '0': 'expense', '1': 'income', '2': 'transfer_out', '3': 'receivable', '4': 'payable', '5': 'receivable',
  '6': 'payable', '7': 'balance_adjustment', '12': 'fee', '13': 'discount', '14': 'reward', '15': 'interest', '16': 'fee',
};

/** Counts keyed by MOZE type code, merged per entry kind in first-seen order; unknown keys pass through. */
function kindCounts(value: unknown): [string, number][] {
  const merged = new Map<string, number>();
  for (const [key, count] of counts(value)) {
    const kind = MOZE_TYPE_KINDS[key] ?? key;
    merged.set(kind, (merged.get(kind) ?? 0) + count);
  }
  return [...merged.entries()];
}

/** Chinese labels for the importer's needs-review reason keys; unknown keys are shown as sent. */
const REVIEW_REASON_LABELS: Record<string, string> = {
  unpaired_transfer: '轉帳未配對',
  fx_backup_rate_missing: '缺少備份匯率',
  reward_source_from_package: '回饋來源推定',
  cross_currency_settlement: '跨幣別結清',
  settlement_overflow: '結清金額超過原款',
  settlement_original_missing: '找不到結清對應款項',
};

function reviewReasons(value: unknown): [string, number][] {
  const pairs = Array.isArray(value) ? value.map(reason => [String(reason), 1] as [string, number]) : counts(value);
  return pairs.map(([key, count]) => [REVIEW_REASON_LABELS[key] ?? key, count]);
}

function names(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.map(item => (typeof item === 'string' ? item : String(record(item)['name'] ?? ''))).filter(Boolean);
  }
  return Object.keys(record(value));
}

export interface ScheduleReportView {
  recurring: number;
  installment: number;
  single: number;
  recordsMapped: number;
  rewardsIgnored: number;
  alreadyPosted: number;
  alreadySkipped: number;
  amountDiffers: number;
  review: number;
  /** R-F1: owner-edited pending periods whose MOZE record turned past. */
  ownerPending: number;
  /** R-A3: pending periods (owner-kept amounts) with a line MOZE disagrees with. */
  pendingAmountDiffers: number;
  pastAmountLines: ScheduleAmountLineView[];
  pendingAmountLines: ScheduleAmountLineView[];
  /** past + pending lines in one list, for display. */
  amountLines: ScheduleAmountLineView[];
  ownerPendingLines: { definitionId: number; seq: number; date: string }[];
  dependantsSuppressed: number;
  dependantLines: { mozeId: string; parentMozeId: string; type: number }[];
  reviewLines: { mozeId: string; label: string }[];
}

export interface ScheduleAmountLineView {
  definitionId: number;
  name: string;
  seq: number;
  date: string;
  kind: string;
  amount: string;
  mozeAmount: string;
}

/** Chinese copy of the schedules block's `review[].reason`; unknown reasons are shown as sent. */
const SCHEDULE_REVIEW_LABELS: Record<string, string> = {
  interval_mismatch: '間隔與記錄不符',
  same_date: '同日已有期別',
  no_records: '沒有期別記錄',
  event_missing: '找不到對應事件',
  seq_conflict: '期別序號衝突',
  account_missing: '找不到帳戶',
  loan_missing: '找不到貸款',
  not_live: '已不在 MOZE 啟用中',
};

function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function amountLines(value: unknown): ScheduleAmountLineView[] {
  return list(value).map(item => {
    const row = record(item);
    return {
      definitionId: Number(row['definition_id']) || 0, name: String(row['name'] ?? ''), seq: Number(row['seq']) || 0, date: String(row['date'] ?? ''),
      kind: String(row['kind'] ?? ''), amount: String(row['amount'] ?? ''), mozeAmount: String(row['moze_amount'] ?? ''),
    };
  });
}

function scheduleView(value: unknown): ScheduleReportView | null {
  const block = record(value);
  if (!('definitions' in block)) {
    return null;
  }
  const created = (kind: string) => Number(record(record(block['definitions'])[kind])['created']) || 0;
  const ownerPending = list(block['past_records_owner_pending']).map(record);
  const dependants = record(block['dependants_suppressed']);
  const pending = amountLines(block['amount_differs']);
  const past = amountLines(record(block['past_records_amount_differs'])['lines']);
  return {
    recurring: created('recurring'),
    installment: created('installment'),
    single: created('single'),
    recordsMapped: Number(block['records_mapped']) || 0,
    rewardsIgnored: Number(block['rewards_ignored']) || 0,
    alreadyPosted: Number(block['past_records_already_posted']) || 0,
    alreadySkipped: Number(block['past_records_already_skipped']) || 0,
    amountDiffers: Number(record(block['past_records_amount_differs'])['count']) || 0,
    review: list(block['review']).length,
    ownerPending: ownerPending.length,
    pendingAmountDiffers: new Set(pending.map(line => `${line.definitionId}#${line.seq}`)).size,
    pastAmountLines: past,
    pendingAmountLines: pending,
    amountLines: [...past, ...pending],
    ownerPendingLines: ownerPending.map(row => ({
      definitionId: Number(row['definition_id']) || 0, seq: Number(row['seq']) || 0, date: String(row['date'] ?? ''),
    })),
    dependantsSuppressed: Number(dependants['count']) || 0,
    dependantLines: list(dependants['records']).map(record).map(row => ({
      mozeId: String(row['moze_id'] ?? ''), parentMozeId: String(row['parent_moze_id'] ?? ''), type: Number(row['type']) || 0,
    })),
    reviewLines: list(block['review']).map(record).map(row => ({
      mozeId: String(row['moze_id'] ?? ''), label: SCHEDULE_REVIEW_LABELS[String(row['reason'])] ?? String(row['reason'] ?? ''),
    })),
  };
}

/** Normalises the backup import report (`import_run` with `summary`, or the bare summary) for display. */
export function summarizeReport(report: unknown): ReportView {
  const root = record(report);
  const summary = 'summary' in root ? record(root['summary']) : root;

  const compared = record(summary['compared_accounts']);
  const accounts = Array.isArray(summary['accounts']) ? (summary['accounts'] as unknown[]).map(record) : [];
  const review = summary['needs_review'];
  const reviewRecord = record(review);
  const outliers = summary['fx_outliers'];

  return {
    status: typeof root['status'] === 'string' ? (root['status'] as string) : null,
    kindCounts: counts(summary['kind_counts']),
    skippedFuture: kindCounts(summary['skipped_future']),
    compared: 'compared' in compared ? { compared: Number(compared['compared']) || 0, total: Number(compared['total']) || 0 } : null,
    changedAccounts: accounts
      .filter(account => account['previous_moze_part'] !== null && account['previous_moze_part'] !== undefined)
      .filter(account => Number(account['previous_moze_part']) !== Number(account['moze_part']))
      .map(account => ({
        name: String(account['name'] ?? ''),
        currency: String(account['currency'] ?? ''),
        previous: String(account['previous_moze_part']),
        current: String(account['moze_part']),
      })),
    settingsSkipped: names(summary['settings_skipped']),
    needsReview: typeof review === 'number' ? review : Number(reviewRecord['count']) || 0,
    reviewReasons: reviewReasons(reviewRecord['reasons']),
    fxOutliers: Array.isArray(outliers) ? outliers.length : Number(outliers) || 0,
    schedules: scheduleView(summary['schedules']),
  };
}
