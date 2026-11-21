import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import type { TodayCard } from './calendar-pane.service';

/**
 * The narrow calendar's 今天 bar (shown while no day is selected): today's count, entry names and net in the
 * current month, or 回到今天 in any other month. Presentational — the parent handles `pick`.
 */
@Component({
  selector: 'app-calendar-today-bar',
  standalone: true,
  templateUrl: './calendar-today-bar.html',
  styleUrl: './calendar-today-bar.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class CalendarTodayBarComponent {
  /** Today's card (null while loading or in another month). */
  readonly card = input<TodayCard | null>(null);
  /** True when the shown month is today's month. */
  readonly current = input.required<boolean>();
  /** Today's ISO date (for the disc when the card is null). */
  readonly today = input.required<string>();
  readonly pick = output<void>();

  protected readonly dayOfMonth = computed(() => String(Number((this.card()?.date ?? this.today()).slice(8, 10))));
  protected readonly label = computed(() =>
    this.current() ? `今天 · ${this.card()?.count ?? 0} 筆，${this.card()?.netText ?? ''}` : '回到今天',
  );
}
