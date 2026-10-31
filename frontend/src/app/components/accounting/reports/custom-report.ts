import { Location, NgTemplateOutlet } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, effect, inject, signal, untracked } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { forkJoin } from 'rxjs';

import { CategoryNode, LedgerAccount, Project } from '../../../models/accounting.model';
import {
  BreakdownResponse,
  BreakdownRow,
  BreakdownTotals,
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
import { todayIso } from '../dates';
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
import { Change, change, donutArcs, n0, niceCeiling, num, pct1, seriesColor, tickLabel, yOf } from './report-math';
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

const DESKTOP: ChartLayout = { width: 760, height: 280, plot: { left: 52, width: 700, top: 30, height: 210 }, labelY: 270, ticks: true };
const PHONE: ChartLayout = { width: 358, height: 210, plot: { left: 0, width: 358, top: 20, height: 166 }, labelY: 204, ticks: false };
const MAX_SERIES = 7;

/** The metric's figure in a response's `totals` (`count` → the entry count). */
function totalOf(totals: Partial<BreakdownTotals> | undefined, metric: ReportMetric): number {
  return metric === 'count' ? num(totals?.count) : num(totals?.[metric]);
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
}

interface XLabel {
  x: string;
  label: string;
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
  styleUrl: './custom-report.scss',
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

  readonly preview = signal<BreakdownResponse | null>(null);
  readonly previewState = signal<LoadState>('loading');
  readonly showPreview = computed(() => this.started() && configValid(this.config()));
  readonly layout = computed<ChartLayout>(() => (this.phone() ? PHONE : DESKTOP));
  protected readonly viewBox = computed(() => `0 0 ${this.layout().width} ${this.layout().height}`);

  private readonly rows = computed<BreakdownRow[]>(() => this.preview()?.rows ?? []);
  /** The rows added up (the table's 合計 row and the donut shares); above the period when tag rows overlap. */
  private readonly metricTotal = computed(() => this.rows().reduce((sum, row) => sum + num(row.amount), 0));
  private readonly rowCount = computed(() => this.rows().reduce((sum, row) => sum + row.count, 0));
  private readonly compareRowTotal = computed(() => (this.config().compare ? this.rows().reduce((sum, row) => sum + num(row.compare_amount), 0) : null));
  /** The period's own figures from `totals` / `compare.totals` (each entry once, whatever the grouping). */
  private readonly periodTotal = computed(() => totalOf(this.preview()?.totals, this.config().metric));
  private readonly count = computed(() => this.preview()?.totals.count ?? 0);
  private readonly compareTotal = computed(() => {
    const compare = this.preview()?.compare;
    return this.config().compare && compare ? totalOf(compare.totals, this.config().metric) : null;
  });
  /** `group_by` / `split_by` 標籤: an entry counts once per tag, so the rows add up to more than the period. */
  readonly overlapping = computed(() => !!this.preview()?.overlapping);
  readonly groupLabel = computed(() => DIMENSION_LABELS[this.config().group_by]);
  readonly compareLabel = computed(() => (this.config().compare ? COMPARE_LABELS[this.config().compare!] : ''));

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

  protected rowLabel(row: BreakdownRow): string {
    if (row.key === null) return `（無${DIMENSION_LABELS[this.config().group_by]}）`;
    if (row.key === '__other__') return '其他';
    return row.label;
  }

  readonly figures = computed<Figure[]>(() => {
    const rows = this.rows();
    if (!rows.length) return [];
    const total = this.periodTotal();
    const count = this.count();
    const compareTotal = this.compareTotal();
    const money = (value: number) => (this.config().metric === 'count' ? n0(value) : `$${n0(value)}`);
    const max = rows.reduce((best, row) => (num(row.amount) > num(best.amount) ? row : best), rows[0]);
    const byMonth = this.config().group_by === 'month';
    const diff = compareTotal !== null ? change(total, compareTotal, true) : null;
    return [
      {
        label: '合計',
        value: money(total),
        foot: diff ? `${diff.text} 比${this.compareLabel()}` : `${count} 筆`,
        direction: diff?.direction ?? null,
      },
      { label: `平均每${byMonth ? '月' : '組'}`, value: money(total / rows.length), foot: `${rows.length} 組`, direction: null },
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
    if (this.config().compare) legend.push({ label: this.compareLabel(), color: 'transparent', outline: true });
    return legend;
  });

  private readonly max = computed(() => {
    const rows = this.rows();
    const values = rows.map(row => num(row.amount));
    if (this.config().compare) values.push(...rows.map(row => num(row.compare_amount)));
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
    const compare = !!this.config().compare;
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
        if (height > 0) rects.push({ x: x.toFixed(1), y: y.toFixed(1), width: width.toFixed(1), height: Math.max(height - 1, 0).toFixed(1), fill: seriesColor(k), outline: false });
      });
      if (compare) {
        const value = num(row.compare_amount);
        const top = yOf(value, plot, max);
        rects.push({ x: (cx + 3).toFixed(1), y: top.toFixed(1), width: width.toFixed(1), height: (bottom - top).toFixed(1), fill: 'transparent', outline: true });
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
      const y = yOf(Math.max(total, this.config().compare ? num(row.compare_amount) : 0), plot, this.max());
      return { x: (plot.left + slot * index + slot / 2).toFixed(1), label: this.rowLabel(row), total: n0(total), totalY: (y - 8).toFixed(1), pointY: yOf(total, plot, this.max()).toFixed(1) };
    });
  });
  protected readonly labelY = computed(() => this.layout().labelY);
  readonly linePoints = computed(() => this.xLabels().map(x => `${x.x},${x.pointY}`).join(' '));
  readonly comparePoints = computed(() => {
    if (!this.config().compare) return '';
    const { plot } = this.layout();
    return this.rows().map((row, index) => `${this.xLabels()[index].x},${yOf(num(row.compare_amount), plot, this.max()).toFixed(1)}`).join(' ');
  });
  readonly arcs = computed(() => donutArcs(this.rows().map((row, index) => ({ value: num(row.amount), color: seriesColor(index) }))));
  readonly chartAria = computed(() => `${this.name() || '報表'}：${this.rows().map(row => `${this.rowLabel(row)} ${n0(row.amount)}`).join('、')}`);

  readonly columns = computed<{ label: string; kind: ColumnKind }[]>(() => [
    ...this.seriesKeys().map(label => ({ label, kind: 'series' as const })),
    { label: '合計', kind: 'total' },
    { label: '筆數', kind: 'count' },
    ...(this.config().compare ? [{ label: this.compareLabel(), kind: 'base' as const }, { label: '增減', kind: 'change' as const }] : []),
  ]);
  readonly tableRows = computed<TableRow[]>(() => {
    const rows = this.rows();
    if (!rows.length) return [];
    const keys = this.seriesKeys();
    const compare = !!this.config().compare;
    const cells = (values: number[], total: number, count: number, base: number | null): TableCell[] => {
      const list: TableCell[] = [
        ...values.map(value => ({ text: n0(value), kind: 'series' as const })),
        { text: n0(total), kind: 'total' },
        { text: String(count), kind: 'count', muted: true },
      ];
      if (compare) {
        const diff = change(total, base ?? 0, false);
        list.push(
          { text: n0(base ?? 0), kind: 'base', muted: true },
          base === 0 && total > 0 ? { text: '新增', kind: 'change', direction: 'more' } : { text: diff.text, kind: 'change', direction: diff.direction },
        );
      }
      return list;
    };
    const body = rows.map(row => ({ label: this.rowLabel(row), total: false, cells: cells(this.seriesValues(row), num(row.amount), row.count, compare ? num(row.compare_amount) : null) }));
    const seriesTotals = keys.map((_, k) => rows.reduce((sum, row) => sum + this.seriesValues(row)[k], 0));
    return [...body, { label: '合計', total: true, cells: cells(seriesTotals, this.metricTotal(), this.rowCount(), this.compareRowTotal()) }];
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
      untracked(() => (show ? this.loadPreview(config, today) : this.preview.set(null)));
    });
  }

  private loadPreview(config: ReportConfig, today: string): void {
    const id = ++this.previewRequestId;
    this.previewState.set('loading');
    this.reports.getBreakdown(configQuery(config, today)).subscribe({
      next: preview => {
        if (id !== this.previewRequestId) return;
        this.preview.set(preview);
        this.previewState.set('ready');
      },
      error: () => {
        if (id !== this.previewRequestId) return;
        this.preview.set(null);
        this.previewState.set('error');
      },
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
