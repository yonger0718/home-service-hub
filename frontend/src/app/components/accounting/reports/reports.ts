import { ChangeDetectionStrategy, Component, computed, effect, inject, signal, untracked } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router } from '@angular/router';
import { forkJoin } from 'rxjs';

import { BreakdownResponse, TrendResponse } from '../../../models/reports.model';
import { ReportsService } from '../../../services/reports.service';
import { PageHeaderComponent } from '../../shell/page-header/page-header';
import { SegToggleComponent, SegToggleOption } from '../../ui/seg-toggle/seg-toggle';
import { todayIso } from '../dates';
import { formatMoney, formatSigned } from '../format';
import { ReportOverviewComponent } from './overview-tab';
import {
  PeriodUnit,
  ReportPeriod,
  change,
  compareLabels,
  elapsedDays,
  monthOf,
  n0,
  num,
  pct1,
  periodFromParams,
  periodLabel,
  periodParams,
  periodWithUnit,
  shiftPeriod,
  trendRange,
} from './report-math';
import { ReportTrendComponent } from './trend-tab';

export type ReportTab = 'overview' | 'trend' | 'rank' | 'plan';

export const REPORT_TABS: readonly [ReportTab, string][] = [
  ['overview', '總覽'],
  ['trend', '趨勢與比較'],
  ['rank', '排行'],
  ['plan', '專案與預算'],
];

export type LoadState = 'loading' | 'ready' | 'error';

interface Figure {
  label: string;
  value: string;
  foot: string;
}

const CURRENCY_NAMES: Record<string, string> = { TWD: '台幣', JPY: '日圓', USD: '美元' };

/**
 * 財務 › 報表 (`/accounting/reports`, openspec `add-accounting-reports`): the period control, the statistics rules,
 * four figures and the tabs. The period and the tab live in the URL (`?month=2026-10&tab=trend`), so a tab is a
 * link and ‹ › is history. This PR ships 總覽 and 趨勢與比較; 排行 and 專案與預算 follow.
 */
