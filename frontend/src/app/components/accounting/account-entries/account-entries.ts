import { ChangeDetectionStrategy, Component, OnInit, computed, inject, signal } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';

import {
  ENTRY_KIND_LABELS,
  EntryKind,
  LedgerAccount,
  LedgerEntry,
} from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import { formatAmount, isNegative } from '../format';

export const PAGE_SIZE = 50;

@Component({
  selector: 'app-accounting-account-entries',
  standalone: true,
  imports: [RouterLink],
  templateUrl: './account-entries.html',
  styleUrl: './account-entries.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AccountingAccountEntriesComponent implements OnInit {
  private accountingService = inject(AccountingService);
  private route = inject(ActivatedRoute);
  private requestId = 0;

  readonly accountId = Number(this.route.snapshot.paramMap.get('id'));
  readonly account = signal<LedgerAccount | null>(null);
  readonly entries = signal<LedgerEntry[]>([]);
  readonly total = signal(0);
  readonly loading = signal(false);
  readonly loadError = signal(false);
  readonly kind = signal<EntryKind | null>(null);
  readonly dateFrom = signal<string | null>(null);
  readonly dateTo = signal<string | null>(null);
  readonly hasMore = computed(() => this.entries().length < this.total());

  readonly kindOptions = Object.entries(ENTRY_KIND_LABELS) as [EntryKind, string][];
  readonly kindLabels = ENTRY_KIND_LABELS;
  readonly formatAmount = formatAmount;
  readonly isNegative = isNegative;

  ngOnInit(): void {
    this.accountingService.getAccounts().subscribe({
      next: accounts => this.account.set(accounts.find(a => a.id === this.accountId) ?? null),
    });
    this.load(true);
  }

  load(reset: boolean): void {
    const id = ++this.requestId;
    if (reset) {
      this.entries.set([]);
      this.total.set(0);
    }
    this.loading.set(true);
    this.loadError.set(false);
    const offset = reset ? 0 : this.entries().length;
    this.accountingService
      .getEntries(this.accountId, {
        limit: PAGE_SIZE,
        offset,
        kind: this.kind(),
        date_from: this.dateFrom(),
        date_to: this.dateTo(),
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
          this.loadError.set(true);
          this.loading.set(false);
        },
      });
  }

  setKind(value: string): void {
    this.kind.set(value ? (value as EntryKind) : null);
    this.load(true);
  }

  setDateFrom(value: string): void {
    this.dateFrom.set(value || null);
    this.load(true);
  }

  setDateTo(value: string): void {
    this.dateTo.set(value || null);
    this.load(true);
  }

  when(entry: LedgerEntry): string {
    return entry.entry_time ? `${entry.entry_date} ${entry.entry_time.slice(0, 5)}` : entry.entry_date;
  }

  /** Tooltip for converted foreign-currency entries: the amount as recorded and the rate used. */
  originalTitle(entry: LedgerEntry): string | null {
    if (entry.original_amount === null || entry.original_currency === null) {
      return null;
    }
    return `原幣 ${formatAmount(entry.original_amount, entry.original_currency)} · 匯率 ${Number(entry.fx_rate)}`;
  }

  title(entry: LedgerEntry): string {
    return [entry.name, entry.merchant].filter(Boolean).join(' · ');
  }
}
