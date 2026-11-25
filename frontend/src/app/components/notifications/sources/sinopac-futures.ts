import { shortDate } from '../../accounting/dates';
import { CathayRun, FuturesPending, FuturesPendingItem, FuturesPendingStatus, FuturesRuns } from '../../../models/portfolio.model';
import { NotifLink, NotifTask } from '../notif-task';
import { CathayRunState, RESULT_TEXT, RunSchedule, cathayRunState, lastScheduledRun, runCodeText, runStamp, sinceMeta } from './cathay';

/** The futures job's timer (deploy/statements …sinopac-futures.timer, notifier `sinopac_futures_run_time`). */
export const FUTURES_SCHEDULE: RunSchedule = { hour: 6, minute: 45 };
/** The server cuts `reason` to this many characters (sinopac_futures_statement_service PENDING_REASON_CHARS). */
export const FUTURES_REASON_CHARS = 40;

/** The import page's 永豐期貨對帳單 card: the page scrolls it into view and focuses it once (`#sinopac-futures`). */
export const FUTURES_CARD_FRAGMENT = 'sinopac-futures';
const LOOK: NotifLink = { label: '查看', route: '/portfolio/import', fragment: FUTURES_CARD_FRAGMENT };
const RUN_TITLE = {
  failed: '永豐期貨對帳單匯入失敗',
  latched: '永豐期貨對帳單匯入已停用',
  stale: '永豐期貨對帳單今天沒有執行',
} as const;
const STALE_WHEN = '06:45 的排程沒有回報';

const MONTH_KEY = /^(\d{4})-(\d{2})$/;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const SHORT_DATE = /^\d{2}\/\d{2}$/;

/** `YYYY-MM-DD` → `MM/DD`; `YYYY-MM` → `YYYY/MM 月對帳單`. */
export function futuresItemLabel(key: string): string {
  const month = MONTH_KEY.exec(key);
  return month ? `${month[1]}/${month[2]} 月對帳單` : shortDate(key);
}

/** The item's name in titles, toasts and button names: `永豐期貨對帳單 MM/DD` / `永豐期貨 YYYY/MM 月對帳單`. */
export function futuresItemName(key: string): string {
  return MONTH_KEY.test(key) ? `永豐期貨 ${futuresItemLabel(key)}` : `永豐期貨對帳單 ${futuresItemLabel(key)}`;
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

/** latched / failed / stale as 國泰 (same grace), against the 06:45 schedule. */
export function futuresRunState(latest: CathayRun | null, now: Date): CathayRunState {
  return cathayRunState(latest, now, FUTURES_SCHEDULE);
}

interface RunText { readonly title: string; readonly when: string; readonly meta: string | null }

function runText(state: CathayRunState, latest: CathayRun | null, now: Date): RunText | null {
  switch (state) {
    case 'failed':
      return { title: RUN_TITLE.failed, when: runStamp(latest!.run_at, now), meta: runCodeText(latest!.code) };
    case 'latched':
      return { title: RUN_TITLE.latched, when: runStamp(latest!.run_at, now), meta: '排程已停用，之後不會自動匯入' };
    case 'stale':
      return { title: RUN_TITLE.stale, when: STALE_WHEN, meta: latest ? `上次執行 ${runStamp(latest.run_at, now)}` : '還沒有執行紀錄' };
    default:
      return null;
  }
}

function runFingerprint(state: CathayRunState, latest: CathayRun | null, now: Date): string {
  return state === 'stale' ? `stale|${lastScheduledRun(now, FUTURES_SCHEDULE).toISOString()}` : `${state}|${latest?.run_at ?? ''}`;
}

/** Items by key, then status (the server's order, kept whatever it sends). */
function sortedItems(pending: FuturesPending): FuturesPendingItem[] {
  return [...pending.items].sort((a, b) => a.key.localeCompare(b.key) || a.status.localeCompare(b.status));
}

/** One task per pending item (`sinopac_futures:<key>:<status>`), then `sinopac_futures:run` unless the run is ok. */
export function futuresTasks(pending: FuturesPending, runs: FuturesRuns, now: Date): NotifTask[] {
  const tasks: NotifTask[] = sortedItems(pending).map(item => {
    const name = futuresItemName(item.key);
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
      fingerprint: `${item.status}|${item.reason ?? ''}`,
    };
  });
  const state = futuresRunState(runs.latest, now);
  const text = runText(state, runs.latest, now);
  if (text) {
    tasks.push({
      id: 'sinopac_futures:run',
      source: 'portfolio',
      from: 'sinopac_futures',
      tier: 'system',
      icon: 'pi-exclamation-triangle',
      title: text.title,
      when: text.when,
      meta: text.meta,
      amount: null,
      progress: null,
      primary: LOOK,
      secondary: [],
      sortKey: '9999-12-31',
      fingerprint: runFingerprint(state, runs.latest, now),
    });
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

export interface FuturesView {
  readonly state: CathayRunState;
  /** ok only. */
  readonly runLine: string | null;
  readonly banner: { readonly tone: 'error' | 'warn'; readonly heading: string; readonly detail: string } | null;
  /** Bad run only: `最近一次執行：…`; null when ok or when there is no run. */
  readonly runDetails: string | null;
  /** `上次成功 …`, bad run only. */
  readonly lastOk: string | null;
  readonly items: readonly FuturesItemView[];
  readonly more: number;
  /** The 匯入 dot's label for a bad run (the run title), else null. */
  readonly dotLabel: string | null;
  /** The 匯入 dot's label for pending items alone (`N 件永豐期貨對帳單待處理`), else null. */
  readonly pendingLabel: string | null;
}

/** The import card's view: the 國泰 card's rules with the futures items and titles. */
export function futuresView(pending: FuturesPending, runs: FuturesRuns, now: Date): FuturesView {
  const { latest, latest_ok } = runs;
  const state = futuresRunState(latest, now);
  const text = runText(state, latest, now);
  const items = sortedItems(pending).map(item => ({
    id: `${item.key}:${item.status}`,
    key: item.key,
    status: item.status,
    label: futuresItemLabel(item.key),
    text: futuresReasonText(item),
    name: futuresItemName(item.key),
  }));
  const more = futuresMore(pending);
  let runLine: string | null = null;
  if (state === 'ok' && latest) {
    const nothing = latest.files_seen === 0 && latest.posted === 0;
    runLine = `${runStamp(latest.run_at, now)} · ${nothing ? '沒有新的對帳單' : `已匯入 ${latest.posted} 份`}`;
    if (latest.files_seen > latest.posted) runLine += ` · 收到 ${latest.files_seen} 份`;
  }
  return {
    state,
    runLine,
    banner: text && { tone: state === 'stale' ? 'warn' : 'error', heading: text.title, detail: text.meta ? `${text.when} · ${text.meta}` : text.when },
    runDetails: text && latest
      ? `最近一次執行：${runStamp(latest.run_at, now)} · ${RESULT_TEXT[latest.result]} · 收到 ${latest.files_seen} 份 · 已匯入 ${latest.posted} 份`
      : null,
    lastOk: text && latest_ok ? `上次成功 ${runStamp(latest_ok.run_at, now)}` : null,
    items,
    more,
    dotLabel: text?.title ?? null,
    pendingLabel: items.length > 0 ? `${items.length + more} 件永豐期貨對帳單待處理` : null,
  };
}
