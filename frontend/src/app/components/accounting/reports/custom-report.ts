import { Location, NgTemplateOutlet } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, effect, inject, signal, untracked } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { catchError, forkJoin, of } from 'rxjs';

import { CategoryNode, LedgerAccount, Project } from '../../../models/accounting.model';
import {
  BreakdownResponse,
  BreakdownRow,
  PeriodPreset,
  ReportChart,
  ReportCompare,
  ReportConfig,
  ReportDimension,
  ReportFilters,
  ReportMetric,
  SavedReport,
  SavedReportInput,
} from '../../../models/reports.model';
import { AccountingService } from '../../../services/accounting.service';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { ReportsService } from '../../../services/reports.service';
import { SwitchComponent } from '../../ui/switch/switch';
import { addDays, todayIso } from '../dates';
import { writeErrorMessage } from '../http-errors';
import {
  CHART_LABELS,
  COMPARE_LABELS,
  DIMENSION_LABELS,
  GROUP_DIMENSIONS,
  METRIC_LABELS,
  PRESETS,
  PRESET_LABELS,
  ReferenceLists,
  TEMPLATES,
  WEEKDAY_LABELS,
  chartOptions,
  configQuery,
  configValid,
  describeConfig,
  emptyConfig,
  filterChips,
  fittingChart,
  isPreset,
  resolvePeriod,
  withoutFilter,
} from './report-config';
import {
  BucketGranularity,
  Change,
  ReportPeriod,
  afterTodayLine,
  baseChange,
  axisParts,
  bucketText,
  hatchLines,
  donutArcs,
  isRunning,
  metricTotal as totalOf,
  n0,
  niceCeiling,
  num,
  pct1,
  runningBucket,
  runningCompareLabels,
  shiftDate,
  runningSuffix,
  seriesColor,
  tickLabel,
  toDateNouns,
  totalChange,
  windowEmpty,
  yOf,
  dollars,
} from './report-math';
import type { LoadState } from './reports';

type FilterKind = 'account_id' | 'category_id' | 'project_id' | 'tag' | 'merchant' | 'weekday';

const FILTER_KINDS: [FilterKind, string][] = [
  ['category_id', '類別'],
  ['account_id', '帳戶'],
  ['project_id', '專案'],
  ['tag', '標籤'],
  ['merchant', '商家'],
  ['weekday', '星期'],
];

/** The preview chart's drawing (ReportBuilder 760 × 280; ReportSavedPhone 358 × 210). */
interface ChartLayout {
  width: number;
  height: number;
  plot: { left: number; width: number; top: number; height: number };
  labelY: number;
  ticks: boolean;
}

const DESKTOP: ChartLayout = { width: 760, height: 290, plot: { left: 52, width: 700, top: 30, height: 210 }, labelY: 262, ticks: true };
const PHONE: ChartLayout = { width: 358, height: 226, plot: { left: 0, width: 358, top: 20, height: 166 }, labelY: 204, ticks: false };
const MAX_SERIES = 7;

/** The hatch over a running stack: stripe segments for the bar's rectangle, in the SVG's one-decimal strings. */
function stripes(x: number, y: number, width: number, height: number): Rect['hatch'] {
  return hatchLines(x, y, width, height).map(l => ({ x1: l.x1.toFixed(1), y1: l.y1.toFixed(1), x2: l.x2.toFixed(1), y2: l.y2.toFixed(1) }));
}

/** The start of the bucket `today` falls in, when it is still running (a month, or a Monday-based week); null for other groupings. */
function runningBucketStart(group: ReportDimension, today: string): string | null {
  let start: string;
  if (group === 'month') start = `${today.slice(0, 7)}-01`;
  else if (group === 'week') start = addDays(today, -((new Date(`${today}T00:00:00Z`).getUTCDay() + 6) % 7));
  else return null;
  return runningBucket(start, group, today) ? start : null;
}

/** The first day of the bucket a row's key names. */
function bucketStart(key: string, group: ReportDimension): string {
  return group === 'month' ? `${key.slice(0, 7)}-01` : key;
}

/**
 * F2: the comparison the to-date read asks for. A running period whose windows coincide (`single`, a running year) is
 * labelled 去年同期 either way, so `previous` asks for `last_year`: the API's previous window is another range then.
 */
function toDateCompare(compare: ReportCompare | undefined, period: ReportPeriod, today: string): ReportCompare | undefined {
  return compare === 'previous' && runningCompareLabels(period, today).single ? 'last_year' : compare;
}

interface Figure {
  label: string;
  value: string;
  foot: string;
  direction: Change['direction'] | null;
}

