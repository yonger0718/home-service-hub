import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { Params, RouterLink } from '@angular/router';

import { BreakdownResponse, BreakdownRow, ReportDimension, TrendResponse } from '../../../models/reports.model';
import { SegToggleComponent, SegToggleOption } from '../../ui/seg-toggle/seg-toggle';
import type { LoadState } from './reports';
import { ReportPeriod, barGeometry, bucketLabel, n0, num, pct1, periodLabel } from './report-math';

/** The four ranking dimensions (spec: 商家, 標籤, 對象, 帳戶). */
export type RankDimension = Extract<ReportDimension, 'merchant' | 'tag' | 'counterparty' | 'account'>;

export const RANK_DIMENSIONS: readonly RankDimension[] = ['merchant', 'tag', 'counterparty', 'account'];

interface DimensionCopy {
  label: string;
  /** The row of entries without the dimension. */
  none: string;
  /** `共 N 家` … */
  count: (n: number) => string;
}

export const RANK_COPY: Record<RankDimension, DimensionCopy> = {
  merchant: { label: '商家', none: '（無商家）', count: n => `共 ${n} 家` },
  tag: { label: '標籤', none: '（無標籤）', count: n => `共 ${n} 個標籤` },
  counterparty: { label: '對象', none: '（無對象）', count: n => `共 ${n} 位對象` },
  account: { label: '帳戶', none: '（無帳戶）', count: n => `${n} 個帳戶` },
};

export const OTHER_KEY = '__other__';

interface RankLine {
  key: string | null;
  label: string;
  /** Entries without the dimension, or the fold of the rest: drawn muted, no six-month bars. */
  muted: boolean;
  count: number;
  avgText: string;
  amountText: string;
  width: string;
}

interface Stat {
  label: string;
  value: string;
}

/** The six-month drawing (ReportsDesktop 明細統計): viewBox 300 × 90. */
const SPARK = { width: 300, height: 90, plot: { left: 4, width: 292, top: 6, height: 66 }, labelY: 88 };

/** The panel statistics the API computes on spend rows only (refunds left out). */
const SPEND_ONLY = new Set(['中位數', '最大', '最小', '平均間隔']);

/**
 * 排行: spend ranked by 商家 / 標籤 / 對象 / 帳戶 with a bar, count and average per row; rows without the dimension
 * muted; a note on overlapping tag totals. The selected row's panel shows its total, count, average, median, max,
 * min, average interval, share, and six monthly bars.
 */
