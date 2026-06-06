import { ChangeDetectionStrategy, Component, OnInit, inject, signal } from '@angular/core';
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