interface Rect {
  x: string;
  y: string;
  width: string;
  height: string;
  fill: string;
  outline: boolean;
  /** The running bucket's stack: a hatch is drawn over it. */
  running: boolean;
  hatch: { x1: string; y1: string; x2: string; y2: string }[];
}

interface XLabel {
  x: string;
  label: string;
  /** `group_by: month`: the axis draws the month and, on the first bar and each January, the year as a second line. */
  parts: { month: string; year: string | null } | null;
  total: string;
  totalY: string;
  pointY: string;
}

type ColumnKind = 'series' | 'total' | 'count' | 'base' | 'change';

interface TableCell {
  text: string;
  kind: ColumnKind;
  muted?: boolean;
  direction?: Change['direction'];
}

interface TableRow {
  label: string;
  total: boolean;
  running?: boolean;
  cells: TableCell[];
}

/**
 * 自訂報表 (`/accounting/reports/custom/new`, `/custom/:id`; openspec `add-accounting-reports` D4–D6). From 760 px:
 * the editor — name, period, 看什麼 / 依什麼分組 / 再細分, filters, chart, 比較, 釘選 — beside a live preview (figures,
 * chart, table) with 儲存 / 另存新報表 / 刪除. Below 760 px: the saved report read-only, 編輯條件 in a bottom sheet
 * that applies without saving; creating is desktop only.
 */