@Component({
  selector: 'app-report-rank',
  imports: [RouterLink, SegToggleComponent],
  templateUrl: './rank-tab.html',
  styleUrl: './rank-tab.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ReportRankComponent {
  readonly rank = input<BreakdownResponse | null>(null);
  readonly rankState = input<LoadState>('loading');
  readonly dimension = input.required<RankDimension>();
  readonly period = input.required<ReportPeriod>();
  /** The selected row's key (`null` is the （無…）row); undefined = nothing selected yet. */
  readonly selectedKey = input<string | null | undefined>(undefined);
  /** The selected row's six monthly buckets. */
  readonly spark = input<TrendResponse | null>(null);
  readonly sparkState = input<LoadState>('loading');
  /** `YYYY-MM` the timeline opens on. */
  readonly drillMonth = input.required<string>();

  readonly dimensionChange = output<RankDimension>();
  readonly select = output<string | null>();

  protected readonly options: SegToggleOption[] = RANK_DIMENSIONS.map(id => ({ value: id, label: RANK_COPY[id].label }));
  protected readonly copy = computed(() => RANK_COPY[this.dimension()]);
  protected readonly sparkWidth = SPARK.width;
  protected readonly sparkHeight = SPARK.height;
  protected readonly sparkLabelY = SPARK.labelY;

  private readonly rows = computed(() => (this.rank()?.rows ?? []).filter(row => num(row.amount) > 0 || num(row.refund) > 0));

  readonly lines = computed<RankLine[]>(() => {
    const max = Math.max(...this.rows().map(row => num(row.amount)), 0);
    return this.rows().map(row => ({
      key: row.key,
      label: this.labelOf(row),
      muted: row.key === null || row.key === OTHER_KEY,
      count: row.count,
      avgText: n0(row.avg),
      amountText: n0(row.amount),
      width: `${max > 0 ? ((num(row.amount) / max) * 100).toFixed(1) : 0}%`,
    }));
  });

  /** `共 31 家 · 顯示前 200` with the tag note when totals overlap. */
  readonly foot = computed(() => {
    const rank = this.rank();
    if (!rank) return '';
    const named = this.rows().filter(row => row.key !== null && row.key !== OTHER_KEY).length;
    const parts = [this.copy().count(named)];
    if (this.rows().some(row => row.key === OTHER_KEY)) parts.push('顯示前 200');
    if (rank.overlapping) parts.push('一筆可有多個標籤，合計會大於支出');
    return parts.join(' · ');
  });

  readonly selected = computed<BreakdownRow | null>(() => {
    const key = this.selectedKey();
    return key === undefined ? null : (this.rows().find(row => row.key === key) ?? null);
  });
  readonly selectedLabel = computed(() => (this.selected() ? this.labelOf(this.selected()!) : ''));
  readonly selectedMuted = computed(() => this.selected()?.key === null || this.selected()?.key === OTHER_KEY);
  readonly panelTitle = computed(() => `${this.copy().label} · ${periodLabel(this.period())}`);

  readonly stats = computed<Stat[]>(() => {
    const row = this.selected();
    if (!row) return [];
    const total = num(this.rank()?.totals.expense);
    const stats: Stat[] = [
      { label: '合計', value: n0(row.amount) },
      { label: '筆數', value: String(row.count) },
      { label: '平均', value: n0(row.avg) },
    ];
    if (!this.selectedMuted()) {
      if (row.median !== null) stats.push({ label: '中位數', value: n0(row.median) });
      if (row.max !== null) stats.push({ label: '最大', value: n0(row.max) });
      if (row.min !== null) stats.push({ label: '最小', value: n0(row.min) });
      if (row.avg_interval_days !== null) stats.push({ label: '平均間隔', value: `${row.avg_interval_days} 天` });
    }
    stats.push({ label: '占支出', value: pct1(num(row.amount), total) });
    const average = this.sparkAverage();
    if (average !== null) stats.push({ label: `近 ${this.sparkValues().length} 月月均`, value: n0(average) });
    return stats;
  });

  /** The API computes 中位數 / 最大 / 最小 / 平均間隔 on spend rows only; 筆數 and 平均 keep the refunds. */
  readonly statsNote = computed(() =>
    this.stats().some(stat => SPEND_ONLY.has(stat.label)) ? '中位數、最大、最小、平均間隔只算支出（退款不計入統計）；筆數、平均含退款' : null,
  );

  private readonly sparkValues = computed(() => (this.spark()?.buckets ?? []).map(bucket => num(Object.values(bucket.values)[0])));
  private readonly sparkAverage = computed(() => {
    const spark = this.spark();
    if (!spark || this.selectedMuted()) return null;
    const value = Object.values(spark.average)[0];
    return value === undefined ? null : num(value);
  });
  readonly sparkBars = computed(() => {
    const buckets = this.spark()?.buckets ?? [];
    const values = this.sparkValues();
    const geometry = barGeometry(values, SPARK.plot, Math.max(...values, 0));
    return geometry.map((bar, index) => ({ ...bar, label: bucketLabel(buckets[index].start), last: index === geometry.length - 1 }));
  });
  readonly sparkAria = computed(() => {
    const buckets = this.spark()?.buckets ?? [];
    const values = this.sparkValues();
    return `${this.selectedLabel()} 近 ${buckets.length} 個月支出：${buckets.map((bucket, index) => `${bucketLabel(bucket.start)} ${n0(values[index])}`).join('、')}`;
  });

  /** The timeline filtered to the selected row; none for 對象 (the timeline has no such filter) or the muted rows. */
  readonly drillParams = computed<Params | null>(() => {
    const row = this.selected();
    if (!row || this.selectedMuted()) return null;
    const params: Params = { month: this.drillMonth() };
    switch (this.dimension()) {
      case 'merchant': params['merchant'] = row.label; break;
      case 'tag': params['tag'] = row.label; break;
      case 'account': params['account_id'] = row.key; break;
      default: return null;
    }
    return params;
  });

  protected labelOf(row: BreakdownRow): string {
    if (row.key === null) return this.copy().none;
    if (row.key === OTHER_KEY) return '其他';
    return row.label;
  }

  setDimension(value: string): void {
    this.dimensionChange.emit(value as RankDimension);
  }

  pick(key: string | null): void {
    this.select.emit(key);
  }
}
