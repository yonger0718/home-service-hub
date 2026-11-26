import { shortDate } from '../../accounting/dates';
import { CathayPending, CathayPendingDay, CathayRun, CathayRuns } from '../../../models/portfolio.model';
import { NotifLink, NotifSourceKey, NotifTask } from '../notif-task';

export const CATHAY_RUN_HOUR = 6;
export const CATHAY_RUN_MINUTE = 40;
export const CATHAY_GRACE_MINUTES = 15;

export type CathayRunState = 'ok' | 'failed' | 'latched' | 'stale';

/** A daily job's scheduled Asia/Taipei time; its run is due `CATHAY_GRACE_MINUTES` after it. */
export interface RunSchedule { readonly hour: number; readonly minute: number }
const CATHAY_SCHEDULE: RunSchedule = { hour: CATHAY_RUN_HOUR, minute: CATHAY_RUN_MINUTE };

// Asia/Taipei has no DST: a fixed +08:00 offset, read through UTC getters only.
const TAIPEI_MS = 8 * 3600_000;
const DAY_MS = 86_400_000;
const IMPORT_PAGE = '/portfolio/import';
/** The import page's 國泰每日對帳單 card: the page scrolls it into view and focuses it once (`#cathay`). */
export const CATHAY_CARD_FRAGMENT = 'cathay';
const LOOK: NotifLink = { label: '查看', route: IMPORT_PAGE, fragment: CATHAY_CARD_FRAGMENT };
/**
 * A daily statement job as the run rules see it: its schedule and its run-task titles. Shared by the 國泰 and the
 * 永豐期貨 sources (the same latched / failed / stale rules and copy, only the names and the time differ).
 */
export interface RunJob {
  readonly schedule: RunSchedule;
  readonly titles: { readonly failed: string; readonly latched: string; readonly stale: string };
}
const CATHAY_JOB: RunJob = {
  schedule: CATHAY_SCHEDULE,
  titles: {
    failed: '國泰對帳單匯入失敗',
    latched: '國泰對帳單匯入已停用',
    stale: '國泰對帳單今天沒有執行',
  },
};

/** `now` shifted so that its UTC fields read as Taipei wall-clock. */
const taipei = (d: Date): Date => new Date(d.getTime() + TAIPEI_MS);
const pad = (n: number): string => String(n).padStart(2, '0');
const taipeiDay = (t: Date): number => Math.floor(t.getTime() / DAY_MS);

/**
 * The most recent 06:40 Asia/Taipei (or `at`) that is at least 15 minutes before `now` (as notify/cathay.py
 * last_scheduled_run).
 */
export function lastScheduledRun(now: Date, at: RunSchedule = CATHAY_SCHEDULE): Date {
  const cutoff = now.getTime() - CATHAY_GRACE_MINUTES * 60_000;
  const t = taipei(new Date(cutoff));
  let candidate = Date.UTC(t.getUTCFullYear(), t.getUTCMonth(), t.getUTCDate(), at.hour, at.minute) - TAIPEI_MS;
  if (candidate > cutoff) candidate -= DAY_MS;
  return new Date(candidate);
}

