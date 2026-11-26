import { shortDate } from '../../accounting/dates';
import { FuturesPending, FuturesPendingItem, FuturesPendingStatus, FuturesRuns } from '../../../models/portfolio.model';
import { NotifLink, NotifTask } from '../notif-task';
import { CathayRunState, RunJob, RunSchedule, RunStatusView, cathayRunState, runStatusView, runTask, sinceMeta } from './cathay';

/** The futures job's timer (deploy/statements …sinopac-futures.timer, notifier `sinopac_futures_run_time`). */
export const FUTURES_SCHEDULE: RunSchedule = { hour: 6, minute: 45 };
/** The server cuts `reason` to this many characters (sinopac_futures_statement_service PENDING_REASON_CHARS). */
export const FUTURES_REASON_CHARS = 40;

/** The import page's 永豐期貨對帳單 card: the page scrolls it into view and focuses it once (`#sinopac-futures`). */
export const FUTURES_CARD_FRAGMENT = 'sinopac-futures';
const LOOK: NotifLink = { label: '查看', route: '/portfolio/import', fragment: FUTURES_CARD_FRAGMENT };
const FUTURES_JOB: RunJob = {
  schedule: FUTURES_SCHEDULE,
  titles: {
    failed: '永豐期貨對帳單匯入失敗',
    latched: '永豐期貨對帳單匯入已停用',
    stale: '永豐期貨對帳單今天沒有執行',
  },
};
/** The card's line while the job has never reported a run (rollout: the SPA ships before the worker). */
const NOT_ENABLED = '尚未啟用自動匯入';

/** The 國泰 run states, plus 'never': no run reported yet (automatic import not enabled), which raises nothing. */
export type FuturesRunState = CathayRunState | 'never';

const MONTH_KEY = /^(\d{4})-(\d{2})$/;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const SHORT_DATE = /^\d{2}\/\d{2}$/;

/** `YYYY-MM-DD` → `MM/DD`; `YYYY-MM` → `YYYY/MM 月對帳單`. */
export function futuresItemLabel(key: string): string {
  const month = MONTH_KEY.exec(key);
  return month ? `${month[1]}/${month[2]} 月對帳單` : shortDate(key);
}

/**
 * The incident's name in titles, toasts and button names: `永豐期貨對帳單 MM/DD` / `永豐期貨 YYYY/MM 月對帳單`, with
 * ` · 不同版本` on a conflict, so the two incidents of one key never share a name.
 */
export function futuresItemName(key: string, status: FuturesPendingStatus): string {
  const base = MONTH_KEY.test(key) ? `永豐期貨 ${futuresItemLabel(key)}` : `永豐期貨對帳單 ${futuresItemLabel(key)}`;
  return status === 'conflict' ? `${base} · 不同版本` : base;
}

/** One findings code in plain words; null when it is not a whole code (unknown, or cut by the server). */
function codeText(code: string): string | null {
  if (code === 'carry') return '月初餘額接不上';
  const [name, value] = [code.slice(0, code.indexOf(':')), code.slice(code.indexOf(':') + 1)];
  if (!code.includes(':') || value === '') return null;
  if (name === 'prev') {
    if (SHORT_DATE.test(value)) return `前日餘額接不上（${value}）`;
    if (ISO_DATE.test(value)) return `前日餘額接不上（${shortDate(value)}）`;
    return null;
  }
  if (name === 'gap' && /^\d+$/.test(value)) return `缺 ${Number(value)} 份日對帳單`;
  if (name === 'diff' && /^[A-Z]+$/.test(value)) return `與日對帳單不符：${value.split('').join(' ')}`;
  return null;
}

/**
 * Line 2 of a pending item. A conflict: 收到內容不同的同日 / 同月對帳單. Findings: each code in plain words, joined by
 * `；`; a code the app does not know shows as sent. A reason at the server's 40-character cap may end inside a code:
 * that last code shows raw, and the line ends with `…`.
 */
