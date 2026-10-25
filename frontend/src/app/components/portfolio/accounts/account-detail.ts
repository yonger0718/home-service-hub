import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  OnInit,
  computed,
  effect,
  inject,
  input,
  signal,
  untracked,
} from '@angular/core';
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { FormBuilder, FormsModule, ReactiveFormsModule, Validators } from '@angular/forms';
import { ActivatedRoute } from '@angular/router';
import { BehaviorSubject, Observable, catchError, debounceTime, distinctUntilChanged, filter, map, merge, of, switchMap } from 'rxjs';

import { ConfirmationService, MessageService } from 'primeng/api';
import { ButtonModule } from 'primeng/button';
import { CardModule } from 'primeng/card';
import { ChartModule } from 'primeng/chart';
import { CheckboxModule } from 'primeng/checkbox';
import { ConfirmDialogModule } from 'primeng/confirmdialog';
import { DatePickerModule } from 'primeng/datepicker';
import { DialogModule } from 'primeng/dialog';
import { InputTextModule } from 'primeng/inputtext';
import { MultiSelectModule } from 'primeng/multiselect';
import { PaginatorModule, PaginatorState } from 'primeng/paginator';
import { ProgressSpinnerModule } from 'primeng/progressspinner';
import { SelectButtonModule } from 'primeng/selectbutton';
import { SelectModule } from 'primeng/select';
import { TagModule } from 'primeng/tag';
import { ToastModule } from 'primeng/toast';

import {
  BalanceHistory,
  BrokerAccount,
  BrokerEnum,
  CashTransaction,
  CashTransactionPaged,
  CashTransactionQuery,
  CashTransactionType,
  CreateCashTransaction,
  CreateCashTransactionType,
  PatchBrokerAccount,
} from '../../../models/portfolio.model';
import { PortfolioService } from '../../../services/portfolio.service';
import { AppearanceService } from '../../../services/appearance.service';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { formatMoney } from '../../ui/format';
import { SwitchComponent } from '../../ui/switch/switch';
import { TagComponent } from '../../ui/tag/tag';
import { BROKER_OPTIONS } from './accounts-list';
import { FilterBarComponent, FilterChip } from '../../ui/filter-bar/filter-bar';
import { PageHeaderComponent } from '../../shell/page-header/page-header';
import { CASH_SOURCE_VARIANTS, cashRow, formatCash, runningBalances } from './cash-rows';

type BalanceWindow = '1M' | '3M' | '1Y' | 'All';

const PAGE_SIZE_KEY = 'portfolio.cashTxns.pageSize';
const MERGE_RELATED_KEY_PREFIX = 'accounts.merge.';
const DEFAULT_PAGE_SIZE = 25;
/** A write whose dialog was opened for an account that is no longer the one shown. */
const ACCOUNT_SWITCHED_DETAIL = '帳戶已切換，請重新操作';

const WINDOW_OPTIONS: { label: BalanceWindow; value: BalanceWindow }[] = [
  { label: '1M', value: '1M' },
  { label: '3M', value: '3M' },
  { label: '1Y', value: '1Y' },
  { label: 'All', value: 'All' },
];

export const TYPE_OPTIONS: { label: string; value: CashTransactionType }[] = [
  { label: '入金', value: 'deposit' },
  { label: '出金', value: 'withdraw' },
  { label: '買進交割', value: 'buy_settle' },
  { label: '賣出交割', value: 'sell_settle' },
  { label: '手續費', value: 'fee' },
  { label: '交易稅', value: 'tax' },
  { label: '現金股利', value: 'dividend_cash' },
  { label: '利息收入', value: 'interest_in' },
  { label: '融資利息', value: 'margin_interest' },
  { label: '匯費', value: 'wire_fee' },
  { label: '換匯', value: 'fx_convert' },
];

const MANUAL_TYPE_OPTIONS = TYPE_OPTIONS.filter(option =>
  ['deposit', 'withdraw', 'interest_in', 'margin_interest', 'wire_fee', 'fx_convert']
    .includes(option.value),
);

const SORT_OPTIONS = [
  { value: 'txn_date:desc', label: '日期 新→舊' },
  { value: 'txn_date:asc', label: '日期 舊→新' },
  { value: 'amount:desc', label: '金額 高→低' },
  { value: 'amount:asc', label: '金額 低→高' },
];

