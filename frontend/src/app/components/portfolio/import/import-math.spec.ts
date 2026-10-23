import { describe, expect, it } from 'vitest';
import { BrokerCsvImportResult, ImportResult, ImportRow } from '../../../models/portfolio.model';
import {
  ImportAttempt,
  ROW_RESULT_LABEL,
  attemptLine,
  classifyCommit,
  confirmCount,
  detectedFormat,
  importCounts,
  rowResult,
  stepFor,
  typeFromQuery,
} from './import-math';

type StatusRow = ImportRow;

/** Status is assigned loosely on purpose: the helpers must tolerate unknown wire values. */
function row(index: number, payload: Record<string, string | null> = {}, status?: string): StatusRow {
  const r: StatusRow = { row_index: index, fingerprint: `fp-${index}`, payload };
  if (status !== undefined) (r as { status?: unknown }).status = status;
  return r;
}

function rows(count: number, start = 1, status?: string): StatusRow[] {
  return Array.from({ length: count }, (_, i) => row(start + i, { name: `股票${start + i}` }, status));
}

function importResult(over: Partial<ImportResult> = {}): ImportResult {
  return {
    parsed: 0,
    created: 0,
    skipped_duplicates: 0,
    dry_run: true,
    errors: [],
    created_ids: [],
    rows: [],
    ...over,
  };
}

function brokerResult(over: Partial<BrokerCsvImportResult> = {}): BrokerCsvImportResult {
  return {
    detected_broker: 'firstrade',
    dry_run: true,
    transactions: [],
    cash_flows: [],
    counts: { created: 0, skipped: 0, rejected: 0 },
    errors: [],
    ...over,
  };
}

/** Generic CSV dry-run as the serializer emits it: would_* stay 0, skipped_duplicates is live. */
const GENERIC_DRY_RUN = importResult({
  parsed: 41,
  would_insert: 0,
  would_skip_duplicate: 0,
  skipped_duplicates: 3,
  errors: [{ row_index: 42, message: '日期格式錯誤' }],
  rows: rows(41),
  csv_format: 'generic',
  dry_run: true,
});

/** Cathay dry-run: would_* are live, skipped_duplicates stays 0. */
const CATHAY_DRY_RUN = importResult({
  parsed: 41,
  would_insert: 38,
  would_skip_duplicate: 3,
  skipped_duplicates: 0,
  errors: [{ row_index: 42, message: '日期格式錯誤' }],
  rows: rows(41),
  csv_format: 'cathay',
  dry_run: true,
});

/** Option B rows: 38 new, 2 duplicate, 1 unresolved; wire counters disagree on purpose. */
const STATUS_DRY_RUN = importResult({
  parsed: 41,
  skipped_duplicates: 9,
  would_insert: 0,
  would_skip_duplicate: 0,
  errors: [{ row_index: 42, message: '日期格式錯誤' }],
  rows: [...rows(38, 1, 'new'), ...rows(2, 39, 'duplicate'), ...rows(1, 41, 'unresolved')],
  csv_format: 'generic',
  dry_run: true,
});

describe('ROW_RESULT_LABEL', () => {
  it('maps every row result to its verbatim copy', () => {
    expect(ROW_RESULT_LABEL).toEqual({
      new: '新增',
      duplicate: '重複 · 略過',
      unresolved: '代號待確認',
      error: '錯誤',
    });
  });
});

