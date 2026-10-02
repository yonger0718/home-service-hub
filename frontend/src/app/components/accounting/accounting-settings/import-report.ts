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

function names(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.map(item => (typeof item === 'string' ? item : String(record(item)['name'] ?? ''))).filter(Boolean);
  }
  return Object.keys(record(value));
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
    reviewReasons: Array.isArray(reviewRecord['reasons'])
      ? (reviewRecord['reasons'] as unknown[]).map(reason => [String(reason), 1] as [string, number])
      : counts(reviewRecord['reasons']),
    fxOutliers: Array.isArray(outliers) ? outliers.length : Number(outliers) || 0,
  };
}
