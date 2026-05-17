import { ChangeDetectionStrategy, Component, OnDestroy, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { finalize } from 'rxjs';

import { ButtonModule } from 'primeng/button';
import { CardModule } from 'primeng/card';
import { CheckboxModule } from 'primeng/checkbox';
import { FileUploadModule } from 'primeng/fileupload';
import { MessageService } from 'primeng/api';
import { SelectButtonModule } from 'primeng/selectbutton';
import { TableModule } from 'primeng/table';
import { ToastModule } from 'primeng/toast';

import { ImportKind, ImportResult, RecalcStatus } from '../../../models/portfolio.model';
import { PortfolioService } from '../../../services/portfolio.service';

interface ImportOption {
  label: string;
  value: ImportKind;
  hint: string;
}

const POLL_INTERVAL_MS = 5_000;

@Component({
  selector: 'app-portfolio-import',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    ButtonModule,
    CardModule,
    CheckboxModule,
    FileUploadModule,
    SelectButtonModule,
    TableModule,
    ToastModule,
  ],
  providers: [MessageService],
  templateUrl: './import.html',
  styleUrl: './import.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class PortfolioImportComponent implements OnInit, OnDestroy {
  private portfolioService = inject(PortfolioService);
  private messageService = inject(MessageService);

  readonly kindOptions: ImportOption[] = [
    {
      label: '交易',
      value: 'transactions',
      hint:
        '代號(symbol), 類別(type), 股數(quantity), 價格(price), 交易日期(trade_date), 手續費(fee), 稅金(tax), 名稱(name) — 任一語言皆可；' +
        '另可附加 order_id(委託書號/訂單編號/委託編號) 區分同日同價同量交易',
    },
    {
      label: '股利',
      value: 'dividends',
      hint: '代號(symbol), 金額(amount), 除息日(ex_dividend_date), 入帳日(received_date) — 任一語言皆可',
    },
  ];

  readonly kind = signal<ImportKind>('transactions');
  readonly file = signal<File | null>(null);
  readonly busy = signal<boolean>(false);
  readonly result = signal<ImportResult | null>(null);
  readonly recalcStatus = signal<RecalcStatus>({ state: 'idle' });
  readonly hasHeader = signal<boolean>(true);

  private pollHandle: ReturnType<typeof setTimeout> | null = null;
  private statusRequestInFlight = false;

  ngOnInit(): void {
    // Refresh might have happened mid-recalc — surface the current state on mount.
    this.fetchRecalcStatus(/*startPollingIfRunning=*/ true);
  }

  ngOnDestroy(): void {
    this.stopPolling();
  }

  get headerHint(): string {
    return this.kindOptions.find(option => option.value === this.kind())?.hint ?? '';
  }

  onSelect(event: { files: File[] }): void {
    const next = event.files?.[0] ?? null;
    this.file.set(next);
    this.result.set(null);
  }

  onClear(): void {
    this.file.set(null);
    this.result.set(null);
  }

  preview(): void {
    this.upload(true);
  }

  commit(): void {
    this.upload(false);
  }

  retryRecalc(): void {
    this.portfolioService.triggerRecalc().subscribe({
      next: () => {
        this.messageService.add({
          severity: 'info', summary: '重新觸發重算', life: 3000,
        });
        this.startPolling();
      },
      error: err => {
        const detail = err?.error?.detail || err?.message || '未知錯誤';
        this.messageService.add({
          severity: 'error', summary: '重算觸發失敗', detail, life: 6000,
        });
      },
    });
  }

  private upload(dryRun: boolean): void {
    const file = this.file();
    if (!file) {
      this.messageService.add({ severity: 'warn', summary: '請先選擇 CSV', life: 3000 });
      return;
    }
    this.busy.set(true);
    this.portfolioService.uploadCsv(this.kind(), file, dryRun, this.hasHeader()).subscribe({
      next: result => {
        this.result.set(result);
        this.busy.set(false);
        const summary = dryRun ? '預覽完成' : '匯入完成';
        const detail =
          `已解析 ${result.parsed} 筆；` +
          `${dryRun ? '可新增' : '已新增'} ${result.created} 筆；` +
          `重複略過 ${result.skipped_duplicates} 筆；錯誤 ${result.errors.length} 筆`;
        const severity = result.errors.length > 0 ? 'warn' : 'success';
        this.messageService.add({ severity, summary, detail, life: 5000 });
        if (!dryRun && result.recalc_scheduled) {
          this.messageService.add({
            severity: 'info', summary: '資料重算執行中…',
            detail: '稍後將自動更新淨值圖與股利紀錄', life: 4000,
          });
          this.startPolling();
        }
      },
      error: err => {
        this.busy.set(false);
        const detail = err?.error?.detail || err?.error?.message || err?.message || '未知錯誤';
        this.messageService.add({ severity: 'error', summary: '匯入失敗', detail, life: 6000 });
      },
    });
  }

  private startPolling(): void {
    this.stopPolling();
    this.fetchRecalcStatus(true);
  }

  private scheduleNextPoll(): void {
    this.stopPolling();
    this.pollHandle = setTimeout(() => this.fetchRecalcStatus(false), POLL_INTERVAL_MS);
  }

  private stopPolling(): void {
    if (this.pollHandle !== null) {
      clearTimeout(this.pollHandle);
      this.pollHandle = null;
    }
  }

  private fetchRecalcStatus(startPollingIfRunning: boolean): void {
    // In-flight guard: a slow status response must not race a freshly-fired one.
    // The chained setTimeout below only schedules the next poll after the current
    // request settles, so this guard is mainly belt-and-suspenders for the
    // ngOnInit + post-commit double-call edges.
    if (this.statusRequestInFlight) return;
    this.statusRequestInFlight = true;
    this.portfolioService.getRecalcStatus().pipe(
      finalize(() => {
        this.statusRequestInFlight = false;
      }),
    ).subscribe({
      next: status => {
        const prevState = this.recalcStatus().state;
        this.recalcStatus.set(status);
        if (status.state === 'running') {
          // Keep polling as long as we were polling already (prevState=running),
          // or the caller explicitly asked us to start (ngOnInit / startPolling).
          if (startPollingIfRunning || prevState === 'running') {
            this.scheduleNextPoll();
          }
          return;
        }
        // Settled: emit completion toast only on transitions out of 'running'.
        // (status.state is narrowed away from 'running' by the early return above.)
        const justFinished = prevState === 'running';
        if (status.state === 'completed' && justFinished) {
          this.messageService.add({
            severity: 'success', summary: '資料重算完成', life: 4000,
          });
        } else if (status.state === 'partial' && justFinished) {
          const failed = (status.steps ?? [])
            .filter(s => s.status === 'failed' || s.status === 'partial')
            .map(s => s.name)
            .join(', ');
          this.messageService.add({
            severity: 'warn',
            summary: '資料重算部分失敗',
            detail: `失敗步驟：${failed || '未知'}。可點擊「重試」`,
            life: 8000,
          });
        } else if (status.state === 'failed' && justFinished) {
          const firstError = (status.steps ?? []).find(s => s.error)?.error ?? '未知錯誤';
          this.messageService.add({
            severity: 'error',
            summary: '資料重算失敗',
            detail: firstError,
            life: 8000,
          });
        }
        this.stopPolling();
      },
      error: () => {
        // Status endpoint failing should not loop forever, and should not leave
        // the banner stuck on 'running' (which would mislead the user into
        // thinking work is still happening).
        this.recalcStatus.update(prev =>
          prev.state === 'running' ? { ...prev, state: 'failed' } : prev,
        );
        this.messageService.add({
          severity: 'warn',
          summary: '無法取得重算狀態',
          detail: '請稍後重試，或手動點擊「重試」',
          life: 5000,
        });
        this.stopPolling();
      },
    });
  }

  payloadKeys(rows: ImportResult['rows']): string[] {
    return rows.length === 0 ? [] : Object.keys(rows[0].payload);
  }
}
