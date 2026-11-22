import { CathayPending, CathayRun, CathayRuns } from '../../../models/portfolio.model';
import { cathayMore, cathayRunState, cathayTasks, cathayView, dayStatusText, lastScheduledRun, runStamp } from './cathay';

const at = (s: string) => new Date(s);
const run = (o: Partial<CathayRun> = {}): CathayRun => ({ run_at: '2026-10-28T06:41:00+08:00', result: 'ok', code: null, files_seen: 1, posted: 1, ...o });
const NONE: CathayPending = { items: [], total: 0, truncated: false };
const now = at('2026-10-28T07:00:00+08:00');
const okRuns: CathayRuns = { latest: run(), latest_ok: run() };
const day = (o: Partial<CathayPending['items'][number]> = {}) => ({
  trade_date: '2026-10-28', status: 'held' as const, reason: null, since: null, last_at: null, ...o,
});

describe('cathay run rule', () => {
  it('spec 3.4: at 07:00 with the latest run at yesterday 06:41 the run is stale and a 系統 task says the 06:40 run did not report', () => {
    const n = at('2026-10-28T07:00:00+08:00');
    const runs = { latest: run({ run_at: '2026-10-27T06:41:00+08:00' }), latest_ok: null };
    expect(cathayRunState(runs.latest, n)).toBe('stale');
    expect(cathayTasks(NONE, runs, n)).toEqual([expect.objectContaining({
      id: 'cathay:run', tier: 'system', source: 'portfolio', from: 'cathay', title: '國泰對帳單今天沒有執行',
      when: '06:40 的排程沒有回報', meta: '上次執行 昨天 06:41', primary: { label: '查看', route: '/portfolio/import' },
      fingerprint: 'stale|2026-10-27T22:40:00.000Z' })]);
  });

  it.each([
    ['2026-10-28T06:54:00+08:00', 'ok'],
    ['2026-10-28T06:55:00+08:00', 'stale'],
  ])('latest yesterday 06:41, now %s → %s', (n, state) => {
    expect(cathayRunState(run({ run_at: '2026-10-27T06:41:00+08:00' }), at(n))).toBe(state);
  });

  it('latest today 06:41 ok at 07:00 → ok, no run task', () => {
    expect(cathayRunState(run(), now)).toBe('ok');
    expect(cathayTasks(NONE, okRuns, now)).toEqual([]);
  });

  it('the same instants written in UTC give the same verdicts (Review Focus: device zone)', () => {
    const latest = run({ run_at: '2026-10-26T22:41:00Z' });
    expect(cathayRunState(latest, at('2026-10-27T22:54:00Z'))).toBe('ok');
    expect(cathayRunState(latest, at('2026-10-27T22:55:00Z'))).toBe('stale');
  });

  it('lastScheduledRun: 06:54 → yesterday 06:40; 06:55 → today 06:40; 00:10 → yesterday 06:40', () => {
    expect(lastScheduledRun(at('2026-10-28T06:54:00+08:00')).toISOString()).toBe('2026-10-26T22:40:00.000Z');
    expect(lastScheduledRun(at('2026-10-28T06:55:00+08:00')).toISOString()).toBe('2026-10-27T22:40:00.000Z');
    expect(lastScheduledRun(at('2026-10-28T00:10:00+08:00')).toISOString()).toBe('2026-10-26T22:40:00.000Z');
  });

  it('a run exactly at the scheduled 06:40:00 is ok; one second earlier is stale (run_at < scheduled is strict)', () => {
    expect(cathayRunState(run({ run_at: '2026-10-28T06:40:00+08:00' }), now)).toBe('ok');
    expect(cathayRunState(run({ run_at: '2026-10-28T06:39:59+08:00' }), now)).toBe('stale');
  });

  it('latched yesterday and nothing since stays latched, not stale (notifier precedence)', () => {
    expect(cathayRunState(run({ run_at: '2026-10-27T06:41:00+08:00', result: 'latched' }), now)).toBe('latched');
  });

  it('an unreadable run_at is stale, never ok', () => {
    expect(cathayRunState(run({ run_at: 'not a time' }), now)).toBe('stale');
  });

  it('no run at all is stale with 還沒有執行紀錄', () => {
    expect(cathayRunState(null, now)).toBe('stale');
    expect(cathayTasks(NONE, { latest: null, latest_ok: null }, now)).toEqual([
      expect.objectContaining({ id: 'cathay:run', meta: '還沒有執行紀錄' })]);
  });

  it('failed today: 國泰對帳單匯入失敗, when 今天 06:41, meta 錯誤代碼 password_file', () => {
    const latest = run({ result: 'failed', code: 'password_file' });
    const [t] = cathayTasks(NONE, { latest, latest_ok: null }, now);
    expect(t).toMatchObject({ id: 'cathay:run', title: '國泰對帳單匯入失敗', when: '今天 06:41', meta: '錯誤代碼 password_file',
      icon: 'pi-exclamation-triangle', sortKey: '9999-12-31', fingerprint: `failed|${latest.run_at}`, amount: null, progress: null, secondary: [] });
  });

  it('failed yesterday and nothing since stays failed (notifier precedence), when 昨天 06:41', () => {
    const latest = run({ result: 'failed', code: 'x', run_at: '2026-10-27T06:41:00+08:00' });
    expect(cathayRunState(latest, now)).toBe('failed');
    expect(cathayTasks(NONE, { latest, latest_ok: null }, now)[0]).toMatchObject({ title: '國泰對帳單匯入失敗', when: '昨天 06:41' });
  });

  it('latched: 國泰對帳單匯入已停用 with 排程已停用，之後不會自動匯入', () => {
    const latest = run({ result: 'latched' });
    expect(cathayRunState(latest, now)).toBe('latched');
    expect(cathayTasks(NONE, { latest, latest_ok: null }, now)[0]).toMatchObject({
      title: '國泰對帳單匯入已停用', meta: '排程已停用，之後不會自動匯入', fingerprint: `latched|${latest.run_at}` });
  });

  it('a failed run without a code has no meta', () => {
    expect(cathayTasks(NONE, { latest: run({ result: 'failed' }), latest_ok: null }, now)[0].meta).toBeNull();
  });

  it('runStamp: 今天 / 昨天 / MM/DD HH:mm by the Taipei calendar', () => {
    expect(runStamp('2026-10-28T06:41:00+08:00', now)).toBe('今天 06:41');
    expect(runStamp('2026-10-27T06:41:00+08:00', now)).toBe('昨天 06:41');
    expect(runStamp('2026-10-25T22:41:00Z', now)).toBe('10/26 06:41');
    expect(runStamp('2026-10-27T16:30:00Z', now)).toBe('今天 00:30');
  });
});

