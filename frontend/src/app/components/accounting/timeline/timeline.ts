import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  OnInit,
  computed,
  effect,
  inject,
  signal,
  untracked,
  viewChild,
} from '@angular/core';
import { DOCUMENT, NgTemplateOutlet } from '@angular/common';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Router } from '@angular/router';
import { filter, forkJoin, fromEvent } from 'rxjs';

import {
  Counterparty,
  DailySummary,
  DEFAULT_PREFERENCE,
  ENTRY_KIND_LABELS,
  EntryKind,
  LedgerAccount,
  LedgerEntry,
  MonthSummary,
  Preference,
  ScheduleInstance,
  defaultCategoryIcon,
} from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { AccountingLayoutComponent } from '../accounting-layout/accounting-layout';
import { BillingEvent, billingEvents, reminderDues, upcomingDues } from '../billing/billing-math';
import { BillState, BillingService, billKey } from '../billing/billing.service';
import { CalendarMonthComponent } from '../calendar-month/calendar-month';
import { todayIso } from '../dates';
import { schedulePill } from '../schedule-math';
import { KIND_PILLS, KindPill, colorOf, fxLine, iconOf, isHandledKey, pad, shiftMonth as shiftYearMonth } from '../accounting-ui';
import { displayTitle, formatMoney, formatSigned } from '../format';

export const TIMELINE_PAGE_SIZE = 50;
/** One day's entries in the 日曆 view are read in a single request. */
export const CALENDAR_DAY_LIMIT = 500;
export const TIMELINE_VIEW_KEY = 'hh.accounting.timelineView';
/** The search field commits this long after the last keystroke (Enter commits at once). */
export const QUERY_DEBOUNCE_MS = 300;


export type TimelineView = 'list' | 'calendar';

/** The remembered view; otherwise 日曆 in the two-pane layout and 清單 elsewhere (read once, resizes keep the view). */
function initialView(wide: boolean): TimelineView {
  try {
    const stored = localStorage.getItem(TIMELINE_VIEW_KEY);
    if (stored === 'calendar' || stored === 'list') {
      return stored;
    }
  } catch {
    // Storage blocked: fall through to the layout default.
  }
  return wide ? 'calendar' : 'list';
}

function storeView(view: TimelineView): void {
  try {
    localStorage.setItem(TIMELINE_VIEW_KEY, view);
  } catch {
    // Storage blocked (private mode): the choice simply is not remembered.
  }
}

const WEEKDAYS = ['週日', '週一', '週二', '週三', '週四', '週五', '週六'];
const TRANSFER_KINDS = new Set<EntryKind>(['transfer_out', 'transfer_in']);

export const TIMELINE_FILTER_KINDS: EntryKind[] = [
  'expense',
  'income',
  'transfer_out',
  'transfer_in',
  'receivable',
  'payable',
  'reward',
  'refund',
  'fee',
  'discount',
  'interest',
  'balance_adjustment',
];

export interface TimelinePill {
  label: string;
  tone: KindPill['tone'] | 'review';
}

export interface TimelineRow {
  key: string;
  entryId: number;
  icon: string;
  color: string;
  title: string;
  sub: string;
  amountText: string;
  tone: 'out' | 'in' | 'neutral';
  fx: string | null;
  pills: TimelinePill[];
  groupCount: number | null;
}

/** One 繳費提醒 line in the 日曆 day panel (only statements with a remaining balance). */
export interface BillLine {
  key: string;
  accountId: number;
  name: string;
  amountText: string;
  /** `已繳 $X · 剩餘 $Y` for a partly paid statement; null when nothing was paid yet. */
  paidText: string | null;
}

export interface TimelineDay {
  date: string;
  label: string;
  net: number;
  rows: TimelineRow[];
}

export function currentMonth(today = new Date()): string {
  return `${today.getFullYear()}-${pad(today.getMonth() + 1)}`;
}

export function shiftMonth(month: string, delta: number): string {
  const [year, monthNumber] = month.split('-').map(Number);
  const [nextYear, nextMonth] = shiftYearMonth(year, monthNumber, delta);
  return `${nextYear}-${pad(nextMonth)}`;
}

