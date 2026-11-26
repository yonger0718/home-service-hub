import { HttpErrorResponse } from '@angular/common/http';
import { computed, signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Observable, Subject, of, throwError } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { CathayRun, CathayRuns, FuturesPendingItem, FuturesSummary } from '../../../../models/portfolio.model';
import { NotificationCenterService } from '../../../../services/notification-center.service';
import { PortfolioService } from '../../../../services/portfolio.service';
import { ToastService } from '../../../../services/toast.service';
import { NotifSourceKey } from '../../../notifications/notif-task';
import { FuturesView, futuresView } from '../../../notifications/sources/sinopac-futures';
import { FuturesStatusCardComponent } from './futures-status-card';

/** 2026-10-29 10:00 Asia/Taipei: today's 06:45 run is due. */
const NOW = new Date('2026-10-29T02:00:00Z');
/** 06:46 Taipei today / yesterday. */
const TODAY_0646 = '2026-10-28T22:46:00Z';
const YESTERDAY_0646 = '2026-10-27T22:46:00Z';

const run = (over: Partial<CathayRun> = {}): CathayRun => ({ run_at: TODAY_0646, result: 'ok', code: null, files_seen: 1, posted: 1, ...over });
const item = (key: string, over: Partial<FuturesPendingItem> = {}): FuturesPendingItem => ({
  key, kind: key.length === 7 ? 'month' : 'day', status: 'findings', reason: 'gap:1', since: null, last_at: null, ...over,
});
const view = (items: FuturesPendingItem[], runs: CathayRuns, extra: { total?: number; truncated?: boolean } = {}): FuturesView =>
  futuresView({ items, total: items.length, truncated: false, ...extra }, runs, NOW);
const OK_RUNS: CathayRuns = { latest: run(), latest_ok: run() };
const HEALTHY = view([], OK_RUNS);
const TWO_ON_ONE_DAY = view([item('2026-10-27', { reason: 'prev:10/23' }), item('2026-10-27', { status: 'conflict', reason: null })], OK_RUNS);

const summary = (value: Partial<NonNullable<FuturesSummary['value']>> | null): FuturesSummary => ({
  as_of: '2026-10-29',
  value: value && {
    total_equity: '1250000.0000', balance: '1000000.0000', source_date: '2026-10-28', source_kind: 'daily',
    positions_open: false, frozen: false, ...value,
  },
  latest_daily: null,
  month: null,
});

