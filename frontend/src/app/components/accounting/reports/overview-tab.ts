import { ChangeDetectionStrategy, Component, computed, effect, input, signal, untracked } from '@angular/core';
import { RouterLink } from '@angular/router';

import { BreakdownResponse, BreakdownRow } from '../../../models/reports.model';
import { SegToggleComponent, SegToggleOption } from '../../ui/seg-toggle/seg-toggle';
import { Change, change, donutArcs, gross, n0, num, pct1, seriesColor } from './report-math';

type Kind = 'expense' | 'income';

interface CategoryLine {
  key: string;
  label: string;
  color: string;
  count: number;
  /** Gross (amount + refund): the donut and the list add up to the period's spending before refunds. */
  gross: number;
  amountText: string;
  share: string;
}

interface SplitLine {
  label: string;
  count: number;
  amountText: string;
  width: string;
}

interface Stat {
  label: string;
  value: string;
  direction: Change['direction'] | null;
}

/**
 * 總覽: a donut and a category list for 支出 or 收入, the refund line, and the selected category's detail card
 * (subcategories, change against the previous period, average per entry, share, 在記帳記錄看這 N 筆).
 */
@Component({
  selector: 'app-report-overview',
  imports: [RouterLink, SegToggleComponent],
  templateUrl: './overview-tab.html',
  styleUrl: './overview-tab.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ReportOverviewComponent {
  /** Expense by category, split by subcategory, compared with the previous period. */
  readonly expense = input.required<BreakdownResponse>();
  /** Income by category, split by subcategory, compared with the previous period. */
  readonly income = input.required<BreakdownResponse>();
  readonly labels = input.required<{ previous: string; lastYear: string }>();
  /** `YYYY-MM` the timeline opens on. */
  readonly drillMonth = input.required<string>();

  protected readonly kinds: SegToggleOption[] = [
    { value: 'expense', label: '支出' },
    { value: 'income', label: '收入' },
  ];
  readonly kind = signal<Kind>('expense');
  /** The selected row's key; null = the largest row. */
  readonly selectedKey = signal<string | null>(null);

  private readonly response = computed(() => (this.kind() === 'expense' ? this.expense() : this.income()));
  private readonly rows = computed(() => this.response().rows.filter(row => row.key !== null && gross(row) > 0));
  readonly listTotal = computed(() => this.rows().reduce((sum, row) => sum + gross(row), 0));

  readonly lines = computed<CategoryLine[]>(() =>
    this.rows().map((row, index) => ({
      key: row.key!,
      label: row.label,
      color: row.color ?? seriesColor(index),
      count: row.count,
      gross: gross(row),
      amountText: n0(gross(row)),
      share: pct1(gross(row), this.listTotal()),
    })),
  );

  readonly selectedIndex = computed(() => {
    const key = this.selectedKey();
    const index = key === null ? -1 : this.rows().findIndex(row => row.key === key);
    return index === -1 ? (this.rows().length ? 0 : -1) : index;
  });
  readonly selected = computed<BreakdownRow | null>(() => this.rows()[this.selectedIndex()] ?? null);
  readonly selectedLine = computed<CategoryLine | null>(() => this.lines()[this.selectedIndex()] ?? null);

  readonly arcs = computed(() => donutArcs(this.lines().map(line => ({ value: line.gross, color: line.color }))));
  readonly donutLabel = computed(() => (this.kind() === 'expense' ? '支出（未扣退款）' : '收入'));
  readonly donutTotal = computed(() => n0(this.listTotal()));
  readonly donutAria = computed(() => {
    const lines = this.lines();
    const title = `${this.kind() === 'expense' ? '支出' : '收入'}類別占比`;
    return lines.length ? `${title}：${lines.map(line => `${line.label} ${line.share}`).join('、')}` : title;
  });

  /** 退款 −N, naming the categories it came from, when the period has refunds (支出 only). */
  readonly refundLine = computed(() => {
    if (this.kind() !== 'expense') return null;
    const total = num(this.expense().totals.refund);
    if (!(total > 0)) return null;
    const sources = this.expense().rows.filter(row => num(row.refund) > 0).map(row => row.label);
    return { label: sources.length ? `退款（${sources.join('、')}）` : '退款', text: `−${n0(total)}` };
  });

  readonly splits = computed<SplitLine[]>(() => {
    const splits = (this.selected()?.splits ?? []).filter(split => num(split.amount) > 0);
    const max = Math.max(...splits.map(split => num(split.amount)), 0);
    return splits.map(split => ({
      label: split.label,
      count: split.count,
      amountText: n0(split.amount),
      width: `${max > 0 ? ((num(split.amount) / max) * 100).toFixed(1) : 0}%`,
    }));
  });

  readonly stats = computed<Stat[]>(() => {
    const row = this.selected();
    if (!row) return [];
    const previous = row.compare_amount == null ? null : change(num(row.amount), num(row.compare_amount));
    return [
      { label: this.labels().previous, value: previous?.text ?? '—', direction: previous?.direction ?? null },
      { label: '每筆平均', value: n0(row.avg), direction: null },
      { label: this.kind() === 'expense' ? '占支出' : '占收入', value: pct1(gross(row), this.listTotal()), direction: null },
    ];
  });

  /** `/accounting?month=…&category_id=…`: the timeline filtered to the selected category. */
  readonly drillParams = computed(() => {
    const row = this.selected();
    return row ? { month: this.drillMonth(), category_id: row.key } : null;
  });

  constructor() {
    // Another period or kind: the selection follows the rows, so a key that vanished falls back to the largest row.
    effect(() => {
      this.kind();
      untracked(() => this.selectedKey.set(null));
    });
  }

  setKind(value: string): void {
    this.kind.set(value as Kind);
  }

  select(key: string): void {
    this.selectedKey.set(key);
  }
}
