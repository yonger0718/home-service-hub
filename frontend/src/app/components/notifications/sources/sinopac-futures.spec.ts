import { CathayRun, CathayRuns, FuturesPending, FuturesPendingItem } from '../../../models/portfolio.model';
import {
  FUTURES_CARD_FRAGMENT,
  futuresItemLabel,
  futuresMore,
  futuresReasonText,
  futuresRunState,
  futuresTasks,
  futuresView,
} from './sinopac-futures';

const at = (s: string) => new Date(s);
const run = (o: Partial<CathayRun> = {}): CathayRun => ({ run_at: '2026-10-28T06:46:00+08:00', result: 'ok', code: null, files_seen: 1, posted: 1, ...o });
const NONE: FuturesPending = { items: [], total: 0, truncated: false };
/** 2026-10-28 07:30 Asia/Taipei: today's 06:45 run is due (grace 15 minutes). */
const now = at('2026-10-28T07:30:00+08:00');
const okRuns: CathayRuns = { latest: run(), latest_ok: run() };
const item = (o: Partial<FuturesPendingItem> = {}): FuturesPendingItem => ({
  key: '2026-10-27', kind: 'day', status: 'findings', reason: 'prev:10/23', since: null, last_at: null, ...o,
});
const pending = (items: FuturesPendingItem[], extra: Partial<FuturesPending> = {}): FuturesPending => ({ items, total: items.length, truncated: false, ...extra });
const LOOK = { label: '查看', route: '/portfolio/import', fragment: 'sinopac-futures' };

describe('sinopac futures pending items', () => {
  it('one task per item with id sinopac_futures:<key>:<status>', () => {
    const [t] = futuresTasks(pending([item({ since: '2026-10-28T06:46:00+08:00' })]), okRuns, now);
    expect(t).toEqual({
      id: 'sinopac_futures:2026-10-27:findings',
      source: 'portfolio',
      from: 'sinopac_futures',
      tier: 'system',
      icon: 'pi-file',
      title: '永豐期貨對帳單 10/27',
      when: '前日餘額接不上（10/23）',
      meta: '自 10/28 起',
      amount: null,
      progress: null,
      primary: { label: '知道了', command: { kind: 'ackFutures', key: '2026-10-27', status: 'findings', name: '永豐期貨對帳單 10/27' } },
      secondary: [LOOK],
      sortKey: '2026-10-27',
      fingerprint: 'findings|prev:10/23',
    });
  });

  it('conflict and findings on one key give two tasks, with distinct titles (the conflict one suffixed · 不同版本)', () => {
    const items = [item({ status: 'findings' }), item({ status: 'conflict', reason: null })];
    const tasks = futuresTasks(pending(items), okRuns, now);
    expect(tasks.map(t => [t.id, t.title, t.when])).toEqual([
      ['sinopac_futures:2026-10-27:conflict', '永豐期貨對帳單 10/27 · 不同版本', '收到內容不同的同日對帳單'],
      ['sinopac_futures:2026-10-27:findings', '永豐期貨對帳單 10/27', '前日餘額接不上（10/23）'],
    ]);
    expect(tasks.map(t => (t.primary as { command: { name: string } }).command.name)).toEqual([
      '永豐期貨對帳單 10/27 · 不同版本',
      '永豐期貨對帳單 10/27',
    ]);
    const month = futuresTasks(pending([item({ key: '2026-09', kind: 'month', status: 'conflict', reason: null })]), okRuns, now);
    expect(month[0].title).toBe('永豐期貨 2026/09 月對帳單 · 不同版本');
    expect(tasks.map(t => (t.primary as { command: { status: string } }).command.status)).toEqual(['conflict', 'findings']);
  });

  it('a month key reads YYYY/MM 月對帳單; items come by key then status, the run task last', () => {
    const items = [item({ key: '2026-10-27' }), item({ key: '2026-09', kind: 'month', reason: 'carry' })];
    const tasks = futuresTasks(pending(items), { latest: run({ result: 'failed' }), latest_ok: null }, now);
    expect(tasks.map(t => [t.id, t.title])).toEqual([
      ['sinopac_futures:2026-09:findings', '永豐期貨 2026/09 月對帳單'],
      ['sinopac_futures:2026-10-27:findings', '永豐期貨對帳單 10/27'],
      ['sinopac_futures:run', '永豐期貨對帳單匯入失敗'],
    ]);
  });

  it('查看 links to /portfolio/import#sinopac-futures (items and the run task)', () => {
    expect(FUTURES_CARD_FRAGMENT).toBe('sinopac-futures');
    const yesterday = run({ run_at: '2026-10-27T06:46:00+08:00' });
    const tasks = futuresTasks(pending([item()]), { latest: yesterday, latest_ok: yesterday }, now);
    expect(tasks[0].secondary).toEqual([LOOK]);
    expect(tasks[1]).toMatchObject({ id: 'sinopac_futures:run', primary: LOOK, secondary: [] });
  });

  it('a changed reason gives a new fingerprint; a newer last_at alone does not', () => {
    const f = (i: FuturesPendingItem) => futuresTasks(pending([i]), okRuns, now)[0].fingerprint;
    expect(f(item({ reason: 'gap:2' }))).not.toBe(f(item({ reason: 'gap:1' })));
    expect(f(item({ last_at: '2026-10-30T06:46:00+08:00' }))).toBe(f(item({ last_at: '2026-10-29T06:46:00+08:00' })));
  });

  it.each([
    [{ status: 'conflict', kind: 'day', reason: null }, '收到內容不同的同日對帳單'],
    [{ status: 'conflict', kind: 'month', reason: null }, '收到內容不同的同月對帳單'],
    [{ reason: 'prev:10/23' }, '前日餘額接不上（10/23）'],
    [{ reason: 'prev:2026-03-06' }, '前日餘額接不上（03/06）'],
    [{ reason: 'gap:3' }, '缺 3 份日對帳單'],
    [{ reason: 'carry' }, '月初餘額接不上'],
    [{ reason: 'diff:BK' }, '與日對帳單不符：B K'],
    [{ reason: 'gap:1,carry,diff:BGHKM' }, '缺 1 份日對帳單；月初餘額接不上；與日對帳單不符：B G H K M'],
    [{ reason: null }, '對帳單有待確認的差異'],
    [{ reason: '' }, '對帳單有待確認的差異'],
    [{ reason: 'odd' }, 'odd'],
  ] as const)('futuresReasonText %o → %s', (o, text) => {
    expect(futuresReasonText(item(o as Partial<FuturesPendingItem>))).toBe(text);
  });

  it('a reason cut at 40 characters mid-token shows the raw tail with …', () => {
    // 40 characters: the last code was cut inside its date.
    const cut = 'prev:10/23,gap:12,carry,diff:BGHKM,prev:1';
    const reason = cut.slice(0, 40);
    expect(reason.length).toBe(40);
    expect(futuresReasonText(item({ reason }))).toBe('前日餘額接不上（10/23）；缺 12 份日對帳單；月初餘額接不上；與日對帳單不符：B G H K M；prev:…');
    // Cut right after a separator: nothing raw to show, still marked as cut.
    expect(futuresReasonText(item({ reason: 'gap:12,'.padEnd(40, ',').slice(0, 40) }))).toBe('缺 12 份日對帳單…');
  });

  it('futuresItemLabel: a date → MM/DD; a month → YYYY/MM 月對帳單', () => {
    expect(futuresItemLabel('2026-03-06')).toBe('03/06');
    expect(futuresItemLabel('2026-03')).toBe('2026/03 月對帳單');
  });

  it('futuresMore: total 73 with 50 items → 23; not truncated → 0', () => {
    const items = Array.from({ length: 50 }, (_, i) => item({ key: `2026-09-${String((i % 28) + 1).padStart(2, '0')}` }));
    expect(futuresMore({ items, total: 73, truncated: true })).toBe(23);
    expect(futuresMore({ items, total: 73, truncated: false })).toBe(0);
  });
});