@Component({
  selector: 'app-accounting-reports',
  imports: [PageHeaderComponent, SegToggleComponent, ReportOverviewComponent, ReportTrendComponent],
  templateUrl: './reports.html',
  styleUrl: './reports.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AccountingReportsComponent {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly reports = inject(ReportsService);
  private requestId = 0;
  private trendRequestId = 0;

  private readonly params = toSignal(this.route.queryParamMap, { initialValue: this.route.snapshot.queryParamMap });
  readonly today = signal(todayIso());
  // Same dates → the same period: the loads below must not re-run on an unrelated query change (`tab`).
  readonly period = computed(() => periodFromParams(this.params(), this.today()), {
    equal: (a, b) => a.unit === b.unit && a.from === b.from && a.to === b.to,
  });
  readonly tab = computed<ReportTab>(() => {
    const tab = this.params().get('tab');
    return REPORT_TABS.some(([id]) => id === tab) ? (tab as ReportTab) : 'overview';
  });

  protected readonly tabs = REPORT_TABS;
  protected readonly units: SegToggleOption[] = [
    { value: 'month', label: '月' },
    { value: 'year', label: '年' },
    { value: 'custom', label: '自訂' },
  ];

  /** Expense by category with subcategory splits and the previous period; the same against last year; income. */
  readonly expense = signal<BreakdownResponse | null>(null);
  readonly expenseLastYear = signal<BreakdownResponse | null>(null);
  readonly income = signal<BreakdownResponse | null>(null);
  readonly state = signal<LoadState>('loading');
  readonly trend = signal<TrendResponse | null>(null);
  readonly trendState = signal<LoadState>('loading');
  /** The trend's category (chip); null = everything. */
  readonly trendSeries = signal<number | null>(null);
  /** The figures are the handoff's sample month (no report API yet). */
  readonly sample = this.reports.sample;

  readonly currency = computed(() => this.expense()?.currency ?? 'TWD');
  readonly labels = computed(() => compareLabels(this.period()));
  readonly periodText = computed(() => periodLabel(this.period()));
  protected readonly stepLabels = computed(() => {
    const unit = this.period().unit;
    return unit === 'year' ? ['上一年', '下一年'] : unit === 'month' ? ['上個月', '下個月'] : ['前一期', '後一期'];
  });
  /** The month the timeline opens on from 在記帳記錄看這 N 筆 (the timeline shows one month). */
  readonly drillMonth = computed(() => monthOf(this.period().from));

  /** The statistics rules under the title; the currencies left out follow when the API reports any. */
  readonly rules = computed(() => {
    const currency = this.currency();
    const parts = [`以${CURRENCY_NAMES[currency] ?? currency}計`, '退款抵減支出', '轉帳不計入'];
    const missing = new Set([...(this.expense()?.missing_rates ?? []), ...(this.income()?.missing_rates ?? [])]);
    if (missing.size) parts.push(`未換算：${[...missing].join('、')}`);
    return parts.join(' · ');
  });

  readonly figures = computed<Figure[]>(() => {
    const expense = this.expense();
    const income = this.income();
    const lastYear = this.expenseLastYear();
    if (!expense || !income) {
      return [
        { label: '支出', value: '—', foot: '' },
        { label: '收入', value: '—', foot: '' },
        { label: '結餘', value: '—', foot: '' },
        { label: '日均支出', value: '—', foot: '' },
      ];
    }
    const currency = expense.currency;
    const labels = this.labels();
    const spent = num(expense.totals.expense);
    const earned = num(income.totals.income);
    const previous = expense.compare ? change(spent, num(expense.compare.totals.expense)).text : '—';
    const yearAgo = lastYear?.compare ? change(spent, num(lastYear.compare.totals.expense)).text : '—';
    const topIncome = income.rows
      .filter(row => num(row.amount) > 0)
      .slice(0, 2)
      .map(row => `${row.label} ${n0(row.amount)}`);
    const elapsed = elapsedDays(this.period(), this.today());
    return [
      { label: '支出', value: formatMoney(spent, currency), foot: `${labels.previous} ${previous} · ${labels.lastYear} ${yearAgo}` },
      { label: '收入', value: formatMoney(earned, currency), foot: topIncome.length ? topIncome.join(' · ') : '本期沒有收入' },
      { label: '結餘', value: formatSigned(earned - spent, currency), foot: earned > 0 ? `儲蓄率 ${pct1(earned - spent, earned)}` : '儲蓄率 —' },
      { label: '日均支出', value: formatMoney(spent / elapsed.days, currency), foot: elapsed.running ? `已過 ${elapsed.days} 天` : `共 ${elapsed.days} 天` },
    ];
  });

  constructor() {
    effect(() => {
      const period = this.period();
      untracked(() => this.load(period));
    });
    effect(() => {
      const period = this.period();
      const series = this.trendSeries();
      untracked(() => this.loadTrend(period, series));
    });
  }

  reload(): void {
    this.load(this.period());
    this.loadTrend(this.period(), this.trendSeries());
  }

  private load(period: ReportPeriod): void {
    const id = ++this.requestId;
    this.state.set('loading');
    const range = { date_from: period.from, date_to: period.to };
    forkJoin({
      expense: this.reports.getBreakdown({ ...range, metric: 'expense', group_by: 'category', split_by: 'subcategory', compare: 'previous' }),
      lastYear: this.reports.getBreakdown({ ...range, metric: 'expense', group_by: 'category', compare: 'last_year' }),
      income: this.reports.getBreakdown({ ...range, metric: 'income', group_by: 'category', split_by: 'subcategory', compare: 'previous' }),
    }).subscribe({
      next: ({ expense, lastYear, income }) => {
        if (id !== this.requestId) return;
        this.expense.set(expense);
        this.expenseLastYear.set(lastYear);
        this.income.set(income);
        this.state.set('ready');
      },
      error: () => {
        if (id !== this.requestId) return;
        this.expense.set(null);
        this.expenseLastYear.set(null);
        this.income.set(null);
        this.state.set('error');
      },
    });
  }

  private loadTrend(period: ReportPeriod, series: number | null): void {
    const id = ++this.trendRequestId;
    this.trendState.set('loading');
    const range = trendRange(period);
    this.reports
      .getTrend({ date_from: range.from, date_to: range.to, metric: 'expense', granularity: 'month', series: series === null ? undefined : [series] })
      .subscribe({
        next: trend => {
          if (id !== this.trendRequestId) return;
          this.trend.set(trend);
          this.trendState.set('ready');
        },
        error: () => {
          if (id !== this.trendRequestId) return;
          this.trend.set(null);
          this.trendState.set('error');
        },
      });
  }

  // ---- URL state ------------------------------------------------------------------------------------------------

  setTab(tab: ReportTab): void {
    this.navigate({ tab: tab === 'overview' ? null : tab });
  }

  setUnit(unit: string): void {
    this.navigate(periodParams(periodWithUnit(this.period(), unit as PeriodUnit)));
  }

  move(delta: 1 | -1): void {
    this.navigate(periodParams(shiftPeriod(this.period(), delta)));
  }

  setCustom(from: string, to: string): void {
    if (!from || !to) return;
    this.navigate(periodParams({ unit: 'custom', from: from <= to ? from : to, to: from <= to ? to : from }));
  }

  private navigate(queryParams: Record<string, string | null>): void {
    void this.router.navigate([], { relativeTo: this.route, queryParams, queryParamsHandling: 'merge', replaceUrl: true });
  }
}
