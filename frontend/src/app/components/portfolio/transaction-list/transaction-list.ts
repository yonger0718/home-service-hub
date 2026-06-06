import { Component, OnDestroy, OnInit, computed, inject, signal, ViewChild, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { PortfolioService } from '../../../services/portfolio.service';
import { BROKER_LABELS, Broker, MarketCode, Transaction, TransactionType, TransactionQuery, brokerLabel } from '../../../models/portfolio.model';

const BROKER_LABELS_MAP = BROKER_LABELS;
import { TableModule } from 'primeng/table';
import { ButtonModule } from 'primeng/button';
import { DialogModule } from 'primeng/dialog';
import { FormsModule } from '@angular/forms';
import { InputTextModule } from 'primeng/inputtext';
import { InputNumberModule } from 'primeng/inputnumber';
import { SelectButtonModule } from 'primeng/selectbutton';
import { SelectModule } from 'primeng/select';
import { DatePickerModule } from 'primeng/datepicker';
import { PaginatorModule, PaginatorState } from 'primeng/paginator';
import { ProgressSpinnerModule } from 'primeng/progressspinner';
import { ConfirmationService, MessageService, MenuItem } from 'primeng/api';
import { ConfirmDialogModule } from 'primeng/confirmdialog';
import { ToastModule } from 'primeng/toast';
import { MenuModule } from 'primeng/menu';
import { Menu } from 'primeng/menu';
import { BtnComponent } from '../../ui/btn/btn';
import { SegToggleComponent, SegToggleOption } from '../../ui/seg-toggle/seg-toggle';
import { TimelineComponent, TimelineRow } from '../../ui/timeline/timeline';

const SORT_OPTIONS = [
  { value: 'trade_date:desc', label: '日期 新→舊' },
  { value: 'trade_date:asc', label: '日期 舊→新' },
  { value: 'symbol:asc', label: '代碼 A→Z' },
  { value: 'symbol:desc', label: '代碼 Z→A' },
];

const SIDE_OPTIONS = [
  { value: 'BUY', label: '買進' },
  { value: 'SELL', label: '賣出' },
];

const MARKET_OPTIONS: { label: MarketCode; value: MarketCode }[] = [
  { label: 'TW', value: 'TW' },
  { label: 'US', value: 'US' },
  { label: 'LSE', value: 'LSE' },
];

const DEFAULT_CURRENCY_BY_MARKET: Record<Exclude<MarketCode, 'TW'>, string> = {
  US: 'USD',
  LSE: 'GBP',
};

@Component({
  selector: 'app-portfolio-transactions',
  imports: [
    CommonModule, TableModule, ButtonModule, DialogModule, FormsModule,
    InputTextModule, InputNumberModule, SelectButtonModule, SelectModule,
    DatePickerModule, PaginatorModule, ProgressSpinnerModule,
    ConfirmDialogModule, ToastModule, MenuModule,
    BtnComponent, SegToggleComponent, TimelineComponent,
  ],
  providers: [ConfirmationService, MessageService],
  templateUrl: './transaction-list.html',
  styleUrl: './transaction-list.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class PortfolioTransactionListComponent implements OnInit, OnDestroy {
  private portfolioService = inject(PortfolioService);
  private confirmationService = inject(ConfirmationService);
  private messageService = inject(MessageService);

  @ViewChild('menu') menu!: Menu;
  menuItems: MenuItem[] = [];

  transactions = signal<Transaction[]>([]);
  total = signal<number>(0);
  loading = signal<boolean>(false);
  showDialog = signal<boolean>(false);
  isEdit = signal<boolean>(false);

  symbolNames = signal<Record<string, string>>({});
  searchInput = signal<string>('');
  dateRange: Date[] | null = null;

  query = signal<TransactionQuery>({ offset: 0, limit: 25, sort: 'trade_date:desc' });
  readonly sortOptions = SORT_OPTIONS;
  readonly sideOptions = SIDE_OPTIONS;
  readonly sideFilterOptions: SegToggleOption[] = [
    { label: '全部', value: 'ALL' },
    { label: '買進', value: 'BUY' },
    { label: '賣出', value: 'SELL' },
  ];
  readonly availableBrokers = signal<Broker[]>([]);
  readonly brokerFilterOptions = computed<SegToggleOption[]>(() => {
    const brokers = this.availableBrokers();
    if (brokers.length === 0) return [];
    return [
      { label: '全部', value: 'ALL' },
      ...brokers.map(broker => ({ label: this.brokerLabel(broker), value: broker })),
    ];
  });
  selectedBroker = signal<'ALL' | Broker>('ALL');

  private brokerLabel(broker: Broker): string {
    return brokerLabel(broker);
  }
  readonly rowsPerPageOptions = [25, 50, 100];
  readonly marketOptions = MARKET_OPTIONS;
  readonly fxSubmitAttempted = signal(false);

  readonly timelineRows = computed<TimelineRow[]>(() =>
    this.transactions().map(t => {
      const isBuy = t.type === TransactionType.BUY;
      return {
        date: t.trade_date ?? '',
        side: isBuy ? 'buy' : 'sell',
        sideLabel: isBuy ? '買進' : '賣出',
        primary: `${this.symbolDisplay(t)} ${t.symbol}`,
        metaBadge: this.timelineBadge(t),
        meta: `${Number(t.quantity).toLocaleString('zh-TW')} × ${Number(t.price).toFixed(2)}`,
        amount: `${isBuy ? '-' : '+'}${this.formatTransactionAmount(t)}`,
        amountVariant: isBuy ? 'buy' : 'sell',
      };
    }),
  );

  private filterDebounce: ReturnType<typeof setTimeout> | null = null;
  private fetchSeq = 0;

  newTransaction: Partial<Transaction> = {
    type: TransactionType.BUY,
    market: 'TW',
    quantity: 0,
    price: 0,
    fee: 0,
    tax: 0,
  };

  transactionTypes = [
    { label: '買進', value: TransactionType.BUY },
    { label: '賣出', value: TransactionType.SELL },
  ];

  ngOnInit() {
    this.portfolioService.getSymbolNames().subscribe(map => this.symbolNames.set(map));
    this.portfolioService.getTransactionBrokers().subscribe(brokers => this.availableBrokers.set(brokers));
    this.fetch();
  }

  ngOnDestroy() {
    if (this.filterDebounce) clearTimeout(this.filterDebounce);
  }

  fetch() {
    const seq = ++this.fetchSeq;
    this.loading.set(true);
    this.portfolioService.getTransactions(this.query()).subscribe({
      next: paged => {
        if (seq !== this.fetchSeq) return;
        this.transactions.set(paged.items);
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

  private updateFilters(patch: Partial<TransactionQuery>, debounce = false) {
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
    const matches = Object.entries(map).filter(([, name]) => name?.includes(input));
    return matches.length === 1 ? matches[0][0] : null;
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

  onSideChange(side: 'BUY' | 'SELL' | null) {
    this.updateFilters({ side: side ?? null });
  }

  onSideFilterChange(side: string) {
    this.onSideChange(side === 'ALL' ? null : side as 'BUY' | 'SELL');
  }

  onBrokerFilterChange(value: string) {
    const broker = value === 'ALL' ? null : (value as Broker);
    this.selectedBroker.set(value === 'ALL' ? 'ALL' : (value as Broker));
    this.updateFilters({ broker });
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
    this.selectedBroker.set('ALL');
    this.query.set({ offset: 0, limit: this.query().limit ?? 25, sort: 'trade_date:desc' });
    this.fetch();
  }

  hasActiveFilters(): boolean {
    const q = this.query();
    return !!(q.symbol || q.date_from || q.date_to || q.side);
  }

  private toIsoDate(d: Date | string): string {
    if (typeof d === 'string') return d.slice(0, 10);
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
  }

  symbolDisplay(t: Transaction): string {
    // Skip placeholder names where the transaction's stored name is just
    // the ticker repeated (legacy import artefact) — fall through to the
    // symbol_map dictionary instead.
    const stored = t.name && t.name !== t.symbol ? t.name : null;
    return stored || this.symbolNames()[t.symbol] || t.symbol;
  }

  showMenu(event: MouseEvent, transaction: Transaction) {
    this.menuItems = [
      { label: '編輯', icon: 'pi pi-pencil', command: () => this.editTransaction(transaction) },
      { separator: true },
      { label: '刪除', icon: 'pi pi-trash', styleClass: 'text-danger', command: () => this.deleteTransaction(transaction) },
    ];
    this.menu.toggle(event);
  }

  openNew() {
    this.isEdit.set(false);
    this.fxSubmitAttempted.set(false);
    this.newTransaction = { type: TransactionType.BUY, market: 'TW', quantity: 0, price: 0, fee: 0, tax: 0 };
    this.showDialog.set(true);
  }

  editTransaction(transaction: Transaction) {
    this.isEdit.set(true);
    this.fxSubmitAttempted.set(false);
    this.newTransaction = {
      ...transaction,
      market: transaction.market ?? 'TW',
      trade_date: transaction.trade_date ? new Date(transaction.trade_date) : undefined,
    };
    this.showDialog.set(true);
  }

  deleteTransaction(transaction: Transaction) {
    this.confirmationService.confirm({
      message: `確定要刪除 ${transaction.symbol} 的這筆交易紀錄嗎？`,
      header: '確認刪除',
      icon: 'pi pi-exclamation-triangle',
      accept: () => {
        this.portfolioService.deleteTransaction(transaction.id).subscribe({
          next: () => {
            const nextTotal = Math.max(this.total() - 1, 0);
            const limit = this.query().limit ?? 25;
            const maxOffset = nextTotal > 0
              ? Math.floor((nextTotal - 1) / limit) * limit
              : 0;
            this.query.set({
              ...this.query(),
              offset: Math.min(this.query().offset ?? 0, maxOffset),
            });
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

  allInTotal(t: Transaction): number {
    const gross = Number(t.price) * Number(t.quantity);
    const fee = Number(t.fee || 0);
    const tax = Number(t.tax || 0);
    return t.type === TransactionType.BUY ? gross + fee + tax : gross - fee - tax;
  }

  allInUnitPrice(t: Transaction): number {
    const qty = Number(t.quantity);
    return qty > 0 ? this.allInTotal(t) / qty : Number(t.price);
  }

  formatCurrency(value: number | string | null | undefined): string {
    return new Intl.NumberFormat('zh-TW', {
      style: 'currency',
      currency: 'TWD',
      minimumFractionDigits: 0,
    }).format(Number(value ?? 0));
  }

  formatTransactionAmount(t: Transaction): string {
    const value = this.allInTotal(t);
    const currency = (t.currency ?? 'TWD').toUpperCase();
    // GBp = pence; render verbatim 4dp with GBp suffix (per Phase 3 native-display rule).
    if (t.currency === 'GBp') {
      return `${value.toLocaleString('en-US', { minimumFractionDigits: 4, maximumFractionDigits: 4 })} GBp`;
    }
    const decimals = currency === 'TWD' ? 0 : 2;
    const formatted = value.toLocaleString('en-US', {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    });
    return `${formatted} ${currency}`;
  }

  timelineBadge(t: Transaction): string | undefined {
    const badges = [
      t.market && t.market !== 'TW' ? t.market : null,
      t.broker ? this.brokerLabel(t.broker) : null,
    ].filter(Boolean) as string[];
    return badges.length > 0 ? badges.join(' · ') : undefined;
  }

  selectedMarket(): MarketCode {
    return this.newTransaction.market ?? 'TW';
  }

  isForeignTrade(): boolean {
    return this.selectedMarket() !== 'TW';
  }

  defaultBrokerForMarket(): Broker {
    return this.selectedMarket() === 'TW' ? 'TW_CATHAY' : 'FOREIGN_MANUAL';
  }

  readonly formBrokerOptions = (Object.keys(BROKER_LABELS_MAP) as Broker[]).map(broker => ({
    label: BROKER_LABELS_MAP[broker],
    value: broker,
  }));

  onMarketChange(market: MarketCode): void {
    this.newTransaction.market = market;
    this.fxSubmitAttempted.set(false);
    if (market === 'TW') {
      delete this.newTransaction.currency;
      delete this.newTransaction.fx_rate_to_twd;
      this.fxRateAuto.set(null);
      this.newTransaction.broker = 'TW_CATHAY';
      return;
    }
    this.newTransaction.currency = DEFAULT_CURRENCY_BY_MARKET[market];
    this.newTransaction.broker = this.defaultBrokerForMarket();
    this.tryFetchFxRate();
  }

  onTradeDateChange(): void {
    this.tryFetchFxRate();
  }

  onCurrencyChange(): void {
    this.tryFetchFxRate();
  }

  readonly fxRateAuto = signal<string | null>(null);
  readonly fxRateFetching = signal<boolean>(false);

  private tryFetchFxRate(): void {
    if (!this.isForeignTrade()) {
      this.fxRateAuto.set(null);
      return;
    }
    const currency = (this.newTransaction.currency ?? '').toUpperCase();
    const tradeDate = this.newTransaction.trade_date;
    if (!currency || currency === 'TWD' || !tradeDate) return;
    const iso = this.toIsoDate(tradeDate as Date | string);
    this.fxRateFetching.set(true);
    this.portfolioService.getFxRate(currency, iso).subscribe({
      next: result => {
        this.fxRateFetching.set(false);
        if (result.rate_to_twd == null) {
          this.fxRateAuto.set(null);
          return;
        }
        this.fxRateAuto.set(String(result.rate_to_twd));
        if (this.newTransaction.fx_rate_to_twd == null || Number(this.newTransaction.fx_rate_to_twd) <= 0) {
          this.newTransaction.fx_rate_to_twd = Number(result.rate_to_twd);
        }
      },
      error: () => {
        this.fxRateFetching.set(false);
        this.fxRateAuto.set(null);
      },
    });
  }

  showFxRateError(): boolean {
    return this.fxSubmitAttempted() && this.isFxRateInvalid();
  }

  private isFxRateInvalid(): boolean {
    if (!this.isForeignTrade()) return false;
    const rate = Number(this.newTransaction.fx_rate_to_twd);
    return !Number.isFinite(rate) || rate <= 0;
  }

  private transactionPayload(): Partial<Transaction> {
    const payload: Partial<Transaction> = { ...this.newTransaction };
    const market = this.selectedMarket();
    if (market === 'TW') {
      delete payload.market;
      delete payload.currency;
      delete payload.fx_rate_to_twd;
      return payload;
    }

    payload.market = market;
    payload.currency = payload.currency || DEFAULT_CURRENCY_BY_MARKET[market];
    payload.fx_rate_to_twd = Number(payload.fx_rate_to_twd);
    return payload;
  }

  saveTransaction() {
    this.fxSubmitAttempted.set(true);
    if (this.isFxRateInvalid()) return;

    const payload = this.transactionPayload();
    if (this.isEdit() && this.newTransaction.id) {
      this.portfolioService.updateTransaction(this.newTransaction.id, payload).subscribe({
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
      this.portfolioService.createTransaction(payload).subscribe({
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
