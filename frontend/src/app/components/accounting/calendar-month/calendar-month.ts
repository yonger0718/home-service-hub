import { ChangeDetectionStrategy, Component, ElementRef, computed, inject, input, output, signal } from '@angular/core';

import { DailySummaryDay } from '../../../models/accounting.model';
import { BillingEvent } from '../billing/billing-math';
import { addDays, monthRange, todayIso } from '../dates';
import { CalendarCell, buildCalendar, cellAriaLabel, compactNet, heatRatio, maxDailyExpense, wideNet } from './calendar-math';

const WEEKDAY_NAMES = ['日', '一', '二', '三', '四', '五', '六'];
const MAX_DOTS = 3;

interface CalendarCellView extends CalendarCell {
  /** Net of the day (income + expense), formatted; null when nothing is shown. */
  net: string | null;
  /** 0..1 share of the month's heaviest spend. */
  heat: number;
  dots: string[];
  more: number;
  bill: 'due' | 'paid' | null;
  invoices: number;
  future: boolean;
  weekend: boolean;
  label: string;
  tabIndex: 0 | -1;
  /** Cards whose payment falls due on this day. */
  dueCount: number;
  /** A card's statement closes on this day (top border). */
  closing: boolean;
}

/** Month grid for the timeline's 日曆 view: per-day spend / income from `GET /entries/summary/daily`. */
@Component({
  selector: 'app-calendar-month',
  standalone: true,
  templateUrl: './calendar-month.html',
  styleUrl: './calendar-month.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class CalendarMonthComponent {
  /** `YYYY-MM`. */
  readonly month = input.required<string>();
  readonly days = input<DailySummaryDay[]>([]);
  readonly loading = input(false);
  /** The daily summary could not be read: no quiet-day dash, since an empty `days` then means unknown, not quiet. */
  readonly failed = input(false);
  readonly currency = input('TWD');
  /** `Preference.week_start`: 0 = Sunday first, 1 = Monday first. */
  readonly weekStart = input(0);
  readonly selected = input<string | null>(null);
  /** Credit-card closing / due days of the month (`billingEvents`). */
  readonly billing = input<BillingEvent[]>([]);
  readonly today = input(todayIso());
  /** Date → category colours, largest first (the cell shows three and `+N`). */
  readonly dots = input<ReadonlyMap<string, readonly string[]>>(new Map());
  readonly invoiceDays = input<ReadonlyMap<string, number>>(new Map());
  /** Due days whose every due event's statement is fully paid. */
  readonly paidDays = input<ReadonlySet<string>>(new Set());
  /** Narrow figures and dot-style marks (< 1024 px). */
  readonly compact = input(false);
  /** Week mode: only the row containing this date renders, and no dots. */
  readonly weekOf = input<string | null>(null);
  readonly daySelected = output<string>();

  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly focusDate = signal<string | null>(null);

  readonly weekdays = computed(() => Array.from({ length: 7 }, (_, i) => WEEKDAY_NAMES[(this.weekStart() + i) % 7]));

  /** The 6-row grid, cut to the week of `weekOf` in week mode. */
  private readonly grid = computed(() => {
    const grid = buildCalendar(this.month(), this.days(), this.weekStart());
    const weekOf = this.weekOf();
    return weekOf ? grid.filter(row => row.some(cell => cell.date === weekOf)) : grid;
  });

  /** The one tab stop: the first of focus / selection / today / the 1st that is a rendered, enabled cell. */
  private readonly tabDate = computed(() => {
    const month = this.month();
    const today = this.today();
    const cells = this.grid().flat().filter(cell => !cell.outside);
    const candidates = [this.focusDate(), this.selected(), today, `${month}-01`];
    for (const date of candidates) {
      if (date && cells.some(cell => cell.date === date)) {
        return date;
      }
    }
    return cells[0]?.date ?? null;
  });

  readonly rows = computed(() => {
    const currency = this.currency();
    const compact = this.compact();
    const today = this.today();
    const weekOf = this.weekOf();
    const tabDate = this.tabDate();
    const unknown = this.loading() || this.failed();
    // Future days show no figure, so their spend must not scale the month's heat.
    const maxExpense = maxDailyExpense(this.days().filter(day => day.date <= today));
    const dueCounts = new Map<string, number>();
    const closingDays = new Set<string>();
    for (const event of this.billing()) {
      if (event.kind === 'due') {
        dueCounts.set(event.date, (dueCounts.get(event.date) ?? 0) + 1);
      } else {
        closingDays.add(event.date);
      }
    }
    return this.grid().map(row =>
      row.map((cell): CalendarCellView => {
        const future = cell.date > today;
        const quiet = cell.count === 0;
        const net = cell.income + cell.expense;
        let netText: string | null = null;
        if (!cell.outside && !future) {
          netText = quiet ? (compact || unknown ? null : '—') : compact ? compactNet(net) : wideNet(net);
        }
        const dueCount = cell.outside ? 0 : (dueCounts.get(cell.date) ?? 0);
        const bill = dueCount > 0 ? (this.paidDays().has(cell.date) ? 'paid' : 'due') : null;
        const invoices = cell.outside ? 0 : (this.invoiceDays().get(cell.date) ?? 0);
        const colours = cell.outside || future || weekOf ? [] : (this.dots().get(cell.date) ?? []);
        const weekday = new Date(Date.UTC(Number(cell.date.slice(0, 4)), Number(cell.date.slice(5, 7)) - 1, cell.day)).getUTCDay();
        return {
          ...cell,
          net: netText,
          heat: cell.outside || future ? 0 : heatRatio(cell.expense, maxExpense),
          dots: colours.slice(0, MAX_DOTS),
          more: Math.max(0, colours.length - MAX_DOTS),
          bill,
          invoices,
          future,
          weekend: weekday === 0 || weekday === 6,
          label: cellAriaLabel({ date: cell.date, count: cell.count, expense: cell.expense, income: cell.income, currency, bill, dueCount, invoices }),
          tabIndex: cell.date === tabDate ? 0 : -1,
          dueCount,
          closing: !cell.outside && closingDays.has(cell.date),
        };
      }),
    );
  });

  onGridKeydown(event: KeyboardEvent): void {
    if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) {
      return;
    }
    const month = this.month();
    const current = this.tabDate();
    if (!current) {
      return;
    }
    let target: string;
    switch (event.key) {
      case 'ArrowLeft':
        target = addDays(current, -1);
        break;
      case 'ArrowRight':
        target = addDays(current, 1);
        break;
      case 'ArrowUp':
        target = addDays(current, -7);
        break;
      case 'ArrowDown':
        target = addDays(current, 7);
        break;
      case 'Home':
        target = `${month}-01`;
        break;
      case 'End':
        target = monthRange(month).to;
        break;
      default:
        return;
    }
    const button = this.host.nativeElement.querySelector<HTMLButtonElement>(`button.cell[data-date="${target}"]`);
    // Handled keys are always cancelled, even when the move is refused, so the layout's ↑/↓ row stepping stays out.
    event.preventDefault();
    if (!button || button.disabled) {
      return;
    }
    this.focusDate.set(target);
    button.focus();
  }

  pick(cell: CalendarCell): void {
    if (!cell.outside) {
      this.focusDate.set(cell.date);
      this.daySelected.emit(cell.date);
    }
  }
}