export function monthRange(month: string): { from: string; to: string } {
  const [year, monthNumber] = month.split('-').map(Number);
  const lastDay = new Date(year, monthNumber, 0).getDate();
  return { from: `${month}-01`, to: `${month}-${pad(lastDay)}` };
}

export function monthLabel(month: string): string {
  const [year, monthNumber] = month.split('-').map(Number);
  return `${year} 年 ${monthNumber} 月`;
}

export function dayLabel(date: string): string {
  const [year, month, day] = date.split('-').map(Number);
  return `${pad(month)}/${pad(day)} ${WEEKDAYS[new Date(year, month - 1, day).getDay()]}`;
}

function toneOf(amount: number, kind: EntryKind): TimelineRow['tone'] {
  if (TRANSFER_KINDS.has(kind) || amount === 0) {
    return 'neutral';
  }
  return amount < 0 ? 'out' : 'in';
}

function subLine(entry: LedgerEntry): string {
  const lead = entry.kind === 'receivable' || entry.kind === 'payable' ? entry.counterparty : entry.merchant;
  return [lead, ...entry.tags.map(tag => `#${tag}`)].filter(Boolean).join(' · ');
}

/** `分期 #5/36` / `週期 #25` (and `部分` while the period is partial) for an entry a schedule wrote. */
export function schedulePills(entry: Pick<LedgerEntry, 'schedule'>): TimelinePill[] {
  const link = entry.schedule;
  if (!link) {
    return [];
  }
  const pills: TimelinePill[] = [{ label: schedulePill(link), tone: '' }];
  if (link.is_partial) {
    pills.push({ label: '部分', tone: 'review' });
  }
  return pills;
}

function pillsOf(entry: LedgerEntry): TimelinePill[] {
  const pills: TimelinePill[] = schedulePills(entry);
  const kindPill = KIND_PILLS[entry.kind];
  if (kindPill) {
    pills.push(kindPill);
  }
  if (entry.project) {
    pills.push({ label: entry.project, tone: '' });
  }
  if (!TRANSFER_KINDS.has(entry.kind)) {
    pills.push({ label: entry.account_name, tone: '' });
  }
  if (entry.needs_review) {
    pills.push({ label: '待確認', tone: 'review' });
  }
  return pills;
}

