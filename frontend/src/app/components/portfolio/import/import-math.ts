import { BrokerCsvImportResult, ImportResult, ImportRow, RecalcStatus, UnresolvedName } from '../../../models/portfolio.model';
import type { TagVariant } from '../../ui/tag/tag';

export type ImportType = 'transactions' | 'dividends' | 'broker';

/** Per-row preview result; read from `row.status` when the API sends it (Task 1b). */
export type RowResult = 'new' | 'duplicate' | 'rehash' | 'unresolved' | 'error';

export const ROW_RESULT_LABEL: Record<RowResult, string> = {
  new: '新增',
  duplicate: '重複 · 略過',
  rehash: '更新',
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
  /** Cathay rows re-fingerprinted in place (`rehash`); the 更新 tag shows only when > 0. */
  updated: number;
}

type AnyResult = ImportResult | BrokerCsvImportResult;

const ROW_RESULTS: ReadonlySet<string> = new Set<RowResult>(['new', 'duplicate', 'rehash', 'unresolved', 'error']);

/** Title / screen-reader hint on a 更新 row tag. */
export const REHASH_HINT = '既有交易原地更新：指紋、多空、當沖標記與現金腳';

/** Same keys the preview grid's 股票 column reads (`import.html`), plus `stock_name`. */
const NAME_KEYS = ['name', '名稱', '股票', 'stock_name'];

const MANUAL_WARNING = '將以通用手動 CSV 解析';

/** `row.status` when it is one of the five known values; tolerant of models without the field. */
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

/**
 * 解析 / 重複 / 錯誤 (+ 更新) count tags (Decision 4 wire rule). Row statuses win; without them
 * 更新 is `would_rehash` on a Cathay dry run, `rehashed` on a commit, else 0.
 */
export function importCounts(type: ImportType, r: AnyResult | null, csvFormat: string | null): ImportCounts {
  if (!r) return { parsed: 0, duplicates: 0, errors: 0, updated: 0 };
  const list = tableRows(type, r);
  const errors = r.errors?.length ?? 0;
  const withStatus = hasStatuses(list);
  const statusDuplicates = withStatus ? countStatus(list, 'duplicate') : null;
  const statusUpdated = withStatus ? countStatus(list, 'rehash') : null;
  if (type === 'broker') {
    const b = r as BrokerCsvImportResult;
    return { parsed: list.length, duplicates: statusDuplicates ?? b.counts?.skipped ?? 0, errors, updated: statusUpdated ?? 0 };
  }
  const t = r as ImportResult;
  const cathay = cathayDryRun(t, csvFormat);
  const wireDuplicates = cathay ? (t.would_skip_duplicate ?? 0) : (t.skipped_duplicates ?? 0);
  const wireUpdated = cathay ? (t.would_rehash ?? 0) : t.dry_run ? 0 : (t.rehashed ?? 0);
  return { parsed: t.parsed ?? 0, duplicates: statusDuplicates ?? wireDuplicates, errors, updated: statusUpdated ?? wireUpdated };
}

/** N in 確認匯入 N 筆 (Decision 7); never negative, errors never subtracted for 交易/股利; 更新 rows insert nothing. */
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
  const counts = importCounts(type, r, csvFormat);
  return Math.max(0, (t.parsed ?? 0) - counts.duplicates - counts.updated);
}

function rowName(row: ImportRow): string | null {
  const payload = row.payload ?? {};
  for (const key of NAME_KEYS) {
    const value = payload[key];
    if (value !== undefined && value !== null && value !== '') return value;
  }
  return null;
}

/** Row status first; otherwise error → unresolved → new. Never invents `duplicate` or `rehash`. */
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

/**
 * Banner (a) (Decision 9): only for a failed attempt that wrote nothing (`created 0`), so the
 * 整批未寫入 sentence is always true. Row errors → `{file} 有 {n} 列錯誤`; else `{file}，{reason}`.
 */
