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

  private readonly tabDate = computed(() => {
    const month = this.month();
    const today = this.today();
    const focus = this.focusDate();
    return (focus?.slice(0, 7) === month ? focus : null) ?? this.selected() ?? (today.slice(0, 7) === month ? today : `${month}-01`);
  });

  readonly rows = computed(() => {
    const currency = this.currency();
    const compact = this.compact();
    const today = this.today();
    const weekOf = this.weekOf();
    const tabDate = this.tabDate();
    const maxExpense = maxDailyExpense(this.days());
    const dueCounts = new Map<string, number>();
    const closingDays = new Set<string>();
    for (const event of this.billing()) {
      if (event.kind === 'due') {
        dueCounts.set(event.date, (dueCounts.get(event.date) ?? 0) + 1);
      } else {
        closingDays.add(event.date);
      }
    }
    let grid = buildCalendar(this.month(), this.days(), this.weekStart());
    if (weekOf) {
      grid = grid.filter(row => row.some(cell => cell.date === weekOf));
    }
    return grid.map(row =>
      row.map((cell): CalendarCellView => {
        const future = cell.date > today;
        const quiet = cell.count === 0;
        const net = cell.income + cell.expense;
        let netText: string | null = null;
        if (!cell.outside && !future) {
          netText = quiet ? (compact ? null : '—') : compact ? compactNet(net) : wideNet(net);
        }
        const dueCount = cell.outside ? 0 : (dueCounts.get(cell.date) ?? 0);
        const bill = dueCount > 0 ? (this.paidDays().has(cell.date) ? 'paid' : 'due') : null;
        const invoices = cell.outside ? 0 : (this.invoiceDays().get(cell.date) ?? 0);
        const colours = cell.outside || weekOf ? [] : (this.dots().get(cell.date) ?? []);
        const weekday = new Date(Date.UTC(Number(cell.date.slice(0, 4)), Number(cell.date.slice(5, 7)) - 1, cell.day)).getUTCDay();
        return {
          ...cell,
          net: netText,
          heat: cell.outside ? 0 : heatRatio(cell.expense, maxExpense),
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
    const month = this.month();
    const current = this.tabDate();
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
    event.preventDefault();
    if (target.slice(0, 7) !== month) {
      return;
    }
    const button = this.host.nativeElement.querySelector<HTMLButtonElement>(`button.cell[data-date="${target}"]`);
    if (button) {
      this.focusDate.set(target);
      button.focus();
    }
  }

  pick(cell: CalendarCell): void {
    if (!cell.outside) {
      this.daySelected.emit(cell.date);
    }
  }
}