/** Group the newest-first entries by day; one row per split group and per transfer pair. */
export function buildDays(entries: LedgerEntry[], mainCurrency: string, hideRewards: boolean): TimelineDay[] {
  const transferLegs = new Map<string, LedgerEntry[]>();
  for (const entry of entries) {
    if (entry.transfer_group_id && TRANSFER_KINDS.has(entry.kind)) {
      transferLegs.set(entry.transfer_group_id, [...(transferLegs.get(entry.transfer_group_id) ?? []), entry]);
    }
  }

  const days = new Map<string, TimelineDay>();
  const seen = new Set<string>();
  for (const entry of entries) {
    if (hideRewards && entry.kind === 'reward') {
      continue;
    }
    let row: TimelineRow;
    let net = 0;
    let netCurrency = entry.currency;
    if (entry.group) {
      const key = `g${entry.group.id}`;
      if (seen.has(key)) {
        continue;
      }
      seen.add(key);
      // `total` is null for a mixed-currency group without a cached rate: show a dash and leave it out of the day net.
      const total = entry.group.total === null ? null : Number(entry.group.total);
      row = {
        key,
        entryId: entry.id,
        icon: iconOf(entry),
        color: colorOf(entry),
        title: entry.group.name?.trim() || displayTitle(entry),
        sub: subLine(entry),
        amountText: total === null ? '—' : formatSigned(total, entry.group.currency),
        tone: total === null || total === 0 ? 'neutral' : total < 0 ? 'out' : 'in',
        fx: null,
        pills: pillsOf(entry).filter(pill => pill.tone !== 'rv'),
        groupCount: entry.group.count,
      };
      net = total ?? 0;
      netCurrency = entry.group.currency;
    } else if (entry.transfer_group_id && TRANSFER_KINDS.has(entry.kind)) {
      const key = `t${entry.transfer_group_id}`;
      if (seen.has(key)) {
        continue;
      }
      seen.add(key);
      const legs = transferLegs.get(entry.transfer_group_id) ?? [entry];
      const out = legs.find(leg => leg.kind === 'transfer_out') ?? entry;
      const incoming = legs.find(leg => leg.kind === 'transfer_in');
      row = {
        key,
        entryId: out.id,
        icon: out.category_icon ?? defaultCategoryIcon(null, 'transfer_out'),
        color: colorOf(out),
        title: out.name?.trim() || '轉帳',
        sub: incoming && incoming !== out ? `${out.account_name} → ${incoming.account_name}` : out.account_name,
        amountText: formatMoney(Math.abs(Number(out.amount)), out.currency),
        tone: 'neutral',
        fx: fxLine(out),
        pills: [...schedulePills(out), { label: '轉帳', tone: '' }],
        groupCount: null,
      };
    } else {
      const amount = Number(entry.amount);
      const tone = toneOf(amount, entry.kind);
      row = {
        key: `e${entry.id}`,
        entryId: entry.id,
        icon: iconOf(entry),
        color: colorOf(entry),
        title: displayTitle(entry),
        sub: subLine(entry),
        amountText: tone === 'neutral' ? formatMoney(Math.abs(amount), entry.currency) : formatSigned(amount, entry.currency),
        tone,
        fx: fxLine(entry),
        pills: pillsOf(entry),
        groupCount: null,
      };
      net = TRANSFER_KINDS.has(entry.kind) ? 0 : amount;
    }

    let day = days.get(entry.entry_date);
    if (!day) {
      day = { date: entry.entry_date, label: dayLabel(entry.entry_date), net: 0, rows: [] };
      days.set(entry.entry_date, day);
    }
    day.rows.push(row);
    if (netCurrency === mainCurrency) {
      day.net += net;
    }
  }
  return [...days.values()];
}

