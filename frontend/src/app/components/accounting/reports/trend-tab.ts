import { ChangeDetectionStrategy, Component, computed, inject, input, output } from '@angular/core';

import { BreakdownResponse, TREND_ALL, TrendResponse } from '../../../models/reports.model';
import { LayoutModeService } from '../../../services/layout-mode.service';
import type { LoadState } from './reports';
import {
  BarGeometry,
  Change,
  ReportPeriod,
  barGeometry,
  bucketLabel,
  change,
  compareLabels,
  compareTitle,
  n0,
  niceCeiling,
  num,
  periodNouns,
  seriesColor,
  tickLabel,
  yOf,
} from './report-math';

/** The chart's drawing (ReportsDesktop / ReportsPhone): the SVG viewBox, the plot area, the label baseline. */
interface ChartLayout {
  width: number;
  height: number;
  plot: { left: number; width: number; top: number; height: number };
  labelY: number;
  /** Phones drop the value axis: the bars and the average line carry the figures. */
  ticks: boolean;
}

const DESKTOP: ChartLayout = { width: 960, height: 260, plot: { left: 48, width: 904, top: 20, height: 200 }, labelY: 248, ticks: true };
const PHONE: ChartLayout = { width: 358, height: 200, plot: { left: 0, width: 358, top: 14, height: 166 }, labelY: 196, ticks: false };

interface Chip {
  id: number | null;
  label: string;
}

interface Bar extends BarGeometry {
  label: string;
  last: boolean;
}

interface CompareRow {
  key: string;
  label: string;
  color: string | null;
  total: boolean;
  current: string;
  previous: string;
  lastYear: string;
  monthChange: Change;
  yearChange: Change;
}

/**
 * 趨勢與比較: twelve monthly bars with a dashed average, switchable between all expense and single categories,
 * and the comparison table (this period, the previous one, the same period last year, ▲ / ▼ changes, a total row).
 */
@Component({
  selector: 'app-report-trend',
  templateUrl: './trend-tab.html',
  styleUrl: './trend-tab.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ReportTrendComponent {
  readonly trend = input<TrendResponse | null>(null);
  readonly trendState = input<LoadState>('loading');
  /** Expense by category compared with the previous period (the chips and the table). */
  readonly expense = input<BreakdownResponse | null>(null);
  /** Expense by category compared with the same period last year. */
  readonly lastYear = input<BreakdownResponse | null>(null);
  readonly period = input.required<ReportPeriod>();
  /** The selected category id; null = everything. */
  readonly series = input<number | null>(null);
  readonly seriesChange = output<number | null>();

  private readonly layoutMode = inject(LayoutModeService);
  /** The phone drawing below 760 px, the desktop one from there (the SVG scales to the card either way). */
  readonly layout = computed<ChartLayout>(() => (this.layoutMode.mode() === 'phone' ? PHONE : DESKTOP));
  protected readonly viewBox = computed(() => `0 0 ${this.layout().width} ${this.layout().height}`);
  protected readonly plotLeft = computed(() => this.layout().plot.left);
  protected readonly plotRight = computed(() => this.layout().plot.left + this.layout().plot.width);

  private readonly categoryRows = computed(() => (this.expense()?.rows ?? []).filter(row => row.key !== null && /^\d+$/.test(row.key)));

  readonly chips = computed<Chip[]>(() => [
    { id: null, label: '全部支出' },
    ...this.categoryRows()
      .filter(row => num(row.amount) > 0 || num(row.refund) > 0)
      .map(row => ({ id: Number(row.key), label: num(row.refund) > 0 ? `${row.label}（扣退款）` : row.label })),
  ]);

  readonly seriesName = computed(() => {
    const id = this.series();
    return id === null ? '全部支出' : (this.categoryRows().find(row => Number(row.key) === id)?.label ?? '類別');
  });
  readonly title = computed(() => {
    const months = this.trend()?.buckets.length ?? 12;
    return this.series() === null ? `每月支出 · 近 ${months} 個月` : `每月支出 · ${this.seriesName()} · 近 ${months} 個月`;
  });

  private readonly seriesKey = computed(() => (this.series() === null ? TREND_ALL : String(this.series())));
  private readonly values = computed(() => (this.trend()?.buckets ?? []).map(bucket => num(bucket.values[this.seriesKey()])));
  readonly average = computed(() => num(this.trend()?.average[this.seriesKey()]));
  private readonly max = computed(() => niceCeiling(Math.max(...this.values(), this.average(), 0)));

  readonly bars = computed<Bar[]>(() => {
    const buckets = this.trend()?.buckets ?? [];
    const geometry = barGeometry(this.values(), this.layout().plot, this.max());
    return geometry.map((bar, index) => ({ ...bar, label: bucketLabel(buckets[index].start), last: index === geometry.length - 1 }));
  });
  readonly grid = computed(() => {
    if (!this.layout().ticks) return [];
    return [0, 0.5, 1].map(fraction => {
      const value = this.max() * fraction;
      const y = yOf(value, this.layout().plot, this.max());
      return { y: y.toFixed(1), textY: (y + 4).toFixed(1), label: tickLabel(value) };
    });
  });
  readonly averageY = computed(() => yOf(this.average(), this.layout().plot, this.max()).toFixed(1));
  readonly averageTextY = computed(() => (yOf(this.average(), this.layout().plot, this.max()) - 6).toFixed(1));
  readonly averageLabel = computed(() => `${this.trend()?.buckets.length ?? 12} 個月平均 ${n0(this.average())}`);
  readonly chartAria = computed(() => {
    const buckets = this.trend()?.buckets ?? [];
    const values = this.values();
    return `${this.seriesName()}近 ${buckets.length} 個月：${buckets.map((bucket, index) => `${bucketLabel(bucket.start)} ${n0(values[index])}`).join('、')}`;
  });

  readonly nouns = computed(() => periodNouns(this.period()));
  /** Phones fold the table to two lines per category; the changes then carry `比 9 月` / `比去年 10 月` themselves. */
  readonly labels = computed(() => compareLabels(this.period()));
  readonly compareTitle = computed(() => `比較 · ${compareTitle(this.period())}`);

  readonly compareRows = computed<CompareRow[]>(() => {
    const expense = this.expense();
    if (!expense) return [];
    const lastYearByKey = new Map((this.lastYear()?.rows ?? []).map(row => [row.key, num(row.compare_amount)]));
    const rows = this.categoryRows()
      .map((row, index) => {
        const current = num(row.amount);
        const previous = num(row.compare_amount);
        const yearAgo = lastYearByKey.get(row.key) ?? 0;
        return {
          key: row.key!,
          label: row.label,
          color: row.color ?? seriesColor(index),
          total: false,
          current: n0(current),
          previous: n0(previous),
          lastYear: n0(yearAgo),
          monthChange: change(current, previous, true),
          yearChange: change(current, yearAgo, true),
          sort: current,
        };
      })
      .sort((a, b) => b.sort - a.sort);
    const current = num(expense.totals.expense);
    const previous = num(expense.compare?.totals.expense);
    const yearAgo = num(this.lastYear()?.compare?.totals.expense);
    return [
      ...rows,
      {
        key: '__total__',
        label: '合計',
        color: null,
        total: true,
        current: n0(current),
        previous: n0(previous),
        lastYear: n0(yearAgo),
        monthChange: change(current, previous, true),
        yearChange: change(current, yearAgo, true),
      },
    ];
  });

  pick(id: number | null): void {
    if (id !== this.series()) this.seriesChange.emit(id);
  }
}
