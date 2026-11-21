import { ChangeDetectionStrategy, Component, DestroyRef, computed, effect, inject, input, output, signal, untracked } from '@angular/core';
import { RouterLink } from '@angular/router';
import { Subscription } from 'rxjs';

import { BreakdownResponse, BreakdownRow } from '../../../models/reports.model';
import { ReportsService } from '../../../services/reports.service';
import { SegToggleComponent, SegToggleOption } from '../../ui/seg-toggle/seg-toggle';
import { Change, ReportPeriod, afterTodayLine, baseChange, donutArcs, drillText, gross, isRunning, n0, num, pct1, runningCompareLabels, seriesColor, windowEmpty } from './report-math';

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
  /** 新增, — 停止 and 上期無資料 read in the muted text colour, never in the spend colours. */
  muted: boolean;
}

interface MerchantLine {
  label: string;
  count: number;
  amountText: string;
}

/** The merchants card shows this many named merchants, plus the entries without one. */
const MERCHANT_LIMIT = 5;

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
  /** Expense by category, split by subcategory, compared with the previous period (to date while the period runs). */
  readonly expense = input.required<BreakdownResponse>();
  /** Income by category, split by subcategory, compared with the previous period (to date while the period runs). */
  readonly income = input.required<BreakdownResponse>();
  /**
   * Expense by category against the same dates last year. A running period whose comparisons coincide (`single`, a
   * running year) reads its one 去年同期 comparison from here: the previous response covers another window then.
   */
  readonly expenseLastYear = input<BreakdownResponse | null>(null);
  /** While a period runs: the same over the whole period, no comparison. Shown when `whole` is on, and counted for the 「另有」 line. */
  readonly expenseWhole = input<BreakdownResponse | null>(null);
  readonly incomeWhole = input<BreakdownResponse | null>(null);
  /** The whole-period view: figures come from the whole responses, and the change stat is left out. */
  readonly whole = input(false);
  readonly today = input.required<string>();
  /** The range the figures cover, for the merchant read. */
  readonly range = input.required<{ from: string; to: string }>();
  readonly toggleWhole = output<void>();
  readonly labels = input.required<{ previous: string; lastYear: string }>();
  /** `YYYY-MM` the timeline opens on. */
  readonly drillMonth = input.required<string>();
  /** The report period: the link promises N entries only for a 月 period. */
  readonly period = input.required<ReportPeriod>();

  protected readonly kinds: SegToggleOption[] = [
    { value: 'expense', label: '支出' },
    { value: 'income', label: '收入' },
  ];
  readonly kind = signal<Kind>('expense');
  /** The selected row's key; null = the largest row. */
  readonly selectedKey = signal<string | null>(null);

  private readonly reports = inject(ReportsService);

  /** The to-date (or finished-period) response of the shown kind: the comparison and the 「另有」 count read it. */
  private readonly cutResponse = computed(() => (this.kind() === 'expense' ? this.expense() : this.income()));
  private readonly wholeResponse = computed(() => (this.kind() === 'expense' ? this.expenseWhole() : this.incomeWhole()));
  private readonly response = computed(() => (this.whole() ? (this.wholeResponse() ?? this.cutResponse()) : this.cutResponse()));
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
    const expense = this.response();
    const total = num(expense.totals.refund);
    if (!(total > 0)) return null;
    const sources = expense.rows.filter(row => num(row.refund) > 0).map(row => row.label);
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
    const stats: Stat[] = [];
    const running = isRunning(this.period(), this.today());
    const labels = running ? runningCompareLabels(this.period(), this.today()) : null;
    // F1: a running `single` period compares with last year's same dates; 收入 has no last-year read, so no stat.
    const single = !!labels?.single;
    if (!this.whole() && !(single && this.kind() === 'income')) {
      const source = single ? this.expenseLastYear() : this.response();
      const base = single ? source?.rows.find(r => r.key === row.key)?.compare_amount : row.compare_amount;
      const previous = baseChange(num(row.amount), num(base), windowEmpty(source), single ? '去年無資料' : '上期無資料');
      const noBase = base == null;
      stats.push({
        label: labels ? labels.previous : this.labels().previous,
        value: noBase ? '—' : previous.text,
        direction: noBase || previous.kind !== 'delta' ? null : previous.direction,
        muted: !noBase && previous.kind !== 'delta',
      });
    }
    stats.push(
      { label: '每筆平均', value: n0(row.avg), direction: null, muted: false },
      { label: this.kind() === 'expense' ? '占支出' : '占收入', value: pct1(gross(row), this.listTotal()), direction: null, muted: false },
    );
    return stats;
  });

  /** R3 for the shown kind; 改回截至 whenever the whole view is on. */
  readonly afterToday = computed(() => afterTodayLine(this.cutResponse(), this.wholeResponse(), this.kind(), this.today(), this.whole()));

  /** D5: a category whose only split is itself has nothing to list, so the card shows its 常去商家 instead. */
  readonly merchantMode = computed(() => {
    const row = this.selected();
    return !!row && row.splits?.length === 1 && row.splits[0].label === row.label;
  });
  /** null while loading, when the read failed, or when the card is not in merchant mode. */
  readonly merchants = signal<MerchantLine[] | null>(null);
  private readonly merchantCache = new Map<string, MerchantLine[]>();
  private merchantRead: Subscription | null = null;

  /** `/accounting?month=…&category_id=…`: the timeline filtered to the selected category. */
  readonly drillParams = computed(() => {
    const row = this.selected();
    return row ? { month: this.drillMonth(), category_id: row.key } : null;
  });

  readonly drillLabel = computed(() => drillText(this.period(), this.selected()?.count ?? 0));

  constructor() {
    // Another period or kind: the selection follows the rows, so a key that vanished falls back to the largest row.
    effect(() => {
      this.kind();
      untracked(() => this.selectedKey.set(null));
    });
    // One merchant read per period range, kind and category, kept for as long as the tab lives.
    effect(() => {
      const row = this.selected();
      const { from, to } = this.range();
      const kind = this.kind();
      const target = this.merchantMode() && row?.key ? row.key : null;
      untracked(() => this.loadMerchants(from, to, kind, target));
    });
    inject(DestroyRef).onDestroy(() => this.merchantRead?.unsubscribe());
  }

  private loadMerchants(from: string, to: string, kind: Kind, key: string | null): void {
    this.merchantRead?.unsubscribe();
    this.merchantRead = null;
    if (key === null) {
      this.merchants.set(null);
      return;
    }
    const id = `${from}|${to}|${kind}|${key}`;
    const cached = this.merchantCache.get(id);
    if (cached) {
      this.merchants.set(cached);
      return;
    }
    this.merchants.set(null);
    this.merchantRead = this.reports
      .getBreakdown({ date_from: from, date_to: to, metric: kind, group_by: 'merchant', category_id: [Number(key)] })
      .subscribe({
        next: response => {
          const named = response.rows.filter(row => row.key !== null && row.key !== '__other__').slice(0, MERCHANT_LIMIT);
          const none = response.rows.find(row => row.key === null);
          const lines = (none ? [...named, { ...none, label: '（無商家）' }] : named).map(row => ({ label: row.label, count: row.count, amountText: n0(gross(row)) }));
          this.merchantCache.set(id, lines);
          this.merchants.set(lines);
        },
        error: () => this.merchants.set(null),
      });
  }

  setKind(value: string): void {
    this.kind.set(value as Kind);
  }

  select(key: string): void {
    this.selectedKey.set(key);
  }
}
