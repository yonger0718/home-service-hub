import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { ActivatedRoute, Router, convertToParamMap, provideRouter } from '@angular/router';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  FuturesDay,
  FuturesDetail,
  FuturesDetailSummary,
  FuturesFill,
  FuturesPositions,
} from '../../../models/portfolio.model';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { PortfolioFuturesDetailComponent } from './futures-detail';

function summary(over: Partial<FuturesDetailSummary> = {}): FuturesDetailSummary {
  return {
    futures_realized: '12000.0000',
    premium_net: '-300.0000',
    fees_and_tax: '450.0000',
    deposits_net: '100000.0000',
    source: 'monthly',
    complete: true,
    reason: null,
    missing_days: 0,
    ...over,
  };
}

function fill(over: Partial<FuturesFill> = {}): FuturesFill {
  return {
    kind: 'future',
    order_no: 'sA1B2-01',
    product: '範例期貨',
    month: '202603',
    strike: null,
    right: null,
    action: '平賣',
    qty: 2,
    price: '20500',
    tax: '20.0000',
    fee: '10.0000',
    pnl: '1500.0000',
    closes: [],
    ...over,
  };
}

function day(over: Partial<FuturesDay> = {}): FuturesDay {
  return {
    date: '2026-03-05',
    no_trade: false,
    fills: [fill()],
    fills_total: { tax: '20.0000', fee: '10.0000', pnl: '1500.0000' },
    deposits: [],
    ...over,
  };
}

function detail(over: Partial<FuturesDetail> = {}): FuturesDetail {
  return { month: '2026-03', summary: summary(), days: [day()], positions: null, ...over };
}

function setup(query: Record<string, string> = {}, phone = false) {
  const navigate = vi.fn(() => Promise.resolve(true));
  TestBed.configureTestingModule({
    providers: [
      provideHttpClient(),
      provideHttpClientTesting(),
      provideRouter([]),
      { provide: ActivatedRoute, useValue: { snapshot: { queryParamMap: convertToParamMap(query) } } },
    ],
  });
  const router = TestBed.inject(Router);
  vi.spyOn(router, 'navigate').mockImplementation(navigate);
  if (phone) TestBed.inject(LayoutModeService).set('phone');
  const http = TestBed.inject(HttpTestingController);
  const fixture = TestBed.createComponent(PortfolioFuturesDetailComponent);
  fixture.detectChanges();
  const el = fixture.nativeElement as HTMLElement;
  return {
    fixture,
    el,
    http,
    navigate,
    text: () => el.textContent!.replace(/\s+/g, ' '),
    months: (months: string[]) => {
      http.expectOne('/api/portfolio/futures/months').flush({ months });
      fixture.detectChanges();
    },
    detailReq: (month: string) => http.expectOne(r => r.url === '/api/portfolio/futures/detail' && r.params.get('month') === month),
  };
}

function loaded(body: FuturesDetail, query: Record<string, string> = {}, phone = false) {
  const s = setup(query, phone);
  s.months([body.month]);
  s.detailReq(body.month).flush(body);
  s.fixture.detectChanges();
  return s;
}

