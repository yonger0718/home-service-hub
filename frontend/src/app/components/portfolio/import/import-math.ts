import { BrokerCsvImportResult, ImportResult, ImportRow } from '../../../models/portfolio.model';

export type ImportType = 'transactions' | 'dividends' | 'broker';

/** Per-row preview result; read from `row.status` when the API sends it (Task 1b). */
export type RowResult = 'new' | 'duplicate' | 'unresolved' | 'error';

export const ROW_RESULT_LABEL: Record<RowResult, string> = {
  new: '新增',
  duplicate: '重複 · 略過',
  unresolved: '代號待確認',
  error: '錯誤',
};

export interface ImportAttempt {
  at: string;
  file: string;
  type: ImportType;
  ok: boolean;
  created?: number;
  errors?: number;
  reason?: string | null;
}

export interface ImportCounts {
  parsed: number;
  duplicates: number;
  errors: number;
}

type AnyResult = ImportResult | BrokerCsvImportResult;

const ROW_RESULTS: ReadonlySet<string> = new Set<RowResult>(['new', 'duplicate', 'unresolved', 'error']);

/** Same keys the preview grid's 股票 column reads (`import.html`), plus `stock_name`. */
const NAME_KEYS = ['name', '名稱', '股票', 'stock_name'];

const MANUAL_WARNING = '將以通用手動 CSV 解析';

/** `row.status` when it is one of the four known values; tolerant of models without the field. */
function statusOf(row: unknown): RowResult | null {
  const status = (row as { status?: unknown } | null)?.status;
  return typeof status === 'string' && ROW_RESULTS.has(status) ? (status as RowResult) : null;
}

function tableRows(type: ImportType, r: AnyResult): ImportRow[] {
  if (type === 'broker') {
    const b = r as BrokerCsvImportResult;
    return [...(b.transactions ?? []), ...(b.cash_flows ?? [])];
  }
  return (r as ImportResult).rows ?? [];
}

function countStatus(list: ImportRow[], status: RowResult): number {
  return list.filter(row => statusOf(row) === status).length;
}

function hasStatuses(list: ImportRow[]): boolean {
  return list.some(row => statusOf(row) !== null);
}

/** The serializer's `would_*` numbers are only live on a Cathay dry-run (`imports.py`, `broker_cathay_service.py`). */
function cathayDryRun(r: ImportResult, csvFormat: string | null): boolean {
  return csvFormat === 'cathay' && r.dry_run === true;
}

/** 解析 / 重複 / 錯誤 count tags (Decision 4 wire rule). */
export function importCounts(type: ImportType, r: AnyResult | null, csvFormat: string | null): ImportCounts {
  if (!r) return { parsed: 0, duplicates: 0, errors: 0 };
  const list = tableRows(type, r);
  const errors = r.errors?.length ?? 0;
  const statusDuplicates = hasStatuses(list) ? countStatus(list, 'duplicate') : null;
  if (type === 'broker') {
    const b = r as BrokerCsvImportResult;
    return { parsed: list.length, duplicates: statusDuplicates ?? b.counts?.skipped ?? 0, errors };
  }
  const t = r as ImportResult;
  const wireDuplicates = cathayDryRun(t, csvFormat) ? (t.would_skip_duplicate ?? 0) : (t.skipped_duplicates ?? 0);
  return { parsed: t.parsed ?? 0, duplicates: statusDuplicates ?? wireDuplicates, errors };
}

/** N in 確認匯入 N 筆 (Decision 7); never negative, errors never subtracted for 交易/股利. */
export function confirmCount(type: ImportType, r: AnyResult | null, csvFormat: string | null): number {
  if (!r) return 0;
  const list = tableRows(type, r);
  if (hasStatuses(list)) return countStatus(list, 'new');
  if (type === 'broker') {
    const b = r as BrokerCsvImportResult;
    const rendered = new Set(list.map(row => row.row_index));
    const tableErrors = new Set((b.errors ?? []).map(e => e.row_index).filter(index => rendered.has(index))).size;
    return Math.max(0, list.length - (b.counts?.skipped ?? 0) - tableErrors);
  }
  const t = r as ImportResult;
  if (cathayDryRun(t, csvFormat)) return Math.max(0, t.would_insert ?? 0);
  return Math.max(0, (t.parsed ?? 0) - importCounts(type, r, csvFormat).duplicates);
}