describe('sinopac futures run rule (06:45 Asia/Taipei + 15 minutes, the 國泰 rules)', () => {
  it.each([
    ['2026-10-28T06:59:00+08:00', 'ok'],
    ['2026-10-28T07:00:00+08:00', 'stale'],
  ])('latest yesterday 06:46, now %s → %s', (n, state) => {
    expect(futuresRunState({ latest: run({ run_at: '2026-10-27T06:46:00+08:00' }), latest_ok: null }, at(n))).toBe(state);
  });

  it('a run exactly at 06:45:00 is ok; one second earlier is stale; an unreadable run_at is stale', () => {
    expect(futuresRunState({ latest: run({ run_at: '2026-10-28T06:45:00+08:00' }), latest_ok: null }, now)).toBe('ok');
    expect(futuresRunState({ latest: run({ run_at: '2026-10-28T06:44:59+08:00' }), latest_ok: null }, now)).toBe('stale');
    expect(futuresRunState({ latest: run({ run_at: 'not a time' }), latest_ok: null }, now)).toBe('stale');
  });

  it('run task when latched / failed / stale; none when ok', () => {
    expect(futuresTasks(NONE, okRuns, now)).toEqual([]);

    const failed = run({ result: 'failed', code: 'password_file' });
    expect(futuresTasks(NONE, { latest: failed, latest_ok: null }, now)).toEqual([expect.objectContaining({
      id: 'sinopac_futures:run', from: 'sinopac_futures', tier: 'system', icon: 'pi-exclamation-triangle', title: '永豐期貨對帳單匯入失敗',
      when: '今天 06:46', meta: '讀不到對帳單密碼檔', sortKey: '9999-12-31', fingerprint: `failed|${failed.run_at}` })]);

    // Latched yesterday and nothing since stays latched (not stale).
    const latched = run({ result: 'latched', run_at: '2026-10-27T06:46:00+08:00' });
    expect(futuresTasks(NONE, { latest: latched, latest_ok: null }, now)).toEqual([expect.objectContaining({
      id: 'sinopac_futures:run', title: '永豐期貨對帳單匯入已停用', when: '昨天 06:46', meta: '排程已停用，之後不會自動匯入' })]);

    const yesterday = run({ run_at: '2026-10-27T06:46:00+08:00' });
    expect(futuresTasks(NONE, { latest: yesterday, latest_ok: yesterday }, now)).toEqual([expect.objectContaining({
      id: 'sinopac_futures:run', title: '永豐期貨對帳單今天沒有執行', when: '06:45 的排程沒有回報', meta: '上次執行 昨天 06:46',
      fingerprint: 'stale|2026-10-27T22:45:00.000Z' })]);
  });

  it('never run (latest and latest_ok both null): automatic import not enabled yet — no run task', () => {
    expect(futuresRunState({ latest: null, latest_ok: null }, now)).toBe('never');
    expect(futuresTasks(NONE, { latest: null, latest_ok: null }, now)).toEqual([]);
    // Pending items still give their tasks.
    expect(futuresTasks(pending([item()]), { latest: null, latest_ok: null }, now).map(t => t.id)).toEqual(['sinopac_futures:2026-10-27:findings']);
  });

  it('once either run exists the 國泰 rules apply: latest_ok alone (latest null) is stale with 還沒有執行紀錄', () => {
    const ok = run({ run_at: '2026-10-27T06:46:00+08:00' });
    expect(futuresRunState({ latest: null, latest_ok: ok }, now)).toBe('stale');
    expect(futuresTasks(NONE, { latest: null, latest_ok: ok }, now)).toEqual([expect.objectContaining({
      id: 'sinopac_futures:run', title: '永豐期貨對帳單今天沒有執行', when: '06:45 的排程沒有回報', meta: '還沒有執行紀錄' })]);
    // A first run that failed raises the alarm at once.
    expect(futuresTasks(NONE, { latest: run({ result: 'failed' }), latest_ok: null }, now).map(t => t.id)).toEqual(['sinopac_futures:run']);
  });
});

