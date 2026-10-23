import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { PortfolioService } from '../../../services/portfolio.service';
import { Transaction, TransactionType } from '../../../models/portfolio.model';
import { PortfolioTransactionListComponent } from './transaction-list';

describe('PortfolioTransactionListComponent market picker', () => {
  let portfolioService: {
    getSymbolNames: ReturnType<typeof vi.fn>;
    getTransactions: ReturnType<typeof vi.fn>;
    getTransactionBrokers: ReturnType<typeof vi.fn>;
    createTransaction: ReturnType<typeof vi.fn>;
    updateTransaction: ReturnType<typeof vi.fn>;
    deleteTransaction: ReturnType<typeof vi.fn>;
    getRealizedPnl: ReturnType<typeof vi.fn>;
  };

  beforeEach(async () => {
    portfolioService = {
      getSymbolNames: vi.fn().mockReturnValue(of({})),
      getTransactions: vi.fn().mockReturnValue(of({ items: [], total: 0 })),
      getTransactionBrokers: vi.fn().mockReturnValue(of([])),
      createTransaction: vi.fn().mockReturnValue(of({})),
      updateTransaction: vi.fn().mockReturnValue(of({})),
      deleteTransaction: vi.fn().mockReturnValue(of(undefined)),
      getRealizedPnl: vi.fn().mockReturnValue(of({ items: [], total: 0, summary: { filter_scope_total: '0', filter_scope_count: 0, ytd_total: '0', ytd_count: 0 } })),
    };

    await TestBed.configureTestingModule({
      imports: [PortfolioTransactionListComponent],
      providers: [{ provide: PortfolioService, useValue: portfolioService }],
    })
      .overrideComponent(PortfolioTransactionListComponent, {
        set: {
          imports: [CommonModule, FormsModule],
          template: `
            <button class="open" type="button" (click)="openNew()">open</button>
            @if (showDialog()) {
              <select
                class="market"
                [ngModel]="selectedMarket()"
                (ngModelChange)="onMarketChange($event)">
                @for (option of marketOptions; track option.value) {
                  <option [value]="option.value">{{ option.label }}</option>
                }
              </select>
              @if (isForeignTrade()) {
                <input class="currency" [(ngModel)]="newTransaction.currency" />
                <input class="fx-rate" type="number" [(ngModel)]="newTransaction.fx_rate_to_twd" />
              }
              @if (showFxRateError()) {
                <span class="fx-error">FX must be greater than 0</span>
              }
              <button class="save" type="button" (click)="saveTransaction()">save</button>
            }
          `,
        },
      })
      .compileComponents();
  });

  it('hides FX inputs for TW trades and strips FX fields from the payload', () => {
    const fixture = TestBed.createComponent(PortfolioTransactionListComponent);
    fixture.detectChanges();
    fixture.nativeElement.querySelector('.open').click();
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('.currency')).toBeNull();
    fixture.componentInstance.newTransaction = {
      ...fixture.componentInstance.newTransaction,
      symbol: '2330',
      currency: 'USD',
      fx_rate_to_twd: 31,
    };
    fixture.nativeElement.querySelector('.save').click();

    const payload = portfolioService.createTransaction.mock.calls[0][0];
    expect(payload).not.toHaveProperty('currency');
    expect(payload).not.toHaveProperty('fx_rate_to_twd');
  });

  it('prefills USD for US trades', () => {
    const fixture = TestBed.createComponent(PortfolioTransactionListComponent);
    fixture.detectChanges();
    fixture.componentInstance.openNew();
    fixture.componentInstance.onMarketChange('US');
    fixture.detectChanges();

    expect(fixture.nativeElement.querySelector('.currency')).not.toBeNull();
    expect(fixture.componentInstance.newTransaction.currency).toBe('USD');
  });

  it('prefills GBP for LSE trades and preserves a GBp override on submit', () => {
    const fixture = TestBed.createComponent(PortfolioTransactionListComponent);
    fixture.detectChanges();
    fixture.componentInstance.openNew();
    fixture.componentInstance.onMarketChange('LSE');
    fixture.componentInstance.newTransaction = {
      ...fixture.componentInstance.newTransaction,
      symbol: 'VOD',
      currency: 'GBp',
      fx_rate_to_twd: 0.41,
    };

    fixture.componentInstance.saveTransaction();

    expect(portfolioService.createTransaction).toHaveBeenCalledWith(
      expect.objectContaining({ market: 'LSE', currency: 'GBp', fx_rate_to_twd: 0.41 }),
    );
  });

  it('blocks non-positive FX rates for foreign trades', () => {
    const fixture = TestBed.createComponent(PortfolioTransactionListComponent);
    fixture.detectChanges();
    fixture.componentInstance.openNew();
    fixture.componentInstance.onMarketChange('US');
    fixture.componentInstance.newTransaction.fx_rate_to_twd = 0;

    fixture.componentInstance.saveTransaction();
    fixture.detectChanges();

    expect(portfolioService.createTransaction).not.toHaveBeenCalled();
    expect(fixture.nativeElement.querySelector('.fx-error')).not.toBeNull();
  });

  it('shows foreign rows in their own currency in the quantity line', () => {
    const fixture = TestBed.createComponent(PortfolioTransactionListComponent);
    fixture.detectChanges();
    fixture.componentInstance.transactions.set([
      transaction({ symbol: '2330', market: 'TW' }),
      transaction({ symbol: 'AAPL', market: 'US', currency: 'USD', fx_rate_to_twd: 30 }),
    ]);

    const rows = fixture.componentInstance.timelineRows();

    expect(rows[0].meta).toBe('1 股 @ 100.00');
    expect(rows[1].meta).toBe('1 股 @ 100.00 USD');
    expect(rows.every(row => row.metaBadge === undefined)).toBe(true);
  });

  it('carries the broker label on every row', () => {
    const fixture = TestBed.createComponent(PortfolioTransactionListComponent);
    fixture.detectChanges();
    fixture.componentInstance.transactions.set([
      transaction({ symbol: 'TW1', market: 'TW', broker: 'TW_MANUAL' }),
      transaction({ symbol: 'TW2', market: 'TW', broker: 'TW_CATHAY' }),
      transaction({ symbol: 'IB1', market: 'US', broker: 'IB' }),
      transaction({ symbol: 'TW3', market: 'TW', broker: null }),
    ]);

    const rows = fixture.componentInstance.timelineRows();

    expect(rows.map(row => row.broker)).toEqual(['國泰', '國泰', 'IB', undefined]);
  });
});

function transaction(overrides: Partial<Transaction> = {}): Transaction {
  return {
    id: 1,
    symbol: '2330',
    type: TransactionType.BUY,
    quantity: 1,
    price: 100,
    fee: 0,
    tax: 0,
    broker: 'TW_MANUAL' as const,
    trade_date: '2026-05-01',
    ...overrides,
  };
}
