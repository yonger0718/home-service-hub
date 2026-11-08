import { HttpRequest, provideHttpClient } from '@angular/common/http';
import { HttpTestingController, TestRequest, provideHttpClientTesting } from '@angular/common/http/testing';
import { Component, signal } from '@angular/core';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { Project } from '../../../models/accounting.model';
import { BreakdownQuery, BreakdownResponse, BreakdownRow, ReportCompare, ReportDimension, ReportMetric, TREND_ALL, TrendQuery, TrendResponse } from '../../../models/reports.model';
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

  /** Opens the page and answers its own reads: the saved reports and the projects (the rules line names the excluded ones). */
  async function open(url: string, mode: LayoutMode = 'panes', projects: Project[] = []): Promise<{ harness: RouterTestingHarness; el: HTMLElement }> {
    TestBed.inject(LayoutModeService).set(mode);
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(url);
    harness.detectChanges();
    http.expectOne(r => r.url === '/api/accounting/reports/saved').flush(new SampleSavedReportStore().list());
    http.expectOne('/api/accounting/projects').flush(projects);
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

  it('names the 不計入報表 projects on the rules line (the October header test covers none)', async () => {
    const projects: Project[] = [
      { id: 1, name: '東京旅行', is_archived: true, sort_order: 0, moze_id: null, exclude_from_reports: true },
      { id: 2, name: '客廳整理', is_archived: false, sort_order: 1, moze_id: null, exclude_from_reports: false },
      { id: 3, name: '公司代墊', is_archived: false, sort_order: 2, moze_id: null, exclude_from_reports: true },
    ];
    const { harness, el } = await open('/accounting/reports?month=2026-10', 'panes', projects);
    flushSample();
    harness.detectChanges();
    expect(el.querySelector('.page-subtitle')?.textContent?.trim()).toBe('以台幣計 · 退款抵減支出 · 轉帳不計入 · 不計入：東京旅行、公司代墊');
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

  it('points 在記帳記錄看 at the month the timeline opens for a 年 period, without the whole-year count', async () => {
    const { harness, el } = await open('/accounting/reports?year=2026');
    flushSample();
    harness.detectChanges();
    const drill = el.querySelector<HTMLAnchorElement>('.ov-drill')!;
    expect(drill.textContent?.trim()).toBe('在記帳記錄看 2026-01（期間第一個月） ›');
    expect(drill.getAttribute('href')).toBe('/accounting?month=2026-01&category_id=1');
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
    expect(texts(el, '.tr-label .tr-month')).toEqual(['11 月', '12 月', '1 月', '2 月', '3 月', '4 月', '5 月', '6 月', '7 月', '8 月', '9 月', '10 月']);
    expect(texts(el, '.tr-label .tr-year')).toEqual(['2025', '2026']);
    const labels = Array.from(el.querySelectorAll('.tr-label'));
    expect(labels[0].querySelector('.tr-year')).not.toBeNull();
    expect(labels[2].querySelector('.tr-year')).not.toBeNull();
    expect(labels[5].querySelector('.tr-year')).toBeNull();
    expect(el.querySelector('.tr-average-label')?.textContent).toBe('12 個月平均 40,608');
    expect(el.querySelector('.tr-chart')?.getAttribute('aria-label')).toMatch(/^全部支出近 12 個月：2025 年 11 月 38,900、/);
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
    expect(chart.getAttribute('viewBox')).toBe('0 0 358 212');
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
    // Both periods are over, so the year keeps the three finished-period reads (a running year is covered below).
    vi.setSystemTime(new Date(2027, 0, 15, 10, 0));
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
    // A calendar year's previous period is last year: one comparison, named 去年, no 前年 column.
    expect(texts(el, '.rp-figure-foot')[0]).toBe('比去年 ▲ 2,282');
    expect(el.querySelector('.tr-card:nth-of-type(2) h2')?.textContent?.trim()).toBe('比較 · 2026 年 對去年');
    expect(texts(el, '.tr-table thead th')).toEqual(['類別', '今年', '去年', '增減']);
    const total = Array.from(el.querySelectorAll('.tr-table tbody tr')).at(-1)!;
    expect(texts(total, 'td')).toEqual(['41,890', '39,608', '▲ 2,282 (5.8%)']);
    expect(total.querySelectorAll('td')[2].getAttribute('data-label')).toBe('比去年');
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
      expect(texts(el, '.rk-spark-label .rk-month')).toEqual(['5 月', '6 月', '7 月', '8 月', '9 月', '10 月']);
      expect(texts(el, '.rk-spark-label .rk-year')).toEqual(['2026']);
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
      // The API computes those four on spend rows only; 筆數 and 平均 keep the refunds.
      expect(el.querySelector('.rk-stats-note')?.textContent?.trim()).toBe('中位數、最大、最小、平均間隔只算支出（退款不計入統計）；筆數、平均含退款');
      expect(el.querySelector('.rk-spark-chart')?.getAttribute('aria-label')).toMatch(/^路易莎 近 6 個月支出：5 月 880、/);
      const drill = el.querySelector<HTMLAnchorElement>('.rk-drill')!;
      expect(drill.textContent?.trim()).toBe('在記帳記錄看這 12 筆 ›');
      expect(drill.getAttribute('href')).toBe('/accounting?month=2026-10&merchant=%E8%B7%AF%E6%98%93%E8%8E%8E');
    });

    /** Opens 排行 with the ranking answered by one custom 饗食天堂 row, selected, and its six months answered. */
    async function openRankWith(extra: Partial<BreakdownRow>) {
      const opened = await open('/accounting/reports?month=2026-10&tab=rank');
      const first = pending();
      const ranking = first.breakdowns[3];
      const report = sampleBreakdown(breakdownQuery(ranking));
      const row: BreakdownRow = { ...report.rows[0], key: '饗食天堂', label: '饗食天堂', amount: '1250', refund: '0', count: 1, avg: '1250', median: '1250', max: '1250', min: '1250', first_date: '2026-10-03', last_date: '2026-10-03', avg_interval_days: null, ...extra };
      const others = { ...first, breakdowns: first.breakdowns.filter(r => r !== ranking) };
      answer(others);
      ranking.flush({ ...report, rows: [row] });
      opened.harness.detectChanges();
      answer(pending());
      opened.harness.detectChanges();
      return opened;
    }

    it('shows 只有 1 筆 with the date and amount for one visit', async () => {
      const { el } = await openRankWith({});
      const stats = texts(el, '.rk-stat');
      expect(stats[0]).toBe('只有 1 筆 · 10/3 · $1,250');
      expect(stats.slice(1).map(s => s?.split(' ')[0])).toEqual(['占支出', ...(stats.length > 2 ? ['近'] : [])]);
      const text = el.querySelector('.rk-panel')!.textContent!;
      for (const word of ['中位數', '最大', '最小', '平均間隔', '合計', '筆數']) expect(text).not.toContain(word);
      expect(el.querySelector('.rk-stats-note')).toBeNull();
    });

    it('signs a negative single entry before the dollar sign', async () => {
      const { el } = await openRankWith({ amount: '-50', refund: '100', avg: '-50' });
      expect(texts(el, '.rk-stat')[0]).toBe('只有 1 筆 · 10/3 · −$50');
    });

    it('omits the date when the API has no statistics', async () => {
      const { el } = await openRankWith({ first_date: null, last_date: null, median: null, max: null, min: null });
      expect(texts(el, '.rk-stat')[0]).toBe('只有 1 筆 · $1,250');
    });

    it('hides 平均間隔 for entries on one day', async () => {
      const { el } = await openRankWith({ count: 3, amount: '900', avg: '300', first_date: '2026-10-03', last_date: '2026-10-03', avg_interval_days: 0 });
      const labels = texts(el, '.rk-stat').map(s => s?.split(' ')[0]);
      expect(labels).toContain('筆數');
      expect(labels).not.toContain('平均間隔');
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
      expect(el.querySelector('.rk-stats-note')).toBeNull();
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
    const PROJECTS: Project[] = [
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
      const opened = await open('/accounting/reports?month=2026-10&tab=plan', 'panes', PROJECTS);
      const reads = pending();
      expect(reads.breakdowns).toHaveLength(4);
      expect(breakdownQuery(reads.breakdowns[3])).toEqual({ date_from: '2000-01-01', date_to: '2026-10-31', metric: 'expense', group_by: 'project', split_by: 'category', compare: undefined, include_excluded_projects: true });
      answer(reads);
      const store = flushStatus();
      // The projects came with the page; the tab reads only the categories.
      http.expectNone('/api/accounting/projects');
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
      expect(cards[0].querySelector('.pl-project-title small')?.textContent?.trim()).toBe('10/10 – 10/15 · 6 天 · 日均 4,143 · 不計入報表');
      expect(cards[0].querySelector('.pl-tag')?.textContent?.trim()).toBe('已結束');
      expect(cards[0].querySelector('.pl-project-total')?.textContent?.replace(/\s+/g, ' ').trim()).toBe('$24,860 預算 25,000 · 用了 99.4%');
      expect(texts(cards[0], '.pl-legend > span')).toEqual(['機票 9,800', '住宿 8,400', '餐飲 3,960', '交通 1,420', '購物 1,280']);
      expect(cards[1].querySelector('.pl-project-title small')?.textContent?.trim()).toBe('09/01 起 · 進行中');
      expect(cards[1].querySelector('.pl-tag')?.textContent?.trim()).toBe('進行中');
      // #261: the sample has spending without a project → one link to the month's timeline filtered to 無專案.
      const drill = el.querySelector<HTMLAnchorElement>('.pl-projects .pl-drill');
      expect(drill?.textContent?.trim()).toBe('沒掛專案的支出 · 在記帳記錄看 ›');
      expect(drill?.getAttribute('href')).toBe('/accounting?month=2026-10&no_project=1');
    });

    it('keeps the project cards when the API leaves first / last date out (over 50 groups), without inventing dates, and never renders the 其他 fold as a card', async () => {
      const { harness, el } = await open('/accounting/reports?month=2026-10&tab=plan', 'panes', PROJECTS);
      const reads = pending();
      const lifetime = reads.breakdowns.pop()!;
      answer(reads);
      const report = sampleBreakdown(breakdownQuery(lifetime));
      const undated = report.rows.filter(row => row.key !== null).map(row => ({ ...row, first_date: null, last_date: null }));
      lifetime.flush({ ...report, rows: [...undated, { ...undated[0], key: '__other__', label: '其他', amount: '999' }] });
      flushStatus();
      http.expectOne(r => r.url === '/api/accounting/categories').flush(ROOTS);
      harness.detectChanges();

      const cards = Array.from(el.querySelectorAll('.pl-project'));
      expect(cards.map(c => c.querySelector('.pl-project-title b')?.textContent?.trim())).toEqual(['東京旅行', '客廳整理']);
      expect(cards[0].querySelector('.pl-project-title small')?.textContent?.trim()).toBe('不計入報表');
      expect(cards[0].querySelector('.pl-tag')?.textContent?.trim()).toBe('已結束');
      expect(cards[1].querySelector('.pl-project-title small')?.textContent?.trim()).toBe('');
      expect(cards[1].querySelector('.pl-tag')?.textContent?.trim()).toBe('進行中');
      expect(el.querySelector('.pl-projects .pl-empty')).toBeNull();
      expect(el.querySelector('.pl-projects .pl-drill')).toBeNull(); // no 無專案 row → no link
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
  describe('running periods (polish D1, D8)', () => {
    interface Fixture {
      from: string;
      to: string;
      metric?: ReportMetric;
      /** One row; the totals follow it. */
      amount?: number;
      count?: number;
      compare?: { amount: number; count: number };
    }

    function response(fixture: Fixture): BreakdownResponse {
      const metric = fixture.metric ?? 'expense';
      const amount = fixture.amount ?? 0;
      const count = fixture.count ?? 0;
      const rows: BreakdownRow[] = count
        ? [{ key: '1', label: '餐飲', color: null, amount: String(amount), refund: '0', count, avg: '0', median: null, min: null, max: null, first_date: null, last_date: null, avg_interval_days: null }]
        : [];
      const totals = (a: number, c: number) => ({ expense: metric === 'expense' ? String(a) : '0', income: metric === 'income' ? String(a) : '0', net: '0', count: c, refund: '0' });
      return {
        currency: 'TWD',
        date_from: fixture.from,
        date_to: fixture.to,
        totals: totals(amount, count),
        compare: fixture.compare ? { date_from: '2026-10-01', date_to: '2026-10-07', totals: totals(fixture.compare.amount, fixture.compare.count) } : undefined,
        rows,
        overlapping: false,
        missing_rates: [],
      };
    }

    const queryOf = (request: TestRequest) => breakdownQuery(request);

    /** Answers the page's reads by range: the to-date ones carry the compare figures, the whole ones none. */
    function answerBy(
      reads: Pending,
      today: string,
      figures: { toDate?: Partial<Fixture>; whole?: Partial<Fixture>; previous?: number; lastYear?: number; previousCount?: number; lastYearCount?: number; income?: number },
    ): void {
      for (const request of reads.breakdowns) {
        const query = queryOf(request);
        const cut = query.date_to === today;
        const base = { from: query.date_from, to: query.date_to, metric: query.metric };
        if (query.compare === 'previous' || query.compare === 'last_year') {
          const isPrevious = query.compare === 'previous';
          const compare = query.metric === 'income' ? undefined : { amount: (isPrevious ? figures.previous : figures.lastYear) ?? 0, count: (isPrevious ? figures.previousCount : figures.lastYearCount) ?? 1 };
          request.flush(response({ ...base, ...(query.metric === 'income' ? { amount: figures.income ?? 0, count: 1 } : figures.toDate), compare }));
        } else {
          request.flush(response({ ...base, ...(query.metric === 'income' ? { amount: figures.income ?? 0, count: 1 } : cut ? figures.toDate : figures.whole) }));
        }
      }
      for (const request of reads.trends) request.flush(sampleTrend(trendQuery(request)));
    }

    async function openOn(url: string, day: [number, number, number]) {
      vi.setSystemTime(new Date(day[0], day[1] - 1, day[2], 10, 0));
      return open(url);
    }

    it('loads a running month to date plus two whole-month breakdowns', async () => {
      const { harness, el } = await openOn('/accounting/reports?month=2026-11', [2026, 11, 7]);
      const reads = pending();
      const queries = reads.breakdowns.map(queryOf);
      expect(queries).toHaveLength(5);
      expect(queries.filter(q => q.date_to === '2026-11-07')).toHaveLength(3);
      const whole = queries.filter(q => q.date_to === '2026-11-30');
      expect(whole).toHaveLength(2);
      expect(whole.every(q => q.date_from === '2026-11-01' && q.compare === undefined && q.split_by === 'subcategory')).toBe(true);
      expect(whole.map(q => q.metric).sort()).toEqual(['expense', 'income']);
      expect(reads.trends.map(trendQuery)[0]).toMatchObject({ date_from: '2025-12-01', date_to: '2026-11-07' });

      answerBy(reads, '2026-11-07', { toDate: { amount: 4233, count: 9 }, whole: { amount: 9000, count: 12 }, previous: 6910, lastYear: 5120, income: 1000 });
      harness.detectChanges();
      expect(texts(el, '.rp-figure-value')[0]).toBe('$4,233');
      expect(texts(el, '.rp-figure-foot')[0]).toBe('比 10 月同期 ▼ 2,677 · 比去年同期 ▼ 887 · 截至 11/7');
    });

    it('shows one last-year segment for a running year, read from the last-year response', async () => {
      const { harness, el } = await openOn('/accounting/reports?year=2026', [2026, 11, 7]);
      answerBy(pending(), '2026-11-07', { toDate: { amount: 4233, count: 9 }, whole: { amount: 4233, count: 9 }, previous: 1, lastYear: 5120, income: 1000 });
      harness.detectChanges();
      expect(texts(el, '.rp-figure-foot')[0]).toBe('比去年同期 ▼ 887 · 截至 11/7');
    });

    it('loads a month on its last day as a finished month', async () => {
      const { harness, el } = await openOn('/accounting/reports?month=2026-11', [2026, 11, 30]);
      const reads = pending();
      expect(reads.breakdowns.map(queryOf).map(q => q.date_to)).toEqual(['2026-11-30', '2026-11-30', '2026-11-30']);
      answerBy(reads, '2026-11-30', { toDate: { amount: 4233, count: 9 }, previous: 6910, lastYear: 5120, income: 1000 });
      harness.detectChanges();
      expect(texts(el, '.rp-figure-foot')[0]).not.toContain('截至');
      expect(texts(el, '.rp-figure-foot')[0]).toContain('比 10 月 ▼ 2,677');
    });

    it('reads 上期無資料 when the previous window counts nothing', async () => {
      const { harness, el } = await openOn('/accounting/reports?month=2026-11', [2026, 11, 7]);
      answerBy(pending(), '2026-11-07', { toDate: { amount: 4233, count: 9 }, whole: { amount: 4233, count: 9 }, previous: 0, previousCount: 0, lastYear: 5120, income: 1000 });
      harness.detectChanges();
      const foot = texts(el, '.rp-figure-foot')[0]!;
      expect(foot).toContain('上期無資料');
      expect(foot).toContain('比去年同期 ▼ 887');
      expect(foot).not.toContain('▲');
      expect(foot).not.toContain('比 10 月同期 上期無資料');
    });

    it('keeps a to-date view with no rows yet and still names the later rows', async () => {
      const { harness, el } = await openOn('/accounting/reports?month=2026-11', [2026, 11, 1]);
      answerBy(pending(), '2026-11-01', { toDate: { amount: 0, count: 0 }, whole: { amount: 13420, count: 2 }, previous: 500, lastYear: 500, income: 0 });
      harness.detectChanges();
      expect(texts(el, '.rp-figure-value')[0]).toBe('$0');
      // F4: a total is never 停止; 0 against 500 is the delta.
      expect(texts(el, '.rp-figure-foot')[0]).toBe('比 10 月同期 ▼ 500 · 比去年同期 ▼ 500 · 截至 11/1');
      const page = harness.routeDebugElement!.componentInstance as AccountingReportsComponent;
      expect(page.expenseWhole()?.rows.map(row => row.count)).toEqual([2]);
      expect(page.expenseWhole()?.totals.expense).toBe('13420');
      expect(page.expense()?.rows).toEqual([]);
      // The line itself is 總覽's (task 7); the page hands it the whole response and today.
      expect(page.today()).toBe('2026-11-01');
    });

    it('switches to the whole month and back, and resets on another month', async () => {
      const { harness, el } = await openOn('/accounting/reports?month=2026-11', [2026, 11, 7]);
      const figures = { toDate: { amount: 4233, count: 9 }, whole: { amount: 9000, count: 12 }, previous: 6910, lastYear: 5120, income: 1000 };
      answerBy(pending(), '2026-11-07', figures);
      harness.detectChanges();
      const page = harness.routeDebugElement!.componentInstance as AccountingReportsComponent;
      expect(page.running()).toBe(true);

      page.toggleWhole();
      harness.detectChanges();
      const reads = pending();
      expect(reads.breakdowns).toHaveLength(0);
      expect(page.wholeView()).toBe(true);
      expect(page.viewRange()).toEqual({ from: '2026-11-01', to: '2026-11-30' });
      expect(reads.trends.map(trendQuery)[0]).toMatchObject({ date_to: '2026-11-30' });
      for (const request of reads.trends) request.flush(sampleTrend(trendQuery(request)));
      expect(texts(el, '.rp-figure-value')[0]).toBe('$9,000');
      expect(texts(el, '.rp-figure-foot')[0]).toBe('12 筆');

      // A stale response for the old period is ignored; the new month resets the view.
      el.querySelector<HTMLButtonElement>('.rp-prev')!.click();
      await harness.fixture.whenStable();
      harness.detectChanges();
      const old = pending();
      expect(page.wholeView()).toBe(false);
      expect(old.breakdowns.map(queryOf).every(q => q.date_from === '2026-10-01')).toBe(true);
      el.querySelector<HTMLButtonElement>('.rp-next')!.click();
      await harness.fixture.whenStable();
      harness.detectChanges();
      const fresh = pending();
      expect(fresh.breakdowns).toHaveLength(5);
      answerBy(fresh, '2026-11-07', figures);
      harness.detectChanges();
      // The October answers arrive late.
      answerBy({ breakdowns: old.breakdowns, trends: [] }, '2026-10-31', { toDate: { amount: 1, count: 1 }, previous: 1, lastYear: 1 });
      for (const request of old.trends) request.flush(sampleTrend(trendQuery(request)));
      harness.detectChanges();
      expect(texts(el, '.rp-figure-value')[0]).toBe('$4,233');
    });

    it('projects the period control into the page header', async () => {
      const { el } = await open('/accounting/reports?month=2026-10');
      flushSample();
      expect(el.querySelector('.page-actions .rp-period')).not.toBeNull();
      expect(el.querySelectorAll('.rp-period')).toHaveLength(1);
    });

    it('on a phone renders the period control after the page header, under the sub-nav (N1)', async () => {
      const { el } = await open('/accounting/reports?month=2026-10', 'phone');
      flushSample();
      const header = el.querySelector('app-page-header')!;
      const period = el.querySelector('.rp-period')!;
      expect(el.querySelectorAll('.rp-period')).toHaveLength(1);
      expect(header.contains(period)).toBe(false);
      expect(header.nextElementSibling).toBe(period);
    });

    it('outside a phone keeps the period control in the header actions (N1)', async () => {
      const { el } = await open('/accounting/reports?month=2026-10', 'sheet');
      flushSample();
      expect(el.querySelector('app-page-header .page-actions .rp-period')).not.toBeNull();
      expect(el.querySelectorAll('.rp-period')).toHaveLength(1);
    });

    const scss = (name: string) => readFileSync(join(process.cwd(), 'src/app/components/accounting/reports', name), 'utf8');

    it('keeps the header on one row from 1024 px and wraps the period control below', () => {
      const css = scss('reports.scss');
      const media = /@media \(max-width: 1023\.98px\) \{([\s\S]*?)\n  \}\n\}|@media \(max-width: 1023\.98px\) \{([\s\S]*?)\n\}/.exec(css);
      const block = media?.[1] ?? media?.[2] ?? '';
      expect(block).toContain('.page-actions');
      expect(block).toMatch(/flex(-basis)?:\s*(1 1 )?100%|width:\s*100%/);
      // M2: the actions go after the title and the gear, so the period control stays under them (the phone sub-nav is handled by the template, see the next test).
      expect(block).toMatch(/\.page-actions\s*\{[^}]*order:\s*1\b/);
      const outside = css.replace(/@media[^{]*\{(?:[^{}]|\{[^{}]*\})*\}/g, '');
      for (const rule of outside.match(/[^{}]*\.rp-period\b[^{}]*\{[^}]*\}/g) ?? []) {
        expect(rule).not.toMatch(/display:\s*none|order:|position:|transform|margin-top/);
      }
    });

    it('leaves the 記一筆 clearance to the shell', () => {
      for (const name of ['reports.scss', 'custom-report.scss']) {
        const css = scss(name);
        expect(css).not.toContain('--fab-clear');
        expect(css).not.toMatch(/\n\.(rp|cr) \{[^}]*margin-bottom:\s*-/);
      }
    });

    it('paints the running hatch with stripe lines, never a url(#id) reference (WebKit <base href>)', () => {
      for (const file of ['trend-tab.scss', 'custom-report.scss', 'trend-tab.html', 'custom-report.html']) {
        expect(scss(file)).not.toContain('url(#');
        expect(scss(file)).not.toContain('<pattern');
      }
      expect(scss('trend-tab.html')).toContain('<line');
      expect(scss('trend-tab.html')).toMatch(/class="tr-hatch"/);
      expect(scss('custom-report.html')).toMatch(/class="cr-hatch"/);
      expect(scss('custom-report.html')).toMatch(/<line[^>]*class="cr-hatch-line"/);
    });

    it('lets the saved table headings and row labels wrap on phones so 增減 stays in view', () => {
      const css = scss('custom-report.scss');
      const phone = css.match(/\/\* Phones \(ReportSavedPhone\)[\s\S]*?\.cr--phone & \{([\s\S]*?)\n  \}/)?.[1] ?? '';
      expect(phone).toMatch(/thead th,\s*\.cr-col-name\s*\{[^}]*white-space:\s*normal/);
    });

    it('renders the to-date figures when a whole-period read fails (M4)', async () => {
      const { harness, el } = await openOn('/accounting/reports?month=2026-11', [2026, 11, 7]);
      const reads = pending();
      const whole = reads.breakdowns.filter(request => queryOf(request).date_to === '2026-11-30');
      expect(whole).toHaveLength(2);
      whole[0].flush({ code: 'x', message: 'x', trace_id: 'x' }, { status: 500, statusText: 'Server Error' });
      answerBy({ breakdowns: reads.breakdowns.filter(request => request !== whole[0]), trends: reads.trends }, '2026-11-07', {
        toDate: { amount: 4233, count: 9 }, whole: { amount: 9000, count: 12 }, previous: 6910, lastYear: 5120, income: 1000,
      });
      harness.detectChanges();
      for (const request of http.match(r => r.url === BREAKDOWN && r.params.get('group_by') === 'merchant')) request.flush(sampleBreakdown(breakdownQuery(request)));
      expect(el.querySelector('.rp-error')).toBeNull();
      expect(texts(el, '.rp-figure-value')[0]).toBe('$4,233');
      expect(texts(el, '.rp-figure-foot')[0]).toBe('比 10 月同期 ▼ 2,677 · 比去年同期 ▼ 887 · 截至 11/7');
      expect(el.querySelector('.ov-after')).toBeNull();
    });

    it('names the currencies left out of the whole period in the whole view (M6)', async () => {
      const { harness, el } = await openOn('/accounting/reports?month=2026-11', [2026, 11, 7]);
      const reads = pending();
      answerBy({ breakdowns: reads.breakdowns.filter(request => queryOf(request).date_to !== '2026-11-30'), trends: reads.trends }, '2026-11-07', {
        toDate: { amount: 4233, count: 9 }, previous: 6910, lastYear: 5120, income: 1000,
      });
      for (const request of reads.breakdowns.filter(r => queryOf(r).date_to === '2026-11-30')) {
        const query = queryOf(request);
        request.flush({ ...response({ from: query.date_from, to: query.date_to, metric: query.metric, amount: 9000, count: 12 }), missing_rates: query.metric === 'expense' ? ['JPY'] : [] });
      }
      harness.detectChanges();
      for (const request of http.match(r => r.url === BREAKDOWN && r.params.get('group_by') === 'merchant')) request.flush(sampleBreakdown(breakdownQuery(request)));
      expect(el.querySelector('.page-subtitle')?.textContent).not.toContain('未換算');
      const page = harness.routeDebugElement!.componentInstance as AccountingReportsComponent;
      page.toggleWhole();
      harness.detectChanges();
      for (const request of pending().trends) request.flush(sampleTrend(trendQuery(request)));
      for (const request of http.match(r => r.url === BREAKDOWN && r.params.get('group_by') === 'merchant')) request.flush(sampleBreakdown(breakdownQuery(request)));
      harness.detectChanges();
      expect(el.querySelector('.page-subtitle')?.textContent).toContain('未換算：JPY');
    });
  });

  describe('總覽 detail (polish D1, D3, D5)', () => {
    const MERCHANT_HOST = (request: HttpRequest<unknown>) => request.url === BREAKDOWN && request.params.get('group_by') === 'merchant';

    function cat(key: string, label: string, amount: number, count: number, extra: Partial<BreakdownRow> = {}): BreakdownRow {
      return { key, label, color: null, amount: String(amount), refund: '0', count, avg: '0', median: null, min: null, max: null, first_date: null, last_date: null, avg_interval_days: null, ...extra };
    }

    function reply(query: BreakdownQuery, rows: BreakdownRow[], windowCount?: number): BreakdownResponse {
      const metric = query.metric ?? 'expense';
      const sum = rows.reduce((total, row) => total + Number(row.amount), 0);
      const count = rows.reduce((total, row) => total + row.count, 0);
      const totals = (a: number, c: number) => ({ expense: metric === 'expense' ? String(a) : '0', income: metric === 'income' ? String(a) : '0', net: '0', count: c, refund: '0' });
      return {
        currency: 'TWD',
        date_from: query.date_from,
        date_to: query.date_to,
        totals: totals(sum, count),
        compare: query.compare && windowCount !== undefined ? { date_from: '2026-09-01', date_to: '2026-09-30', totals: totals(0, windowCount) } : undefined,
        rows,
        overlapping: false,
        missing_rates: [],
      };
    }

    /** Answers the category reads: `expenseRows` for 支出 (whichever window), nothing for 收入; trends with the sample. */
    function answerCategories(reads: Pending, expenseRows: BreakdownRow[], windowCount = 5): void {
      for (const request of reads.breakdowns) {
        const query = breakdownQuery(request);
        request.flush(reply(query, query.metric === 'income' ? [] : expenseRows, windowCount));
      }
      for (const request of reads.trends) request.flush(sampleTrend(trendQuery(request)));
    }

    const housing = (extra: Partial<BreakdownRow> = {}) =>
      cat('7', '居家', 13420, 2, { compare_amount: '12000', splits: [{ key: '70', label: '居家', amount: '13420', count: 2 }], ...extra });
    const food = () => cat('2', '餐飲', 5000, 9, { compare_amount: '4000', splits: [{ key: '21', label: '午餐', amount: '5000', count: 9 }] });

    it('lists 常去商家 for a category without subcategories', async () => {
      const { harness, el } = await open('/accounting/reports?month=2026-10');
      answerCategories(pending(), [housing(), food()]);
      harness.detectChanges();

      const request = http.expectOne(MERCHANT_HOST);
      const params = request.request.params;
      expect([params.get('date_from'), params.get('date_to'), params.get('metric'), params.getAll('category_id')]).toEqual(['2026-10-01', '2026-10-31', 'expense', ['7']]);
      request.flush(reply({ date_from: '2026-10-01', date_to: '2026-10-31', metric: 'expense', group_by: 'merchant' }, [cat('房東', '房東', 12000, 1), cat('台電', '台電', 1420, 1)]));
      harness.detectChanges();

      const card = el.querySelector('.ov-detail')!;
      expect(card.textContent).toContain('常去商家');
      expect(texts(card, '.ov-merchant-name')).toEqual(['房東', '台電']);
      expect(texts(card, '.ov-merchant-amount')).toEqual(['12,000', '1,420']);
      expect(card.querySelector('.ov-split')).toBeNull();
      expect(card.textContent).not.toContain('沒有子類別');
    });

    it('shows at most five merchants and names the entries without one （無商家）', async () => {
      const { harness, el } = await open('/accounting/reports?month=2026-10');
      answerCategories(pending(), [housing()]);
      harness.detectChanges();
      const rows = ['甲', '乙', '丙', '丁', '戊', '己'].map((name, i) => cat(name, name, 100 - i, 1));
      http.expectOne(MERCHANT_HOST).flush(reply({ date_from: '2026-10-01', date_to: '2026-10-31', metric: 'expense', group_by: 'merchant' }, [...rows, { ...cat('x', '', 50, 1), key: null }]));
      harness.detectChanges();
      expect(texts(el, '.ov-merchant-name')).toEqual(['甲', '乙', '丙', '丁', '戊', '（無商家）']);
    });

    it('reads the merchants once per period and category', async () => {
      const { harness, el } = await open('/accounting/reports?month=2026-10');
      answerCategories(pending(), [housing(), food()]);
      harness.detectChanges();
      http.expectOne(MERCHANT_HOST).flush(reply({ date_from: '2026-10-01', date_to: '2026-10-31', metric: 'expense', group_by: 'merchant' }, [cat('房東', '房東', 12000, 1)]));
      harness.detectChanges();

      const click = (name: string) => Array.from(el.querySelectorAll<HTMLButtonElement>('.ov-row')).find(row => row.textContent?.includes(name))!.click();
      click('餐飲');
      harness.detectChanges();
      click('居家');
      harness.detectChanges();
      http.expectNone(MERCHANT_HOST);
      expect(texts(el, '.ov-merchant-name')).toEqual(['房東']);
    });

    it('shows figures and the drill link only when the merchants fail', async () => {
      const { harness, el } = await open('/accounting/reports?month=2026-10');
      answerCategories(pending(), [housing()]);
      harness.detectChanges();
      http.expectOne(MERCHANT_HOST).flush({ code: 'x', message: 'x', trace_id: 'x' }, { status: 500, statusText: 'Server Error' });
      harness.detectChanges();
      const card = el.querySelector('.ov-detail')!;
      expect(card.querySelector('.ov-merchant')).toBeNull();
      expect(card.querySelector('.ov-stat')).not.toBeNull();
      expect(card.querySelector('.ov-drill')).not.toBeNull();
    });

    it('mutes 新增 when the category had nothing last period', async () => {
      const { harness, el } = await open('/accounting/reports?month=2026-10');
      answerCategories(pending(), [cat('2', '餐飲', 5000, 9, { compare_amount: '0', splits: [{ key: '21', label: '午餐', amount: '5000', count: 9 }] })]);
      harness.detectChanges();
      const stat = el.querySelector('.ov-stat b')!;
      expect(stat.textContent?.trim()).toBe('新增');
      expect(stat.classList.contains('more')).toBe(false);
      expect(stat.classList.contains('less')).toBe(false);
      expect(stat.classList.contains('muted')).toBe(true);
    });

    it('reads 上期無資料, muted, when the previous window counts nothing', async () => {
      const { harness, el } = await open('/accounting/reports?month=2026-10');
      answerCategories(pending(), [food()], 0);
      harness.detectChanges();
      const stat = el.querySelector('.ov-stat b')!;
      expect(stat.textContent?.trim()).toBe('上期無資料');
      expect(stat.classList.contains('muted')).toBe(true);
    });

    describe('while the period runs', () => {
      const cutReply = (query: BreakdownQuery, today: string, toDate: BreakdownRow[], whole: BreakdownRow[]) =>
        reply(query, query.metric === 'income' ? [] : query.date_to === today ? toDate : whole, 5);

      function answerCut(reads: Pending, today: string, toDate: BreakdownRow[], whole: BreakdownRow[]): void {
        for (const request of reads.breakdowns) {
          const query = breakdownQuery(request);
          request.flush(cutReply(query, today, toDate, whole));
        }
        for (const request of reads.trends) request.flush(sampleTrend(trendQuery(request)));
      }

      it('names rows after today under the list and emits the switch', async () => {
        vi.setSystemTime(new Date(2026, 10, 7, 10, 0));
        const { harness, el } = await open('/accounting/reports?month=2026-11');
        const page = harness.routeDebugElement!.componentInstance as AccountingReportsComponent;
        answerCut(pending(), '2026-11-07', [cat('2', '餐飲', 4233, 9, { compare_amount: '3000' })], [cat('2', '餐飲', 17653, 11)]);
        harness.detectChanges();

        const after = el.querySelector<HTMLButtonElement>('button.ov-after')!;
        expect(after.textContent?.trim()).toBe('另有 11/7 之後已記的 2 筆 $13,420 未計入');
        expect(texts(el, '.ov-row .ov-amount')).toEqual(['4,233']);
        expect(texts(el, '.ov-stat')).toHaveLength(3);

        after.click();
        await harness.fixture.whenStable();
        harness.detectChanges();
        expect(page.wholeView()).toBe(true);
        for (const request of pending().trends) request.flush(sampleTrend(trendQuery(request)));
        harness.detectChanges();
        expect(el.querySelector('button.ov-after')?.textContent?.trim()).toBe('改回截至 11/7');
        expect(texts(el, '.ov-row .ov-amount')).toEqual(['17,653']);
        // The whole view hides the change stat.
        expect(texts(el, '.ov-stat').some(text => text?.includes('比 '))).toBe(false);

        el.querySelector<HTMLButtonElement>('button.ov-after')!.click();
        harness.detectChanges();
        expect(page.wholeView()).toBe(false);
        expect(el.querySelector('button.ov-after')?.textContent?.trim()).toBe('另有 11/7 之後已記的 2 筆 $13,420 未計入');
        for (const request of pending().trends) request.flush(sampleTrend(trendQuery(request)));
      });

      it('has no 「另有」 line when nothing is dated after today', async () => {
        vi.setSystemTime(new Date(2026, 10, 7, 10, 0));
        const { harness, el } = await open('/accounting/reports?month=2026-11');
        answerCut(pending(), '2026-11-07', [cat('2', '餐飲', 4233, 9)], [cat('2', '餐飲', 4233, 9)]);
        harness.detectChanges();
        expect(el.querySelector('.ov-after')).toBeNull();
      });

      it('labels the change against the same days of the previous month', async () => {
        vi.setSystemTime(new Date(2026, 10, 7, 10, 0));
        const { harness, el } = await open('/accounting/reports?month=2026-11');
        answerCut(pending(), '2026-11-07', [cat('2', '餐飲', 4233, 9, { compare_amount: '3000' })], [cat('2', '餐飲', 4233, 9)]);
        harness.detectChanges();
        expect(texts(el, '.ov-stat')[0]).toBe('比 10 月同期 ▲ 1,233');
      });

      it('names the later rows even when nothing is dated up to today', async () => {
        vi.setSystemTime(new Date(2026, 10, 1, 10, 0));
        const { harness, el } = await open('/accounting/reports?month=2026-11');
        answerCut(pending(), '2026-11-01', [], [cat('7', '居家', 13420, 2)]);
        harness.detectChanges();
        expect(el.querySelector('.ov-after')?.textContent?.trim()).toBe('另有 11/1 之後已記的 2 筆 $13,420 未計入');
        for (const request of http.match(MERCHANT_HOST)) request.flush(reply(breakdownQuery(request), []));
      });

      const kindButton = (el: HTMLElement, label: string) =>
        Array.from(el.querySelectorAll<HTMLButtonElement>('.ov-share app-seg-toggle button')).find(b => b.textContent?.trim() === label)!;
      const salary = (amount: number, count: number) => cat('9', '薪資', amount, count, { splits: [{ key: '91', label: '月薪', amount: String(amount), count }] });

      it('reads a running year against last year from the last-year response (F1)', async () => {
        vi.setSystemTime(new Date(2026, 10, 7, 10, 0));
        const { harness, el } = await open('/accounting/reports?year=2026');
        for (const request of pending().breakdowns) {
          const query = breakdownQuery(request);
          if (query.metric === 'income') request.flush(reply(query, [salary(68000, 1)], 5));
          // The previous window (Feb 1–Dec 7 2025) and the last-year one differ: only the last-year figure may show.
          else if (query.compare === 'last_year') request.flush(reply(query, [food()].map(row => ({ ...row, compare_amount: '6000' })), 5));
          else request.flush(reply(query, [food()], 5));
        }
        for (const request of pending().trends) request.flush(sampleTrend(trendQuery(request)));
        harness.detectChanges();
        expect(texts(el, '.ov-stat')[0]).toBe('比去年同期 ▼ 1,000');

        kindButton(el, '收入').click();
        harness.detectChanges();
        expect(el.querySelector('.ov-detail h2')?.textContent).toContain('薪資');
        expect(texts(el, '.ov-stat').map(text => text?.split(' ')[0])).toEqual(['每筆平均', '占收入']);
      });

      it('keeps 改回截至 on screen after switching to a kind with no later rows (I2)', async () => {
        vi.setSystemTime(new Date(2026, 10, 7, 10, 0));
        const { harness, el } = await open('/accounting/reports?month=2026-11');
        const page = harness.routeDebugElement!.componentInstance as AccountingReportsComponent;
        for (const request of pending().breakdowns) {
          const query = breakdownQuery(request);
          const whole = query.date_to === '2026-11-30';
          request.flush(reply(query, query.metric === 'income' ? [salary(68000, whole ? 2 : 1)] : [food()], 5));
        }
        for (const request of pending().trends) request.flush(sampleTrend(trendQuery(request)));
        harness.detectChanges();
        expect(el.querySelector('.ov-after')).toBeNull();

        kindButton(el, '收入').click();
        harness.detectChanges();
        el.querySelector<HTMLButtonElement>('button.ov-after')!.click();
        harness.detectChanges();
        expect(page.wholeView()).toBe(true);
        for (const request of pending().trends) request.flush(sampleTrend(trendQuery(request)));

        kindButton(el, '支出').click();
        harness.detectChanges();
        expect(el.querySelector('button.ov-after')?.textContent?.trim()).toBe('改回截至 11/7');
        el.querySelector<HTMLButtonElement>('button.ov-after')!.click();
        harness.detectChanges();
        expect(page.wholeView()).toBe(false);
        for (const request of pending().trends) request.flush(sampleTrend(trendQuery(request)));
        for (const request of http.match(MERCHANT_HOST)) request.flush(reply(breakdownQuery(request), []));
      });
    });
  });

  describe('趨勢與比較 (polish D1, D3, D4)', () => {
    const row = (key: string, label: string, amount: number, count: number, compare?: number): BreakdownRow => ({
      key, label, color: null, amount: String(amount), refund: '0', count, avg: '0', median: null, min: null, max: null,
      first_date: null, last_date: null, avg_interval_days: null, ...(compare === undefined ? {} : { compare_amount: String(compare) }),
    });

    function breakdown(query: BreakdownQuery, rows: BreakdownRow[], windowCount?: number, windowAmount = 0): BreakdownResponse {
      const metric = query.metric ?? 'expense';
      const sum = rows.reduce((total, r) => total + Number(r.amount), 0);
      const count = rows.reduce((total, r) => total + r.count, 0);
      const totals = (a: number, c: number) => ({ expense: metric === 'expense' ? String(a) : '0', income: metric === 'income' ? String(a) : '0', net: '0', count: c, refund: '0' });
      return {
        currency: 'TWD',
        date_from: query.date_from,
        date_to: query.date_to,
        totals: totals(sum, count),
        compare: query.compare && windowCount !== undefined ? { date_from: '2026-10-01', date_to: '2026-10-07', totals: totals(windowAmount, windowCount) } : undefined,
        rows: metric === 'income' ? [] : rows,
        overlapping: false,
        missing_rates: [],
      };
    }

    const SERIES = [0, 0, 0, 0, 0, 0, 0, 0, 31200, 39608, 41890, 4233];
    const MONTHS = ['2025-12', '2026-01', '2026-02', '2026-03', '2026-04', '2026-05', '2026-06', '2026-07', '2026-08', '2026-09', '2026-10', '2026-11'];
    const trendOf = (values: number[]): TrendResponse => ({
      currency: 'TWD',
      granularity: 'month',
      buckets: MONTHS.slice(MONTHS.length - values.length).map((m, i) => ({ start: `${m}-01`, values: { [TREND_ALL]: String(values[i]) } })),
      average: {},
    });

    interface Figures {
      toDate: BreakdownRow[];
      whole?: BreakdownRow[];
      previousCount?: number;
      lastYearCount?: number;
      lastYearRows?: BreakdownRow[];
      previousTotal?: number;
      lastYearTotal?: number;
      trend?: TrendResponse;
    }

    function answerTrend(reads: Pending, today: string, figures: Figures): void {
      for (const request of reads.breakdowns) {
        const query = breakdownQuery(request);
        if (query.compare === 'previous') request.flush(breakdown(query, figures.toDate, figures.previousCount ?? 5, figures.previousTotal));
        else if (query.compare === 'last_year') request.flush(breakdown(query, figures.lastYearRows ?? figures.toDate, figures.lastYearCount ?? 5, figures.lastYearTotal));
        else request.flush(breakdown(query, query.date_to === today ? figures.toDate : (figures.whole ?? figures.toDate)));
      }
      for (const request of reads.trends) request.flush(figures.trend ?? trendOf(SERIES));
    }

    async function openTrend(day: [number, number, number], figures: Figures, url = '/accounting/reports?month=2026-11&tab=trend') {
      vi.setSystemTime(new Date(day[0], day[1] - 1, day[2], 10, 0));
      const { harness, el } = await open(url);
      const today = `${day[0]}-${String(day[1]).padStart(2, '0')}-${String(day[2]).padStart(2, '0')}`;
      answerTrend(pending(), today, figures);
      harness.detectChanges();
      return { harness, el, today };
    }

    const FOOD = [row('2', '餐飲', 4233, 9, 3000)];

    it('averages the months with data and labels 3 個月平均 37,566', async () => {
      const { el } = await openTrend([2026, 11, 7], { toDate: FOOD });
      expect(el.querySelector('.tr-average-label')?.textContent).toBe('3 個月平均 37,566');
      expect(el.querySelector('.tr-average')).not.toBeNull();
    });

    it('draws no average for a ledger younger than a month', async () => {
      const { el } = await openTrend([2026, 11, 7], { toDate: FOOD, trend: trendOf([4233]) });
      expect(el.querySelector('.tr-average')).toBeNull();
      expect(el.querySelector('.tr-average-label')).toBeNull();
    });

    it('draws the running month solid with a dashed 預估 box on top, and explains it under the chart', async () => {
      const { el } = await openTrend([2026, 11, 7], { toDate: FOOD });
      const bar = Array.from(el.querySelectorAll('.tr-bar')).at(-1)!;
      const box = el.querySelector('.tr-chart .tr-forecast')!;
      expect(box).not.toBeNull();
      expect(el.querySelectorAll('.tr-chart .tr-forecast')).toHaveLength(1);
      expect(Number(box.getAttribute('y')) + Number(box.getAttribute('height'))).toBeCloseTo(Number(bar.getAttribute('y')), 0);
      expect(box.getAttribute('x')).toBe(bar.getAttribute('x'));
      expect(el.querySelector('.tr-chart .tr-hatch')).toBeNull();
      expect(bar.classList.contains('hatched')).toBe(false);
      // 4,233 + 37,566 × 23/30 = 33,034.
      expect(el.querySelector('.tr-forecast-note')?.textContent?.trim()).toBe('11 月預估 $33,034 ＝ 至 11/7 $4,233 ＋ 平均 $37,566 × 剩 23/30 天');
      expect(el.querySelector('.tr-chart')?.getAttribute('aria-label')).toContain('11 月 進行中（至 11/7） 4,233，預估 33,034');
    });

    it('keeps the forecast at what is already recorded for the month when that is higher', async () => {
      const { el } = await openTrend([2026, 11, 7], { toDate: FOOD, whole: [row('2', '餐飲', 50000, 12)] });
      expect(el.querySelector('.tr-forecast-note')?.textContent?.trim()).toBe('11 月預估 $50,000（含 11/7 之後已記的帳）');
    });

    it('hatches the running month instead when there is no average to forecast from', async () => {
      const { el } = await openTrend([2026, 11, 7], { toDate: FOOD, trend: trendOf([4233]) });
      expect(el.querySelector('.tr-chart .tr-forecast')).toBeNull();
      expect(el.querySelector('.tr-forecast-note')).toBeNull();
      expect(Array.from(el.querySelectorAll('.tr-bar')).at(-1)!.classList.contains('hatched')).toBe(true);
      expect(el.querySelectorAll('.tr-chart .tr-hatch line').length).toBeGreaterThan(3);
      expect(el.querySelector('.tr-chart pattern')).toBeNull();
      expect(el.querySelector('.tr-chart')!.outerHTML).not.toContain('url(#');
    });

    it('hatches the running month', async () => {
      const { el } = await openTrend([2026, 11, 7], { toDate: FOOD });
      const bars = Array.from(el.querySelectorAll('.tr-bar'));
      expect(bars.at(-1)!.classList.contains('running')).toBe(true);
      expect(bars.filter(bar => bar.classList.contains('running'))).toHaveLength(1);
      expect(el.querySelector('.tr-chart')?.getAttribute('aria-label')).toContain('11 月 進行中（至 11/7） 4,233');
      expect(texts(el, '.tr-label .tr-year')).toEqual(['2025', '2026']);
    });

    it('heads the table 本月至今（11/1–11/7） / 上月同期 / 去年同期 while running', async () => {
      const { el } = await openTrend([2026, 11, 7], { toDate: FOOD });
      expect(texts(el, '.tr-table thead th')).toEqual(['類別', '本月至今（11/1–11/7）', '上月同期', '月增減', '去年同期', '年增減']);
    });

    it('hides an empty last-year column with a note', async () => {
      const { el } = await openTrend([2026, 11, 7], { toDate: FOOD, lastYearCount: 0 });
      expect(texts(el, '.tr-table thead th')).toEqual(['類別', '本月至今（11/1–11/7）', '上月同期', '月增減']);
      expect(el.querySelector('.tr-hidden-note')?.textContent?.trim()).toBe('去年同期沒有記錄，已隱藏比較欄');
    });

    it('hides an empty previous column with a note', async () => {
      const { el } = await openTrend([2026, 11, 7], { toDate: FOOD, previousCount: 0 });
      expect(texts(el, '.tr-table thead th')).toEqual(['類別', '本月至今（11/1–11/7）', '去年同期', '年增減']);
      expect(el.querySelector('.tr-hidden-note')?.textContent?.trim()).toBe('上一期沒有記錄，已隱藏比較欄');
    });

    it('names both empty windows when neither comparison has records', async () => {
      const { el } = await openTrend([2026, 11, 7], { toDate: FOOD, previousCount: 0, lastYearCount: 0 });
      expect(texts(el, '.tr-table thead th')).toEqual(['類別', '本月至今（11/1–11/7）']);
      expect(texts(el, '.tr-hidden-note')).toEqual(['上一期沒有記錄，已隱藏比較欄', '去年同期沒有記錄，已隱藏比較欄']);
    });

    describe('a running year', () => {
      const YEAR = '/accounting/reports?year=2026&tab=trend';

      it('shows one 去年同期 column read from the last-year response', async () => {
        const { el } = await openTrend([2026, 11, 7], { toDate: [row('2', '餐飲', 4233, 9, 3000)], lastYearRows: [row('2', '餐飲', 4233, 9, 7777)] }, YEAR);
        const heads = texts(el, '.tr-table thead th');
        expect(heads).toHaveLength(4);
        expect(heads.slice(2)).toEqual(['去年同期', '增減']);
        expect(texts(el, '.tr-table tbody tr:first-child td')).toEqual(['4,233', '7,777', '▼ 3,544 (45.6%)']);
        expect(el.querySelector('.tr-table tbody tr td.tr-change')?.getAttribute('data-label')).toBe('比去年同期');
        expect(el.querySelector('.tr-hidden-note')).toBeNull();
      });

      it('drops the column with the 去年同期 note when last year has no records', async () => {
        const { el } = await openTrend([2026, 11, 7], { toDate: FOOD, lastYearCount: 0 }, YEAR);
        expect(texts(el, '.tr-table thead th')).toHaveLength(2);
        expect(el.querySelector('.tr-hidden-note')?.textContent?.trim()).toBe('去年同期沒有記錄，已隱藏比較欄');
      });
    });

    it('names rows after today under the table and switches to the whole month', async () => {
      const { harness, el } = await openTrend([2026, 11, 7], { toDate: [row('2', '餐飲', 4233, 9, 3000)], whole: [row('2', '餐飲', 17653, 11)] });
      const page = harness.routeDebugElement!.componentInstance as AccountingReportsComponent;
      const after = el.querySelector<HTMLButtonElement>('button.tr-after')!;
      expect(after.textContent?.trim()).toBe('另有 11/7 之後已記的 2 筆 $13,420 未計入');

      after.click();
      await harness.fixture.whenStable();
      harness.detectChanges();
      expect(page.wholeView()).toBe(true);
      for (const request of pending().trends) request.flush(trendOf(SERIES));
      harness.detectChanges();
      expect(texts(el, '.tr-table thead th')).toEqual(['類別', '本月']);
      expect(texts(el, '.tr-table tbody tr:last-child td')).toEqual(['17,653']);
      expect(el.querySelector('button.tr-after')?.textContent?.trim()).toBe('改回截至 11/7');
    });

    it('has no 「另有」 line when nothing is dated after today', async () => {
      const { el } = await openTrend([2026, 11, 7], { toDate: FOOD, whole: FOOD });
      expect(el.querySelector('.tr-after')).toBeNull();
    });

    it('reads the 合計 row as a delta on a first day with no entries (F4)', async () => {
      const { el } = await openTrend([2026, 11, 1], { toDate: [], whole: [row('2', '餐飲', 500, 1)], previousTotal: 500, lastYearTotal: 800 });
      const total = Array.from(el.querySelectorAll('.tr-table tbody tr')).at(-1)!;
      expect(texts(total, 'td')).toEqual(['0', '500', '▼ 500 (100.0%)', '800', '▼ 800 (100.0%)']);
      expect(total.textContent).not.toContain('停止');
    });

    it('titles the comparison after the to-date columns, and drops the comparison wording in the whole view (M5)', async () => {
      const { harness, el } = await openTrend([2026, 11, 7], { toDate: FOOD, whole: [row('2', '餐飲', 17653, 11)] });
      expect(el.querySelector('section[aria-label="比較"] h2')?.textContent?.trim()).toBe('比較 · 本月至今 對 上月同期、對 去年同期');
      el.querySelector<HTMLButtonElement>('button.tr-after')!.click();
      harness.detectChanges();
      for (const request of pending().trends) request.flush(trendOf(SERIES));
      harness.detectChanges();
      const title = el.querySelector('section[aria-label="比較"] h2')?.textContent?.trim();
      expect(title).toBe('類別支出 · 2026 年 11 月');
    });

    it('titles a running year 本期至今 對 去年同期', async () => {
      const { el } = await openTrend([2026, 11, 7], { toDate: FOOD }, '/accounting/reports?year=2026&tab=trend');
      expect(el.querySelector('section[aria-label="比較"] h2')?.textContent?.trim()).toBe('比較 · 本期至今 對 去年同期');
    });

    it('offers 改回截至 in the whole view though no expense is dated after today (I2)', async () => {
      const { harness, el } = await openTrend([2026, 11, 7], { toDate: FOOD, whole: FOOD });
      const page = harness.routeDebugElement!.componentInstance as AccountingReportsComponent;
      page.toggleWhole();
      harness.detectChanges();
      for (const request of pending().trends) request.flush(trendOf(SERIES));
      harness.detectChanges();
      expect(el.querySelector('button.tr-after')?.textContent?.trim()).toBe('改回截至 11/7');
    });
  });
});
