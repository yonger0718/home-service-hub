import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  OnInit,
  computed,
  effect,
  inject,
  input,
  signal,
  untracked,
  viewChild,
} from '@angular/core';
import { DOCUMENT, NgTemplateOutlet } from '@angular/common';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { NavigationEnd, Params, Router } from '@angular/router';
import { filter, forkJoin, fromEvent } from 'rxjs';

import {
  AllEntriesQuery,
  CategoryNode,
  Counterparty,
  DEFAULT_PREFERENCE,
  DailySummary,
  ENTRY_KIND_LABELS,
  EntryKind,
  LedgerAccount,
  LedgerEntry,
  MonthSummary,
  Preference,
  Project,
  ScheduleInstance,
  defaultCategoryIcon,
} from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import { BusinessCalendarService } from '../../../services/business-calendar.service';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { AccountingLayoutComponent } from '../accounting-layout/accounting-layout';
import { BillingEvent, billingEvents, payDateOf, reminderDues, upcomingDues } from '../billing/billing-math';
import { BillState, BillingService, billKey } from '../billing/billing.service';
import { countReminders } from '../billing/reminder-count';
import { SkeletonComponent } from '../skeleton/skeleton';
import { FolderIcon, SplitFolderComponent } from '../split-folder/split-folder';
import { groupCommands, listOpenState } from '../split-group/group-nav';
import { CalendarMonthComponent } from '../calendar-month/calendar-month';
import { CalendarDataService } from '../calendar-month/calendar-data.service';
import { CalendarPaneService, MonthDue, TodayCard } from '../calendar-month/calendar-pane.service';
import { categoryColorMap } from '../calendar-month/calendar-math';
import { inboxBellCount } from '../inbox/inbox-view';
import { InboxService } from '../inbox/inbox.service';
import type { InboxEinvoice } from '../inbox/einvoice.model';
import { parseTags } from '../entry-form/entry-draft';
import { addDays, currentMonth, dayLabel, monthLabel, monthRange, todayIso } from '../dates';
import { calendarParams, readCalendarQuery, writeCalendarQuery } from './calendar-url';
import { nextDueItems } from '../month-overview/due-items';
import { AccountingOverviewService, OverviewState } from '../month-overview/overview.service';
import { schedulePill } from '../schedule-math';
import { KIND_PILLS, KindPill, QUERY_DEBOUNCE_MS, colorOf, fxLine, iconOf, isHandledKey, pad, shiftMonth as shiftYearMonth } from '../accounting-ui';
import { displayTitle, formatMoney, formatSigned } from '../format';
import { FilterBarComponent, FilterChip } from '../../ui/filter-bar/filter-bar';
import { batchKey, batchSub, loadedBatches } from '../reward-batch';
import { PageHeaderComponent } from '../../shell/page-header/page-header';
import { FALLBACK_COLOR, barSegments, categoryDistribution, distributionAriaText } from './category-distribution';
import { reviewTitle } from '../review-reason';

export const TIMELINE_PAGE_SIZE = 50;
/** One day's entries in the 日曆 view are read in a single request. */
export const CALENDAR_DAY_LIMIT = 500;
export const TIMELINE_VIEW_KEY = 'hh.accounting.timelineView';
// Moved to leaf modules so the layout and the day panel never value-import this lazily loaded page; re-exported here.
export { QUERY_DEBOUNCE_MS } from '../accounting-ui';
export { dayLabel } from '../dates';

/** '' for an empty or non-numeric bound, else the trimmed decimal text as typed (the API takes a decimal string). */
export function normalizeAmountBound(value: string): string {
  const text = value.trim();
  return text !== '' && /^-?\d+(\.\d+)?$/.test(text) ? text : '';
}

/** `GET /categories` returns roots with nested `children`; one flat list in tree order for a <select>: each root
 *  followed by its children, labelled `root › child` (any depth, labels joined the same way). */
export function flattenCategories(roots: CategoryNode[]): CategoryNode[] {
  const out: CategoryNode[] = [];
  const walk = (nodes: CategoryNode[], prefix: string) => {
    for (const node of nodes) {
      const name = prefix ? `${prefix} › ${node.name}` : node.name;
      out.push({ ...node, name });
      walk(node.children ?? [], name);
    }
  };
  walk(roots, '');
  return out;
}


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
  /** Hover text (the 待確認 reason, #306). */
  title?: string;
}

export interface TimelineRow {
  key: string;
  entryId: number;
  /**
   * Entries the row stands for on this page: a group row's members loaded so far (the detail may route to any of them),
   * else just `entryId`.
   */
  memberIds: number[];
  icon: string;
  color: string;
  title: string;
  sub: string;
  amountText: string;
  tone: 'out' | 'in' | 'neutral';
  fx: string | null;
  pills: TimelinePill[];
  groupCount: number | null;
  /** A split group's folder tile: the icons of its members on this page and the group's full count; null otherwise. */
  folder: { icons: FolderIcon[]; count: number } | null;
  /** `發票：` + the linked-invoice item names a search matched; null when none. */
  invoiceHits: string | null;
  /** #304: an engine reward batch folded into this row; its loaded members, shown in place when expanded. */
  batch?: { members: TimelineRow[] } | null;
  /** #304: a batch member drawn under its expanded batch row. */
  nested?: boolean;
  /** `HH:MM` from `entry_time`, null without one. */
  time: string | null;
}

/** The selected day's net as the list groups it (main-currency rows only). */
export interface DayNet {
  net: number;
  text: string;
  excluded: boolean;
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
  /** Main-currency rows only. */
  net: number;
  /** A row with a non-zero amount outside the main currency (or a mixed group without a rate) is left out of `net`. */
  excluded: boolean;
  rows: TimelineRow[];
}

export { currentMonth, monthLabel, monthRange } from '../dates';

export function shiftMonth(month: string, delta: number): string {
  const [year, monthNumber] = month.split('-').map(Number);
  const [nextYear, nextMonth] = shiftYearMonth(year, monthNumber, delta);
  return `${nextYear}-${pad(nextMonth)}`;
}