/** `/accounting` home (spec "Timeline home page"); also the left pane of the wide layout. */
@Component({
  selector: 'app-ledger-timeline',
  standalone: true,
  imports: [CalendarMonthComponent, NgTemplateOutlet],
  templateUrl: './timeline.html',
  styleUrls: ['../filters.scss', '../entry-row.scss', './timeline.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class LedgerTimelineComponent implements OnInit {
  private readonly accounting = inject(AccountingService);
  private readonly router = inject(Router);
  private readonly layoutMode = inject(LayoutModeService);
  private readonly layout = inject(AccountingLayoutComponent, { optional: true });
  private readonly document = inject(DOCUMENT);
  private requestId = 0;
  private summaryRequestId = 0;
  private dailyRequestId = 0;
  private dayRequestId = 0;
  private queryTimer: ReturnType<typeof setTimeout> | null = null;
  private readonly queryInput = viewChild<ElementRef<HTMLInputElement>>('queryInput');

  private readonly bills = inject(BillingService);

  readonly preference = signal<Preference | null>(null);
  readonly accounts = signal<LedgerAccount[]>([]);
  /** For the 🔔 count only (counterparties with an open amount). */
  private readonly counterparties = signal<Counterparty[]>([]);
  private counterpartyRequestId = 0;
  /** For the 🔔 count only: the 待完成交易 queue (`queue=true`). */
  private readonly queue = signal<ScheduleInstance[]>([]);
  private queueRequestId = 0;
  readonly month = signal(currentMonth());
  readonly summary = signal<MonthSummary | null>(null);
  readonly entries = signal<LedgerEntry[]>([]);
  /** `total` of the current filters' `/entries` page; null while that page is in flight or after it failed. */
  readonly total = signal<number | null>(null);
  readonly loading = signal(false);
  readonly loadError = signal(false);
  readonly accountFilter = signal<number | null>(null);
  readonly kindFilter = signal<EntryKind | null>(null);
  readonly query = signal('');
  readonly filtersOpen = signal(false);
  readonly view = signal<TimelineView>(initialView(untracked(this.layoutMode.mode) === 'panes'));
  readonly daily = signal<DailySummary | null>(null);
  readonly selectedDay = signal<string | null>(null);
  readonly dayEntries = signal<LedgerEntry[]>([]);
  readonly dayLoading = signal(false);
  readonly dayError = signal(false);
  readonly sentinel = viewChild<ElementRef<HTMLElement>>('sentinel');

  /** Any list filter set: the month totals are then labelled as unfiltered (spec §1.1). */
  readonly filtersActive = computed(
    () => this.accountFilter() !== null || this.kindFilter() !== null || this.query() !== '',
  );
  readonly isPhone = computed(() => this.layoutMode.mode() === 'phone');
  readonly mainCurrency = computed(() => this.preference()?.main_currency ?? DEFAULT_PREFERENCE.main_currency);
  readonly hideRewards = computed(() => this.preference()?.hide_rewards_on_timeline ?? false);
  readonly days = computed(() => buildDays(this.entries(), this.mainCurrency(), this.hideRewards()));
  readonly hasMore = computed(() => this.entries().length < (this.total() ?? 0));
  readonly selectedId = computed(() => this.layout?.selectedEntryId() ?? null);
  readonly monthText = computed(() => monthLabel(this.month()));
  readonly weekStart = computed(() => this.preference()?.week_start ?? DEFAULT_PREFERENCE.week_start);
  /** Outlined in the 日曆 grid; refreshed on a month change and when the tab becomes visible (past midnight). */
  readonly today = signal(todayIso());
  /** The selected day's rows as the list groups them (`buildDays`), so filters apply to rows and net alike. */
  private readonly dayGroup = computed(
    () => buildDays(this.dayEntries(), this.mainCurrency(), this.hideRewards())[0] ?? null,
  );
  readonly dayRows = computed(() => this.dayGroup()?.rows ?? []);
  /** Net of the rows shown (main-currency rows only, the list's per-day rule); the grid keeps the summary figures. */
  readonly dayNet = computed(() => {
    const group = this.dayGroup();
    return group ? { net: group.net, text: formatSigned(group.net, this.mainCurrency()) } : null;
  });
  readonly selectedDayText = computed(() => {
    const date = this.selectedDay();
    return date ? dayLabel(date) : '';
  });
  /** Credit-card closing / due days of the shown month (empty without cards that have a closing day). */
  private readonly monthBilling = computed(() => billingEvents(this.accounts(), this.month()));
  /** Due days from today through today + 6 (may reach into next month). */
  private readonly upcomingEvents = computed(() => upcomingDues(this.accounts(), this.today()));
  /** Each card's current statement as the reminder centre lists it (for the 🔔 count). */
  private readonly reminderEvents = computed(() => reminderDues(this.accounts(), this.today()));
  /** Due events whose statement is needed: the shown month's, the 近 7 天 window's and the 🔔's (deduplicated). */
  private readonly trackedDues = computed(() => {
    const events = new Map<string, BillingEvent>();
    for (const event of [...this.monthBilling(), ...this.upcomingEvents(), ...this.reminderEvents()]) {
      if (event.kind === 'due') {
        events.set(billKey(event), event);
      }
    }
    return [...events.values()];
  });
  /** A due event's resolved statement when something is left to pay; null otherwise (also while unresolved). */
  private openBill(event: BillingEvent): BillState | null {
    const bill = this.bills.bill(event);
    return bill && bill.remaining > 0 ? bill : null;
  }
  /** What the grid marks: every closing day, and the due days of statements with a remaining balance. */
  readonly billing = computed(() => this.monthBilling().filter(event => event.kind === 'closing' || this.openBill(event) !== null));
  readonly billLines = computed(() => {
    const day = this.selectedDay();
    if (this.view() !== 'calendar' || !day) {
      return [];
    }
    const lines: BillLine[] = [];
    for (const event of this.monthBilling()) {
      const bill = event.kind === 'due' && event.date === day ? this.openBill(event) : null;
      if (bill) {
        lines.push({
          key: billKey(event),
          accountId: event.accountId,
          name: event.name,
          amountText: formatMoney(bill.statement, bill.currency),
          paidText:
            bill.paid > 0
              ? `已繳 ${formatMoney(bill.paid, bill.currency)} · 剩餘 ${formatMoney(bill.remaining, bill.currency)}`
              : null,
        });
      }
    }
    return lines;
  });
  /**
   * 🔔 count: cards with something left to pay, counterparties with open debt rows, and 待完成交易 items due today or
   * earlier or partial (paused and ended definitions are not in the queue).
   */
  readonly reminderCount = computed(
    () =>
      this.reminderEvents().filter(event => this.openBill(event) !== null).length +
      this.counterparties().filter(counterparty => (counterparty.open_count ?? 0) > 0).length +
      this.queue().filter(item => item.is_partial || item.due_date <= this.today()).length,
  );
  /** The list view's 近 7 天 banner: upcoming due days with a remaining balance. */
  readonly upcomingBills = computed(() => this.upcomingEvents().filter(event => this.openBill(event) !== null));
  readonly missingRates = computed(() =>
    (this.view() === 'calendar' ? this.daily()?.missing_rates : this.summary()?.missing_rates) ?? [],
  );
  readonly kindOptions = TIMELINE_FILTER_KINDS.map(kind => ({ kind, label: ENTRY_KIND_LABELS[kind] }));
  readonly formatSigned = formatSigned;

  constructor() {
    fromEvent(this.document, 'visibilitychange')
      .pipe(
        filter(() => this.document.visibilityState === 'visible'),
        takeUntilDestroyed(inject(DestroyRef)),
      )
      .subscribe(() => this.today.set(todayIso()));

    effect(() => {
      if (!this.preference()) {
        return;
      }
      this.month();
      this.accountFilter();
      this.kindFilter();
      this.query();
      this.accounting.entriesChanged();
      if (this.view() === 'calendar') {
        // The list page is not shown: skip it, and switching back starts again from page 1.
        untracked(() => this.clearList());
        return;
      }
      untracked(() => this.load(true));
    });

    // 日曆: per-day figures for the month (a saved preference re-reads `preference`, which re-runs this).
    effect(() => {
      if (!this.preference()) {
        return;
      }
      const month = this.month();
      const view = this.view();
      this.accounting.entriesChanged();
      untracked(() => (view === 'calendar' ? this.loadDaily(month) : this.dropDaily()));
    });

    // 日曆: the tapped day's entries, with the list's filters.
    effect(() => {
      if (!this.preference()) {
        return;
      }
      const day = this.selectedDay();
      const view = this.view();
      this.accountFilter();
      this.kindFilter();
      this.query();
      this.accounting.entriesChanged();
      untracked(() => (view === 'calendar' && day ? this.loadDay(day) : this.clearDay()));
    });

    effect(() => {
      if (!this.preference()) {
        return;
      }
      const month = this.month();
      this.accounting.entriesChanged();
      untracked(() => this.loadSummary(month));
    });

    // Statements of the due events the grid and the banner need (`BillingService` reads each once per change round).
    effect(() => {
      if (!this.preference()) {
        return;
      }
      const events = this.trackedDues();
      this.bills.generation();
      untracked(() => this.bills.ensure(events, this.today()));
    });

    // The 🔔's counterparties: open amounts move with entry writes; a backup import (accountsChanged) replaces them.
    effect(() => {
      this.accounting.entriesChanged();
      this.accounting.accountsChanged();
      untracked(() => {
        this.loadCounterparties();
        this.loadQueue();
      });
    });

    // A saved preference (settings pane beside this list in the wide layout) is re-read; the effects above then reload.
    let seenPreferenceChange = this.accounting.preferenceChanged();
    effect(() => {
      const change = this.accounting.preferenceChanged();
      if (change === seenPreferenceChange) {
        return;
      }
      seenPreferenceChange = change;
      untracked(() =>
        this.accounting.getPreference().subscribe({ next: preference => this.preference.set(preference), error: () => undefined }),
      );
    });

    // The account filter follows account writes (a new card, a rename, an archive) made beside this list.
    let seenAccountsChange = this.accounting.accountsChanged();
    effect(() => {
      const change = this.accounting.accountsChanged();
      if (change === seenAccountsChange) {
        return;
      }
      seenAccountsChange = change;
      untracked(() =>
        this.accounting.getAccounts().subscribe({ next: accounts => this.accounts.set(accounts), error: () => undefined }),
      );
    });

    effect(onCleanup => {
      const element = this.sentinel()?.nativeElement;
      if (!element || typeof IntersectionObserver === 'undefined') {
        return;
      }
      const observer = new IntersectionObserver(
        items => {
          if (items.some(item => item.isIntersecting)) {
            this.loadMore();
          }
        },
        { rootMargin: '200px' },
      );
      observer.observe(element);
      onCleanup(() => observer.disconnect());
    });
    inject(DestroyRef).onDestroy(() => this.clearQueryTimer());
  }

  ngOnInit(): void {
    forkJoin({
      preference: this.accounting.getPreference(),
      accounts: this.accounting.getAccounts(),
    }).subscribe({
      next: ({ preference, accounts }) => {
        this.accounts.set(accounts);
        this.preference.set(preference);
      },
      error: () => this.preference.set(DEFAULT_PREFERENCE),
    });
  }

  load(reset: boolean): void {
    const id = ++this.requestId;
    const { from, to } = monthRange(this.month());
    const offset = reset ? 0 : this.entries().length;
    // On a reset the old rows stay until the new page lands (no empty flash, scroll kept).
    this.loading.set(true);
    this.loadError.set(false);
    if (reset) {
      // Never show the previous filters' count while this page is in flight.
      this.total.set(null);
    }
    const account = this.accountFilter();
    this.accounting
      .getAllEntries({
        limit: TIMELINE_PAGE_SIZE,
        offset,
        date_from: from,
        date_to: to,
        kind: this.kindFilter(),
        q: this.query() || null,
        account_id: account === null ? undefined : [account],
        hide_rewards: this.hideRewards(),
      })
      .subscribe({
        next: page => {
          if (id !== this.requestId) {
            return;
          }
          this.entries.set(reset ? page.items : [...this.entries(), ...page.items]);
          this.total.set(page.total);
          this.loading.set(false);
        },
        error: () => {
          if (id !== this.requestId) {
            return;
          }
          if (reset) {
            // The rows on screen belong to the previous month / filter: never show them beside the error.
            this.entries.set([]);
            this.total.set(null);
          }
          this.loadError.set(true);
          this.loading.set(false);
        },
      });
  }

  loadMore(): void {
    if (this.hasMore() && !this.loading()) {
      this.load(false);
    }
  }

  private loadSummary(month: string): void {
    const id = ++this.summaryRequestId;
    this.accounting.getMonthSummary(month).subscribe({
      next: summary => {
        if (id === this.summaryRequestId) {
          this.summary.set(summary);
        }
      },
      error: () => {
        if (id === this.summaryRequestId) {
          this.summary.set(null);
        }
      },
    });
  }

  private clearList(): void {
    ++this.requestId;
    this.entries.set([]);
    this.total.set(null);
    this.loading.set(false);
    this.loadError.set(false);
  }

  private loadDaily(month: string): void {
    const id = ++this.dailyRequestId;
    this.accounting.getDailySummary(month).subscribe({
      next: daily => {
        if (id === this.dailyRequestId) {
          this.daily.set(daily);
        }
      },
      error: () => {
        if (id === this.dailyRequestId) {
          this.daily.set(null);
        }
      },
    });
  }

  private dropDaily(): void {
    ++this.dailyRequestId;
    this.daily.set(null);
  }

  private loadDay(date: string): void {
    const id = ++this.dayRequestId;
    this.dayLoading.set(true);
    this.dayError.set(false);
    const account = this.accountFilter();
    this.accounting
      .getAllEntries({
        limit: CALENDAR_DAY_LIMIT,
        offset: 0,
        date_from: date,
        date_to: date,
        kind: this.kindFilter(),
        q: this.query() || null,
        account_id: account === null ? undefined : [account],
        hide_rewards: this.hideRewards(),
      })
      .subscribe({
        next: page => {
          if (id === this.dayRequestId) {
            this.dayEntries.set(page.items);
            this.dayLoading.set(false);
          }
        },
        error: () => {
          if (id === this.dayRequestId) {
            this.dayEntries.set([]);
            this.dayError.set(true);
            this.dayLoading.set(false);
          }
        },
      });
  }

  private clearDay(): void {
    ++this.dayRequestId;
    this.dayEntries.set([]);
    this.dayLoading.set(false);
    this.dayError.set(false);
  }

  private loadQueue(): void {
    const id = ++this.queueRequestId;
    this.accounting.getScheduleInstances({ queue: true }).subscribe({
      next: items => {
        if (id === this.queueRequestId) {
          this.queue.set(items);
        }
      },
      // Swallowed like the counterparties: the 🔔 is a hint; the reminder centre reports its own load errors.
      error: () => undefined,
    });
  }

  private loadCounterparties(): void {
    const id = ++this.counterpartyRequestId;
    this.accounting.getCounterparties().subscribe({
      next: counterparties => {
        if (id === this.counterpartyRequestId) {
          this.counterparties.set(counterparties);
        }
      },
      // Intentionally swallowed: the 🔔 count is a hint. On failure it keeps the last known counterparties (none on
      // first load), and the reminder centre itself reports its own load errors.
      error: () => undefined,
    });
  }

  openReminders(): void {
    void this.router.navigate(['/accounting/reminders']);
  }

  setView(view: TimelineView): void {
    if (view !== this.view()) {
      this.view.set(view);
      storeView(view);
    }
  }

  /** Tapping the selected day again closes it. */
  selectDay(date: string): void {
    this.selectedDay.update(current => (current === date ? null : date));
  }

  /** The 近 7 天 banner: 日曆 on the first upcoming due day (next month's when it falls there). */
  openUpcoming(): void {
    const first = this.upcomingBills()[0];
    if (!first) {
      return;
    }
    this.setView('calendar');
    this.month.set(first.date.slice(0, 7));
    this.selectedDay.set(first.date);
  }

  openCard(accountId: number): void {
    void this.router.navigate(['/accounting/accounts', accountId]);
  }

  moveMonth(delta: number): void {
    this.selectedDay.set(null);
    this.today.set(todayIso());
    this.month.update(month => shiftMonth(month, delta));
  }

  setAccount(value: string): void {
    this.accountFilter.set(value ? Number(value) : null);
  }

  setKind(value: string): void {
    this.kindFilter.set(value ? (value as EntryKind) : null);
  }

  setQuery(value: string): void {
    this.query.set(value.trim());
  }

  onQueryInput(value: string): void {
    this.clearQueryTimer();
    this.queryTimer = setTimeout(() => {
      this.queryTimer = null;
      this.setQuery(value);
    }, QUERY_DEBOUNCE_MS);
  }

  /** Enter commits now; Esc empties a field that has text and stops there (an empty field lets Esc through). */
  onQueryKeydown(event: KeyboardEvent): void {
    if (isHandledKey(event)) {
      return;
    }
    const input = event.target as HTMLInputElement;
    if (event.key === 'Enter') {
      event.preventDefault();
      this.clearQueryTimer();
      this.setQuery(input.value);
    } else if (event.key === 'Escape' && input.value !== '') {
      event.stopPropagation();
      this.clearQueryTimer();
      input.value = '';
      this.setQuery('');
    }
  }

  private clearQueryTimer(): void {
    if (this.queryTimer !== null) {
      clearTimeout(this.queryTimer);
      this.queryTimer = null;
    }
  }

  clearFilters(): void {
    this.clearQueryTimer();
    const input = this.queryInput()?.nativeElement;
    if (input) {
      input.value = '';
    }
    this.accountFilter.set(null);
    this.kindFilter.set(null);
    this.query.set('');
  }


  toggleFilters(): void {
    this.filtersOpen.update(open => !open);
  }

  open(row: TimelineRow): void {
    void this.router.navigate(['/accounting/entries', row.entryId]);
  }

  expenseText(summary: MonthSummary): string {
    return formatSigned(-Math.abs(Number(summary.expense)), summary.currency);
  }

  incomeText(summary: MonthSummary): string {
    return formatSigned(Math.abs(Number(summary.income)), summary.currency);
  }
}
