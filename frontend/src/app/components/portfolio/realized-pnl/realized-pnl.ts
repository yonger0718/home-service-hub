import { ChangeDetectionStrategy, Component, OnDestroy, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ButtonModule } from 'primeng/button';
import { DatePickerModule } from 'primeng/datepicker';
import { InputTextModule } from 'primeng/inputtext';
import { PaginatorModule, PaginatorState } from 'primeng/paginator';
import { ProgressSpinnerModule } from 'primeng/progressspinner';
import { SelectButtonModule } from 'primeng/selectbutton';
import { SelectModule } from 'primeng/select';
import { ToastModule } from 'primeng/toast';
import { ToggleSwitchModule } from 'primeng/toggleswitch';
import { TooltipModule } from 'primeng/tooltip';
import { MessageService } from 'primeng/api';

import { Broker, RealizedPnlEvent, RealizedPnlQuery, RealizedPnlSummary, brokerLabel } from '../../../models/portfolio.model';
import { PortfolioService } from '../../../services/portfolio.service';
import { NativeAmountPipe } from '../../../pipes/native-amount.pipe';
import { ListItemComponent } from '../../shared/list-item/list-item';
import { SegToggleComponent, SegToggleOption } from '../../ui/seg-toggle/seg-toggle';

const PAGE_SIZE_KEY = 'portfolio.realizedPnl.pageSize';
const DEFAULT_PAGE_SIZE = 25;
type YearPreset = 'YTD' | number | null;

const SORT_OPTIONS = [
  { value: 'trade_date:desc', label: '日期 新→舊' },
  { value: 'trade_date:asc', label: '日期 舊→新' },
  { value: 'realized_pnl:desc', label: '損益 高→低' },
  { value: 'realized_pnl:asc', label: '損益 低→高' },
];