describe('cathay pending dates', () => {
  it('a held date: 確認 command, 查看 secondary, text from the reason', () => {
    const [t] = cathayTasks({ items: [{ trade_date: '2026-10-28', status: 'held', reason: 'unresolved_symbol:測試股票', since: '2026-10-29T06:41:00+08:00', last_at: '2026-10-29T06:41:00+08:00' }], total: 1, truncated: false }, okRuns, now);
    expect(t).toMatchObject({ id: 'cathay:2026-10-28', tier: 'system', source: 'portfolio', from: 'cathay', icon: 'pi-file', title: '國泰對帳單 10/28', when: '保留：名稱對不到代號 測試股票', meta: '自 10/29 起',
      primary: { label: '確認', command: { kind: 'ackCathay', tradeDate: '2026-10-28', name: '國泰對帳單 10/28' } },
      secondary: [{ label: '查看', route: '/portfolio/import' }], sortKey: '2026-10-28', fingerprint: 'held|unresolved_symbol:測試股票', amount: null, progress: null });
  });

  it('no since → no meta; since is read by the Taipei calendar', () => {
    expect(cathayTasks({ items: [day()], total: 1, truncated: false }, okRuns, now)[0].meta).toBeNull();
    expect(cathayTasks({ items: [day({ since: '2026-10-28T16:30:00Z' })], total: 1, truncated: false }, okRuns, now)[0].meta).toBe('自 10/29 起');
  });

  it.each([
    [{ status: 'held', reason: null }, '保留'],
    [{ status: 'held', reason: 'unsupported_category' }, '保留：含融資融券交易'],
    [{ status: 'held', reason: '其他原因' }, '保留：其他原因'],
    [{ status: 'mismatch', reason: null }, '對不上 CSV'],
    [{ status: 'conflict', reason: null }, '收到不同版本的對帳單'],
  ] as const)('dayStatusText %o → %s', (d, text) => {
    expect(dayStatusText(d)).toBe(text);
  });

  it('a date whose status changed has a new fingerprint; a newer last_at alone does not', () => {
    const f = (d: ReturnType<typeof day>) => cathayTasks({ items: [d], total: 1, truncated: false }, okRuns, now)[0].fingerprint;
    expect(f(day({ status: 'mismatch' }))).not.toBe(f(day()));
    expect(f(day({ last_at: '2026-10-30T06:41:00+08:00' }))).toBe(f(day({ last_at: '2026-10-29T06:41:00+08:00' })));
  });

  it('dates come oldest first and the run task last', () => {
    const items = [day({ trade_date: '2026-10-28' }), day({ trade_date: '2026-10-26' })];
    const runs = { latest: run({ result: 'failed' }), latest_ok: null };
    expect(cathayTasks({ items, total: 2, truncated: false }, runs, now).map(t => t.id))
      .toEqual(['cathay:2026-10-26', 'cathay:2026-10-28', 'cathay:run']);
  });

  it('cathayMore: total 73 with 50 items → 23; truncated with total 50 and 50 items → 0; not truncated → 0', () => {
    const items = Array.from({ length: 50 }, (_, i) => day({ trade_date: `2026-09-${String((i % 28) + 1).padStart(2, '0')}` }));
    expect(cathayMore({ items, total: 73, truncated: true })).toBe(23);
    expect(cathayMore({ items, total: 50, truncated: true })).toBe(0);
    expect(cathayMore({ items, total: 73, truncated: false })).toBe(0);
    expect(cathayMore({ items, total: 40, truncated: true })).toBe(0);
  });
});

