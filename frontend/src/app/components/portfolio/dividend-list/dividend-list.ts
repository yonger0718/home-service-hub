import { Component, OnDestroy, OnInit, inject, signal, ViewChild, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { PortfolioService } from '../../../services/portfolio.service';
import { Dividend, DividendQuery } from '../../../models/portfolio.model';
import { TableModule } from 'primeng/table';
import { ButtonModule } from 'primeng/button';
import { DialogModule } from 'primeng/dialog';
import { FormsModule } from '@angular/forms';
import { InputTextModule } from 'primeng/inputtext';
import { InputNumberModule } from 'primeng/inputnumber';
import { DatePickerModule } from 'primeng/datepicker';
import { SelectModule } from 'primeng/select';
import { PaginatorModule, PaginatorState } from 'primeng/paginator';
import { ProgressSpinnerModule } from 'primeng/progressspinner';
import { ConfirmationService, MessageService, MenuItem } from 'primeng/api';
import { ConfirmDialogModule } from 'primeng/confirmdialog';
import { ToastModule } from 'primeng/toast';
import { MenuModule } from 'primeng/menu';
import { Menu } from 'primeng/menu';
import { ListItemComponent } from '../../shared/list-item/list-item';

const SORT_OPTIONS = [
  { value: 'ex_dividend_date:desc', label: '除息日 新→舊' },
  { value: 'ex_dividend_date:asc', label: '除息日 舊→新' },
  { value: 'symbol:asc', label: '代碼 A→Z' },
  { value: 'symbol:desc', label: '代碼 Z→A' },
];

const SOURCE_OPTIONS = [
  { value: 'manual', label: '手動' },
  { value: 'auto:TWT49U', label: '自動 (TWSE)' },
  { value: 'csv', label: 'CSV 匯入' },
];

@Component({
  selector: 'app-portfolio-dividends',
  imports: [
    CommonModule, TableModule, ButtonModule, DialogModule, FormsModule,
    InputTextModule, InputNumberModule, DatePickerModule, SelectModule,
    PaginatorModule, ProgressSpinnerModule,
    ConfirmDialogModule, ToastModule, MenuModule, ListItemComponent,
  ],
  providers: [ConfirmationService, MessageService],
  templateUrl: './dividend-list.html',
  styleUrl: './dividend-list.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class PortfolioDividendListComponent implements OnInit, OnDestroy {
  private portfolioService = inject(PortfolioService);
  private confirmationService = inject(ConfirmationService);
  private messageService = inject(MessageService);

  @ViewChild('menu') menu!: Menu;
  menuItems: MenuItem[] = [];

  dividends = signal<Dividend[]>([]);
  total = signal<number>(0);
  loading = signal<boolean>(false);
  symbolNames = signal<Record<string, string>>({});
  showDialog = signal<boolean>(false);
  isEdit = signal<boolean>(false);

  searchInput = signal<string>('');
  dateRange: Date[] | null = null;

  query = signal<DividendQuery>({ offset: 0, limit: 25, sort: 'ex_dividend_date:desc' });
  readonly sortOptions = SORT_OPTIONS;
  readonly sourceOptions = SOURCE_OPTIONS;
  readonly rowsPerPageOptions = [25, 50, 100];

  private filterDebounce: ReturnType<typeof setTimeout> | null = null;
  private fetchSeq = 0;

  nameFor(symbol: string): string | null {
    return this.symbolNames()[symbol] ?? null;
  }

  newDividend: Partial<Dividend> = {
    amount: 0,
    fee: 0,
    tax: 0,
  };

  grossHint(): string | null {
    const qty = Number(this.newDividend.quantity_at_record_date ?? 0);
    const perShare = Number(this.newDividend.cash_dividend_per_share ?? 0);
    if (qty <= 0 || perShare <= 0) {
      return null;
    }
    const gross = qty * perShare;
    const fee = Number(this.newDividend.fee ?? 0);
    const tax = Number(this.newDividend.tax ?? 0);
    const fmt = (v: number) => `NT$${v.toLocaleString('en-US', { maximumFractionDigits: 2 })}`;
    if (tax > 0 || fee > 0) {
      const parts: string[] = [];
      if (fee > 0) parts.push(`手續費 −${fmt(fee)}`);
      if (tax > 0) parts.push(`補充保費 −${fmt(tax)}`);
      return `配息 ${fmt(gross)} (${parts.join(', ')})`;
    }
    return `配息 ${fmt(gross)}`;
  }

  ngOnInit() {
    this.portfolioService.getSymbolNames().subscribe(map => this.symbolNames.set(map));
    this.fetch();
  }

  ngOnDestroy() {
    if (this.filterDebounce) clearTimeout(this.filterDebounce);
  }

  fetch() {
    const seq = ++this.fetchSeq;
    this.loading.set(true);
    this.portfolioService.getDividends(this.query()).subscribe({
      next: paged => {
        if (seq !== this.fetchSeq) return;
        this.dividends.set(paged.items);
        this.total.set(paged.total);
        this.loading.set(false);
      },
      error: () => {
        if (seq !== this.fetchSeq) return;
        this.loading.set(false);
        this.messageService.add({ severity: 'error', summary: '錯誤', detail: '查詢失敗，請檢查篩選條件' });
      },
    });
  }

  private updateFilters(patch: Partial<DividendQuery>, debounce = false) {
    this.query.set({ ...this.query(), ...patch, offset: 0 });
    if (this.filterDebounce) clearTimeout(this.filterDebounce);
    if (debounce) {
      this.filterDebounce = setTimeout(() => this.fetch(), 300);
    } else {
      this.fetch();
    }
  }

  onSearchInput(value: string) {
    this.searchInput.set(value ?? '');
    const trimmed = (value ?? '').trim();
    const symbol = this.resolveSymbol(trimmed);
    this.updateFilters({ symbol: symbol || null }, true);
  }

  private resolveSymbol(input: string): string | null {
    if (!input) return null;
    if (/^[0-9A-Za-z.]+$/.test(input)) return input.toUpperCase();
    const map = this.symbolNames();
    for (const [ticker, name] of Object.entries(map)) {
      if (name === input) return ticker;
    }
    for (const [ticker, name] of Object.entries(map)) {
      if (name && name.includes(input)) return ticker;
    }
    return input;
  }

  onDateRangeChange(range: Date[] | null) {
    if (!range || range.length === 0) {
      this.updateFilters({ date_from: null, date_to: null });
      return;
    }
    if (range.length === 2 && range[0] && range[1]) {
      this.updateFilters({
        date_from: this.toIsoDate(range[0]),
        date_to: this.toIsoDate(range[1]),
      });
    }
  }

  onSourceChange(source: string | null) {
    this.updateFilters({ source: source ?? null });
  }

  onSortChange(sort: string) {
    this.updateFilters({ sort });
  }

  onPageChange(event: PaginatorState) {
    const offset = event.first ?? 0;
    const limit = event.rows ?? 25;
    this.query.set({ ...this.query(), offset, limit });
    this.fetch();
  }

  clearFilters() {
    this.searchInput.set('');
    this.dateRange = null;
    this.query.set({ offset: 0, limit: this.query().limit ?? 25, sort: 'ex_dividend_date:desc' });
    this.fetch();
  }

  hasActiveFilters(): boolean {
    const q = this.query();
    return !!(q.symbol || q.date_from || q.date_to || q.source);
  }

  private toIsoDate(d: Date): string {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
  }

  showMenu(event: MouseEvent, dividend: Dividend) {
    this.menuItems = [
      { label: '編輯', icon: 'pi pi-pencil', command: () => this.editDividend(dividend) },
      { separator: true },
      { label: '刪除', icon: 'pi pi-trash', styleClass: 'text-danger', command: () => this.deleteDividend(dividend) },
    ];
    this.menu.toggle(event);
  }

  openNew() {
    this.isEdit.set(false);
    this.newDividend = { amount: 0, fee: 0, tax: 0 };
    this.showDialog.set(true);
  }

  editDividend(dividend: Dividend) {
    this.isEdit.set(true);
    this.newDividend = { ...dividend, ex_dividend_date: dividend.ex_dividend_date ? new Date(dividend.ex_dividend_date) : undefined };
    this.showDialog.set(true);
  }

  deleteDividend(dividend: Dividend) {
    this.confirmationService.confirm({
      message: `確定要刪除 ${dividend.symbol} 的這筆股利紀錄嗎？`,
      header: '確認刪除',
      icon: 'pi pi-exclamation-triangle',
      accept: () => {
        this.portfolioService.deleteDividend(dividend.id).subscribe({
          next: () => {
            this.messageService.add({ severity: 'success', summary: '成功', detail: '紀錄已刪除' });
            this.fetch();
          },
          error: () => {
            this.messageService.add({ severity: 'error', summary: '錯誤', detail: '刪除失敗，請稍後再試' });
          },
        });
      },
    });
  }

  saveDividend() {
    if (this.isEdit() && this.newDividend.id) {
      this.portfolioService.updateDividend(this.newDividend.id, this.newDividend).subscribe({
        next: () => {
          this.showDialog.set(false);
          this.fetch();
          this.messageService.add({ severity: 'success', summary: '成功', detail: '紀錄已更新' });
        },
        error: () => {
          this.messageService.add({ severity: 'error', summary: '錯誤', detail: '更新失敗，請檢查欄位' });
        },
      });
    } else {
      this.portfolioService.createDividend(this.newDividend).subscribe({
        next: () => {
          this.showDialog.set(false);
          this.fetch();
          this.messageService.add({ severity: 'success', summary: '成功', detail: '紀錄已新增' });
        },
        error: () => {
          this.messageService.add({ severity: 'error', summary: '錯誤', detail: '新增失敗，請檢查欄位' });
        },
      });
    }
  }
}
