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
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { Subscription, catchError, combineLatest, finalize, forkJoin, interval, of, switchMap, takeUntil, takeWhile, timer } from 'rxjs';

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
import { PageHeaderComponent, PageMenuItem } from '../../shell/page-header/page-header';
import {
  MARKET_LABELS,
  PendingRow,
  allDividendPages,
  abbrInk,
  allocation,
  carryHoldings,
  holdingAbbr,
  millionsTick,
  composition,
  pendingRows,
  quoteTimeLabel,
  todayChange,
} from './dashboard-math';
import { hoverGuidePlugin } from './chart-guide.plugin';
import { Amount, displayedSign, formatMoney, formatPct } from '../../ui/format';
import { isTwdCurrency } from '../money';
import { marketLabel } from '../market';

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
  styleUrls: ['./dashboard.scss', '../kpi-cards.scss'],
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
  /** `quote_refresh.finished_at` from the latest status read. */
  private readonly quoteFinishedAt = signal<string | null>(null);
  /** Re-evaluates the quote line each minute so the 盤中 / 台股收盤 tag follows the clock (-1: interval starts at 0). */
  private readonly minuteTick = toSignal(interval(60_000), { initialValue: -1 });
  /** `行情更新於 HH:mm · …` under the page title; null hides it. */
  readonly quoteLine = computed(() => {
    this.minuteTick();
    return quoteTimeLabel(this.quoteFinishedAt(), this.clock());
  });
  private quoteStatusLoad?: Subscription;
  /** 待收股利 rows; null when the dividends or names are unavailable, which omits the block. */
  readonly pendingDividends = signal<PendingRow[] | null>(null);
  private pendingDividendsLoad?: Subscription;

  readonly rangeOptions: SegToggleOption[] = [
    { label: '1M', value: '1M' },
    { label: '3M', value: '3M' },
    { label: 'YTD', value: 'YTD' },
    { label: '1Y', value: '1Y' },
    { label: '5Y', value: '5Y' },
  ];

  readonly xirrGapTooltip = '缺少此期間的淨值或價格資料。請執行資料重算後再重新整理。';
  readonly estimateHint = '估算・非可用現金';

  chartData: any = null;
  chartOptions: any = this.buildChartOptions();
  readonly chartPlugins = [hoverGuidePlugin];

  readonly isPhone = computed(() => this.layoutMode.mode() === 'phone');
  /** Phone header ⋯ menu: the actions that sit in the header from 760 px. */
  readonly phoneMenu = computed<PageMenuItem[]>(() => [
    { label: '匯入券商', icon: 'pi-upload', routerLink: '/portfolio/import', queryParams: { type: 'broker' } },
    { label: '刷新行情', icon: 'pi-refresh', run: () => this.loadSummary(), disabled: this.loading() },
  ]);

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
      total: this.twdPlain(summary.total_assets_twd),
      todayAmount: today.amount,
      todayText: this.money(today.amount, 'TWD', 'change'),
      todaySign: displayedSign(today.amount, this.money(today.amount, 'TWD', 'change')),
      todayPercent: today.percent,
      ...parts,
      barLabel: `資產組成：股票 ${this.pct(parts.stocksPct)}，現金 ${this.pct(parts.cashPct)}`,
    };
  });

  readonly filteredHoldings = computed(() => {
    const filter = this.marketFilter();
    const rows = this.flatHoldings();
    return filter === 'ALL' ? rows : rows.filter(h => h.market === filter);
  });

  readonly allocationSlices = computed(() => allocation(this.filteredHoldings()));

  private readonly allocationTokens = computed(
    () => new Map(this.allocationSlices().filter(s => s.market).map(s => [`${s.market}:${s.symbol}`, s.token])),
  );

  readonly showMarketGroups = computed(() =>
    this.filteredHoldings().some(holding => holding.market !== 'TW'),
  );

  readonly holdingGroups = computed(() => this.groupHoldingsByMarket(this.filteredHoldings(), 'market_value', 'desc'));

  protected readonly marketLabel = marketLabel;

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
  /** Live-region text for the focused point; the chart itself is a canvas behind role="img". */
  readonly focusedPointText = signal('');

  onChartFocus(): void {
    this.showChartPoint(this.chartPoints().length - 1);
  }

  onChartKeydown(event: KeyboardEvent): void {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    event.preventDefault();
    const last = this.chartPoints().length - 1;
    // The pointer may have moved the tooltip since the last key press: continue from what is shown.
    const hovered = this.netWorthChart?.chart?.tooltip?.getActiveElements?.()[0]?.index;
    const from = hovered ?? (this.focusedPointIndex < 0 ? last : this.focusedPointIndex);
    const step = event.key === 'ArrowLeft' ? -1 : 1;
    this.showChartPoint(Math.min(last, Math.max(0, from + step)));
  }

  onChartBlur(): void {
    this.focusedPointIndex = -1;
    this.focusedPointText.set('');
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
    const point = this.chartPoints()[index];
    if (!point) return;
    const { total, holdings, cash } = this.seriesValues(point);
    this.focusedPointText.set(
      `${this.pointDate(point)} · 帳戶總值 ${this.money(total)} · 持股淨值 ${this.money(holdings)} · 現金 ${this.money(cash)}`,
    );
  }

  private pointDate(point: NetworthPoint): string {
    return point.date.slice(0, 10).replaceAll('-', '/');
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
    this.loadPendingDividends();
    this.loadNetworthHistory();

    combineLatest([this.appearance.dark$, this.appearance.gainLoss$])
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe(() => this.refreshChartTheme());

    this.portfolioService.cashLedgerChanged$
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe(() => {
        this.networthCache.clear();
        this.reloadSummary();
        this.loadPendingDividends();
        this.loadNetworthHistory();
      });
  }

  selectRange(value: string): void {
    const range = value as PortfolioRange;
    this.range.set(range);
    const cached = this.networthCache.get(range);
    if (cached) {
      this.chartPoints.set(carryHoldings(cached));
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
    sortBy?: 'unrealized_pnl' | 'market_value',
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

  holdingAbbr(holding: StockHolding): string {
    return holdingAbbr(holding.symbol, holding.name);
  }

  /** Allocation colour of the holding, or the muted token when it falls into 其他. */
  holdingToken(holding: StockHolding): string {
    return this.allocationTokens().get(`${holding.market}:${holding.symbol}`) ?? '--app-text-muted';
  }

  /** True when the holding's fill needs dark initials in light mode. */
  holdingInkDark(holding: StockHolding): boolean {
    return abbrInk(this.holdingToken(holding)) === 'dark';
  }

  /** `台股 · 4 檔 · $2,612,900` */
  groupHeader(group: { market: MarketCode; holdings: StockHolding[] }): string {
    const total = group.holdings.reduce((sum, h) => sum + (Number(h.market_value) || 0), 0);
    return `${MARKET_LABELS[group.market]} · ${group.holdings.length} 檔 · ${this.money(total)}`;
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
    return this.money(holding.market_value);
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

  /** Trend sign of the 預估含息損益 value as printed ($0 is neither up nor down). */
  protected estimatedSign(totals: { estimated_pnl_with_dividends?: Amount; currency?: string | null }): number {
    const value = totals.estimated_pnl_with_dividends;
    return displayedSign(value, this.money(value, totals.currency, 'change'));
  }

  /** Trend sign of the XIRR value as printed (a ratio, shown as a percentage). */
  protected xirrSign(xirr: number | null | undefined): number {
    return xirr == null ? 0 : displayedSign(xirr, this.pct(xirr * 100));
  }

  /** Trend sign of a holding's 預估含息損益 as printed (a body that rounds to zero is unsigned). */
  protected holdingEstimateSign(holding: StockHolding): number {
    const value = this.holdingEstimate(holding);
    return displayedSign(value, this.money(value, this.holdingCurrency(holding), 'change'));
  }

  /** Trend sign of a TW holding's 未實現價差 as printed. */
  protected holdingPriceSign(holding: StockHolding): number {
    return displayedSign(holding.unrealized_pnl, this.money(holding.unrealized_pnl, 'TWD', 'change'));
  }

  /** Market-filtered totals stay in the market's native currency and carry an ISO code: they get the lower clamp. */
  protected isForeignTotals(totals: { currency?: string | null }): boolean {
    return !isTwdCurrency(totals.currency);
  }

  money(value: Amount, currency?: string | null, sign?: 'change'): string {
    return formatMoney(value, { currency, sign });
  }

  /** Hero total and legend: grouped TWD without the currency sign (the 總資產 · TWD label names it). */
  twdPlain(value: Amount): string {
    return formatMoney(value, { symbol: false });
  }

  pct(value: Amount, sign?: 'change'): string {
    return formatPct(value, { sign });
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
        // A filter on a market the new summary no longer holds would leave the toggle without a selection.
        const filter = this.marketFilter();
        if (filter !== 'ALL' && !this.availableMarkets().includes(filter)) this.selectMarketFilter('ALL');
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
  // Only the latest read may set the line: an earlier one finishing late would show an older time.
  private loadQuoteLine(): void {
    this.quoteStatusLoad?.unsubscribe();
    this.quoteStatusLoad = this.portfolioService
      .getRecalcStatus()
      .pipe(catchError(() => of(null)), takeUntilDestroyed(this.destroyRef))
      .subscribe(status => this.quoteFinishedAt.set(status?.quote_refresh?.finished_at ?? null));
  }

  private loadPendingDividends(): void {
    this.pendingDividendsLoad?.unsubscribe();
    const dividends = allDividendPages(offset =>
      this.portfolioService.getDividends({ limit: 100, offset, sort: 'ex_dividend_date:desc' }),
    ).pipe(catchError(() => of(null)));
    const names = this.portfolioService.getSymbolNames().pipe(catchError(() => of(null)));
    this.pendingDividendsLoad = forkJoin([dividends, names])
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe(([rows, map]) =>
        this.pendingDividends.set(rows && map ? pendingRows(rows, d => map[d.symbol] ?? d.symbol) : null),
      );
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
      this.chartPoints.set(carryHoldings(cached));
      this.rebuildChart();
      this.prefetchRemainingRanges(range);
      return;
    }
    const { from, interval: historyInterval } = this.rangeQuery(range);
    this.portfolioService.getNetworthHistory(from, undefined, historyInterval).subscribe({
      next: (points) => {
        this.networthCache.set(range, points);
        if (this.range() === range) {
          this.chartPoints.set(carryHoldings(points));
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
    this.focusedPointIndex = -1;
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
              return point ? this.pointDate(point) : '';
            },
            label: (ctx: any) => `${ctx.dataset?.label ?? ''} ${this.money(ctx.parsed?.y ?? 0)}`,
            afterBody: (items: any[]) => {
              const point = this.chartPoints()[items[0]?.dataIndex];
              return point ? `現金 ${this.money(this.seriesValues(point).cash)}` : '';
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
            callback: (value: number, _index: number, ticks: { value: number }[]) => millionsTick(Number(value), ticks),
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