/** A day group's net (main-currency rows only) as the day header shows it. */
function dayNetOf(group: TimelineDay | null, currency: string): DayNet | null {
  return group ? { net: group.net, text: formatSigned(group.net, currency), excluded: group.excluded } : null;
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
    pills.push({ label: '待確認', tone: 'review', title: reviewTitle(entry) });
  }
  return pills;
}

function invoiceHitsOf(entry: LedgerEntry): string | null {
  return entry.invoice_item_hits?.length ? `發票：${entry.invoice_item_hits.join('、')}` : null;
}

function timeOf(entry: Pick<LedgerEntry, 'entry_time'>): string | null {
  return entry.entry_time?.slice(0, 5) ?? null;
}

function plainRow(entry: LedgerEntry): TimelineRow {
  const amount = Number(entry.amount);
  const tone = toneOf(amount, entry.kind);
  return {
    key: `e${entry.id}`,
    entryId: entry.id,
    memberIds: [entry.id],
    icon: iconOf(entry),
    color: colorOf(entry),
    title: displayTitle(entry),
    sub: subLine(entry),
    amountText: tone === 'neutral' ? formatMoney(Math.abs(amount), entry.currency) : formatSigned(amount, entry.currency),
    tone,
    fx: fxLine(entry),
    pills: pillsOf(entry),
    groupCount: null,
    folder: null,
    invoiceHits: invoiceHitsOf(entry),
    time: timeOf(entry),
  };
}

/** Group the newest-first entries by day; one row per split group, per transfer pair and per reward batch (#304). */
export function buildDays(
  entries: LedgerEntry[],
  mainCurrency: string,
  hideRewards: boolean,
  foldBatches = true,
): TimelineDay[] {
  const transferLegs = new Map<string, LedgerEntry[]>();
  // #304: a filter that can drop some of a batch's rows turns folding off, so a row's and the day's totals stay true.
  const batches = foldBatches ? loadedBatches(hideRewards ? entries.filter(entry => entry.kind !== 'reward') : entries) : new Map<string, LedgerEntry[]>();
  // Only the members loaded so far: the folder draws these and counts the rest of `group.count` as `+N`.
  const splitIcons = new Map<number, FolderIcon[]>();
  const groupMembers = new Map<number, number[]>();
  for (const entry of entries) {
    if (entry.group) {
      groupMembers.set(entry.group.id, [...(groupMembers.get(entry.group.id) ?? []), entry.id]);
    }
    if (entry.transfer_group_id && TRANSFER_KINDS.has(entry.kind)) {
      transferLegs.set(entry.transfer_group_id, [...(transferLegs.get(entry.transfer_group_id) ?? []), entry]);
    }
    if (entry.group?.kind === 'split') {
      splitIcons.set(entry.group.id, [...(splitIcons.get(entry.group.id) ?? []), { icon: iconOf(entry), color: colorOf(entry) }]);
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
    let unrated = false;
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
        memberIds: groupMembers.get(entry.group.id) ?? [entry.id],
        icon: iconOf(entry),
        color: colorOf(entry),
        title: entry.group.name?.trim() || (entry.group.kind === 'split' ? '多類別' : displayTitle(entry)),
        sub: subLine(entry),
        amountText: total === null ? '—' : formatSigned(total, entry.group.currency),
        tone: total === null || total === 0 ? 'neutral' : total < 0 ? 'out' : 'in',
        fx: null,
        pills: pillsOf(entry).filter(pill => pill.tone !== 'rv'),
        groupCount: entry.group.count,
        folder: entry.group.kind === 'split' ? { icons: splitIcons.get(entry.group.id) ?? [], count: entry.group.count } : null,
        invoiceHits: invoiceHitsOf(entry),
        time: timeOf(entry),
      };
      net = total ?? 0;
      netCurrency = entry.group.currency;
      unrated = total === null;
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
        memberIds: [out.id],
        icon: out.category_icon ?? defaultCategoryIcon(null, 'transfer_out'),
        color: colorOf(out),
        title: out.name?.trim() || '轉帳',
        sub: incoming && incoming !== out ? `${out.account_name} → ${incoming.account_name}` : out.account_name,
        amountText: formatMoney(Math.abs(Number(out.amount)), out.currency),
        tone: 'neutral',
        fx: fxLine(out),
        pills: [...schedulePills(out), { label: '轉帳', tone: '' }],
        groupCount: null,
        folder: null,
        invoiceHits: invoiceHitsOf(entry),
        time: timeOf(out),
      };
    } else if (batches.has(batchKey(entry) ?? '')) {
      // #304: one row per engine reward batch, at its newest loaded member; the server's count / total cover it all.
      const key = batchKey(entry)!;
      if (seen.has(key)) {
        continue;
      }
      seen.add(key);
      const batch = entry.reward_batch!;
      const members = batches.get(key)!;
      const total = Number(batch.total);
      const kindPill = KIND_PILLS[entry.kind];
      row = {
        key,
        entryId: entry.id,
        memberIds: members.map(member => member.id),
        icon: iconOf(entry),
        color: colorOf(entry),
        title: batch.rule_name,
        sub: batchSub(batch),
        amountText: formatSigned(total, batch.currency),
        tone: total === 0 ? 'neutral' : total < 0 ? 'out' : 'in',
        fx: null,
        pills: [...(kindPill ? [kindPill] : []), { label: entry.account_name, tone: '' }],
        groupCount: null,
        folder: null,
        invoiceHits: null,
        time: timeOf(entry),
        batch: { members: members.map(member => ({ ...plainRow(member), nested: true })) },
      };
      net = total;
      netCurrency = batch.currency;
    } else {
      row = plainRow(entry);
      net = TRANSFER_KINDS.has(entry.kind) ? 0 : Number(entry.amount);
    }

    let day = days.get(entry.entry_date);
    if (!day) {
      day = { date: entry.entry_date, label: dayLabel(entry.entry_date), net: 0, excluded: false, rows: [] };
      days.set(entry.entry_date, day);
    }
    day.rows.push(row);
    if (unrated) {
      day.excluded = true;
    } else if (netCurrency === mainCurrency) {
      day.net += net;
    } else if (net !== 0) {
      day.excluded = true;
    }
  }
  return [...days.values()];
}

