import { Component, ElementRef, Injector, OnDestroy, OnInit, afterNextRender, computed, inject, signal, ViewChild, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Subscription, catchError, of } from 'rxjs';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { PortfolioService } from '../../../services/portfolio.service';
import { BrokerAccount, Dividend, DividendQuery, ExDividendRecord } from '../../../models/portfolio.model';
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
import { BtnComponent } from '../../ui/btn/btn';
import { BentoComponent } from '../../ui/bento/bento';
import { SegToggleComponent, SegToggleOption } from '../../ui/seg-toggle/seg-toggle';
import { TagComponent } from '../../ui/tag/tag';
import { PageHeaderComponent } from '../../shell/page-header/page-header';
import { allPages } from '../paging';
import { periodLabel } from '../period';
import { DividendTotals, dividendTotals } from './dividend-math';
import { DividendStatusFilter, dividendRow, matchesStatus } from './dividend-rows';

const SORT_OPTIONS = [
  { value: 'ex_dividend_date:desc', label: '除息日 新→舊' },
  { value: 'ex_dividend_date:asc', label: '除息日 舊→新' },
  { value: 'symbol:asc', label: '代碼 A→Z' },
  { value: 'symbol:desc', label: '代碼 Z→A' },
];

const STATUS_OPTIONS: SegToggleOption[] = [
  { value: 'all', label: '全部' },
  { value: 'pending', label: '待收' },
  { value: 'confirmed', label: '已收款' },
  { value: 'legacy', label: '舊資料' },
];

const SOURCE_OPTIONS = [
  { value: 'manual', label: '手動' },
  { value: 'auto:TWT49U', label: '自動 (TWSE)' },
  { value: 'csv', label: 'CSV 匯入' },
];

