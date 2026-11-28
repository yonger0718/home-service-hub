import { shortDate } from '../../accounting/dates';
import { FuturesPending, FuturesPendingItem, FuturesPendingStatus, FuturesRuns } from '../../../models/portfolio.model';
import { NotifLink, NotifTask, NotifWake } from '../notif-task';
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

/** A findings reason the app cannot put in plain words (an unknown code): shown once, never as sent. */
const UNKNOWN_REASON = '對帳單有待確認的差異';
/** At most this many reasons are written out; the rest is ` 等 N 項` (N: the reasons in all, as the e-invoice card). */
export const FUTURES_REASONS_SHOWN = 2;
/** `diff` letters against the sums of the month's daily rows (spec §6.4); `M` is against the last daily balance. */
const DIFF_SUM_LABELS: Readonly<Record<string, string>> = { B: '出入金', G: '權利金', H: '手續費與稅', K: '期貨平倉損益' };

/** One whole findings code in plain words: one reason, or two for a `diff` with both summed letters and `M`. */
function codeReasons(code: string): string[] {
  if (code === 'carry') return ['月初餘額接不上'];
  const at = code.indexOf(':');
  const [name, value] = at < 0 ? [code, ''] : [code.slice(0, at), code.slice(at + 1)];
  if (name === 'prev' && SHORT_DATE.test(value)) return [`前日餘額接不上（${value}）`];
  if (name === 'prev' && ISO_DATE.test(value)) return [`前日餘額接不上（${shortDate(value)}）`];
  if (name === 'gap' && /^\d+$/.test(value)) return [`缺 ${Number(value)} 份日對帳單`];
  if (name === 'diff' && /^[A-Z]+$/.test(value)) {
    const letters = value.split('');
    const sums = letters.filter(l => l in DIFF_SUM_LABELS).map(l => DIFF_SUM_LABELS[l]);
    const out: string[] = [];
    if (sums.length > 0) out.push(`${sums.join('、')}與日對帳單加總不符`);
    if (letters.includes('M')) out.push('月底餘額與最後一份日對帳單不符');
    if (letters.some(l => l !== 'M' && !(l in DIFF_SUM_LABELS))) out.push(UNKNOWN_REASON);
    return out;
  }
  return [UNKNOWN_REASON];
}

/**
 * Line 2 of a pending item. A conflict: 收到內容不同的同日 / 同月對帳單. Findings: each reason in plain words (never a
 * code), the first two joined by `；`, then ` 等 N 項` when there are more (N: all of them). A reason at the server's
 * 40-character cap may end inside a code: that code is not shown and counts as one more reason.
 */
export function futuresReasonText(item: Pick<FuturesPendingItem, 'kind' | 'status' | 'reason'>): string {
  if (item.status === 'conflict') return item.kind === 'month' ? '收到內容不同的同月對帳單' : '收到內容不同的同日對帳單';
  const reason = item.reason ?? '';
  const codes = reason.split(',');
  const tail = reason.length >= FUTURES_REASON_CHARS ? codes.pop() ?? '' : '';
  const reasons = [...new Set(codes.filter(code => code !== '').flatMap(codeReasons))];
  const total = reasons.length + (tail !== '' ? 1 : 0);
  if (reasons.length === 0) return UNKNOWN_REASON;
  const shown = reasons.slice(0, FUTURES_REASONS_SHOWN).join('；');
  return total > FUTURES_REASONS_SHOWN || total > reasons.length ? `${shown} 等 ${total} 項` : shown;
}

/**
 * The snooze wake of an incident (owner ruling 2026-11-26, #442). Findings with exactly one whole `gap:<N>` code
 * (缺 N 份日對帳單): `count` N and the reason with that code as `gap` in the key, so a larger N wakes a snoozed task and
 * a smaller one does not; any other change of the reason changes the key and wakes. Otherwise the whole reason is the
 * key. A code the server cut at the 40-character cap is not whole, so a cut `gap:` code compares as text.
 */
export function futuresWake(item: Pick<FuturesPendingItem, 'status' | 'reason'>): NotifWake {
  const reason = item.reason ?? '';
  const codes = reason.split(',');
  const whole = reason.length >= FUTURES_REASON_CHARS ? codes.length - 1 : codes.length;
  const gaps = codes.slice(0, whole).flatMap((code, i) => (/^gap:\d+$/.test(code) ? [i] : []));
  if (item.status !== 'findings' || gaps.length !== 1) {
    return { key: `${item.status}|${reason}` };
  }
  const [at] = gaps;
  const count = Number(codes[at].slice('gap:'.length));
  return { key: `${item.status}|${codes.map((code, i) => (i === at ? 'gap' : code)).join(',')}`, count };
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
      primary: { label: '已處理', command: { kind: 'ackFutures', key: item.key, status: item.status, name } },
      secondary: [LOOK],
      sortKey: item.key,
      wake: futuresWake(item),
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
