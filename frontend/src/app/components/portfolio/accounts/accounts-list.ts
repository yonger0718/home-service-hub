import { ChangeDetectionStrategy, Component, OnInit, computed, inject, input, output, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router } from '@angular/router';

import { MessageService } from 'primeng/api';
import { ButtonModule } from 'primeng/button';
import { CardModule } from 'primeng/card';
import { DatePickerModule } from 'primeng/datepicker';
import { DialogModule } from 'primeng/dialog';
import { InputTextModule } from 'primeng/inputtext';
import { ProgressSpinnerModule } from 'primeng/progressspinner';
import { SelectModule } from 'primeng/select';
import { TagModule } from 'primeng/tag';
import { ToastModule } from 'primeng/toast';

import {
  AccountsList,
  BrokerAccount,
  BrokerEnum,
  CreateBrokerAccount,
} from '../../../models/portfolio.model';
import { PortfolioService } from '../../../services/portfolio.service';
import { CashFlowFormComponent } from '../cash-flow-form/cash-flow-form';
import { PageHeaderComponent } from '../../shell/page-header/page-header';
import { BtnComponent } from '../../ui/btn/btn';
import { TagComponent } from '../../ui/tag/tag';
import { abbrInk } from '../dashboard/dashboard-math';
import { groupedInteger } from '../money';
import { squareToken } from '../square';
import { formatCash } from './cash-rows';

const EMPTY_ACCOUNTS_LIST: AccountsList = {
  items: [],
  target_currency: 'TWD',
  total_target_balance: '0',
  skipped_currencies: [],
};

export const BROKER_OPTIONS: { label: string; value: BrokerEnum }[] = [
  { label: '國泰證券', value: 'cathay' },
  { label: '永豐證券', value: 'sinopac' },
  { label: 'Firstrade', value: 'firstrade' },
  { label: 'Interactive Brokers', value: 'ib' },
  { label: 'Charles Schwab', value: 'cs' },
  { label: '其他', value: 'other' },
];

/** The master-mode total card (Decision 9): what it shows and how many overdraft accounts it leaves out. */
export interface CashTotal {
  /** Server `total_target_balance` minus the TWD value of every overdraft account that has one. */
  total: number;
  /** Overdraft accounts (`native_balance < 0`) with a non-null `target_balance`, i.e. the ones taken out of `total`. */
  pendingCount: number;
}

/**
 * The server total sums every converted balance, negatives included; an overdraft account is one whose 入金 has not
 * been recorded yet, so its (negative) TWD value is taken back out. One without a `target_balance` is not in the server
 * sum (it is listed under 未換算) and is not counted.
 */
export function cashTotal(list: AccountsList): CashTotal {
  const pending = list.items.filter(
    account => Number(account.native_balance) < 0 && account.target_balance != null && account.target_balance !== '',
  );
  const excluded = pending.reduce((sum, account) => sum + Number(account.target_balance), 0);
  return { total: Number(list.total_target_balance ?? 0) - excluded, pendingCount: pending.length };
}

const BROKER_LABELS: Record<BrokerEnum, string> = {
  cathay: '國泰證券',
  sinopac: '永豐證券',
  firstrade: 'Firstrade',
  ib: 'Interactive Brokers',
  cs: 'Charles Schwab',
  other: 'Other',
};

