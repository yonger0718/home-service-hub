import { ChangeDetectionStrategy, Component, computed, effect, inject, signal, untracked } from '@angular/core';
import { DatePipe, NgTemplateOutlet } from '@angular/common';
import { RouterLink } from '@angular/router';
import { Observable, catchError, forkJoin, map, of } from 'rxjs';
import { SkeletonComponent } from '../skeleton/skeleton';

interface ImportLookup { ok: boolean; v: ImportRun | null; }


import { ImportRun, LedgerAccount } from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import { currentDues, duePillText } from '../billing/billing-math';
import { BillState, BillingService } from '../billing/billing.service';
import { dueDate, statementPeriod } from '../cycle';
import { daysBetween, shortDate, todayIso } from '../dates';
import { formatMoney } from '../format';

/** A credit card row's third line: what is left on its current statement and when it is due. */
export interface CardDue {
  owedText: string;
  pill: string;
  overdue: boolean;
}

const STATUS_LABELS: Record<ImportRun['status'], string> = {
  running: '匯入中',
  succeeded: '成功',
  failed: '失敗',
  dry_run: '試算',
};

export interface AccountNode {
  account: LedgerAccount;
  children: LedgerAccount[];
  total: number;
  totalCurrency: string;
}

export interface AccountGroupView {
  key: string;
  name: string;
  /** Σ `balance_main` of the group's converted accounts; null when no account in the group has a rate. */
  subtotal: number | null;
  /** True when at least one account in the group has no main-currency rate (`balance_main` null). */
  partial: boolean;
  nodes: AccountNode[];
}

