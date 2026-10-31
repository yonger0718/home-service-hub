import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, TestRequest, provideHttpClientTesting } from '@angular/common/http/testing';
import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { BreakdownQuery, ReportCompare, ReportDimension, ReportMetric, TrendQuery } from '../../../models/reports.model';
import { LayoutMode, LayoutModeService, MATCH_MEDIA } from '../../../services/layout-mode.service';
import { NavBadgeService } from '../../../services/nav-badge.service';
import { ReportsService } from '../../../services/reports.service';
import { AccountingReportsComponent } from './reports';
import { sampleBreakdown, sampleTrend } from './sample-report-data';

@Component({ template: '' })
class BlankPage {}

const BREAKDOWN = '/api/accounting/reports/breakdown';
const TREND = '/api/accounting/reports/trend';

function breakdownQuery(request: TestRequest): BreakdownQuery {
  const params = request.request.params;
  return {
    date_from: params.get('date_from')!,
    date_to: params.get('date_to')!,
    metric: (params.get('metric') ?? 'expense') as ReportMetric,
    group_by: params.get('group_by') as ReportDimension,
    split_by: (params.get('split_by') as ReportDimension | null) ?? undefined,
    compare: (params.get('compare') as ReportCompare | null) ?? undefined,
  };
}

function trendQuery(request: TestRequest): TrendQuery {
  const params = request.request.params;
  return {
    date_from: params.get('date_from')!,
    date_to: params.get('date_to')!,
    granularity: 'month',
    series: params.getAll('series')?.map(Number) ?? undefined,
  };
}