describe('futuresView', () => {
  it('healthy: run line, no banner, no items', () => {
    expect(futuresView(NONE, okRuns, now)).toEqual({ state: 'ok', runLine: '今天 06:46 · 已匯入 1 份', banner: null,
      runDetails: null, lastOk: null, items: [], more: 0, dotLabel: null, pendingLabel: null });
  });

  it('failed / latched: error banner with run details and 上次成功; stale: warn banner', () => {
    const v = futuresView(NONE, { latest: run({ result: 'failed', code: 'password_file', files_seen: 2, posted: 1 }), latest_ok: run({ run_at: '2026-10-27T06:46:00+08:00' }) }, now);
    expect(v).toMatchObject({ state: 'failed', runLine: null, lastOk: '上次成功 昨天 06:46', dotLabel: '永豐期貨對帳單匯入失敗',
      runDetails: '最近一次執行：今天 06:46 · 失敗 · 收到 2 份 · 已匯入 1 份',
      banner: { tone: 'error', heading: '永豐期貨對帳單匯入失敗', detail: '今天 06:46 · 讀不到對帳單密碼檔' } });
    expect(futuresView(NONE, { latest: run({ result: 'latched' }), latest_ok: null }, now).banner)
      .toEqual({ tone: 'error', heading: '永豐期貨對帳單匯入已停用', detail: '今天 06:46 · 排程已停用，之後不會自動匯入' });
    const yesterday = run({ run_at: '2026-10-27T06:46:00+08:00' });
    expect(futuresView(NONE, { latest: yesterday, latest_ok: yesterday }, now).banner)
      .toEqual({ tone: 'warn', heading: '永豐期貨對帳單今天沒有執行', detail: '06:45 的排程沒有回報 · 上次執行 昨天 06:46' });
  });

  it('never run: a neutral 尚未啟用自動匯入 line, no banner, no dot', () => {
    expect(futuresView(NONE, { latest: null, latest_ok: null }, now)).toEqual({ state: 'never', runLine: '尚未啟用自動匯入', banner: null,
      runDetails: null, lastOk: null, items: [], more: 0, dotLabel: null, pendingLabel: null });
  });

  it('items: id, key, status, label, text, name; pendingLabel counts the server total when truncated', () => {
    const items = [item({ status: 'findings' }), item({ status: 'conflict', reason: null })];
    const v = futuresView(pending(items, { total: 5, truncated: true }), okRuns, now);
    expect(v.items).toEqual([
      { id: '2026-10-27:conflict', key: '2026-10-27', status: 'conflict', label: '10/27', text: '收到內容不同的同日對帳單', name: '永豐期貨對帳單 10/27 · 不同版本' },
      { id: '2026-10-27:findings', key: '2026-10-27', status: 'findings', label: '10/27', text: '前日餘額接不上（10/23）', name: '永豐期貨對帳單 10/27' },
    ]);
    expect(v.more).toBe(3);
    expect(v.pendingLabel).toBe('5 件永豐期貨對帳單待處理');
    expect(futuresView(pending(items), okRuns, now).pendingLabel).toBe('2 件永豐期貨對帳單待處理');
    expect(futuresView(NONE, okRuns, now).pendingLabel).toBeNull();
  });
});