/** latched → 'latched'; failed → 'failed'; null, or run_at before lastScheduledRun(now, at) → 'stale'; else 'ok'. */
export function cathayRunState(latest: CathayRun | null, now: Date, at: RunSchedule = CATHAY_SCHEDULE): CathayRunState {
  if (latest?.result === 'latched') return 'latched';
  if (latest?.result === 'failed') return 'failed';
  // `!(a >= b)` rather than `a < b`: an unreadable run_at (NaN) is stale, never ok.
  if (!latest || !(new Date(latest.run_at).getTime() >= lastScheduledRun(now, at).getTime())) return 'stale';
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

/** The worker's run codes in plain words (worker/cathay.py `CathayError`), exact match. */
const RUN_CODE_TEXT: Readonly<Record<string, string>> = {
  password_file: '讀不到對帳單密碼檔',
  state_file: '狀態檔損壞',
  decrypt: '密碼打不開對帳單',
  pdf_malformed: '對帳單 PDF 損壞',
  pdf_too_large: '對帳單 PDF 太大',
  extract: '讀不出對帳單文字',
  'portfolio:body': '投資服務回應異常',
};

function isTemporaryStatus(status: string): boolean {
  return status.startsWith('5') || status === '408' || status === '429';
}

/**
 * A failed run's code for the owner: a known code in plain words, `portfolio:<3 digits>` as the HTTP status the
 * portfolio answered, anything else (parser codes, drive errors, connection errors) as `錯誤代碼 <code>`; null when
 * there is no code. The one mapping for the 通知 task's meta and the card banner.
 */
export function runCodeText(code: string | null): string | null {
  // Defensive: a non-string code (an invalid body) names nothing rather than throwing while a task is computed.
  if (typeof code !== 'string' || !code) return null;
  if (Object.hasOwn(RUN_CODE_TEXT, code)) return RUN_CODE_TEXT[code];
  const status = /^portfolio:(\d{3})$/.exec(code);
  // A failed (not latched) run carries only 5xx / 408 / 429 (worker/cathay.py): temporary, not a refusal.
  if (status) return `${isTemporaryStatus(status[1]) ? '投資服務暫時無法匯入' : '投資服務拒絕匯入'}（${status[1]}）`;
  return `錯誤代碼 ${code}`;
}

export interface RunText { readonly title: string; readonly when: string; readonly meta: string | null }

/** Title / when / meta of a bad run; null when the state is 'ok'. The stale `when` names the job's scheduled time. */
export function runText(state: CathayRunState, latest: CathayRun | null, now: Date, job: RunJob = CATHAY_JOB): RunText | null {
  switch (state) {
    case 'failed':
      return { title: job.titles.failed, when: runStamp(latest!.run_at, now), meta: runCodeText(latest!.code) };
    case 'latched':
      return { title: job.titles.latched, when: runStamp(latest!.run_at, now), meta: '排程已停用，之後不會自動匯入' };
    case 'stale':
      return {
        title: job.titles.stale,
        when: `${pad(job.schedule.hour)}:${pad(job.schedule.minute)} 的排程沒有回報`,
        meta: latest ? `上次執行 ${runStamp(latest.run_at, now)}` : '還沒有執行紀錄',
      };
    default:
      return null;
  }
}

function runFingerprint(state: CathayRunState, latest: CathayRun | null, now: Date, job: RunJob): string {
  return state === 'stale' ? `stale|${lastScheduledRun(now, job.schedule).toISOString()}` : `${state}|${latest?.run_at ?? ''}`;
}

/** The 系統 run task (`id`, from `from`, 查看 → `look`) for a bad run; null when the state is 'ok'. */
export function runTask(
  state: CathayRunState,
  latest: CathayRun | null,
  now: Date,
  job: RunJob,
  task: { readonly id: string; readonly from: NotifSourceKey; readonly look: NotifLink },
): NotifTask | null {
  const text = runText(state, latest, now, job);
  if (!text) return null;
  return {
    id: task.id,
    source: 'portfolio',
    from: task.from,
    tier: 'system',
    icon: 'pi-exclamation-triangle',
    title: text.title,
    when: text.when,
    meta: text.meta,
    amount: null,
    progress: null,
    primary: task.look,
    secondary: [],
    sortKey: '9999-12-31',
    wake: { key: runFingerprint(state, latest, now, job) },
  };
}

const RESULT_TEXT = { ok: '成功', failed: '失敗', latched: '已停用' } as const;

/** The run results a status card can name (RESULT_TEXT's keys): a body with any other result is invalid. */
export const RUN_RESULTS: readonly string[] = Object.keys(RESULT_TEXT);

/** The run part of a status card's view. */
export interface RunStatusView {
  readonly state: CathayRunState;
  /** ok only. */
  readonly runLine: string | null;
  readonly banner: { readonly tone: 'error' | 'warn'; readonly heading: string; readonly detail: string } | null;
  /** Bad run only: `最近一次執行：{stamp} · {result} · 收到 N 份 · 已匯入 N 份`; null when ok or when there is no run. */
  readonly runDetails: string | null;
  /** `上次成功 …`, bad run only. */
  readonly lastOk: string | null;
  /** The 匯入 dot's label for a bad run (the run title), else null. */
  readonly dotLabel: string | null;
}

/** The run part of the card view for `state` (from `cathayRunState(runs.latest, now, job.schedule)`). */
export function runStatusView(state: CathayRunState, runs: CathayRuns, now: Date, job: RunJob = CATHAY_JOB): RunStatusView {
  const { latest, latest_ok } = runs;
  const text = runText(state, latest, now, job);
  let runLine: string | null = null;
  if (state === 'ok' && latest) {
    // 沒有新的對帳單 only when nothing arrived: a statement received but held reads 已匯入 0 份 · 收到 N 份.
    const nothing = latest.files_seen === 0 && latest.posted === 0;
    runLine = `${runStamp(latest.run_at, now)} · ${nothing ? '沒有新的對帳單' : `已匯入 ${latest.posted} 份`}`;
    if (latest.files_seen > latest.posted) runLine += ` · 收到 ${latest.files_seen} 份`;
  }
  return {
    state,
    runLine,
    // A latched job stays off until someone acts on the host: error, as failed; stale (and missing) stays a warning.
    banner: text && { tone: state === 'stale' ? 'warn' : 'error', heading: text.title, detail: text.meta ? `${text.when} · ${text.meta}` : text.when },
    runDetails: text && latest
      ? `最近一次執行：${runStamp(latest.run_at, now)} · ${RESULT_TEXT[latest.result]} · 收到 ${latest.files_seen} 份 · 已匯入 ${latest.posted} 份`
      : null,
    lastOk: text && latest_ok ? `上次成功 ${runStamp(latest_ok.run_at, now)}` : null,
    dotLabel: text?.title ?? null,
  };
}

export function sinceMeta(since: string | null): string | null {
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
    primary: { label: '已處理', command: { kind: 'ackCathay', tradeDate: d.trade_date, name: `國泰對帳單 ${shortDate(d.trade_date)}` } },
    secondary: [LOOK],
    sortKey: d.trade_date,
    wake: { key: `${d.status}|${d.reason ?? ''}` },
  }));
  const run = runTask(cathayRunState(runs.latest, now), runs.latest, now, CATHAY_JOB, { id: 'cathay:run', from: 'cathay', look: LOOK });
  if (run) {
    tasks.push(run);
  }
  return tasks;
}

export interface CathayDayView { readonly tradeDate: string; readonly label: string; readonly text: string }

export interface CathayView extends RunStatusView {
  readonly days: readonly CathayDayView[];
  readonly more: number;
  /** The 匯入 dot's label for pending dates alone (`N 天的國泰對帳單待處理`), else null. */
  readonly pendingLabel: string | null;
}


export function cathayView(pending: CathayPending, runs: CathayRuns, now: Date): CathayView {
  const days = [...pending.items]
    .sort((a, b) => a.trade_date.localeCompare(b.trade_date))
    .map(d => ({ tradeDate: d.trade_date, label: shortDate(d.trade_date), text: dayStatusText(d) }));
  const more = cathayMore(pending);
  return {
    ...runStatusView(cathayRunState(runs.latest, now), runs, now),
    days,
    more,
    // The server's total when it truncated (the listed dates plus the unlisted rest), else the dates listed.
    pendingLabel: days.length > 0 ? `${days.length + more} 天的國泰對帳單待處理` : null,
  };
}
