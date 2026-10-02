import { ChangeDetectionStrategy, Component, OnInit, computed, inject, signal } from '@angular/core';
import { DatePipe } from '@angular/common';
import { RouterLink } from '@angular/router';
import { forkJoin } from 'rxjs';

import { ImportRun, LedgerAccount } from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import { formatAmount, isNegative } from '../format';

const STATUS_LABELS: Record<ImportRun['status'], string> = {
  running: '匯入中',
  succeeded: '成功',
  failed: '失敗',
  dry_run: '試算',
};

@Component({
  selector: 'app-accounting-accounts',
  standalone: true,
  imports: [DatePipe, RouterLink],
  templateUrl: './accounts.html',
  styleUrl: './accounts.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AccountingAccountsComponent implements OnInit {
  private accountingService = inject(AccountingService);

  readonly accounts = signal<LedgerAccount[]>([]);
  readonly latestImport = signal<ImportRun | null>(null);
  readonly loaded = signal(false);
  readonly loadError = signal(false);
  readonly reviewCount = computed(
    () => this.latestImport()?.summary?.unpaired_transfers?.length ?? 0,
  );
  readonly formatAmount = formatAmount;
  readonly isNegative = isNegative;

  ngOnInit(): void {
    forkJoin({
      accounts: this.accountingService.getAccounts(),
      latest: this.accountingService.getLatestImport(),
    }).subscribe({
      next: ({ accounts, latest }) => {
        this.accounts.set(accounts);
        this.latestImport.set(latest);
        this.loaded.set(true);
      },
      error: () => {
        this.loadError.set(true);
        this.loaded.set(true);
      },
    });
  }

  statusLabel(run: ImportRun): string {
    return STATUS_LABELS[run.status];
  }
}