describe('AccountingReportsComponent', () => {
  let http: HttpTestingController;

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date(2026, 9, 31, 10, 0));
    TestBed.configureTestingModule({
      providers: [
        provideRouter([
          { path: 'accounting/reports', component: AccountingReportsComponent },
          { path: '**', component: BlankPage },
        ]),
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: MATCH_MEDIA, useValue: null },
        { provide: NavBadgeService, useValue: { badges: signal({}) } },
      ],
    });
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => {
    // A failed read cancels the two others in its forkJoin; those stay listed as cancelled.
    http.verify({ ignoreCancelled: true });
    vi.useRealTimers();
  });

  const live = (requests: TestRequest[]) => requests.filter(request => !request.cancelled);

  interface Pending {
    breakdowns: TestRequest[];
    trends: TestRequest[];
  }

  /** Takes the open report reads (`match` removes them from the controller, so keep what it returns). */
  function pending(): Pending {
    return { breakdowns: live(http.match(r => r.url === BREAKDOWN)), trends: live(http.match(r => r.url === TREND)) };
  }

  /** Answers the reads with the handoff's sample month (as the real API will, for October 2026). */
  function answer({ breakdowns, trends }: Pending): void {
    for (const request of breakdowns) request.flush(sampleBreakdown(breakdownQuery(request)));
    for (const request of trends) request.flush(sampleTrend(trendQuery(request)));
  }

  const flushSample = () => answer(pending());

  async function open(url: string, mode: LayoutMode = 'panes'): Promise<{ harness: RouterTestingHarness; el: HTMLElement }> {
    TestBed.inject(LayoutModeService).set(mode);
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(url);
    harness.detectChanges();
    return { harness, el: harness.routeNativeElement as HTMLElement };
  }

  const texts = (el: Element, selector: string) => Array.from(el.querySelectorAll(selector)).map(node => node.textContent?.replace(/\s+/g, ' ').trim());

  it('asks for expense (with the previous period and last year) and income over the month in the URL', async () => {
    await open('/accounting/reports?month=2026-10');

    const reads = pending();
    expect(reads.breakdowns.map(breakdownQuery)).toEqual([
      { date_from: '2026-10-01', date_to: '2026-10-31', metric: 'expense', group_by: 'category', split_by: 'subcategory', compare: 'previous' },
      { date_from: '2026-10-01', date_to: '2026-10-31', metric: 'expense', group_by: 'category', split_by: undefined, compare: 'last_year' },
      { date_from: '2026-10-01', date_to: '2026-10-31', metric: 'income', group_by: 'category', split_by: 'subcategory', compare: 'previous' },
    ]);
    expect(reads.trends.map(trendQuery)).toEqual([{ date_from: '2025-11-01', date_to: '2026-10-31', granularity: 'month', series: undefined }]);
    expect(reads.trends[0].request.params.get('metric')).toBe('expense');
    answer(reads);
  });

  it('shows the October header: 支出 with both comparisons, 收入, 結餘 with the savings rate, 日均支出', async () => {
    const { harness, el } = await open('/accounting/reports?month=2026-10');
    flushSample();
    harness.detectChanges();

    expect(el.querySelector('h1')?.textContent?.trim()).toBe('報表');
    expect(el.querySelector('.page-subtitle')?.textContent?.trim()).toBe('以台幣計 · 退款抵減支出 · 轉帳不計入');
    expect(el.querySelector('.rp-period-label')?.textContent?.trim()).toBe('2026 年 10 月');
    expect(texts(el, '.rp-figure-label')).toEqual(['支出', '收入', '結餘', '日均支出']);
    expect(texts(el, '.rp-figure-value')).toEqual(['$41,890', '$68,126', '+$26,236', '$1,351']);
    expect(texts(el, '.rp-figure-foot')).toEqual(['比 9 月 ▲ 2,282 · 比去年 10 月 ▼ 2,160', '薪資 68,000 · 回饋 126', '儲蓄率 38.5%', '共 31 天']);
    expect(el.querySelector('.rp-sample')).toBeNull();
  });

  it('lists the categories on gross with the refund as its own line, and details the selected one', async () => {
    const { harness, el } = await open('/accounting/reports?month=2026-10');
    flushSample();
    harness.detectChanges();

    expect(texts(el, '.ov-row .ov-name')).toEqual(['居家3 筆', '餐飲46 筆', '交通15 筆', '購物9 筆', '娛樂5 筆', '訂閱5 筆', '其他4 筆']);
    expect(texts(el, '.ov-row .ov-amount')).toEqual(['20,250', '9,860', '4,110', '3,980', '2,050', '1,078', '1,052']);
    expect(el.querySelector('.ov-donut-total')?.textContent).toBe('42,380');
    expect(el.querySelector('.ov-refund')?.textContent?.replace(/\s+/g, '')).toBe('退款（購物）−490');
    expect(el.querySelector('.ov-donut')?.getAttribute('aria-label')).toMatch(/^支出類別占比：居家 47\.8%、餐飲 23\.3%/);

    // The largest row is selected first; 餐飲 on a click.
    expect(el.querySelector('.ov-detail h2')?.textContent?.trim()).toBe('居家 · 細項');
    (Array.from(el.querySelectorAll<HTMLButtonElement>('.ov-row')).find(row => row.textContent?.includes('餐飲')))!.click();
    harness.detectChanges();
    expect(el.querySelector('.ov-detail h2')?.textContent?.trim()).toBe('餐飲 · 細項');
    expect(texts(el, '.ov-split-name')).toEqual(['晚餐11 筆', '午餐19 筆', '咖啡12 筆', '早餐9 筆', '宵夜5 筆']);
    expect(texts(el, '.ov-stat')).toEqual(['比 9 月 ▲ 1,220', '每筆平均 214', '占支出 23.3%']);
    expect(el.querySelector('.ov-stat b')?.className).toBe('more');
    const drill = el.querySelector<HTMLAnchorElement>('.ov-drill')!;
    expect(drill.textContent?.trim()).toBe('在記帳記錄看這 46 筆 ›');
    expect(drill.getAttribute('href')).toBe('/accounting?month=2026-10&category_id=2');
  });

  it('switches to 收入 and back without a new read', async () => {
    const { harness, el } = await open('/accounting/reports?month=2026-10');
    flushSample();
    harness.detectChanges();

    Array.from(el.querySelectorAll<HTMLButtonElement>('.ov-share app-seg-toggle button')).find(b => b.textContent?.trim() === '收入')!.click();
    harness.detectChanges();
    expect(texts(el, '.ov-row .ov-name')).toEqual(['薪資1 筆', '回饋3 筆']);
    expect(el.querySelector('.ov-donut-label')?.textContent).toBe('收入');
    expect(el.querySelector('.ov-refund')).toBeNull();
    expect(texts(el, '.ov-stat').at(-1)).toBe('占收入 99.8%');
    http.expectNone(() => true);
  });

  it('renders the trend tab from ?tab=trend: twelve bars, the average, and the comparison table with — for no change', async () => {
    const { harness, el } = await open('/accounting/reports?month=2026-10&tab=trend');
    flushSample();
    harness.detectChanges();

    expect(el.querySelector('.rp-tab.on')?.textContent?.trim()).toBe('趨勢與比較');
    expect(el.querySelector('app-report-overview')).toBeNull();
    expect(el.querySelectorAll('.tr-bar')).toHaveLength(12);
    expect(el.querySelector('.tr-bar.last')).not.toBeNull();
    expect(texts(el, '.tr-label')).toEqual(['11月', '12月', '1月', '2月', '3月', '4月', '5月', '6月', '7月', '8月', '9月', '10月']);
    expect(el.querySelector('.tr-average-label')?.textContent).toBe('12 個月平均 40,608');
    expect(el.querySelector('.tr-chart')?.getAttribute('aria-label')).toMatch(/^全部支出近 12 個月：11月 38,900、/);
    expect(texts(el, '.tr-tick')).toEqual(['0', '25k', '50k']);

    expect(el.querySelector('.tr-card:nth-of-type(2) h2')?.textContent?.trim()).toBe('比較 · 10 月 對 9 月、對去年 10 月');
    expect(texts(el, '.tr-table thead th')).toEqual(['類別', '本月', '上月', '月增減', '去年同月', '年增減']);
    const rows = Array.from(el.querySelectorAll('.tr-table tbody tr'));
    expect(rows.map(row => row.querySelector('th')?.textContent?.trim())).toEqual(['居家', '餐飲', '交通', '購物', '娛樂', '訂閱', '其他', '合計']);
    const subscription = rows.find(row => row.textContent?.includes('訂閱'))!;
    expect(texts(subscription, 'td')).toEqual(['1,078', '1,078', '—', '880', '▲ 198 (22.5%)']);
    expect(subscription.querySelectorAll('td')[2].classList.contains('same')).toBe(true);
    expect(subscription.querySelectorAll('td')[2].getAttribute('data-label')).toBe('比 9 月');
    expect(subscription.querySelectorAll('td')[4].getAttribute('data-label')).toBe('比去年 10 月');
    const shopping = rows.find(row => row.textContent?.includes('購物'))!;
    expect(texts(shopping, 'td')).toEqual(['3,490', '3,410', '▲ 80 (2.3%)', '5,230', '▼ 1,740 (33.3%)']);
    expect(shopping.querySelectorAll('td')[4].classList.contains('less')).toBe(true);
    const total = rows.at(-1)!;
    expect(total.classList.contains('total')).toBe(true);
    expect(texts(total, 'td')).toEqual(['41,890', '39,608', '▲ 2,282 (5.8%)', '44,050', '▼ 2,160 (4.9%)']);
  });

  it('draws the phone chart without a value axis, in its own viewBox', async () => {
    const { harness, el } = await open('/accounting/reports?month=2026-10&tab=trend', 'phone');
    flushSample();
    harness.detectChanges();

    const chart = el.querySelector('.tr-chart')!;
    expect(chart.getAttribute('viewBox')).toBe('0 0 358 200');
    expect(chart.classList.contains('compact')).toBe(true);
    expect(el.querySelectorAll('.tr-tick')).toHaveLength(0);
    expect(el.querySelectorAll('.tr-bar')).toHaveLength(12);
    expect(el.querySelector('.tr-average-label')?.textContent).toBe('12 個月平均 40,608');
    expect(texts(el, '.tr-table thead th')).toEqual(['類別', '本月', '上月', '月增減', '去年同月', '年增減']);
  });

  it('re-reads the trend for one category when its chip is picked', async () => {
    const { harness, el } = await open('/accounting/reports?month=2026-10&tab=trend');
    flushSample();
    harness.detectChanges();

    expect(texts(el, '.tr-chips button')).toEqual(['全部支出', '居家', '餐飲', '交通', '購物（扣退款）', '娛樂', '訂閱', '其他']);
    Array.from(el.querySelectorAll<HTMLButtonElement>('.tr-chips button')).find(b => b.textContent?.trim() === '餐飲')!.click();
    harness.detectChanges();

    const reads = pending();
    expect(reads.breakdowns).toEqual([]);
    expect(reads.trends.map(request => request.request.params.getAll('series'))).toEqual([['2']]);
    answer(reads);
    harness.detectChanges();
    expect(el.querySelector('.tr-head h2')?.textContent?.trim()).toBe('每月支出 · 餐飲 · 近 12 個月');
    expect(el.querySelector('.tr-chips button[aria-checked="true"]')?.textContent?.trim()).toBe('餐飲');
    expect(el.querySelector('.tr-average-label')?.textContent).toBe('12 個月平均 9,704');
  });

  it('moves the month with ‹ › and the unit with 月 / 年, keeping the tab in the URL', async () => {
    const { harness, el } = await open('/accounting/reports?month=2026-10&tab=trend');
    flushSample();
    harness.detectChanges();
    const router = TestBed.inject(Router);

    el.querySelector<HTMLButtonElement>('.rp-prev')!.click();
    await harness.fixture.whenStable();
    harness.detectChanges();
    expect(router.url).toBe('/accounting/reports?month=2026-09&tab=trend');
    expect(el.querySelector('.rp-period-label')?.textContent?.trim()).toBe('2026 年 9 月');
    const september = pending();
    expect(september.breakdowns.map(r => r.request.params.get('date_to'))).toEqual(['2026-09-30', '2026-09-30', '2026-09-30']);
    expect(september.trends.map(r => r.request.params.get('date_from'))).toEqual(['2025-10-01']);
    answer(september);

    Array.from(el.querySelectorAll<HTMLButtonElement>('.rp-period app-seg-toggle button')).find(b => b.textContent?.trim() === '年')!.click();
    await harness.fixture.whenStable();
    harness.detectChanges();
    expect(router.url).toBe('/accounting/reports?tab=trend&year=2026');
    expect(el.querySelector('.rp-period-label')?.textContent?.trim()).toBe('2026 年');
    const year = pending();
    expect(year.breakdowns.map(r => r.request.params.get('date_from'))).toEqual(['2026-01-01', '2026-01-01', '2026-01-01']);
    expect(year.trends).toHaveLength(1);
    answer(year);
    harness.detectChanges();
    expect(texts(el, '.rp-figure-foot')[0]).toBe('比去年 ▲ 2,282 · 比前年 ▼ 2,160');
    expect(texts(el, '.tr-table thead th')).toEqual(['類別', '今年', '去年', '增減', '前年', '年增減']);
  });

  it('links each tab through the URL and keeps the figures above every tab', async () => {
    const { harness, el } = await open('/accounting/reports?month=2026-10');
    flushSample();
    harness.detectChanges();

    Array.from(el.querySelectorAll<HTMLButtonElement>('.rp-tab')).find(b => b.textContent?.trim() === '排行')!.click();
    await harness.fixture.whenStable();
    harness.detectChanges();
    expect(TestBed.inject(Router).url).toBe('/accounting/reports?month=2026-10&tab=rank');
    expect(el.querySelector('.rp-tab.on')?.getAttribute('aria-selected')).toBe('true');
    expect(el.querySelector('.rp-soon')).not.toBeNull();
    expect(texts(el, '.rp-figure-value')[0]).toBe('$41,890');
    http.expectNone(() => true);
  });

  it('falls back to the sample month when the report API is not there yet, and says so', async () => {
    const { harness, el } = await open('/accounting/reports?month=2026-10');
    for (const request of http.match(() => true)) request.flush({ detail: 'Not Found' }, { status: 404, statusText: 'Not Found' });
    harness.detectChanges();

    expect(TestBed.inject(ReportsService).sample()).toBe(true);
    expect(el.querySelector('.rp-sample')?.textContent).toContain('示範資料');
    expect(texts(el, '.rp-figure-value')[0]).toBe('$41,890');
    expect(el.querySelector('app-report-overview')).not.toBeNull();
  });

  it('shows the error state with 重試 when a read fails for another reason', async () => {
    const { harness, el } = await open('/accounting/reports?month=2026-10');
    const failure = { status: 500, statusText: 'Server Error' };
    const first = pending();
    first.breakdowns[0].flush({ detail: 'boom' }, failure);
    first.trends[0].flush({ detail: 'boom' }, failure);
    harness.detectChanges();

    expect(el.querySelector('.rp-error')?.textContent).toContain('報表讀取失敗');
    expect(el.querySelector('.rp-figure')).toBeNull();
    el.querySelector<HTMLButtonElement>('.rp-retry')!.click();
    const retried = pending();
    expect(retried.breakdowns).toHaveLength(3);
    expect(retried.trends).toHaveLength(1);
    answer(retried);
    harness.detectChanges();
    expect(el.querySelector('.rp-error')).toBeNull();
    expect(texts(el, '.rp-figure-value')[0]).toBe('$41,890');
  });
});