@Component({
  selector: 'app-custom-report',
  imports: [NgTemplateOutlet, RouterLink, SwitchComponent],
  templateUrl: './custom-report.html',
  styleUrls: ['../sheet.scss', './custom-report.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '(document:keydown.escape)': 'onEscape($event)' },
})
export class CustomReportComponent {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly location = inject(Location);
  private readonly reports = inject(ReportsService);
  private readonly accounting = inject(AccountingService);
  private readonly layoutMode = inject(LayoutModeService);
  private previewRequestId = 0;

  protected readonly presets = PRESETS;
  protected readonly presetLabels = PRESET_LABELS;
  protected readonly metrics = Object.entries(METRIC_LABELS) as [ReportMetric, string][];
  protected readonly dimensions = GROUP_DIMENSIONS.map(id => [id, DIMENSION_LABELS[id]] as [ReportDimension, string]);
  protected readonly compares = Object.entries(COMPARE_LABELS) as [ReportCompare, string][];
  protected readonly filterKinds = FILTER_KINDS;
  protected readonly weekdays = WEEKDAY_LABELS;
  protected readonly templates = TEMPLATES;
  protected readonly chartLabels = CHART_LABELS;

  private readonly params = toSignal(this.route.paramMap, { initialValue: this.route.snapshot.paramMap });
  /** null = `/custom/new`. */
  readonly id = computed(() => {
    const id = this.params().get('id');
    return id && /^\d+$/.test(id) ? Number(id) : null;
  });
  readonly isNew = computed(() => this.id() === null);
  readonly phone = computed(() => this.layoutMode.mode() === 'phone');
  readonly today = signal(todayIso());
  readonly sample = this.reports.sample;

  // ---- saved reports and reference lists ------------------------------------------------------------------------

  readonly saved = signal<SavedReport[]>([]);
  readonly savedState = signal<LoadState>('loading');
  readonly refs = signal<ReferenceLists>({ categories: [], accounts: [], projects: [] });

  /** The report being edited (none for a new one or an unknown id). */
  readonly report = computed(() => this.saved().find(r => r.id === this.id()) ?? null);
  readonly missing = computed(() => !this.isNew() && this.savedState() === 'ready' && !this.report());

  // ---- the draft -----------------------------------------------------------------------------------------------

  readonly name = signal('');
  readonly config = signal<ReportConfig>(emptyConfig());
  readonly chart = signal<ReportChart>('bar');
  readonly pinned = signal(false);
  /** A new report shows the empty state until a grouping or a template is chosen. */
  readonly started = signal(false);
  readonly staleRefs = computed(() => this.report()?.stale_refs ?? []);

  readonly sentence = computed(() => (this.started() ? describeConfig(this.config(), this.refs()) : '還沒有條件'));
  readonly periodNote = computed(() => {
    const period = this.config().period;
    const resolved = resolvePeriod(period, this.today());
    const dates = `${resolved.from.replaceAll('-', '/')} – ${resolved.to.replaceAll('-', '/')}`;
    return isPreset(period) && period.preset !== 'all' ? `${dates} · 相對期間：每次打開都往後滾動` : dates;
  });
  readonly currentPreset = computed(() => (isPreset(this.config().period) ? (this.config().period as { preset: PeriodPreset }).preset : 'custom'));
  readonly customFrom = computed(() => (isPreset(this.config().period) ? '' : (this.config().period as { from: string }).from));
  readonly customTo = computed(() => (isPreset(this.config().period) ? '' : (this.config().period as { to: string }).to));
  readonly chips = computed(() => filterChips(this.config().filters, this.refs()));
  readonly charts = computed(() => chartOptions(this.config()));
  readonly splitOptions = computed(() => this.dimensions.filter(([id]) => id !== this.config().group_by));

  // ---- the add-filter form -------------------------------------------------------------------------------------

  readonly filterKind = signal<FilterKind>('category_id');
  readonly filterValue = signal('');
  readonly filterOptions = computed<{ value: string; label: string }[]>(() => {
    const refs = this.refs();
    switch (this.filterKind()) {
      case 'category_id':
        return refs.categories.flatMap(root => [{ value: String(root.id), label: root.name }, ...root.children.map(child => ({ value: String(child.id), label: `${root.name} › ${child.name}` }))]);
      case 'account_id':
        return refs.accounts.map(account => ({ value: String(account.id), label: account.name }));
      case 'project_id':
        return refs.projects.map(project => ({ value: String(project.id), label: project.name }));
      case 'weekday':
        return WEEKDAY_LABELS.map((label, index) => ({ value: String(index + 1), label: `星期${label}` }));
      default:
        return [];
    }
  });

  // ---- preview ---------------------------------------------------------------------------------------------------

  /** The to-date read (the whole period when it is not running, which is also the only read then). */
  readonly preview = signal<BreakdownResponse | null>(null);
  /** The whole period without a comparison; only while the period runs. */
  readonly previewWhole = signal<BreakdownResponse | null>(null);
  /** R4: the whole-period view; reset when the conditions or the report change, and not kept in the URL. */
  readonly wholeView = signal(false);
  /** The running bucket's figure over the previous bucket's same days (R2); null when there is none or its read failed. */
  readonly runningBase = signal<number | null>(null);
  private readonly runningBaseEmpty = signal(false);
  private readonly period = computed(() => resolvePeriod(this.config().period, this.today()));
  /** The period has started and its last day is still ahead. */
  readonly running = computed(() => isRunning(this.period(), this.today()));
  /** What the screen reads: the whole period in the whole view, else the to-date read. */
  private readonly view = computed(() => (this.wholeView() && this.previewWhole() ? this.previewWhole() : this.preview()));
  private readonly isWhole = computed(() => this.wholeView() && !!this.previewWhole());
  private readonly toDate = computed(() => this.running() && !this.isWhole());
  /** The comparison in play: none in the whole view. */
  private readonly cmp = computed(() => (this.isWhole() ? undefined : this.config().compare));
  /** D3: the comparison window has no entries, so its columns are left out. */
  private readonly compareEmpty = computed(() => !!this.cmp() && windowEmpty(this.view()));
  private readonly compareOn = computed(() => !!this.cmp() && !this.compareEmpty());
  private readonly spansYears = computed(() => this.period().from.slice(0, 4) !== this.period().to.slice(0, 4));
  /** The first day of the running bucket (to-date view of a month or week report), else null. */
  private readonly runningStart = computed(() => (this.toDate() ? runningBucketStart(this.config().group_by, this.today()) : null));
  readonly previewState = signal<LoadState>('loading');
  readonly showPreview = computed(() => this.started() && configValid(this.config()));
  readonly layout = computed<ChartLayout>(() => (this.phone() ? PHONE : DESKTOP));
  protected readonly viewBox = computed(() => `0 0 ${this.layout().width} ${this.layout().height}`);

  private readonly rows = computed<BreakdownRow[]>(() => this.view()?.rows ?? []);
  /** The rows added up (the table's 合計 row and the donut shares); above the period when tag rows overlap. */
  private readonly metricTotal = computed(() => this.rows().reduce((sum, row) => sum + num(row.amount), 0));
  private readonly rowCount = computed(() => this.rows().reduce((sum, row) => sum + row.count, 0));
  private readonly compareRowTotal = computed(() => (this.compareOn() ? this.rows().reduce((sum, row) => sum + (this.baseOf(row) ?? 0), 0) : null));
  /** The period's own figures from `totals` / `compare.totals` (each entry once, whatever the grouping). */
  private readonly periodTotal = computed(() => totalOf(this.view()?.totals, this.config().metric));
  private readonly count = computed(() => this.view()?.totals.count ?? 0);
  private readonly compareTotal = computed(() => {
    const compare = this.view()?.compare;
    return this.compareOn() && compare ? totalOf(compare.totals, this.config().metric) : null;
  });
  /** `group_by` / `split_by` 標籤: an entry counts once per tag, so the rows add up to more than the period. */
  readonly overlapping = computed(() => !!this.view()?.overlapping);
  readonly groupLabel = computed(() => DIMENSION_LABELS[this.config().group_by]);
  /** The comparison's name: 上期同期 / 去年同期 while the period runs (D1), else 上一期 / 去年同期. */
  readonly compareLabel = computed(() => {
    const compare = this.config().compare;
    if (!compare) return '';
    if (!this.toDate()) return COMPARE_LABELS[compare];
    const nouns = toDateNouns(this.period(), this.today());
    return compare === 'previous' ? nouns.previous : nouns.lastYear;
  });

  private isRunningRow(row: BreakdownRow): boolean {
    const start = this.runningStart();
    return start !== null && row.key !== null && row.key !== '__other__' && bucketStart(row.key, this.config().group_by) === start;
  }

  /** A row's comparison figure: the running bucket's is the previous bucket's same days (R2); null when that read failed (M4). */
  private baseOf(row: BreakdownRow): number | null {
    return this.isRunningRow(row) ? this.runningBase() : num(row.compare_amount);
  }

  /**
   * F4: the base of a total. `compare.totals` (each entry once), else the rows' bases added up; the 合計 row of a tag
   * grouping adds up overlapping rows, so it takes the rows' bases (`rowSum`). The figure and the row share this path.
   */
  private totalBase(rowSum: boolean): number | null {
    if (!this.compareOn()) return null;
    return rowSum && this.config().group_by === 'tag' ? this.compareRowTotal() : (this.compareTotal() ?? this.compareRowTotal());
  }

  /** The running bucket's comparison window has no entries. */
  private emptyBase(row: BreakdownRow): boolean {
    return this.isRunningRow(row) && this.runningBaseEmpty();
  }

  /** The split series in the chart and the table: the largest seven, the rest folded into 其他. */
  readonly seriesKeys = computed<string[]>(() => {
    if (!this.config().split_by) return [];
    const totals = new Map<string, number>();
    for (const row of this.rows()) for (const split of row.splits ?? []) totals.set(split.label, (totals.get(split.label) ?? 0) + num(split.amount));
    const sorted = [...totals.entries()].sort((a, b) => b[1] - a[1]).map(([label]) => label);
    return sorted.length > MAX_SERIES ? [...sorted.slice(0, MAX_SERIES - 1), '其他'] : sorted;
  });

  private seriesValues(row: BreakdownRow): number[] {
    const keys = this.seriesKeys();
    const values = keys.map(() => 0);
    for (const split of row.splits ?? []) {
      let index = keys.indexOf(split.label);
      if (index === -1) index = keys.indexOf('其他');
      if (index !== -1) values[index] += num(split.amount);
    }
    return values;
  }

  private axisParts(row: BreakdownRow, index: number): { month: string; year: string | null } | null {
    return this.config().group_by === 'month' && row.key !== null && row.key !== '__other__' ? axisParts(row.key, index) : null;
  }

  /** A row's name; time dimensions read as D7 words (`9 月`, `10/5 週`), without the running suffix. */
  protected bucketLabel(row: BreakdownRow): string {
    const group = this.config().group_by;
    if (row.key === null) return `（無${DIMENSION_LABELS[group]}）`;
    if (row.key === '__other__') return '其他';
    if (group === 'month' || group === 'week' || group === 'day') return bucketText(row.key, group as BucketGranularity, this.spansYears());
    return row.label;
  }

  protected rowLabel(row: BreakdownRow): string {
    const label = this.bucketLabel(row);
    return this.isRunningRow(row) ? `${label} ${runningSuffix(this.today())}` : label;
  }

  readonly figures = computed<Figure[]>(() => {
    const rows = this.rows();
    if (!rows.length) return [];
    const total = this.periodTotal();
    const count = this.count();
    const base = this.totalBase(false);
    const money = (value: number) => (this.config().metric === 'count' ? n0(value) : dollars(value));
    const max = rows.reduce((best, row) => (num(row.amount) > num(best.amount) ? row : best), rows[0]);
    const byMonth = this.config().group_by === 'month';
    // F4: a total is a delta, never 新增 / 停止 (an empty window has no comparison column at all).
    const diff = base !== null ? totalChange(total, base, false, '上期無資料', true) : null;
    // 平均每月 / 平均每組 leave the running bucket out of the sum and the count.
    const skip = this.running() ? runningBucketStart(this.config().group_by, this.today()) : null;
    const running = skip ? rows.find(row => row.key !== null && row.key !== '__other__' && bucketStart(row.key, this.config().group_by) === skip) : undefined;
    const averaged = rows.length - (running ? 1 : 0);
    const averagedTotal = running ? total - num(running.amount) : total;
    return [
      {
        label: '合計',
        value: money(total),
        foot: diff ? `${diff.text} 比${this.compareLabel()}` : `${count} 筆`,
        direction: diff?.direction ?? null,
      },
      { label: `平均每${byMonth ? '月' : '組'}`, value: averaged ? money(averagedTotal / averaged) : '—', foot: `${averaged} 組`, direction: null },
      { label: '每筆平均', value: money(count ? total / count : 0), foot: `${count} 筆`, direction: null },
      { label: '最高', value: this.rowLabel(max), foot: money(num(max.amount)), direction: null },
    ];
  });

  readonly legend = computed<{ label: string; color: string; outline: boolean }[]>(() => {
    if (this.chart() === 'donut') {
      const total = this.metricTotal();
      return this.rows().map((row, index) => ({ label: `${this.rowLabel(row)} ${pct1(num(row.amount), total)}`, color: seriesColor(index), outline: false }));
    }
    const keys = this.seriesKeys();
    const legend = keys.length ? keys.map((label, index) => ({ label, color: seriesColor(index), outline: false })) : [{ label: METRIC_LABELS[this.config().metric], color: seriesColor(0), outline: false }];
    if (this.cmp()) legend.push({ label: this.compareLabel(), color: 'transparent', outline: true });
    return legend;
  });

  private readonly max = computed(() => {
    const rows = this.rows();
    const values = rows.map(row => num(row.amount));
    if (this.compareOn()) values.push(...rows.map(row => this.baseOf(row) ?? 0));
    return niceCeiling(Math.max(...values, 0));
  });

  readonly grid = computed(() => {
    if (!this.layout().ticks) return [];
    return [0, 0.5, 1].map(fraction => {
      const value = this.max() * fraction;
      const y = yOf(value, this.layout().plot, this.max());
      return { y: y.toFixed(1), textY: (y + 4).toFixed(1), label: tickLabel(value) };
    });
  });

  /** Stacked bars (one per series) and, with a comparison, a dashed outline beside each. */
  readonly rects = computed<Rect[]>(() => {
    const rows = this.rows();
    const { plot } = this.layout();
    const compare = this.compareOn();
    const slot = plot.width / Math.max(rows.length, 1);
    const width = Math.min(slot * (compare ? 0.36 : 0.5), 90);
    const bottom = plot.top + plot.height;
    const max = this.max();
    const rects: Rect[] = [];
    rows.forEach((row, index) => {
      const cx = plot.left + slot * index + slot / 2;
      const x = compare ? cx - width - 3 : cx - width / 2;
      const values = this.seriesKeys().length ? this.seriesValues(row) : [num(row.amount)];
      let y = bottom;
      values.forEach((value, k) => {
        const height = max > 0 ? (value / max) * plot.height : 0;
        y -= height;
        if (height > 0) rects.push({ x: x.toFixed(1), y: y.toFixed(1), width: width.toFixed(1), height: Math.max(height - 1, 0).toFixed(1), fill: seriesColor(k), outline: false, running: this.isRunningRow(row), hatch: this.isRunningRow(row) ? stripes(x, y, width, Math.max(height - 1, 0)) : [] });
      });
      const base = this.baseOf(row);
      if (compare && base !== null) {
        const top = yOf(base, plot, max);
        rects.push({ x: (cx + 3).toFixed(1), y: top.toFixed(1), width: width.toFixed(1), height: (bottom - top).toFixed(1), fill: 'transparent', outline: true, running: false, hatch: [] });
      }
    });
    return rects;
  });

  readonly xLabels = computed<XLabel[]>(() => {
    const rows = this.rows();
    const { plot, labelY } = this.layout();
    const slot = plot.width / Math.max(rows.length, 1);
    return rows.map((row, index) => {
      const total = num(row.amount);
      const y = yOf(Math.max(total, this.compareOn() ? (this.baseOf(row) ?? 0) : 0), plot, this.max());
      return { x: (plot.left + slot * index + slot / 2).toFixed(1), label: this.bucketLabel(row), parts: this.axisParts(row, index), total: n0(total), totalY: (y - 8).toFixed(1), pointY: yOf(total, plot, this.max()).toFixed(1) };
    });
  });
  protected readonly labelY = computed(() => this.layout().labelY);
  readonly linePoints = computed(() => this.xLabels().map(x => `${x.x},${x.pointY}`).join(' '));
  readonly comparePoints = computed(() => {
    if (!this.compareOn()) return '';
    const { plot } = this.layout();
    // N2: a bucket without a base (the running one when its base read failed) has no point; the line skips it.
    const points: string[] = [];
    this.rows().forEach((row, index) => {
      const base = this.baseOf(row);
      if (base !== null) points.push(`${this.xLabels()[index].x},${yOf(base, plot, this.max()).toFixed(1)}`);
    });
    return points.join(' ');
  });
  readonly arcs = computed(() => donutArcs(this.rows().map((row, index) => ({ value: num(row.amount), color: seriesColor(index) }))));
  readonly chartAria = computed(() => `${this.name() || '報表'}：${this.rows().map(row => `${this.rowLabel(row)} ${n0(row.amount)}`).join('、')}`);

  readonly columns = computed<{ label: string; kind: ColumnKind }[]>(() => [
    ...this.seriesKeys().map(label => ({ label, kind: 'series' as const })),
    { label: this.toDate() ? toDateNouns(this.period(), this.today()).current : '合計', kind: 'total' },
    { label: '筆數', kind: 'count' },
    ...(this.compareOn() ? [{ label: this.compareLabel(), kind: 'base' as const }, { label: '增減', kind: 'change' as const }] : []),
  ]);
  /** D3: muted notes naming the comparison column left out because its window has no entries. */
  readonly hiddenNotes = computed(() => {
    if (!this.compareEmpty()) return [];
    const compare = this.toDate() ? toDateCompare(this.config().compare, this.period(), this.today()) : this.config().compare;
    return [compare === 'previous' ? '上一期沒有記錄，已隱藏比較欄' : '去年同期沒有記錄，已隱藏比較欄'];
  });
  /** R3 on the report's metric; 改回截至 whenever the whole view is on. */
  readonly afterToday = computed(() => afterTodayLine(this.preview(), this.previewWhole(), this.config().metric, this.today(), this.isWhole()));
  readonly tableRows = computed<TableRow[]>(() => {
    const rows = this.rows();
    if (!rows.length) return [];
    const keys = this.seriesKeys();
    const compare = this.compareOn();
    const cells = (values: number[], total: number, count: number, base: number | null, empty = false, isTotal = false): TableCell[] => {
      const list: TableCell[] = [
        ...values.map(value => ({ text: n0(value), kind: 'series' as const })),
        { text: n0(total), kind: 'total' },
        { text: String(count), kind: 'count', muted: true },
      ];
      if (compare && base === null) {
        // M4: the running bucket's base read failed, so the row shows no change.
        list.push({ text: '—', kind: 'base', muted: true }, { text: '—', kind: 'change', muted: true });
      } else if (compare) {
        // F4: a key may be 新增 / 停止; the 合計 row is a delta.
        const diff = (isTotal ? totalChange : baseChange)(total, base ?? 0, empty, '上期無資料', false);
        list.push(
          { text: n0(base ?? 0), kind: 'base', muted: true },
          { text: diff.text, kind: 'change', direction: diff.direction, muted: diff.kind !== 'delta' },
        );
      }
      return list;
    };
    const body = rows.map(row => ({ label: this.rowLabel(row), total: false, cells: cells(this.seriesValues(row), num(row.amount), row.count, compare ? this.baseOf(row) : null, this.emptyBase(row)), running: this.isRunningRow(row) }));
    const seriesTotals = keys.map((_, k) => rows.reduce((sum, row) => sum + this.seriesValues(row)[k], 0));
    return [...body, { label: '合計', total: true, cells: cells(seriesTotals, this.metricTotal(), this.rowCount(), this.totalBase(true), false, true) }];
  });

  // ---- phone: 編輯條件 sheet -----------------------------------------------------------------------------------

  readonly sheetOpen = signal(false);
  readonly sheetDraft = signal<ReportConfig>(emptyConfig());

  // ---- writes ------------------------------------------------------------------------------------------------------

  readonly saving = signal(false);
  readonly message = signal<string | null>(null);
  readonly confirmingDelete = signal(false);

  constructor() {
    this.reports.listSaved().subscribe({
      next: list => {
        this.saved.set(list);
        this.savedState.set('ready');
      },
      error: () => this.savedState.set('error'),
    });
    forkJoin({ categories: this.accounting.getCategories('expense'), accounts: this.accounting.getAccounts(), projects: this.accounting.getProjects() }).subscribe({
      next: refs => this.refs.set(refs),
      error: () => undefined,
    });
    // Another id (or the list arriving): the draft follows the saved report; `new` starts empty. A save that only
    // refreshes the same report in the list leaves the draft (and the preview) alone.
    let applied: number | null | undefined;
    effect(() => {
      const report = this.report();
      const isNew = this.isNew();
      untracked(() => {
        const key = report ? report.id : isNew ? null : undefined;
        if (key === applied) return;
        applied = key;
        if (report) {
          this.name.set(report.name);
          this.config.set(structuredClone(report.config));
          this.chart.set(fittingChart(report.config, report.chart));
          this.pinned.set(report.pinned);
          this.started.set(true);
        } else if (isNew) {
          this.name.set('');
          this.config.set(emptyConfig());
          this.chart.set('bar');
          this.pinned.set(false);
          this.started.set(false);
        }
        this.message.set(null);
        this.confirmingDelete.set(false);
      });
    });
    effect(() => {
      const show = this.showPreview();
      const config = this.config();
      const today = this.today();
      untracked(() => {
        this.wholeView.set(false);
        if (show) this.loadPreview(config, today);
        else this.preview.set(null);
      });
    });
  }

  private loadPreview(config: ReportConfig, today: string): void {
    const id = ++this.previewRequestId;
    this.previewState.set('loading');
    const query = configQuery(config, today);
    const period = resolvePeriod(config.period, today);
    const fail = () => {
      if (id !== this.previewRequestId) return;
      this.preview.set(null);
      this.previewWhole.set(null);
      this.runningBase.set(null);
      this.previewState.set('error');
    };
    if (!isRunning(period, today)) {
      this.reports.getBreakdown(query).subscribe({
        next: preview => {
          if (id !== this.previewRequestId) return;
          this.preview.set(preview);
          this.previewWhole.set(null);
          this.runningBase.set(null);
          this.runningBaseEmpty.set(false);
          this.previewState.set('ready');
        },
        error: fail,
      });
      return;
    }
    // D1: the to-date read, the whole period (no comparison) for 「另有…」, and with a comparison on a month or week
    // report the previous bucket's same days (R2) for the running bucket's change.
    // The base covers the same days as the running bucket's visible part: s = max(bucket start, period start), moved
    // back one calendar month (day clipped) or seven days.
    const bucket = config.compare ? runningBucketStart(config.group_by, today) : null;
    const s = bucket && bucket < period.from ? period.from : bucket;
    const baseWindow = s ? (config.group_by === 'month' ? { from: shiftDate(s, -1), to: shiftDate(today, -1) } : { from: addDays(s, -7), to: addDays(today, -7) }) : null;
    // M4: the whole and the base reads are auxiliary; a failure drops the 「另有」 line or the running bucket's change only.
    forkJoin({
      toDate: this.reports.getBreakdown({ ...query, date_to: today, compare: toDateCompare(config.compare, period, today) }),
      whole: this.reports.getBreakdown({ ...query, compare: undefined }).pipe(catchError(() => of(null))),
      base: baseWindow
        ? this.reports.getBreakdown({ ...query, date_from: baseWindow.from, date_to: baseWindow.to, split_by: undefined, compare: undefined }).pipe(catchError(() => of(null)))
        : of(null),
    }).subscribe({
      next: ({ toDate, whole, base }) => {
        if (id !== this.previewRequestId) return;
        this.preview.set(toDate);
        this.previewWhole.set(whole);
        this.runningBase.set(base ? totalOf(base.totals, config.metric) : null);
        this.runningBaseEmpty.set(!!base && base.totals.count === 0);
        this.previewState.set('ready');
      },
      error: fail,
    });
  }

  // ---- editing ---------------------------------------------------------------------------------------------------

  private patch(patch: Partial<ReportConfig>): void {
    this.started.set(true);
    const next = { ...this.config(), ...patch };
    if (next.split_by === next.group_by) delete next.split_by;
    this.config.set(next);
    this.chart.set(fittingChart(next, this.chart()));
  }

  setPreset(preset: PeriodPreset | 'custom'): void {
    if (preset === 'custom') {
      const resolved = resolvePeriod(this.config().period, this.today());
      this.patch({ period: { from: resolved.from, to: resolved.to } });
    } else {
      this.patch({ period: { preset } });
    }
  }

  setCustomRange(from: string, to: string): void {
    if (from && to) this.patch({ period: from <= to ? { from, to } : { from: to, to: from } });
  }

  setMetric(value: string): void {
    this.patch({ metric: value as ReportMetric });
  }

  setGroup(value: string): void {
    this.patch({ group_by: value as ReportDimension });
  }

  setSplit(value: string): void {
    this.patch({ split_by: value ? (value as ReportDimension) : undefined });
  }

  setCompare(value: string): void {
    this.patch({ compare: value ? (value as ReportCompare) : undefined });
  }

  setChart(chart: ReportChart): void {
    if (!this.charts().find(option => option.chart === chart)?.reason) this.chart.set(chart);
  }

  removeFilter(key: keyof ReportFilters): void {
    this.patch({ filters: withoutFilter(this.config().filters, key) });
  }

  setFilterKind(value: string): void {
    this.filterKind.set(value as FilterKind);
    this.filterValue.set('');
  }

  /** Adds the form's value to the filter: ids and weekdays append to their list, text replaces. */
  addFilter(): void {
    const kind = this.filterKind();
    const value = this.filterValue().trim();
    if (!value) return;
    const filters = { ...this.config().filters };
    if (kind === 'merchant') filters.merchant = value;
    else if (kind === 'tag') filters.tag = [...new Set([...(filters.tag ?? []), ...value.split(/[\s,，、]+/).filter(Boolean)])];
    else filters[kind] = [...new Set([...(filters[kind] ?? []), Number(value)])];
    this.patch({ filters });
    this.filterValue.set('');
  }

  useTemplate(template: (typeof TEMPLATES)[number]): void {
    this.name.set(template.name);
    this.config.set(structuredClone(template.config));
    this.chart.set(fittingChart(template.config, template.chart));
    this.started.set(true);
  }

  private input(): SavedReportInput {
    return { name: this.name().trim(), config: structuredClone(this.config()), chart: this.chart(), pinned: this.pinned() };
  }

  canSave(): boolean {
    return !!this.name().trim() && this.showPreview() && !this.saving();
  }

  save(): void {
    if (!this.canSave()) return;
    const id = this.id();
    this.saving.set(true);
    this.message.set(null);
    (id === null ? this.reports.createSaved(this.input()) : this.reports.updateSaved(id, this.input())).subscribe({
      next: report => {
        this.saving.set(false);
        this.saved.update(list => (id === null ? [...list, report] : list.map(r => (r.id === report.id ? report : r))));
        if (id === null) void this.router.navigate(['/accounting/reports/custom', report.id], { replaceUrl: true });
      },
      error: error => {
        this.saving.set(false);
        this.message.set(error?.status === 409 ? '已有同名的報表' : writeErrorMessage(error));
      },
    });
  }

  /** A copy under the current name (with （複本） when it is taken), opened in place of this one. */
  saveAs(): void {
    if (!this.canSave()) return;
    const input = this.input();
    if (this.saved().some(r => r.name === input.name)) input.name = `${input.name}（複本）`;
    this.saving.set(true);
    this.message.set(null);
    this.reports.createSaved(input).subscribe({
      next: report => {
        this.saving.set(false);
        this.saved.update(list => [...list, report]);
        void this.router.navigate(['/accounting/reports/custom', report.id]);
      },
      error: error => {
        this.saving.set(false);
        this.message.set(writeErrorMessage(error));
      },
    });
  }

  remove(): void {
    const id = this.id();
    if (id === null) return;
    if (!this.confirmingDelete()) {
      this.confirmingDelete.set(true);
      return;
    }
    this.saving.set(true);
    this.reports.deleteSaved(id).subscribe({
      next: () => {
        this.saving.set(false);
        this.saved.update(list => list.filter(r => r.id !== id));
        void this.router.navigateByUrl('/accounting/reports');
      },
      error: error => {
        this.saving.set(false);
        this.confirmingDelete.set(false);
        this.message.set(writeErrorMessage(error));
      },
    });
  }

  // ---- phone sheet -----------------------------------------------------------------------------------------------

  openSheet(): void {
    this.sheetDraft.set(structuredClone(this.config()));
    this.sheetOpen.set(true);
  }

  closeSheet(): void {
    this.sheetOpen.set(false);
  }

  draftPatch(patch: Partial<ReportConfig>): void {
    const next = { ...this.sheetDraft(), ...patch };
    if (next.split_by === next.group_by) delete next.split_by;
    this.sheetDraft.set(next);
  }

  draftPreset(value: string): void {
    if (value === 'custom') {
      const resolved = resolvePeriod(this.sheetDraft().period, this.today());
      this.draftPatch({ period: { from: resolved.from, to: resolved.to } });
    } else {
      this.draftPatch({ period: { preset: value as PeriodPreset } });
    }
  }

  draftPresetValue(): string {
    return isPreset(this.sheetDraft().period) ? (this.sheetDraft().period as { preset: string }).preset : 'custom';
  }

  draftRemoveFilter(key: keyof ReportFilters): void {
    this.draftPatch({ filters: withoutFilter(this.sheetDraft().filters, key) });
  }

  draftChips() {
    return filterChips(this.sheetDraft().filters, this.refs());
  }

  /** 套用: the draft becomes the conditions on screen (not saved; saving is desktop only). */
  applySheet(): void {
    const draft = this.sheetDraft();
    if (configValid(draft)) {
      this.config.set(draft);
      this.chart.set(fittingChart(draft, this.chart()));
      this.started.set(true);
    }
    this.sheetOpen.set(false);
  }

  onEscape(event: Event): void {
    if (this.sheetOpen()) {
      event.preventDefault();
      this.closeSheet();
    }
  }

  back(): void {
    if (this.router.lastSuccessfulNavigation()?.previousNavigation) this.location.back();
    else void this.router.navigateByUrl('/accounting/reports');
  }

  protected readonly stringify = (value: unknown) => String(value ?? '');
}