@Component({
  selector: 'app-portfolio-accounts-list',
  standalone: true,
  imports: [
    PageHeaderComponent,
    BtnComponent,
    TagComponent,
    CommonModule,
    ReactiveFormsModule,
    ButtonModule,
    CardModule,
    DatePickerModule,
    DialogModule,
    InputTextModule,
    ProgressSpinnerModule,
    SelectModule,
    TagModule,
    ToastModule,
    CashFlowFormComponent,
  ],
  providers: [MessageService],
  templateUrl: './accounts-list.html',
  styleUrl: './accounts-list.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class PortfolioAccountsListComponent implements OnInit {
  private portfolioService = inject(PortfolioService);
  private messageService = inject(MessageService);
  private router = inject(Router);
  private fb = inject(FormBuilder);

  readonly brokerOptions = BROKER_OPTIONS;
  readonly currencyOptions = ['TWD', 'USD', 'GBP', 'JPY'].map(value => ({ label: value, value }));
  readonly accountsList = signal<AccountsList>(EMPTY_ACCOUNTS_LIST);
  readonly accounts = signal<BrokerAccount[]>([]);
  readonly loading = signal<boolean>(false);
  readonly createDialogVisible = signal<boolean>(false);
  readonly creating = signal<boolean>(false);
  readonly cashFormVisible = signal<boolean>(false);

  /** Master-detail page (Decision 9): button cards, no page header (the page owns it). */
  readonly master = input(false);
  /** The card shown as selected in master mode. */
  readonly selectedId = input<number | null>(null);
  /** The accounts after every successful fetch. */
  readonly loaded = output<BrokerAccount[]>();

  private readonly total = computed(() => cashTotal(this.accountsList()));
  readonly displayedTotal = computed(() => this.total().total);
  readonly overdraftCount = computed(() => this.total().pendingCount);
  /** `$150,000`: whole TWD, `−` before the `$` when negative. */
  readonly totalText = computed(() => {
    const total = this.displayedTotal();
    return `${total < 0 ? '−' : ''}$${groupedInteger(Math.abs(total))}`;
  });

  openCashForm(): void {
    this.cashFormVisible.set(true);
  }

  createForm = this.fb.group({
    broker: this.fb.nonNullable.control<BrokerEnum>('cathay', Validators.required),
    nickname: this.fb.nonNullable.control('', Validators.required),
    currency: this.fb.nonNullable.control('TWD', Validators.required),
    opening_balance: this.fb.nonNullable.control('0'),
    opening_date: this.fb.nonNullable.control<string | Date>(this.todayIso(), Validators.required),
  });

  ngOnInit(): void {
    this.fetchAccounts();
  }

  fetchAccounts(): void {
    this.loading.set(true);
    this.portfolioService.getAccounts({ in_currency: 'TWD' }).subscribe({
      next: list => {
        this.accountsList.set(list);
        this.accounts.set(list.items);
        this.loading.set(false);
        this.loaded.emit(list.items);
      },
      error: () => {
        this.loading.set(false);
        this.messageService.add({ severity: 'error', summary: '錯誤', detail: '帳戶讀取失敗' });
      },
    });
  }

  openCreateDialog(): void {
    this.createForm.reset({
      broker: 'cathay',
      nickname: '',
      currency: 'TWD',
      opening_balance: '0',
      opening_date: this.todayIso(),
    });
    this.createDialogVisible.set(true);
  }

  closeCreateDialog(): void {
    this.createDialogVisible.set(false);
  }

  submitCreate(): void {
    if (this.createForm.invalid || this.creating()) {
      this.createForm.markAllAsTouched();
      return;
    }

    const raw = this.createForm.getRawValue();
    const body: CreateBrokerAccount = {
      broker: raw.broker,
      nickname: raw.nickname.trim(),
      currency: raw.currency,
      opening_balance: raw.opening_balance || '0',
      opening_date: this.toIsoDate(raw.opening_date),
      is_active: true,
    };

    this.creating.set(true);
    this.portfolioService.createAccount(body).subscribe({
      next: () => {
        this.creating.set(false);
        this.createDialogVisible.set(false);
        this.messageService.add({ severity: 'success', summary: '已新增', detail: '帳戶已建立' });
        this.fetchAccounts();
      },
      error: () => {
        this.creating.set(false);
        this.messageService.add({ severity: 'error', summary: '錯誤', detail: '新增帳戶失敗' });
      },
    });
  }

  goToDetail(account: BrokerAccount): void {
    void this.router.navigate(['/portfolio/accounts', account.id]);
  }

  brokerLabel(broker: BrokerEnum): string {
    return BROKER_LABELS[broker] ?? broker;
  }

  isOverdraft(account: BrokerAccount): boolean {
    return Number(account.native_balance) < 0;
  }

  /** Master card line: `{broker} · {currency}`, plus `· 期初 {amount}（{YYYY/MM/DD}）` on the selected card. */
  cardMeta(account: BrokerAccount, selected: boolean): string {
    const base = `${this.brokerLabel(account.broker)} · ${account.currency}`;
    if (!selected) return base;
    const date = account.opening_date.slice(0, 10).replaceAll('-', '/');
    return `${base} · 期初 ${formatCash(account.opening_balance, account.currency)}（${date}）`;
  }

  /** Master card balance in its own currency, no prefix (`438,360`, `−2,379.54`). */
  cardBalance(account: BrokerAccount): string {
    return formatCash(account.native_balance, account.currency);
  }

  brokerToken(account: BrokerAccount): string {
    return squareToken(account.broker);
  }

  brokerInk(account: BrokerAccount): 'light' | 'dark' {
    return abbrInk(squareToken(account.broker));
  }

  formatCurrency(value: string | number | null | undefined, currency: string): string {
    const amount = Number(value ?? 0);
    const maximumFractionDigits = currency === 'JPY' || currency === 'TWD' ? 0 : 2;
    if (currency === 'TWD') {
      const sign = amount < 0 ? '-' : '';
      return `${sign}NT$${Math.abs(amount).toLocaleString('zh-TW', { maximumFractionDigits })}`;
    }
    return amount.toLocaleString('zh-TW', { style: 'currency', currency, maximumFractionDigits });
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
}
