import { shortDate } from '../../accounting/dates';
import { Dividend, RecalcStatus } from '../../../models/portfolio.model';
import { NavBadge } from '../../shell/navigation';
import { ImportAttempt } from '../../portfolio/import/import-math';
import { NotifLink, NotifTask, NotifTrackingRow } from '../notif-task';
import { localStamp } from './ledger-misc';

export const DIVIDENDS_ROUTE = '/portfolio/dividends';

const DIVIDENDS_LINK: NotifLink = { label: '查看', route: DIVIDENDS_ROUTE, queryParams: { status: 'pending' } };

/** A `payment_date` may carry a time part; the day is its first ten characters. */
const dayOf = (d: Dividend): string | null => (d.payment_date ? d.payment_date.slice(0, 10) : null);

/** Dated rows first (earliest first), undated last; ties by id. */
function byPayment(a: Dividend, b: Dividend): number {
  const da = dayOf(a);
  const db = dayOf(b);
  if (da && db) return da < db ? -1 : da > db ? 1 : a.id - b.id;
  if (da) return -1;
  if (db) return 1;
  return a.id - b.id;
}

/** `receipt_status === 'pending'` rows split by payment date: `due` = date ≤ today, `waiting` = later or no date. */
export function splitPendingDividends(dividends: readonly Dividend[], today: string): { due: Dividend[]; waiting: Dividend[] } {
  const due: Dividend[] = [];
  const waiting: Dividend[] = [];
  for (const d of dividends) {
    if (d.receipt_status !== 'pending') continue;
    const day = dayOf(d);
    (day !== null && day <= today ? due : waiting).push(d);
  }
  return { due, waiting };
}

/** The one 要你確認 task for `due`, or null when empty. */
export function dividendBatch(due: readonly Dividend[]): NotifTask | null {
  if (due.length === 0) return null;
  const sorted = [...due].sort(byPayment);
  const symbols = [...new Set(sorted.map(d => d.symbol))];
  // Up to three symbols; with more, 「等 N 檔」 where N is every distinct symbol.
  const more = symbols.length > 3 ? ` 等 ${symbols.length} 檔` : '';
  const earliest = dayOf(sorted[0]) ?? '';
  return {
    id: 'div:batch',
    source: 'portfolio',
    from: 'dividends',
    tier: 'confirm',
    icon: 'pi-percentage',
    title: `股利待確認收款 · ${due.length} 筆`,
    when: `最早 ${shortDate(earliest)} 發放`,
    meta: symbols.slice(0, 3).join('、') + more,
    amount: null,
    progress: null,
    primary: DIVIDENDS_LINK,
    secondary: [],
    sortKey: earliest,
    wake: { members: due.map(d => d.id).sort((a, b) => a - b).map(String) },
  };
}

/** The 追蹤中 row for `waiting`, or null when empty. */
export function dividendsRow(waiting: readonly Dividend[]): NotifTrackingRow | null {
  if (waiting.length === 0) return null;
  const sorted = [...waiting].sort(byPayment);
  const parts = sorted.slice(0, 3).map(d => {
    const day = dayOf(d);
    return `${d.symbol} ${day ? shortDate(day) : '日期未定'}`;
  });
  // As the batch task: up to three, then 「等 N 筆」 where N is every row.
  if (sorted.length > 3) parts.push(`等 ${sorted.length} 筆`);
  return {
    id: 'dividends',
    source: 'portfolio',
    title: `股利待發放 · ${waiting.length} 筆`,
    meta: parts.join(' · '),
    count: waiting.length,
    link: DIVIDENDS_LINK,
  };
}

/** The 股利 dot for `due`, or null when empty. */
export function dividendsBadge(due: readonly Dividend[]): NavBadge | null {
  return due.length === 0 ? null : { dot: true, tone: 'primary', label: `${due.length} 筆待確認收款` };
}

/** The recalc chain failed (`kind !== 'quotes' && state === 'failed'`: the rule at notification-center.service.ts:240). */
export function recalcFailed(recalc: RecalcStatus | null): boolean {
  return !!recalc && recalc.kind !== 'quotes' && recalc.state === 'failed';
}

/** One 系統 task when the recalc failed or the device's newest attempt has `ok === false`; the recalc wins when both. */
export function importFailureTask(recalc: RecalcStatus | null, lastAttempt: ImportAttempt | undefined): NotifTask | null {
  let key: string;
  let title: string;
  let when: string;
  let meta: string | null;
  if (recalc && recalcFailed(recalc)) {
    key = recalc.started_at ?? 'recalc';
    title = '最近一次匯入的重算失敗';
    when = recalc.started_at ? localStamp(recalc.started_at) : '';
    meta = null;
  } else if (lastAttempt && !lastAttempt.ok) {
    key = lastAttempt.at;
    title = '最近一次匯入失敗';
    when = localStamp(lastAttempt.at);
    meta = lastAttempt.file;
  } else {
    return null;
  }
  return {
    id: `import:fail:${key}`,
    source: 'portfolio',
    from: 'imports',
    tier: 'system',
    icon: 'pi-upload',
    title,
    when,
    meta,
    amount: null,
    progress: null,
    primary: { label: '查看', route: '/portfolio/import' },
    secondary: [],
    sortKey: key,
    wake: { key },
  };
}