const BROKER_LABELS: Record<BrokerEnum, string> = Object.fromEntries(
  BROKER_OPTIONS.map(option => [option.value, option.label]),
) as Record<BrokerEnum, string>;

interface CashQueryEvent {
  query: CashTransactionQuery;
  immediate: boolean;
}

function isCashQueryEvent(event: CashQueryEvent | null): event is CashQueryEvent {
  return event !== null;
}

@Component({
  selector: 'app-portfolio-account-detail',
  standalone: true,
  imports: [
    PageHeaderComponent,
    FilterBarComponent,
    FormsModule,
    ReactiveFormsModule,
    ButtonModule,
    CardModule,
    ChartModule,
    CheckboxModule,
    ConfirmDialogModule,
    DatePickerModule,
    DialogModule,
    InputTextModule,
    MultiSelectModule,
    PaginatorModule,
    ProgressSpinnerModule,
    SelectButtonModule,
    SelectModule,
    TagModule,
    ToastModule,
    SwitchComponent,
    TagComponent,
  ],
  providers: [ConfirmationService, MessageService],
  templateUrl: './account-detail.html',
  styleUrl: './account-detail.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class PortfolioAccountDetailComponent implements OnInit {
  private portfolioService = inject(PortfolioService);
  private confirmationService = inject(ConfirmationService);
  private messageService = inject(MessageService);
  private route = inject(ActivatedRoute);
  private fb = inject(FormBuilder);
  private destroyRef = inject(DestroyRef);
  private layout = inject(LayoutModeService);
  private filterQuery$ = new BehaviorSubject<CashQueryEvent | null>(null);

  readonly windowOptions = WINDOW_OPTIONS;
  readonly typeOptions = TYPE_OPTIONS;
  readonly manualTypeOptions = MANUAL_TYPE_OPTIONS;
  readonly sortOptions = SORT_OPTIONS;
  readonly sourceVariants = CASH_SOURCE_VARIANTS;
  readonly defaultPageSize = DEFAULT_PAGE_SIZE;
  readonly rowsPerPageOptions = [25, 50, 100];
  readonly selectedWindow = signal<BalanceWindow>('3M');
  /** Master-detail page (Decision 10): the selected account; null falls back to the route's `:id`. */
  readonly accountId = input<number | null>(null);
  /** Inside the master-detail page: no page header (the page owns it). */
  readonly embedded = input(false);
  private readonly routeAccountId = signal<number>(0);
  /** The account every load and write targets. */
  readonly effectiveId = computed(() => this.accountId() ?? this.routeAccountId());
  readonly account = signal<BrokerAccount | null>(null);
  readonly transactions = signal<CashTransaction[]>([]);
  readonly total = signal<number>(0);
  readonly loadingTransactions = signal<boolean>(false);
  readonly loadingHistory = signal<boolean>(false);
  readonly balanceHistory = signal<BalanceHistory>({ account_id: 0, currency: 'TWD', points: [] });
  readonly selectedTypes = signal<CashTransactionType[]>([]);
  readonly mergeRelated = signal<boolean>(false);
  readonly expandedTransactionIds = signal<Set<number>>(new Set<number>());
  readonly editDialogVisible = signal<boolean>(false);
  readonly transactionDialogVisible = signal<boolean>(false);
  readonly savingAccount = signal<boolean>(false);
  readonly savingTransaction = signal<boolean>(false);
  /** The account is loaded and is the one selected: only then may a dialog open (AGENT-223). */
  readonly accountReady = computed(() => {
    const account = this.account();
    return account !== null && account.id === this.effectiveId();
  });
  /** The account each dialog was opened for; its write goes there or nowhere. */
  private editAccountId: number | null = null;
  private transactionAccountId: number | null = null;

  dateRange: Date[] | null = null;
  query = signal<CashTransactionQuery>({
    offset: 0,
    limit: this.readPageSize(),
    sort: 'txn_date:desc',
  });

  /** The query of the page now shown (the running balance must match the rows, not a pending query). */
  private readonly shownQuery = signal<CashTransactionQuery>(this.query());
  /** Phone: icon + title · amount, the date beneath the amount (Decision 13). */
  readonly compact = computed(() => this.layout.mode() === 'phone');
  readonly rows = computed(() => this.transactions().map(cashRow));
  readonly legRows = computed(() => this.transactions().map(txn => (txn.child_legs ?? []).map(cashRow)));
  /** 餘額 per row (Decision 12), anchored at the account's balance; null hides the column. */
  readonly balanceTexts = computed(() => {
    const account = this.account();
    // No anchor yet, or rows of another account (a switch clears them, so a guard only): no column. After a
    // reload() the new balance may briefly meet the old rows of the same account; that short race is accepted.
    if (!account || this.transactions().some(txn => txn.account_id !== account.id)) return null;
    const balances = runningBalances(this.transactions(), account.native_balance, this.shownQuery(), this.todayIso());
    return balances?.map(value => formatCash(value, account.currency)) ?? null;
  });

  editForm = this.fb.group({
    nickname: this.fb.nonNullable.control('', Validators.required),
    opening_balance: this.fb.nonNullable.control('0'),
    opening_date: this.fb.nonNullable.control<string | Date>(this.todayIso(), Validators.required),
    is_active: this.fb.nonNullable.control(true),
  });

  transactionForm = this.fb.group({
    txn_date: this.fb.nonNullable.control<string | Date>(this.todayIso(), Validators.required),
    type: this.fb.nonNullable.control<CreateCashTransactionType>('deposit', Validators.required),
    amount: this.fb.nonNullable.control('', Validators.required),
    note: this.fb.nonNullable.control(''),
  });

  private readonly dark = toSignal(inject(AppearanceService).dark$, { initialValue: false });

  readonly chartData = computed(() => {
    // The token values flip on :root with the theme; reading dark() is what re-runs this computed.
    this.dark();
    const points = this.balanceHistory().points;
    const currency = this.balanceHistory().currency || this.account()?.currency || 'TWD';

    return {
      labels: points.map(point => point.date),
      datasets: [
        {
          label: `餘額 (${currency})`,
          data: points.map(point => Number(point.balance)),
          borderColor: this.cssVar('--app-primary'),
          backgroundColor: this.cssVar('--app-primary-soft'),
          borderWidth: 2,
          pointRadius: 0,
          pointHoverRadius: 4,
          tension: 0.2,
          stepped: true,
          fill: true,
        },
      ],
    };
  });

  readonly chartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    interaction: {
      mode: 'index',
      intersect: false,
    },
    plugins: {
      legend: {
        position: 'top',
      },
      tooltip: {
        callbacks: {
          label: (context: { dataset?: { label?: string }; parsed?: { y?: number } }) => {
            const label = context.dataset?.label ?? '餘額';
            return `${label}: ${this.formatCurrency(context.parsed?.y ?? 0, this.balanceCurrency())}`;
          },
        },
      },
    },
    scales: {
      x: {
        grid: {
          display: false,
        },
        ticks: {
          maxTicksLimit: 8,
          maxRotation: 0,
          autoSkip: true,
          // Chart.js passes the label index first, then the visible tick index.
          callback: (value: string | number, _tickIndex?: number) => this.tickLabel(Number(value)),
        },
      },
      y: {
        beginAtZero: false,
        ticks: {
          callback: (value: string | number) => this.formatCurrency(value, this.balanceCurrency()),
        },
      },
    },
  };

  private cssVar(name: string): string {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  private tickLabel(labelIndex: number): string {
    const date = this.chartData().labels[labelIndex] ?? '';
    return date.slice(5).replace('-', '/');
  }

  ngOnInit(): void {
    const queryEvents$ = this.filterQuery$.pipe(filter(isCashQueryEvent));
    merge(
      queryEvents$.pipe(filter(event => event.immediate)),
      queryEvents$.pipe(
        filter(event => !event.immediate),
        debounceTime(300),
        distinctUntilChanged((left, right) => JSON.stringify(left.query) === JSON.stringify(right.query)),
      ),
    )
      .pipe(
        switchMap(event => {
          const accountId = this.effectiveId();
          return this.requestTransactions(accountId, event.query).pipe(map(paged => ({ paged, query: event.query, accountId })));
        }),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe(({ paged, query, accountId }) => this.applyTransactions(paged, query, accountId));

    // Embedded beside the list: follow cash writes made anywhere (the route-driven page refetches on each visit).
    this.portfolioService.cashLedgerChanged$
      .pipe(filter(() => this.embedded()), takeUntilDestroyed(this.destroyRef))
      .subscribe(() => this.reload());

    this.route.paramMap.pipe(takeUntilDestroyed(this.destroyRef)).subscribe(params => {
      const id = Number(params.get('id'));
      if (!id) return;
      this.routeAccountId.set(id);
    });
  }

  /** Each change of the effective account (route `:id` or the `accountId` input) loads it once. */
  private readonly loadOnAccountChange = effect(() => {
    const id = this.effectiveId();
    untracked(() => this.selectAccount(id));
  });

  private selectAccount(id: number): void {
    this.clearAccountState();
    if (!id) return;
    const mergeRelated = this.readMergeRelated(id);
    this.mergeRelated.set(mergeRelated);
    this.query.set(this.withMergeRelated({ ...this.query(), offset: 0 }, mergeRelated));
    this.loadAccount();
    this.loadBalanceHistory();
    this.fetchTransactions();
  }

  /**
   * The detail instance is reused across accounts (master-detail): drop everything of the previous one, so its header,
   * rows and dialogs never stand for the next account while that one loads (AGENT-223).
   */
  private clearAccountState(): void {
    this.account.set(null);
    this.transactions.set([]);
    this.total.set(0);
    this.loadingTransactions.set(false);
    this.loadingHistory.set(false);
    this.balanceHistory.set({ account_id: 0, currency: 'TWD', points: [] });
    this.expandedTransactionIds.set(new Set<number>());
    this.closeEditDialog();
    this.closeTransactionDialog();
    this.resetEditForm();
    this.resetTransactionForm();
    this.confirmationService.close();
  }

  onWindowChange(window: BalanceWindow | null): void {
    if (!window || window === this.selectedWindow()) return;
    this.selectedWindow.set(window);
    this.loadBalanceHistory();
  }

  onDateRangeChange(range: Date[] | null): void {
    if (!range || range.length === 0) {
      this.dateRange = null;
      this.updateFilters({ date_from: undefined, date_to: undefined });
      return;
    }

    if (range.length === 2 && range[0] && range[1]) {
      this.updateFilters({
        date_from: this.toIsoDate(range[0]),
        date_to: this.toIsoDate(range[1]),
      });
    }
  }

  onTypeFilterChange(types: CashTransactionType[]): void {
    this.selectedTypes.set(types);
    this.updateFilters({ type: types.length === 1 ? types[0] : undefined });
  }

  onSortChange(sort: string): void {
    this.updateFilters({ sort });
  }

  onPageChange(event: PaginatorState): void {
    const offset = event.first ?? 0;
    const limit = event.rows ?? DEFAULT_PAGE_SIZE;
    this.persistPageSize(limit);
    this.query.set({ ...this.query(), offset, limit });
    this.fetchTransactions();
  }

  onMergeRelatedChange(value: boolean): void {
    if (value === this.mergeRelated()) return;
    this.mergeRelated.set(value);
    this.persistMergeRelated(this.effectiveId(), value);
    this.expandedTransactionIds.set(new Set<number>());

    const next = this.withMergeRelated({ ...this.query(), offset: 0 }, value);
    this.query.set(next);
    this.filterQuery$.next({ query: next, immediate: false });
  }

  openEditDialog(): void {
    const account = this.account();
    if (!account || !this.accountReady()) return;
    this.editForm.reset({
      nickname: account.nickname,
      opening_balance: account.opening_balance,
      opening_date: account.opening_date,
      is_active: account.is_active,
    });
    this.editAccountId = account.id;
    this.editDialogVisible.set(true);
  }

  closeEditDialog(): void {
    this.editDialogVisible.set(false);
    this.editAccountId = null;
  }

  submitEdit(): void {
    const accountId = this.editAccountId;
    if (!this.isWriteTarget(accountId)) {
      this.abortSwitchedWrite(() => this.closeEditDialog());
      return;
    }
    if (this.editForm.invalid || this.savingAccount()) {
      this.editForm.markAllAsTouched();
      return;
    }

    const raw = this.editForm.getRawValue();
    const patch: PatchBrokerAccount = {
      nickname: raw.nickname.trim(),
      opening_balance: raw.opening_balance || '0',
      opening_date: this.toIsoDate(raw.opening_date),
      is_active: raw.is_active,
    };

    this.savingAccount.set(true);
    this.portfolioService.patchAccount(accountId, patch).subscribe({
      next: account => {
        this.savingAccount.set(false);
        // Switched away while saving: the write landed on its own account; leave the one now shown alone.
        if (account.id === this.effectiveId()) {
          this.account.set(account);
          this.closeEditDialog();
        }
        this.messageService.add({ severity: 'success', summary: '已更新', detail: '帳戶資料已更新' });
        if (!this.embedded()) this.loadBalanceHistory();
        this.portfolioService.notifyCashLedgerChanged();
      },
      error: () => {
        this.savingAccount.set(false);
        this.messageService.add({ severity: 'error', summary: '錯誤', detail: '帳戶更新失敗' });
      },
    });
  }

  openTransactionDialog(): void {
    if (!this.accountReady()) return;
    this.resetTransactionForm();
    this.transactionAccountId = this.effectiveId();
    this.transactionDialogVisible.set(true);
  }

  isOverdraft(): boolean {
    const account = this.account();
    if (!account) return false;
    return Number(account.native_balance) < 0;
  }

  overdraftAmount(): string {
    const account = this.account();
    if (!account) return '0';
    return Math.abs(Number(account.native_balance)).toString();
  }

  openTopupQuickFix(): void {
    if (!this.accountReady()) return;
    this.transactionForm.reset({
      txn_date: this.todayIso(),
      type: 'deposit',
      amount: this.overdraftAmount(),
      note: '補登未記錄入金',
    });
    this.transactionAccountId = this.effectiveId();
    this.transactionDialogVisible.set(true);
  }

  closeTransactionDialog(): void {
    this.transactionDialogVisible.set(false);
    this.transactionAccountId = null;
  }

  submitTransaction(): void {
    const accountId = this.transactionAccountId;
    if (!this.isWriteTarget(accountId)) {
      this.abortSwitchedWrite(() => this.closeTransactionDialog());
      return;
    }
    if (this.transactionForm.invalid || this.savingTransaction()) {
      this.transactionForm.markAllAsTouched();
      return;
    }

    const account = this.account();
    const raw = this.transactionForm.getRawValue();
    const note = raw.note.trim();
    const body: CreateCashTransaction = {
      txn_date: this.toIsoDate(raw.txn_date),
      type: raw.type,
      amount: raw.amount,
      currency: account?.currency ?? 'TWD',
      note: note || null,
    };

    this.savingTransaction.set(true);
    this.portfolioService.createCashTransaction(accountId, body).subscribe({
      next: () => {
        this.savingTransaction.set(false);
        if (accountId === this.effectiveId()) this.closeTransactionDialog();
        this.messageService.add({ severity: 'success', summary: '已新增', detail: '交易已建立' });
        this.ledgerWritten();
      },
      error: () => {
        this.savingTransaction.set(false);
        this.messageService.add({ severity: 'error', summary: '錯誤', detail: '新增交易失敗' });
      },
    });
  }

  confirmDelete(txn: CashTransaction): void {
    const signedAmount = Number(txn.amount).toLocaleString('zh-TW', {
      maximumFractionDigits: txn.currency === 'JPY' || txn.currency === 'TWD' ? 0 : 2,
      minimumFractionDigits: 0,
      signDisplay: 'always',
    });
    const note = txn.note ? ` — ${txn.note}` : '';
    const accountId = this.effectiveId();

    this.confirmationService.confirm({
      header: '刪除交易',
      message: `${cashRow(txn).title} ${signedAmount} ${txn.currency} on ${txn.txn_date}${note}`,
      icon: 'pi pi-exclamation-triangle',
      acceptLabel: '刪除',
      rejectLabel: '取消',
      acceptButtonStyleClass: 'p-button-danger',
      accept: () => this.executeDelete(txn, accountId),
    });
  }

  /** `accountId`: the account the delete was confirmed for (the one shown when the row was clicked). */
  executeDelete(txn: CashTransaction, accountId: number = this.effectiveId()): void {
    if (!accountId) return;
    if (!this.isWriteTarget(accountId)) {
      this.abortSwitchedWrite(() => undefined);
      return;
    }

    this.portfolioService.deleteCashTransaction(accountId, txn.id).subscribe({
      next: () => {
        this.ledgerWritten();
        this.messageService.add({ severity: 'success', summary: '成功', detail: '已刪除' });
      },
      error: error => {
        this.messageService.add({
          severity: 'error',
          summary: '錯誤',
          detail: `刪除失敗: ${this.deleteErrorMessage(error)}`,
        });
      },
    });
  }

  clearFilters(): void {
    this.dateRange = null;
    this.selectedTypes.set([]);
    this.query.set(this.withMergeRelated({
      offset: 0,
      limit: this.query().limit ?? DEFAULT_PAGE_SIZE,
      sort: 'txn_date:desc',
    }));
    this.fetchTransactions();
  }

  /** Lifts the 類型 multiselect panel and the 排序 select panel above the open filter popover. */
  readonly panelOverlayOptions = { baseZIndex: 1200 };

  /** Secondary filters only: 類型 stays inline and never chips. */
  readonly filterChips = computed<FilterChip[]>(() => {
    const { date_from, date_to } = this.query();
    return date_from || date_to ? [{ key: 'date', label: `日期：${date_from ?? '…'} – ${date_to ?? '…'}` }] : [];
  });

  removeFilter(key: string): void {
    if (key === 'date') {
      this.dateRange = null;
      this.onDateRangeChange(null);
    }
  }

  resetSecondaryFilters(): void {
    for (const { key } of this.filterChips()) this.removeFilter(key);
  }

  transactionKey(txn: CashTransaction): string {
    return `${txn.id}:${txn.import_fingerprint}`;
  }

  isTransactionExpanded(id: number): boolean {
    return this.expandedTransactionIds().has(id);
  }

  toggleTransactionLegs(id: number): void {
    const next = new Set(this.expandedTransactionIds());
    if (next.has(id)) {
      next.delete(id);
    } else {
      next.add(id);
    }
    this.expandedTransactionIds.set(next);
  }

  brokerLabel(broker: BrokerEnum): string {
    return BROKER_LABELS[broker] ?? broker;
  }

  formatCurrency(value: string | number | null | undefined, currency: string): string {
    return formatMoney(value, { currency });
  }

  /** A write may go out only to the account its dialog was opened for, while that account is still the one shown. */
  private isWriteTarget(accountId: number | null): accountId is number {
    return accountId !== null && accountId === this.effectiveId() && this.account()?.id === accountId;
  }

  private abortSwitchedWrite(close: () => void): void {
    close();
    this.messageService.add({ severity: 'warn', summary: '已取消', detail: ACCOUNT_SWITCHED_DETAIL });
  }

  private resetEditForm(): void {
    this.editForm.reset({ nickname: '', opening_balance: '0', opening_date: this.todayIso(), is_active: true });
  }

  private resetTransactionForm(): void {
    this.transactionForm.reset({ txn_date: this.todayIso(), type: 'deposit', amount: '', note: '' });
  }

  /** After this account's ledger changed here: reload (embedded, through the notification) and tell the others. */
  private ledgerWritten(): void {
    if (!this.embedded()) this.reload();
    this.portfolioService.notifyCashLedgerChanged();
  }

  private reload(): void {
    if (!this.effectiveId()) return;
    this.loadAccount();
    this.loadBalanceHistory();
    this.fetchTransactions();
  }

  private loadAccount(): void {
    this.portfolioService.getAccounts({ include_inactive: true }).subscribe({
      next: list => {
        const account = list.items.find(item => item.id === this.effectiveId()) ?? null;
        this.account.set(account);
      },
      error: () => {
        this.messageService.add({ severity: 'error', summary: '錯誤', detail: '帳戶讀取失敗' });
      },
    });
  }

  private loadBalanceHistory(): void {
    const id = this.effectiveId();
    if (!id) return;
    const range = this.balanceRange(this.selectedWindow());
    this.loadingHistory.set(true);
    this.portfolioService.getBalanceHistory(id, range).subscribe({
      next: history => {
        // A late answer for an account no longer shown (a quick switch) must not replace the current chart.
        if (history.account_id !== this.effectiveId()) return;
        this.balanceHistory.set(history);
        this.loadingHistory.set(false);
      },
      error: () => {
        this.loadingHistory.set(false);
        this.messageService.add({ severity: 'error', summary: '錯誤', detail: '餘額走勢讀取失敗' });
      },
    });
  }

  private fetchTransactions(): void {
    this.filterQuery$.next({ query: this.query(), immediate: true });
  }

  private requestTransactions(accountId: number, query: CashTransactionQuery): Observable<CashTransactionPaged> {
    this.loadingTransactions.set(true);
    const requestQuery = this.withMergeRelated(query);
    return this.portfolioService.getCashTransactions(accountId, requestQuery).pipe(
      catchError(() => {
        this.messageService.add({ severity: 'error', summary: '錯誤', detail: '現金交易讀取失敗' });
        return of({ items: [], total: 0, offset: requestQuery.offset ?? 0, limit: requestQuery.limit ?? DEFAULT_PAGE_SIZE });
      }),
    );
  }

  private applyTransactions(paged: CashTransactionPaged, query: CashTransactionQuery, accountId: number): void {
    // A late page for an account no longer shown (nothing newer cancelled it, e.g. the selection cleared) is dropped.
    if (accountId !== this.effectiveId()) return;
    this.shownQuery.set(query);
    this.transactions.set(paged.items);
    this.total.set(paged.total);
    this.loadingTransactions.set(false);
  }

  private updateFilters(patch: Partial<CashTransactionQuery>): void {
    const next = this.withMergeRelated({ ...this.query(), ...patch, offset: 0 });
    this.query.set(next);
    this.filterQuery$.next({ query: next, immediate: false });
  }

  private deleteErrorMessage(error: unknown): string {
    if (typeof error === 'object' && error !== null) {
      if ('error' in error) {
        const body = (error as { error?: unknown }).error;
        if (typeof body === 'string' && body.trim()) return body;
        if (typeof body === 'object' && body !== null && 'detail' in body) {
          const detail = (body as { detail?: unknown }).detail;
          if (typeof detail === 'string' && detail.trim()) return detail;
        }
      }
      if ('message' in error) {
        const message = (error as { message?: unknown }).message;
        if (typeof message === 'string' && message.trim()) return message;
      }
    }

    return '請稍後再試';
  }

  private balanceRange(window: BalanceWindow): { date_from: string; date_to: string } {
    const today = new Date();
    if (window === 'All') {
      return {
        date_from: this.account()?.opening_date ?? '1900-01-01',
        date_to: this.toIsoDate(today),
      };
    }

    const days = {
      '1M': 30,
      '3M': 90,
      '1Y': 365,
    }[window];
    const from = new Date(today);
    from.setDate(from.getDate() - days);

    return {
      date_from: this.toIsoDate(from),
      date_to: this.toIsoDate(today),
    };
  }

  private balanceCurrency(): string {
    return this.balanceHistory().currency || this.account()?.currency || 'TWD';
  }

  private todayIso(): string {
    return this.toIsoDate(new Date());
  }

  private toIsoDate(value: string | Date): string {
    if (typeof value === 'string') return value;
    const year = value.getFullYear();
    const month = String(value.getMonth() + 1).padStart(2, '0');
    const day = String(value.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
  }

  private readPageSize(): number {
    if (typeof localStorage === 'undefined') return DEFAULT_PAGE_SIZE;
    const stored = Number(localStorage.getItem(PAGE_SIZE_KEY));
    return this.rowsPerPageOptions.includes(stored) ? stored : DEFAULT_PAGE_SIZE;
  }

  private persistPageSize(limit: number): void {
    if (typeof localStorage === 'undefined') return;
    localStorage.setItem(PAGE_SIZE_KEY, String(limit));
  }

  private withMergeRelated(query: CashTransactionQuery, value = this.mergeRelated()): CashTransactionQuery {
    if (value) {
      return { ...query, merge_related: true };
    }

    return { ...query, merge_related: undefined };
  }

  private readMergeRelated(accountId: number): boolean {
    if (typeof localStorage === 'undefined') return false;
    return localStorage.getItem(this.mergeStorageKey(accountId)) === '1';
  }

  private persistMergeRelated(accountId: number, value: boolean): void {
    if (!accountId || typeof localStorage === 'undefined') return;
    localStorage.setItem(this.mergeStorageKey(accountId), value ? '1' : '0');
  }

  private mergeStorageKey(accountId: number): string {
    return `${MERGE_RELATED_KEY_PREFIX}${accountId}`;
  }
}
