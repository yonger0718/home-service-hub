import { shortDate } from '../../accounting/dates';
import { CathayPending, CathayPendingDay, CathayRun, CathayRuns } from '../../../models/portfolio.model';
import { NotifLink, NotifTask } from '../notif-task';

export const CATHAY_RUN_HOUR = 6;
export const CATHAY_RUN_MINUTE = 40;
export const CATHAY_GRACE_MINUTES = 15;

export type CathayRunState = 'ok' | 'failed' | 'latched' | 'stale';

// Asia/Taipei has no DST: a fixed +08:00 offset, read through UTC getters only.
const TAIPEI_MS = 8 * 3600_000;
const DAY_MS = 86_400_000;
const IMPORT_PAGE = '/portfolio/import';
const LOOK: NotifLink = { label: '查看', route: IMPORT_PAGE };
const RUN_TITLE = {
  failed: '國泰對帳單匯入失敗',
  latched: '國泰對帳單匯入已停用',
  stale: '國泰對帳單今天沒有執行',
} as const;

/** `now` shifted so that its UTC fields read as Taipei wall-clock. */
const taipei = (d: Date): Date => new Date(d.getTime() + TAIPEI_MS);
const pad = (n: number): string => String(n).padStart(2, '0');
const taipeiDay = (t: Date): number => Math.floor(t.getTime() / DAY_MS);

/** The most recent 06:40 Asia/Taipei that is at least 15 minutes before `now` (as notify/cathay.py last_scheduled_run). */
export function lastScheduledRun(now: Date): Date {
  const cutoff = now.getTime() - CATHAY_GRACE_MINUTES * 60_000;
  const t = taipei(new Date(cutoff));
  let candidate = Date.UTC(t.getUTCFullYear(), t.getUTCMonth(), t.getUTCDate(), CATHAY_RUN_HOUR, CATHAY_RUN_MINUTE) - TAIPEI_MS;
  if (candidate > cutoff) candidate -= DAY_MS;
  return new Date(candidate);
}

/** latched → 'latched'; failed → 'failed'; null, or run_at before lastScheduledRun(now) → 'stale'; else 'ok'. */
export function cathayRunState(latest: CathayRun | null, now: Date): CathayRunState {
  if (latest?.result === 'latched') return 'latched';
  if (latest?.result === 'failed') return 'failed';
  // `!(a >= b)` rather than `a < b`: an unreadable run_at (NaN) is stale, never ok.
  if (!latest || !(new Date(latest.run_at).getTime() >= lastScheduledRun(now).getTime())) return 'stale';
  return 'ok';
}

/** `今天 06:41` / `昨天 06:41` / `10/26 06:41`, all by the Asia/Taipei calendar. */
export function runStamp(iso: string, now: Date): string {
  const t = taipei(new Date(iso));
  const hm = `${pad(t.getUTCHours())}:${pad(t.getUTCMinutes())}`;
  const diff = taipeiDay(taipei(now)) - taipeiDay(t);
  if (diff === 0) return `今天 ${hm}`;
  if (diff === 1) return `昨天 ${hm}`;
  return `${pad(t.getUTCMonth() + 1)}/${pad(t.getUTCDate())} ${hm}`;
}

/** Line 2 of a pending date. */
export function dayStatusText(day: Pick<CathayPendingDay, 'status' | 'reason'>): string {
  if (day.status === 'mismatch') return '對不上 CSV';
  if (day.status === 'conflict') return '收到不同版本的對帳單';
  const r = day.reason;
  if (!r) return '保留';
  if (r.startsWith('unresolved_symbol:')) return `保留：名稱對不到代號 ${r.slice('unresolved_symbol:'.length)}`;
  if (r === 'unsupported_category') return '保留：含融資融券交易';
  return `保留：${r}`;
}

/** `total − items.length` when `truncated` and positive, else 0. */
export function cathayMore(pending: CathayPending): number {
  return pending.truncated ? Math.max(0, pending.total - pending.items.length) : 0;
}

interface RunText { readonly title: string; readonly when: string; readonly meta: string | null }

