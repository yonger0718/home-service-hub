import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { PortfolioService } from '../../../services/portfolio.service';
import { TransactionType } from '../../../models/portfolio.model';
import { PortfolioTransactionListComponent } from './transaction-list';

describe('PortfolioTransactionListComponent market picker', () => {
  let portfolioService: {
    getSymbolNames: ReturnType<typeof vi.fn>;
    getTransactions: ReturnType<typeof vi.fn>;
    createTransaction: ReturnType<typeof vi.fn>;
    updateTransaction: ReturnType<typeof vi.fn>;
    deleteTransaction: ReturnType<typeof vi.fn>;
  };

  beforeEach(async () => {
    portfolioService = {
      getSymbolNames: vi.fn().mockReturnValue(of({})),
      getTransactions: vi.fn().mockReturnValue(of({ items: [], total: 0 })),
      createTransaction: vi.fn().mockReturnValue(of({})),
      updateTransaction: vi.fn().mockReturnValue(of({})),
      deleteTransaction: vi.fn().mockReturnValue(of(undefined)),
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

  it('adds timeline market badges only for non-TW rows', () => {
    const fixture = TestBed.createComponent(PortfolioTransactionListComponent);
    fixture.detectChanges();
    fixture.componentInstance.transactions.set([
      transaction({ symbol: '2330', market: 'TW' }),
      transaction({ symbol: 'AAPL', market: 'US' }),
    ]);

    const rows = fixture.componentInstance.timelineRows();

    expect(rows[0].metaBadge).toBeUndefined();
    expect(rows[1].metaBadge).toBe('US');
  });
});

function transaction(overrides: Partial<Parameters<PortfolioTransactionListComponent['symbolDisplay']>[0]> = {}) {
  return {
    id: 1,
    symbol: '2330',
    type: TransactionType.BUY,
    quantity: 1,
    price: 100,
    fee: 0,
    tax: 0,
    trade_date: '2026-05-01',
    ...overrides,
  };
}
