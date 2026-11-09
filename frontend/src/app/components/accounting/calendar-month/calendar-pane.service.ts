import { Injectable, signal } from '@angular/core';

import { BreakdownRow } from '../../../models/reports.model';
import { currentMonth, todayIso } from '../dates';
import { InboxEinvoice } from '../inbox/einvoice.model';
import { OverviewState } from '../month-overview/overview.service';
import type { BillLine, DayNet, TimelineDay, TimelineRow } from '../timeline/timeline';
import { CalendarPartState } from './calendar-data.service';

/** The 今天 card of the right pane: today's count / net and the first entries' titles. */
export interface TodayCard {
  date: string;
  count: number;
  /** Titles of up to three of today's entries, joined with `、`. */
  names: string;
  netText: string;
  net: number;
}

/** One 這個月要處理 due line (a statement with a remaining balance). */
export interface MonthDue {
  key: string;
  accountId: number;
  name: string;
  date: string;
  amountText: string;
}

/** What the right pane can ask the timeline to do (the timeline owns every read and the URL). */
export interface CalendarCommands {
  stepEntry(delta: 1 | -1): void;
  openInvoice(invoice: InboxEinvoice): void;
  setQuery(text: string): void;
}

/**
 * What the timeline publishes for the 日曆 view's right pane. A plain signals holder: the timeline is the only
 * writer, the pane components only read (and call `commands`); no reads and no router here.
 */
@Injectable({ providedIn: 'root' })
export class CalendarPaneService {
  readonly month = signal(currentMonth());
  readonly today = signal(todayIso());
  readonly selectedDay = signal<string | null>(null);
  readonly dayRows = signal<TimelineRow[]>([]);
  readonly dayNet = signal<DayNet | null>(null);
  readonly dayState = signal<OverviewState>('ready');
  readonly billLines = signal<BillLine[]>([]);
  readonly todayCard = signal<TodayCard | null>(null);
  readonly merchants = signal<BreakdownRow[]>([]);
  readonly merchantsState = signal<CalendarPartState>('loading');
  readonly invoices = signal<InboxEinvoice[]>([]);
  readonly monthDues = signal<MonthDue[]>([]);
  /** Mirror of the timeline's `query` (the filter bar's text); the right pane changes it through `commands.setQuery`. */
  readonly search = signal('');
  readonly searchDays = signal<TimelineDay[]>([]);
  readonly searchState = signal<OverviewState>('ready');
  readonly commands = signal<CalendarCommands | null>(null);

  clear(): void {
    this.month.set(currentMonth());
    this.today.set(todayIso());
    this.selectedDay.set(null);
    this.dayRows.set([]);
    this.dayNet.set(null);
    this.dayState.set('ready');
    this.billLines.set([]);
    this.todayCard.set(null);
    this.merchants.set([]);
    this.merchantsState.set('loading');
    this.invoices.set([]);
    this.monthDues.set([]);
    this.search.set('');
    this.searchDays.set([]);
    this.searchState.set('ready');
    this.commands.set(null);
  }
}
