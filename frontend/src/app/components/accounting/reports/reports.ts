import { ChangeDetectionStrategy, Component, computed, effect, inject, signal, untracked } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { Observable, catchError, forkJoin, of } from 'rxjs';

import { CategoryNode, Project } from '../../../models/accounting.model';
import { BreakdownResponse, BreakdownRow, BudgetInput, BudgetStatusResponse, SavedReport, TrendQuery, TrendResponse } from '../../../models/reports.model';
import { AccountingService } from '../../../services/accounting.service';
import { NgTemplateOutlet } from '@angular/common';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { ReportsService } from '../../../services/reports.service';
import { PageHeaderComponent } from '../../shell/page-header/page-header';
import { SegToggleComponent, SegToggleOption } from '../../ui/seg-toggle/seg-toggle';
import { todayIso } from '../dates';
import { formatMoney, formatSigned } from '../format';
import { writeErrorMessage } from '../http-errors';
import { ReportOverviewComponent } from './overview-tab';
import { BudgetAmount, ReportPlanComponent } from './plan-tab';
import { OTHER_KEY, RANK_DIMENSIONS, RankDimension, ReportRankComponent } from './rank-tab';
import {
  PeriodUnit,
  ReportPeriod,
  asOfText,
  compareLabels,
  elapsedDays,
  isRunning,
  monthOf,
  n0,
  num,
  pct1,
  periodFromParams,
  periodLabel,
  periodParams,
  periodWithUnit,
  runningCompareLabels,
  shiftPeriod,
  totalChange,
  trendRange,
  viewRange,
  windowEmpty,
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
  imports: [NgTemplateOutlet, RouterLink, PageHeaderComponent, SegToggleComponent, ReportOverviewComponent, ReportTrendComponent, ReportRankComponent, ReportPlanComponent],
  templateUrl: './reports.html',
  styleUrl: './reports.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AccountingReportsComponent {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly reports = inject(ReportsService);
  private readonly accounting = inject(AccountingService);
  private readonly layoutMode = inject(LayoutModeService);
  private requestId = 0;
  private trendRequestId = 0;
  private rankRequestId = 0;
  private sparkRequestId = 0;
  private planRequestId = 0;
  private referenceRequested = false;
  private projectsFailed = false;

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
  /** 排行's dimension (`?dim=`), 商家 by default. */
  readonly dimension = computed<RankDimension>(() => {
    const dim = this.params().get('dim');
    return RANK_DIMENSIONS.includes(dim as RankDimension) ? (dim as RankDimension) : 'merchant';
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
  /** While a period runs: the same two breakdowns over the whole period, no comparison (the 「另有」 line, the whole view). */
  readonly expenseWhole = signal<BreakdownResponse | null>(null);
  readonly incomeWhole = signal<BreakdownResponse | null>(null);
  /** The whole-period view (design D1): one signal for the page, reset with the period, not kept in the URL. */
  readonly wholeView = signal(false);
  readonly running = computed(() => isRunning(this.period(), this.today()));
  readonly viewRange = computed(() => viewRange(this.period(), this.today(), this.wholeView()));
  readonly viewExpense = computed(() => (this.wholeView() ? (this.expenseWhole() ?? this.expense()) : this.expense()));
  readonly viewIncome = computed(() => (this.wholeView() ? (this.incomeWhole() ?? this.income()) : this.income()));
  readonly state = signal<LoadState>('loading');
  readonly trend = signal<TrendResponse | null>(null);
  readonly trendState = signal<LoadState>('loading');
  /** The trend's category (chip); null = everything. */
  readonly trendSeries = signal<number | null>(null);
  /** 排行: the ranked rows of the dimension, the selected row and its six-month bars. Read only while the tab is open. */
  readonly rank = signal<BreakdownResponse | null>(null);
  readonly rankState = signal<LoadState>('loading');
  readonly rankSelected = signal<string | null | undefined>(undefined);
  readonly spark = signal<TrendResponse | null>(null);
  readonly sparkState = signal<LoadState>('loading');
  /** 專案與預算: the month's budget status, the projects' lifetime breakdown, and the reference lists behind both. */
  readonly budgets = signal<BudgetStatusResponse | null>(null);
  readonly budgetsState = signal<LoadState>('loading');
  readonly projectReport = signal<BreakdownResponse | null>(null);
  readonly projectReportState = signal<LoadState>('loading');
  readonly projectList = signal<Project[]>([]);
  readonly expenseRoots = signal<CategoryNode[]>([]);
  readonly planBusy = signal(false);
  readonly planError = signal<string | null>(null);
  /** Saved reports (design D4): the pinned ones follow the four tabs on desktop and line up as chips on phones. */
  readonly savedReports = signal<SavedReport[]>([]);
  readonly pinnedReports = computed(() => this.savedReports().filter(report => report.pinned));
  readonly isPhone = computed(() => this.layoutMode.mode() === 'phone');
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

  /**
   * The statistics rules under the title; the 不計入報表 projects and the currencies left out follow when there are any
   * (those of the responses on screen: the whole period's in the whole view).
   */
  readonly rules = computed(() => {
    const currency = this.currency();
    const parts = [`以${CURRENCY_NAMES[currency] ?? currency}計`, '退款抵減支出', '轉帳不計入'];
    const missing = new Set([...(this.viewExpense()?.missing_rates ?? []), ...(this.viewIncome()?.missing_rates ?? [])]);
    const excluded = this.projectList().filter(project => project.exclude_from_reports).map(project => project.name);
    if (excluded.length) parts.push(`不計入：${excluded.join('、')}`);
    if (missing.size) parts.push(`未換算：${[...missing].join('、')}`);
    return parts.join(' · ');
  });

  readonly figures = computed<Figure[]>(() => {
    const expense = this.viewExpense();
    const income = this.viewIncome();
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
    const period = this.period();
    const today = this.today();
    const whole = this.wholeView();
    const running = this.running() && !whole;
    const spent = num(expense.totals.expense);
    const earned = num(income.totals.income);
    const topIncome = income.rows
      .filter(row => num(row.amount) > 0)
      .slice(0, 2)
      .map(row => `${row.label} ${n0(row.amount)}`);
    const elapsed = whole ? elapsedDays(period, period.to) : elapsedDays(period, today);
    return [
      { label: '支出', value: formatMoney(spent, currency), foot: this.spendFoot(expense, lastYear, spent, running, whole) },
      { label: '收入', value: formatMoney(earned, currency), foot: topIncome.length ? topIncome.join(' · ') : '本期沒有收入' },
      { label: '結餘', value: formatSigned(earned - spent, currency), foot: earned > 0 ? `儲蓄率 ${pct1(earned - spent, earned)}` : '儲蓄率 —' },
      { label: '日均支出', value: formatMoney(spent / elapsed.days, currency), foot: elapsed.running ? `已過 ${elapsed.days} 天` : `共 ${elapsed.days} 天` },
    ];
  });

  /**
   * The 支出 foot: the whole view counts entries; a running period compares the to-date range; otherwise the whole period.
   * A total reads as a delta (never 新增 / 停止), or 上期無資料 / 去年無資料 when the window is empty.
   */
  private spendFoot(expense: BreakdownResponse, lastYear: BreakdownResponse | null, spent: number, running: boolean, whole: boolean): string {
    if (whole) return `${expense.rows.reduce((sum, row) => sum + row.count, 0)} 筆`;
    const labels = running ? runningCompareLabels(this.period(), this.today()) : this.labels();
    const segment = (label: string, source: BreakdownResponse | null | undefined, emptyText: '上期無資料' | '去年無資料'): string => {
      if (!source?.compare) return `${label} —`;
      const result = totalChange(spent, num(source.compare.totals.expense), windowEmpty(source), emptyText);
      return result.kind === 'none' ? result.text : `${label} ${result.text}`;
    };
    // One window (a calendar year, or windows that coincide): said once, from the last-year response when running.
    const text = labels.single
      ? segment(labels.previous, running ? lastYear : expense, running ? '去年無資料' : '上期無資料')
      : `${segment(labels.previous, expense, '上期無資料')} · ${segment(labels.lastYear, lastYear, '去年無資料')}`;
    return running ? `${text} · ${asOfText(this.today())}` : text;
  }

  constructor() {
    this.reports.listSaved().subscribe({ next: list => this.savedReports.set(list), error: () => undefined });
    this.loadProjects();
    effect(() => {
      const period = this.period();
      untracked(() => {
        this.wholeView.set(false);
        this.load(period);
      });
    });
    effect(() => {
      const period = this.period();
      const series = this.trendSeries();
      const whole = this.wholeView();
      untracked(() => this.loadTrend(period, series, whole));
    });
    effect(() => {
      if (this.tab() !== 'rank') return;
      const period = this.period();
      const dimension = this.dimension();
      const whole = this.wholeView();
      untracked(() => this.loadRank(period, dimension, whole));
    });
    effect(() => {
      const row = this.rankRow();
      const period = this.period();
      const dimension = this.dimension();
      const whole = this.wholeView();
      untracked(() => this.loadSpark(row, period, dimension, whole));
    });
    effect(() => {
      if (this.tab() !== 'plan') return;
      const period = this.period();
      untracked(() => this.loadPlan(period));
    });
  }

  /** With the page: the rules line names the 不計入報表 projects, and 專案與預算 reuses the list (re-read there after a failure). */
  private loadProjects(): void {
    this.projectsFailed = false;
    this.accounting.getProjects().subscribe({ next: projects => this.projectList.set(projects), error: () => (this.projectsFailed = true) });
  }

  private loadPlan(period: ReportPeriod): void {
    const id = ++this.planRequestId;
    this.planError.set(null);
    this.loadBudgets(period, id);
    this.projectReportState.set('loading');
    // The projects' whole life (a project budget counts it all), 不計入報表 projects included: the cards name them.
    this.reports
      .getBreakdown({ date_from: '2000-01-01', date_to: period.to, metric: 'expense', group_by: 'project', split_by: 'category', include_excluded_projects: true })
      .subscribe({
        next: report => {
          if (id !== this.planRequestId) return;
          this.projectReport.set(report);
          this.projectReportState.set('ready');
        },
        error: () => {
          if (id !== this.planRequestId) return;
          this.projectReport.set(null);
          this.projectReportState.set('error');
        },
      });
    if (this.projectsFailed) this.loadProjects();
    if (!this.referenceRequested) {
      this.referenceRequested = true;
      this.accounting.getCategories('expense').subscribe({ next: roots => this.expenseRoots.set(roots), error: () => (this.referenceRequested = false) });
    }
  }

  private loadBudgets(period: ReportPeriod, id = ++this.planRequestId): void {
    this.budgetsState.set('loading');
    this.reports.getBudgetStatus(monthOf(period.from)).subscribe({
      next: status => {
        if (id !== this.planRequestId) return;
        this.budgets.set(status);
        this.budgetsState.set('ready');
      },
      error: () => {
        if (id !== this.planRequestId) return;
        this.budgets.set(null);
        this.budgetsState.set('error');
      },
    });
  }

  /** A budget write, then the month's status again; a failure keeps what is on screen and says why. */
  private writeBudgets(request: Observable<unknown>): void {
    this.planBusy.set(true);
    this.planError.set(null);
    request.subscribe({
      next: () => {
        this.planBusy.set(false);
        this.loadBudgets(this.period());
      },
      error: error => {
        this.planBusy.set(false);
        this.planError.set(writeErrorMessage(error));
      },
    });
  }

  saveBudgetAmounts(amounts: BudgetAmount[]): void {
    if (!amounts.length) return;
    const month = monthOf(this.period().from);
    this.writeBudgets(forkJoin(amounts.map(({ id, amount }) => this.reports.updateBudget(id, { amount, effective_from: month }))));
  }

  addBudget(input: BudgetInput): void {
    this.writeBudgets(this.reports.createBudget(input));
  }

  removeBudget(id: number): void {
    this.writeBudgets(this.reports.deleteBudget(id));
  }

  /** The selected ranked row, when it has its own six-month bars (not （無…）, not the fold). */
  private readonly rankRow = computed<BreakdownRow | null>(() => {
    const key = this.rankSelected();
    if (key === undefined || key === null || key === OTHER_KEY) return null;
    return this.rank()?.rows.find(row => row.key === key) ?? null;
  });

  reload(): void {
    this.load(this.period());
    this.loadTrend(this.period(), this.trendSeries(), this.wholeView());
    if (this.tab() === 'rank') this.loadRank(this.period(), this.dimension(), this.wholeView());
    if (this.tab() === 'plan') this.loadPlan(this.period());
  }

  private loadRank(period: ReportPeriod, dimension: RankDimension, whole: boolean): void {
    const id = ++this.rankRequestId;
    this.rankState.set('loading');
    this.rankSelected.set(undefined);
    const view = viewRange(period, this.today(), whole);
    this.reports.getBreakdown({ date_from: view.from, date_to: view.to, metric: 'expense', group_by: dimension }).subscribe({
      next: rank => {
        if (id !== this.rankRequestId) return;
        this.rank.set(rank);
        this.rankState.set('ready');
        // The largest row opens in the panel, as the canvas shows it.
        this.rankSelected.set(rank.rows[0]?.key);
      },
      error: () => {
        if (id !== this.rankRequestId) return;
        this.rank.set(null);
        this.rankState.set('error');
      },
    });
  }

  /** Six monthly buckets of the selected row: the trend filtered to that merchant / tag / counterparty / account. */
  private loadSpark(row: BreakdownRow | null, period: ReportPeriod, dimension: RankDimension, whole: boolean): void {
    const id = ++this.sparkRequestId;
    this.spark.set(null);
    if (!row || row.key === null) return;
    this.sparkState.set('loading');
    const range = trendRange({ ...period, to: viewRange(period, this.today(), whole).to }, 6);
    const filter: Partial<TrendQuery> =
      dimension === 'merchant'
        ? { merchant: row.label }
        : dimension === 'tag'
          ? { tag: [row.label] }
          : dimension === 'counterparty'
            ? { counterparty_id: Number(row.key) }
            : { account_id: [Number(row.key)] };
    this.reports.getTrend({ date_from: range.from, date_to: range.to, metric: 'expense', granularity: 'month', ...filter }).subscribe({
      next: spark => {
        if (id !== this.sparkRequestId) return;
        this.spark.set(spark);
        this.sparkState.set('ready');
      },
      error: () => {
        if (id !== this.sparkRequestId) return;
        this.sparkState.set('error');
      },
    });
  }

  private load(period: ReportPeriod): void {
    const id = ++this.requestId;
    this.state.set('loading');
    const running = isRunning(period, this.today());
    // While running, the header's three reads cover the to-date range; two whole-period reads (no `compare`) follow.
    const range = { date_from: period.from, date_to: running ? this.today() : period.to };
    const whole = { date_from: period.from, date_to: period.to, group_by: 'category', split_by: 'subcategory' } as const;
    // A failed whole read only drops the 「另有」 line: the to-date figures still show.
    const wholeRead = (metric: 'expense' | 'income') =>
      running ? this.reports.getBreakdown({ ...whole, metric }).pipe(catchError(() => of(null))) : of(null);
    forkJoin({
      expense: this.reports.getBreakdown({ ...range, metric: 'expense', group_by: 'category', split_by: 'subcategory', compare: 'previous' }),
      lastYear: this.reports.getBreakdown({ ...range, metric: 'expense', group_by: 'category', compare: 'last_year' }),
      income: this.reports.getBreakdown({ ...range, metric: 'income', group_by: 'category', split_by: 'subcategory', compare: 'previous' }),
      expenseWhole: wholeRead('expense'),
      incomeWhole: wholeRead('income'),
    }).subscribe({
      next: ({ expense, lastYear, income, expenseWhole, incomeWhole }) => {
        if (id !== this.requestId) return;
        this.expense.set(expense);
        this.expenseLastYear.set(lastYear);
        this.income.set(income);
        this.expenseWhole.set(expenseWhole);
        this.incomeWhole.set(incomeWhole);
        this.state.set('ready');
      },
      error: () => {
        if (id !== this.requestId) return;
        this.expense.set(null);
        this.expenseLastYear.set(null);
        this.income.set(null);
        this.expenseWhole.set(null);
        this.incomeWhole.set(null);
        this.state.set('error');
      },
    });
  }

  private loadTrend(period: ReportPeriod, series: number | null, whole: boolean): void {
    const id = ++this.trendRequestId;
    this.trendState.set('loading');
    // Twelve months ending at the view's end: today while the period runs, its last day in the whole view.
    const view = viewRange(period, this.today(), whole);
    const range = trendRange({ ...period, to: view.to });
    if (isRunning(period, this.today())) range.to = view.to;
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

  /** 「另有…未計入」 / 「改回截至」: the page switches between the to-date and the whole-period view. */
  toggleWhole(): void {
    this.wholeView.update(whole => !whole);
  }

  setTab(tab: ReportTab): void {
    this.navigate({ tab: tab === 'overview' ? null : tab });
  }

  setDimension(dimension: RankDimension): void {
    this.navigate({ dim: dimension === 'merchant' ? null : dimension });
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