describe('importCounts / confirmCount — 交易 / 股利', () => {
  it('generic fixture reads skipped_duplicates and never subtracts errors', () => {
    expect(importCounts('transactions', GENERIC_DRY_RUN, 'generic')).toEqual({ parsed: 41, duplicates: 3, errors: 1 });
    expect(confirmCount('transactions', GENERIC_DRY_RUN, 'generic')).toBe(38);
  });

  it('Cathay dry-run reads would_skip_duplicate and would_insert', () => {
    expect(importCounts('transactions', CATHAY_DRY_RUN, 'cathay')).toEqual({ parsed: 41, duplicates: 3, errors: 1 });
    expect(confirmCount('transactions', CATHAY_DRY_RUN, 'cathay')).toBe(38);
  });

  it('Cathay committed response falls back to skipped_duplicates', () => {
    const committed = importResult({ ...CATHAY_DRY_RUN, dry_run: false, skipped_duplicates: 5, would_skip_duplicate: 3 });
    expect(importCounts('transactions', committed, 'cathay').duplicates).toBe(5);
    expect(confirmCount('transactions', committed, 'cathay')).toBe(36);
  });

  it('works the same for dividends', () => {
    expect(importCounts('dividends', GENERIC_DRY_RUN, 'generic')).toEqual({ parsed: 41, duplicates: 3, errors: 1 });
    expect(confirmCount('dividends', GENERIC_DRY_RUN, 'generic')).toBe(38);
  });

  it('status-bearing rows win over the wire counters', () => {
    expect(importCounts('transactions', STATUS_DRY_RUN, 'generic')).toEqual({ parsed: 41, duplicates: 2, errors: 1 });
    expect(confirmCount('transactions', STATUS_DRY_RUN, 'generic')).toBe(38);
  });

  it('clamps the confirm count at 0', () => {
    const weird = importResult({ parsed: 2, skipped_duplicates: 5, rows: rows(2) });
    expect(confirmCount('transactions', weird, 'generic')).toBe(0);
  });

  it('null result is all zeros', () => {
    expect(importCounts('transactions', null, null)).toEqual({ parsed: 0, duplicates: 0, errors: 0 });
    expect(confirmCount('transactions', null, null)).toBe(0);
    expect(importCounts('broker', null, null)).toEqual({ parsed: 0, duplicates: 0, errors: 0 });
    expect(confirmCount('broker', null, null)).toBe(0);
  });
});

describe('importCounts / confirmCount — 券商對帳單', () => {
  const broker = brokerResult({
    transactions: rows(5, 1),
    cash_flows: rows(2, 6),
    counts: { created: 0, skipped: 1, rejected: 2 },
    errors: [
      { row_index: 3, reason: '數量為零' }, // a rendered table row
      { row_index: 99, reason: '無法解析' }, // parse error, not in the tables
    ],
  });

  it('counts both tables, counts.skipped and all errors', () => {
    expect(importCounts('broker', broker, null)).toEqual({ parsed: 7, duplicates: 1, errors: 2 });
  });

  it('confirm subtracts skipped and only the errors that hit a rendered row', () => {
    expect(confirmCount('broker', broker, null)).toBe(5);
  });

  it('status rows across transactions and cash_flows drive duplicates and confirm', () => {
    const withStatus = brokerResult({
      transactions: [...rows(3, 1, 'new'), ...rows(1, 4, 'duplicate')],
      cash_flows: [...rows(1, 5, 'new'), ...rows(1, 6, 'duplicate')],
      counts: { created: 0, skipped: 9, rejected: 0 },
    });
    expect(importCounts('broker', withStatus, null)).toEqual({ parsed: 6, duplicates: 2, errors: 0 });
    expect(confirmCount('broker', withStatus, null)).toBe(4);
  });

  it('clamps at 0', () => {
    const weird = brokerResult({ transactions: rows(1), counts: { created: 0, skipped: 4, rejected: 0 } });
    expect(confirmCount('broker', weird, null)).toBe(0);
  });
});

