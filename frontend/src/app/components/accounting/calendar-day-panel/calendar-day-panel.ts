import { NgTemplateOutlet } from '@angular/common';
import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { formatSigned } from '../format';
import { OverviewState } from '../month-overview/overview.service';
import { SkeletonComponent } from '../skeleton/skeleton';
import { SplitFolderComponent } from '../split-folder/split-folder';
import { BillLine, DayNet, TimelineDay, TimelineRow, dayLabel } from '../timeline/timeline';

/**
 * The wide calendar's right pane: one day's bills and entries (`mode = 'day'`) or a month search's matches grouped by
 * day (`mode = 'results'`). Presentational — the layout feeds it rows (batches unfolded) and handles every output.
 * `[` / `]` step the day while focus is inside the panel (not in a text field).
 */
@Component({
  selector: 'app-calendar-day-panel',
  standalone: true,
  imports: [NgTemplateOutlet, SkeletonComponent, SplitFolderComponent],
  templateUrl: './calendar-day-panel.html',
  styleUrls: ['../entry-row.scss', './calendar-day-panel.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '(keydown)': 'onKeydown($event)' },
})
export class CalendarDayPanelComponent {
  readonly mode = input<'day' | 'results'>('day');
  /** Day mode: the shown day, `YYYY-MM-DD`. */
  readonly day = input<string | null>(null);
  readonly today = input.required<string>();
  /** Day mode: the day's rows. */
  readonly rows = input<TimelineRow[]>([]);
  readonly net = input<DayNet | null>(null);
  readonly state = input<OverviewState>('ready');
  readonly billLines = input<BillLine[]>([]);
  /** Results mode: the matches grouped by day. */
  readonly days = input<TimelineDay[]>([]);
  /** Results mode: the search text shown in the header. */
  readonly query = input('');
  /** Results mode: currency for each day group's net (an addition to the brief; defaults to TWD). */
  readonly currency = input('TWD');
  /** The open entry's id; its row gets `aria-current`. */
  readonly selectedId = input<number | null>(null);

  /** ‹ › and `[` `]`. */
  readonly step = output<1 | -1>();
  /** 本月摘要 (day mode) / ✕ (results mode). */
  readonly clear = output<void>();
  readonly open = output<TimelineRow>();
  /** ＋ 在這天記一筆, with the day's date. */
  readonly add = output<string>();
  /** A bill line, with its account id. */
  readonly openCard = output<number>();

  protected readonly dayLabel = dayLabel;
  protected readonly formatSigned = formatSigned;

  protected isCurrent(row: TimelineRow): boolean {
    const id = this.selectedId();
    return id !== null && (row.entryId === id || row.memberIds.includes(id));
  }

  protected subText(row: TimelineRow): string {
    return [row.time, row.sub].filter(Boolean).join(' · ');
  }

  protected emptyText(): string {
    const day = this.day();
    return day !== null && day > this.today() ? '還沒到這天' : '這天沒有記錄';
  }

  onKeydown(event: KeyboardEvent): void {
    if (this.mode() !== 'day' || (event.key !== '[' && event.key !== ']')) {
      return;
    }
    if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) {
      return;
    }
    const target = event.target;
    if (target instanceof HTMLElement && (target.closest('input, textarea, select, [contenteditable]:not([contenteditable="false"])') || target.isContentEditable)) {
      return;
    }
    event.preventDefault();
    this.step.emit(event.key === ']' ? 1 : -1);
  }
}
