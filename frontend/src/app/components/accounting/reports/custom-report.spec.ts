import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, TestRequest, provideHttpClientTesting } from '@angular/common/http/testing';
import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { BreakdownQuery, ReportCompare, ReportDimension, ReportMetric, SavedReport } from '../../../models/reports.model';
import { LayoutMode, LayoutModeService, MATCH_MEDIA } from '../../../services/layout-mode.service';
import { CustomReportComponent } from './custom-report';
import { SampleSavedReportStore, sampleBreakdown } from './sample-report-data';

@Component({ template: '' })
class BlankPage {}

const SAVED = '/api/accounting/reports/saved';
const BREAKDOWN = '/api/accounting/reports/breakdown';
const ROOTS = [
  { id: 2, kind: 'expense', parent_id: null, name: '餐飲', icon: null, color: null, sort_order: 0, is_hidden: false, default_account_id: null, default_project_id: null, children: [
    { id: 201, kind: 'expense', parent_id: 2, name: '午餐', icon: null, color: null, sort_order: 0, is_hidden: false, default_account_id: null, default_project_id: null, children: [] },
  ] },
  { id: 6, kind: 'expense', parent_id: null, name: '訂閱', icon: null, color: null, sort_order: 1, is_hidden: false, default_account_id: null, default_project_id: null, children: [] },
];
const PROJECTS = [
  { id: 1, name: '東京旅行', is_archived: true, sort_order: 0, moze_id: null },
  { id: 3, name: '京都 2024', is_archived: true, sort_order: 1, moze_id: null },
  { id: 4, name: '大阪 2025', is_archived: true, sort_order: 2, moze_id: null },
];

function queryOf(request: TestRequest): BreakdownQuery {
  const params = request.request.params;
  const numbers = (name: string) => (params.has(name) ? params.getAll(name)!.map(Number) : undefined);
  return {
    date_from: params.get('date_from')!,
    date_to: params.get('date_to')!,
    metric: (params.get('metric') ?? 'expense') as ReportMetric,
    group_by: params.get('group_by') as ReportDimension,
    split_by: (params.get('split_by') as ReportDimension | null) ?? undefined,
    compare: (params.get('compare') as ReportCompare | null) ?? undefined,
    category_id: numbers('category_id'),
    project_id: numbers('project_id'),
    weekday: numbers('weekday'),
    include_excluded_projects: params.has('include_excluded_projects') ? params.get('include_excluded_projects') === 'true' : undefined,
  };
}