export function failureBanner(a: ImportAttempt | undefined): { lead: string; body: string } | null {
  if (!a || a.ok || (a.created ?? 0) > 0) return null;
  const what = (a.errors ?? 0) > 0 ? `${a.file} 有 ${a.errors} 列錯誤` : `${a.file}，${a.reason || '未知錯誤'}`;
  return { lead: '最近一次匯入失敗', body: `（${stamp(a.at)}）：${what}，整批未寫入。修正後重新上傳即可。` };
}

/** Banner (b): a failed import recalc (rows may have been written, so no nothing-written sentence). */
export function recalcBanner(status: RecalcStatus): string | null {
  if (status.kind === 'quotes' || status.state !== 'failed') return null;
  const reason = (status.steps ?? []).find(s => s.error)?.error || status.current_step || '未知錯誤';
  return `最近一次匯入的重算失敗：${reason}`;
}

/** The recalc panel (Decision 12): step 3, a running / failed / partial status, or a quote run. */
export function recalcPanelVisible(status: RecalcStatus, step: 1 | 2 | 3): boolean {
  return step === 3 || status.kind === 'quotes' || status.state === 'running' || status.state === 'failed' || status.state === 'partial';
}

function nonEmptyString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null;
}

/** CSV rows in error: only an `errors[]` body counts; a FastAPI 422 `detail` array is request validation, not rows. */
function httpErrorRows(body: unknown): number {
  const b = body as { errors?: unknown } | null | undefined;
  return Array.isArray(b?.errors) ? b.errors.length : 0;
}

