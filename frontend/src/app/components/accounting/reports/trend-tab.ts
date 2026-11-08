import { ChangeDetectionStrategy, Component, computed, effect, inject, input, output, signal } from '@angular/core';

import { BreakdownResponse, TREND_ALL, TrendResponse } from '../../../models/reports.model';
import { LayoutModeService } from '../../../services/layout-mode.service';
import type { LoadState } from './reports';
import {
  BarGeometry,
  BarTip,
  Change,
  ReportPeriod,
  RunningForecast,
  barGeometry,
  afterTodayLine,
  axisParts,
  hatchLines,
  baseChange,
  bucketText,
  compareLabels,
  compareTitle,
  daysInMonth,
  dollars,
  isRunning,
  md,
  metricTotal,
  monthAverage,
  n0,
  niceCeiling,
  num,
  periodLabel,
  periodNouns,
  runningBucket,
  runningCompareLabels,
  runningForecast,
  runningCompareTitle,
  runningSuffix,
  seriesColor,
  toDateNouns,
  tickLabel,
  tipLabel,
  tipPlacement,
  totalChange,
  windowEmpty,
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

const DESKTOP: ChartLayout = { width: 960, height: 274, plot: { left: 48, width: 904, top: 20, height: 200 }, labelY: 242, ticks: true };
const PHONE: ChartLayout = { width: 358, height: 212, plot: { left: 0, width: 358, top: 14, height: 166 }, labelY: 193, ticks: false };

interface Chip {
  id: number | null;
  label: string;
}

interface Bar extends BarGeometry {
  label: { month: string; year: string | null };
  last: boolean;
  running: boolean;
  /** The running month without a forecast: stripes say the bar is still filling. */
  hatched: boolean;
  hatch: { x1: number; y1: number; x2: number; y2: number }[];
  /** The running month's dashed 預估 box, from the bar's top up to the forecast. */
  forecast: { x: string; y: string; width: string; height: string } | null;
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

/** Which comparison columns the table shows, and how they read. */
interface CompareColumns {
  current: string;
  previous: string | null;
  lastYear: string | null;
  monthChangeHead: string;
  labels: { previous: string; lastYear: string };
  /** The muted notes naming each column left out because its window has no entries. */
  hiddenNotes: string[];
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
  /** Expense by category over the whole period (no comparison); only while the period runs. */
  readonly expenseWhole = input<BreakdownResponse | null>(null);
  /** The whole-period view: only the current column, from `expenseWhole`. */
  readonly whole = input(false);
  readonly today = input.required<string>();
  readonly period = input.required<ReportPeriod>();
  /** The selected category id; null = everything. */
  readonly series = input<number | null>(null);
  readonly seriesChange = output<number | null>();
  readonly toggleWhole = output<void>();

  private readonly layoutMode = inject(LayoutModeService);
  /** The phone drawing below 760 px, the desktop one from there (the SVG scales to the card either way). */
  readonly layout = computed<ChartLayout>(() => (this.layoutMode.mode() === 'phone' ? PHONE : DESKTOP));
  protected readonly viewBox = computed(() => `0 0 ${this.layout().width} ${this.layout().height}`);
  protected readonly plotLeft = computed(() => this.layout().plot.left);
  protected readonly plotRight = computed(() => this.layout().plot.left + this.layout().plot.width);

  /** The response the table and the chips show: the whole period in the whole view, else the to-date one. */
  private readonly shown = computed(() => (this.whole() ? (this.expenseWhole() ?? this.expense()) : this.expense()));
  private readonly categoryRows = computed(() => (this.shown()?.rows ?? []).filter(row => row.key !== null && /^\d+$/.test(row.key)));

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
  /** D4: the average of the months with data through the last finished one; null draws no line. */
  private readonly monthMean = computed(() =>
    monthAverage((this.trend()?.buckets ?? []).map((bucket, index) => ({ start: bucket.start, value: this.values()[index] })), this.today()),
  );
  readonly average = computed(() => this.monthMean()?.average ?? null);

  /**
   * 預估 for the running month (to-date view only): its figure so far plus the average month's share of the days left,
   * never below what the month already holds when the period is that month (the whole-period read covers it then).
   */
  readonly forecast = computed<RunningForecast | null>(() => {
    const buckets = this.trend()?.buckets ?? [];
    if (this.whole() || buckets.length === 0) return null;
    const start = buckets[buckets.length - 1].start.slice(0, 10);
    if (!runningBucket(start.slice(0, 7), 'month', this.today())) return null;
    const month = start.slice(0, 7);
    const end = `${month}-${String(daysInMonth(month)).padStart(2, '0')}`;
    const toDate = this.values()[buckets.length - 1];
    const period = this.period();
    let recorded: number | null = null;
    const cut = this.expense();
    const whole = this.expenseWhole();
    if (cut && whole && period.from >= start && period.to <= end) {
      const id = this.series();
      const amount = (response: BreakdownResponse) =>
        id === null ? metricTotal(response.totals, 'expense') : num(response.rows.find(row => row.key === String(id))?.amount);
      recorded = toDate + amount(whole) - amount(cut);
    }
    return runningForecast({ toDate, typical: this.average(), start, end, today: this.today(), recorded });
  });

  /** The tapped month (its bucket start); null shows no tip. A new category closes it. */
  readonly picked = signal<string | null>(null);
  private readonly closeOnSeries = effect(() => {
    this.series();
    this.picked.set(null);
  });
  /** One tap target per month: the whole column, so a thin phone bar is easy to hit. */
  readonly hits = computed(() => {
    const buckets = this.trend()?.buckets ?? [];
    const { plot } = this.layout();
    const slot = plot.width / Math.max(buckets.length, 1);
    return buckets.map((bucket, index) => ({
      key: bucket.start,
      label: tipLabel(this.tipAt(index)),
      x: (plot.left + slot * index).toFixed(1),
      y: plot.top.toFixed(1),
      width: slot.toFixed(1),
      height: plot.height.toFixed(1),
    }));
  });
  /** The tapped month's figure; the running month shows 實際 and 預估. */
  readonly tip = computed<BarTip | null>(() => {
    const key = this.picked();
    const index = (this.trend()?.buckets ?? []).findIndex(bucket => bucket.start === key);
    return index < 0 ? null : this.tipAt(index);
  });

  private tipAt(index: number): BarTip {
    const buckets = this.trend()!.buckets;
    const bar = this.bars()[index];
    const spansYears = buckets[0].start.slice(0, 4) !== buckets[buckets.length - 1].start.slice(0, 4);
    const name = bucketText(buckets[index].start, 'month', spansYears);
    const value = this.values()[index];
    const forecast = bar.running ? this.forecast() : null;
    const title = bar.running && !this.whole() ? `${name}（至 ${md(this.today(), this.today())}）` : name;
    const rows = forecast
      ? [{ label: '實際', value: dollars(value) }, { label: '預估', value: dollars(forecast.value) }]
      : [{ label: null, value: dollars(value) }];
    const top = Math.min(Number(bar.y), bar.forecast ? Number(bar.forecast.y) : Infinity);
    const { width, height } = this.layout();
    return { title, rows, ...tipPlacement(Number(bar.cx), top, width, height, rows.length) };
  }

  /** A tap (or Enter / Space) on a month toggles its tip; a tap elsewhere on the chart closes it. */
  pickBar(key: string, event: Event): void {
    event.stopPropagation();
    event.preventDefault();
    this.picked.update(current => (current === key ? null : key));
  }
  private readonly max = computed(() => niceCeiling(Math.max(...this.values(), this.average() ?? 0, this.forecast()?.value ?? 0, 0)));

  readonly bars = computed<Bar[]>(() => {
    const buckets = this.trend()?.buckets ?? [];
    const geometry = barGeometry(this.values(), this.layout().plot, this.max());
    return geometry.map((bar, index) => {
      const last = index === geometry.length - 1;
      const running = last && runningBucket(buckets[index].start.slice(0, 7), 'month', this.today());
      const forecast = running ? this.forecast() : null;
      const hatched = running && !forecast;
      const top = forecast ? yOf(forecast.value, this.layout().plot, this.max()) : 0;
      return {
        ...bar,
        label: axisParts(buckets[index].start, index),
        last,
        running,
        hatched,
        hatch: hatched ? hatchLines(Number(bar.x), Number(bar.y), Number(bar.width), Number(bar.height)) : [],
        forecast: forecast ? { x: bar.x, y: top.toFixed(1), width: bar.width, height: Math.max(Number(bar.y) - top, 0).toFixed(1) } : null,
      };
    });
  });
  readonly grid = computed(() => {
    if (!this.layout().ticks) return [];
    return [0, 0.5, 1].map(fraction => {
      const value = this.max() * fraction;
      const y = yOf(value, this.layout().plot, this.max());
      return { y: y.toFixed(1), textY: (y + 4).toFixed(1), label: tickLabel(value) };
    });
  });
  readonly averageY = computed(() => yOf(this.average() ?? 0, this.layout().plot, this.max()).toFixed(1));
  readonly averageTextY = computed(() => (yOf(this.average() ?? 0, this.layout().plot, this.max()) - 6).toFixed(1));
  readonly averageLabel = computed(() => `${this.monthMean()?.months ?? 0} 個月平均 ${n0(this.average())}`);
  readonly chartAria = computed(() => {
    const buckets = this.trend()?.buckets ?? [];
    const values = this.values();
    const spansYears = buckets.length > 0 && buckets[0].start.slice(0, 4) !== buckets[buckets.length - 1].start.slice(0, 4);
    const name = (bucket: { start: string }, index: number) => {
      const text = bucketText(bucket.start, 'month', spansYears);
      return index === buckets.length - 1 && runningBucket(bucket.start.slice(0, 7), 'month', this.today()) ? `${text} ${runningSuffix(this.today())}` : text;
    };
    const forecast = this.forecast();
    const value = (index: number) => (index === buckets.length - 1 && forecast ? `${n0(values[index])}，預估 ${n0(forecast.value)}` : n0(values[index]));
    return `${this.seriesName()}近 ${buckets.length} 個月：${buckets.map((bucket, index) => `${name(bucket, index)} ${value(index)}`).join('、')}`;
  });

  /** D1: the to-date columns while the period runs (and not in the whole view). */
  private readonly toDate = computed(() => isRunning(this.period(), this.today()) && !this.whole());

  /**
   * The card title follows the columns: the to-date nouns while the period runs, the period's own words once it is over;
   * the whole view has no comparison columns, so it names the period only.
   */
  readonly compareTitle = computed(() => {
    if (this.whole()) return `類別支出 · ${periodLabel(this.period())}`;
    return `比較 · ${this.toDate() ? runningCompareTitle(this.period(), this.today()) : compareTitle(this.period())}`;
  });

  /**
   * The comparison columns. A running period shows `本月至今（…） / 上月同期 / 去年同期`; one comparison (`single`) is read
   * from the `compare=last_year` response. A column whose window has no entries is dropped with its change column (D3);
   * the whole view keeps the current column only.
   */
  readonly columns = computed<CompareColumns>(() => {
    const period = this.period();
    const running = this.toDate();
    const nouns = running ? toDateNouns(period, this.today()) : periodNouns(period);
    const labels = running ? runningCompareLabels(period, this.today()) : compareLabels(period);
    const monthChangeHead = period.unit === 'month' && !nouns.single ? '月增減' : '增減';
    if (this.whole()) return { current: periodNouns(period).current, previous: null, lastYear: null, monthChangeHead, labels, hiddenNotes: [] };
    if (nouns.single) {
      const empty = windowEmpty(this.singleSource());
      return {
        current: nouns.current,
        previous: empty ? null : nouns.previous,
        lastYear: null,
        monthChangeHead,
        labels,
        hiddenNotes: empty ? ['去年同期沒有記錄，已隱藏比較欄'] : [],
      };
    }
    const previousEmpty = windowEmpty(this.expense());
    const lastYearEmpty = windowEmpty(this.lastYear());
    return {
      current: nouns.current,
      previous: previousEmpty ? null : nouns.previous,
      lastYear: lastYearEmpty ? null : nouns.lastYear,
      monthChangeHead,
      labels,
      hiddenNotes: [...(previousEmpty ? ['上一期沒有記錄，已隱藏比較欄'] : []), ...(lastYearEmpty ? ['去年同期沒有記錄，已隱藏比較欄'] : [])],
    };
  });

  /** The one comparison of a `single` screen: last year's response while a period runs, else the previous one (the same window). */
  private readonly singleSource = computed(() => (this.toDate() ? this.lastYear() : this.expense()));

  /** R3 on expense; 改回截至 whenever the whole view is on. */
  readonly afterToday = computed(() => afterTodayLine(this.expense(), this.expenseWhole(), 'expense', this.today(), this.whole()));

  readonly compareRows = computed<CompareRow[]>(() => {
    const expense = this.shown();
    if (!expense) return [];
    const single = this.toDate() ? toDateNouns(this.period(), this.today()).single : periodNouns(this.period()).single;
    const previousEmpty = windowEmpty(single ? this.singleSource() : expense);
    const lastYearEmpty = windowEmpty(this.lastYear());
    const previousText = single ? '去年無資料' : '上期無資料';
    const previousOf = new Map((single ? this.singleSource() : expense)?.rows.map(row => [row.key, num(row.compare_amount)]));
    const lastYearByKey = new Map((this.lastYear()?.rows ?? []).map(row => [row.key, num(row.compare_amount)]));
    const rows = this.categoryRows()
      .map((row, index) => {
        const current = num(row.amount);
        const previous = previousOf.get(row.key) ?? 0;
        const yearAgo = lastYearByKey.get(row.key) ?? 0;
        return {
          key: row.key!,
          label: row.label,
          color: row.color ?? seriesColor(index),
          total: false,
          current: n0(current),
          previous: n0(previous),
          lastYear: n0(yearAgo),
          monthChange: baseChange(current, previous, previousEmpty, previousText, true),
          yearChange: baseChange(current, yearAgo, lastYearEmpty, '去年無資料', true),
          sort: current,
        };
      })
      .sort((a, b) => b.sort - a.sort);
    const current = num(expense.totals.expense);
    const previous = num((single ? this.singleSource() : expense)?.compare?.totals.expense);
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
        // F4: the total is a delta (never 新增 / 停止), or the empty-window note.
        monthChange: totalChange(current, previous, previousEmpty, previousText, true),
        yearChange: totalChange(current, yearAgo, lastYearEmpty, '去年無資料', true),
      },
    ];
  });

  pick(id: number | null): void {
    if (id !== this.series()) this.seriesChange.emit(id);
  }
}