describe('CustomReportComponent', () => {
  let http: HttpTestingController;

  function configure(): void {
    TestBed.configureTestingModule({
      providers: [
        provideRouter([
          { path: 'accounting/reports/custom/new', component: CustomReportComponent },
          { path: 'accounting/reports/custom/:id', component: CustomReportComponent },
          { path: '**', component: BlankPage },
        ]),
        provideHttpClient(),
        provideHttpClientTesting(),
        { provide: MATCH_MEDIA, useValue: null },
      ],
    });
    http = TestBed.inject(HttpTestingController);
  }

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date(2026, 9, 31, 10, 0));
    configure();
  });

  afterEach(() => {
    http.verify({ ignoreCancelled: true });
    vi.useRealTimers();
  });

  const texts = (el: Element, selector: string) => Array.from(el.querySelectorAll(selector)).map(node => node.textContent?.replace(/\s+/g, ' ').trim());
  const button = (el: Element, selector: string, label: string) => Array.from(el.querySelectorAll<HTMLButtonElement>(selector)).find(b => b.textContent?.trim() === label)!;

  /** Opens the editor and answers the saved list and the reference lists. */
  async function open(url: string, mode: LayoutMode = 'panes', saved: SavedReport[] = new SampleSavedReportStore().list()) {
    TestBed.inject(LayoutModeService).set(mode);
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(url);
    harness.detectChanges();
    http.expectOne(r => r.url === SAVED).flush(saved);
    const roots = http.expectOne(r => r.url === '/api/accounting/categories');
    expect(roots.request.params.get('kind')).toBe('expense');
    roots.flush(ROOTS);
    http.expectOne(r => r.url === '/api/accounting/accounts').flush([]);
    http.expectOne('/api/accounting/projects').flush(PROJECTS);
    harness.detectChanges();
    return { harness, el: harness.routeNativeElement as HTMLElement };
  }

  /** Answers every pending breakdown read (a running period asks for several) and returns the first one's query. */
  function answerPreviews(expected: number): BreakdownQuery[] {
    const requests = http.match(r => r.url === BREAKDOWN);
    expect(requests).toHaveLength(expected);
    const queries = requests.map(queryOf);
    requests.forEach((request, index) => request.flush(sampleBreakdown(queries[index])));
    return queries;
  }
  function answerPreview(expected = 1): BreakdownQuery {
    return answerPreviews(expected)[0];
  }

  it('opens 週末外食 with its config resolved as of today, and previews figures, chart and table', async () => {
    const { harness, el } = await open('/accounting/reports/custom/1');
    const query = answerPreview();
    harness.detectChanges();

    expect(query).toEqual({
      date_from: '2026-05-01', date_to: '2026-10-31', metric: 'expense', group_by: 'month', split_by: 'subcategory', compare: 'last_year',
      category_id: [2], project_id: undefined, weekday: [6, 7], include_excluded_projects: undefined,
    });
    expect(el.querySelector<HTMLInputElement>('.cr-name')?.value).toBe('週末外食');
    expect(el.querySelector('.cr-sentence')?.textContent?.trim()).toBe('近 6 個月 · 支出 · 依月份 · 細分到子類別 · 類別 餐飲 且 星期 六、日 · 和去年同期比較');
    expect(el.querySelector('.cr-hint')?.textContent?.trim()).toBe('2026/05/01 – 2026/10/31 · 相對期間：每次打開都往後滾動');
    expect(texts(el, '.cr-nav .cr-saved')).toEqual(['週末外食', '訂閱月費', '日本旅行比較']);
    expect(Array.from(el.querySelectorAll('.cr-nav .cr-saved .pi')).map(i => i.getAttribute('aria-label'))).toEqual(['已釘選到報表分頁', '已釘選到報表分頁']);
    expect(el.querySelector('.cr-nav .cr-saved.on')?.getAttribute('aria-current')).toBe('page');
    expect(texts(el, '.cr-chips .cr-chip')).toEqual(['類別：餐飲✕', '星期：六、日✕']);

    const charts = Array.from(el.querySelectorAll<HTMLButtonElement>('.cr-chart-seg button'));
    expect(charts.map(c => c.getAttribute('aria-checked'))).toEqual(['true', 'false', 'false', 'false']);
    expect(charts[2].getAttribute('aria-disabled')).toBe('true');
    expect(charts[2].getAttribute('title')).toBe('有細分時不能用圓環');
    expect(charts[1].getAttribute('aria-disabled')).toBeNull();

    expect(texts(el, '.cr-figure')).toEqual(['合計 $17,960 ▲ 80 (0.4%) 比去年同期', '平均每月 $2,993 6 組', '每筆平均 $339 53 筆', '最高 7 月 $3,540']);
    expect(el.querySelector('.cr-figure-foot')?.classList.contains('more')).toBe(true);
    expect(texts(el, '.cr-legend > span')).toEqual(['晚餐', '午餐', '宵夜', '去年同期']);
    // Six stacked bars of three segments plus six comparison outlines.
    expect(el.querySelectorAll('.cr-chart rect')).toHaveLength(24);
    expect(el.querySelectorAll('.cr-chart rect.cr-outline')).toHaveLength(6);
    expect(texts(el, '.cr-table thead th')).toEqual(['月份', '晚餐', '午餐', '宵夜', '合計', '筆數', '去年同期', '增減']);
    const rows = Array.from(el.querySelectorAll('.cr-table tbody tr'));
    expect(rows.map(row => row.querySelector('th')?.textContent?.trim())).toEqual(['5 月', '6 月', '7 月', '8 月', '9 月', '10 月', '合計']);
    expect(texts(rows[0], 'td')).toEqual(['1,820', '980', '320', '3,120', '9', '2,610', '▲ 510']);
    expect(texts(rows[6], 'td')).toEqual(['10,700', '5,510', '1,750', '17,960', '53', '17,880', '▲ 80']);
    expect(rows[6].classList.contains('total')).toBe(true);
  });

  it('rolls the relative period forward: opened on 2026-12-02 it covers July to December', async () => {
    vi.setSystemTime(new Date(2026, 11, 2, 9, 0));
    const { harness } = await open('/accounting/reports/custom/1');
    const queries = answerPreviews(3);
    harness.detectChanges();
    // The period runs (July 1 – December 31): the first read is cut at today, the second one covers it all, the third is December 1–2's base.
    expect(queries.map(q => [q.date_from, q.date_to, q.compare])).toEqual([
      ['2026-07-01', '2026-12-02', 'last_year'],
      ['2026-07-01', '2026-12-31', undefined],
      ['2026-11-01', '2026-11-02', undefined],
    ]);
  });

  it('saves with PUT, saves a copy with POST and opens it, deletes after a second tap', async () => {
    const { harness, el } = await open('/accounting/reports/custom/1');
    answerPreview();
    harness.detectChanges();
    const router = TestBed.inject(Router);

    const name = el.querySelector<HTMLInputElement>('.cr-name')!;
    name.value = '週末外食 2';
    name.dispatchEvent(new Event('input'));
    harness.detectChanges();
    button(el, '.cr-actions button', '儲存').click();
    const put = http.expectOne(r => r.method === 'PUT' && r.url === `${SAVED}/1`);
    expect(put.request.body).toMatchObject({ name: '週末外食 2', chart: 'bar', pinned: true, config: { v: 1, period: { preset: 'last_6_months' }, group_by: 'month' } });
    put.flush({ ...put.request.body, id: 1, sort_order: 0 });
    harness.detectChanges();
    expect(texts(el, '.cr-nav .cr-saved')[0]).toBe('週末外食 2');
    http.expectNone(r => r.url === BREAKDOWN);

    button(el, '.cr-actions button', '另存新報表').click();
    const post = http.expectOne(r => r.method === 'POST' && r.url === SAVED);
    // The name is taken by the report just saved: the copy gets （複本）.
    expect(post.request.body.name).toBe('週末外食 2（複本）');
    post.flush({ ...post.request.body, id: 9, sort_order: 3 });
    await harness.fixture.whenStable();
    harness.detectChanges();
    expect(router.url).toBe('/accounting/reports/custom/9');
    http.expectOne(r => r.url === BREAKDOWN).flush(sampleBreakdown({ date_from: '2026-05-01', date_to: '2026-10-31', group_by: 'month' }));
    harness.detectChanges();

    const del = button(el, '.cr-actions button', '刪除');
    del.click();
    harness.detectChanges();
    expect(del.textContent?.trim()).toBe('確定刪除');
    http.expectNone(r => r.method === 'DELETE');
    del.click();
    http.expectOne(r => r.method === 'DELETE' && r.url === `${SAVED}/9`).flush(null);
    await harness.fixture.whenStable();
    expect(router.url).toBe('/accounting/reports');
  });

  it('starts a new report from the empty state; a template fills the conditions and runs the preview', async () => {
    const { harness, el } = await open('/accounting/reports/custom/new');
    http.expectNone(r => r.url === BREAKDOWN);
    expect(el.querySelector('.cr-empty b')?.textContent?.trim()).toBe('從一個問題開始');
    expect(texts(el, '.cr-templates button')).toEqual(['週末外食', '訂閱月費', '日本旅行比較']);
    expect(el.querySelector('.cr-sentence')?.textContent?.trim()).toBe('還沒有條件');
    expect(el.querySelector<HTMLButtonElement>('.cr-save')?.disabled).toBe(true);
    expect(el.querySelector('.cr-save-as')).toBeNull();

    button(el, '.cr-templates button', '日本旅行比較').click();
    harness.detectChanges();
    const query = answerPreview();
    harness.detectChanges();
    expect(query).toMatchObject({ group_by: 'project', split_by: 'category', project_id: [3, 4, 1], include_excluded_projects: true, date_from: '2000-01-01', date_to: '2026-10-31' });
    expect(el.querySelector<HTMLInputElement>('.cr-name')?.value).toBe('日本旅行比較');
    expect(texts(el, '.cr-chips .cr-chip')).toEqual(['專案：京都 2024、大阪 2025、東京旅行✕']);
    expect(Array.from(el.querySelectorAll('.cr-table tbody th')).map(th => th.textContent?.trim())).toEqual(['京都 2024', '大阪 2025', '東京旅行', '合計']);
    expect(el.querySelector<HTMLButtonElement>('.cr-save')?.disabled).toBe(false);

    button(el, '.cr-actions button', '儲存').click();
    const post = http.expectOne(r => r.method === 'POST' && r.url === SAVED);
    expect(post.request.body).toMatchObject({ name: '日本旅行比較', pinned: false, chart: 'bar' });
    post.flush({ ...post.request.body, id: 4, sort_order: 3 });
    await harness.fixture.whenStable();
    expect(TestBed.inject(Router).url).toBe('/accounting/reports/custom/4');
    // `custom/new` and `custom/:id` are two routes: the editor is created afresh and reads again.
    harness.detectChanges();
    http.expectOne(r => r.url === SAVED).flush([...new SampleSavedReportStore().list(), { ...post.request.body, id: 4, sort_order: 3 }]);
    http.expectOne(r => r.url === '/api/accounting/categories').flush(ROOTS);
    http.expectOne(r => r.url === '/api/accounting/accounts').flush([]);
    http.expectOne('/api/accounting/projects').flush(PROJECTS);
    harness.detectChanges();
    expect(answerPreview().project_id).toEqual([3, 4, 1]);
    harness.detectChanges();
    expect(harness.routeNativeElement?.querySelector<HTMLInputElement>('.cr-name')?.value).toBe('日本旅行比較');
  });

  it('offers 圓環 for a single dimension and re-runs the preview when a condition changes', async () => {
    const { harness, el } = await open('/accounting/reports/custom/2');
    answerPreview(2);
    harness.detectChanges();
    const charts = Array.from(el.querySelectorAll<HTMLButtonElement>('.cr-chart-seg button'));
    expect(charts[2].getAttribute('aria-disabled')).toBeNull();
    expect(charts[1].getAttribute('title')).toBe('只有依月份、週、日分組才能用折線');
    charts[2].click();
    harness.detectChanges();
    expect(el.querySelector('.cr-donut')).not.toBeNull();
    expect(texts(el, '.cr-legend > span')[0]).toBe('Netflix 36.2%');

    const split = el.querySelector<HTMLSelectElement>('.cr-split-by')!;
    split.value = 'category';
    split.dispatchEvent(new Event('change'));
    harness.detectChanges();
    const query = answerPreview(2);
    harness.detectChanges();
    expect(query.split_by).toBe('category');
    // The donut no longer fits: the chart falls back to bars.
    expect(el.querySelector('.cr-donut')).toBeNull();
    expect(el.querySelector('.cr-chart-seg button[aria-checked="true"]')?.textContent?.trim()).toBe('長條');

    button(el, '.cr-chip button', '✕').click();
    harness.detectChanges();
    expect(answerPreview(2).category_id).toBeUndefined();
  });

  it('takes 合計 and its comparison from totals, and notes overlapping tag rows under the row sum', async () => {
    const { harness, el } = await open('/accounting/reports/custom/1');
    answerPreview();
    harness.detectChanges();
    expect(el.querySelector('.cr-overlap')).toBeNull();

    const group = el.querySelector<HTMLSelectElement>('.cr-group-by')!;
    group.value = 'tag';
    group.dispatchEvent(new Event('change'));
    harness.detectChanges();
    const request = http.expectOne(r => r.url === BREAKDOWN);
    expect(queryOf(request).group_by).toBe('tag');
    const row = (key: string, amount: string, count: number, compare: string) => ({
      key, label: key, color: null, amount, refund: '0', count, avg: '300', median: null, min: null, max: null,
      first_date: null, last_date: null, avg_interval_days: null, compare_amount: compare, splits: [],
    });
    // One 600 entry tagged 家用 and 朋友 counts in both rows; the period is 900 over 3 entries (700 last year).
    request.flush({
      currency: 'TWD', date_from: '2026-05-01', date_to: '2026-10-31',
      totals: { expense: '900', income: '0', net: '-900', count: 3, refund: '0' },
      compare: { date_from: '2025-05-01', date_to: '2025-10-31', totals: { expense: '700', income: '0', net: '-700', count: 2, refund: '0' } },
      rows: [row('家用', '600', 2, '500'), row('朋友', '600', 2, '300')],
      overlapping: true,
      missing_rates: [],
    });
    harness.detectChanges();

    expect(texts(el, '.cr-figure')).toEqual(['合計 $900 ▲ 200 (28.6%) 比去年同期', '平均每組 $450 2 組', '每筆平均 $300 3 筆', '最高 家用 $600']);
    const total = Array.from(el.querySelectorAll('.cr-table tbody tr')).at(-1)!;
    expect(texts(total, 'td')).toEqual(['1,200', '4', '800', '▲ 400']);
    expect(el.querySelector('.cr-table-card .cr-overlap')?.textContent?.trim()).toBe('一筆可有多個標籤，合計會大於支出');
  });

  it('says which saved conditions no longer exist', async () => {
    const list = new SampleSavedReportStore().list();
    list[0].stale_refs = ['category:41'];
    const { harness, el } = await open('/accounting/reports/custom/1', 'panes', list);
    answerPreview();
    harness.detectChanges();
    expect(el.querySelector('.cr-stale')?.textContent).toContain('部分條件已失效（category:41）');
  });

  it('on a phone shows the report read-only and applies 編輯條件 without saving', async () => {
    const { harness, el } = await open('/accounting/reports/custom/1', 'phone');
    answerPreview();
    harness.detectChanges();

    expect(el.querySelector('h1')?.textContent?.trim()).toBe('週末外食');
    expect(el.querySelector('.cr-save')).toBeNull();
    expect(el.querySelector('.cr-name')).toBeNull();
    expect(texts(el, '.cr-figure')).toHaveLength(4);
    expect(el.querySelector('.cr-chart')?.getAttribute('viewBox')).toBe('0 0 358 226');

    button(el, 'button', '編輯條件').click();
    harness.detectChanges();
    const sheet = el.querySelector('[role="dialog"]')!;
    expect(texts(sheet, '.cr-field > span')).toEqual(['期間', '看什麼', '依什麼分組', '再細分', '篩選', '比較']);
    const compare = sheet.querySelectorAll<HTMLSelectElement>('select')[4];
    compare.value = '';
    compare.dispatchEvent(new Event('change'));
    harness.detectChanges();
    button(sheet, 'button', '套用').click();
    harness.detectChanges();
    expect(el.querySelector('[role="dialog"]')).toBeNull();
    const query = answerPreview();
    expect(query.compare).toBeUndefined();
    http.expectNone(r => r.method === 'PUT');
  });

  it('on a phone a new report points at the desktop', async () => {
    const { el } = await open('/accounting/reports/custom/new', 'phone');
    expect(el.querySelector('h1')?.textContent?.trim()).toBe('新報表');
    expect(el.querySelector('.cr-note')?.textContent).toContain('電腦版');
    http.expectNone(r => r.url === BREAKDOWN);
  });

  // ---- to date, running bucket, labels, empty columns (polish D1, D3, D4, D7) ----------------------------------------

  const NOV7 = new Date(2026, 10, 7, 10, 0);
  const row = (key: string, amount: number, count: number, compare?: number) => ({
    key, label: key, color: null, amount: String(amount), refund: '0', count, avg: '0', median: null, min: null, max: null,
    first_date: null, last_date: null, avg_interval_days: null, compare_amount: compare === undefined ? undefined : String(compare), splits: [],
  });
  const answer = (rows: ReturnType<typeof row>[], total: number, count: number, compare?: { total: number; count: number }) => ({
    currency: 'TWD', date_from: '', date_to: '',
    totals: { expense: String(total), income: '0', net: '0', count, refund: '0' },
    compare: compare ? { date_from: '', date_to: '', totals: { expense: String(compare.total), income: '0', net: '0', count: compare.count, refund: '0' } } : null,
    rows, overlapping: false, missing_rates: [],
  });
  const reportWith = (patch: Record<string, unknown>): SavedReport[] => {
    const list = new SampleSavedReportStore().list();
    list[0].config = { ...list[0].config, split_by: undefined, ...patch } as SavedReport['config'];
    return list;
  };
  const wholeMonths = (november: [number, number]) => [
    row('2026-06-01', 300, 3, 250), row('2026-07-01', 400, 4, 300), row('2026-08-01', 500, 5, 350), row('2026-09-01', 367, 3, 400), row('2026-10-01', 450, 4, 400),
    row('2026-11-01', november[0], november[1], 999),
  ];

  /** Opens 週末外食 on 2026-11-07 with a comparison and answers its three reads; returns the queries in request order. */
  async function openRunning(compare: ReportCompare, metric: ReportMetric = 'expense') {
    vi.setSystemTime(NOV7);
    const { harness, el } = await open('/accounting/reports/custom/1', 'panes', reportWith({ compare, metric }));
    const requests = http.match(r => r.url === BREAKDOWN);
    const queries = requests.map(queryOf);
    requests.forEach((request, index) => {
      const query = queries[index];
      if (query.compare) request.flush(answer(wholeMonths([120, 2]), 2137, 21, { total: 2000, count: 15 }));
      else if (query.date_to === '2026-11-30') request.flush(answer(wholeMonths([180, 3]), 2197, 22));
      else request.flush(answer([row('2026-10-01', 90, 2)], 90, 2));
    });
    harness.detectChanges();
    return { harness, el, queries };
  }

  describe('axis labels and the running hatch (WebKit)', () => {
    const axis = (el: Element) => Array.from(el.querySelectorAll('.cr-chart .cr-x'));

    it('draws a two-line month axis over two years: the year only on the first bar and each January', async () => {
      const { harness, el } = await open('/accounting/reports/custom/1', 'panes', reportWith({ period: { preset: 'last_12_months' }, compare: undefined }));
      answerPreview();
      harness.detectChanges();

      expect(axis(el)).toHaveLength(12);
      expect(texts(el, '.cr-chart .cr-x .cr-x-month')).toEqual(['11 月', '12 月', '1 月', '2 月', '3 月', '4 月', '5 月', '6 月', '7 月', '8 月', '9 月', '10 月']);
      expect(texts(el, '.cr-chart .cr-x .cr-x-year')).toEqual(['2025', '2026']);
      const labels = axis(el);
      expect(labels[0].querySelector('.cr-x-year')).not.toBeNull();
      expect(labels[2].querySelector('.cr-x-year')).not.toBeNull();
      expect(labels[5].querySelector('.cr-x-year')).toBeNull();
      // The year tspan restarts at the label's x and drops a line.
      const year = labels[0].querySelector('.cr-x-year')!;
      expect(year.getAttribute('x')).toBe(labels[0].getAttribute('x'));
      expect(year.getAttribute('dy')).not.toBeNull();
      // The table keeps the full form.
      expect(texts(el, '.cr-table tbody th')[0]).toBe('2025 年 11 月');
    });

    it('shows the year on the first bar only when the report stays in one year', async () => {
      const { harness, el } = await open('/accounting/reports/custom/1');
      answerPreview();
      harness.detectChanges();
      expect(texts(el, '.cr-chart .cr-x .cr-x-month')).toEqual(['5 月', '6 月', '7 月', '8 月', '9 月', '10 月']);
      expect(texts(el, '.cr-chart .cr-x .cr-x-year')).toEqual(['2026']);
    });

    it('keeps the one-line short form for weeks, without a year line', async () => {
      const { harness, el } = await open('/accounting/reports/custom/1', 'panes', reportWith({ period: { preset: 'last_6_months' }, group_by: 'week', compare: undefined }));
      const request = http.match(r => r.url === BREAKDOWN)[0];
      request.flush(answer([row('2026-10-05', 100, 1), row('2026-10-12', 200, 2)], 300, 3));
      harness.detectChanges();
      expect(texts(el, '.cr-chart .cr-x')).toEqual(['10/5 週', '10/12 週']);
      expect(el.querySelector('.cr-chart .cr-x-year')).toBeNull();
    });

    it('draws the running bucket solid with a dashed 預估 box on its stack, explained under the chart', async () => {
      const { el } = await openRunning('previous');
      const boxes = el.querySelectorAll('.cr-chart .cr-forecast');
      expect(boxes).toHaveLength(1);
      const bars = Array.from(el.querySelectorAll('.cr-chart rect:not(.cr-outline):not(.cr-forecast)'));
      const november = bars.at(-1)!;
      expect(boxes[0].getAttribute('x')).toBe(november.getAttribute('x'));
      expect(Number(boxes[0].getAttribute('y')) + Number(boxes[0].getAttribute('height'))).toBeCloseTo(Number(november.getAttribute('y')), 0);
      expect(el.querySelector('.cr-chart .cr-hatch')).toBeNull();
      // 120 + (2,137 − 120) / 5 × 23/30 = 429; the whole November read holds 180, below it.
      expect(el.querySelector('.cr-forecast-note')?.textContent?.trim()).toBe('11 月預估 $429 ＝ 至 11/7 $120 ＋ 平均 $403 × 剩 23/30 天');
      expect(texts(el, '.cr-legend > span')).toContain('預估');
      expect(el.querySelector('.cr-chart')?.getAttribute('aria-label')).toContain('預估 429');
    });

    it('counts the forecast in 筆 for the count metric', async () => {
      const { el } = await openRunning('previous', 'count');
      expect(el.querySelector('.cr-forecast-note')?.textContent?.trim()).toMatch(/^11 月預估 \d+ 筆 ＝ 至 11\/7 \d+ 筆 ＋ 平均 \d+ 筆 × 剩 23\/30 天$/);
    });

    it('drops the forecast in the whole-period view', async () => {
      const { harness, el } = await openRunning('previous');
      el.querySelector<HTMLButtonElement>('button.cr-after')!.click();
      harness.detectChanges();
      for (const request of http.match(r => r.url === BREAKDOWN)) request.flush(answer(wholeMonths([180, 3]), 2197, 22));
      harness.detectChanges();
      expect(el.querySelector('.cr-chart .cr-forecast')).toBeNull();
      expect(el.querySelector('.cr-forecast-note')).toBeNull();
    });

    it('leaves 收入 without a forecast: a salary lands once early in the month, so the pace would count it twice', async () => {
      const { el } = await openRunning('previous', 'income');
      expect(el.querySelector('.cr-chart .cr-forecast')).toBeNull();
      expect(el.querySelector('.cr-forecast-note')).toBeNull();
      expect(el.querySelectorAll('.cr-chart .cr-hatch')).toHaveLength(1);
    });

    it('hatches the running bucket instead when there is nothing to forecast from (結餘), with no url(#...) reference anywhere', async () => {
      const { el } = await openRunning('previous', 'net');
      expect(el.querySelector('.cr-chart .cr-forecast')).toBeNull();
      const stripes = el.querySelectorAll('.cr-chart .cr-hatch line');
      expect(stripes.length).toBeGreaterThan(3);
      expect(el.querySelectorAll('.cr-chart .cr-hatch')).toHaveLength(1);
      expect(el.querySelector('.cr-chart pattern')).toBeNull();
      expect(el.querySelector('.cr-chart')!.outerHTML).not.toContain('url(#');
    });
  });

  it('reads November 1–7 against October 1–7 in 近 6 個月 by month', async () => {
    const { el, queries } = await openRunning('previous');

    expect(queries).toHaveLength(3);
    expect(queries[0]).toMatchObject({ date_from: '2026-06-01', date_to: '2026-11-07', compare: 'previous', group_by: 'month' });
    expect(queries[1]).toMatchObject({ date_from: '2026-06-01', date_to: '2026-11-30', compare: undefined });
    expect(queries[2]).toMatchObject({ date_from: '2026-10-01', date_to: '2026-10-07', compare: undefined, group_by: 'month' });
    expect(texts(el, '.cr-table thead th')).toEqual(['月份', '本期至今（6/1–11/7）', '筆數', '上期同期', '增減']);
    const rows = Array.from(el.querySelectorAll('.cr-table tbody tr'));
    expect(rows.map(r => r.querySelector('th')?.textContent?.trim())).toEqual(['6 月', '7 月', '8 月', '9 月', '10 月', '11 月 進行中（至 11/7）', '合計']);
    // November's change is against the short range (90), not against its compare_amount (999).
    expect(texts(rows[5], 'td')).toEqual(['120', '2', '90', '▲ 30']);
    expect(texts(rows[0], 'td')).toEqual(['300', '3', '250', '▲ 50']);
    expect(texts(rows[6], 'td')).toEqual(['2,137', '21', '2,000', '▲ 137']);
    expect(texts(el, '.cr-figure')[1]).toBe('平均每月 $403 5 組');
    expect(el.querySelectorAll('.cr-chart .cr-forecast')).toHaveLength(1);
  });

  it("uses the previous bucket's same days for the running bucket against last year too (R2)", async () => {
    const { el, queries } = await openRunning('last_year');

    expect(queries[0]).toMatchObject({ date_to: '2026-11-07', compare: 'last_year' });
    expect(queries[2]).toMatchObject({ date_from: '2026-10-01', date_to: '2026-10-07', compare: undefined });
    expect(texts(el, '.cr-table thead th')).toEqual(['月份', '本期至今（6/1–11/7）', '筆數', '去年同期', '增減']);
    const rows = Array.from(el.querySelectorAll('.cr-table tbody tr'));
    expect(texts(rows[5], 'td')).toEqual(['120', '2', '90', '▲ 30']);
    expect(texts(rows[6], 'td')).toEqual(['2,137', '21', '2,000', '▲ 137']);
  });

  it('reads 9 月 under 最高', async () => {
    const { harness, el } = await open('/accounting/reports/custom/1', 'panes', reportWith({ compare: undefined }));
    const request = http.expectOne(r => r.url === BREAKDOWN);
    request.flush(answer([row('2026-05-01', 100, 1), row('2026-06-01', 300, 3), row('2026-07-01', 200, 2), row('2026-08-01', 150, 2), row('2026-09-01', 367, 4), row('2026-10-01', 250, 3)], 1367, 15));
    harness.detectChanges();
    expect(texts(el, '.cr-figure')[3]).toBe('最高 9 月 $367');
  });

  it('hides empty last-year columns with a note and marks no 新增', async () => {
    const { harness, el } = await open('/accounting/reports/custom/1', 'panes', reportWith({ compare: 'last_year' }));
    http.expectOne(r => r.url === BREAKDOWN).flush(
      answer([row('2026-08-01', 300, 3, 0), row('2026-09-01', 367, 4, 0), row('2026-10-01', 250, 3, 0)], 917, 10, { total: 0, count: 0 }),
    );
    harness.detectChanges();
    expect(texts(el, '.cr-table thead th')).toEqual(['月份', '合計', '筆數']);
    expect(texts(el, '.cr-hidden-note')).toEqual(['去年同期沒有記錄，已隱藏比較欄']);
    expect(el.querySelector('.cr-table')?.textContent).not.toContain('新增');
    expect(el.querySelector('.cr-table-card')?.textContent).not.toContain('上期無資料');
  });

  it('does not run a week whose last day is today', async () => {
    vi.setSystemTime(new Date(2026, 10, 8, 10, 0));
    const { harness, el } = await open('/accounting/reports/custom/1', 'panes', reportWith({ period: { preset: 'this_month' }, group_by: 'week', compare: 'previous' }));
    const requests = http.match(r => r.url === BREAKDOWN);
    const queries = requests.map(queryOf);
    // The month runs (11/8 < 11/30) but the week of 11/2 ends today: the to-date and whole reads only.
    expect(queries.map(q => [q.date_from, q.date_to, q.compare])).toEqual([
      ['2026-11-01', '2026-11-08', 'previous'],
      ['2026-11-01', '2026-11-30', undefined],
    ]);
    requests[0].flush(answer([row('2026-10-26', 80, 1, 70), row('2026-11-02', 120, 2, 100)], 200, 3, { total: 170, count: 3 }));
    requests[1].flush(answer([row('2026-10-26', 80, 1), row('2026-11-02', 120, 2)], 200, 3));
    harness.detectChanges();
    expect(texts(el, '.cr-table tbody th')).toEqual(['10/26 週', '11/2 週', '合計']);
    expect(el.querySelector('.cr-table-card')?.textContent).not.toContain('進行中');
    expect(el.querySelector('.cr-after')).toBeNull();
  });

  it('names rows after today and switches to the whole period', async () => {
    const { harness, el } = await openRunning('previous');
    const after = el.querySelector<HTMLButtonElement>('.cr-after')!;
    expect(after.textContent?.trim()).toBe('另有 11/7 之後已記的 1 筆 $60 未計入');
    after.click();
    harness.detectChanges();
    expect(el.querySelector('.cr-after')?.textContent?.trim()).toBe('改回截至 11/7');
    expect(texts(el, '.cr-table thead th')).toEqual(['月份', '合計', '筆數']);
    const rows = Array.from(el.querySelectorAll('.cr-table tbody tr'));
    expect(texts(rows[5], 'th')).toEqual(['11 月']);
    expect(texts(rows[5], 'td')).toEqual(['180', '3']);
    expect(texts(rows[6], 'td')).toEqual(['2,197', '22']);
    el.querySelector<HTMLButtonElement>('.cr-after')!.click();
    harness.detectChanges();
    expect(el.querySelector('.cr-after')?.textContent?.trim()).toBe('另有 11/7 之後已記的 1 筆 $60 未計入');
    expect(texts(el, '.cr-table tbody th')[5]).toBe('11 月 進行中（至 11/7）');
  });

  it('words the 另有 line without an amount for the count metric', async () => {
    const { el } = await openRunning('previous', 'count');
    expect(el.querySelector('.cr-after')?.textContent?.trim()).toBe('另有 11/7 之後已記的 1 筆未計入');
  });

  // ---- fix round 1 ---------------------------------------------------------------------------------------------------

  const thirdRead = (queries: BreakdownQuery[]) => queries.filter(q => !q.compare)[1] ?? null;

  it('clips the running base to the period: this month by week on a Thursday reads 11/24–11/26', async () => {
    vi.setSystemTime(new Date(2026, 11, 3, 10, 0));
    const { harness } = await open('/accounting/reports/custom/1', 'panes', reportWith({ period: { preset: 'this_month' }, group_by: 'week', compare: 'previous' }));
    const queries = answerPreviews(3);
    harness.detectChanges();
    expect([thirdRead(queries)!.date_from, thirdRead(queries)!.date_to, thirdRead(queries)!.split_by]).toEqual(['2026-11-24', '2026-11-26', undefined]);
  });

  it('reads a running week against the 7 days before it, and labels it', async () => {
    vi.setSystemTime(NOV7);
    const { harness, el } = await open('/accounting/reports/custom/1', 'panes', reportWith({ period: { preset: 'this_month' }, group_by: 'week', compare: 'previous' }));
    const requests = http.match(r => r.url === BREAKDOWN);
    const queries = requests.map(queryOf);
    expect(queries).toHaveLength(3);
    expect([queries[2].date_from, queries[2].date_to]).toEqual(['2026-10-26', '2026-10-31']);
    requests[0].flush(answer([row('2026-10-26', 80, 1, 70), row('2026-11-02', 120, 2, 999)], 200, 3, { total: 170, count: 3 }));
    requests[1].flush(answer([row('2026-10-26', 80, 1), row('2026-11-02', 120, 2)], 200, 3));
    requests[2].flush(answer([row('2026-10-26', 100, 2)], 100, 2));
    harness.detectChanges();
    const rows = Array.from(el.querySelectorAll('.cr-table tbody tr'));
    expect(texts(rows[1], 'th')).toEqual(['11/2 週 進行中（至 11/7）']);
    expect(texts(rows[1], 'td')).toEqual(['120', '2', '100', '▲ 20']);
  });

  it('clips a custom range that starts mid-month: 11/5–11/30 on 11/7 reads against 10/5–10/7', async () => {
    vi.setSystemTime(NOV7);
    const { harness } = await open('/accounting/reports/custom/1', 'panes', reportWith({ period: { from: '2026-11-05', to: '2026-11-30' }, group_by: 'month', compare: 'previous' }));
    const queries = answerPreviews(3);
    harness.detectChanges();
    expect([thirdRead(queries)!.date_from, thirdRead(queries)!.date_to]).toEqual(['2026-10-05', '2026-10-07']);
  });

  it('keeps the compare.totals base for a month report split by tag', async () => {
    const { harness, el } = await open('/accounting/reports/custom/1', 'panes', reportWith({ split_by: 'tag', compare: 'last_year' }));
    const request = http.expectOne(r => r.url === BREAKDOWN);
    // Tag-split rows overlap (`overlapping`), yet the grouping is time: the row compare amounts add up to 800, compare.totals say 700.
    request.flush({ ...answer([row('2026-09-01', 300, 3, 500), row('2026-10-01', 400, 4, 300)], 700, 7, { total: 700, count: 6 }), overlapping: true });
    harness.detectChanges();
    const total = Array.from(el.querySelectorAll('.cr-table tbody tr')).at(-1)!;
    expect(texts(total, 'td').slice(-2)).toEqual(['700', '—']);
  });

  it('keeps 另有 reachable when nothing is dated up to today', async () => {
    vi.setSystemTime(NOV7);
    const { harness, el } = await open('/accounting/reports/custom/1', 'panes', reportWith({ compare: undefined }));
    const requests = http.match(r => r.url === BREAKDOWN);
    expect(requests).toHaveLength(2);
    requests[0].flush(answer([], 0, 0));
    requests[1].flush(answer([row('2026-11-01', 180, 2)], 180, 2));
    harness.detectChanges();
    expect(el.querySelector('.cr-preview .cr-note')?.textContent?.trim()).toBe('這些條件下沒有記錄');
    const after = el.querySelector<HTMLButtonElement>('.cr-after')!;
    expect(after.textContent?.trim()).toBe('另有 11/7 之後已記的 2 筆 $180 未計入');
    after.click();
    harness.detectChanges();
    expect(texts(el, '.cr-table tbody th')[0]).toBe('11 月');
  });

  // ---- B2 final fix wave ---------------------------------------------------------------------------------------------

  it('asks a running year compared with the previous period for last year, the window its label names (F2)', async () => {
    vi.setSystemTime(NOV7);
    const { harness, el } = await open('/accounting/reports/custom/1', 'panes', reportWith({ period: { preset: 'this_year' }, compare: 'previous' }));
    const requests = http.match(r => r.url === BREAKDOWN);
    const queries = requests.map(queryOf);
    expect(queries[0]).toMatchObject({ date_from: '2026-01-01', date_to: '2026-11-07', compare: 'last_year' });
    expect(queries[1]).toMatchObject({ date_from: '2026-01-01', date_to: '2026-12-31', compare: undefined });
    requests.forEach((request, index) => request.flush(queries[index].compare ? answer(wholeMonths([120, 2]), 2137, 21, { total: 2000, count: 15 }) : answer(wholeMonths([120, 2]), 2137, 21)));
    harness.detectChanges();
    expect(texts(el, '.cr-table thead th')).toEqual(['月份', '本期至今（1/1–11/7）', '筆數', '去年同期', '增減']);
  });

  it('reads the 合計 row and figure as a delta against a window with entries but a 0 total (F4)', async () => {
    const { harness, el } = await open('/accounting/reports/custom/1', 'panes', reportWith({ compare: 'last_year' }));
    http.expectOne(r => r.url === BREAKDOWN).flush(
      answer([row('2026-08-01', 300, 3, 0), row('2026-09-01', 367, 4, 0), row('2026-10-01', 250, 3, 0)], 917, 10, { total: 0, count: 3 }),
    );
    harness.detectChanges();
    const total = Array.from(el.querySelectorAll('.cr-table tbody tr')).at(-1)!;
    expect(texts(total, 'td')).toEqual(['917', '10', '0', '▲ 917']);
    expect(total.querySelector('td.cr-col-change')?.classList.contains('more')).toBe(true);
    expect(texts(el, '.cr-figure-foot')[0]).toBe('▲ 917 比去年同期');
  });

  it('previews the to-date figures when the whole read fails, without the 另有 line (M4)', async () => {
    vi.setSystemTime(NOV7);
    const { harness, el } = await open('/accounting/reports/custom/1', 'panes', reportWith({ compare: 'previous' }));
    const requests = http.match(r => r.url === BREAKDOWN);
    const queries = requests.map(queryOf);
    requests.forEach((request, index) => {
      const query = queries[index];
      if (query.compare) request.flush(answer(wholeMonths([120, 2]), 2137, 21, { total: 2000, count: 15 }));
      else if (query.date_to === '2026-11-30') request.flush({ code: 'x', message: 'x', trace_id: 'x' }, { status: 500, statusText: 'Server Error' });
      else request.flush(answer([row('2026-10-01', 90, 2)], 90, 2));
    });
    harness.detectChanges();
    expect(el.querySelector('.cr-preview [role="alert"]')).toBeNull();
    expect(texts(el, '.cr-table tbody tr:last-child td')).toEqual(['2,137', '21', '2,000', '▲ 137']);
    expect(el.querySelector('.cr-after')).toBeNull();
  });

  it('shows no change on the running bucket when its base read fails (M4)', async () => {
    vi.setSystemTime(NOV7);
    const { harness, el } = await open('/accounting/reports/custom/1', 'panes', reportWith({ compare: 'previous' }));
    const requests = http.match(r => r.url === BREAKDOWN);
    const queries = requests.map(queryOf);
    requests.forEach((request, index) => {
      const query = queries[index];
      if (query.compare) request.flush(answer(wholeMonths([120, 2]), 2137, 21, { total: 2000, count: 15 }));
      else if (query.date_to === '2026-11-30') request.flush(answer(wholeMonths([180, 3]), 2197, 22));
      else request.flush({ code: 'x', message: 'x', trace_id: 'x' }, { status: 500, statusText: 'Server Error' });
    });
    harness.detectChanges();
    expect(el.querySelector('.cr-preview [role="alert"]')).toBeNull();
    const rows = Array.from(el.querySelectorAll('.cr-table tbody tr'));
    expect(texts(rows[5], 'th')).toEqual(['11 月 進行中（至 11/7）']);
    expect(texts(rows[5], 'td')).toEqual(['120', '2', '—', '—']);
    expect(rows[5].querySelector('td.cr-col-change')?.classList.contains('muted')).toBe(true);
    expect(texts(rows[0], 'td')).toEqual(['300', '3', '250', '▲ 50']);
    expect(el.querySelector('.cr-after')?.textContent?.trim()).toBe('另有 11/7 之後已記的 1 筆 $60 未計入');
  });

  it('ends the comparison line before a running bucket whose base read failed (N2)', async () => {
    vi.setSystemTime(NOV7);
    const list = reportWith({ compare: 'previous' });
    list[0].chart = 'line';
    const { harness, el } = await open('/accounting/reports/custom/1', 'panes', list);
    const requests = http.match(r => r.url === BREAKDOWN);
    const queries = requests.map(queryOf);
    requests.forEach((request, index) => {
      const query = queries[index];
      if (query.compare) request.flush(answer(wholeMonths([120, 2]), 2137, 21, { total: 2000, count: 15 }));
      else if (query.date_to === '2026-11-30') request.flush(answer(wholeMonths([180, 3]), 2197, 22));
      else request.flush({ code: 'x', message: 'x', trace_id: 'x' }, { status: 500, statusText: 'Server Error' });
    });
    harness.detectChanges();
    const points = el.querySelector('.cr-compare-line')?.getAttribute('points')?.trim().split(' ') ?? [];
    expect(points).toHaveLength(5);
    expect(el.querySelectorAll('.cr-line')).toHaveLength(1);
  });

  it('signs negative saved figures as −$N, not $−N', async () => {
    const list = reportWith({ metric: 'net', compare: undefined });
    const { harness, el } = await open('/accounting/reports/custom/1', 'panes', list);
    const [request] = http.match(r => r.url === BREAKDOWN);
    request.flush({
      currency: 'TWD', date_from: '', date_to: '',
      totals: { expense: '900', income: '0', net: '-900', count: 3, refund: '0' }, compare: null,
      rows: [row('2026-09-01', -300, 1), row('2026-10-01', -600, 2)], overlapping: false, missing_rates: [],
    });
    harness.detectChanges();
    const figures = texts(el, '.cr-figure');
    expect(figures[0]).toContain('−$900');
    expect(figures.join(' ')).not.toContain('$−');
    expect(figures[1]).toContain('−$450');
  });

  it('leaves the running bucket out of 平均 in the whole view too', async () => {
    const { harness, el } = await openRunning('previous');
    el.querySelector<HTMLButtonElement>('.cr-after')!.click();
    harness.detectChanges();
    expect(texts(el, '.cr-figure')[1]).toBe('平均每月 $403 5 組');
  });
});
