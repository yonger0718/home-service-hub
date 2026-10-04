import { ChangeDetectionStrategy, Component, computed, effect, inject, signal, untracked } from '@angular/core';
import { NgTemplateOutlet } from '@angular/common';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { Observable, catchError, forkJoin, of } from 'rxjs';

import { Counterparty, LedgerAccount, LedgerEntry, ScheduleInstance } from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { AccountingToastService, scheduleActionError } from '../accounting-toast';
import { isHandledKey } from '../accounting-ui';
import { BillingEvent, dueCountdown, reminderDues } from '../billing/billing-math';
import { BillState, BillingService } from '../billing/billing.service';
import { daysBetween, shortDate, slashDate, todayIso } from '../dates';
import { formatMoney, formatSigned } from '../format';
import { TimelineRow, buildDays } from '../timeline/timeline';
import { QueueRow, queueRows } from './schedule-queue';

/** Open receivables / payables read for the counts and dates, and per expanded counterparty. */
export const REMINDER_ENTRY_LIMIT = 500;

/** A timeline row with the reminder centre's optional remaining slot. */
export type ReminderEntryRow = TimelineRow & { remaining: DebtRemaining | null };

export type ReminderTab = 'all' | 'cards' | 'debts' | 'pending';

export const REMINDER_TABS: { key: ReminderTab; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'cards', label: '信用卡帳單' },
  { key: 'debts', label: '借還款追蹤' },
  { key: 'pending', label: '待完成交易' },
];

export function isReminderTab(value: string | null): value is ReminderTab {
  return REMINDER_TABS.some(option => option.key === value);
}

export interface CardReminder {
  accountId: number;
  name: string;
  icon: string;
  due: string;
  dueText: string;
  figures: { label: string; text: string }[];
  countdown: string;
  /** Past the due date (negative tone) / due today or tomorrow (warning tone). */
  overdue: boolean;
  soon: boolean;
}

/** One side of a counterparty: what it owes the owner (應收) or what the owner owes it (應付). */
export interface DebtSide {
  kind: 'receivable' | 'payable';
  /** `應收 +$220 · +¥5,000` / `應付 −$100`, per currency. */
  totalText: string;
  countText: string;
}

export interface DebtReminder {
  id: number;
  name: string;
  /** 應收 then 應付; a side with nothing open is left out. */
  sides: DebtSide[];
}

/** The 借還款追蹤 filters: one 對象 and / or one side; null is 全部. */
export interface DebtFilter {
  counterpartyId: number | null;
  kind: DebtKind | null;
}

export type DebtKind = 'receivable' | 'payable';

export const DEBT_KIND_OPTIONS: { kind: DebtKind; label: string }[] = [
  { kind: 'receivable', label: '應收' },
  { kind: 'payable', label: '應付' },
];

/** The entries a 借還款追蹤 filter keeps (client-side, on the loaded rows). */
export function filterDebtEntries(entries: LedgerEntry[], filter: DebtFilter): LedgerEntry[] {
  return entries.filter(
    entry =>
      (filter.counterpartyId === null || entry.counterparty_id === filter.counterpartyId) &&
      (filter.kind === null || entry.kind === filter.kind),
  );
}

/** A debt original's remaining in the expanded rows (replaces the entry amount). */
export interface DebtRemaining {
  /** `+$170` / `−$100` (signed remaining) / `已結清`. */
  text: string;
  tone: 'in' | 'out' | 'muted';
  /** The entry's date, then `原始 $420 · 已收 $250` once something came back: `2026/09/01 · 原始 $420 · 已收 $250`. */
  detail: string;
}

/** What is left on an open receivable / payable original; `open_amount` falls back to the whole amount. */
function openOf(entry: LedgerEntry): number {
  return Number(entry.open_amount ?? Math.abs(Number(entry.amount)));
}

export function debtRemaining(entry: LedgerEntry): DebtRemaining | null {
  if (entry.kind !== 'receivable' && entry.kind !== 'payable') {
    return null;
  }
  if (entry.is_closed) {
    return { text: '已結清', tone: 'muted', detail: slashDate(entry.entry_date) };
  }
  const receivable = entry.kind === 'receivable';
  const open = openOf(entry);
  const original = Math.abs(Number(entry.amount));
  const back = Math.round((original - open) * 1e4) / 1e4;
  return {
    text: formatSigned(receivable ? open : -open, entry.currency),
    tone: receivable ? 'in' : 'out',
    detail:
      back > 0
        ? `${slashDate(entry.entry_date)} · 原始 ${formatMoney(original, entry.currency)} · ${receivable ? '已收' : '已還'} ${formatMoney(back, entry.currency)}`
        : slashDate(entry.entry_date),
  };
}

