import { ChangeDetectionStrategy, Component, OnDestroy, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DatePickerModule } from 'primeng/datepicker';
import { PaginatorModule, PaginatorState } from 'primeng/paginator';
import { ProgressSpinnerModule } from 'primeng/progressspinner';
import { SelectButtonModule } from 'primeng/selectbutton';
import { SelectModule } from 'primeng/select';
import { ToastModule } from 'primeng/toast';
import { TooltipModule } from 'primeng/tooltip';
import { MessageService } from 'primeng/api';
import { Subscription, catchError, of } from 'rxjs';

import { Broker, RealizedPnlEvent, RealizedPnlQuery, RealizedPnlSummary, brokerLabel } from '../../../models/portfolio.model';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { PortfolioService } from '../../../services/portfolio.service';
import { BentoComponent } from '../../ui/bento/bento';
import { SegToggleComponent, SegToggleOption } from '../../ui/seg-toggle/seg-toggle';
import { SwitchComponent } from '../../ui/switch/switch';
import { TagComponent } from '../../ui/tag/tag';
import { PageHeaderComponent } from '../../shell/page-header/page-header';
import { allPages } from '../paging';
import {
  MARKET_FILTERS,
  MarketFilter,
  barExtents,
  barViews,
  barsAriaText,
  barsYear,
  clampOffset,
  matchesMarket,
  monthlyBars,
  rangeLabel,
  realizedCards,
  realizedTotals,
} from './realized-math';
import { SHORT_WARNING, realizedRows } from './realized-rows';

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
    PageHeaderComponent,
    FormsModule,
    DatePickerModule,
    PaginatorModule,
    ProgressSpinnerModule,
    SelectButtonModule,
    SelectModule,
    ToastModule,
    TooltipModule,
    BentoComponent,
    SegToggleComponent,
    SwitchComponent,
    TagComponent,
  ],
  providers: [MessageService],
  templateUrl: './realized-pnl.html',
  styleUrl: './realized-pnl.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class PortfolioRealizedPnlComponent implements OnInit, OnDestroy {
  private portfolioService = inject(PortfolioService);
  private messageService = inject(MessageService);
  private layoutMode = inject(LayoutModeService);
  private readonly today = new Date();
  private currentYear = this.today.getFullYear();

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
  /** Catalog of brokers present in the user's data, fetched independently of
   * the paginated/filtered events list so chips don't disappear after the
   * server narrows results to a single broker. */
  readonly brokerCatalog = signal<Broker[]>([]);
  expandedKey = signal<string | null>(null);
  summary = signal<RealizedPnlSummary>({
    filter_scope_total: '0',
    filter_scope_count: 0,
    ytd_total: '0',
    ytd_count: 0,
  });

  /**
   * Every sell of the active filter (Decision 1), for the cards, the bars and the 台股/美股 list; null while it
   * loads or after a failure. Every derived view reads `marketEvents()`, never this set directly.
   */
  readonly allEvents = signal<RealizedPnlEvent[] | null>(null);
  readonly allCapped = signal(false);
  readonly allFailed = signal(false);
  readonly allLoading = computed(() => this.allEvents() === null && !this.allFailed());
  readonly market = signal<MarketFilter>('ALL');
  /** Page start of the client-paged list under 台股/美股 (the server page serves 全部). */
  readonly clientOffset = signal(0);
  readonly marketEvents = computed(() => this.allEvents()?.filter(e => matchesMarket(e, this.market())) ?? null);
  readonly marketOptions: SegToggleOption[] = [
    { label: '全部', value: 'ALL' },
    { label: '台股', value: 'TW' },
    { label: '美股', value: 'US' },
  ];
  /** The list page's summary belongs to the active filter (false from a reload until its page lands). */
  private readonly summaryFresh = signal(false);

  readonly isPhone = computed(() => this.layoutMode.mode() === 'phone');
  readonly headerSubtitle = computed(() => `${rangeLabel(this.query())} · ${this.marketEvents()?.length ?? this.total()} 筆賣出`);
  /** Decision 2: the range card is the server total under 全部 (it stays right when the set is capped), else Σ of the set. */
  readonly cards = computed(() => {
    const set = this.marketEvents();
    const totals = set ? realizedTotals(set, this.today) : null;
    const headline = this.market() === 'ALL' ? this.serverHeadline(totals) : (totals?.total ?? null);
    const cards = realizedCards(totals, headline, rangeLabel(this.query()), this.today);
    return this.isPhone() ? cards.slice(0, 2) : cards;
  });
  readonly barsYear = computed(() => barsYear(this.query(), this.today));
  private readonly monthBars = computed(() => monthlyBars(this.marketEvents() ?? [], this.barsYear(), this.today));
  readonly bars = computed(() => barViews(this.monthBars()));
  readonly barExtents = computed(() => barExtents(this.monthBars()));
  readonly barsAriaText = computed(() => barsAriaText(this.monthBars(), this.barsYear()));

  readonly brokerFilterOptions = computed<SegToggleOption[]>(() => [
    { label: '全部', value: 'ALL' },
    ...this.availableBrokers().map(broker => ({ label: brokerLabel(broker), value: broker })),
  ]);

  readonly showBrokerFilter = computed(() => this.availableBrokers().length > 0);

  readonly pageSize = computed(() => this.query().limit ?? DEFAULT_PAGE_SIZE);

  /**
   * The listed sells: the server page under 全部; under 台股/美股 one client page of `marketEvents()` (its offset
   * clamped to the last page), or — while the set loads or after it failed — the server page's sells of that market.
   */
  readonly filteredEvents = computed(() => {
    const market = this.market();
    if (market === 'ALL') return this.events();
    const set = this.marketEvents();
    if (!set) return this.events().filter(e => matchesMarket(e, market));
    const limit = this.pageSize();
    const offset = clampOffset(this.clientOffset(), set.length, limit);
    return set.slice(offset, offset + limit);
  });
  readonly rows = computed(() => {
    const names = this.symbolNames();
    return realizedRows(
      this.filteredEvents(),
      e => (e.name && e.name !== e.symbol ? e.name : null) || names[e.symbol] || null,
      e => brokerLabel(e.broker),
    );
  });
  readonly shortWarning = SHORT_WARNING;
  /** Paginator and count: the server total and offset under 全部, the market set's under 台股/美股. */
  readonly listTotal = computed(() =>
    this.market() === 'ALL' ? this.total() : (this.marketEvents()?.length ?? this.filteredEvents().length),
  );
  readonly listFirst = computed(() => (this.market() === 'ALL' ? (this.query().offset ?? 0) : this.clientOffset()));

  dateRange: Date[] | null = null;
  query = signal<RealizedPnlQuery>({
    offset: 0,
    limit: this.readPageSize(),
    sort: 'trade_date:desc',
  });

  private filterDebounce: ReturnType<typeof setTimeout> | null = null;
  private fetchSeq = 0;
  private allSeq = 0;
  private listSub?: Subscription;
  private allSub?: Subscription;
  /** A fetch skipped under 台股/美股 left the server page behind the filter; 全部 fetches it again. */
  private listStale = false;

  ngOnInit() {
    this.portfolioService.getSymbolNames().subscribe(map => this.symbolNames.set(map));
    this.portfolioService.getTransactionBrokers().subscribe(brokers => this.brokerCatalog.set(brokers));
    this.reload();
  }

  ngOnDestroy() {
    if (this.filterDebounce) clearTimeout(this.filterDebounce);
    this.listSub?.unsubscribe();
    this.allSub?.unsubscribe();
  }

  /** The server list page; only under 全部 (台股/美股 page `marketEvents()` client-side). */
  fetch() {
    if (this.market() !== 'ALL') {
      // Drop any in-flight page of the old filter so it cannot land later.
      this.listSub?.unsubscribe();
      this.fetchSeq++;
      this.loading.set(false);
      this.listStale = true;
      return;
    }
    this.listStale = false;
    const seq = ++this.fetchSeq;
    this.loading.set(true);
    this.listSub?.unsubscribe();
    this.listSub = this.portfolioService.getRealizedPnl(this.query()).subscribe({
      next: paged => {
        if (seq !== this.fetchSeq) return;
        this.events.set(paged.items);
        this.ensureSelectedBroker();
        this.total.set(paged.total);
        this.summary.set(paged.summary);
        this.summaryFresh.set(true);
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

  /**
   * Every sell of the active filter in the current sort. A failure hides the set silently (the list keeps its page);
   * an older load is cancelled and any response from it is dropped.
   */
  loadAll() {
    this.allSub?.unsubscribe();
    const seq = ++this.allSeq;
    this.clientOffset.set(0);
    this.allEvents.set(null);
    this.allCapped.set(false);
    this.allFailed.set(false);
    const { symbol, date_from, date_to, year, day_trade_only, broker, sort } = this.query();
    this.allSub = allPages(
      offset => this.portfolioService.getRealizedPnl({ symbol, date_from, date_to, year, day_trade_only, broker, sort, limit: 200, offset }),
      200,
      50,
    )
      .pipe(catchError(() => of(null)))
      .subscribe(result => {
        if (seq !== this.allSeq) return;
        if (!result) {
          this.allFailed.set(true);
          return;
        }
        this.allEvents.set(result.items);
        this.allCapped.set(result.capped);
        this.clampClientOffset();
      });
  }

  /** The market toggle's value; anything that is not a market filter is ignored. */
  onMarketSelect(value: string) {
    const market = MARKET_FILTERS.find(m => m === value);
    if (market) this.onMarketChange(market);
  }

  onMarketChange(value: MarketFilter) {
    this.market.set(value);
    this.clientOffset.set(0);
    this.expandedKey.set(null);
    if (value === 'ALL' && this.listStale) this.fetch();
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
    if (this.market() !== 'ALL') {
      // The server page keeps its offset; a new page size leaves it stale for 全部.
      if (limit !== this.query().limit) {
        this.query.set({ ...this.query(), offset: 0, limit });
        this.listStale = true;
      }
      this.clientOffset.set(offset);
      this.clampClientOffset();
      this.expandedKey.set(null);
      return;
    }
    this.query.set({ ...this.query(), offset, limit });
    this.fetch();
  }

  clearFilters() {
    this.market.set('ALL');
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
    this.reload();
  }

  readonly hasActiveFilters = computed(() => {
    const q = this.query();
    return !!(
      q.symbol ||
      q.date_from ||
      q.date_to ||
      q.year ||
      q.day_trade_only ||
      this.selectedBroker() !== 'ALL' ||
      this.market() !== 'ALL'
    );
  });

  selectBrokerFilter(value: string): void {
    const next = value as 'ALL' | Broker;
    this.selectedBroker.set(next);
    this.expandedKey.set(null);
    this.query.set({
      ...this.query(),
      broker: next === 'ALL' ? null : next,
      offset: 0,
    });
    this.reload();
  }

  toggleExpanded(key: string) {
    this.expandedKey.set(this.expandedKey() === key ? null : key);
  }

  /**
   * The 全部 headline: the server total of the current filter; while that page is on its way `null` (`—`); after it
   * failed the set's own Σ (the same figure) unless the set is capped.
   */
  private serverHeadline(totals: { total: number } | null): number | null {
    if (this.summaryFresh()) return Number(this.summary().filter_scope_total);
    if (this.loading() || this.allCapped()) return null;
    return totals?.total ?? null;
  }

  /** The filter-wide set and the list page; on every filter or sort change (a page or market change never reloads the set). */
  private reload() {
    this.summaryFresh.set(false);
    this.loadAll();
    this.fetch();
  }

  private updateFilters(patch: Partial<RealizedPnlQuery>, debounce = false) {
    this.query.set({ ...this.query(), ...patch, offset: 0 });
    if (this.filterDebounce) clearTimeout(this.filterDebounce);
    if (debounce) {
      this.filterDebounce = setTimeout(() => this.reload(), 300);
    } else {
      this.reload();
    }
  }

  /** A page picked during a reload may lie past the new set's end: move to its last page. */
  private clampClientOffset(): void {
    const set = this.marketEvents();
    if (set) this.clientOffset.set(clampOffset(this.clientOffset(), set.length, this.pageSize()));
  }

  private availableBrokers(): Broker[] {
    return this.brokerCatalog();
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
