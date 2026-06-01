import {
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  Component,
  DestroyRef,
  OnInit,
  ViewChild,
  computed,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { combineLatest, finalize, interval, switchMap, takeUntil, takeWhile, timer } from 'rxjs';

import { ChartModule, UIChart } from 'primeng/chart';
import { SkeletonModule } from 'primeng/skeleton';
import { TooltipModule } from 'primeng/tooltip';
import { MessageService } from 'primeng/api';

import { PortfolioService } from '../../../services/portfolio.service';
import { AppearanceService } from '../../../services/appearance.service';
import { PortfolioSummary, ExDividendRecord, NetworthPoint, StockHolding } from '../../../models/portfolio.model';
import { BtnComponent } from '../../ui/btn/btn';
import { SegToggleComponent, SegToggleOption } from '../../ui/seg-toggle/seg-toggle';
import { BentoComponent } from '../../ui/bento/bento';
import { PctBadgeComponent } from '../../ui/pct-badge/pct-badge';

type PortfolioRange = '1M' | '3M' | 'YTD' | '1Y' | '5Y';

@Component({
  selector: 'app-portfolio-dashboard',
  imports: [
    CommonModule,
    ChartModule,
    SkeletonModule,
    TooltipModule,
    BtnComponent,
    SegToggleComponent,
    BentoComponent,
    PctBadgeComponent,
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

  @ViewChild('netWorthChart') netWorthChart?: UIChart;

  protected readonly Number = Number;
  readonly summary = signal<PortfolioSummary | null>(null);
  readonly upcomingExDividends = signal<ExDividendRecord[]>([]);
  readonly loading = signal(false);
  readonly range = signal<PortfolioRange>('1Y');
  readonly chartPoints = signal<NetworthPoint[]>([]);
  readonly expandedSymbol = signal<string | null>(null);

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

  toggleHoldingExpand(symbol: string): void {
    this.expandedSymbol.set(this.expandedSymbol() === symbol ? null : symbol);
  }

  isHoldingExpanded(symbol: string): boolean {
    return this.expandedSymbol() === symbol;
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

  formatCurrency(value: number | string | null | undefined): string {
    return new Intl.NumberFormat('zh-TW', {
      style: 'currency',
      currency: 'TWD',
      minimumFractionDigits: 0,
    }).format(Number(value ?? 0));
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
        takeWhile(status => !['completed', 'partial', 'failed'].includes(status.state), true),
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
      },
      error: (err) => {
        console.error('Failed to load portfolio summary', err);
        this.loading.set(false);
      },
    });
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
    const values = this.chartValues();
    const up = this.periodReturn() >= 0;
    const line = this.cssVar(up ? '--app-trend-positive' : '--app-trend-negative');
    const fill = this.cssVar(up ? '--app-trend-positive-soft' : '--app-trend-negative-soft');

    this.chartData = {
      labels: points.map(point => this.formatChartLabel(point.date)),
      datasets: [
        {
          data: values,
          borderColor: line,
          backgroundColor: fill,
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

  private chartValues(): number[] {
    return this.chartPoints().map(point => Number(point.total_market_value || 0));
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
            label: (ctx: any) => {
              const v = Number(ctx.parsed?.y ?? 0);
              return new Intl.NumberFormat('zh-TW', {
                style: 'currency',
                currency: 'TWD',
                minimumFractionDigits: 0,
              }).format(v);
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
