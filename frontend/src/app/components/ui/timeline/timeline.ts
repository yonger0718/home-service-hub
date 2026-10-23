import { ChangeDetectionStrategy, Component, computed, input, output, TemplateRef } from '@angular/core';

import { NgTemplateOutlet } from '@angular/common';

import { SideTagComponent, SideTagVariant } from '../side-tag/side-tag';
import { TagComponent } from '../tag/tag';

export type TimelineAmountVariant = 'neutral' | 'positive' | 'negative' | 'buy' | 'sell' | 'dividend' | 'income' | 'expense';

export interface TimelineRow {
  id?: string | number;
  date: string | Date;
  side: SideTagVariant;
  sideLabel?: string;
  primary: string;
  meta?: string;
  metaBadge?: string;
  amount?: string;
  amountVariant?: TimelineAmountVariant;
  /** Second line under `meta` (e.g. fees). */
  sub?: string;
  /** Chips after the SideTag (e.g. 當沖). */
  tags?: string[];
  broker?: string;
  /** Small line under the amount. */
  amountNote?: string;
  /** Coloured initials square before the name; `token` is a CSS custom property name such as `--app-primary`. */
  square?: TimelineSquare;
}

export interface TimelineSquare {
  text: string;
  token: string;
  ink: 'light' | 'dark';
}

interface TimelineGroup {
  key: string;
  day: string;
  month: string;
  rows: TimelineRow[];
}

interface TimelineMonth {
  key: string;
  label: string;
  heading: string | null;
  rows: TimelineRow[];
}

const WEEKDAYS = ['週日', '週一', '週二', '週三', '週四', '週五', '週六'];

/** Calendar parts: a string's own `YYYY-MM-DD` prefix (no time-zone shift), a Date's local date. */
function dateParts(date: string | Date): [number, number, number] | null {
  if (date instanceof Date) {
    return Number.isNaN(date.getTime()) ? null : [date.getFullYear(), date.getMonth() + 1, date.getDate()];
  }
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(date);
  return match ? [Number(match[1]), Number(match[2]), Number(match[3])] : null;
}

/** `YYYY-MM`: the first 7 characters of a string date, a Date's local year and month. */
function monthKeyOf(date: string | Date): string {
  if (typeof date === 'string') return date.slice(0, 7);
  const parts = dateParts(date);
  return parts ? `${parts[0]}-${String(parts[1]).padStart(2, '0')}` : '';
}

/** `2026-10` → `2026 年 10 月`; anything else as is, or `—` when empty. */
function monthLabelOf(key: string): string {
  const match = /^(\d{4})-(\d{2})$/.exec(key);
  return match ? `${match[1]} 年 ${Number(match[2])} 月` : key || '—';
}

