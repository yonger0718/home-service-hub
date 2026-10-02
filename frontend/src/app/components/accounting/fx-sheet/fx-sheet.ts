import { ChangeDetectionStrategy, Component, OnInit, computed, inject, input, model, output, signal } from '@angular/core';

import { LedgerAccount } from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import { roundHalfAway } from '../amount-math';
import { currencyDecimals, formatNumber } from '../format';

/** Foreign-currency state of an entry (design D21); amounts unsigned. */
export interface FxValue {
  original_amount: string;
  original_currency: string;
  /** Rate shown: the online one, or what the owner typed. */
  fx_rate: string | null;
  /** Converted amount in the account currency. */
  amount: string | null;
  /** True: the server converts with the cached daily rate (`fx_source = fx_api`). */
  use_online: boolean;
  /** Which field the owner typed when `use_online` is false; that one is sent (`fx_source = manual`). */
  manual: 'rate' | 'amount' | null;
  rate_date: string | null;
}

export const FX_CURRENCIES = ['TWD', 'JPY', 'USD', 'EUR', 'KRW', 'CNY', 'HKD', 'GBP', 'THB', 'SGD', 'AUD'];

function parseNumber(text: string): number {
  return text.trim() === '' ? Number.NaN : Number(text.replace(/,/g, ''));
}

/** 幣種 sheet (mockup `ov-fx`): 原幣金額, 匯率 with 線上匯率 date, 轉換成 <account currency>, 採用線上匯率. */
@Component({
  selector: 'app-fx-sheet',
  standalone: true,
  templateUrl: './fx-sheet.html',
  styleUrls: ['../sheet.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class FxSheetComponent implements OnInit {
  private readonly accounting = inject(AccountingService);

  readonly account = input.required<LedgerAccount>();
  readonly entryDate = input.required<string>();
  /** The amount tile's current value, used when the sheet opens without a conversion. */
  readonly originalAmount = input('');
  readonly value = model<FxValue | null>(null);
  readonly closed = output<void>();

  readonly currency = signal('');
  readonly original = signal('');
  readonly rate = signal('');
  readonly converted = signal('');
  readonly useOnline = signal(true);
  readonly manual = signal<'rate' | 'amount' | null>(null);
  readonly rateDate = signal<string | null>(null);
  readonly rateError = signal(false);

  readonly currencies = computed(() => [...new Set([this.account().currency, ...FX_CURRENCIES])]);
  readonly isForeign = computed(() => this.currency() !== this.account().currency);
  readonly convertedValue = computed((): number | null => {
    if (this.manual() === 'amount') {
      const typed = parseNumber(this.converted());
      return Number.isFinite(typed) ? typed : null;
    }
    const original = parseNumber(this.original());
    const rate = parseNumber(this.rate());
    if (!Number.isFinite(original) || !Number.isFinite(rate)) {
      return null;
    }
    return roundHalfAway(original * rate, currencyDecimals(this.account().currency));
  });
  readonly convertedText = computed(() => {
    if (this.manual() === 'amount') {
      return this.converted();
    }
    const value = this.convertedValue();
    return value === null ? '' : formatNumber(value, this.account().currency);
  });
  readonly rateNote = computed(() => {
    if (this.rateError()) {
      return '查無線上匯率，請手動輸入';
    }
    const date = this.rateDate();
    return this.useOnline() && date ? `${date.replace(/-/g, '/')} 線上匯率` : '手動匯率';
  });

  ngOnInit(): void {
    const value = this.value();
    this.currency.set(value?.original_currency ?? this.account().currency);
    this.original.set(value?.original_amount ?? this.originalAmount());
    this.rate.set(value?.fx_rate ?? '');
    this.converted.set(value?.manual === 'amount' ? (value.amount ?? '') : '');
    this.useOnline.set(value?.use_online ?? true);
    this.manual.set(value?.manual ?? null);
    this.rateDate.set(value?.rate_date ?? null);
    if (this.isForeign() && this.useOnline()) {
      this.fetchRate();
    }
  }

  setCurrency(code: string): void {
    this.currency.set(code);
    this.rate.set('');
    this.converted.set('');
    this.manual.set(null);
    this.rateDate.set(null);
    this.rateError.set(false);
    this.useOnline.set(true);
    if (this.isForeign()) {
      this.fetchRate();
    }
  }

  setOriginal(text: string): void {
    this.original.set(text);
  }

  editRate(text: string): void {
    this.rate.set(text);
    this.useOnline.set(false);
    this.manual.set('rate');
  }

  editConverted(text: string): void {
    this.converted.set(text);
    this.useOnline.set(false);
    this.manual.set('amount');
  }

  toggleOnline(): void {
    if (this.useOnline()) {
      this.useOnline.set(false);
      this.manual.set('rate');
      return;
    }
    this.useOnline.set(true);
    this.manual.set(null);
    this.converted.set('');
    this.fetchRate();
  }

  confirm(): void {
    if (!this.isForeign()) {
      this.value.set(null);
      this.closed.emit();
      return;
    }
    const original = parseNumber(this.original());
    const rate = parseNumber(this.rate());
    const converted = this.convertedValue();
    this.value.set({
      original_amount: Number.isFinite(original) ? String(original) : '',
      original_currency: this.currency(),
      fx_rate: Number.isFinite(rate) ? String(rate) : null,
      amount: converted === null ? null : String(converted),
      use_online: this.useOnline(),
      manual: this.useOnline() ? null : this.manual(),
      rate_date: this.rateDate(),
    });
    this.closed.emit();
  }

  cancel(): void {
    this.closed.emit();
  }

  private fetchRate(): void {
    this.rateError.set(false);
    this.accounting.getFxRate(this.entryDate(), this.currency(), this.account().currency).subscribe({
      next: result => {
        if (!this.useOnline()) {
          return;
        }
        this.rate.set(String(Number(result.rate)));
        this.rateDate.set(result.date);
      },
      error: () => {
        this.rateError.set(true);
        this.useOnline.set(false);
        this.manual.set('rate');
      },
    });
  }
}
