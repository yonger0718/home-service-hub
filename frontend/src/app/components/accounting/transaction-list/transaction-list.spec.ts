import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { describe, expect, it, beforeEach, vi } from 'vitest';

import { AccountingService } from '../../../services/accounting.service';
import { TransactionListComponent } from './transaction-list';

describe('TransactionListComponent', () => {
  let accountingService: {
    getTransactions: ReturnType<typeof vi.fn>;
    getCategories: ReturnType<typeof vi.fn>;
    getCards: ReturnType<typeof vi.fn>;
    getPaymentMethods: ReturnType<typeof vi.fn>;
    createTransaction: ReturnType<typeof vi.fn>;
    updateTransaction: ReturnType<typeof vi.fn>;
    deleteTransaction: ReturnType<typeof vi.fn>;
    refundTransaction: ReturnType<typeof vi.fn>;
    triggerRecurringGeneration: ReturnType<typeof vi.fn>;
  };

  beforeEach(async () => {
    accountingService = {
      getTransactions: vi.fn().mockReturnValue(of([])),
      getCategories: vi.fn().mockReturnValue(of([{ id: 1, name: '餐飲', color: '#533afd' }])),
      getCards: vi.fn().mockReturnValue(of([{ id: 7, name: '國泰 CUBE 卡', billingDay: 1, rewardCycleType: 'CALENDAR', alertThreshold: 30000, defaultPaymentMethod: '信用卡' }])),
      getPaymentMethods: vi.fn().mockReturnValue(of([{ id: 1, name: '現金', isActive: true }])),
      createTransaction: vi.fn().mockReturnValue(of({})),
      updateTransaction: vi.fn().mockReturnValue(of({})),
      deleteTransaction: vi.fn().mockReturnValue(of(undefined)),
      refundTransaction: vi.fn().mockReturnValue(of({})),
      triggerRecurringGeneration: vi.fn().mockReturnValue(of({ message: 'ok' })),
    };

    await TestBed.configureTestingModule({
      imports: [TransactionListComponent],
      providers: [{ provide: AccountingService, useValue: accountingService }],
    }).compileComponents();
  });

  it('opens the transaction dialog from the add button', () => {
    const fixture = TestBed.createComponent(TransactionListComponent);
    fixture.detectChanges();

    const add = (Array.from(fixture.nativeElement.querySelectorAll('button')) as HTMLButtonElement[])
      .find(button => button.textContent?.includes('新增交易')) as HTMLButtonElement;
    add.click();
    fixture.detectChanges();

    expect(fixture.componentInstance.displayDialog).toBe(true);
    expect(fixture.componentInstance.newTxn.transactionType).toBe('EXPENSE');
  });

  it('saves a valid transaction through the existing service', () => {
    const fixture = TestBed.createComponent(TransactionListComponent);
    fixture.detectChanges();
    const component = fixture.componentInstance;
    component.showDialog();
    component.newTxn.item = '午餐';
    component.newTxn.categoryId = 1;
    component.newTxn.transactionAmount = 120;
    component.newTxn.paidAmount = 120;

    component.saveTransaction();

    expect(accountingService.createTransaction).toHaveBeenCalledWith(expect.objectContaining({
      item: '午餐',
      categoryId: 1,
      transactionAmount: 120,
      transactionType: 'EXPENSE',
    }));
    expect(component.displayDialog).toBe(false);
  });
});