@Component({
  selector: 'app-portfolio-dividends',
  imports: [
    PageHeaderComponent,
    CommonModule, TableModule, ButtonModule, DialogModule, FormsModule,
    InputTextModule, InputNumberModule, DatePickerModule, SelectModule,
    PaginatorModule, ProgressSpinnerModule,
    ConfirmDialogModule, ToastModule, MenuModule,
    BtnComponent, BentoComponent, SegToggleComponent, TagComponent,
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
  private layoutMode = inject(LayoutModeService);
  private host = inject<ElementRef<HTMLElement>>(ElementRef);
  private injector = inject(Injector);

  @ViewChild('menu') menu!: Menu;
  menuItems: MenuItem[] = [];

  dividends = signal<Dividend[]>([]);
  total = signal<number>(0);
  loading = signal<boolean>(false);
  symbolNames = signal<Record<string, string>>({});
  upcomingExDividends = signal<ExDividendRecord[]>([]);
  showDialog = signal<boolean>(false);
  isEdit = signal<boolean>(false);

  searchInput = signal<string>('');
  dateRange: Date[] | null = null;

  query = signal<DividendQuery>({ offset: 0, limit: 25, sort: 'ex_dividend_date:desc' });
  readonly sortOptions = SORT_OPTIONS;
  readonly sourceOptions = SOURCE_OPTIONS;
  readonly rowsPerPageOptions = [25, 50, 100];
  readonly statusOptions = STATUS_OPTIONS;
  readonly isPhone = computed(() => this.layoutMode.mode() === 'phone');
  /** Every account, inactive ones too, loaded once on init (retried when the receipt form opens after a failure). */
  private readonly accounts = signal<BrokerAccount[]>([]);
  private accountsFailed = false;
  /** Receiving-account names for the 收款 column. */
  readonly accountNames = computed<ReadonlyMap<number, string>>(() => new Map(this.accounts().map(a => [a.id, a.nickname])));
  /** The receipt form's 收款帳戶 choices: active TWD accounts. */
  readonly receiptAccounts = computed(() => this.accounts().filter(a => a.is_active && a.currency === 'TWD'));

  /** Filter-wide TWD totals (every row of the active filter, not the list page); null while loading or after a failure. */
  readonly summary = signal<(DividendTotals & { foreignWithoutFx: number }) | null>(null);
  /** Every row of the active filter in the current sort; null until the first load lands or after a failure (a reload keeps the previous set). */
  readonly summaryRows = signal<Dividend[] | null>(null);
  /** The 5 000-row cap tripped for `summaryRows` (kept, like the rows, while a reload runs). */
  readonly summaryCapped = signal(false);
  readonly summaryFailed = signal(false);
  /** A filter-wide load is running; `summaryRows` may still hold the previous set meanwhile. */
  private readonly summaryLoading = signal(false);
  /** One line under the summary: rows the TWD sums leave out, and the 5 000-row cap. */
  readonly summaryNote = computed(() => {
    if (!this.summary()) return '';
    const parts: string[] = [];
    const withoutFx = this.summary()?.foreignWithoutFx ?? 0;
    if (withoutFx > 0) parts.push(`不含外幣 ${withoutFx} 筆`);
    if (this.summaryCapped()) parts.push('⋯ 僅載入前 5,000 筆');
    return parts.join(' · ');
  });

  /** Status SegToggle; any value but 全部 lists the filter-wide set (Decision 7), paged client-side from `statusOffset` (reset by every `loadAll`). */
  readonly status = signal<DividendStatusFilter>('all');
  private readonly statusOffset = signal(0);
  /** The selected status's rows of the filter-wide set; null on 全部 or while the set is not loaded. */
  readonly statusSet = computed(() => {
    const status = this.status();
    const rows = this.summaryRows();
    return status === 'all' || !rows ? null : rows.filter(d => matchesStatus(d, status));
  });
  /** A status is selected and no set has landed yet: the list waits rather than show the unfiltered page. */
  private readonly statusWaiting = computed(() => this.status() !== 'all' && this.summaryRows() === null && !this.summaryFailed());
  /** A status is selected but the set failed: the server page stays, with the list error. */
  readonly statusFailed = computed(() => this.status() !== 'all' && this.summaryFailed());
  readonly listRows = computed<Dividend[]>(() => {
    const set = this.statusSet();
    if (set) return set.slice(this.statusOffset(), this.statusOffset() + (this.query().limit ?? 25));
    return this.statusWaiting() ? [] : this.dividends();
  });
  /** Status set length in status mode (the previous set's while a reload runs), server `total` otherwise; null until a status set lands. */
  readonly listTotal = computed(() => this.statusSet()?.length ?? (this.statusWaiting() ? null : this.total()));
  readonly listOffset = computed(() => (this.statusSet() ? this.statusOffset() : this.query().offset ?? 0));
  /** In status mode a reload keeps the previous set listed (stale-while-revalidate) with 查詢中 beside it. */
  readonly listLoading = computed(() => this.loading() || this.statusWaiting() || (this.statusSet() !== null && this.summaryLoading()));
  readonly headerSubtitle = computed(() => `${periodLabel(this.query())} · 共 ${this.listTotal() ?? '—'} 筆`);
  readonly rowViews = computed(() => {
    const accounts = this.accountNames();
    return this.listRows().map(d => ({ d, v: dividendRow(d, this.nameFor(d.symbol), id => accounts.get(id) ?? null) }));
  });

  /** The row whose inline receipt form is open; one at a time. */
  readonly openReceiptId = signal<number | null>(null);
  /** The open row as listed now (its latest revision); null when it left the list or is no longer pending, so no form renders. */
  readonly receiptRow = computed(() => {
    const id = this.openReceiptId();
    return id === null ? null : this.listRows().find(d => d.id === id && d.receipt_status === 'pending') ?? null;
  });
  readonly receiptBusy = signal(false);
  readonly receiptDate = signal('');
  readonly receiptAccountId = signal<number | null>(null);

  /** Opens the form under a pending row, closing any other, and focuses 實際收款日; ignored while a confirmation is in flight. */
  openReceipt(row: Dividend): void {
    if (row.receipt_status !== 'pending' || this.receiptBusy()) return;
    this.receiptDate.set('');
    this.receiptAccountId.set(null);
    this.openReceiptId.set(row.id);
    if (this.accountsFailed) this.loadAccounts();
    this.focusAfterRender('.receipt-form input[type="date"]');
  }

  /** Closes the form and clears its fields (also on every filter, sort, status or page change). */
  closeReceipt(): void {
    this.openReceiptId.set(null);
    this.receiptDate.set('');
    this.receiptAccountId.set(null);
  }

  /** 取消: closes the form and returns focus to the row's 確認收款. */
  cancelReceipt(): void {
    const id = this.openReceiptId();
    this.closeReceipt();
    if (id !== null) this.focusRowButton(id);
  }

  onReceiptAccount(value: string): void {
    this.receiptAccountId.set(value ? Number(value) : null);
  }

  /**
   * Submits the open row as listed now. On success and on error the form closes, focus returns to the row's
   * 確認收款 while it is still listed, and both the page and the filter-wide set reload.
   */
  confirmReceipt(): void {
    const row = this.receiptRow();
    const receiptDate = this.receiptDate();
    const accountId = this.receiptAccountId();
    if (!row) {
      this.closeReceipt();
      return;
    }
    if (!receiptDate || accountId === null || this.receiptBusy()) return;
    this.receiptBusy.set(true);
    const settle = () => { this.receiptBusy.set(false); this.closeReceipt(); this.focusRowButton(row.id); this.reload(); };
    this.portfolioService.confirmDividendReceipt(row.id, { receipt_date: receiptDate,
      account_id: accountId, revision: row.revision ?? 1 }).subscribe({
      next: () => settle(),
      error: err => {
        settle();
        this.messageService.add({ severity: 'error', summary: '收款確認未完成', detail: err.error?.detail ?? '請重新載入後核對' });
      },
    });
  }

  private focusRowButton(id: number): void {
    this.focusAfterRender(`[data-row-id="${id}"] .c-actions app-btn button`);
  }

  private focusAfterRender(selector: string): void {
    afterNextRender(() => this.host.nativeElement.querySelector<HTMLElement>(selector)?.focus(), { injector: this.injector });
  }

  /** A failure is silent for the 收款 names; while the receipt form is rendered it raises the toast. */
  private loadAccounts(): void {
    this.accountsFailed = false;
    this.portfolioService.getAccounts({ include_inactive: true }).subscribe({
      next: result => this.accounts.set(result.items),
      error: () => {
        this.accountsFailed = true;
        if (this.receiptRow()) this.messageService.add({ severity: 'error', summary: '無法取得收款帳戶' });
      },
    });
  }

  private filterDebounce: ReturnType<typeof setTimeout> | null = null;
  private fetchSeq = 0;
  private summarySeq = 0;
  private summarySub?: Subscription;

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
    this.loadUpcomingExDividends();
    this.loadAccounts();
    this.reload();
  }

  ngOnDestroy() {
    if (this.filterDebounce) clearTimeout(this.filterDebounce);
    this.summarySub?.unsubscribe();
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

  /**
   * Every row of the active filter (symbol, dates, source) in the current sort, for the totals and the status
   * filter. A failure hides the summary silently (the list keeps its page); an older load is cancelled and any
   * response from it is dropped. The cards blank while it runs, but `summaryRows` keeps the previous set until
   * the new one lands, so a status list does not flash empty (a failure drops it).
   */
  loadAll() {
    this.summarySub?.unsubscribe();
    const seq = ++this.summarySeq;
    // A new set (filter, sort or data change) restarts the status list at its first page.
    this.statusOffset.set(0);
    const { symbol, date_from, date_to, source, sort } = this.query();
    this.summary.set(null);
    this.summaryFailed.set(false);
    this.summaryLoading.set(true);
    this.summarySub = allPages(offset => this.portfolioService.getDividends({ symbol, date_from, date_to, source, sort, limit: 100, offset }))
      .pipe(catchError(() => of(null)))
      .subscribe(result => {
        if (seq !== this.summarySeq) return;
        this.summaryLoading.set(false);
        if (!result) {
          this.summaryRows.set(null);
          this.summaryCapped.set(false);
          this.summaryFailed.set(true);
          return;
        }
        this.summary.set(dividendTotals(result.items));
        this.summaryRows.set(result.items);
        this.summaryCapped.set(result.capped);
        this.clampStatusOffset();
      });
  }

  /** A page picked on the previous set during a reload may lie past the new set's end: move to its last page. */
  private clampStatusOffset(): void {
    const set = this.statusSet();
    if (!set) return;
    const length = set.length;
    const limit = this.query().limit ?? 25;
    if (this.statusOffset() >= length) this.statusOffset.set(Math.max(0, Math.floor((length - 1) / limit) * limit));
  }

  /** The filter-wide set and the list page; on every filter, sort or data change (a page change only fetches). */
  private reload() {
    this.loadAll();
    this.fetch();
  }

  private updateFilters(patch: Partial<DividendQuery>, debounce = false) {
    this.query.set({ ...this.query(), ...patch, offset: 0 });
    this.statusOffset.set(0);
    this.closeReceipt();
    if (this.filterDebounce) clearTimeout(this.filterDebounce);
    if (debounce) {
      this.filterDebounce = setTimeout(() => this.reload(), 300);
    } else {
      this.reload();
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

  /** 全部 returns to server paging at offset 0; a status lists its share of the loaded set from its start. */
  onStatusChange(value: string) {
    this.status.set(value as DividendStatusFilter);
    this.statusOffset.set(0);
    this.closeReceipt();
    if (value === 'all') {
      this.query.set({ ...this.query(), offset: 0 });
      this.fetch();
    }
  }

  /** Status mode pages the loaded set without a request; otherwise the server page is fetched. */
  onPageChange(event: PaginatorState) {
    const offset = event.first ?? 0;
    const limit = event.rows ?? 25;
    this.closeReceipt();
    if (this.statusSet()) {
      this.statusOffset.set(offset);
      this.query.set({ ...this.query(), limit });
      return;
    }
    this.query.set({ ...this.query(), offset, limit });
    this.fetch();
  }

  clearFilters() {
    this.searchInput.set('');
    this.dateRange = null;
    this.status.set('all');
    this.statusOffset.set(0);
    this.closeReceipt();
    this.query.set({ offset: 0, limit: this.query().limit ?? 25, sort: 'ex_dividend_date:desc' });
    this.reload();
  }

  hasActiveFilters(): boolean {
    const q = this.query();
    return !!(q.symbol || q.date_from || q.date_to || q.source) || this.status() !== 'all';
  }

  private toIsoDate(d: Date): string {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
  }

  /** Card figures: whole TWD (no `-$0`), `—` while unknown. */
  formatCardAmount(value: number | null | undefined): string {
    return value == null ? '—' : this.formatCurrency(Math.round(value) || 0);
  }

  formatCurrency(value: number | string | null | undefined): string {
    return new Intl.NumberFormat('zh-TW', {
      style: 'currency',
      currency: 'TWD',
      minimumFractionDigits: 0,
    }).format(Number(value ?? 0));
  }

  private loadUpcomingExDividends(): void {
    this.portfolioService.getUpcomingExDividends().subscribe({
      next: data => this.upcomingExDividends.set(data),
      error: () => this.upcomingExDividends.set([]),
    });
  }

  onRowAction(action: { id: string | number; event: MouseEvent }) {
    const record = this.listRows().find(row => row.id === action.id);
    if (record) this.showMenu(action.event, record);
  }

  showMenu(event: MouseEvent, dividend: Dividend) {
    if (dividend.receipt_status && dividend.receipt_status !== 'pending') {
      this.messageService.add({ severity: 'info', summary: '需人工釐清，不可直接改寫已收款或舊資料' });
      return;
    }
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
            this.reload();
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
          this.reload();
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
          this.reload();
          this.messageService.add({ severity: 'success', summary: '成功', detail: '紀錄已新增' });
        },
        error: () => {
          this.messageService.add({ severity: 'error', summary: '錯誤', detail: '新增失敗，請檢查欄位' });
        },
      });
    }
  }
}