describe('rowResult', () => {
  const r = importResult({
    errors: [{ row_index: 2, message: 'bad' }],
    unresolved_names: [{ name: '台積電', occurrences: 1, sample_dates: ['2026-10-01'] }],
  });

  it('status wins over the fallback derivation', () => {
    expect(rowResult(row(2, { name: '台積電' }, 'duplicate'), r)).toBe('duplicate');
    expect(rowResult(row(5, {}, 'error'), r)).toBe('error');
    expect(rowResult(row(2, {}, 'new'), r)).toBe('new');
  });

  it('error row without status → error', () => {
    expect(rowResult(row(2, { name: '台積電' }), r)).toBe('error');
  });

  it('unresolved name without status → unresolved (name / 名稱 / 股票 keys)', () => {
    expect(rowResult(row(3, { name: '台積電' }), r)).toBe('unresolved');
    expect(rowResult(row(3, { 名稱: '台積電' }), r)).toBe('unresolved');
    expect(rowResult(row(3, { 股票: '台積電' }), r)).toBe('unresolved');
  });

  it('otherwise new — never duplicate without a status', () => {
    expect(rowResult(row(4, { name: '聯發科' }), r)).toBe('new');
    expect(rowResult(row(4, { name: '聯發科' }), importResult({ skipped_duplicates: 10 }))).toBe('new');
  });

  it('ignores an unknown status value and falls back', () => {
    expect(rowResult(row(2, {}, 'weird'), r)).toBe('error');
  });

  it('works on broker results (errors carry reason)', () => {
    const b = brokerResult({ errors: [{ row_index: 7, reason: 'x' }] });
    expect(rowResult(row(7), b)).toBe('error');
    expect(rowResult(row(8), b)).toBe('new');
  });
});

describe('detectedFormat', () => {
  it('交易/股利: cathay → 國泰證券, generic → 通用 CSV', () => {
    expect(detectedFormat('transactions', importResult({ csv_format: 'cathay' }), null)).toEqual({ label: '國泰證券', warning: null });
    expect(detectedFormat('dividends', importResult({ csv_format: 'generic' }), null)).toEqual({ label: '通用 CSV', warning: null });
  });

  it('券商: detected_broker upper-cased', () => {
    expect(detectedFormat('broker', brokerResult({ detected_broker: 'firstrade' }), null)).toEqual({ label: 'FIRSTRADE', warning: null });
  });

  it('券商: manual or null → manual with the generic-CSV warning', () => {
    const expected = { label: 'manual', warning: '將以通用手動 CSV 解析' };
    expect(detectedFormat('broker', brokerResult({ detected_broker: 'manual' }), null)).toEqual(expected);
    expect(detectedFormat('broker', brokerResult({ detected_broker: null }), null)).toEqual(expected);
  });

  it('null result → fallback label', () => {
    expect(detectedFormat('transactions', null, '國泰證券')).toEqual({ label: '國泰證券', warning: null });
    expect(detectedFormat('broker', null, 'IB')).toEqual({ label: 'IB', warning: null });
    expect(detectedFormat('transactions', null, null)).toEqual({ label: '通用 CSV', warning: null });
  });
});

describe('stepFor', () => {
  it('1 without a result, 2 with one, 3 while committing or once committed', () => {
    expect(stepFor(false, false, false)).toBe(1);
    expect(stepFor(true, false, false)).toBe(2);
    expect(stepFor(true, true, false)).toBe(3);
    expect(stepFor(true, false, true)).toBe(3);
    expect(stepFor(false, false, true)).toBe(3);
  });
});

describe('typeFromQuery', () => {
  it('maps the four cases', () => {
    expect(typeFromQuery('broker')).toBe('broker');
    expect(typeFromQuery('dividends')).toBe('dividends');
    expect(typeFromQuery('transactions')).toBe('transactions');
    expect(typeFromQuery(null)).toBe('transactions');
    expect(typeFromQuery('nonsense')).toBe('transactions');
  });
});

describe('attemptLine', () => {
  const at = (mo: number, d: number, h: number, mi: number) => new Date(2026, mo - 1, d, h, mi).toISOString();

  it('renders the canvas failure line', () => {
    const a: ImportAttempt = { at: at(10, 21, 22, 10), file: 'a.csv', type: 'transactions', ok: false, created: 0, errors: 1 };
    expect(attemptLine(a)).toBe('10/21 22:10 · 失敗 · 1 列錯誤');
  });

  it('renders the canvas success line', () => {
    const a: ImportAttempt = { at: at(10, 1, 21, 42), file: 'a.csv', type: 'transactions', ok: true, created: 18 };
    expect(attemptLine(a)).toBe('10/01 21:42 · 成功 · 新增 18 筆');
  });

  it('renders the reason form for an HTTP failure', () => {
    const a: ImportAttempt = { at: at(3, 5, 9, 7), file: 'a.csv', type: 'broker', ok: false, reason: '檔案過大' };
    expect(attemptLine(a)).toBe('03/05 09:07 · 失敗 · 檔案過大');
  });
});

