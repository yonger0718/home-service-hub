import {
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  Component,
  DestroyRef,
  InjectionToken,
  OnInit,
  ViewChild,
  computed,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { catchError, combineLatest, finalize, interval, of, switchMap, takeUntil, takeWhile, timer } from 'rxjs';

import { ChartModule, UIChart } from 'primeng/chart';
import { SkeletonModule } from 'primeng/skeleton';
import { TooltipModule } from 'primeng/tooltip';
import { MessageService } from 'primeng/api';

import { PortfolioService } from '../../../services/portfolio.service';
import { AppearanceService } from '../../../services/appearance.service';
import { LayoutModeService } from '../../../services/layout-mode.service';
import {
  ExDividendRecord,
  MarketCode,
  NetworthPoint,
  PortfolioSummary,
  StockHolding,
  holdingKey,
} from '../../../models/portfolio.model';
import { NativeAmountPipe } from '../../../pipes/native-amount.pipe';
import { BtnComponent } from '../../ui/btn/btn';
import { SegToggleComponent, SegToggleOption } from '../../ui/seg-toggle/seg-toggle';
import { BentoComponent } from '../../ui/bento/bento';
import { PctBadgeComponent } from '../../ui/pct-badge/pct-badge';
import { PageHeaderComponent } from '../../shell/page-header/page-header';
import { MARKET_LABELS, composition, quoteTimeLabel, todayChange } from './dashboard-math';
import { hoverGuidePlugin } from './chart-guide.plugin';

/** Current time for the quote line's 盤中 / 台股收盤 tag; overridable in tests. */
export const DASHBOARD_CLOCK = new InjectionToken<() => Date>('DASHBOARD_CLOCK', {
  providedIn: 'root',
  factory: () => () => new Date(),
});

type PortfolioRange = '1M' | '3M' | 'YTD' | '1Y' | '5Y';

/** 帳戶總值 / 持股淨值 at one chart point; cash is the gap between the two lines. */
interface ChartSeriesValues {
  total: number;
  holdings: number;
  cash: number;
}

@Component({
  selector: 'app-portfolio-dashboard',
  imports: [
    PageHeaderComponent,
    CommonModule,
    RouterLink,
    ChartModule,
    SkeletonModule,
    TooltipModule,
    BtnComponent,
    SegToggleComponent,
    BentoComponent,
    PctBadgeComponent,
    NativeAmountPipe,
  ],
  templateUrl: './dashboard.html',
  styleUrl: './dashboard.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class PortfolioDashboardComponent implements OnInit {
  private readonly portfolioService = inject(PortfolioService);
  private readonly appearance = inject(AppearanceService);
  private readonly messageService = inject(MessageService, { optional: true });
  private readonly destroyRef = inject(DestroyRef);
  private readonly cdr = inject(ChangeDetectorRef);
  private readonly clock = inject(DASHBOARD_CLOCK);
  private readonly layoutMode = inject(LayoutModeService);

  @ViewChild('netWorthChart') netWorthChart?: UIChart;

  protected readonly Number = Number;
  readonly summary = signal<PortfolioSummary | null>(null);
  readonly upcomingExDividends = signal<ExDividendRecord[]>([]);
  readonly loading = signal(false);
  readonly range = signal<PortfolioRange>('1Y');
  readonly chartPoints = signal<NetworthPoint[]>([]);
  readonly expandedHoldingKey = signal<string | null>(null);
  readonly marketFilter = signal<'ALL' | MarketCode>('ALL');
  /** `行情更新於 HH:mm · …` under the page title; null hides it. */
  readonly quoteLine = signal<string | null>(null);

  readonly rangeOptions: SegToggleOption[] = [
    { label: '1M', value: '1M' },
    { label: '3M', value: '3M' },
    { label: 'YTD', value: 'YTD' },
    { label: '1Y', value: '1Y' },
    { label: '5Y', value: '5Y' },
  ];

  readonly xirrGapTooltip = '缺少此期間的淨值或價格資料。請執行資料重算後再重新整理。';

  chartData: any = null;
  chartOptions: any = this.buildChartOptions();
  readonly chartPlugins = [hoverGuidePlugin];

  readonly isPhone = computed(() => this.layoutMode.mode() === 'phone');

  /** Latest values for the chart legend; null hides it. */
  readonly chartLegend = computed<ChartSeriesValues | null>(() => {
    const points = this.chartPoints();
    return points.length ? this.seriesValues(points[points.length - 1]) : null;
  });

  readonly periodReturn = computed(() => {
    const values = this.chartValues();
    if (values.length < 2 || values[0] === 0) return 0;
    return (values[values.length - 1] / values[0] - 1) * 100;
  });

  readonly selectedPortfolioXirr = computed(() => {
    const summary = this.summary();
    if (!summary) return null;
    return this.selectedXirr(summary);
  });

  readonly flatHoldings = computed(() =>
    (this.summary()?.holdings ?? []).map(holding => ({
      ...holding,
      market: (holding.market ?? 'TW') as MarketCode,
    })),
  );

  readonly availableMarkets = computed<MarketCode[]>(() => {
    const order: MarketCode[] = ['TW', 'US', 'LSE'];
    const present = new Set([...this.flatHoldings().map(h => h.market), ...Object.keys(this.summary()?.market_totals ?? {})]);
    return order.filter(m => present.has(m));
  });

  readonly marketFilterOptions = computed<SegToggleOption[]>(() => [
    { label: '全部', value: 'ALL' },
    ...this.availableMarkets().map(m => ({ label: MARKET_LABELS[m], value: m })),
  ]);

  readonly hero = computed(() => {
    const summary = this.summary();
    if (!summary) return null;
    const today = todayChange(summary);
    const parts = composition(summary);
    return {
      total: this.formatCurrency(summary.total_assets_twd),
      todayAmount: today.amount,
      todayText: `${today.amount > 0 ? '+' : today.amount < 0 ? '-' : ''}${this.formatCurrency(Math.abs(today.amount))}`,
      todayPercent: today.percent,
      ...parts,
      barLabel: `資產組成：股票 ${parts.stocksPct.toFixed(1)}%，現金 ${parts.cashPct.toFixed(1)}%`,
    };
  });

  readonly filteredHoldings = computed(() => {
    const filter = this.marketFilter();
    const rows = this.flatHoldings();
    return filter === 'ALL' ? rows : rows.filter(h => h.market === filter);
  });

  readonly showMarketGroups = computed(() =>
    this.filteredHoldings().some(holding => holding.market !== 'TW'),
  );

  readonly holdingGroups = computed(() => this.groupHoldingsByMarket(this.filteredHoldings()));

  readonly isMarketFiltered = computed(() => this.marketFilter() !== 'ALL');

  readonly displayCurrency = computed<string>(() => {
    const filter = this.marketFilter();
    if (filter === 'ALL' || filter === 'TW') return 'TWD';
    const rows = this.filteredHoldings();
    return rows[0]?.native_currency ?? (filter === 'US' ? 'USD' : 'GBP');
  });

  readonly filteredTotals = computed(() => {
    const summary = this.summary();
    if (!summary) return null;
    const filter = this.marketFilter();
    const cur = this.displayCurrency();

    if (filter === 'ALL') {
      return {
        currency: 'TWD',
        total_market_value: summary.total_market_value,
        total_cost: summary.total_cost,
        total_unrealized_pnl: summary.total_unrealized_pnl,
        total_dividends: summary.total_dividends,
        pending_dividends_net: summary.total_pending_dividends_net,
        estimated_pnl_with_dividends: this.optionalNumber(summary.estimated_pnl_with_dividends),
        estimated_pnl_percent: this.optionalNumber(summary.estimated_pnl_percent),
      };
    }
    // Server aggregates exact costs and closed TW receivables; summing displayed
    // holdings would lose that scope and use rounded average-cost denominators.
    const totals = summary.market_totals?.[filter];
    return {
      currency: totals?.currency ?? cur,
      total_market_value: totals?.market_value,
      total_cost: totals?.cost,
      total_unrealized_pnl: totals?.unrealized_pnl,
      total_dividends: totals?.recorded_dividends,
      pending_dividends_net: totals?.pending_dividends_net,
      estimated_pnl_with_dividends: this.optionalNumber(totals?.estimated_pnl_with_dividends),
      estimated_pnl_percent: this.optionalNumber(totals?.estimated_pnl_percent),
    };
  });

  private optionalNumber(value: number | string | null | undefined): number | null {
    if (value == null || value === '') return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  holdingEstimate(holding: StockHolding): number | null {
    return this.optionalNumber(holding.market === 'TW'
      ? holding.estimated_pnl_with_dividends : holding.estimated_pnl_with_dividends_native);
  }

  holdingEstimatePercent(holding: StockHolding): number | null {
    return this.optionalNumber(holding.market === 'TW'
      ? holding.estimated_pnl_percent : holding.estimated_pnl_percent_native);
  }

  holdingCurrency(holding: StockHolding): string | null {
    return holding.market === 'TW' ? 'TWD' : holding.native_currency;
  }

  /** Point shown while the chart has keyboard focus; -1 when none. */
  private focusedPointIndex = -1;

  onChartFocus(): void {
    this.showChartPoint(this.chartPoints().length - 1);
  }

  onChartKeydown(event: KeyboardEvent): void {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    event.preventDefault();
    const last = this.chartPoints().length - 1;
    const from = this.focusedPointIndex < 0 ? last : this.focusedPointIndex;
    const step = event.key === 'ArrowLeft' ? -1 : 1;
    this.showChartPoint(Math.min(last, Math.max(0, from + step)));
  }

  onChartBlur(): void {
    this.focusedPointIndex = -1;
    const chart = this.netWorthChart?.chart;
    if (!chart) return;
    chart.setActiveElements([]);
    chart.tooltip?.setActiveElements([], { x: 0, y: 0 });
    chart.update('none');
  }

  private showChartPoint(index: number): void {
    const chart = this.netWorthChart?.chart;
    if (!chart || index < 0) return;
    this.focusedPointIndex = index;
    const active = [{ datasetIndex: 0, index }, { datasetIndex: 1, index }];
    const element = chart.getDatasetMeta?.(0)?.data?.[index];
    chart.setActiveElements(active);
    chart.tooltip?.setActiveElements(active, { x: element?.x ?? 0, y: element?.y ?? 0 });
    chart.update('none');
  }

  private readonly networthCache = new Map<PortfolioRange, NetworthPoint[]>();
  private readonly prefetchOrder: PortfolioRange[] = ['1M', '3M', 'YTD', '5Y'];

  ngOnInit(): void {
    this.loading.set(true);
    this.reloadSummary();
    this.portfolioService.refreshQuotes().subscribe({
      next: (response) => {
        if (response?.refresh_scheduled) {
          this.pollRecalcStatus();
          return;
        }
        this.reloadSummary();
      },
      error: (err) => {
        if (err.status === 409) {
          this.notify('另一筆重算進行中, 稍候再試');
        } else if (err.status !== 204) {
          console.error('refreshQuotes failed', err);
        }
      },
    });
    this.loadExDividends();
    this.loadNetworthHistory();

    combineLatest([this.appearance.dark$, this.appearance.gainLoss$])
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe(() => this.refreshChartTheme());

    this.portfolioService.cashLedgerChanged$
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe(() => {
        this.networthCache.clear();
        this.reloadSummary();
        this.loadNetworthHistory();
      });
  }

  selectRange(value: string): void {
    const range = value as PortfolioRange;
    this.range.set(range);
    const cached = this.networthCache.get(range);
    if (cached) {
      this.chartPoints.set(cached);
      this.rebuildChart();
      return;
    }
    this.loadNetworthHistory();
  }

  selectMarketFilter(value: string): void {
    this.marketFilter.set(value as 'ALL' | MarketCode);
    this.expandedHoldingKey.set(null);
  }

  toggleHoldingExpand(holding: StockHolding): void {
    const key = this.holdingTrackKey(holding);
    this.expandedHoldingKey.set(this.expandedHoldingKey() === key ? null : key);
  }

  isHoldingExpanded(holding: StockHolding): boolean {
    return this.expandedHoldingKey() === this.holdingTrackKey(holding);
  }

  holdingTrackKey(holding: StockHolding): string {
    return holdingKey(holding);
  }

  groupHoldingsByMarket(
    holdings: StockHolding[],
    sortBy?: keyof Pick<StockHolding, 'unrealized_pnl'>,
    direction: 'asc' | 'desc' = 'asc',
  ): { market: MarketCode; holdings: StockHolding[] }[] {
    const order: MarketCode[] = ['TW', 'US', 'LSE'];

    return order
      .map(market => {
        const rows = holdings.filter(holding => holding.market === market);
        if (sortBy) {
          rows.sort((a, b) => {
            const left = Number(a[sortBy] ?? 0);
            const right = Number(b[sortBy] ?? 0);
            return direction === 'asc' ? left - right : right - left;
          });
        }
        return { market, holdings: rows };
      })
      .filter(group => group.holdings.length > 0);
  }

  fxTooltip(holding: StockHolding): string | null {
    if (
      holding.market === 'TW'
      || holding.live_fx_rate_to_twd == null
      || holding.native_currency == null
    ) return null;
    const label = holding.native_currency === 'GBp' ? 'GBP' : holding.native_currency;
    return `Revalued at 1 ${label} = ${holding.live_fx_rate_to_twd} TWD`;
  }

  marketValueDisplay(holding: StockHolding): string {
    if (holding.market !== 'TW' && holding.live_fx_rate_to_twd == null) return '—';
    return this.formatCurrency(holding.market_value);
  }

  loadSummary(): void {
    this.loading.set(true);
    this.portfolioService.refreshQuotes().subscribe({
      next: (response) => {
        if (response?.refresh_scheduled) {
          this.pollRecalcStatus();
          return;
        }
        this.reloadSummary();
      },
      error: (err) => {
        if (err.status === 409) {
          this.notify('另一筆重算進行中, 稍候再試');
        } else if (err.status !== 204) {
          console.error('refreshQuotes failed', err);
        }
        this.reloadSummary();
      },
    });
  }

  /** Hero legend amounts: grouped TWD without the currency sign. */
  formatAmount(value: number): string {
    return new Intl.NumberFormat('zh-TW', { maximumFractionDigits: 0 }).format(value);
  }

  formatCurrency(value: number | string | null | undefined): string {
    return new Intl.NumberFormat('zh-TW', {
      style: 'currency',
      currency: 'TWD',
      minimumFractionDigits: 0,
    }).format(Number(value ?? 0));
  }

  nativeMarketValue(holding: StockHolding): number | null {
    const qty = Number(holding.total_quantity ?? 0);
    const close = Number(holding.native_close ?? 0);
    if (!qty || !close) return null;
    return qty * close;
  }

  nativeFromTwd(twdValue: number | string | null | undefined, holding: StockHolding): number | null {
    if (twdValue == null || twdValue === '') return null;
    const v = Number(twdValue);
    if (!Number.isFinite(v)) return null;
    if (holding.market === 'TW') return v;
    const fx = Number(holding.live_fx_rate_to_twd ?? 0);
    if (fx <= 0) return null;
    const base = v / fx;
    return holding.native_currency === 'GBp' ? base * 100 : base;
  }

  formatNative(value: number | string | null | undefined, currency: string | null | undefined): string {
    if (value == null || value === '') return '—';
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return '—';
    if (!currency || currency === 'TWD') return this.formatCurrency(numeric);
    const decimals = currency === 'GBp' ? 4 : 2;
    return `${numeric.toFixed(decimals)} ${currency}`;
  }

  formatXirr(value: number | null | undefined): string {
    if (value == null) return 'N/A';
    return `${(value * 100).toFixed(2)}%`;
  }

  selectedXirr(source: PortfolioSummary | StockHolding): number | null | undefined {
    const isSummary = 'holdings' in source;

    switch (this.range()) {
      case '1M':
        return isSummary ? source.portfolio_xirr_1m : source.xirr_1m;
      case '3M':
        return isSummary ? source.portfolio_xirr_3m : source.xirr_3m;
      case 'YTD':
        return isSummary ? source.portfolio_xirr_ytd : source.xirr_ytd;
      case '1Y':
        return isSummary ? source.portfolio_xirr_1y : source.xirr_1y;
      case '5Y':
        return isSummary ? source.portfolio_xirr : source.xirr;
    }
  }

  private pollRecalcStatus(): void {
    interval(1000)
      .pipe(
        switchMap(() => this.portfolioService.getRecalcStatus()),
        takeWhile(status => !['completed', 'partial', 'failed'].includes(status.quote_refresh?.state ?? status.state), true),
        takeUntil(timer(30000)),
        takeUntilDestroyed(this.destroyRef),
        finalize(() => this.reloadSummary()),
      )
      .subscribe({
        error: (err) => console.error('getRecalcStatus failed', err),
      });
  }

  private reloadSummary(): void {
    this.portfolioService.getSummary().subscribe({
      next: (data) => {
        this.summary.set(data);
        this.loading.set(false);
        this.loadQuoteLine();
      },
      error: (err) => {
        console.error('Failed to load portfolio summary', err);
        this.loading.set(false);
      },
    });
  }

  // pollRecalcStatus() keeps no status body, so the quote time gets its own read after each summary.
  private loadQuoteLine(): void {
    this.portfolioService
      .getRecalcStatus()
      .pipe(catchError(() => of(null)), takeUntilDestroyed(this.destroyRef))
      .subscribe(status => this.quoteLine.set(quoteTimeLabel(status?.quote_refresh?.finished_at, this.clock())));
  }

  private loadExDividends(): void {
    this.portfolioService.getUpcomingExDividends().subscribe({
      next: (data) => this.upcomingExDividends.set(data),
      error: () => this.upcomingExDividends.set([]),
    });
  }

  private loadNetworthHistory(): void {
    const range = this.range();
    const cached = this.networthCache.get(range);
    if (cached) {
      this.chartPoints.set(cached);
      this.rebuildChart();
      this.prefetchRemainingRanges(range);
      return;
    }
    const { from, interval: historyInterval } = this.rangeQuery(range);
    this.portfolioService.getNetworthHistory(from, undefined, historyInterval).subscribe({
      next: (points) => {
        this.networthCache.set(range, points);
        if (this.range() === range) {
          this.chartPoints.set(points);
          this.rebuildChart();
        }
        this.prefetchRemainingRanges(range);
      },
      error: (err) => {
        console.error('Failed to load net-worth history', err);
        if (this.range() === range) {
          this.chartPoints.set([]);
          this.rebuildChart();
        }
      },
    });
  }

  private prefetchRemainingRanges(active: PortfolioRange): void {
    const targets = this.prefetchOrder.filter(r => r !== active && !this.networthCache.has(r));
    if (targets.length === 0) return;
    setTimeout(() => {
      for (const range of targets) {
        if (this.networthCache.has(range)) continue;
        const { from, interval: historyInterval } = this.rangeQuery(range);
        this.portfolioService.getNetworthHistory(from, undefined, historyInterval).subscribe({
          next: (points) => this.networthCache.set(range, points),
          error: () => {},
        });
      }
    }, 1500);
  }

  private rangeQuery(range: PortfolioRange): { from: string; interval: 'day' | 'week' | 'month' } {
    const today = new Date();
    const from = new Date(today);
    let historyInterval: 'day' | 'week' | 'month' = 'day';

    if (range === '1M') {
      from.setMonth(today.getMonth() - 1);
    } else if (range === '3M') {
      from.setMonth(today.getMonth() - 3);
      historyInterval = 'week';
    } else if (range === 'YTD') {
      from.setMonth(0, 1);
      historyInterval = 'week';
    } else if (range === '1Y') {
      from.setFullYear(today.getFullYear() - 1);
      historyInterval = 'week';
    } else {
      from.setFullYear(today.getFullYear() - 5);
      historyInterval = 'month';
    }

    return { from: this.formatDateOnly(from), interval: historyInterval };
  }

  private formatDateOnly(date: Date): string {
    const yyyy = date.getFullYear();
    const mm = `${date.getMonth() + 1}`.padStart(2, '0');
    const dd = `${date.getDate()}`.padStart(2, '0');
    return `${yyyy}-${mm}-${dd}`;
  }

  private refreshChartTheme(): void {
    this.rebuildChart();
    // OnPush: subscription callback runs outside Angular's input-binding cycle, so
    // markForCheck() ensures the new chartData/chartOptions reach <p-chart>'s setter
    // (which reinits Chart.js with new colours) instead of only redrawing old config.
    this.cdr.markForCheck();
    this.netWorthChart?.chart?.update?.('none');
  }

  private rebuildChart(): void {
    const points = this.chartPoints();
    const up = this.periodReturn() >= 0;
    const stockLine = this.cssVar(up ? '--app-trend-positive' : '--app-trend-negative');
    const stockFill = this.cssVar(up ? '--app-trend-positive-soft' : '--app-trend-negative-soft');
    const cashLine = this.cssVar('--app-accent');
    const cashFill = this.cssVar('--app-accent-soft');

    this.chartData = {
      labels: points.map(point => this.formatChartLabel(point.date)),
      datasets: [
        {
          label: '帳戶總值',
          data: points.map(point => this.totalAssetsValue(point)),
          borderColor: cashLine,
          backgroundColor: cashFill,
          borderWidth: 2.5,
          tension: 0.35,
          fill: '+1',
          pointRadius: 0,
          pointHoverRadius: 4,
        },
        {
          label: '持股淨值',
          data: points.map(point => this.numberFrom(point.total_market_value)),
          borderColor: stockLine,
          backgroundColor: stockFill,
          borderWidth: 2.5,
          tension: 0.35,
          fill: true,
          pointRadius: 0,
          pointHoverRadius: 4,
        },
      ],
    };
    this.chartOptions = this.buildChartOptions();
  }

  private seriesValues(point: NetworthPoint): ChartSeriesValues {
    const total = this.totalAssetsValue(point);
    const holdings = this.numberFrom(point.total_market_value);
    return { total, holdings, cash: Math.max(0, total - holdings) };
  }

  private chartValues(): number[] {
    return this.chartPoints().map(point => this.totalAssetsValue(point));
  }

  private totalAssetsValue(point: NetworthPoint): number {
    const explicitTotal = this.numberFrom(point.total_assets_twd);
    if (explicitTotal !== 0) return explicitTotal;
    return this.numberFrom(point.total_market_value) + this.numberFrom(point.total_cash_twd);
  }

  private numberFrom(value: number | string | null | undefined): number {
    return Number(value || 0);
  }

  private buildChartOptions(): any {
    const muted = this.cssVar('--app-text-muted');
    const grid = document.documentElement.classList.contains('app-dark-mode')
      ? 'rgba(255,255,255,0.08)'
      : '#e6eaf0';

    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: 'index', intersect: false, axis: 'x' },
      hover: { mode: 'index', intersect: false, axis: 'x' },
      plugins: {
        legend: { display: false },
        tooltip: {
          mode: 'index',
          intersect: false,
          axis: 'x',
          callbacks: {
            title: (items: any[]) => {
              const point = this.chartPoints()[items[0]?.dataIndex];
              return point ? point.date.slice(0, 10).replaceAll('-', '/') : '';
            },
            label: (ctx: any) => `${ctx.dataset?.label ?? ''} ${this.formatCurrency(ctx.parsed?.y ?? 0)}`,
            afterBody: (items: any[]) => {
              const point = this.chartPoints()[items[0]?.dataIndex];
              return point ? `現金 ${this.formatCurrency(this.seriesValues(point).cash)}` : '';
            },
          },
        },
      },
      scales: {
        x: {
          grid: { display: false },
          ticks: { color: muted, font: { size: 11 } },
        },
        y: {
          stacked: false,
          grid: { color: grid },
          ticks: {
            color: muted,
            font: { size: 11 },
            callback: (value: number) => `${(Number(value) / 1_000_000).toFixed(1)}M`,
          },
        },
      },
    };
  }

  private cssVar(name: string): string {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  private formatChartLabel(date: string): string {
    const parsed = new Date(date);
    if (Number.isNaN(parsed.getTime())) return date;
    const mm = (parsed.getMonth() + 1).toString().padStart(2, '0');
    if (this.range() === '5Y') return `${parsed.getFullYear()}/${mm}`;
    return `${parsed.getMonth() + 1}/${parsed.getDate().toString().padStart(2, '0')}`;
  }

  private notify(msg: string): void {
    if (this.messageService) {
      this.messageService.add({ severity: 'warn', summary: '提醒', detail: msg });
      return;
    }

    console.warn(msg);
  }
}
