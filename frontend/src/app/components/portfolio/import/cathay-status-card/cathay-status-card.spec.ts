/// <reference types="node" />
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { HttpErrorResponse, provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { computed, signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Subject } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { errorLoggingInterceptor } from '../../../../interceptors/error-logging.interceptor';
import { CathayPending, CathayPendingDay, CathayRun, CathayRuns } from '../../../../models/portfolio.model';
import { NotificationCenterService } from '../../../../services/notification-center.service';
import { PortfolioService } from '../../../../services/portfolio.service';
import { ToastService } from '../../../../services/toast.service';
import { NotifSourceKey } from '../../../notifications/notif-task';
import { CathayView, cathayView } from '../../../notifications/sources/cathay';
import { CathayStatusCardComponent } from './cathay-status-card';

/** 2026-10-29 10:00 Asia/Taipei: today's 06:40 run is due. */
const NOW = new Date('2026-10-29T02:00:00Z');
/** 06:41 Taipei today / yesterday. */
const TODAY_0641 = '2026-10-28T22:41:00Z';
const YESTERDAY_0641 = '2026-10-27T22:41:00Z';

const run = (over: Partial<CathayRun> = {}): CathayRun => ({ run_at: TODAY_0641, result: 'ok', code: null, files_seen: 1, posted: 1, ...over });
const day = (trade_date: string, over: Partial<CathayPendingDay> = {}): CathayPendingDay => ({
  trade_date,
  status: 'held',
  reason: null,
  since: null,
  last_at: null,
  ...over,
});
const view = (items: CathayPendingDay[], runs: CathayRuns, extra: Partial<CathayPending> = {}): CathayView =>
  cathayView({ items, total: items.length, truncated: false, ...extra }, runs, NOW);

const HELD = day('2026-10-28', { reason: 'unresolved_symbol:測試股票', since: '2026-10-28T23:00:00Z' });
const HEALTHY = view([], { latest: run(), latest_ok: run() });
const WITH_HELD = view([HELD], { latest: run(), latest_ok: run() });

describe('CathayStatusCardComponent', () => {
  const cathay = signal<CathayView | null>(HEALTHY);
  const round = signal(1);
  const settledRound = signal(1);
  const failedSources = signal<readonly NotifSourceKey[]>([]);
  const refresh = vi.fn();
  /** As the service: starts the next round and returns its (new) id. */
  const invalidate = vi.fn(() => {
    round.update(r => r + 1);
    return round();
  });
  const stub = {
    cathay,
    round,
    settledRound,
    loaded: computed(() => settledRound() > 0),
    loading: computed(() => round() > settledRound()),
    failedSources,
    refresh,
    invalidate,
  };

  /** One Subject per 已處理, in call order. */
  let acks: Subject<{ trade_date: string; ack_status: string }>[] = [];
  const portfolio = {
    ackCathayDay: vi.fn((_tradeDate: string) => {
      const s = new Subject<{ trade_date: string; ack_status: string }>();
      acks.push(s);
      return s.asObservable();
    }),
  };

  let fixture: ComponentFixture<CathayStatusCardComponent>;
  let toasts: ToastService;
  let logged: ReturnType<typeof vi.spyOn>;

  const text = (el: Element | null | undefined) => el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const root = () => fixture.nativeElement as HTMLElement;
  const q = (sel: string) => root().querySelector<HTMLElement>(sel);
  const settle = async () => {
    await fixture.whenStable();
    fixture.detectChanges();
  };
  const ackButton = (label = '10/28') => q(`button[aria-label="已處理：國泰對帳單 ${label}"]`) as HTMLButtonElement | null;
  const button = (label: string) =>
    Array.from(root().querySelectorAll<HTMLButtonElement>('button')).find(b => text(b) === label) ?? null;
  const answer = async (index = 0) => {
    acks[index].next({ trade_date: '2026-10-28', ack_status: 'held' });
    acks[index].complete();
    await settle();
  };
  const fail = async (status: number, error: unknown = null, index = 0) => {
    acks[index].error(new HttpErrorResponse({ status, error, url: '/api/portfolio/imports/cathay-statement/days/2026-10-28/ack' }));
    await settle();
  };

  function mount(extra: unknown[] = []): HTMLElement {
    TestBed.configureTestingModule({
      providers: [{ provide: NotificationCenterService, useValue: stub }, { provide: PortfolioService, useValue: portfolio }, ...(extra as never[])],
    });
    fixture = TestBed.createComponent(CathayStatusCardComponent);
    fixture.autoDetectChanges();
    toasts = TestBed.inject(ToastService);
    return root();
  }

  beforeEach(() => {
    cathay.set(HEALTHY);
    round.set(1);
    settledRound.set(1);
    failedSources.set([]);
    refresh.mockClear();
    invalidate.mockClear();
    portfolio.ackCathayDay.mockClear();
    acks = [];
    logged = vi.spyOn(console, 'error').mockImplementation(() => undefined);
  });
  afterEach(() => {
    TestBed.resetTestingModule();
    vi.restoreAllMocks();
  });

  it('opening the card asks for a refresh with reason open', () => {
    mount();
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(refresh).toHaveBeenCalledWith({ reason: 'open' });
    expect(invalidate).not.toHaveBeenCalled();
  });

  it('healthy, nothing pending: 今天 06:41 · 已匯入 1 份, no banner, 沒有需要處理的日期', () => {
    const el = mount();
    expect(text(el.querySelector('app-bento h2'))).toBe('國泰每日對帳單');
    expect(text(el.querySelector('.cathay-run-line'))).toBe('今天 06:41 · 已匯入 1 份');
    expect(el.querySelector('app-banner')).toBeNull();
    expect(el.querySelector('.cathay-run-details')).toBeNull();
    expect(text(el)).not.toContain('上次成功');
    expect(text(el.querySelector('.cathay-none'))).toBe('沒有需要處理的日期');
    expect(el.querySelectorAll('.cathay-day').length).toBe(0);
    expect(text(el)).not.toContain('未列出');
    expect(el.querySelector('.cathay-unlatch')).toBeNull();
  });

  it('failed run with code password_file: an error banner saying 讀不到對帳單密碼檔, then 上次成功 昨天 06:41', () => {
    cathay.set(view([], { latest: run({ result: 'failed', code: 'password_file', posted: 0 }), latest_ok: run({ run_at: YESTERDAY_0641 }) }));
    const el = mount();
    const banner = el.querySelector('app-banner')!;
    expect(banner).not.toBeNull();
    expect(banner.querySelector('.banner--error')).not.toBeNull();
    expect(text(banner.querySelector('.banner-title'))).toBe('國泰對帳單匯入失敗');
    expect(text(banner.querySelector('.banner-detail'))).toBe('今天 06:41 · 讀不到對帳單密碼檔');
    expect(el.querySelector('.cathay-unlatch')).toBeNull();
    // Not dismissible, no action.
    expect(banner.querySelector('button')).toBeNull();
    expect(text(el.querySelector('.cathay-last-ok'))).toBe('上次成功 昨天 06:41');
    expect(el.querySelector('.cathay-run-line')).toBeNull();
  });

  it('a failed run with files_seen 3 and posted 2 shows 最近一次執行：今天 06:41 · 失敗 · 收到 3 份 · 已匯入 2 份 beside the error banner', () => {
    cathay.set(view([], { latest: run({ result: 'failed', code: 'parse_error', files_seen: 3, posted: 2 }), latest_ok: null }));
    const el = mount();
    const banner = el.querySelector('app-banner')!;
    expect(banner.querySelector('.banner--error')).not.toBeNull();
    const details = el.querySelector('p.cathay-run-details')!;
    expect(text(details)).toBe('最近一次執行：今天 06:41 · 失敗 · 收到 3 份 · 已匯入 2 份');
    // Right after the banner.
    expect(banner.nextElementSibling).toBe(details);
    expect(el.querySelector('.cathay-last-ok')).toBeNull();
  });

  it('a latched run shows an error banner and how to recover: 排除原因後，在主機執行：python -m worker cathay --unlatch', () => {
    cathay.set(view([], { latest: run({ result: 'latched', code: 'decrypt', posted: 0 }), latest_ok: run({ run_at: YESTERDAY_0641 }) }));
    const el = mount();
    expect(el.querySelector('app-banner .banner--error')).not.toBeNull();
    expect(el.querySelector('app-banner .banner--warn')).toBeNull();
    expect(text(el.querySelector('app-banner .banner-title'))).toBe('國泰對帳單匯入已停用');
    expect(text(el.querySelector('app-banner .banner-detail'))).toBe('今天 06:41 · 排程已停用，之後不會自動匯入');
    expect(text(el.querySelector('.cathay-run-details'))).toBe('最近一次執行：今天 06:41 · 已停用 · 收到 1 份 · 已匯入 0 份');
    const unlatch = el.querySelector('p.cathay-unlatch')!;
    expect(text(unlatch)).toBe('排除原因後，在主機執行：python -m worker cathay --unlatch');
    const code = unlatch.querySelector('code')!;
    expect(code.textContent).toBe('python -m worker cathay --unlatch');
    // Right under the banner's details, before 上次成功.
    expect(el.querySelector('.cathay-run-details')?.nextElementSibling).toBe(unlatch);
    expect(unlatch.nextElementSibling).toBe(el.querySelector('.cathay-last-ok'));
  });

  it('the recovery line\'s <code> wraps on a phone and stays selectable (source pin)', () => {
    const css = readFileSync(join(process.cwd(), 'src/app/components/portfolio/import/cathay-status-card/cathay-status-card.scss'), 'utf8');
    const rule = css.slice(css.indexOf('.cathay-unlatch code {'));
    const body = rule.slice(0, rule.indexOf('}'));
    expect(body).toMatch(/overflow-wrap:\s*anywhere/);
    expect(body).toMatch(/white-space:\s*normal/);
    expect(body).toMatch(/user-select:\s*text/);
    expect(body).not.toMatch(/#[0-9a-f]{3,8}\b|rgba?\(/i);
  });

  it('stale and missing runs show a warn banner, without the recovery line', async () => {
    cathay.set(view([], { latest: run({ run_at: YESTERDAY_0641 }), latest_ok: run({ run_at: YESTERDAY_0641 }) }));
    const el = mount();
    expect(el.querySelector('app-banner .banner--warn')).not.toBeNull();
    expect(el.querySelector('.cathay-unlatch')).toBeNull();
    expect(text(el.querySelector('app-banner .banner-title'))).toBe('國泰對帳單今天沒有執行');
    expect(text(el.querySelector('app-banner .banner-detail'))).toBe('06:40 的排程沒有回報 · 上次執行 昨天 06:41');
    expect(text(el.querySelector('.cathay-run-details'))).toBe('最近一次執行：昨天 06:41 · 成功 · 收到 1 份 · 已匯入 1 份');
    expect(text(el.querySelector('.cathay-last-ok'))).toBe('上次成功 昨天 06:41');

    // No run at all: the banner, no details line.
    cathay.set(view([], { latest: null, latest_ok: null }));
    await settle();
    expect(el.querySelector('app-banner .banner--warn')).not.toBeNull();
    expect(text(el.querySelector('app-banner .banner-detail'))).toBe('06:40 的排程沒有回報 · 還沒有執行紀錄');
    expect(el.querySelector('.cathay-run-details')).toBeNull();
    expect(el.querySelector('.cathay-last-ok')).toBeNull();
  });

  it('a held date lists 10/28 保留：名稱對不到代號 測試股票 with a button named 已處理：國泰對帳單 10/28', () => {
    cathay.set(view([day('2026-10-30', { status: 'mismatch' }), HELD], { latest: run(), latest_ok: run() }));
    const el = mount();
    expect(text(el.querySelector('.cathay-days-title'))).toBe('需要你處理的日期');
    const rows = Array.from(el.querySelectorAll('.cathay-day'));
    expect(rows.map(r => [text(r.querySelector('b')), text(r.querySelector('small'))])).toEqual([
      ['10/28', '保留：名稱對不到代號 測試股票'],
      ['10/30', '對不上 CSV'],
    ]);
    const btn = ackButton()!;
    expect(btn).not.toBeNull();
    expect(text(btn)).toBe('已處理');
    expect(btn.disabled).toBe(false);
    expect(btn.closest('app-btn')?.querySelector('button.btn-soft')).toBe(btn);
    expect(rows[0].contains(btn)).toBe(true);
    expect(el.querySelector('.cathay-none')).toBeNull();
  });

  it('more 23: 還有 23 天未列出，處理完上面的會接著顯示', () => {
    const items = Array.from({ length: 50 }, (_, i) => day(`2026-0${1 + Math.floor(i / 28)}-${String(1 + (i % 28)).padStart(2, '0')}`));
    cathay.set(view(items, { latest: run(), latest_ok: run() }, { total: 73, truncated: true }));
    const el = mount();
    expect(el.querySelectorAll('.cathay-day').length).toBe(50);
    expect(text(el.querySelector('.cathay-more'))).toBe('還有 23 天未列出，處理完上面的會接著顯示');
  });

  it('before the first round: 讀取中…; source failed: 無法讀取國泰對帳單狀態 and 重試 calls invalidate()', async () => {
    round.set(1);
    settledRound.set(0);
    cathay.set(null);
    const el = mount();
    const loading = el.querySelector('[role="status"]')!;
    expect(text(loading)).toBe('讀取中…');
    expect(el.querySelector('app-banner')).toBeNull();
    expect(el.querySelector('.cathay-none')).toBeNull();

    failedSources.set(['dividends', 'cathay']);
    settledRound.set(1);
    await settle();
    expect(text(el)).not.toContain('讀取中…');
    expect(text(el.querySelector('app-banner .banner-title'))).toBe('無法讀取國泰對帳單狀態');
    const retry = button('重試')!;
    expect(retry).not.toBeNull();
    expect(retry.disabled).toBe(false);
    retry.click();
    await settle();
    expect(invalidate).toHaveBeenCalledTimes(1);
    // Disabled while the round runs.
    expect(button('重試')!.disabled).toBe(true);
    button('重試')!.click();
    expect(invalidate).toHaveBeenCalledTimes(1);
    expect(el.querySelector('.cathay-day')).toBeNull();
  });

  it('the first round still in flight (a slow other source): the 國泰 answer shows at once; its failure shows at once', async () => {
    round.set(1);
    settledRound.set(0);
    cathay.set(null);
    const el = mount();
    expect(text(el.querySelector('[role="status"]'))).toBe('讀取中…');

    // The 國泰 source answered; the round has not settled (another source is slow).
    cathay.set(WITH_HELD);
    await settle();
    expect(text(el)).not.toContain('讀取中…');
    expect(text(el.querySelector('.cathay-run-line'))).toBe('今天 06:41 · 已匯入 1 份');
    expect(ackButton()).not.toBeNull();

    // A later first-round-like state where 國泰 failed before the round settled: the unread banner, not 讀取中….
    cathay.set(null);
    failedSources.set(['cathay']);
    await settle();
    expect(text(el)).not.toContain('讀取中…');
    expect(text(el.querySelector('app-banner .banner-title'))).toBe('無法讀取國泰對帳單狀態');
  });

  it('another source failing does not hide the card', () => {
    failedSources.set(['dividends']);
    const el = mount();
    expect(text(el.querySelector('.cathay-run-line'))).toBe('今天 06:41 · 已匯入 1 份');
    expect(text(el)).not.toContain('無法讀取');
  });

  it('已處理 posts once, the button is busy, then 已處理：國泰對帳單 10/28 and invalidate()', async () => {
    cathay.set(WITH_HELD);
    round.set(3);
    settledRound.set(3);
    mount();
    ackButton()!.click();
    ackButton()!.click();
    await settle();
    expect(portfolio.ackCathayDay).toHaveBeenCalledTimes(1);
    expect(portfolio.ackCathayDay).toHaveBeenCalledWith('2026-10-28');
    expect(ackButton()!.disabled).toBe(true);
    expect(ackButton()!.getAttribute('aria-busy')).toBe('true');
    expect(invalidate).not.toHaveBeenCalled();
    await answer();
    expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'done', text: '已處理：國泰對帳單 10/28', action: null }));
    expect(invalidate).toHaveBeenCalledTimes(1);
    expect(refresh).toHaveBeenCalledTimes(1); // the open only
    expect(logged).not.toHaveBeenCalled();
  });

  it('a successful ack keeps the row busy until settledRound() >= N, then the row is gone with the new view', async () => {
    cathay.set(WITH_HELD);
    round.set(3);
    settledRound.set(3);
    mount();
    ackButton()!.click();
    await settle();
    await answer();
    expect(round()).toBe(4);
    expect(ackButton()!.disabled).toBe(true);
    // Round N settles with the date gone.
    cathay.set(HEALTHY);
    settledRound.set(4);
    await settle();
    expect(ackButton()).toBeNull();
    expect(text(q('.cathay-none'))).toBe('沒有需要處理的日期');
  });

  it('404 → 國泰對帳單 10/28：已經不需要處理 as done; 500 → 國泰對帳單 10/28 標記失敗 with 重試', async () => {
    cathay.set(WITH_HELD);
    round.set(3);
    settledRound.set(3);
    mount();
    ackButton()!.click();
    await settle();
    await fail(404, { detail: 'Not Found' });
    expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'done', text: '國泰對帳單 10/28：已經不需要處理', action: null }));
    expect(invalidate).toHaveBeenCalledTimes(1);
    expect(logged).not.toHaveBeenCalled();
    expect(ackButton()!.disabled).toBe(true);
    settledRound.set(4);
    await settle();
    expect(ackButton()!.disabled).toBe(false);

    ackButton()!.click();
    await settle();
    await fail(500, { message: 'Internal Server Error: db down' }, 1);
    expect(toasts.current()).toEqual(
      expect.objectContaining({ kind: 'error', text: '國泰對帳單 10/28 標記失敗', action: expect.objectContaining({ label: '重試' }) }),
    );
    expect(document.body.textContent).not.toContain('db down');
    expect(root().textContent).not.toContain('db down');
    expect(logged).toHaveBeenCalledTimes(1);
    expect(logged.mock.calls[0][1]).toBeInstanceOf(HttpErrorResponse);
    expect(invalidate).toHaveBeenCalledTimes(2);
  });

  it('403: 標記失敗 without 重試', async () => {
    cathay.set(WITH_HELD);
    mount();
    ackButton()!.click();
    await settle();
    await fail(403, { detail: 'write scope required' });
    expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'error', text: '國泰對帳單 10/28 標記失敗', action: null }));
    expect(invalidate).toHaveBeenCalledTimes(1);
  });

  it('failed ack, invalidate() returned round N: while N is in flight 已處理 is disabled and 重試 re-raises the toast; once settledRound() is N the button is enabled and 重試 posts again — no round N+1', async () => {
    cathay.set(WITH_HELD);
    round.set(3);
    settledRound.set(3);
    mount();
    ackButton()!.click();
    await settle();
    await fail(500);
    expect(invalidate).toHaveBeenCalledTimes(1);
    expect(invalidate.mock.results[0].value).toBe(4);
    // Round 3 (the pre-invalidation one) is settled: still busy.
    settledRound.set(3);
    await settle();
    expect(ackButton()!.disabled).toBe(true);
    const first = toasts.current()!.id;
    toasts.runAction(first);
    await settle();
    expect(portfolio.ackCathayDay).toHaveBeenCalledTimes(1);
    expect(toasts.current()).toEqual(
      expect.objectContaining({ kind: 'error', text: '國泰對帳單 10/28 標記失敗', action: expect.objectContaining({ label: '重試' }) }),
    );
    expect(toasts.current()!.id).not.toBe(first);
    // Round N itself settles: released.
    settledRound.set(4);
    await settle();
    expect(round()).toBe(4);
    expect(ackButton()!.disabled).toBe(false);
    toasts.runAction(toasts.current()!.id);
    await settle();
    expect(portfolio.ackCathayDay.mock.calls).toEqual([['2026-10-28'], ['2026-10-28']]);
    expect(ackButton()!.disabled).toBe(true);
  });

  it('a later round superseding N also releases: invalidate() returned 4, round 4 never settles, settledRound() becomes 5 → enabled', async () => {
    cathay.set(WITH_HELD);
    round.set(3);
    settledRound.set(3);
    mount();
    ackButton()!.click();
    await settle();
    await fail(500);
    expect(invalidate.mock.results[0].value).toBe(4);
    // A write elsewhere supersedes round 4.
    round.set(5);
    await settle();
    expect(ackButton()!.disabled).toBe(true);
    settledRound.set(5);
    await settle();
    expect(ackButton()!.disabled).toBe(false);
  });

  it('one POST per press through the app interceptor, also on a 500', async () => {
    cathay.set(WITH_HELD);
    TestBed.configureTestingModule({
      providers: [
        { provide: NotificationCenterService, useValue: stub },
        PortfolioService,
        provideHttpClient(withInterceptors([errorLoggingInterceptor])),
        provideHttpClientTesting(),
      ],
    });
    vi.spyOn(console, 'group').mockImplementation(() => undefined);
    vi.spyOn(console, 'groupEnd').mockImplementation(() => undefined);
    vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    fixture = TestBed.createComponent(CathayStatusCardComponent);
    fixture.autoDetectChanges();
    toasts = TestBed.inject(ToastService);
    const http = TestBed.inject(HttpTestingController);
    ackButton()!.click();
    await settle();
    const url = '/api/portfolio/imports/cathay-statement/days/2026-10-28/ack';
    const posts = http.match(r => r.url.endsWith(url));
    expect(posts.length).toBe(1);
    expect(posts[0].request.method).toBe('POST');
    posts[0].flush({ message: 'boom' }, { status: 500, statusText: 'Server Error' });
    await settle();
    expect(http.match(r => r.url.endsWith(url)).length).toBe(0);
    expect(toasts.current()?.text).toBe('國泰對帳單 10/28 標記失敗');
    http.verify();
  });

  describe('focus after 已處理 and 重試', () => {
    const TWO = view([HELD, day('2026-10-30', { status: 'mismatch' })], { latest: run(), latest_ok: run() });
    const ONLY_30 = view([day('2026-10-30', { status: 'mismatch' })], { latest: run(), latest_ok: run() });
    let outside: HTMLButtonElement | null = null;

    /** Mounted in the document (focus needs it), at round 3 settled. */
    function attach(): HTMLElement {
      round.set(3);
      settledRound.set(3);
      const el = mount();
      document.body.appendChild(el);
      return el;
    }
    /** Presses a row's 已處理 from the keyboard's point of view: focused, then pressed. */
    const press = async (label: string) => {
      ackButton(label)!.focus();
      expect(document.activeElement).toBe(ackButton(label));
      ackButton(label)!.click();
      await settle();
    };
    /**
     * What a browser does with a focused button that turns disabled: focus falls to <body>. (jsdom keeps focus on a
     * disabled button and ignores its blur(), so focus moves to a throwaway control that is then removed.)
     */
    const dropFocus = () => {
      const t = document.createElement('button');
      document.body.appendChild(t);
      t.focus();
      t.remove();
      expect(document.activeElement).toBe(document.body);
    };

    afterEach(() => {
      root()?.remove();
      outside?.remove();
      outside = null;
    });

    it('success with two rows: once round N settles without the date, focus goes to the next row’s 已處理', async () => {
      cathay.set(TWO);
      attach();
      await press('10/28');
      dropFocus();
      await answer();
      cathay.set(ONLY_30);
      settledRound.set(4);
      await settle();
      expect(ackButton('10/28')).toBeNull();
      expect(document.activeElement).toBe(ackButton('10/30'));
    });

    it('success on the last row: focus goes to the previous row’s 已處理', async () => {
      cathay.set(TWO);
      attach();
      await press('10/30');
      dropFocus();
      await answer();
      cathay.set(WITH_HELD);
      settledRound.set(4);
      await settle();
      expect(ackButton('10/30')).toBeNull();
      expect(document.activeElement).toBe(ackButton('10/28'));
    });

    it('success with one row: focus goes to 沒有需要處理的日期 (tabindex -1)', async () => {
      cathay.set(WITH_HELD);
      attach();
      await press('10/28');
      dropFocus();
      await answer();
      cathay.set(HEALTHY);
      settledRound.set(4);
      await settle();
      const none = q('.cathay-none')!;
      expect(none.getAttribute('tabindex')).toBe('-1');
      expect(document.activeElement).toBe(none);
    });

    it('failed ack: focus comes back to the same 已處理 once round N settles, not before', async () => {
      cathay.set(WITH_HELD);
      attach();
      await press('10/28');
      dropFocus();
      await fail(500);
      settledRound.set(3);
      await settle();
      expect(document.activeElement).toBe(document.body);
      settledRound.set(4);
      await settle();
      expect(ackButton('10/28')!.disabled).toBe(false);
      expect(document.activeElement).toBe(ackButton('10/28'));
    });

    it('focus the owner moved elsewhere before the round settled is left alone', async () => {
      cathay.set(TWO);
      attach();
      outside = document.createElement('button');
      document.body.appendChild(outside);
      await press('10/28');
      dropFocus();
      outside.focus();
      await answer();
      cathay.set(ONLY_30);
      settledRound.set(4);
      await settle();
      expect(document.activeElement).toBe(outside);
    });

    it('an 已處理 sent from the toast’s 重試 (the button not focused) moves no focus', async () => {
      cathay.set(WITH_HELD);
      attach();
      ackButton()!.click();
      await settle();
      await fail(500);
      settledRound.set(4);
      await settle();
      dropFocus();
      toasts.runAction(toasts.current()!.id);
      await settle();
      await answer(1);
      cathay.set(HEALTHY);
      settledRound.set(5);
      await settle();
      expect(document.activeElement).toBe(document.body);
    });

    it('banner 重試 that reads: focus goes to the first 已處理, or to 沒有需要處理的日期', async () => {
      failedSources.set(['cathay']);
      cathay.set(null);
      attach();
      button('重試')!.focus();
      button('重試')!.click();
      await settle();
      dropFocus();
      failedSources.set([]);
      cathay.set(TWO);
      settledRound.set(4);
      await settle();
      expect(document.activeElement).toBe(ackButton('10/28'));

      // Again, with nothing pending.
      failedSources.set(['cathay']);
      cathay.set(null);
      await settle();
      button('重試')!.focus();
      button('重試')!.click();
      await settle();
      dropFocus();
      failedSources.set([]);
      cathay.set(HEALTHY);
      settledRound.set(5);
      await settle();
      expect(document.activeElement).toBe(q('.cathay-none'));
    });

    it('banner 重試 that fails again: focus goes back to 重試', async () => {
      failedSources.set(['cathay']);
      cathay.set(null);
      attach();
      button('重試')!.focus();
      button('重試')!.click();
      await settle();
      dropFocus();
      settledRound.set(4);
      await settle();
      expect(document.activeElement).toBe(button('重試'));
    });
  });
});