@Component({
  selector: 'app-portfolio-realized-pnl',
  imports: [
    CommonModule,
    FormsModule,
    ButtonModule,
    DatePickerModule,
    InputTextModule,
    PaginatorModule,
    ProgressSpinnerModule,
    SelectButtonModule,
    SelectModule,
    ToastModule,
    ToggleSwitchModule,
    TooltipModule,
    NativeAmountPipe,
    ListItemComponent,
    SegToggleComponent,
  ],
  providers: [MessageService],
  templateUrl: './realized-pnl.html',
  styleUrl: './realized-pnl.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class PortfolioRealizedPnlComponent implements OnInit, OnDestroy {
  private portfolioService = inject(PortfolioService);
  private messageService = inject(MessageService);
  private currentYear = new Date().getFullYear();

  readonly Number = Number;
  readonly pageSizeStorageKey = PAGE_SIZE_KEY;
  readonly sortOptions = SORT_OPTIONS;
  readonly rowsPerPageOptions = [25, 50, 100];
  readonly yearPresetOptions = [
    { value: 'YTD', label: 'YTD' },
    { value: this.currentYear - 1, label: String(this.currentYear - 1) },
    { value: this.currentYear - 2, label: String(this.currentYear - 2) },
    { value: null, label: 'All' },
  ];

  events = signal<RealizedPnlEvent[]>([]);
  total = signal<number>(0);
  loading = signal<boolean>(false);
  symbolNames = signal<Record<string, string>>({});
  searchInput = signal<string>('');
  selectedYear = signal<YearPreset>(null);
  selectedBroker = signal<'ALL' | Broker>('ALL');
  expandedKey = signal<string | null>(null);
  summary = signal<RealizedPnlSummary>({
    filter_scope_total: '0',
    filter_scope_count: 0,
    ytd_total: '0',
    ytd_count: 0,
  });

  readonly showForeignColumns = computed(() =>
    this.events().some(event => (event.market ?? 'TW') !== 'TW'),
  );

  readonly brokerFilterOptions = computed<SegToggleOption[]>(() => [
    { label: '全部', value: 'ALL' },
    ...this.availableBrokers().map(broker => ({ label: brokerLabel(broker), value: broker })),
  ]);

  brokerLabel(broker: Broker | null | undefined): string {
    return brokerLabel(broker);
  }

  readonly showBrokerColumn = computed(() => this.availableBrokers().length > 0);
  readonly showBrokerFilter = computed(() => this.availableBrokers().length > 0);

  readonly filteredEvents = computed(() => {
    const broker = this.selectedBroker();
    const rows = this.events();
    return broker === 'ALL' ? rows : rows.filter(event => event.broker === broker);
  });

  dateRange: Date[] | null = null;
  query = signal<RealizedPnlQuery>({
    offset: 0,
    limit: this.readPageSize(),
    sort: 'trade_date:desc',
  });

  private filterDebounce: ReturnType<typeof setTimeout> | null = null;
  private fetchSeq = 0;

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
    this.portfolioService.getRealizedPnl(this.query()).subscribe({
      next: paged => {
        if (seq !== this.fetchSeq) return;
        this.events.set(paged.items);
        this.ensureSelectedBroker();
        this.total.set(paged.total);
        this.summary.set(paged.summary);
        this.expandedKey.set(null);
        this.loading.set(false);
      },
      error: () => {
        if (seq !== this.fetchSeq) return;
        this.loading.set(false);
        this.messageService.add({ severity: 'error', summary: '錯誤', detail: '查詢失敗，請檢查篩選條件' });
      },
    });
  }

  onSearchInput(value: string) {
    this.searchInput.set(value ?? '');
    const trimmed = (value ?? '').trim();
    const symbol = this.resolveSymbol(trimmed);
    this.updateFilters({ symbol: symbol || null }, true);
  }

  onDateRangeChange(range: Date[] | null) {
    this.selectedYear.set(null);
    if (!range || range.length === 0) {
      this.updateFilters({ date_from: null, date_to: null, year: null });
      return;
    }
    if (range.length === 2 && range[0] && range[1]) {
      this.updateFilters({
        date_from: this.toIsoDate(range[0]),
        date_to: this.toIsoDate(range[1]),
        year: null,
      });
    }
  }

  onYearPresetChange(preset: YearPreset) {
    this.selectedYear.set(preset);
    this.dateRange = null;
    const year = preset === 'YTD' ? this.currentYear : preset;
    this.updateFilters({ year, date_from: null, date_to: null });
  }

  onDayTradeOnlyChange(value: boolean) {
    this.updateFilters({ day_trade_only: value ? true : null });
  }

  onSortChange(sort: string) {
    this.updateFilters({ sort });
  }

  onPageChange(event: PaginatorState) {
    const offset = event.first ?? 0;
    const limit = event.rows ?? DEFAULT_PAGE_SIZE;
    this.persistPageSize(limit);
    this.query.set({ ...this.query(), offset, limit });
    this.fetch();
  }

  clearFilters() {
    this.searchInput.set('');
    this.dateRange = null;
    this.selectedYear.set(null);
    this.selectedBroker.set('ALL');
    this.expandedKey.set(null);
    this.query.set({
      offset: 0,
      limit: this.query().limit ?? DEFAULT_PAGE_SIZE,
      sort: 'trade_date:desc',
    });
    this.fetch();
  }

  hasActiveFilters(): boolean {
    const q = this.query();
    return !!(q.symbol || q.date_from || q.date_to || q.year || q.day_trade_only || this.selectedBroker() !== 'ALL');
  }

  selectBrokerFilter(value: string): void {
    this.selectedBroker.set(value as 'ALL' | Broker);
    this.expandedKey.set(null);
  }

  showBrokerBadge(event: RealizedPnlEvent): boolean {
    return !!event.broker;
  }

  toggleExpanded(event: RealizedPnlEvent) {
    const key = this.eventKey(event);
    this.expandedKey.set(this.expandedKey() === key ? null : key);
  }

  eventKey(event: RealizedPnlEvent): string {
    return [
      event.trade_date,
      event.symbol,
      event.market,
      event.quantity,
      event.sell_price,
      event.realized_pnl,
    ].join('|');
  }

  symbolDisplay(event: RealizedPnlEvent): string {
    const stored = event.name && event.name !== event.symbol ? event.name : null;
    return stored || this.symbolNames()[event.symbol] || event.symbol;
  }

  ytdTradeCount(): number {
    return this.summary().ytd_count ?? 0;
  }

  formatCurrency(value: string | number): string {
    return Number(value).toLocaleString('zh-TW', {
      style: 'currency',
      currency: 'TWD',
      maximumFractionDigits: 0,
    });
  }

  pnlClass(value: string | number): Record<string, boolean> {
    const amount = Number(value);
    return {
      'is-positive': amount > 0,
      'is-negative': amount < 0,
    };
  }

  private updateFilters(patch: Partial<RealizedPnlQuery>, debounce = false) {
    this.query.set({ ...this.query(), ...patch, offset: 0 });
    if (this.filterDebounce) clearTimeout(this.filterDebounce);
    if (debounce) {
      this.filterDebounce = setTimeout(() => this.fetch(), 300);
    } else {
      this.fetch();
    }
  }

  private availableBrokers(): Broker[] {
    const seen = new Set<Broker>();
    const brokers: Broker[] = [];
    for (const event of this.events()) {
      const broker = event.broker;
      if (!broker || seen.has(broker)) continue;
      seen.add(broker);
      brokers.push(broker);
    }
    return brokers;
  }

  private ensureSelectedBroker(): void {
    const selected = this.selectedBroker();
    if (selected === 'ALL') return;
    if (!this.availableBrokers().includes(selected)) {
      this.selectedBroker.set('ALL');
    }
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

  private toIsoDate(d: Date): string {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
  }

  private readPageSize(): number {
    if (typeof localStorage === 'undefined') return DEFAULT_PAGE_SIZE;
    const stored = Number(localStorage.getItem(PAGE_SIZE_KEY));
    return this.rowsPerPageOptions.includes(stored) ? stored : DEFAULT_PAGE_SIZE;
  }

  private persistPageSize(limit: number) {
    if (typeof localStorage === 'undefined') return;
    if (this.rowsPerPageOptions.includes(limit)) {
      localStorage.setItem(PAGE_SIZE_KEY, String(limit));
    }
  }
}