/** Title / when / meta of a bad run; null when the state is 'ok'. */
function runText(state: CathayRunState, latest: CathayRun | null, now: Date): RunText | null {
  switch (state) {
    case 'failed':
      return { title: RUN_TITLE.failed, when: runStamp(latest!.run_at, now), meta: latest!.code ? `錯誤代碼 ${latest!.code}` : null };
    case 'latched':
      return { title: RUN_TITLE.latched, when: runStamp(latest!.run_at, now), meta: '排程已停用，之後不會自動匯入' };
    case 'stale':
      return { title: RUN_TITLE.stale, when: '06:40 的排程沒有回報', meta: latest ? `上次執行 ${runStamp(latest.run_at, now)}` : '還沒有執行紀錄' };
    default:
      return null;
  }
}

function runFingerprint(state: CathayRunState, latest: CathayRun | null, now: Date): string {
  return state === 'stale' ? `stale|${lastScheduledRun(now).toISOString()}` : `${state}|${latest?.run_at ?? ''}`;
}

function sinceMeta(since: string | null): string | null {
  if (!since) return null;
  const t = taipei(new Date(since));
  return `自 ${pad(t.getUTCMonth() + 1)}/${pad(t.getUTCDate())} 起`;
}

/** One task per pending date (sorted by trade_date), then `cathay:run` unless the state is 'ok'. */
export function cathayTasks(pending: CathayPending, runs: CathayRuns, now: Date): NotifTask[] {
  const days = [...pending.items].sort((a, b) => a.trade_date.localeCompare(b.trade_date));
  const tasks: NotifTask[] = days.map(d => ({
    id: `cathay:${d.trade_date}`,
    source: 'portfolio',
    from: 'cathay',
    tier: 'system',
    icon: 'pi-file',
    title: `國泰對帳單 ${shortDate(d.trade_date)}`,
    when: dayStatusText(d),
    meta: sinceMeta(d.since),
    amount: null,
    progress: null,
    primary: { label: '確認', command: { kind: 'ackCathay', tradeDate: d.trade_date, name: `國泰對帳單 ${shortDate(d.trade_date)}` } },
    secondary: [LOOK],
    sortKey: d.trade_date,
    fingerprint: `${d.status}|${d.reason ?? ''}`,
  }));
  const state = cathayRunState(runs.latest, now);
  const text = runText(state, runs.latest, now);
  if (text) {
    tasks.push({
      id: 'cathay:run',
      source: 'portfolio',
      from: 'cathay',
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

export interface CathayDayView { readonly tradeDate: string; readonly label: string; readonly text: string }

export interface CathayView {
  readonly state: CathayRunState;
  /** ok only. */
  readonly runLine: string | null;
  readonly banner: { readonly tone: 'error' | 'warn'; readonly heading: string; readonly detail: string } | null;
  /** Bad run only: `最近一次執行：{stamp} · {result} · 收到 N 份 · 已匯入 N 份`; null when ok or when there is no run. */
  readonly runDetails: string | null;
  /** `上次成功 …`, bad run only. */
  readonly lastOk: string | null;
  readonly days: readonly CathayDayView[];
  readonly more: number;
  /** The 匯入 dot's label for a bad run (the run title), else null. */
  readonly dotLabel: string | null;
}

const RESULT_TEXT = { ok: '成功', failed: '失敗', latched: '已停用' } as const;

export function cathayView(pending: CathayPending, runs: CathayRuns, now: Date): CathayView {
  const { latest, latest_ok } = runs;
  const state = cathayRunState(latest, now);
  const text = runText(state, latest, now);
  const days = [...pending.items]
    .sort((a, b) => a.trade_date.localeCompare(b.trade_date))
    .map(d => ({ tradeDate: d.trade_date, label: shortDate(d.trade_date), text: dayStatusText(d) }));
  let runLine: string | null = null;
  if (state === 'ok' && latest) {
    runLine = `${runStamp(latest.run_at, now)} · ${latest.posted === 0 ? '沒有新的對帳單' : `已匯入 ${latest.posted} 份`}`;
    if (latest.files_seen > latest.posted) runLine += ` · 收到 ${latest.files_seen} 份`;
  }
  return {
    state,
    runLine,
    banner: text && { tone: state === 'failed' ? 'error' : 'warn', heading: text.title, detail: text.meta ? `${text.when} · ${text.meta}` : text.when },
    runDetails: text && latest
      ? `最近一次執行：${runStamp(latest.run_at, now)} · ${RESULT_TEXT[latest.result]} · 收到 ${latest.files_seen} 份 · 已匯入 ${latest.posted} 份`
      : null,
    lastOk: text && latest_ok ? `上次成功 ${runStamp(latest_ok.run_at, now)}` : null,
    days,
    more: cathayMore(pending),
    dotLabel: text?.title ?? null,
  };
}
