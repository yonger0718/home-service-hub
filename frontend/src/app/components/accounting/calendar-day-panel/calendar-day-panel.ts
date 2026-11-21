import { NgTemplateOutlet } from '@angular/common';
import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { formatSigned } from '../format';
import { OverviewState } from '../month-overview/overview.service';
import { SkeletonComponent } from '../skeleton/skeleton';
import { SplitFolderComponent } from '../split-folder/split-folder';
import { dayLabel } from '../dates';
import type { BillLine, DayNet, TimelineDay, TimelineRow } from '../timeline/timeline';

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
  host: { '(keydown)': 'onKeydown($event)', '[class.dp--narrow]': 'narrow()' },
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
  /** Results mode: currency for each day group's net (the main currency; defaults to TWD). */
  readonly currency = input('TWD');
  /** Results mode: the month has more matches than loaded (`載入更多`). */
  readonly hasMore = input(false);
  /** Day mode: a search or list filter is active, so an empty day says 這天沒有符合條件的記錄. */
  readonly filtered = input(false);
  /** Narrow calendar (under the week strip): no 本月摘要, a swipe hint, 今天 on the label, the net toned by sign. */
  readonly narrow = input(false);
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
  /** 重試 after 記錄讀取失敗: the day's rows (day mode) or the month's matches (results mode). */
  readonly retry = output<void>();
  /** Results mode: 載入更多. */
  readonly loadMore = output<void>();

  protected readonly dayLabel = dayLabel;
  protected readonly formatSigned = formatSigned;

  protected isCurrent(row: TimelineRow): boolean {
    const id = this.selectedId();
    return id !== null && (row.entryId === id || row.memberIds.includes(id));
  }

  protected label(day: string): string {
    return this.narrow() && day === this.today() ? `${dayLabel(day)} · 今天` : dayLabel(day);
  }

  protected subText(row: TimelineRow): string {
    return [row.time, row.sub].filter(Boolean).join(' · ');
  }

  protected emptyText(): string {
    const day = this.day();
    if (day !== null && day > this.today()) {
      return '還沒到這天';
    }
    return this.filtered() ? '這天沒有符合條件的記錄' : '這天沒有記錄';
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