/** Cards with something left to pay on their current statement, soonest due first. */
export function cardReminders(
  bills: BillState[],
  accounts: Pick<LedgerAccount, 'id' | 'icon'>[],
  today: string,
): CardReminder[] {
  const icons = new Map(accounts.map(account => [account.id, account.icon]));
  return bills
    .filter(bill => bill.remaining > 0)
    .sort((a, b) => a.due.localeCompare(b.due) || a.accountId - b.accountId)
    .map(bill => {
      const days = daysBetween(today, bill.due);
      return {
        accountId: bill.accountId,
        name: bill.name,
        icon: icons.get(bill.accountId) ?? '💳',
        due: bill.due,
        dueText: `${shortDate(bill.due)} 截止`,
        figures: [
          { label: '應繳', text: formatMoney(bill.statement, bill.currency) },
          { label: '已繳', text: formatMoney(bill.paid, bill.currency) },
          { label: '剩餘', text: formatMoney(bill.remaining, bill.currency) },
        ],
        countdown: dueCountdown(bill.due, today),
        overdue: days < 0,
        soon: days === 0 || days === 1,
      };
    });
}

/**
 * Counterparties with open receivable / payable rows (`GET /entries?open=true`), by name: 應收 and 應付 each
 * summed from the rows' `open_amount` per currency, so the two sides never net each other out.
 */
export function debtReminders(counterparties: Counterparty[], openEntries: LedgerEntry[]): DebtReminder[] {
  const names = new Map(counterparties.map(counterparty => [counterparty.id, counterparty.name]));
  const byCounterparty = new Map<number, LedgerEntry[]>();
  for (const entry of openEntries) {
    if (entry.counterparty_id !== null && (entry.kind === 'receivable' || entry.kind === 'payable')) {
      byCounterparty.set(entry.counterparty_id, [...(byCounterparty.get(entry.counterparty_id) ?? []), entry]);
    }
  }
  const rows: DebtReminder[] = [];
  for (const [id, entries] of byCounterparty) {
    const sides: DebtSide[] = [];
    for (const kind of ['receivable', 'payable'] as const) {
      const ofKind = entries.filter(entry => entry.kind === kind);
      if (ofKind.length === 0) {
        continue;
      }
      const totals = new Map<string, number>();
      for (const entry of ofKind) {
        totals.set(entry.currency, (totals.get(entry.currency) ?? 0) + openOf(entry));
      }
      const sign = kind === 'receivable' ? 1 : -1;
      const texts = [...totals].map(([currency, total]) => formatSigned(sign * total, currency));
      sides.push({
        kind,
        totalText: `${kind === 'receivable' ? '應收' : '應付'} ${texts.join(' · ')}`,
        countText: `${ofKind.length} 筆${kind === 'receivable' ? '應收' : '應付'}款項`,
      });
    }
    rows.push({ id, name: names.get(id) ?? entries[0].counterparty ?? '', sides });
  }
  return rows.sort((a, b) => a.name.localeCompare(b.name) || a.id - b.id);
}

