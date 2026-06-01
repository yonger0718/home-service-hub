import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { SideTagComponent, SideTagVariant } from '../side-tag/side-tag';

export type TimelineAmountVariant = 'neutral' | 'positive' | 'negative' | 'buy' | 'sell' | 'dividend' | 'income' | 'expense';

export interface TimelineRow {
  date: string | Date;
  side: SideTagVariant;
  sideLabel?: string;
  primary: string;
  meta?: string;
  amount?: string;
  amountVariant?: TimelineAmountVariant;
}

interface TimelineGroup {
  key: string;
  day: string;
  month: string;
  rows: TimelineRow[];
}

@Component({
  selector: 'app-timeline',
  imports: [SideTagComponent],
  template: `
    <div class="timeline">
      @for (group of groups(); track group.key) {
        @for (row of group.rows; track row.primary + row.amount + $index) {
          <div class="tl-item">
            @if ($first) {
              <div class="tl-date">
                <span class="d">{{ group.day }}</span>
                <span class="m">{{ group.month }}</span>
              </div>
            } @else {
              <div class="tl-date" aria-hidden="true"></div>
            }

            <article class="tl-card">
              <div class="tl-lhs">
                <app-side-tag [variant]="row.side" [label]="row.sideLabel || ''"></app-side-tag>
                <div>
                  <div class="tl-name">{{ row.primary }}</div>
                  @if (row.meta) {
                    <div class="tl-meta">{{ row.meta }}</div>
                  }
                </div>
              </div>
              @if (row.amount) {
                <div class="tl-amt {{ amountClass(row) }}">{{ row.amount }}</div>
              }
            </article>
          </div>
        }
      }
    </div>
  `,
  styleUrl: './timeline.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class TimelineComponent {
  readonly rows = input<TimelineRow[]>([]);

  protected readonly groups = computed<TimelineGroup[]>(() => {
    const groups = new Map<string, TimelineGroup>();

    for (const row of this.rows()) {
      const date = row.date instanceof Date ? row.date : new Date(row.date);
      const key = Number.isNaN(date.getTime()) ? String(row.date) : date.toISOString().slice(0, 10);
      const group = groups.get(key) ?? {
        key,
        day: Number.isNaN(date.getTime()) ? key : date.getDate().toString().padStart(2, '0'),
        month: Number.isNaN(date.getTime()) ? '' : date.toLocaleString('en-US', { month: 'short' }).toUpperCase(),
        rows: [],
      };

      group.rows.push(row);
      groups.set(key, group);
    }

    return [...groups.values()];
  });

  protected amountClass(row: TimelineRow): string {
    return row.amountVariant ?? 'neutral';
  }
}