@Component({
  selector: 'app-timeline',
  imports: [SideTagComponent, TagComponent, NgTemplateOutlet],
  template: `
    <div class="timeline" [class.tl-compact]="compact()" [class.tl-by-month]="groupBy() === 'month'">
      @if (groupBy() === 'month') {
        @for (month of months(); track month.key) {
          <section class="tl-month-group">
            <div class="tl-month">
              <span class="tl-month-label">{{ month.label }}</span>
              @if (month.heading !== null) {
                <span class="tl-month-sum">{{ month.heading }}</span>
              }
            </div>
            <div class="tl-month-rows">
              @for (row of month.rows; track row.id ?? (row.primary + row.amount + $index)) {
                <div class="tl-item">
                  @if (!compact()) {
                    <div class="tl-date tl-day">
                      <span class="d">{{ dayLabel(row.date) }}</span>
                      <span class="m">{{ weekday(row.date) }}</span>
                    </div>
                  }
                  <ng-container [ngTemplateOutlet]="card" [ngTemplateOutletContext]="{ $implicit: row }"></ng-container>
                </div>
              }
            </div>
          </section>
        }
      } @else {
        @for (group of groups(); track group.key) {
          @for (row of group.rows; track row.id ?? (row.primary + row.amount + $index)) {
            <div class="tl-item">
              @if (!compact()) {
                @if ($first) {
                  <div class="tl-date">
                    <span class="d">{{ group.day }}</span>
                    <span class="m">{{ group.month }}</span>
                  </div>
                } @else {
                  <div class="tl-date" aria-hidden="true"></div>
                }
              }
              <ng-container [ngTemplateOutlet]="card" [ngTemplateOutletContext]="{ $implicit: row }"></ng-container>
            </div>
          }
        }
      }
    </div>

    <ng-template #card let-row>
      <article class="tl-card" [class.tl-card-expanded]="rowDetails()" [class.tl-card-rich]="!!row.square" [attr.data-row-id]="row.id">
        <div class="tl-lhs">
          @if (row.square; as square) {
            <span class="tl-square" [class.ink-dark]="square.ink === 'dark'" [class.ink-light]="square.ink === 'light'"
              [style.background]="'var(' + square.token + ')'" aria-hidden="true">{{ square.text }}</span>
          } @else {
            <app-side-tag [variant]="row.side" [label]="row.sideLabel || ''"></app-side-tag>
          }
          <div>
            @if (row.square || row.tags?.length) {
              <div class="tl-name tl-name-line">
                <span class="tl-primary">{{ row.primary }}</span>
                @if (row.square) {
                  <app-side-tag [variant]="row.side" [label]="row.sideLabel || ''"></app-side-tag>
                }
                @for (tag of row.tags ?? []; track $index) {
                  <app-tag variant="warning">{{ tag }}</app-tag>
                }
              </div>
            } @else {
              <div class="tl-name">{{ row.primary }}</div>
            }
            @if (compact()) {
              @if (compactMeta(row); as meta) {
                <div class="tl-meta"><span>{{ meta }}</span></div>
              }
            } @else if (!row.square && (row.meta || row.metaBadge)) {
              <div class="tl-meta">
                @if (row.metaBadge) {
                  <span class="tl-meta-badge">{{ row.metaBadge }}</span>
                }
                @if (row.meta) {
                  <span>{{ row.meta }}</span>
                }
              </div>
            }
            @if (!compact() && !row.square && row.sub) {
              <div class="tl-sub">{{ row.sub }}</div>
            }
          </div>
        </div>
        @if (!compact()) {
          @if (row.square) {
            <div class="tl-qty">
              @if (row.meta) {
                <div class="tl-qty-main">{{ row.meta }}</div>
              }
              @if (row.sub) {
                <div class="tl-sub">{{ row.sub }}</div>
              }
            </div>
          }
          @if (row.square || row.broker) {
            <div class="tl-broker">{{ row.broker ?? '' }}</div>
          }
        }
        @if (compact() || row.amountNote) {
          <div class="tl-amt-col">
            @if (row.amount) {
              <div class="tl-amt {{ amountClass(row) }}">{{ row.amount }}</div>
            }
            @if (compact()) {
              <div class="tl-amt-note tl-amt-date">{{ dayLabel(row.date) }}</div>
            } @else {
              <div class="tl-amt-note">{{ row.amountNote }}</div>
            }
          </div>
        } @else if (row.amount) {
          <div class="tl-amt {{ amountClass(row) }}">{{ row.amount }}</div>
        }
        @if (rowActions() && row.id !== undefined) {
          <button type="button" class="tl-action" [attr.aria-label]="'操作 ' + row.primary"
            aria-haspopup="menu" (click)="rowAction.emit({ id: row.id, event: $event })">⋯</button>
        }
        @if (rowDetails(); as details) {
          <div class="tl-details">
            <ng-container [ngTemplateOutlet]="details" [ngTemplateOutletContext]="{ $implicit: row }"></ng-container>
          </div>
        }
      </article>
    </ng-template>
  `,
  styleUrl: './timeline.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class TimelineComponent {
  readonly rows = input<TimelineRow[]>([]);
  readonly rowDetails = input<TemplateRef<{ $implicit: TimelineRow }> | null>(null);
  readonly rowActions = input(false);
  readonly groupBy = input<'day' | 'month'>('day');
  readonly groupHeading = input<((key: string) => string) | null>(null);
  readonly compact = input(false);
  readonly rowAction = output<{ id: string | number; event: MouseEvent }>();

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

  /** Month mode: one group per `YYYY-MM` in first-seen order, with the caller's right-hand heading text. */
  protected readonly months = computed<TimelineMonth[]>(() => {
    const heading = this.groupHeading();
    const months = new Map<string, TimelineMonth>();
    for (const row of this.rows()) {
      const key = monthKeyOf(row.date);
      let month = months.get(key);
      if (!month) {
        month = { key, label: monthLabelOf(key), heading: heading ? heading(key) : null, rows: [] };
        months.set(key, month);
      }
      month.rows.push(row);
    }
    return [...months.values()];
  });

  /** `MM/DD`, or the raw value when it is not a date. */
  protected dayLabel(date: string | Date): string {
    const parts = dateParts(date);
    if (!parts) return typeof date === 'string' ? date : '';
    return `${String(parts[1]).padStart(2, '0')}/${String(parts[2]).padStart(2, '0')}`;
  }

  /** zh-TW weekday (`週四`) of the calendar date, computed in UTC so no time zone shifts it. */
  protected weekday(date: string | Date): string {
    const parts = dateParts(date);
    return parts ? WEEKDAYS[new Date(Date.UTC(parts[0], parts[1] - 1, parts[2])).getUTCDay()] : '';
  }

  /** Compact meta line: `meta · broker`. */
  protected compactMeta(row: TimelineRow): string {
    return [row.meta, row.broker].filter(Boolean).join(' · ');
  }

  protected amountClass(row: TimelineRow): string {
    return row.amountVariant ?? 'neutral';
  }
}