/** The 專案 select's value for 「no project」 (#261); the API takes it as `no_project=true`. */
export const NO_PROJECT = 'none';
export const NO_PROJECT_LABEL = '無專案';
export type ProjectFilter = number | typeof NO_PROJECT | null;

/** `/accounting` home (spec "Timeline home page"); also the left pane of the wide layout. */
@Component({
  selector: 'app-ledger-timeline',
  standalone: true,
  imports: [PageHeaderComponent, CalendarMonthComponent, NgTemplateOutlet, SkeletonComponent, SplitFolderComponent, FilterBarComponent],
  templateUrl: './timeline.html',
  styleUrls: ['../entry-row.scss', './timeline.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class LedgerTimelineComponent implements OnInit {
  /** True when the layout renders the page header above the list card. */
  readonly embedded = input(false);
  private readonly accounting = inject(AccountingService);
  private readonly router = inject(Router);
  protected readonly layoutMode = inject(LayoutModeService);
  private readonly calendarData = inject(CalendarDataService);
  private readonly layout = inject(AccountingLayoutComponent, { optional: true });
  private readonly document = inject(DOCUMENT);
  private requestId = 0;
  private summaryRequestId = 0;
  private dailyRequestId = 0;
  private dayRequestId = 0;
  /** An invoice whose issue day was just selected; resolved when that day's entries arrive (`openInvoice`). */
  private pendingInvoice: InboxEinvoice | null = null;
  /** Filters of the rows on screen; a reset load for another key clears them (skeleton), the same key keeps them. */
  private listKey: string | null = null;
  private dayKey: string | null = null;

  private queryTimer: ReturnType<typeof setTimeout> | null = null;
  private readonly queryInput = viewChild<ElementRef<HTMLInputElement>>('queryInput');

  private readonly bills = inject(BillingService);
  private readonly calendar = inject(BusinessCalendarService);
  private readonly inbox = inject(InboxService);
  protected readonly overview = inject(AccountingOverviewService);
  private readonly pane = inject(CalendarPaneService);

  readonly preference = signal<Preference | null>(null);
  readonly accounts = signal<LedgerAccount[]>([]);
  /** For the 🔔 count only (counterparties with an open amount). */
  private readonly counterparties = signal<Counterparty[]>([]);
  private counterpartyRequestId = 0;
  /** For the 🔔 count only: the 待完成交易 queue (`queue=true`). */
  private readonly queue = signal<ScheduleInstance[]>([]);
  /** Not reset on a reload (the old queue stays shown): 'loading' only until the first answer. */
  private readonly queueState = signal<OverviewState>('loading');
  private queueRequestId = 0;
  /** For the 🔔 count only: reconciliation inbox cards waiting for the owner's answer (`counts.ask`). */
  private readonly inboxAsk = signal(0);
  private inboxRequestId = 0;
  readonly month = signal(currentMonth());
  readonly summary = signal<MonthSummary | null>(null);
  readonly summaryLoading = signal(true);
  readonly summaryError = signal(false);
  /** 支出類別分佈 legend: top five roots + 其他 + 退款 of the full month (filters ignored, like the totals); [] → no bar. */
  readonly distribution = computed(() => categoryDistribution(this.summary()));
  /** The bar draws every legend line except the legend-only 退款. */
  readonly distributionBar = computed(() => barSegments(this.distribution()));
  readonly distributionAria = computed(() => distributionAriaText(this.distribution()));

  readonly entries = signal<LedgerEntry[]>([]);
  /** `total` of the current filters' `/entries` page; null while that page is in flight or after it failed. */
  readonly total = signal<number | null>(null);
  readonly loading = signal(true);
  readonly loadError = signal(false);
  readonly accountFilter = signal<number | null>(null);
  readonly kindFilter = signal<EntryKind | null>(null);
  readonly query = signal('');
  /** Attribute filters (2026-10-21): category (sub-categories included server-side), project, tags (comma-separated),
   *  merchant substring and a signed amount range; all ANDed with the filters above. */
  readonly categoryFilter = signal<number | null>(null);
  /** A project id, or `NO_PROJECT` for the rows without one (#261: the set the owner backfills project tags on). */
  readonly projectFilter = signal<ProjectFilter>(null);
  readonly tagFilter = signal('');
  readonly merchantFilter = signal('');
  readonly amountMin = signal('');
  readonly amountMax = signal('');
  /** Category / project choices, loaded when the 篩選 panel opens (focus is kept as a fallback), not on init. */
  readonly categoryOptions = signal<CategoryNode[]>([]);
  /** Category id → colour, from the raw category trees (the calendar's dots). */
  private readonly categoryColors = signal<ReadonlyMap<number, string>>(new Map());
  readonly projectOptions = signal<Project[]>([]);
  private optionsRequested = false;
  private readonly chosenView = signal<TimelineView>(initialView(untracked(this.layoutMode.mode) === 'panes'));
  /** The day in the URL (`?day=`); null when none is selected. */
  private readonly urlDay = signal<string | null>(null);
  /** A day in the URL forces 日曆 without touching the stored choice. */
  readonly view = computed<TimelineView>(() => (this.urlDay() !== null ? 'calendar' : this.chosenView()));
  /** 日曆 at ≥ 1024 px: the month figures sit in the monthbar and the day's rows live in the right pane. */
  readonly wideCalendar = computed(() => this.view() === 'calendar' && this.layoutMode.mode() === 'panes');
  readonly daily = signal<DailySummary | null>(null);
  readonly dailyLoading = signal(false);
  readonly dailyError = signal(false);

  readonly selectedDay = computed(() => this.urlDay());
  readonly dayEntries = signal<LedgerEntry[]>([]);
  readonly dayLoading = signal(false);
  readonly dayError = signal(false);
  /** Up to three of today's entries, for the 今天 card (current month in 日曆 view only). */
  private readonly todayEntries = signal<LedgerEntry[]>([]);
  private todayRequestId = 0;
  readonly sentinel = viewChild<ElementRef<HTMLElement>>('sentinel');

  /** Any list filter set: the month totals are then labelled as unfiltered (spec §1.1). */
  readonly extraKey = computed(() =>
    [this.categoryFilter(), this.projectFilter(), this.tagFilter(), this.merchantFilter(), this.amountMin(), this.amountMax()].join('|'),
  );
  /** #304: a filter that may keep only some rows of a reward batch (an account or 回饋 kind filter keeps them all). */
  readonly batchCutting = computed(
    () => this.query() !== '' || this.extraKey() !== '|||||' || (this.kindFilter() !== null && this.kindFilter() !== 'reward'),
  );
  readonly filtersActive = computed(
    () =>
      this.accountFilter() !== null ||
      this.kindFilter() !== null ||
      this.query() !== '' ||
      this.extraKey() !== '|||||',
  );
  readonly isPhone = computed(() => this.layoutMode.mode() === 'phone');
  readonly mainCurrency = computed(() => this.preference()?.main_currency ?? DEFAULT_PREFERENCE.main_currency);
  readonly hideRewards = computed(() => this.preference()?.hide_rewards_on_timeline ?? false);
  readonly days = computed(() => buildDays(this.entries(), this.mainCurrency(), this.hideRewards(), !this.batchCutting()));
  readonly hasMore = computed(() => this.entries().length < (this.total() ?? 0));
  readonly selectedId = computed(() => this.layout?.selectedEntryId() ?? null);

  /** #304: keys of the reward batch rows expanded in place (a session-only view state). */
  readonly expandedBatches = signal<ReadonlySet<string>>(new Set());

  isExpanded(row: TimelineRow): boolean {
    return this.expandedBatches().has(row.key);
  }

  toggleBatch(key: string): void {
    const next = new Set(this.expandedBatches());
    if (!next.delete(key)) {
      next.add(key);
    }
    this.expandedBatches.set(next);
  }

  /** The routed entry is this row's entry or, for a group row, any of its members on this page. */
  isCurrent(row: TimelineRow): boolean {
    const id = this.selectedId();
    return id !== null && row.memberIds.includes(id);
  }
  readonly monthText = computed(() => monthLabel(this.month()));
  readonly weekStart = computed(() => this.preference()?.week_start ?? DEFAULT_PREFERENCE.week_start);
  /** Outlined in the 日曆 grid; refreshed on a month change and when the tab becomes visible (past midnight). */
  readonly today = signal(todayIso());
  /** The selected day's rows as the list groups them (`buildDays`), so filters apply to rows and net alike. */
  private readonly dayGroup = computed(
    () => buildDays(this.dayEntries(), this.mainCurrency(), this.hideRewards(), !this.batchCutting())[0] ?? null,
  );
  readonly dayRows = computed(() => this.dayGroup()?.rows ?? []);
  /** Net of the rows shown (main-currency rows only, the list's per-day rule); the grid keeps the summary figures. */
  readonly dayNet = computed<DayNet | null>(() => dayNetOf(this.dayGroup(), this.mainCurrency()));
  /** The wide calendar's day panel: the same day without batch folding (one row per entry; the list keeps folding). */
  private readonly paneDayGroup = computed(
    () => buildDays(this.dayEntries(), this.mainCurrency(), this.hideRewards(), false)[0] ?? null,
  );
  readonly selectedDayText = computed(() => {
    const date = this.selectedDay();
    return date ? dayLabel(date) : '';
  });
  /** Credit-card closing / due days of the shown month (empty without cards that have a closing day). */
  private readonly monthBilling = computed(() => billingEvents(this.accounts(), this.month(), this.calendar.calendar()));
  /** Due days from today through today + 6 (may reach into next month). */
  private readonly upcomingEvents = computed(() => upcomingDues(this.accounts(), this.today(), this.calendar.calendar()));
  /** Each card's current statement as the reminder centre lists it (for the 🔔 count). */
  private readonly reminderEvents = computed(() => reminderDues(this.accounts(), this.today(), this.calendar.calendar()));
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
  /** The overview pane's three due rows: dues with a remaining statement (45-day horizon) + the pending queue. */
  readonly dueItems = computed(() =>
    nextDueItems(
      this.reminderEvents().flatMap(event => {
        const bill = this.openBill(event);
        return bill
          ? [{ key: billKey(event), name: event.name, date: event.date, payDate: payDateOf(event), amountText: formatMoney(bill.statement, bill.currency) }]
          : [];
      }),
      this.queue(),
      this.today(),
    ),
  );
  /** The overview's due list: loading until the queue answered and every tracked statement settled; error if either failed. */
  readonly dueState = computed<OverviewState>(() => {
    const events = this.reminderEvents().filter(event => event.kind === 'due');
    if (this.queueState() === 'loading' || events.some(event => !this.bills.isSettled(event))) {
      return 'loading';
    }
    return this.queueState() === 'error' || events.some(event => this.bills.failed(event)) ? 'error' : 'ready';
  });
  /** A due event's statement when the grid marks it: resolved and positive (open or fully paid); a zero or refund-heavy one is not. */
  private markedBill(event: BillingEvent): BillState | null {
    const bill = this.bills.bill(event);
    return bill !== null && bill.statement > 0 ? bill : null;
  }
  /** What the grid marks: every closing day, and the due days of statements worth a mark (open or fully paid). */
  readonly billing = computed(() => this.monthBilling().filter(event => event.kind === 'closing' || this.markedBill(event) !== null));
  /**
   * Due days whose marked statements are all paid in full: the grid reads 已繳 there, 帳單 otherwise. A due event whose
   * statement is still loading or failed is unknown, and unknown is never 已繳 — it keeps its day at 帳單.
   */
  readonly paidDays = computed<ReadonlySet<string>>(() => {
    const open = new Set<string>();
    const marked = new Set<string>();
    for (const event of this.monthBilling()) {
      if (event.kind !== 'due') {
        continue;
      }
      const bill = this.bills.bill(event);
      if (bill === null) {
        open.add(event.date);
      } else if (bill.statement > 0) {
        marked.add(event.date);
        if (bill.remaining > 0) {
          open.add(event.date);
        }
      }
    }
    return new Set([...marked].filter(date => !open.has(date)));
  });
  /** The month's category dots as colours, in the service's order; an unknown category falls back to the muted colour. */
  readonly calendarDots = computed<ReadonlyMap<string, readonly string[]>>(() => {
    const colors = this.categoryColors();
    return new Map(
      [...this.calendarData.data().dots].map(([date, splits]) => [date, splits.map(split => colors.get(Number(split.key)) ?? FALLBACK_COLOR)]),
    );
  });
  readonly invoiceDays = computed(() => this.calendarData.data().invoiceDays);
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
  /** 🔔 count (`countReminders`, shared with the nav badge), including 待處理 inbox cards to decide. */
  readonly reminderCount = computed(() =>
    countReminders({
      dues: this.reminderEvents(),
      bill: event => this.bills.bill(event),
      counterparties: this.counterparties(),
      queue: this.queue(),
      today: this.today(),
      inboxAsk: this.inboxAsk(),
    }),
  );
  /** The list view's 近 7 天 banner: upcoming due days with a remaining balance. */
  readonly upcomingBills = computed(() => this.upcomingEvents().filter(event => this.openBill(event) !== null));
  readonly missingRates = computed(() =>
    (this.view() === 'calendar' ? this.daily()?.missing_rates : this.summary()?.missing_rates) ?? [],
  );
  readonly kindOptions = TIMELINE_FILTER_KINDS.map(kind => ({ kind, label: ENTRY_KIND_LABELS[kind] }));
  readonly formatSigned = formatSigned;
  protected readonly NO_PROJECT = NO_PROJECT;

  constructor() {
    effect(() => this.overview.month.set(this.month()));
    effect(() => this.overview.summary.set(this.summary()));
    effect(() => this.overview.dueItems.set(this.dueItems()));
    effect(() => this.overview.summaryState.set(this.summaryLoading() ? 'loading' : this.summaryError() ? 'error' : 'ready'));
    effect(() => this.overview.dueState.set(this.dueState()));
    inject(DestroyRef).onDestroy(() => this.overview.clear());
    effect(() => this.calendarData.month.set(this.view() === 'calendar' ? this.month() : null));
    effect(() => {
      if (this.view() === 'calendar') {
        this.ensureOptions();
      }
    });
    inject(DestroyRef).onDestroy(() => this.calendarData.month.set(null));
    this.publishToPane();
    // 報表 drill-through (add-accounting-reports D7): `/accounting?month=2026-10&category_id=12` opens that month
    // filtered to the category. Read once at construction: the list is created afresh when the URL comes from 報表.
    const query = this.router.routerState.snapshot.root.queryParamMap;
    this.syncFromUrl();
    this.router.events
      .pipe(
        filter(event => event instanceof NavigationEnd),
        takeUntilDestroyed(inject(DestroyRef)),
      )
      .subscribe(() => {
        // A pane route (/accounting/entry, …/reminders) carries no calendar params: keep month/day until the list URL
        // (or any URL carrying them) comes back.
        const url = this.router.url;
        const path = url.split(/[?#]/)[0];
        const query = this.router.routerState.snapshot.root.queryParamMap;
        if (path !== '/accounting' && !query.has('month') && !query.has('day')) {
          return;
        }
        this.syncFromUrl();
      });
    const category = query.get('category_id');
    if (category && /^\d+$/.test(category)) {
      this.categoryFilter.set(Number(category));
      this.ensureOptions(); // so the chip can name the category
    }
    // 排行 rows: the same link filtered to a merchant, a tag or an account.
    const account = query.get('account_id');
    if (account && /^\d+$/.test(account)) {
      this.accountFilter.set(Number(account));
    }
    this.merchantFilter.set(query.get('merchant')?.trim() ?? '');
    this.tagFilter.set(query.get('tag')?.trim() ?? '');
    // 報表 專案與預算: the 無專案 line links here with `no_project=1` (#261).
    if (['1', 'true'].includes(query.get('no_project') ?? '')) {
      this.projectFilter.set(NO_PROJECT);
    }
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
      this.extraKey();
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
      this.extraKey();
      this.accounting.entriesChanged();
      untracked(() => (view === 'calendar' && day ? this.loadDay(day) : this.dropDayEntries()));
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
        this.loadInboxCount();
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

  /** What the 日曆 view's right pane reads: the timeline owns every read and publishes the results. */
  private publishToPane(): void {
    const pane = this.pane;
    effect(() => this.overview.calendar.set(this.view() === 'calendar'));
    effect(() => pane.month.set(this.month()));
    effect(() => pane.today.set(this.today()));
    effect(() => pane.selectedDay.set(this.selectedDay()));
    effect(() => pane.dayRows.set(this.paneDayGroup()?.rows ?? []));
    effect(() => pane.dayNet.set(dayNetOf(this.paneDayGroup(), this.mainCurrency())));
    effect(() => pane.dayState.set(this.dayError() ? 'error' : this.dayLoading() && this.dayEntries().length === 0 ? 'loading' : 'ready'));
    effect(() => pane.billLines.set(this.billLines()));
    effect(() => pane.invoices.set(this.calendarData.data().invoices));
    effect(() => this.calendarData.wide.set(this.layoutMode.mode() === 'panes'));
    effect(() => pane.merchants.set(this.calendarData.data().merchants));
    effect(() => pane.merchantsState.set(this.calendarData.data().merchantsState));
    effect(() => pane.search.set(this.query()));
    effect(() => pane.monthDues.set(this.monthDues()));
    effect(() => pane.todayCard.set(this.todayCard()));
    // The 今天 card's three titles: only in 日曆 view of the current month, and only where the right pane exists.
    effect(() => {
      if (!this.preference()) {
        return;
      }
      const show = this.layoutMode.mode() === 'panes' && this.view() === 'calendar' && this.month() === this.today().slice(0, 7);
      const today = this.today();
      this.hideRewards();
      this.accounting.entriesChanged();
      untracked(() => (show ? this.loadToday(today) : this.dropToday()));
    });
    pane.commands.set({
      stepEntry: () => undefined, // Task 8
      openInvoice: invoice => this.openInvoice(invoice),
      setQuery: text => {
        this.clearQueryTimer();
        this.setQuery(text);
      },
    });
    inject(DestroyRef).onDestroy(() => {
      this.pendingInvoice = null;
      pane.clear();
      this.overview.calendar.set(false);
    });
  }

  /** 這個月要處理: the shown month's open statements, plus (current month only) dues of the next 7 days that fall next month. */
  private readonly monthDues = computed<MonthDue[]>(() => {
    const events = this.monthBilling().filter(event => event.kind === 'due');
    if (this.month() === this.today().slice(0, 7)) {
      const last = monthRange(this.month()).to;
      events.push(...this.upcomingBills().filter(event => event.date > last));
    }
    const dues = new Map<string, MonthDue>();
    for (const event of events) {
      const bill = this.openBill(event);
      if (bill) {
        dues.set(billKey(event), {
          key: billKey(event),
          accountId: event.accountId,
          name: event.name,
          date: event.date,
          amountText: formatMoney(bill.statement, bill.currency),
        });
      }
    }
    return [...dues.values()].sort((a, b) => a.date.localeCompare(b.date));
  });

  private readonly todayCard = computed<TodayCard | null>(() => {
    const today = this.today();
    if (this.view() !== 'calendar' || this.month() !== today.slice(0, 7)) {
      return null;
    }
    const daily = this.daily();
    const day = daily?.days.find(item => item.date === today);
    const net = day ? Number(day.expense) + Number(day.income) : 0;
    return {
      date: today,
      count: day?.count ?? 0,
      names: this.todayEntries().map(displayTitle).join('、'),
      netText: formatSigned(net, daily?.currency ?? this.mainCurrency()),
      net,
    };
  });

  private loadToday(date: string): void {
    const id = ++this.todayRequestId;
    this.accounting
      .getAllEntries({ limit: 3, offset: 0, date_from: date, date_to: date, hide_rewards: this.hideRewards() })
      .subscribe({
        next: page => {
          if (id === this.todayRequestId) {
            this.todayEntries.set(page.items);
          }
        },
        error: () => {
          if (id === this.todayRequestId) {
            this.todayEntries.set([]);
          }
        },
      });
  }

  private dropToday(): void {
    ++this.todayRequestId;
    this.todayEntries.set([]);
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

  private filterKey(scope: string): string {
    return [scope, this.accountFilter(), this.kindFilter(), this.query(), this.hideRewards(), this.extraKey()].join('|');
  }

  /** The attribute filters as `/entries` query fields; absent fields are left out by `toParams`. */
  private extraQuery(): Pick<AllEntriesQuery, 'tag' | 'category_id' | 'project_id' | 'no_project' | 'merchant' | 'amount_min' | 'amount_max'> {
    const tags = parseTags(this.tagFilter()); // the entry form's rule: `#` stripped, split on space / , / ， / 、
    const category = this.categoryFilter();
    const project = this.projectFilter();
    return {
      tag: tags.length ? tags : undefined,
      category_id: category === null ? undefined : [category],
      project_id: project === null || project === NO_PROJECT ? undefined : project,
      no_project: project === NO_PROJECT ? true : undefined,
      merchant: this.merchantFilter() || null,
      amount_min: this.amountMin() || null,
      amount_max: this.amountMax() || null,
    };
  }

  load(reset: boolean): void {
    const id = ++this.requestId;
    const { from, to } = monthRange(this.month());
    const offset = reset ? 0 : this.entries().length;
    this.loading.set(true);
    this.loadError.set(false);
    if (reset) {
      // Another month / filter: the old rows are not this list, so the skeleton replaces them. The same key (a
      // refresh after an entry write or a preference save) keeps them until the new page lands.
      const key = this.filterKey(this.month());
      if (key !== this.listKey) {
        this.entries.set([]);
        this.listKey = key;
      }
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
        include_invoice_items: this.query() ? true : undefined,
        account_id: account === null ? undefined : [account],
        hide_rewards: this.hideRewards(),
        ...this.extraQuery(),
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
          }
          this.total.set(null);
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
    this.summary.set(null);
    this.summaryLoading.set(true);
    this.summaryError.set(false);
    this.accounting.getMonthSummary(month).subscribe({
      next: summary => {
        if (id === this.summaryRequestId) {
          this.summary.set(summary);
          this.summaryLoading.set(false);
        }
      },
      error: () => {
        if (id === this.summaryRequestId) {
          this.summary.set(null);
          this.summaryError.set(true);
          this.summaryLoading.set(false);
        }
      },
    });
  }

  retrySummary(): void {
    this.loadSummary(this.month());
  }

  private clearList(): void {
    this.listKey = null;
    ++this.requestId;
    this.entries.set([]);
    this.total.set(null);
    this.loading.set(false);
    this.loadError.set(false);
  }

  private loadDaily(month: string): void {
    const id = ++this.dailyRequestId;
    if (this.daily()?.month !== month) {
      this.daily.set(null);
    }
    this.dailyLoading.set(true);
    this.dailyError.set(false);
    this.accounting.getDailySummary(month).subscribe({
      next: daily => {
        if (id === this.dailyRequestId) {
          this.daily.set(daily);
          this.dailyLoading.set(false);
        }
      },
      error: () => {
        if (id === this.dailyRequestId) {
          this.daily.set(null);
          this.dailyError.set(true);
          this.dailyLoading.set(false);
        }
      },
    });
  }

  retryDaily(): void {
    this.loadDaily(this.month());
  }

  private dropDaily(): void {
    ++this.dailyRequestId;
    this.daily.set(null);
    this.dailyLoading.set(false);
    this.dailyError.set(false);
  }

  private loadDay(date: string): void {
    const id = ++this.dayRequestId;
    this.dayLoading.set(true);
    this.dayError.set(false);
    const key = this.filterKey(date);
    if (key !== this.dayKey) {
      this.dayEntries.set([]);
      this.dayKey = key;
    }
    const account = this.accountFilter();
    this.accounting
      .getAllEntries({
        limit: CALENDAR_DAY_LIMIT,
        offset: 0,
        date_from: date,
        date_to: date,
        kind: this.kindFilter(),
        q: this.query() || null,
        include_invoice_items: this.query() ? true : undefined,
        account_id: account === null ? undefined : [account],
        hide_rewards: this.hideRewards(),
        ...this.extraQuery(),
      })
      .subscribe({
        next: page => {
          if (id === this.dayRequestId) {
            this.dayEntries.set(page.items);
            this.dayLoading.set(false);
            this.settleInvoice(date, page.items);
          }
        },
        error: () => {
          if (id === this.dayRequestId) {
            this.pendingInvoice = null;
            this.dayEntries.set([]);
            this.dayError.set(true);
            this.dayLoading.set(false);
          }
        },
      });
  }

  /**
   * 這個月要處理's invoice line: select its issue day; once that day's entries arrive, open the invoice's only
   * candidate (an entry, or a split group through a member row of that day). Otherwise only the day stays selected.
   */
  private openInvoice(invoice: InboxEinvoice): void {
    const date = invoice.issue_date;
    const same = this.selectedDay() === date;
    this.pendingInvoice = invoice;
    this.writeCalendar(date.slice(0, 7), date);
    if (!same || this.dayLoading()) {
      // A read of the day is coming (or in flight): it settles the invoice.
      return;
    }
    if (this.dayError()) {
      // The day's read failed: retry it, so the pending invoice settles against that answer, not a later unrelated one.
      this.retryDay();
      return;
    }
    // The day is already on screen: no read follows, its entries are the ones loaded.
    this.settleInvoice(date, this.dayEntries());
  }

  /** Resolves the pending invoice against `date`'s entries; cleared in every path. */
  private settleInvoice(date: string, entries: LedgerEntry[]): void {
    const invoice = this.pendingInvoice;
    this.pendingInvoice = null;
    if (!invoice || invoice.issue_date !== date || invoice.candidates.length !== 1) {
      return;
    }
    const [candidate] = invoice.candidates;
    const extras = { queryParams: calendarParams(this.router.routerState.snapshot.root.queryParamMap) };
    if (candidate.kind === 'entry') {
      void this.router.navigate(['/accounting/entries', candidate.id], extras);
      return;
    }
    const member = entries.find(entry => entry.group?.id === candidate.id);
    if (member) {
      void this.router.navigate(groupCommands(member.id, null), extras);
    }
  }

  private dropDayEntries(): void {
    this.pendingInvoice = null;
    this.dayKey = null;
    ++this.dayRequestId;
    this.dayEntries.set([]);
    this.dayLoading.set(false);
    this.dayError.set(false);
  }

  retryDay(): void {
    const day = this.selectedDay();
    if (day) {
      this.loadDay(day);
    }
  }

  private loadQueue(): void {
    const id = ++this.queueRequestId;
    this.accounting.getScheduleInstances({ queue: true }).subscribe({
      next: items => {
        if (id === this.queueRequestId) {
          this.queue.set(items);
          this.queueState.set('ready');
        }
      },
      // The 🔔 is a hint (the reminder centre reports its own load errors); the overview's due list shows 讀取失敗.
      error: () => {
        if (id === this.queueRequestId) {
          this.queueState.set('error');
        }
      },
    });
  }

  /** `counts` are computed server-side whatever the limit, so one card is enough. */
  private loadInboxCount(): void {
    const id = ++this.inboxRequestId;
    this.inbox.get({ limit: 1 }).subscribe({
      next: data => {
        if (id === this.inboxRequestId) {
          this.inboxAsk.set(inboxBellCount(data.counts));
        }
      },
      // Swallowed like the queue: the reminder centre reports its own inbox load error.
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
    if (view === 'list' && this.urlDay() !== null) {
      this.clearDay();
    }
    if (view !== this.chosenView()) {
      this.chosenView.set(view);
      storeView(view);
    }
  }

  private syncFromUrl(): void {
    const { month, day } = readCalendarQuery(this.router.routerState.snapshot.root.queryParamMap);
    if (month !== null) {
      this.month.set(month);
    }
    this.urlDay.set(day);
  }

  /** Writes month / day into the URL (merged with the other query keys) and applies them at once. */
  private writeCalendar(month: string, day: string | null): void {
    void writeCalendarQuery(this.router, month, day);
    this.month.set(month);
    this.urlDay.set(day);
  }

  clearDay(): void {
    this.writeCalendar(this.month(), null);
  }

  /** ‹ › : the previous / next day, crossing month edges. */
  stepDay(delta: 1 | -1): void {
    const day = this.selectedDay();
    if (day === null) {
      return;
    }
    const next = addDays(day, delta);
    this.writeCalendar(next.slice(0, 7), next);
  }

  /** Tapping the selected day again closes it. */
  selectDay(date: string): void {
    if (date === this.selectedDay()) {
      this.clearDay();
    } else {
      this.writeCalendar(date.slice(0, 7), date);
    }
  }

  /** The 近 7 天 banner: 日曆 on the first upcoming due day (next month's when it falls there). */
  openUpcoming(): void {
    const first = this.upcomingBills()[0];
    if (!first) {
      return;
    }
    this.chosenView.set('calendar');
    storeView('calendar');
    this.writeCalendar(first.date.slice(0, 7), first.date);
  }

  openCard(accountId: number): void {
    void this.router.navigate(['/accounting/accounts', accountId]);
  }

  moveMonth(delta: number): void {
    this.pendingInvoice = null;
    this.today.set(todayIso());
    this.writeCalendar(shiftMonth(this.month(), delta), null);
  }

  setAccount(value: string): void {
    this.accountFilter.set(value ? Number(value) : null);
  }

  setKind(value: string): void {
    this.kindFilter.set(value ? (value as EntryKind) : null);
  }

  setCategory(value: string): void {
    this.categoryFilter.set(value ? Number(value) : null);
  }

  setProject(value: string): void {
    this.projectFilter.set(value === NO_PROJECT ? NO_PROJECT : value ? Number(value) : null);
  }

  setTag(value: string): void {
    this.tagFilter.set(value.trim());
  }

  setMerchant(value: string): void {
    this.merchantFilter.set(value.trim());
  }

  /** A bound is kept only when it is a number; a half-typed value never reaches the request. The inputs are bound to
   *  these signals, so a filter row that is closed and reopened on a phone shows what is still filtering. */
  setAmountMin(value: string): void {
    this.amountMin.set(normalizeAmountBound(value));
  }

  setAmountMax(value: string): void {
    this.amountMax.set(normalizeAmountBound(value));
  }

  /** Category and project choices are fetched once, when the owner first reaches for them. */
  ensureOptions(): void {
    if (this.optionsRequested) {
      return;
    }
    this.optionsRequested = true;
    forkJoin([this.accounting.getCategories('expense'), this.accounting.getCategories('income'), this.accounting.getProjects()]).subscribe({
      next: ([expense, income, projects]) => {
        this.categoryOptions.set(flattenCategories([...expense, ...income]));
        this.categoryColors.set(categoryColorMap([...expense, ...income]));
        this.projectOptions.set(projects.filter(project => !project.is_archived));
      },
      error: () => {
        this.optionsRequested = false;
      },
    });
  }

  setQuery(value: string): void {
    this.query.set(value.trim());
  }

  onQueryCompositionStart(): void {
    this.clearQueryTimer();
  }

  /** Keep IME candidates out of committed filters; compositionend schedules the final DOM value too. */
  onQueryInput(event: Event): void {
    this.clearQueryTimer();
    if ((event as InputEvent).isComposing) {
      return;
    }
    const value = (event.target as HTMLInputElement).value;
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
    this.categoryFilter.set(null);
    this.projectFilter.set(null);
    this.tagFilter.set('');
    this.merchantFilter.set('');
    this.amountMin.set('');
    this.amountMax.set('');
  }


  /** Secondary-filter chips: one per set category/project/etc. Account and search are never chips. */
  readonly filterChips = computed<FilterChip[]>(() => {
    const chips: FilterChip[] = [];
    const kind = this.kindFilter();
    if (kind !== null) {
      chips.push({ key: 'kind', label: `類型：${ENTRY_KIND_LABELS[kind]}` });
    }
    const category = this.categoryFilter();
    if (category !== null) {
      const name = this.categoryOptions().find(o => o.id === category)?.name;
      chips.push({ key: 'category', label: name ? `類別：${name}` : '類別' });
    }
    const project = this.projectFilter();
    if (project === NO_PROJECT) {
      chips.push({ key: 'project', label: `專案：${NO_PROJECT_LABEL}` });
    } else if (project !== null) {
      const name = this.projectOptions().find(o => o.id === project)?.name;
      chips.push({ key: 'project', label: name ? `專案：${name}` : '專案' });
    }
    if (this.tagFilter()) {
      chips.push({ key: 'tag', label: `標籤：${this.tagFilter()}` });
    }
    if (this.merchantFilter()) {
      chips.push({ key: 'merchant', label: `商家：${this.merchantFilter()}` });
    }
    const min = this.amountMin();
    const max = this.amountMax();
    if (min || max) {
      chips.push({ key: 'amount', label: min && max ? `金額：${min} – ${max}` : min ? `金額 ≥ ${min}` : `金額 ≤ ${max}` });
    }
    return chips;
  });

  removeFilter(key: string): void {
    switch (key) {
      case 'kind': this.setKind(''); break;
      case 'category': this.setCategory(''); break;
      case 'project': this.setProject(''); break;
      case 'tag': this.setTag(''); break;
      case 'merchant': this.setMerchant(''); break;
      case 'amount': this.setAmountMin(''); this.setAmountMax(''); break;
    }
  }

  resetSecondaryFilters(): void {
    for (const key of ['kind', 'category', 'project', 'tag', 'merchant', 'amount']) {
      this.removeFilter(key);
    }
  }

  /** Category / project choices load the first time the filter panel opens. */
  onFilterPanelOpen(open: boolean): void {
    if (open) {
      this.ensureOptions();
    }
  }

  /** The calendar view carries its month and day into an opened entry; the 清單 view drops the query. */
  private calendarExtras(): { queryParams?: Params } {
    return this.view() === 'calendar' ? { queryParams: calendarParams(this.router.routerState.snapshot.root.queryParamMap) } : {};
  }

  /**
   * A split row opens its 多類別 group view (search results included: they are these rows); any other row its detail.
   * The list counts as the history entry below the group view only while it is the page on screen (`listOpenState`).
   */
  open(row: TimelineRow): void {
    if (row.batch) {
      this.toggleBatch(row.key);
      return;
    }
    if (row.folder) {
      const state = listOpenState(this.router.url, '/accounting');
      void this.router.navigate(groupCommands(row.entryId, null), { ...(state ? { state } : {}), ...this.calendarExtras() });
      return;
    }
    const extras = this.calendarExtras();
    void this.router.navigate(['/accounting/entries', row.entryId], extras);
  }

  expenseText(summary: MonthSummary): string {
    return formatSigned(-Math.abs(Number(summary.expense)), summary.currency);
  }

  incomeText(summary: MonthSummary): string {
    return formatSigned(Math.abs(Number(summary.income)), summary.currency);
  }
}
