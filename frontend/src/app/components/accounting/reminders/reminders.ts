import { ChangeDetectionStrategy, Component, computed, effect, inject, signal, untracked } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { forkJoin } from 'rxjs';

import { Counterparty, LedgerAccount, LedgerEntry } from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { BillingEvent, dueCountdown, reminderDues } from '../billing/billing-math';
import { BillState, BillingService } from '../billing/billing.service';
import { daysBetween, shortDate, slashDate, todayIso } from '../dates';
import { formatMoney, formatSigned } from '../format';
import { TimelineRow, buildDays } from '../timeline/timeline';

/** Open receivables / payables read for the counts and dates, and per expanded counterparty. */
export const REMINDER_ENTRY_LIMIT = 500;

export type ReminderTab = 'all' | 'cards' | 'debts';

export const REMINDER_TABS: { key: ReminderTab; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'cards', label: '信用卡帳單' },
  { key: 'debts', label: '借還款追蹤' },
];

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

export interface DebtReminder {
  id: number;
  name: string;
  totals: { key: string; text: string; tone: 'in' | 'out' }[];
  countText: string;
  /** Latest entry date among the open entries; null when none was read (e.g. beyond the read limit). */
  last: string | null;
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
 * Counterparties with a non-zero open amount, most recent open entry first. Totals are `open_amounts` (receivable
 * positive, payable negative); counts and the date come from the open entries.
 */
export function debtReminders(counterparties: Counterparty[], openEntries: LedgerEntry[]): DebtReminder[] {
  const rows = counterparties
    .filter(counterparty => counterparty.open_amounts.some(open => Number(open.amount) !== 0))
    .map(counterparty => {
      const entries = openEntries.filter(entry => entry.counterparty_id === counterparty.id);
      const receivable = entries.filter(entry => entry.kind === 'receivable').length;
      const payable = entries.filter(entry => entry.kind === 'payable').length;
      const counts = [
        receivable ? `${receivable} 筆應收款項` : null,
        payable ? `${payable} 筆應付款項` : null,
      ].filter(Boolean);
      const last = entries.reduce<string | null>((latest, entry) => (!latest || entry.entry_date > latest ? entry.entry_date : latest), null);
      return {
        id: counterparty.id,
        name: counterparty.name,
        totals: counterparty.open_amounts
          .filter(open => Number(open.amount) !== 0)
          .map(open => ({
            key: open.currency,
            text: formatSigned(open.amount, open.currency),
            tone: Number(open.amount) > 0 ? ('in' as const) : ('out' as const),
          })),
        countText: counts.join(' · '),
        last,
      };
    });
  return rows.sort((a, b) => (b.last ?? '').localeCompare(a.last ?? '') || a.name.localeCompare(b.name));
}

/** `/accounting/reminders` (提醒中心): unpaid card statements and open receivables / payables. */
@Component({
  selector: 'app-accounting-reminders',
  standalone: true,
  imports: [RouterLink],
  templateUrl: './reminders.html',
  styleUrls: ['../entry-row.scss', './reminders.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AccountingRemindersComponent {
  private readonly accounting = inject(AccountingService);
  private readonly bills = inject(BillingService);
  private readonly router = inject(Router);
  private readonly layoutMode = inject(LayoutModeService);
  private requestId = 0;
  private expandRequestId = 0;

  readonly tabs = REMINDER_TABS;
  readonly tab = signal<ReminderTab>('all');
  readonly accounts = signal<LedgerAccount[]>([]);
  readonly counterparties = signal<Counterparty[]>([]);
  readonly openEntries = signal<LedgerEntry[]>([]);
  readonly loaded = signal(false);
  readonly loadError = signal(false);
  readonly today = signal(todayIso());
  readonly expanded = signal<number | null>(null);
  readonly expandedEntries = signal<LedgerEntry[] | null>(null);
  readonly expandError = signal(false);

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
  readonly debtRows = computed(() => debtReminders(this.counterparties(), this.openEntries()));
  /** Every statement read at least once: the empty state waits for them. */
  private readonly billsSettled = computed(() => this.dues().every((event: BillingEvent) => this.bills.isSettled(event)));
  readonly showCards = computed(() => this.tab() !== 'debts');
  readonly showDebts = computed(() => this.tab() !== 'cards');
  readonly empty = computed(() => {
    if (!this.loaded() || this.loadError()) {
      return false;
    }
    const cards = this.showCards() ? this.cardRows().length : 0;
    const debts = this.showDebts() ? this.debtRows().length : 0;
    return cards === 0 && debts === 0 && (!this.showCards() || this.billsSettled());
  });
  readonly expandedRows = computed<TimelineRow[]>(() =>
    buildDays(this.expandedEntries() ?? [], 'TWD', false).flatMap(day => day.rows),
  );

  constructor() {
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
    }).subscribe({
      next: ({ accounts, counterparties, open }) => {
        if (id !== this.requestId) {
          return;
        }
        this.accounts.set(accounts);
        this.counterparties.set(counterparties);
        this.openEntries.set(open.items);
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

  setTab(tab: ReminderTab): void {
    this.tab.set(tab);
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

  open(row: TimelineRow): void {
    void this.router.navigate(['/accounting/entries', row.entryId]);
  }

  readonly slashDate = slashDate;
}