describe('cathayView', () => {
  it('healthy: runLine 今天 06:41 · 已匯入 1 份, no banner, no days', () => {
    expect(cathayView(NONE, okRuns, now)).toEqual({ state: 'ok', runLine: '今天 06:41 · 已匯入 1 份', banner: null,
      runDetails: null, lastOk: null, days: [], more: 0, dotLabel: null });
  });

  it('posted 0 → 沒有新的對帳單; files_seen 3, posted 1 → … · 已匯入 1 份 · 收到 3 份', () => {
    expect(cathayView(NONE, { latest: run({ files_seen: 0, posted: 0 }), latest_ok: null }, now).runLine).toBe('今天 06:41 · 沒有新的對帳單');
    expect(cathayView(NONE, { latest: run({ files_seen: 3, posted: 1 }), latest_ok: null }, now).runLine).toBe('今天 06:41 · 已匯入 1 份 · 收到 3 份');
  });

  it('failed: error banner naming password_file, lastOk 上次成功 昨天 06:41, dotLabel 國泰對帳單匯入失敗', () => {
    const v = cathayView(NONE, { latest: run({ result: 'failed', code: 'password_file' }), latest_ok: run({ run_at: '2026-10-27T06:41:00+08:00' }) }, now);
    expect(v).toMatchObject({ state: 'failed', runLine: null, lastOk: '上次成功 昨天 06:41', dotLabel: '國泰對帳單匯入失敗',
      banner: { tone: 'error', heading: '國泰對帳單匯入失敗', detail: '今天 06:41 · 錯誤代碼 password_file' } });
  });

  it('latched / stale: warn banner; ok: dotLabel null', () => {
    const latched = cathayView(NONE, { latest: run({ result: 'latched' }), latest_ok: null }, now);
    expect(latched.banner).toEqual({ tone: 'warn', heading: '國泰對帳單匯入已停用', detail: '今天 06:41 · 排程已停用，之後不會自動匯入' });
    const stale = cathayView(NONE, { latest: null, latest_ok: null }, now);
    expect(stale.banner).toEqual({ tone: 'warn', heading: '國泰對帳單今天沒有執行', detail: '06:40 的排程沒有回報 · 還沒有執行紀錄' });
    expect(stale.dotLabel).toBe('國泰對帳單今天沒有執行');
    expect(cathayView(NONE, okRuns, now).dotLabel).toBeNull();
  });

  it('failed with files_seen 3, posted 2: runDetails 最近一次執行：今天 06:41 · 失敗 · 收到 3 份 · 已匯入 2 份', () => {
    const v = cathayView(NONE, { latest: run({ result: 'failed', files_seen: 3, posted: 2 }), latest_ok: null }, now);
    expect(v.runDetails).toBe('最近一次執行：今天 06:41 · 失敗 · 收到 3 份 · 已匯入 2 份');
    expect(v.lastOk).toBeNull();
  });

  it('runDetails: latched → 已停用; stale after an ok run → 昨天 06:41 · 成功 · 收到 1 份 · 已匯入 1 份; no run → null; healthy → null', () => {
    expect(cathayView(NONE, { latest: run({ result: 'latched' }), latest_ok: null }, now).runDetails).toBe('最近一次執行：今天 06:41 · 已停用 · 收到 1 份 · 已匯入 1 份');
    const yest = run({ run_at: '2026-10-27T06:41:00+08:00' });
    expect(cathayView(NONE, { latest: yest, latest_ok: yest }, now).runDetails).toBe('最近一次執行：昨天 06:41 · 成功 · 收到 1 份 · 已匯入 1 份');
    expect(cathayView(NONE, { latest: null, latest_ok: null }, now).runDetails).toBeNull();
    expect(cathayView(NONE, okRuns, now).runDetails).toBeNull();
  });

  it('days: label, text and more', () => {
    const v = cathayView({ items: [day({ trade_date: '2026-10-28', status: 'conflict' })], total: 5, truncated: true }, okRuns, now);
    expect(v.days).toEqual([{ tradeDate: '2026-10-28', label: '10/28', text: '收到不同版本的對帳單' }]);
    expect(v.more).toBe(4);
  });
});