/** Numeric value for sums; a null `balance_main` (no rate) counts as 0, i.e. it is excluded. */
function value(amount: string | null | undefined): number {
  const parsed = Number(amount ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

/** Groups in API order; children of a 主帳戶 nest under it when the master is itself top-level. */
export function buildAccountGroups(accounts: LedgerAccount[], mainCurrency: string): AccountGroupView[] {
  const byId = new Map(accounts.map(account => [account.id, account]));
  const masterOf = (account: LedgerAccount): LedgerAccount | null => {
    const id = account.combined_account_id;
    if (id === null || id === undefined || id === account.id) {
      return null;
    }
    return byId.get(id) ?? null;
  };
  const parentOf = (account: LedgerAccount): LedgerAccount | null => {
    const master = masterOf(account);
    return master && masterOf(master) === null ? master : null;
  };

  const childrenOf = new Map<number, LedgerAccount[]>();
  for (const account of accounts) {
    const parent = parentOf(account);
    if (parent) {
      childrenOf.set(parent.id, [...(childrenOf.get(parent.id) ?? []), account]);
    }
  }

  const groups = new Map<string, AccountGroupView>();
  for (const account of accounts) {
    if (parentOf(account)) {
      continue;
    }
    const name = account.group_name ?? '未分組';
    let group = groups.get(name);
    if (!group) {
      group = { key: name, name, subtotal: null, partial: false, nodes: [] };
      groups.set(name, group);
    }
    const children = childrenOf.get(account.id) ?? [];
    const family = [account, ...children];
    const sameCurrency = children.every(child => child.currency === account.currency);
    const total = family.reduce((sum, member) => sum + value(sameCurrency ? member.balance : member.balance_main), 0);
    group.nodes.push({ account, children, total, totalCurrency: sameCurrency ? account.currency : mainCurrency });
    for (const member of family) {
      if (member.balance_main === null || member.balance_main === undefined) {
        group.partial = true;
      } else {
        group.subtotal = (group.subtotal ?? 0) + value(member.balance_main);
      }
    }
  }
  const ungrouped = groups.get('未分組');
  if (ungrouped) {
    groups.delete('未分組');
    groups.set('未分組', ungrouped);
  }
  return [...groups.values()];
}

@Component({
  selector: 'app-accounting-accounts',
  standalone: true,
  imports: [DatePipe, NgTemplateOutlet, RouterLink, SkeletonComponent],
  templateUrl: './accounts.html',
  styleUrl: './accounts.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AccountingAccountsComponent {
  private accountingService = inject(AccountingService);
  private readonly bills = inject(BillingService);
  /** Guards the page load (accounts + preference). */
  private accountsRequestId = 0;
  /** Guards the import lookup; an import 重試 bumps only this one (spec §1.3). */
  private importRequestId = 0;

  readonly accounts = signal<LedgerAccount[]>([]);
  readonly latestImport = signal<ImportRun | null>(null);
  readonly mainCurrency = signal('TWD');
  readonly loaded = signal(false);
  readonly loadError = signal(false);
  readonly importError = signal(false);
  readonly collapsed = signal<ReadonlySet<string>>(new Set());
  readonly archivedOpen = signal(false);
  readonly archived = signal<LedgerAccount[] | null>(null);
  /** Refreshed on every load, so a list left open past midnight moves its pills on the next reload. */
  readonly today = signal(todayIso());

  readonly groups = computed(() => buildAccountGroups(this.accounts(), this.mainCurrency()));
  private readonly included = computed(() => this.accounts().filter(account => account.include_in_total));
  readonly total = computed(() => this.included().reduce((sum, account) => sum + value(account.balance_main), 0));
  readonly assets = computed(() =>
    this.included().reduce((sum, account) => sum + Math.max(0, value(account.balance_main)), 0),
  );
  readonly liabilities = computed(() =>
    this.included().reduce((sum, account) => sum + Math.max(0, -value(account.balance_main)), 0),
  );
  readonly reviewCount = computed(() => {
    const summary = this.latestImport()?.summary as unknown as
      | { needs_review?: { count?: number }; unpaired_transfers?: unknown[] }
      | null
      | undefined;
    return summary?.needs_review?.count ?? summary?.unpaired_transfers?.length ?? 0;
  });
  readonly formatMoney = formatMoney;
  /** Each card's current statement (the same resolution as the calendar and the reminder centre). */
  private readonly dues = computed(() => currentDues(this.accounts(), this.today()));
  /** Per card id: the current statement when something is left to pay. */
  private readonly owed = computed(() => {
    const owed = new Map<number, BillState>();
    for (const event of this.dues()) {
      const bill = this.bills.bill(event);
      if (bill && bill.remaining > 0) {
        owed.set(event.accountId, bill);
      }
    }
    return owed;
  });

  constructor() {
    // The list stays beside the detail pane at ≥ 760 px, so it reloads after any account or entry write
    // (balances, names, groups, archive state), the same way the timeline reloads on `entriesChanged`.
    // A preference save reloads too: `balance_main` is computed server-side in the (possibly new) main currency,
    // and `getPreference()` then returns the saved value.
    effect(() => {
      this.accountingService.accountsChanged();
      this.accountingService.entriesChanged();
      this.accountingService.preferenceChanged();
      untracked(() => this.load());
    });

    effect(() => {
      const dues = this.dues();
      this.bills.generation();
      untracked(() => this.bills.ensure(dues, this.today()));
    });
  }

  private load(): void {
    const id = ++this.accountsRequestId;
    const importId = ++this.importRequestId;
    this.today.set(todayIso());
    forkJoin({
      accounts: this.accountingService.getAccounts(),
      latest: this.importLookup(),
      preference: this.accountingService.getPreference().pipe(catchError(() => of(null))),
    }).subscribe({
      next: ({ accounts, latest, preference }) => {
        if (importId === this.importRequestId) {
          this.applyImport(latest);
        }
        if (id !== this.accountsRequestId) {
          return;
        }
        this.accounts.set(accounts);
        this.mainCurrency.set(preference?.main_currency ?? 'TWD');
        this.loadError.set(false);
        this.loaded.set(true);
        if (this.archived() !== null) {
          this.archived.set(null);
          if (this.archivedOpen()) {
            this.loadArchived();
          }
        }
      },
      error: () => {
        if (id !== this.accountsRequestId) {
          return;
        }
        this.loadError.set(true);
        this.loaded.set(true);
      },
    });
  }

  /** `GET /imports/latest` that never fails the page: 404 → `{ok: true, v: null}`, any error → `{ok: false}`. */
  private importLookup(): Observable<ImportLookup> {
    return this.accountingService.getLatestImport().pipe(
      map((v): ImportLookup => ({ ok: true, v })),
      catchError(() => of<ImportLookup>({ ok: false, v: null })),
    );
  }

  private applyImport(result: ImportLookup): void {
    this.latestImport.set(result.v);
    this.importError.set(!result.ok);
  }

  /** The import panel's 重試: only the import lookup, so an accounts reload in flight stays valid. */
  retryImport(): void {
    const importId = ++this.importRequestId;
    this.importLookup().subscribe(result => {
      if (importId === this.importRequestId) {
        this.applyImport(result);
      }
    });
  }

  /** The whole-page 重試 after `帳戶讀取失敗`. */
  retry(): void {
    this.loadError.set(false);
    this.loaded.set(false);
    this.load();
  }

  private archivedRequest = 0;

  /** Only the latest request's answer is applied (reopened section, reload after a write). */
  private loadArchived(): void {
    const id = ++this.archivedRequest;
    this.accountingService.getAccounts(true).subscribe({
      next: all => {
        if (id === this.archivedRequest) {
          this.archived.set(all.filter(account => account.is_archived));
        }
      },
      error: () => {
        if (id === this.archivedRequest) {
          this.archived.set([]);
        }
      },
    });
  }

  statusLabel(run: ImportRun): string {
    return STATUS_LABELS[run.status];
  }

  isCollapsed(key: string): boolean {
    return this.collapsed().has(key);
  }

  toggleGroup(key: string): void {
    const next = new Set(this.collapsed());
    if (next.has(key)) {
      next.delete(key);
    } else {
      next.add(key);
    }
    this.collapsed.set(next);
  }

  toggleArchived(): void {
    this.archivedOpen.set(!this.archivedOpen());
    if (this.archivedOpen() && this.archived() === null) {
      this.loadArchived();
    }
  }

  /** Second line: a foreign currency (with its main-currency value) or available credit, rules, exclusion. */
  meta(account: LedgerAccount): string {
    const parts: string[] = [];
    if (account.is_credit) {
      if (account.available_credit !== null && account.available_credit !== undefined) {
        parts.push(`可用額度 ${formatMoney(account.available_credit, account.currency)}`);
      }
    } else if (account.currency !== this.mainCurrency()) {
      // No cached rate to the main currency: the server sends null, shown as — (and left out of every sum).
      const approx = account.balance_main === null ? '—' : formatMoney(account.balance_main, this.mainCurrency());
      parts.push(`${account.currency} · 約 ${approx}`);
    }
    // A main-currency account shows no currency code (spec: the currency appears when not the main currency).
    parts.push(...(account.rule_summaries ?? []));
    if (!account.include_in_total) {
      parts.push('不納入總餘額');
    }
    return parts.join(' · ');
  }

  /** `待繳帳款 $X` and `MM/DD 繳費截止` / `明天繳費截止` / `今天繳費截止` / `已逾期`; null when nothing is left to pay. */
  cardDue(account: LedgerAccount): CardDue | null {
    const bill = this.owed().get(account.id);
    if (!bill) {
      return null;
    }
    return {
      owedText: `待繳帳款 ${formatMoney(bill.remaining, bill.currency)}`,
      pill: duePillText(bill.due, this.today()),
      overdue: daysBetween(this.today(), bill.due) < 0,
    };
  }

  /** The cycle pill (next closing and its due day), shown while nothing is left to pay on the current statement. */
  duePill(account: LedgerAccount): string | null {
    if (!account.is_credit) {
      return null;
    }
    // A null closing day: the statement is the calendar month, closing on its last day.
    const period = statementPeriod(account.closing_day ?? null, this.today());
    const due = dueDate(period.end, account.due_rule ?? null, account.due_value ?? null);
    return due ? `結帳 ${shortDate(period.end)} · 繳款 ${shortDate(due)}` : `結帳 ${shortDate(period.end)}`;
  }
}
