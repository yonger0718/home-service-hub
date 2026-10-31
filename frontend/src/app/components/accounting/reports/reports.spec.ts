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
import { SampleBudgetStore, SampleSavedReportStore, sampleBreakdown, sampleTrend } from './sample-report-data';

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
    ...(params.has('include_excluded_projects') ? { include_excluded_projects: params.get('include_excluded_projects') === 'true' } : {}),
  };
}

function trendQuery(request: TestRequest): TrendQuery {
  const params = request.request.params;
  const numbers = (name: string) => params.getAll(name)?.map(Number) ?? undefined;
  return {
    date_from: params.get('date_from')!,
    date_to: params.get('date_to')!,
    granularity: 'month',
    series: numbers('series'),
    merchant: params.get('merchant'),
    tag: params.getAll('tag') ?? undefined,
    counterparty_id: params.has('counterparty_id') ? Number(params.get('counterparty_id')) : null,
    account_id: numbers('account_id'),
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
    http.expectOne(r => r.url === '/api/accounting/reports/saved').flush(new SampleSavedReportStore().list());
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
    expect(reads.trends.map(trendQuery)).toEqual([
      { date_from: '2025-11-01', date_to: '2026-10-31', granularity: 'month', series: undefined, merchant: null, tag: undefined, counterparty_id: null, account_id: undefined },
    ]);
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

    Array.from(el.querySelectorAll<HTMLButtonElement>('.rp-tab')).find(b => b.textContent?.trim() === '專案與預算')!.click();
    await harness.fixture.whenStable();
    harness.detectChanges();
    expect(TestBed.inject(Router).url).toBe('/accounting/reports?month=2026-10&tab=plan');
    expect(el.querySelector('.rp-tab.on')?.getAttribute('aria-selected')).toBe('true');
    expect(el.querySelector('app-report-plan')).not.toBeNull();
    // The figures above the tabs are not read again; only the tab's own reads go out.
    expect(texts(el, '.rp-figure-value')[0]).toBe('$41,890');
    const reads = pending();
    expect(reads.breakdowns.map(r => r.request.params.get('group_by'))).toEqual(['project']);
    expect(reads.trends).toEqual([]);
    answer(reads);
    for (const request of http.match(() => true)) request.flush(request.request.url.endsWith('/status') ? new SampleBudgetStore().status('2026-10', '2026-10-31') : []);
  });

  describe('排行', () => {
    const rankRows = (el: Element) => Array.from(el.querySelectorAll<HTMLButtonElement>('.rk-row'));
    const rankNames = (el: Element) => rankRows(el).map(row => row.querySelector('.rk-name')?.textContent?.trim());

    /** Opens 排行 on October: the page reads, then the ranking, then the first row's six months. */
    async function openRank(url = '/accounting/reports?month=2026-10&tab=rank') {
      const opened = await open(url);
      const first = pending();
      expect(first.breakdowns).toHaveLength(4);
      const ranking = first.breakdowns[3];
      expect(breakdownQuery(ranking)).toMatchObject({ date_from: '2026-10-01', date_to: '2026-10-31', metric: 'expense' });
      answer(first);
      opened.harness.detectChanges();
      return { ...opened, dimension: ranking.request.params.get('group_by') };
    }

    it('ranks merchants with bars, mutes （無商家） and the fold, and opens the largest row in the panel', async () => {
      const { harness, el, dimension } = await openRank();
      expect(dimension).toBe('merchant');
      // The first row's six-month bars.
      const spark = pending();
      expect(spark.trends.map(r => [r.request.params.get('merchant'), r.request.params.get('date_from'), r.request.params.get('date_to')])).toEqual([['房東', '2026-05-01', '2026-10-31']]);
      answer(spark);
      harness.detectChanges();

      expect(rankNames(el)).toEqual(['房東', '鼎泰豐', '中油', '全聯', '台電', '（無商家）', 'UNIQLO', '路易莎', '其他']);
      const rows = rankRows(el);
      expect(rows[0].classList.contains('on')).toBe(true);
      expect(rows[5].classList.contains('muted')).toBe(true);
      expect(rows[8].classList.contains('muted')).toBe(true);
      expect(rows[1].querySelector('.rk-title small')?.textContent?.trim()).toBe('3 筆 · 平均 980');
      expect((rows[1].querySelector('.rk-bar span') as HTMLElement).style.width).toBe('16.3%');
      expect(el.querySelector('.rk-foot')?.textContent?.replace(/\s+/g, ' ').trim()).toBe('共 7 家 · 顯示前 200 · 點一列看平均、中位數、間隔');
      expect(el.querySelector('.rk-panel h2')?.textContent?.trim()).toBe('房東');
      expect(el.querySelector('.rk-panel-head small')?.textContent?.trim()).toBe('商家 · 2026 年 10 月');
      expect(el.querySelectorAll('.rk-spark-bar')).toHaveLength(6);
      expect(texts(el, '.rk-spark-label')).toEqual(['5月', '6月', '7月', '8月', '9月', '10月']);
    });

    it('shows 路易莎 statistics on selection: 12 筆, 平均 86, 中位數 85, 最大 120, 最小 65, 平均間隔 2.5 天', async () => {
      const { harness, el } = await openRank();
      answer(pending());
      harness.detectChanges();

      rankRows(el).find(row => row.textContent?.includes('路易莎'))!.click();
      harness.detectChanges();
      const spark = pending();
      expect(spark.breakdowns).toEqual([]);
      expect(spark.trends.map(r => r.request.params.get('merchant'))).toEqual(['路易莎']);
      answer(spark);
      harness.detectChanges();

      expect(el.querySelector('.rk-panel h2')?.textContent?.trim()).toBe('路易莎');
      expect(texts(el, '.rk-stat')).toEqual([
        '合計 1,035', '筆數 12', '平均 86', '中位數 85', '最大 120', '最小 65', '平均間隔 2.5 天', '占支出 2.5%', '近 6 月月均 992',
      ]);
      expect(el.querySelector('.rk-spark-chart')?.getAttribute('aria-label')).toMatch(/^路易莎 近 6 個月支出：5月 880、/);
      const drill = el.querySelector<HTMLAnchorElement>('.rk-drill')!;
      expect(drill.textContent?.trim()).toBe('在記帳記錄看這 12 筆 ›');
      expect(drill.getAttribute('href')).toBe('/accounting?month=2026-10&merchant=%E8%B7%AF%E6%98%93%E8%8E%8E');
    });

    it('keeps （無商家） to the plain figures: no six months, no drill link', async () => {
      const { harness, el } = await openRank();
      answer(pending());
      harness.detectChanges();

      rankRows(el)[5].click();
      harness.detectChanges();
      http.expectNone(() => true);
      expect(el.querySelector('.rk-panel h2')?.textContent?.trim()).toBe('（無商家）');
      expect(texts(el, '.rk-stat')).toEqual(['合計 1,240', '筆數 9', '平均 138', '占支出 3.0%']);
      expect(el.querySelector('.rk-spark')).toBeNull();
      expect(el.querySelector('.rk-drill')).toBeNull();
    });

    it('switches the dimension through the URL, keeps the period, and notes overlapping tag totals', async () => {
      const { harness, el } = await openRank();
      answer(pending());
      harness.detectChanges();

      Array.from(el.querySelectorAll<HTMLButtonElement>('.rk-head app-seg-toggle button')).find(b => b.textContent?.trim() === '標籤')!.click();
      await harness.fixture.whenStable();
      harness.detectChanges();
      expect(TestBed.inject(Router).url).toBe('/accounting/reports?month=2026-10&tab=rank&dim=tag');
      const reads = pending();
      expect(reads.breakdowns.map(breakdownQuery)).toEqual([{ date_from: '2026-10-01', date_to: '2026-10-31', metric: 'expense', group_by: 'tag', split_by: undefined, compare: undefined }]);
      expect(reads.trends).toEqual([]);
      answer(reads);
      harness.detectChanges();
      // The first tag row is （無標籤）: no six months for it.
      expect(pending().trends).toEqual([]);

      expect(rankNames(el)).toEqual(['（無標籤）', '家用', '朋友', '出差', '生日']);
      expect(el.querySelector('.rk-foot')?.textContent).toContain('共 4 個標籤 · 一筆可有多個標籤，合計會大於支出');
      expect(el.querySelector('.rk-panel-head small')?.textContent?.trim()).toBe('標籤 · 2026 年 10 月');

      rankRows(el)[1].click();
      harness.detectChanges();
      const spark = pending();
      expect(spark.trends.map(r => r.request.params.getAll('tag'))).toEqual([['家用']]);
      answer(spark);
      harness.detectChanges();
      expect(el.querySelector<HTMLAnchorElement>('.rk-drill')?.getAttribute('href')).toBe('/accounting?month=2026-10&tag=%E5%AE%B6%E7%94%A8');
    });

    it('filters the six months by account id for 帳戶 and offers no link for 對象', async () => {
      const { harness, el } = await openRank('/accounting/reports?month=2026-10&tab=rank&dim=account');
      const spark = pending();
      expect(spark.trends.map(r => r.request.params.getAll('account_id'))).toEqual([['1']]);
      answer(spark);
      harness.detectChanges();
      expect(el.querySelector('.rk-panel h2')?.textContent?.trim()).toBe('玉山銀行');
      expect(el.querySelector<HTMLAnchorElement>('.rk-drill')?.getAttribute('href')).toBe('/accounting?month=2026-10&account_id=1');

      Array.from(el.querySelectorAll<HTMLButtonElement>('.rk-head app-seg-toggle button')).find(b => b.textContent?.trim() === '對象')!.click();
      await harness.fixture.whenStable();
      harness.detectChanges();
      answer(pending());
      harness.detectChanges();
      rankRows(el)[1].click();
      harness.detectChanges();
      const counterparty = pending();
      expect(counterparty.trends.map(r => r.request.params.get('counterparty_id'))).toEqual(['2']);
      answer(counterparty);
      harness.detectChanges();
      expect(el.querySelector('.rk-panel h2')?.textContent?.trim()).toBe('媽媽');
      expect(el.querySelector('.rk-drill')).toBeNull();
    });
  });

  describe('專案與預算', () => {
    const BUDGETS = '/api/accounting/reports/budgets';
    const PROJECTS = [
      { id: 1, name: '東京旅行', is_archived: true, sort_order: 0, moze_id: null, exclude_from_reports: true },
      { id: 2, name: '客廳整理', is_archived: false, sort_order: 1, moze_id: null, exclude_from_reports: false },
    ];
    const ROOTS = [
      { id: 1, kind: 'expense', parent_id: null, name: '居家', icon: null, color: '#123456', sort_order: 0, is_hidden: false, default_account_id: null, default_project_id: null, children: [] },
      { id: 2, kind: 'expense', parent_id: null, name: '餐飲', icon: null, color: null, sort_order: 1, is_hidden: false, default_account_id: null, default_project_id: null, children: [] },
    ];

    function flushStatus(store = new SampleBudgetStore()) {
      const status = http.expectOne(r => r.url === `${BUDGETS}/status`);
      expect(status.request.params.get('month')).toBe('2026-10');
      status.flush(store.status('2026-10', '2026-10-31'));
      return store;
    }

    async function openPlan() {
      const opened = await open('/accounting/reports?month=2026-10&tab=plan');
      const reads = pending();
      expect(reads.breakdowns).toHaveLength(4);
      expect(breakdownQuery(reads.breakdowns[3])).toEqual({ date_from: '2000-01-01', date_to: '2026-10-31', metric: 'expense', group_by: 'project', split_by: 'category', compare: undefined, include_excluded_projects: true });
      answer(reads);
      const store = flushStatus();
      http.expectOne('/api/accounting/projects').flush(PROJECTS);
      const roots = http.expectOne(r => r.url === '/api/accounting/categories');
      expect(roots.request.params.get('kind')).toBe('expense');
      roots.flush(ROOTS);
      opened.harness.detectChanges();
      return { ...opened, store };
    }

    it('draws the month\'s budgets with the time marker, 剩 N and 超出 N, and the project cards', async () => {
      const { el } = await openPlan();

      expect(el.querySelector('.pl-budgets h2')?.textContent?.trim()).toBe('10 月預算');
      expect(el.querySelector('.pl-sub')?.textContent?.trim()).toBe('已過 31 / 31 天 · 直線＝時間進度');
      const budgets = Array.from(el.querySelectorAll('.pl-budget'));
      expect(budgets.map(b => b.querySelector('.pl-budget-name')?.textContent?.trim())).toEqual(['餐飲', '交通', '購物', '娛樂', '全部支出']);
      expect(budgets.map(b => b.querySelector('.pl-budget-figures')?.textContent?.replace(/\s+/g, ''))).toEqual(['9,860/10,000', '4,110/5,000', '3,490/3,000', '2,050/2,500', '41,890/45,000']);
      expect(budgets.map(b => b.querySelector('.pl-note')?.textContent?.trim())).toEqual(['剩 140', '剩 890', '超出 490（116.3%）', '剩 450', '剩 3,110']);
      const shopping = budgets[2];
      expect(shopping.classList.contains('over')).toBe(true);
      expect((shopping.querySelector('.pl-fill') as HTMLElement).style.width).toBe('100%');
      expect((shopping.querySelector('.pl-marker') as HTMLElement).style.left).toBe('100%');
      expect(budgets[4].classList.contains('total')).toBe(true);

      const cards = Array.from(el.querySelectorAll('.pl-project'));
      expect(cards.map(c => c.querySelector('.pl-project-title b')?.textContent?.trim())).toEqual(['東京旅行', '客廳整理']);
      expect(cards[0].querySelector('.pl-project-title small')?.textContent?.trim()).toBe('10/10 – 10/15 · 6 天 · 日均 4,143 · 不計入月報');
      expect(cards[0].querySelector('.pl-tag')?.textContent?.trim()).toBe('已結束');
      expect(cards[0].querySelector('.pl-project-total')?.textContent?.replace(/\s+/g, ' ').trim()).toBe('$24,860 預算 25,000 · 用了 99.4%');
      expect(texts(cards[0], '.pl-legend > span')).toEqual(['機票 9,800', '住宿 8,400', '餐飲 3,960', '交通 1,420', '購物 1,280']);
      expect(cards[1].querySelector('.pl-project-title small')?.textContent?.trim()).toBe('09/01 起 · 進行中');
      expect(cards[1].querySelector('.pl-tag')?.textContent?.trim()).toBe('進行中');
    });

    it('edits amounts from the month on, adds a budget for a category without one, and removes one', async () => {
      const { harness, el, store } = await openPlan();

      el.querySelector<HTMLButtonElement>('.pl-edit')!.click();
      harness.detectChanges();
      const inputs = Array.from(el.querySelectorAll<HTMLInputElement>('.pl-edit-row .pl-amount:not(.pl-new-amount)'));
      expect(inputs.map(input => input.value)).toEqual(['10000', '5000', '3000', '2500', '45000', '25000', '20000']);
      // Only 居家 is left for a new category budget; both projects already have one.
      expect(texts(el, '.pl-new-scope option')).toEqual(['新增預算…', '居家']);

      inputs[0].value = '12000';
      inputs[0].dispatchEvent(new Event('input'));
      harness.detectChanges();
      el.querySelector<HTMLButtonElement>('.pl-save')!.click();
      const put = http.expectOne(r => r.method === 'PUT' && r.url === `${BUDGETS}/1`);
      expect(put.request.body).toEqual({ amount: '12000', effective_from: '2026-10' });
      put.flush(store.update(1, put.request.body));
      flushStatus(store);
      harness.detectChanges();
      expect(el.querySelector('.pl-editor')).toBeNull();
      expect(el.querySelector('.pl-budget .pl-budget-figures')?.textContent?.replace(/\s+/g, '')).toBe('9,860/12,000');

      el.querySelector<HTMLButtonElement>('.pl-edit')!.click();
      harness.detectChanges();
      const scope = el.querySelector<HTMLSelectElement>('.pl-new-scope')!;
      scope.value = 'category:1';
      scope.dispatchEvent(new Event('change'));
      const amount = el.querySelector<HTMLInputElement>('.pl-new-amount')!;
      amount.value = '20000';
      amount.dispatchEvent(new Event('input'));
      harness.detectChanges();
      el.querySelector<HTMLButtonElement>('.pl-add')!.click();
      const post = http.expectOne(r => r.method === 'POST' && r.url === BUDGETS);
      expect(post.request.body).toEqual({ scope: 'category', category_id: 1, project_id: null, amount: '20000', effective_from: '2026-10' });
      post.flush(store.create(post.request.body));
      flushStatus(store);
      harness.detectChanges();
      expect(texts(el, '.pl-edit-row .pl-edit-name')).toContain('居家');

      Array.from(el.querySelectorAll<HTMLButtonElement>('.pl-remove')).find(b => b.getAttribute('aria-label') === '刪除 娛樂 預算')!.click();
      const del = http.expectOne(r => r.method === 'DELETE' && r.url === `${BUDGETS}/4`);
      del.flush(null);
      store.remove(4);
      flushStatus(store);
      harness.detectChanges();
      expect(texts(el, '.pl-edit-row .pl-edit-name')).not.toContain('娛樂');
    });

    it('keeps the editor and says why when a write fails', async () => {
      const { harness, el } = await openPlan();
      el.querySelector<HTMLButtonElement>('.pl-edit')!.click();
      harness.detectChanges();
      Array.from(el.querySelectorAll<HTMLButtonElement>('.pl-remove'))[0].click();
      http.expectOne(r => r.method === 'DELETE').flush({ detail: 'boom' }, { status: 500, statusText: 'Server Error' });
      harness.detectChanges();
      expect(el.querySelector('.pl-error')).not.toBeNull();
      expect(el.querySelector('.pl-editor')).not.toBeNull();
      http.expectNone(r => r.url === `${BUDGETS}/status`);
    });
  });

  it('lists the pinned saved reports after the four tabs on desktop, as chips on phones', async () => {
    const desktop = await open('/accounting/reports?month=2026-10');
    flushSample();
    desktop.harness.detectChanges();
    const tabs = Array.from(desktop.el.querySelectorAll<HTMLElement>('.rp-tabs .rp-tab'));
    expect(tabs.map(tab => tab.textContent?.trim())).toEqual(['總覽', '趨勢與比較', '排行', '專案與預算', '週末外食', '訂閱月費', '＋ 自訂報表']);
    expect(tabs[4].getAttribute('href')).toBe('/accounting/reports/custom/1');
    expect(tabs[6].getAttribute('href')).toBe('/accounting/reports/custom/new');
    expect(desktop.el.querySelector('.rp-saved')).toBeNull();
    http.verify({ ignoreCancelled: true });
    TestBed.resetTestingModule();

    TestBed.configureTestingModule({
      providers: [
        provideRouter([{ path: 'accounting/reports', component: AccountingReportsComponent }, { path: '**', component: BlankPage }]),
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: MATCH_MEDIA, useValue: null },
        { provide: NavBadgeService, useValue: { badges: signal({}) } },
      ],
    });
    http = TestBed.inject(HttpTestingController);
    const phone = await open('/accounting/reports?month=2026-10', 'phone');
    flushSample();
    phone.harness.detectChanges();
    expect(texts(phone.el, '.rp-tabs .rp-tab')).toEqual(['總覽', '趨勢與比較', '排行', '專案與預算']);
    expect(texts(phone.el, '.rp-saved .rp-saved-chip')).toEqual(['週末外食', '訂閱月費']);
    expect(phone.el.querySelector('.rp-saved-chip')?.getAttribute('href')).toBe('/accounting/reports/custom/1');
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
