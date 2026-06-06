import { ChangeDetectionStrategy, Component, EventEmitter, Input, Output, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { ButtonModule } from 'primeng/button';
import { DatePickerModule } from 'primeng/datepicker';
import { DialogModule } from 'primeng/dialog';
import { InputNumberModule } from 'primeng/inputnumber';
import { InputTextModule } from 'primeng/inputtext';
import { MessageService } from 'primeng/api';
import { SelectModule } from 'primeng/select';
import { ToastModule } from 'primeng/toast';

import { BROKER_LABELS, Broker, BrokerCashBalance, BrokerCashFlow } from '../../../models/portfolio.model';
import { PortfolioService } from '../../../services/portfolio.service';

const TYPE_OPTIONS: { label: string; value: BrokerCashFlow['type'] }[] = [
  { label: '存入 Deposit', value: 'deposit' },
  { label: '提領 Withdrawal', value: 'withdrawal' },
  { label: '利息 Interest', value: 'interest' },
  { label: '股利 Dividend', value: 'dividend_cash' },
  { label: '費用 Fee', value: 'fee' },
];

const CURRENCY_OPTIONS = ['TWD', 'USD', 'GBP', 'JPY'].map(value => ({ label: value, value }));

const BROKER_CURRENCY_DEFAULT: Record<Broker, string> = {
  TW_CATHAY: 'TWD',
  TW_SINOPAC: 'TWD',
  TW_MANUAL: 'TWD',
  IB: 'USD',
  FIRSTRADE: 'USD',
  SCHWAB: 'USD',
  FOREIGN_MANUAL: 'USD',
};

@Component({
  selector: 'app-cash-flow-form',
  standalone: true,
  imports: [
    CommonModule,
    ReactiveFormsModule,
    ButtonModule,
    DatePickerModule,
    DialogModule,
    InputNumberModule,
    InputTextModule,
    SelectModule,
    ToastModule,
  ],
  providers: [MessageService],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <p-toast></p-toast>
    <p-dialog
      [visible]="visible"
      (visibleChange)="onVisibleChange($event)"
      [modal]="true"
      [closable]="!submitting()"
      [style]="{ width: '32rem', maxWidth: '95vw' }"
      header="新增現金流"
    >
      <form [formGroup]="form" (ngSubmit)="submit()" class="cash-flow-form" novalidate>
        <div class="field">
          <label>券商</label>
          <p-select
            formControlName="broker"
            [options]="brokerOptions"
            optionLabel="label"
            optionValue="value"
            (onChange)="onBrokerChange($event.value)"
            styleClass="w-full">
          </p-select>
        </div>
        <div class="row">
          <div class="field">
            <label>類型</label>
            <p-select formControlName="type" [options]="typeOptions" optionLabel="label" optionValue="value" styleClass="w-full"></p-select>
          </div>
          <div class="field">
            <label>幣別</label>
            <p-select formControlName="currency" [options]="currencyOptions" optionLabel="label" optionValue="value" styleClass="w-full"></p-select>
          </div>
        </div>
        <div class="row">
          <div class="field">
            <label>金額</label>
            <input pInputText formControlName="amount" inputmode="decimal" placeholder="0.00" />
          </div>
          <div class="field">
            <label>日期</label>
            <p-datepicker formControlName="date" dateFormat="yy-mm-dd" appendTo="body" styleClass="w-full"></p-datepicker>
          </div>
        </div>
        <div class="field">
          <label>備註</label>
          <input pInputText formControlName="note" placeholder="optional" />
        </div>
        <div class="actions">
          <p-button label="取消" severity="secondary" [text]="true" (onClick)="close()" [disabled]="submitting()"></p-button>
          <p-button type="submit" label="儲存" [loading]="submitting()" [disabled]="form.invalid"></p-button>
        </div>
      </form>
    </p-dialog>
  `,
  styles: [`
    .cash-flow-form { display: flex; flex-direction: column; gap: 0.75rem; padding-top: 0.5rem; }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem; }
    .field { display: flex; flex-direction: column; gap: 0.25rem; }
    .field label { font-size: 0.85rem; color: var(--p-text-muted-color); }
    .actions { display: flex; justify-content: flex-end; gap: 0.5rem; margin-top: 0.5rem; }
  `],
})
export class CashFlowFormComponent {
  @Input() visible = false;
  @Input() initialBroker: Broker | null = null;
  @Output() visibleChange = new EventEmitter<boolean>();
  @Output() created = new EventEmitter<BrokerCashBalance>();

  private fb = inject(FormBuilder);
  private portfolioService = inject(PortfolioService);
  private messageService = inject(MessageService);

  readonly brokerOptions = (Object.keys(BROKER_LABELS) as Broker[]).map(broker => ({
    label: BROKER_LABELS[broker],
    value: broker,
  }));
  readonly typeOptions = TYPE_OPTIONS;
  readonly currencyOptions = CURRENCY_OPTIONS;
  readonly submitting = signal<boolean>(false);

  form = this.fb.group({
    broker: this.fb.nonNullable.control<Broker>('TW_CATHAY', Validators.required),
    type: this.fb.nonNullable.control<BrokerCashFlow['type']>('deposit', Validators.required),
    currency: this.fb.nonNullable.control<string>('TWD', Validators.required),
    amount: this.fb.nonNullable.control<string>('', Validators.required),
    date: this.fb.nonNullable.control<Date>(new Date(), Validators.required),
    note: this.fb.control<string | null>(null),
  });

  ngOnChanges(): void {
    if (this.initialBroker) {
      this.form.patchValue({
        broker: this.initialBroker,
        currency: BROKER_CURRENCY_DEFAULT[this.initialBroker] ?? 'TWD',
      });
    }
  }

  onVisibleChange(value: boolean): void {
    this.visible = value;
    this.visibleChange.emit(value);
  }

  onBrokerChange(broker: Broker): void {
    this.form.patchValue({ currency: BROKER_CURRENCY_DEFAULT[broker] ?? 'TWD' });
  }

  close(): void {
    this.onVisibleChange(false);
  }

  submit(): void {
    if (this.form.invalid || this.submitting()) {
      this.form.markAllAsTouched();
      return;
    }
    const raw = this.form.getRawValue();
    const payload: BrokerCashFlow = {
      broker: raw.broker,
      date: this.toIsoDate(raw.date),
      type: raw.type,
      amount: raw.amount,
      currency: raw.currency,
      note: raw.note ?? null,
    };
    this.submitting.set(true);
    this.portfolioService.createBrokerCashFlow(payload).subscribe({
      next: result => {
        this.submitting.set(false);
        this.messageService.add({ severity: 'success', summary: '已新增', detail: `${BROKER_LABELS[raw.broker]} ${result.currency}` });
        this.created.emit(result);
        this.form.reset({
          broker: raw.broker,
          type: 'deposit',
          currency: raw.currency,
          amount: '',
          date: new Date(),
          note: null,
        });
        this.onVisibleChange(false);
      },
      error: err => {
        this.submitting.set(false);
        this.messageService.add({ severity: 'error', summary: '失敗', detail: err?.error?.detail ?? '新增失敗' });
      },
    });
  }

  private toIsoDate(value: Date | string): string {
    if (typeof value === 'string') return value;
    const year = value.getFullYear();
    const month = String(value.getMonth() + 1).padStart(2, '0');
    const day = String(value.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
  }
}