/** `/accounting/reminders` (提醒中心): card statements, open debts, 待完成交易 and (Task 24) 週期／分期. */
@Component({
  selector: 'app-accounting-reminders',
  standalone: true,
  imports: [RouterLink, NgTemplateOutlet],
  templateUrl: './reminders.html',
  styleUrls: ['../filters.scss', '../entry-row.scss', './reminders.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '(keydown)': 'onKeydown($event)' },
})
export class AccountingRemindersComponent {
  private readonly accounting = inject(AccountingService);
  private readonly bills = inject(BillingService);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);
  private readonly layoutMode = inject(LayoutModeService);
  private readonly toast = inject(AccountingToastService);
  private requestId = 0;
  private expandRequestId = 0;

  readonly tabs = REMINDER_TABS;
  readonly tab = signal<ReminderTab>('all');
  readonly accounts = signal<LedgerAccount[]>([]);
  readonly counterparties = signal<Counterparty[]>([]);
  readonly openEntries = signal<LedgerEntry[]>([]);
  readonly queue = signal<ScheduleInstance[]>([]);
  readonly queueFailed = signal(false);
  readonly loaded = signal(false);
  readonly loadError = signal(false);
  readonly today = signal(todayIso());
  readonly expanded = signal<number | null>(null);
  readonly expandedEntries = signal<LedgerEntry[] | null>(null);
  readonly expandError = signal(false);
  /** The 待完成交易 item whose inline 略過這一期？剩餘不變 prompt is open. */
  readonly confirmingSkip = signal<number | null>(null);
  readonly busyInstance = signal<number | null>(null);
  readonly actionError = signal<string | null>(null);
  /** 借還款追蹤 filters: kept while the page is open, applied on that tab only. */
  readonly debtCounterparty = signal<number | null>(null);
  readonly debtKind = signal<DebtKind | null>(null);
  readonly debtKindOptions = DEBT_KIND_OPTIONS;

  readonly isPhone = computed(() => this.layoutMode.mode() === 'phone');
  private readonly dues = computed(() => reminderDues(this.accounts(), this.today()));
  readonly cardRows = computed(() =>
    cardReminders(
      this.dues()
        .map(event => this.bills.bill(event))
        .filter((bill): bill is BillState => bill !== null),
      this.accounts(),
      this.today(),
    ),
  );
  /** Every 對象 with open rows, unfiltered: the 對象 select's options. */
  readonly debtCounterparties = computed(() => debtReminders(this.counterparties(), this.openEntries()));
  readonly showDebtFilters = computed(() => this.tab() === 'debts');
  private readonly debtFilter = computed<DebtFilter>(() =>
    this.showDebtFilters()
      ? { counterpartyId: this.debtCounterparty(), kind: this.debtKind() }
      : { counterpartyId: null, kind: null },
  );
  /** 對象 rows rebuilt from the filtered open rows, so their totals and counts follow the filters. */
  readonly debtRows = computed(() =>
    debtReminders(this.counterparties(), filterDebtEntries(this.openEntries(), this.debtFilter())),
  );
  /** Open rows exist, but none passes the filters. */
  readonly debtFilteredOut = computed(
    () => this.showDebtFilters() && this.debtRows().length === 0 && this.debtCounterparties().length > 0,
  );
  readonly queueGroups = computed(() => queueRows(this.queue(), this.today()));
  /** Cards whose statement read failed: shown as 帳單讀取失敗 with 重試, never as "nothing to pay". */
  private readonly failedDues = computed(() => this.dues().filter(event => this.bills.failed(event)));
  readonly billsFailed = computed(() => this.failedDues().length > 0);
  /** `帳單讀取失敗`, with the distinct reasons appended (`（幣別不同）`). */
  readonly billErrorText = computed(() => {
    const reasons = [
      ...new Set(this.failedDues().map(event => this.bills.failureReason(event)).filter((reason): reason is string => !!reason)),
    ];
    return reasons.length > 0 ? `帳單讀取失敗（${reasons.join('、')}）` : '帳單讀取失敗';
  });
  /** Every statement read at least once: the empty state waits for them. */
  private readonly billsSettled = computed(() => this.dues().every((event: BillingEvent) => this.bills.isSettled(event)));
  readonly showCards = computed(() => this.tab() === 'all' || this.tab() === 'cards');
  readonly showDebts = computed(() => this.tab() === 'all' || this.tab() === 'debts');
  readonly showQueue = computed(() => this.tab() === 'all' || this.tab() === 'pending');
  readonly empty = computed(() => {
    if (!this.loaded() || this.loadError()) {
      return false;
    }
    const groups = this.queueGroups();
    const cards = this.showCards() ? this.cardRows().length : 0;
    const debts = this.showDebts() ? this.debtCounterparties().length : 0;
    const queue = this.tab() === 'pending' ? groups.due.length + groups.upcoming.length : this.tab() === 'all' ? groups.due.length : 0;
    const failed = (this.showCards() && this.billsFailed()) || (this.showQueue() && this.queueFailed());
    return cards === 0 && debts === 0 && queue === 0 && !failed && (!this.showCards() || this.billsSettled());
  });
  /**
   * The expanded counterparty's open entries as timeline rows, one per entry (a split group's debt line is its own
   * row here), each with its signed remaining in the row's optional `remaining` slot.
   */
  readonly expandedRows = computed<ReminderEntryRow[]>(() => {
    const entries = filterDebtEntries(this.expandedEntries() ?? [], this.debtFilter());
    const byId = new Map(entries.map(entry => [entry.id, entry]));
    return buildDays(entries.map(entry => ({ ...entry, group: null })), 'TWD', false)
      .flatMap(day => day.rows)
      .map(row => {
        const entry = byId.get(row.entryId);
        return { ...row, remaining: entry ? debtRemaining(entry) : null };
      });
  });

  constructor() {
    this.route.queryParamMap.pipe(takeUntilDestroyed()).subscribe(params => {
      const tab = params.get('tab');
      this.tab.set(isReminderTab(tab) ? tab : 'all');
    });

    effect(() => {
      this.accounting.entriesChanged();
      this.accounting.accountsChanged();
      untracked(() => {
        this.today.set(todayIso());
        this.load();
        const open = this.expanded();
        if (open !== null) {
          this.loadExpanded(open);
        }
      });
    });

    effect(() => {
      const dues = this.dues();
      this.bills.generation();
      untracked(() => this.bills.ensure(dues, this.today()));
    });
  }

  private load(): void {
    const id = ++this.requestId;
    forkJoin({
      accounts: this.accounting.getAccounts(),
      counterparties: this.accounting.getCounterparties(),
      open: this.accounting.getAllEntries({ open: true, limit: REMINDER_ENTRY_LIMIT, offset: 0 }),
      queue: this.accounting.getScheduleInstances({ queue: true }).pipe(catchError(() => of(null))),
    }).subscribe({
      next: ({ accounts, counterparties, open, queue }) => {
        if (id !== this.requestId) {
          return;
        }
        this.accounts.set(accounts);
        this.counterparties.set(counterparties);
        this.openEntries.set(open.items);
        this.queue.set(queue ?? []);
        this.queueFailed.set(queue === null);
        this.loadError.set(false);
        this.loaded.set(true);
      },
      error: () => {
        if (id !== this.requestId) {
          return;
        }
        this.loadError.set(true);
        this.loaded.set(true);
      },
    });
  }

  private loadExpanded(counterpartyId: number): void {
    const id = ++this.expandRequestId;
    this.expandError.set(false);
    this.accounting
      .getAllEntries({ counterparty_id: counterpartyId, open: true, limit: REMINDER_ENTRY_LIMIT, offset: 0 })
      .subscribe({
        next: page => {
          if (id === this.expandRequestId) {
            this.expandedEntries.set(page.items);
          }
        },
        error: () => {
          if (id === this.expandRequestId) {
            this.expandedEntries.set([]);
            this.expandError.set(true);
          }
        },
      });
  }

  /** 重試: reads every failed statement again in a new round. */
  retryBills(): void {
    const today = this.today();
    for (const event of this.failedDues()) {
      this.bills.retry(event, today);
    }
  }

  setTab(tab: ReminderTab): void {
    this.tab.set(tab);
  }

  setDebtCounterparty(value: string): void {
    this.debtCounterparty.set(value ? Number(value) : null);
  }

  setDebtKind(value: string): void {
    this.debtKind.set(value === 'receivable' || value === 'payable' ? value : null);
  }

  /** Tapping a counterparty shows its open entries under it; tapping it again (or another) closes it. */
  toggleDebt(counterpartyId: number): void {
    if (this.expanded() === counterpartyId) {
      ++this.expandRequestId;
      this.expanded.set(null);
      this.expandedEntries.set(null);
      return;
    }
    this.expanded.set(counterpartyId);
    this.expandedEntries.set(null);
    this.loadExpanded(counterpartyId);
  }

  openCard(accountId: number): void {
    void this.router.navigate(['/accounting/accounts', accountId]);
  }

  /** The entry's detail; its ✕ (and a delete) comes back to the reminder centre. */
  open(row: Pick<TimelineRow, 'entryId'>): void {
    void this.router.navigate(['/accounting/entries', row.entryId], { state: { closeTo: 'reminders' } });
  }

  // ---- 待完成交易 -------------------------------------------------------------------------------------------

  /** One schedule action; on success the service bumps entriesChanged, which reloads the list and the 🔔. */
  private act(row: QueueRow, request: Observable<unknown>): void {
    this.busyInstance.set(row.id);
    this.actionError.set(null);
    request.subscribe({
      next: () => {
        this.busyInstance.set(null);
        this.confirmingSkip.set(null);
      },
      error: (error: unknown) => {
        this.busyInstance.set(null);
        this.actionError.set(scheduleActionError(error, this.toast));
      },
    });
  }

  post(row: QueueRow): void {
    this.act(row, this.accounting.postScheduleInstance(row.id));
  }

  askSkip(row: QueueRow): void {
    this.confirmingSkip.set(row.id);
  }

  skip(row: QueueRow): void {
    this.act(row, this.accounting.skipScheduleInstance(row.id));
  }

  catchUp(row: QueueRow): void {
    this.act(row, this.accounting.catchUpSchedule(row.definitionId));
  }

  repost(row: QueueRow): void {
    this.act(row, this.accounting.repostScheduleInstance(row.id, row.amounts));
  }

  acceptPartial(row: QueueRow): void {
    this.act(row, this.accounting.acceptPartialScheduleInstance(row.id));
  }

  /** Esc closes an open 略過 prompt first (marked handled so the layout's Esc does not also close the pane). */
  onKeydown(event: KeyboardEvent): void {
    if (isHandledKey(event)) {
      return;
    }
    if (event.key === 'Escape' && this.confirmingSkip() !== null) {
      event.preventDefault();
      this.confirmingSkip.set(null);
    }
  }
}