describe('FuturesStatusCardComponent', () => {
  const futures = signal<FuturesView | null>(HEALTHY);
  const round = signal(1);
  const settledRound = signal(1);
  const failedSources = signal<readonly NotifSourceKey[]>([]);
  const refresh = vi.fn();
  const invalidate = vi.fn(() => {
    round.update(r => r + 1);
    return round();
  });
  const stub = {
    futures,
    round,
    settledRound,
    loaded: computed(() => settledRound() > 0),
    loading: computed(() => round() > settledRound()),
    failedSources,
    refresh,
    invalidate,
  };

  let acks: Subject<void>[] = [];
  let summaryAnswer: Observable<FuturesSummary> = of(summary({}));
  const portfolio = {
    ackFuturesItem: vi.fn((_key: string, _status: string) => {
      const s = new Subject<void>();
      acks.push(s);
      return s.asObservable();
    }),
    getFuturesSummary: vi.fn(() => summaryAnswer),
  };

  let fixture: ComponentFixture<FuturesStatusCardComponent>;
  let toasts: ToastService;

  const text = (el: Element | null | undefined) => el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const root = () => fixture.nativeElement as HTMLElement;
  const q = (sel: string) => root().querySelector<HTMLElement>(sel);
  const settle = async () => {
    await fixture.whenStable();
    fixture.detectChanges();
  };
  const ackButton = (id: string) => q(`li.futures-item[data-item="${id}"] button`) as HTMLButtonElement | null;
  const button = (label: string) =>
    Array.from(root().querySelectorAll<HTMLButtonElement>('button')).find(b => text(b) === label) ?? null;

  function mount(): HTMLElement {
    TestBed.configureTestingModule({
      providers: [{ provide: NotificationCenterService, useValue: stub }, { provide: PortfolioService, useValue: portfolio }],
    });
    fixture = TestBed.createComponent(FuturesStatusCardComponent);
    fixture.autoDetectChanges();
    toasts = TestBed.inject(ToastService);
    return root();
  }

  beforeEach(() => {
    futures.set(HEALTHY);
    round.set(1);
    settledRound.set(1);
    failedSources.set([]);
    refresh.mockClear();
    invalidate.mockClear();
    portfolio.ackFuturesItem.mockClear();
    portfolio.getFuturesSummary.mockClear();
    acks = [];
    summaryAnswer = of(summary({}));
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
  });
  afterEach(() => {
    TestBed.resetTestingModule();
    vi.restoreAllMocks();
  });

  it('opening the card asks for a refresh with reason open and reads the futures summary once', () => {
    mount();
    expect(refresh).toHaveBeenCalledWith({ reason: 'open' });
    expect(portfolio.getFuturesSummary).toHaveBeenCalledTimes(1);
    expect(portfolio.getFuturesSummary).toHaveBeenCalledWith();
  });

  it('shows run state: healthy run line, nothing pending', () => {
    const el = mount();
    expect(text(el.querySelector('app-bento h2'))).toBe('永豐期貨對帳單');
    expect(text(el.querySelector('.futures-run-line'))).toBe('今天 06:46 · 已匯入 1 份');
    expect(el.querySelector('app-banner')).toBeNull();
    expect(text(el.querySelector('.futures-none'))).toBe('沒有需要處理的對帳單');
  });

  it('shows run state: failed, latched (with the unlatch command) and stale banners', async () => {
    futures.set(view([], { latest: run({ result: 'failed', code: 'password_file', posted: 0 }), latest_ok: run({ run_at: YESTERDAY_0646 }) }));
    const el = mount();
    expect(el.querySelector('app-banner .banner--error')).not.toBeNull();
    expect(text(el.querySelector('app-banner .banner-title'))).toBe('永豐期貨對帳單匯入失敗');
    expect(text(el.querySelector('app-banner .banner-detail'))).toBe('今天 06:46 · 讀不到對帳單密碼檔');
    expect(text(el.querySelector('.futures-run-details'))).toBe('最近一次執行：今天 06:46 · 失敗 · 收到 1 份 · 已匯入 0 份');
    expect(text(el.querySelector('.futures-last-ok'))).toBe('上次成功 昨天 06:46');
    expect(el.querySelector('.futures-unlatch')).toBeNull();
    expect(el.querySelector('.futures-run-line')).toBeNull();

    futures.set(view([], { latest: run({ result: 'latched' }), latest_ok: null }));
    await settle();
    expect(text(el.querySelector('app-banner .banner-title'))).toBe('永豐期貨對帳單匯入已停用');
    expect(text(el.querySelector('.futures-unlatch'))).toBe('排除原因後，在主機執行：python -m worker sinopac-futures --unlatch');
    expect(el.querySelector('.futures-unlatch code')?.textContent).toBe('python -m worker sinopac-futures --unlatch');

    futures.set(view([], { latest: run({ run_at: YESTERDAY_0646 }), latest_ok: run({ run_at: YESTERDAY_0646 }) }));
    await settle();
    expect(el.querySelector('app-banner .banner--warn')).not.toBeNull();
    expect(text(el.querySelector('app-banner .banner-detail'))).toBe('06:45 的排程沒有回報 · 上次執行 昨天 06:46');
    expect(el.querySelector('.futures-unlatch')).toBeNull();
  });

  it('never run: the neutral line 尚未啟用自動匯入, no banner', () => {
    futures.set(view([], { latest: null, latest_ok: null }));
    const el = mount();
    expect(el.querySelector('app-banner')).toBeNull();
    expect(text(el.querySelector('.futures-run-line'))).toBe('尚未啟用自動匯入');
    expect(text(el.querySelector('.futures-none'))).toBe('沒有需要處理的對帳單');
  });

  it('lists pending items with ack: one row per incident, labelled by key, with 已處理 named after the item', () => {
    futures.set(view([item('2026-09', { reason: 'diff:BK' }), ...[item('2026-10-27', { reason: 'prev:10/23' }), item('2026-10-27', { status: 'conflict', reason: null })]], OK_RUNS));
    const el = mount();
    expect(text(el.querySelector('.futures-items-title'))).toBe('需要你處理的對帳單');
    const rows = Array.from(el.querySelectorAll('li.futures-item'));
    expect(rows.map(r => [r.getAttribute('data-item'), text(r.querySelector('b')), text(r.querySelector('small'))])).toEqual([
      ['2026-09:findings', '2026/09 月對帳單', '與日對帳單不符：B K'],
      ['2026-10-27:conflict', '10/27', '收到內容不同的同日對帳單'],
      ['2026-10-27:findings', '10/27', '前日餘額接不上（10/23）'],
    ]);
    const btn = ackButton('2026-10-27:findings')!;
    expect(text(btn)).toBe('已處理');
    expect(btn.getAttribute('aria-label')).toBe('已處理：永豐期貨對帳單 10/27 前日餘額接不上（10/23）');
    expect(ackButton('2026-10-27:conflict')!.getAttribute('aria-label')).toBe('已處理：永豐期貨對帳單 10/27 · 不同版本 收到內容不同的同日對帳單');
    expect(el.querySelector('.futures-none')).toBeNull();
  });

  it('more: 還有 N 件未列出', () => {
    const items = Array.from({ length: 50 }, (_, i) => item(new Date(Date.UTC(2026, 6, 1 + i)).toISOString().slice(0, 10)));
    futures.set(view(items, OK_RUNS, { total: 53, truncated: true }));
    mount();
    expect(text(q('.futures-more'))).toBe('還有 3 件未列出，處理完上面的會接著顯示');
  });

  it('已處理 posts that key and status once; on success the 已處理 toast and a re-read, the row busy until it settles', async () => {
    futures.set(TWO_ON_ONE_DAY);
    round.set(3);
    settledRound.set(3);
    mount();
    ackButton('2026-10-27:conflict')!.click();
    ackButton('2026-10-27:conflict')!.click();
    await settle();
    expect(portfolio.ackFuturesItem).toHaveBeenCalledTimes(1);
    expect(portfolio.ackFuturesItem).toHaveBeenCalledWith('2026-10-27', 'conflict');
    expect(ackButton('2026-10-27:conflict')!.disabled).toBe(true);
    // The other incident on the same day stays available.
    expect(ackButton('2026-10-27:findings')!.disabled).toBe(false);
    acks[0].next(undefined);
    acks[0].complete();
    await settle();
    expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'done', text: '已處理：永豐期貨對帳單 10/27 · 不同版本' }));
    expect(invalidate).toHaveBeenCalledTimes(1);
    expect(ackButton('2026-10-27:conflict')!.disabled).toBe(true);
    settledRound.set(4);
    futures.set(view([item('2026-10-27', { reason: 'prev:10/23' })], OK_RUNS));
    await settle();
    expect(ackButton('2026-10-27:conflict')).toBeNull();
    expect(ackButton('2026-10-27:findings')!.disabled).toBe(false);
  });

  it('404 → 已經不需要處理 and a re-read; 500 → 標記失敗 with 重試', async () => {
    futures.set(TWO_ON_ONE_DAY);
    mount();
    ackButton('2026-10-27:findings')!.click();
    await settle();
    acks[0].error(new HttpErrorResponse({ status: 404, url: '/api/portfolio/imports/sinopac-futures-statement/days/2026-10-27/ack' }));
    await settle();
    expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'done', text: '永豐期貨對帳單 10/27：已經不需要處理' }));
    expect(invalidate).toHaveBeenCalledTimes(1);

    settledRound.set(2);
    await settle();
    ackButton('2026-10-27:findings')!.click();
    await settle();
    acks[1].error(new HttpErrorResponse({ status: 500, error: { message: 'db down' } }));
    await settle();
    expect(toasts.current()).toEqual(
      expect.objectContaining({ kind: 'error', text: '永豐期貨對帳單 10/27 標記失敗', action: expect.objectContaining({ label: '重試' }) }),
    );
    expect(invalidate).toHaveBeenCalledTimes(2);
  });

  it('the source unread: 無法讀取永豐期貨對帳單狀態 with 重試; another source failing does not hide the card', async () => {
    failedSources.set(['cathay']);
    const el = mount();
    expect(text(el.querySelector('.futures-run-line'))).toBe('今天 06:46 · 已匯入 1 份');
    failedSources.set(['sinopac_futures']);
    futures.set(null);
    await settle();
    expect(text(el.querySelector('app-banner .banner-title'))).toBe('無法讀取永豐期貨對帳單狀態');
    button('重試')!.click();
    expect(invalidate).toHaveBeenCalledTimes(1);
  });

  it('before the futures answer: 讀取中…', () => {
    futures.set(null);
    settledRound.set(0);
    const el = mount();
    expect(text(el.querySelector('.futures-loading'))).toBe('讀取中…');
  });

  describe('權益總值', () => {
    it('shows the equity with its source date', () => {
      const el = mount();
      expect(text(el.querySelector('[data-testid="futures-status-equity"]'))).toBe('$1,250,000');
      expect(text(el.querySelector('.futures-source'))).toBe('資料日 10/28');
      expect(el.querySelector('.futures-frozen')).toBeNull();
    });

    it('frozen note for an old source with open positions', () => {
      summaryAnswer = of(summary({ source_date: '2026-10-23', positions_open: true, frozen: true }));
      const el = mount();
      expect(text(el.querySelector('.futures-source'))).toContain('資料日 10/23');
      expect(text(el.querySelector('.futures-frozen'))).toBe('持倉中，數字停在 10/23');
    });

    it.each([
      ['the source is today', { source_date: '2026-10-29', positions_open: true, frozen: false }],
      ['flat', { source_date: '2026-10-23', positions_open: false, frozen: false }],
    ])('no frozen note when %s', (_name, value) => {
      summaryAnswer = of(summary(value));
      const el = mount();
      expect(el.querySelector('.futures-frozen')).toBeNull();
    });

    it('no statement yet: 尚無期貨對帳單; a failed read: 讀不到期貨權益', async () => {
      summaryAnswer = of(summary(null));
      const el = mount();
      expect(text(el.querySelector('.futures-equity-none'))).toBe('尚無期貨對帳單');
      expect(el.querySelector('[data-testid="futures-status-equity"]')).toBeNull();
      expect(el.querySelector('.futures-equity-none')!.getAttribute('role')).toBeNull();
      TestBed.resetTestingModule();
      summaryAnswer = throwError(() => new HttpErrorResponse({ status: 500 }));
      const failed = mount();
      expect(text(failed.querySelector('.futures-equity-none'))).toBe('讀不到期貨權益');
      expect(failed.querySelector('.futures-equity-none')!.getAttribute('role')).toBe('alert');
    });
  });
});
