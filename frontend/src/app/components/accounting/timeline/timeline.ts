import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  OnInit,
  computed,
  effect,
  inject,
  signal,
  untracked,
  viewChild,
} from '@angular/core';
import { NgTemplateOutlet } from '@angular/common';
import { Router } from '@angular/router';
import { forkJoin } from 'rxjs';

import {
  DailySummary,
  DEFAULT_PREFERENCE,
  ENTRY_KIND_LABELS,
  EntryKind,
  LedgerAccount,
  LedgerEntry,
  MonthSummary,
  Preference,
  defaultCategoryIcon,
} from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { AccountingLayoutComponent } from '../accounting-layout/accounting-layout';
import { CalendarMonthComponent } from '../calendar-month/calendar-month';
import { KIND_PILLS, KindPill, colorOf, fxLine, iconOf, pad, shiftMonth as shiftYearMonth } from '../accounting-ui';
import { displayTitle, formatMoney, formatSigned } from '../format';

export const TIMELINE_PAGE_SIZE = 50;
/** One day's entries in the 日曆 view are read in a single request. */
export const CALENDAR_DAY_LIMIT = 500;
export const TIMELINE_VIEW_KEY = 'hh.accounting.timelineView';

export type TimelineView = 'list' | 'calendar';

function readStoredView(): TimelineView {
  try {
    return localStorage.getItem(TIMELINE_VIEW_KEY) === 'calendar' ? 'calendar' : 'list';
  } catch {
    return 'list';
  }
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

function pillsOf(entry: LedgerEntry): TimelinePill[] {
  const pills: TimelinePill[] = [];
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
        pills: [{ label: '轉帳', tone: '' }],
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
  styleUrls: ['../filters.scss', './timeline.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class LedgerTimelineComponent implements OnInit {
  private readonly accounting = inject(AccountingService);
  private readonly router = inject(Router);
  private readonly layoutMode = inject(LayoutModeService);
  private readonly layout = inject(AccountingLayoutComponent, { optional: true });
  private requestId = 0;
  private summaryRequestId = 0;
  private dailyRequestId = 0;
  private dayRequestId = 0;

  readonly preference = signal<Preference | null>(null);
  readonly accounts = signal<LedgerAccount[]>([]);
  readonly month = signal(currentMonth());
  readonly summary = signal<MonthSummary | null>(null);
  readonly entries = signal<LedgerEntry[]>([]);
  readonly total = signal(0);
  readonly loading = signal(false);
  readonly loadError = signal(false);
  readonly accountFilter = signal<number | null>(null);
  readonly kindFilter = signal<EntryKind | null>(null);
  readonly query = signal('');
  readonly filtersOpen = signal(false);
  readonly view = signal<TimelineView>(readStoredView());
  readonly daily = signal<DailySummary | null>(null);
  readonly selectedDay = signal<string | null>(null);
  readonly dayEntries = signal<LedgerEntry[]>([]);
  readonly dayLoading = signal(false);
  readonly dayError = signal(false);
  readonly sentinel = viewChild<ElementRef<HTMLElement>>('sentinel');

  readonly isPhone = computed(() => this.layoutMode.mode() === 'phone');
  readonly mainCurrency = computed(() => this.preference()?.main_currency ?? DEFAULT_PREFERENCE.main_currency);
  readonly hideRewards = computed(() => this.preference()?.hide_rewards_on_timeline ?? false);
  readonly days = computed(() => buildDays(this.entries(), this.mainCurrency(), this.hideRewards()));
  readonly hasMore = computed(() => this.entries().length < this.total());
  readonly selectedId = computed(() => this.layout?.selectedEntryId() ?? null);
  readonly monthText = computed(() => monthLabel(this.month()));
  readonly weekStart = computed(() => this.preference()?.week_start ?? DEFAULT_PREFERENCE.week_start);
  readonly dayRows = computed(() => buildDays(this.dayEntries(), this.mainCurrency(), this.hideRewards()).flatMap(day => day.rows));
  /** The selected day's net from the daily summary (converted to the main currency, like the grid figures). */
  readonly dayNet = computed(() => {
    const daily = this.daily();
    const date = this.selectedDay();
    if (!daily || !date) {
      return null;
    }
    const day = daily.days.find(item => item.date === date);
    const net = day ? Number(day.income) + Number(day.expense) : 0;
    return { net, text: formatMoney(net, daily.currency, { sign: true }) };
  });
  readonly selectedDayText = computed(() => {
    const date = this.selectedDay();
    return date ? dayLabel(date) : '';
  });
  readonly missingRates = computed(() =>
    (this.view() === 'calendar' ? this.daily()?.missing_rates : this.summary()?.missing_rates) ?? [],
  );
  readonly kindOptions = TIMELINE_FILTER_KINDS.map(kind => ({ kind, label: ENTRY_KIND_LABELS[kind] }));
  readonly formatSigned = formatSigned;

  constructor() {
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
            this.total.set(0);
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
    this.total.set(0);
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

  moveMonth(delta: number): void {
    this.selectedDay.set(null);
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