function rowName(row: ImportRow): string | null {
  const payload = row.payload ?? {};
  for (const key of NAME_KEYS) {
    const value = payload[key];
    if (value !== undefined && value !== null && value !== '') return value;
  }
  return null;
}

/** Row status first; otherwise error → unresolved → new. Never invents `duplicate`. */
export function rowResult(row: ImportRow, r: AnyResult): RowResult {
  const status = statusOf(row);
  if (status) return status;
  if ((r.errors ?? []).some(e => e.row_index === row.row_index)) return 'error';
  const unresolved = (r as ImportResult).unresolved_names ?? [];
  const name = rowName(row);
  if (name !== null && unresolved.some(u => u.name === name)) return 'unresolved';
  return 'new';
}

/** 偵測格式 label (+ the manual-CSV warning for an unrecognised broker file). */
export function detectedFormat(
  type: ImportType,
  r: AnyResult | null,
  fallback: string | null,
): { label: string; warning: string | null } {
  if (!r) return { label: fallback ?? '通用 CSV', warning: null };
  if (type === 'broker') {
    const broker = ((r as BrokerCsvImportResult).detected_broker ?? '').trim();
    if (!broker || broker.toLowerCase() === 'manual') return { label: 'manual', warning: MANUAL_WARNING };
    return { label: broker.toUpperCase(), warning: null };
  }
  return { label: (r as ImportResult).csv_format === 'cathay' ? '國泰證券' : '通用 CSV', warning: null };
}

export function stepFor(hasResult: boolean, committing: boolean, committed: boolean): 1 | 2 | 3 {
  if (committing || committed) return 3;
  return hasResult ? 2 : 1;
}

export function typeFromQuery(q: string | null): ImportType {
  return q === 'broker' || q === 'dividends' ? q : 'transactions';
}

function pad(n: number): string {
  return String(n).padStart(2, '0');
}

/** `MM/DD HH:mm` in local time; the raw value when it does not parse. */
function stamp(at: string): string {
  const d = new Date(at);
  if (Number.isNaN(d.getTime())) return at;
  return `${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** `10/01 21:42 · 成功 · 新增 18 筆` / `10/21 22:10 · 失敗 · 1 列錯誤` / `… · 失敗 · {reason}`. */
export function attemptLine(a: ImportAttempt): string {
  const head = `${stamp(a.at)} · ${a.ok ? '成功' : '失敗'}`;
  if (a.ok) return `${head} · 新增 ${a.created ?? 0} 筆`;
  if ((a.errors ?? 0) > 0) return `${head} · ${a.errors} 列錯誤`;
  return `${head} · ${a.reason || '未知錯誤'}`;
}

function nonEmptyString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null;
}

function httpErrorRows(body: unknown): number {
  const b = body as { errors?: unknown; detail?: unknown } | null | undefined;
  if (Array.isArray(b?.errors)) return b.errors.length;
  if (Array.isArray(b?.detail)) return b.detail.length;
  return 0;
}

function httpReason(err: unknown): string {
  const e = err as { status?: unknown; message?: unknown; error?: { detail?: unknown; message?: unknown } | null } | null;
  return (
    nonEmptyString(e?.error?.detail) ??
    nonEmptyString(e?.error?.message) ??
    (typeof e?.status === 'number' && e.status > 0 ? `HTTP ${e.status}` : null) ??
    nonEmptyString(e?.message) ??
    '未知錯誤'
  );
}

/**
 * History entry for a commit outcome (Decision 8): HTTP error → failed with a reason;
 * 200 with nothing created and row errors → failed; 200 with nothing created and no
 * errors (all duplicates) → success; anything created → success, even with row errors.
 */
export function classifyCommit(
  res: AnyResult | null,
  httpError: unknown | null,
  file: string,
  type: ImportType,
  now: Date,
): ImportAttempt {
  const at = now.toISOString();
  if (httpError != null || !res) {
    const rowsInError = httpErrorRows((httpError as { error?: unknown } | null)?.error);
    const attempt: ImportAttempt = { at, file, type, ok: false, created: 0, reason: httpReason(httpError) };
    if (rowsInError > 0) attempt.errors = rowsInError;
    return attempt;
  }
  const created = type === 'broker' ? ((res as BrokerCsvImportResult).counts?.created ?? 0) : ((res as ImportResult).created ?? 0);
  const errors = res.errors?.length ?? 0;
  return { at, file, type, ok: created > 0 || errors === 0, created, errors, reason: null };
}
