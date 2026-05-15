import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { ButtonModule } from 'primeng/button';
import { CardModule } from 'primeng/card';
import { FileUploadModule } from 'primeng/fileupload';
import { MessageService } from 'primeng/api';
import { SelectButtonModule } from 'primeng/selectbutton';
import { TableModule } from 'primeng/table';
import { ToastModule } from 'primeng/toast';

import { ImportKind, ImportResult } from '../../../models/portfolio.model';
import { PortfolioService } from '../../../services/portfolio.service';

interface ImportOption {
  label: string;
  value: ImportKind;
  hint: string;
}

@Component({
  selector: 'app-portfolio-import',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    ButtonModule,
    CardModule,
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
export class PortfolioImportComponent {
  private portfolioService = inject(PortfolioService);
  private messageService = inject(MessageService);

  readonly kindOptions: ImportOption[] = [
    {
      label: '交易',
      value: 'transactions',
      hint: 'symbol,type,quantity,price,trade_date,fee,tax,name',
    },
    {
      label: '股利',
      value: 'dividends',
      hint: 'symbol,amount,ex_dividend_date,received_date',
    },
  ];

  readonly kind = signal<ImportKind>('transactions');
  readonly file = signal<File | null>(null);
  readonly busy = signal<boolean>(false);
  readonly result = signal<ImportResult | null>(null);

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

  private upload(dryRun: boolean): void {
    const file = this.file();
    if (!file) {
      this.messageService.add({ severity: 'warn', summary: '請先選擇 CSV', life: 3000 });
      return;
    }
    this.busy.set(true);
    this.portfolioService.uploadCsv(this.kind(), file, dryRun).subscribe({
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
      },
      error: err => {
        this.busy.set(false);
        const detail = err?.error?.detail || err?.error?.message || err?.message || '未知錯誤';
        this.messageService.add({ severity: 'error', summary: '匯入失敗', detail, life: 6000 });
      },
    });
  }

  payloadKeys(rows: ImportResult['rows']): string[] {
    return rows.length === 0 ? [] : Object.keys(rows[0].payload);
  }
}
