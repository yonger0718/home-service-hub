import { describe, expect, it } from 'vitest';
import { BrokerCsvImportResult, ImportResult, ImportRow } from '../../../models/portfolio.model';
import {
  ImportAttempt,
  RECALC_STATE_LABEL,
  RECALC_STEP_LABEL,
  REHASH_HINT,
  ROW_RESULT_LABEL,
  STEP_STATUS_LABEL,
  attemptLine,
  brokerCountSummary,
  cashFlowRow,
  cell,
  classifyCommit,
  confirmCount,
  detectedFormat,
  extractImportErrors,
  failureBanner,
  fixLines,
  importCounts,
  labelOf,
  importErrorDetail,
  importErrorText,
  rowErrorText,
  overrideMeta,
  previewCountSummary,
  previewRow,
  recalcBanner,
  recalcPanelVisible,
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

/** Cathay rows with statuses (Task 7): 36 new, 2 duplicate, 3 rehash; counters agree per the invariant. */
const CATHAY_STATUS_DRY_RUN = importResult({
  parsed: 41,
  would_insert: 36,
  would_skip_duplicate: 2,
  would_rehash: 3,
  skipped_duplicates: 0,
  errors: [],
  rows: [...rows(36, 1, 'new'), ...rows(2, 37, 'duplicate'), ...rows(3, 39, 'rehash')],
  csv_format: 'cathay',
  dry_run: true,
});

describe('ROW_RESULT_LABEL', () => {
  it('maps every row result to its verbatim copy', () => {
    expect(ROW_RESULT_LABEL).toEqual({
      new: '新增',
      duplicate: '重複 · 略過',
      covered: '對帳單已涵蓋',
      rehash: '更新',
      unresolved: '代號待確認',
      error: '錯誤',
    });
  });
});

describe('covered rows', () => {
  const r = importResult({ parsed: 3, rows: [...rows(1, 1, 'new'), ...rows(1, 2, 'covered'), ...rows(1, 3, 'duplicate')], csv_format: 'cathay', dry_run: true });
  it('covered row gets the label and duplicate variant', () => {
    expect(previewRow(r.rows[1], r)).toMatchObject({ result: 'covered', label: '對帳單已涵蓋', variant: 'neutral', dimmed: true, hint: null });
  });
  it('covered rows are not counted as new', () => {
    expect(importCounts('transactions', r, 'cathay')).toEqual({ parsed: 3, duplicates: 1, errors: 0, updated: 0, covered: 1 });
    expect(confirmCount('transactions', r, 'cathay')).toBe(1);
  });
});

describe('importCounts / confirmCount — 交易 / 股利', () => {
  it('generic fixture reads skipped_duplicates and never subtracts errors', () => {
    expect(importCounts('transactions', GENERIC_DRY_RUN, 'generic')).toEqual({ parsed: 41, duplicates: 3, errors: 1, updated: 0, covered: 0 });
    expect(confirmCount('transactions', GENERIC_DRY_RUN, 'generic')).toBe(38);
  });

  it('Cathay dry-run reads would_skip_duplicate and would_insert', () => {
    expect(importCounts('transactions', CATHAY_DRY_RUN, 'cathay')).toEqual({ parsed: 41, duplicates: 3, errors: 1, updated: 0, covered: 0 });
    expect(confirmCount('transactions', CATHAY_DRY_RUN, 'cathay')).toBe(38);
  });

  it('Cathay committed response falls back to skipped_duplicates', () => {
    const committed = importResult({ ...CATHAY_DRY_RUN, dry_run: false, skipped_duplicates: 5, would_skip_duplicate: 3 });
    expect(importCounts('transactions', committed, 'cathay').duplicates).toBe(5);
    expect(confirmCount('transactions', committed, 'cathay')).toBe(36);
  });

  it('works the same for dividends', () => {
    expect(importCounts('dividends', GENERIC_DRY_RUN, 'generic')).toEqual({ parsed: 41, duplicates: 3, errors: 1, updated: 0, covered: 0 });
    expect(confirmCount('dividends', GENERIC_DRY_RUN, 'generic')).toBe(38);
  });

  it('status-bearing rows win over the wire counters', () => {
    expect(importCounts('transactions', STATUS_DRY_RUN, 'generic')).toEqual({ parsed: 41, duplicates: 2, errors: 1, updated: 0, covered: 0 });
    expect(confirmCount('transactions', STATUS_DRY_RUN, 'generic')).toBe(38);
  });

  it('Cathay rehash rows count as 更新, not 重複, and are excluded from 確認匯入 N', () => {
    expect(importCounts('transactions', CATHAY_STATUS_DRY_RUN, 'cathay')).toEqual({ parsed: 41, duplicates: 2, errors: 0, updated: 3, covered: 0 });
    expect(confirmCount('transactions', CATHAY_STATUS_DRY_RUN, 'cathay')).toBe(36);
    // Statuses win even when the counters disagree.
    const disagree = importResult({ ...CATHAY_STATUS_DRY_RUN, would_rehash: 9, would_insert: 0 });
    expect(importCounts('transactions', disagree, 'cathay').updated).toBe(3);
    expect(confirmCount('transactions', disagree, 'cathay')).toBe(36);
  });

  it('without statuses 更新 reads would_rehash on a Cathay dry run and rehashed on a commit', () => {
    const wire = importResult({ ...CATHAY_DRY_RUN, would_insert: 36, would_rehash: 2 });
    expect(importCounts('transactions', wire, 'cathay')).toEqual({ parsed: 41, duplicates: 3, errors: 1, updated: 2, covered: 0 });
    expect(confirmCount('transactions', wire, 'cathay')).toBe(36);
    const committed = importResult({ ...wire, dry_run: false, skipped_duplicates: 3, rehashed: 2 });
    expect(importCounts('transactions', committed, 'cathay').updated).toBe(2);
    expect(confirmCount('transactions', committed, 'cathay')).toBe(36);
    // A generic dry run never reports 更新.
    expect(importCounts('transactions', importResult({ ...GENERIC_DRY_RUN, would_rehash: 4 }), 'generic').updated).toBe(0);
  });

  it('clamps the confirm count at 0', () => {
    const weird = importResult({ parsed: 2, skipped_duplicates: 5, rows: rows(2) });
    expect(confirmCount('transactions', weird, 'generic')).toBe(0);
  });

  it('null result is all zeros', () => {
    expect(importCounts('transactions', null, null)).toEqual({ parsed: 0, duplicates: 0, errors: 0, updated: 0, covered: 0 });
    expect(confirmCount('transactions', null, null)).toBe(0);
    expect(importCounts('broker', null, null)).toEqual({ parsed: 0, duplicates: 0, errors: 0, updated: 0, covered: 0 });
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
    expect(importCounts('broker', broker, null)).toEqual({ parsed: 7, duplicates: 1, errors: 2, updated: 0, covered: 0 });
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
    expect(importCounts('broker', withStatus, null)).toEqual({ parsed: 6, duplicates: 2, errors: 0, updated: 0, covered: 0 });
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
    expect(rowResult(row(2, { name: '台積電' }, 'rehash'), r)).toBe('rehash');
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
    // The fallback never invents `rehash` either, whatever the counters say.
    expect(rowResult(row(4, { name: '聯發科' }), importResult({ csv_format: 'cathay', would_rehash: 10, rehashed: 10 }))).toBe('new');
  });

  it('ignores an unknown status value and falls back', () => {
    expect(rowResult(row(2, {}, 'weird'), r)).toBe('error');
  });

  it('applies unresolved_names on a broker result too', () => {
    const b = { ...brokerResult(), unresolved_names: [{ name: 'Apple Inc', occurrences: 1, sample_dates: [] }] } as BrokerCsvImportResult;
    expect(rowResult(row(2, { name: 'Apple Inc' }), b)).toBe('unresolved');
    expect(rowResult(row(3, { name: 'Microsoft' }), b)).toBe('new');
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

  it('a FastAPI 422 detail array is request validation, not rows: no errors count, the line shows the joined msgs', () => {
    const err = { status: 422, error: { detail: [{ loc: ['body', 'file'], msg: 'field required', type: 'missing' }] } };
    const a = classifyCommit(null, err, 'c.csv', 'transactions', now);
    expect(a).toEqual({ at: iso, file: 'c.csv', type: 'transactions', ok: false, created: 0, reason: 'field required' });
    expect(attemptLine(a)).toBe('10/21 22:10 · 失敗 · field required');
    expect(attemptLine(a)).not.toContain('列錯誤');
  });

  it('a {code,message} body: error.message is the reason, and wins over detail', () => {
    expect(classifyCommit(null, { status: 400, error: { code: 'csv_encoding', message: 'CSV 編碼不是 UTF-8' } }, 'a.csv', 'transactions', now).reason)
      .toBe('CSV 編碼不是 UTF-8');
    expect(classifyCommit(null, { status: 400, error: { code: 'x', message: '訊息', detail: '細節' } }, 'a.csv', 'transactions', now).reason)
      .toBe('訊息');
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

describe('extractImportErrors / importErrorDetail', () => {
  it('reads {errors:[{row_index, reason}]} bodies', () => {
    const err = { status: 422, error: { errors: [{ row_index: 4, reason: 'bad date' }] } };
    expect(extractImportErrors(err)).toEqual([{ row_index: 4, reason: 'bad date' }]);
    expect(importErrorDetail(err)).toBe('第 4 列：bad date');
  });

  it('a pydantic 422 detail array is not rows: no row errors, its msgs are the reason without a row prefix', () => {
    const err = { status: 422, error: { detail: [{ loc: ['body', 7], msg: 'Field required' }, { loc: ['body', 'file'], msg: 'missing' }] } };
    expect(extractImportErrors(err)).toEqual([]);
    expect(importErrorDetail(err)).toBe('Field required；missing');
  });

  it('a whole-batch error (row_index 0 / null) renders the reason alone; NaN never leaks', () => {
    const err = { status: 422, error: { errors: [{ row_index: 0, reason: 'IntegrityError' }, { row_index: null, message: 'x' }, { row_index: 'n', reason: 'y' }] } };
    expect(extractImportErrors(err)).toEqual([{ row_index: 0, reason: 'IntegrityError' }, { row_index: 0, reason: 'x' }, { row_index: 0, reason: 'y' }]);
    expect(importErrorDetail(err)).toBe('IntegrityError；x；y');
    expect(rowErrorText({ row_index: 5, reason: 'bad' })).toBe('第 5 列：bad');
    expect(rowErrorText({ row_index: 0, reason: 'bad' })).toBe('bad');
  });

  it('falls back to the body message, string detail, the request message, then 未知錯誤', () => {
    expect(extractImportErrors({ error: { detail: 'boom' } })).toEqual([]);
    expect(importErrorDetail({ error: { detail: 'boom' } })).toBe('boom');
    expect(importErrorDetail({ message: 'Http failure' })).toBe('Http failure');
    expect(importErrorDetail({ error: { code: 'x', message: 'm' } })).toBe('m');
    expect(importErrorDetail({ message: 'Http failure', error: { code: 409, message: 'recalc in progress', trace_id: null, detail: 'd' } }))
      .toBe('recalc in progress');
    expect(importErrorDetail(null)).toBe('未知錯誤');
  });
});

describe('brokerCountSummary', () => {
  it('labels dry-run and commit counts', () => {
    const counts = { created: 3, skipped: 1, rejected: 2 };
    expect(brokerCountSummary(brokerResult({ dry_run: false, counts }))).toBe('已新增 3；重複 1；錯誤 2');
  });

  it('a dry run reads the row statuses, not the wire counts', () => {
    const r = brokerResult({
      dry_run: true,
      counts: { created: 0, skipped: 0, rejected: 0 },
      transactions: [row(2, {}, 'new'), row(3, {}, 'new'), row(4, {}, 'duplicate'), row(5, {}, 'rehash')],
      cash_flows: [row(6, {}, 'new'), row(7, {}, 'error')],
      errors: [{ row_index: 7, reason: 'bad' }],
    });
    expect(brokerCountSummary(r)).toBe('可新增 3；重複 1；更新 1；錯誤 1');
  });
});

describe('previewCountSummary', () => {
  it('a Cathay dry run reads would_insert / would_skip_duplicate, not created / skipped_duplicates', () => {
    expect(previewCountSummary('transactions', CATHAY_DRY_RUN, 'cathay')).toBe('可新增 38；重複 3；錯誤 1');
  });

  it('shows 更新 only when > 0', () => {
    const r = importResult({ parsed: 6, csv_format: 'cathay', would_insert: 3, would_skip_duplicate: 1, would_rehash: 2 });
    expect(previewCountSummary('transactions', r, 'cathay')).toBe('可新增 3；重複 1；更新 2；錯誤 0');
  });

  it('shows 對帳單已涵蓋 only when > 0', () => {
    const r = importResult({ parsed: 3, rows: [...rows(1, 1, 'new'), ...rows(1, 2, 'covered'), ...rows(1, 3, 'duplicate')], csv_format: 'cathay', dry_run: true });
    expect(previewCountSummary('transactions', r, 'cathay')).toBe('可新增 1；重複 1；對帳單已涵蓋 1；錯誤 0');
  });
});

describe('cell', () => {
  it('reads the nested payload first, then the flat row, skipping empty values', () => {
    expect(cell({ payload: { symbol: 'MSFT' }, symbol: 'AAPL' }, ['symbol'])).toBe('MSFT');
    expect(cell({ symbol: 'AAPL' }, ['symbol'])).toBe('AAPL');
    expect(cell({ payload: { a: '', b: null, c: 0 } }, ['a', 'b', 'c'])).toBe('0');
    expect(cell({ payload: {} }, ['x'])).toBeNull();
    expect(cell(null, ['x'])).toBeNull();
  });
});

describe('previewRow', () => {
  it('maps a 交易 row with name, symbol, side and result', () => {
    const r = importResult({ unresolved_names: [{ name: '台灣50正2', occurrences: 1, sample_dates: [] }] });
    const v = previewRow(row(2, { trade_date: '2026-10-21', name: '台灣50正2', type: 'BUY', quantity: '1000', price: '285.30' }), r);
    expect(v).toMatchObject({
      date: '2026-10-21', name: '台灣50正2', symbol: null, side: 'buy', sideLabel: '買進',
      quantity: '1000', price: '285.30', result: 'unresolved', label: '代號待確認', variant: 'warning', dimmed: false,
    });
  });

  it('falls back to the symbol as the name and reads 賣 / SELL as a sell', () => {
    const v = previewRow(row(3, { symbol: '2330', side: '賣出' }), importResult());
    expect(v).toMatchObject({ name: '2330', symbol: null, side: 'sell', sideLabel: '賣出', date: '-', quantity: '-' });
    expect(previewRow(row(3, { name: '台積電', symbol: '2330', type: 'SELL' }), importResult())).toMatchObject({ name: '台積電', symbol: '2330', side: 'sell' });
  });

  it('dims only duplicates; errors are danger, new and duplicate are neutral', () => {
    const r = importResult({ errors: [{ row_index: 5, message: 'x' }] });
    expect(previewRow(row(5), r)).toMatchObject({ result: 'error', variant: 'danger', dimmed: false });
    expect(previewRow(row(6), r)).toMatchObject({ result: 'new', label: '新增', variant: 'neutral', dimmed: false });
    expect(previewRow(row(7, {}, 'duplicate'), r)).toMatchObject({ label: '重複 · 略過', variant: 'neutral', dimmed: true, hint: null });
  });

  it('a rehash row is a neutral 更新 tag with the in-place hint and is not dimmed', () => {
    expect(previewRow(row(8, {}, 'rehash'), importResult())).toMatchObject({
      result: 'rehash', label: '更新', variant: 'neutral', dimmed: false, hint: REHASH_HINT,
    });
    expect(REHASH_HINT).toBe('既有交易原地更新：指紋、多空、當沖標記與現金腳');
    expect(previewRow(row(9, {}, 'new'), importResult()).hint).toBeNull();
  });

  it('carries the 股利 amount and 入帳日', () => {
    const v = previewRow(row(2, { ex_dividend_date: '2026-09-04', symbol: '2330', amount: '25', received_date: '2026-10-09' }), importResult());
    expect(v).toMatchObject({ date: '2026-09-04', amount: '25', received: '2026-10-09' });
  });

  it('reads broker rows (nested payload, flat legacy shape)', () => {
    const b = brokerResult({ errors: [{ row_index: 9, reason: 'bad' }] });
    expect(previewRow({ row_index: 2, payload: { symbol: 'MSFT', quantity: '2', price: '321.50', type: 'BUY', trade_date: '2026-06-05' } }, b))
      .toMatchObject({ date: '2026-06-05', name: 'MSFT', quantity: '2', price: '321.50', side: 'buy', result: 'new' });
    expect(previewRow({ row_index: 9, symbol: 'AAPL', type: 'SELL', 交易量: '3' }, b)).toMatchObject({ name: 'AAPL', side: 'sell', quantity: '3', result: 'error' });
  });
});

describe('cashFlowRow', () => {
  it('reads date / type / amount / currency from the payload and dims duplicates', () => {
    const b = brokerResult();
    expect(cashFlowRow({ row_index: 3, payload: { date: '2026-06-05', cash_flow_type: 'deposit', amount: '2500.00', currency: 'USD' } }, b))
      .toEqual({ rowIndex: 3, date: '2026-06-05', kind: 'deposit', amount: '2500.00', currency: 'USD', dimmed: false });
    expect(cashFlowRow({ row_index: 4, status: 'duplicate', type: 'fee' }, b)).toMatchObject({ kind: 'fee', amount: '-', dimmed: true });
  });
});

describe('fixLines', () => {
  it('prefers HTTP-body row errors, else the 200 body errors (message or reason)', () => {
    const r = importResult({ errors: [{ row_index: 17, message: '價格欄位為空' }] });
    expect(fixLines(r, [])).toEqual([{ row_index: 17, reason: '價格欄位為空' }]);
    expect(fixLines(r, [{ row_index: 3, reason: 'bad' }])).toEqual([{ row_index: 3, reason: 'bad' }]);
    expect(fixLines(brokerResult({ errors: [{ row_index: 3, reason: 'missing FX rate' }] }), [])).toEqual([{ row_index: 3, reason: 'missing FX rate' }]);
    expect(fixLines(null, [])).toEqual([]);
  });

  it('a whole-batch 200-body error (Cathay IntegrityError, row_index 0 or null) keeps row_index 0', () => {
    const r = importResult({ errors: [{ row_index: 0, message: 'IntegrityError: duplicate key' }, { row_index: null as unknown as number, message: 'ValueError' }] });
    expect(fixLines(r, [])).toEqual([{ row_index: 0, reason: 'IntegrityError: duplicate key' }, { row_index: 0, reason: 'ValueError' }]);
  });
});

describe('overrideMeta', () => {
  it('joins occurrences and sample dates', () => {
    expect(overrideMeta({ name: 'x', occurrences: 3, sample_dates: ['10/21', '10/14', '09/30'] })).toBe('3 筆 · 10/21、10/14、09/30');
    expect(overrideMeta({ name: 'x', occurrences: 2, sample_dates: [] })).toBe('2 筆');
  });
});

describe('importErrorText', () => {
  it('returns the string detail only when the body has no row errors', () => {
    expect(importErrorText({ status: 400, error: { detail: 'CSV 編碼不是 UTF-8' } })).toBe('CSV 編碼不是 UTF-8');
    expect(importErrorText({ status: 422, error: { errors: [{ row_index: 3, reason: 'bad' }], detail: 'x' } })).toBeNull();
    expect(importErrorText({ status: 422, error: { detail: [{ loc: ['body', 2], msg: 'bad' }] } })).toBe('bad');
    expect(importErrorText({ status: 500, error: { detail: '  ' } })).toBeNull();
    expect(importErrorText({ status: 0, message: 'offline' })).toBeNull();
    expect(importErrorText(null)).toBeNull();
  });

  it('reads the shared {code, message, trace_id} body: message first, then detail', () => {
    expect(importErrorText({ status: 400, error: { code: 400, message: 'missing column trade_date', trace_id: null } })).toBe('missing column trade_date');
    expect(importErrorText({ status: 400, error: { code: 400, message: 'm', detail: 'd', trace_id: 't' } })).toBe('m');
    expect(importErrorText({ status: 400, error: { code: 400, message: '', detail: 'd' } })).toBe('d');
    expect(importErrorText({ status: 422, error: { code: 422, message: 'm', errors: [{ row_index: 3, reason: 'bad' }] } })).toBeNull();
  });
});

describe('failureBanner', () => {
  const at = new Date(2026, 9, 21, 22, 10).toISOString();

  it('row errors: {file} 有 {n} 列錯誤 with the nothing-written sentence', () => {
    const a: ImportAttempt = { at, file: 'a.csv', type: 'transactions', ok: false, created: 0, errors: 1 };
    expect(failureBanner(a)).toEqual({
      lead: '最近一次匯入失敗',
      body: '（10/21 22:10）：a.csv 有 1 列錯誤，整批未寫入。修正後重新上傳即可。',
    });
  });

  it('HTTP failure: {file}，{reason}', () => {
    const a: ImportAttempt = { at, file: 'a.csv', type: 'broker', ok: false, created: 0, reason: 'HTTP 500' };
    expect(failureBanner(a)?.body).toBe('（10/21 22:10）：a.csv，HTTP 500，整批未寫入。修正後重新上傳即可。');
  });

  it('no banner for a success, a partial write or no attempt', () => {
    expect(failureBanner(undefined)).toBeNull();
    expect(failureBanner({ at, file: 'a.csv', type: 'transactions', ok: true, created: 0, errors: 0 })).toBeNull();
    expect(failureBanner({ at, file: 'a.csv', type: 'transactions', ok: true, created: 18, errors: 2 })).toBeNull();
    expect(failureBanner({ at, file: 'a.csv', type: 'transactions', ok: false, created: 3, errors: 2 })).toBeNull();
  });
});

describe('recalcBanner', () => {
  it('a failed import recalc names the first step error, without the nothing-written sentence', () => {
    const text = recalcBanner({ kind: 'import', state: 'failed', current_step: 'networth_backfill', steps: [
      { name: 'symbol_map_backfill', status: 'ok' }, { name: 'networth_backfill', status: 'failed', error: 'TWSE timeout' },
    ] });
    expect(text).toBe('最近一次匯入的重算失敗：TWSE timeout');
    expect(text).not.toContain('整批未寫入');
    expect(text).not.toContain('沒有寫入任何資料');
  });

  it('falls back to the current_step label, then 未知錯誤', () => {
    expect(recalcBanner({ state: 'failed', current_step: 'networth_backfill', steps: [] })).toBe('最近一次匯入的重算失敗：淨值回補');
    expect(recalcBanner({ state: 'failed', current_step: 'awaiting_lock' })).toBe('最近一次匯入的重算失敗：等待鎖定');
    expect(recalcBanner({ state: 'failed' })).toBe('最近一次匯入的重算失敗：未知錯誤');
  });

  it('an unknown current_step falls back to its raw name', () => {
    expect(recalcBanner({ state: 'failed', current_step: 'future_step' })).toBe('最近一次匯入的重算失敗：future_step');
  });

  it('none for a quote-only run or a non-failed state', () => {
    expect(recalcBanner({ kind: 'quotes', state: 'failed' })).toBeNull();
    expect(recalcBanner({ kind: 'import', state: 'partial' })).toBeNull();
    expect(recalcBanner({ state: 'idle' })).toBeNull();
  });
});

describe('recalcPanelVisible', () => {
  it('step 3, or a running / failed / partial status, or a quote run', () => {
    expect(recalcPanelVisible({ state: 'idle' }, 3)).toBe(true);
    expect(recalcPanelVisible({ state: 'idle' }, 1)).toBe(false);
    expect(recalcPanelVisible({ state: 'completed', kind: 'import' }, 2)).toBe(false);
    for (const state of ['running', 'failed', 'partial'] as const) {
      expect(recalcPanelVisible({ state }, 1)).toBe(true);
    }
    expect(recalcPanelVisible({ state: 'completed', kind: 'quotes' }, 1)).toBe(true);
  });
});

describe('recalc labels (#111)', () => {
  it('labelOf maps a known raw value', () => {
    expect(labelOf(RECALC_STATE_LABEL, 'running')).toBe('執行中');
    expect(labelOf(RECALC_STEP_LABEL, 'symbol_map_backfill')).toBe('代號對照補齊');
    expect(labelOf(STEP_STATUS_LABEL, 'skipped')).toBe('略過');
  });

  it('labelOf returns an unknown raw value unchanged', () => {
    expect(labelOf(RECALC_STATE_LABEL, 'weird')).toBe('weird');
    expect(labelOf(RECALC_STEP_LABEL, 'future_step')).toBe('future_step');
  });

  it('labelOf returns an empty string for null / undefined', () => {
    expect(labelOf(RECALC_STATE_LABEL, null)).toBe('');
    expect(labelOf(RECALC_STATE_LABEL, undefined)).toBe('');
  });

  it('the maps carry only the real orchestrator names', () => {
    expect(Object.keys(RECALC_STATE_LABEL).sort()).toEqual(['completed', 'failed', 'idle', 'partial', 'running']);
    expect(Object.keys(RECALC_STEP_LABEL).sort()).toEqual(['awaiting_lock', 'dividend_auto_record', 'networth_backfill', 'symbol_map_backfill']);
    expect(Object.keys(STEP_STATUS_LABEL).sort()).toEqual(['failed', 'ok', 'partial', 'skipped']);
  });
});