describe('classifyCommit', () => {
  const now = new Date(2026, 9, 21, 22, 10);
  const iso = now.toISOString();

  it('HTTP error → ok false with the body detail as reason', () => {
    const err = { status: 413, message: 'Http failure response', error: { detail: '檔案過大' } };
    expect(classifyCommit(null, err, 'a.csv', 'transactions', now)).toEqual({
      at: iso, file: 'a.csv', type: 'transactions', ok: false, created: 0, reason: '檔案過大',
    });
  });

  it('HTTP error reason falls back to body message, then status, then message', () => {
    expect(classifyCommit(null, { status: 500, error: { message: '伺服器錯誤' } }, 'a.csv', 'transactions', now).reason).toBe('伺服器錯誤');
    expect(classifyCommit(null, { status: 502, message: 'Http failure', error: null }, 'a.csv', 'transactions', now).reason).toBe('HTTP 502');
    expect(classifyCommit(null, new Error('網路中斷'), 'a.csv', 'transactions', now).reason).toBe('網路中斷');
    expect(classifyCommit(null, {}, 'a.csv', 'transactions', now).reason).toBe('未知錯誤');
  });

  it('HTTP error with a row-error body counts the errors', () => {
    const err = { status: 422, error: { errors: [{ row_index: 3, reason: 'x' }, { row_index: 4, reason: 'y' }] } };
    const a = classifyCommit(null, err, 'b.csv', 'broker', now);
    expect(a.ok).toBe(false);
    expect(a.errors).toBe(2);
    expect(attemptLine(a)).toBe('10/21 22:10 · 失敗 · 2 列錯誤');
  });

  it('200 with created 0 and errors → ok false, errors n', () => {
    const res = importResult({ dry_run: false, created: 0, errors: [{ row_index: 1, message: 'x' }] });
    expect(classifyCommit(res, null, 'a.csv', 'transactions', now)).toEqual({
      at: iso, file: 'a.csv', type: 'transactions', ok: false, created: 0, errors: 1, reason: null,
    });
  });

  it('200 with created 0 and no errors (all duplicates) → ok true, created 0', () => {
    const res = importResult({ dry_run: false, created: 0, skipped_duplicates: 12 });
    expect(classifyCommit(res, null, 'a.csv', 'dividends', now)).toEqual({
      at: iso, file: 'a.csv', type: 'dividends', ok: true, created: 0, errors: 0, reason: null,
    });
  });

  it('200 with created 18 → ok true even with row errors', () => {
    const res = importResult({ dry_run: false, created: 18, errors: [{ row_index: 1, message: 'x' }] });
    expect(classifyCommit(res, null, 'a.csv', 'transactions', now)).toEqual({
      at: iso, file: 'a.csv', type: 'transactions', ok: true, created: 18, errors: 1, reason: null,
    });
  });

  it('broker reads counts.created', () => {
    const res = brokerResult({ dry_run: false, counts: { created: 6, skipped: 1, rejected: 0 } });
    expect(classifyCommit(res, null, 'f.csv', 'broker', now)).toEqual({
      at: iso, file: 'f.csv', type: 'broker', ok: true, created: 6, errors: 0, reason: null,
    });
    const failed = brokerResult({ dry_run: false, counts: { created: 0, skipped: 0, rejected: 1 }, errors: [{ row_index: 2, reason: 'x' }] });
    expect(classifyCommit(failed, null, 'f.csv', 'broker', now).ok).toBe(false);
  });
});
