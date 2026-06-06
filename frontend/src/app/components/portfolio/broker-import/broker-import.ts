import { ChangeDetectionStrategy, Component, ElementRef, ViewChild, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ButtonModule } from 'primeng/button';
import { ProgressSpinnerModule } from 'primeng/progressspinner';
import { TableModule } from 'primeng/table';
import { ToastModule } from 'primeng/toast';
import { MessageService } from 'primeng/api';

import { BrokerCsvImportResult } from '../../../models/portfolio.model';
import { PortfolioService } from '../../../services/portfolio.service';

type ImportError = BrokerCsvImportResult['errors'][number];

@Component({
  selector: 'app-portfolio-broker-import',
  standalone: true,
  imports: [CommonModule, ButtonModule, ProgressSpinnerModule, TableModule, ToastModule],
  providers: [MessageService],
  templateUrl: './broker-import.html',
  styleUrl: './broker-import.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class PortfolioBrokerImportComponent {
  private readonly portfolioService = inject(PortfolioService);
  private readonly messageService = inject(MessageService);

  @ViewChild('fileInput') private fileInput?: ElementRef<HTMLInputElement>;

  readonly file = signal<File | null>(null);
  readonly busy = signal(false);
  readonly result = signal<BrokerCsvImportResult | null>(null);
  readonly uploadErrors = signal<ImportError[]>([]);

  readonly transactions = computed(() => this.result()?.transactions ?? []);
  readonly cashFlows = computed(() => this.result()?.cash_flows ?? []);
  readonly errors = computed(() => {
    const resultErrors = this.result()?.errors ?? [];
    return resultErrors.length > 0 ? resultErrors : this.uploadErrors();
  });

  readonly detectedBrokerLabel = computed(() => {
    const broker = this.result()?.detected_broker;
    return broker && broker.trim() ? broker : 'manual';
  });

  onFileSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const next = input.files?.[0] ?? null;
    if (!next) return;
    this.file.set(next);
    this.result.set(null);
    this.uploadErrors.set([]);
    this.preview();
  }

  clear(): void {
    this.file.set(null);
    this.result.set(null);
    this.uploadErrors.set([]);
    this.clearNativeInput();
  }

  preview(): void {
    this.upload(true);
  }

  commit(): void {
    this.upload(false);
  }

  field(row: any, keys: string[]): string {
    for (const key of keys) {
      const value = row?.[key];
      if (value !== undefined && value !== null && value !== '') return String(value);
    }
    return '-';
  }

  private upload(dryRun: boolean): void {
    const file = this.file();
    if (!file) {
      this.messageService.add({ severity: 'warn', summary: '請先選擇 CSV' });
      return;
    }

    this.busy.set(true);
    this.portfolioService.uploadBrokerCsv(file, dryRun).subscribe({
      next: result => {
        this.result.set(result);
        this.uploadErrors.set([]);
        this.busy.set(false);
        const severity = result.errors.length > 0 || result.counts.rejected > 0 ? 'warn' : 'success';
        this.messageService.add({
          severity,
          summary: dryRun ? '預覽完成' : '匯入完成',
          detail: this.countSummary(result),
          life: 5000,
        });
        if (!dryRun && severity === 'success') {
          this.file.set(null);
          this.clearNativeInput();
        }
      },
      error: err => {
        const errors = this.extractErrors(err);
        this.uploadErrors.set(errors);
        this.busy.set(false);
        this.messageService.add({
          severity: 'error',
          summary: '匯入失敗',
          detail: errors.map(error => `第 ${error.row_index} 列：${error.reason}`).join('；') || this.errorDetail(err),
          life: 6000,
        });
      },
    });
  }

  private countSummary(result: BrokerCsvImportResult): string {
    const createdLabel = result.dry_run ? '可新增' : '已新增';
    return `${createdLabel} ${result.counts.created}；重複 ${result.counts.skipped}；錯誤 ${result.counts.rejected}`;
  }

  private extractErrors(err: any): ImportError[] {
    const direct = err?.error?.errors;
    if (Array.isArray(direct)) return direct.map((error: any) => this.normalizeError(error));
    const detail = err?.error?.detail;
    if (Array.isArray(detail)) return detail.map((error: any) => this.normalizeError(error));
    return [];
  }

  private normalizeError(error: any): ImportError {
    return {
      row_index: Number(error?.row_index ?? error?.loc?.at?.(-1) ?? 0),
      reason: String(error?.reason ?? error?.message ?? error?.msg ?? '未知錯誤'),
    };
  }

  private errorDetail(err: any): string {
    const detail = err?.error?.detail ?? err?.error?.message ?? err?.message;
    return typeof detail === 'string' ? detail : '未知錯誤';
  }

  private clearNativeInput(): void {
    if (this.fileInput?.nativeElement) {
      this.fileInput.nativeElement.value = '';
    }
  }
}
