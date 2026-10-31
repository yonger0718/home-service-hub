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

  function answerPreview(): BreakdownQuery {
    const request = http.expectOne(r => r.url === BREAKDOWN);
    const query = queryOf(request);
    request.flush(sampleBreakdown(query));
    return query;
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

    expect(texts(el, '.cr-figure')).toEqual(['合計 $17,960 ▲ 80 (0.4%) 比去年同期', '平均每月 $2,993 6 組', '每筆平均 $339 53 筆', '最高 7月 $3,540']);
    expect(el.querySelector('.cr-figure-foot')?.classList.contains('more')).toBe(true);
    expect(texts(el, '.cr-legend > span')).toEqual(['晚餐', '午餐', '宵夜', '去年同期']);
    // Six stacked bars of three segments plus six comparison outlines.
    expect(el.querySelectorAll('.cr-chart rect')).toHaveLength(24);
    expect(el.querySelectorAll('.cr-chart rect.cr-outline')).toHaveLength(6);
    expect(texts(el, '.cr-table thead th')).toEqual(['月份', '晚餐', '午餐', '宵夜', '合計', '筆數', '去年同期', '增減']);
    const rows = Array.from(el.querySelectorAll('.cr-table tbody tr'));
    expect(rows.map(row => row.querySelector('th')?.textContent?.trim())).toEqual(['5月', '6月', '7月', '8月', '9月', '10月', '合計']);
    expect(texts(rows[0], 'td')).toEqual(['1,820', '980', '320', '3,120', '9', '2,610', '▲ 510']);
    expect(texts(rows[6], 'td')).toEqual(['10,700', '5,510', '1,750', '17,960', '53', '17,880', '▲ 80']);
    expect(rows[6].classList.contains('total')).toBe(true);
  });

  it('rolls the relative period forward: opened on 2026-12-02 it covers July to December', async () => {
    vi.setSystemTime(new Date(2026, 11, 2, 9, 0));
    const { harness } = await open('/accounting/reports/custom/1');
    const query = answerPreview();
    harness.detectChanges();
    expect([query.date_from, query.date_to]).toEqual(['2026-07-01', '2026-12-31']);
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
    answerPreview();
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
    const query = answerPreview();
    harness.detectChanges();
    expect(query.split_by).toBe('category');
    // The donut no longer fits: the chart falls back to bars.
    expect(el.querySelector('.cr-donut')).toBeNull();
    expect(el.querySelector('.cr-chart-seg button[aria-checked="true"]')?.textContent?.trim()).toBe('長條');

    button(el, '.cr-chip button', '✕').click();
    harness.detectChanges();
    expect(answerPreview().category_id).toBeUndefined();
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
    expect(el.querySelector('.cr-chart')?.getAttribute('viewBox')).toBe('0 0 358 210');

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
});