export function futuresReasonText(item: Pick<FuturesPendingItem, 'kind' | 'status' | 'reason'>): string {
  if (item.status === 'conflict') return item.kind === 'month' ? '收到內容不同的同月對帳單' : '收到內容不同的同日對帳單';
  const reason = item.reason ?? '';
  const cut = reason.length >= FUTURES_REASON_CHARS;
  const codes = reason.split(',');
  const tail = cut ? codes.pop() ?? '' : '';
  const parts = codes.filter(code => code !== '').map(code => codeText(code) ?? code);
  if (tail !== '') parts.push(tail);
  if (parts.length === 0) return '對帳單有待確認的差異';
  return `${parts.join('；')}${cut ? '…' : ''}`;
}

/** `total − items.length` when `truncated` and positive, else 0. */
export function futuresMore(pending: FuturesPending): number {
  return pending.truncated ? Math.max(0, pending.total - pending.items.length) : 0;
}

/**
 * 'never' while no run was ever reported (`latest` and `latest_ok` both null): the job is not enabled yet. Once either
 * exists, latched / failed / stale as 國泰 (same grace), against the 06:45 schedule.
 */
export function futuresRunState(runs: FuturesRuns, now: Date): FuturesRunState {
  if (runs.latest === null && runs.latest_ok === null) return 'never';
  return cathayRunState(runs.latest, now, FUTURES_SCHEDULE);
}

/** Items by key, then status (the server's order, kept whatever it sends). */
function sortedItems(pending: FuturesPending): FuturesPendingItem[] {
  return [...pending.items].sort((a, b) => a.key.localeCompare(b.key) || a.status.localeCompare(b.status));
}

/** One task per pending item (`sinopac_futures:<key>:<status>`), then `sinopac_futures:run` unless the run is ok. */
export function futuresTasks(pending: FuturesPending, runs: FuturesRuns, now: Date): NotifTask[] {
  const tasks: NotifTask[] = sortedItems(pending).map(item => {
    const name = futuresItemName(item.key, item.status);
    return {
      id: `sinopac_futures:${item.key}:${item.status}`,
      source: 'portfolio',
      from: 'sinopac_futures',
      tier: 'system',
      icon: 'pi-file',
      title: name,
      when: futuresReasonText(item),
      meta: sinceMeta(item.since),
      amount: null,
      progress: null,
      primary: { label: '知道了', command: { kind: 'ackFutures', key: item.key, status: item.status, name } },
      secondary: [LOOK],
      sortKey: item.key,
      wake: { key: `${item.status}|${item.reason ?? ''}` },
    };
  });
  const state = futuresRunState(runs, now);
  const run = state === 'never' ? null : runTask(state, runs.latest, now, FUTURES_JOB, { id: 'sinopac_futures:run', from: 'sinopac_futures', look: LOOK });
  if (run) {
    tasks.push(run);
  }
  return tasks;
}

export interface FuturesItemView {
  /** `<key>:<status>`: one row per incident. */
  readonly id: string;
  readonly key: string;
  readonly status: FuturesPendingStatus;
  readonly label: string;
  readonly text: string;
  readonly name: string;
}

export interface FuturesView extends Omit<RunStatusView, 'state'> {
  /** 'never': `runLine` is 尚未啟用自動匯入, and there is no banner and no dot. */
  readonly state: FuturesRunState;
  readonly items: readonly FuturesItemView[];
  readonly more: number;
  /** The 匯入 dot's label for pending items alone (`N 件永豐期貨對帳單待處理`), else null. */
  readonly pendingLabel: string | null;
}

/** The import card's view: the 國泰 card's rules with the futures items and titles. */
export function futuresView(pending: FuturesPending, runs: FuturesRuns, now: Date): FuturesView {
  const state = futuresRunState(runs, now);
  const items = sortedItems(pending).map(item => ({
    id: `${item.key}:${item.status}`,
    key: item.key,
    status: item.status,
    label: futuresItemLabel(item.key),
    text: futuresReasonText(item),
    name: futuresItemName(item.key, item.status),
  }));
  const more = futuresMore(pending);
  const run: Omit<RunStatusView, 'state'> & { readonly state: FuturesRunState } =
    state === 'never'
      ? { state, runLine: NOT_ENABLED, banner: null, runDetails: null, lastOk: null, dotLabel: null }
      : runStatusView(state, runs, now, FUTURES_JOB);
  return {
    ...run,
    items,
    more,
    pendingLabel: items.length > 0 ? `${items.length + more} 件永豐期貨對帳單待處理` : null,
  };
}