function httpReason(err: unknown): string {
  const e = err as { status?: unknown; message?: unknown; error?: { detail?: unknown; message?: unknown } | null } | null;
  return (
    nonEmptyString(e?.error?.message) ??
    nonEmptyString(e?.error?.detail) ??
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

export interface ImportRowError {
  row_index: number;
  reason: string;
}

function normalizeRowError(error: unknown): ImportRowError {
  const e = error as { row_index?: unknown; loc?: unknown; reason?: unknown; message?: unknown; msg?: unknown } | null;
  const loc = Array.isArray(e?.loc) ? e.loc[e.loc.length - 1] : undefined;
  const index = Number(e?.row_index ?? loc ?? 0);
  return {
    row_index: Number.isFinite(index) ? index : 0,
    reason: String(e?.reason ?? e?.message ?? e?.msg ?? '未知錯誤'),
  };
}

/** Row errors from an HTTP error body: `{errors:[{row_index, reason}]}` or a validation `detail` array; `[]` otherwise. */
export function extractImportErrors(err: unknown): ImportRowError[] {
  const body = (err as { error?: { errors?: unknown; detail?: unknown } | null } | null)?.error;
  const list = Array.isArray(body?.errors) ? body.errors : Array.isArray(body?.detail) ? body.detail : [];
  return list.map(normalizeRowError);
}

/** The string `detail` of an HTTP error body that carries no row errors (shown under 需要修正的列); null otherwise. */
export function importErrorText(err: unknown): string | null {
  if (extractImportErrors(err).length > 0) return null;
  return nonEmptyString((err as { error?: { detail?: unknown } | null } | null)?.error?.detail);
}

/** Toast detail for a failed upload: the row errors when present, else the body's string detail/message. */
export function importErrorDetail(err: unknown): string {
  const rows = extractImportErrors(err);
  if (rows.length > 0) return rows.map(e => `第 ${e.row_index} 列：${e.reason}`).join('；');
  const e = err as { message?: unknown; error?: { detail?: unknown; message?: unknown } | null } | null;
  return nonEmptyString(e?.error?.detail) ?? nonEmptyString(e?.error?.message) ?? nonEmptyString(e?.message) ?? '未知錯誤';
}

export function brokerCountSummary(r: BrokerCsvImportResult): string {
  return `${r.dry_run ? '可新增' : '已新增'} ${r.counts.created}；重複 ${r.counts.skipped}；錯誤 ${r.counts.rejected}`;
}

/** A cell from a preview row: the nested `payload` first (real API), then the flat row (legacy fixtures); empty → null. */
export function cell(row: unknown, keys: string[]): string | null {
  const r = row as { payload?: Record<string, unknown> | null } & Record<string, unknown> | null | undefined;
  for (const key of keys) {
    const value = r?.payload?.[key] ?? r?.[key];
    if (value !== undefined && value !== null && value !== '') return String(value);
  }
  return null;
}

const RESULT_VARIANT: Record<RowResult, TagVariant> = {
  new: 'neutral',
  duplicate: 'neutral',
  rehash: 'neutral',
  unresolved: 'warning',
  error: 'danger',
};

export interface PreviewRowView {
  rowIndex: number;
  date: string;
  name: string;
  symbol: string | null;
  side: 'buy' | 'sell';
  sideLabel: string;
  quantity: string;
  price: string;
  amount: string;
  received: string;
  result: RowResult;
  label: string;
  variant: TagVariant;
  /** Title / screen-reader hint on the result tag (`rehash` only). */
  hint: string | null;
  /** Only `duplicate` rows are dimmed (a `rehash` row is updated, not skipped). */
  dimmed: boolean;
}

/** One 預覽 grid row (交易 / 股利 / 券商 transactions); cells via `cell`, result via `rowResult`. */
export function previewRow(row: ImportRow | Record<string, unknown>, r: AnyResult): PreviewRowView {
  const name = cell(row, ['name', '名稱', '股票', 'stock_name']);
  const symbol = cell(row, ['symbol', '代號']);
  const sideText = (cell(row, ['type', 'side', '買賣', '類別', '交易類型']) ?? '').toUpperCase();
  const side = sideText.includes('SELL') || sideText.includes('賣') ? 'sell' : 'buy';
  const result = rowResult(row as ImportRow, r);
  return {
    rowIndex: Number((row as { row_index?: unknown }).row_index ?? 0),
    date: cell(row, ['trade_date', 'ex_dividend_date', 'date', '日期']) ?? '-',
    name: name ?? symbol ?? '-',
    symbol: name ? symbol : null,
    side,
    sideLabel: side === 'sell' ? '賣出' : '買進',
    quantity: cell(row, ['quantity', 'qty', '股數', '交易量']) ?? '-',
    price: cell(row, ['price', '價格']) ?? '-',
    amount: cell(row, ['amount', '金額']) ?? '-',
    received: cell(row, ['received_date', '入帳日']) ?? '-',
    result,
    label: ROW_RESULT_LABEL[result],
    variant: RESULT_VARIANT[result],
    hint: result === 'rehash' ? REHASH_HINT : null,
    dimmed: result === 'duplicate',
  };
}

export interface CashFlowRowView {
  rowIndex: number;
  date: string;
  kind: string;
  amount: string;
  currency: string;
  dimmed: boolean;
}

/** One 現金流 row (日期 / 類型 / 金額 / 幣別). */
export function cashFlowRow(row: Record<string, unknown>, r: BrokerCsvImportResult): CashFlowRowView {
  return {
    rowIndex: Number(row['row_index'] ?? 0),
    date: cell(row, ['date', 'trade_date']) ?? '-',
    kind: cell(row, ['cash_flow_type', 'type']) ?? '-',
    amount: cell(row, ['amount']) ?? '-',
    currency: cell(row, ['currency']) ?? '-',
    dimmed: rowResult(row as unknown as ImportRow, r) === 'duplicate',
  };
}

/** 需要修正的列: row errors from an HTTP error body win, else the 200 body's `errors` (`message` or `reason`). */
export function fixLines(r: AnyResult | null, uploadErrors: ImportRowError[]): ImportRowError[] {
  if (uploadErrors.length > 0) return uploadErrors;
  return (r?.errors ?? []).map(e => {
    const err = e as { row_index: number; message?: string; reason?: string };
    return { row_index: err.row_index, reason: err.reason ?? err.message ?? '未知錯誤' };
  });
}

/** `{occurrences} 筆 · {dates joined by 、}`. */
export function overrideMeta(u: UnresolvedName): string {
  const dates = u.sample_dates ?? [];
  return dates.length > 0 ? `${u.occurrences} 筆 · ${dates.join('、')}` : `${u.occurrences} 筆`;
}
