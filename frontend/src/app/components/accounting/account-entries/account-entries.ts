import { ChangeDetectionStrategy, Component, DestroyRef, OnInit, computed, effect, inject, signal, untracked } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';

import { AccountDetail, AccountPeriodSummary, ENTRY_KIND_LABELS, EntryKind, LedgerEntry } from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import { KIND_PILLS, KindPill, colorOf, fxLine, iconOf } from '../accounting-ui';
import { Period, periodLabel, shiftPeriod, statementPeriod } from '../cycle';
import { todayIso } from '../dates';
import { formatMoney } from '../format';

export const PAGE_SIZE = 200;

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
  private destroyRef = inject(DestroyRef);
  private requestId = 0;
  private summaryRequestId = 0;

  /** From `paramMap` (observable): at ≥ 760 px the pane reuses this component when only `:id` changes (Task 21). */
  readonly accountId = signal(0);
  readonly today = todayIso();
  readonly account = signal<AccountDetail | null>(null);
  readonly period = signal<Period | null>(null);
  /** Header totals of the whole period from `GET /accounts/{id}/summary`; never derived from the loaded page. */
  readonly periodSummary = signal<AccountPeriodSummary | null>(null);
  readonly currency = computed(() => this.periodSummary()?.currency ?? this.account()?.currency ?? 'TWD');
  readonly entries = signal<LedgerEntry[]>([]);
  readonly total = signal(0);
  readonly loading = signal(false);
  readonly loadError = signal(false);
  readonly kind = signal<EntryKind | null>(null);
  readonly q = signal<string | null>(null);
  readonly dateFrom = signal<string | null>(null);
  readonly dateTo = signal<string | null>(null);
  readonly hasMore = computed(() => this.entries().length < this.total());

  readonly range = computed(() => ({
    from: this.dateFrom() ?? this.period()?.start ?? null,
    to: this.dateTo() ?? this.period()?.end ?? null,
  }));
  readonly periodText = computed(() => {
    const period = this.period();
    return period ? periodLabel(period) : '';
  });
  readonly periodYear = computed(() => this.period()?.end.slice(0, 4) ?? '');

  readonly kindOptions = Object.entries(ENTRY_KIND_LABELS) as [EntryKind, string][];
  readonly formatMoney = formatMoney;
  readonly iconOf = iconOf;
  readonly colorOf = colorOf;

  constructor() {
    // Period totals: on every period change (account switch, ‹ ›) and after any entry write.
    effect(() => {
      const period = this.period();
      const accountId = this.accountId();
      this.accountingService.entriesChanged();
      if (!period || !accountId) {
        return;
      }
      untracked(() => this.loadSummary(accountId, period));
    });

    // Rows: after an entry write (e.g. the FAB form saving beside this pane), reload page 1 of the current
    // account / period / filters, keeping the old rows until the new page lands, as the timeline does.
    let seenEntriesChange = this.accountingService.entriesChanged();
    effect(() => {
      const change = this.accountingService.entriesChanged();
      if (change === seenEntriesChange) {
        return;
      }
      seenEntriesChange = change;
      untracked(() => {
        if (this.account() && this.period()) {
          this.load(true, true);
        }
      });
    });
  }

  ngOnInit(): void {
    this.route.paramMap.pipe(takeUntilDestroyed(this.destroyRef)).subscribe(params => {
      // Drop any page still in flight for the previous account.
      this.requestId++;
      this.accountId.set(Number(params.get('id')));
      this.account.set(null);
      this.period.set(null);
      this.periodSummary.set(null);
      this.kind.set(null);
      this.q.set(null);
      this.dateFrom.set(null);
      this.dateTo.set(null);
      this.entries.set([]);
      this.total.set(0);
      this.loadError.set(false);
      this.loadAccount();
    });
  }

  private loadAccount(): void {
    const accountId = this.accountId();
    this.accountingService.getAccount(accountId).subscribe({
      next: account => {
        // The pane is reused across :id changes; drop a response for an account no longer shown.
        if (accountId !== this.accountId()) {
          return;
        }
        this.account.set(account);
        this.period.set(statementPeriod(account.closing_day ?? null, this.today));
        this.load(true);
      },
      error: () => {
        if (accountId === this.accountId()) {
          this.loadError.set(true);
        }
      },
    });
  }

  private loadSummary(accountId: number, period: Period): void {
    const id = ++this.summaryRequestId;
    this.periodSummary.set(null);
    this.accountingService.getAccountSummary(accountId, period.start, period.end).subscribe({
      next: summary => {
        if (id === this.summaryRequestId) {
          this.periodSummary.set(summary);
        }
      },
      error: () => {
        if (id === this.summaryRequestId) {
          this.periodSummary.set(null);
        }
      },
    });
  }

  /** `keepRows`: on a reset, leave the current rows on screen until the new first page lands. */
  load(reset: boolean, keepRows = false): void {
    const id = ++this.requestId;
    if (reset && !keepRows) {
      this.entries.set([]);
      this.total.set(0);
    }
    this.loading.set(true);
    this.loadError.set(false);
    const range = this.range();
    this.accountingService
      .getEntries(this.accountId(), {
        limit: PAGE_SIZE,
        offset: reset ? 0 : this.entries().length,
        kind: this.kind(),
        date_from: range.from,
        date_to: range.to,
        q: this.q(),
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

  movePeriod(delta: number): void {
    const account = this.account();
    const period = this.period();
    if (!account || !period) {
      return;
    }
    this.period.set(shiftPeriod(account.closing_day ?? null, period, delta));
    this.dateFrom.set(null);
    this.dateTo.set(null);
    this.load(true);
  }

  setKind(value: string): void {
    this.kind.set(value ? (value as EntryKind) : null);
    this.load(true);
  }

  setQuery(value: string): void {
    this.q.set(value.trim() || null);
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

  title(entry: LedgerEntry): string {
    return entry.name || entry.category?.split('/').pop() || ENTRY_KIND_LABELS[entry.kind];
  }

  subtitle(entry: LedgerEntry): string {
    return [entry.merchant || entry.counterparty, ...(entry.tags ?? []).map(tag => `#${tag}`)].filter(Boolean).join(' · ');
  }

  originalLine(entry: LedgerEntry): string | null {
    return fxLine(entry);
  }

  kindPill(entry: LedgerEntry): KindPill | null {
    return KIND_PILLS[entry.kind] ?? null;
  }
}