describe('PortfolioFuturesDetailComponent', () => {
  afterEach(() => {
    TestBed.inject(LayoutModeService).set(null);
    TestBed.inject(HttpTestingController).verify();
    TestBed.resetTestingModule();
  });

  it('renders monthly summary with missing-days note', () => {
    const { text, el } = loaded(detail({ summary: summary({ missing_days: 2 }) }));
    const cell = (k: string) => el.querySelector(`[data-testid="summary-${k}"]`)!;
    expect(cell('realized').classList.contains('up')).toBe(true);
    expect(cell('fees').textContent).not.toContain('+');
    expect(cell('fees').classList.contains('up') || cell('fees').classList.contains('down')).toBe(false);
    expect(cell('deposits').textContent).toContain('+');
    expect(cell('deposits').classList.contains('up') || cell('deposits').classList.contains('down')).toBe(false);
    expect(text()).toContain('本月期貨平倉損益');
    expect(text()).toContain('權利金收支');
    expect(text()).toContain('手續費＋期交稅');
    expect(text()).toContain('存提淨額');
    expect(text()).toContain('缺 2 天日對帳單，明細不完整');
    expect(text()).toContain('摘要以月對帳單為準');
    expect(text()).not.toContain('日對帳單加總');
  });

  it('marks daily_sum incomplete', () => {
    const { text } = loaded(detail({ summary: summary({ source: 'daily_sum', complete: false, reason: 'findings' }) }));
    expect(text()).toContain('日對帳單加總');
    expect(text()).toContain('可能不完整');
  });

  it('shows option pnl as 權利金 with sign', () => {
    const opt = fill({ kind: 'option', order_no: 'sA1B2-02', product: '範例選', strike: '21000', right: 'Call', pnl: '-300.0000', action: '新買', price: '6' });
    const { el } = loaded(detail({ days: [day({ fills: [fill(), opt] })] }));
    const rows = Array.from(el.querySelectorAll('tbody tr[data-testid="fill-row"]'));
    expect(rows).toHaveLength(2);
    const optRow = rows.find(r => r.textContent!.includes('範例選'))!;
    expect(optRow.textContent).toContain('權利金');
    expect(optRow.textContent).not.toContain('平倉損益');
    expect(optRow.querySelector('[data-testid="fill-pnl"]')!.classList.contains('down')).toBe(true);
    const futRow = rows.find(r => r.textContent!.includes('範例期貨'))!;
    expect(futRow.textContent).toContain('平倉損益');
    expect(futRow.querySelector('[data-testid="fill-pnl"]')!.classList.contains('up')).toBe(true);
  });

  it('shows a dash for a futures opening fill and keeps 權利金 for option openings', () => {
    const open = fill({ action: '新買', pnl: '0.0000' });
    const optOpen = fill({ kind: 'option', product: '範例選', strike: '21000', right: 'Call', action: '新賣', pnl: '300.0000' });
    const { el } = loaded(detail({ days: [day({ fills: [open, optOpen] })] }));
    const rows = Array.from(el.querySelectorAll('tbody tr[data-testid="fill-row"]'));
    const futRow = rows.find(r => r.textContent!.includes('範例期貨'))!;
    expect(futRow.textContent).not.toContain('平倉損益');
    expect(futRow.querySelector('[data-testid="fill-pnl"]')!.textContent!.trim()).toBe('—');
    const optRow = rows.find(r => r.textContent!.includes('範例選'))!;
    expect(optRow.textContent).toContain('權利金');
    expect(optRow.querySelector('[data-testid="fill-pnl"]')!.classList.contains('up')).toBe(true);
  });

  it('shows a dash on the phone card for a futures opening fill', () => {
    const { el } = loaded(detail({ days: [day({ fills: [fill({ action: '新賣', pnl: '0.0000' })] })] }), {}, true);
    const card = el.querySelector('[data-testid="fill-card"]')!;
    expect(card.textContent).not.toContain('平倉損益');
    expect(card.querySelector('[data-testid="fill-pnl"]')!.textContent!.trim()).toBe('—');
  });

  it('toggles 平倉明細 with arrows, accent text and aria-expanded', () => {
    const closing = fill({ closes: [{ date: '2026-03-02', qty: 1, price: '20400' }] });
    const { el, fixture } = loaded(detail({ days: [day({ fills: [closing] })] }));
    const btn = () => el.querySelector('[data-testid="close-toggle"]') as HTMLButtonElement;
    expect(btn().textContent!.trim()).toBe('▸ 平倉明細');
    expect(btn().getAttribute('aria-expanded')).toBe('false');
    expect(btn().classList.contains('fd-toggle')).toBe(true);
    btn().click();
    fixture.detectChanges();
    expect(btn().textContent!.trim()).toBe('▾ 收合');
    expect(btn().getAttribute('aria-expanded')).toBe('true');
  });

  it('expands closing links', () => {
    const closing = fill({ closes: [{ date: '2026-03-02', qty: 1, price: '20400' }] });
    const { el, fixture, text } = loaded(detail({ days: [day({ fills: [closing] })] }));
    expect(text()).not.toContain('平倉 → 03/02');
    (el.querySelector('[data-testid="close-toggle"]') as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(text()).toContain('平倉 → 03/02 1 口 @ 20400');
  });

  it('no-trade day', () => {
    const { text } = loaded(detail({ days: [day({ no_trade: true, fills: [], fills_total: null })] }));
    expect(text()).toContain('當日無交易');
  });

  it('shows deposits as 入金 and 出金', () => {
    const { text } = loaded(detail({ days: [day({ deposits: [{ direction: 'in', amount: '50000.0000' }, { direction: 'out', amount: '20000.0000' }] })] }));
    expect(text()).toContain('入金');
    expect(text()).toContain('出金');
    expect(text()).toContain('$50,000');
  });

  it('renders one card per fill on a phone', () => {
    const { el } = loaded(detail(), {}, true);
    expect(el.querySelectorAll('table').length).toBe(0);
    expect(el.querySelectorAll('[data-testid="fill-card"]').length).toBe(1);
  });

  describe('positions', () => {
    const positions = (over: Partial<FuturesPositions> = {}): FuturesPositions => ({
      date: '2026-01-28',
      month_end_may_differ: true,
      items: [
        { product: '範例期貨', month: '202603', strike: null, right: null, side: '買', qty: 1, entry_price: '20000', mark_price: '20100', unrealized: '100.0000', premium_value: '0.0000' },
        { product: '範例選', month: '202603', strike: '21000', right: 'Call', side: '賣', qty: 1, entry_price: '6', mark_price: '5', unrealized: '-30.0000', premium_value: '-200.0000' },
        { product: '範例選', month: '202603', strike: '21500', right: 'Put', side: '買', qty: 1, entry_price: '4', mark_price: '5', unrealized: '40.0000', premium_value: '150.0000' },
      ],
      ...over,
    });

    it('positions carried from earlier month', () => {
      const { text } = loaded(detail({ positions: positions() }));
      expect(text()).toContain('留倉：01/28 日對帳單');
      expect(text()).toContain('月底留倉可能不同');
      expect(text()).toContain('未沖銷損益');
      expect(text()).toContain('權利金市值');
    });

    it('has separate 權利金市值 and 未沖銷損益 columns', () => {
      const { el } = loaded(detail({ positions: positions() }));
      const heads = Array.from(el.querySelectorAll('[data-testid="positions-card"] thead th')).map(h => h.textContent!.trim());
      expect(heads.slice(-2)).toEqual(['權利金市值', '未沖銷損益']);
      const rows = Array.from(el.querySelectorAll('[data-testid="positions-card"] tbody tr'));
      const cell = (r: Element, k: string) => r.querySelector(`[data-testid="${k}"]`) as HTMLElement;
      const [fut, sold, bought] = rows;
      expect(cell(fut, 'pos-premium').textContent!.trim()).toBe('—');
      expect(cell(fut, 'pos-unrealized').classList.contains('up')).toBe(true);
      expect(cell(sold, 'pos-premium').textContent).toMatch(/[-−]\$?200/);
      expect(cell(sold, 'pos-premium').classList.contains('up')).toBe(false);
      expect(cell(sold, 'pos-premium').classList.contains('down')).toBe(false);
      expect(cell(sold, 'pos-unrealized').classList.contains('down')).toBe(true);
      expect(cell(bought, 'pos-premium').textContent).not.toMatch(/[-−]/);
      expect(cell(bought, 'pos-premium').classList.contains('up')).toBe(false);
      expect(cell(bought, 'pos-unrealized').classList.contains('up')).toBe(true);
    });

    it('hides the card when positions is null', () => {
      const { text, el } = loaded(detail({ positions: null }));
      expect(text()).not.toContain('留倉：');
      expect(el.querySelector('[data-testid="positions-card"]')).toBeNull();
    });
  });

  it('defaults to newest month and honours query param', () => {
    const a = setup();
    a.months(['2026-03', '2026-02']);
    a.detailReq('2026-03').flush(detail());
    TestBed.inject(HttpTestingController).verify();
    TestBed.resetTestingModule();

    const b = setup({ month: '2026-02' });
    b.months(['2026-03', '2026-02']);
    b.detailReq('2026-02').flush(detail({ month: '2026-02' }));
  });

  it('changing the picker reloads and updates the query param with replaceUrl', () => {
    const s = setup();
    s.months(['2026-03', '2026-02']);
    s.detailReq('2026-03').flush(detail());
    s.fixture.detectChanges();
    const select = s.el.querySelector('[data-testid="month-picker"]') as HTMLSelectElement;
    select.value = '2026-02';
    select.dispatchEvent(new Event('change'));
    s.detailReq('2026-02').flush(detail({ month: '2026-02' }));
    expect(s.navigate).toHaveBeenCalledWith([], expect.objectContaining({ queryParams: { month: '2026-02' }, replaceUrl: true }));
  });

  describe('states', () => {
    it('404 month shows no-statement copy', () => {
      const s = setup({ month: '2025-01' });
      s.months(['2026-03']);
      s.detailReq('2025-01').flush({ detail: 'x' }, { status: 404, statusText: 'Not Found' });
      s.fixture.detectChanges();
      expect(s.text()).toContain('這個月份沒有期貨對帳單');
      expect(s.text()).not.toContain('期貨資料載入失敗');
      expect(s.el.querySelector('[data-testid="month-picker"]')).not.toBeNull();
    });

    it('500: a red banner 期貨資料載入失敗 with 重試; 重試 asks for the same month again', () => {
      const s = setup();
      s.months(['2026-03']);
      s.detailReq('2026-03').flush('boom', { status: 500, statusText: 'Server Error' });
      s.fixture.detectChanges();
      const banner = s.el.querySelector('app-banner .banner--error');
      expect(banner?.querySelector('.banner-title')?.textContent?.trim()).toBe('期貨資料載入失敗');
      expect(s.el.querySelector('[data-testid="month-picker"]')).not.toBeNull();
      const retry = banner!.querySelector<HTMLButtonElement>('.banner-action')!;
      expect(retry.textContent!.trim()).toBe('重試');
      retry.click();
      s.fixture.detectChanges();
      s.detailReq('2026-03').flush(detail());
      s.fixture.detectChanges();
      expect(s.el.querySelector('app-banner')).toBeNull();
      expect(s.text()).toContain('本月摘要');
    });

    it('malformed month param falls back to the newest month', () => {
      const s = setup({ month: '2026-13' });
      s.months(['2026-03', '2026-02']);
      s.detailReq('2026-03').flush(detail());
      s.fixture.detectChanges();
      expect(s.el.querySelector('[data-testid="fd-error"]')).toBeNull();
    });

    it('no months: 尚無期貨對帳單, one line on what will show, a link 到匯入頁 › to the 永豐期貨 card; no detail request', () => {
      const s = setup();
      s.months([]);
      const empty = s.el.querySelector('[data-testid="fd-empty"]')!;
      expect(empty.querySelector('b')?.textContent?.trim()).toBe('尚無期貨對帳單');
      expect(empty.querySelector('p')?.textContent?.trim()).toBe('永豐期貨對帳單匯入後，這裡會按月份列出每天的成交、出入金和留倉。');
      const link = empty.querySelector<HTMLAnchorElement>('a')!;
      expect(link.textContent!.replace(/\s+/g, ' ').trim()).toBe('到匯入頁 ›');
      expect(link.querySelector('[aria-hidden="true"]')?.textContent).toBe('›');
      expect(link.getAttribute('href')).toBe('/portfolio/import#sinopac-futures');
      s.http.expectNone(r => r.url === '/api/portfolio/futures/detail');
    });

    it('months request failure: the red banner with 重試; 重試 asks for the months again, then the newest month', () => {
      const s = setup();
      s.http.expectOne('/api/portfolio/futures/months').flush('boom', { status: 500, statusText: 'Server Error' });
      s.fixture.detectChanges();
      const banner = s.el.querySelector('app-banner .banner--error')!;
      expect(banner.querySelector('.banner-title')?.textContent?.trim()).toBe('期貨資料載入失敗');
      expect(s.el.querySelector('[data-testid="month-picker"]')).toBeNull();
      banner.querySelector<HTMLButtonElement>('.banner-action')!.click();
      s.fixture.detectChanges();
      s.months(['2026-03']);
      s.detailReq('2026-03').flush(detail());
      s.fixture.detectChanges();
      expect(s.el.querySelector('app-banner')).toBeNull();
      expect(s.el.querySelector('[data-testid="month-picker"]')).not.toBeNull();
    });
  });
});
